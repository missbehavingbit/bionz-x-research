# E0613 — Original USB key inhibition, its owner, and the release boundary

Date: 2026-11-19. Offline QEMU only. Follow-up to
[E0612](0612-captured-usb-receive-and-ready-gate.md) and the
[USB attach-chain plan](../plans/qemu-usb-attach-chain-plan.md).

## Result

The original firmware deliberately inhibits physical USB key `0x07000001`
during startup. The message reaches I-InputMain, matches its live subscription
for application event `0x15040010`, and is suppressed by the original key-state
inhibit byte. This is the measured application-delivery boundary, not a
missing VBUS edge, failed OSAL delivery, absent subscription, or unknown ORIL
return hop.

Two paired observations (`LIVEVIEW_SOURCE=none` and `bars`) show the same
subscription match and native suppression reason `0x11`. A second pair
identifies the original startup owner and observes its actual key-state write
`0 -> 1`. The original helper that clears inhibition, and its six discovered
direct call sites, are not executed in the bounded observations. No flag was
cleared by the host, no USB event was injected, and no hardware model changed.

The next boundary is the original initialization path that should release
this inhibition. Drawing a P display, producing synthetic luma, and the sampled
ROOT readiness result from E0612 do not establish that this path completed.

## Input/tool identity and policies

The captured library identities remain those qualified against the saved
filesystem in E0611/E0612:

| Input | SHA-256 |
| --- | --- |
| libObj.so | `e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1` |
| viewUnified2.so | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` |
| libUsbControl.so | `b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2` |

Each generator checks these captured hashes. Each replay retains QEMU/plugin/
generated-C/boot-manifest hashes, console, process maps, QMP edge receipts and
LCD captures. User probes require 32 matching original instruction bytes
before accepting a hit. New I-InputMain probes additionally require the
original stack message to contain physical USB key `0x07000001`, before
counting toward the log limit. Bounded snapshots contain only declared user
RAM fields (maximum 176 bytes per individual read); none changes guest state.

Policies preserve E0612: verified prepared inputs and a new disposable overlay,
`REAR_CONTROLS=1`, `USB_VBUS_TEST=1`, `USB_CID_HIGH=1`,
`EVSEL_LATE_US=500`, `ULOGIO=''`, no network, no mod/firmware patch, original
rear-center input and QMP attach/duplicate-attach/detach. The host observation
budget is 240 seconds; these are bounded replays, not unlimited waits or a
timing-complete machine. All cited paired replays quit their owned QEMU with 0.
No physical camera, USB device or physical SD was accessed.

## Live subscription and exact suppression

The matched live node has handle `3`, queue `0x007AE05E`, 212 key entries,
and a callback resolving to **viewUnified2+0x2BD52C**. This corrects E0612's
registration-order candidate `+0x2BD034`; the actual node pointer is stronger
evidence than registration order.

The live key entry, not merely the saved library data, contains:

| Field | Observed value |
| --- | --- |
| Physical code | `0x07000001` |
| Application event | `0x15040010` |
| Criterion | `0x7FFFFFFE` |
| Physical-key table index | `0x19D` |
| Entry bytes `+0x40`, `+0x41` | `0`, `0` |

Original instruction sites and snapshots show, for both attach and detach:

1. `libObj+0x862178` matches this physical key in this subscription.
2. `+0x86218C` observes the entry-specific byte `+0x41` clear. The node's
   byte `+0x24` is also zero; this selects the original transformation path,
   not an assumed enabled/disabled interpretation of that node byte.
3. Original transform `+0x861018` returns `0` at `+0x8621F4`. Its output
   contains event `0x15040010`, status `0`, and lookup index `0x19D`, even
   when the incoming physical status is `1`.
4. At `+0x8622C6` and `+0x8622C8`, original registers are `r6=0`, `r3=0x11`.
   The original instruction path builds bit `0x01` from a zero transform
   result and bit `0x10` when physical-key state byte `+0x20` is nonzero
   with its word `+0x24` bit 0 clear. It takes the no-delivery path.
5. The USB-focused callback queue-send site `+0x8624B0` is not hit.
   InputService's USB createEvent and ActionOpenUSB remain unobserved.

The physical inhibit state and entry-specific suppression bytes are different
fields. This record does not label the entire subscription disabled or claim
that USB is absent from its key list.

## Original owner and actual store

The captured startup helper in the ViewRoot code region, view+`0x230768`,
queries the literal model alias **`@M001`**, operation `2`, then parameter
`0x100`. Both replays observe query success and value **`1`** at `+0x23079C`.
Original comparisons exempt values `2` and `15`; value `1` takes the branch
to `+0x230754`.

That helper loads literal `0x07000001`, sets the boolean argument to `1`,
and tail-calls `InputService::setPhysicalKeyInh(int, bool)`. Its observed
return site resolves to view+`0x2307AA`. There is one observed request for
this USB physical key and it requests inhibition; no request to clear the
key is observed in these runs.

The wrapper at captured libObj+`0x3D59B0` uses the internal implementation
at `+0x860DA4`, not the separately exported `InfraInput::SetKeyInh` entry
`+0x860E9C`. The first exploratory setter trace therefore did not see a
store at the exported entry. The refined trace observes the original
internal store directly:

| Site | Original observation |
| --- | --- |
| `+0x860DA4` | physical key `0x07000001`, boolean `1`, fourth argument `0` |
| `+0x860E38` before native STRB | key-state byte `+0x20 = 0` |
| `+0x860E3C` after native STRB | same key-state address, byte `+0x20 = 1` |
| `+0x860E44` after native flag store | byte `+0x20 = 1`, word `+0x24 = 0` |

The observed physical-state record contains the original key and index
`0x19D`. The host did not perform these writes.

`@M001` has not been identity-mapped here to a named model. In particular,
its parameter `0x100` must not be conflated with E0612's separate ROOT
parameter `0x102`.

## Native release path and remaining boundary

Original helper view+`0x41AD18` loads the same physical key and boolean `0`,
then tail-calls the same InputService API. A bounded Thumb branch-candidate
scan and retained original disassembly identify six direct callers:
`+0x1C6856`, `+0x1C7014`, `+0x626246`, `+0x626F00`, `+0x6586E6`, `+0x6593FC`.
All six sites and the helper itself have zero qualified hits in both release
replays. This scan is not exhaustive for indirect calls or other libraries.

Nearby still-recording initialization code calls an init-done-related byte
setter at `+0x4116C4`. The probe observes two calls with `r0=0` and none with
`r0=1` in each replay. This is limited to the observed helper, not proof of
every possible writer to the global byte. Relating these paths to the
independent E0591 captured sequencer step-26 wait requires another original
call-chain/causal observation; this record does not assume that dependency.

The appropriate continuation is to follow the original still-recording
initialization state transition and establish its missing contract. Calling
the clear helper manually, changing `@M001` to an exempt value, clearing the
inhibit byte, or setting an init-done flag would replace firmware decisions
and is not a compatibility fix.

## Receipts and verification

Artifacts under `artifacts/qemu-a6400/`:

- `e0613-usb-subscriptions/` and `bars/`: live node/key snapshots, original
  transform returns and reason masks; `subscription-analysis.json` decodes
  offsets from the retained original instruction listings.
- `e0613-usb-inhibit/`: exploratory exported-setter and wrapper caller trace.
- `e0613-usb-inhibit-owner/`: refined internal original store observations.
- `e0613-usb-release-gate/` and `bars/`: paired startup alias/value, original
  store, release-helper/call-site and init-done-related observations.

`find-inhibit-calls.py` and `find-inhibit-release.py` preserve hash-pinned
Thumb branch candidates plus bounded objdump receipts. ELF `.text` coordinates
are used for branch decoding; no arbitrary file-offset interpretation of
writable data is introduced. Short candidate windows can start within an
earlier instruction; the target branch instruction itself is checked against
original bytes/listing before use. No standalone candidate proves ownership.

The existing explicit legacy Ghidra 12.1.4/JDK 21 bounded headless profile
retains separate source-hash/command/query/output receipts. The filter and
ViewRoot query windows provide static leads, not runtime replies. Decompilation
of tiny tail-call helpers crosses mixed-state PLT bytes and emits bad-instruction
pseudocode despite an overall completed report. That pseudocode is rejected;
the original helper instructions and native runtime store observations above
are the evidence. No default REA import or cross-profile cache reuse is claimed.

One host preflight attempting a Windows Python path through bash failed on
quoting. It was not a firmware run; the candidate scans instead use Python
stdlib directly in WSL. No tool installation or global configuration changed.
The final inline receipt-writing command also exited 1 without output;
the standalone receipt writer then succeeded. `host-tool-failures.json`
preserves both host-only failures, and `tool-files-manifest.json` retains
27 research tool identities separately from firmware input allowlists.

`audit.py` passes **56 receipt checks** across the subscription and release
pairs: owned exits, two edges, preserved tool identities, no exhausted user
log caps, live mapping/bytes, transform/dispatch gate, actual callback pointer,
original owner/store, model alias/value and unobserved release sites. Python
syntax checks cover the research scripts. Production emulator code did not
change; no new production regression-suite or physical deployment result is claimed.

Reproduce in fresh output directories from the repository root, retaining the
same source hashes and captured-library profile. Use the artifact generators
and `replay-vbus` modules, then `correlate`, `analyze-subscriptions` and `audit`.
Preserve all existing generations; the `bars` subdirectory refuses reuse.
