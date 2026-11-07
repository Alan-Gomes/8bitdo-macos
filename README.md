# 8BitDo Ultimate 2C Wireless: native Xbox controller firmware for macOS

This project makes an 8BitDo Ultimate 2C Wireless (Bluetooth mode) work as a
fully supported controller on macOS, with no drivers, no shims and no daemons.
A firmware patch changes what the controller presents to the host:

- it introduces itself as a **Microsoft Xbox Elite Wireless Controller
  (Bluetooth)**, an identity macOS's GameController framework supports, and
- it emits input reports in the **real Xbox Bluetooth wire format**, so every
  control lands where Apple's driver expects it, and
- it repairs the rumble path, so Apple's haptics stack (and every game that
  uses it) can drive the motors.

Verified on macOS (Apple Silicon, macOS 27).

## Disclaimer

**This project was developed with AI assistance**, including the firmware
reverse engineering, the patch tooling and the boot tools. Review the code
before running it.

Flashing modified firmware is done entirely at your own risk. The author is
not responsible for a bricked controller, lost data, damaged hardware, or any
other consequences of using this project. The official 8BitDo updater can
always restore stock firmware over USB, and `flash-firmware.sh` always takes a
backup first, but that is not a guarantee of recovery.

## Requirements

- 8BitDo Ultimate 2C Wireless controller (model `0x301B`)
- The official 1.06 beta firmware as input (the only supported base image),
  or, for the "Black Myth: Wukong" edition, that edition's own factory image
  (see [Black Myth: Wukong edition](#black-myth-wukong-edition))
- macOS with Xcode command line tools (`clang`) to build the tools

## Usage

### Quick start: flash the prebuilt firmware

Download the patched firmware:

```sh
curl -LO https://github.com/Alan-Gomes/8bitdo-macos/releases/download/0.02/ultimate2c-1.06beta-xbox-full.dat
```

Then follow [Flashing](#flashing) below.

### Flashing

1. Put the controller in BOOT mode: hold **LB + RB** while plugging in the USB
   cable. It enumerates as "8BitDo Boot".

2. Flash:

   ```sh
   I_UNDERSTAND_BRICK_RISK=1 ./flash-firmware.sh ultimate2c-1.06beta-xbox-full.dat
   ```

   The script verifies the firmware hash, builds the boot tools, backs up the
   controller's current flash to `backups/`, then writes the new firmware and
   verifies the readback byte-for-byte.

3. Forget and re-pair the controller in System Settings, under Bluetooth.

4. Test. It appears in System Settings as a gamepad and works in any game
   that accepts an Xbox controller. If it does not, check that it was
   re-paired after flashing.

To restore stock firmware, flash the unmodified 1.06 beta or any `backups/`
image with `./build/ultimate2c_boot_flash` while the controller is in BOOT
mode.

### Build it yourself

1. Download the 1.06 beta firmware:

   ```sh
   curl -LO https://github.com/Alan-Gomes/8bitdo-macos/releases/download/firmware-ultimate-2c-v1.06-beta/8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
   ```

2. Build the patched firmware (verifies the input and output hashes):

   ```sh
   ./patch-xbox-full-firmware.sh 8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
   ```

   Output: `ultimate2c-1.06beta-xbox-full.dat`. It matches the prebuilt
   download above byte for byte (same SHA-256), so it can be flashed with the
   same [Flashing](#flashing) steps.

### Black Myth: Wukong edition

The Wukong edition is the same controller (model `0x301B`, same USB bootloader)
with its own firmware build: the PnP ID is `2DC8:301B` instead of `2DC8:6002`,
the version constants differ, and every table after file offset `0x8DC0` sits
16 bytes earlier. The 1.06 beta build above is *not* the right base for it.
On the unit this was developed on, Bluetooth did not work at all with any
firmware, stock included: the mode-switch check always reported 2.4G, so the
BLE stack was never initialised and nothing was ever advertised, whichever way
the switch was set. The Wukong build fixes both:

- the full Xbox emulation patch, relocated to this image (same cave and
  handler code; only the cave's return jump is re-encoded), and
- the mode-switch check (`fw 0xC42C`) forced to "Bluetooth". **2.4G mode is
  unavailable with this build**; USB works as before.

8BitDo does not publish this image, so take it from the controller:

1. BOOT mode (hold **LB + RB** while plugging in USB), then back it up:

   ```sh
   ./build/ultimate2c_boot_backup 0x6000 0x12400 wukong-stock.bin
   ```

   (Build the tools first with `clang`, as `flash-firmware.sh` does, or run
   `flash-firmware.sh` once without `I_UNDERSTAND_BRICK_RISK` to build them.)
   The backup's SHA-256 must be
   `32153cc974dcd864f6b359c1940d31ff950cd42264d9302544b31ed2e6eeb3a5`;
   a different hash means a different factory build, and the patch script
   refuses it.

2. Build the patched firmware (verifies input and output hashes):

   ```sh
   ./patch-wukong-xbox-full-firmware.sh wukong-stock.bin
   ```

   Output: `ultimate2c-wukong-xbox-full.dat`.

3. Flash it with the same [Flashing](#flashing) steps. `flash-firmware.sh`
   accepts this build's hash too.

4. If the controller was ever paired with the Mac before (with the stock
   firmware, it pairs but is not a usable gamepad), **forget it in System
   Settings and toggle Bluetooth off and on** before pairing again. macOS
   caches the GATT database per address; with the stale 10-byte report map
   only the sticks work.

To go back to stock, flash the backup wrapped in the standard header:
`patch_ultimate2c_wukong_xbox_full.py` shows the 28-byte wrapper, or flash
any `backups/` image with `./build/ultimate2c_boot_flash` (it needs the
wrapper; the backup tool writes raw images).


## Status

| Feature | Works on macOS |
|---|---|
| Left / right stick (analog, full range) | yes |
| LT / RT (analog) | yes |
| A B X Y | yes |
| LB / RB | yes |
| + / − (Menu / Options) | yes |
| Home | yes |
| L3 / R3 (stick clicks) | yes |
| D-pad (all eight directions) | yes |
| Rumble (CoreHaptics / GameController haptics) | yes |
| L4 / R4 extra bumpers | wire only, see below |

The L4/R4 buttons are encoded correctly on the wire (Xbox Elite paddle byte,
verified with a packet capture) and work in SDL-based readers, but macOS's
GameController framework does not expose paddle elements for this emulated
device (`GCXboxGamepad.paddleButton1-4` are nil; Apple only surfaces them
after its genuine Xbox Elite handshake, which this patch does not replicate).

Everything else about the controller is stock: the 2.4 GHz connection mode,
USB mode, button remapping and turbo combos are untouched. Only the Bluetooth
presentation changed.

## Checksums

| File | SHA-256 |
|---|---|
| Input: official 1.06 beta (`0x301B`) | `11c8d195d3ca76da24a33ba92ad9d7ae8ad6f455244864bed4c66898e9074bfb` |
| Output: patched firmware | `18e2c710def1832177b17dbade1f81b9f2aa8c11bf7e5ce71567d0e6d05f34c1` |
| Input: Wukong edition factory image, raw boot backup | `32153cc974dcd864f6b359c1940d31ff950cd42264d9302544b31ed2e6eeb3a5` |
| Input: same, with the 28-byte `.dat` wrapper | `f310f98189058148077e96c35efa5f94fbdbe2740b6fd1c07684c00c6d0a16d4` |
| Output: Wukong edition patched firmware | `66fc9538e4cfa9e0596f3e94a78f9c5b5c6a4e2f19968724b663d5befb342a28` |

`patch-xbox-full-firmware.sh` refuses any input that does not match the first
hash and any output that does not match the second. `flash-firmware.sh`
flashes only the second and refuses everything else.

## Documentation

- [docs/firmware-patch.md](docs/firmware-patch.md) - what the patch changes,
  every offset, both wire formats, and the evidence behind each decision
- [docs/developing-patches.md](docs/developing-patches.md) - how the patch was
  reverse engineered (host-side measurements, locating firmware code paths,
  the rumble debugging case study), the TC32 toolchain, addressing rules,
  code caves, known firmware landmarks, reference material, and a from-zero
  checklist for new patches
