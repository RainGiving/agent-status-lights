# Halo ring host control — firmware patch

`halo-host-control.patch` adds a host-driven override for the 45-LED Halo ring
to NuPhy's own Halo75 V2 firmware. Stock firmware exposes the ring only through
`Fn + M` keycodes; nothing in VIA can reach it (a sweep of `id_custom_get_value`
across all 256 channels answers on `0x03`, the standard RGB Matrix, alone).

The patch adds ~544 bytes and touches three files:

| File | Change |
| --- | --- |
| `ansi.h` | `halo_host_t` and the `HALO_HOST_*` mode enum |
| `side.c` | The animation primitives, plus an intercept at the top of `m_side_led_show()` |
| `ansi.c` | `via_custom_value_command_kb()` on vendor channel `0x10` |

The firmware implements primitives only. Colour, speed, tail length and
brightness all arrive over VIA at runtime, so retuning the look never needs a
reflash — which matters, because each reflash costs a DFU cycle that wipes
EEPROM.

## Wire format

```
0x07 0x10 0x01 <mode> <r> <g> <b> <speed> <param> <bright>
 ^    ^    ^
 |    |    +-- id_halo_animation
 |    +------- id_halo_channel (vendor)
 +------------ id_custom_set_value
```

`0x08` in place of `0x07` reads the current animation back. `0x09` (save) is
accepted as a no-op: the ring is a transient display, so persisting it would
only mean booting into the last task's colour.

| mode | Name | `param` |
| --- | --- | --- |
| 0 | `RELEASE` — hand the ring back to the firmware | — |
| 1 | `SOLID` | — |
| 2 | `PULSE` — whole ring breathes together | — |
| 3 | `COMET` — a lit head orbits with a fading tail | tail length in LEDs |
| 4 | `STROBE` | duty cycle, percent |
| 5 | `FILL` — sweeps around once, then holds | — |

Mode `0` is what keeps the user's own `Fn + M` setting intact: the daemon
releases the ring whenever it has nothing to report.

## Building

See `docs/PROTOCOL.md` for the toolchain notes. In short:

```bash
git clone --branch nuphy-keyboards https://github.com/nuphy-src/qmk_firmware ~/qmk_nuphy
cd ~/qmk_nuphy
git submodule update --init --depth 1 lib/chibios lib/chibios-contrib lib/printf lib/lufa lib/vusb
git apply /path/to/halo-host-control.patch
qmk compile -kb nuphy/halo75_v2/ansi -km via
```

Homebrew's `arm-none-eabi-gcc` ships without newlib and cannot build this; use
ARM's own toolchain tarball.

## Flashing

Back up the VIA keymap first — entering the bootloader wipes EEPROM:

```bash
build/via_backup > backups/keymap.txt
# hold Esc and plug the keyboard in
dfu-util -a 0 -d 0483:df11 -s 0x08000000:leave -D ~/qmk_nuphy/nuphy_halo75_v2_ansi_via.bin
build/via_restore backups/keymap.txt
```

`backups/stock-firmware-backup.bin` is a full 128 KiB read of the factory
firmware, taken with `dfu-util -U`. Flash it back the same way to roll back.
