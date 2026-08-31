// Read-only dump of every value_id on the RGB Matrix channel, plus VIA keyboard
// values. Used to diff keyboard state before/after pressing a physical Fn key.
// Only id_custom_get_value (0x08) and id_get_keyboard_value (0x02) are sent --
// never a command id we have not verified, and never a write.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { VID=0x19f5, PID=0x3315, UP=0xff60, US=0x61, RPT=32 };
typedef struct { bool received; IOReturn result; uint8_t buf[RPT]; } In;

static long prop(IOHIDDeviceRef d, CFStringRef k){
    CFTypeRef v=IOHIDDeviceGetProperty(d,k); long r=-1;
    if(v&&CFGetTypeID(v)==CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v,kCFNumberLongType,&r);
    return r;
}
static void cb(void*c,IOReturn res,void*s,IOHIDReportType t,uint32_t i,uint8_t*r,CFIndex n){
    (void)s;(void)t;(void)i; In*in=c; in->result=res;
    memcpy(in->buf,r,(size_t)(n>RPT?RPT:n)); in->received=true; CFRunLoopStop(CFRunLoopGetCurrent());
}
static bool ex(IOHIDDeviceRef d,In*in,const uint8_t*req){
    uint8_t o[RPT]; memset(o,0,RPT); memcpy(o,req,RPT); memset(in,0,sizeof(*in));
    if(IOHIDDeviceSetReport(d,kIOHIDReportTypeOutput,0,o,RPT)!=kIOReturnSuccess) return false;
    CFRunLoopRunInMode(kCFRunLoopDefaultMode,0.35,false);
    return in->received && in->result==kIOReturnSuccess;
}
int main(void){
    IOHIDManagerRef m=IOHIDManagerCreate(kCFAllocatorDefault,kIOHIDOptionsTypeNone);
    int vid=VID,pid=PID;
    CFNumberRef vn=CFNumberCreate(kCFAllocatorDefault,kCFNumberIntType,&vid);
    CFNumberRef pn=CFNumberCreate(kCFAllocatorDefault,kCFNumberIntType,&pid);
    const void*k[]={CFSTR(kIOHIDVendorIDKey),CFSTR(kIOHIDProductIDKey)}; const void*v[]={vn,pn};
    IOHIDManagerSetDeviceMatching(m,CFDictionaryCreate(kCFAllocatorDefault,k,v,2,
        &kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks));
    IOHIDManagerOpen(m,kIOHIDOptionsTypeNone);
    CFSetRef set=IOHIDManagerCopyDevices(m); if(!set) return 2;
    CFIndex n=CFSetGetCount(set); IOHIDDeviceRef list[n]; CFSetGetValues(set,(const void**)list);
    IOHIDDeviceRef dev=NULL;
    for(CFIndex i=0;i<n;i++) if(prop(list[i],CFSTR(kIOHIDPrimaryUsagePageKey))==UP &&
                                prop(list[i],CFSTR(kIOHIDPrimaryUsageKey))==US) dev=list[i];
    if(!dev||IOHIDDeviceOpen(dev,kIOHIDOptionsTypeNone)!=kIOReturnSuccess) return 3;
    In in={0}; uint8_t b[RPT]={0};
    IOHIDDeviceRegisterInputReportCallback(dev,b,sizeof(b),cb,&in);
    IOHIDDeviceScheduleWithRunLoop(dev,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);

    for(int id=0; id<16; id++){
        uint8_t req[RPT]={0x02,(uint8_t)id};
        if(ex(dev,&in,req) && in.buf[0]!=0xff){
            bool nz=false; for(int j=2;j<10;j++) if(in.buf[j]) nz=true;
            if(nz){ printf("kbdvalue %02x :",id); for(int j=2;j<10;j++) printf(" %02x",in.buf[j]); printf("\n"); }
        }
    }
    for(int id=0; id<256; id++){
        uint8_t req[RPT]={0x08,0x03,(uint8_t)id};
        if(ex(dev,&in,req) && in.buf[0]!=0xff){
            bool nz=false; for(int j=3;j<11;j++) if(in.buf[j]) nz=true;
            if(nz){ printf("ch03 vid %02x :",id); for(int j=3;j<11;j++) printf(" %02x",in.buf[j]); printf("\n"); }
        }
    }
    IOHIDDeviceClose(dev,kIOHIDOptionsTypeNone);
    return 0;
}
