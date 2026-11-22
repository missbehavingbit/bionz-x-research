# E0665 — QEMU reaches the a6400 shooting screen and the step-32 wait without a camera capture

Date: 2026-11-21. **Strictly offline.** No camera, no capture. All firmware bytes come from two
public Sony updaters: the a6400 package (2019, rootfs `/version.txt` `38111_03.2019052701`) and
one file, `ulogio.bin`, from the ZV-E10 package. No guest code, sequencer variable or Ready flag
is changed.

Until now every QEMU result needed one of two private inputs: a read-only eMMC capture of the
author's camera (E0524) or the historical `qemu-a6400/data/` disk image, which is not in the
repository. Without that camera, nobody could reproduce the QEMU milestones.

## 0. Short answer

1. **`tools/research/qemu_packaged_inputs.py` builds the whole `qemu-a6400/data/` layout from
   the a6400 updater.**
   - The SDM table comes from `0300_partconf/partinf.conf`.
   - Partitions 3, 5, 7 and 15 are copied byte for byte.
   - The vfat partitions are formatted with `mount.conf` geometry and filled from `0800_appli`.
   - Kernel, initrd, `av-cam.bin` and `Backup.bin` come from the same package.
   - Two builds are byte-identical. `emmc.img` is
     `fec5bcf65a418d32adca1c7a8e8f01efb845ddbd52471c70bfcc0923d4490e4f`.
2. **The serial boot reaches every E0521 milestone, on a single-release image set.** The boot
   runs through:
   - `init`;
   - all modules except the absent `sata_drv`/`msen_drv`;
   - `LIRO rev:4-MUSASHI` and `end entry_main`;
   - `DMP_2D_Initialize : 0` and stella `HeapAllocator`;
   - BusyBox ash.

   E0521 mixed a 2021 kernel with 2019 partitions. Here the kernel is the package's own 2019
   build.
3. **`backup.ko` needs `/setting/Backup.bin`.**
   - Without the file, `backup_init` fails in `backupfile_select_path`. Both `Backup_pri_file`
     and `Backup_sec_file` name that path.
   - `osal_ulogio` and the modules after it then fail on `Unknown symbol Backup_read`.
   - The builder copies into `/setting` the file that `0110_backup/MainDest.txt` names:
     `CX62600_UC2.bin` (`241c8521…`), the E0521 preset.
4. **The powered sub-micro replay no longer needs a camera.**
   - The packaged WBI1 (`nflasha5`) holds the 384-byte SIO ch3 RX slots A/C/B at PA
     `0x034433A4`, in its section at `0x03405000`.
   - All three CRCs are valid. Slot B TLV `0x03` is hibernate `00 40 00 02`, the state E0651
     found in the camera's own WBI.
   - E0651's transformation sets it to powered `00 00 00 03` and restamps the CRC. This
     changes exactly bytes 289, 291, 382 and 383.
   - The result is `7dfdd07a5b9292ec20187c934663f39bc470718372d33a4d9e2a7872fbc664c2`.
5. **`ulogio.bin` is required.**
   - The a6400 updater does not carry it. Without it, `insmod osal_ulogio.ko` fails, the rear
     LCD console stays black for 320 s, and module 1 stays at `0x0100`.
   - With the ZV-E10 copy (`5def1fa8…`, the stand-in `run.sh` already uses, E0525):
     - the stock P-mode OSD appears: `P`, `NO CARD`, the AF frame marks and the exposure scale;
     - VPH ch51 is programmed with `lb=0x400`, 570 lines and bases alternating between
       `0x14fbc000` and `0x14e90000`.
6. **The sequencer stops at the packaged step-32 wait.** Read through QMP `xp` with no observer
   and no breakpoint:
   - module 3 reports `0x0326` at `0x64353570`;
   - the request halfword at `0x64350F06` is `0x032E`;
   - the Ydet counter at `0x643EE7DE` is `0x0000`;
   - all three hold from 60 s to 320 s.

   This is the E0561 state, on public inputs. The step-32 gate can now be studied by anyone.

## 1. Inputs and build

| Input (updater path) | Bytes | SHA-256 |
| --- | --- | --- |
| `0300_partconf/partinf.conf` | 4,017 | `fc23dc2edb8e378e7985fb5da4d09a0e4c5f2445d679a730fd1d544dde831f78` |
| `0700_part_image/dev/nflasha3` | 31,467,520 | `ae8c63f2ce3e567b21f9fd91659eea5c3d04d724b9a2dc7982ed3486d9fe77c4` |
| `0700_part_image/dev/nflasha5` (WBI1) | 33,486,976 | `19a702b030306a32f4287131da314e5c89d66163997f0f9a23b0967981b0001b` |
| `0700_part_image/dev/nflasha7` | 5,870,592 | `305e289ecc1d768544af71c83d58ce23ec4d232be1e7572d9d4f68f0f12188cc` |
| `0700_part_image/dev/nflasha15` | 214,664,192 | `0e9b19144b021d8b1b1908554a7ac9cdc6f8b9599eeeb1d53118b8832a296e56` |
| `nflasha3_unpacked/vmlinux.bin` → `nflasha3-Image` | 4,085,888 | `12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707` |
| `nflasha3_unpacked/initrd.img` → `nflasha3-initrd` | 3,963,904 | `15803c2d5e2ba7be50de08edc87f7cce335f1763b38a814a133cce2663a13e83` |
| `nflasha3_unpacked/av-cam.bin` | 16,674,456 | `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e` (= E0521, E0561) |
| `0110_backup/…/CX62600_UC2.bin` | 1,148,036 | `241c8521035bc9ff1edbe66cf3d42d81879a4bef8ff9e21cbfb84530a1446154` (= E0521) |
| ZV-E10 `0800_appli/setting/ulogio.bin` | | `5def1fa87d8d19e895f848572d995e377099e865ddcde37cb22d12f885eceb7e` |

```bash
python3 -m tools.research.qemu_packaged_inputs <a6400>/firmware.tar_unpacked qemu-a6400/data
cd qemu-a6400
ULOGIO=<zve10>/firmware.tar_unpacked/0800_appli/setting/ulogio.bin \
SUBMICRO_FRAMES=data/submicro-frames-powered.bin \
SUBMICRO_FRAMES_SHA=7dfdd07a5b9292ec20187c934663f39bc470718372d33a4d9e2a7872fbc664c2 \
EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1 AVCAM=data/av-cam.bin \
TIMEOUT=420 QEMU_EXTRA='-icount shift=0,sleep=on -qmp unix:a6400.qmp,server,nowait -net none -no-reboot' \
bash run.sh
```

`SUBMICRO_FRAMES_SHA` is new in `run.sh`. It declares the hash of a replay extract other than the
captured one. The captured extract (`6d5026e3…`) is still the default, so a different extract has
to be named and is never taken silently. The QEMU build is 11.1.3 with the pinned patch;
`qemu-system-arm` is `67e6f1e4…`, built here. The QMP probes are
`human-monitor-command xp /1hx 0x04353570`, `xp /8hx 0x04350f04` and `xp /1hx 0x043ee7de`, every
20 s. They use physical addresses: E0553 reads `0x64351F28` at PA `0x04351F28`. The probes also
take `screendump` at 100, 200 and 300 s.

## 2. What the image contains

| Partition | Mount | Origin |
| --- | --- | --- |
| nflasha3, 5, 7, 15 | `/system`, `/wbi`, `/rootfs`, `/usr` | packaged, byte for byte |
| nflasha2 | `/setting` | constructed FAT: `0800_appli/setting` plus `Backup.bin` |
| nflasha10, 18 | `/tmp`, `/lens` | constructed FAT from `0800_appli/tmp`, `/lens` |
| nflasha1, 6, 11, 12, 13, 17 | vfat | constructed FAT, empty |
| nflasha4, 16, 23 | raw / prfile | zeros: the updater does not carry them |

- The SDM header is `"82461.00"`, followed by 23 rows in `partinf.conf` order.
- It sits at backing offset 8 MiB, behind the two 4 MiB boot partitions (E0524).
- The backing file is 2 GiB, the power of two the Sony eMMC backend requires.
- Zero-filled ranges are emulator space, not camera content.
- `run.sh` adds `ulogio.bin` to its private copy of the setting partition, not to `emmc.img`.

## 3. Runs

Each run used a fresh qcow2 overlay. Times are host seconds after QMP connected. With
`-icount shift=0,sleep=on`, guest time follows the host clock while the guest idles, so the OSD
stage seen at a given second varies between runs. The sequencer words did not vary once
reached.

| Run | Image, policy | Observed |
| --- | --- | --- |
| `pku` | `fec5bcf6…`, ZV-E10 `ulogio.bin` | Module 1 request and report are both `0x0118` from 40 s (E0557's on-time `VD_Sequence`). At 100 s and 200 s the screen has 1,626 non-black pixels: `P`, `NO CARD`, AF marks, EV scale. At 300 s it has 861: `P` and the EV scale. ch51 is programmed three times, alternating bases |
| `stp` | as `pku` | Module 3 report `0x0326`, request `0x032E`, counter `0x0000`, from 60 s to 320 s. Request halfwords from `0x64350F04`: `0118 032e 0282 0481 0580 0680 0702 0920` (hex). 861 pixels at 100, 200 and 300 s |
| `pkn` | as `pku`, `ULOGIO=` | `insmod osal_ulogio.ko … failed` and `ulogiod init failed`. Module 1 stays `0x0100` to 320 s. ch51 is never programmed. The console is black at 100, 200 and 300 s |
| `stb`, `stb2` | as `pku`, `LIVEVIEW_SOURCE=bars` (`stb` ran beside `stp`, `stb2` ran alone) | From 80–100 s the request halfwords become `0102 0302 0202 0405 0502 0602 0702 0920` and module 3 reports `0x0302`. Nothing changes until 320 s. CamPig logs frames 1–8 only, then nothing. `stb2` shows 861 pixels at 100, 200 and 300 s |
| `pk1` | earlier image `cbca5fdb…`, `SUBMICRO_PAGE=` | serial E0521 milestones (§0.2) |
| `pk0` | image without `Backup.bin` (`7acd7113…`) | `backup_init() error (selct_path)`, 20 module failures, `ulogiod init failed`, user fault in `libMWF.so MonQueue::IsFull` |

`cbca5fdb…` differs from `fec5bcf6…` only in the FAT timestamp of `/setting/Backup.bin`. The
first builder stamped the copy with the current time. The final builder keeps the packaged
time, so that builds are byte-identical. Two earlier powered runs on `cbca5fdb…` showed the
same screens and the same ch51 programming.

## 4. Alternative explanations

- **"The shooting screen comes from a stale image, not from this boot."** Without
  `ulogio.bin` the console stays black for the whole run. Each run starts from a fresh overlay,
  and QEMU has no screen image of its own.
- **"The powered frames are the author's capture under another name."** Their hash differs
  from the pinned captured extract. `run.sh` refuses them unless `SUBMICRO_FRAMES_SHA` names the
  new hash. The bytes come from the packaged WBI, and the only change is the documented TLV
  and CRC.
- **"`0x032E` at `0x64350F06` is a coincidence."** The value and the report `0x0326` at E0561's
  address are the documented pair. The zero counter at E0561's `0x643EE7DE` matches its finding
  that no Ydet record arrives. The probe reads memory only.
- **"This replaces the captured boot."** It does not. E0591's captured step-26 and lens-rail
  results, and the captured settings, still need the capture.

## 5. Confidence and limitations

- **Verified:**
  - the byte-identical double build and every input hash;
  - the serial milestones;
  - the `backup.ko` dependency on `/setting/Backup.bin`;
  - the `ulogio.bin` dependency;
  - the WBI frame location, CRCs and TLV state, and the four changed bytes;
  - the screen pixel counts at the times listed;
  - the sequencer halfwords over 320 s.
- **Not reproduced: E0591's bars result.** E0591 replayed bars on its reconstructed packaged
  boot, which kept the captured kernel, initrd and layout, and reached step 42 of 43. On these
  public inputs, bars twice led to the `x02` request set above, and CamPig stopped after 8
  frames. Why the source changes the sequence is not known. That question is next.
- **Not shown:** a bars picture on the console. ch51 is programmed with `en144=0x233` (YUV
  layer bit set), but the dumps show only the OSD. The cause in `vph_compose` is not
  diagnosed.
- **Not camera content:** the zero-filled partitions (MPR backup, Android, PMBP) and the
  constructed FAT volumes. The package is the 2019 release; the owner's unit runs 2.00.
- **One file from another model:** `ulogio.bin` comes from the ZV-E10 updater.

## 6. Next

1. Find why `LIVEVIEW_SOURCE=bars` moves every module to its `x02` request here, unlike E0591,
   and why CamPig stops after frame 8.
2. On the default source, follow E0561: find what should run the `PQD_YdetRegReadManager`
   producer so that the counter at `0x643EE7DE` passes 6 and module 3 accepts `0x032E`.
