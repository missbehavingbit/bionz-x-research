# E0561 — QEMU `sony-a6400`: module 3 holds step 32 because no PQD Ydet statistics record ever arrives

Date: 2026-11-17. **Strictly offline.** Builds on E0557. All runs use the opt-in `EVSEL_LATE_US=500`. E0558–E0560 are separate Unicorn records, so this result is E0561.

Follow-up: [E0563](0563-qemu-ydet-adapter-and-opd-comparison.md) found a direct Thumb call to the producer adapter at `0x63D3DD36`, superseding this record's negative cross-reference search.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

At step 32, module 3 reports `0x0326` against request `0x032E`. Module 3 accepts `0x32E` only after a per-frame counter passes 6. That counter increments only on frames that bring a new, valid PQD Y-detection (luminance statistics) record. The record producer, in `PQD_YdetRegReadManager.cpp`, never runs. Its consumer reads the zero-initialised record every frame. No device model changed in this record.

## 1. Chain, from the sequencer back to the missing input

| step | site | what it does | evidence |
| --- | --- | --- | --- |
| report | publisher `0x63926B00` | Module 3 (index 2) reports `*(u16*)*(0x6434CA54+0x0C)` = `0x64353570` while bit 2 of `0x643334E8+0x8E` is clear. Each load in that block feeds the *next* setter call | disasm; `0x04333576 = 0x0000` [C] |
| writer | `0x63DBDC18` (from `0x63B8D408`) | the only writer of `0x64353570`: `0x0300 → 0x0320 → 0x0323 → 0x0326`, then no change (228 writes) | Z2 watch `e0558c` [C] |
| transition | `0x63BB3028` | `tbh` on the requested state (`req − 0x301`, 143 cases). A case writes the new state only when an input completion field is set. `0x326` needs `in[7] == 1`; `0x32E` (`0x63BB3914`) needs `in[0x4F] != 0` | disasm; breakpoints hit the `0x326` and `0x32E` cases [C] |
| input | `0x63BB2D00` | `in` is a stack struct (`0x60907E34`); `in[0x4F] = obj[0xBC8]`, `obj = 0x643ED99C` | disasm; `r5 = 0x643ED99C` at `0x63BB2E4C`, flag read 0 on 87 frames (`e0558g`) [C] |
| copy | ARM `memcpy` `0x63AB8098` | `0x643ED99C` and `0x643F0F38` are copied into each other with delta `0x359C`. At the `stm` the source is `r1 − 16`, because `ldm r1!` runs first | Z2 hits at `0x63AB80CC`/`0x63AB80D4` in `e0558f`/`e0558g`/`e0558j` [C] |
| flag rule | `0x63BC5E7C` (store `0x63BC5F2E`, `r6 = 0x643F0F38`) | When `[r6] == 0x32E`: `a = b2C6 ? b2C7 : 0`. The flag is `a` while counter `[r6+0xE42] ≤ 6`, else `a ? a : 1`. Then `*(0x64429E48+0x52)` == 1 forces 0, == 2 forces 1 | disasm; at the `0x32E` branch, counter = 0 and `a` = 0 on 18 of 18 frames (`e0558i`) [C] |
| counter | `0x63BC6200` | Its counter pointer is `0x643EE7DE` (`0x643ED99C+0xE42`). It increments when output byte `ce` of `0x63BEDD04` is set. It clears unless the request argument is `0x32E` | disasm; stack args `643539F8 643EE7DE <req>`; req reached `0x032E` on 31 frames (`e0558l`); only clears and no increments at `0x643EE7DE` (`e0558k`) [C] |
| new-data test | `0x63BEDD04` → `0x63BEE788` | Copies the current `0x148`-byte record (two 40-word arrays, then `+0x140`, `+0x141`, seq `+0x142`, valid `+0x144`) out of a ping-pong pair at `GOT[0x54B4]` (index `GOT[0x357C]`). `ce = valid && !+0x141 && seq != last` | disasm; consumer hit 195 times, valid read 0 (`e0558m`) [C] |
| producer | `0x63BEE670` (via adapter `0x63DC6F58`) | Builds the record from `0x63DC7880(handle, 0/1)` buffers, sets valid = 1, writes it through `0x63BEE874`, toggles the index | disasm; assert file `PQD_YdetRegReadManager.cpp` [C]; **0 hits** in `e0558m` [C] |

Related strings in the image: `CamCore::DevYdetGetter`, `CamCore::DevYdetSetter`, `job_PQD_YdetGetLatestVal`, `PQD_YdetRegValBufWrapper.cpp`, `idt_YDET_SetWaku_api.cpp`, `YDET/EC_YDET.cpp`, `CUS_YDET_PASS_{ON,CLR,INSEL,RGB,XSD_HD,LOG_ON,OFFSET,GAIN,LPF_ON,SKIP_MODE,MODE,FLD}_CTL`, `CUS_YDET_PASS_AREA_SHOW` [C]. The adapter `0x63DC6F58` (`producer(msg[4], msg[0], (u16)msg[8])`) has no absolute, GOT, PIC-literal or `bl`/`b.w` reference that a whole-image scan finds [C]. How it is reached is not known.

The a5000 equivalent is the OPD statistics block, `0xF3030000/0xF3031000`, IRQs 67–72. Upstream records that it never fires (`RE_LIVEVIEW_IMAGE.md` §2.1, `RE_SENSOR_ISP.md` §2.5 at upstream HEAD). That sequencer's step table has no `0x32E` state, so it never needed the block [C]. Whether the CXD90045 Ydet engine sits at the same addresses is not checked [A].

## 2. Runs

Same host, binary `2720703c…`, legacy `run.sh` inputs as E0557, with `EVSEL_LATE_US=500`. Breakpoint and watch runs used `qemu-a6400/tools/rsp_hitcount.py`. This record adds `--wp addr:len[:label]` (Z2 write watchpoints: remove, single-step the thread, read the new value, re-arm) and `--stack N`. The client still writes no guest memory or registers.

| run | probe | content |
| --- | --- | --- |
| `e0558a` | status 2 (shell silent 138.9 s) | `0x6434CA54` context words, selector 0, index 32 |
| `e0558b` | 0 | watch `0x641F3940`: that is module 2's source (`0x0282`), not module 3's (slot misread corrected in §1) |
| `e0558c` | 0 | watch `0x64353570`: writer `0x63DBDC18`; final `0x0326`; index 32 |
| `e0558d`/`e0558e` | 0 | `0x326`/`0x32E` cases hit; the four `+0xBC8` store sites: only `0x63BC5F2E` runs, on `0x643F0F38` |
| `e0558f` | 3 (port in use) | the watch attached to the previous run's QEMU (same configuration) and caught the `memcpy` into `0x643EE564` |
| `e0558g`/`e0558h` | 0 | `0x643F1B10` writers; `GOT[0x6DC]` object `0x6442D708` — a side path (`+0xBD8`), not the flag |
| `e0558i` | 0 | `0x32E` branch: counter 0, `a` 0 |
| `e0558j`/`e0558k` | 0 | counter copy and source: only `memcpy` and the clear at `0x63BC6530` |
| `e0558l` | 0 | `0x63BC6200` stack args per frame, request `0x0300…0x032E` |
| `e0558m` | 0 | producer 0 hits, consumer 195 hits with valid 0 |

The `e0558f` collision came from starting a run before the previous QEMU had exited. Later runs wait until no `qemu-system-arm` process remains.

## 3. Boundary

- No device model, firmware input or guest state changed. No statistics were injected.
- How the Ydet producer is triggered is not identified: interrupt, message source or register block. Neither is the record's data format beyond the layout in §1.
- `0x64429E48+0x52` (force 0/1) and `obj+0x2C6/0x2C7` were read only as 0 on the `0x32E` path.

**Next:** find what posts the message that reaches `0x63DC6F58`, then the register block and IRQ behind `DevYdetGetter`/`CUS_YDET_*`. Start with a breakpoint on the `job_PQD_YdetGetLatestVal` path and the a5000 OPD addresses. A model may only report completion with data the firmware can validate. A plausible-looking fake luminance record would be fabricated input. Decide that contract before writing any model.

## 4. Receipts

`artifacts/qemu-a6400/e0561/` (run tags keep the working name `e0558*`): the RSP logs listed above, `e0558a-probe.trim.txt` and `identities.sha256`.
