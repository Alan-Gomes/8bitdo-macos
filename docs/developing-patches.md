# Developing new firmware patches

This document assumes no prior context. It covers what the device is, how the
Xbox emulation patch was developed end to end, which tools worked, how to
reverse engineer a new code path, and how to flash and test safely. Read
[firmware-patch.md](firmware-patch.md) alongside it for the concrete changes
the current patch makes.

## The device

The 8BitDo Ultimate 2C Wireless (model `0x301B`) is a game controller with:

- a **Telink B80** SoC (TLSR8208 class): a self-developed TC32 CPU core, plus
  Bluetooth Low Energy and 2.4 GHz proprietary radios. **No Bluetooth
  Classic**, which rules out ever presenting as a Switch Pro Controller or a
  DualShock/DualSense (they pair over Classic).
- two connection modes: **Bluetooth Low Energy** (a GATT HID device) and
  **2.4 GHz** (a proprietary link to a USB dongle). The patch only touches
  the BLE presentation; 2.4 GHz and USB behaviour are stock.
- a USB bootloader that enumerates as `2DC8:3208` ("8BitDo Boot") when the
  controller powers up with **LB + RB held**. This is the flash path.

The firmware ships as a 74,780-byte `.dat` file. Over BLE the controller
exposes a standard GATT HID service: a report map, two input report
characteristics, an output characteristic, plus the PnP ID characteristic
that tells the host "what" the device is. Firmware addresses in disassembly
maps to file offsets as `file = fw - 0x6000 + 0x1C`; the 28-byte file wrapper
and the 0x6000 base are what account for the difference.

## The problem the current patch solves

macOS routes game controllers through two paths. **IOKit HID** is generic and
sees almost any device; **GameController.framework** is what Apple-native
games use, and it claims a fixed allowlist of devices, matched by vendor and
product ID in
`/System/Library/Extensions/AppleGameControllerPersonality.kext`
(`IOKitPersonalities` entries with `IOProviderClass IOHIDInterface`,
`VendorID`, `ProductID`). Inspect it with `plutil -convert xml1 -o - ...`.

The Ultimate 2C's vendor/product ID is not on that list, so the framework
never claims it, and every Apple-native game ignores it. Worse, even after
changing the identity to something allowlisted (an Xbox PID), the framework
**parses input reports with the fixed Xbox Bluetooth layout regardless of
what the device's own HID report descriptor says** — measured, not assumed,
by logging raw HID bytes and GameController element values side by side and
fitting every possible byte/encoding combination against the observations.
The stock 10-byte reports put buttons where the Xbox parser reads axis bytes,
so nothing binds.

Conclusion, after measurement: making the controller work for
GameController.framework is not an identity problem alone. The **wire data
must be rewritten in firmware** to the Xbox format. That is what the patch
does: a small amount of injected TC32 code converts the stock report into
the Xbox report on every frame.

## How the patch was developed (case study)

The workflow in full, because it generalizes to any future patch.

### 1. Establish host-side truth first

Before touching firmware, measure exactly what the host does:

- `ioreg -c IOHIDDevice -r -l -w0` shows every HID device and the properties
  macOS attached. After the identity change this is where
  `GameControllerCategory = xbox` and `GameControllerSupport = Yes` appear —
  proof the personality kext matched.
- Query GameController.framework directly (a small Objective-C program):
  `[GCController controllers]` lists claimed devices; comparing a device that
  is claimed against one that is not tells you whether a change worked.
  Note the list is **asynchronous**: poll it for a few seconds before
  concluding anything.
- Log raw HID reports (`IOHIDDevice` input report callback) and framework
  values together, one press at a time, to learn the parse.

For the fit between raw bytes and framework values, brute force beats
guesswork: for every element (each stick axis, each trigger), try every byte,
byte pair, endianness, encoding and sign, and score the mean absolute error
against what the framework reported. The winning candidate *is* the layout.
This is how the Xbox layout was confirmed: `LeftX` = report byte 2 (16-bit
pair high byte), `LeftY` = byte 4, `RightX` = byte 6, `RightY` = byte 8,
triggers from byte 9, hat byte 12, buttons bytes 13/14.

### 2. Find the firmware code path to change

Disassemble the stock firmware (see "Disassembling" below) and search for
anchors. What worked here, in the order it worked:

1. **Handle constants.** The GATT table lists the input characteristic at
   handle `0x1D` (29). Grepping for `tmovs rX, #29` finds every place the
   firmware mentions it. Most hits were arithmetic idioms, but one cluster
   set up registers and called a function — that is a notify call site.
2. **Protocol bytes.** The ATT "Handle Value Notification" opcode is `0x1B`
   (27). Searching `tmovs rX, #27` found the packet builder that writes
   `[1B][handle lo][handle hi][value...]`. That function is the generic
   notify routine, at file `0xE654`: `r0` = connection marker (`0x40`),
   `r1` = handle, `r2` = value pointer, `r3` = value length.
3. **Callers.** Grepping `tjl 0xE654` (and the file-offset equivalent) found
   exactly two callers. One passed handle 29 and length 10 with the payload
   pointer on the stack — the input report notify, at file `0x5B40`. The
   surrounding function (entry `0x59CC`, `tpush {r4,r5,lr}` then
   `tsub sp, #24`) is the input report builder.
4. **The stack tells you where the data is.** That builder writes its
   10-byte payload at `sp` and passes `sp` to the notify call. When the hook
   replaces the call, `sp` still points there: the cave first lowers `sp` by
   32 bytes for its own work buffer, so adding 32 back recovers the caller's
   `sp` — the stock payload.

### 3. Understand both data formats

Two formats mattered: what the firmware produces (stock) and what the host
expects (Xbox). The stock layout was nailed down by pressing one control at
a time and watching the raw report change — plus reading the builder's
disassembly to confirm. The Xbox layout came from the host-side fitting
above, cross-checked against two public references: SDL's
`SDL_hidapi_xboxone.c` (packet ids, rumble packet format) and xpadneo's
protocol documentation (descriptor, button bit positions, paddle byte).

The two formats are documented side by side in
[firmware-patch.md](firmware-patch.md#3-5-input-reports-the-code-cave).

### 4. Inject code: the cave

The stock firmware has 220 bytes of unused space after its CRC region (file
`0x12340..0x1241C`) — zero-filled, not covered by the checksum, and large
enough for several cave routines. The patch:

1. overwrites the input notify call with a jump to the cave,
2. the cave reads the stock 10-byte payload and builds the 19-byte Xbox
   payload next to it (the report map was separately replaced so the BLE
   stack declares — and therefore delivers — the larger report),
3. calls the firmware's own notify function with the new payload, and
4. jumps back to the instruction after the hook.

Everything else — the GATT table, the report map, the input/notify plumbing —
is reused, not reimplemented. Prefer that: the less stock code you replace,
the fewer ways the firmware can break.

### 5. The rumble path (a worked example of the loop)

Rumble was dead because the output report characteristic was unusable: one
byte long, with a report reference pointing at RAM that is not populated at
pairing time. macOS therefore never wrote output reports. The fix repointed
the reference at a static `[03 02]`, grew the length to 8, and installed a
write callback (new handler code in the trailer) that feeds the firmware's
motor scheduler.

Debugging that path is the best example of the method:

1. Direct writes (`IOHIDDeviceSetReport`) returned success but nothing
   moved → not a framework problem.
2. An **always-rumble handler** (ignores its arguments; rumbles on any call)
   vibrated → the callback was wired correctly; the data reading was wrong.
3. A **probe handler** (rumble iff one specific buffer byte is nonzero) plus
   a numbered sweep on the host (each packet sets exactly one byte) mapped
   the callback buffer: value byte 0 arrives at buffer offset 9, so
   `value[3]`/`value[4]` (the motor bytes SDL sends) are `buffer[12]`/
   `buffer[13]`.
4. One sweep crashed the controller at the packet carrying a motor byte.
   Comparing the crashing handler against an inert one proved the crash was
   in the handler's call to the motor scheduler — and comparing against the
   stock firmware's own call pattern revealed the bug: the cave had been
   linked in file-offset space but called the scheduler by its **firmware
   address**. See "Two address spaces" below. Fixed, rumble worked
   immediately: both direct writes and Apple's CoreHaptics path.

### 6. Keep the patch reproducible

Every patch script here follows the same shape, and future ones should too:

- validate every patch site against the expected stock bytes before writing
  (refuse to patch anything unexpected),
- apply only the documented changes,
- recompute the Telink CRC (only needed for changes inside the CRC region,
  but harmless always),
- print every change with its offset, and
- pin both the input and the output SHA-256 in the wrapper script so a
  corrupted or wrong file can never be flashed by mistake.

## The experimental loop

1. Edit the patch script.
2. Build: `./patch-xbox-full-firmware.sh <stock.dat>`.
3. Verify the diff locally before flashing: rebuild, check the hash matches
   the pinned one (for the current patch), or diff the `.dat` against the
   previous build and confirm only intended regions changed.
4. **BOOT mode:** hold LB + RB while plugging in USB ("8BitDo Boot").
5. Flash: `I_UNDERSTAND_BRICK_RISK=1 ./flash-firmware.sh <file.dat>`. The
   script verifies the hash, backs up the current flash to `backups/`, writes
   and verifies the readback. Keep the backups.
6. Wake the controller (Home). Re-pair rules:
   - Report map, identity, or GATT changes → **forget and re-pair** in
     System Settings, or macOS serves its cached GATT database and you test
     stale data.
   - Trailer-only changes (handlers, caves) → just reconnect; no re-pair.
7. Test the actual target: the game, or a small host program that exercises
   the path you changed.
8. Iterate. Each loop is a couple of minutes once the boot tools are built.

## Tools: what worked, what did not

### tc32-elf-objdump — the workhorse

The official Telink toolchain, distributed as prebuilt binaries in the
`flyskywhy/tc32` repository on the `macos` branch (Mach-O x86_64; runs under
Rosetta on Apple Silicon):

```sh
git clone -b macos --depth 1 https://github.com/flyskywhy/tc32.git /tmp/tc32-toolchain
```

Its `tc32-elf-objdump` disassembles a raw `.dat` directly
(`-D -b binary -m tc32`) with no ELF wrapping and decodes the 32-bit
instructions (calls) correctly — which is what the whole RE effort needed.
`tc32-elf-as`, `tc32-elf-ld` and `tc32-elf-objcopy` assemble and link the
caves. Setup: none. This is all you need.

### Ghidra — installed, tried, superseded

Early on, a minimal Python decoder (hand-built from the public TC32 opcode
tables in `rgov/Ghidra_TELink_TC32` and the `tc32-asm.py` gist) decoded
16-bit instructions but not the 32-bit `tjl` calls, leaving holes exactly
where the interesting code was. The "proper" answer looked like Ghidra plus
a TC32 processor module (`leommxj/ghidra_tc32`), so Ghidra was installed
(`brew install ghidra`, which also pulls a JDK).

Before going further, `tc32-elf-objdump` (above) was found and disassembled
the entire image perfectly with zero setup. **Ghidra was never actually used**
on this project, was uninstalled afterwards, and can be skipped entirely.
Bring it back only if you need interactive cross-referencing or decompilation
of larger functions; the tc32 processor module exists if you do. For this
firmware's small, mostly-linear code paths, objdump plus grep was faster.

### The Python decoder

A partial TC32 decoder lived in this repo for quick greps but was removed
with the other development tools. If you want one, assemble it from
`rgov`'s opcode gist; be aware it only covers the 16-bit forms unless you
add the 32-bit encodings.

### Reference material used

| Source | What it provided |
|---|---|
| `flyskywhy/tc32` (macos branch) | toolchain binaries |
| `rgov` TC32 opcode gist / `rgov/Ghidra_TELink_TC32` | instruction encodings |
| `atar-axis/xpadneo` `docs/descriptors/xbxs.md`, `xbe2_*.md` | Xbox BT descriptor, packet layout, paddle byte, button bits |
| `libsdl-org/SDL` `SDL_hidapi_xboxone.c` | Xbox BT packet ids, rumble packet format `[03 0F LT RT LOW HIGH FF 00 EB]` |
| Apple SDK headers `GCXboxGamepad.h`, `GCDeviceHaptics.h` | paddle and haptics APIs the host exposes |
| `/System/Library/Extensions/AppleGameControllerPersonality.kext` | the actual VID/PID allowlist macOS matches |
| Telink B80 SDK strings in the firmware image | SoC family confirmation |

### Host-side inspection cheatsheet

```sh
# what macOS thinks the device is
ioreg -c IOHIDDevice -r -l -w0 | grep -E 'Product|GameController|VendorID|ProductID'

# Bluetooth-level view (PID changed? connected?)
system_profiler SPBluetoothDataType | sed -n '/Connected/,/Not Connected/p'

# flash mode detection
ioreg -p IOUSB -w0 | grep "8BitDo Boot"
```

For GameController questions, a tiny Objective-C program beats any CLI:
`[GCController controllers]` (poll it — asynchronous), then read
`vendorName`, `productCategory`, `extendedGamepad`, and for Xbox profiles
the `GCXboxGamepad` extras (paddles, share).

## Two address spaces, and the rule

The firmware image occupies two address systems:

- **File offsets** — positions in the `.dat` file. All diffing, patching and
  (by convention here) disassembly use these.
- **Firmware addresses** — what the CPU executes at: `fw = file - 0x1C + 0x6000`.

The critical rule: **assemble and link every code addition in file-offset
space.** Relative jumps (`tjl`, `tj`, conditional branches) encode an offset
from the instruction, and the linker resolves a symbol by subtracting the link
base. If you link a cave at a file offset but call a target by its firmware
address, the encoded offset is off by exactly `0x6000 - 0x1C`, the branch
lands in unrelated memory, and the firmware hard-faults at the moment that
code runs.

This actually happened: a rumble handler calling the motor scheduler by its
firmware address (`0xFB14`) crashed the controller the instant a nonzero
motor byte arrived, while the identical handler copied instruction-for-
instruction from the stock firmware worked, because the stock code's literal
held the firmware address and used an indirect call. When in doubt, verify
where a branch lands with `objdump` before flashing:

```sh
/tmp/tc32-toolchain/bin/tc32-elf-objdump -d cave.elf | grep -A1 tjl
```

If you need to call something through a firmware address (for example to
mirror existing code exactly), put the address in a literal and use the
indirect form:

```asm
    tloadr r3, [pc, #8]     ; literal below
    tjl    tramp            ; call local trampoline
    ; ... return point
    tnop
lit:
    .word  0xfb14           ; the firmware address
tramp:
    tjex  r3                ; jump to r3; return address is already in lr
```

Note that immediate offsets in disassembly listings (`[pc, #8]` on a
`tloadr`) are byte offsets from the instruction, so the literal sits eight
bytes later — and the literal itself is a plain data word, not code.

## Disassembling

Point objdump at the `.dat` itself, so printed addresses equal file offsets:

```sh
tc32-elf-objdump -D -b binary -m tc32 firmware.dat > fw.dis
```

Then grep. To convert a firmware address seen in other material (datasheet,
older notes) to a file offset: `file = fw - 0x6000 + 0x1C`. The motor
scheduler, for instance, lives at file `0x9B30` = fw `0xFB14`.

Useful greppable anchors, with the caveat that objdump pads with spaces so
grep patterns need `[[:space:]]+` or fixed-string matching:

| Find | Pattern |
|---|---|
| 8-bit constants | `tmovs rX, #<n>` |
| direct calls to a function | `tjl[[:space:]]+0x<file offset>` |
| calls to an external address | `tloadr rX, [pc, #<n>]  ; (0x<file offset>)` |
| byte stores at a known offset | `tstorerb[[:space:]]+rX, \[rY, #<n>\]` |
| register-indirect calls | `tjex` |

## Assembling a cave

The exact recipe used by this project (link in **file-offset** space):

```sh
T=/tmp/tc32-toolchain/bin
$T/tc32-elf-as -o cave.o cave.s
$T/tc32-elf-ld -Ttext=0x12340 \
    --defsym=cave_notify=0xe654 --defsym=cave_back=0x5b44 \
    -o cave.elf cave.o
$T/tc32-elf-objcopy -O binary --only-section=.text cave.elf cave.bin
```

Notes:

- `--defsym` gives names to addresses inside the existing firmware; the
  linker resolves relative branches to them correctly when they are file
  offsets.
- The entry-point warning ("cannot find entry symbol _start") is harmless.
- Embed the resulting bytes in the patch script as a hex constant, and verify
  after building: disassemble the patched `.dat` at the cave's offset and
  check that the code matches what you wrote. Do this before flashing, every
  time.

### Instruction gotchas (this assembler fork)

- Immediates: `tmovs rX, #imm8` for 8-bit constants; `tmov rX, rY` for
  register moves; `tadds rX, #imm`, `tadd rX, #imm` both appear — the
  assembler accepts the non-`s` spelling for the 3-bit forms.
- Three-operand arithmetic requires the destination to be one of the sources:
  `tand r2, r1` (after `tmov r2, r0`) — writing `tand r2, r0, r1` is rejected
  with "dest must overlap one source register".
- `tjl imm` is a direct call (relative); `tjex rX` jumps to a register and is
  the accepted spelling for indirect calls; `tjl rX` is not assembled.
- `;` comments were rejected by this assembler in testing ("junk at end of
  line"); keep assembly comment-free or use the C-preprocessor route.
- 32-bit instructions (the direct `tjl`) appear as one four-byte line in
  objdump output; do not confuse them with two 16-bit instructions.
- Left shifts of 31, 30, 29, ... then arithmetic right shifts of 31 are the
  idiom for extracting one bit and sign-extending it (`bit << 31` then
  `>> 31` gives 0 or -1); masking (`tand` with a constant) then `tshftr` is
  simpler when you need a bit at a lower position.

## Finding hook sites

What worked in this project:

1. **Search for constants.** `tmovs rX, #29` finds every use of handle 0x1D.
   Numbers are the most reliable greppable anchors.
2. **Search for call targets.** `grep "tjl     0x<function>"` finds callers of
   a known function. Build a picture of the call graph one address at a time.
3. **Identify protocol builders by their bytes.** The ATT notify sender was
   found because it constructs a packet beginning `1B <handle>` (ATT Handle
   Value Notification opcode). Protocol constants are easy to spot and lead
   straight to the function that uses them.
4. **Watch the stack.** The input report builder copies 10 stock bytes to
   `sp` and passes that pointer to the notify function; the hook therefore
   sees the payload at `sp + <frame size>` when it enters.

## Code caves

- **Free space.** The 220-byte trailer (`0x12340..0x1241C`) is zero, outside
  the CRC region, and large enough for several caves. The only in-image zero
  run is 76 bytes at `0x24D0`; prefer the trailer.
- **Hook mechanics.** Overwrite the bytes of a call you want to replace with
  `tjl cave` (4 bytes) plus `nop`s to the end of the instruction span (12
  bytes was the span in our case: a four-instruction setup plus the call).
  The cave receives the exact register state of the call site, so any
  argument registers still carry their values; save `lr` and any registers
  you clobber (`tpush {r4, lr}` / `tpop {r4, pc}`).
- **Return.** End with `tjl <address after the hook>` (a file offset), not
  with a bare return, because the return address in `lr` points at the cave.
- **Stack.** If you need scratch space, `tsub sp, #N` on entry and
  `tadd sp, #N` after the final call — the notify function never returns a
  value (the stock code clears `r0` itself).

## GATT attribute table

Attribute entries are 24 bytes each, starting at file `0x11504`, struct
`<HBBIIIII>`:

| Field | Meaning |
|---|---|
| 0 | flags / unused |
| 1 | properties (0x01 read, 0x02 write, 0x03 read+write) |
| 2 | UUID length (2) |
| 3 | attribute length (value size in bytes) |
| 4 | pointer to the UUID (file address, two bytes) |
| 5 | pointer to the value (file address or RAM address) |
| 6 | write callback (firmware address, thumb bit set) |
| 7 | unused |

Scanning this table for known UUIDs (2A4D report, 2908 report reference,
2A4E report map) maps the characteristics and is the fastest way to find what
to change for a GATT-level patch. Firmware addresses in fields 4-6 use the
`fw` space; convert with `fw = file - 0x1C + 0x6000` when cross-checking
against a disassembly listing in file space.

## Known firmware landmarks

Discovered during this project; reuse them instead of re-deriving:

| Landmark | File offset | Notes |
|---|---|---|
| Input report builder | `0x59CC` | `tpush {r4,r5,lr}` + `tsub sp, #24`; builds the 10-byte stock payload at `sp` |
| Input notify call (hook site) | `0x5B38..0x5B44` | `notify(0x40, 0x1D, sp, 10)`; 12 bytes, safe to replace |
| Generic ATT notify function | `0xE654` | `(conn=0x40, handle, value ptr, length)`; builds `[1B][handle][value]` |
| Motor scheduler | `0x9B30` | `(left, right, duration)`; the known motor handler passes duration 30 |
| Report map value | `0x11414` | stock 116 bytes; GATT entry at `0x11864` |
| PnP ID value | `0x11488` | 7 bytes: source, VID LE, PID LE, version LE |
| Input characteristic value buffer (handle 0x19) | RAM `0x843170` entry at `0x1174C` | |
| Input characteristic value buffer (handle 0x1D) | RAM `0x843148` entry at `0x117AC` | the one that notifies |
| Output characteristic value entry | `0x1181C` | length at +4, write callback at +16 |
| Output report reference entry | `0x11834` | value pointer at +12 |
| CRC | stored at `0x1233C` | `hsize` 0x12324 at payload+0x18; trailer starts `0x12340` |
| Free space | `0x12340..0x1241C` | 220 bytes, outside CRC coverage |

## Recomputing the CRC

Any change inside the CRC-covered region requires this; trailer-only changes
do not (but running it unconditionally is harmless):

```python
import struct, zlib
WRAPPER = 0x1C
payload = data[WRAPPER:]
hsize = struct.unpack_from("<I", payload, 0x18)[0]
crc = (~zlib.crc32(payload[:hsize - 4])) & 0xFFFFFFFF
struct.pack_into("<I", data, WRAPPER + hsize - 4, crc)
```

The payload must still start with the `KNLT` tag at payload+8; validate it
before trusting any offsets.

## Probe techniques for unknown packet layouts

When you do not know where a written value lands in a callback buffer, or
whether a callback fires at all:

1. **Inert handler** (returns immediately): tells you the write path itself
   is safe, separating "my handler crashes" from "the write crashes".
2. **Always-on handler** (ignores arguments, does the action unconditionally):
   proves the callback fires and the downstream call works.
3. **Probe handler** (acts iff one specific buffer byte is nonzero) plus a
   **numbered sweep** on the host (packet N sets exactly one byte, N 1..8):
   the packet number that triggers the action identifies the offset. In this
   project that mapped value byte 0 to buffer offset 9, and with SDL's
   packet format it selected `value[3]`/`value[4]` = `buffer[12]`/`buffer[13]`
   as the motor bytes.

Do these in separate flashes, and keep the host sweep tool printing
one line per packet with a delay long enough to feel the motor (about
1.5 s). If the controller disconnects mid-sweep, note exactly which packet;
that is usually a firmware fault in code that ran for that byte, not a link
problem.

## Flashing details

- The bootloader enumerates as `2DC8:3208`; the flasher refuses anything
  else.
- Flashing erases and rewrites the whole 74,752-byte payload; it is not
  incremental.
- The controller resets itself when flashing finishes, disconnects from USB
  and comes back over Bluetooth (first to the previously paired identity).
- `backups/` in this repo holds full pre-flash images; restoring one is the
  same `./build/ultimate2c_boot_flash` command while the controller is in
  BOOT mode.
- Version bytes visible in the flasher output live at `0x01017040`.

## From zero: checklist for a new patch

1. **Write down the goal as a host-observable behaviour** ("GameController
   exposes paddle buttons", "the rumble motors spin", "the device appears as
   X"). Behaviour first; implementation later.
2. **Measure the host side before changing anything.** Record what happens
   today, with which tool. If you cannot observe the result, you cannot
   verify the patch.
3. **Locate the firmware code path** with the disassembly and the anchor
   techniques above (constants, protocol bytes, callers, stack).
4. **Learn both data formats** — what the firmware produces, what the host
   expects — and write them down as byte tables.
5. **Choose the smallest change.** Data-only patches (identity, GATT fields,
   report map) first; a code cave only when behaviour itself must change.
   Prefer reusing firmware functions (notify, scheduler) over reimplementing.
6. **Assemble in file-offset space**; verify branch targets with objdump;
   embed bytes in a patch script that validates every site against the stock
   image, applies the documented diff, and recomputes the CRC.
7. **Pin hashes** in a wrapper script (stock input, patched output) and diff
   old vs new builds locally before flashing.
8. **Flash with backup, then test the host-observable behaviour.** Re-pair if
   the GATT database changed.
9. **Iterate with probes** (inert / always-on / single-byte) when a path
   misbehaves.
10. **Document the offsets, formats and decisions** in these files; note what
    was tried and rejected so the next person does not repeat it.

## Hardware facts

- SoC: Telink B80 (TLSR8208 class), BLE + 2.4 GHz proprietary, no Bluetooth
  Classic.
- Flash: 74,752-byte payload in the `.dat`; bootloader enumerates as
  `2DC8:3208` ("8BitDo Boot").
- Firmware addresses: `fw = file - 0x1C + 0x6000`.
- Version bytes live at `0x01017040` (visible in the flash tool output).
- The controller resets itself after a successful flash and reconnects to
  the host over Bluetooth.
