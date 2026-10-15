# The firmware patch

This document describes exactly what `patch_ultimate2c_xbox_full.py` changes in
the 8BitDo Ultimate 2C Wireless firmware, why each change exists, and how it
was verified. It is the reference for the current patch; for the workflow used
to develop firmware changes, see [developing-patches.md](developing-patches.md).

## Why a firmware patch at all

macOS routes game controllers through two paths:

1. The GameController framework, which Apple-native games use. It claims a
   fixed allowlist of devices (Xbox, PlayStation, Nintendo Switch, MFi),
   matched by USB vendor and product ID in
   `AppleGameControllerPersonality.kext`. Anything else is visible as a HID
   device but never appears to games.
2. IOKit HID directly, which SDL and other libraries use. This path is generic
   and works with almost anything.

The Ultimate 2C in Bluetooth mode was on neither path in a useful way: its
vendor/product ID is not on the GameController allowlist, so the framework
never claims it. Apple's kernel does still attach GameController-support code
to any device whose product ID appears in the allowlist — but even for those,
the driver parses input reports with the **fixed Xbox Bluetooth layout**, and
the controller's stock reports do not match it. Relabeling alone is not
enough; the wire data must change too.

One hardware fact constrains the solution: the controller's radio is a Telink
B80, which speaks Bluetooth Low Energy and 2.4 GHz proprietary protocols, but
not Bluetooth Classic. The allowlisted identities that require Bluetooth
Classic (Nintendo Switch Pro Controller, DualShock 4, DualSense) are therefore
unreachable. The Xbox controllers are the allowlisted family that speaks BLE,
so the patch makes the 2C present as one.

## The base image

The stock 1.06 beta firmware is a 74,780-byte `.dat`:

| Region | File offset | Notes |
|---|---|---|
| Wrapper | `0x00..0x1C` | 28-byte header |
| Payload | `0x1C..0x12400` | 74,752 bytes; `KNLT` tag at payload+8 |
| Covered by CRC | payload start to `0x1233C` | `hsize` (payload+0x18) is `0x12324`; the CRC is stored at `hsize-4` |
| Trailer | `0x12340..0x1241C` | 220 zero bytes, **not** covered by the CRC |

Firmware addresses in disassembly map to file offsets as
`file = fw - 0x6000 + 0x1C`. Note that the trailer can be rewritten freely
without recomputing the CRC; anything inside the CRC-covered region cannot.

## What the patch changes

| # | Change | File offset | Stock | Patched |
|---|---|---|---|---|
| 1 | Bluetooth PnP ID | `0x11488` | `02 c8 2d 02 60 01 00` | `02 5e 04 13 22 0b 01 00` |
| 2 | HID report map | `0x11414` | 116-byte stock map | 98-byte Xbox map |
| 3 | GATT map length | `0x11868` | `0x74` (116) | `0x62` (98) |
| 4 | Input notify hook | `0x5B38` | `notify(0x40, 0x1D, sp, 10)` call | `tjl 0x12340` + 4 nops |
| 5 | Code cave | `0x12340` | zeros | 132 bytes of TC32 |
| 6 | Motor handler | `0x123C4` | zeros | 18 bytes of TC32 |
| 7 | Static report reference | `0x123D6` | zeros | `03 02` |
| 8 | Output attr length | `0x11820` | `1` | `8` |
| 9 | Output write callback | `0x1182C` | `0` | `0x183A9` |
| 10 | Output report reference | `0x11840` | `0x842E14` (RAM) | `0x183BA` (static) |
| 11 | Telink CRC | `0x1233C` | recomputed | recomputed |

### 1. Identity: the PnP ID

The PnP ID GATT characteristic value is seven bytes: a vendor ID source
(`02`, USB SIG), the vendor ID little-endian, the product ID little-endian,
and a two-byte version. The stock controller advertises video
`0x2DC8`/`0x6002`; the patch writes `0x045E` (`045E:0B22`, Microsoft Xbox
Elite V2 Controller, Bluetooth). macOS then attaches its Xbox gamepad driver
to the device: `GameControllerCategory = xbox`, and the GameController
framework lists it (name aside) as an Xbox controller.

The Elite product ID was chosen over the plain Series X one (`0x0B13`)
because Apple documents extra paddle buttons only for Elite-class controllers;
this turned out to be necessary but not sufficient for paddle support (see
"Paddles" below).

### 2. Report map

The HID report map the device serves over GATT describes the input report:
four 16-bit stick axes (X Y Rx Ry), two 16-bit trigger axes (accel/brake),
a 4-bit hat with logical range 1..8, sixteen buttons, four vendor-page bytes
(the Elite report tail, where the paddles live), and an 8-byte output report
3 for rumble. Nineteen input data bytes plus report ID 1 match the real Xbox
Elite Bluetooth report length. It replaces the stock 116-byte map in place;
the GATT attribute's length field (change 3) tells the host the map is 98
bytes.

The stock map is a near-miss of the Xbox layout: same byte offsets for the
sticks and triggers, but 8-bit fields instead of 16-bit, a hat declared 0..7,
and no fields where the button bytes or the Elite tail belong. Field widths
must match what the cave emits byte for byte; a mismatch truncates reports
(the BLE stack drops whatever the map does not declare) and controls
silently die.

### 3-5. Input reports: the code cave

The stock firmware builds a 10-byte input payload on the stack of its report
builder (file `0x59CC`) and notifies it on the input characteristic (handle
`0x1D`) with length 10. The patch replaces that notify call site with a jump
into a code cave and reimplements the whole conversion there.

The hook overwrites exactly the 12 bytes of the notify call with
`tjl 0x12340` plus four `nop`s and changes nothing else. The cave:

1. allocates 32 bytes of stack, keeping a pointer to the stock payload
   (`r5 = sp + 32`, exactly the caller's `sp`, where the stock bytes sit),
2. writes the 19-byte Xbox payload into the new buffer (`r4 = sp`), and
3. calls the firmware's own notify function (`0xE654`, signature
   `(conn, handle, ptr, len)`) with `(0x40, 0x1D, buffer, 19)`,
4. restores the stack and jumps back to the instruction after the hook
   (`0x5B44`).

The two payload formats:

**Stock 10-byte payload** (as built by the firmware):

```
[0] hat: 0..7 clockwise from up, 15 = released
[1] LX  [2] LY  [3] RX  [4] RY   8-bit, 0x7F = center
[5] RT  [6] LT                   8-bit
[7] buttons low:  bit0 A, bit1 B, bit2 L4, bit3 X, bit4 Y, bit5 R4, bit6 LB, bit7 RB
[8] buttons high: bit2 minus, bit3 plus, bit4 Home, bit5 L3, bit6 R3
[9] battery level
```

**Xbox 19-byte payload** (as written by the cave; the BLE host prepends
report ID 1, producing 20-byte HID reports):

```
[0..1]   X    (left stick X)    16-bit LE, the 8-bit value in both bytes
[2..3]   Y    (left stick Y)
[4..5]   Rx   (right stick X)
[6..7]   Ry   (right stick Y)
[8..9]   accel (LT)             8-bit value shifted left 2, 10-bit in 16-bit field
[10..11] brake (RT)
[12]     hat: 1..8 clockwise from up, 0 = released (stock 15 maps to 0)
[13]     buttons low  = stock[7] & 0xDB   (A B X Y LB RB)
[14]     buttons high = stock[8] & 0x7C   (minus plus Home L3 R3)
[15]     paddles: L4 -> bit0, R4 -> bit1
[16]     paddles, repeated (the paddle byte position differs between references)
[17]     0 (profile)
[18]     0 (trigger settings)
```

The button windows in bytes 13/14 are the measured truth: Apple's driver
reads exactly the bits a real Xbox controller uses there, and the stock
firmware's button bits land elsewhere. The masks keep the stock bit positions
that coincide (and drop L4/R4 from the main bytes; they go to the paddle
byte). The exact byte-to-element mapping was validated both against hardware
captures and against xpadneo's documentation of the Xbox Bluetooth layout,
which is the same layout Apple's driver follows.

### 6-10. Rumble

The stock output characteristic was unusable for rumble: its length was one
byte, and its report reference pointed at a RAM location (`0x842E14`) that is
not populated when macOS reads the GATT database at pairing time. macOS
therefore never wrote output reports to the device.

The patch repairs the path end to end:

- the characteristic's length grows to 8 bytes (change 8),
- its report reference points at a static `[03 02]` in the trailer
  (report 3, output; changes 7 and 10), and
- its write callback runs a new TC32 handler (change 9) that reads the
  packet's two main motor bytes and calls the firmware's own motor scheduler
  (file `0x9B30`, called with `(left, right, 30)`).

The packet format is the one SDL sends to Xbox Bluetooth controllers:

```
[03 0F LT RT LOW HIGH FF 00 EB]      report 3, as written on the wire
```

The value bytes delivered to the write callback start after the report ID
and begin at buffer offset 9 (measured with a probe handler that vibrated on
a single buffer byte, cross-checked against packet sweeps). The handler
therefore reads `buffer[12]` (LOW) and `buffer[13]`
(HIGH) and feeds those to the scheduler — the same two bytes a genuine Xbox
controller's rumble would drive.

Verified on macOS: both Apple's CoreHaptics path (the one games use, via
`GCDeviceHaptics`) and direct `IOHIDDeviceSetReport` writes spin the motors.

### Paddles (L4 / R4)

L4 and R4 are readable in the stock payload (bits 2 and 5 of the buttons-low
byte) and are written to the Xbox paddle byte (bits 0 and 1 of bytes 15/16,
verified by capture). They work in SDL-based readers.

macOS's GameController framework, however, does not expose paddle elements
for this device: `GCXboxGamepad.paddleButton1-4` are nil. Apple only surfaces
the Elite's extra buttons after its private handshake with a genuine Elite
(profile feature reports), which this patch does not replicate. Until that is
reverse-engineered, L4/R4 remain wire-only on macOS.

## What stays stock

Everything not listed above: a second copy of the stock map bytes elsewhere in
the image (`0x11C79`) is left untouched (which table, if any, references it
was not established), all other GATT entries, the boot keyboard
characteristic, battery reporting, button remapping and turbo features, USB
and 2.4 GHz behaviour. The patch is data plus two small code additions; the
main firmware logic is untouched.

## Rebuilding and verifying

```sh
./patch-xbox-full-firmware.sh 8bitdo-ultimate-2c-wireless_v1.06-beta_model-0x301B.dat
```

The script refuses any input whose SHA-256 is not the official 1.06 beta and
any output whose SHA-256 is not the known-good result
(`18e2c710def1832177b17dbade1f81b9f2aa8c11bf7e5ce71567d0e6d05f34c1`). To
inspect a build, disassemble with the tc32 toolchain (see
[developing-patches.md](developing-patches.md)) and compare with the offsets
table above.
