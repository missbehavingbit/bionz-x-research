# E0652 — QEMU a6400 late black-screen scanout and buffer capture

Indexed: 2026-11-19.

Date: 2026-11-18. Offline QEMU full-system investigation following E0574 and [E0651](0651-qemu-a6400-stock-finder-monitor-rear-lcd.md). No physical camera, SD card, or USB device was accessed.

Labels: `[C]` confirmed by atomic QMP/guest receipts, `[I]` inference, `[A]` explicit assumption.

## Result

Captured atomic VPH/SYSV scanout registers, both `640 x 480` RGBA4444 OSD display buffers (`0x23a80000` and `0x23b16000`), VPH head 0 and head 1 screendumps, and `/proc/snsc_boot_time` across matched healthy and failed states during the E0574 post-menu late black-screen failure `[C]`.

**Diagnosis: the guest firmware stopped drawing and muted panel scanout; QEMU did not fail to display an active buffer `[C]`.**

Specifically, when the guest's `Power Save Start Time` expires (`1 Min` stock default once `Bluetooth Rmt Ctrl` is `Off`, or `10 Sec` when selected in `Setup2`), Sony's power manager triggers `EV OFF add_off_factor: 0x8 :0x920021` (Auto Power Save), executes `EV SET_PANEL_OUT`, clears VPH panel layer-enable bits `0` and `1` at `0xf3440144` (`0x1000233` / `0x3000233` -> `0x3000230`), zero-fills the active front OSD buffer A (`0x23a80000`, `0` nonzero pixels) while leaving `ch53.base` pointed at `0x23a80000` (with stale backbuffer B `0x23b16000` unreferenced at `3,669` nonzero-alpha pixels), transitions `modelCamera` to `DEACTIVE`, and blocks in `Mt F00_010_libObj.so` `[C]`. Subsequent `MENU`, `DISP`, and `MENU` presses produce four identical all-black `640 x 480` captures (`a6087ec5178c...`), reproducing E0574's exact symptom while QEMU's SYSV/VPH timers continue running normally `[C]`.

## Inputs and reproduction

Inputs match E0651 (`emmc.img` `18e37ec3...`, `av-cam.bin` `90d515cf...`, `Backup.bin` `13345a90...`, WBI-derived 384 B sub-micro frames `3385ec34...` `[A: powered Slot B TLV 0x03 per E0527]`, QEMU binary `8c042f45...`, `REAR_LCD_DIAGNOSTIC=0`, `EVSEL_LATE_US=500`, `-icount shift=0,sleep=on`).

1. **Why a 133.8 s guest-uptime run with untouched `captured_Backup.bin` stayed visible (`late-black-watch-01`) `[C]`:** In the camera-captured `Backup.bin`, `Network2 -> Bluetooth Rmt Ctrl` is `On`, which inhibits Auto Power Save even though `Setup2 -> Power Save Start Time` is `1 Min` (`step13_setup2.png`, `step14_psave_click.png`). E0573/E0574 booted without `--inputs`, where `Bluetooth Rmt Ctrl` is `Off` and the `1 Min` timer is active.
2. **Stock `1 Min` Power Save replay (`late-black-1min-01`) `[C]`:** Booted unmodified `captured_Backup.bin`, dismissed the NTSC notice (`ret`), opened `MENU` onto `Network2 -> Bluetooth Rmt Ctrl`, switched `Bluetooth Rmt Ctrl` to `Off` (leaving `Power Save Start Time` at its stock `1 Min`), and closed `MENU` at `~5.9 s` guest uptime (`96.64 s` host). The rear-LCD shooting screen remained visible through `63.34 s` guest uptime (`313.7 s` host), then transitioned to persistent all-black at `65.93 s` guest uptime (`325.79 s` host — `60.0 s` after menu close):

```bash
python3 -u artifacts/qemu-a6400/stock-rear-lcd-20261118/observe_run.py \
  --qemu-dir "$HOME/sony-a6400-qemu-11.1.3" \
  --inputs-dir "$HOME/a6400-work/inputs" \
  --submicro artifacts/qemu-a6400/stock-rear-lcd-20261118/wbi-powered-submicro-384.bin \
  --run-dir NEW_RUN_DIR \
  --finder-mode captured \
  --key-seq "ret:5,m:5,ret:3,down:3,ret:4,m:5" \
  --observe-seconds 340
```

3. **Independent `10 Sec` Power Save replay (`finder-menu-01`) `[C]`:** Setting `Setup2 -> Power Save Start Time -> 10 Sec` and `Bluetooth Rmt Ctrl -> Off` reproduced the identical register, buffer, and `snsc_boot_time` transition `11.79 s` after closing `MENU` (`428.04 s -> 439.83 s`).

## Matched healthy vs. late black-screen scanout and buffer state (`late-black-1min-01`)

Each snapshot paused QEMU via QMP `stop` before reading VPH/SYSV MMIO words, dumping `0x23a80000` and `0x23b16000` (`614,400` bytes each), and taking VPH head 0/1 screendumps.

| Register / buffer | `02c_after_key_seq` (`96.64 s`, visible) | `03_late_black_initial` (`325.79 s`, blank) | `04_late_black_after_keys` (`337.89 s`, after `MENU, DISP, MENU`) |
| --- | --- | --- | --- |
| `VPH +0x028` (`0xf3440028`, clock) | `0x111` | `0x111` | `0x111` |
| `VPH +0x144` (`0xf3440144`, panel enable) | `0x1000233` ( in `finder-menu-01`) / `0x3000232`–`0x3000233` (OSD bit 1 set) | `0x3000230` (YUV bit 0 = `0`, OSD bit 1 = `0`) | `0x3000230` (unchanged) |
| `VPH +0x404` (`0xf3440404`, OSD bpp) | `0x0` (16 bpp RGBA4444) | `0x0` | `0x0` |
| `AIF ch51` (`0xf3441200`, panel YUV) | `ctrl=0x2`, `base=0x14e90000`, `lb=0x400`, `skip=0x80`, `lines=570` | `ctrl=0x2`, `base=0x14e90000`, `lb=0x400`, `skip=0x80`, `lines=570` | `ctrl=0x2`, `base=0x14e90000`, `lb=0x400`, `skip=0x80`, `lines=570` |
| `AIF ch53` (`0xf3441300`, panel OSD) | `ctrl=0x2`, `base=0x23a80000`, `lb=0x500`, `lines=480`, `pitch=0x500` | `ctrl=0x2`, `base=0x23a80000`, `lb=0x500`, `lines=480`, `pitch=0x500` | `ctrl=0x2`, `base=0x23a80000`, `lb=0x500`, `lines=480`, `pitch=0x500` |
| `AIF ch54` (`0xf3441380`, line OSD) | `ctrl=0x2`, `base=0x23a80000`, `lb=0x500`, `lines=240` | `ctrl=0x2`, `base=0x23a80000`, `lb=0x500`, `lines=240` | `ctrl=0x2`, `base=0x23a80000`, `lb=0x500`, `lines=240` |
| `SYSV +0x98` (`0xf3402098`, frame counter) | advancing | `0x7c2` | `0x82a` (`+104` frames; VPH/SYSV still ticking) |
| OSD Buffer A (`0x23a80000`, `614,400` B) | `5,241` nonzero-alpha px (`6dd27a22c487...`) | `0` nonzero px (`34c69899504b...`, all zeros) | `0` nonzero px (`34c69899504b...`, all zeros) |
| OSD Buffer B (`0x23b16000`, `614,400` B) | `3,669` nonzero-alpha px (`b7fca075810f...`) | `3,669` nonzero-alpha px (`b7fca075810f...`, stale, unreferenced) | `3,669` nonzero-alpha px (`b7fca075810f...`, stale, unreferenced) |
| VPH head 0 (`640 x 480` PPM) | `3,127` nonblack px (`ae1f447700e6...`) | `0` nonblack px (`a6087ec5178c...`) | `0` nonblack px (`a6087ec5178c...`) |
| VPH head 1 (HDMI idle surface) | `307,200` nonblack px (`e851c0d0cbd2...`) | `307,200` nonblack px (`e851c0d0cbd2...`) | `307,200` nonblack px (`e851c0d0cbd2...`) |

Guest `/proc/snsc_boot_time` in `late-black-1min-01` recorded the exact off-sequence at `65,927,721 us`:

- `EV OFF add_off_factor: 0x8 :0x920021` -> `EV OFF go inact_app(active) by off: 0x8` -> `EV OFF PS NG OFFSEQ`
- `EV SET_PANEL_OUT` (`65,961,034 us`, `65,977,869 us`, `65,978,029 us`)
- `00B : modelCamera DEACTIVE` (`65,984,170 us`)
- `Mt F00_010_libObj.so` (`65,985,268 us`, `B` without `E`)

## Implications and next boundary

1. **QEMU VPH note (`cxd90014_vph.c`):** `vph_compose` currently checks `R(0x144) & 1` for YUV (`ch51`) and composes `ch53` unconditionally from `R(AIF(2) + 0x20)`; head 0 still rendered black because the guest zero-filled front buffer A (`0x23a80000`) in addition to clearing `0xf3440144` bit 1 (`0x3000230`).
2. **Preventing or recovering from Auto Power Save in long workbench sessions:** With `captured_Backup.bin` (`Bluetooth Rmt Ctrl = On`) or `Power Save Start Time = 30 Min`, the panel stays visible past `133.8 s` guest uptime (`late-black-watch-01`). Supporting clean power-save wake requires completing the guest `Mt F00_010_libObj.so` off-sequence / sub-micro sleep handshake and shutter half-press wake path.
3. Failed trial retained: `late-black-preset-01` passed raw `CX62600_UC2.bin` (`1,148,036` B, lacking the `0x02E80000` loader header word) to `-device loader`, loading at `0x0` and timing out before `SetContentTarget`.

Receipts, raw `614,400`-byte buffer dumps, PPM/PNG frames, and JSON reports are retained under `artifacts/qemu-a6400/late-black-scanout-20261118/` and `artifacts/qemu-a6400/stock-rear-lcd-20261118/finder-menu-01/`.

## Follow-up: body shutter input and save boundary

The captured ORIL port map at `0x64339D04` and `0x64339D14` identifies
body S1 as `P0.1` / IRQ 122 and body S2 as `P1.5` / IRQ 142. These
are the contacts used by the new opt-in rear panel bindings. The
constructed-board test checks half-press, full press, and release with
both keys held. In the captured shooting-view replay, S1 passed
`0xF2910010` from `0x2C9E` to `0x2C9C` and back. This is proof of input
delivery at the board, not autofocus, shutter release, or wake.

The original menu visibly changed `Bluetooth Rmt Ctrl` from `On` to
`Off` in `bringup-wake-03/`. After closing the menu and issuing Linux
`sync`, the run hit its host observation bound before Auto Power Save.
The host then read the firmware-owned `/setting/Backup.bin` from the
closed disposable qcow2 overlay. It was still 1,171,036 bytes with the
original SHA-256 `13345a908433d7130adb382ee01a951ac8e691d091defd666d4d8a8af081232d`.
A menu change and Linux `sync` do not establish that the camera's
backup service committed the setting. No stock settings reload is
claimed from that trial.

An independent phase-only run (`bringup-wake-04/`) waited for genuine
Auto Power Save with the same captured firmware, powered WBI fixture,
500 us event delay and QEMU `0a9b0834…`. Stock selection set
`Bluetooth Rmt Ctrl` to `Off` and left the timer at `1 Min`. The observer
used `-icount shift=0,sleep=on`; it did not alter guest time or power
state. The panel was active at 167.5 s host time and blank at 538.1 s.

| Quiesced sample | VPH `+0x144` | P0 input | SYSV frame | Nonblack LCD pixels |
| --- | --- | --- | --- | --- |
| Shooting view | `0x01000233` | `0x2C9E` | `0x11E` | 3,127 |
| Auto Power Save | `0x03000230` | `0x2C9E` | `0x809` | 0 |
| MENU negative control | `0x03000230` | `0x2C9E` | `0x81B` | 0 |
| S1 held | `0x03000232` | `0x2C9C` | `0x837` | 2,140 |
| S1 released | `0x03000232` | `0x2C9E` | `0x858` | 2,140 |

The original shooting OSD returned on half-press and stayed visible
after release. `liro-122` recorded two interrupts. This proves rear-LCD
wake via S1 in that stock run; it does not prove a full camera-mode
restart or wake of the optional synthetic YUV layer. The sequencer
remained at step 26 throughout. The run quit through QMP with exit 0.
The firmware-owned `Backup.bin` also remained byte-identical after
this sleep/wake cycle and `sync`. An orderly, original backup commit
and reloading a changed setting in a fresh process remain unverified.

## Follow-up: settings survive an explicit native commit (2026-11-19)

In the captured replay, change `Bluetooth Rmt Ctrl` from `On` to `Off`
using the stock directional and center controls. In
`bringup-native-backup-commit-03`, an offline ARM diagnostic in the guest
uses the original `libbackup.so` and `libosal_uipc.so` to call
`Backup_sync_all`. The diagnostic records `BKRC=00000000` and
`BK_STATUS=0`. After QMP exit the closed eMMC overlay contains a
different firmware-written `Backup.bin` (from `13345a90…` to
`02ce1a091e4b04b2341d34c01696586fecd964031a1f322a0d2e380cbb414e85`).

For `bringup-native-settings-restart-01`, flatten that closed overlay,
read the actual FAT file, then use those exact bytes as the bootloader
preload for a new QEMU process. No setting bytes are generated or edited
by the host. Navigate back to the setting without changing or saving it:
the stock menu displays `Off`, and both screenshots hash to
`2bd093bd65523553c13f771c1aa9e3716333c7ac06d61264b967a9ee33f18901`.
Both runs use QEMU `ec238071349217da08664e96c988f4195e9d5a8a072de305d9de89eba9ec96d1`,
the captured ORIL and the powered sub-micro replay.

This proves persistence through the explicit original save API, not an
automatic shutdown commit. In `bringup-settings-power-lever-01`, the
virtual power lever blanked the display but did not change `Backup.bin`.
In the same run the wheel counter went `0 -> 1 -> 0` while the tested
stock dialog stayed identical. The application-level wheel result
was later verified separately.

Repeating the unassisted power-off at captured step 42
(`bringup-settings-step42-power-01`) again blanked the LCD and stopped
at the original `Mt F00_010_libObj.so` boundary without a changed
`Backup.bin`. The [wheel follow-up](0573-qemu-a6400-rear-panel-controls.md#follow-up-stock-wheel-detents-2026-11-19)
separately verifies both scroll directions.

With the later DFS switch model enabled, `bringup-dfs-auto-save-power-01`
again shows `Bluetooth Rmt Ctrl: Off` before the latched power-off input.
The stock power manager enters the `0x7` off path, completes the sampled
`Mt F00_010_libObj.so` teardown, and disables both rear LCD layers.
Readback of the closed eMMC overlay's `/setting/Backup.bin` still matches
the captured original (`13345a908433…`). The replay did not call a save
helper. Clearing the earlier camera teardown boundary did not, on its
own, establish automatic shutdown saving.
