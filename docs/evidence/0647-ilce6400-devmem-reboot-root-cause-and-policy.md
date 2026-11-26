# E0647 — Why a single-page `/dev/mem` read rebooted the ILCE-6400, and what may be read

Date: 2026-11-19. Static analysis of the live kernel Image plus read-only `/proc` and `/sys`
reads on the connected camera, after a single-page `/dev/mem` read rebooted the camera. No `/dev/mem` access was made
for this record.

## Result

1. **Exact faulting access [C, camera].** Sony's kernel log survives the warm reboot in the
   reserved `klog` buffer (`0x035A5000`, `/proc/klog`). Its exception record names:
   - program `/tmp_bt/_ro_page`, PID 1840, CPU 1;
   - only one `/dev/mem` mapping at the time, `r-- 0x000f8000 mem`;
   - `external abort on non-linefetch (0x008)`, then `Entering exception monitor.`, then
     `WARMBOOT`.

   The reboot was caused by reading PA **`0x000F8000`**. The `0x00000000` page was mapped,
   read and unmapped before it without a fault.
2. **The kernel does not guard `/dev/mem` [C, static].**
   - **No `STRICT_DEVMEM`:** there is no `devmem_is_allowed` in the symbols.
   - **`valid_mmap_phys_addr_range`** (`0x601180EC`) accepts any range below 4 GiB.
   - **Sony's attribute check** `phys_mem_access_prot_allowed` (`0x6011F550`,
     `arch/arm/mach-cxd900x0/mmap.c`) classifies the address with `arch_phys_to_cachetype2`:
     17 cached RAM, 18 uncached RAM or SRAM `0xFE000000-0xFEFFFFFF`, 19 straddling, 0
     device. It acts on conflicts only when `mmap.debug != 0`: 1 warns, 2 refuses, and any
     other value calls `__bug_noarg`. `mmap.debug` lives in BSS (default 0).
     `/sys/module/mmap/parameters/debug` reads **0** on the camera, so the check is off.
   - **`phys_mem_access_prot`** (`0x601182C8`): a page in a present sparsemem section becomes
     Normal non-cacheable with `O_SYNC` and **cacheable** without it; anything else becomes
     Strongly-ordered.
   - **`read_mem`** goes through `valid_phys_addr_range` (`0x601180B0`, up to `high_memory`)
     and the kernel linear map, so a bad address there faults in kernel mode.
3. **The hardware is the limit, and one bus error reboots [C].**
   - **BAM is not involved:** the bus access monitor is off (`/proc/bam`: `BAM: off`).
   - **Where the abort comes from:** the interconnect or memory controller answers PA
     `0x000F8000` with an external abort. That first MiB is withheld from Linux
     (`memrsv=1M@0`) and is not plain DRAM for CPU reads [C for `0xF8000`; the rest of the
     MiB is unknown].
   - **No SIGBUS fallback:** Sony's `em_exception_monitor` handles the user-mode synchronous
     external abort as fatal (warm reboot) instead of sending SIGBUS. A failed read cannot
     be contained.
4. **Why QEMU passed [C].** The board model backs the whole first MiB with RAM, and the same
   kernel in QEMU also has `mmap.debug = 0`.
5. **Earlier incidents match the same rule [I].**
   - **Earlier reboots:** a bulk 16 MiB `/dev/mem` scan (E0314 note) and a `/dev/stream`
     read of OSD DRAM pages (E0297) both rebooted the camera.
   - **Successful reads:** a single MMIO register page (`0xF2915000`, E0643) worked, as did
     ptrace reads of the same OSD DRAM through `im.elf`'s own mapping (E0298).
   - Their klog records are gone, so their exact faults are not established.
6. **Unrelated older crash in the same buffer [C].** Before this session's boot, the previous
   warm-boot segment contains an Oops by `liro-kliro_101` (av-cam), `Illeagle Instruction at
   0x63907870`, 2.4 s after resume. Not caused by this work; noted for the av-cam track.

## Policy (`tools/devmem_policy.py`)

| Level | Physical range | Why |
|---|---|---|
| ALLOWED | `0xF2915000` (MISCREG/eFuse ID) | read OK on the camera (E0643) |
| GO (per-address user go-ahead) | other pages inside the kernel's static device map: `0xF0000000-0xF4FFFFFF`, `0xF5000000`, `0xF6000000`, `0xF8000000`, `0xF9000000` (1 MiB each), `0xFA000000-0xFDFFFFFF`, `0xBF800000`, `0xBFC00000` | decoded by hardware the kernel itself uses, but registers may have read side effects (FIFO pop, read-to-clear) |
| GO | SRAM `0xFE000000-0xFE0FFFFF` (SMP mailbox page `0xFE005000`) | the kernel writes it through its static map; not yet read from user space on the camera |
| FORBIDDEN | reserved first MiB `0x00000000-0x000FFFFF` | `0x000F8000` proven fatal |
| FORBIDDEN | DRAM `0x00100000-0x3FFFFFFF`, `0x80000000-0x8FFFFFFF` | precautionary: an alias with different attributes, and DRAM reads via `/dev/stream` and bulk `/dev/mem` rebooted. Use the owner's mapping (process memory / ptrace) |
| FORBIDDEN | SRAM window tail `0xFE100000-0xFEFFFFFF`, every other address | not decoded or backing unknown: a bus error means a reboot |
| FORBIDDEN | multi-page ranges, unaligned addresses, `read()`/`write()` on `/dev/mem`, `/dev/kmem`, `/dev/stream` | bulk risk; kernel-mode fault path |

Always safe and preferred:
- `/proc` and `/sys` (including `/proc/klog`, `/proc/wdt`, `/proc/l2x0`, `/proc/iomem`);
- process memory through the debugger (`OP_MEM`, ptrace), which uses the owner's mapping.

## Inputs

| Item | SHA-256 |
| --- | --- |
| Kernel Image (live, same as QEMU) | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` |
| `/proc/klog` capture | `5b32e18f20c32a92723bbcf23e405adc2116b75a55ecdaa5e1adb2f8ab2de973` |
| `/proc/bam`, `/proc/wdt`, `/proc/l2x0`, `/proc/tmonitor`, `/proc/uptime` | see `artifacts/tests/devmem-incident-1/` |

Commands: `arm-linux-gnueabi-objdump -D -b binary -marm --adjust-vma=0x60108000` over the
functions named above. Camera reads: `tools.camera_debugger` `read_all` on `/proc/klog`,
`/proc/bam`, `/proc/wdt`, `/proc/l2x0`, `/proc/tmonitor`, `/proc/uptime`, plus `exec` reading
`/sys/module/mmap/parameters/debug`. Camera receipts: uptime monotonic across the reads
(494 → 618 → 680 s), and the debugger answers.

## Follow-up: emulation and the reboot path (QEMU, same day)

### Why an uncaught user abort reboots [C, static]

The path follows `do_DataAbort` (`0x6010854C`) through the live handler table at
`0x604CE710` (`.data`, 16-byte entries):
1. FSR `0x008` maps to entry 8, which is `do_bad` with SIGBUS, "external abort on
   non-linefetch".
2. `arm_notify_die` (`0x6011174C`) then decides: in user mode it calls `force_sig_info(SIGBUS)`;
   in kernel mode it calls `die()`.
3. Sony's monitor is the exported `exception_check` hook (`0x604EDA80`, set to
   `em_exception_monitor` by `__boottime_em_module_init`). It is called only from `die()` and
   `do_coredump()`.
4. SIGBUS's default action is a core dump. So an uncaught user abort leads to `do_coredump`,
   then the monitor, then WARMBOOT. A process that handles SIGBUS never reaches the hook.

### QEMU model of the camera behaviour [QEMU]

- **The option:** `-M sony-a6400,lowmem-abort=on` (`LOWMEM_ABORT=1`, QEMU SHA-256
  `01f417dd…500b`) answers PA `0x000F8000-0x000F8FFF` with `MEMTX_DECODE_ERROR`.
  - The camera's FSR has ExT = 0 (Cortex-A5 AXI DECERR, no slave decodes the address).
    `MEMTX_ERROR` (SLVERR) would give `0x1008`, as the first build did.
  - The option clears `ignore_memory_transaction_failures` on each CPU and maps a whole-space
    unimplemented device at priority −1000, so every other unassigned access still reads 0.
- **Reproduction** (`artifacts/tests/lowmem-abort-repro-2`):
  the original `ro_page` read `0x0` OK, then `0x000F8000`, giving `external abort on
  non-linefetch (0x008)`, the mapping `r-- 0x000f8000 mem`, and `Entering exception monitor.`.
  This is the camera's record. The QEMU guest then stops in the monitor instead of
  warm-booting.
- **Caught variant** (`--catch`, `artifacts/tests/lowmem-abort-catch-1`): `ro_page` v2
  (SHA-256 `8c9f360b…5c64`, `sigsetjmp` plus SIGBUS/SIGSEGV handlers, unbuffered stdout)
  reports `0x000f8000 bus error caught: signal 7`. No monitor runs, and the shell answers
  afterwards (uptime 49 → 206 s).
  - Physical confirmation needs an explicit go-ahead.
  - Imprecise aborts (FSR `0x406`) and kernel-mode faults (`read()`, `/dev/kmem` of the same
    range) still go through `die()` and cannot be caught this way.

### Camera confirmation: a caught bus error does not reboot [PHYSICAL, user go-ahead]

The user approved one read of PA `0x000F8000` with `ro_page` v2.
Receipt: `artifacts/tests/camera-sigbus-1/result.json`.

| Step | Observation |
|---|---|
| Before | ping `pid=703`, uptime 3,198.8 s, `/tmp_bt` without helper |
| Push | `/tmp_bt/_ro_page`, 529412 bytes, SHA-256 `8c9f360b…5c64`, read-back verified |
| Run | output `[RO_PAGE] 0x000f8000 bus error caught: signal 7 code 0 at word 0`, `RC=0`; the kernel log has `Unhandled fault: external abort on non-linefetch (0x008) at 0x040a0000` (the user VA of the mapping) |
| After | helper removed; uptime 3,234.2 → 3,243.9 s (no reboot); ping `pid=703`; no new exception-monitor record in `/proc/klog` |

What this establishes:
- **Same fault as the crash:** it is the same precise AXI decode error (FSR `0x008`).
- **Signal handling decides the outcome:** a process that handles SIGBUS survives it on the
  camera, as the static path predicts.
- **Policy update:** `tools/devmem_policy.py` gains `catching=True`. With it, the reserved
  first MiB and undecoded holes become per-address GO instead of FORBIDDEN. DRAM stays
  FORBIDDEN, and `read()` on `/dev/mem` stays FORBIDDEN.
- **Not covered:** imprecise aborts and kernel-mode faults still reboot.

## Next boundary

- **Bound the first-MiB window:** only by explicit per-address go-ahead, one page per boot,
  starting far from `0xF8000`.
- **QEMU fidelity:** model the external abort opt-in (`lowmem-abort=on`) instead of backing the
  faulting page with RAM.
