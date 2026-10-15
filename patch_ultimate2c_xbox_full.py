#!/usr/bin/env python3
"""Xbox Elite BLE emulation for the Ultimate 2C Wireless: full wire-format rewrite.

macOS's GameController framework claims the pad under an Xbox BLE identity and
parses input reports with the real Xbox BLE layout (measured against live
captures and the documented Xbox Bluetooth layout). This build makes the
controller a full Xbox Elite-compatible pad:

  1. Bluetooth PnP ID -> Microsoft Xbox Elite V2 Controller BLE (045E:0B22).
     The Elite identity is what makes GameController expose the two extra
     paddle buttons (GCXboxGamepad.paddleButton1/2): Apple's documentation
     states the standard Xbox Wireless Controller has no paddle buttons while
     the Elite has four extra digital buttons.
  2. HID report map replaced in place (fits the stock 116-byte
     slot): 16-bit X/Y/Rx/Ry sticks, 16-bit accel/brake triggers, 4-bit hat
     (1..8, matching the wire), 16 buttons, 4 vendor bytes (the Elite report
     tail: share/select, paddles, profile, trigger settings) - a 19-data-byte
     input report, exactly the Elite BLE report length.
  3. A TC32 code cave in the post-CRC trailer is hooked into the input report
     builder (notify call at 0x5B38). The cave reads the stock 10-byte payload
     and writes the 19-byte Elite payload:

        wire[0..1]   X      (left stick X)      16-bit LE, byte duplicated
        wire[2..3]   Y      (left stick Y)      16-bit LE
        wire[4..5]   Rx     (right stick X)     16-bit LE
        wire[6..7]   Ry     (right stick Y)     16-bit LE
        wire[8..9]   accel  (LT)                10-bit <<2
        wire[10..11] brake  (RT)                10-bit <<2
        wire[12]     hat    (1..8, 0 = none)
        wire[13]     A B X Y LB RB              (bits 0,1,3,4,6,7)
        wire[14]     Options Menu Home L3 R3    (bits 2,3,4,5,6)
        wire[15]     paddles: L4 -> bit0, R4 -> bit1
        wire[16]     paddles, same value (written to both 15 and 16: the
                     Elite PP byte position differs between references)
        wire[17]     0 (profile)
        wire[18]     0 (trigger settings)

     then notifies handle 0x1D with length 19.
  4. Rumble: the stock output-report characteristic is dead (1-byte length and
     a report reference pointing at unpopulated RAM), which is why macOS
     never delivered rumble. Its report reference is repointed to a static
     [03 02] (report 3 = the Xbox BLE rumble report), its length grows to 8,
     and its write callback runs a new TC32 handler that reads the two main
     motor bytes (value[3] = low, value[4] = high - the Xbox BT rumble layout
     SDL uses: [3, 0x0F, LT, RT, LOW, HIGH, 0xFF, 0x00, 0xEB]) and feeds the
     firmware's motor scheduler.
  5. Telink CRC recomputed.

Cave and handler are hand-written TC32 assembled with the tc32-elf toolchain;
the machine code is embedded byte for byte. Everything else stays stock.
"""
import argparse
import struct
import zlib
from pathlib import Path

WRAPPER_SIZE = 0x1C
IMAGE_BASE = 0x6000

PNP_VALUE_FILE = 0x11488
STOCK_PNP = bytes.fromhex("02 c8 2d 02 60 01 00")
XBOX_VID = 0x045E
XBOX_PID = 0x0B22  # Xbox Elite V2 Controller BLE

REPORT_MAP_FILE = 0x11414
STOCK_MAP_LEN = 0x74
GATT_MAP_ENTRY = 0x11864
STOCK_MAP = bytes.fromhex(
    "05 01 09 05 a1 01 85 01 05 01 15 00 25 07 46 3b 01 95 01 75 04 65 14 09 39"
    " 81 42 75 01 95 04 81 01 15 00 26 ff 00 09 30 09 31 09 32 09 35 95 04 75 08"
    " 81 02 05 02 15 00 26 ff 00 09 c4 09 c5 95 02 75 08 81 02 05 09 19 01 29 10"
    " 15 00 25 01 75 01 95 10 81 02 05 06 09 20 15 00 25 64 75 08 95 01 81 02 05"
    " 0f 09 70 85 05 15 00 25 64 75 08 95 04 91 02 c0")

HOOK_FILE = 0x5B38
STOCK_HOOK = bytes.fromhex("40 a0 1d a1 6a 06 0a a3 08 90 88 9d")

ENTRY_VALUE_OUTPUT = 0x1181C   # attr_len @+4, write callback @+16
ENTRY_REF_OUTPUT = 0x11834     # report reference value pointer @+12

CAVE_FILE = 0x12340
TRAILER_LEN = 0xDC

HOOK_HEX = "0c90029cc006c006c006c006"
CAVE_HEX = "88606c066d0620b5684820406040a848a040e040e848204160412849a041e041a84981f0214281f96142684981f0a14281f9e142284808a801c400a0008001b02043e849dba108006043284a7ca10800a04300a0e84902ec04a10a0092f803ec20a10b001bf91a03e243224400a06044a04440a01da122ec13a3fc974b990860f397c09b"
NEW_MAP_HEX = "05010905a1018501093009310933093426ffff950475108102050209c409c526ffff95027510810205010939250895017504814295017504810305091901291025019510750181020600ff0901750895048102050f092185030970750895089102c0"
HANDLER_HEX = "106504ec204b614b1ea2f797af9b00a0106d"

HOOK = bytes.fromhex(HOOK_HEX)
CAVE = bytes.fromhex(CAVE_HEX)
NEW_MAP = bytes.fromhex(NEW_MAP_HEX)
HANDLER = bytes.fromhex(HANDLER_HEX)

HANDLER_FILE = CAVE_FILE + len(CAVE)
STATIC_REF_FILE = HANDLER_FILE + len(HANDLER)
STATIC_REF = bytes.fromhex("03 02")  # report 3, type output

OUTPUT_VALUE_LEN = 8


def fw_addr(file_off):
    return file_off - WRAPPER_SIZE + IMAGE_BASE


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


def patch_firmware(src, dst):
    data = bytearray(src.read_bytes())

    if data[0:4] != bytes.fromhex("6a 00 01 00"):
        raise SystemExit(f"input is not the 1.06 beta (header {data[0:4].hex(' ')})")

    got = bytes(data[PNP_VALUE_FILE:PNP_VALUE_FILE + len(STOCK_PNP)])
    if got != STOCK_PNP:
        raise SystemExit(f"PnP ID is not stock at 0x{PNP_VALUE_FILE:x}: {got.hex(' ')}")

    got = bytes(data[REPORT_MAP_FILE:REPORT_MAP_FILE + STOCK_MAP_LEN])
    if got != STOCK_MAP:
        raise SystemExit("report map is not stock")

    ent = struct.unpack_from("<HBBIIIII", data, GATT_MAP_ENTRY)
    if ent[3] != STOCK_MAP_LEN:
        raise SystemExit(f"GATT map entry length is 0x{ent[3]:x}, expected 0x{STOCK_MAP_LEN:x}")

    got = bytes(data[HOOK_FILE:HOOK_FILE + len(STOCK_HOOK)])
    if got != STOCK_HOOK:
        raise SystemExit(f"hook site is not stock at 0x{HOOK_FILE:x}: {got.hex(' ')}")

    if data[CAVE_FILE:CAVE_FILE + TRAILER_LEN] != bytes(TRAILER_LEN):
        raise SystemExit("trailer is not empty")
    if len(CAVE) + len(HANDLER) + len(STATIC_REF) > TRAILER_LEN:
        raise SystemExit("trailer blob too large")
    if len(NEW_MAP) > STOCK_MAP_LEN:
        raise SystemExit(f"new map too large: {len(NEW_MAP)} > {STOCK_MAP_LEN}")

    # 1. PnP ID -> Microsoft Xbox Elite identity.
    pnp = bytearray(STOCK_PNP)
    struct.pack_into("<H", pnp, 1, XBOX_VID)
    struct.pack_into("<H", pnp, 3, XBOX_PID)
    data[PNP_VALUE_FILE:PNP_VALUE_FILE + len(pnp)] = pnp

    # 2. New report map in place; GATT length updated.
    data[REPORT_MAP_FILE:REPORT_MAP_FILE + STOCK_MAP_LEN] = NEW_MAP + bytes(STOCK_MAP_LEN - len(NEW_MAP))
    struct.pack_into("<I", data, GATT_MAP_ENTRY + 4, len(NEW_MAP))

    # 3. Hook + cave + motor handler + static report reference.
    data[HOOK_FILE:HOOK_FILE + len(HOOK)] = HOOK
    data[CAVE_FILE:CAVE_FILE + len(CAVE)] = CAVE
    data[HANDLER_FILE:HANDLER_FILE + len(HANDLER)] = HANDLER
    data[STATIC_REF_FILE:STATIC_REF_FILE + len(STATIC_REF)] = STATIC_REF

    # 4. Output report: usable length, static [03 02] reference, motor handler.
    struct.pack_into("<I", data, ENTRY_VALUE_OUTPUT + 4, OUTPUT_VALUE_LEN)
    struct.pack_into("<I", data, ENTRY_VALUE_OUTPUT + 16, fw_addr(HANDLER_FILE) | 1)
    struct.pack_into("<I", data, ENTRY_REF_OUTPUT + 12, fw_addr(STATIC_REF_FILE))

    stored_crc = update_telink_crc(data)
    dst.write_bytes(data)

    print(f"patched {src} -> {dst}")
    print(f"PnP ID      @0x{PNP_VALUE_FILE:x}: {STOCK_PNP.hex(' ')} -> {bytes(pnp).hex(' ')}")
    print(f"    VID 0x{XBOX_VID:04X} (Microsoft), PID 0x{XBOX_PID:04X} (Xbox Elite V2 Controller BLE)")
    print(f"report map  @0x{REPORT_MAP_FILE:x}: {STOCK_MAP_LEN} -> {len(NEW_MAP)} bytes (19 data bytes, Elite tail)")
    print(f"GATT len    @0x{GATT_MAP_ENTRY + 4:x}: {STOCK_MAP_LEN} -> {len(NEW_MAP)}")
    print(f"hook        @0x{HOOK_FILE:x}: {STOCK_HOOK.hex(' ')} -> {HOOK.hex(' ')}")
    print(f"cave        @0x{CAVE_FILE:x} (fw 0x{fw_addr(CAVE_FILE):x}): {len(CAVE)} bytes")
    print(f"handler     @0x{HANDLER_FILE:x} (fw 0x{fw_addr(HANDLER_FILE):x}): {len(HANDLER)} bytes, cb=0x{fw_addr(HANDLER_FILE) | 1:x}")
    print(f"static ref  @0x{STATIC_REF_FILE:x} (fw 0x{fw_addr(STATIC_REF_FILE):x}): {STATIC_REF.hex(' ')} (report 3, output)")
    print(f"output att  @0x{ENTRY_VALUE_OUTPUT:x}: len {OUTPUT_VALUE_LEN}, ref -> 0x{fw_addr(STATIC_REF_FILE):x}")
    print(f"updated Telink CRC: 0x{stored_crc:08x}")


def main():
    parser = argparse.ArgumentParser(
        description="Ultimate 2C Wireless: Xbox Elite BLE identity, full Xbox wire format, rumble path."
    )
    parser.add_argument("src", nargs="?",
                        default="8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat")
    parser.add_argument("dst", nargs="?", default="ultimate2c-1.06beta-xbox-full.dat")
    args = parser.parse_args()
    patch_firmware(Path(args.src), Path(args.dst))


if __name__ == "__main__":
    main()
