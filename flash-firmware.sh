#!/bin/bash
# Flashes the patched firmware to an 8BitDo Ultimate 2C Wireless controller
# through its USB bootloader.
#
#   I_UNDERSTAND_BRICK_RISK=1 ./flash-firmware.sh [firmware.dat]
#
# Only the deterministic result of ./patch-xbox-full-firmware.sh is accepted,
# identified by its SHA-256. Verifies the firmware before doing anything,
# builds the boot tools, backs up the controller's current flash, then writes
# and verifies the new firmware.
#
# Put the controller in BOOT mode first: it must enumerate as "8BitDo Boot"
# (hold LB + RB while plugging in the USB cable). Normal mode will be refused
# by the flash tool.

set -euo pipefail
cd "$(dirname "$0")"

# Firmware builds this script will flash: the deterministic result of patching
# the official 1.06 beta with patch-xbox-full-firmware.sh.
XBOX_FULL_SHA256="18e2c710def1832177b17dbade1f81b9f2aa8c11bf7e5ce71567d0e6d05f34c1"

FW="${1:-ultimate2c-1.06beta-xbox-full.dat}"

if [ ! -f "$FW" ]; then
  echo "no such file: $FW" >&2
  echo "build it first with: ./patch-xbox-full-firmware.sh /path/to/1.06-beta-firmware.dat" >&2
  exit 1
fi

echo "verifying firmware..."
ACTUAL="$(shasum -a 256 "$FW" | awk '{print $1}')"
if [ "$ACTUAL" != "$XBOX_FULL_SHA256" ]; then
  echo "ERROR: this is not the expected patched firmware." >&2
  echo "  expected sha256: $XBOX_FULL_SHA256" >&2
  echo "  actual sha256:   $ACTUAL" >&2
  echo "  build it with: ./patch-xbox-full-firmware.sh /path/to/1.06-beta-firmware.dat" >&2
  exit 1
fi
echo "  firmware ok"

if [ "${I_UNDERSTAND_BRICK_RISK:-}" != "1" ]; then
  echo
  echo "Refusing to flash by default: firmware flashing can brick the controller."
  echo "Read README.md, put the controller in BOOT mode (it shows up as"
  echo "\"8BitDo Boot\", USB VID:PID 0x2dc8:0x3208), then run:"
  echo
  echo "  I_UNDERSTAND_BRICK_RISK=1 ./flash-firmware.sh \"$FW\""
  exit 2
fi

echo "building boot tools..."
mkdir -p build
clang -O2 -Wall -Wextra -framework IOKit -framework CoreFoundation \
  -o build/ultimate2c_boot_backup ultimate2c_boot_backup.c
clang -O2 -Wall -Wextra -framework IOKit -framework CoreFoundation \
  -o build/ultimate2c_boot_flash ultimate2c_boot_flash.c

mkdir -p backups
BACKUP="backups/controller-flash-$(date +%Y%m%d-%H%M%S).bin"
echo "backing up current controller flash to $BACKUP ..."
if ./build/ultimate2c_boot_backup 0x6000 0x12400 "$BACKUP"; then
  echo "  backup saved ($(stat -f%z "$BACKUP") bytes)"
else
  echo "ERROR: backup failed - refusing to flash." >&2
  exit 1
fi

echo
echo "flashing $FW ..."
./build/ultimate2c_boot_flash "$FW"

echo
echo "Done. The controller resets into normal firmware on its own."
echo "Re-pair Bluetooth before testing (forget the device first if it was"
echo "paired before, so macOS re-reads the GATT database)."
