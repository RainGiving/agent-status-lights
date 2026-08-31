// SPDX-License-Identifier: MIT
// Voice-input watcher: holds the "voice" status light on while the user is
// dictating, and drops it when they stop.
//
// Two independent triggers, either or both (voice.conf decides):
//
//   hotkey      a listen-only CGEventTap matching one key combination. Needs
//               Input Monitoring, which the user grants by hand once.
//   microphone  the default input device's kAudioDevicePropertyDeviceIsRunning-
//               Somewhere. Needs no permission at all, and ends the state on
//               its own when recording stops, but it lights up for any
//               recording, not only for dictation.
//
// The tap compares each key event against the one configured combination and
// forwards a single boolean. It never reports which key was pressed, and it
// never sees anything for keys other than the configured one.
//
// Config is a flat key=value file written by the daemon, re-read on mtime, so
// changing the shortcut in the settings app takes effect without a restart.
#include <ApplicationServices/ApplicationServices.h>
#include <CoreAudio/CoreAudio.h>
#include <IOKit/hidsystem/IOHIDLib.h>
#include <signal.h>
#include <stdarg.h>
#include <time.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

enum {
    TRIGGER_HOTKEY = 1,
    TRIGGER_MIC    = 2,
};

// Only these four are matched. The device-independent bits (numeric pad, help,
// caps lock) would otherwise make an otherwise identical combination miss.
#define MOD_MASK (kCGEventFlagMaskControl | kCGEventFlagMaskShift | \
                  kCGEventFlagMaskAlternate | kCGEventFlagMaskCommand)

#define POLL_SECONDS 0.25

typedef struct {
    bool         enabled;
    int          triggers;
    int          keycode;
    CGEventFlags modifiers;
    bool         toggle;       /* press to start, press again to stop */
    double       tail_seconds; /* keep the light on this long after the trigger drops */
} config_t;

static config_t    g_config = {true, TRIGGER_HOTKEY, 49, kCGEventFlagMaskControl, false, 1.2};
static char        g_conf_path[1024];
static char        g_sock_path[1024];
static time_t      g_conf_mtime  = 0;
static bool        g_hotkey_on   = false;
static bool        g_mic_on      = false;
static bool        g_sent_active = false;
static double      g_off_at      = 0;   /* 0 = no pending tail */
static CFMachPortRef g_tap       = NULL;
static volatile sig_atomic_t g_stop = 0;

static double now_seconds(void) { return CFAbsoluteTimeGetCurrent(); }

__attribute__((format(printf, 1, 2)))
static void logline(const char *fmt, ...) {
    char stamp[32];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &tm);
    printf("%s ", stamp);
    va_list args;
    va_start(args, fmt);
    vprintf(fmt, args);
    va_end(args);
    printf("\n");
    fflush(stdout);
}

/* ------------------------------------------------------------------ config */

static CGEventFlags parse_modifiers(const char *value) {
    /* Accepts either a hex mask ("0x40000") or names ("control+shift"). */
    if (value[0] == '0' && (value[1] == 'x' || value[1] == 'X')) {
        return (CGEventFlags)strtoul(value, NULL, 16) & MOD_MASK;
    }
    CGEventFlags flags = 0;
    if (strstr(value, "control") || strstr(value, "ctrl")) flags |= kCGEventFlagMaskControl;
    if (strstr(value, "shift"))                            flags |= kCGEventFlagMaskShift;
    if (strstr(value, "option") || strstr(value, "alt"))   flags |= kCGEventFlagMaskAlternate;
    if (strstr(value, "command") || strstr(value, "cmd"))  flags |= kCGEventFlagMaskCommand;
    return flags;
}

static void trim(char *s) {
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t')) {
        s[--n] = '\0';
    }
}

/* Returns true when the file was read and differed from what we had. */
static bool load_config(void) {
    struct stat st;
    if (stat(g_conf_path, &st) != 0) return false;
    if (st.st_mtime == g_conf_mtime) return false;
    g_conf_mtime = st.st_mtime;

    FILE *f = fopen(g_conf_path, "r");
    if (!f) return false;

    config_t next = g_config;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        trim(line);
        if (line[0] == '\0' || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        const char *key = line, *value = eq + 1;

        if (!strcmp(key, "enabled")) {
            next.enabled = (value[0] == '1' || value[0] == 't' || value[0] == 'y');
        } else if (!strcmp(key, "trigger")) {
            next.triggers = 0;
            if (strstr(value, "hotkey"))     next.triggers |= TRIGGER_HOTKEY;
            if (strstr(value, "microphone")) next.triggers |= TRIGGER_MIC;
            if (!strcmp(value, "both"))      next.triggers = TRIGGER_HOTKEY | TRIGGER_MIC;
        } else if (!strcmp(key, "keycode")) {
            next.keycode = atoi(value);
        } else if (!strcmp(key, "modifiers")) {
            next.modifiers = parse_modifiers(value);
        } else if (!strcmp(key, "mode")) {
            next.toggle = !strcmp(value, "toggle");
        } else if (!strcmp(key, "tail_ms")) {
            next.tail_seconds = atoi(value) / 1000.0;
        }
    }
    fclose(f);

    bool changed = memcmp(&next, &g_config, sizeof(config_t)) != 0;
    g_config = next;
    return changed;
}

/* ------------------------------------------------------------------ output */

/* The daemon and the settings app both need to know whether the tap actually
 * came up, and only this process can answer that: Input Monitoring is granted
 * to this binary, so a check made anywhere else reports the wrong process. */
static void write_status(const char *watcher, const char *monitoring) {
    char path[1100], tmp[1160];
    const char *home = getenv("HOME");
    if (!home) return;
    snprintf(path, sizeof(path),
             "%s/Library/Application Support/ClaudeHalo65/voice.status", home);
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "watcher=%s\ninput_monitoring=%s\n", watcher, monitoring);
    fclose(f);
    rename(tmp, path);
}

static const char *access_word(IOHIDAccessType type) {
    return type == kIOHIDAccessTypeGranted ? "granted"
         : type == kIOHIDAccessTypeDenied  ? "denied" : "unknown";
}

static void send_state(bool active) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return;
    struct timeval tv = {0, 250000};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, g_sock_path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) == 0) {
        char payload[96];
        int n = snprintf(payload, sizeof(payload),
                         "{\"source\": \"voice\", \"active\": %s}\n",
                         active ? "true" : "false");
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = write(fd, payload + off, (size_t)(n - off));
            if (w <= 0) break;
            off += w;
        }
    }
    close(fd);
}

/* One place decides what the daemon is told, so the two triggers can overlap
 * without fighting: the light is on while either is on, and the tail only runs
 * once both have let go. */
static void publish(void) {
    bool wanted = g_config.enabled &&
                  (((g_config.triggers & TRIGGER_HOTKEY) && g_hotkey_on) ||
                   ((g_config.triggers & TRIGGER_MIC) && g_mic_on));

    if (wanted) {
        g_off_at = 0;
        if (!g_sent_active) {
            g_sent_active = true;
            send_state(true);
            logline("voice on");
        }
        return;
    }
    if (!g_sent_active) return;
    if (g_config.tail_seconds <= 0) {
        g_sent_active = false;
        g_off_at = 0;
        send_state(false);
        logline("voice off");
        return;
    }
    if (g_off_at == 0) g_off_at = now_seconds() + g_config.tail_seconds;
}

static void expire_tail(void) {
    if (g_off_at == 0 || now_seconds() < g_off_at) return;
    g_off_at = 0;
    g_sent_active = false;
    send_state(false);
    logline("voice off");
}

/* ------------------------------------------------------------------ hotkey */

static CGEventRef on_key(CGEventTapProxy proxy, CGEventType type,
                         CGEventRef event, void *refcon) {
    (void)proxy;
    (void)refcon;

    // A tap that takes too long is disabled by the system rather than dropped;
    // re-enabling is the whole recovery.
    if (type == kCGEventTapDisabledByTimeout || type == kCGEventTapDisabledByUserInput) {
        if (g_tap) CGEventTapEnable(g_tap, true);
        logline("event tap re-enabled after %s",
                type == kCGEventTapDisabledByTimeout ? "timeout" : "user input");
        return event;
    }
    if (type != kCGEventKeyDown && type != kCGEventKeyUp) return event;

    int keycode = (int)CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode);
    if (keycode != g_config.keycode) return event;

    if (type == kCGEventKeyDown) {
        if ((CGEventGetFlags(event) & MOD_MASK) != g_config.modifiers) return event;
        // Auto-repeat while held would toggle on and off in toggle mode.
        if (CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat)) return event;
        g_hotkey_on = g_config.toggle ? !g_hotkey_on : true;
        publish();
    } else if (!g_config.toggle && g_hotkey_on) {
        // Key-up carries whatever modifiers are still down, so it is matched on
        // the key alone: letting go of the modifier first must still end it.
        g_hotkey_on = false;
        publish();
    }
    return event;
}

static bool start_tap(void) {
    IOHIDAccessType granted = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent);
    if (granted != kIOHIDAccessTypeGranted) {
        // Asking is what puts the binary into the Input Monitoring list, even
        // when the user has to flip the switch there by hand afterwards. It is
        // asked exactly once: launchd restarts this process on a throttle, and
        // a prompt on every restart would be its own kind of broken. Deleting
        // the marker (install.py voice) is what asks again.
        char marker[1100];
        const char *home = getenv("HOME");
        if (home) {
            snprintf(marker, sizeof(marker),
                     "%s/Library/Application Support/ClaudeHalo65/voice.asked", home);
            if (access(marker, F_OK) != 0) {
                IOHIDRequestAccess(kIOHIDRequestTypeListenEvent);
                FILE *f = fopen(marker, "w");
                if (f) fclose(f);
            }
        }
        write_status("needs input monitoring", access_word(granted));
        logline("input monitoring not granted (%s): add halo65_voice under "
                "System Settings > Privacy & Security > Input Monitoring",
                access_word(granted));
        return false;
    }

    CGEventMask mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp);
    g_tap = CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
                             kCGEventTapOptionListenOnly, mask, on_key, NULL);
    if (!g_tap) {
        logline("could not create the event tap even though access is granted");
        return false;
    }
    CFRunLoopSourceRef source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, g_tap, 0);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes);
    CFRelease(source);
    CGEventTapEnable(g_tap, true);
    write_status("running", "granted");
    return true;
}

/* -------------------------------------------------------------- microphone */

// Polled rather than subscribed: one HAL property read every quarter second
// costs nothing, and it re-resolves the default device on every pass, so
// plugging in a headset mid-dictation cannot strand the state.
static bool microphone_running(void) {
    AudioObjectPropertyAddress which = {kAudioHardwarePropertyDefaultInputDevice,
                                        kAudioObjectPropertyScopeGlobal,
                                        kAudioObjectPropertyElementMain};
    AudioDeviceID device = kAudioObjectUnknown;
    UInt32 size = sizeof(device);
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &which, 0, NULL, &size, &device)
            != noErr || device == kAudioObjectUnknown) {
        return false;
    }
    AudioObjectPropertyAddress running = {kAudioDevicePropertyDeviceIsRunningSomewhere,
                                          kAudioObjectPropertyScopeGlobal,
                                          kAudioObjectPropertyElementMain};
    UInt32 value = 0;
    size = sizeof(value);
    if (AudioObjectGetPropertyData(device, &running, 0, NULL, &size, &value) != noErr) {
        return false;
    }
    return value != 0;
}

/* ---------------------------------------------------------------- run loop */

static void tick(CFRunLoopTimerRef timer, void *info) {
    (void)timer;
    (void)info;

    if (g_stop) {
        if (g_sent_active) send_state(false);
        CFRunLoopStop(CFRunLoopGetCurrent());
        return;
    }
    if (load_config()) {
        logline("config reloaded: enabled=%d trigger=%d keycode=%d modifiers=0x%llx mode=%s tail=%.1fs",
                g_config.enabled, g_config.triggers, g_config.keycode,
                (unsigned long long)g_config.modifiers,
                g_config.toggle ? "toggle" : "hold", g_config.tail_seconds);
        if (!g_config.enabled || !(g_config.triggers & TRIGGER_HOTKEY)) g_hotkey_on = false;
        publish();
    }
    if (g_config.enabled && (g_config.triggers & TRIGGER_MIC)) {
        bool mic = microphone_running();
        if (mic != g_mic_on) {
            g_mic_on = mic;
            publish();
        }
    } else if (g_mic_on) {
        g_mic_on = false;
        publish();
    }
    expire_tail();
}

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

int main(int argc, char **argv) {
    const char *home = getenv("HOME");
    if (!home) { fprintf(stderr, "no HOME\n"); return 2; }
    snprintf(g_conf_path, sizeof(g_conf_path),
             "%s/Library/Application Support/ClaudeHalo65/voice.conf", home);
    snprintf(g_sock_path, sizeof(g_sock_path),
             "%s/Library/Application Support/ClaudeHalo65/status.sock", home);
    load_config();

    if (argc > 1 && !strcmp(argv[1], "check")) {
        IOHIDAccessType granted = IOHIDCheckAccess(kIOHIDRequestTypeListenEvent);
        printf("input_monitoring=%s\n", granted == kIOHIDAccessTypeGranted ? "granted" :
                                        granted == kIOHIDAccessTypeDenied ? "denied" : "unknown");
        printf("enabled=%d trigger=%d keycode=%d modifiers=0x%llx mode=%s tail_ms=%d\n",
               g_config.enabled, g_config.triggers, g_config.keycode,
               (unsigned long long)g_config.modifiers,
               g_config.toggle ? "toggle" : "hold", (int)(g_config.tail_seconds * 1000));
        printf("microphone_running=%d\n", microphone_running() ? 1 : 0);
        return granted == kIOHIDAccessTypeGranted ? 0 : 1;
    }

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    // Whatever the last run left behind is not true any more.
    send_state(false);

    if (g_config.enabled && (g_config.triggers & TRIGGER_HOTKEY)) {
        if (!start_tap()) {
            // Exiting lets launchd bring it back on its throttle once the user
            // has granted the permission, which a long-lived retry loop inside
            // the process would not pick up anyway.
            return 1;
        }
        logline("watching keycode %d with modifiers 0x%llx (%s)",
                g_config.keycode, (unsigned long long)g_config.modifiers,
                g_config.toggle ? "toggle" : "hold");
    }
    if (g_config.enabled && (g_config.triggers & TRIGGER_MIC)) {
        logline("watching the default input device");
        if (!(g_config.triggers & TRIGGER_HOTKEY)) {
            write_status("running", "not needed");
        }
    }
    if (!g_config.enabled) {
        write_status("disabled", access_word(IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)));
        logline("voice trigger disabled in settings; idling");
    }

    CFRunLoopTimerRef timer = CFRunLoopTimerCreate(kCFAllocatorDefault,
                                                   CFAbsoluteTimeGetCurrent() + POLL_SECONDS,
                                                   POLL_SECONDS, 0, 0, tick, NULL);
    CFRunLoopAddTimer(CFRunLoopGetCurrent(), timer, kCFRunLoopCommonModes);
    CFRunLoopRun();
    CFRelease(timer);
    if (g_sent_active) send_state(false);
    return 0;
}
