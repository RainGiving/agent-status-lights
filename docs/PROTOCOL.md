# NuPhy Halo65 V2 lighting over VIA — measured notes

Everything here was measured against a physical NuPhy Halo65 V2 on macOS via
IOKit HID. It is not an official spec; a NuPhy firmware update could invalidate
any of it.

## Device

```text
Product     NuPhy Halo65 V2
USB VID:PID 0x19f5:0x3315
Transport   USB cable only, and both wireless paths were measured:

            Bluetooth        enumerates as "NuPhy Halo65 V2-1", keyboard
                             interfaces only, no 0xFF60/0x61. via_scan finds
                             nothing and halo65_ledctl exits 2.
            2.4G receiver    "NuPhy Halo65 V2 Dongle" 0x19f5:0x3247 offers two
                             interfaces, 0x0001/0x06 (keyboard) and 0xFF31/0x74
                             (QMK console). No VIA channel either.
```

Four HID interfaces are present. There is **no** vendor-private interface — this
is a stock QMK device:

| Usage page | Usage | In/Out | What it is |
| --- | --- | --- | --- |
| `0x0001` | `0x06` | 8 / 1 | Keyboard |
| `0x0001` | `0x02` | 32 / 2 | Mouse / consumer |
| `0xFF60` | `0x61` | 32 / 32 | **VIA raw HID** — the only channel used here |
| `0xFF31` | `0x74` | 32 / 32 | QMK debug console (`CONSOLE_ENABLE`), output only |

`0xFF31/0x74` is QMK's own `CONSOLE_USAGE_PAGE`/`CONSOLE_USAGE`, not a NuPhy
extension. The older NuPhy Console protocol used by Air75/Halo75 **V1** lives on
usage page `0xFF00` and is absent on V2 — consistent with nudelta's README,
which directs V2 owners to VIA.

## VIA surface

Protocol version reported by `id_get_protocol_version` (`0x01`): **12**.

Reports are 32 bytes, report ID 0, sent with `IOHIDDeviceSetReport` and answered
by an input report.

A sweep of `id_custom_get_value` (`0x08`) across **all 256 channel ids** found
exactly one that does not answer `0xff` (unhandled):

| Channel | Status |
| --- | --- |
| `0x00` custom / vendor | unhandled |
| `0x01` QMK backlight | unhandled |
| `0x02` QMK RGBLIGHT | unhandled |
| `0x03` **QMK RGB Matrix** | **supported** |
| `0x04`–`0xff` | unhandled |

A sweep of all 256 `value_id`s on channel `0x03` found exactly four real fields:

| value_id | Field | Range |
| --- | --- | --- |
| `0x01` | brightness | 0–255 |
| `0x02` | effect | 0–45 |
| `0x03` | effect speed | 0–255 |
| `0x04` | colour | hue 0–255, sat 0–255 |

`id_custom_set_value` (`0x07`) with the same channel/value ids writes them.
`id_custom_save` (`0x09`) is **never** sent, so nothing reaches EEPROM and a
power cycle restores the user's own settings.

## The Halo side strip is not reachable

The keyboard has two visually distinct zones: the per-key typing backlight and
the strip around the base ("Halo"), which the keyboard controls separately with
`Fn + M + arrows`.

Changing the Halo colour *and* effect from the keyboard produced **no change
anywhere in VIA's readable state** — only the two main-matrix fields the same
key presses happened to alter also moved. Combined with the channel sweep above,
the Halo strip is driven by a firmware subsystem that VIA does not expose.

The practical consequence is the good one: writes made here **cannot disturb the
Halo strip**. Confirmed by observation — the strip survives colour, brightness
and effect writes unchanged.

The one exception is **effect 0** (`RGB_MATRIX_NONE`), which blanks the LED
driver and takes the Halo strip down with it. The daemon rejects effect 0
everywhere for that reason.

## Effects

The effect count is a property of the **firmware build**, not of the model, and
this project changes it by reflashing:

| Firmware | `RGB_MATRIX_EFFECT_MAX` | Valid effect ids |
| --- | --- | --- |
| NuPhy factory (reports USB version 0.0.1) | 46 | 0–45 |
| Built from `nuphy-src/qmk_firmware` + our patch (1.1.9) | 43 | 0–42 |

Both numbers were measured the same way — write effect `255`, read back what the
firmware clamped to — and the 42 matches the compiler's own expansion of
`enum rgb_matrix_effects`, obtained by preprocessing `quantum.h` with the build's
`cflags.txt`. That is the only reliable way to get the mapping: the enum order is
the include order in `rgb_matrix_effects.inc` filtered by which animations
`keyboard.json` enables, plus whatever the keymap's `rgb_matrix_user.inc` appends,
and reading those by hand gets it wrong.

This build's last two effects are NuPhy's own, from
`keymaps/via/rgb_matrix_user.inc`:

| id | Effect |
| --- | --- |
| `41` | `CUSTOM_game_mode` — lights only ESC, WASD and the arrows |
| `42` | `CUSTOM_position_mode` — lights only F, J and Up |

Both read `rgb_matrix_config.hsv`, so both honour the configured colour.

Effects usable as a status indicator are the ones that paint with the configured
hue: `1`–`10`, `29`–`36`, `39`, `40`, and the two custom modes. The cycle,
rainbow and `HUE_*` families (`11`–`28`, plus `37`/`38`) animate hue themselves
and discard the configured colour, so they cannot encode a state.

Effect `0` is `RGB_MATRIX_NONE`. It blanks the LED driver and takes the Halo ring
down with it, so nothing here ever writes it.

## Brightness off-by-one

This firmware stores brightness one step below what VIA is told: writing `X`
reads back as `X-1`, measured consistently across 0–255 (writing `0` gives `0`).

Left uncompensated this decays: each save/restore cycle reads the already-lowered
value and writes it back one lower again, dimming the keyboard by one step per
Claude Code turn. `rgb_set()` therefore pre-compensates brightness writes by one,
which makes a write/read round-trip exact. Brightness 255 is unreachable through
VIA; 254 is the ceiling and is visually identical.

Hue, saturation, effect and speed all round-trip exactly and need no correction.

## Replies must be matched to requests

The VIA interface is opened without `kIOHIDOptionsTypeSeizeDevice` — it has to
be, or the daemon and the CLI tools could not both use it. macOS then delivers
**every** input report to **every** process that has the device open. A reader
that simply takes the next report to arrive will therefore parse another
process's answer as its own whenever two are active at the same time.

Measured on the reader that predates the matching below: 12 concurrent
`get` calls returned **11 different answers and not one correct one**, with the
fields visibly shuffled between value ids (the effect slot holding a brightness,
and so on). It is not a rare race — with any concurrency at all it is the common
case.

The consequence reaches past a wrong line of output. `matrix_read()` is what
records the user's own lighting before takeover; a shuffled read is persisted
to `state.json` and written back to the keyboard on uninstall.

VIA echoes the head of the request in its reply, which is enough to tell one
answer from another. How much it echoes depends on the command:

| Command | Echoed prefix | Reply |
| --- | --- | --- |
| `0x01` `id_get_protocol_version` | command only | `[0x01, hi, lo]` |
| `0x02` `id_get_keyboard_value` | command + value id | `[0x02, value_id, data…]` |
| `0x07`/`0x08` custom set/get | command + channel + value id | `[cmd, channel, value_id, data…]` |

An unhandled request comes back with byte 0 replaced by `0xff`, the remaining
echoed bytes intact — so the match must compare byte 0 against *either* the
command or `0xff`, and the rest exactly. Both `halo65_ledctl` and `via_scan`
loop, discarding reports that do not match, until theirs arrives or the
deadline passes. Verified with 18 concurrent readers across both binaries:
all 18 agreed, and agreed with a single reader.

## The wireless status link (LED-bit channel)

Bluetooth exposes no VIA interface at all, so everything above is
cable-only. What *does* survive a wireless link, measured end to end:

- The BLE report descriptor offers exactly three host-to-keyboard paths:
  a 5-bit LED output report on report 1, a 16-bit feature report on
  report 1, and a 5-bit LED output report on report 2.
- Only the LED report reaches the firmware. In the serial protocol between
  the closed-source RF module and the main MCU, the single command carrying
  host data is `CMD_RF_STS_SYSC`, whose reply byte 6 lands in
  `dev_info.rf_led` — masked to the low three bits (NumLock, CapsLock,
  ScrollLock). The feature report has no serial command at all and never
  arrives.
- Caps Lock cannot carry data: macOS writes the whole LED byte on every
  caps toggle, wiping the other two bits until the sender's next refresh.
- Confirmed live over Bluetooth: LED bits written on the host showed on the
  keyboard (stock caps indicator), i.e. the path
  host → BLE → RF module → serial → MCU works.

That leaves **2 usable bits** (NumLock = wire bit 0, ScrollLock = wire
bit 1), polled by `dev_sts_sync()`. `firmware/halo-host-control.patch` turns
them into a status channel; `src/halo65_leds.c` is the encoder,
`halo_link.c` in the patch is the decoder.

### Status symbols

| Wire | Meaning |
| --- | --- |
| `00` | idle (also the OS's natural resting value — see the wipe rules) |
| `01` | running |
| `10` | permission |
| `11` | escape; the next symbol is `01` failure, `10` completed, `00` voice |

Direct states are re-asserted by rewriting the same value every 100 ms.
Escaped states re-assert as a repeating `11`(250 ms)/operand(250 ms) cycle.
A started pair always completes, so the decoder's outstanding escape can
never consume a fresh direct symbol as its operand.

The decoder trusts any non-zero symbol immediately (only the sender writes
those bits non-zero). `00` is ambiguous — sender-idle or OS wipe — so it is
accepted only after 300 ms of persistence, and ignored outright for 300 ms
after a caps edge. Idle therefore costs one refresh period extra; the other
five states do not.

### Adaptive polling

`dev_sts_sync()` runs every 200 ms until the LED byte changes, then every
50 ms until 2 s pass with no change. One sync costs ~2 ms (1 ms forced delay
plus up to 1 ms waiting for the ack), so the fast interval spends ~4 % of
the main loop, and only while symbols are actually moving.

### Config frames

Holding `11` for 1200 ms (decoder threshold 800 ms, measured from *its*
first sample of the 11, which can lag a slow-poll interval behind the
write — hence the margin) opens config mode. Every wire transition then
carries one base-3 digit: `next = (prev + 1 + trit) mod 4`. The stream is
self-clocking — only edges matter, no shared timing — which is what the
per-symbol +1 buys: consecutive symbols always differ.

Two trits form one 3-bit group (values 0–7; 8 flags corruption), groups
form byte-aligned frames:

```
[ field<<4 | state ] [ payload ] [ crc8 (poly 0x07) ]
field 0  ring colour    payload r g b
field 1  ring params    payload mode speed param bright
field 2  matrix         payload effect speed flags h s v scale
```

Each frame is sent three times in a row at 100 ms per symbol with a 450 ms
gap after every copy; the decoder applies a frame only after **two
consecutive identical CRC-clean copies**, so one corrupted copy (a caps
press mid-transfer, a missed sample) costs nothing. A transmission ends by
parking the current status symbol for 900 ms; the decoder re-latches it on
its 700 ms exit timeout, so the display can never stick.

Net rate: 10 trits/s ≈ 15.8 bit/s. The task-one estimate of 40 bit/s
assumed one symbol per 50 ms poll, which needs a shared clock the channel
does not have; self-clocking trits are what actually survive the jitter.

### Timing thresholds

Every decoder threshold sits below the matching sender timing:

| Decoder (halo_link.h) | ms | Sender (halo65_leds.c) | ms |
| --- | --- | --- | --- |
| wipe guard / zero confirm | 300 | status rewrite | 100 |
| config entry (from its sample) | 800 | entry hold | 1200 |
| frame-gap reset | 300 | inter-copy gap | 450 |
| config exit (silence) | 700 | park hold | 900 |
| — | — | escape phase | 250 |
| — | — | data symbol | 100 |

### Simulated end-to-end latencies

The real encoder (`halo65_leds simulate`) driven against the real decoder
(`halo_link.c` compiled for the host) through a channel model with 5–35 ms
BLE delay, adaptive-poll sampling with jitter, and caps wipes. 20 runs with
random phases:

| State change to | min | median | max (ms) |
| --- | --- | --- | --- |
| running / permission | 25 | ~170 | ~580 |
| failure | 279 | 314 | 342 |
| completed / voice | 288 | ~360–620 | 840 |
| idle | 338 | 387 | 563 |

The direct-state maxima come from leaving an escaped state (the started
pair must finish, up to 500 ms); the escaped-state maximum from a pair that
straddles a slow-poll gap and is only read on the next cycle. 30 caps-press
trials against every held state produced zero spurious transitions, and a
colour-change frame went from queued to applied in **~7.6 s** (entry 1.2 s
+ two 2.8 s copies + debounce). A 10-minute fuzz of random states, wipes
and syncs settled correctly and never stored a corrupted frame — CRC-8 plus
the 2-match rule held.

On-hardware Bluetooth numbers are to be measured after the reflash; the
model's BLE-delay envelope (5–35 ms) is the untested assumption.

### State slots over VIA (value id 0x02)

Wireless carries 2-bit state numbers, so the *looks* live in the keyboard:
`user_config.halo_slots[6]`, 14 bytes per state (ring mode/r/g/b/speed/
param/bright + matrix effect/speed/flags/h/s/v/scale), EEPROM-backed with
their own magic byte. When the cable is in, the daemon writes all six slots
in one burst — channel `0x10`, value id `0x02`, payload `[state, 14 bytes]`
— and `0x09` (save) commits them; wireless sessions then only ever send
state numbers, and config frames update single fields at 15.8 bit/s.
`matrix flags` bit 0 means the matrix follows the ring colour: the firmware
recomputes h/s from the ring RGB and scales v, so a wireless colour change
stays one 5-byte frame. Value id `0x01` (the live animation) is unchanged
and still never persists.

`EECONFIG_USER_DATA_SIZE` grew 12 → 96 for the slots. The size doubles as
the datablock's version stamp, so pre-slot EEPROM content reads back
invalid and re-inits — NuPhy's own fields from their defaults, the slots
from a compiled copy of the daemon's defaults.

## Discovering an unknown board

`scripts/install.py scan` (binary: `src/via_scan.c`) does the read-only version
of everything above against **any** keyboard, not just this one. It matches on
HID usage page `0xFF60` / usage `0x61` rather than on a vendor/product id, so it
finds boards nobody has mapped, then reports the VIA protocol version and sweeps
the lighting channels to see which the firmware implements. `scan --deep` sweeps
all 256 channel ids; on this keyboard that takes 1.0 s and confirms the finding
above — `0x03`, plus `0x10` once the patch is flashed, and nothing else.

Channel ids are safe to sweep because they are arguments to `0x08`. VIA
**command** ids are not, and nothing here sweeps them: `0x0A` is
`id_eeprom_reset` and `0x0B` is `id_bootloader_jump`.

## Tools

`scripts/via_probe.c` and `scripts/via_dump.c` are the read-only probes used to
establish the above. Neither writes to the keyboard.

Note that sweeping VIA **command** ids is *not* safe and is deliberately not
done: `0x0A` is `id_eeprom_reset` and `0x0B` is `id_bootloader_jump`, which would
wipe the VIA keymap and drop the board into its bootloader.
