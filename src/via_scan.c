// SPDX-License-Identifier: MIT
// Read-only discovery for QMK keyboards that expose VIA raw HID (macOS).
//
// Answers three questions, in order, without writing a single byte to any
// keyboard:
//
//   1. Is a QMK/VIA keyboard plugged in at all?  Matched by HID usage page
//      0xFF60 / usage 0x61, which is VIA's own raw interface -- not by a
//      vendor/product id, so this finds boards nobody has mapped yet.
//   2. What is it?  Vendor and product ids, the USB string descriptors, and
//      the VIA protocol version it reports.
//   3. What lighting does it have?  id_custom_get_value against each lighting
//      channel. A channel the firmware does not implement answers 0xff, so
//      "answers at all" is the test, and the reply doubles as the channel's
//      current value.
//
// Output is JSON on stdout so install.py and the settings app can both read it.
//
// SAFETY: only VIA commands 0x01 (protocol version), 0x02 (get keyboard value)
// and 0x08 (get custom value) are ever sent. Sweeping VIA *command* ids is not
// safe and is deliberately not done anywhere here: 0x0A is id_eeprom_reset and
// 0x0B is id_bootloader_jump, which would wipe the keymap and drop the board
// into its bootloader. Channel ids inside 0x08 are safe to sweep; command ids
// are not.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum {
    VIA_USAGE_PAGE = 0xff60,
    VIA_USAGE = 0x61,
    REPORT_SIZE = 32,

    VIA_GET_PROTOCOL_VERSION = 0x01,
    VIA_GET_KEYBOARD_VALUE = 0x02,
    VIA_CUSTOM_GET_VALUE = 0x08,

    KB_UPTIME = 0x01,
    KB_FIRMWARE_VERSION = 0x04,
};

// The lighting channels VIA itself defines, plus the one this project's own
// firmware patch adds. A board can implement any subset; most implement one.
//
// How a channel's reply should be read. Only the QMK-defined channels share a
// value layout; a vendor channel means whatever its firmware decided, so
// guessing "brightness" at byte 3 would print a confident wrong number.
typedef enum { LAYOUT_QMK, LAYOUT_HALO_RING, LAYOUT_UNKNOWN } Layout;

typedef struct { uint8_t id; const char *key; const char *human; Layout layout; } Channel;

static const Channel CHANNELS[] = {
    {0x00, "vendor",     "vendor / custom",                            LAYOUT_UNKNOWN},
    {0x01, "backlight",  "QMK backlight (single colour)",              LAYOUT_QMK},
    {0x02, "rgblight",   "QMK RGBLIGHT (underglow strip)",             LAYOUT_QMK},
    {0x03, "rgb_matrix", "QMK RGB Matrix (per-key)",                   LAYOUT_QMK},
    {0x04, "led_matrix", "QMK LED Matrix (single-colour per-key)",     LAYOUT_QMK},
    {0x05, "audio",      "QMK audio",                                  LAYOUT_QMK},
    {0x10, "halo_ring",  "Halo ring (this project's firmware patch)",  LAYOUT_HALO_RING},
};
static const size_t CHANNEL_COUNT = sizeof(CHANNELS) / sizeof(CHANNELS[0]);

// value_ids shared by the QMK lighting channels. Not every channel implements
// every one -- backlight has no colour, for instance -- so each is probed.
typedef struct { uint8_t id; const char *key; } Value;

// Keep in step with enum halo_host_mode in firmware/halo-host-control.patch.
static const char *HALO_MODES[] = {"release", "solid", "pulse", "comet", "strobe", "fill"};

static const Value VALUES[] = {
    {0x01, "brightness"},
    {0x02, "effect"},
    {0x03, "speed"},
    {0x04, "color"},
};
static const size_t VALUE_COUNT = sizeof(VALUES) / sizeof(VALUES[0]);

typedef struct {
    bool received;
    IOReturn result;
    CFIndex len;
    uint8_t buf[REPORT_SIZE];
} In;

static long prop_num(IOHIDDeviceRef device, CFStringRef key) {
    CFTypeRef value = IOHIDDeviceGetProperty(device, key);
    long result = -1;
    if (value && CFGetTypeID(value) == CFNumberGetTypeID()) {
        CFNumberGetValue((CFNumberRef)value, kCFNumberLongType, &result);
    }
    return result;
}

// Copies a device string property into `out` as JSON-safe text. Anything below
// 0x20 or above 0x7e is dropped rather than escaped: these are USB string
// descriptors shown to a human, and a mangled byte is not worth carrying.
static void prop_string(IOHIDDeviceRef device, CFStringRef key, char *out, size_t size) {
    out[0] = '\0';
    CFTypeRef value = IOHIDDeviceGetProperty(device, key);
    if (!value || CFGetTypeID(value) != CFStringGetTypeID()) return;
    char raw[256];
    if (!CFStringGetCString((CFStringRef)value, raw, sizeof(raw), kCFStringEncodingUTF8)) return;
    size_t j = 0;
    for (size_t i = 0; raw[i] && j + 1 < size; i++) {
        unsigned char c = (unsigned char)raw[i];
        if (c == '"' || c == '\\') {
            if (j + 2 >= size) break;
            out[j++] = '\\';
            out[j++] = (char)c;
        } else if (c >= 0x20 && c <= 0x7e) {
            out[j++] = (char)c;
        }
    }
    out[j] = '\0';
}

static void input_cb(void *ctx, IOReturn res, void *sender, IOHIDReportType type,
                     uint32_t report_id, uint8_t *report, CFIndex length) {
    (void)sender; (void)type; (void)report_id;
    In *in = ctx;
    in->result = res;
    in->len = length > REPORT_SIZE ? REPORT_SIZE : length;
    memcpy(in->buf, report, (size_t)in->len);
    in->received = true;
    CFRunLoopStop(CFRunLoopGetCurrent());
}

// One request/response. `timeout` is short on purpose: an unimplemented channel
// still answers (with 0xff), so a real timeout means the device is not talking
// at all, and a scan must not stall for seconds per probe when that happens.
//
// `echo_len` is how many leading bytes of the request VIA repeats back, and it
// varies by command: id_get_protocol_version echoes the command byte alone and
// puts data in bytes 1-2, id_get_keyboard_value also echoes the value id, and
// id_custom_get_value echoes command, channel and value id. Only those bytes
// may be compared -- matching further would reject a valid reply as foreign.
//
// The comparison is not optional. The VIA interface is opened without
// kIOHIDOptionsTypeSeizeDevice, so macOS hands every input report to every
// process that has the device open; with the daemon running, a scan that took
// the next report to arrive would routinely parse the daemon's answers as its
// own and report another channel's values under this one's name. See the same
// note in halo75_ledctl.c.
static bool exchange(IOHIDDeviceRef device, In *in, const uint8_t *request,
                     size_t echo_len, double timeout) {
    uint8_t out[REPORT_SIZE];
    memset(out, 0, REPORT_SIZE);
    memcpy(out, request, REPORT_SIZE);
    memset(in, 0, sizeof(*in));
    if (IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, 0, out, REPORT_SIZE)
            != kIOReturnSuccess) {
        return false;
    }

    CFAbsoluteTime deadline = CFAbsoluteTimeGetCurrent() + timeout;
    for (;;) {
        CFTimeInterval remaining = deadline - CFAbsoluteTimeGetCurrent();
        if (remaining <= 0) return false;
        in->received = false;
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, remaining, false);
        if (!in->received || in->result != kIOReturnSuccess) return false;
        if ((size_t)in->len < echo_len) continue;
        // Byte 0 is the command on a handled reply and 0xff on an unhandled
        // one, so it is compared separately; the rest must match exactly.
        if (in->buf[0] != request[0] && in->buf[0] != 0xff) continue;
        bool same = true;
        for (size_t i = 1; i < echo_len; i++) {
            if (in->buf[i] != request[i]) { same = false; break; }
        }
        if (same) return true;
    }
}

// True when the firmware handled the request. VIA echoes the request back with
// byte 0 set to 0xff for anything it does not implement, which is what makes a
// channel sweep meaningful.
static bool handled(const In *in) { return in->len > 0 && in->buf[0] != 0xff; }

static void scan_device(IOHIDDeviceRef device, bool deep, bool first) {
    char manufacturer[256], product[256], serial_present[8];
    prop_string(device, CFSTR(kIOHIDManufacturerKey), manufacturer, sizeof(manufacturer));
    prop_string(device, CFSTR(kIOHIDProductKey), product, sizeof(product));
    // The serial number itself is a device identifier we have no use for, so
    // only its presence is reported -- enough to tell two identical boards
    // apart in the UI without putting the value in a log or a JSON file.
    CFTypeRef serial = IOHIDDeviceGetProperty(device, CFSTR(kIOHIDSerialNumberKey));
    snprintf(serial_present, sizeof(serial_present), "%s", serial ? "true" : "false");

    long vendor = prop_num(device, CFSTR(kIOHIDVendorIDKey));
    long product_id = prop_num(device, CFSTR(kIOHIDProductIDKey));

    printf("%s\n    {\n", first ? "" : ",");
    printf("      \"vendor_id\": \"0x%04lx\",\n", vendor);
    printf("      \"product_id\": \"0x%04lx\",\n", product_id);
    printf("      \"manufacturer\": \"%s\",\n", manufacturer);
    printf("      \"product\": \"%s\",\n", product);
    printf("      \"has_serial\": %s,\n", serial_present);

    if (IOHIDDeviceOpen(device, kIOHIDOptionsTypeNone) != kIOReturnSuccess) {
        // Almost always VIA or a vendor configurator holding the interface
        // open: raw HID is exclusive, and the other app got there first.
        printf("      \"reachable\": false,\n");
        printf("      \"error\": \"could not open the VIA interface -- quit VIA or the "
               "vendor's own configurator app and try again\"\n    }");
        return;
    }

    In in = {0};
    uint8_t callback_buffer[REPORT_SIZE] = {0};
    IOHIDDeviceRegisterInputReportCallback(device, callback_buffer, sizeof(callback_buffer),
                                           input_cb, &in);
    IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);

    uint8_t request[REPORT_SIZE];

    memset(request, 0, REPORT_SIZE);
    request[0] = VIA_GET_PROTOCOL_VERSION;
    bool alive = exchange(device, &in, request, 1, 1.0);
    if (!alive) {
        printf("      \"reachable\": false,\n");
        printf("      \"error\": \"the VIA interface is there but did not answer\"\n    }");
        IOHIDDeviceClose(device, kIOHIDOptionsTypeNone);
        return;
    }
    printf("      \"reachable\": true,\n");
    printf("      \"via_protocol\": %d,\n", (in.buf[1] << 8) | in.buf[2]);

    memset(request, 0, REPORT_SIZE);
    request[0] = VIA_GET_KEYBOARD_VALUE;
    request[1] = KB_FIRMWARE_VERSION;
    if (exchange(device, &in, request, 2, 0.5) && handled(&in)) {
        unsigned long version = ((unsigned long)in.buf[2] << 24) | ((unsigned long)in.buf[3] << 16)
                              | ((unsigned long)in.buf[4] << 8) | in.buf[5];
        printf("      \"firmware_version\": %lu,\n", version);
    }

    memset(request, 0, REPORT_SIZE);
    request[0] = VIA_GET_KEYBOARD_VALUE;
    request[1] = KB_UPTIME;
    if (exchange(device, &in, request, 2, 0.5) && handled(&in)) {
        unsigned long uptime = ((unsigned long)in.buf[2] << 24) | ((unsigned long)in.buf[3] << 16)
                             | ((unsigned long)in.buf[4] << 8) | in.buf[5];
        printf("      \"uptime_ms\": %lu,\n", uptime);
    }

    printf("      \"lighting\": {");
    bool first_channel = true;
    for (size_t c = 0; c < CHANNEL_COUNT; c++) {
        memset(request, 0, REPORT_SIZE);
        request[0] = VIA_CUSTOM_GET_VALUE;
        request[1] = CHANNELS[c].id;
        request[2] = VALUES[0].id;                 // brightness: every channel has one
        if (!exchange(device, &in, request, 3, 0.5) || !handled(&in)) continue;

        printf("%s\n        \"%s\": {\n", first_channel ? "" : ",", CHANNELS[c].key);
        printf("          \"channel\": \"0x%02x\",\n", CHANNELS[c].id);
        printf("          \"description\": \"%s\",\n", CHANNELS[c].human);
        printf("          \"values\": {");
        bool first_value = true;
        if (CHANNELS[c].layout == LAYOUT_QMK) {
            for (size_t v = 0; v < VALUE_COUNT; v++) {
                memset(request, 0, REPORT_SIZE);
                request[0] = VIA_CUSTOM_GET_VALUE;
                request[1] = CHANNELS[c].id;
                request[2] = VALUES[v].id;
                if (!exchange(device, &in, request, 3, 0.5) || !handled(&in)) continue;
                // Colour is the one two-byte value (hue, sat); the rest use
                // byte 3 and leave byte 4 zero.
                if (VALUES[v].id == 0x04) {
                    printf("%s\n            \"hue\": %d,\n            \"sat\": %d",
                           first_value ? "" : ",", in.buf[3], in.buf[4]);
                } else {
                    printf("%s\n            \"%s\": %d", first_value ? "" : ",",
                           VALUES[v].key, in.buf[3]);
                }
                first_value = false;
            }
        } else if (CHANNELS[c].layout == LAYOUT_HALO_RING) {
            // One value id carrying the whole animation, not a set of
            // independent fields: mode, colour, speed, tail and brightness all
            // ride in a single reply. Offsets match halo75_ledctl's halo-get.
            uint8_t mode = in.buf[3];
            const char *name = mode < sizeof(HALO_MODES) / sizeof(HALO_MODES[0])
                             ? HALO_MODES[mode] : "?";
            printf("\n            \"mode\": \"%s\",\n", name);
            printf("            \"r\": %d, \"g\": %d, \"b\": %d,\n",
                   in.buf[4], in.buf[5], in.buf[6]);
            printf("            \"speed\": %d,\n            \"param\": %d,\n",
                   in.buf[7], in.buf[8]);
            printf("            \"brightness\": %d", in.buf[9]);
            first_value = false;
        } else {
            // Unknown vendor layout. Report the payload bytes and let a human
            // decide what they mean rather than inventing field names.
            printf("\n            \"raw\": \"");
            for (int b = 3; b < 11; b++) printf("%02x", in.buf[b]);
            printf("\"");
            first_value = false;
        }
        printf("%s          }\n        }", first_value ? "" : "\n");
        first_channel = false;
    }
    printf("%s      },\n", first_channel ? "" : "\n");

    // A board nobody has mapped may put its lighting on a channel outside the
    // list above. Sweeping the remaining channel ids is safe -- these are
    // arguments to 0x08, not command ids -- and it is the only way to find one.
    printf("      \"extra_channels\": [");
    bool first_extra = true;
    if (deep) {
        for (int id = 0; id <= 0xff; id++) {
            bool known = false;
            for (size_t c = 0; c < CHANNEL_COUNT; c++) {
                if (CHANNELS[c].id == id) { known = true; break; }
            }
            if (known) continue;
            memset(request, 0, REPORT_SIZE);
            request[0] = VIA_CUSTOM_GET_VALUE;
            request[1] = (uint8_t)id;
            request[2] = VALUES[0].id;
            if (!exchange(device, &in, request, 3, 0.3) || !handled(&in)) continue;
            printf("%s\"0x%02x\"", first_extra ? "" : ", ", id);
            first_extra = false;
        }
    }
    printf("],\n");
    printf("      \"deep_scan\": %s\n    }", deep ? "true" : "false");

    IOHIDDeviceClose(device, kIOHIDOptionsTypeNone);
}

int main(int argc, char **argv) {
    bool deep = false;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--deep") == 0) deep = true;
    }

    IOHIDManagerRef manager = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    // Matched by usage page rather than by vendor id: the point is to find
    // boards this project has never heard of. Only interfaces on VIA's own
    // page are ever opened, so the keyboard and mouse interfaces -- the ones
    // that would demand Input Monitoring permission -- are left alone.
    IOHIDManagerSetDeviceMatching(manager, NULL);
    IOHIDManagerOpen(manager, kIOHIDOptionsTypeNone);

    CFSetRef devices = IOHIDManagerCopyDevices(manager);
    printf("{\n  \"devices\": [");
    bool first = true;
    if (devices) {
        CFIndex count = CFSetGetCount(devices);
        if (count > 0) {
            IOHIDDeviceRef list[count];
            CFSetGetValues(devices, (const void **)list);
            for (CFIndex i = 0; i < count; i++) {
                if (prop_num(list[i], CFSTR(kIOHIDPrimaryUsagePageKey)) != VIA_USAGE_PAGE) continue;
                if (prop_num(list[i], CFSTR(kIOHIDPrimaryUsageKey)) != VIA_USAGE) continue;
                scan_device(list[i], deep, first);
                first = false;
            }
        }
        CFRelease(devices);
    }
    printf("%s  ]\n}\n", first ? "" : "\n");
    return first ? 1 : 0;      // exit 1 means "no VIA keyboard found"
}
