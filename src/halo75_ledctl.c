// SPDX-License-Identifier: MIT
// NuPhy Halo75 V2 RGB Matrix controller over the VIA raw HID channel (macOS).
// Only volatile id_custom_set_value writes are sent; id_custom_save is never used,
// so nothing is persisted to keyboard EEPROM.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    HALO75_VID = 0x19f5,
    HALO75_PID = 0x32f5,
    VIA_USAGE_PAGE = 0xff60,
    VIA_USAGE = 0x61,
    REPORT_SIZE = 32,

    VIA_CUSTOM_SET_VALUE = 0x07,
    VIA_CUSTOM_GET_VALUE = 0x08,
    CH_RGB_MATRIX = 0x03,
    CH_HALO = 0x10,        /* vendor channel added by firmware/halo-host-control.patch */
    HALO_ANIMATION = 0x01,
    RGB_BRIGHTNESS = 0x01,
    RGB_EFFECT = 0x02,
    RGB_SPEED = 0x03,
    RGB_COLOR = 0x04,
};

typedef struct { bool received; IOReturn result; CFIndex len; uint8_t buf[REPORT_SIZE]; } In;

static long prop(IOHIDDeviceRef d, CFStringRef k) {
    CFTypeRef v = IOHIDDeviceGetProperty(d, k);
    long r = -1;
    if (v && CFGetTypeID(v) == CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v, kCFNumberLongType, &r);
    return r;
}

static void input_cb(void *ctx, IOReturn res, void *s, IOHIDReportType t,
                     uint32_t rid, uint8_t *r, CFIndex n) {
    (void)s; (void)t; (void)rid;
    In *in = ctx;
    in->result = res;
    in->len = n > REPORT_SIZE ? REPORT_SIZE : n;
    memcpy(in->buf, r, (size_t)in->len);
    in->received = true;
    CFRunLoopStop(CFRunLoopGetCurrent());
}

// Sends one 32-byte VIA report and waits for the reply. Returns false on
// timeout, transport error, or a 0xff "unsupported command" echo.
static bool via_exchange(IOHIDDeviceRef dev, In *in, const uint8_t *req) {
    uint8_t out[REPORT_SIZE];
    memset(out, 0, REPORT_SIZE);
    memcpy(out, req, REPORT_SIZE);
    memset(in, 0, sizeof(*in));
    if (IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, 0, out, REPORT_SIZE) != kIOReturnSuccess) {
        fprintf(stderr, "via write failed\n");
        return false;
    }
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 1.5, false);
    if (!in->received || in->result != kIOReturnSuccess) {
        fprintf(stderr, "via read timed out\n");
        return false;
    }
    if (in->buf[0] == 0xff) {
        fprintf(stderr, "via command unsupported by firmware\n");
        return false;
    }
    return true;
}

static bool rgb_get(IOHIDDeviceRef dev, In *in, uint8_t value_id, uint8_t out[2]) {
    uint8_t req[REPORT_SIZE] = {VIA_CUSTOM_GET_VALUE, CH_RGB_MATRIX, value_id};
    if (!via_exchange(dev, in, req)) return false;
    out[0] = in->buf[3];
    out[1] = in->buf[4];
    return true;
}

static bool rgb_set(IOHIDDeviceRef dev, In *in, uint8_t value_id, uint8_t a, uint8_t b) {
    // This firmware stores brightness one step below what VIA is told: writing
    // X reads back as X-1, measured across the whole 0-255 range. Left alone,
    // every save/restore cycle would dim the keyboard by one step forever, so
    // brightness writes are pre-compensated to make a read-back round-trip
    // stable. 255 is simply unreachable; 254 is the ceiling.
    if (value_id == RGB_BRIGHTNESS && a > 0 && a < 255) a = (uint8_t)(a + 1);
    uint8_t req[REPORT_SIZE] = {VIA_CUSTOM_SET_VALUE, CH_RGB_MATRIX, value_id, a, b};
    return via_exchange(dev, in, req);
}

static IOHIDDeviceRef find_via_device(IOHIDManagerRef mgr) {
    int vid = HALO75_VID, pid = HALO75_PID;
    CFNumberRef vn = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &vid);
    CFNumberRef pn = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &pid);
    const void *keys[] = {CFSTR(kIOHIDVendorIDKey), CFSTR(kIOHIDProductIDKey)};
    const void *vals[] = {vn, pn};
    CFDictionaryRef match = CFDictionaryCreate(kCFAllocatorDefault, keys, vals, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    IOHIDManagerSetDeviceMatching(mgr, match);
    IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);
    CFSetRef set = IOHIDManagerCopyDevices(mgr);
    if (!set) return NULL;
    CFIndex n = CFSetGetCount(set);
    if (n <= 0) return NULL;
    IOHIDDeviceRef list[n];
    CFSetGetValues(set, (const void **)list);
    for (CFIndex i = 0; i < n; i++) {
        if (prop(list[i], CFSTR(kIOHIDPrimaryUsagePageKey)) == VIA_USAGE_PAGE &&
            prop(list[i], CFSTR(kIOHIDPrimaryUsageKey)) == VIA_USAGE) {
            return list[i];
        }
    }
    return NULL;
}

/* Accepts a mode name or its number. Keep in step with enum halo_host_mode. */
static const char *HALO_MODES[] = {"release", "solid", "pulse", "comet", "strobe", "fill"};

static bool parse_halo_mode(const char *text, uint8_t *out) {
    for (size_t i = 0; i < sizeof(HALO_MODES) / sizeof(HALO_MODES[0]); i++) {
        if (strcmp(text, HALO_MODES[i]) == 0) { *out = (uint8_t)i; return true; }
    }
    char *end = NULL;
    long v = strtol(text, &end, 0);
    if (!end || *end != '\0' || v < 0 || v > 5) return false;
    *out = (uint8_t)v;
    return true;
}

static bool parse_u8(const char *text, uint8_t *out) {
    char *end = NULL;
    long v = strtol(text, &end, 0);
    if (!end || *end != '\0' || v < 0 || v > 255) return false;
    *out = (uint8_t)v;
    return true;
}

static void usage(void) {
    fprintf(stderr,
        "usage:\n"
        "  halo75_ledctl get\n"
        "  halo75_ledctl set <effect> <hue> <sat> <brightness>\n"
        "  halo75_ledctl color <hue> <sat> <brightness>\n"
        "  halo75_ledctl restore <effect> <hue> <sat> <brightness> <speed>\n"
        "\n"
        "  halo75_ledctl halo <mode> <r> <g> <b> <speed> <param> <brightness>\n"
        "  halo75_ledctl halo-get\n"
        "      mode: release|solid|pulse|comet|strobe|fill\n"
        "      param: comet tail length in LEDs, or strobe duty cycle percent\n"
        "      needs the halo-host-control firmware patch; stock firmware answers\n"
        "      \"unsupported\"\n");
}

int main(int argc, char **argv) {
    if (argc < 2) { usage(); return 64; }
    const char *op = argv[1];

    IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    IOHIDDeviceRef dev = find_via_device(mgr);
    if (!dev) { fprintf(stderr, "Halo75 V2 VIA interface not found (USB cable connected?)\n"); return 2; }
    if (IOHIDDeviceOpen(dev, kIOHIDOptionsTypeNone) != kIOReturnSuccess) {
        fprintf(stderr, "VIA raw HID open failed (quit VIA / NuPhy Console and retry)\n");
        return 3;
    }
    In in = {0};
    uint8_t cbbuf[REPORT_SIZE] = {0};
    IOHIDDeviceRegisterInputReportCallback(dev, cbbuf, sizeof(cbbuf), input_cb, &in);
    IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);

    int rc = 0;
    if (strcmp(op, "get") == 0) {
        uint8_t bright[2], effect[2], speed[2], color[2];
        if (!rgb_get(dev, &in, RGB_BRIGHTNESS, bright) ||
            !rgb_get(dev, &in, RGB_EFFECT, effect) ||
            !rgb_get(dev, &in, RGB_SPEED, speed) ||
            !rgb_get(dev, &in, RGB_COLOR, color)) {
            rc = 5;
        } else {
            printf("EFFECT=%u HUE=%u SAT=%u VAL=%u SPEED=%u\n",
                   effect[0], color[0], color[1], bright[0], speed[0]);
        }
    } else if (strcmp(op, "set") == 0 && argc == 6) {
        uint8_t effect, hue, sat, val;
        if (!parse_u8(argv[2], &effect) || !parse_u8(argv[3], &hue) ||
            !parse_u8(argv[4], &sat) || !parse_u8(argv[5], &val)) { usage(); rc = 64; }
        else if (!rgb_set(dev, &in, RGB_EFFECT, effect, 0) ||
                 !rgb_set(dev, &in, RGB_COLOR, hue, sat) ||
                 !rgb_set(dev, &in, RGB_BRIGHTNESS, val, 0)) { rc = 5; }
    } else if (strcmp(op, "color") == 0 && argc == 5) {
        uint8_t hue, sat, val;
        if (!parse_u8(argv[2], &hue) || !parse_u8(argv[3], &sat) ||
            !parse_u8(argv[4], &val)) { usage(); rc = 64; }
        else if (!rgb_set(dev, &in, RGB_COLOR, hue, sat) ||
                 !rgb_set(dev, &in, RGB_BRIGHTNESS, val, 0)) { rc = 5; }
    } else if (strcmp(op, "restore") == 0 && argc == 7) {
        uint8_t effect, hue, sat, val, speed;
        if (!parse_u8(argv[2], &effect) || !parse_u8(argv[3], &hue) ||
            !parse_u8(argv[4], &sat) || !parse_u8(argv[5], &val) ||
            !parse_u8(argv[6], &speed)) { usage(); rc = 64; }
        else if (!rgb_set(dev, &in, RGB_EFFECT, effect, 0) ||
                 !rgb_set(dev, &in, RGB_COLOR, hue, sat) ||
                 !rgb_set(dev, &in, RGB_BRIGHTNESS, val, 0) ||
                 !rgb_set(dev, &in, RGB_SPEED, speed, 0)) { rc = 5; }
    } else if (strcmp(op, "halo") == 0 && argc == 9) {
        uint8_t mode, r, g, b, speed, param, bright;
        if (!parse_halo_mode(argv[2], &mode) || !parse_u8(argv[3], &r) ||
            !parse_u8(argv[4], &g) || !parse_u8(argv[5], &b) ||
            !parse_u8(argv[6], &speed) || !parse_u8(argv[7], &param) ||
            !parse_u8(argv[8], &bright)) { usage(); rc = 64; }
        else {
            uint8_t req[REPORT_SIZE] = {VIA_CUSTOM_SET_VALUE, CH_HALO, HALO_ANIMATION,
                                        mode, r, g, b, speed, param, bright};
            if (!via_exchange(dev, &in, req)) rc = 5;
        }
    } else if (strcmp(op, "halo-get") == 0) {
        uint8_t req[REPORT_SIZE] = {VIA_CUSTOM_GET_VALUE, CH_HALO, HALO_ANIMATION};
        if (!via_exchange(dev, &in, req)) {
            rc = 5;
        } else {
            uint8_t mode = in.buf[3];
            printf("MODE=%s R=%u G=%u B=%u SPEED=%u PARAM=%u BRIGHT=%u\n",
                   mode < sizeof(HALO_MODES) / sizeof(HALO_MODES[0]) ? HALO_MODES[mode] : "?",
                   in.buf[4], in.buf[5], in.buf[6], in.buf[7], in.buf[8], in.buf[9]);
        }
    } else {
        usage();
        rc = 64;
    }

    IOHIDDeviceClose(dev, kIOHIDOptionsTypeNone);
    return rc;
}
