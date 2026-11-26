# E0698 — WiFiSync camera-side mod: read-only WFS transfer over a 5 GHz AP (offline)

**Date:** 2026-11-24
**Scope:** implement the camera half of the locked plan (Phases 0–2) with no
camera access: no USB session, no mode change, no Wi-Fi enable, no SD or flash
access. Everything below is offline/disassembly, host code execution, or
Unicorn execution of original or new code.
**Status of physical gates:** the 5 GHz AP and the on-camera Fsys reads are
**not** run; the Phase 1/2 Go/No-Go checks remain open (section 9).

## 1. Deliverables

| Path | Bytes | SHA-256 | Role |
| --- | --- | --- | --- |
| `mods/wifisync/wifisync_core.c` | 37,968 | `4acbe75a…f9ab13c4` | portable queue/Smart filter/HTTP/Range/CRC/power core |
| `mods/wifisync/wifisync_mod.c` | 36,809 | `1c105eb7…a16eeed2` | im.elf adapter (syscalls, Fsys, hook, GPIO, AP control) |
| `mods/wifisync/wifisync_ap.sh` | 4,644 | `13fdf9c4…0c49decc` | canonical camera AP script |
| `artifacts/tests/wifisync-build/WiFiSync.mod` | 29,672 | `8fcbb90ece61da70b565e045483cf0a2687432643ba88ec2424ce7f6b90b930a` | SMOD v2 mod, 311,296 B bss, 1 hook |
| `tools/research/wifisync_client.py` | 11,293 | `b2cda5a6…b90d0af1` | reference PC client (Range resume, CRC/SHA, ack, dedup) |
| `tools/research/wifisync_contracts.py` | 12,075 | `fe9c9a59…507d2ba7` | Phase 0 contract extraction |
| `artifacts/tests/wifisync-unicorn/wifisync_checks_arm.elf` | 97,044 | `54cafecc…412fc01e` | Cortex-A5 check binary |

Inputs: `libmpr.so` 12,740,352 B SHA-256 `40a86215…5b09bb12`;
`libInfraFsys.so` 411,548 B SHA-256 `75f24fd0…db8c9af2c`;
`bcm4339.bin` 558,561 B SHA-256 `6b720704…f337621fd9`.

## 2. Phase 0 contracts (`python -m tools.research.wifisync_contracts`)

22/22 checks pass; manifest `artifacts/analysis/wifisync-contracts/manifest.json`.

| Fact | Value |
| --- | --- |
| `libmpr.so` FNV-1a(first 4 KiB) | `0x7a4ed0d1` |
| `libInfraFsys.so` FNV-1a(first 4 KiB) | `0x61e41f02` |
| `MprMonCtrl::ConPutWriteStill` | `libmpr.so + 0x008791a4` (Thumb bit at `…a5`), reference VA `0x079bd1a4`, size 304 |
| 12-byte hook window | `2de9f041 0d46 00af 0446 4049` |
| `Fsys_Open` (read contract) | `tst r6,#3`; `orreq r6,#1`; handle attr 3 for read; create bit 2 |
| `Fsys_Read` | returns `PrFILE_fread` byte count; handle attr needs bits 1+2 |
| `Fsys_Stat` | calls `PrFILE_fstat`; `PFFILE_GetStat` stores a 64-bit size at `[r1]`, attr at `+0x1d` |
| AP tools | `wpa_supplicant`, `dhcpd`, `wl`, `iperf`, `iw` present |
| NVRAM | `high/bcm4339_H_*.txt` sample has `aa5g=1` |
| CLM | `CLM DATA` present in `bcm4339.bin` |
| BLE | `bin/bsa_server` present (identity only; no GATT contract claimed) |

## 3. Core behaviour on Cortex-A5 Thumb (Unicorn)

`python mods/wifisync/test_wifisync_unicorn.py artifacts/tests/wifisync-build --no-build`

* packaging: 17 checks (name/version/flags, one hook at `0x079bd1a4`, original
  bytes equal to the firmware file, SMNU 3 items with cfg offset 4, one RLC1
  word, size < 64 KiB, bss 311,296)
* core: 76 named checks on Cortex-A5 (72 portable assertions + 4 runner checks)
* **93/93 pass**, receipt `artifacts/tests/wifisync-unicorn/receipt.json`.

The portable assertions cover: CRC-32 vectors and slice-by-4 vs bitwise,
name/path construction, queue/dedup/watermark, Smart Single-Best in all three
capture modes, late-ARW supersession, the 300 ms burst quiet window, growth
re-arming the CRC, size floors, HTTP `/status` `/manifest` `/file` `/ack`
`/events` `/` and 400/404/405/409/416 paths, Range bodies at exact offsets,
bounded manifest paging, ack idempotence, long-poll wake, and the power machine
(OFF→ACTIVE→STANDBY→ACTIVE→OFF).

## 4. End-to-end protocol on a real socket (host reference server)

`python3 mods/wifisync/test_wifisync_host.py` (run in WSL; real TCP, real files)

* discovery + Smart rule: `DSC00001.JPG`, `DSC00002.ARW`, `DSC00003.ARW`
  (the `DSC00003.JPG` of a RAW+JPG pair is absent)
* full download: size, CRC-32 and SHA-256 equal to the source file
* interrupted transfer: 64 KiB written, closed, resumed with
  `Range: bytes=65536-`, final SHA-256 equal
* `/ack` watermark, duplicate ack (`"duplicate":true`), `409` on a wrong size,
  `404` on an unknown sequence
* `/events` long poll woke on a new photo
* 15-photo mixed burst: all discovered, no `.JPG` duplicate of a RAW+JPG stem,
  every downloaded file SHA-256-identical
* manifest empty after ack; a repeat scan adds nothing
* **62/62 pass**, receipt `artifacts/tests/wifisync-host-matrix/receipt.json`

## 5. Mod packaging (SMOD v2)
`bash mods/wifisync/build_wsl.sh artifacts/tests/wifisync-build` prints
`bss=307628 aligned=311296` and a pack report with `code_bytes: 29092`,
`hooks: 1`, `submenu_items: 3`, one relocated word (`0x5508` →
`0x79bd1ad`, `libmpr.so`). The build rejects non-PIC relocations, libc or
`__aeabi_*` helpers, and derives `--bss` from `sizeof(struct ws_mod_state)`
(4-byte rodata probe read at its file offset).

The 12-byte hook is valid because the first 8 bytes of `ConPutWriteStill` are
`mov`-only; the trampoline replays them and resumes at `+8`, so the original
PC-relative `ldr r1,[pc,#0x100]` executes at its real address. Replaying the full
12 bytes would have loaded a literal from the trampoline's PC.

## 6. Repository suite

`python -m unittest discover -s tests -q` → **2658 tests, 6 failures, 109 skipped**.
The 6 failures are outside this work and pre-date it:
`emulator.test_run_b26_uvc_prearm` (Track B artifact pins),
`test_mod_manager…validate_and_resolve_mlv2_and_frozen_pmca` (a
`Sony-PMCA-RE` checkout exists in the repo root) and four
`test_qemu_dwc3_composite.CompositeContractStaticTests` cases (the modified
`qemu-overlay` in the working tree). The new tests pass: 6 contract tests and
8 script/config guard tests. The research-status documents were regenerated
(`python -m tools.research.render_research_status --write`) so the 5
generated-artifact tests pass again; the map was rebuilt
(`python -m tools.research.knowledge_map build`; `check` reports OK).

## 7. Safety
* No `/dev/mem`, no bulk physical reads, no flash reads/writes, no SD mount.
* WL_REG_ON via `/dev/gpio` ioctl `0xC0104702` (group 9, bit 18; mode 9 high /
  17 low), matching the physically verified `custom_os/launcher/wlan_gpio.c`.
* All writes under `/tmp_bt/wifisync/`; the card is read-only through Fsys
  (`A:/mods/wifisync.cfg`, optional `A:/mods/wifisync_ap.sh`).
* `bcmdhd` is unloaded on OFF only when the mod owns `/tmp_bt/wifisync/owned`;
  a Sony Wi-Fi session makes `up` refuse (`busy Sony WiFi ON`).
* Identity checks fail closed (size + FNV + prologues + hook bytes).

## 8. Assumptions and explicit non-claims

* `ConPutWriteStill` is used as a completion *notification*. Its monitor record
  carries entry IDs and still-file types, not a path; the mod does not parse it.
* The worker thread is created with `SYS_clone` and shared glibc TLS (no
  `CLONE_SETTLS`); only read-only Fsys entry points are called from it. This is
  a bounded risk, not a proof of Fsys thread-safety.
* Directory enumeration (`Fsys_FindFirst/Next`) is **not** used: its argument
  layout and DTA fields are not qualified. Discovery is a bounded name probe
  (`DSC0<index>`) with a 512-miss stop and a non-advancing steady window.
* CRC camera timing (< 80 ms/24 MB), 5 GHz live exposure, `iperf` throughput,
  AP association, `dhcpd` behaviour and standby wake latency are **not**
  measured.
* No Android code was written.

## 9. Deviations from the plan text

1. **No `Fsys_FindFirst` catch-up scan** (unqualified ABI); replaced by the
   bounded probe described in §7.
2. **CRC is cached per file** rather than emitted strictly "on the fly" (HTTP
   needs the header first); `crc_step` computes it in the maintenance loop.
3. **Two power levels stay as the plan permits**: `standby=deep` is implemented
   but documented as unusable without the Phase 2.5 BLE wake.
4. **The AP script is embedded** and regenerated from `wifisync_ap.sh` at build
   time; an SD override at `A:/mods/wifisync_ap.sh` still wins.
5. `/events` is a 20 s long poll that runs maintenance between 200 ms slices
   (single worker thread); no second thread was added.

## 10. Next boundary (needs an explicit go-ahead)

A single supervised session on the physical ILCE-6400:

1. Load `WiFiSync.mod` RAM-only and press ON; read
   `/tmp_bt/mods/wifisync.log` (`wlan_power_on`, `ap_rc`, `listen_fd`) and
   `/tmp_bt/wifisync/ap.log` (`bands`, `chanspec`, `5g_unavailable`).
2. Verify `wl bands`/`wl chanlist` expose Ch 36/40/44/48; run `iperf` against
   the PC (target ≥ 8 MB/s); single shot in JPG, RAW, RAW+JPG; confirm the mod
   queues only the ARW for RAW+JPG and that the trigger cadence is unchanged.
3. Fetch with `tools/research/wifisync_client.py --once`; compare SHA-256 with
   the card; cut the link mid-ARW and confirm Range resume.
4. Press OFF; confirm `bcmdhd` unloaded, WL_REG_ON low, `/proc/uptime`
   monotonic and no reboot.
