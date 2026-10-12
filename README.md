# 8BitDo Ultimate 2C firmware for macOS support

Patched firmware for the 8BitDo Ultimate 2C Wireless controller with full
macOS support.

## Disclaimer

This project was developed with AI assistance, including the firmware patch
tooling and the bootloader tools. Review the code before
running it.

Flashing modified firmware is done entirely at your own risk. The author is
not responsible for a bricked controller, lost data, damaged hardware, or any
other consequences of using this project. If you are not comfortable with
that, do not flash anything. The official 8BitDo updater can always restore
stock firmware over USB, but that is not a guarantee of recovery.

## Requirements

- 8BitDo Ultimate 2C Wireless controller (model `0x301B`)

## Usage

1. Download the patched firmware:

   ```sh
   curl -LO https://github.com/Alan-Gomes/8bitdo-macos/releases/download/0.01/ultimate2c-1.06beta-macos.dat
   ```

   `flash-firmware.sh` verifies its SHA-256 before flashing, so a corrupted or
   wrong download is caught. To build the patched firmware yourself instead,
   see Building from source.

2. Put the controller in BOOT mode. Hold LB and RB and connect the USB cable.

3. Flash:

   ```sh
   I_UNDERSTAND_BRICK_RISK=1 ./flash-firmware.sh ultimate2c-1.06beta-macos.dat
   ```

   The script verifies the firmware hash, builds the boot tools, backs up the
   controller's current flash to `backups/`, then writes the new firmware and
   verifies the readback byte-for-byte.

4. Re-pair Bluetooth. If the controller was paired before, forget it first in
   System Settings, under Bluetooth.

5. Test in a game. The controller should work fully on macOS with nothing else
   installed.

## Building from source

To build the patched firmware from the official 1.06 beta:

1. Download the 1.06 beta firmware:

   ```sh
   curl -LO https://github.com/Alan-Gomes/8bitdo-macos/releases/download/firmware-ultimate-2c-v1.06-beta/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
   ```

2. Patch it:

   ```sh
   ./patch-firmware.sh 8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
   ```

   The script verifies the SHA-256 of both the input and the output. Output:
   `ultimate2c-1.06beta-macos.dat`.

## Checksums

| File | SHA-256 |
|---|---|
| Input: official 1.06 beta (`0x301B`) | `11c8d195d3ca76da24a33ba92ad9d7ae8ad6f455244864bed4c66898e9074bfb` |
| Patched firmware, released and built | `804d6475624c6e9b76381bd40391605bb757c25a84b326a5b4701c2d89523cb6` |

`patch-firmware.sh` refuses any input that does not match the first hash and
any output that does not match the second. `flash-firmware.sh` refuses to flash
anything that does not match the second, including the released download.

## What the patch changes

The 1.06 beta works correctly as a controller on macOS, but two things keep
macOS and game libraries from treating it as a fully supported device:

1. It advertises USB product ID `0x6002`, which game controller libraries do
   not recognize. They fall back to a generic driver with limited support.
2. macOS refuses to deliver output reports. The GATT attribute is declared one
   byte long and its report reference points at RAM that is not populated at
   pairing time.

The patch applies four changes to the firmware:

- The product ID changes from `0x6002` to `0x6101`, the ID of the 8BitDo SN30
  Pro BT. Game controller libraries recognize that device, claim the
  controller, and probe for feature report 6.
- Feature report 6 goes on the unused boot-keyboard output slot (handle
  `0x14`), read-only, 8 static bytes, report reference `[06 03]`. Capability
  probes succeed and libraries see the controller's full feature set.
- Output report 5 becomes usable. Its attribute length grows from `1` to `4`
  and its report reference points at static `[05 02]`. macOS now delivers
  `IOHIDDeviceSetReport(5, ...)`, the standard output report for this device
  family.
- A motor handler (68 bytes, in the post-CRC trailer) routes the output report
  to the firmware's motor scheduler, so rumble commands from games reach the
  motors.

The patch leaves the two real input characteristics untouched, so buttons,
sticks and triggers behave like stock 1.06.

All changes are data-only: table entries, the HID report map (moved to the
trailer and extended), and a code cave. The patch script recomputes the Telink
CRC and validates every site against the original bytes before writing.

## Recovery

Flashing can brick the controller; that is why `flash-firmware.sh` refuses to
run without `I_UNDERSTAND_BRICK_RISK=1` and always takes a backup first.

- **Back to stock beta.** Flash the original 1.06 beta `.dat` with the boot
  tool directly. The boot tool validates the firmware header; the hash check
  in `flash-firmware.sh` applies only to that script:

  ```sh
  ./build/ultimate2c_boot_flash /path/to/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
  ```

- **Back to official release.** The official 8BitDo updater app can reflash
  v1.09 over USB at any time.
- **From your own backup.** `backups/` contains the flash image the controller
  had before flashing.

## Notes and limitations

- The battery level games see is wrong, because the report is 11 bytes and
  SDL's driver expects more. Everything else behaves like an SN30 Pro BT.
- Games that rumble through macOS GameController haptics, a DualSense-only
  API, still will not rumble. SDL-based rumble works.
- Tested on one Mac and one controller.
