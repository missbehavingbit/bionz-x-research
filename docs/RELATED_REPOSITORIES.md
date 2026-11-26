# Related Research Repositories

An agent-facing discovery map for Sony camera emulation, firmware formats,
hardware contracts, service protocols and application research. Consult the
relevant entries before implementing a new contract or repeating an experiment.

## Scope and provenance

- Initial catalog: 2026-11-16, based on the twelve upstream READMEs linked below.
  Descriptions summarize upstream scope, not findings reproduced in this project.
- This is a reference catalog, not a dependency manifest, compatibility matrix,
  source audit or authorization to run upstream tools. No upstream tools were
  cloned or executed to create it.
- Links follow upstream `HEAD` and can change. Before relying on a finding,
  record the repository, exact commit, file/section, camera body, firmware
  version and relevant input hashes in a local evidence record.

## How agents should use this catalog

1. Read the [current checkpoint](CURRENT_STATE.md), relevant local
   [evidence](evidence/README.md) and [agent rules](../AGENTS.MD) first.
2. Select references by the contract being investigated, not just camera name:
   - Boot, QEMU, timers, interrupts or display: [emulation](#emulation-and-hardware-contracts).
   - Update packages, partitions or UI containers: [formats](#firmware-formats-and-storage-layouts).
   - USB service protocols or runtime tooling: [service interfaces](#service-interfaces-and-runtime-tooling).
   - Product gates or newer architectures: [cross-generation comparisons](#cross-generation-boot-and-feature-research).
3. Follow the upstream README to the actual code or evidence. Treat upstream
   claims as leads until checked against identity-matched local inputs. Shared
   symbols, product families or SoC names do not prove identical register maps,
   interrupts, ABIs, settings identifiers or UI behavior.
4. Distinguish upstream observations, local observations and inference. If
   sources disagree, preserve their dates and input identities and resolve the
   discrepancy; do not replace newer local evidence with an upstream summary.
   Record material conclusions in [evidence/](evidence/) using the
   [validation rules](VALIDATION.md).
5. Inspect tools before execution. Several references include service-mode
   transitions, persistent settings/preload changes, raw storage writes,
   firmware installation or driver changes. Listing them here grants no
   permission for those operations; local safety boundaries remain in force.
6. Check the license and attribution requirements of each file before reuse.
   Keep proprietary inputs, dumps, credentials and keys out of this repository.
   Summaries and additions here must remain English-only.

## Emulation and hardware contracts

| Repository | Upstream scope | Research starting points | Transfer boundary |
| --- | --- | --- | --- |
| [Oein/a5000-emulator](https://github.com/Oein/a5000-emulator) | ILCE-5000 full-system QEMU emulation | [README](https://github.com/Oein/a5000-emulator/blob/HEAD/README.md): QEMU overlay, device models, boot hand-off and hardware notes | Earlier camera generation. Re-derive each contract for `CXD90045`; emulated or host-injected behavior is not automatically original-firmware behavior. |
| [cokeenet/zve10-qemu](https://github.com/cokeenet/zve10-qemu) | ZV-E10 / `CXD90045` firmware execution, system mapping and menu research | [README](https://github.com/cokeenet/zve10-qemu/blob/HEAD/README.md): QEMU workstream, firmware/system reports, display and menu work | Same SoC family is a comparison lead, not an a6400 profile. Separate stock execution, instrumentation and menu-port results; hardware recovery instructions are not approved procedures here. |

## Firmware formats and storage layouts

| Repository | Upstream scope | Research starting points | Transfer boundary |
| --- | --- | --- | --- |
| [ma1co/fwtool.py](https://github.com/ma1co/fwtool.py) | Sony firmware unpacking across multiple container and chip families, including `CXD90045` FDAT | [README](https://github.com/ma1co/fwtool.py/blob/HEAD/README.md): supported formats and updater/dump extraction inputs | Confirm the exact container and supported variant. Extraction support does not establish runtime compatibility or safe repacking/flashing. |
| [Lost-week-1025/CXD90045-FPSU](https://github.com/Lost-week-1025/CXD90045-FPSU) | ZV-1F / ZV-E10 package structures and UI/input-layer comparison | [README](https://github.com/Lost-week-1025/CXD90045-FPSU/blob/HEAD/README.md): nested containers, integrity layers, partition tables, UXC resources and touch-input analysis | Static comparison and feasibility analysis, not a hardware-verified UI transplant. Translate research summaries into English and independently verify format/ABI claims. |
| [CliffVale/sony-a6000-firmware-research](https://github.com/CliffVale/sony-a6000-firmware-research) | ILCE-6000 firmware 3.21 unpacking and raw-dump comparison | [README](https://github.com/CliffVale/sony-a6000-firmware-research/blob/HEAD/README.md): partition verification, region mapping and Android userdata analysis | Earlier body and storage layout. Do not generalize its Android runtime, partition offsets or updater-shell procedure to the a6400. |

## Service interfaces and runtime tooling

| Repository | Upstream scope | Research starting points | Transfer boundary |
| --- | --- | --- | --- |
| [DevCrashDay/a6400](https://github.com/DevCrashDay/a6400) | ILCE-6400 firmware 2.00 homebrew and runtime hooks | [README](https://github.com/DevCrashDay/a6400/blob/HEAD/README.md): native ARM execution, preload hooks, camera controls and reported verification limits | Same body still requires exact firmware/library identity checks. Persistent hooks and settings changes are outside ordinary offline/read-only work; compare historical limitations with current local evidence. |
| [masterflai/pmca-re-reloaded](https://github.com/masterflai/pmca-re-reloaded) | Cross-platform Rust Sony USB toolkit spanning multiple device generations | [README](https://github.com/masterflai/pmca-re-reloaded/blob/HEAD/README.md): shared GUI/CLI core, device information, settings memory and device support | Inspect the specific protocol/backend. App installation, firmware updates, settings writes and automatic driver handling are not authorized by this reference. |
| [dino2048/zv-e10-toolkit](https://github.com/dino2048/zv-e10-toolkit) | ZV-E10 Linux service-interface and BootROM/fastboot tooling; reported firmware 2.02/2.03 coverage | [README](https://github.com/dino2048/zv-e10-toolkit/blob/HEAD/README.md): Linux file/backup verification route and separate fastboot route | Keep Linux service observations separate from bare-metal recovery behavior. Bulk reads, mode changes and partition restore require their own scope; upstream hardware receipts do not verify our device. |
| [YuuLuo/a7c2-service-preload](https://github.com/YuuLuo/a7c2-service-preload) | ILCE-7CM2 firmware 2.01 service-session and temporary loader execution research | [README](https://github.com/YuuLuo/a7c2-service-preload/blob/HEAD/README.md): protocol notes, Jiritsu path behavior, dynamic-loader chain and execution evidence | Newer body and firmware-specific behavior. Preload/file writes are not read-only operations; temporary execution does not imply persistent firmware modification or an a6400 execution path. |

## Cross-generation boot and feature research

These are comparative references, not declarations of `CXD90045` compatibility.

| Repository | Upstream scope | Research starting points | Transfer boundary |
| --- | --- | --- | --- |
| [DavidBuchanan314/ILCE-7M4-RE](https://github.com/DavidBuchanan314/ILCE-7M4-RE) | ILCE-7M4 / A7 IV boot and hardware reverse engineering | [README](https://github.com/DavidBuchanan314/ILCE-7M4-RE/blob/HEAD/README.md): serial boot, fastboot payloads, eMMC organization and memory map | Different platform. Do not transplant addresses, storage/crypto assumptions or BootROM procedures; flash/restore operations are not approved here. |
| [OlliV/fx30_fw](https://github.com/OlliV/fx30_fw) | ILME-FX30 firmware 6.01 extraction and feature survey | [README](https://github.com/OlliV/fx30_fw/blob/HEAD/README.md): UFU/FDAT extraction, application strings, menu survey and feature probes | Different generation/container variant. Separate offline findings from service probes and RAM/settings writes; dormant code is not proof of an available feature. |
| [leogenot/sony-a7iv-feature-research](https://github.com/leogenot/sony-a7iv-feature-research) | ILCE-7M4 firmware 6.02 extraction, product classes and feature availability | [README](https://github.com/leogenot/sony-a7iv-feature-research/blob/HEAD/README.md): research evidence, product-class analysis, feature inventory and runtime gates | Shared implementation or an accepted backup setting does not prove a reachable menu or recording path. Do not carry setting identifiers or hardware verification across models. |

## Maintaining the catalog

- Add a row to the closest section with the canonical repository URL, explicit
  body/firmware scope where known, an upstream starting point, concrete research
  topics and a transfer limitation. Mark unknown scope as unknown.
- When changing a description, record its review date and source. The date above
  covers the initial inventory only; it is not a rolling verification date.
- Keep detailed findings and pinned citations in evidence records, not in this
  navigation list. Link any local result that supersedes an upstream assumption
  without silently rewriting historical evidence.
- Do not turn this catalog into automatic cloning, dependency installation,
  firmware downloading or camera probing.
