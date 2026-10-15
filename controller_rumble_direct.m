// controller_rumble_direct.m
//
// Buzzes the controller by writing Xbox Bluetooth rumble packets straight to
// its HID output report 3 with IOHIDDeviceSetReport - the same packet SDL
// sends, bypassing GameController/CoreHaptics entirely.
//
//   clang -fobjc-arc controller_rumble_direct.m -framework Foundation \
//         -framework IOKit -o build/controller_rumble_direct
//   ./build/controller_rumble_direct
//
// Alternates both motors full on / off six times, about 1.2 s apart. This is
// the ground-truth test for the firmware's rumble chain: if this vibrates but
// gc_rumble_test does not, the firmware path works and the caller (Apple
// haptics, or the game) is the problem; if neither vibrates, the firmware's
// output report plumbing is broken.
//
// Defaults: VID 0x045E PID 0x0B22 (the Xbox Elite identity the patch uses).
#import <Foundation/Foundation.h>
#import <IOKit/hid/IOHIDManager.h>
#import <IOKit/hid/IOHIDKeys.h>
#include <stdio.h>

static IOHIDDeviceRef g_dev = NULL;
static void dev_cb(void *ctx, IOReturn res, void *sender, IOHIDDeviceRef dev) {
    IOReturn r = IOHIDDeviceOpen(dev, kIOHIDOptionsTypeNone);
    g_dev = dev;
    printf("device matched (open %s)\n", r == kIOReturnSuccess ? "ok" : "failed"); fflush(stdout);
}
int main(int argc, char **argv) {
    @autoreleasepool {
        IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
        NSArray *m = @[ @{ @kIOHIDVendorIDKey: @(0x045E), @kIOHIDProductIDKey: @(0x0B22) } ];
        IOHIDManagerSetDeviceMatchingMultiple(mgr, (__bridge CFArrayRef)m);
        IOHIDManagerRegisterDeviceMatchingCallback(mgr, dev_cb, NULL);
        IOHIDManagerScheduleWithRunLoop(mgr, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
        IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone);
        for (int w = 0; !g_dev && w < 30; w++)
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
        if (!g_dev) { printf("no device\n"); return 1; }

        // SDL's Xbox BT rumble packet: [03 0F LT RT LOW HIGH FF 00 EB]
        for (int i = 0; i < 6; i++) {
            uint8_t pkt[9] = { 0x03, 0x0F, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x00, 0xEB };
            if (i % 2 == 0) { pkt[4] = 0xFF; pkt[5] = 0xFF; }  // both motors full
            IOReturn r = IOHIDDeviceSetReport(g_dev, kIOHIDReportTypeOutput, 0x03, pkt, sizeof pkt);
            printf("buzz %d: %s\n", i + 1, r == kIOReturnSuccess ? "sent ok" : "FAILED"); fflush(stdout);
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:1.2]];
        }
        printf("done\n");
    }
    return 0;
}
