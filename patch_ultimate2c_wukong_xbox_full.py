#!/usr/bin/env python3
"""Xbox Elite BLE emulation for the 8BitDo Ultimate 2C Wireless, Black Myth: Wukong edition.

Same patch as patch_ultimate2c_xbox_full.py (see that file and docs/firmware-patch.md),
applied to the Wukong edition's factory firmware (PnP ID 2DC8:301B, hsize 0x12314)
instead of the 1.06 beta. Every data table sits 16 bytes earlier in this image and
the trailer starts at file 0x12330, so the offsets below are the 1.06 beta offsets
minus 0x10 for everything after file 0x8DC0. The cave and handler are the same
machine code; the only relative call that changes is the cave's return to the hook
site (the hook site did not move, the cave did).

Additional change for this edition: on the Wukong unit tested, the firmware never
selects Bluetooth mode (the mode-switch check at fw 0xC42C always reports 2.4G, so
BLE init is never reached and nothing is advertised, with stock firmware too).
This build makes that check always return "Bluetooth". 2.4G mode is therefore
unavailable with this build; wired USB is unaffected.
"""
import argparse
import struct
from pathlib import Path

WRAPPER_SIZE = 0x1C
IMAGE_BASE = 0x6000

PNP_VALUE_FILE = 0x11478
STOCK_PNP = bytes.fromhex("02 c8 2d 1b 30 01 00")
XBOX_VID = 0x045E
XBOX_PID = 0x0B22  # Xbox Elite V2 Controller BLE

REPORT_MAP_FILE = 0x11404
STOCK_MAP_LEN = 0x74
GATT_MAP_ENTRY = 0x11854
STOCK_MAP = bytes.fromhex(
    "05 01 09 05 a1 01 85 01 05 01 15 00 25 07 46 3b 01 95 01 75 04 65 14 09 39"
    " 81 42 75 01 95 04 81 01 15 00 26 ff 00 09 30 09 31 09 32 09 35 95 04 75 08"
    " 81 02 05 02 15 00 26 ff 00 09 c4 09 c5 95 02 75 08 81 02 05 09 19 01 29 10"
    " 15 00 25 01 75 01 95 10 81 02 05 06 09 20 15 00 25 64 75 08 95 01 81 02 05"
    " 0f 09 70 85 05 15 00 25 64 75 08 95 04 91 02 c0")

HOOK_FILE = 0x5B38
STOCK_HOOK = bytes.fromhex("40 a0 1d a1 6a 06 0a a3 08 90 80 9d")  # notify(0x40, 0x1D, sp, 10) -> tjl 0x14628

ENTRY_VALUE_OUTPUT = 0x1180C   # attr_len @+4, write callback @+16
ENTRY_REF_OUTPUT = 0x11824     # report reference value pointer @+12

CAVE_FILE = 0x12330
TRAILER_LEN = 0xEC

# Mode-switch check: fw 0xC42C 'tloadr r3,[pc,#24]; tloadrb r3,[r3]' -> 'tmovs r0,#0; tjex lr'
MODE_CHECK_FILE = 0xC42C - IMAGE_BASE + WRAPPER_SIZE
STOCK_MODE_CHECK = bytes.fromhex("06 0b 1b 48")
FORCED_BT_MODE_CHECK = bytes.fromhex("00 a0 70 07")

CAVE_HEX = "88606c066d0620b5684820406040a848a040e040e848204160412849a041e041a84981f0214281f96142684981f0a14281f9e142284808a801c400a0008001b02043e849dba108006043284a7ca10800a04300a0e84902ec04a10a0092f803ec20a10b001bf91a03e243224400a06044a04440a01da122ec13a3fc974b990860f397c09b"
NEW_MAP_HEX = "05010905a1018501093009310933093426ffff950475108102050209c409c526ffff95027510810205010939250895017504814295017504810305091901291025019510750181020600ff0901750895048102050f092185030970750895089102c0"
HANDLER_HEX = "106504ec204b614b1ea2f797af9b00a0106d"

CAVE = bytearray.fromhex(CAVE_HEX)
NEW_MAP = bytes.fromhex(NEW_MAP_HEX)
HANDLER = bytes.fromhex(HANDLER_HEX)

HANDLER_FILE = CAVE_FILE + len(CAVE)
STATIC_REF_FILE = HANDLER_FILE + len(HANDLER)
STATIC_REF = bytes.fromhex("03 02")  # report 3, type output

OUTPUT_VALUE_LEN = 8

# .dat wrapper (version, address 0x6000, length 0x12400, pid 0x301B, ...) as used by the official files
RAW_BACKUP_WRAPPER = bytes.fromhex("6a00010000600000002401001b300000cdda783f0000000000000000")

RETURN_FW = 0xBB28     # instruction after the hook (file 0x5B44)
CAVE_RETURN_OFF = 0x80  # tjl inside the cave that jumps back


def fw_addr(file_off):
    return file_off - WRAPPER_SIZE + IMAGE_BASE


def tjl(site_fw, target_fw):
    """Encode a TC32 32-bit tjl (BL-like): hi = 0x9000|off[22:12], lo = 0x9800|off[11:1]."""
    off = target_fw - (site_fw + 4)
    if off < 0:
        off += 0x800000
    hi = 0x9000 | ((off >> 12) & 0x7FF)
    lo = 0x9800 | ((off >> 1) & 0x7FF)
    return struct.pack("<HH", hi, lo)


def patch_firmware(src, dst):
    data = bytearray(src.read_bytes())
    if len(data) == 0x12400:
        # raw image from ultimate2c_boot_backup: add the standard .dat wrapper
        data = bytearray(RAW_BACKUP_WRAPPER) + data
    if len(data) != WRAPPER_SIZE + 0x12400:
        raise SystemExit(f"unexpected input size {len(data)}")

    payload = memoryview(data)[WRAPPER_SIZE:]
    if payload[0x08:0x0C].tobytes() != b"KNLT":
        raise SystemExit("not a Telink KNLT firmware payload")
    hsize = struct.unpack_from("<I", payload, 0x18)[0]
    if hsize != 0x12314:
        raise SystemExit(f"unexpected hsize 0x{hsize:x}; this script is for the Wukong edition image")

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
    if struct.unpack_from("<I", data, ENTRY_VALUE_OUTPUT + 4)[0] != 1 or struct.unpack_from("<I", data, ENTRY_REF_OUTPUT + 12)[0] != 0x842E14:
        raise SystemExit("output characteristic entries are not stock")
    trailer = bytes(data[CAVE_FILE:CAVE_FILE + TRAILER_LEN])
    if trailer not in (bytes(TRAILER_LEN), b"\xff" * TRAILER_LEN):
        raise SystemExit("trailer is not empty")
    got = bytes(data[MODE_CHECK_FILE:MODE_CHECK_FILE + 4])
    if got != STOCK_MODE_CHECK:
        raise SystemExit(f"mode check is not stock at 0x{MODE_CHECK_FILE:x}: {got.hex(' ')}")
    if len(CAVE) + len(HANDLER) + len(STATIC_REF) > TRAILER_LEN:
        raise SystemExit("trailer blob too large")

    # 1. PnP ID -> Microsoft Xbox Elite identity.
    pnp = bytearray(STOCK_PNP)
    struct.pack_into("<H", pnp, 1, XBOX_VID)
    struct.pack_into("<H", pnp, 3, XBOX_PID)
    data[PNP_VALUE_FILE:PNP_VALUE_FILE + len(pnp)] = pnp

    # 2. New report map in place; GATT length updated.
    data[REPORT_MAP_FILE:REPORT_MAP_FILE + STOCK_MAP_LEN] = NEW_MAP + bytes(STOCK_MAP_LEN - len(NEW_MAP))
    struct.pack_into("<I", data, GATT_MAP_ENTRY + 4, len(NEW_MAP))

    # 3. Hook -> cave, cave return re-encoded for this image, handler, static reference.
    hook = tjl(fw_addr(HOOK_FILE), fw_addr(CAVE_FILE)) + bytes.fromhex("c006" * 4)
    data[HOOK_FILE:HOOK_FILE + len(hook)] = hook
    CAVE[CAVE_RETURN_OFF:CAVE_RETURN_OFF + 4] = tjl(fw_addr(CAVE_FILE) + CAVE_RETURN_OFF, RETURN_FW)
    data[CAVE_FILE:CAVE_FILE + len(CAVE)] = CAVE
    data[HANDLER_FILE:HANDLER_FILE + len(HANDLER)] = HANDLER
    data[STATIC_REF_FILE:STATIC_REF_FILE + len(STATIC_REF)] = STATIC_REF
    # zero the rest of the trailer (it is erased flash, 0xFF, on this image)
    rest = CAVE_FILE + len(CAVE) + len(HANDLER) + len(STATIC_REF)
    data[rest:CAVE_FILE + TRAILER_LEN] = bytes(CAVE_FILE + TRAILER_LEN - rest)

    # 4. Output report: usable length, static [03 02] reference, motor handler.
    struct.pack_into("<I", data, ENTRY_VALUE_OUTPUT + 4, OUTPUT_VALUE_LEN)
    struct.pack_into("<I", data, ENTRY_VALUE_OUTPUT + 16, fw_addr(HANDLER_FILE) | 1)
    struct.pack_into("<I", data, ENTRY_REF_OUTPUT + 12, fw_addr(STATIC_REF_FILE))

    # 5. Force Bluetooth mode.
    data[MODE_CHECK_FILE:MODE_CHECK_FILE + 4] = FORCED_BT_MODE_CHECK

    # The Wukong factory image's stored Telink CRC does not match the covered region even
    # when unmodified, and the controller runs it, so the CRC word is left untouched.

    dst.write_bytes(data)
    print(f"patched {src} -> {dst}")
    print(f"PnP ID      @0x{PNP_VALUE_FILE:x}: {STOCK_PNP.hex(' ')} -> {bytes(pnp).hex(' ')}")
    print(f"report map  @0x{REPORT_MAP_FILE:x}: {STOCK_MAP_LEN} -> {len(NEW_MAP)} bytes; GATT len @0x{GATT_MAP_ENTRY + 4:x}")
    print(f"hook        @0x{HOOK_FILE:x}: {STOCK_HOOK.hex(' ')} -> {hook.hex(' ')}")
    print(f"cave        @0x{CAVE_FILE:x} (fw 0x{fw_addr(CAVE_FILE):x}): {len(CAVE)} bytes, return tjl -> 0x{RETURN_FW:x}")
    print(f"handler     @0x{HANDLER_FILE:x} (fw 0x{fw_addr(HANDLER_FILE):x}), cb=0x{fw_addr(HANDLER_FILE) | 1:x}")
    print(f"static ref  @0x{STATIC_REF_FILE:x} (fw 0x{fw_addr(STATIC_REF_FILE):x})")
    print(f"mode check  @0x{MODE_CHECK_FILE:x} (fw 0xc42c): forced Bluetooth")


def main():
    parser = argparse.ArgumentParser(description="Ultimate 2C Wireless (Wukong edition): Xbox Elite BLE identity + forced Bluetooth mode.")
    parser.add_argument("src")
    parser.add_argument("dst", nargs="?", default="ultimate2c-wukong-xbox-full.dat")
    args = parser.parse_args()
    patch_firmware(Path(args.src), Path(args.dst))


if __name__ == "__main__":
    main()
