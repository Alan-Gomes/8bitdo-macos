#define _DARWIN_C_SOURCE
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDLib.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum {
    VENDOR_8BITDO = 0x2dc8,
    PID_ULTIMATE_2C_BOOT = 0x3208,
    REPORT_ID_BOOT = 5,
    REPORT_SIZE = 64,
    BOOT_PAYLOAD_OFFSET = 18,
    BOOT_MAX_READ = REPORT_SIZE - BOOT_PAYLOAD_OFFSET
};

typedef struct {
    uint8_t buf[REPORT_SIZE];
    CFIndex len;
    int ready;
    IOReturn result;
} input_state_t;

static void set_u16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)(v >> 8);
}

static void set_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)((v >> 8) & 0xff);
    p[2] = (uint8_t)((v >> 16) & 0xff);
    p[3] = (uint8_t)((v >> 24) & 0xff);
}

static uint16_t get_u16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static CFMutableDictionaryRef match_dict(int vid, int pid) {
    CFMutableDictionaryRef dict =
        CFDictionaryCreateMutable(kCFAllocatorDefault, 0,
                                  &kCFTypeDictionaryKeyCallBacks,
                                  &kCFTypeDictionaryValueCallBacks);
    CFNumberRef vid_ref = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &vid);
    CFNumberRef pid_ref = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &pid);
    CFDictionarySetValue(dict, CFSTR(kIOHIDVendorIDKey), vid_ref);
    CFDictionarySetValue(dict, CFSTR(kIOHIDProductIDKey), pid_ref);
    CFRelease(vid_ref);
    CFRelease(pid_ref);
    return dict;
}

static void input_callback(void *context, IOReturn result, void *sender,
                           IOHIDReportType type, uint32_t report_id,
                           uint8_t *report, CFIndex report_len) {
    (void)sender;
    (void)type;
    (void)report_id;
    input_state_t *state = (input_state_t *)context;
    state->result = result;
    state->len = report_len > REPORT_SIZE ? REPORT_SIZE : report_len;
    memcpy(state->buf, report, (size_t)state->len);
    state->ready = 1;
}

static IOHIDDeviceRef open_boot_device(IOHIDManagerRef *out_manager) {
    IOHIDManagerRef manager = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    CFMutableDictionaryRef dict = match_dict(VENDOR_8BITDO, PID_ULTIMATE_2C_BOOT);
    IOHIDManagerSetDeviceMatching(manager, dict);
    CFRelease(dict);
    IOHIDManagerOpen(manager, kIOHIDOptionsTypeNone);
    CFSetRef devices = IOHIDManagerCopyDevices(manager);
    if (!devices || CFSetGetCount(devices) == 0) {
        if (devices) CFRelease(devices);
        CFRelease(manager);
        return NULL;
    }
    IOHIDDeviceRef device = NULL;
    CFSetGetValues(devices, (const void **)&device);
    CFRetain(device);
    CFRelease(devices);
    IOReturn open_result = IOHIDDeviceOpen(device, kIOHIDOptionsTypeNone);
    if (open_result != kIOReturnSuccess) {
        fprintf(stderr, "IOHIDDeviceOpen failed: 0x%08x\n", open_result);
        CFRelease(device);
        CFRelease(manager);
        return NULL;
    }
    IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    *out_manager = manager;
    return device;
}

static IOReturn send_boot_command(IOHIDDeviceRef device, uint16_t cmd, uint16_t cmd_params,
                                  uint16_t len, uint32_t total_len, uint32_t offset,
                                  input_state_t *state) {
    uint8_t report[REPORT_SIZE];
    memset(report, 0, sizeof(report));
    report[0] = REPORT_ID_BOOT;
    set_u16(report + 1, cmd);
    set_u16(report + 3, cmd_params);
    set_u16(report + 5, len);
    set_u16(report + 7, 0);
    set_u32(report + 9, total_len);
    set_u32(report + 13, offset);

    state->ready = 0;
    state->len = 0;
    memset(state->buf, 0, sizeof(state->buf));

    IOReturn result = IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, REPORT_ID_BOOT,
                                           report, sizeof(report));
    if (result != kIOReturnSuccess) {
        return result;
    }

    for (int i = 0; i < 500 && !state->ready; i++) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
    }
    return state->ready ? state->result : kIOReturnTimeout;
}

static int boot_read(IOHIDDeviceRef device, input_state_t *state, uint32_t address,
                     uint8_t *out, uint16_t len) {
    if (len > BOOT_MAX_READ) len = BOOT_MAX_READ;
    IOReturn result = send_boot_command(device, 5, 0, len, len, address, state);
    if (result != kIOReturnSuccess) {
        fprintf(stderr, "read 0x%08x len %u failed: 0x%08x\n", address, len, result);
        return -1;
    }
    if (state->len < BOOT_PAYLOAD_OFFSET + len) {
        fprintf(stderr, "short response at 0x%08x: %ld bytes\n", address, (long)state->len);
        return -1;
    }
    uint16_t response_cmd = get_u16(state->buf + 2);
    uint16_t response_param = get_u16(state->buf + 4);
    if (response_cmd != 0 || response_param != 5) {
        fprintf(stderr, "unexpected read response at 0x%08x: cmd=%u param=%u\n",
                address, response_cmd, response_param);
        return -1;
    }
    memcpy(out, state->buf + BOOT_PAYLOAD_OFFSET, len);
    return len;
}

int main(int argc, char **argv) {
    const uint32_t default_address = 0x6000;
    const uint32_t default_length = 0x12400;
    const char *default_output = "ultimate2c-flash-backup-0x6000-0x12400.bin";

    uint32_t address = argc > 1 ? (uint32_t)strtoul(argv[1], NULL, 0) : default_address;
    uint32_t length = argc > 2 ? (uint32_t)strtoul(argv[2], NULL, 0) : default_length;
    const char *output_path = argc > 3 ? argv[3] : default_output;

    IOHIDManagerRef manager = NULL;
    IOHIDDeviceRef device = open_boot_device(&manager);
    if (!device) {
        fprintf(stderr, "8BitDo Ultimate 2C BOOT device not found.\n");
        return 1;
    }

    input_state_t state;
    memset(&state, 0, sizeof(state));
    uint8_t input_buffer[REPORT_SIZE];
    memset(input_buffer, 0, sizeof(input_buffer));
    IOHIDDeviceRegisterInputReportCallback(device, input_buffer, sizeof(input_buffer),
                                           input_callback, &state);

    IOReturn id_result = send_boot_command(device, 8, 0, 4, 4, 0, &state);
    if (id_result != kIOReturnSuccess) {
        fprintf(stderr, "identify command failed: 0x%08x\n", id_result);
        return 1;
    }
    uint16_t pid = get_u16(state.buf + BOOT_PAYLOAD_OFFSET);
    printf("BOOT identify PID: 0x%04x\n", pid);

    uint8_t version_bytes[8];
    memset(version_bytes, 0, sizeof(version_bytes));
    if (boot_read(device, &state, 0x01017040, version_bytes, sizeof(version_bytes)) > 0) {
        printf("Version bytes @0x01017040:");
        for (size_t i = 0; i < sizeof(version_bytes); i++) printf(" %02x", version_bytes[i]);
        printf("  main=%u beta=%u\n", get_u16(version_bytes), get_u16(version_bytes + 4));
    }

    FILE *out = fopen(output_path, "wb");
    if (!out) {
        perror(output_path);
        return 1;
    }

    uint8_t chunk[BOOT_MAX_READ];
    uint32_t done = 0;
    while (done < length) {
        uint16_t want = (uint16_t)((length - done) < BOOT_MAX_READ ? (length - done) : BOOT_MAX_READ);
        int got = boot_read(device, &state, address + done, chunk, want);
        if (got <= 0) {
            fclose(out);
            return 1;
        }
        fwrite(chunk, 1, (size_t)got, out);
        done += (uint32_t)got;
        if ((done % 4094) < BOOT_MAX_READ || done == length) {
            printf("read %u / %u bytes\r", done, length);
            fflush(stdout);
        }
    }
    printf("\nSaved %u bytes to %s\n", length, output_path);
    fclose(out);

    IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    IOHIDDeviceClose(device, kIOHIDOptionsTypeNone);
    CFRelease(device);
    CFRelease(manager);
    return 0;
}
