# WiFiSync — camera-side `.JPG` / `.ARW` background transfer

A RAM-only `SMOD v2` mod for the Sony **ILCE-6400** (`CXD90045`, firmware 2.00)
that turns the camera into a 5 GHz Wi-Fi access point and serves newly captured
photos over an HTTP/1.1 subset on TCP `:8765`, so a phone can fetch them in the
background without leaving the shooting mode.

This directory is the **camera half** of the plan; the Android application is a
separate workstream.

| Item | State |
| --- | --- |
| Camera-side core (queue, Smart Single-Best, HTTP/Range/CRC-32, power machine) | written, host-tested (72 checks), Cortex-A5 Unicorn-tested |
| `WiFiSync.mod` build (SMOD v2 + RLC1 + SMNU, 1 hook) | builds reproducibly; packaging checked offline |
| Read-only Sony Fsys contract (`Open/Read/Close/Stat`) | disassembly-verified; prologue identity checks in the mod |
| 5 GHz AP script (`bcmdhd` + `wpa_supplicant` AP mode + `dhcpd`) | written, POSIX-checked; **not yet run on a camera** |
| Reference PC client (`tools/research/wifisync_client.py`) | end-to-end tested against the host server (62 checks) |
| Physical camera validation (Phase 1/2 gates) | **open** — needs one supervised session |

## Files

| File | Role |
| --- | --- |
| `wifisync_core.[ch]` | Portable, freestanding core: ring queue, discovery, Smart Single-Best, CRC-32 slice-by-4, HTTP/1.1 subset (`/status`, `/events`, `/manifest`, `/file`, `/ack`), Range, power state machine. No libc, no heap. |
| `wifisync_mod.c` | in-`im.elf` adapter: raw syscalls, Sony Fsys backend with identity checks, `ConPutWriteStill` interposition, WL_REG_ON GPIO ioctl, `/tmp_bt/wifisync/ap.sh` control, SMOD lifecycle. |
| `wifisync_hook.S` | 12-byte `ConPutWriteStill` interposition trampoline (calls the counter, replays the prologue, resumes at `+8`). |
| `wifisync_ap.sh` | Canonical camera-side AP script (RAM-only, `/tmp_bt/wifisync` only). |
| `wifisync.cfg` | Example `E:\mods\wifisync.cfg`. |
| `build_wsl.sh` | Cross-build + pack (`SMOD v2`, `--bss 311296`, RLC1 for `libmpr.so`). |
| `build_checks_arm_wsl.sh` | Builds the Cortex-A5 check binary used by Unicorn. |
| `wifisync_host.c` | x86-64 reference server (same core, real sockets/files). |
| `test_wifisync_host.py` | Protocol matrix over TCP (discovery, Smart rule, resume, ack, events, burst). |
| `test_wifisync_unicorn.py` | Runs the core on Cortex-A5 Thumb in Unicorn + strict `.mod` packaging checks. |
| `test_checks_host.c`, `wifisync_checks.c` | 72 portable assertions shared by both runners. |

## Build

```bash
wsl.exe --exec bash mods/wifisync/build_wsl.sh artifacts/tests/wifisync-build
```

The output is `artifacts/tests/wifisync-build/WiFiSync.mod` (29,672 bytes,
SHA-256 `8fcbb90ece61da70b565e045483cf0a2687432643ba88ec2424ce7f6b90b930a` for
the receipt in this commit) plus `pack.json`, the disassembly and the symbol
map. The build enforces: no absolute/GOT relocations, no libc/`__aeabi_*`
helpers, a single RLC1 word (the hook resume address), and an exact `--bss`
derived from `sizeof(struct ws_mod_state)`.

## Install (RAM-only)

1. Copy to the card root:
   - `WiFiSync.mod` → `E:\mods\WiFiSync.mod`
   - `wifisync.cfg` → `E:\mods\wifisync.cfg` (edit SSID/PSK first)
   - optional `wifisync_ap.sh` → `E:\mods\wifisync_ap.sh` (overrides the embedded script)
2. Install/load Mod Loader v2 as usual (`tools/research/install_mlv2.py` or the
   Mod Manager GUI). The `.mod` is RAM-only; a camera restart unloads it.
3. Camera menu → tab 7 (`Mods`) → `WiFiSync` → press center to switch **ON**.
   The first ON takes a few seconds (module load + AP start).
4. Join `A6400-5G-SYNC` from the phone/PC and open `http://192.168.122.1:8765/status`.

**Turn WiFiSync OFF before powering the camera off.** OFF drops the AP,
unloads `bcmdhd` and drives WL_REG_ON low.

## Protocol (TCP `:8765`, no TLS — WPA2-CCMP protects the link)

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/status` | GET | JSON: `camera_id`, `fw`, `queue_len`, `queue_head`, `ack`, `power_state`, `ap_chanspec`, `ap_width`, `captures`, `files_served`, `errors`, `uptime_ms`, `next_index`, `discovering` |
| `/events?since=N` | GET | Long poll (20 s): returns immediately when a scan enqueues a photo (`"woke":true`) |
| `/manifest?since=N` | GET | Bounded page of unacknowledged files (`seq_id`, `name`, `ext`, `size`, `mtime`, `crc32` or `null`) |
| `/file/<seq>` | GET | Stream with `X-File-Size`, whole-file `X-CRC32`, `Accept-Ranges: bytes`; `Range: bytes=<start>[-<end>]` returns `206` |
| `/ack/<seq>` | POST | Body `{"size":N,"crc32":"hex"}`; verifies, advances the watermark, idempotent |

The PC client resumes with `Range: bytes=<.part size>-`, verifies size + CRC-32,
renames atomically and only then POSTs the ack.

## Smart Single-Best

One DCF stem (`DSC0xxxx`) produces at most one transfer:

| Camera wrote | Queued |
| --- | --- |
| `.JPG` only | `DSC0xxxx.JPG` |
| `.ARW` only | `DSC0xxxx.ARW` |
| `.JPG` + `.ARW` | `DSC0xxxx.ARW` only |

A late `.ARW` supersedes an already queued `.JPG` of the same stem; the
superseded record is skipped by the ack watermark.

## Discovery model (no directory API)

The mod reads the card through Sony's **read-only Fsys API** (`Fsys_Open` flag
`1`, `Fsys_Read`, `Fsys_Close`, `Fsys_Stat`) and therefore does not enumerate
directories in v1. Instead it probes `DSC0<index>.ARW`/`.JPG` with `Fsys_Stat`:

* first sweep: `first_index` (default 1) + `discover_span` (9999), 128 probes
  per maintenance tick, stopped after 512 consecutive misses once a run was
  found;
* steady state: one bounded forward window (`probe_forward`, default 8) ahead of
  the last known stem — misses never move the cursor, so an idle camera does not
  walk the range;
* a capture event adds one backward resync window (`probe_back`) to catch a
  numbering reset;
* files under `min_arw`/`min_jpg` are ignored, and probing pauses while a burst
  is being written (`last_capture < 300 ms`).

Directory enumeration through `Fsys_FindFirst/Next` is the next contract to
verify; the DTA layout and the third argument are not qualified yet, so v1 does
not guess them.

## Power states

| State | What happens |
| --- | --- |
| `OFF` | AP down, `bcmdhd` unloaded, WL_REG_ON low |
| `ACTIVE` | AP up; `wl pm 0` |
| `STANDBY` (`standby=low`) | `wl pm 2`, AP stays associated, wake < 200 ms |
| `STANDBY` (`standby=deep`) | AP down + WL_REG_ON low; needs the Phase 2.5 BLE wake (not implemented) |

`ACTIVE -> STANDBY` after `idle_sec` (default 45 s) with an empty queue. A new
capture or a request forces `ACTIVE`.

## Tests and receipts

```bash
# 72 core assertions on x86-64 (real files/sockets in the matrix below)
wsl.exe --exec bash -lc "cd '/mnt/c/.../a6400 project' && \
  gcc -std=c11 -Wall -Wextra -Werror -O2 -Imods/wifisync \
  -o artifacts/tests/wifisync/host/wifisync_checks \
  mods/wifisync/wifisync_core.c mods/wifisync/wifisync_checks.c \
  mods/wifisync/test_checks_host.c && \
  ./artifacts/tests/wifisync/host/wifisync_checks"

# end-to-end protocol matrix (62 checks) -> artifacts/tests/wifisync-host-matrix/receipt.json
wsl.exe --exec bash -lc "cd '/mnt/c/.../a6400 project' && python3 mods/wifisync/test_wifisync_host.py"

# Cortex-A5 Thumb + packaging (93 checks) -> artifacts/tests/wifisync-unicorn/receipt.json
.\venv\Scripts\python.exe mods\wifisync\test_wifisync_unicorn.py artifacts\tests\wifisync-build --no-build

# Phase 0 firmware contracts (22 checks) -> artifacts/analysis/wifisync-contracts/manifest.json
.\venv\Scripts\python.exe -m tools.research.wifisync_contracts
```

## Safety

* No `/dev/mem`, no flash access, no SD mount; WL_REG_ON uses the `/dev/gpio`
  ioctl only.
* All writes are under `/tmp_bt/wifisync/`; the SD card is read-only through
  Fsys and only `A:/mods/wifisync.cfg` / `A:/mods/wifisync_ap.sh` are read.
* The capture hook is installed by the loader only while the mod is ON and is
  reverted on OFF.
* Identity checks fail closed: a different `libmpr.so`/`libInfraFsys.so` build
  (size, first 4 KiB FNV, function prologues) leaves the mod OFF.

## Known limits

* The 5 GHz AP has not been run on hardware yet; the script logs
  `5g_unavailable` and refuses unless `allow_2g=1` if `wl bands` does not
  expose the `a` band. That gate is the Phase 1 Go/No-Go.
* `Fsys` calls run from the worker thread with shared glibc TLS (the clone does
  not set `CLONE_SETTLS`); only read-only Fsys entry points are called.
* Numbering across DCF folders (`100MSDCF` → `101MSDCF`) is not followed;
  set `folder=` for a different folder.
* BLE wake-on-demand (Phase 2.5) is not implemented; `standby=deep` is therefore
  only usable with the phone retrying.
