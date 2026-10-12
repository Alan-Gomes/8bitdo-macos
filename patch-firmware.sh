#!/bin/bash
# Applies the native-rumble patch to an 8BitDo Ultimate 2C Wireless 1.06 beta
# firmware file.
#
#   ./patch-firmware.sh /path/to/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat [output.dat]
#
# Verifies the input file's SHA-256 before patching and the output file's
# SHA-256 after, so a wrong or corrupted file can never produce a firmware
# that gets flashed by mistake.

set -euo pipefail
cd "$(dirname "$0")"

# Official 1.06 beta for model 0x301B (the only supported input).
BETA_SHA256="11c8d195d3ca76da24a33ba92ad9d7ae8ad6f455244864bed4c66898e9074bfb"
# Deterministic result of patching that exact input.
PATCHED_SHA256="804d6475624c6e9b76381bd40391605bb757c25a84b326a5b4701c2d89523cb6"

SRC="${1:-}"
DST="${2:-ultimate2c-1.06beta-native-rumble.dat}"

if [ -z "$SRC" ]; then
  echo "usage: ./patch-firmware.sh /path/to/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat [output.dat]"
  exit 1
fi
if [ ! -f "$SRC" ]; then
  echo "no such file: $SRC" >&2
  exit 1
fi

echo "verifying input firmware..."
ACTUAL="$(shasum -a 256 "$SRC" | awk '{print $1}')"
if [ "$ACTUAL" != "$BETA_SHA256" ]; then
  echo "ERROR: this is not the 1.06 beta firmware." >&2
  echo "  expected sha256: $BETA_SHA256" >&2
  echo "  actual sha256:   $ACTUAL" >&2
  exit 1
fi
echo "  input ok (1.06 beta, model 0x301B)"

python3 patch_ultimate2c_native_rumble.py "$SRC" "$DST"

echo "verifying output firmware..."
ACTUAL="$(shasum -a 256 "$DST" | awk '{print $1}')"
if [ "$ACTUAL" != "$PATCHED_SHA256" ]; then
  echo "ERROR: patched output does not match the known-good build." >&2
  echo "  expected sha256: $PATCHED_SHA256" >&2
  echo "  actual sha256:   $ACTUAL" >&2
  exit 1
fi
echo "  output ok"

echo
echo "Patched firmware ready: $DST"
echo "Flash it with: ./flash-firmware.sh \"$DST\""
