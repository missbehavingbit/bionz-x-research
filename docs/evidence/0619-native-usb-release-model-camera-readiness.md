# E0619 — Native USB release waits for ModelCamera readiness

Date: 2026-11-19. Scope: offline captured-firmware QEMU observations and bounded
static analysis. No physical camera, USB device or physical SD was accessed.

## Result

E0613's original USB-inhibit clear helper remains uncalled, but its enclosing
still-recording initialization paths are now observed. They stop at an original
model-readiness check: binding group `1` queries `0x0F` (`ModelStillRec`) and
`0x0B` (`ModelCamera`). The latter returns a valid Boolean false. This is an
application initialization dependency, not a lost USB message or failed query
transport. It does not yet establish the missing hardware contract.

The P display, the sampled ROOT `0x102=2`, and optional luma do not imply this
separate readiness condition is satisfied. No guest readiness, init-done,
physical-key state, event, or firmware instruction was changed by these probes.

## Inputs, tools and bounded policy

The captured profile is the E0611/E0613 profile, hash checked by every generator:

| Input | SHA-256 |
| --- | --- |
| Captured libObj.so | `e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1` |
| Captured viewUnified2.so | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` |
| Captured libUsbControl.so | `b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2` |

The kernel, av-cam and backup identities remain those recorded in E0611.
QEMU 11.1.3 SHA-256 is
`e4fa5e6814b7522309c77843a87ec1ecbd9cedda98a99b27d80a0dcd4d9e6104`.

`docs/RELATED_REPOSITORIES.md`, `docs/REA_WORKFLOW.md` and
`docs/DEPENDENCY_WORKFLOW.md` were consulted. No upstream claim supplies a new
local contract. The configured current Ghidra installation is unavailable;
the explicit existing legacy Ghidra 12.1.4/JDK 21 profile uses a disposable
raw Thumb import, no global analysis, bounded query windows, and retained
source hashes/commands/results. Tail-call pseudocode that runs into mixed-state
PLT bytes is rejected even when the batch reports completed. Native instructions
and mapped runtime observations provide the result.

Passive TCG probes validate 32 original instruction bytes, then record registers
and bounded user-RAM snapshots (at most 176 bytes here). Records are resolved
against captured executable mappings. The status-only generation filters opcode
`0x7FFFFFFF` before its 4096-per-CPU/site counter. Probes do not execute guest
helpers or supply return values.

Replay policy: `REAR_CONTROLS=1 USB_VBUS_TEST=1 USB_CID_HIGH=1
EVSEL_LATE_US=500`, empty ULOGIO, unique temporary run/overlay, QEMU
`-icount shift=0,sleep=on -net none -no-reboot`. After native boot and map
capture, the existing center-key action dismisses the notice. QMP sets VBUS
`true,true,false`; the duplicate produces no extra edge. The existing handler
at `0x602AD2AC` observes exactly `3,2` at `0xF2920010`. Each run has a 240-second
outer deadline and quits only its owned VM. This is a budget, not elapsed-time
or timing-model proof. The final pair differs only by `LIVEVIEW_SOURCE=none`
versus the existing opt-in `bars` source.

## Original release path

`e0614-native-usb-release/` retains paired none/bars replays. In each:

- The original still-recording factory is observed once, and helpers
  `view+0x1C67C0` and `view+0x1C6E80` each execute four times.
- Their object fields show `+0x228=1`, `+0x22C=0`, and byte `+0x189` becoming 1.
  The relevant branch is `((word228 != 0 && byte189 != 0) || word22C != 0)`.
- The first test, `view+0x1A1764`, passes with result 0 (two first-path and
  three second-path return observations).
- The second test, `view+0x1A07D0`, returns 1 at `+0x1C6844` twice and
  `+0x1C6FFC` three times, blocking the native release.
- `view+0x41AD18` and the release call sites `+0x1C6856`/`+0x1C7014` remain
  unobserved. On the second path, the original `setInitDone(true)` call would
  precede release after both checks pass; neither is forced by the host.

The helper `+0x1A07D0` first calls `ViewBase::IsModelPrepare(this,1)`.
When unprepared it returns the blocking result immediately. Only a prepared
view reaches `CmnViewInhUtil::isFactorOn(0x3A0,0x63F,true)`. In the separate
factor replay, prepare returns false 129 times and true once; that sole factor
query returns 0. Thus an active factor is not the observed persistent blocker.
The instruction at `+0x1A080A` is before the final move into r0 and is not
interpreted as a return value.

## Binding, transport and actual provider

The `e0614-model-prepare/` replay records the binding group word, view pointer,
binding record pointer and stack frame at the query and both return sites.
`audit.py` pairs them before the next query with all three identities equal:

| Group 1 dependency | Typed Boolean | Paired queries |
| --- | --- | --- |
| `0x0F` | true | 131 |
| `0x0B` | false | 129 |
| `0x0B` | true | 2 |
| `0xD4` | true | 2 |

All 264 paired queries return transport success 1 at `libObj+0x3ED530`.
The typed Boolean is recorded at `+0x3ED53E`. This distinguishes a valid
not-ready answer from an absent model or unsupported response. Initial true
answers are retained; readiness is not described as permanently false.

The original call chain is:

1. `ViewBase::IsModelPrepare` (`libObj+0x3ED674`) finds the declared group record.
2. `+0x3ED4E4` iterates declared model IDs and queries opcode `0x7FFFFFFF`.
3. `ModelManager` lookup at `+0x849520` resolves a live node and checks its state.
4. Dispatch at `+0x841156` loads the original owner from node `+0x1C` and invokes
   its vtable `+0x2C` method.
5. Both models use the original common method `libObj+0x3FDC80`. At `+0x3FDC98`
   it reads owner byte `+0x64` and constructs Boolean parameter 0, then returns
   native success 0. The manager converts that to query success 1.

The status-only owner replay observes 138 `0x0B` calls and 190 `0x0F` calls.
The runtime owner vtable minus mapped libObj base resolves through the original
ELF typeinfo to `11ModelCamera` and `13ModelStillRec`, respectively. The audit
uses PT_LOAD virtual-to-file translation: writable data has a different file
offset, so it is not read as a raw VA index.

The final `e0614-camera-ready/` none/bars pair records the complete 128-byte
owner prefix and the exact native load result. Each has 343 status calls;
every snapshot byte `+0x64` equals r1 after the original load:

| Model | Ready byte | Calls per run |
| --- | --- | --- |
| `ModelCamera` / `0x0B` | 0 | 134 |
| `ModelCamera` / `0x0B` | 1 | 4 |
| `ModelStillRec` / `0x0F` | 1 | 190 |

Other models account for the remaining calls. Neither status log cap is
exhausted. The paired luma policy does not remove this dependency.

The separate `e0619-model-ready-writer/` replay observes four calls to the
original setter `libObj+0x3FE020` for the same `ModelCamera` object. Requested
values and native post-store byte readbacks are both `1,0,1,0`. Return offsets
are `0x4AA106,0x4A9EFE,0x4AA106,0x4AA0A4`. Thus the final false value is an
original application store, not merely an uninitialized byte or failed query.
The final call instruction at `+0x4AA0A0` explicitly passes false. Its bounded
listing branches there when byte `this+0x61E4` is nonzero or stack word
`[r7+4]` equals 1. The exact source/meaning of these inputs remains open;
their values were not captured at that branch in this generation. Nearest
exported objdump labels are not used to name this internal function.

## Receipts, failures and next boundary

All paths below are under `artifacts/qemu-a6400/`:

- `e0614-native-usb-release/`: native release pair and bounded Ghidra receipts.
- `e0614-model-factor/`: preparation/factor separation.
- `e0614-model-prepare/`: exact dependency query/transport/Boolean pairing.
- `e0614-model-owner/`: discovery owner trace; its generic dispatch probe
  exhausts its 4096-row cap and is not used to prove absent later calls.
- `e0614-model-status/`: status-only provider/type qualification. Its unqualified
  view-site zeros do not prove a missing view; view claims use the mapped pairs.
- `e0614-camera-ready/`: final byte/load pair, audit and rendered captures.
- `e0619-model-ready-writer/`: continuation at the native readiness writer.

Artifact names beginning `e0614-` were created before the separate contributor
records E0614–E0618 appeared. They are preserved without renaming or claiming
those evidence IDs; this research record is E0619.

Reproduce into new directories, retaining generators and original hashes:
`python3 -m artifacts.qemu-a6400.e0614-camera-ready.prepare`, compile its passive
plugin against the retained QEMU 11.1.3 header, run the `replay-vbus` module, then
`correlate`. For the pair set `LIVEVIEW_SOURCE=bars E0611_RUN_LABEL=bars`.
The replay refuses to overwrite an existing labeled directory.
Run `python -m artifacts.qemu-a6400.e0614-camera-ready.audit` from the repository
root. The audit checks original identities, release outcomes, exact query
pairing, provider/type resolution and native byte loads.

Nine bounded replays complete with both owned exit codes zero and exactly two
native VBUS edges. The audit passes **1677 receipt checks**, including recomputed
retained plugin/source identities. The tool manifest retains 39 identities
and 25 Python syntax checks under `e0619-model-ready-writer/`.
`host-tool-failures.json` preserves inline quoting, initial audit-schema and
path/patch preflight failures; these are host-only failures, not discarded
firmware runs. Losslessly rendered final-pair captures retain the original
P overlay, with the existing luma bars only in the opt-in bars run.

No production emulator code changed. Focused research syntax/receipt checks
apply; no new full regression-suite or physical deployment result is claimed.
No `ActionOpenUSB`, original USB mode screen, demand-loaded MSC/Remote service,
or end-to-end USB enumeration is established by this record.

The next boundary is the native `ModelCamera` readiness writer and its original
state/event dependency. The bounded base listing identifies a conditional
store at `libObj+0x3FE04A` and constructor initialization at `+0x3FE570`.
This is not permission to preset that byte. An association with the separate
E0591 step-26/VD wait remains unproven until the original call/state chain
connects them. Phase 2 hardware implementation remains gated on that evidence.
