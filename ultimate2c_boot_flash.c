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
    BOOT_MAX_READ = REPORT_SIZE - BOOT_PAYLOAD_OFFSET,
    BOOT_MAX_WRITE = 46,
    DAT_WRAPPER_SIZE = 0x1C
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

static uint16_t crc16_modbus(const uint8_t *data, size_t len) {
    uint16_t crc = 0xffff;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc & 1) ? (uint16_t)((crc >> 1) ^ 0xa001) : (uint16_t)(crc >> 1);
        }
    }
    return crc;
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

static IOReturn send_boot_command_ex(IOHIDDeviceRef device, uint8_t first_byte,
                                     uint16_t cmd, uint16_t cmd_params, uint16_t len,
                                     uint16_t crc, uint32_t total_len, uint32_t offset,
                                     const uint8_t *payload, uint16_t payload_len,
                                     int wait_response, input_state_t *state) {
    uint8_t report[REPORT_SIZE];
    memset(report, 0, sizeof(report));
    report[0] = first_byte;
    set_u16(report + 1, cmd);
    set_u16(report + 3, cmd_params);
    set_u16(report + 5, len);
    set_u16(report + 7, crc);
    set_u32(report + 9, total_len);
    set_u32(report + 13, offset);
    if (payload && payload_len) {
        if (payload_len > BOOT_MAX_WRITE) return kIOReturnBadArgument;
        memcpy(report + 17, payload, payload_len);
    }

    state->ready = 0;
    state->len = 0;
    memset(state->buf, 0, sizeof(state->buf));

    IOReturn result = IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, first_byte,
                                           report, sizeof(report));
    if (result != kIOReturnSuccess) {
        return result;
    }
    if (!wait_response) {
        return kIOReturnSuccess;
    }

    for (int i = 0; i < 500 && !state->ready; i++) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, true);
    }
    return state->ready ? state->result : kIOReturnTimeout;
}

static IOReturn send_boot_command(IOHIDDeviceRef device, uint16_t cmd, uint16_t cmd_params,
                                  uint16_t len, uint32_t total_len, uint32_t offset,
                                  input_state_t *state) {
    return send_boot_command_ex(device, REPORT_ID_BOOT, cmd, cmd_params, len, 0, total_len,
                                offset, NULL, 0, 1, state);
}

static int expect_ack(input_state_t *state, uint16_t expected_param, const char *label) {
    if (state->len < BOOT_PAYLOAD_OFFSET) {
        fprintf(stderr, "%s short ack: %ld bytes\n", label, (long)state->len);
        return -1;
    }
    uint16_t response_cmd = get_u16(state->buf + 2);
    uint16_t response_param = get_u16(state->buf + 4);
    if (response_cmd != 0 || response_param != expected_param) {
        fprintf(stderr, "%s unexpected ack: cmd=%u param=%u\n", label, response_cmd,
                response_param);
        return -1;
    }
    return 0;
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

static int boot_erase(IOHIDDeviceRef device, input_state_t *state, uint32_t address,
                      uint32_t len) {
    IOReturn result = send_boot_command_ex(device, REPORT_ID_BOOT, 4, 0, 0, 0, len, address,
                                           NULL, 0, 1, state);
    if (result != kIOReturnSuccess) {
        fprintf(stderr, "erase 0x%08x len %u failed: 0x%08x\n", address, len, result);
        return -1;
    }
    return expect_ack(state, 4, "erase");
}

static int boot_write(IOHIDDeviceRef device, input_state_t *state, uint32_t address,
                      const uint8_t *data, uint32_t len, uint32_t total_len) {
    uint32_t done = 0;
    while (done < len) {
        uint16_t chunk_len = (uint16_t)((len - done) < BOOT_MAX_WRITE ? (len - done) : BOOT_MAX_WRITE);
        uint16_t crc = crc16_modbus(data + done, chunk_len);
        IOReturn result = send_boot_command_ex(device, REPORT_ID_BOOT, 3, 0, chunk_len, crc,
                                               total_len, address + done, data + done,
                                               chunk_len, 1, state);
        if (result != kIOReturnSuccess) {
            fprintf(stderr, "write 0x%08x len %u failed: 0x%08x\n", address + done,
                    chunk_len, result);
            return -1;
        }
        if (expect_ack(state, 3, "write") != 0) {
            return -1;
        }
        done += chunk_len;
        if ((done % 4094) < BOOT_MAX_WRITE || done == len) {
            printf("wrote %u / %u bytes\r", done, len);
            fflush(stdout);
        }
    }
    printf("\n");
    return 0;
}

static int verify_flash(IOHIDDeviceRef device, input_state_t *state, uint32_t address,
                        const uint8_t *expected, uint32_t len) {
    uint8_t chunk[BOOT_MAX_READ];
    uint32_t done = 0;
    while (done < len) {
        uint16_t want = (uint16_t)((len - done) < BOOT_MAX_READ ? (len - done) : BOOT_MAX_READ);
        int got = boot_read(device, state, address + done, chunk, want);
        if (got <= 0) return -1;
        if (memcmp(chunk, expected + done, (size_t)got) != 0) {
            for (int i = 0; i < got; i++) {
                if (chunk[i] != expected[done + (uint32_t)i]) {
                    fprintf(stderr, "verify mismatch at 0x%08x: got 0x%02x expected 0x%02x\n",
                            address + done + (uint32_t)i, chunk[i],
                            expected[done + (uint32_t)i]);
                    return -1;
                }
            }
        }
        done += (uint32_t)got;
        if ((done % 4094) < BOOT_MAX_READ || done == len) {
            printf("verified %u / %u bytes\r", done, len);
            fflush(stdout);
        }
    }
    printf("\n");
    return 0;
}

static int boot_reset(IOHIDDeviceRef device, input_state_t *state) {
    IOReturn result = send_boot_command_ex(device, REPORT_ID_BOOT, 7, 0, 0, 0, 0, 0, NULL, 0,
                                           0, state);
    if (result != kIOReturnSuccess) {
        fprintf(stderr, "reset command failed: 0x%08x\n", result);
        return -1;
    }
    return 0;
}

static uint8_t *read_file(const char *path, size_t *len_out) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        perror(path);
        return NULL;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fclose(f);
        return NULL;
    }
    long len = ftell(f);
    if (len < 0) {
        fclose(f);
        return NULL;
    }
    rewind(f);
    uint8_t *data = malloc((size_t)len);
    if (!data) {
        fclose(f);
        return NULL;
    }
    if (fread(data, 1, (size_t)len, f) != (size_t)len) {
        free(data);
        fclose(f);
        return NULL;
    }
    fclose(f);
    *len_out = (size_t)len;
    return data;
}

int main(int argc, char **argv) {
    const char *default_firmware =
        "build/firmware/ultimate2c-controller-v1.09-bt-rumble-proportional-latch30.dat";
    const char *firmware_path = argc > 1 ? argv[1] : default_firmware;

    size_t dat_len = 0;
    uint8_t *dat = read_file(firmware_path, &dat_len);
    if (!dat) return 1;
    if (dat_len < DAT_WRAPPER_SIZE + 28) {
        fprintf(stderr, "firmware file too short\n");
        free(dat);
        return 1;
    }
    uint32_t version = get_u32(dat);
    uint32_t address = get_u32(dat + 4);
    uint32_t length = get_u32(dat + 8);
    uint32_t pid_in_dat = get_u32(dat + 12);
    if (address != 0x6000 || length != 0x12400 || pid_in_dat != 0x301b ||
        dat_len < DAT_WRAPPER_SIZE + length) {
        fprintf(stderr,
                "refusing unexpected firmware: version=0x%x address=0x%x length=0x%x pid=0x%x bytes=%zu\n",
                version, address, length, pid_in_dat, dat_len);
        free(dat);
        return 1;
    }
    const uint8_t *payload = dat + DAT_WRAPPER_SIZE;
    printf("Firmware: %s\n", firmware_path);
    printf("Header: version=0x%x address=0x%x length=0x%x pid=0x%x\n", version, address,
           length, pid_in_dat);

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
    if (pid != 0x301b) {
        fprintf(stderr, "refusing to flash non-Ultimate-2C PID 0x%04x\n", pid);
        free(dat);
        return 1;
    }

    uint8_t version_bytes[8];
    memset(version_bytes, 0, sizeof(version_bytes));
    if (boot_read(device, &state, 0x01017040, version_bytes, sizeof(version_bytes)) > 0) {
        printf("Version bytes @0x01017040:");
        for (size_t i = 0; i < sizeof(version_bytes); i++) printf(" %02x", version_bytes[i]);
        printf("  main=%u beta=%u\n", get_u16(version_bytes), get_u16(version_bytes + 4));
    }

    printf("Erasing flash...\n");
    if (boot_erase(device, &state, address, length) != 0) {
        free(dat);
        return 1;
    }

    printf("Writing patched firmware...\n");
    if (boot_write(device, &state, address, payload, length, length) != 0) {
        fprintf(stderr, "write failed; leaving controller in BOOT mode\n");
        free(dat);
        return 1;
    }

    printf("Reading back for verification...\n");
    if (verify_flash(device, &state, address, payload, length) != 0) {
        fprintf(stderr, "verification failed; leaving controller in BOOT mode\n");
        free(dat);
        return 2;
    }

    printf("Verified. Resetting controller into normal firmware...\n");
    if (boot_reset(device, &state) != 0) {
        free(dat);
        return 1;
    }
    printf("Reset command sent.\n");

    IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    IOHIDDeviceClose(device, kIOHIDOptionsTypeNone);
    CFRelease(device);
    CFRelease(manager);
    free(dat);
    return 0;
}
