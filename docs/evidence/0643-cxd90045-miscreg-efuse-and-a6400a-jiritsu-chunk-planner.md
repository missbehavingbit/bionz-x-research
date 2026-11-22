# E0643 - CXD90045 MISCREG/eFuse decode, physical MSC read, and ILCE-6400A Jiritsu RAM-chunk planner

Date: 2026-11-19. Combines offline disassembly of preserved `CXD90045` boot/kernel/service images, one authorized read-only single-page `/dev/mem` observation on the connected `ILCE-6400` in Mass Storage, and an offline `plan-chunk-dump` extension to `tools/research/a6400a_jiritsu_bootstrap.py` (no `ILCE-6400A` USB session, no firmware flash write, and no persistent camera change).

## Result

1. **No kernel or second-stage signature check in the analyzed `ILCE-6400` / `ILCE-6400A` binaries.**
   Neither `ILCE-6400` `DGU5` (`nflashaB0`), `ILCE-6400` main kernel (`8.2.6.0`, 24,600 symbols), nor `ILCE-6400A` 1.01 main kernel (`8.2.7.6`, 24,514 symbols) contains an `efuse`, `otp`, `rsa`, `trustzone`, `tz_`, or `smc` driver or cryptographic kernel signature verification. Any hardware OTP/eFuse root-of-trust verification on `CXD90045` is bounded to the masked SoC BootROM before handoff to `nflashaB0` (`0xFE047000`).
2. **`CXD90045` `MISCREG`/eFuse and `ASV` MMIO addresses decoded from `DGU5` and physically verified on `ILCE-6400`.**
   Thumb-2 disassembly of `nflashaB0` (`0xFE04D59A..0xFE04D720`) resolves `0xF2915000` (`EFUSE_CHIPID` / `EFUSE_TYPEID`), `0xF2915010` (`MODEREAD`), SRAM `0xFE040600` (`MIS` parameter block), and `0xF2927000` (`ASV` voltage-binning register). A bounded single-page (`4 KiB`, `O_RDONLY | PROT_READ`) `/dev/mem` read on the live `ILCE-6400` returns `0xF2915000 = 0x00000500` and `0xF2915010 = 0x0c01002b` (chip/type/mode IDs only, readable from Linux, with `0xF2915004..0xF291500C` and `0xF2915014..0xF291503C` zero).
3. **`ILCE-6400A` 1.01 `libJiritsu.so` synchronous `dlopen` and `tmpfs` OOM root cause + offline chunk planner.**
   Disassembly of `adj_cmd_set_file2` (`0x5A68..0x5D26`) in `ILCE-6400A` 1.01 `libJiritsu.so` confirms `dlopen("/tmp/jiritsu/libJit.so", RTLD_LAZY)` at `0x5D08` and `dlclose()` at `0x5D20` execute synchronously on the `im.elf` thread. Staging full partitions or large buffers in RAM `tmpfs` exhausts memory and triggers `im.elf` OOM / hardware watchdog (`wdt.timeout=500`) resets; re-uploading `libJit.so` in the same power cycle spawns competing background loops. `tools/research/a6400a_jiritsu.py` and `tools/research/a6400a_jiritsu_bootstrap.py --mode plan-chunk-dump` now generate bounded single-slot `<= 512 KiB` RAM-chunk plans (including `/dev/nflashaB0` and `/dev/nflashaB1`) and an asynchronous polling worker script strictly offline.

## 1. Inputs and identities

| Input | Bytes | SHA-256 | Role |
| --- | ---: | --- | --- |
| `ILCE-6400` `nflashaB0-4194304.bin` (`E0414`) | 4,194,304 | `3db81064bbb62f2af3937bc68adac7b37482a8f3a582734cb2036fd5a3f4d260` | `EXBL` + `DGU5` `V85.00.03` bootloader |
| `ILCE-6400` `live-kallsyms.json` (`E0415`) | 2,547,031 | `edfab49ef7c933f1ea45d62c6f61c2dfafa93f14b05d7c6660d8899e000cbc02` | Decoded symbols for live `vmlinux.bin` (`d7a9ae09…`) |
| `ILCE-6400A` 1.01 `nflasha3_unpacked/vmlinux.bin` | 4,117,760 | `9d51ab60df8246f10cb2afb12fb504d7f148fae437ee404f5b9837cc3b8f85ca` | Sony CE Linux `8.2.7.6` main kernel (`E0506`) |
| `ILCE-6400A` 1.01 `nflasha15_unpacked/lib/libJiritsu.so` | 162,568 | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` | `Jiritsu` service handler (`E0554`) |
| Freestanding ARM helper `ro_devmem_tiny` | 1,244 | `6750a05b8e85ccf4fb0243f85bcae465f3d63e38bf1900df5085ec52d9425242` | Temporary `/tmp_bt/_ro_devmem`, `O_RDONLY` + `PROT_READ` `mmap2`, removed after single read |

Cross-check of `ILCE-6400` (2.00 package) vs. `ILCE-6400A` (1.01 package) `nflasha3` boot payloads also shows byte-identical `ldr_drv.bin` (`d5cec3780a459f49…`), `dfe_app.bin` (`8f9a8d3f8e270d95…`), `dfe_dat.bin` (`703f457a480eee74…`), `lif_app.bin` (`5e1ebaa18e498e32…`), `bonobo.bin` (`a9e739bc28aa8431…`), and `dat.crypterName: CXD90045`.

## 2. `DGU5` `MISCREG`/eFuse decode and live `ILCE-6400` read

- **Static decode (`arm-linux-gnueabi-objdump -M force-thumb` on `nflashaB0`):**
  - Non-zero data in `nflashaB0` ends at `0x272EB`; no trailing cryptographic signature block follows `EXBL`.
  - Function `0xFE04D59A` reads `0xF2927000 & 0x1F` (`LDR:ASV val:0x%08X(@0x%08X)`).
  - Function `0xFE04D5E0` (`LDR:read MISCREG`) copies `0x200` bytes from SRAM `0xFE040600` (`0xFE04D604`). If byte `+0` is `!= 0xFF`, it takes `EFUSE_TYPEID = byte[+1] << 8`, `EFUSE_CHIPID = byte[+0]`, and reads `MODEREAD` from MMIO `0xF2915010` (`0xFE04D63E`); otherwise (`0xFE04D702`), it sets `EFUSE_TYPEID = 0x700` and reads `EFUSE_CHIPID` from `0xF2915000` (`uxtb`, `0xFE04D712`).
- **Live `ILCE-6400` read-only observation (MSC, `tools.camera_debugger`):**
  - Initial `dd if=/dev/mem bs=4096 count=1 skip=993557` failed with `dd: /dev/mem: Bad address` (`EFAULT`) because ARM Linux `read_mem()` only translates `System RAM` in `/proc/iomem`.
  - Single-page `mmap2` (`O_RDONLY | O_SYNC`, `PROT_READ`, `4096` bytes at `0xF2915000`) via `/tmp_bt/_ro_devmem` succeeded and was immediately removed:
    - `0xF2915000`: `0x00000500`
    - `0xF2915010`: `0x0c01002b`
    - `0xF2915004..0xF291500C` and `0xF2915014..0xF291503C`: `0x00000000`
  - Post-check camera receipts: `/proc/uptime` monotonic (`4897.90` → `4942.00` → `5009.50` → `5175.60`), debugger `ping` responsive (`PONG protocol=2 nonce=0xa6400001 pid=700 transport=usb_extcmd`), `/tmp_bt` clean.

## 3. `ILCE-6400A` `libJiritsu.so` execution constraint and RAM-chunk planner

- Disassembly of `adj_cmd_set_file2` (`0x5A68..0x5D26`) in `ILCE-6400A` 1.01 `libJiritsu.so` shows that after writing `/tmp/jiritsu/libJit.so`, the handler calls `dlopen("/tmp/jiritsu/libJit.so", RTLD_LAZY)` at `0x5D08` and `dlclose()` at `0x5D20` synchronously before returning.
- Updated `tools/research/a6400a_jiritsu.py` and `tools/research/a6400a_jiritsu_bootstrap.py`:
  - `STORAGE_PATH` now allows `/dev/nflashaB0` and `/dev/nflashaB1` while retaining `/dev/nflasha23` rejection (`EXCLUDED_STORAGE`).
  - `chunk_read_command` prepends `busybox rm -f {destination}` before `dd` + `sha256sum` into a single reused `{ram_dir}/chunk` path (`<= 512 KiB`).
  - `plan_partition_chunk_dump` and `--mode plan-chunk-dump` generate bounded chunk plans (`A6400A_DUMP_PARTITIONS`) and `ephemeral_chunk_worker_script` (asynchronous polling worker for `/tmp/jiritsu/run.sh`, `/tmp_network` `tmpfs` verified).
  - Live camera modes (`check`, `start-ram-debugger`, `jiritsu-dump`) remain closed offline (`exit 2`, `JiritsuUnverified`) until live `ILCE-6400A` `GET_FILE` framing is verified.

## 4. Verification

```powershell
python -m tools.research.a6400a_jiritsu_bootstrap --mode plan-chunk-dump --partition /dev/nflashaB0 --report artifacts\tests\e0643-check\chunk-plan.json
python -m unittest tests.test_a6400a_jiritsu -v
python -m tools.research.ci_check --summary
```

- `tests.test_a6400a_jiritsu` and CI guardrails pass; `plan-chunk-dump` stays strictly offline and writes no camera or flash state.

## 5. Supervised ILCE-6400A temporary-worker follow-up

Date: 2026-11-19 UTC. This is a separate follow-up to the original offline
planner above. **No new raw partition bytes were acquired.** The owner
explicitly permitted temporary Jiritsu service-workspace writes, RAM staging,
and temporary rebinding to the existing camera-specific WinUSB package.
Firmware installation, settings changes, raw storage writes and
`/dev/nflasha23` remained excluded.

### Implemented contract

The [worker controller](../../tools/research/a6400a_jiritsu_worker.py) and
[chunk client](../../tools/research/a6400a_jiritsu.py) use ordinary Jiritsu
file transfers, not a serial/terminal dump:

1. Verify fresh native model/physical-port receipts, system version
   `38117_03.2026041003`, and the actual `libJiritsu.so` SHA-256
   `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff`.
2. Admit only the reviewed `054C:0336` primary endpoints. The experimental
   client uses the preserved live legacy GET_FILE profile: outer function
   `0x40`, category `0x1001`, command `8`, selectors `01 00 00`, version-3
   replies and a leading status word. It does not enable generic FF01 reads,
   replace the older unverified packet maps, or qualify another body.
3. Upload/read back the small script, then load the authored constructor once.
   The loader forks/executes the script and returns promptly in its parent.
   It now preserves r7 and eight-byte stack alignment. Local symbol binding
   keeps its constructor from being interposed by a process-global symbol.
4. Require a nonce-tagged ready file, root UID fields and a demonstrated RAM
   parent before acquisition. Persistent, unknown and conflicting parent
   mounts are refused. Linux `rootfs`/`ramfs`/`tmpfs` are explicit RAM types;
   this does not establish that a path hidden by an ext2 overmount is RAM.
5. Cross-check live `/proc/partitions` and sysfs sector capacities. Stage at
   most one 512-KiB data file, pull it via USB, compare its camera/host hash,
   durably append it on the host, and remove it before the next request.
   Data staging size is not a bound on total process, COW or DMM memory.
6. Preserve partial/unverified output and retire an unresolved USB channel.
   The control-file commit is consumed before execution; stale output cannot
   authorize another chunk. A low/unknown MemFree result fails explicitly.
   `dd` errors remain visible, while successful diagnostic text cannot be
   mistaken for the checksum.

Default camera entry remains closed. `check`, `jiritsu-dump` and
`cleanup-worker` additionally require the explicit
`--owner-authorized-jiritsu-workspace` policy, firmware confirmation and fresh
receipts; dumping/cleanup also require the authored loader manifest.
`cleanup-worker --owned-receipt ...` performs no loader upload and deletes
only identity-matched owned files.

### Actual camera observations and preserved failures

Receipts remain under
`artifacts/analysis/a6400a-jiritsu-worker-20261119/`; proprietary inputs and
generated payloads are not committed.

| Attempt | Observation | Boundary |
| --- | --- | --- |
| `chunk-capture-01` | Version/library checks and script/loader readback passed; no ready marker | Initial script depended on unqualified `awk`; its precise failure was not observed |
| `chunk-capture-02` | Nonce-tagged startup log proves authored shell execution | Script reported `RAM filesystem refused: rootfs`; no inventory or partition read |
| `chunk-capture-03` | Startup log later recovered during cleanup | `MountCandidate: / rootfs`, `MountCandidate: / ext2`, then `Ambiguous parent mounts refused`; ready request sequence 16 was acknowledged but no reply header arrived |

The third attempt did not assume that the first rootfs entry described the
active mount. It sent no speculative abort/STOP on the unresolved channel.
It did not obtain root-UID readiness, a live capacity inventory or a raw chunk.
None of these failures is promoted to proof of OOM, a watchdog reset or
complete helper termination.

The final implementation additionally emits a nonce-tagged initialization
error and idles on a refused start rather than deliberately ending the child
before the service closes. The host recognizes that explicit failure without
treating it as a ready worker. **That post-attempt hardening was tested only
offline and was not uploaded again.** Upstream
[ZV-E10 lifetime observations](https://github.com/dino2048/zv-e10-toolkit/blob/43f1e662c6f8da38d9aaf1dde3eac0c6e0d6238e/linux/docs/jiritsu.md)
are a supporting lead, not a verified explanation of the 6400A timeout.

### Inputs, commands and verification

The freestanding helper source SHA-256 is
`190b148918e9cd3ae6f31847c0d96e26a305d9624dcf015c4ed9f6e888f52675`.
The locally bound 5,296-byte loader is
`7a792398148217342962b36b2c844c04ac4cc84541ccac287e96a959aba0c535`.
Attempt 3's 2,922-byte uploaded script is
`3420ea473960b8d7e7a9879c347321adf978004ff962a74bd56a27b6902fcac2`;
its exact failed version is preserved separately from subsequent source edits.

Tool identities: Windows Python 3.14.4, Unicorn 2.1.4, pyelftools 0.33,
Capstone 5.0.9, PyUSB 1.3.1, and the existing default WSL
`REA-Ubuntu-24.04` ARM GCC 13.3.0
(`Ubuntu 13.3.0-6ubuntu2~24.04`). No dependencies were installed.

Executed from the repository root:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode build-loader --output-dir artifacts\analysis\a6400a-jiritsu-worker-20261119\loader-build-02
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode jiritsu-dump --owner-authorized-jiritsu-workspace --confirm-firmware-101 --permit-control-pipe --baseline artifacts\analysis\a6400a-jiritsu-worker-20261119\host-preflight-04\receipt.json --identity-receipt artifacts\analysis\a6400a-jiritsu-worker-20261119\native-identity-04\receipt.json --loader-manifest artifacts\analysis\a6400a-jiritsu-worker-20261119\loader-build-02\manifest.json --partitions all-allowed --output-dir artifacts\analysis\a6400a-jiritsu-worker-20261119\chunk-capture-03
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_jiritsu tests.test_a6400a_jiritsu_packets tests.test_a6400a_fixed_acquire -q
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

- Loader build/constructor controls: **passed**. Fork success/failure return
  with preserved registers/stack; the constructed child closes descriptors
  and reaches execve. These syscall fixtures are not a physical execution
  receipt or a timing model. Original fixed-upload controls also pass with
  explicit DMM/libc/mount/loader fixtures.
- Focused tests: **62 run, 1 skipped, no failures or errors**.
- Generated shell exercised against declared Linux filesystem/process
  fixtures: **1,052,672 bytes** in **524,288 / 524,288 / 4,096** byte chunks,
  maximum observed data file **524,288 bytes**, saved hash
  `e3628f81e3df28fd371af9c9507567ded049e0bd3ec0eebac4bd81429e056faf`.
  Duplicate, persistent-mount and ambiguous-mount starts are refused. Those
  fixtures do not access camera/USB devices or real block sources.
- Full suite: **2,159 run, 35 errors, 206 skipped; not green**. All 35 retained
  error blocks are emulator `UC_ERR_NOMEM` allocation failures. No guard or
  test was weakened to hide them. Python compilation also passes.

The Linux RAM-filesystem classification is grounded in
[Linux v3.0 ramfs/rootfs implementation](https://github.com/torvalds/linux/blob/v3.0/fs/ramfs/inode.c)
and [kernel documentation](https://docs.kernel.org/filesystems/ramfs-rootfs-initramfs.html#what-is-rootfs).
Neither source identifies the active backing of this camera's overlapping
mounts.

### Cleanup, final recovery and next boundary

After an owner-confirmed battery-removal cycle, `owned-cleanup-01` reads and
matches the exact script/loader and the log's nonce, deletes those three owned
files and observes the stock stat-failure status afterward. That status is
not an independent errno/O_EXCL attestation. Its commands are reads, mount,
path selection and deletion: **no command 7/16 upload or new worker start**.
All earlier failed receipts remain unchanged.

The original USBSTOR binding was restored using only the already-installed,
hash-checked camera-specific packages. An additional owner-confirmed
battery-removal cycle was followed by `native-postflight-final` and
`host-final`: native inquiry/model reply **passed**, model **ILCE-6400A**,
transaction **0.203281 seconds**, **USBSTOR / OK / error 0**. Unacknowledged
STOP is not relabeled successful. No `/proc/uptime`, debugger or `/tmp_bt`
cleanliness attestation was obtained or invented.

**Next boundary:** identify the actual mount source/backing below the
overlapping root entries, or separately review a size-limited private RAM
mount under an authorized namespace. Do not remove the ambiguity check,
assume ext2 is either RAM or flash, stage a whole partition, or replay a
loader before owner recovery. Full camera firmware acquisition remains
unqualified.

## 6. Safe RAM workspace qualification

Date: 2026-11-20 UTC. The new `qualify-ram` mode is restricted to filesystem
metadata and constructed known-pattern files. **It does not read firmware
partitions.** Its temporary Jiritsu workspace writes and camera-specific driver
rebinding retain the owner's explicit scope; ordinary offline tests do not
contact USB.

### Actual backing of the earlier candidate directories

`artifacts/analysis/a6400a-ram-proof-20261119/probe-live-02/` preserves the
successful diagnostic transport, native binary frame and refusal. The
2,052-byte read-only helper is SHA-256
`18c0c312ef2b2adb319e0614d22bcfb05a28ed3cbfedef17bd643c8c79796cc1`.
Its raw ARM EABI stat64/statfs calls returned:

| Directory | st_dev | Filesystem magic | Corroborating mountinfo |
| --- | --- | --- | --- |
| `/tmp_network` | `64519` (`252:7`) | `0xEF53` (ext2) | `/dev/nflasha7` mounted at `/` |
| `/tmp_bt` | `64519` (`252:7`) | `0xEF53` (ext2) | Same root device |
| `/tmp` | `64522` (`252:10`) | `0x4D44` (MSDOS/VFAT) | `/dev/nflasha10` mounted at `/tmp` |

The process reported root UIDs. Kernel major 1 was registered as `ramdisk`,
and RAM-device sysfs entries existed, but **that did not make these paths
RAM-backed**. The boot command line's `root=/dev/ram0` describes a boot input,
not the active directory's observed device.

The existing-directory proof correctly refused before creating any data file
or sending a data acknowledgment. The diagnostic worker remained alive for
cleanup; uptime advanced from **4809.04 to 4810.32 seconds**. The owned
script, loader, diagnostic executable, metadata, acknowledgment and log were
removed through the scoped client; USBSTOR restoration was verified.
Unacknowledged STOP was not treated as complete termination.

### Explicit private tmpfs path

The owner subsequently authorized one new empty, nonce-named directory under
`/tmp/jiritsu`, a **2 MiB** private tmpfs with **nodev/nosuid/noexec**, and
emptying, unmounting and removing that owned mountpoint. This is distinct from
assuming that a directory prefix implies RAM.

The [native probe](../../tools/native_camera/ram_workspace_probe.c) accepts only
the fixed private namespace and a 32-character hexadecimal nonce. It refuses
an existing mountpoint rather than overwriting it. Its private mount uses
exactly `size=2097152,mode=0700` and mount flags `14`. The version-2 native frame
records full paths, syscall status, device number, statfs type, capacity and
flags. Before test data is authorized, the host requires successful creation,
actual tmpfs magic, exactly 2 MiB capacity, the three safety flags and
sufficient available space. The helper rechecks the binding before each phase.
Unmount uses no force/lazy flags and must precede removal of the owned directory.
Failed creation and rollback statuses remain explicit.

The [host controller](../../tools/research/a6400a_ram_probe.py) authorizes a
4 KiB pattern first, then a 512 KiB pattern only after three verified reads of
the first. Both use one data slot. Every read must have the expected byte count,
camera/host hash, independently constructed contents and independently reread
durable host output. A changed or short result stops the sequence and retains
unverified bytes. The worker records uptime across stages and remains alive on
initialization failure so the service can be closed in the reviewed order.

Use the existing
[bootstrap entry point](../../tools/research/a6400a_jiritsu_bootstrap.py) with
`--mode build-ram-probe` for the offline native build. A supervised private
test additionally selects `--mode qualify-ram --private-tmpfs`, the explicit
owner policy, firmware confirmation, fresh original-binding/native-identity
receipts, both native build manifests and a new output directory.
`--private-tmpfs` is rejected outside qualification mode; it does not enable
`jiritsu-dump` or relax its storage guards.

### Offline verification

The private helper source is SHA-256
`3b0c80c9b16b524873e913e0401e54816af62cefd84e6de22544d9b98e2b3d71`.
Its 3,056-byte executable is
`c6c95a56d4245fe9b54efed516bb596f3bcbca30509430ac52ffe02ea6856a31`.
`native-build-03/manifest.json` records the build and fourteen bounded original
helper execution controls with explicit syscall fixtures. They cover exact
mount flags/path/size, existing-directory refusal, unsupported tmpfs rollback,
binding/capacity/flag mismatches and acknowledged versus busy unmounts.
No actual host or camera mount is performed by those controls.

The generated private probe script also completes the six known-pattern reads
and cleanup against an owned Linux filesystem fixture with simulated native
mount evidence. Its maximum observed data file is **524,288 bytes**. These are
constructed results, not physical camera RAM qualification.

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode build-ram-probe --output-dir artifacts\analysis\a6400a-ram-proof-20261119\native-build-03
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_jiritsu tests.test_a6400a_jiritsu_packets tests.test_a6400a_fixed_acquire -q
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_packets --report-dir artifacts\tests\a6400a-ram-proof-packets-20261120
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

Results: native build/controls **passed**; focused suite **72 run, 1 skipped,
no failures/errors**; actual packaged Jiritsu replay **18 commands and 7
packet fixtures, USB disabled**. The recorded full-suite run is **2,241 tests,
30 errors, 225 skipped**, all retained errors on emulator allocation paths;
it is not a green full-suite result.

### Physical private-RAM result and remaining transfer gate

`probe-live-04`, `probe-live-05` and `probe-live-06` now prove actual private
RAM backing, without weakening the existing-directory rejection:

- Native exclusive mkdir and tmpfs mount return **0**.
- The nonce-named private directory has `st_dev=10`, filesystem magic
  **`0x01021994`**, block size **4,096**, and **512 blocks**:
  exactly **2,097,152 bytes**. Initially all 512 blocks are available.
- Actual statfs flags are **4142 (`0x102E`)**; the required lower flag bits
  are **14**, qualifying **nosuid, nodev and noexec**, without read-only.
- The read-only native binding recheck returns **0**.
- Cleanup's native unmount and rmdir return **0**, and subsequent directory
  stat/statfs return **-2**. The owned script, loader, native executable,
  metadata, acknowledgment and log are removed separately with byte identity
  checks. USBSTOR restoration is verified.

**Reliable data transfers remain unqualified. No test-data file or firmware
partition was read in these attempts.** The earlier broad diagnostic hit a
combined gate; instrumentation identifies the actual blocker as free memory:

| Attempt | Memory observation | Outcome |
| --- | --- | --- |
| `probe-live-04` | Combined gate; reason not separately measured | RAM backing proved, no data |
| `probe-live-05` | `MemFree=7864 KiB`, native binding exit `0` | Below the unchanged `8192 KiB` guard |
| `probe-live-06` | Five observations: `7724, 7724, 7668, 7584, 7584 KiB`; native binding exit `0` | Bounded wait did not obtain required headroom |

The compact private script stops gathering unneeded boot/RAM-device details,
but this does not establish that diagnostic overhead caused the low memory.
The five-check wait observes existing memory only: it does not drop caches,
kill processes, switch modes, lower the memory floor or fabricate readiness.
Offline shell fixtures specifically verify **8191 KiB refuses all data** and
**8192 KiB permits the six exact pattern reads**, with the same cleanup.
The 8-MiB floor is a conservative probe policy, not a measured Sony firmware
minimum or a qualified peak DMM allocation budget.

All failed receipts remain unchanged. The last session's uptime advances
from **164.37 to 170.62 seconds** through acknowledged private-mount cleanup.
After the owner's final battery-removal cycle, `host-final` verifies original
**USBSTOR / OK / error 0**, and `native-postflight-final` returns **ILCE-6400A**
in **0.204331 seconds**. STOP remains unacknowledged; no debugger or `/tmp_bt`
cleanliness attestation is invented.

The exact executed main-controller and shared-worker sources are preserved in
`compact-run-source-snapshots/`, matched to the last receipt:
`ff7dc48c322ec7d480340d0f1e22c599e8b7d999a29e5d510081324cc2a51103`
and `a3722bd4d018e4851b0ffb78bca3a230d0d7eedf49c33cf090f1bfb3b471043d`.
The unchanged-at-capture host probe and native C sources are also preserved.
No old source or receipt hash was refreshed.

### Post-run cleanup-receipt hardening

The complete wire receipts are 76,389 / 75,975 / 137,336 bytes, larger than
the existing 64-KiB metadata-input bound. The subsequent host-only change
emits a separate bounded `ownership.json`, linked by SHA-256 to the unmodified
full receipt. Future recovery selects that file with `--owned-receipt`; it
does not relax metadata admission or discard wire events. Actual probe runs
above predate that change, which was not uploaded or hardware-replayed.
New runs also record the host RAM-probe module hash explicitly.

Tmpfs can use swap; filesystem type alone therefore does not prove exclusive
RAM residency. Swap state was **not observed** in the physical attempts above.
The final host-only safeguard requires a valid, readable `/proc/swaps` table
with no active entries before authorizing data, and rechecks it before each
phase. Missing or invalid evidence is a refusal, not an inactive-swap default.
It never enables/disables swap. This guard passed constructed active/missing
and inactive-swap tests but was not hardware-replayed after final postflight.

Final host validation:

- Focused suite: **75 run, 1 skipped, no failures/errors**.
- Full suite: **2,244 run, 34 errors, 225 skipped**, all retained errors on
  emulator allocation paths; not a green full-suite result.
- Python compilation and Pylance diagnostics report no code errors.

**Next boundary:** verify inactive swap and qualify genuine memory headroom
or measure/reduce the worker and Jiritsu transfer memory footprint before
attempting known-data reads. The private mount itself is proved; that is not
proof of safe 512-KiB transfers, exclusive RAM residency or a complete
firmware dump. No further camera operation was performed after final postflight.

## 7. Native reader and first boot-area captures

Date: 2026-11-21 UTC. The owner reconfirmed **ILCE-6400A firmware 1.01**,
permitted UAC elevation and one-process `RemoteSigned` execution, and explicitly
confirmed battery-removal recovery between supervised attempts. No machine or
user execution policy was changed.

**Three new raw partition images are complete. Acquisition of the remaining
partitions is not complete.** Files remain private under
`artifacts/analysis/a6400a-nflasha-dump-20261121/live-03/capture/`.

| Camera source | Verified bytes | SHA-256 |
| --- | ---: | --- |
| `/dev/nflashaB0` | 2,097,152 | `45d2f01904eda9e330ffcbf12e8e540670fc1c3854986554861c25906fa1b14e` |
| `/dev/nflashaB1` | 2,097,152 | `5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee` |
| `/dev/nflasha1` | 8,323,072 | `0802ea53fb6ebde7af7080d12cfd78b4232ee7f6ef0f5bf8bd6c29f7171b1a76` |

`nflasha7.bin.partial` contains a verified **524,288-byte prefix**, SHA-256
`fdc846a10366d723d0dd154adaa141b07951aefa3997831d00843cf5c3a5af19`,
of its measured **8,388,608-byte** capacity. It is not renamed or padded into
a complete image. Streaming source/RAM/host checksums, closed-file readback,
the elevated wrapper's independent readback, and another final host readback
agree for all three images and the prefix.

### Contract and original-code support

The existing [controller](../../tools/research/a6400a_jiritsu_worker.py)
now has an explicit `--native-reader --private-tmpfs` path through the
[bootstrap](../../tools/research/a6400a_jiritsu_bootstrap.py). It retains
default closed entry, explicit owner policy, fresh original-driver/native-model
receipts, live version/library identity, fixed transfer bounds and all storage
exclusions. The actual library still matches
`c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff`.

Original stock packet/upload consumers remain those qualified in
[E0670](0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md)
and [E0525](0525-a6400a-jiritsu-senser-offline-packets.md). The native helper is
authored code, not recovered Sony code or an emulated kernel. Its constructor
uses the already-authored `vfork`/direct-exec path; the checker now validates
that actual path rather than incorrectly expecting the earlier `fork` ABI.

Before native reads, the host verifies the exclusive 2-MiB tmpfs, safety flags,
root identity, inactive swap and six exact known-pattern file transfers:
three 4-KiB reads followed by three 512-KiB reads. Each is durably saved and
independently compared. The verified proof file is then removed before the
first partition's memory check. Only typed `inventory`, `chunk` and `remove`
operations are admitted; arbitrary shell commands are not native-reader input.

Every raw chunk requires the unchanged **8,192-KiB MemFree floor**, live
`/proc/partitions`/sysfs agreement, a matching block-device `fstat64` identity
and matching fd capacity. Source opens are strictly read-only/no-follow.
ARM Linux headers and build assertions establish `fstat64=197`, its 104-byte
layout, and **ARM32 `BLKGETSIZE64=0x80041272`**. The earlier 64-bit ioctl value
in constructed controls was corrected before any physical raw read; those
earlier controls alone did not establish the kernel ABI.

The helper stages at most one 512-KiB file, hashes source bytes and reread RAM
bytes, then the host verifies, durably appends and removes the slot. Bounded
`POSIX_FADV_DONTNEED` advice covers only the source range just read. No global
cache drop, process killing, memory-floor reduction, physical-memory scan,
raw storage write, firmware installation or SD write is performed.

### Live geometry and retained failures

The actual boot areas are **2 MiB each**, not the offline planner's nominal
4-MiB defaults. Live capacity always overrides planning assumptions. Eighteen
nonzero nodes are observed, including excluded `nflasha23`; both boot areas
are included. The whole-device `nflasha` alias is retained in the raw inventory
but is not admitted as a bounded partition. No SD or MSC LUN is relabeled.

| Attempt | Observation | Stop |
| --- | --- | --- |
| `live-01` | Private RAM and inactive swap verified | `MemFree=4,784 KiB`; no pattern or partition data |
| `live-02` | Six actual known-pattern transfers and live capacities verified | Resident 512-KiB proof file left `MemFree=7,756 KiB`; no raw source read |
| `live-03` | Proof slot retired; three raw images and one prefix verified | Next `nflasha7` chunk refused at `MemFree=8,168 KiB`, below 8,192 |

The proof-slot ordering defect is fixed in the shared controller and covered
by host/native fixtures that charge resident test data against free memory.
The final refusal is not bypassed or converted to success. Earlier failed
receipts and executed source versions remain unchanged in
`source-snapshots-live-01.zip`, `source-snapshots-live-02.zip` and
`source-snapshots-live-03.zip`.

These are **non-atomic live raw reads**, not a factory-pristine backup.
Authorized temporary Jiritsu files reside on `nflasha6` while the worker runs;
a future image of that node must disclose those mutations. `nflasha23`,
whole-device holes and missing partitions remain unacquired. No earlier
capture is substituted for a new image.

### Tool identities and validation

- ARM GCC: `13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04)`.
- Native executable: **15,924 bytes**, SHA-256
  `11f6bdf572d1b87b4ac5fd325c8df61ab6f57c113208b7bcf0de81146d2c1dd7`.
  `-fno-ident` omits non-loadable compiler identification, keeping the
  unchanged 16,000-byte upload cap; executable/ABI metadata is not removed.
- Native C source:
  `5b62fe1c0ff6f0cdf4c244cb61df3d4683b1c3e72f279ff8f21132a414749b86`.
- Constructor: **5,500 bytes**, SHA-256
  `42640bea9c489ba4feed7f4b308dd87cca6cff26e48cb22139d28303387822d9`.
- Python: existing Windows **3.14.4** environment; no dependencies installed.

Executed from the repository root:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode build-loader --output-dir artifacts\analysis\a6400a-nflasha-dump-20261121\loader-build-01
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode build-ram-probe --output-dir artifacts\analysis\a6400a-nflasha-dump-20261121\native-build-08
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode jiritsu-dump --native-reader --private-tmpfs --owner-authorized-jiritsu-workspace --confirm-firmware-101 --permit-control-pipe --baseline artifacts\analysis\a6400a-nflasha-dump-20261121\live-03\baseline\receipt.json --identity-receipt artifacts\analysis\a6400a-nflasha-dump-20261121\live-03\identity\receipt.json --loader-manifest artifacts\analysis\a6400a-nflasha-dump-20261121\loader-build-01\manifest.json --ram-probe-manifest artifacts\analysis\a6400a-nflasha-dump-20261121\native-build-08\manifest.json --partitions all-allowed --output-dir artifacts\analysis\a6400a-nflasha-dump-20261121\live-03\capture
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_jiritsu tests.test_a6400a_jiritsu_packets tests.test_a6400a_fixed_acquire -q
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_packets --report-dir artifacts\tests\a6400a-native-dump-packets-20261121
.\venv\Scripts\python.exe -m unittest discover -s tests -q
```

Both builds exit **0**. Four constructor and twenty-two native ARM controls
pass with explicit constructed syscalls. Focused tests: **79 run, 1 skipped,
no failures/errors**. Original packaged replay: **18 commands, 7 fixtures,
USB disabled**, exit **0**. Live acquisition exits **2** with preserved partial
coverage, not success. Pylance reports no code errors.

The completed full-suite run is **2,249 tests, 33 errors, 225 skipped**; all
33 error blocks report emulator `UC_ERR_NOMEM`. A later full-suite invocation
terminates with Windows status **0xC00000FD** without a valid completion
record. Neither is reported green or hidden by a weaker test/guard.

### Recovery and next boundary

Each attempt archives its logs and byte-matched owned files before deletion.
Native private unmount/rmdir return **0**; source/log/config/acknowledgment
cleanup and original USBSTOR restoration are observed. The last worker's
uptime advances **102.84 -> 125.32 seconds** through cleanup. STOP remains
unacknowledged; it is not treated as termination.

The owner confirms the final battery-removal cycle. `final-host-postflight-01`
observes **USBSTOR / OK / error 0** at the original port, and
`final-native-postflight-01` verifies **ILCE-6400A** in **0.203332 seconds**
with one response-only BUSY wait. No debugger, `/tmp_bt` inventory, drained
DMA or retrospective successful STOP is invented. No further acquisition
is attempted after that recovery.

The remaining boundary is stable, genuinely measured memory headroom for the
remaining chunks. Preserve the three images and verified prefix; do not lower
the floor, replay an unresolved request, reuse a one-shot marker or describe
this subset as a complete firmware backup.

## 8. Read-only cache reclamation, atomic IPC and all 17 allowed partitions

Date: 2026-11-21 UTC. Without lowering the **8,192-KiB `MemFree` floor**,
changing global `/proc/sys/vm/drop_caches`, or admitting `/dev/nflasha23`,
targeted read-only `POSIX_FADV_DONTNEED` / `POSIX_FADV_RANDOM` controls and
atomic `tmpfs` response publication complete the remaining **14** allowed
partitions across supervised runs `live-04` through `live-07`.

Hard-linked consolidated raw partition set remains private under
`artifacts/analysis/a6400a-nflasha-dump-20261121/complete-allowed-set/`
(containing exclusively the **17 raw `nflasha*.bin` camera images,
1,354,694,656 bytes**; host manifests and QEMU-prepared companion outputs live
separately under `qemu-prepared-inputs/`, and original `live-03` through
`live-07` run directories and intermediate failed receipts are preserved
unmodified):

| Camera source | Verified bytes | SHA-256 | Run |
| --- | ---: | --- | --- |
| `/dev/nflashaB0` | 2,097,152 | `45d2f01904eda9e330ffcbf12e8e540670fc1c3854986554861c25906fa1b14e` | `live-03` |
| `/dev/nflashaB1` | 2,097,152 | `5647f05ec18958947d32874eeb788fa396a05d0bab7c1b71f112ceb7e9b31eee` | `live-03` |
| `/dev/nflasha1` | 8,323,072 | `0802ea53fb6ebde7af7080d12cfd78b4232ee7f6ef0f5bf8bd6c29f7171b1a76` | `live-03` |
| `/dev/nflasha2` | 20,971,520 | `45003ec2f812b7d0b8a3053506e182591af58b4b7a8a72b970615754e597f49d` | `live-06` |
| `/dev/nflasha3` | 50,331,648 | `67c2c278d2694f8d08ae507cfb452c31f16f74989b10337f98491dc9c0ba28e8` | `live-07` |
| `/dev/nflasha4` | 12,582,912 | `9acb5274eb76bfa04fa5825a324213f68e84fd2369c87fe2f0779b6f1334ba87` | `live-05` |
| `/dev/nflasha5` | 62,914,560 | `d131b73f9c4610bdc3f549a65e94d70c11cc92d3071b878d65ef46a8fe13b3b6` | `live-07` |
| `/dev/nflasha6` | 16,777,216 | `65ed63b8a7a86d1b7ec3cae429b7a999b71e5597f19dea3ca36d3f9d9feb9fac` | `live-07` |
| `/dev/nflasha7` | 8,388,608 | `186e6f914079c1fb1717fdb1d7b0ebfbe1c6be61a4a2f3af232373de53202dba` | `live-04` |
| `/dev/nflasha10` | 12,582,912 | `8597983e999c30f4beddf40d396dc21693aa7fe87457c841a08f080ef843276a` | `live-05` |
| `/dev/nflasha11` | 524,288,000 | `2ccd8e30f8734dc2b8c1c734c7fa748efbfffc2319c05f93893d025a9692be4b` | `live-07` |
| `/dev/nflasha12` | 41,943,040 | `6f45f5493a1672c865dcf354e078e4b277d5124f89e0191091c3cbf66b912b1b` | `live-07` |
| `/dev/nflasha13` | 4,194,304 | `f4beede1c3a8c9f1516c4ec91a337942f311902b70f702076320cf477cdfedd9` | `live-04` |
| `/dev/nflasha15` | 314,572,800 | `aebb1c7426357c887c214bacfc3b71c124b22dc7e074f91ea2e91078a242d747` | `live-07` |
| `/dev/nflasha16` | 146,800,640 | `a6b881004b30e6eada284149d14f2502f26bf03fac68d98307f772d0648abc56` | `live-07` |
| `/dev/nflasha17` | 104,857,600 | `3d95a28bd2ffe52a3bff38145144e4e2f068a9e39beb92fec38891110f949cb9` | `live-07` |
| `/dev/nflasha18` | 20,971,520 | `cbd81ab40a284d1960c57eb0ec942554b7f143020dba489e888902a7b0873f41` | `live-06` |

### Technical root causes and verified fixes

1. **Unmapped boot/asset page cache (`+16.1 MiB` genuine `MemFree`):**
   `live-03` showed `Cached = 50,704 KiB` vs `Mapped = 19,972 KiB` (`~30.7 MiB`
   unmapped page cache), dominated by read-once boot/asset files such as
   `/system/av-cam.bin` (`16,822,380` B) and `/usr/share/app/*.uxc`. Advising
   read-only `POSIX_FADV_DONTNEED` (`arm_fadvise64_64` syscall `270`, `advice=4`)
   on those explicit packaged paths raised live `MemFree` from `8,424..8,500 KiB`
   to **`24,564..24,660 KiB`** while leaving `Mapped` (`19,956..19,964 KiB`)
   unchanged. Adding `POSIX_FADV_RANDOM` (`advice=1`) before `lseek`/`read` and
   whole-device `POSIX_FADV_DONTNEED` (`offset=0, len=0`) after each chunk
   eliminated kernel block read-ahead accumulation: across **1,262,485,504 bytes**
   (`2,408` chunks) in `live-07`, `MemFree` ended at **`25,432 KiB`** (`Buffers`
   `492 KiB`, uptime `144.02 -> 1106.64 s` monotonic).
2. **File-IPC races under 10-ms daemon polling (`live-04`..`live-06` -> `live-07`):**
   - `live-04` completed `nflasha7` and `nflasha13`, then stopped with error `70`
     when `ram_probe.elf` observed `0`-byte `go` in the window between Jiritsu
     Command `15` (`SET_PATH` `O_CREAT|O_TRUNC`) and Command `16` (`SET_BYTES`).
     Ignoring `len == 0` on `go` resolved that race.
   - `live-05` completed `nflasha4` and `nflasha10`, then observed Jiritsu status
     `0x1000000a` when `GET_FILE` read `output` right after `open(output, O_TRUNC)`
     zeroed it. `live-06` completed `nflasha2` and `nflasha18`, then observed
     `0x10000008` when `unlink(output)` coincided with `GET_FILE` between
     `stat()` and `open()`. Writing `output.tmp`, closing it, and replacing
     `output` in-place via POSIX `rename(output.tmp, output)` (`__NR_rename = 38`,
     without unlinking `output`) plus handling transient `0x10000006` /
     `0x10000008` / `0x1000000a` in `_poll` completed all **8** remaining
     partitions (`4,819` native commands, `43,140` Jiritsu wire exchanges) in
     `live-07` with zero errors.
3. **Independent cross-validation with E0673:**
   `live-07`'s 120-chunk capture of `/dev/nflasha5` (`62,914,560` bytes) yields
   SHA-256 `d131b73f9c4610bdc3f549a65e94d70c11cc92d3071b878d65ef46a8fe13b3b6`,
   matching [E0673](0673-a6400a-first-actual-nflasha5-capture.md)'s stock
   `0601/32` fixed-reader capture bit-for-bit.

### Tool identities, limits and final recovery

- Thumb-2 native executable (`native-build-12/ram_probe.elf`): **12,032 bytes**,
  SHA-256 `33afcff910926b5b8c75e3b1bc23866b86a0485f72d99de4802b80d7d5fd5894`
  (C source SHA-256 `a7d1ef32043be225c17dece1a078e08d58b3bc081c1e014f6d9cf69f37cbbb94`;
  **14 CLI + 9 daemon = 23 Unicorn controls passed**).
- Focused offline tests: **80 run, 1 skipped, 0 failures/errors**; status sync
  and layout checks: **53 run, OK**.
- Executed source archives `source-snapshots-live-04.zip` through
  `source-snapshots-live-07.zip` are preserved alongside each run receipt.
- **Remaining provenance limits:** `/dev/nflasha23` (`EXCLUDED_STORAGE`) and
  whole-device `/dev/nflasha` (including unpartitioned gaps) are intentionally
  not read (`full_firmware_captured = false`). Captures of mounted partitions
  (especially `/dev/nflasha6`, which held temporary `/tmp/jiritsu` files during
  acquisition) are non-atomic live snapshots, not factory-pristine offline
  images.
- **Final recovery:** `live-07` verified private `tmpfs` `umount2`/`rmdir`,
  byte-matched `/tmp/jiritsu` file deletion, and `USBSTOR` restoration. After
  the owner's final OFF / battery-removal / ON cycle, `final-host-postflight-02`
  verified **USBSTOR / OK / error 0** at the original port, and
  `final-native-postflight-02` verified **ILCE-6400A / `0032858730`** in
  **0.204192 seconds**.
