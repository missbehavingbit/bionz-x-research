# Changes

## v1.20 (2026-11-19)

* **`tools/make_sdcard.py`**: builds a ready-formatted memory card without root or mtools: MBR +
  FAT32 laid out like the SD formatter (partition at sector 8192, 32 KiB clusters, data on a 4 MiB
  boundary), the pictures as `DCIM/100MSDCF/DSC00001.JPG ...` and Sony's empty marker file
  `PRIVATE/SONY/SONYCARD.IND`. Default picture: `tools/a7c_test_image.jpg` (generated test pattern
  with EXIF and a 160x120 thumbnail); `--image` takes your own JPEGs. Checked with `fsck.fat`.
* **Result: a pre-filled card does not get past the problem yet.** The camera mounts it and reads
  the boot sector, FAT, root, DCIM, 100MSDCF, PRIVATE and SONY, but then stops before drawing its
  first screen (as the FAT32 test in v1.14), and writes nothing. The empty card still reaches the
  "Format?" question. Details in docs §7.22.
* `A7C_CARD_WP=1` sets the SD write-protect line (0xF2915010 bit 25) for experiments; no visible
  effect so far.

## v1.19 (2026-11-19)

* **Reliable key presses.** The emulator now holds every camera key for at least 150 ms of the
  camera's own (virtual) time (`A7C_KEY_MS`). Before, a press was timed in host time, but under
  icount the guest's time runs unevenly (an idle guest jumps ahead), so a press was missed at times
  and auto-repeated in the menus at others (one click moved two or three pages). Checked: one Up /
  Down click moves the selection exactly one step.
* **Panel:** a click is now a short press (the 1.5 s host hold is gone); holding the mouse button is a
  long press. New `--qemu-arg` option (extra QEMU arguments).
* `run.sh`: `A7C_APPEND="..."` adds kernel parameters (e.g. `log_buf_len=8M norandmaps`).
* Memory-card format: narrowed further (docs §7.21), not fixed yet.

## v1.18 (2026-11-18)

* **Fix: the WSL rebuild failed with "Registering `cxd90045-dwc3' which already exists"** (v1.16/v1.17).
  sony_a7c.c included hw/usb/cxd90045_dwc3.c directly, and `tools/rebuild_wsl.sh` also adds new
  overlay .c files to meson, so the model was built twice. The include is gone; the file is built
  through meson only (rebuild_wsl.sh as before, and setup-qemu.sh now adds such files too).

## v1.17 (2026-11-18)

* **Web panel.** `start_a7c_panel.bat` (or `python3 tools/a7c_panel.py` in WSL) starts the emulator
  in the full mode without a QEMU window and opens http://localhost:8642: the camera screen plus the
  A7C's controls (mode dial, exposure compensation, C1, AEL, shutter half/full, REC, MENU, Fn, play,
  delete, the control wheel with OK and rotation), the same keys on the keyboard, pause / restart /
  save screenshot, and the emulator's state. Buttons follow the mouse (press and hold), and a click
  is held for at least 1.5 s (`--min-hold`), because the camera samples its buttons slowly in the
  emulator. Idea from bionz-x-research qemu-a6400 tools/rear_panel.py; written for the A7C.
* Checked through the page: OK answers the format question, MENU opens the camera's menu, the
  arrows move in it, MENU closes it.

## v1.16 (2026-11-18)

* **USB controller (DWC3).** The DWC3 gadget model from the updated bionz-x-research qemu-a6400
  (`cxd90045-dwc3`, E0579/E0590) is now in the emulator, mapped at 0xF020C000 with IRQ 229 like on
  the a6400. Before, the kernel logged "dwc3: Reset Timed Out / probe failed -110" and
  "usb_gadget_register_driver() failed(-19)"; both are gone. On in `A7C_FULL` (`A7C_DWC3=0` turns it
  off). There is no USB connection to a PC yet: the camera only starts its USB mode once a cable
  (VBUS) is reported, and that is not modelled.
* Debug: `A7C_SM_TXLOG=1` prints the CPU's power-micro frames (slots A/B/C and ch0 05..08) when they
  change.
* Docs §7.19: what in the updated qemu-a6400 was checked and what was taken over.

## v1.15 (2026-11-18)

* **`start_a7c_full.bat`:** one double-click updates and rebuilds the WSL copy, creates an empty
  memory card if there is none (an existing card is kept) and starts the emulator in the full mode.
* **`create_sdcard_wsl.bat`:** creates or replaces the empty card `out/sdcard.img` (4 GB, or e.g. `8G`).
* Docs §7.18: where the card formatting stops (inside the camera's own software, before any card
  access).

## v1.14 (2026-11-18)

* **The camera finds the memory card.** With `A7C_SD_IMAGE=file` (an empty file, e.g. 4 GB) the
  camera detects the card, reads it and asks "Unable to use memory card. Format?", as a real camera
  does with a blank card. Needs `fw/submicro_frames_live.bin` (from the live-capture tool), which
  `A7C_FULL` now uses automatically.
* Formatting and using a card do not finish yet (docs §7.18).
* PCIe: the card reader is now found the same way as on the camera (hot-plug of slot 2).
* Docs §7.18.

## v1.13 (2026-11-18)

* **Mode dial keys now follow the real dial:** 1 AUTO, 2 P, 3 A, 4 S, 5 M, 6 MR1, 7 MR2, 8 MR3,
  9 movie, 0 S&Q (checked on the camera's own screen). The emulator starts in P
  (`A7C_MODE_DIAL=AUTO` etc. to change).
* **C1 and AEL buttons** (keys C and A), found by testing the camera's input lines one by one.
* Debug: `qom-set /machine/gpio poke P.N=L` in the QEMU monitor drives any input line.
* Experimental: `A7C_SD_IMAGE=file` puts a memory card in the card reader; the camera does not use it
  yet (docs §7.17).
* Docs §7.17.

## v1.12 (2026-11-18)

* **More controls:** H = shutter half press (closes the menu, as on the camera), Space = full press,
  `[` / `]` = control wheel (scrolls the menu), 1-9 and 0 = mode dial positions, `=` / `-` = exposure
  compensation dial. Which number is which mode is not known yet.
* **Steadier screen:** the screen no longer flickers to black between frames, and the rear-panel picture
  is read in the right colour format.
* Docs §7.16.

## v1.11 (2026-11-18)

* **Buttons work: the camera's own menus open.** In the emulator window: M = MENU, arrows = up/down/
  left/right, Enter = centre, P = playback, F = Fn, Delete = delete, R = movie. (`A7C_FULL=1`, or
  `A7C_REAR_CONTROLS=1`.) Hold a key about a second; the emulated camera runs slower than real time.
* **The window shows what the camera outputs** (`A7C_SCREEN=vph`, in `A7C_FULL`): the A7C draws its UI
  on the viewfinder output; the VPH screen now shows that instead of staying black.
* `A7C_FULL` also sets `A7C_GPIO_LIVE=1` (buttons released at start).
* Docs §7.15, `docs/a7c_proc_udm_input.txt` (the A7C key table).

## v1.10 (2026-11-17)

* **The camera app now runs and draws its own screen.** `A7C_FULL=1 ./run.sh` (or
  `run_emulator_wsl.bat A7C_FULL=1`) shows the firmware's "NO CARD" screen; the app no longer powers
  itself off.
* New: memory-card reader on PCIe1 (`A7C_CARD_SLOT=1`): root port + O2 Micro SD host as on the
  camera, no card inserted. Needed once the power micro's slot C is answered.
* evsel `late-src`/`late-us` from bionz-x-research qemu-a6400 (`A7C_EVSEL_LATE_US`, opt-in).
* Docs §7.14.

## v1.9 (2026-11-17)

* **Power-chip replies are now accepted.** Found av-cam's check for the run-time replies (commands
  05-08): the XOR of the reply's 64 halfwords must be 0x0F0F and byte 2 must echo the command. The
  model now always sends valid replies; with `A7C_CH0_FILE` it replays the camera's own (all accepted).
* **Slot C found** (third power-micro frame, 0x61/0x60, like the a6400). Answering it makes the kernel
  power the memory-card slot, which the emulator does not have yet (PCIe BUG), so it is opt-in:
  `A7C_SLOTC=1`.
* `A7C_GPIO_LIVE=1`: GPIO input levels of the switched-on camera (experimental).
* Live-capture tool: the power-micro frame file now also holds slot C (384 bytes; the emulator reads
  the first 256 as before).
* Docs §7.13: the app still powers itself off ~20 s in (`OFF_BY_KEY`, power-manager factor 7): next.

## v1.8 (2026-11-17)

* **Faster runs: `A7C_ICOUNT=2`.** With `A7C_ICOUNT=0` emulated time ran about 44x slower than real.
  Shift 2 is several times faster, the power-chip start-up still completes, and the camera app runs
  to its power checks and stays up (no suspend). Recommended:
  `A7C_ICOUNT=2 A7C_MACHINE_OPTS=evsel=on ./run.sh`.
* **Power-chip run-time replies.** After the start-up the camera polls the power chip on ch0 with
  commands 06/07/08; these replies (battery, power status, ...) are still zeros, so the app reports
  "Unknown" battery and power state. `A7C_CH0_FILE=<capture>/pa_0414db00_ch0_buffers.bin` replays the
  camera's own replies.
* **Live-capture tool:** also reads the power-chip ch0 buffers (PA 0x0414D000, new file
  `pa_0414db00_ch0_buffers.bin`, both passes).
* Docs §7.12: the micro's start-up state now matches the live camera byte for byte; correction: the
  "ready" byte is 0 on the camera too.

## v1.7 (2026-11-17)

* **Power-chip (SIO ch0) start-up fixed:** the micro model now returns the running 16-bit checksum of
  the uploaded program in each reply, as av-cam checks it (docs §7.12). The start-up script now runs
  through all steps (1 -> 7) and error 9 is gone under `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on`;
  av-cam goes on to decode its first UI images.
* The sub-micro's status exchange (ch3) pauses while the program upload runs (strobe held high),
  because ch3 traffic during the upload is av-cam's error-9 condition.
* Still open: the micro's one-time "ready" report (ready byte 0x6453B9E1 stays 0); screen stays black.

## v1.6 (2026-11-17)

* **SIO ch0 model:** every 0x39 command is now acknowledged with the current request code (the code
  the 0x0D polls carry), as av-cam checks after 0x0E starts too. The micro start-up script now
  reaches step 3 of 7 (was 2) before the CPU restarts the micro; error 9 still follows.
* Logging of ch0 requests is off by default: `A7C_CH0_LOG=1` prints them to the terminal (the earlier
  `-d unimp` logging could grow to many GB).
* Findings in docs §7.11 (step counter 0x6453B9E2, ready byte 0x6453B9E1, request code 0x6453B9DF,
  the upload is the micro's own program; corrections to §7.9/7.10).

## v1.5 (2026-11-17)

* **`rebuild_qemu_wsl.bat` installs missing build tools.** A fresh WSL Ubuntu has none of QEMU's build
  tools ("ERROR: Cannot find Ninja"). The script now checks for them (git, build-essential,
  ninja-build, pkg-config, python3-venv, libglib2.0-dev, libpixman-1-dev, zlib1g-dev, libfdt-dev,
  libgtk-3-dev and libsdl2-dev for the emulator window), lists what is missing and offers to install
  it with `sudo apt-get` (asks for your WSL password).
* **`run_emulator_wsl.bat`** says "QEMU is not built yet: run rebuild_qemu_wsl.bat first" instead of
  failing in `run.sh` when there is no build.

## v1.4 (2026-11-17)

* **SIO ch0 model** (the power micro's "0x39" command channel). Before, nothing was attached, so the
  CPU read its own requests back as replies, started a program-upload loop to the micro and ended in
  error 9. Now ch0 answers the 0x0A query with the live camera's reply and acknowledges 0x0D status
  polls (the check av-cam does at 0x63876B00). The polls are now accepted; error 9 still follows
  later in the exchange (next: the 0x10-byte poll reply and the `F5 00` transfers). Only matters with
  `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on`; the default boot is unchanged.
* Findings in docs §7.9–7.10; reply to the menu-tab handoff in `docs/HANDOFF_REPLY_menu_tab_switch.md`.
* Capture tool: double-clicking `a7c_live_mem.py` opens the window.

## v1.3 (2026-11-17)

* **Fix: `rebuild_qemu_wsl.bat` with no existing WSL copy.** v1.2 then tried to build QEMU inside
  the Windows folder, and QEMU refuses paths with spaces ("main directory cannot contain spaces").
  It now creates `~/a7c-emulator` in WSL, links its `fw`, `img` and `dump` back to the Windows
  folder (nothing large is copied), moves a QEMU source tree left behind by the failed attempt
  instead of downloading it again, and builds there. Later runs update that copy.
  `setup-qemu.sh` now stops with a clear message when its path has spaces.
* **`run_emulator_wsl.bat`**: starts the emulator in `~/a7c-emulator` from Windows; settings can
  be passed, e.g. `run_emulator_wsl.bat "A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on"`.

## v1.2 (2026-11-17)

* **New camera units** (machine option `units`, on by default; `units=off` to compare): ADC units
  0/1 (0xF2406000/0xF2407000, IRQ 215/216, all keys released), the four SA DSP cores ("null SA",
  0xF34A0000/0xF34B0000/0xF34C0000/0xF35C0000, IRQs 51–56/61–66, with the core ID strings av-cam
  checks) and the P2S sensor register writer (page 0xF3C00000, IRQ 38). Ported from the a6400
  machine in bionz-x-research (originally from the a5000 emulator); all four are referenced by
  the A7C av-cam.bin and active on the live camera. av-cam now reads the SA core IDs.
* **`rebuild_qemu_wsl.bat`** (+ `tools/rebuild_wsl.sh`): finds the WSL copy of the emulator,
  backs up and syncs its sources from this folder, copies changed overlay files into QEMU and
  rebuilds. `setup-qemu.sh` now copies changed overlay files on every run.
* **Live capture tool:** pieces shrink automatically when the camera's `/tmp_bt` is full
  (it has only ~40 KB free), so the av-cam data reads now complete.
* **Findings** (docs §7.8): live camera interrupt rates, evsel configuration (four frame sources,
  50 Hz frame interrupts), GIC enables; error 9 is the power-micro link handler's deliberate stop
  when link state byte [0x644C4734+2] == 3, a value copied from a mode table (writers
  0x63874306 / 0x638743E0); the frozen kernel timestamps under icount were the crash, not a clock fault.

## v1.1 (2026-11-15)
Since v.1 (session 4: boot to the camera app, power-micro link, GPU display).

* **Timing:** `A7C_ICOUNT=0` runs QEMU with `-icount shift=0`, so emulated time follows the guest's
  instructions instead of host speed. With `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on` the firmware's
  1/8-frame timer steps meet their deadlines and the camera **sends the power-lever / clock / battery
  frame (slot A) for the first time**. It then stops at av-cam's own fatal-error check (error 9),
  which is next (docs §7.7).
* **Machine options:** `A7C_MACHINE_OPTS` (appended to `-M`); evsel `-global cxd90014-evsel.start-ms=N`;
  experimental `handoff=FILE` / `A7C_HANDOFF_FILE` (captured bootloader hand-off page at PA 0x20021000);
  `A7C_SUBMICRO_FILE` (other power-micro frames, e.g. live ones).
* **Findings** (docs §7.6, §7.7, §10): the two gates in front of slot A, the HS step sequence on evsel
  comparator 1, the per-frame routine 0x63967E58, live camera reads of the power-manager table, the
  hand-off page and the power-micro frames, and the clock byte layout.
* **Live capture tool v2** (folder `a7c-live-capture`, next to the emulator): reads memory twice
  ~5 s apart (hand-off page, power-micro frames, power-manager table, 4 MB of av-cam data), camera
  object 1's +0x0C target, `/proc/interrupts`, `dmesg`, boot steps, and (optionally) the evsel, GPIO
  and GIC distributor registers. Read-only; uses the research repository's debugger client.
