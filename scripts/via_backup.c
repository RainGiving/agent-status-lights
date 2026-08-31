// SPDX-License-Identifier: MIT
// Read-only backup of the Halo65 V2's VIA dynamic keymap, taken before flashing.
// Sends only id_dynamic_keymap_get_layer_count (0x11) and
// id_dynamic_keymap_get_buffer (0x12) -- both reads. Writes the raw buffer to
// stdout as hex so it can be replayed later with id_dynamic_keymap_set_buffer.
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum { VID=0x19f5, PID=0x3315, UP=0xff60, US=0x61, RPT=32, CHUNK=28 };
typedef struct { bool received; IOReturn result; CFIndex len; uint8_t buf[RPT]; } In;

static long prop(IOHIDDeviceRef d, CFStringRef k){
    CFTypeRef v=IOHIDDeviceGetProperty(d,k); long r=-1;
    if(v&&CFGetTypeID(v)==CFNumberGetTypeID()) CFNumberGetValue((CFNumberRef)v,kCFNumberLongType,&r);
    return r;
}
static void cb(void*c,IOReturn res,void*s,IOHIDReportType t,uint32_t i,uint8_t*r,CFIndex n){
    (void)s;(void)t;(void)i; In*in=c; in->result=res; in->len=n>RPT?RPT:n;
    memcpy(in->buf,r,(size_t)in->len); in->received=true; CFRunLoopStop(CFRunLoopGetCurrent());
}
static bool ex(IOHIDDeviceRef d,In*in,const uint8_t*req,size_t echo){
    uint8_t o[RPT]; memset(o,0,RPT); memcpy(o,req,RPT); memset(in,0,sizeof(*in));
    if(IOHIDDeviceSetReport(d,kIOHIDReportTypeOutput,0,o,RPT)!=kIOReturnSuccess) return false;
    // The VIA interface is not opened exclusively, so macOS delivers every input
    // report to every process that has it open -- taking the next one to arrive
    // means parsing another process's answer as ours. Here that would put one
    // chunk's key codes at another chunk's offset, which the restore would then
    // write to the keyboard. VIA echoes the head of the request; match on it.
    // See docs/PROTOCOL.md, "Replies must be matched to requests".
    CFAbsoluteTime deadline=CFAbsoluteTimeGetCurrent()+1.5;
    for(;;){
        CFTimeInterval left=deadline-CFAbsoluteTimeGetCurrent();
        if(left<=0) return false;
        in->received=false;
        CFRunLoopRunInMode(kCFRunLoopDefaultMode,left,false);
        if(!in->received||in->result!=kIOReturnSuccess) return false;
        if((size_t)in->len<echo) continue;
        if(in->buf[0]==0xff) return false;
        if(in->buf[0]!=req[0]) continue;
        bool same=true;
        for(size_t i=1;i<echo;i++) if(in->buf[i]!=req[i]){ same=false; break; }
        if(same) return true;
    }
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
    CFSetRef set=IOHIDManagerCopyDevices(m); if(!set){fprintf(stderr,"not found\n");return 2;}
    CFIndex n=CFSetGetCount(set); IOHIDDeviceRef list[n]; CFSetGetValues(set,(const void**)list);
    IOHIDDeviceRef dev=NULL;
    for(CFIndex i=0;i<n;i++) if(prop(list[i],CFSTR(kIOHIDPrimaryUsagePageKey))==UP &&
                                prop(list[i],CFSTR(kIOHIDPrimaryUsageKey))==US) dev=list[i];
    if(!dev||IOHIDDeviceOpen(dev,kIOHIDOptionsTypeNone)!=kIOReturnSuccess){fprintf(stderr,"open failed\n");return 3;}
    In in={0}; uint8_t b[RPT]={0};
    IOHIDDeviceRegisterInputReportCallback(dev,b,sizeof(b),cb,&in);
    IOHIDDeviceScheduleWithRunLoop(dev,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);

    uint8_t req[RPT]={0x11};                       // id_dynamic_keymap_get_layer_count
    if(!ex(dev,&in,req,1)){fprintf(stderr,"layer count read failed\n");return 4;}
    int layers=in.buf[1];
    const int rows=6, cols=17;
    int total=layers*rows*cols*2;
    fprintf(stderr,"layers=%d rows=%d cols=%d  keymap buffer=%d bytes\n",layers,rows,cols,total);

    printf("# halo65_v2 VIA dynamic keymap backup\n");
    printf("# layers=%d rows=%d cols=%d bytes=%d\n",layers,rows,cols,total);
    for(int off=0; off<total; off+=CHUNK){
        int len = (total-off) < CHUNK ? (total-off) : CHUNK;
        uint8_t r2[RPT]={0x12,(uint8_t)(off>>8),(uint8_t)(off&0xff),(uint8_t)len};
        if(!ex(dev,&in,r2,4)){fprintf(stderr,"buffer read failed at offset %d\n",off);return 5;}
        printf("%04x:",off);
        for(int j=0;j<len;j++) printf(" %02x",in.buf[4+j]);
        printf("\n");
    }
    IOHIDDeviceClose(dev,kIOHIDOptionsTypeNone);
    fprintf(stderr,"backup complete, nothing was written to the keyboard\n");
    return 0;
}
