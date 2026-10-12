# 8bitdo-patched

Patched firmware for the 8BitDo Ultimate 2C Wireless controller with full
macOS support.

## Requirements

- 8BitDo Ultimate 2C Wireless controller (model `0x301B`)
- The official 1.06 beta firmware for it, available from the
  [firmware-ultimate-2c-v1.06-beta release](https://github.com/fanatic75/8bitdo-mac-flash/releases/download/firmware-ultimate-2c-v1.06-beta/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat)
  (see the checksums below)
- macOS with Xcode command line tools (to build the boot tools)

## Usage

1. Download the 1.06 beta firmware if you do not have it:

   ```sh
   curl -LO https://github.com/fanatic75/8bitdo-mac-flash/releases/download/firmware-ultimate-2c-v1.06-beta/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
   ```

2. Patch it:

   ```sh
   ./patch-firmware.sh 8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
   ```

   The script verifies the SHA-256 of both the input and the output. Output:
   `ultimate2c-1.06beta-native-rumble.dat`.

3. Put the controller in BOOT mode. It must enumerate as `8BitDo Boot`
   (USB VID:PID `0x2dc8:0x3208`). The tools refuse anything else.

4. Flash:

   ```sh
   I_UNDERSTAND_BRICK_RISK=1 ./flash-firmware.sh
   ```

   The script verifies the firmware hash, builds the boot tools, backs up the
   controller's current flash to `backups/`, then writes the new firmware and
   verifies the readback byte-for-byte.

5. Re-pair Bluetooth. If the controller was paired before, forget it first in
   System Settings, under Bluetooth.

6. Test in a game. The controller should work fully on macOS with nothing else
   installed.

## Checksums

| File | SHA-256 |
|---|---|
| Input: official 1.06 beta (`0x301B`) | `11c8d195d3ca76da24a33ba92ad9d7ae8ad6f455244864bed4c66898e9074bfb` |
| Output: patched firmware | `804d6475624c6e9b76381bd40391605bb757c25a84b326a5b4701c2d89523cb6` |

`patch-firmware.sh` refuses any input that does not match the first hash and
any output that does not match the second. `flash-firmware.sh` refuses to flash
anything that does not match the second.

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
- The patched firmware derives from 8BitDo's firmware, so do not redistribute
  it. The patch tooling here is MIT, same as the research repo it came from.
