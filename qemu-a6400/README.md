# Sony a6400 QEMU Machine (`sony-a6400`)

Out-of-tree QEMU v11.1.3 overlay and patch for the Sony ILCE-6400 (`CXD90045` / `ARM-CXD900X0`), modeled after the clean `qemu-overlay/` + `patches/` pattern used by `Oein/a5000-emulator`. The previous 10.1.0 build and evidence remain preserved. See [verified tool versions, regression results and rollback](../docs/TOOLCHAIN.md).

An explicit [adapted ZV-E10 menu experiment](../docs/ZVE10_ADAPTED_MENU.md)
uses this board with a separate boot policy and checked private inputs.

## Prerequisites (WSL2 Ubuntu)

Install the QEMU build dependencies in WSL2 Ubuntu:

```bash
sudo apt update
sudo apt install -y build-essential git meson ninja-build pkg-config \
  libglib2.0-dev libpixman-1-dev libfdt-dev zlib1g-dev python3-venv \
  python3-tomli libsdl2-dev
```

## Setup & Build

From the repository root inside WSL2, using the installed research environment:

```bash
bash qemu-a6400/setup-qemu.sh
export PATH="$HOME/a6400-rea-tools/build-tools-3.15.2/bin:$PATH"
cd "$HOME/sony-a6400-qemu-11.1.3/build"
../configure --python="$HOME/a6400-rea-tools/build-tools-3.15.2/bin/python" --target-list=arm-softmmu --enable-sdl --disable-werror --disable-docs
ninja -j2 qemu-system-arm qemu-img qemu-io
```

The patch also honors eMMC CMD1 sector-addressing requests below 2 GiB. Without
that negotiation, the original driver requests sector `0x52002` but QEMU reads
byte address `0x52002` instead of `0x0A400400`, so a valid captured ext2 rootfs
cannot mount. This is a device-protocol correction, not a firmware patch or a
reason to inflate the disk to 4 GiB. Optional native regressions use constructed
disks and paused CPUs, requiring neither firmware nor camera:

```powershell
wsl.exe --exec python3 -m unittest tests.test_qemu_emmc -v
```

Run this command from the repository root. The ordinary Windows suite skips
these eight native tests when the Linux QEMU build is unavailable. The additional
constructed-board check exercises debounce holds, immediate wheel and lever
input, and both synchronous display callbacks. Its PPM files are not
original-firmware frame evidence.

## Milestone 1: Initial Bring-Up

Verify the machine registers cleanly:

```bash
"$HOME/sony-a6400-qemu-11.1.3/build/qemu-system-arm" -M help | grep sony-a6400
```

## Interactive rear panel

On this Windows workstation, double-click `a6400_QEMU.bat` on the
Desktop. It starts the emulator and opens the panel in the default
browser at `http://127.0.0.1:8641/`. With prepared camera inputs and a
layout-aware loader build present it starts in mod-development mode, and
the page toolbar loads, watches and hot-reloads mods and pauses, resumes
or restarts the emulator (see [MOD_DEVELOPMENT.md](MOD_DEVELOPMENT.md)). Running it while the panel is
already open reuses that instance. The LCD starts displaying when
firmware publishes its first frame.

Rebuild QEMU with the current board overlay. From the repository root in WSL,
start a new run with a fresh log path:

```bash
EVSEL_LATE_US=500 python3 qemu-a6400/tools/rear_panel.py --log a6400-panel.log
```

Open [the local rear panel](http://127.0.0.1:8640/). The image is QEMU's
VPH output. The buttons drive the emulated ADC ladders, GPIO lines or
sub-micro link. Hover over the control wheel and scroll to rotate it, or
use `[` and `]`. Wheel turns and AF/MF–AEL selector toggles are immediate
edges; MENU, directional presses and the AF/MF–AEL button still use a hold
for firmware debounce. Press Ctrl+C in the launch terminal to stop the local run.

A powered menu replay needs the [E0527](../docs/evidence/0527-qemu-a6400-real-submicro-frames-power-on-holds.md)
fixture. Use `SUBMICRO_PAGE=/path/to/camera-page-0x03443000.bin` or
`SUBMICRO_FRAMES=/path/to/submicro-frames.bin` for the verified 384-byte A/C/B
extract. The launcher checks the original fixture digest before staging the
overlay. An explicit empty `SUBMICRO_PAGE=` is useful for serial diagnostics,
not for a powered menu test.

For the captured boot, `SUBMICRO_LENS_POWER=1` opts in to the slot-B
rail-status reply. The [matched replay](../docs/evidence/0591-qemu-a6400-first-live-view-frame.md#captured-step-26-sub-micro-lens-rail-acknowledgement)
uses the explicitly qualified powered WBI fixture and `LIVEVIEW_SOURCE=bars`
to release the original step-26 lens-state wait. The default frozen-reply
behavior is unchanged. No attached lens is modeled.

The panel uses stock firmware display selection by default ([E0651](../docs/evidence/0651-qemu-a6400-stock-finder-monitor-rear-lcd.md)):
with `REAR_CONTROLS=1` holding GPIO `P0.2` (`KEY_OTHER_EYE_CONTACT`) idle high, stock `FINDER/MONITOR`
(`Auto` and `Monitor(Manual)`) renders the rear-LCD layout without patching
`viewUnified2.so` (`e` toggles the emulated eye sensor; `--diagnostic-lcd`
retains the older [E0572](../docs/evidence/0572-qemu-a6400-rear-lcd-layout-on-panel.md)
overlay edit). `A6400_QEMU_DIR` can point at an alternate QEMU build.

One wheel turn advances the virtual TPU by four phase counts, moving the
stock menu highlight in either direction. The AF/MF--AEL selector and
button select separate stock actions: the tested AF/MF assignment
opens Drive Mode, and AEL shows exposure lock while held. See the
[control replays](../docs/evidence/0573-qemu-a6400-rear-panel-controls.md#follow-up-stock-wheel-detents-2026-11-19).

With `REAR_CONTROLS=1`, the panel also exposes **Half-press shutter** (`s`)
and **Full press shutter** (Space, QMP `spc`). Half-press drives body S1
(`P0.1`); full press holds S1 and drives S2 (`P1.5`). These are hardware
inputs, not wake or capture commands. Half-press wakes the stock OSD after
Auto Power Save. `SHUTTER_EDGE_IRQ=1` opts in to latching just S1/S2
in the virtual GIC. This avoids missed short pulses without making the
pulses longer or changing the other controls. It is an emulated boot
policy, not a measurement of the physical camera's GPIO circuitry.
The latched **Power** lever changes the native sub-micro
input, but automatic settings commit is not proven. See the [explicit
native save and restart](../docs/evidence/0652-qemu-a6400-late-black-screen-scanout-capture.md#follow-up-settings-survive-an-explicit-native-commit-2026-11-19)
for the verified settings-persistence path.

For the captured boot only, set `IDT_CAM` to the unmodified
`sabin/idt_cam.bin` from the matching `nflasha3` boot set. The launcher
checks the original kernel, ORIL and image hashes, then preloads the
auxiliary image at physical `0x20152000`. In the rail-enabled bars
profile this releases the native final step-42 predicate; it does not
provide autofocus, stock JPEG playback, or still recording. See the
[native comparison and replay limits](../docs/evidence/0650-qemu-virtual-media-gates.md#the-original-idt-sidecar-releases-the-captured-step-42-predicate).
With that profile, `DDRB_CONTROL=1` opts in to the narrow DDR-B
idle-to-active transition used by the stock power manager. It restores
the native EE reply and lets the mounted playback browser initialize.
It does not emulate DDR timing or self-refresh and does not by itself
prove that a JPEG was displayed or recorded.

`DFS_CONTROL=1` independently enables the clock-switch status handshake
used by the stock UPM driver. In the captured profile with DDR-B enabled,
the original codec-idle request returns and Folder View (Still) lists the
seeded SD entry. This models completion, not clock rates. Opening the
entry reaches the original playback worker but still does not display
the photo. See the [playback evidence](../docs/evidence/0650-qemu-virtual-media-gates.md#the-dfs-clock-switch-releases-the-separate-upm-rule-0-wait).

`ZMA1_STARTUP=1` enables the captured startup-program interrupt on
that accelerator. With it, `ZMA1_JPEG_FILL=1` also models the stock
firmware's neutral playback-buffer fill and its accelerator result.
The native fill now returns, but the stock LCD still says **Unable
to display**. Neither operation decodes the JPEG.

For the guarded ARM mod workflow (camera-captured inputs, Mod Loader v2 install and mod hot reload
with `--inputs`, `--loader` and `--mod`), see [Developing camera mods in QEMU](MOD_DEVELOPMENT.md).
Prepared inputs read from `/mnt/c` slow every guest disk access. The panel's `--inputs` copies them
to the Linux filesystem once (boot to the menu 63 s → 35 s, E0577).

## Offline SD and USB scenarios

These are separate gates. The SD regression runs on `sony-a6400`. The USB
regression uses an isolated virtual OHCI host and QEMU's Mass Storage device,
not the Sony gadget. Neither accesses a physical card or USB port.

```bash
# From the repository root in WSL, with the current QEMU overlay built:
python3 -m unittest tests.test_qemu_media.QemuRemovableSdTests \
  tests.test_qemu_media.QemuUsbMassStorageTests tests.test_qemu_emmc -v
```

### SD insertion, playback and persistence

Use a **writable raw image made for this experiment**, with a separately approved
SHA-256. For playback, put a known JPEG under `DCIM/100MSDCF/DSC00001.JPG` on
the image before boot. Do not select a physical device, a captured partition,
or the emulator's eMMC. Do not write to the image from the host while QEMU owns
it. In WSL, with prepared inputs configured as below:

```bash
export SD_IMAGE="$HOME/a6400-media/playback.img"
export SD_SHA256='<approved raw-image SHA-256>'
scratch=$(mktemp -d)
export RUN_DIR="$scratch/guest"
export QMP="$scratch/qmp.sock"
REAR_CONTROLS=1 \
QEMU_EXTRA="-icount shift=0,sleep=on -net none -qmp unix:$QMP,server=on,wait=off" \
  bash qemu-a6400/run.sh
```

1. **Empty versus inserted:** The slot starts empty. With the guest ready, run
   `python3 -m tools.research.qemu_media sd-event insert --qmp "$QMP"` in
   a second terminal (set `QMP` to the same socket there). The slot drives
   live card presence in the sub-micro reply and active-low SD0 detect at
   `0xF2915010` bit 24; bit 16 reports no Memory Stick. Check guest
   discovery of `mmca10`/`mmca10p1` on `/proc/partitions`; pre-existing
   `/dev/mmca*` nodes or QMP success do not prove a mount. For the distinct
   card-present-at-boot control, use `SD_IMAGE` and `SD_SHA256` with
   `python3 -m tools.research.qemu_boot_probe --sd-at-boot --inputs "$A6400_QEMU_DATA_DIR" --report-dir artifacts/qemu-a6400/NEW-SD-BOOT --guest-command "cat /proc/partitions"`.
2. **Playback:** Compare Play with the slot empty and with the fixture inserted.
   Send QMP `send-key` with qcode `p` and `hold-time` 250, or use the rear
   panel's Play button. Capture the original display on both heads with QMP
   `screendump` (`device: "vph"`). Require the image to match the known
   fixture; a changed or nonblack OSD is not proof of playback. For a
   high-contrast fixture, `python -m tools.research.qemu_media verify-playback
   --fixture DSC00001.JPG --empty empty.ppm --inserted playback.ppm` checks the
   comparison in the repository Python environment. If the photo is drawn
   within a smaller rectangle, declare `--image-box LEFT TOP RIGHT BOTTOM`
   before the replay. The maximum mean error of any RGB channel at 64x48
   must be at most 12 for the inserted case and greater than 12 for the empty
   control. This is a fixture check, not a general image-quality metric.
3. **Persistence:** Use `sd-event eject` only after guest I/O is quiescent.
   The native test checks the card-present and remove bits, an SD ADMA2 write
   and readback after a new QEMU process, plus an unchanged internal eMMC
   digest. A firmware-level persistence test must separately prove that the
   same guest-created file survives reinsertion with a fresh eMMC overlay.
   Review the new card digest before reopening an intentionally modified image.

For **capture**, do not count a preloaded JPEG or a rendered OSD. First verify
fresh changing live frames from the firmware's image path. Use a separate card
image and export its before/after `DCIM` trees from the virtual image after
clean shutdown. Trigger the camera through an original input, not a host write
or a guest shell file copy. For a photo, independently decode it in the
repository Python environment (Pillow required):

```bash
python -m tools.research.qemu_media verify-photo \
  --before path/to/before-export --after path/to/after-export \
  --reopened path/to/reopened-export
```

Pass `--reopened` with a separate export from the restart replay. The
tool requires the same new DCIM path and digest in that export; the
replay receipts must separately prove it was the same card reopened
in a fresh QEMU process. Current firmware playback and capture
limits are in [E0650](../docs/evidence/0650-qemu-virtual-media-gates.md).

### USB baseline before streaming

The virtual USB test resets the root port, reads descriptors on EP0, assigns
an address, sets the MSC configuration, and checks Bulk-Only Transport
readiness, capacity and a known sector. It also checks cable removal and the
unchanged read-only fixture image. For a USB-dependent Sony mod, repeat this
gate on the **original Sony gadget** (MSC, or PTP `GetDeviceInfo` and a
session/object read) before attempting UVC or PTP liveview. The QEMU USB
fixture is not evidence that the Sony DWC3 device/OTG path enumerates.

For the stock PC Remote gate, use `tools.research.qemu_usb_host_probe`
with `--stock-attach before-first-view --stock-ptp-info` and the qualified
inputs in [E0655](../docs/evidence/0655-qemu-first-view-ptp-and-native-dwc3-reset.md).
The probe requires native UDC configuration and a validated Sony
`GetDeviceInfo` data/response pair. Stock firmware currently suppresses the
USB input before gadget start, even after Play restores `ModelCamera`
readiness. Do not count diagnostic HID enumeration or native bulk OUT
fixtures as a Sony PTP transfer.

## Milestone 2: Main firmware boot (E0521)

### Rebuild inputs from a local camera capture

The preferred input set uses the captured SDM partition table, every nonempty
partition image, and the kernel, initrd, av-cam and settings extracted from those
same images. It does not substitute update-package libraries or default settings.
Original capture files are opened read-only; generated files remain git-ignored.
Capture/output overlap is refused before creating any directories or receipts.
The native QEMU eMMC backing layout is `nflashaB0` (4 MiB), `nflashaB1`
(4 MiB), then the user area. The captured SDM header therefore belongs at
backing-file offset 8 MiB, and each user partition at `8 MiB + SDM.start`.
Manifest schema 2 records this translation; placing the SDM header at file
offset zero makes the guest reject the partition table.

Create the ignored `qemu-a6400/inputs.local.json` once for this workstation:

```json
{
  "schema_version": 1,
  "dump_dir": "C:\\private\\a6400-nflasha-readonly",
  "manifest_sha256": "<verified SHA-256 of manifest.json>",
  "checksums_sha256": "<verified SHA-256 of SHA256SUMS.txt>"
}
```

Check these initial identities against the acquisition receipts. Do not refresh
hashes to silence a mismatch. The tool uses the capture's complete per-device
`manifest.json` and the existing checksum list; it does not rewrite either.

From PowerShell in the repository root:

```powershell
$bootInputs = 'artifacts\qemu-a6400\inputs-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs prepare --report-dir $bootInputs --allow-unacquired-zero-fill
.\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs verify --report-dir $bootInputs --sources
$linuxInputs = (wsl.exe --exec wslpath -a -u (Resolve-Path $bootInputs).Path).Trim()
wsl.exe --exec env "A6400_QEMU_DATA_DIR=$linuxInputs" TIMEOUT=110 bash qemu-a6400/run.sh
```

`--allow-unacquired-zero-fill` explicitly permits zero-filled unacquired disk
ranges, including the tail required by QEMU's power-of-two card-size restriction.
The manifest distinguishes captured capacity, emulated capacity and every such
range. This is a reconstructed emulator disk, **not** a complete physical-disk
dump. Writable source filesystems were also not acquired atomically.

Each preparation requires a new output directory and produces a deterministic
`boot-manifest.json`, disk image and preload files. Repeating preparation with the
same sources, tool bytes and policy produces identical output hashes. Before
booting, `run.sh` checks all prepared hashes, uses the recorded serial command
line and creates a new run directory. `RUN_DIR` may select another new directory.
The raw base image is never attached writable: the guest uses a fresh qcow2 overlay.
An exit caused by `TIMEOUT` is a bounded stop, not proof of successful boot.

### Bounded task and interrupt observations

The standard-library observer runs inside WSL, captures real serial command exit
statuses, stops all emulated CPUs for snapshots and verifies QMP shutdown:

```powershell
wsl.exe --exec python3 -u -m tools.research.qemu_boot_probe --inputs $linuxInputs --report-dir artifacts/qemu-a6400/NEW-PROBE --seconds 120 --wait-for "Created HeapAllocator:" --guest-command "cat /proc/interrupts" --liro-task 1 --liro-task 43 --liro-task 65 --liro-irq 101 --liro-irq 102
```

Serial input is paced and acknowledged one command at a time; commands are not
retried automatically. Active console I/O stays on Linux storage and is
hash-verified when archived, avoiding concurrent cloud-backed DrvFS reads.

The optional LIRO decoder is deliberately restricted to the captured E0524
kernel/usr hashes. It binds the original ELF offsets to this run's module
section addresses, archives each bounded RAM read and does not unwind runnable
tasks. Other builds must establish their own layout; do not replace hashes to
make them pass.

IRQ observations use native GIC enable/disable/input traces, not debugger reads
of GIC MMIO. A debug read reset the emulator connection; the native SMP GIC path
requires a current CPU and is not safe in that QMP context. No IRQ, event flag
or semaphore is signalled by the observer.
See [E0524](../docs/evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md)
for the verified wait and the remaining producer boundary.

### Inputs from the public updaters (E0665)

No capture and no historical disk are needed. From the unpacked a6400 updater, plus
`ulogio.bin` from the unpacked ZV-E10 updater:

```bash
python3 -m tools.research.qemu_packaged_inputs <a6400>/firmware.tar_unpacked qemu-a6400/data
cd qemu-a6400
ULOGIO=<zve10>/firmware.tar_unpacked/0800_appli/setting/ulogio.bin \
SUBMICRO_FRAMES=data/submicro-frames-powered.bin \
SUBMICRO_FRAMES_SHA=$(sha256sum data/submicro-frames-powered.bin | cut -d' ' -f1) \
EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1 AVCAM=data/av-cam.bin bash run.sh
```

The builder needs `mkfs.fat` and `mcopy`. It records in `data/manifest.json` which bytes are
packaged and which are constructed. Partitions the updater does not carry are zero-filled.
Without `ulogio.bin`, `osal_ulogio` fails and the rear LCD stays black. To get a serial-only
boot, set `SUBMICRO_PAGE=` instead of the two `SUBMICRO_*` variables. The step-32 wait can be
read with QMP: `xp /1hx 0x04353570` (module 3 report) and `xp /1hx 0x04350f06` (its request).

### Historical input layout

Without `A6400_QEMU_DATA_DIR`, the legacy launcher expects the locally prepared
`qemu-a6400/data/` layout and its original `AVCAM` override behavior:

```bash
bash qemu-a6400/run.sh  # TIMEOUT=80 bash qemu-a6400/run.sh for a bounded run
```

`run.sh` reproduces the Sony bootloader's hand-off: kernel at `0x00108000`, initrd at `0x00700000`,
`"kemco"+cmdline` at `0x00103000` (machine code), the main-boot `/setting/kemco.txt` cmdline (console
swapped to `ttyAM0`), `Backup.bin` preloaded at its header word-0 address (`0x02E80000`) and
`av-cam.bin` at `0x035C6000`. The eMMC image is attached through a fresh qcow2 overlay.

The default build path is versioned and selected from
[`tools/toolchain.json`](../tools/toolchain.json); `A6400_QEMU_DIR` remains an
explicit override. Setup refuses a different source commit or a conflicting patch.
The 11.1.3 port preserves the old eMMC backend geometry through an explicit
Sony-only compatibility property; it does not resize the preserved disk image.
Prepared captured inputs or the legacy `data/` layout are still required;
neither is supplied by the toolchain upgrade.

State at E0521: kernel → init → all eMMC partitions mounted → 40 kernel modules → av-cam
`entry_main` → `stella`/`DMP_2D` → BusyBox shell on `ttyAM0`. See
[docs/evidence/0521-qemu-a6400-main-firmware-boots-to-avcam.md](../docs/evidence/0521-qemu-a6400-main-firmware-boots-to-avcam.md).

## Milestone 3: SMP (E0522)

The machine models 4 Cortex-A5 cores (`-smp 1` still works). A CPU0 stub presets GIC SPI targets
and enters the kernel. CPU1–3 wait on the kernel's SRAM mailbox (`0xFE005010`). Timers use the CXD4115
model (adapted from Oein/a5000-emulator), with per-CPU ticks on IRQ 153/155/156/157. `run.sh`
defaults to `-icount shift=0,sleep=on`. See
[docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md](../docs/evidence/0522-qemu-a6400-smp-four-cores-and-cxd4115-timers.md).

Findings, known gaps and the ordered next steps: [STATUS.md](STATUS.md).

Next, at the time of E0522: av-cam's hardware dependencies (no `liro-*` IRQ fires yet): sub-micro link, ADC, front end;
then the Sugilite display pipeline.

Earlier no-peripheral captured-profile startup investigation:
[E0527](../docs/evidence/0527-qemu-task43-initialization-flag-wait.md) finds
Task 43 in the first display-initialization wait on flag 10, mask 3, with
pattern zero. Enabled IRQs 101/102 never assert; Task 65 is dormant.
Those runs stop at the display/VPH producer boundary, without an injected flag
or assumed sub-micro completion. These addresses do not apply to the historical
packaged input set above. Later device-model and rear-panel progress below is
not negated by that earlier bounded observation.
Before each new blocker, check whether `Oein/a5000-emulator` already solved the CXD90014 equivalent.

## Milestone 4: Captured inputs and qualified waits (E0524)

The sections above this heading describe how to rebuild hash-checked inputs and
how the LIRO observer qualifies Task 43, IRQs 101/102, and Task 65. Keep that
workflow. The device work below uses the same boot.

## Milestone 5: Device models through the viewfinder console (E0525–E0541)

`run.sh` can add `/setting/ulogio.bin` to the per-run overlay (E0525) and can
replay the captured sub-micro page or its verified A/C/B extract (E0527).
Set `ULOGIO=` to skip the overlay addition. The default `ULOGIO` path is a
local ZV-E10 stand-in, not an a6400 capture.

The overlay staging supports `RUN_DIR` paths with spaces. For panel captures,
target VPH head 0 with `screendump /path/panel.ppm vph 0`. VPH head 1 is the
separate HDMI output.

`CAMPIG_ENABLE_READBACK=1` enables the experimental CamPig IRQ enable word
at `0xF3000050` (`-M sony-a6400,campig-enable-readback=on`). It latches the
last value written and resets to zero. The firmware uses a read-modify-write
to enable paths 3 and 2. This option covers the enable word; frame completion
and statistics are still separate research boundaries.

`SGC1_EVENT_A=1` enables the experimental input synchronizer event A
(`-M sony-a6400,sgc1-event-a=on`). It schedules the programmed line compare
on the virtual clock, using a 36 MHz source inferred in E0568. Event A
latches status bit 0 and, when enabled, raises IRQ 35. The experiment
covers the input event and leaves CamPig IRQ 36 and Ydet data unmodelled.

### Experimental live-view input

Set `LIVEVIEW_SOURCE=bars` to enable Sgc1 raster completion, CamPig/Ydet, and
RC0 DMA. `ramp` selects a different built-in test image; `pgm:/absolute/path`
uses a binary P5 grayscale image with an 8-bit maxval. `none` supplies a
black scene with the models enabled. Leave `LIVEVIEW_SOURCE` unset to keep
the default machine. The two diagnostic switches above are not required
when the live-view model is enabled.

The CamPig/AIF overlay retains the a5000 model attribution and is licensed
under GPL-2.0-or-later; see [COPYING](COPYING).

From the repository root in WSL, with the rebuilt QEMU and verified
captured boot inputs, use the powered 384-byte sub-micro fixture documented in
[E0651](../docs/evidence/0651-qemu-a6400-stock-finder-monitor-rear-lcd.md):

```bash
qemu=/path/to/pinned/qemu-checkout
inputs=/path/to/verified/prepared-inputs
micro=/path/to/wbi-powered-submicro-384.bin
work=$(mktemp -d /tmp/a6400-liveview.XXXXXX)
cp -- "$micro" "$work/submicro.bin"
printf '3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9  %s\n' \
    "$work/submicro.bin" | sha256sum --check &&
A6400_QEMU_DIR="$qemu" A6400_QEMU_DATA_DIR="$inputs" RUN_DIR="$work/run" \
    SUBMICRO_PAGE= SUBMICRO_FRAMES= ULOGIO= \
    EVSEL_LATE_US=500 REAR_CONTROLS=1 REAR_LCD_DIAGNOSTIC=0 LIVEVIEW_SOURCE=bars \
    QEMU_EXTRA="-icount shift=0,sleep=on -M submicro-frames=$work/submicro.bin -qmp unix:$work/qmp,server=on,wait=off" \
    bash qemu-a6400/run.sh
```

`REAR_CONTROLS=1` idles the active-low EVF eye contact input high so the
firmware can select the rear LCD without the diagnostic overlay. If the
NTSC notice covers the image, dismiss it with the normal rear-center
control (QMP `send-key` with qcode `ret`, 180 ms hold). Verify VPH head 0
and its ch51 luma plane, not just the ch53 overlay.
[E0591](../docs/evidence/0591-qemu-a6400-first-live-view-frame.md) records
the matched control and chart, fixture provenance and model assumptions.
The RC0 test scaler writes through the guest-programmed DMA descriptor
only after both reader and writer are armed. A visible test image does not
qualify the physical sensor, RAW/color ISP, or mode-sequencer completion.

`tools/a6400_probe.py` boots, waits until a checkpoint appears in
`/proc/snsc_boot_time`, runs shell commands, and stops QEMU.

The overlay adds SIO, GPIO, ADC, the timing-event block, VPH/SYSV, a Sugilite
GPU, SA DSP ID responses, and a P2S writer. Five guest seconds after
`SetContentTarget`, graphic console 0 shows the viewfinder readout (E0536).
In that replay the YUV layer was off. Forcing `isOutputEvf` to return 0 did
not select the rear-LCD skin (E0541). See [STATUS.md](STATUS.md).

## USB and sensor diagnostics

These diagnostics run offline against the ILCE-6400 main firmware. Prepare
captured inputs using the workflow above. The E0579 boot set already contains
`ulogio.bin`, so its runs use `ULOGIO=` to skip the launcher's optional copy.

- Rebuild the board overlay and set `P2S_TRACE=1` before running `run.sh`.
  The console records up to 64 accepted starts and their modeled completion
  or reset. Each start includes the raw transfer config, mode, and low bytes
  of the 112 staging words at `0xF3C00340–0xF3C004FC`. The staging words can
  retain bytes from previous transfers. Use the trace to compare guest
  programming with the IMX284 tables in [E0578](../docs/evidence/0578-a6400-open-gate-3-2-recording-feasibility.md).
- `tools/handover_guard.c` (E0638) checks the ARMv7 cache/MMU teardown before a
  kexec-style jump. Build it like the DWC3 observer below, using
  `-I "$A6400_QEMU_DIR/include/plugins"`, then add
  `-plugin /abs/handover_guard.so,entry=0xADDR,exit=1 -d plugin -D guard.log`.
  `tests/test_qemu_handover_guard.py` builds it with `tools/handover_payload.S`.
  Add `,dma=/abs/qemu-a6400/tools/handover_dma_contracts.txt` for the DMA
  quiescence check (E0639).
- Build the DWC3 observer against the QEMU 10.1 plugin API and append
  `-plugin /absolute/path/dwc3_trace.so` to your `QEMU_EXTRA` options.
  It records guest PC, physical register, access direction, width and
  value for matched accesses from the ILCE-6400 kernel. The footer reports
  counts and truncation per core. Preserve the default `-icount` option
  when comparing to the E0579 replay.

```bash
# Run from the project root with A6400_QEMU_DIR set to the QEMU checkout.
gcc -std=c11 -Wall -Wextra -Werror -Wno-unused-parameter -fPIC -shared \
    $(pkg-config --cflags glib-2.0) -I "$A6400_QEMU_DIR/include/qemu" \
    qemu-a6400/tools/dwc3_trace.c -o /tmp/dwc3_trace.so
```

Current boot observations and the next USB and sensor boundaries are recorded
in [E0579](../docs/evidence/0579-qemu-usb-and-sensor-observation.md).

### Virtual USB cable

Launch with `USB_VBUS_TEST=1` and a local `-qmp` socket in `QEMU_EXTRA`.
After the guest has started, send `qmp_capabilities` and set the cable
state. Use `false` to disconnect.

```json
{"execute":"qom-set","arguments":{"path":"/machine","property":"usb-vbus","value":true}}
```

The guest's FUSB handler reads the new status on IRQ 230. The experiment
starts disconnected and resets to disconnected. DWC3 gadget transactions
remain the next boundary. See [E0580](../docs/evidence/0580-qemu-usb-vbus-interrupt.md).
