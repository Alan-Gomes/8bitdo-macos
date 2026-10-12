#!/usr/bin/env python3
"""macOS support firmware patch for the Ultimate 2C Wireless (1.06 beta base).

Goal: make macOS and game libraries treat the controller as a fully supported
device, including native Bluetooth rumble, with no shim, no daemon, no
workaround.

How it works
------------
Game libraries ship a built-in 8BitDo HIDAPI driver that claims several 8BitDo
PIDs over Bluetooth (SF30/SN30/Pro2/Pro3...). For those devices it probes
*feature report 6*; if the probe succeeds it enables rumble and sends rumble
as a 5-byte output report: [0x05, low, high, 0, 0] via IOHIDDeviceSetReport.

The 1.06 beta advertises PID 0x6002, which no library driver claims, so the
library falls back to the IOKit driver, whose rumble path needs macOS
ForceFeedback support the device does not expose.

This patch makes the controller look like a device the library already
supports:

  1. PnP ID  0x6002 -> 0x6101 (8BitDo SN30 Pro BT), so the 8BitDo driver
     claims the device and runs its feature-report-6 probe.
  2. Add a feature-report-6 characteristic by repurposing the *boot keyboard
     output report* slot (handle 0x14), which macOS ignores in report-protocol
     mode:
       - char declaration  "12 14 00 22 2a" -> "02 14 00 4d 2a"
         (read-only REPORT char, same handle, uuid 2A4D)
       - value entry: uuid 2A22 -> 2A4D, attr_len 1 -> 8, value -> static
         8 zero bytes in the trailer
       - its CCCD descriptor (2902) becomes the Report Reference (2908) and
         points at static [06 03]
     The two real input report characteristics (handles 0x19 and 0x1D) are
     left completely untouched - the firmware notifies input on 0x1D, so any
     change there kills input.
  3. Add feature report 6 to the HID report map (moved to the post-CRC trailer
     because the map has no room to grow in place) and repoint the REPORT_MAP
     attribute at it.
  4. Fix the output report path so macOS actually delivers SetReport(5):
     attribute length 1 -> 4 and the Report Reference repointed to static
     [05 02] (the original points at RAM). Report ID 5 is kept, exactly what
     SDL sends.
  5. Install the proportional motor handler in the trailer and point the
     output characteristic's write callback at it.

All patches are data-only, every site is validated against the original bytes,
and the Telink CRC is recomputed.
"""
import argparse
import struct
import zlib
from pathlib import Path


WRAPPER_SIZE = 0x1C
IMAGE_BASE = 0x6000

# --- GATT attribute table (beta: base 0x11504, 24-byte entries) ---
TABLE_BASE = 0x11504
ENTRY_VALUE_BOOT_KBD = 0x116E4   # boot keyboard output value: uuid 2A22, alen 1
ENTRY_CCCD_BOOT_KBD = 0x116FC    # CCCD for that char -> becomes 2908 ref
ENTRY_VALUE_OUTPUT = 0x1181C     # output report value: attr_len 1, w = 0
ENTRY_REF_OUTPUT = 0x11834       # output report reference: RAM 0x842E14
ENTRY_VALUE_REPORT_MAP = 0x11864 # report map value: attr_len 0x74
ENTRY_REF_INPUT_A = 0x1178C      # input A reference (handle 0x19) - must stay stock
ENTRY_REF_INPUT_B = 0x117EC      # input B reference (handle 0x1D) - must stay stock

CHAR_BOOT_KBD_DECL_FILE = 0x11380  # inline char declaration "12 14 00 22 2a"

# --- PnP ID value (7 bytes at file 0x11488: 02 c8 2d 02 60 01 00) ---
PNP_VALUE_FILE = 0x11488
PNP_VID_PID_OFF = 3  # bytes 3..4 = PID little-endian

# --- report map ---
REPORT_MAP_FILE = 0x11414
REPORT_MAP_LEN = 0x74
FEATURE_COLLECTION = bytes.fromhex(
    "06 00 ff"        # usage page vendor 0xFF00
    "09 06"           # usage 6
    "85 06"           # report id 6
    "15 00"           # logical min 0
    "26 ff 00"        # logical max 255
    "75 08"           # report size 8
    "95 08"           # report count 8
    "b1 02"           # feature (data, var, abs)
)

# --- trailer (post-CRC, file 0x12340, 220 zero bytes) ---
TRAILER_FILE = 0x12340
HANDLER_ADDR = 0x18324  # trailer + 0
STATIC_OUTPUT_REF_ADDR = 0x18368  # [05 02]
STATIC_FEATURE_REF_ADDR = 0x1836A  # [06 03]
FEATURE_VALUE_ADDR = 0x1836C  # 8 zero bytes
NEW_MAP_ADDR = 0x18374  # new report map

SCHEDULER_ADDR = 0xFB14  # beta motor scheduler

# Shared in-image uuid byte locations, referenced by existing table entries.
UUID_2A4D_PTR = 0x000173D8  # file 0x113F4, bytes "4d 2a"
UUID_2908_PTR = 0x000174E4  # file 0x11500, bytes "08 29"

# Same handler as the v1.09 proportional build, scheduler literal retargeted.
HANDLER_V109 = bytes.fromhex(
    "10 65"
    "41 4a 83 4a c4 4a 02 4b"
    "20 ec 8c 02 00 c2 08 ec"
    "00 f6 00 fe 11 ec 9a 02"
    "00 c2 19 ec 09 f6 09 fe"
    "0b ec 1e a2 03 03 02 c1"
    "00 a0 00 a1 01 a2 02 0b"
    "00 90 05 98 00 a0 10 6d"
    "c0 46"
    "68 fa 00 00"
    "18 07"
    "c0 46"
)
if HANDLER_V109[60:64] != b"\x68\xfa\x00\x00":
    raise SystemExit("handler layout changed: scheduler literal not at offset 60")
HANDLER = HANDLER_V109[:60] + struct.pack("<I", SCHEDULER_ADDR) + HANDLER_V109[64:]

TARGET_PID = 0x6101  # 8BitDo SN30 Pro BT


def foff(fw_addr):
    return WRAPPER_SIZE + fw_addr - IMAGE_BASE


def addr(file_off):
    return IMAGE_BASE + file_off - WRAPPER_SIZE


def update_telink_crc(data):
    payload = memoryview(data)[WRAPPER_SIZE:]
    if payload[0x08:0x0C].tobytes() != b"KNLT":
        raise SystemExit("not a Telink KNLT firmware payload")
    hsize = struct.unpack_from("<I", payload, 0x18)[0]
    if hsize > len(payload) or (hsize & 0x0F) != 4:
        raise SystemExit(f"invalid Telink hsize 0x{hsize:x}")
    crc_without_word = zlib.crc32(payload[: hsize - 4]) & 0xFFFFFFFF
    stored_crc = (~crc_without_word) & 0xFFFFFFFF
    struct.pack_into("<I", data, WRAPPER_SIZE + hsize - 4, stored_crc)
    return stored_crc


def check(data, off, expected, label):
    got = bytes(data[off:off + len(expected)])
    if got != expected:
        raise SystemExit(f"{label}: expected {expected.hex(' ')} at 0x{off:x}, found {got.hex(' ')}")


def patch_firmware(src: Path, dst: Path) -> None:
    data = bytearray(src.read_bytes())

    # ---- validate input ----
    if data[0:4] != bytes.fromhex("6a 00 01 00"):
        raise SystemExit(f"input is not the 1.06 beta (header {data[0:4].hex(' ')})")
    check(data, PNP_VALUE_FILE, bytes.fromhex("02 c8 2d 02 60 01 00"), "PnP value")
    check(data, CHAR_BOOT_KBD_DECL_FILE, bytes.fromhex("12 14 00 22 2a"), "boot kbd char declaration")
    check(data, REPORT_MAP_FILE, bytes.fromhex("05 01 09 05 a1 01 85 01"), "report map head")
    check(data, REPORT_MAP_FILE + REPORT_MAP_LEN - 1, b"\xc0", "report map end")
    if data[TRAILER_FILE:TRAILER_FILE + 0xD8] != b"\x00" * 0xD8:
        raise SystemExit("trailer is not empty")

    t = struct.unpack_from("<HBBIIIII", data, ENTRY_VALUE_BOOT_KBD)
    if t != (0, 0x01, 2, 1, 0x000174CE, 0x00843150, 0, 0):
        raise SystemExit(f"unexpected boot kbd value tuple: {t!r}")
    t = struct.unpack_from("<HBBIIIII", data, ENTRY_CCCD_BOOT_KBD)
    if t != (0, 0x03, 2, 2, 0x00017340, 0x00843160, 0, 0):
        raise SystemExit(f"unexpected boot kbd cccd tuple: {t!r}")
    t = struct.unpack_from("<HBBIIIII", data, ENTRY_VALUE_OUTPUT)
    if t != (0, 0x03, 2, 1, 0x000173D8, 0x0084315C, 0, 0):
        raise SystemExit(f"unexpected output value tuple: {t!r}")
    t = struct.unpack_from("<HBBIIIII", data, ENTRY_REF_OUTPUT)
    if t != (0, 0x03, 2, 2, 0x000174E4, 0x00842E14, 0, 0):
        raise SystemExit(f"unexpected output ref tuple: {t!r}")
    t = struct.unpack_from("<HBBIIIII", data, ENTRY_VALUE_REPORT_MAP)
    if t != (0, 0x01, 2, REPORT_MAP_LEN, 0x0001735A, 0x000173F8, 0, 0):
        raise SystemExit(f"unexpected report map tuple: {t!r}")
    # the real input chars must be stock (firmware notifies input on 0x1D)
    t = struct.unpack_from("<HBBIIIII", data, ENTRY_REF_INPUT_A)
    if t != (0, 0x03, 2, 2, 0x000174E4, 0x00842E10, 0, 0):
        raise SystemExit(f"input A ref is not stock: {t!r}")
    t = struct.unpack_from("<HBBIIIII", data, ENTRY_REF_INPUT_B)
    if t != (0, 0x03, 2, 2, 0x000174E4, 0x00842E0C, 0, 0):
        raise SystemExit(f"input B ref is not stock: {t!r}")

    # ---- build new report map ----
    old_map = bytes(data[REPORT_MAP_FILE:REPORT_MAP_FILE + REPORT_MAP_LEN])
    new_map = old_map[:-1] + FEATURE_COLLECTION + old_map[-1:]
    if len(new_map) != REPORT_MAP_LEN + len(FEATURE_COLLECTION):
        raise SystemExit("map length arithmetic failed")

    # ---- build trailer blob ----
    blob = bytearray()
    blob += HANDLER
    assert len(blob) == len(HANDLER)
    blob += bytes.fromhex("05 02")
    blob += bytes.fromhex("06 03")
    blob += b"\x00" * 8
    blob += new_map
    if len(blob) > 0xD8:
        raise SystemExit(f"trailer blob too large: {len(blob)} > 216")

    # ---- apply patches ----
    # 1. PnP PID 0x6002 -> 0x6101
    struct.pack_into("<H", data, PNP_VALUE_FILE + PNP_VID_PID_OFF, TARGET_PID)

    # 2. boot kbd output char becomes a read-only REPORT char (feature report 6)
    data[CHAR_BOOT_KBD_DECL_FILE] = 0x02                       # props 0x12 -> 0x02
    data[CHAR_BOOT_KBD_DECL_FILE + 3:CHAR_BOOT_KBD_DECL_FILE + 5] = bytes.fromhex("4d 2a")  # uuid 2A22 -> 2A4D
    struct.pack_into("<I", data, ENTRY_VALUE_BOOT_KBD + 4, 8)             # attr_len 1 -> 8
    struct.pack_into("<I", data, ENTRY_VALUE_BOOT_KBD + 8, UUID_2A4D_PTR) # uuid 2A22 -> 2A4D
    struct.pack_into("<I", data, ENTRY_VALUE_BOOT_KBD + 12, FEATURE_VALUE_ADDR)  # value -> static
    struct.pack_into("<I", data, ENTRY_CCCD_BOOT_KBD + 8, UUID_2908_PTR)  # uuid 2902 -> 2908
    struct.pack_into("<I", data, ENTRY_CCCD_BOOT_KBD + 12, STATIC_FEATURE_REF_ADDR)  # value -> [06 03]

    # 3. report map: move to trailer, extend with feature report 6
    struct.pack_into("<I", data, ENTRY_VALUE_REPORT_MAP + 4, len(new_map))
    struct.pack_into("<I", data, ENTRY_VALUE_REPORT_MAP + 12, NEW_MAP_ADDR)

    # 4. output report: attr_len 1 -> 4, ref -> static [05 02], callback -> handler
    struct.pack_into("<I", data, ENTRY_VALUE_OUTPUT + 4, 4)
    struct.pack_into("<I", data, ENTRY_REF_OUTPUT + 12, STATIC_OUTPUT_REF_ADDR)
    struct.pack_into("<I", data, ENTRY_VALUE_OUTPUT + 16, HANDLER_ADDR | 1)

    # 5. trailer blob
    data[TRAILER_FILE:TRAILER_FILE + len(blob)] = blob

    stored_crc = update_telink_crc(data)
    dst.write_bytes(data)

    print(f"patched {src} -> {dst}")
    print(f"PnP ID @0x{PNP_VALUE_FILE + PNP_VID_PID_OFF:x}: PID 0x6002 -> 0x{TARGET_PID:04x} (SN30 Pro BT)")
    print(f"boot kbd char @0x{CHAR_BOOT_KBD_DECL_FILE:x}: props 0x12 -> 0x02, uuid 2A22 -> 2A4D")
    print(f"boot kbd value: attr_len 1 -> 8, uuid -> 2A4D, value -> 0x{FEATURE_VALUE_ADDR:x} (8 zero bytes)")
    print(f"boot kbd cccd: uuid 2902 -> 2908, value -> 0x{STATIC_FEATURE_REF_ADDR:x} ([06 03])")
    print(f"report map: 0x{REPORT_MAP_FILE:x} len 0x{REPORT_MAP_LEN:x} -> 0x{NEW_MAP_ADDR:x} len 0x{len(new_map):x} (+feature report 6)")
    print(f"output report: attr_len 1 -> 4, ref RAM 0x842E14 -> 0x{STATIC_OUTPUT_REF_ADDR:x} ([05 02]), w -> 0x{HANDLER_ADDR | 1:x}")
    print(f"handler @0x{HANDLER_ADDR:x} ({len(HANDLER)} bytes, scheduler 0x{SCHEDULER_ADDR:x}), blob {len(blob)}/216 bytes")
    print(f"updated Telink CRC: 0x{stored_crc:08x}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch the Ultimate 2C Wireless 1.06 beta for native SDL rumble over Bluetooth."
    )
    parser.add_argument("src", nargs="?", default="8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat")
    parser.add_argument("dst", nargs="?", default="ultimate2c-1.06beta-macos.dat")
    args = parser.parse_args()
    patch_firmware(Path(args.src), Path(args.dst))


if __name__ == "__main__":
    main()
