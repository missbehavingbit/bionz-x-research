# Documentation Index

Central navigation map for the Sony BIONZ X (`CXD90045`) universal emulator and reverse-engineering platform.

## 1. Architecture & Multi-Device Platform

| Document | Purpose |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layered system architecture (`targets/`, `machine/`, `devices/`, `execution/`, `images/`, `trace/`, `frontend/`) and L0–L3 emulation levels |
| [PLATFORM_MODEL.md](PLATFORM_MODEL.md) | Cross-model `CXD90045` evidence across 13 bodies and 9 kernels (`E0477–E0489`) |
| [ADDING_A_BIONZX_CAMERA.md](ADDING_A_BIONZX_CAMERA.md) | Step-by-step L0–L3 guide for adding support for a new camera body |
| [architecture/multi-device-abstraction.md](architecture/multi-device-abstraction.md) | How `TargetProfile`, `BootLayoutSpec`, and `kallsyms` drive `UnicornMachine` and `InterpreterProbe` without hardcoded `a6400` constants |
| [architecture/l2-kernel-boot-and-mmu.md](architecture/l2-kernel-boot-and-mmu.md) | L2 full-system kernel boot (`EXBL`, `ARM-CXD900X0`, `kemco`/ATAGS, `UC_TLB_VIRTUAL`, ARMv7 aborts, Thumb `ret_to_user`) |
| [architecture/l3-userspace-and-ipc.md](architecture/l3-userspace-and-ipc.md) | L3 user-space `im.elf` execution, OSAL/UIPC queues, `MWF::ObjMsg`, `--thread-timeslice`, and `stella HeapAllocator` findings |
| [architecture/hardware-and-partitions.md](architecture/hardware-and-partitions.md) | `nflasha` flash partition layout, GPES `SMAPH-S30` BAR `0xF1000000`, OSD A/B display, and DWC3 EP0 USB gadget notes |

## 2. Current Status & Workflows

| Document | Purpose |
| --- | --- |
| [CURRENT_STATE.md](CURRENT_STATE.md) | Active checkpoint across all four tracks (L2 Boot, L3 App/GUI, Track B UVC/PTP, Multi-Device) |
| [REPOSITORY.md](REPOSITORY.md) | Repository layout, module launch paths, and structural CI checks |
| [RELATED_REPOSITORIES.md](RELATED_REPOSITORIES.md) | Agent-facing catalog of related Sony research repositories, starting points, provenance rules and cross-model transfer limits |
| [Adapted ZV-E10 menu integration](ZVE10_ADAPTED_MENU.md) | Explicit upstream QEMU boot profile, checked inputs, native UART replay and VPH scanout checks |
| [External ZV-E10 menu receipt](evidence/external-zve10-menu/README.md) | Contributor-local adapted menu, English screenshots and boundaries; not an upstream stock-boot milestone |
| [LIVE_CAMERA_EMULATOR_WORKFLOW.md](LIVE_CAMERA_EMULATOR_WORKFLOW.md) | Safe read-only live camera observation and emulator handoff workflow |
| [NATIVE_USB_DEBUGGER.md](NATIVE_USB_DEBUGGER.md) | Native USB debugger protocol, per-body qualification and safety rules |
| [Local ILCE-6400A firmware-dump research](CURRENT_STATE.md#local-ilce-6400a-firmware-dump-research) | Verified local captures, artifact paths, and the remaining full-dump boundary |
| [A6400A Jiritsu packet mapping](evidence/0525-a6400a-jiritsu-senser-offline-packets.md) | Packaged service dispatch, Senser/OSAL layouts and reproducible offline packet fixtures; no USB backend |
| [A6400A DMM metadata and lifetime](evidence/0532-a6400a-dmm-metadata-bounds-and-lifetime.md) | Opt-in single-extent metadata, original share/access counters and bounded borrowed raw replies; memory-only sink, USB disabled |
| [REA_WORKFLOW.md](REA_WORKFLOW.md) | Connected REA/provider availability and Ghidra / WSL workflow (`tools.research.rea_wsl_batch`) |
| [QEMU workflow](../qemu-a6400/README.md) | Reproducible captured boot inputs, native eMMC regression checks, and the later device-model records through the viewfinder console |
| [TOOLCHAIN.md](TOOLCHAIN.md) | Qualified current/legacy research tools, dependency pins, QEMU compatibility checks and rollback |
| [DEPENDENCY_WORKFLOW.md](DEPENDENCY_WORKFLOW.md) | Dependency and call-chain tracing workflow |
| [VALIDATION.md](VALIDATION.md) | Differential validation and fidelity rules |
| [GLES TGID completion and shared-memory boundary](evidence/0530-gles-tgid-completion-and-shared-memory-boundary.md) | Original completion/reset and recovery branches; guarded software-shared versus GPU mappings, repeated real-process stop and no claimed frame |
| [Unicorn L3 allocator, GLES init & Sugilite GPU](evidence/0657-unicorn-l3-allocator-gles-init-and-sugilite-gpu.md) | Thumb-2 stale-`ITSTATE` root cause (`1.50B` insns, `0` ownership errors without padding), `sugilite_open` 4-byte + `rm_rqueue_init` 128 KiB init, `24` Sugilite GPU/IRQ 76 jobs, and transparent `viewRootPowerSw.uxc` OSD A/B boundary |
| [Unicorn SIO and GPIO from the QEMU oracle](evidence/0677-unicorn-sio-gpio-oracle.md) | Channel-3 start/done and GPIO port-1 `0x32`, taken from a 64k-record QEMU MMIO capture |
| [Unicorn SYSV/VPH frame clock](evidence/0676-unicorn-sysv-vph-frame-clock.md) | Opt-in port of the QEMU engine-frame clock, panel vsync, and evsel `ext-vd` path that unblocked `ObjMic` on QEMU |

## 3. UVC / PTP & Feature Workstreams

| Document | Purpose |
| --- | --- |
| [UVC_PTP_STATUS.md](UVC_PTP_STATUS.md) | Detailed status of Track B UVC/PTP composite transport |
| [CONTRIBUTING_UVC_PTP.md](CONTRIBUTING_UVC_PTP.md) | Contributor guide for UVC/PTP work |
| [ARCHITECTURE_UVC_MONITOR.md](ARCHITECTURE_UVC_MONITOR.md) | UVC monitor architecture (hash-pinned by `E0348`) |
| [UVC_HANDOVER_PLAN.md](UVC_HANDOVER_PLAN.md) | UVC/PTP handover plan |
| [CINEMAHUD_STOCK_PTP.md](CINEMAHUD_STOCK_PTP.md) | CinemaHUD stock PTP reference |
| [ISP_TRANSFORM_CATALOG.md](ISP_TRANSFORM_CATALOG.md) | Transform formats, control dependencies, evidence, and open verification for ARRI tuning |
| [ROADMAP.md](ROADMAP.md) | Long-term project roadmap |

## 4. Evidence, Decisions & Archives

| Directory / File | Purpose |
| --- | --- |
| [evidence/](evidence/) | Dated, hash-pinned evidence records (`0001`–`0489+`) |
| [Local a6400A E0667](evidence/0667-a6400a-debugger-abi-and-live-channel-boundary.md) | a6400A debugger ABI fixtures, live channel refusal and remaining bootstrap gates |
| [Local a6400A E0668](evidence/0668-a6400a-live-senser-entry-and-recovery.md) | Repeated authenticated Senser entry, working drivers and live file-read status 0x80/zero payload; no capture |
| [Local a6400A E0669](evidence/0669-a6400a-offline-senser-dispatch-and-native-read-gates.md) | Original missing-handler/terminal/adjustment gates, native extcmd declarations and unsafe getter cleanup; own 1.01 L3 prefix |
| [Local a6400A E0670](evidence/0670-a6400a-ghidra-reader-consumers-and-fixed-partition-candidate.md) | Ghidra/Unicorn read consumers, 256-byte scratch and fixed nflasha5 candidate; cleanup/host-transfer gates and own-image L3 progress |
| [Local a6400A E0671](evidence/0671-a6400a-fixed-reader-framing-and-completion-controls.md) | Bounded fixed-node framing and 60 MiB constructed replay; original completion/release and failed-delivery ownership; no live adapter or capture |
| [Local a6400A E0672](evidence/0672-a6400a-guarded-fixed-acquisition-and-supervised-gate.md) | Guarded 0336 client, real write counts, exact-capacity constructed disk capture, partial retention, one-shot recovery policy and supervised connection sequence |
| [Local a6400A E0673](evidence/0673-a6400a-first-actual-nflasha5-capture.md) | Actual 60 MiB nflasha5 capture, independent host hash, failed idle handoff, unacknowledged exit/recovery boundary and remaining-partition research |
| [Local a6400A E0674](evidence/0674-a6400a-readonly-msc-lun-capture-and-flash-boundary.md) | Actual read-only 2 MiB MSC LUN 1 capture, unverified nflasha23 backing, native postflight, unchanged guards and blocked remaining firmware scope |
| [Local a6400A E0675](evidence/0675-a6400a-expanded-bootstrap-review-and-full-dump-boundary.md) | Expanded-scope bootstrap investigation, fresh identity/postflight, existing capture integrity, Jiritsu internal-flash and MPR file-write effects; no new partition capture |
| [decisions/](decisions/) | Architectural decision records (ADRs) |
| [archives/agent-notes-2026-11-13-e0210-to-e0476.md](archives/agent-notes-2026-11-13-e0210-to-e0476.md) | Complete verbatim archive of `AGENTS.md` experiment logs `E0210–E0476` |
| [archives/agent-notes-2026-11-09.md](archives/agent-notes-2026-11-09.md) | Earlier `AGENTS.md` historical notes (prior to 2026-11-09) |
| [archives/research-status-through-e0298.md](archives/research-status-through-e0298.md) | Archived README status narrative through `E0298` |
