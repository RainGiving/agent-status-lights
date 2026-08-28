# NuPhy Halo75 V2 lighting over VIA — measured notes

Everything here was measured against a physical NuPhy Halo75 V2 on macOS via
IOKit HID. It is not an official spec; a NuPhy firmware update could invalidate
any of it.

## Device

```text
Product     NuPhy Halo75 V2
USB VID:PID 0x19f5:0x32f5
Transport   USB cable (a 2.4G dongle or Bluetooth does not expose the VIA interface)
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

## Tools

`scripts/via_probe.c` and `scripts/via_dump.c` are the read-only probes used to
establish the above. Neither writes to the keyboard.

Note that sweeping VIA **command** ids is *not* safe and is deliberately not
done: `0x0A` is `id_eeprom_reset` and `0x0B` is `id_bootloader_jump`, which would
wipe the VIA keymap and drop the board into its bootloader.
