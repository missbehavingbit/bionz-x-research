# α7C bring-up notes (emulator v1.20)

Evidence tags: **D** = read from the A7C dump (metadata, partitions, binaries);
**K** = printed by the stock kernel running in the emulator; **X** = from
disassembly of the A7C kernel / modules / av-cam; **A6** = inherited from the
sony-a6400 machine, not re-verified on the A7C.

## 1. SoC and memory
| Item | Value | Ev. |
|---|---|---|
| CPU | 4 × Cortex-A5 r0p1 (`CPU part 0xc05`), VFPv4-D16, no NEON | D (proc_cpuinfo), K |
| Machine | `ARM-CXD900X0`, ID 0xDA9 (same as A6400) | D, X (literal in Image) |
| Kernel | `3.0.27_nl-rt106+ #1 SMP PREEMPT RT Mon Nov 15 03:46:31 JST 2021` | D |
| RAM | `mem=1G@0`, `mem=256M@0x80000000@1` → 1 GiB + **256 MiB** | D (cmdline), K |
| Reserved | `memrsv=0x1A5E000@0x02E68000` (Backup + av-cam), `680M@0x15800000:uc`, `256M@0x80000000:uc` | D |
| GIC / SCU | A9MPCore private region 0xF8000000 | A6, K |
| UART0 | 0xF2000000, PL011, IRQ 150 (`ttyAM0`) | D (iomem), K |
| UART3 | 0xF2003000 (registration fails in the emulator, harmless) | D, K |
| Timer | CXD4115 @ 0xF2403000 | A6, X, K |
| eMMC host | SDHCI @ 0xF0304000 | A6, X, K |
| USB | DWC3 @ 0xF0200000 (`sony_usb_init: CXD4132`) | D, K (not modelled) |
| PCIe | RC0 DBI 0xF0140000, RC1 DBI 0xF0180000, ctrl = DBI+0x20000; windows 0xC0000000–0xE7FFFFFF / 0xE8000000–0xEFFFFFFF | D (iomem), X |

## 2. Flash layout
- `/dev/nflasha` **is the eMMC user area, linear, from LBA 0** (D, K). It starts with a
  Sony table `"82461.00"`: 48 entries at +0x20 of `{start, size, flag, 0xffffffff}`
  in bytes. Every entry matches the size of the individually dumped `nflashaN.raw`,
  and the ext2 superblocks of nflasha7/15 sit exactly at the listed starts.
- `nflashaB0` / `nflashaB1` (4 MiB each, B0 begins `EXBL`) are the eMMC boot partitions.
- **Addressing.** Sony's ALFReD eMMC driver always sends *sector* addresses, as for any real eMMC
  part. Upstream QEMU sector-addresses a card only above 2 GiB, and this flash is 1,872 MiB. Byte
  addressing made only LBA 0 (the partition table) read correctly, and every mount failed with
  `can't find an ext2 filesystem` (K). **Fixed in the model:** new eMMC property
  `sector-addressing` (patch to `hw/sd/sd.c`: OCR CCS, CSD C_SIZE = 0xFFF, block addresses), set by
  the machine. eMMC images may also have any 512-byte-multiple size (the SD power-of-2 rule does not
  apply: eMMC capacity is EXT_CSD SEC_COUNT). The image is the dump as is, no padding
  (kernel: `eMMC: 1864MiB`). Session 1 worked around this by padding to 4 GiB.
- Partitions: 1 boot FAT · 2 `/setting` · 3 `/system` · 4 CMMe · 5 WBI image ·
  7 `/` ext2 · 10 `/etc`,`/tmp` · 11 `/log` · 12 `/cert` · 13 hash/history ·
  15 `/usr` ext2 · 17 · 18 `/lens` · 23 (D: proc_mounts, table).

## 3. What the bootloader leaves in RAM (reproduced by the machine)
| Item | Where | Consumer | Ev. |
|---|---|---|---|
| Image | PA 0x00108000 | — | A6, K |
| initrd | PA 0x00700000 (`initrd=0x700000,8M`) | kernel | D, K |
| `kemco` + cmdline | PA 0x00103000, r2 | `setup_arch` (`cmdline override at 60103005`) | A6, K |
| SPI targets / secondary release | GICD_ITARGETSR, SRAM 0xFE005010 | gic init / smp | A6, K (`Brought up 4 CPUs`) |
| **Backup.bin** | PA = header word 0 = **0x02E80000** | `backup.ko Backup_load=0` (init) | D, X (init strings), K |
| **av-cam.bin** | PA **0x035C6000** (`LIRO` magic at +4, vectors at VA 0x635C60xx) | `liro.ko`: `Param:adrs=035c6000` | D, K |
| eMMC info table | PA 0x80000000 (`emmc.ko tbl=0x80000000`) | emmc.ko: warns `Information table is invalid` and continues | X, K (not reproduced) |

Without Backup.bin in RAM, `backup_init() error (file_load)` and ~15 modules fail
on missing `Backup_*` symbols (K). Without av-cam in RAM:
`Magic unmatched 0 != 4c49524f` → `tsk_entry_1st() error` (K).

**Fixed in the model (bootloader stand-in, `a7c_bootloader_reset` in `sony_a7c.c`).** At every reset,
as the real bootloader does on every power-on, the machine reads the eMMC through its drive: the
"82461.00" table at LBA 0, then the FAT root directories of nflasha2 and nflasha3. It copies
`/setting/Backup.bin` to the PA in its header word 0 and `/system/av-cam.bin` (after checking the
`LIRO` magic) to its link base from its own vector table (`+0x0C & ~0xFFF` − PAGE_OFFSET
0x60000000). No host-side copies or `-device loader` are needed. Settings the camera saved to flash
are picked up on the next reset (checked: `system_reset` → av-cam loads and starts again).
`-M sony-a7c,bootloader=off` disables it. Session 1 worked around this with `-device loader` in `run.sh`.

## 4. A7C-specific hardware model added
**PCIe root complexes** (`a7c_create_pcie` in `sony_a7c.c`). `PCIeRC_Initialize`
(0x60122A80) programs ctrl+0x000/0x100, then polls ctrl+0x000 bit 30 up to 10⁶
times and calls `BUG()` (`pcie_ctl.c:428`) if it never sets. That killed
`mmc_deferred_st` in the first runs (K). The model is plain register storage with
bit 30 forced on, so the kernel enumerates an empty bus 0x80 (K). The SD/memory
card slot (`mmc-pci` @ 0000:81:00.0 on the real camera) needs an endpoint model.

## 5. Session 2: from the av-cam crash to the running camera app
Each row: symptom (K/X evidence) → cause → fix.

| # | Symptom | Cause | Fix in `sony_a7c.c` |
|---|---|---|---|
| 1 | av-cam `liro-kliro_65` NULL read at 0x63DAF23A (`ldr r4,[r2,#0x44]` = 0) | `0x63DA556C` copies `base[0x4950]` with base = **0xFE5F0B00**. The kernel maps 0xFD000000–0xFEFFFFFF 1:1 (gva2gpa), but the model only had 1 MiB SRAM at 0xFE000000, so writes were dropped and reads returned 0. A gdb watchpoint showed the field is written, but with 0 (X, gdb) | RAM 0xFE100000–0xFEFFFFFF at low priority ("fe_xram"). [real size of on-chip memory unknown] |
| 2 | after that, CPU0 spins in av-cam forever | `pollingWait_Denali_0E8` (0x637A51C4, "IMG:DFE DDR3 Init") polls the **Cadence Denali** DDR controller of the DFE at **0xFC01C000** +0x0E8 bit 16. Its timeout uses get-time 0x63784A34 = read **0xF3C30000+0xE0**, which was frozen at 0, so it never times out (X) | Denali model: +0x0E8 reads bit16 set. 0xF3C30000+0xE0 = 1 MHz counter (+0xE8 = 0x17 → 24 MHz/24 [?]) |
| 3 | no output on UART0 at all, no shell | the cxd900x0 console driver unmasks TXIM (IMSC 0x50→0x70) and **waits for the TX interrupt before writing**; QEMU's PL011 raises TXRIS only after a write (trace `pl011_*`). The kernel also needs **`amba2.console=1`** (param exists; Sony's debug cmdline in nflasha1 has `amba2.console=0`) | PL011 property `tx-level-irq` (patch) set on UART0; `run.sh` adds `amba2.console=1` with `console=ttyAM0` → stock init starts `/bin/ash` on the console |
| 4 | `im.elf` UI draws nothing; frames are 640×480 PPF transfers | the A6400 GPU stub only raises completion IRQs. Same GPU as the a5000 (same `sugilite_register_init` sequence, PICA-style PSC/PPF/P3D). **PPF addresses are >>2**: src 0x20312200, dst **0x22AB2000 / 0x22B48000** (= `im.elf`'s /dev/stream mappings, 0x96000 = 640×480×16 bpp), fmt 0x4401 = RGBA4444 | a5000's `dmp_smaph.c` + `dmp_shader.c`; `sony-fbview` follows the last linear PPF destination (QEMU console 0) |
| 5 | boot step `Og 001_ObjMic` never ends (`/proc/snsc_boot_time`) | `ObjMic` waits on av-cam's audio side, which is paced by the frame clock. All `liro-*` IRQs were at 0 | a5000's VPH + **SYSV** model (0xF3440000 / 0xF3402000, IRQ 98/101/102), mapped above the 0xF3400000 clock stub; boot stub sets GICD_ICFGR6 = 0x28A0 (98/99/101/102 edge, as the a5000 bootloader) |
| 6 | with the a5000 **evsel** (0xF3C30000, IRQ 83/84) the boot stalls earlier at `Ii 082_libgpe.so` / `055_libInfraAvbbc.so` (bisected) | not analysed [?] | evsel off by default (`-M sony-a7c,evsel=on`); 0xF3C30000 is only the counter |
| 7 | — | catch-all for 0xF0000000–0xFCFFFFFF was read-0/write-ignore; av-cam write-then-verify sequences (a5000: AudioIp) need read-back | sparse **register file** catch-all (logs every access with `-d unimp`) |

New A7C facts:
* **IRQ map** from `/proc/interrupts` in the guest: GPU `dmpgles2` 76, eMMC 227, UART0 150, ticks 153/155–157,
  ldec 171, xdmac 189–196, dma330 115–119, sircs 197/198, i2c 199/200, ms 206, dwc3 229, fusb 230/231,
  PCIe 237/239/245/247, rstreq 254, L2 255, NFC 126/127, and ~80 `liro-N` (av-cam) lines incl. 79, 83, 84, 98, 99, 101, 102.
* `im.elf` maps (`/proc/<pid>/maps`): GPU regs 0xF1000000 via /dev/mem, GLES heap /dev/stream PA 0x20312000 (32 MiB),
  OSD buffers 0x22AB2000 / 0x22B48000, `/dev/uipc` PA 0x09800000, WBI 0x20028000 / 0x20098000 / 0x20145000.
* `im.elf` debug log ("ulog") lives in the 0x02E68000 carve-out; `tools/ulog.py` extracts it.
* av-cam keeps a boot log around PA 0x0325B000 (`[INIT] boot init start/end`, `[MCPINMAIN] PinGet…`).
  It reports `[IDT]: IDT : Init NG : address = 20152000` (sensor front end; not yet looked at).

## 6. How to find the next blocker (method that worked)
1. `tools/uicheck.sh 200`: unfinished `im.elf` boot steps. The last "B" without "E" names the stuck module.
2. In the shell: threads of `im.elf` with their `wchan`. `echo t > /proc/sysrq-trigger` gives every task's
   kernel backtrace; av-cam frames stop at its veneers, so dump the task's kernel stack (`memsave`) and scan
   it for Thumb return addresses inside av-cam (0x635C6000–0x64587400).
3. `tools/hwscan.sh`: registers read hundreds of thousands of times = poll loops on missing hardware.
4. `info registers -a` in the monitor: which code the CPUs are spinning in; disassemble AV-CAM.BIN
   (raw image, VA = 0x635C6000 + offset; Thumb) or the kernel (VA 0x60108000 + offset; ARM).
5. Check the a5000-emulator docs for the same block: the CXD900X0 shares most of its camera
   blocks and IRQ numbers with the CXD90014.

## 7. Session 4: power micro (sub-micro) link and the evsel stall

### 7.1 Link wiring (A7C), read from av-cam's own variables
av-cam has the a5000's link driver (same strings: `FrtComSkip`, `TskJobNG`, `ReleaseLock`, `S2REQ`,
`BATT_AUTH`; same Backup ids). `0x6376D618` reads the board lines from Backup into globals through
the GOT (base 0x646519C0). Read in a running guest with `xp`:

| role | Backup ids | a5000 | **A7C** |
|---|---|---|---|
| micro strobe (each edge = link event 0) | F90465/66 | p0.12 (IRQ 133) | **p0.8** (IRQ 129) |
| micro request (low = frame, high = command) | F904CA/CB | p3.17 | **p2.5** |
| event 16 | F9045F/60 | p0.7 | **p0.9** (IRQ 130) |
| "ExpBefore" line | F90200/01 | p0.16 | **p1.2** (IRQ 139) |
| S2REQ (CPU → micro) | F90455/56 | p10.3 | **p7.15** |
| SIO chip select | F900B1/6D | p5.14 | p5.14 |
| link SIO channel | F900D4 | 3 | **3** |

GPIO and SIO are the same blocks as on the a5000: kernel `/proc/udm/devices` lists `gpio 18` at
0xF2910000 + 0x100·n; av-cam uses SIO 0xF2405000 + 0x200·ch for 6 channels (IRQ 201+ch).
**On the A7C both the 8-byte headers and the 128-byte frames run on ch3** (trace `cxd_sio_xfer`).
ch0 carries a different device (first byte 0x39) and ch1 carries 2-byte transfers.

### 7.2 Frames: from your camera's warm-boot snapshot
`nflasha5` is a compressed WBI RAM snapshot (`tools/wbi.py`, self-contained; 656 sections). It
excludes av-cam's image and data but includes the 0x03085000–0x03585000 carve-out. A scan for 128-byte
frames with a valid CRC-16/CCITT-FALSE found:

| PA | frame |
|---|---|
| 0x034433A4 | micro → CPU slot A (type 0x20): byte 28 = 0x01 (**power lever ON**), bytes 6–12 = clock (02 14 01 07 11 01 09 = valid, 2020-01-07 17:01:09) |
| 0x034434A4 | micro → CPU slot B (0x30): TLVs 01, 02, 03 (data[1] = 0x40 = **powering off**: the snapshot was taken at power-off), 0B, 10, 14 |
| 0x034435C8 / 0x034436C8 | CPU → micro slot A (0x21) / slot B (0x31; TLV 0A data[1] = 0x60 = power-off request) |

Note: on the A7C slot B sits **+0x100** after slot A (a5000: +0x80). `tools/a7c_extract.py` writes
`fw/submicro_frames.bin` with the TLV 3 flag cleared and the CRCs recomputed.

### 7.3 Model (`sony_a7c.c`, `-M sony-a7c,submicro=fw/submicro_frames.bin`, set by `run.sh`)
* `cxd90014-gpio` (a5000, `board=a7c`: all external lines idle low) and `cxd90014-sio` (a5000, 6 channels).
* The micro HLE (port of the a5000 one) is attached to SIO ch3. It replays the slot A / slot B frames,
  with the power lever forced ON, the clock from the QEMU RTC (and the CPU's clock writes honoured),
  slot-A 0x41 memory requests answered, and power-off requests acknowledged in TLV 3.
  It strobes p0.8 (1 ms every 50 ms) with p2.5 low.
* Boot stub: GICD_ICFGR8 = 0x00800028 makes IRQ 129/130/139 edge-triggered. As level interrupts,
  the 1 ms strobe pulse re-entered the ISR about 100 times per edge (530,000 IRQs in 2 min).

**Result:** the link runs (about 7,900 slot-B exchanges in 220 s, answered), **but the CPU never sends
slot A**, and slot A is the frame that carries the power lever, the clock and (probably) the battery.
On the a5000 slot A is sent only from the engine frame tick (link event 4, `bed1` in the state table),
which needs the evsel frame events. The app therefore still logs `OFF_BY_KEY`, `OSAL_DATE_NOTIME` and
an unknown battery state, and suspends at ~67 s.

### 7.4 The evsel stall: root cause and fix
With `evsel=on`, CPU2 spun forever at av-cam 0x638659FE:
`str 1 → [0xF3C08000+0x1E0]; loop: ldr [0xF3C08000+0x1E0]; cmp 1; beq loop`. That is a self-clearing
trigger in the sensor-interface block, called from the evsel frame-event path. The read-back catch-all
kept it at 1. **Fix:** the catch-all now has a self-clearing register list (`a7c_selfclear[]`,
0xF3C081E0). evsel itself was never the problem.

### 7.5 What evsel exposes next
With the spin fixed and `evsel=on`, av-cam task `liro-kliro_66` faults about 15 s into boot
(0x63967364: `ldrb r3,[r3,#0x5EF]` with r3 = object 1 +0x0C = 0). Object 1 (static 0x643FF988, lookup
0x639663C0) is filled by 0x63DA55C2 from the 0xFE5F0B00 table only on the **mode-2 pass** of a
camera-mode initialiser (0x6396AB48: `cmp r4,#2`). A gdb breakpoint shows that initialiser has **not run
at all** when the first frame event arrives (watchpoint on 0x643FF994: never written). So frame events
reach the pipeline before the app has configured a camera mode. `evsel-sources` (bit mask of the
timing-source ids that fire, e.g. `0x20000000` = source 29, the engine frame) does not avoid it, and
slot A was still not sent before the crash.

### 7.6 Session 5: the two gates in front of slot A (confirmed against the live camera)
The HS handler 0x63969DB4 (argument `r0 = x<<8 | stage`, stage 0..3, see §10.4) posts link event 4
(poster 0x63D19ED8, at 0x63969F1E) only when **both** hold:

1. **Argument matches the expected step:** `r0>>8 == *(*(0x643FFA4C+4))` and
   `r0&0xFF == (*(0x643FFA4C+0x28))[1]`. Live camera table +0x3F64 (a copy of this key) reads
   0x00070301, then 0x00162803 (stage 1, then stage 3). §10.4 shows these arguments are posted by the
   comparator-1 callback 0x63968EFC itself, not by the frame sources.
2. **Tick flag** `*(0xFE5F0B00)+0x3F68 != 0`. Set at 0x63968F46 by callback 0x63968EFC once counter
   +0x3F61 reaches threshold +0x3F60 (= 4). Cleared at 0x639680D6 in init 0x63967E58, which also
   registers that callback (0x639684B4) on **evsel comparator 1** (0x63784D98 → 0x63D32164 →
   0x63784C5C writes 0xF3C30110 + 8k arm / +0x114 compare). The callback re-arms itself (0x63968FF4).
   Live camera: +0x3F60 = 04, +0x3F61 = 01 → 03, +0x3F68 = 00 → 01 between two captures.

With evsel off (the default) there are no comparators, so nothing runs. With
`-M sony-a7c,evsel=on,evsel-sources=0` (comparators on, all frame-event channels masked) the emulator's
+0x3F60..+0x3F68 reads 04 00 .. 01. **But the stage byte +0x3F61 stays 0:** sampled every 20 s for
300 s (guest suspends at ~66 s), object 1 +0x0C (PA 0x043FF994) stays 0 and the comparator registers
(0xF3C30110..) read 0, so comparator 1 stops being re-armed after a few firings. Its re-arm condition
(0x63968FD0..0x63969010, a counter against a limit) and the tick divisor `Y >> 2` (global at
0x645FA628, past the end of the av-cam image) are the next things to read, in the emulator and on the
camera. With frame events on (source 29), task 66 (handler 0x63967350, registered in the same init at
0x63968480) faults as in §7.5, because stage 2 has not run.

Live reads: see §10. The power-manager table cannot be pasted in as is (pointer words). Live clock bytes:
`02 1a 0b 0f 12 39 1f` (= 2026-11-15 18:57:31, the capture day) vs. the snapshot's `02 14 01 07 11 01 09`
(2020-01-07 17:01:09), which confirms the byte 7–12 layout as year−2000, month, day, hour, minute, second. The hand-off page at 0x20021000 is
nearly static. Loading the snapshot copy at 0x20021000 changed nothing.

`run.sh` takes `A7C_MACHINE_OPTS` (appended to `-M`), and evsel has `-global cxd90014-evsel.start-ms=N`
(no frame events before N ms of virtual time).

### 7.7 Session 5b: the real blocker is emulated time (gdb trace)
Breakpoints with `evsel=on` (gdb needs `maint set target-async off`, `maint set target-non-stop off` and
all hardware breakpoints set before the first `continue`):

* **0x63967E58 is the per-frame (V) routine**, called once per frame from the evsel dispatcher (lr
  0x63D7794D) with r0 = V timestamp. It is armed as a comparator-4 callback at 0x63967876 (GOT entry
  0x64656E8C). Every frame it restarts the HS sequence (clears G+0x3F61 / +0x3F68), posts the task-66
  job 0x63967351 and arms **comparator 1** at V + 5000 us (callback 0x63968EFC, 1/8-frame steps, limit 8
  ticks: Y = 8 at 0x645FA628). Without frame events (`evsel-sources=0`) it never runs: the values seen
  there before (Y = 8, count = 7, flag = 1) are just av-cam's initial data.
* The comparator-1 callback's checks pass (tick r6 == expected, count < 7) and it re-arms. **But every
  compare value the firmware writes is already in the past** (`d = cmp - now` from -900 to -57,000 us).
  In the model the frame routine itself takes 15–55 ms of emulated time, so only 1–2 of the 8 steps
  happen before the next frame restarts the sequence. Stage 2 (object 1 filled) is never reached, and
  the task-66 job reads the empty object 1 (§7.5). The firmware is fine; QEMU's default timing (virtual
  time = host time) makes the 4 Cortex-A5s far slower than real against their 1 MHz timers.
* **Fix: instruction-counted time**, `-icount shift=0` (`A7C_ICOUNT=0`). The boot to av-cam then takes
  about 1.5 s of emulated time, like a real camera. With `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on`
  the old §7.5 fault is gone and **the CPU sends slot A (type 0x21) for the first time** (trace
  `cxd_sio_data`). Shortly after, task 66 stops at a deliberate crash, 0x6386D64A
  (`movs r3,#0; str r3,[r3]`, after `0x63D77D0E(9)` when byte [r7+2] == 3, r7 = 0x644C4734). That
  is av-cam's own fatal-error path; what reports error 9 is the next thing to trace.
* Not explained yet: the kernel's printk timestamps stop at about 1.5 s under icount (the clocksource
  model may not follow icount), and QEMU had exited before the 400 s check ended.

### 7.8 Session 7b (v1.6): micro start-up script decoded (step counter, request code, ready byte); every 0x39
command acknowledged with the current code; script reaches step 3 of 7.
Session 7 (v1.4): error 9 traced to SIO ch0 = the power micro's 0x39 command channel; the echo from the
unattached channel caused a firmware-upload loop; ch0 model answers the query and acknowledges polls.
Session 6 (v1.2): live capture, ported units, error 9
**Live capture** `dump/live-mem-20261116-103503` (camera on; av-cam reads failed: `/tmp_bt` has only
~40 KB free, tool fixed to shrink its pieces):
* Interrupt rates (`text/interrupt_rates.txt`): evsel 84 = 850/s, 83 = 100/s; frame/display 98, 99,
  101, 102 = **50/s** (the model assumes a ~30 Hz frame); power-micro strobe 129 = 50/s (model: 20/s);
  SIO ch0 (201) 450/s, ch1 (202) 65/s, ch3 (204) 150/s; ADC 215/216 = 200/100 per s; GPU 76 = 85/s.
* evsel registers: enable +0x10 = 0xF0 (channels 4–7), sources ch4..7 = 23, 30, 29, 28;
  comparators 1, 8, 9 armed. GIC: 132 interrupts enabled (list in the manifest).
* SA cores: liro-51/54/55/61 have non-zero counts; liro-38 (P2S) is registered.

**Ported units** (`units=on`, default): ADC, four SA cores, P2S, from the a6400 machine in
bionz-x-research (originally a5000). The A7C av-cam.bin has the same SA ID strings (0x63FF7BB0),
SA base table (0x63AB7C2C), ADC bases and P2S unit table (0x63E9027C). With them av-cam reads the
SA core IDs; the default boot is unchanged (app initialises, suspends).

**Error 9.** Under `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on` the boot still sends one slot A (and a
new type-0x61 frame) and then stops at 0x6386D64A. That function (0x6386D110) is the power-micro
link's frame handler (r7 = link state 0x644C4734, GOT +0x51B8). After a status check it requires
link state byte [r7+2] to be 1 or 2; 3 reports event 9 (message 0x17 via 0x63D77D0E → 0x6386C1A4)
and stores to address 0 on purpose. The byte is copied from a per-mode table
(`*(0x6387329C()) + [r4+0x7B] + 0x217`, and `+0x237` at 0x638743E0), so the next step is which
mode index is selected and what the live camera's state byte is (re-run the capture).
The frozen kernel timestamps under icount (§7.7) were this stop, not a clock fault: the a6400
machine uses the same timer model with `-icount shift=0,sleep=on` as its default.

### 7.9 Error 9 traced to the SIO ch0 device (live capture `newdump/live-mem-20261117-120716`)
The live camera's av-cam data (4 MB, both passes) answers the error-9 question:
* Link state byte [0x644C4736] is **3 on the real camera too**: state 3 is normal.
* The stop needs flag **0x6453B935** = 1 (`0x63870590`). Live: 0. Its writer (0x63870FD0..0x63871030,
  in the slot handler 0x63870614) clears it when `0x63877094()` = byte **0x6453B9DF** is non-zero,
  and sets it ("waiting", up to 600 calls) while that byte is 0 and the entry table at 0x6453B96A
  (11 × {id, flag, value}, filled at 0x63871878) is incomplete. Live: table entries 5–7 = 01 01 01,
  0x6453B9DF = 6. Emulator at the stop: table all 0, 0x6453B9DF = 4 (0 when the stop happened).
* **0x6453B9DF is the sequence number of the SIO ch0 protocol**, not of the power micro: 0x638756F0
  builds its 8-byte header `39 0D 00 seq ?? 00 x^36 seq^2` (the "first byte 0x39" traffic on ch0 noted
  in §7.1). Nothing answers ch0 in the model, so that device's handshake stalls; the power-micro link
  then waits for it and, with state 3, stops with error 9. Live rate on ch0: 450 IRQ/s.
* Object 1 +0x0C on the live camera = 0xFE5F1614 (inside the power-manager table), as the HS stage-2
  fill would set it.

### 7.10 The SIO ch0 device is the power micro's command channel (v1.4)
* **Protocol.** Every ch0 request starts with 0x39; byte 1 is the command. Builders in the link driver
  (av-cam 0x638755A2 / 0x63875650): **0x0A** query (static template at 0x643AD6A8:
  `39 0A FF 03 00 20 E0 00`), **0x0E** start (`39 0E 00 00 00 00 36 01`), **0x0F** write a data chunk
  (`39 0F addr_hi addr_lo 00 3E …`, 248 bytes of data with XOR check bytes, 0x5400 upward), **0x0D**
  status poll (`39 0D 00 seq len 00 chk chk`, seq = 0x6453B9DF).
* **Why error 9 happened.** The SIO model's transfer buffer is shared TX/RX, so with nothing attached
  the CPU read its own request back as the reply. It then uploaded ~1 KB in 0x0F chunks (steps 0–21 of
  0x63875650) and polled with 0x0D; every poll failed the check in 0x63876B00
  (`((rx[3] ^ (seq << 4)) & 0x7F) == 0`), so after 29 retries the link set state 7 (event 0x3A), the
  power-micro "waiting" flag stayed set and av-cam stopped with error 9.
* **Model** (`a7c_ch0_xfer`, attached to ch0): replies `FF 7F 00 00 00 00 00 AA` to 0x0A (the live
  camera's RX buffer at 0x643AD6B0) and acknowledges 0x0D polls (`rx[3] = (seq << 4) & 0x7F`); other
  requests get zeros instead of an echo. gdb shows the poll is now accepted (`ACK-OK`).
* **Phase.** The link's phase byte 0x6453B9E2 drives a jump table (0x63876628, cases 0–0x41). The live
  camera sits in **phase 7** (idle) with state word 0x643AD988 = 4; the emulator reaches phase 2. Phase
  changes also read link bytes +0xD/+0xE/+0xF and the object at 0x6453B8CF (both identical to the
  live camera).
* **Still open.** After the first accepted poll the CPU sends a poll asking for 0x10 bytes
  (`39 0D 00 04 10 00 26 06`), then repeats start + polls, then 242 two-byte `F5 00` transfers, and
  still stops with error 9. Next: decode the reply expected for the 0x10-byte request (the data after
  the poll) and what `F5 00` is.

### 7.11 The micro start-up script (v1.6, gdb watchpoints)
* **Corrections to §7.9/§7.10.** The "ready" byte that `0x63877094` returns is **0x6453B9E1** (not
  0x6453B9DF). 0x6453B9DF is the **request code** set by `0x638757B4(code, arg)` (arg → 0x6453B9E3),
  which then sends the 0x0D poll `39 0D 00 code arg 00 arg^36 code^2`. The reply check in
  0x63876B00 compares rx[3] with `code << 4`, also after 0x0E. The model now keeps the last poll's
  code and answers every 0x39 command with it.
* **0x6453B9E2 is a step counter** through a fixed script (jump table 0x63876628): each acknowledged
  reply runs one step and `0x63876A4A` adds 1. Steps 0–6 run the micro start-up, 7 = done (the live
  camera sits at 7, with ready byte 0x6453B9E1 = 1 set at 0x638768DE and upload state word
  0x643AD988 = 4). Steps 0x20/0x22/0x30/0x32/0x40 are other entries of the same table.
* **The ch0 upload is the micro's own program** (0x0F chunks from 0x5400, then polls). av-cam treats
  ch3 status traffic during that upload as fatal: the link handler (0x6386D110) stops with error 9
  when "waiting" 0x6453B935 = 1, state word > 4 and link state [0x644C4736] == 3. gdb: that is true at
  the very first slot exchange (state 6, step 3).
* **Where it stands.** The emulator's script reaches step 3, then the CPU restarts the micro (state 6,
  set at 0x63876D3A, re-sends 0x0E) and loops start + polls; after 7 rounds it sends ~350 2-byte
  `F5 00` transfers (its give-up / reset sequence) and stops with error 9 at the next exchange. Holding
  the micro HLE silent until the upload (or until `F5`) and delaying evsel frames
  (`start-ms=3000`) did not change step 3. Next: what step 3 (handler 0x638767AA: object
  `0x6386DB50()+1`, link byte +0xF) needs from the micro's replies, likely real data in the poll
  reply rather than only the acknowledged code.

### 7.12 Session 8 (v1.7): power-micro start-up completes (step 7, no error 9)
* **What was wrong.** Each micro reply carries the micro's running 16-bit checksum of its program
  in rx[4..5]. The CPU's chunk builder (0x638755A2) adds every uploaded 0x0F data byte into
  0x644C4F7A as big-endian halfwords (even byte << 8, odd byte as is; data from tx[6], length
  len-8, two XOR check bytes at the end; already-sent addresses are not added twice). Reply handler
  0x63876BA4 keeps the micro's value in 0x644C4F82 (previous one in 0x644C4F78), counts replies in
  0x644C4F7C and, once the count passes [0x644C4F88]+3, compares 0x644C4F78 with 0x644C4F7A at
  0x63876CBE. The model returned zeros, so the compare failed, the CPU retried 5 times and then
  restarted the micro (state 6) - the loop that ended in `F5 00` and error 9.
* **Fix (sony_a7c.c, `a7c_ch0_xfer`).** The ch0 model now sums the 0x0F data the same way and returns
  the sum in rx[4..5] of every reply; a real micro can only report what it had before the current
  transfer, so the new chunk is added after the reply. 0x0E (start) clears the sum.
* **Gate re-added, but tied to the upload.** While 0x0F chunks arrive (and for 300 ms after the last
  one), the micro HLE holds its strobe p0.8 high, so the CPU skips its ch3 slot exchange (ch3 traffic
  during the upload is the error-9 condition of §7.11).
* **Result (gdb, `A7C_ICOUNT=0 A7C_MACHINE_OPTS=evsel=on`).** Every checksum compare matches (e.g.
  2CD1 = 2CD1 at step 3, EEFF = EEFF at step 6); the step counter 0x6453B9E2 runs 1 -> 7, the state
  word 0x643AD988 ends at 4 (live camera: 4). No crash at 0x6386D64A in a 25-minute run. av-cam goes
  on to decode its first UI images (`[LDEC OK #0..4]`, never seen before).
* **Micro state now matches the live camera.** Monitor reads after the start-up, against
  `newdump/live-mem-20261117-120716`: 0x6453B9D8..E2 = `00 00 00 00 00 00 01 06 00 00 07` and
  0x6453B930..37 = `00 15 E8 00 00 00 00 00` in both. (Correction: the ready byte 0x6453B9E1 is **0 on
  the live camera too**; the "1" in §7.11 was wrong. The waiting flag 0x6453B935 is 1 only briefly.)
  The ready byte is set by script entry 0x40 (0x638768DE), started from the link handler at
  0x6386D594 when byte +0x81 of object [GOT+0x5280] (0x643AD0E8) is 2, or at 0x6386CAAC on the mode
  change 0x501 -> 0x530; neither is needed for a normal start.
* **The micro's run-time channel is ch0 too.** After step 7 the CPU keeps exchanging 128-byte frames on
  ch0: `39 06 06 00 00 30 00 01 00 00 00 FF 00 00 01 00`, the same with 07 and 08, each followed by a
  2-byte `F5 00`, plus slot A (0x21, 0x61) and slot B (0x31) on ch3 (trace `cxd_sio_xfer`/`_data`).
  gdb on the SIO start 0x6386DE84 (lr 0x6386E685, ch 0): replies go to 0x6414DCA0 (06), 0x6414DBA0 (07)
  and 0x6414DD20 (08); [GOT+0x98FC] = 0x6414DB20 and [GOT+0x2E90] = 0x6414DCA0 are the parser's
  pointers (0x63870614: 0x6386F434 / 0x6386F448 / 0x6386F45C). The A7C path (0x63870C4A, the 0x97
  check at 0x643AD523 is for another model; live 0x00) copies paged data from these replies into the
  status block [GOT+0x33BC] / [GOT+0x9324] = 0x6453A952. The model still answers these with zeros,
  which is the likely reason the app logs `[M_POWER] changeBatteryConnectState() Unknown`,
  `ActionPowerStatusChanged() Unknown Type:0xFFFFFFFF`, `OFF_BY_KEY` and `TIME_IS_SETTLED is not
  SETtled`. The live-capture tool now also reads PA 0x0414D000 +0x1000 (these buffers) so the real
  replies can be replayed.
* **Speed: use `A7C_ICOUNT=2`.** With shift 0 emulated time runs about 44x slower than real (2.7 s per
  120 s), with shift 2 about 4x slower at first and faster once idle (25 s after 240 s). The start-up
  still completes with shift 2 (step 7, same bytes as above); the app runs to its power checks
  (ulog at 17-22 s, above) and stays up without suspending (checked to 155 s uptime). Screen black.
  (Later runs with longer checks show it does suspend, at about 20-28 s: §7.13.)

### 7.13 Session 14 (v1.14): pciehp hot-plug of the card reader, card detect (0xF2915010 bit 16 + live micro frames): the camera reads the card and offers to format it.
Session 13 (v1.13): mode-dial positions decoded, C1/AEL from a GPIO scan, GPIO poke, SD card (experimental).
Session 12 (v1.12): shutter (S1/S2), control wheel, mode and exposure dials; steadier VPH screen.
Session 11 (v1.11): rear controls (ADC, CAkey1, GPIO) -> the camera menu; VPH line-output OSD shown.
Session 10 (v1.10): slot C + PCIe card reader -> no power-off, first firmware screen ("NO CARD").
Session 9 (v1.9): live ch0 replies, the reply check, the OFF_BY_KEY power-off, slot C
Live capture `newdump/live-mem-20261117-190338` (camera on, shooting screen, lens on, battery 80%, USB
connected) with the new `ch0` region (PA 0x0414D000):
* **Run-time replies.** Commands 05/06/07/08 have their replies at 0x6414DB20 / DCA0 / DBA0 / DD20
  (the TX buffer is 0x6414DC20, the 0x0D poll at 0x6414DDA0). Each reply: bytes 0-1 zero (the CPU clears
  them), byte 2 = the command, then data, values that tick between the two passes (sensor-like).
* **Reply check found.** 0x6387176E (per reply; r0 0..3 = command 5..8 via getters 0x6386F434 /
  F448 / F45C / F470) calls **0x63D77EC0(buf, 0x40)**: the XOR of the 64 little-endian halfwords must be
  **0x0F0F** (all 8 captured replies pass), and byte 2 must echo the command. It records
  {seen, echo ok, check ok} in the table at 0x6453B96A (entry = command) and counts bad replies in
  `0x6387329C()+0x2BA..0x2BD`. The model now always answers with a valid reply (an empty one, or the
  captured one with `A7C_CH0_FILE`); gdb: every 06/07/08 reply is accepted (echo 1, check 1).
* **The app still powers off.** With icount shift 2 the boot reaches `Os 008_ObjCamera`, then upm
  (pid 92) logs `EV OFF add_off_factor: 0x7 :0x920168` -> `go inact_app(active) by off: 0x7` ->
  `I_INV_CMP go inact_app2` and the app gets `EV onSuspending` (20-28 s). The app logged
  `[M_ROOT] setCautionOffFactor() OffFactor : OFF_BY_KEY` first. bionz-x-research's a6400 notes call
  this "the power lever reads OFF (factor 7)", fixed there by the sub-micro replay plus evsel.
  Not changed by: the captured ch0 replies, the live slot A/B frames, slot A byte 28 left as captured
  (the live A7C frame has byte 28 = 0x00 while switched on, so the a5000 lever bit there is not the
  A7C's lever), or the live GPIO input levels (`A7C_GPIO_LIVE=1`, from
  live-mem-20261117-115416/hw).
* **Slot C.** The A7C also exchanges a third ch3 frame like the a6400: CPU 0x61 / micro 0x60, RX at
  0x63443424 between slot A and slot B (live: `60 00 00 00 07 11`, CPU: `61 00 00 00 07 11 00 00 01`).
  The model answered 0x61 with slot A. With any valid 0x60 reply (`A7C_SLOTC=1`) the kernel then
  tries to power PCIe slot 2 (the memory-card reader): `pcie_slot_ctrl: can not open
  /sys/bus/pci/slots/2/power`, `BUG:PCI1:pcie_root_ctrl: RC not found`, kernel BUG at pcie.c:841, and
  later im.elf stops in the exception monitor. So slot C is the micro's media/slot-power exchange, and
  answering it needs a PCIe root port (with a hotplug slot) on RC1. Opt-in until then.
  The capture tool now also saves slot C (A, B, C in `pa_034433a4_submicro_frames.bin`).

### 7.14 Session 10 (v1.10): memory-card reader on PCIe1 -> the app runs and draws ("NO CARD")
Resources: bionz-x-research qemu-a6400 (E0526/E0527: on the a6400 the factor-7 power-off goes away
once all three micro slots, A/C/B, are replayed; E0557 evsel `late-src`/`late-us`; E0572/E0573 rear
LCD and rear controls).
* **Slot C was the missing piece for OFF_BY_KEY**, as on the a6400. On the A7C, answering it makes the
  app power the memory-card reader, which sits behind PCIe1 (camera proc_iomem: bus 0000:81,
  `mmc-pci` BARs 0xE8000000 4 KiB and 0xE8001000 2 KiB).
* **Kernel config access (stock vmlinux):** root port config = DBI (0xF0180000) directly
  (`f_pcie2_dme_read_config` 0x601215D8, `pcie_ctl_root_config` 0x60122894); other buses: target
  `type<<16 | bus<<8 | slot<<3 | func` to DBI+0x1110 (`pcie_ctl_cfg_io_set` 0x601228B8), then the
  config window at PA 0xBFE00000 (ch0 0xBFA00000; ioremapped 0xB080A000 / 0xB0808000);
  `pcie_ctl_cfg_read` 0x601228F0 writes ~0 to DBI+0x1000 and treats a non-zero read-back as "no
  device". INTA of ch1 = irq 240 (`f_pcie2_dme_map_irq`). `pcie_root_ctrl` 0x60121BD8 BUG()s ("RC
  not found") when bus 0x80 has no device 80:00.0.
* **Model (`A7C_CARD_SLOT=1`):** DBI of ch1 holds a root-port header (bridge class, PCIe cap, DLL
  active; ids are placeholders), DBI+0x1000 is write-1-to-clear, and the window answers type-0
  slot 0 with an O2 Micro 1217:8520 SD host (mmc_drv.ko ids 8520/8750/8751; 8520 needs config byte
  0xDF = 0x11/0x12, else "maybe FUJIN2, unsupported"; `A7C_CARD_ID=8750` picks another). BAR0 =
  QEMU `generic-sdhci` with no card, BAR1 = storage. The kernel assigns the BARs exactly as on the
  camera (0xE8000000 / 0xE8001000) and `mmc-pci` probes OK.
* **Result (`A7C_FULL=1` = icount 2 + evsel + slot C + card reader):** no `add_off_factor`, no
  `onSuspending`, `EV onStarting` at ~20 s, hundreds of GPU draws, and the first firmware-drawn
  screen: **"NO CARD"** plus the four focus-area marks (screenshot `a7c_first_ui_nocard.png`). Same
  with or without the captured ch0 replies. Still logged, harmless: `pcie_slot_ctrl: can not open
  /sys/bus/pci/slots/2/power` (the root port has no hot-plug slot).
* evsel `late-src`/`late-us` ported (`A7C_EVSEL_LATE_US=N`, opt-in, not needed for the above).

### 7.15 Session 11 (v1.11): rear controls -> the camera's own menus
Method of bionz-x-research qemu-a6400 E0573, with the A7C's key table (guest `/proc/udm/input`,
saved as `docs/a7c_proc_udm_input.txt`; names from libObj.so's key catalog):
| Key | Signal | Host key |
|---|---|---|
| ENTER / UP / LEFT / DOWN / RIGHT | ADC unit 1 ch3 (port 0x0F) 45 / 182 / 356 / 520 / 686 | Enter, arrows |
| Fn / Delete | ADC unit 1 ch0 (port 0x0C) 45 / 520 | F, Delete |
| MENU / PLAY | slot A CAkey1 bytes 28/29, bit 2 / bit 1 high | M, P |
| MOVIE (KEY_TACT_STARTSTOP) | GPIO P0.10 low | R |
Other entries: mode dial = ADC ports 0x10/0x11 (5 bands each, `KEY_POS_MODE_DIAL`), exposure
compensation dial = ports 0x12/0x13, vari-angle panel switches P0.7, P2.18/19/23, mic/headphone jack
P5.4 / P9.16. Not wired yet: the dials and the control wheel (a6400: TPU counter 0xF2404014).
* **CAkey1 is at bytes 28/29 on the A7C too** (tested: MENU via bytes 30/31 did nothing; bytes 28/29
  open the menu). The live slot-A byte 28 = 0x00 with the camera on is therefore not the lever bit
  as the a5000 reads it; the model keeps forcing bit 0.
* **Screen: the A7C shows its UI on the VPH line output**, not the panel layers (+0x144 = 0): AIF
  ch54 OSD = the UI buffer (0x22AB2000 or 0x22B48000, 0x500 bytes/line, 240 lines, pitch 0xA00),
  line size +0x454 = 704 x 240 (the viewfinder path, like the a6400's default "device 2", E0572).
  `cxd90014-vph.panel-fallback` (default on) shows that OSD on the panel console when the panel is off,
  and `A7C_SCREEN=vph` (in `A7C_FULL`) makes the VPH console the window's first tab. The PPF viewer
  alternated between the drawn and an empty buffer.
* **Result (`A7C_FULL=1`, keys from QEMU):** MENU opens the camera's menu on the last page used
  (Setup3, "USB Connection: Mass Storage", your camera's settings), UP/DOWN move the selection,
  Enter opens "HDMI Settings", MENU closes, PLAY shows "No memory card. Cannot play.".
  Screenshots `a7c_menu_first.png`.
* `A7C_GPIO_LIVE=1` (in `A7C_FULL`) keeps MOVIE (P0.10) and the other active-low inputs released.

### 7.16 Session 12 (v1.12): shutter, control wheel, dials; steadier screen
* **Shutter:** S1 (half press) = GPIO P0.1, S2 (full press) = P1.5, active low, the a5000's wiring
  (a5000-emulator RE_ENGINE_TICK.md §12.1); both lines are high on the switched-on A7C (live GPIO).
  Checked: with the menu open, H (half press) closes it, as on the camera. The A7C av-cam's own reader
  (sysparams 0xF90139/0xF9010A on the a5000) is not traced [?]. Space = full press (S1 + S2) is wired
  but not tried (no card).
* **Control wheel:** av-cam 0x63AA9508 reads the 16-bit counter at 0xF2404014 + (ch << 8), as the
  a6400. Model: 0xF2404000..0xF24041FF with the two counters and read-back storage for the rest of the
  TPU block. Checked: in the menu, `]` twice moves the selection from the last item to the tab row.
* **Mode dial:** ADC unit 1 ch4/ch5 (ports 0x10/0x11), five bands each; keys 1-5 = port 0x10 bands
  (codes 3, 1, 0x14, 0x13, 7), 6-0 = port 0x11 bands (0x15, 0xA, 6, 4, 5). Which code is which dial
  position (AUTO, P, A, S, M, MR, movie, S&Q) is not decoded [?]. Dial changes make the app re-layout
  (ulog: `UserLayoutChanged_ST_NORMAL`, `ST_STBY`, audio-level widgets). `A7C_MODE_DIAL=N` sets the
  position at reset (default: none, as before).
* **Exposure compensation dial:** ports 0x12 (+1..+9) / 0x13 (-1..-9); `=` / `-` step it. Not checked
  visually.
* **VPH screen:** the A7C's +0x404 bit 0 is set while the OSD buffer is RGBA4444 (0x500 bytes per 640
  px); the panel OSD is now read as 16 bpp when a line is 2 bytes per pixel. The AIF channel is
  re-programmed with the next buffer before the UI draws it, so every other frame read an empty buffer;
  the console keeps the last drawn frame over empty runs shorter than 60 refreshes.

### 7.17 Session 13 (v1.13): mode-dial positions, C1/AEL, GPIO poke; memory card (not yet)
* **Mode dial decoded** from screenshots of each band (`Up` first = DISP, so the shooting screen shows
  the mode): port 0x10 bands 0-4 = **P, AUTO, MR3, MR2, MR1**; port 0x11 bands 0-4 = **S&Q, movie,
  M, S, A** (codes 3, 1, 0x14, 0x13, 7 / 0x15, 0xA, 6, 4, 5). Host keys follow the dial: 1 AUTO,
  2 P, 3 A, 4 S, 5 M, 6 MR1, 7 MR2, 8 MR3, 9 movie, 0 S&Q. `A7C_MODE_DIAL=P|AUTO|A|S|M|MR1|MR2|MR3|
  MOVIE|SQ|none` sets it at reset (default P). Each MR position opens the camera's "MR Recall" screen.
* **GPIO poke** (debug): `qom-set /machine/gpio poke P.N=L` drives an input line. Scan of the lines
  that are high on the switched-on camera, one at a time pulled low:
  - P0.2: the screen switches to the exposure-scale view while held: taken as **AEL** [?];
  - P0.3: "This operation or setting not available: Focus Mode Continuous AF": taken as **C1** [?];
  - P0.6, P0.12-P0.15, P1.1, P1.3: no visible change in this state.
  Host keys A = AEL, C = C1.
* **Memory card (experimental, `A7C_SD_IMAGE=file`):** a second `-drive if=sd,index=1` becomes a QEMU
  `sd-card` on the reader's SDHCI. The root port now advertises a hot-plug slot (physical slot 2,
  power controller, NCCS, presence detected) for `pcie_slot_ctrl` (0x60121A50: writes '0'/'1' to
  `/sys/bus/pci/slots/2/power`). Not working yet: no `pcieport`/`pciehp` binding is logged, the slot
  file does not appear, and at ~3.4 s the camera powers the reader off (`mmc_pci_remove_cb`) and does
  not power it on again, so the card is never read ("NO CARD" stays). Next: why pcieport does not bind
  (`pcie_portdrv_probe` 0x603C81C8 -> `pcie_port_device_register` 0x60282E68: service IRQs / caps), and
  what makes the camera power the slot on (card-detect line).

### 7.18 Session 14 (v1.14): the camera finds and reads a memory card
Chain worked out in order (`A7C_SD_IMAGE=file`, plus `A7C_FULL`):
1. **pciehp binds** only once the root port's status register keeps "capabilities list": the kernel
   wrote 0x0007 to the 32-bit command/status word, the model stored 0 into status, and
   `set_pcie_port_type` found no PCIe capability (`pcie_cap` = 0 at `pcie_portdrv_probe` 0x603C81C8,
   DBI trace `A7C_PCIE_TRACE=1`). Status is now read-only (0x0010).
2. **The reader is found by hot-plug, not at boot:** Sony's hook 0x60121F70 in `pci_scan_bridge`
   (0x603C67B0) returns without `pci_scan_child_bus` for a root port. pciehp (`pciehp_force=Y`)
   enumerates the slot only if it starts powered off; with power on it logs "Already enabled on
   slot(2)" and never scans. Slot control now starts with power off (bit 10): pciehp powers it on at
   1.7 s and 81:00.0 appears, as on the camera. `pcie_slot_ctrl` (0x60121A84, called from mmc_drv
   through an ioctl) then switches `/sys/bus/pci/slots/2/power` like the real camera.
3. **Card detect:** mcmn_drv.ko `mcmnd_core_checkinsertedmedia` (.text+0xBF0) -> .text+0x828 reads
   0xF2915010 (a GPIO bank past the 18 ports, "MMC0 CD/WP GPIO_IN") and takes bit 16 = 1 as "SD
   card in"; bits 24/26 are the MS slot. Modelled (bit 16 set while a card image is attached).
4. **But that read only happens after the power micro reports a card:** with the power-off snapshot
   frames (`fw/submicro_frames.bin`) nothing reads 0xF2915010 and "NO CARD" stays; with the
   switched-on camera's frames (`fw/submicro_frames_live.bin`, live capture) the camera reads the CD
   bit, initialises the card (CMD0/8/2/3/9/7/16, CMD6 bus speed, block reads) and shows
   **"Unable to use memory card. Format?"** for a blank image. `A7C_FULL` now uses the live frames
   when that file exists. Which slot-A/B field carries the card state is not isolated yet
   (`mcmnd_rm_callbackpoint_frontmedianotify` .text+0x3690 takes a 7-byte notify).
5. QEMU's SDHCI raises "card inserted" only when the card is plugged in (machine creation); a timer
   now signals the insertion once the driver enables that interrupt.
* **Not working yet:** formatting stops at "Formatting memory card..." (no write command reaches the
  card), and with a card formatted on the PC as FAT32 (MBR, partition at 8192) the camera reads it
  (34 multi-block reads), drops "NO CARD", then the app stops reacting to keys. Next: what the
  driver waits for before the first write (SD status / ACMD, DMA, the write-protect bit next to CD).
* **Format hang, narrowed (v1.15):** after "Enter" on "All data will be deleted. Format?" not a single
  command reaches the SD card (`trace-event sdcard_* on` during the 90 s after Enter), the kernel side is
  idle (`bdev_mmca` in `udif_bdev_thread`, `irq/240-mmc-pci` idle, no task in the mmc driver) and the
  app's log is quiet: the wait is inside av-cam (µITRON) before it asks Linux for any I/O. Likely
  candidates: a media-power or card-state handshake with the power micro, whose frames the model
  only replays statically (the CPU's requests in slot A/B/C are never acknowledged), or a reply from
  mcmn_drv over UIPC (`mcmnd_uipc_setmediapower` .text+0x3D54 was not hit). Next: compare the CPU's
  TX slot frames before/after Enter, and watch `mcmnd_*` UIPC entry points with gdb.
* Slot C default is the live frame `60 00 00 00 07 11` (an empty 0x60 frame also works for the
  power-off).

### 7.19 Session 15 (v1.16): updated qemu-a6400, USB controller

Checked the updated bionz-x-research qemu-a6400 (files dated 2026-11-18, evidence E0574-E0590).

* Most peripheral files (vph, sio, gpio, adc, regfile, timer, sa, smaph) only changed for the
  QEMU 11 API (`hw/core/*.h`, `qemu_graphic_console_create`, `bool` gfx update). We build on
  QEMU 10.1, so there was nothing to take over. The evsel late-src change is already in (v1.13).
  p2s gained an opt-in transfer trace (`trace-transfers`).
* New machine experiments in sony_a6400.c are about the image sensor path (CamPig IRQ enable
  readback at 0xF3000050, E0566; Sgc1 input-sync event A, IRQ 35, E0568/E0569) and USB
  (`usb-vbus-test`, `usb-cid-high` at 0xF2920010, E0580-E0589). None of it concerns the memory card.
* **Taken over: `cxd90045-dwc3`** (hw/usb/cxd90045_dwc3.c, included from sony_a7c.c so an existing
  build tree needs no meson change). The A7C kernel has the same DWC3 at 0xF020C100 and the same
  fusb IRQs (230 VBUS, 231 ID in the console). Before, the catch-all register file read back the
  DCTL soft-reset bit, so the kernel logged "dwc3: Reset Timed Out", and gadgetcore failed with
  -19 (same cause as a6400 E0528). With `A7C_DWC3=1` both messages are gone and the app reaches
  the same screen as before (format prompt), so no regression.
* Not taken over (yet): the VBUS/CID status word (a6400 0xF2920010). On the a6400 even with VBUS
  reported the camera did not start its USB mode (DefRootView gate, E0589), so a PC connection is
  not close. The web rear panel (tools/rear_panel.py) is a6400/moddev specific; an A7C version
  could be made later.
* E0574 (blank panel) matches what we saw: a black frame early in boot is output not yet set up,
  not a broken display model.

### 7.20 Session 16 (v1.17): web panel
`tools/a7c_panel.py` + `tools/a7c_panel.html` (stdlib only), started by `start_a7c_panel.bat`.
It runs `run.sh` with `A7C_FULL=1 A7C_DISPLAY=none`, the serial console in `out/panel-console.log`
and an extra QMP socket (`out/panel-qmp.sock`), and serves 127.0.0.1:8642:
* the screen = QMP `screendump` of console 0 (the VPH console under `A7C_SCREEN=vph`), PPM converted
  to PNG, cached 150 ms, header `X-A7C-Lcd: blank|visible` (as the a6400 panel, E0574);
* keys = QMP `input-send-event` press/release into the rear-control model (§7.15-7.17). Held keys
  (ADC keys, MENU/PLAY, GPIO keys) are released no sooner than `--min-hold` ms (1500) after the press:
  HMP `sendkey` with its 100 ms default is missed, because the camera samples them slowly under
  icount (3000 ms always worked in earlier tests). Dial / wheel / EV keys act on the press.
* POSTs need `X-A7C-Panel: 1`, so another web page cannot send keys.
Verified with a headless browser clicking the page: OK on the format question, MENU, down, MENU.

### 7.21 Session 17 (v1.19): format hang narrowed; reliable keys
* **Keys.** Presses are now held for `A7C_KEY_MS` (150) ms of virtual time in `a7c_rear_key`
  (QEMU_CLOCK_VIRTUAL timer for delayed releases). Host-timed holds were missed or auto-repeated.
* **Tools used:** serial console on a unix socket (`--env A7C_SERIAL=unix:...`) with a small tap that
  logs and forwards commands; busybox applets `dd`, `xxd`, `cut` exist (call as `busybox x`);
  user-space gdb breakpoints work, but **ASLR is on** (`randomize_va_space=1`): boot with
  `A7C_APPEND=norandmaps` for fixed library addresses (libObj 0x05B42000, libInfraFsys 0x06FD2000,
  libInfraMediaCommon 0x087E1000, libmpr 0x070EC000). `-dfilter <ranges>` + HMP `log exec,nochain`
  gives a complete block trace of chosen libraries around one key press.
* **Format path (static):** the format is done on the Linux side, not in av-cam:
  view -> libObj model `M_FORMAT` (vtable 0x13A97E8; GetFormatTime -> SendFormatCmd -> progress ->
  FormatExecComp) -> `ObjCntMgr` command table (0x13BA07C, 86 entries; `ObjCommand_FsysFormatCmd`
  0x8CB214) -> `Fsys_FormatAsync` (libInfraFsys) -> `mcmn_drv_format` (libInfraMediaCommon) -> ioctl
  -> mcmn_drv/mmc_drv (`mmc_mmcp_format`).
* **What happens (dynamic, gdb + block trace):** after "Enter" on "All data will be deleted. Format?"
  the screen shows "Formatting memory card..." but **none** of `ObjCommand_FsysFormatCmd`,
  `Fsys_FormatAsync`, `Fsys_Format`, `mcmn_drv_format`, `MprIf::GetFormatTime`, any ObjCntMgr
  command-table entry or any M_FORMAT vtable method runs, and no SD command reaches the card.
  M_FORMAT only runs a guard (0x585A5E for action 7: param 0x101 = backup 0x01071C48 must be 2; it
  is 1, which is the quick/full-format setting, so not the cause) and its parameter getters.
  What does run: inhibit factors (`[M_Inh]`), the camera model goes ACTIVE -> PrepOFF -> EtryDact ->
  NeutrOff -> Detached (EE stop, `SeqStillCapStop`, `EV CamReady`), models are finalised and
  re-initialised (`M_MEDIA_PMBP`, `M_NET_INFO`, `M_MEDIA_IPTC`, `M_RECPBNEUTRAL`, `ConectMedia`,
  `GuardChgMedia`, `ActionSetCurrentMedia`), then only UI drawing. All Linux threads of im.elf
  (IFsys, IMM, ObjMedia, MMgrCtrl, InfraMediaPwrMg, ObjCntMgr) sit idle in their message waits.
  So the app performs the transition into the "format" state but the step that should hand the
  format to M_FORMAT never comes. Next: find the root/view state machine step after the camera
  model is detached (FMWK FSM `currentState`, the format view in viewUnified6.so, which is loaded at
  a run-dependent address) and what event it waits for (likely a completion from the camera/media
  side that the emulator does not produce).

### 7.22 Session 18 (v1.20): a pre-formatted card with a picture
* `tools/make_sdcard.py` (pure Python) writes a FAT32 SDHC image like the SD formatter (MBR,
  partition at 8192, 32 KiB clusters, data area on a 4 MiB boundary, FSInfo, backup boot sector),
  `DCIM/100MSDCF/DSC00001.JPG` and `PRIVATE/SONY/SONYCARD.IND` (empty marker that
  libInfraFsys `Fsys_Format` .text+0x1E61A creates after formatting). `fsck.fat` clean.
* **Boot with it:** the kernel sees `mmca10` + `mmca10p1`; the app (user-space FAT, libInfraFsys
  pf2) reads MBR, boot sector, FAT1 (4 parts), root, DCIM, 100MSDCF and, with the marker,
  PRIVATE and SONY (trace `sdcard_normal_command`, SD only). No write command. Then the screen
  stays black: the GPU interrupt count stays at ~30 (hundreds on the empty-card boot).
* **Where it stops:** im.elf's ulog matches the empty-card boot up to
  `[stlRecView] UserLayoutChanged_ST_NORMAL` (~22 s). With the empty card, `DefRootView` then runs
  its guards (`GUARD_BT_REMOTE_CONNECTION`, ...) and the UI draws; with the photo card, DefRootView
  never logs again. All im.elf threads (IFsys, IMM, IMD, InfraDatabase, MprIf, HddDcfMMgr,
  Appframework in poll) sit idle in message waits, so the root view waits for an event about the
  mounted media that never arrives.
  (`EV onStopping` at ~22.6 s in /proc/snsc_boot_time is `ObjMic`, not the app: gdb on
  `UActiveObject::onStopping`, object name read from the instance.)
* Not the cause: the USB controller (same with `A7C_DWC3=0`), the SD write-protect line
  (bit 25 of 0xF2915010, `mcmnd_core_getmmcwpdata`; `A7C_CARD_WP=1` changes nothing), the
  SONYCARD.IND marker.
* Common to this and the format hang (§7.21): both stop where the camera would next update the
  card/media state, and neither issues a single write. Next: trace DefRootView / M_MEDIA after the
  mount (block trace with `-dfilter` over libObj during 18..30 s guest time) to find the awaited
  event; compare with the empty-card boot.

## 8. Next steps (in order)
1. **Make `A7C_FULL` the default** once the GUI settles (§7.14), and keep the plain boot as an option.
2. **Memory card writes** (§7.18): formatting and FAT32 card use hang before the first write.
   A small on-screen panel like the a6400's.
3. **Rear LCD vs viewfinder** (E0572: the a6400 boots into the EVF layout; the A7C has an eye sensor).
4. **Live image** (YUV layer) is a separate, longer track (a6400 E0557-E0569).
2. Match the live rates: 50 Hz frame interrupts, power-micro strobe every 20 ms, evsel sources
   23/30/29/28 (§7.8).
5. An SD card image on the card reader (QEMU `sd-card` on the reader's SDHCI). The SA cores are now modelled as "null SA"
   (no image processing); check whether `[IDT]: IDT : Init NG` changes.
4. Then the screen: once the app passes the power check it should draw its first view.
5. GPIO key wiring of the A7C (shutter, buttons; from input.ko's key table in Backup), host keys.

## 9. Session log
Session 1: 1) A6400 machine + A7C kernel → kernel, SMP, eMMC probe OK. 2) eMMC padded to 4 GiB (sector
addressing) → rootfs + init + modules. 3) RAM bank 1 = 256 MiB. 4) Backup.bin preload. 5) PCIe RC link-up.
6) av-cam preload → LIRO starts av-cam.
Session 4: power micro link (wiring from av-cam globals, frames from the WBI snapshot, GPIO + SIO +
micro HLE); evsel stall root-caused (self-clearing 0xF3C081E0) and fixed; evsel's next fault analysed.
Session 5: slot A's two gates found (frame/phase match + comparator-driven tick flag), the flag gate
confirmed with live camera reads and opened with `evsel=on,evsel-sources=0`. `A7C_MACHINE_OPTS`, evsel `start-ms`.
Session 9 (v1.9): ch0 reply check (XOR 0x0F0F), live replies accepted, slot C (opt-in), OFF_BY_KEY power-off traced to upm factor 7.
Session 8b (v1.8): run-time ch0 commands 06/07/08 found, A7C_CH0_FILE, live capture of the ch0 buffers, A7C_ICOUNT=2.
Session 8 (v1.7): ch0 replies carry the micro's program checksum; start-up script completes (step 7), no error 9.
Session 7b (v1.6): micro start-up script decoded (step counter, request code, ready byte); every 0x39
command acknowledged with the current code; script reaches step 3 of 7.
Session 7 (v1.4): error 9 traced to SIO ch0 = the power micro's 0x39 command channel; the echo from the
unattached channel caused a firmware-upload loop; ch0 model answers the query and acknowledges polls.
Session 6 (v1.2): live capture analysed (rates, evsel, GIC), ADC/SA/P2S ported from bionz-x-research,
error 9 traced to the power-micro link state byte, WSL rebuild script, capture tool fixed for /tmp_bt.
Session 5b: gdb trace shows the HS timer deadlines are missed because emulated time runs at host speed;
`-icount shift=0` (`A7C_ICOUNT=0`) fixes it and the camera sends slot A for the first time (§7.7).
Session 5 (second window, merged): live captures of 0xFE5F0B00, 0x20021000 and the power-micro frames,
the HS step sequence behind object 1, `handoff=` / `A7C_HANDOFF_FILE`, `A7C_SUBMICRO_FILE` (§10).
Session 3: workarounds 2), 4) and 6) replaced by real fixes in the model: eMMC `sector-addressing` (any
image size), and the bootloader stand-in loading Backup.bin / av-cam.bin from the eMMC at every reset.
Session 2: rows 1–7 of §5 → camera app fully initialised, console shell, GPU + screen window.

## 10. Session 5: live memory from the running camera
Three regions the dumps cannot hold were read from the switched-on camera with
the live-capture tool, now in `Sony A7C Emulator\a7c-live-capture` (`a7c_live_mem_gui.bat`, tool
`a7c_live_mem.py`, probe `probe\a7c_live_mem_probe.c`; it uses the camera debugger client and Python
environment of sony-a6400-research-main). The probe is pushed to
`/tmp_bt` (RAM), opens `/dev/mem` O_RDONLY, mmaps PROT_READ, copies the pages and is removed
(`/tmp_bt` checked unchanged). Two captures, 3 min apart (uptime 9024 s / 9200 s):
`dump/live-mem-20261115-211521`, `dump/live-mem-20261115-211832`. The same regions from the WBI
snapshot: `dump/snapshot-mem-20261115-200420` (0xFE5F… is not in the snapshot). (P = physical capture)

### 10.1 0xFE5F0B00 table (on-chip memory, §5 row 1)
* Real contents run from 0xFE5F0B00 to about +0x54E8 (965 non-zero words); 227 bytes change between
  the two captures (P).
* av-cam keeps the table's address in the global **0x643FF97C** (loaded at 0x6396AB4C before calling
  0x63DA55C2); 0xFE5F0B00 is not a constant anywhere in av-cam.bin (no literal, no movw/movt) (D).
* **Writers of +0x4944..+0x4964**: 0x63965E8E..0x63965F0E store the return values of getters into
  +0x4944, +0x4948, +0x494C, **+0x4950**, +0x4954, +0x4958, +0x495C, +0x4960, +0x4964; 0x63969800
  writes +0x4950 again (only when `table+0x11B4` bit 3 is clear; it is 0 on the camera, P) and then
  calls the reader 0x63DA556C (D).
* The getters are `array + stride * index_byte` (one-byte index, i.e. double-buffered objects) (D), and
  the live values match them exactly (P):

| Field | Getter | Array | Stride | Index byte | Live 1 / live 2 (index) |
|---|---|---|---|---|---|
| +0x4944 | 0x63B0A828 | 0x64456A74 | 0x2C | 0x6454DA71 | 0x64456A74 / 0x64456A74 (0/0) |
| +0x4948 | 0x63BDEC08 | 0x64494038 | 0xC34 | 0x64551740 | 0x64494038 / 0x64494C6C (0/1) |
| +0x494C | 0x637D57F8 | 0x6429F11C | 0x1F0 | 0x6453785E | 0x6429F30C / 0x6429F30C (1/1) |
| **+0x4950** | **0x63B13A98** | **0x64456B60** | **0x1280** | **0x6454DA7B** | 0x64457DE0 / 0x64456B60 (1/0) |
| +0x4954 | 0x63C52250 | 0x64501E38 | 0x4 | 0x64585911 | 0x64501E3C / 0x64501E38 (1/0) |
| +0x4958 | 0x639EAB40 | 0x64408650 | 0x1318 | 0x645489CD | 0x64409968 / 0x64408650 (1/0) |
| +0x4960 | 0x63C3C638 | 0x644B72E4 | 0x20 | 0x64585342 | 0x644B7304 / 0x644B72E4 (1/0) |
| +0x4964 | 0x63963F18 | — (returns 0x643FF194) | — | — | 0x643FF194 / 0x643FF194 |

  +0x495C comes from 0x63C20764 (loads a global, tail-calls 0x63E373CE; not decoded): 0x644B48C4 /
  0x644B4784 (P).
* So **+0x4950 can never be written as 0** by av-cam: every value is a static av-cam address. The 0
  seen in §5 row 1 came from the store being dropped (no memory behind 0xFE5F0B00), which `fe_xram`
  fixes; with it the model gets the same values as the camera (index aside). Nothing to seed.
* The table feeds 0x63DA55C2, which fills the pipeline objects: **0x643FF988** ("object 1" of §7.5,
  from the camera-mode initialiser 0x6396AB62, mode-2 case) and **0x643FFC40** (0x6396DC14, table
  global 0x643FFE44). The §7.5 crash is therefore about *when* 0x6396AB62 runs, not about the table.
  The mode value `r4` there comes through a jump table (0x6396AB10 is one case) [not traced].

### 10.2 Hand-off page 0x20021000 (`wbimem=0x1000@0x20021000`; optional in the model)
Written by the bootloader; mostly 0xAA fill. Optional in the model:
`A7C_HANDOFF_FILE=dump/live-mem-<time>/pa_20021000_handoff.bin ./run.sh` (machine property
`handoff=`, writes the file to PA 0x20021000 at every reset; default off). Checked: the page reads back
as captured and survives `system_reset`. A kernel+initrd boot without eMMC gives the same log with and
without it (only timing noise). The kernel writes +0x54 itself on every boot
(`resume vector: 0x20021054`, `resume entry: 0x00124d8c`), so that field is not taken from the page. Contents (P, both captures; S = snapshot):
| Offset | Live | Snapshot | Meaning |
|---|---|---|---|
| +0x054 | 0x00124D7C | 0x00124D8C | `pm.resume=0x20021054`; live value = the WBI's resume vector (`tools/wbi.py list`) |
| +0x060 | 0 | 0 | `pm.sus_error` |
| +0x0E8 | 0 | 1 | `wbi_memclr` |
| +0x0EC | 0 | 0 | `em_console` |
| +0x08C | 02 1A 0B 0F 12 3A 30 | 02 14 01 07 11 01 09 | same clock bytes as power-micro slot A |
| +0x1FC.. | text records (`25 0B 00 00` + string): "MIS for DGU4 #Prameter version:400001", "Astra LPDDR3 Initialize Ver.40.00.01", "*** DRAM Ch.A Setting" | same | bootloader DDR-init log |
| +0x25C..+0x2D0 | records {0x20040000, reg, 1, value}: 0xF290A068, 0xF290E068, 0xF2800200, 0xF2804000, 0xF2807000 | same | registers the bootloader set [?] |
Only 5 bytes differ between the two live captures, 38 between live and snapshot. The model leaves
this page zero; whether the kernel needs it on a cold boot is not tested [?].

### 10.3 Power-micro frames (0x634433A4 = PA 0x034433A4)
Live frames pass the CRC. Slot B byte 25 (TLV 3 data[1]) is 0x00 live and 0x40 in the snapshot,
confirming the "powering off" bit `a7c_extract.py` clears; slot B byte 27 is 0x03 live vs 0x02.
`fw/submicro_frames.bin` (snapshot, patched) differs from the live frames in 58 bytes (52 in slot A,
including the clock bytes; 6 in slot B). The live frames are in
`fw/submicro_frames_live.bin`; boot with them using
`A7C_SUBMICRO_FILE=fw/submicro_frames_live.bin ./run.sh` (default unchanged) [not yet booted].

### 10.4 What fills "object 1": the HS step sequence on evsel comparator 1 (D)
Static trace in av-cam.bin (base VA 0x635C6000), behind the §7.5 crash:
1. Setup function **0x63967E58** posts the first HS step and arms **evsel comparator 1** with the
   callback **0x63968EFC** (0x639684B4..0x639684CC: `0x63784D98(1, compare, 1, closure)`).
2. `0x63784D98(ch, compare, arm, closure)` stores the callback in a 10-entry table (0x64286374,
   0x1C each) and calls 0x63784C5C, which writes **0xF3C30000 + 0x114 + 8*ch = compare** and
   **+0x110 + 8*ch = arm** (channel 1: +0x11C / +0x118). The literal 0xF3C30000 is at 0x63784CE8. This is
   the comparator group of `cxd90014_evsel.c` (IRQ 84), which already models these registers.
3. Each comparator-1 firing runs **0x63968EFC**: it counts ticks and every (Y >> 2) ticks advances the
   stage byte at **G+0x3F61** (G = `*(0x643FFD24)`), stopping at G+0x3F60 (done flag G+0x3F68). It
   posts the HS handler **0x63969DB4** with `(x << 8) | stage`, and re-arms comparator 1 while a
   counter is below its limit (0x63968FD0..0x63969010).
4. **0x63969DB4** is the HS handler; the low byte of its argument is the stage, 0..3 (debug names
   `HS0_Mai S` .. `HS3_Mai S`). Stage **2** (the `cmp r4,#2` at 0x6396AB48) calls 0x63DA55C2 with the
   table at `*(0x643FF97C)` and fills **object 1 (0x643FF988)**.

So object 1 is filled only after comparator 1 has fired enough times to reach stage 2. With
`evsel=on`, the model fires the frame sources (group A, source 29) from the start, every 33367 us, so a
frame can reach the pipeline before HS stage 2 has run. Next: compare the comparator-1 tick rate and
the time to stage 2 against the frame period in the model (gdb breakpoints on 0x63968EFC and 0x63969DB4,
r0 low byte = stage, and on the frame routine), and find what starts the frame sources on the camera.
The live-capture tool (v2) reads av-cam data PA 0x04300000..0x04588000 on each pass, which holds
0x643FF97C (table pointer), 0x643FFD24 (= G) and object 1 at 0x643FF988, plus object 1's +0x0C target
if that is elsewhere, to show these on the running camera.
