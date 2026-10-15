#!/bin/bash
# Builds the full Xbox emulation firmware from an official 1.06 beta file.
#
#   ./patch-xbox-full-firmware.sh /path/to/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat [output.dat]
#
# This build presents the controller as a Microsoft Xbox BLE controller AND
# rewrites its input reports into the real Xbox wire format via a TC32 code
# cave, so GameController.framework (and every Apple-native game) sees a fully
# working Xbox pad. See patch_ultimate2c_xbox_full.py for the full story.
#
# Verifies the input file's SHA-256 before patching and the output's after.

set -euo pipefail
cd "$(dirname "$0")"

# Official 1.06 beta for model 0x301B (the only supported input).
BETA_SHA256="11c8d195d3ca76da24a33ba92ad9d7ae8ad6f455244864bed4c66898e9074bfb"
# Deterministic result of the full-emulation patch.
XBOX_FULL_SHA256="18e2c710def1832177b17dbade1f81b9f2aa8c11bf7e5ce71567d0e6d05f34c1"

SRC="${1:-}"
DST="${2:-ultimate2c-1.06beta-xbox-full.dat}"

if [ -z "$SRC" ]; then
  echo "usage: ./patch-xbox-full-firmware.sh /path/to/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat [output.dat]"
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

python3 patch_ultimate2c_xbox_full.py "$SRC" "$DST"

echo "verifying output firmware..."
ACTUAL="$(shasum -a 256 "$DST" | awk '{print $1}')"
if [ "$ACTUAL" != "$XBOX_FULL_SHA256" ]; then
  echo "ERROR: patched output does not match the known-good build." >&2
  echo "  expected sha256: $XBOX_FULL_SHA256" >&2
  echo "  actual sha256:   $ACTUAL" >&2
  exit 1
fi
echo "  output ok"

echo
echo "Full Xbox emulation firmware ready: $DST"
echo "Flash it with: ./flash-firmware.sh \"$DST\""
echo "After flashing, forget and re-pair the controller in System Settings once."