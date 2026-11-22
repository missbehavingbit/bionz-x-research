# L3 User-Space (`im.elf`), OSAL/UIPC & Heap Findings

Consolidated technical reference from `E0298–E0340` and `E0473` for user-space process execution under `InterpreterProbe`.

## 1. Execution Model & Thread Scheduling

- **Entry & Argv (`E0301`)**: Physical PID 149 runs `im.elf debug s g r u l i a lazy-global`.
- **Cooperative-Scheduler Fix (`E0473`)**: `GuestThreads` previously switched only on block/yield/≥100 ms sleep, allowing non-blocking UIPC poll loops to starve other threads. Opt-in `--thread-timeslice N` enforces deterministic timer preemption across runnable threads, unlocking 88+ threads (`THREAD_INFRA0` completion, `THREAD_APP`, `Appframework`, `gui.so`, font loading).
- **Task Identity (`E0302`)**: `KernelTaskIdentityTable` tracks per-clone `pid`/`tgid`/backlinks (`0x5F000000` range) and rejects unobserved callers.

## 2. OSAL / UIPC & `MWF::ObjMsg` Protocol (`E0300–E0338`)

- **Bit-15 Queue Dispatch (`E0331`)**: `libosal_uipc` routes queue IDs with bit 15 set (e.g., `0x00CA8192` for `UsbControl`, `0x012182EB` for `UsbBusPower`, `0x012282F6` for `BusPower`) through process-local user-space queue objects (`+4 flags`, `+8 wait list`, `+0xC head = payload - 0xC`) + `FUTEX_WAKE_PRIVATE`, rather than kernel UIPC ioctls.
- **Kernel Sync Queues (`E0332–E0334`)**: `0x00CA0288` (handle `4109`, `UFW::AdjBase::funcOsalSyncCb` expecting 320-byte `UFW::OsalRequestPacket`) and `0x00CA01A4` (handle `4110`, `UsbControl::funcInputGetCb`).
- **MWF `ObjMsg` & `SETCONFIG` (`E0323–E0337`)**: `ObjMsg` carries `{body_ptr, vtable}` with `ParamMap` and typed `ParamStd<int,7>` (`funcTable` in `libMWF.so`). Inbound `SETCONFIG` (`{0x6103, 0x0A4001, param 0x1006=0}`) dispatches through `UsbControl::MsgId2EventId` → FSM event `0x13` (19) and broadcasts `0x0A4002`.
- **Original local sync-to-async replay ([E0524](../evidence/0524-original-local-osal-setconfig-route.md))**: `EventListner -> SendEvt -> osal_snd_sync_msg(0x00CA8192) -> funcOsalSyncCb -> preProcess -> PostMsg -> osal_snd_msg`. Original registration/allocator/dispatcher code owns the callback, separate envelopes and WAIT_CB wake. Original `UsbControl::Main` receives, dispatches event `0x13`, constructs `0x0A4002` and destroys the message. Checked heap/mutex fixtures balance; startup FSM state stays 0. This is not a full-process replay, physical gadget event or USB mode transition. The adjustment and input callbacks are explicitly refused as SETCONFIG lanes.
- **Original-process startup prerequisite ([E0534](../evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md))**: original libc allocation/free, pthread/TLS state and local queues support configure/start commands and the FSM path `1 -> 5 -> 6 -> 7`. Original bus-power registration creates a `0x48`-byte record and class object; the distinct MSC branch returns -5 without `usbgmscif_init`. Gadget creation is still pending at OTG `SET_MODE` `0x4001E30C` (mode 2), `created=0`; no SETCONFIG event or hardware success is invented. Configure ownership balances; pending-start owners remain live. Its checkpoint retries the unanswered SVC. Native OTG/class/gadgetcore completion must precede original event production and teardown.
- **Timeout Blocking (`E0308`)**: Under `kernel-e0301`/`kernel-e0303`, finite positive `RCV_MSG_TMO` blocks until message arrival instead of returning `-1022` immediately.

## 3. `stella HeapAllocator` Assertion and Size Isolation

- Historical `--thread-timeslice` runs reached `Appframework` (TID 1087) and glibc `malloc.c:4652: _int_malloc: Assertion (unsigned long)(size) >= (unsigned long)(nb) failed`. The archived writes into freed chunk `0x13E708` remain observations, but the earlier **confirmed pool-boundary bug / not an emulator bug** explanation is not established by the cited helper addresses.
- **Original-instruction isolation ([E0526](../evidence/0526-stella-allocator-size-isolation-and-padding-trial.md))**: `libObj+0x79FB3E` binds `[r4, #0x40]` through vtable `0x134C310` slot `+0x30` (`+0x64402D`, returning `singleton + 8`) to the embedded `stella::HeapAllocator` (`vtable 0x1353F20`, slot `0 = +0x7ACBBB`). `libObj+0x79E8C2` / `+0x79E902` dispatch allocation requests; `HeapAllocator+0x7ACBBA` forwards them unchanged to `malloc`. Furthermore, in `PthreadMutex::create` (`+0x7AF46C`), `.bss` `0x13F70A4 = 0` (`getGlobalAllocator` `+0x7B1ADC`) takes the `cbz` fallback (`+0x7AF48C..+0x7AF498`, pushing the archived `LR = +0x7AF49D` frame) and requests `0x24` bytes from `_ZnwjRKSt9nothrow_t`, not from a `stella` pool. A subtract-four edit at `+0x79E8C2` / `+0x79E902` is not a justified size correction.
- Original libc normalizes request `0x4C` to chunk `0x50`, and its `malloc_usable_size` already returns `0x4C` for an ordinary in-use chunk. The following chunk's `prev_size` word is borrowable; chunk `0x13E6B8` / user `0x13E6C0` has exclusive usable end `0x13E70C`. A constructor byte at user offset `0x48` is within this extent.
- The isolated original log-block constructor writes only through offset `0x48`. Opt-in `--guest-patch-logalloc-0x50` changes request/chunk/usable to `0x50/0x58/0x54`; both constructor runs fit, and the four-byte distinction remains. This is a request-padding trial, **not a verified allocator correction**. The flag now requires source identity, a resolved load bias and matching original loaded bytes; the default and original inputs are unchanged.
- Reproduce with [stella_allocator_probe.py](../../tools/research/stella_allocator_probe.py); stock and opt-in reports each pass eleven bounded checks with identical repeated traces.
- **Full-process ownership trace & Unicorn 2.1.4 Thumb-2 stale-`ITSTATE` root cause ([E0657](../evidence/0657-unicorn-l3-allocator-gles-init-and-sugilite-gpu.md))**: Running unmodified `im.elf` with `--heap-ownership-trace` and **without** `--guest-patch-logalloc-0x50` proves original allocation/free ownership is clean across `1.50B` instructions and `120` threads (`0` ownership errors). Every former heap assertion/crash (`malloc.c:4652` at `libc+0x59820`, `_int_free` largebin unlink skips, `_int_malloc+0x59320: mov r0, r6` epilogue skips, and `libObj+0x55F294 -> +0x57CCC0`) was caused by Unicorn 2.1.4 carrying non-zero Thumb-2 `CPSR` `ITSTATE` across `IT` block branches/returns and baking `condexec_bits` into `tb->flags` before `UC_HOOK_BLOCK`. Signature-checked `libc` split/epilogue hooks plus `_thumb_it_legitimately_spans` + TB-restart in `InterpreterProbe._block`/`run_slice` eliminate the skips, and show the `202.58M` `stella_abort` was `CreateFramebuffer failed` on unmapped OSD `unit=0x4092E`.

## 4. Graphics Completion, Shared Memory & Sugilite Execution

[E0530](../evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md)
corrects the historical GPU assumptions. The driver keys owners by current
task TGID (`task+0x108`), not a pointer beyond the u32 user argument. Its
software wait resets completion only on entry; a resumed parked syscall must
not repeat that reset. Signal uses the shared owner's completion/fast flag,
with selective wake across file descriptors. IRQ waits use a separate device
waitqueue. Caller bit 24 selects recovery, not mandatory event presence.

Opt-in `kernel-bound-tgid` implements these bounded same-process rules.
The 128 KiB device mapping is uninitialized vmalloc software shared memory;
only the 16 KiB mapping is GPU MMIO. Unknown shared bytes and unsupported MMIO
stop execution, and the older descriptor-triggered engine-success experiment
cannot be combined with this policy. Two process replays stop identically
at `libObj+0x72BF96` (`146,135,735` instructions) before shared initialization, with OSD A/B unmapped and
zero engine completions.

[E0657](../evidence/0657-unicorn-l3-allocator-gles-init-and-sugilite-gpu.md)
recovers the original 128 KiB shared-memory contract (`grm_gles.ko` `sugilite_open` `0 -> 1` zeroes only bytes `0..3` via `__memzero(shared_area, 4)`, leaving `131,068` bytes untouched until guest `rm_rqueue_init` at `libObj+0x72BF80` `memset`s all `0x20000` bytes and writes `1` to `[shared_va + 0]`). With opt-in `kernel-bound-sugilite`, `captured-wbi-dmm` (all 6 WBI `nflasha5` DMM/GRM pools including `OSD A` `0x23A80000` and `OSD B` `0x23B16000`), coherent `GuestPhysicalRam` (`/dev/stream` + `/dev/mem` + `/dev/ldec` physical `dst` DMA + Sugilite DMA), and finite `rcv_msg_tmo` re-arming (`psm_dig_level_ma` `1 ms` `0x00A3035B` drain -> `THREAD_INFRA1` completion), unmodified `im.elf` executes `62` `/dev/ldec` UI atlas decompressions, loads `viewUnified2.so`, `CautionConfig.so`, and `viewRootPowerSw.uxc`, completes `24` Sugilite GPU jobs (`19` `P3D`, `2` `PSC0`, `3` `PPF`) + `24` IRQ 76 completions, and presents its first double-buffered `640×480` RGBA4444 `viewRootPowerSw.uxc` frames onto `OSD A` and `OSD B`. Those root frames are transparent (`0x0000`) because `DefRootView`'s `CheckerBootState` and `CheckerBootType` await external `av-cam.bin` / sub-micro `ObjPower` (`0x0059000E`) producers.
