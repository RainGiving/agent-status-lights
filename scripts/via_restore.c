// SPDX-License-Identifier: MIT
// Restores a via_backup dump into the keyboard's VIA dynamic keymap.
// Uses id_dynamic_keymap_set_buffer (0x13) only -- it never touches
// id_eeprom_reset (0x0A) or id_bootloader_jump (0x0B).
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { VID=0x19f5, PID=0x3315, UP=0xff60, US=0x61, RPT=32, MAXBUF=8192 };
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

int main(int argc, char **argv){
    if(argc!=2){ fprintf(stderr,"usage: via_restore <backup.txt>\n"); return 64; }
    FILE *f=fopen(argv[1],"r");
    if(!f){ fprintf(stderr,"cannot open %s\n",argv[1]); return 64; }

    static uint8_t data[MAXBUF];
    int total=0;
    char line[512];
    while(fgets(line,sizeof(line),f)){
        if(line[0]=='#') continue;
        char *colon=strchr(line,':');
        if(!colon) continue;
        int off=(int)strtol(line,NULL,16);
        char *p=colon+1;
        int n=0;
        while(*p){
            while(*p==' '||*p=='\n') p++;
            if(!*p) break;
            unsigned int byte;
            if(sscanf(p,"%2x",&byte)!=1) break;
            if(off+n>=MAXBUF){ fprintf(stderr,"backup too large\n"); return 65; }
            data[off+n]=(uint8_t)byte; n++;
            p+=2;
        }
        if(off+n>total) total=off+n;
    }
    fclose(f);
    fprintf(stderr,"parsed %d bytes from %s\n",total,argv[1]);
    if(total==0){ fprintf(stderr,"nothing to restore\n"); return 65; }

    IOHIDManagerRef m=IOHIDManagerCreate(kCFAllocatorDefault,kIOHIDOptionsTypeNone);
    int vid=VID,pid=PID;
    CFNumberRef vn=CFNumberCreate(kCFAllocatorDefault,kCFNumberIntType,&vid);
    CFNumberRef pn=CFNumberCreate(kCFAllocatorDefault,kCFNumberIntType,&pid);
    const void*k[]={CFSTR(kIOHIDVendorIDKey),CFSTR(kIOHIDProductIDKey)}; const void*v[]={vn,pn};
    IOHIDManagerSetDeviceMatching(m,CFDictionaryCreate(kCFAllocatorDefault,k,v,2,
        &kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks));
    IOHIDManagerOpen(m,kIOHIDOptionsTypeNone);
    CFSetRef set=IOHIDManagerCopyDevices(m); if(!set){fprintf(stderr,"keyboard not found\n");return 2;}
    CFIndex n=CFSetGetCount(set); IOHIDDeviceRef list[n]; CFSetGetValues(set,(const void**)list);
    IOHIDDeviceRef dev=NULL;
    for(CFIndex i=0;i<n;i++) if(prop(list[i],CFSTR(kIOHIDPrimaryUsagePageKey))==UP &&
                                prop(list[i],CFSTR(kIOHIDPrimaryUsageKey))==US) dev=list[i];
    if(!dev||IOHIDDeviceOpen(dev,kIOHIDOptionsTypeNone)!=kIOReturnSuccess){fprintf(stderr,"open failed\n");return 3;}
    In in={0}; uint8_t b[RPT]={0};
    IOHIDDeviceRegisterInputReportCallback(dev,b,sizeof(b),cb,&in);
    IOHIDDeviceScheduleWithRunLoop(dev,CFRunLoopGetCurrent(),kCFRunLoopDefaultMode);

    const int CHUNK=28;
    for(int off=0; off<total; off+=CHUNK){
        int len=(total-off)<CHUNK?(total-off):CHUNK;
        uint8_t req[RPT]={0x13,(uint8_t)(off>>8),(uint8_t)(off&0xff),(uint8_t)len};
        memcpy(req+4,data+off,(size_t)len);
        if(!ex(dev,&in,req,4)){ fprintf(stderr,"write failed at offset %d\n",off); return 5; }
    }
    IOHIDDeviceClose(dev,kIOHIDOptionsTypeNone);
    fprintf(stderr,"restored %d bytes\n",total);
    return 0;
}
