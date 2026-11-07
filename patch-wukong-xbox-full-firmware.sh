#!/bin/bash
# Builds the full Xbox emulation firmware for the Ultimate 2C Wireless
# "Black Myth: Wukong" edition from that edition's factory firmware.
#
#   ./patch-wukong-xbox-full-firmware.sh /path/to/wukong-stock.dat [output.dat]
#
# 8BitDo does not publish the Wukong edition image, so take it from the
# controller itself: in BOOT mode run
#   ./build/ultimate2c_boot_backup 0x6000 0x12400 wukong-stock.bin
# The patch script accepts that raw 74,752-byte backup directly (or the
# same bytes behind the standard 28-byte .dat wrapper).
#
# Verifies the input's SHA-256 before patching and the output's after.

set -euo pipefail
cd "$(dirname "$0")"

# Wukong edition factory image (PnP ID 2DC8:301B, hsize 0x12314): raw backup and wrapped forms.
WUKONG_RAW_SHA256="32153cc974dcd864f6b359c1940d31ff950cd42264d9302544b31ed2e6eeb3a5"
WUKONG_DAT_SHA256="f310f98189058148077e96c35efa5f94fbdbe2740b6fd1c07684c00c6d0a16d4"
# Deterministic result of the Wukong full-emulation patch.
WUKONG_XBOX_FULL_SHA256="66fc9538e4cfa9e0596f3e94a78f9c5b5c6a4e2f19968724b663d5befb342a28"

SRC="${1:-}"
DST="${2:-ultimate2c-wukong-xbox-full.dat}"

if [ -z "$SRC" ]; then
  echo "usage: ./patch-wukong-xbox-full-firmware.sh /path/to/wukong-stock.dat [output.dat]"
  exit 1
fi
if [ ! -f "$SRC" ]; then
  echo "no such file: $SRC" >&2
  exit 1
fi

echo "verifying input firmware..."
ACTUAL="$(shasum -a 256 "$SRC" | awk '{print $1}')"
if [ "$ACTUAL" != "$WUKONG_RAW_SHA256" ] && [ "$ACTUAL" != "$WUKONG_DAT_SHA256" ]; then
  echo "ERROR: this is not the known Wukong edition factory firmware." >&2
  echo "  expected sha256: $WUKONG_RAW_SHA256 (raw backup)" >&2
  echo "               or: $WUKONG_DAT_SHA256 (wrapped .dat)" >&2
  echo "  actual sha256:   $ACTUAL" >&2
  exit 1
fi
echo "  input ok (Wukong edition factory image)"

python3 patch_ultimate2c_wukong_xbox_full.py "$SRC" "$DST"

echo "verifying output firmware..."
ACTUAL="$(shasum -a 256 "$DST" | awk '{print $1}')"
if [ "$ACTUAL" != "$WUKONG_XBOX_FULL_SHA256" ]; then
  echo "ERROR: patched output does not match the known-good build." >&2
  echo "  expected sha256: $WUKONG_XBOX_FULL_SHA256" >&2
  echo "  actual sha256:   $ACTUAL" >&2
  exit 1
fi
echo "  output ok"

echo
echo "Wukong edition Xbox emulation firmware ready: $DST"
echo "Flash it with: ./flash-firmware.sh \"$DST\""
echo "After flashing, forget the controller in System Settings, toggle Bluetooth off/on, and pair again."
