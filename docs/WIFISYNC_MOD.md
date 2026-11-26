# WiFiSync: camera-side design and verified contracts

Status: camera-side implementation complete for Phases 0–2 **offline**. The
5 GHz AP and the on-camera WFS reads have **not** been run on a physical camera
in this work; the Phase 1/2 gates below remain open and are the next supervised
session. The Android application is a separate workstream.

Related: [mod README](../mods/wifisync/README.md),
[evidence E0698](evidence/0698-wifisync-camera-side-mod.md),
[BCM4339 baseline](evidence/0406-bcm4339-wifi-bt-tuning-surface-readonly.md),
[CLM parse](evidence/0408-clm-blob-and-wlan-baseline.md),
[native Fsys contracts](evidence/0220-native-filesystem-contracts-and-live-volume-table.md).

## 1. What was built

```text
im.elf (ILCE-6400 2.00)
├── WiFiSync.mod (SMOD v2, 29,672 B, 311,296 B bss, 1 hook)
│   ├── ConPutWriteStill hook ──► capture counter + timestamp (O(1))
│   ├── HTTP :8765 ◄── worker thread (SYS_clone, 64 KiB mmap stack)
│   │     ├── /status /events /manifest /file /ack
│   │     └── Fsys_Open/Read/Close/Stat (read-only, identity-checked)
│   ├── discovery: DSC0<index>.ARW/JPG probe, Smart Single-Best
│   └── AP control: WL_REG_ON GPIO ioctl + /tmp_bt/wifisync/ap.sh
└── bcmdhd + wpa_supplicant (AP mode) + dhcpd  → 192.168.122.1/24
```

All writable state lives in the loader-allocated bss (`struct ws_mod_state`,
307,624 bytes: `ws_server` + 256 KiB streaming buffer + Fsys table + flags).
Nothing is allocated from `im.elf`'s heap. The worker stack is a separate
64 KiB `mmap2`.

## 2. Phase 0 contracts (offline, `tools/research/wifisync_contracts.py`)

22 assertions pass; the manifest is `artifacts/analysis/wifisync-contracts/manifest.json`.

| Contract | Value | How it was verified |
| --- | --- | --- |
| `libmpr.so` identity | size 12,740,352; FNV-1a(first 4 KiB) `0x7a4ed0d1` | file read + hash |
| `libInfraFsys.so` identity | size 411,548; FNV-1a(first 4 KiB) `0x61e41f02` | file read + hash |
| `MprMonCtrl::ConPutWriteStill` | `libmpr.so + 0x008791a4` (Thumb `0x008791a5`); reference VA `0x079bd1a4`; 12-byte prologue `2de9f041 0d46 00af 0446 4049` | symbol table + bytes |
| Hook replay | first 8 bytes are `mov`-only and PC-independent; resume at `+8` keeps the PC-relative `ldr r1,[pc,#0x100]` valid | instruction decode |
| `Fsys_Open` read flag | bit 0; `tst r6,#3` + `orreq r6,#1`; handle attribute 3 for read | instruction decode |
| `Fsys_Open` create flag | bit 2 (`and r2,lr,#4` branch) | instruction decode |
| `Fsys_Read` | bytes from `PrFILE_fread`; handle must carry attr bits 1+2 | instruction decode |
| `Fsys_Stat` | calls `PrFILE_fstat`; `PFFILE_GetStat` stores the 64-bit size at `[r1]`, attribute at `+0x1d` | instruction decode |
| AP tool surface | `wpa_supplicant`, `dhcpd`, `wl`, `iperf`, `iw` present | file inventory |
| 5 GHz front end | NVRAM `aa5g=1`; CLM DATA present | NVRAM/CLM parse |
| BLE surface | `bin/bsa_server` present (identity recorded, no GATT contract claimed) | file inventory |

The mod re-checks all identities at ON: file sizes, first-4-KiB FNV and the exact
function prologues. Any mismatch logs `*_refused` and leaves the mod OFF.

## 3. Capture hook

`ConPutWriteStill` is a monitor callback the camera calls when a still write
completes. The SMOD hook replaces its first 12 bytes with a Thumb jump into
`ws_capture_hook`, which:

1. saves `r0-r3,r12,lr`,
2. calls `ws_hook_note()` (`capture_count++`, `last_capture_ms = now`, `events++`),
3. replays `push.w {r4-r8,lr}` / `mov r5,r1` / `add r7,sp,#0`,
4. resumes at `+8` (the original `mov r4,r0` and the PC-relative literal load).

The hook carries no file name; discovery is driven by the counter plus the
bounded `Fsys_Stat` probe. `Cb_WriteStill` is a monitor record of entry IDs and
still-file types, not a path, so the mod does not interpret its layout.

## 4. Transfer thread and I/O

* One `SYS_clone` thread (`CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM`) with a
  64 KiB `mmap2` stack. It is the only long-running thread; no `fork` inside it
  (AP transitions fork only brief `/bin/sh` helpers from the UI thread).
* The HTTP accept loop uses `poll(200 ms)`. Between requests, and between
  `/events` long-poll slices, the same thread runs `ws_scan` (1 s cadence),
  `ws_crc_step` and `ws_power_tick`. This keeps a single Fsys user on the hot
  path and makes `/events` wake on a real queue change without a second thread.
* Fsys calls are read-only, one file at a time: `open → read chunks → close`.
  Range resume is implemented by reading and discarding the prefix (no
  `Fsys_Seek`; its argument layout is not qualified).
* CRC-32 is slice-by-4 with four runtime-built tables in bss; it is computed
  once per file (on `/file` if not already done) and cached in the queue record.
  The camera timing target (< 80 ms for 24 MB) is **not** measured; only the
  host correctness is proven.

## 5. Smart Single-Best and dedup

* Per stem: `.ARW` wins if present; otherwise `.JPG`.
* A late `.ARW` marks a queued `.JPG` of the same stem as superseded
  (`ready=0`); `/manifest` and `/file` skip it and `ws_queue_ack` advances the
  watermark past it.
* A growth in an already queued file updates its size and re-arms the CRC.
* `/ack` is idempotent; a size/CRC mismatch returns `409` and keeps the
  watermark. The client acks only after `fsync` + atomic rename.

## 6. Power state machine

```text
OFF --(menu ON + AP up)--> ACTIVE --(idle_sec, empty queue)--> STANDBY
STANDBY --(capture or request)--> ACTIVE
any --(menu OFF / shutdown)--> OFF
```

`standby=low`: `wl pm 2`, AP stays associated (wake < 200 ms claimed by the
plan; not measured). `standby=deep`: AP down + WL_REG_ON low, which is only
useful once the Phase 2.5 BLE wake exists; the phone currently has to retry, so
`low` is the default.

## 7. Safety boundaries

* No `/dev/mem`, no bulk physical reads, no flash reads/writes, no SD mount.
* WL_REG_ON uses `/dev/gpio` ioctl `0xC0104702` (group 9, bit 18; mode 9 = high,
  17 = low), the same contract as the physically verified
  `custom_os/launcher/wlan_gpio.c`; the mod does not use the helper binary.
* Only `/tmp_bt/wifisync/` is written (script, config mirror, log, state,
  leases, pid files, wpa config). The card is read-only via Fsys
  (`A:/mods/wifisync.cfg`, optional `A:/mods/wifisync_ap.sh`).
* `bcmdhd` is unloaded on OFF only when the mod created `/tmp_bt/wifisync/owned`;
  a Sony-owned Wi-Fi session makes `up` refuse with `busy Sony WiFi ON`.
* The 5 GHz band gate: if `wl bands` has no `a`, the script logs
  `5g_unavailable` and returns failure unless `allow_2g=1`.

## 8. Verification receipts

| Suite | Result | Artifact |
| --- | --- | --- |
| Core assertions (x86-64) | 72/72 | command output in E0698 |
| Unicorn Cortex-A5 Thumb + packaging | 93/93 | `artifacts/tests/wifisync-unicorn/receipt.json` |
| Host protocol matrix (TCP, Range, CRC, SHA-256, ack, events, burst) | 62/62 | `artifacts/tests/wifisync-host-matrix/receipt.json` |
| Phase 0 contracts | 22/22 | `artifacts/analysis/wifisync-contracts/manifest.json` |
| Script/config guard tests | 8/8 | `tests/test_wifisync_scripts.py` |
| Contract unittest | 6/6 | `tests/test_wifisync_contracts.py` |
## 9. Open gates (physical session)

1. **5 GHz exposure.** With stock `bcm4339.bin` + `high/` NVRAM, run `wl bands`
   and `wl chanlist`; a 5 GHz SoftAP on Ch 36/40/44/48 with `iperf ≥ 8 MB/s`,
   one single shot and one burst, then a clean OFF with no reboot.
2. **WFS read.** Probe `A:/DCIM/100MSDCF` through the mod; confirm that
   `Fsys_Stat`/`Fsys_Read` return the same bytes as the card (SHA-256).
3. **Hook.** Confirm `ConPutWriteStill` fires once per still completion in
   JPG, RAW and RAW+JPG, and that the manifest contains only the ARW in RAW+JPG.
4. **Burst.** 15–20 RAW+JPG frames: the trigger cadence must be unchanged.
5. **Resume.** Cut Wi-Fi mid-ARW; the client must resume with `Range` and match
   the SHA-256.

Do not start Phase 1 without the user's explicit go-ahead: it changes the Wi-Fi
mode, loads kernel modules and starts the radio.
