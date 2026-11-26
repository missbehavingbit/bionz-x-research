# E0557 — QEMU `sony-a6400`: source 30 arrives before source 29, so `VD_Sequence` never runs; ordering them passes step 12

Date: 2026-11-17. **Strictly offline.** Builds on E0553 and E0544.

Labels: [C] confirmed by disassembly or a run receipt, [I] inference, [A] assumption.

## 0. Short answer

Module 1's reported halfword at `0x64351F28` has a third writer that E0553's watchpoint never saw: `0x63923A1A` in `VD_Sequence` (`0x63923874`). `VD_Sequence` was never called in a default boot: 0 hits over 39 engine frames [C]. Only the HS root callback `0x639242D8`, in HS slot 0, posts it. The HS root is registered on timing-event source 30, and the frame start `0x63924A38`, which resets the HS slot byte, is registered on source 29 [C]. The evsel model fires every enabled channel in one pulse. Source 30 holds the lower channel (5, against 6 for source 29), so the HS root always runs first and reads the previous frame's slot 3 [C]. The opt-in evsel properties `late-src=30 late-us=500` deliver source 30 500 µs after the rest of the pulse. With them, `VD_Sequence` runs on time once per frame and module 1 reports `0x0118`. The sequencer passes step 12 and stops at step 32. The VPH YUV layer ch51 is now programmed [C]. The 500 µs phase is an assumption [A].

E0553's open question is also closed. The byte at `0x64354174` is `+0x3270` of the camera block `0x64350F04`. `int_HS` sets it to 1 once per frame [C], so the stall sample of 0 was a mid-frame read.

## 1. Static map (av-cam `90d515cf…`, linked at `0x635C6000`)

The image is position independent. Addresses come from `ldr rX, [pc, #imm]` followed by `add rX, pc`, and from GOT slots off `r4`/`r5` = `0x645A21E4` [C]. A plain literal search for `0x6434CA54` finds nothing.

| site | role | evidence |
| --- | --- | --- |
| `0x63926EE0`–`0x63926F12` | `memcpy(0x6435000C, 0x6435416C, 0x304)`, `memcpy(0x6434C998, 0x6434CA54, 0xBC)`, then `0x6434C998+0x24 = 0x6435000C`. `0x6435416C` is `0x64350F04 + 0x3268` | disasm [C] |
| `0x63925480` | `int_HS` ("FW: int_HS timing error %d/%dV->%d/%dV"). Every `*(0x64542A58)>>2` ticks it increments `+0x3269`. When `+0x3269 + 1 >= +0x3268` it stores 1 to `+0x3270` (`0x639254CA`). It re-arms itself through `0x63763500(1, …)` | disasm, strings [C] |
| `0x63924A38` (store `0x63924C48`) | frame start: clears `+0x3269` and `+0x3270` | disasm [C] |
| `0x639242D8` | HS root. It reads the slot byte `+0x3269`. Slot 0 with `*(*(0x6434CA54+8)+0x90) != 0` posts `VD_Sequence` (functor `0x63923875`), and with 0 it posts `HS_Sequence(V<<8)`. Slots 1–3 go to `0x639243A6` | disasm [C] |
| `0x639244D4` | `HS_Sequence` ("FW: HS_Sequence: start delayed …", "HS2_Rot S", "HS3_Rot S") | strings [C] |
| `0x63923874` | `VD_Sequence` ("VD_Seq S", "FW: VD_Sequence: start delayed …", "VD_Pre wait timeout") | strings [C] |
| `0x63923A0E`–`0x63923A1A` | `r0 = 0x638F8FB0(…)`, `strh r0, [0x64350F04+0x1000, #0x24]` = `0x64351F28` | disasm [C] |
| `0x638E9CD0`–`0x638E9D3C` | `0x63763604(src, 1, cb)`: GOT+`0x5294` (`0x63924A39`) on 29 and 31, GOT+`0x9EC0` (`0x639242D9`) on 30, GOT+`0x9220` on 23 | disasm, GOT words `0x645A7478`, `0x645AC0A4` [C] |

A crude constant-propagation scan from every PIC base finds exactly three static stores to `0x64354174`: `0x639221BC` (init), `0x63924C48` (frame start, 0), and `0x639254CA` (`int_HS`, 1) [C]. It finds two stores to `0x64351F28` through that base, `0x63923A1A` and `0x639267BC`. The third, `0x63922304`, is the init seed E0553 already caught [C].

## 2. Runs

Host: WSL, `~/sony-a6400-qemu/build/qemu-system-arm`, `run.sh` legacy inputs (`av-cam.bin` `90d515cf…`, sub-micro page `14fba4e6…`, ZV-E10 `ulogio.bin` stand-in). All runs used `tools/a6400_probe.py --until SetContentTarget`. Breakpoint runs added `-gdb tcp::12345` and the new read-only RSP client `qemu-a6400/tools/rsp_hitcount.py`. That client inserts TCG breakpoints, records registers, steps the stopped thread, and continues. It writes no guest memory or registers.

### 2.1 Default model, breakpoints (`e0555-rsp.log`, 200 s; `e0555b-rsp.log`, 150 s)

| site | hits | notes |
| --- | --- | --- |
| frame start store `0x63924C48` | 39 | |
| `int_HS` `0x63925480` | 268 | 7 per frame, counter step `0x104A` µs |
| enable set `0x639254CA` | 38 | once per frame, after the 6th `int_HS` |
| enable check `0x639267A4` | 154 | r3 = 1 on the check right after the set |
| HS root `0x639242D8` | 34 | |
| HS root slot-0 branch `0x63924352` | **0** | |
| `HS_Sequence` `0x639244D4` | 102 | args `V<<8|2` and `V<<8|3` on time; a second `V<<8|3` after the HS root takes the delayed path `0x63924568` |
| `VD_Sequence` `0x63923874` | **0** | |
| publish `0x63923A1A` | **0** | |

The recorded order is `… hsseq(2) hsseq(3) hsroot hsseq(3)-delayed …`. In run 1, frame start captured counter `0x000E41B9`. In run 2, the HS root captured `0x000E41A8` in the corresponding frame. Both come from the same model pulse [I].

### 2.2 Channel map (`e0555c-probe.log`, no gdb)

At `SetContentTarget`, HMP `xp` gave `0xF3C30010 = 0x000000F0` and `0xF3C30050..5C = 0x17 0x1E 0x1D 0x1C`. So ch4 = source 23, ch5 = 30, ch6 = 29, ch7 = 28 [C]. Source 31 is not active at that point [C]. `0x64330614` = 12, and the requests are `0105 0285 0320 0400` [C].

### 2.3 `late-src=30 late-us=500` (`e0555d`, `e0555e`, `e0555f`)

The model change (§3) was rebuilt with `ninja qemu-system-arm`. Binary `2720703c…` replaces `78da6428…`. The pre-change source (`2bb09938…`) and binary are kept as `~/a6400-runs/evsel.c.pre-e0555` and `qemu-system-arm.pre-e0555`. Renumbering to E0557 changed only comments: the rebuilt binary hash stayed `2720703c…`.

With breakpoints, 120 s (`e0555d-rsp.log`): HS root 32, slot-0 branch 32, direct `VD_Sequence` post 31, `VD_Sequence` 31, on-time branch `0x63923914` 31, delayed branch 0, publish 31 [C]. The order is `hsseq(2) hsseq(3) hsroot vdqsel vdqdirect vdseq vdontime publish` every frame [C]. The first four publishes store `0x0100`. At the monitor dump after detach, `0x64351F28 = 0x0118` [C].

Without breakpoints:

| run | sample | index `0x64330614` | requests (modules 1–13) | reported |
| --- | --- | --- | --- | --- |
| `e0555e` | checkpoint 48.0 s | 12 | `0105 0285 0320 0400 0500 0600 0780 0000 0910 0A10 0B00 0C01 0D00` | module 1 `0100` |
| `e0555e` | ~2 s later | 28 | `0118 0282 0326 0480 0580 0600 0702 0000 0920 0A83 0B00 0C01 0D00` | |
| `e0555f` | checkpoint 44.6 s + 30 s | **32** | `0118 0282 032E 0481 0580 0680 0702 0000 0920 0A83 0B00 0C01 0D00` | `0118 0282 0326 0481 0580 0680 0702 0000 0920 0A83 0B00 0C01 0D00` |

The step table at `0x63E80F18` (43 entries) is [C]:

```
 0 0105 0285 0320 0400 0500 0600 0780 0910 0A10 0B00 0C01 0D00   12 WAIT{1,2,3,7,9,10}
13 0281 0323 0480 0580 0702 0920 0A11                              20 WAIT{2,10}
21 0118 0A82                                                       23 WAIT{10}
24 0282 0A83                                                       26 WAIT{3}
27 0326                                                            28 WAIT{2,3,4}
29 032E 0481 0680                                                  32 WAIT{3,4,5,6,10,12}
33 0102 0202 0302 0405 0502 0602 0A00 0B10 0C02                    42 WAIT{1..7,9,12}
```

At step 32 the only masked mismatch is module 3: requested `0x032E`, reported `0x0326` [C]. Imager module 10 reports `0x0A83` [C].

The VPH log changed. Before, it printed `YUV layer still off … ch51=0x00000000`. Now it prints `ch51 base=0x14fbc000 lb=0x400 skip=0x80 lines=570 en144=0x233`, with base alternating between `0x14fbc000` and `0x14e90000` (`e0555e`) [C]. At +30 s the IRQ counts are liro-83 1889, liro-84 11301, liro-98 948, and liro-35..38 and liro-51..56 all 0 (`e0555f`) [C]. No oops or panic appears in the console log [C].

### 2.4 Default model after the rebuild (`e0555g-default.log`)

Same binary, no `-global`: the probe ended with status 2, meaning the shell went silent for 137.9 s before the checkpoint (compare E0530). The monitor dump at the end shows index 12, step-0 requests, and module 1 reporting `0100`. The console printed `YUV layer still off at vsync 600/3600 … ch51=0x00000000` [C]. With the property off, the result is the pre-change step-12 wait.

### 2.5 Through `run.sh` (`e0555h-runsh.log`)

`EVSEL_LATE_US=500 python3 tools/a6400_probe.py --until SetContentTarget --cmd "sleep 25" --monitor "xp /20wx 0x04330610"` reached the checkpoint at 44.4 s, status 0. The dump shows index 32 with the same request and report words as `e0555f`, and three `ch51 base=0x14…` lines [C]. That is two of two no-gdb opt-in boots at step 32.

## 3. Model change

`qemu-a6400/qemu-overlay/hw/misc/cxd90014_evsel.c` gets two properties, `late-src` (default 0 = off) and `late-us`. When `late-src` is non-zero, `ev_frame()` fires every enabled channel except those whose `+0x40+4k` source id equals `late-src`. A one-shot timer fires those channels `late-us` later, with their own captured counter. With `late-src = 0` the code path is the old single pulse. `run.sh` passes the properties only when `EVSEL_LATE_US` is set.

Why it is opt-in: the firmware requirement is confirmed. The HS root acts only in slot 0, the frame start sets slot 0, and the first `int_HS` slot step follows about 4170 µs later. The hardware phase between sources 29 and 30 is not measured [A]. 500 µs is any value inside that window. The a5000 model shares the single pulse. Its notes do not name source 30's phase.

## 4. Boundary

- Nothing wrote guest RAM or registers. No firmware input changed. `0x0105` was never injected.
- `VD_Sequence`'s first publishes store `0x0100`, and module 1 later reports `0x0118`. The calls between those were not traced.
- The imager power path is not shown to run. liro-38 (P2S) and liro-51 (SA0) stay at 0, so module 10's `0x0A83` comes from its own state word, not from a powered sensor [I].
- The ch51 frames were not dumped. Their content is unknown.

**Next:** module 3 at step 32. It reports `0x0326` against request `0x032E`. Find its state word and the job that moves `0x26` to `0x2E` (a5000 analogue: `RE_LIVEVIEW_REQUEST.md` §3, where module 10 waited on an SA job). Then check whether the ch51 buffers hold anything.

## 5. Receipts

`artifacts/qemu-a6400/e0557/` (run tags keep the working name `e0555*`): `e0555-rsp.log`, `e0555b-rsp.log`, `e0555d-rsp.log`, plus trimmed probe outputs. The full console logs stay in `~/a6400-runs/` (WSL).
