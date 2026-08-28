// Read-only VIA probe for NuPhy Halo75 V2. Sends only get/query commands.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { VID = 0x19f5, PID = 0x32f5, USAGE_PAGE = 0xff60, USAGE = 0x61, RPT = 32 };

typedef struct { bool received; IOReturn result; CFIndex len; uint8_t buf[RPT]; } In;

static long prop(IOHIDDeviceRef d, CFStringRef k) {
    CFTypeRef v = IOHIDDeviceGetProperty(d, k); long r = -1;
    if (v && CFGetTypeID(v) == CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v, kCFNumberLongType, &r);
    return r;
}
static void cb(void *ctx, IOReturn res, void *s, IOHIDReportType t, uint32_t rid, uint8_t *r, CFIndex n) {
    (void)s; (void)t; (void)rid;
    In *in = ctx; in->result = res; in->len = n > RPT ? RPT : n;
    memcpy(in->buf, r, (size_t)in->len); in->received = true;
    CFRunLoopStop(CFRunLoopGetCurrent());
}
static void hex(const uint8_t *b, size_t n) { for (size_t i = 0; i < n; i++) printf("%02x ", b[i]); printf("\n"); }

static bool xchg(IOHIDDeviceRef d, In *in, const uint8_t *req, const char *label) {
    uint8_t out[RPT]; memset(out, 0, RPT); memcpy(out, req, RPT);
    memset(in, 0, sizeof(*in));
    printf("%-34s TX  ", label); hex(out, 8);
    IOReturn w = IOHIDDeviceSetReport(d, kIOHIDReportTypeOutput, 0, out, RPT);
    if (w != kIOReturnSuccess) { printf("%-34s ERR write 0x%08x\n", "", w); return false; }
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 1.5, false);
    if (!in->received) { printf("%-34s ERR timeout\n", ""); return false; }
    printf("%-34s RX  ", ""); hex(in->buf, 12);
    return true;
}

int main(void) {
    IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    int vid = VID, pid = PID;
    CFNumberRef vn = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &vid);
    CFNumberRef pn = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &pid);
    const void *k[] = {CFSTR(kIOHIDVendorIDKey), CFSTR(kIOHIDProductIDKey)};
    const void *v[] = {vn, pn};
    CFDictionaryRef match = CFDictionaryCreate(kCFAllocatorDefault, k, v, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    IOHIDManagerSetDeviceMatching(mgr, match);
    IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);

    CFSetRef set = IOHIDManagerCopyDevices(mgr);
    if (!set) { fprintf(stderr, "Halo75 V2 not found\n"); return 2; }
    CFIndex n = CFSetGetCount(set);
    IOHIDDeviceRef list[n]; CFSetGetValues(set, (const void **)list);
    IOHIDDeviceRef dev = NULL;
    printf("=== HID interfaces for 19f5:32f5 ===\n");
    for (CFIndex i = 0; i < n; i++) {
        long up = prop(list[i], CFSTR(kIOHIDPrimaryUsagePageKey));
        long u  = prop(list[i], CFSTR(kIOHIDPrimaryUsageKey));
        long mi = prop(list[i], CFSTR(kIOHIDMaxInputReportSizeKey));
        long mo = prop(list[i], CFSTR(kIOHIDMaxOutputReportSizeKey));
        printf("  usagePage=0x%04lx usage=0x%02lx  in=%ld out=%ld%s\n", up, u, mi, mo,
               (up == USAGE_PAGE && u == USAGE) ? "   <-- VIA raw HID" : "");
        if (up == USAGE_PAGE && u == USAGE) dev = list[i];
    }
    if (!dev) { fprintf(stderr, "VIA raw HID interface (0xFF60/0x61) not found\n"); return 3; }

    IOReturn o = IOHIDDeviceOpen(dev, kIOHIDOptionsTypeNone);
    if (o != kIOReturnSuccess) { fprintf(stderr, "open failed: 0x%08x\n", o); return 4; }
    In in = {0}; uint8_t cbbuf[RPT] = {0};
    IOHIDDeviceRegisterInputReportCallback(dev, cbbuf, sizeof(cbbuf), cb, &in);
    IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);

    printf("\n=== VIA read-only queries ===\n");
    uint8_t r[RPT];
    #define REQ(lbl, ...) do { memset(r,0,RPT); uint8_t t[]={__VA_ARGS__}; memcpy(r,t,sizeof(t)); xchg(dev,&in,r,lbl); } while(0)

    REQ("protocol_version",              0x01);
    REQ("kbd_value: uptime",             0x02, 0x01);
    REQ("kbd_value: layout_options",     0x02, 0x02);
    REQ("kbd_value: firmware_version",   0x02, 0x04);

    printf("\n--- channel 0x01 = backlight ---\n");
    REQ("backlight brightness",          0x08, 0x01, 0x01);
    REQ("backlight effect",              0x08, 0x01, 0x02);

    printf("\n--- channel 0x02 = RGBLIGHT (underglow / bottom strip) ---\n");
    REQ("rgblight brightness",           0x08, 0x02, 0x01);
    REQ("rgblight effect",               0x08, 0x02, 0x02);
    REQ("rgblight effect speed",         0x08, 0x02, 0x03);
    REQ("rgblight color (hue,sat)",      0x08, 0x02, 0x04);

    printf("\n--- channel 0x03 = RGB MATRIX (per-key backlight) ---\n");
    REQ("rgbmatrix brightness",          0x08, 0x03, 0x01);
    REQ("rgbmatrix effect",              0x08, 0x03, 0x02);
    REQ("rgbmatrix effect speed",        0x08, 0x03, 0x03);
    REQ("rgbmatrix color (hue,sat)",     0x08, 0x03, 0x04);

    printf("\n--- channel 0x00 = vendor custom (NuPhy?) ---\n");
    REQ("custom ch0 id0",                0x08, 0x00, 0x00);
    REQ("custom ch0 id1",                0x08, 0x00, 0x01);
    REQ("custom ch0 id2",                0x08, 0x00, 0x02);

    IOHIDDeviceClose(dev, kIOHIDOptionsTypeNone);
    printf("\ndone (nothing was written to the keyboard)\n");
    return 0;
}
