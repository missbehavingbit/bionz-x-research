# E0678 — ILCE-7RM4A per-mode USB product ids are BK4 preset units, not compiled descriptors

Date: 2026-11-22. Offline packaged firmware only: no camera, no USB, no updater executed.
This record answers the open boundary of [E0646](0646-ilce-7rm4a-physical-usb-identity-and-getmodelinfo.md):
where does the a7R IVA's host-observed PC Remote PID `0x0D9F` come from, given that it is absent from
the 18 `normalUsbDescriptors` pairs of the packaged 1.13 `config.yaml` ([E0570](0570-ilce-7rm4a-offline-sourced-profile.md))?

## 0. What changes, in one paragraph

The stock USB service library `libInfraUsbCmn.so` exports `ptbl_did`, a 60-entry table of per-"device
id" (DID) records. Its original `DescriptorMaintainer::Overwrite` copies words 6 and 7 of a record
(`+0x18`/`+0x1C`), treats them as **backup unit ids**, reads each with `Backup_get_datasize`/`Backup_read`,
requires the first value to be non-zero and the second to be a non-zero BCD number, and stores the two
halfwords at `+0x32`/`+0x34` of the DID's descriptor buffer. Resolving those units in the package's own
BK4 backup preset, with the project's decoder of the original `Data_SearchId`, gives DID `0x00` →
**`0x0D9D`** (the host-observed Mass Storage PID) and DID `0x3B` → **`0x0D9F`** (the host-observed PC
Remote PID); three more DIDs carry `0x0D9C`, `0x0D9E` and `0x096F`. So `0x0D9F` is preset data that the
USB service writes into its descriptor at runtime, which is why neither compiled descriptors nor
`config.yaml` contain it. The BCD-checked second value, `0x0100` or `0x0200` by preset family, is the
`bcdDevice` candidate; the host saw `REV_0200`.

## 1. Inputs and tool identities

The owner's own updater was placed under the git-ignored `firmware/` directory and processed with the
procedure of `docs/ADDING_A_BIONZX_CAMERA.md` / E0506, with fresh output directories. Nothing was executed
from the package and nothing is committed.

| input | SHA-256 |
| --- | --- |
| `firmware/Update_ILCE7RM4AV113.exe` (369,269,800 B) | `545fdf6cf5009a4d077e8626313974c9ba5ee3741039c3d976c15d85b1246aa1` (= E0570) |
| restored cabinet `Update_ILCE7RM4AV113.cab` (368,793,670 B; `sony_updater_payload`: inverted MSCF at `0x71A2C`) | `5a17cb771ee6c0622d740952a875c79c5b60a27cb5636d9feac260b9e921207c` |
| `FirmwareData_ILCE7RM4AV113.dat` (365,374,952 B; the only member extracted, `expand.exe -F:`) | `13e10d77347e4fd079d6ceca381e7c9c9dd74dd6de3ab5e44b4c9b6c8c78238b` |
| `config.yaml` (fwtool output; CRLF on Windows) | `bdca0815…cba32` as written; **`b511141a…2589f` after CRLF→LF**, equal to E0570 |
| `nflasha15/lib/libInfraUsbCmn.so` | `96638679c31ca8809fae2005fb992653629f040acad72280de10b2b1dcf8c246` |
| `0110_backup/SYSASTRA-DSLR/MGA/CX88900_UC2.bin` (BK4, named by `0110_backup/MainDest.txt`) | `7109822609da4ccddb139b67251373d364bb5b2399b0020f70b694f7971ae736` |
| `0110_backup/SYSASTRA-DSLR/MGA/CX88901_AP2.bin` (BK4, second family) | `311b99686f728209ad55701cb12ab553accadc848c24cca95d820597de166e03` |
| `nflasha3/av-cam.bin` (false-positive check only) | `44e0c17bde9477fca8e2b3b6c2b0c93f309491a78134a4ec230dceeecb9c49fa` (= E0570) |
| `fwtool.py` checkout (`/fwtool.py/`, git-ignored upstream) | `ma1co/fwtool.py` `cdba742b73eed5981480c326aeb30033aabf0223` |
| BK4 decoder | `src/a6400/execution/backup_service.py` `BK4PresetState` (original `Data_SearchId`, E0032/E0063) |
| disassembler | `capstone` from the pinned `requirements.txt`, Thumb mode; PLT stubs resolved through their GOT slot to the `.rel.plt` symbol |
| new tool | `tools/research/usb_did_pid_trace.py` (this record) |
| host | Windows 11, Python 3.14.10, `venv` with pinned `requirements.txt` plus `pycryptodomex`/`pyyaml` for fwtool |

Commands, from the repository root:

```powershell
.\venv\Scripts\python.exe -m tools.research.sony_updater_payload firmware\Update_ILCE7RM4AV113.exe artifacts\analysis\ilce7rm4a-v113\payload
& "$env:SystemRoot\System32\expand.exe" '-F:FirmwareData_ILCE7RM4AV113.dat' artifacts\analysis\ilce7rm4a-v113\payload\Update_ILCE7RM4AV113.cab artifacts\analysis\ilce7rm4a-v113\cabinet
.\venv\Scripts\python.exe .\fwtool.py\fwtool.py unpack -f artifacts\analysis\ilce7rm4a-v113\cabinet\FirmwareData_ILCE7RM4AV113.dat -o firmware\extracted_ilce7rm4a_v113
.\venv\Scripts\python.exe -m tools.research.usb_did_pid_trace --usb-library <extract>\...\nflasha15_unpacked\lib\libInfraUsbCmn.so --preset <extract>\...\MGA\CX88900_UC2.bin --output-dir artifacts\e0678-ilce7rm4a-did-pid-v2\CX88900_UC2
.\venv\Scripts\python.exe -m tools.research.usb_did_pid_trace --usb-library <same> --preset <extract>\...\MGA\CX88901_AP2.bin --output-dir artifacts\e0678-ilce7rm4a-did-pid-v2\CX88901_AP2
```

Reports (git-ignored `artifacts/`): `v2/CX88900_UC2` `35c457b1fd9751a053f8404cbe8e6a7759ea460cc9cf2387dc4ed61bf3b97fc9`,
`v2/CX88901_AP2` `4e4fcedd006f231f9b37e5c291254a539acc1e648efcd1e070aa1018bc62ddc1`. The earlier reports
without the gate replay are preserved beside them (`e0678-ilce7rm4a-did-pid/cx88900_uc2` `b9324f84…74d57`,
`cx88901_ap2` `6824dc95…9badb`); their unit and value columns are identical.

## 2. Negative results first

- **No static device descriptor carries the PID.** Across all 1,383 extracted files (1.35 GB), the
  little-endian pair `4C 05 9F 0D` (VID `0x054C`, PID `0x0D9F`, the device-descriptor layout) occurs
  nowhere, and neither does `4C 05 9D 0D` (Mass Storage) or `4C 05 A8 0C`/`4C 05 AA 0C`.
- **The compiled descriptor tables are runtime-filled.** `libInfraUsbCmn.so`'s ten `*_desc_tbl` symbols
  (including `remotepc_desc_tbl` at `0x1AD68` and `storage_desc_tbl` at `0x1AA40`) point at records
  whose descriptor pointers land at `0x1C000`, beyond the file-backed part of the second `PT_LOAD`
  (`0x1A440` + `0x2564`), i.e. in `.bss`. This matches E0159's a6400 note that the compiled
  `remotepc_desc_tbl` metadata does not carry the physical PID.
- **`av-cam.bin` is a false positive.** Its hit for `9E 0D 9F 0D` at `0x9230DA` sits in a monotone
  halfword run from `0x0C60` upward with sporadic gaps — a code table, not a PID table.

## 3. The DID table and the original code that consumes it

`ptbl_did` is defined in `libInfraUsbCmn.so` at `0x1BFAC`, size 240 (60 words). Nine entries are
non-null; each record names its own index in word 0 (the tool checks this and fails closed otherwise).

Original Thumb code, disassembled from the same file (addresses are library VAs; called functions are
named through their PLT stub's GOT slot and `.rel.plt` relocation):

| function | VA | what the instructions do |
| --- | --- | --- |
| `DescriptorMaintainer::Overwrite(const CUSB_SVC_DID_INFO&)` | `0xC2B6` | `ldr r3,[r5,#0x18]; str r3,[r4]` and `ldr r3,[r5,#0x1c]; str r3,[r4,#4]` — words 6/7 of the DID record become `unit0`/`unit1`; then `isDefaultAddress` → (if 0) `readPresetData` → (if 1) `chkPresetData` → (if 1) `execOverwrite`; tail-calls `printDescriptor` |
| `isDefaultAddress` | `0xC130` | returns 1 (skip) if `unit0 == -1` or `unit1 == -1` |
| `readPresetData` | `0xC19A` | zeroes `val0`/`val1` (`+8`/`+0xA`); for each unit: `Backup_get_datasize(unit) == 2`, then `Backup_read(unit, &val)`; a negative result fails |
| `chkPresetData` | `0xC222` | requires `val0 != 0`, and `val1 != 0` with `isBCD(val1)` |
| `isBCD(unsigned short)` | `0xC16C` | the low three nibbles must each be ≤ 9 (the top nibble is not examined) |
| `execOverwrite(const CUSB_SVC_DID_INFO&)` | `0xC264` | `desc = [info,#0xc]`; `strh val0,[desc,#0x32]`; `strh val1,[desc,#0x34]` |

The record's word 3 (`+0xC`, the descriptor pointer) is zero in the file, consistent with the
runtime-filled descriptor buffers of section 2. `+0x32`/`+0x34` are adjacent halfwords, as `idProduct`
and `bcdDevice` are at offsets 10/12 of a USB device descriptor; that would place a device descriptor at
`+0x28` of the buffer, which is not shown here.

## 4. Units resolved in the package's own preset

`BK4PresetState` validates the BK4 header and checksum and resolves units exactly as the original
`Data_SearchId`. `usb_did_pid_trace` replays the gates of section 3 (`would_overwrite`):

| DID | PID unit (word 6) | PID | 2nd unit (word 7) | 2nd `CX88900_*` | 2nd `CX88901_*` | original gates | host observation (E0646) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `0x00` | `0x00E7002E` @ `0xB9CF6` | **`0x0D9D`** | `0x00E7002C` | `0x0100` | `0x0200` | overwrite | Mass Storage `054C:0D9D`, `REV_0200` |
| `0x0B` | `0x00E7002F` @ `0xB9CF8` | `0x0D9E` | `0x00E7002C` | `0x0100` | `0x0200` | overwrite | not observed |
| `0x0C` | `0x00E7002D` @ `0xB9CF4` | `0x0D9C` | `0x00E7002C` | `0x0100` | `0x0200` | overwrite | not observed |
| `0x37` | `0x00E70032` @ `0xB9D3E` | `0x096F` | `0x00E70033` | `0x0100` | `0x0100` | overwrite | not observed |
| `0x3B` | `0x00E70035` @ `0xB9D44` | **`0x0D9F`** | `0x00E7002C` | `0x0100` | `0x0200` | overwrite | PC Remote `054C:0D9F` |
| `0x0D`, `0x38`, `0x39`, `0x3A` | `0xFFFFFFFF` | — | `0xFFFFFFFF` | — | — | skipped (`isDefaultAddress`) | — |

The neighbouring unit `0x00E70029` @ `0xB9CE0` holds the 16-byte string `DSC`, the SCSI INQUIRY product
string E0646 measured. All 28 `.bin` presets (14 `MGA`, 14 `MGA_HIBIKI2`) carry the block at the same
offsets; they fall into two byte-identical groups over `0xB9CC0..0xB9D60` (12 `*88900_*`, 16 `*88901_*`)
that differ **only** in the byte at `0xB9CF3`, the high byte of unit `0x00E7002C`.

## 5. What is measured, what is inferred

- **Measured:** every hash, symbol, address, record word, unit, offset and value above; the instruction
  sequences of section 3; the negative scans of section 2.
- **Shown by original code:** words 6/7 of a DID record are backup units; the first value overwrites
  `+0x32`, the second (BCD-checked) `+0x34`, of that DID's descriptor buffer; DIDs with `0xFFFFFFFF`
  units keep their compiled descriptor.
- **Inferred (by value match):** DID `0x00` is Mass Storage and DID `0x3B` is PC Remote, because their
  preset PIDs equal the host-observed ones. DIDs `0x0B`, `0x0C` and `0x37` are **not** assigned a mode;
  `0x0D9C` and `0x0D9E` are not claimed as MTP or any other mode.
- **Inferred (layout):** `+0x32`/`+0x34` are `idProduct`/`bcdDevice`, so `0x00E7002C` is `bcdDevice`.
  Support: the code-level BCD check, the adjacency, and the host's `REV_0200`. If so, the owner's body
  carries the `*88901_*` value although `MainDest.txt` names `CX88900_UC2.bin`; whether the updater
  rewrites the backup area at all, and which family a given body was built with, are not shown.
- **Not shown:** that this code runs on the wire path (no execution or emulation was performed); the
  descriptor buffer's base layout; that the live backup area holds these bytes; and why `config.yaml`
  lists `0x0D9C/0x0D9D` but not `0x0D9E`, `0x0D9F` or `0x096F`.

## 6. Superseded

E0646 §3 "where that descriptor comes from is not identified" and the matching `ilce_7rm4a` profile
boundary are superseded: `0x0D9F` is the BK4 preset unit `0x00E70035` that DID `0x3B` of the stock USB
service names and `DescriptorMaintainer::execOverwrite` stores. E0646's observations stand.

## 7. Repository changes

- `tools/research/usb_did_pid_trace.py`: decodes `ptbl_did` from the original library, resolves its
  units in a BK4 preset and replays the original `Overwrite` gates; fails closed on an unrecognised record
  layout or a read that is not file-backed.
- `tests/test_usb_did_pid_trace.py`: constructed BK4/DID fixtures and gate replays (no Sony bytes), plus a
  test of the values above that runs only when the local extraction has the hashes of section 1.
- `src/a6400/targets/bodies/ilce_7rm4a.py`: the PC Remote boundary and `usb` sources cite this record.
- Evidence index, README platform row and the rendered status SVG's receipt count. `STATUS_SNAPSHOT`
  stays on E9103 because `test_research_status_sync` pins it to the numerically highest record.
- Validation: full offline suite in the pinned `venv` (Windows, Python 3.14.10); counts in the PR.

## 8. Next experiment

Running the same tool on another body's package (for example the a6400, whose observed PC Remote
`0x0CAA` is also absent from its packaged pairs, E0570 §4) would test whether the DID layout is shared.
Executing `DescriptorMaintainer::Overwrite` under the project's Unicorn harness with the preset-backed
backup service would replace the layout inference of section 5 with an executed store.
