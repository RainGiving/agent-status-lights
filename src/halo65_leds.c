// SPDX-License-Identifier: MIT
// Wire encoder for the wireless status link: the only host-to-keyboard channel
// that survives Bluetooth.
//
// Over USB the daemon drives the lights through VIA raw HID. Bluetooth exposes
// no VIA interface, and the RF module forwards exactly one host-originated
// byte to the keyboard's main MCU: the standard HID keyboard LED report,
// masked to its low three bits. Caps Lock belongs to the OS -- macOS rewrites
// the whole byte on every toggle -- so the usable payload is NumLock (bit 0)
// and ScrollLock (bit 2): one 2-bit symbol at a time, re-sent periodically
// because the OS overwrites it.
//
// Status states: 00 idle, 01 running, 10 permission; 11 escapes, then
// 01 failure, 10 completed, 00 voice. Escaped states re-assert as a repeating
// 11/operand cycle. Config changes travel as self-clocking base-3 frames --
// every symbol differs from the last, next = (prev + 1 + trit) mod 4 -- opened
// by holding 11 and closed by parking the current status. The decoder lives in
// the firmware's halo_link.c; every timing constant here sits above the
// matching threshold there.
//
// Input is led.state (state number, optional config frames), written by the
// daemon. Setting an LED means opening the keyboard device, which macOS gates
// behind Input Monitoring, so this runs as its own launch agent: the
// permission belongs to this binary rather than to the Python daemon that
// decides what to send.
#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <IOKit/hid/IOHIDUsageTables.h>
#include <IOKit/hidsystem/IOHIDLib.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define TICK_SECONDS      0.02
#define STATE_COUNT       6

/* Encoder timings (ms). The decoder's thresholds in halo_link.h are what
 * they must clear: refresh 100 < wipe guard 300; escape phases < entry 800;
 * symbol 100 > one fast-poll interval (50) plus BLE jitter; rep gap 450
 * inside (300, 700); park 900 > exit 700. The entry hold must beat the
 * decoder's 800 ms measured from when it *samples* the 11 -- which can lag
 * the write by a full slow-poll interval (200 ms) plus BLE delay -- so
 * 1200 keeps ~150 ms of margin before the first data symbol.
 *
 * The first 11 of a fresh escape cycle is held 500 ms: entering the cycle
 * the keyboard still polls slowly, and a single lost poll would make a
 * 250 ms prefix invisible, turning the operand into a false direct state
 * (the "brief running-blue before failure" defect). 500 survives one lost
 * slow poll and still sits under the 800 ms config-entry threshold. */
#define REWRITE_MS        100
#define ESCAPE_PHASE_MS   250
#define ESCAPE_FIRST_MS   500
#define ENTRY_HOLD_MS     1200
#define SYMBOL_MS         100
#define REP_GAP_MS        450
#define PARK_HOLD_MS      900
#define FRAME_DEBOUNCE_MS 400
#define XMIT_REPS         3

static char   g_state_path[1024];
static char   g_status_path[1024];
static volatile sig_atomic_t g_stop = 0;

static bool g_simulate = false;

__attribute__((format(printf, 1, 2)))
static void logline(const char *fmt, ...) {
    /* In simulate mode stdout is the wire; keep prose off it. */
    FILE *out = g_simulate ? stderr : stdout;
    char stamp[32];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &tm);
    fprintf(out, "%s ", stamp);
    va_list args;
    va_start(args, fmt);
    vfprintf(out, fmt, args);
    va_end(args);
    fprintf(out, "\n");
    fflush(out);
}

/* ----------------------------------------------------------------- command */

typedef struct {
    int      state;                 /* 0-5 */
    uint8_t  frames[192];           /* one group of complete config frames */
    size_t   frames_len;
    long     frames_id;             /* 0 = none */
} command_t;

static command_t g_cmd = {0};
static char      g_frames_line[512];   /* last seen frames+id text */
static uint64_t  g_frames_seen_at = 0; /* for the write debounce */

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void read_command(uint64_t now) {
    FILE *f = fopen(g_state_path, "r");
    if (!f) return;
    char line[600];
    int state = -1;
    char frames_text[512] = "";
    long frames_id = 0;
    char frames_hex[400] = "";
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "state=%d", &state) == 1) continue;
        if (sscanf(line, "frames=%399s", frames_hex) == 1) continue;
        if (sscanf(line, "frames_id=%ld", &frames_id) == 1) continue;
    }
    fclose(f);
    if (state >= 0 && state < STATE_COUNT) g_cmd.state = state;

    snprintf(frames_text, sizeof(frames_text), "%s#%ld", frames_hex, frames_id);
    if (strcmp(frames_text, g_frames_line) != 0) {
        strlcpy(g_frames_line, frames_text, sizeof(g_frames_line));
        g_frames_seen_at = now;
        g_cmd.frames_len = 0;
        g_cmd.frames_id  = 0;
        size_t n = strlen(frames_hex);
        if (frames_id > 0 && n >= 2 && n % 2 == 0 && n / 2 <= sizeof(g_cmd.frames)) {
            for (size_t i = 0; i < n / 2; i++) {
                int hi = hexval(frames_hex[2 * i]), lo = hexval(frames_hex[2 * i + 1]);
                if (hi < 0 || lo < 0) { n = 0; break; }
                g_cmd.frames[i] = (uint8_t)((hi << 4) | lo);
            }
            if (n) {
                g_cmd.frames_len = n / 2;
                g_cmd.frames_id  = frames_id;
            }
        }
    }
}

/* ------------------------------------------------------------------ engine */

typedef enum { PH_STATUS, PH_PAIR_ESC, PH_PAIR_OP, PH_ENTRY, PH_DATA, PH_GAP, PH_PARK } phase_t;

static struct {
    phase_t  phase;
    uint64_t phase_at;
    int      wire;         /* last symbol written, -1 = nothing yet */
    uint64_t wrote_at;
    int      pair_state;   /* escaped state the current 11/operand pair asserts */
    bool     pair_first;   /* fresh cycle: hold the 11 for ESCAPE_FIRST_MS */
    uint8_t  trits[1024];  /* all copies, flat */
    int      ntrits, itrit;
    int      copy_end[40]; /* trit index where each copy ends */
    int      ncopies, copy;
    long     xmit_id;      /* frames_id being transmitted, 0 = none */
    long     done_id;      /* last frames_id fully transmitted or skipped */
} eng = {.phase = PH_STATUS, .wire = -1};

/* Provided by the caller: writes one 2-bit symbol to the wire. */
static void wire_write(int symbol, uint64_t now);

static bool is_escaped(int state) { return state >= 3; }

/* failure -> 01, completed -> 00, voice -> 10; see halo_link.h for why the
 * 00 operand belongs to completed of all states. */
static int operand_of(int state) {
    return state == 3 ? 1 : state == 4 ? 0 : 2;
}

static int park_symbol(int state) { return is_escaped(state) ? 3 : state; }

static uint8_t field_payload_len(uint8_t field) {
    switch (field) {
        case 0:  return 3; /* ring colour */
        case 1:  return 4; /* ring params */
        case 2:  return 7; /* matrix */
        default: return 0;
    }
}

/* Frames -> trits. Each frame is padded to a 3-bit boundary of its own, sent
 * XMIT_REPS times in a row -- the decoder applies on two *consecutive*
 * identical CRC-clean frames, and a corrupted copy in between is discarded
 * without breaking the adjacency of the clean ones -- with a gap after every
 * copy so a corrupt header cannot poison more than one copy. */
static bool build_trits(const uint8_t *frames, size_t len) {
    eng.ntrits  = 0;
    eng.ncopies = 0;
    size_t off = 0;
    while (off < len) {
        uint8_t plen = field_payload_len((uint8_t)(frames[off] >> 4));
        if (plen == 0) return false;
        size_t fsize = 1 + plen + 1;
        if (off + fsize > len) return false;
        size_t nbits   = fsize * 8;
        int    fstart  = eng.ntrits;
        for (size_t g = 0; g * 3 < nbits; g++) {
            uint8_t group = 0;
            for (size_t b = 0; b < 3; b++) {
                size_t bit = g * 3 + b;
                uint8_t v = 0;
                if (bit < nbits) {
                    v = (uint8_t)((frames[off + bit / 8] >> (7 - bit % 8)) & 1);
                }
                group = (uint8_t)((group << 1) | v);
            }
            if (eng.ntrits + 2 > (int)sizeof(eng.trits)) return false;
            eng.trits[eng.ntrits++] = (uint8_t)(group / 3);
            eng.trits[eng.ntrits++] = (uint8_t)(group % 3);
        }
        int flen = eng.ntrits - fstart;
        for (int rep = 1; rep < XMIT_REPS; rep++) {
            if (eng.ntrits + flen > (int)sizeof(eng.trits)) return false;
            memcpy(&eng.trits[eng.ntrits], &eng.trits[fstart], (size_t)flen);
            eng.ntrits += flen;
        }
        for (int rep = 0; rep < XMIT_REPS; rep++) {
            if (eng.ncopies >= (int)(sizeof(eng.copy_end) / sizeof(eng.copy_end[0]))) return false;
            eng.copy_end[eng.ncopies++] = fstart + flen * (rep + 1);
        }
        off += fsize;
    }
    return eng.ntrits > 0;
}

static bool frames_pending(uint64_t now) {
    return g_cmd.frames_id > 0 && g_cmd.frames_id != eng.done_id
        && now - g_frames_seen_at >= FRAME_DEBOUNCE_MS;
}

static void xmit_start(uint64_t now) {
    if (!build_trits(g_cmd.frames, g_cmd.frames_len)) {
        logline("frames_id %ld unparseable, skipped", g_cmd.frames_id);
        eng.done_id = g_cmd.frames_id;
        return;
    }
    eng.xmit_id = g_cmd.frames_id;
    eng.copy    = 0;
    eng.itrit   = 0;
    wire_write(3, now);
    eng.phase    = PH_ENTRY;
    eng.phase_at = now;
    logline("sync %ld: %d trit(s) in %d copies, ~%d ms", eng.xmit_id, eng.ntrits,
            eng.ncopies, ENTRY_HOLD_MS + eng.ntrits * SYMBOL_MS
                         + (eng.ncopies - 1) * REP_GAP_MS + PARK_HOLD_MS);
}

static void park(uint64_t now) {
    wire_write(park_symbol(g_cmd.state), now);
    eng.phase    = PH_PARK;
    eng.phase_at = now;
}

static void engine_tick(uint64_t now) {
    uint64_t in_phase = now - eng.phase_at;

    switch (eng.phase) {
        case PH_STATUS:
            if (frames_pending(now)) { xmit_start(now); break; }
            if (is_escaped(g_cmd.state)) {
                eng.pair_state = g_cmd.state;
                eng.pair_first = true;
                wire_write(3, now);
                eng.phase    = PH_PAIR_ESC;
                eng.phase_at = now;
                break;
            }
            if (eng.wire != g_cmd.state || now - eng.wrote_at >= REWRITE_MS) {
                wire_write(g_cmd.state, now);
            }
            break;

        /* A started 11/operand pair always completes: the decoder holds an
         * outstanding escape after sampling the 11, and whatever it sees next
         * is consumed as the operand. */
        case PH_PAIR_ESC:
            if (in_phase >= (eng.pair_first ? ESCAPE_FIRST_MS : ESCAPE_PHASE_MS)) {
                eng.pair_first = false;
                wire_write(operand_of(eng.pair_state), now);
                eng.phase    = PH_PAIR_OP;
                eng.phase_at = now;
            } else if (now - eng.wrote_at >= REWRITE_MS) {
                wire_write(3, now);
            }
            break;

        case PH_PAIR_OP:
            if (in_phase >= ESCAPE_PHASE_MS) {
                if (frames_pending(now)) { xmit_start(now); break; }
                if (is_escaped(g_cmd.state)) {
                    eng.pair_first = g_cmd.state != eng.pair_state;
                    eng.pair_state = g_cmd.state;
                    wire_write(3, now);
                    eng.phase    = PH_PAIR_ESC;
                    eng.phase_at = now;
                } else {
                    eng.phase    = PH_STATUS;
                    eng.phase_at = now;
                }
            } else if (now - eng.wrote_at >= REWRITE_MS) {
                wire_write(operand_of(eng.pair_state), now);
            }
            break;

        /* A transmission always runs to completion: a fresher frames_id waits
         * until the park, the daemon coalesces intermediate edits, and the
         * values are absolute so a stale group doing one last lap is only a
         * delay, never a wrong result. Aborting instead would chain long 11
         * holds whose phase the decoder cannot tell apart. */
        case PH_ENTRY:
            if (in_phase >= ENTRY_HOLD_MS) {
                eng.phase    = PH_DATA;
                eng.phase_at = now;
                wire_write((eng.wire + 1 + eng.trits[eng.itrit++]) & 3, now);
            } else if (now - eng.wrote_at >= REWRITE_MS) {
                wire_write(3, now);
            }
            break;

        case PH_DATA:
            if (now - eng.wrote_at >= SYMBOL_MS) {
                if (eng.itrit < eng.copy_end[eng.copy]) {
                    wire_write((eng.wire + 1 + eng.trits[eng.itrit++]) & 3, now);
                } else if (++eng.copy < eng.ncopies) {
                    eng.phase    = PH_GAP;
                    eng.phase_at = now;
                } else {
                    eng.done_id = eng.xmit_id;
                    eng.xmit_id = 0;
                    logline("sync %ld done", eng.done_id);
                    park(now);
                }
            }
            break;

        case PH_GAP:
            if (in_phase >= REP_GAP_MS) {
                eng.phase    = PH_DATA;
                eng.phase_at = now;
                wire_write((eng.wire + 1 + eng.trits[eng.itrit++]) & 3, now);
            } else if (now - eng.wrote_at >= REWRITE_MS) {
                wire_write(eng.wire, now);
            }
            break;

        case PH_PARK:
            if (in_phase >= PARK_HOLD_MS) {
                if (is_escaped(g_cmd.state)) {
                    /* The park already held the 11; continue with the operand
                     * so the decoder's re-armed escape resolves correctly. */
                    eng.pair_state = g_cmd.state;
                    wire_write(operand_of(g_cmd.state), now);
                    eng.phase    = PH_PAIR_OP;
                    eng.phase_at = now;
                } else {
                    eng.phase    = PH_STATUS;
                    eng.phase_at = now;
                }
            } else if (now - eng.wrote_at >= REWRITE_MS) {
                wire_write(park_symbol(g_cmd.state), now);
            }
            break;
    }
}

static int engine_progress(void) {
    if (eng.phase != PH_DATA && eng.phase != PH_GAP && eng.phase != PH_ENTRY) return -1;
    return eng.ntrits ? eng.itrit * 100 / eng.ntrits : -1;
}

/* ------------------------------------------------------------------ device */

static long prop_num(IOHIDDeviceRef d, CFStringRef key) {
    CFTypeRef v = IOHIDDeviceGetProperty(d, key);
    long out = -1;
    if (v && CFGetTypeID(v) == CFNumberGetTypeID()) {
        CFNumberGetValue((CFNumberRef)v, kCFNumberLongType, &out);
    }
    return out;
}

static void prop_str(IOHIDDeviceRef d, CFStringRef key, char *buf, size_t n) {
    buf[0] = '\0';
    CFTypeRef v = IOHIDDeviceGetProperty(d, key);
    if (v && CFGetTypeID(v) == CFStringGetTypeID()) {
        CFStringGetCString((CFStringRef)v, buf, (CFIndex)n, kCFStringEncodingUTF8);
    }
}

/* The board answers to three identities -- USB, Bluetooth and the 2.4G dongle --
 * so match on the name rather than on a product id that changes with the
 * transport. */
static bool is_halo65(IOHIDDeviceRef dev) {
    char product[256];
    prop_str(dev, CFSTR(kIOHIDProductKey), product, sizeof(product));
    if (!strstr(product, "Halo65")) return false;
    return prop_num(dev, CFSTR(kIOHIDPrimaryUsagePageKey)) == kHIDPage_GenericDesktop &&
           prop_num(dev, CFSTR(kIOHIDPrimaryUsageKey)) == kHIDUsage_GD_Keyboard;
}

static IOHIDManagerRef g_manager;
static char            g_transports[128];
static int             g_devices;

/* Writes both data bits to every Halo65 keyboard interface in ONE output
 * report per report id. Atomicity is load-bearing: setting the two LED
 * elements one IOHIDDeviceSetValue at a time becomes two BLE writes, and a
 * two-bit change (11 -> 00) then shows an intermediate symbol on the wire
 * for a connection interval -- long enough for the keyboard's 50 ms poll to
 * read a state that was never sent (measured: an escape/00 re-assert cycle
 * flashed failure and voice into a held completed). The report carries the
 * OS's real Caps Lock state, so our writes never fight the caps indicator.
 *
 * Layout is the boot-keyboard LED report -- bit = usage - 1 -- which both
 * the USB interface and the BLE report map use here. If SetReport is ever
 * refused, the per-element path is kept as a logged fallback. */
static int write_bits(int num, int scroll, bool verbose) {
    CFSetRef devices = IOHIDManagerCopyDevices(g_manager);
    if (!devices) { g_devices = 0; g_transports[0] = '\0'; return 0; }
    CFIndex count = CFSetGetCount(devices);
    IOHIDDeviceRef *list = calloc((size_t)count ? (size_t)count : 1, sizeof(IOHIDDeviceRef));
    CFSetGetValues(devices, (const void **)list);

    bool caps = (CGEventSourceFlagsState(kCGEventSourceStateCombinedSessionState)
                 & kCGEventFlagMaskAlphaShift) != 0;

    int  touched = 0;
    char transports[128] = "";
    for (CFIndex i = 0; i < count; i++) {
        IOHIDDeviceRef dev = list[i];
        if (!is_halo65(dev)) continue;

        char product[256], transport[64];
        prop_str(dev, CFSTR(kIOHIDProductKey), product, sizeof(product));
        prop_str(dev, CFSTR(kIOHIDTransportKey), transport, sizeof(transport));

        CFArrayRef elements = IOHIDDeviceCopyMatchingElements(dev, NULL, kIOHIDOptionsTypeNone);
        if (!elements) {
            if (verbose) logline("%s (%s): no elements -- input monitoring?", product, transport);
            continue;
        }
        /* One LED output report per report id seen on this interface. */
        struct { uint32_t rid; uint8_t byte; bool carries_data; } reports[4];
        int nreports = 0;
        IOHIDElementRef el_num = NULL, el_scroll = NULL;
        for (CFIndex e = 0; e < CFArrayGetCount(elements); e++) {
            IOHIDElementRef el = (IOHIDElementRef)CFArrayGetValueAtIndex(elements, e);
            if (IOHIDElementGetUsagePage(el) != kHIDPage_LEDs) continue;
            uint32_t usage = IOHIDElementGetUsage(el);
            if (usage < kHIDUsage_LED_NumLock || usage > kHIDUsage_LED_Kana) continue;
            if (usage == kHIDUsage_LED_NumLock) el_num = el;
            if (usage == kHIDUsage_LED_ScrollLock) el_scroll = el;
            uint32_t rid = IOHIDElementGetReportID(el);
            int slot = -1;
            for (int r = 0; r < nreports; r++) {
                if (reports[r].rid == rid) { slot = r; break; }
            }
            if (slot < 0 && nreports < 4) {
                slot = nreports++;
                reports[slot].rid = rid;
                reports[slot].byte = 0;
                reports[slot].carries_data = false;
            }
            if (slot < 0) continue;
            int value = usage == kHIDUsage_LED_NumLock    ? num
                      : usage == kHIDUsage_LED_CapsLock   ? (caps ? 1 : 0)
                      : usage == kHIDUsage_LED_ScrollLock ? scroll : 0;
            reports[slot].byte |= (uint8_t)(value << (usage - kHIDUsage_LED_NumLock));
            if (usage == kHIDUsage_LED_NumLock || usage == kHIDUsage_LED_ScrollLock) {
                reports[slot].carries_data = true;
            }
        }
        bool wrote = false;
        bool need_fallback = false;
        for (int r = 0; r < nreports; r++) {
            if (!reports[r].carries_data) continue;
            uint8_t payload = reports[r].byte;
            if (IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput,
                                     (CFIndex)reports[r].rid, &payload, 1) == kIOReturnSuccess) {
                wrote = true;
            } else {
                need_fallback = true;
            }
        }
        if (need_fallback && !wrote && (el_num || el_scroll)) {
            /* Last resort: the old element-at-a-time path. Not atomic, but a
             * flickering channel still beats a dead one. */
            static bool warned = false;
            if (!warned) {
                warned = true;
                logline("SetReport refused, falling back to per-element LED writes");
            }
            IOHIDElementRef els[2] = {el_num, el_scroll};
            int vals[2] = {num, scroll};
            for (int k = 0; k < 2; k++) {
                if (!els[k]) continue;
                IOHIDValueRef v = IOHIDValueCreateWithIntegerValue(kCFAllocatorDefault,
                                                                   els[k], 0, vals[k]);
                if (IOHIDDeviceSetValue(dev, els[k], v) == kIOReturnSuccess) wrote = true;
                CFRelease(v);
            }
        }
        CFRelease(elements);
        if (wrote) {
            touched++;
            if (transports[0]) strlcat(transports, ",", sizeof(transports));
            strlcat(transports, transport[0] ? transport : "?", sizeof(transports));
        }
        if (verbose) logline("%s (%s): %s", product, transport,
                             wrote ? "wrote LED report" : "no LED elements");
    }
    free(list);
    CFRelease(devices);
    g_devices = touched;
    strlcpy(g_transports, transports, sizeof(g_transports));
    return touched;
}

static void wire_write(int symbol, uint64_t now) {
    eng.wire     = symbol;
    eng.wrote_at = now;
    if (g_simulate) {
        printf("%llu %d\n", (unsigned long long)now, symbol);
        return;
    }
    write_bits(symbol & 1, (symbol >> 1) & 1, false);
}

/* ------------------------------------------------------------------ status */

static const char *access_word(IOHIDAccessType type) {
    return type == kIOHIDAccessTypeGranted ? "granted"
         : type == kIOHIDAccessTypeDenied  ? "denied" : "unknown";
}

static void write_status(const char *sender, const char *monitoring) {
    char tmp[1100];
    snprintf(tmp, sizeof(tmp), "%s.tmp", g_status_path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "sender=%s\ninput_monitoring=%s\ndevices=%d\ntransports=%s\n"
               "state=%d\nmode=%s\n",
            sender, monitoring, g_devices, g_transports, g_cmd.state,
            eng.xmit_id ? "sync" : "status");
    int progress = engine_progress();
    if (progress >= 0) fprintf(f, "sync_progress=%d\n", progress);
    fprintf(f, "frames_done=%ld\nupdated=%ld\n", eng.done_id, (long)time(NULL));
    fclose(f);
    rename(tmp, g_status_path);
}

/* ---------------------------------------------------------------- run loop */

static uint64_t now_ms(void) {
    return (uint64_t)(CFAbsoluteTimeGetCurrent() * 1000.0);
}

static void tick(CFRunLoopTimerRef timer, void *info) {
    (void)timer; (void)info;
    static char last_summary[256];
    static uint64_t last_status_at = 0;

    if (g_stop) {
        write_bits(0, 0, false);
        CFRunLoopStop(CFRunLoopGetCurrent());
        return;
    }
    uint64_t now = now_ms();
    read_command(now);
    engine_tick(now);

    char summary[256];
    snprintf(summary, sizeof(summary), "%d/%d/%s/%d/%ld", g_cmd.state, g_devices,
             g_transports, engine_progress(), eng.done_id);
    if (strcmp(summary, last_summary) != 0 || now - last_status_at >= 5000) {
        strlcpy(last_summary, summary, sizeof(last_summary));
        last_status_at = now;
        write_status(g_devices ? "running" : "no keyboard interface", "granted");
    }
}

static void on_signal(int sig) { (void)sig; g_stop = 1; }

/* -------------------------------------------------------------- simulation */

/* Virtual-clock run of the very engine the daemon drives, HID replaced by
 * stdout. The firmware test harness replays these lines into halo_link.c.
 *   stdin:  "state <ms> <0-5>" / "frames <ms> <hex> <id>" / "end <ms>"
 *   stdout: "<ms> <symbol>" per wire write */
static int simulate(void) {
    static struct { uint64_t at; int state; } states[1024];
    static struct { uint64_t at; char hex[400]; long id; } frames[256];
    int nstates = 0, nframes = 0;
    uint64_t end = 30000;
    char line[600];
    while (fgets(line, sizeof(line), stdin)) {
        unsigned long long at;
        int st;
        if (sscanf(line, "state %llu %d", &at, &st) == 2 && nstates < 1024) {
            states[nstates].at = at; states[nstates].state = st; nstates++;
        } else if (nframes < 256 &&
                   sscanf(line, "frames %llu %399s %ld", &at, frames[nframes].hex,
                          &frames[nframes].id) == 3) {
            frames[nframes].at = at; nframes++;
        } else if (sscanf(line, "end %llu", &at) == 1) {
            end = at;
        }
    }
    for (uint64_t now = 0; now <= end; now += 20) {
        for (int i = 0; i < nstates; i++) {
            if (states[i].at <= now && states[i].at > now - 20) g_cmd.state = states[i].state;
        }
        for (int i = 0; i < nframes; i++) {
            if (frames[i].at <= now && frames[i].at > now - 20) {
                size_t n = strlen(frames[i].hex);
                g_cmd.frames_len = 0;
                for (size_t k = 0; k < n / 2 && k < sizeof(g_cmd.frames); k++) {
                    g_cmd.frames[k] = (uint8_t)((hexval(frames[i].hex[2 * k]) << 4)
                                                | hexval(frames[i].hex[2 * k + 1]));
                    g_cmd.frames_len++;
                }
                g_cmd.frames_id  = frames[i].id;
                g_frames_seen_at = now;
            }
        }
        engine_tick(now);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "simulate")) {
        /* No HID, no permissions: print wire writes against a virtual clock. */
        g_simulate = true;
        return simulate();
    }

    const char *home = getenv("HOME");
    if (!home) { fprintf(stderr, "no HOME\n"); return 2; }
    snprintf(g_state_path, sizeof(g_state_path),
             "%s/Library/Application Support/ClaudeHalo65/led.state", home);
    snprintf(g_status_path, sizeof(g_status_path),
             "%s/Library/Application Support/ClaudeHalo65/leds.status", home);

    IOHIDAccessType granted = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent);
    g_manager = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    IOHIDManagerSetDeviceMatching(g_manager, NULL);
    // Without a run-loop schedule the manager never processes arrival and
    // removal notifications, and its device set freezes at open time -- a
    // keyboard switching USB<->Bluetooth after startup would simply vanish.
    IOHIDManagerScheduleWithRunLoop(g_manager, CFRunLoopGetMain(), kCFRunLoopDefaultMode);
    IOHIDManagerOpen(g_manager, kIOHIDOptionsTypeNone);

    if (argc > 1 && !strcmp(argv[1], "check")) {
        printf("input_monitoring=%s\n", access_word(granted));
        write_bits(0, 0, true);
        return granted == kIOHIDAccessTypeGranted ? 0 : 1;
    }
    if (argc > 2 && !strcmp(argv[1], "set")) {
        int symbol = (int)strtol(argv[2], NULL, 0) & 0x03;
        int touched = write_bits(symbol & 1, (symbol >> 1) & 1, true);
        printf("symbol %d -> %d interface(s)\n", symbol, touched);
        return touched ? 0 : 1;
    }

    if (granted != kIOHIDAccessTypeGranted) {
        // Asking is what puts the binary in the Input Monitoring list even when
        // the switch there still has to be flipped by hand.
        IOHIDRequestAccess(kIOHIDRequestTypeListenEvent);
        write_status("needs input monitoring", access_word(granted));
        logline("input monitoring not granted (%s): add halo65_leds under "
                "System Settings > Privacy & Security > Input Monitoring",
                access_word(granted));
        return 1;
    }

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    /* Frames already in the file predate this process; replaying them would
     * be harmless but noisy, so they are marked done and only new ids send. */
    read_command(now_ms());
    eng.done_id = g_cmd.frames_id;

    write_status("running", "granted");
    logline("watching %s", g_state_path);

    CFRunLoopTimerRef timer = CFRunLoopTimerCreate(kCFAllocatorDefault,
                                                   CFAbsoluteTimeGetCurrent() + TICK_SECONDS,
                                                   TICK_SECONDS, 0, 0, tick, NULL);
    CFRunLoopAddTimer(CFRunLoopGetCurrent(), timer, kCFRunLoopCommonModes);
    CFRunLoopRun();
    CFRelease(timer);
    return 0;
}
