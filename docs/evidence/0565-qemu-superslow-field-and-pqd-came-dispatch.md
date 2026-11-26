# E0565 — SuperSlow resource field and normal PQD CamCore dispatch

Date: 2026-11-17. Offline continuation of [E0564](0564-qemu-ydet-window-configuration-reaches-camcore.md).

## The zero field belongs to SuperSlow

The direct reference to predicate `0x63D3C76D` is at `0x644FE220`, slot `+0x28` of the vtable at `0x644FE1F8`. Its original RTTI resolves through `0x645427E4` to the type name `N6Camera23SuperSlowManagerMainLsiE` at `0x63F1CA9F`. This identifies the object sampled at `0x640D38C0` in E0564. The zero return is from the SuperSlow manager.

Both passive replays looking at the word at VA `0x640D39BC` / PA `0x040D39BC` recorded exactly one matching store: `0x6391EB10` initializes the word to zero. The matching stores at the adjacent words come from the same constructor. The stack argument set at `0x6391E7CA/CE` in method `0x6391E718` supplies the address of object field `+0xFC` to a call at `0x6391E7E0`, with resource kind 22. Callee `0x63CBAB98` writes the acquired pointer through that argument at `0x63CBABC2` after a successful underlying call. The original error format is `FW: SHS CamCore::acquire[%#x]`. Teardown at `0x63D3C812` clears the field.

The constructor ran once, while the SuperSlow acquire method, its resource request, and its teardown store had zero hits. The generic acquire helper ran once from another path. Forcing this object field would skip the resource acquisition and would change SuperSlow state. Its observed zero does not establish a blocker for normal-view Ydet.

## The normal VD_Pre path continues

In a third bounded replay, the instructions following the return from SuperSlow (`0x63927394` and `0x639273A4`) each executed 276 times. The normal state-update path at `0x639276C4` also ran 276 times. The list walker called from `0x639273A4` had 276 entries and zero virtual callback dispatches at `0x6377AEA6`. Its runtime count at PA `0x041E348C` was zero. The associated initializer at `0x6377B258` takes the count from three original enable bytes. The relationship between this list and the Ydet producer has not been established.

## Original Ydet publication chain

| Site | Original instruction evidence |
| --- | --- |
| `0x63902DC2`, `0x63903048`, `0x63905320` | Pass Thumb callback pointer `0x639023AD` at outgoing stack offset `0x14` to `0x63755FE8`. The fourth direct call site `0x63903142` zeroes that slot. Related sites `0x63902E60` and `0x63902ED0` call different CamCore entry points. |
| `0x639023AC` | Reads the sequence halfword from its metadata block, passes it to `0x63D4683C`, and tail-calls `0x6392280C` with the block. |
| `0x6392280C` | Forms a descriptor containing Thumb callback `0x639219B5` via `0x63D3D95C` and calls `0x63921FF8`. Through `0x6393FF78` this builds a message and calls the job submission helper `0x63D404BC`. |
| `0x639219B4` | Has the original debug string `EC::PQD_CamE`. Both paths through its optional work converge at `0x63921A12`. The tail branch at `0x63921A22` invokes `0x63D3DD22` with the getter handle at `*(0x6434CA54+8)+0xFB0` and the incoming metadata. E0563 traced the next call into the Ydet producer. |

The original RTTI arguments at `0x63756020` identify a cast from `CamCore::CoreController` to `CamCore::EEController`. The caller at `0x63905320` has the failure format `FW: CamCore 2ndParamSetError 0x%x`. In the first submission trace, the generic wrapper ran 224 times and both sampled direct callers (`0x63902DC2` and `0x63905320`) had zero hits. The actual Ydet callback and producer also had zero hits.

The all-site replay counted 206 calls and returns at `0x63903048/4C`, the same number of wrapper entries at `0x63755FE8`, and 206 tail calls into `0x63CBCA06`. The wrapper's early error return at `0x6375604A` had zero hits, as did the other three direct call sites. This confirms that normal CamCore jobs carry the required callback pointer through this wrapper. The downstream return status was not sampled. The callback at `0x639023AC`, `EC::PQD_CamE`, and the Ydet producer each had zero hits. QMP again showed the step-32 request `0x032E` and report `0x0326`, and QEMU exited 0 on QMP quit. Follow `0x63CBCA06` into the EE controller and verify its return and the completion event before modelling a device response.

The [pinned a5000 live-view investigation](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/docs/RE_LIVEVIEW_REQUEST.md) supplies a normal imager-job lead and documents its later step-33 wait. The a6400 is still at step 32. The a5000 receipt does not establish which a6400 event completes the CamCore job.

## Reproduction

QEMU binary `2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`, ILCE-6400 ORIL `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`, E0561 legacy inputs and the existing `EVSEL_LATE_US=500` phase assumption. The bounded replays used fresh qcow2 overlays, passive QEMU TCG plugins and HMP read snapshots via QMP at 70 s. The lookup and jump targets above were checked against original Thumb bytes using GNU objdump. Execute each retained `replay.py` with `python3 -u` under WSL from the project root. Receipts are under `artifacts/qemu-a6400/camcore-readiness-writer/`, `camcore-superslow-path/`, `vd-pre-callbacks/` and the PQD follow-up run directories. No device state or guest memory was injected.
