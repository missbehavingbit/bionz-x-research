# E0525 — QEMU `sony-a6400`: missing `/setting/ulogio.bin` starved the ulogio queue `0x13A`; with it, Appframework builds its objects and opens a view, then powers off with factor 7 (power lever)

Date: 2026-11-15. **Strictly offline.** No camera access, no USB. `emmc.img` is unchanged: the
`/setting` addition is written into the per-run qcow2 overlay only. Builds on E0521/E0522.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

1. **Blocker: five threads waiting in `vpool_alloc` [C].** A `/proc/<pid>/task/*/wchan` snapshot
   (297 threads) showed av-cam `liro-kliro_65`, `bootin.elf` and `im.elf` `THREAD_INFRA0/1/2`
   sleeping in `vpool_alloc`. That symbol is exported by `osal_uipc.ko`, the UIPC message
   allocator (`osal_valloc_msg_wait`). It is not a hardware wait.
2. **The full queue [C].** `/proc/osal/uipc` showed one realtime vblock exhausted:
   `13A(81888) 68802020 32` (81,888 B used, 32 B free). Its GMSGQ row was `13A 1078 0 0 0 1 1078 1 0`:
   M = 1,078 pending, C = 0 consumers, F = 1 full. Every other queue was nearly empty.
3. **Why nobody reads it [C].** `Oein/a5000-emulator` `docs/RE_UI_BRINGUP.md` §1 documents that
   checkpoint logging uses `osal_snd_sync_msg(0x8F013A)`, served by `osal_ulogio.ko`. Here
   `init` fails `insmod /usr/kmod/osal_ulogio.ko ulogio_initfile=/setting/ulogio.bin` with
   `error(1)`, and `ulogiod` prints `failed to load config from driver! / init failed`. The a6400
   update package's `0800_appli/setting/` has `DmmConfig.bin`, `appfw_infra`, `facedmy.bin` and
   `kemco.txt`, but **no `ulogio.bin`**. On a real camera the file already exists on `/setting` [I].
4. **Fix [C].** `run.sh` adds `ulogio.bin` to the overlay's `/setting` partition (nflasha2, file
   offset `0x1000000`, 20 MiB FAT12). It extracts the partition, adds the file with
   `qemu-a6400/tools/fat_add_file.py` (refuses to overwrite), and writes the partition into the
   qcow2 overlay with `qemu-io write -s`. Source: the ZV-E10 v2.03 package's `ulogio.bin`
   (`5def1fa8…`, 3,796 B, header `LGNM 191H_Dslr_Astra-Diadem`), a **cross-body stand-in [A]**. Its
   `LOGB` buffer (`0x03085000`, size `0x478000`) and `PRTB` buffer (`0x034FD000`, `0x8000`) both lie inside
   the a6400 main cmdline's `wbimem=0x500000@0x03085000` [C].
5. **Result [C].** `osal_ulogio` loads (`ULOGIOD_MAIN`, `ULOGIOD_CB`, `ULOGIO_LOGREC` threads). Queue `0x13A`
   now has 0 pending and 2 consumers. **No thread waits in `vpool_alloc`.** The thread count rises
   to ~400, and `Appframework` runs its event loop (`poll_schedule_timeout`).
6. **New boundary: power-off factor 7 [C/I].** `/proc/snsc_boot_time` (1,303 lines, now including
   userland) shows `CreateObject()` for ObjPlayer, ObjStillRecorder, ObjCamera, ObjCameraAgent,
   ObjFaceRecorder, ObjMovieRecorder, ObjAudioProcessor, ObjMic and others; `EV onStarting`; and
   `modelBootEE INIT`. At 2.18 s comes `EV OFF add_off_factor: 0x7 :0x920168`,
   `go inact_app(active) by off: 0x7`, `PS NG OFFSEQ`. Then view `Vo 00000002` (`Lc Layer`,
   `Lc Layout`), `EV PIN_NOTIFY` ×2, and `MediaMount /nondev/simulate` at 10.9 s. a5000
   `docs/RE_REBOOT.md` §B identifies factor 7 as `OFF_BY_KEY`, the **power lever**, reported by
   the sub-micro (slot-A RX byte 28 bit 0, 1 = ON) [I for a6400: same platform family, not yet
   decoded here]. With no sub-micro modelled, the lever reads as OFF.

## 1. Hardware-touch map (diagnostic catch-all)

`sony_a6400.c` now maps a low-priority `create_unimplemented_device("cxd90045.unmodelled",
0xF0000000, 0x08000000)`. Behavior is unchanged: reads return 0 and writes are dropped, as in the
unassigned space before. Every access is now logged under `-d unimp`. One 150 s run (95,945
accesses; `artifacts/qemu-a6400/e0524/unimp-access.log.gz`), by 4 KiB page:

| Page | R / W | Likely block (a5000 / `/proc/udm/devices`) |
| --- | --- | --- |
| `0xF2402000` | 0 / 7,527 | watchdog (CXD4115 channel) |
| `0xF2910000` | 64,965 / 26 | GPIO p0.. (`/proc/udm/devices` gpio ch0..17 at `0xF2910000 + 0x100·ch`) |
| `0xF2401000` | 12,493 / 2,015 | I2C (devices: i2c ch0/1, IRQ 199/200) |
| `0xF2404000` | 7,513 / 29 | unknown [?] |
| `0xF2405000` | 30 / 32 | SIO (devices: `0xF2405000 + 0x200·ch`, IRQ 201..205) |
| `0xF2406000`, `0xF2407000` | 85 / 6, 22 / 11 | ADC units 0/1 (a5000: IRQ 215/216) |
| `0xF0102000` | 270 / 217 | work DMA engine (a5000: IRQ 173–188) |
| `0xF3000000`, `0xF3100000`, `0xF3120000`, `0xF3002000`, `0xF3102000` | small | front-end / sensor-interface units (same addresses as the a5000) |
| `0xF3420000..0xF3428000` | small | frame timer area [?] (a5000 SYSV is `0xF3402000`) |
| `0xF3440000` | 0 / 1 | display (a5000: IRQ 79 status) |
| `0xF3C04000`, `0xF3C08000`, `0xF3C30000` | small | unknown [?] |

SIO channel 1 (`0xF2405200`) is configured (`+0x04 = 0x41000000`, `+0x14 = 0x05000000`, …) and
started (`+0x00 = 0x20000000`). It never reports completion: `+0x00` reads 0, and the driver later
writes 0 to stop it [C]. a5000's SIO contract ("set `+0x00` bit29 done, raise IRQ 201+ch") is the
candidate model.

## 2. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0524/threads-before-ulogio.log` | `68d330601256ab339d872f89a18ce49fd940764c8afa94a3a14e5f459dff35e6` |
| `artifacts/qemu-a6400/e0524/uipc-before-ulogio.txt` | `bee3cf0afb5880758f1e65528c1513cd918657b2965cacabb7d5f72f016c3a08` |
| `artifacts/qemu-a6400/e0524/threads-after-ulogio.log` | `9e0f4cf81c95890f93744db314740b81e17a46a57488657ac644f790165113ff` |
| `artifacts/qemu-a6400/e0524/snsc_boot_time-after-ulogio.txt` | `469d41a3674271946cdceeae429c244e37c3c746920a96d169ce5ac2388096a5` |
| `artifacts/qemu-a6400/e0524/unimp-access.log.gz` | `a2cfa5bb5fbd84c66a5d1c37ad2f0930e93e700a88d555dec63a4a058ddbd596` |
| `qemu-a6400/run.sh` | `aee7fd74ca4b2df62ed4c20889f4e59c080dc1c673f61b9e70ead6e42abc9332` |
| `qemu-a6400/tools/fat_add_file.py` | `9f6409960cc8a56e04b26d0e0e8d501101a3c178f7dc34d0b764a57a79ecc5fd` |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `b96f77d4569087661365ed705e37bb140adfab26c3ac4f1c3b7685129c6e5c5b` |
| ZV-E10 `0800_appli/setting/ulogio.bin` | `5def1fa87d8d19e895f848572d995e377099e865ddcde37cb22d12f885eceb7e` |

Commands: `TIMEOUT=110..190 ./run.sh` with a timed serial script (`/proc/*/task/*/wchan`,
`/proc/osal/uipc`, `/proc/snsc_boot_time`, `/proc/interrupts`). For the map:
`QEMU_EXTRA='-icount shift=0,sleep=on -d unimp -D unimp.log' TIMEOUT=150 ./run.sh`.

## 3. Failures and cautions preserved

- `cat /proc/osal/ulogio` oopses the kernel while `osal_ulogio.ko` is not loaded
  (`PC is at 0x5f074ca0`, bad PC in `proc_file_read`). Its proc handler points into the unloaded
  module. That oops was caused by this probe [C].
- The QEMU monitor `xp` of GIC MMIO (`0xF8001100`) under `-icount` crashed QEMU itself (exit 139).
  Do not read device registers through the monitor [C].

## 4. Next boundary

- **Sub-micro link** (power-lever state, factor 7): SIO controller model plus GPIO handshake lines
  (a5000 `RE_SUBMICRO_LINK.md`: strobe p0.12 / IRQ 133, request p3.17; 128-byte TLV frames with
  CRC-16/CCITT-FALSE little-endian). The a6400 pin assignments come from Backup ids F904CA/CB,
  F90465/66, … and must be read from this `Backup.bin`. a5000 replays the micro's frames from a
  real-camera RAM snapshot. The a6400 has none, so a frame source must be found (decoded layout, or a
  read-only capture) before replay [A otherwise].
- Make SIO transfers complete (ch1 at least), and model the watchdog (`0xF2402000`, 7.5k kicks per run).
