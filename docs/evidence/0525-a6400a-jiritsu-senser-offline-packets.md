# E0525 - A6400A Jiritsu routing, Senser encoding and offline packet fixtures

Date: 2026-11-17. **Offline preserved-package analysis and bounded original
instructions with constructed fixtures. USB commands remain disabled.**
Continues the package qualification in [E0506](0506-a6400a-offline-qualification-and-debugger-boundary.md),
not the physical identity transaction in [E0509](0509-a6400a-msc-reply-lifetime-and-verified-native-identity.md).

## Result and boundary

The packaged service path is mapped from Senser adjustment commands to all
**18 Jiritsu commands**, their application callback, request encoder and reply
serializer. The new
[`a6400a_jiritsu_packets`](../../tools/research/a6400a_jiritsu_packets.py)
reproduces **seven packet fixtures** without importing or opening a USB backend.
Two fresh runs produced byte-identical fixture JSON, instruction evidence and
all seven packet binaries.

This is not a service-session authentication, camera transaction, file-access
qualification or debugger bootstrap. No filesystem, mount, OSAL allocation/send,
DMM service or USB transfer is supplied to the original code. File operations
are stopped before their handlers; raw replies stop before DMM metadata handling.
The target remains `reference`.

The upstream catalog was consulted before deriving this contract.
Its newer-body Jiritsu references are leads only; no upstream implementation,
address, authentication material or physical result is substituted for these
identity-matched A6400A inputs.

## 1. Input and tool identities

All three inputs are opened read-only. The updater is hashed, never executed.
Both successful final runs rehash every input after replay.

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `Update_ILCE6400AV101.exe` | 324,175,032 | `fd6ae50755a9202a8b506b6b77aa27ab41ae717e96f05a0d4e2ad699b40d9ad1` |
| Packaged `libJiritsu.so` | 162,568 | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` |
| Packaged `libsencore.so` | 341,036 | `08804d8752c0bdfbad6e3f212cc1feaf2145e71e866a64c3f94bc4c2eca36089` |

Library namespace:
`firmware\extracted_a6400a_v101\firmware.tar_unpacked\0700_part_image\dev\nflasha15_unpacked\lib`.
Both libraries are ELF32 little-endian ARM ET_DYN.

Tooling: Python **3.14.6**, Unicorn **2.1.4**, Capstone **5.0.9**,
pyelftools **0.33**, and the existing WSL REA/Ghidra **12.1.4** provider.
The replay manifest records the replay-tool and shared static-helper hashes,
expanded reproduction command, package versions and the declared Cortex-A9 CPU
fixture. That fixture is not a new physical CPU identification.

Coordinates below are **ELF linked addresses**, not camera runtime addresses.
Thumb function pointers have bit 0 set. Ghidra used a synthetic `+0x10000`
image-base translation; the isolated Unicorn windows use `+0x01000000`.
Neither translation establishes a live mapping.

## 2. Original call chain

1. The relocated `pFunc_level_A`, `pFunc_level_B` and `pFunc_level_C` tables in
   `libsencore.so` associate outer function **`0x0040`** with
   `AdjustControlFunc` (`0x13B4D`).
2. `AdjustControlFunc` receives a four-byte `CommandCodeType` and calls
   `SubsystemCommunicationCommand::TransactionStart` (`0x13371`).
3. Its 1,547-row command table has 18 Jiritsu rows at
   **`0x30944` through `0x30E0C`**, stride **`0x48`**. Every row has category
   **`0x1001`**, subcommand **`0x4001` through `0x4012`**, and queue
   **`0x016F0480`**.
4. Each request replay stops **before `osal_valloc_msg_wait`**, with the original
   arguments `(0x016F0480, output_pointer, 0x140, 1)`. It supplies no allocation
   or send result. A separate direct call to the original encoder
   **`0x12041`** uses an explicitly declared message buffer.
5. That encoder copies **`0x140` bytes** and replaces the command halfword at
   **`message+0x1A`** with its low byte. Thus `0x400F` becomes callback command
   **`0x000F`**, not a different outer Senser function.
6. In `libJiritsu.so`, `jiritsu_init` (`0x3B59`) registers queue
   `0x016F0480` with callback **`0x7015`**. Original callback replays reach all
   18 expected command-handler entries and stop before executing them.
7. Phase-4 wrapper controls additionally reach `adj_cmd_set_file_path`
   (`0x5211`), `adj_cmd_set_file2` (`0x5A69`) and `adj_cmd_get_file2`
   (`0x5F51`), stopping at entry without servicing their file/DMM operations.
8. Original Jiritsu reply builders feed the original Senser reply decoder
   **`0x12B51`** for inline completion and error controls.
   `SubsystemCommunicationCommand::DoExecute` (`0x13091`, static evidence)
   passes the resulting buffer/length to `MemoryData`.
9. Original `SendResponse` (`0xEFA5`), `Serialize` (`0xED71`), and the original
   `MemoryData` getters/advance methods serialize the declared data. Execution
   is intercepted at **`SenserIfSend` (`0x10AC1`)** by a record-only memory
   sink. The USB sender itself does not execute.

The original privilege check is not bypassed: declared `adjCodeLevel=3` permits
the constructed request to reach the allocation boundary; the corresponding
level-1 control returns **`0x80`** before allocation. Neither value is an
observation of an authenticated camera session.

### Complete command map

All rows use category `0x1001` and queue `0x016F0480`.
Names below omit the common `JIRITSU_` prefix.

| Table subcommand | Callback command | Name | First callback handler |
| --- | --- | --- | --- |
| `0x4001` | `0x01` | MODE | `cmd_mode` |
| `0x4002` | `0x02` | MOUNT | `cmd_mount` |
| `0x4003` | `0x03` | FORMAT | `cmd_format` |
| `0x4004` | `0x04` | START | `cmd_start` |
| `0x4005` | `0x05` | DEBUG | `cmd_debug` |
| `0x4006` | `0x06` | FIRMUP | `cmd_high_capacity_send_file` |
| `0x4007` | `0x07` | SET_FILE | `cmd_high_capacity_send_file` |
| `0x4008` | `0x08` | GET_FILE | `cmd_high_capacity_get_file` |
| `0x4009` | `0x09` | FILE_DELETE | `cmd_file_delete` |
| `0x400A` | `0x0A` | GET_FILELIST | `cmd_high_capacity_get_file` |
| `0x400B` | `0x0B` | GET_FILE_HASH | `cmd_high_capacity_get_file` |
| `0x400C` | `0x0C` | COMPARE_MEDIA | `cmd_high_capacity_send_file` |
| `0x400D` | `0x0D` | GET_VERSION | `cmd_high_capacity_get_file` |
| `0x400E` | `0x0E` | FACTORY | `cmd_factory` |
| `0x400F` | `0x0F` | SET_FILE_PATH | `cmd_high_capacity_send_file` |
| `0x4010` | `0x10` | SET_FILE2 | `cmd_high_capacity_send_file` |
| `0x4011` | `0x11` | GET_FILE2 | `cmd_high_capacity_get_file` |
| `0x4012` | `0x12` | FILE_DELETE2 | `cmd_file_delete2` |

## 3. Packet layouts

### Outer Senser header: 12 bytes

All multibyte fields are little-endian: **`<IHHBBBB`**.

| Offset | Field | Fixture value |
| --- | --- | --- |
| `+0x00` | Body byte count, excluding this header | Exact following-body length |
| `+0x04` | Function | `0x0040` |
| `+0x06` | Sequence | `0x1234`, preserved in replies |
| `+0x08` | Version | Declared request fixture `0`; original reply serializer writes `3` |
| `+0x09` | Micon type | Declared fixture `0` |
| `+0x0A` | Offset type | Declared fixture `0` |
| `+0x0B` | Response | Request fixture `0`; reply completion `1` or rejection `0x81` |

The request body begins with **`<HH`**, category `0x1001` and the flagged table
subcommand, followed by constructed argument bytes. The generated request
profile deliberately caps arguments at **292 bytes**; it does not implement
large-request fragmentation.

Example `GET_FILE2` fixture, with arguments `01 FF 00`:

```text
07 00 00 00  40 00  34 12  00 00 00 00  01 10 11 40  01 FF 00
```

### Initial OSAL request: 320 bytes

| Offset | Field |
| --- | --- |
| `+0x00`, `+0x01` | Phase `1`, response/control byte `1` |
| `+0x02` | Preserved 16-bit sequence |
| `+0x04` | Original body size, including the four-byte command code |
| `+0x08` | Outer function `0x0040`; reserved halfword at `+0x0A` |
| `+0x0C`, `+0x0D` | Transfer type and reserved byte |
| `+0x0E` | 16-bit memory-unit handle |
| `+0x10` | Address/unit-type union |
| `+0x14` | Raw transfer length |
| `+0x18` | Category `0x1001` |
| `+0x1A` | Normalized low-byte command |
| `+0x1C` | Up to 292 inline argument bytes |

### Jiritsu reply header: a different 16-byte variant

The reply builders use **`<BBHBBHII`**: phase, response, preserved sequence,
transfer type, reserved byte, unit handle, address/unit-type union, data length.
Inline reply data begins at **`+0x10`**, not request offset `+0x1C`.

| Builder | Phase | Transfer type | Data length |
| --- | --- | --- | --- |
| `packet_set_error` | `0` | `0` | Caller-supplied |
| `packet_set_process_end` | `8` | `0` | Caller-supplied |
| `packet_set_process_req_raw_data` | `2` | `1` | Caller-supplied |
| `packet_set_process_res_raw_data` | `8` | `1` | `g_raw_res_size` |
| `packet_set_fin_del_data` | `0x20` | `0` | Caller-supplied |

The raw-builder controls use a declared DMM object with handle `0x5678`,
unit-type value `0x02030405` and length `0x23`. The original getter instructions
execute; these are not captured camera allocations or physical addresses.

For continuation phases **`4`** and **`0x10`**, the original request encoder
copies reply fields `+4`, `+6`, `+8`, `+0x0C` into request fields `+0x0C`,
`+0x0E`, `+0x10`, `+0x14`, respectively, then normalizes the command again.
Both controls match the full 320-byte expected output.

Inline completion returns `message+0x10`. A raw reply instead reaches
**ELF PC `0x120D0`**, replay PC **`0x010120D0`**, and stops before DMM metadata
handling. No raw memory mapping or completed raw file transfer is claimed.

## 4. File operand and path observations

These are static original-instruction observations, not executed file operations:

- `SET_FILE_PATH` accepts raw sizes **2 through 513**, a leading selector byte
  of zero, and copies the remaining bytes from the DMM buffer after that byte.
  It converts backslashes to slashes and rejects LF, CR and comma. The allocation,
  copied buffer and final path are not supplied as runtime camera state here.
- `SET_FILE2` uses a three-byte selector prefix before file data; `GET_FILE2`
  reads the corresponding three selector bytes; `FILE_DELETE2` reads two.
  The fixtures use mode `1`, selector `0xFF`, and, where present, transform flag
  `0`, solely as constructed inputs.
- Mode `1` uses `/mnt`; mode `0` can mount internal `/dev/nflasha6` on
  `/tmp/jiritsu`. The latter is not an authorized camera input or an approved
  operation. None of these mounts or writes is performed.
- `get_access_filename` (`0x58B1`) applies the selected root to the stored
  custom path. A leading slash is not, by itself, proof of arbitrary absolute
  path access. Stored paths are freed by the inspected file2/delete2 completion
  handlers; this is not a persistent-path contract.
- `libCommuTalk`, script loading and native-library loading are outside replay.
  No key, authentication material, executable payload or preload recipe is
  emitted.

## 5. Fixtures, receipts and reproduction

From the repository root, using a **new output directory** each time:

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_packets `
  --report-dir artifacts\tests\a6400a-jiritsu-NEW

.\venv\Scripts\python.exe -m unittest `
  tests.test_a6400a_jiritsu_packets tests.test_msc_extcmd_trace -v
```

There is no USB flag or transport argument; `--usb` is rejected before input or
output access. Inputs with another hash, malformed requests, oversized inline
arguments, unlisted imports/PCs, unresolved imported-data reads and execution
limits are not accepted as completion. Pure memory helper fixtures are bounded
to 4 KiB. Each original call has a 100,000-instruction and two-second bound.

Successful final receipts:

- `artifacts\tests\a6400a-jiritsu-20261117-05`
- `artifacts\tests\a6400a-jiritsu-20261117-06`

Each contains `manifest.json`, `fixtures.json`, `original-instructions.json`
and seven constructed `.bin` packets:

| Packet | Bytes | Scope |
| --- | ---: | --- |
| `set-file-path.bin` | 36 | Request fixture; no path change |
| `set-file2.bin` | 26 | Request fixture; no write |
| `get-file2.bin` | 19 | Request fixture; no read |
| `file-delete2.bin` | 18 | Request fixture; no deletion |
| `parameter-error.bin` | 16 | Original inline decoder and reply serializer; constructed application error `0x10000201` |
| `rejected-command.bin` | 12 | Original rejection decoder and empty reply serializer, response `0x81` |
| `synthetic-file-reply.bin` | 24 | Original serializer with constructed data; sink accepts at most five bytes per call |

Both `fixtures.json` files are **36,456 bytes**, SHA-256
`554b2a2f6285d0338999cb162d8e5cf73bdd859e30f15edef59c472c4f789ab0`.
The original-instruction reports are byte-identical, SHA-256
`7dd562743aab7db5802602ac3863849ae12fefab86176215edb9a17e172bf3ba`.
The fixture files contain synthetic data, not proprietary executable bytes.

Retained REA batches under `artifacts\tools\rea`:
`a6400a-jiritsu-packets-20261117-02`, `-03`, and
`a6400a-senser-packets-20261117-01`, `-02`, `-03`.
The bounded queries and original instructions are retained with their target
hashes. The initial Jiritsu `-01` batch remains **failed**: Ghidra found a PLT
stub and a body with the same name. Subsequent batches selected exact translated
entry addresses; no failed evidence was reused as successful analysis.

Early replay receipts `-01` and `-02` likewise remain failed. The first stopped
at the unlisted original command-routing gate (`0x11F44`); the second stopped
at an unbound handler PLT entry. The final harness admits only the specifically
analyzed gate and explicit stop-entry bindings, not arbitrary import success.
Receipts `-03` and `-04` are earlier bounded iterations, not the final contract.

## 6. Validation and next boundary

- Focused packet/admission/closed-call checks:
  **15 collected, all passed**, including malformed framing, exact inline bounds,
  source-hash rejection, USB-option rejection, failed-receipt preservation,
  unlisted imports, unmapped memory and instruction limits.
- Expanded packet/PLT/repository-layout/target-profile validation:
  **60 collected, all passed** with
  `.\venv\Scripts\python.exe -m unittest tests.test_a6400a_jiritsu_packets tests.test_msc_extcmd_trace tests.test_arm_plt tests.test_repository_layout tests.test_target_profiles -v`.
  Log: `artifacts\analysis\a6400a-jiritsu-20261117\focused-validation-01.log`.
- Final packet/native-admission/profile/shared-documentation validation:
  **116 collected, all passed** with
  `.\venv\Scripts\python.exe -m unittest tests.test_a6400a_jiritsu_packets tests.test_msc_extcmd_trace tests.test_arm_plt tests.test_a6400a_native_readonly tests.test_repository_layout tests.test_target_profiles tests.test_research_status_sync tests.test_research_status_dashboard -v`.
  Log: `artifacts\analysis\a6400a-jiritsu-20261117\final-scoped-validation.log`.
  The shared latest reference now includes
  [E0526](0526-stella-allocator-size-isolation-and-padding-trial.md), while the
  historical 1,903 / 2026-11-15 fully passing baseline remains unchanged.
  `.\venv\Scripts\python.exe -m tools.research.render_research_status --check`
  and scoped `git diff --check` both passed.
- Both final real-package replays completed: **18 dispatch routes**, **three
  phase-4 payload routes**, **five reply builders**, **two continuation modes**,
  inline and rejection reply decoding, a pre-DMM raw boundary, and the original
  privilege rejection. All seven packet files and both JSON reports match
  exactly between runs.
- Documented full command:
  `.\venv\Scripts\python.exe -m unittest discover -s tests -q`:
  **1,913 collected, eight errors, 126 skipped**, not green. All eight errors are
  `UC_ERR_NOMEM` in the unchanged large-RAM kernel fixture paths
  `emulator.test_cramfs_busybox` and `emulator.test_data_abort`.
  The complete log is
  `artifacts\analysis\a6400a-jiritsu-20261117\full-suite-01.log`.
  Other active research sessions share this worktree; this is a dated run,
  not a new passing baseline.
- The pre-existing SDHCI CLI test attempted to open physical volume `E:` and
  received `FileNotFoundError` before using its synthetic memory source. The
  Jiritsu tool does not call that path. No USB camera command or mode change was
  issued; no claim of a hardware-blocking full-suite runner is made.

Next: derive and independently bound the DMM metadata/extent and lifetime
contract before extending raw file-transfer replay. Authentication, live queue
registration, physical transport, mounted path availability and safe device
access remain separate gates. **Do not transmit these fixtures to a camera.**
