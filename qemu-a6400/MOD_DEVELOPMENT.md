# Developing camera mods in QEMU

The QEMU guest runs the camera's own libraries and the original Sony menu. Mod Loader v2
installs inside that guest with its unchanged guard, and SMOD v2 mods load from host build
outputs. A rebuilt `.mod` replaces the running copy without restarting QEMU
([E0577](../docs/evidence/0577-qemu-real-mods-relocation-and-hot-reload.md)).

## One-time setup

1. Build QEMU with the current overlay (see [README](README.md)). Use a tree whose path has
   no spaces, for example `~/a6400-qemu-moddev`, and pass it with `A6400_QEMU_DIR`.
2. Prepare the camera-captured inputs (E0524). The default `qemu-a6400/data/emmc.img` holds
   a different `libObj.so` build than the camera, so camera mods do not match it.

   ```powershell
   .\venv\Scripts\python.exe -m tools.research.qemu_boot_inputs prepare --report-dir artifacts\qemu-a6400\inputs-moddev-1 --allow-unacquired-zero-fill
   ```

3. Build the loader and a relocatable mod:

   ```bash
   bash tools/native_camera/mlv2/build_wsl.sh artifacts/tests/NEW_LOADER
   bash mods/menu_touch_full/build_mtf_v2_wsl.sh artifacts/tests/NEW_MTF
   ```

## Run

Double-click `a6400_QEMU.bat` on the Desktop. When `artifacts/qemu-a6400/inputs-moddev-1` and a
`artifacts/tests/mlv2-layout-build-*` loader exist, the launcher starts in mod-development mode
(override with `A6400_MODDEV_INPUTS`, `A6400_MODDEV_LOADER`, or `A6400_MODDEV=0` for the plain panel).

The toolbar at the top left of the panel page:

| Control | Action |
| --- | --- |
| Load mod | Upload `.mod` files into the running guest (or drop them anywhere on the page). Uploads are kept in `~/a6400-moddev/mods`. |
| Watch build… | Enter a Windows or WSL path of a build output; every rebuild of that file is hot-reloaded. |
| Reload all / ↻ per mod | Force a reload now (new mapping, fresh state). |
| ✕ per mod | Stop watching and delete the guest copy. Switch the mod OFF in the Mods menu first. |
| Core | Push the loader core again; the shim loads it at the next menu root. |
| Rescan | Press MENU so the menu root, and with it the Mods list, is rebuilt. |
| Pause / Resume | Stop or continue the emulated CPUs. |
| Restart | Reboot QEMU; the loader and every loaded mod are installed again. |
| Frame | Save the current LCD image. |

Below the camera, the Mods panel lists each mod with its last result, the loader log and events.
A new mod appears in the Sony Mods page from the next menu root setup (Rescan, or MENU twice).

After the install the panel opens the menu, then closes and reopens it once, so the first menu you see
already has the seventh tab. The launcher also loads ModsTabLook (`mods/mods_tab_look/build_mtl_v2_wsl.sh`,
newest `artifacts/tests/mtl-v2-build-*`), the E0522 MODS tab artwork as a relocatable SMOD v2 mod with the
auto-on flag: the core switches it ON the first time it lists it, and the tab shows the MODS icon. Switched
OFF (or skipped with `A6400_MODDEV_TABLOOK=`), the core uses the sixth tab's ★ icon. The Mods tab is the
last tab, so Left from the first tab reaches it.

Manual start from the repository root in WSL:

```bash
A6400_QEMU_DIR=$HOME/a6400-qemu-moddev EVSEL_LATE_US=500 python3 -u qemu-a6400/tools/rear_panel.py \
  --port 8650 --log /tmp/moddev.log --inputs artifacts/qemu-a6400/inputs-moddev-1 \
  --loader artifacts/tests/NEW_LOADER --mod artifacts/tests/NEW_MTF/MenuTouchFull.mod
```

Open `http://127.0.0.1:8650/`. The panel does the following:

- copies the prepared inputs to `~/a6400-inputs/` once, which halves boot time compared
  with reading them from `/mnt/c`;
- boots to the menu checkpoint in about 35 s;
- stages and runs the guard, then pushes the core and the mods;
- opens the menu.

The Mods page appears from the next MENU press. The panel line under the LCD shows the
loader state, and `/moddev/log` returns the guest loader log and the last reload result.
The serial console log is written next to `--log`.

## Iterate

Rebuild the mod into the same path, for example by rerunning the packer or the build
script into a new directory and copying the `.mod` over the watched file. The watcher then:

1. pushes the file over the serial console and checks it with the guard's `fnv` operation;
2. publishes it by rename;
3. writes the reload request.

The push is a raw binary transfer: the guest terminal goes raw (`busybox stty raw -echo`) and
`busybox dd bs=1 count=N` receives exactly N bytes in 3.5 KiB chunks (about 0.9 KB/s; see "Warm guest";
 the older `printf` path remains the fallback). The guest BusyBox 1.29.3 has no `head` applet and no
`dd iflag=fullblock`; `stty`, `dd`, `base64` and `xxd` exist only as `busybox <applet>`.

While a mod swallows keys, the MENU nudge never
reaches the Sony menu, so a reload request stays pending until key forwarding stops.

On the Sony UI thread the core loads and hash-checks the new copy, then checks its targets.
It switches the old copy OFF with readback, and only then switches the new copy ON with fresh
state ([E0576](../docs/evidence/0576-mod-loader-v2-reload-request.md)). If no menu refresh
happens, the panel presses MENU at most twice. A rebuilt loader `core.bin` is pushed the same
way and loaded at the next menu root. Edits to `rear_panel.html` show on a browser refresh.

## Warm guest: seconds per test

Boot once and keep the guest running; each test then goes into the running QEMU
(`qemu-a6400/tools/warm.py`, run from the repository root in WSL; from Git Bash prefix
`MSYS_NO_PATHCONV=1`):

```bash
python3 qemu-a6400/tools/warm.py start --mod artifacts/tests/mtf-v2-build/MenuTouchFull.mod   # ~40 s, once
python3 qemu-a6400/tools/warm.py put /path/to/guest_helper /setting/helper                    # large files in 0.5-1.5 s
python3 qemu-a6400/tools/warm.py sh '/setting/helper --check'
python3 qemu-a6400/tools/warm.py press menu center
python3 qemu-a6400/tools/warm.py lcd /tmp/lcd.png
python3 qemu-a6400/tools/warm.py stop
```

Measured 2026-11-20: a complete warm-guest test cycle (put, run command, three keys, LCD) takes
about 9 s, of which about 6 s are the deliberate pauses between keys.

File transfer into a running guest uses the **host window**, a developer option of the machine
model and not a hardware model: `run.sh HOSTWIN=FILE` adds `hostwin=FILE`, which maps the host
file as shared RAM at PA 0x50000000 (16 MiB, between RAM bank 0 and bank 1). `POST /moddev/put`
writes `{ "HWIN", seq, len, fnv1a }` and the data into that file, and the 1.5 KB freestanding guest
helper `qemu-a6400/tools/hostwin_get.c` (staged as `/tmp_bt/hostwin_get` with `--hostwin-helper`)
maps the window through `/dev/mem`, checks length and FNV-1a and writes the file. The window needs
a QEMU built from the current overlay (`warm.py` defaults to `~/sony-a6400-qemu-11.1.3`); without
it `/moddev/put` falls back to the serial console.

Why not the serial console: under `-icount` the guest spends about 1 ms per `read()` system call,
so any scheme that makes the guest handle each byte (the `printf` escapes, or `busybox dd bs=1`
on a raw terminal) runs at about 0.9 KB/s: 274 KB took 315 s. A raw terminal also has no flow
control, so a single send larger than the guest's 4 KiB terminal buffer loses bytes; the raw
fallback therefore sends 3.5 KiB chunks.

## Fast test starts

Measured 2026-11-20 (`artifacts/tests/qemu-speed-1`, `qemu-speed-2`): a start is about 4 s of
QEMU startup, 33 s of firmware boot (single-threaded `-icount`), and 9 s of menu and core load.
Startup files no longer cost serial time:

- `--mod` files, the loader `core.bin` and every `--guest-file DST=SRC` are written onto the run's
  disposable `/setting` overlay before boot (`run.sh SETTING_FILES`, 8.3 names `STGnn.BIN`). In
  the guest they are copied into place (or symlinked when larger than 64 KiB, because the guest
  `/tmp_bt` tmpfs already holds the guard and Sony's files) and FNV-checked. A 726 KB binary is
  ready in 0.4 s instead of about 12 minutes.
- `--guest-cmd CMD` runs a guest shell command after the files are placed, before the install.
- `POST /moddev/sh` (same-origin, `X-Camera-Panel: 1`) runs one command in the guest shell and
  returns its output, for test scripts.
- `A6400_TIMING` replaces the default `-icount shift=0,sleep=on` for measurements. Without icount
  the firmware currently crashes (`liro-kliro_66` vector exception), so keep the
  default for real runs.
- From Git Bash, start `wsl.exe` with `MSYS_NO_PATHCONV=1`; otherwise guest paths such as
  `/tmp_bt/...` in `--guest-cmd` are rewritten to Windows paths.

Example (staging a mod and guest helper in one cold start):

```bash
A6400_QEMU_DIR=$HOME/a6400-qemu-moddev EVSEL_LATE_US=500 python3 -u qemu-a6400/tools/rear_panel.py \
  --port 8650 --log /tmp/moddev.log --inputs artifacts/qemu-a6400/inputs-moddev-1 \
  --loader artifacts/tests/mlv2-layout-build-4 --mod artifacts/tests/mtf-v2-build/MenuTouchFull.mod \
  --guest-file /tmp_bt/probe=/path/to/guest_probe \
  --guest-cmd "/tmp_bt/probe --status"
```

## Writing mods that run on both targets

Use the ILCE-6400 reference addresses (camera layout) in mod sources and pack with
`--reloc-layout tools/native_camera/mlv2/reference_layout_ilce6400.txt`. The packer lists
every literal word inside a library range. Review `reloc_words` in its JSON output, and use
`--no-reloc VALUE` for a constant that only looks like an address. The loader moves those
words, and the hook targets, by each library's runtime base. The same `.mod` file therefore
runs in QEMU's layout and, unmoved, on the camera. Heap and anonymous-memory addresses are
never moved. A mod that compares such a pointer must still be checked on each target.

## Limits

For USB-dependent mods, the [offline media scenarios](README.md#offline-sd-and-usb-scenarios)
verify enumeration and basic Mass Storage reads on a separate fixture.
Do not treat that as Sony gadget compatibility. Require the original
Sony gadget to enumerate and complete a basic MSC or PTP read before
testing streaming.

- There is no QMP absolute-pointer handler for a physical touchscreen.
  An explicit [native touch report replay](../docs/evidence/mods/2026-11-19-firmware-qualification.md#native-touch-follow-up)
  supplies down/move/up below `InputService::callback_tp` instead. The
  matched v2 ON/OFF controls cover tab taps, horizontal swipes, page dots,
  row selection/opening, and popup highlighting/dismissal. They do not
  qualify controller hardware, popup setting commits, or vertical scrolling.
- The guest tmpfs at `/tmp_bt` and the `/setting` staging exist only in the disposable overlay.
- A QEMU pass covers the tested contract. Camera use still needs an explicit go-ahead and a
  RAM-only verification for physical peripherals and timing.

### Staged boots without Sony's application

`python3 qemu-a6400/tools/warm.py start --dmode 2` writes bootin's mode file (`/setting/mode/dmode`,
one ASCII digit: 0 kernel only, 1 global infra, 2 LIRO = infrastructure + av-cam, 3 application) to
the run's overlay and starts the panel in `--bare` mode (shell and helper files only, no Sony UI
checkpoint, no mod loader). `--setting-file NAME=SRC` adds more files before boot, e.g.
`env.txt` (lines `/sbin/init` puts into its environment, such as `LD_PRELOAD=/setting/tap.so`).
In a mode-2 guest, `im.elf i` / `im.elf a` continue the running im.elf with the next phase.
