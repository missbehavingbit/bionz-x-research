# E0564 — QEMU Ydet window configuration reaches CamCore

Date: 2026-11-17. Offline continuation of [E0563](0563-qemu-ydet-adapter-and-opd-comparison.md).

## Runtime path

Two bounded QEMU replays sampled the single-window path without stopping or modifying guest execution. The first replay counted 190 visits each to the descriptor lookup `0x63BED97E`, its return at `0x63BED982`, and the call to write window coordinates at `0x63BED98A`. The null-descriptor target `0x63BED9C2` received zero visits. The second replay counted 198 visits to each of the same sites. The producer at `0x63BEE670` remained idle in both runs, and the mode sequencer remained at step 32 with request `0x032E` and report `0x0326`.

E0563 sampled the window setter handle as `0x6433E538`. For window 0, `0x63DC77EA` computes `handle + 0x4BC` and uses the pointer one word into that descriptor. The snapshots at PA `0x0433E9F8` contain `0xF3000000` at that field. `0x63CC8054` gets this base through `0x63CBE090`, then calls four accessors at `0x63CC7F7E/86/8E/96`. Their original Thumb stores target base plus `0x15848/4C/50/54`.

The observed window setter handle and the producer's result handle require separate identification. The objects at the setter handle's `+0x2B8/+0x2C4` have vtable pointers `0x644FA208/0x644FA3B8`. Their original RTTI identifies them as `DevAcSetter` and `DevAwcSetter`. Those locations therefore cannot be read as the Ydet result descriptors described in E0563. The first physical trace captured their initialization stores and the later assignment of `0xF3000000` to the `DevAwcSetter` object. The separate result accessor at `0x63CC7F9E` forms an address at its supplied base plus `0x15700` and requests a length of `0xA0` bytes. Its actual caller and base require runtime verification.

A PC-filtered memory callback confirmed 32-bit writes at PA and VA `0xF3015848/4C/50/54` with values `0x0E/0x640/0x02/0x23A` in the first eight recorded samples. The corresponding four instructions ran 178 times each. The earlier I/O-only filter omitted these accesses, so its zero output cannot establish absence of MMIO activity. The board currently covers the area with its unimplemented peripheral region.

A second trace watched stores of the original `DevYdetGetter` vtable pointer `0x644FDB98`. Constructor instruction `0x638B0460` initialized seven pairs. The pair at `0x6433F28C/0x6433F298` is at offsets `0x2B8/0x2C4` of `0x6433EFD4`, the third argument of the E0563 Ydet setup call. This identifies the result-side handle candidate independently of the setter object. The runtime call into the result publisher remains unobserved.

The binding trace recorded the firmware assigning `0xF3000000` to the first getter's base field at `0x6433F290` and leaving the second base at zero. Three other sampled getter pairs had the same final binding. With that first getter base, the result accessor's `+0x15700` calculation yields `0xF3015700`. Its entry, the producer and its adapter caller had zero hits. This address follows from the binding and instructions. A result read still has to be observed.

## CamCore start gate

Follow-up [E0565](0565-qemu-superslow-field-and-pqd-came-dispatch.md) identified this specific gate as the SuperSlow resource check. Normal `VD_Pre` continues after it, so these observations do not establish a causal blocker for normal-view Ydet.

In the binding trace, the `VD_Pre` gate function `0x6391E614` and the return from its first virtual call at `0x6391E622` each ran 259 times. The fall-through after the zero check (`0x6391E626`), the later flag check (`0x6391E680`) and the final start call path (`0x6391E68A`) all had zero hits. This narrows the gate identified in E0543 to the return value of virtual slot `+0x28` in this run. Separately, the imager function identified in E0543 at `0x6379093C` ran 211 times, called its virtual slot `+0xFC` and took its return-zero path each time. The sampled hardware work at `0x63790B42` did not run. Function entry therefore cannot serve as evidence that the sensor started.

The loopback breakpoint run under `artifacts/qemu-a6400/camcore-gate-target/` sampled two calls at `0x6391E620` and their returns at `0x6391E622`. Both called Thumb target `0x63D3C76D` on object `0x640D38C0` and returned zero. The target at `0x63D3C76C` reads the word at object offset `0xFC` and returns whether it is nonzero. Thus the field at VA `0x640D39BC` was zero in both samples. The 58 s snapshot from this instrumented run was at step 28. The 70 s passive run establishes persistence of the same early gate at step 32. QEMU and the breakpoint client exited with status 0.

## Upstream comparison

[ILCE-5000 CamCore front end reference](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/misc/cxd90014_campig.c) identifies `0xF3000000` as front-end unit 0 and models frame completion and acknowledgements in the first 256 bytes. Its [sensor pipeline notes](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/docs/RE_SENSOR_ISP.md) identify `0xF3010000` as a sub-block of the same unit. This matches the a6400 setter base and calculated window addresses. The reference front-end source does not implement the four Ydet window accessors or establish the a6400 result-completion trigger.

## Method

- Firmware ORIL SHA-256 `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, QEMU binary `2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`, E0561 legacy inputs, `EVSEL_LATE_US=500` (phase assumption), virtual `icount` and fresh temporary qcow2 overlays. Complete input identities are in `artifacts/qemu-a6400/capture-20261117-retry/input-identities.sha256`.
- Run the `replay.py` scripts under `artifacts/qemu-a6400/ydet-descriptor/`, `ydet-campig/`, `ydet-stores/`, `ydet-getter/` and `ydet-getter-binding/` with `python3 -u` in WSL from the repository root. The scripts build local observation plugins, count selected ORIL Thumb PCs, issue bounded HMP RAM reads through QMP at 70 s host time, then shut down QEMU. Each returned QEMU exit 0. Firmware and guest RAM remained observational inputs. Static accessors were checked with bounded GNU Thumb disassembly and type names with the original vtables and RTTI.
- Next, trace the guest writer of `0x640D39BC` and the event that permits CamCore to start. Verify the result handle that reaches `0x63D3DD22` and the front-end event that produces a result before modelling any completion.
