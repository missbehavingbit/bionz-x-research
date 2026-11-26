# E0663 — Correction: E0483's 29 "IPC call sites" were calls to other imports; the graph had four decoding defects

Date: 2026-11-20. Offline only: thirteen packaged `libObj.so` images, no camera, no execution.

Supersedes the figures of E0483 (the whole IPC table) and the call-graph figures of E0484 and
`docs/PLATFORM_MODEL.md` §3. E0484's central finding — the display entry points have no direct
caller — **stands** and is re-measured here on thirteen bodies with the corrected tools.

## 0. Short answer

1. **None of the 29 rows of `ipc-edges-a6400.tsv` is an IPC call.** GNU objdump labels the
   targets of all 29 sites: 25 call `MWF::RsrcAcsr::Acquire`, two
   `MWF::ParamArray_Template<short, (MWF::Param::Type)42>` constructors, one
   `MprIf::GetGpsDataHeaderByteArrayMaxSize`, and one is a `bl` into the middle of a PLT stub.
   The 17 "constant arguments" belong to those calls, not to any queue.
2. **The real table is 763 IPC call sites in the a6400 `libObj.so`**: 187 `osal_snd_msg`, 406
   `osal_snd_sync_msg`, 114 `osal_rcv_msg_tmo`, 56 `ioctl`. 544 of them pass a constant
   argument, 94 distinct queue ids among the OSAL calls.
3. **The constants are independently corroborated.** Queue ids that this project observed at
   runtime in the emulator appear as constants at the matching call sites: `0x008F013A`
   (E0308, 123 sites), `0x004B024E` (E0307, `UpdaterWrapper::getVersion`), `0x012C032F`
   (E0309), `0x018104BB` (E0309), `0x007A0020` and `0x007A804C` (E0612).
4. **The input handler sends to queue `0x014A0429`.**
   `snd_InfraInputEvent_to_InputServant` makes four calls, all to named functions:
   `ScalarAppfwIF::IsViewModeDiadem`, `osal_valloc_msg_wait`, `osal_snd_sync_msg` (queue
   `0x014A0429`, site `0x3cf712`) and `osal_free_msg`. E0484 reported 15 callees, two named.
5. **E0484 stands on all thirteen bodies:** zero direct calls to any `ObjDisplay_*` symbol.

## 1. Inputs

| input | detail |
| --- | --- |
| primary image | a6400 `.../nflasha15_unpacked/lib/libObj.so`, SHA-256 `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` (the E0483 image) |
| other images | twelve further bodies, identified by SHA-256 prefix in `cross_model/ipc-callgraph-bodies-e0663.tsv` |
| oracle | GNU objdump (`GNU objdump (GNU Binutils) 2.47.20260726`) `func@plt` labels and its disassembly of each site |
| tools | `tools/research/callgraph.py`, `tools/research/ipc_edges.py` as changed by this record |

## 2. The four defects

| defect | effect, measured on the a6400 image | correction |
| --- | --- | --- |
| PLT entries assumed to be 12 bytes | entries reached from Thumb carry a 4-byte `bx pc; nop` prefix (404 of 2,547), so every import after the first prefixed entry was given its neighbour's name | each entry's GOT slot is computed from its `add ip, pc` / `add ip, ip` / `ldr pc, [ip]` immediates and matched to the `.rel.plt` relocation with that `r_offset`; 2,547 of 2,547 agree with objdump |
| `bl` target computed from a word-aligned PC | only `blx` aligns; 156,131 of 555,069 targets were two bytes low | `bl` uses PC = address + 4 and stays in Thumb state |
| Thumb bit ignored when matching callees | Thumb symbol values are odd and targets were even, so no call ever resolved to a function defined in the library (0 of 555,069) | targets are reported in symbol space: a `bl` target carries bit 0 |
| every byte offset scanned | 12,767 "calls" at odd addresses, which Thumb cannot encode, and 4,214 starting inside another 32-bit instruction; 994 targets outside any code | the section is walked by instruction length from even addresses; a target outside every executable section is dropped |

`constant_arguments` had three further defects, each now pinned by a test: `movt` replaced the
value instead of setting its top half; a call between the load and the IPC call did not clobber
`r0`-`r3`; any other write to the register (`adds r0, #1`) left the stale constant in place. It
now also reads literal-pool loads (`ldr rX, [pc, #imm]`), which is how most queue ids are
materialised, and gives up when the bytes before a site do not decode onto it.

## 3. Corrected figures, a6400

| figure | E0483 / E0484 | now |
| --- | --- | --- |
| direct calls decoded | 555,069 | 538,081 |
| calls whose callee has a name | not separable from the defects | 253,541 (253,478 imports through the PLT, 63 functions defined in the library) |
| call sites inside a named function's extent | 5,821 | 5,547 |
| calls named at both ends | 106 (6,713 before the first fix) | 2,785 |
| IPC call sites | 29 | 763 |
| IPC call sites with a constant argument | 17 | 544 |
| callees of `snd_InfraInputEvent_to_InputServant` | 15, two named | 4, four named |
| direct callers of `ObjDisplay_*` | 0 | 0 |

Table: `docs/evidence/cross_model/ipc-edges-a6400-e0663.tsv` (763 rows, same header as E0483's).
The E0483 table is kept unchanged as the failed receipt.

## 4. Thirteen bodies

`docs/evidence/cross_model/ipc-callgraph-bodies-e0663.tsv`: on every body the PLT map names every
`.rel.plt` relocation (2,510-2,572), 403-408 entries carry the Thumb prefix, IPC call sites range
from 727 (a6100A) to 792 (ILCE-9M2, ZV-1) with 70-72% carrying a constant, and no body has a direct
call to an `ObjDisplay_*` symbol. The a6400 and a6600 images give identical IPC counts (763 / 544).
E0483 said the a6100A build "contains no direct `osal_snd_msg` call site at all"; it contains 182.

## 5. Method and repeatability

```bash
python3 -I tools/research/ipc_edges.py <a6400>/libObj.so --tsv > ipc-edges-a6400-e0663.tsv
arm-none-eabi-objdump -d -j .plt <a6400>/libObj.so      # oracle for the PLT map
arm-none-eabi-objdump -d --start-address=0x1871c6 --stop-address=0x1871ca <a6400>/libObj.so
A6400_UNPACKED_ROOT=<dir with unpacked bodies> python3 -m unittest tests.test_callgraph tests.test_ipc_edges
```

Every figure in §3 and §4 is a deterministic function of the image bytes; two runs gave identical
output.

## 6. Confidence and limitations

- **Verified against an independent tool:** the PLT map (2,547 / 2,547 against objdump labels),
  and the real targets of the 29 E0483 sites.
- **Corroborated by runtime observation elsewhere in this project:** six queue ids (§0.3).
- **Not verified:** each of the 544 constants individually. Constant tracking is straight-line
  over the 64 bytes before a site; a branch into that window from elsewhere is not seen, so a
  reported constant is a lead to confirm by disassembly, not a runtime value.
- **Not claimed:** that these are all the IPC traffic. Calls through wrappers, indirect calls and
  other libraries are outside the table.

## 7. Next experiment

Find the receiver of queue `0x014A0429` — the first hop from a button event — in the libraries
that call `osal_rcv_msg_tmo` with it, and walk from there toward the stored `ObjDisplay_*`
pointers that E0484 left open.
