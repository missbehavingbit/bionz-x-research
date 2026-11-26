# Mod and firmware qualification, 2026-11-19

Offline only. No physical camera, SD card, or USB device was accessed. The
captured firmware was never modified; the loader staging and virtual card
replay used separate disposable copies.

## Firmware matrix

These are the available a6400 identities, not all `CXD90045` targets. Compare
builds, not just displayed version numbers.

| Model / build | Mod Loader v2 | MenuTouchFull and ModsTabLook | Other mods |
| --- | --- | --- | --- |
| `ILCE-6400` 2.00, E0524 capture (live build `8.2.6.0`) | RAM install and hot reload verified in [E0577](../0577-qemu-real-mods-relocation-and-hot-reload.md); stock hook and tab table preimages match. | All three mod hook preimages match. Physical v1 gestures: [E0520](../0520-mtf2-full-menu-touch-verified-on-a6400.md). Fixed v2 navigation: [guest ON/OFF replays](#native-touch-follow-up). | Arrivederci was exercised with real Movie ISP on the specified build in [E0555](../0555-arrivederci-menu-exit-recompute-and-cc8-refill.md) and [E0556](../0556-arrivederci-classic-fitted-matrix.md); QEMU listing is not an ISP test. RCRAW diagnostics have separate, bounded evidence ([E0626](../0626-rcraw-w2-probe-qemu-execution.md), [E0631](../0631-rcraw-enable-test-qemu-reload-and-aif-baseline.md)). `USB Streaming Test` remains a diagnostic command, not a qualified streaming mod. |
| `ILCE-6400` 2.00, official package (`8.2.5.7`) | **Incompatible preimages.** The three view code windows match, but neither required stock tab table slot is zero. No install attempted. | **Incompatible preimages.** Both MTF2 hooks and the icon hook mismatch. | No qualified mod runtime; do not carry over ISP addresses, action calls, or observed results. |
| `ILCE-6400A` 1.01, official package (`8.2.7.6`) | **Incompatible preimages.** All three view code windows mismatch. No install attempted. | **Incompatible preimages.** All three mod hook windows mismatch. | [Reference firmware profile only](../0506-a6400a-offline-qualification-and-debugger-boundary.md); no applicable mod runtime was qualified. |

### Input identities and checks

| Build | `libObj.so` SHA-256 | `viewUnified2.so` SHA-256 | `libtestcmd.so` SHA-256 |
| --- | --- | --- | --- |
| Captured 2.00 | `e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1` | `fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d` | `cd9a580f986ab386db37fff51a95d2448ddd297a0dbc3c5499508e02a1c3deac` |
| Packaged 2.00 | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` | `1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2` | `73a821223927d905208d64aacbacc1182822b70dc2e272f08e71ed4382d94359` |
| Packaged 1.01 | `bf8dd162cdd50ffb0cf0cf9e66eacfd75aa3149e5f15176352231f6721666887` | `a51e120f7ff6334902e2aa49e78bfadbcbcf000a6e31f947b1bdb8380edcf168` | `f88cb9f3f4594b8b982bfe164297d8d64269c7e304ad0590eeb7df6e4315dc8f` |

The captured files were read with `debugfs` from the E0524 `nflasha15.bin`
(`70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064`).
The packaged files were taken from each release's `nflasha15_unpacked/lib`.
The checked windows lie in executable `PT_LOAD` segments with `p_offset`
and `p_vaddr` both zero (`arm-linux-gnueabi-readelf -lW`). File offsets
were checked with byte reads and, for the view hooks, tab slots and mod
prologues, Reverify 0.11.0. Its per-hash ledgers retain the results.

- Loader: the three windows in
  [`mlv2_guard.c`](../../../tools/native_camera/mlv2/mlv2_guard.c)
  are at `viewUnified2.so+0x5ab654`, `+0x5aba80`, and `+0x5ac044`.
  The required zero slots at `+0x802a04` and `+0x802a84` match the
  capture; the official 2.00 package has `2ec905022fc90502` and
  `6b025d0008001000` there. Matching code windows alone are insufficient.
- Mods: compare the packers' press/release and artwork hook bytes at
  `libObj.so+0x5f1d48`, `+0x5f1e54`, and `+0x79d4b6`. The package
  has identical MTF2 prologues at other offsets; this is not evidence that
  all of the mod's call sites, data, and ABI moved by the same amount.
  Loader relocation compensates for process load addresses; it is not a port.

The rebuilt core (`db869c73e249c670fb3e93185e9245ae1e95abeca2ba0a90b585b9e7abadd269`),
guard (`740c480cbd676a128fafa728a6b8396d33662a0fff9166505b076e589b3436c7`),
and request (`99d255c58dc8547b38e1c2a835f122fa88f28a86666e11384b25187b25512f85`)
match the E0592 core/request and E0577 guard identities. All 15
`tools.native_camera.mlv2.test_mlv2_unicorn` scenarios passed, including
relocation, replacement, rollback, and the USB command item. The
`mods.menu_touch_full.test_mtf_v2_unicorn` checks passed at two bases on
the E0577-identical relocatable mod (`2c658af25a0f2e9d6daa1f608185975e868db238dfbf60db5f94c3ce9a0b4dd6`).
The successful
core harness used the exact fixed and relocatable builds in their separate
slots; a first attempt incorrectly passed the relocatable file to the
fixed-layout scenario, which does not supply guest maps. These harnesses
do not prove any guest gesture or streaming data path.

## Replay inputs and initial controls

The initial runs used QEMU 11.1.3 (`823fbee11e7aff4fba51a17ab9d733694dd6f21e7bbaa30180b3e3f8a56d62b7`),
E0524 manifest `bf12cfa60d64560d6db6327e42e81fd5d81b96c883c90879228ed67364cdde6d`,
and the explicitly assumed E0651 powered sub-micro replay
(`3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9`).
They used the stock display path, rear controls, `EVSEL_LATE_US=500`,
`-icount shift=0,sleep=on`, and opt-in bar luma. See the run receipts
for the exact launcher environment and ordered QMP inputs.

**Touch:** In `mod-qualification-touch-03`, QMP listed no pointing
device. It rejected the same absolute down input before installation and
after the unchanged guard installed the loader (`MLV2_RESULT=0 changed=1`):
`Input handler not found for event type abs`. The original menu loaded the
core (`core_load ee60f8ea`) and auto-enabled ModsTabLook; MenuTouchFull was
staged but was not observed ON in this replay. No touch event reached the
guest, and this is not an ON/OFF gesture comparison. The first launch
lacked `qemu-io`; the second stopped at its short menu deadline. The final
run used QEMU 11.1.3 `qemu-io` for the disposable overlay only and quit
through QMP with exit 0.

The guest did not expose `/dev/tp`, and its Linux input registry had
no registered touch device. Do not equate that observation with the
absence of the Sony application path: in the captured `libObj.so`,
`I-TpMain` at `+0x858614` calls `osal_rcv_msg_tmo` at `+0x85865c`
on the `0x007b0026` queue from [E0096](../0096-proc-modules-fbresourcemanager-and-mediadetect-poll.md).
It copies a 16-byte message at `+0x8586be` and splits the coordinate word
into two halfwords before calibration; `InputService::callback_tp` is at
`+0x3d2da8`. The separate `/dev/tp` calls at `+0x856caa` and `+0x856d2e`
are configuration I/O. ARMv7/Thumb-2 ELF symbols and GNU disassembly
checked the Ghidra 12.2 queries (ELF import bias `+0x10000`). The separate
persistent PyGhidra project was locked and left untouched. This is static
code evidence, not a delivered event.
A gesture pass still requires the same down/move/up sequence in the guest
with the mod OFF and ON, delivery through the original callback and widget
hooks, and the paired, distinct Sony menu response. A key press, a call
straight to the mod, or failed delivery in both states would not pass.

## Native touch follow-up

The absolute-pointer refusal above still applies. These later replays
submit actual raw reports to Sony's original `0x007b0026` queue with the
guest's own `sndcmd.elf`. They do not call the mod, inject keys for the
gesture, patch the touch dispatcher, or draw UI on the host. This
qualifies behavior from the guest report queue, not the physical digitizer.

The captured `av-cam.bin` (`dd8f8d0c…0209`) producer at `0x636e9398`
allocates a 16-byte report at `0x636e93ee` and sends it at `0x636e944c`.
Its caller at `0x636e7aa4` supplies byte-sized axes and pressure. The
reports use four little-endian words: `0x01000000`, `x << 16 | y`,
contact (`1` during down/move, `0` on release), and the producer's
packed auxiliary word (finger slot 0, byte pressure, 15-bit sample value).
The replay uses pressure 32 while down and 0 on release.

The original v2 build reached all three `InputService::callback_tp`
events and the patched widget entry points, but the menu did not change.
Its `0x956f0` receiver check assumed the camera's heap address. The
fixed build calls the original `WidgetSystem::sys_getInstance` at
`libObj.so+0x5f0d30` and passes that validated receiver to the original
coordinate getter. This retains the instance guard; it does not admit
foreign or null receivers. The relocated getter is Thumb
`0x06308d31` in the reference layout. The decisive fixed guest receiver
was `0x00083f58`.

| Same raw reports in each pair | Mod OFF | Mod ON |
| --- | --- | --- |
| Tab tap: `(94,20)` down, `(95,20)` move/release, sample values 36–38 | `Custom Operation2` stayed byte-identical. | Started on the byte-identical `Custom Operation2` frame and changed to `Network1`. |
| Horizontal swipe: `(185,128)` down, `(126,128)` move, `(74,128)` move/release, sample values 43–46 | `Network1`, first row selected, stayed byte-identical across the gesture and settling sample. | `Network1`, same selection, changed to `Network2`. |

The two swipe starting frames differ only in the first selected row's
label rendering (difference bounds `28,134` to `331,172`, exclusive).
The native page and row selection agree; only the tap starts on an
exactly equal full frame. In the fixed guest `libObj.so` loaded at
`0x0593a000`; the callbacks at `0x05d0cda8` decoded the tap to pixel
positions `(235,37)` and `(238,37)`. The patched release at `0x05f2bd48`
was observed on both ON gestures. OFF restored the original prologues
and reported `mod_off 057a3000`. Unrelated instruction-signature matches
outside the mapped addresses do not count.

`artifacts/qemu-a6400/mod-native-touch-01/` retains the negative original
build run; `mod-native-touch-02/` retains the replay requests, QMP and
serial logs, original frames, and fixed build's passive trace
(`55e61ee0ece881bcaa75021d9e6875d38c84f2fbafcec56684be7b07295a1de6`).
The initial swipe OFF navigation entered a native Custom Key submenu
and submitted no swipe. The later OFF control returned to `Network1`
and submitted the four matching reports. The optional direct state
readback could not format its output because guest `od` was missing;
the original callback, hook, rollback, and frame evidence are independent
of that diagnostic. Both guests quit via QMP with exit 0 and left the
original eMMC hash unchanged.

`mod-qualification-touch-replay.py` starts the exact guarded run with the
optional `mod-qualification-touch-observe.c` probe; `mod-qualification-touch-send.py`
submits reports to that run's bounded local control socket. These replay
sources are retained under `artifacts/qemu-a6400/`. The v2 fix was verified
with build `0d0105e622c944776a3787964f75ce6798f0f7340d867ee47515e12eb6e8df54`
and core `db869c73…d269`. The updated ARM harness exercised two singleton
addresses and rejected a foreign/null singleton; all 15 loader scenarios
passed. The v1 `.mod` rebuilt to its existing SHA-256 `334a3dd4…1b80`.

`artifacts/qemu-a6400/mod-native-touch-03/` extends the control to page
dots, rows, and popups. Sony's native sender returned 0 for all 30
reports. The starting frames were byte-identical for the row sequence
and for the popup option tap. The page-dot starts had the same page but
a different highlighted row. The outside-tap starts had the same popup
and committed value, but a different option highlight. The observer
exhausted its sample limit on unrelated four-byte signature matches
before these gestures, so this run supplies paired queue payloads and
native frames, not the counted callback trace of the tab and swipe run.

| Same raw gesture with the mod OFF and ON | OFF | ON |
| --- | --- | --- |
| Tap page dot `(150,239)` | Stayed on `Custom Operation2`, page `9/9`. | Changed to `Custom Operation1`, page `8/9`. |
| Tap row `(106,133)` twice | Both taps left the starting frame unchanged. | First tap selected `Func. of Touch Oper…`; second opened its original popup. |
| Tap popup option `(86,89)` | No highlight change. | Highlight moved from `Touch Shutter` to `Touch Focus`. |
| Tap outside the popup `(13,140)` | Remained open. | Closed to the original menu. |

The value after closing was still `Touch Shutter`. With the mod OFF,
the original Down key produced the same `Touch Focus` highlight, but
Center also returned with the setting unchanged. This popup qualifies
highlight and dismissal, not a committed option value. The OFF command reported
`mod_off 0405d000`; QMP quit with exit 0 and the eMMC input hash matched.

`mod-native-touch-04` tested a vertical swipe inside the same three-option
popup. All eight submissions returned 0: the same four reports in each
pair, from `(150,121)` down via `(150,93)` and `(150,61)` to release at
`(150,61)`, samples 210–213. Both starts and the OFF result were
byte-identical (`f9df9089…1124`). Only with v2 ON did the highlight
move from `Touch Shutter` to `Touch Focus` (`299f0ac5…7ded`).
The committed radio option stayed `Touch Shutter`. All three choices
were visible, so this qualifies vertical-swipe navigation, not viewport
scrolling in a longer list. The replay hit its operation limit after
the matched captures and exited QEMU with code 0; this run did not
repeat the OFF rollback verified in `-02` and `-03`.

## Stock recording and Open Gate

The captured guest now [mounts the virtual SD card](../0650-qemu-virtual-media-gates.md#follow-up-stock-firmware-mounts-sd-2026-11-19).
Mount and read do not imply recording. The replays use the same captured
ORIL `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`,
prepared boot, and powered sub-micro assumption. Each cold-insert run
uses a disposable copy of the 64 MiB FAT16 card
`12364e0b83b6d92ebeac4ea3b50cb7951dc03550799596e6b585ab8efd90e2b2`.
The source eMMC was `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b`.

| Replay (`artifacts/qemu-a6400/mod-qualification-*`) | Observed boundary |
| --- | --- |
| `powered-media-04` / `-05` | No removable partition and zero SD commands. The diagnostic in `-05` read card type `4` and returned `-22` on mount; SDHCI reported `0x01ff0000`, but MODEREAD was `0x00000000`. |
| `powered-media-07` | Shared-slot detect gave MODEREAD `0x00010000` (no Memory Stick, inserted SD). The original boot mount registered `mmca10` and `mmca10p1`; the diagnostic's *second* mount returned `-16` on the already admitted device. Stock XAVC S 4K `24p 100M` showed the original UHS-I/UHS-II U3 refusal dialog. |
| `stock-movie-09` | Changed File Format via the original menu to AVCHD `60i 17M (FH)`. Both MOVIE pulses were issued with identical black frames before, during, and after. No changed card or clip. |
| `stock-movie-10` | Repeated the AVCHD control with `SUBMICRO_LENS_POWER=1` ([E0591](../0591-qemu-a6400-first-live-view-frame.md#captured-step-26-sub-micro-lens-rail-acknowledgement)). The sampled sequencer word was 42 with luma still not enabled for panel scanout. Before, during, and after were again identical; no changed card or clip. |

Runs `-07`, `-09`, and `-10` each recorded 39 removable-SD commands,
including six CMD17/CMD18 reads and zero CMD24/CMD25 writes. The separate
`eMMC` lines refer to the guest's disposable eMMC overlay, not the SD
card. Each run quit via QMP with exit 0 and left the original eMMC and
the whole virtual card byte-identical. The shared-slot build was
`7f90555050c7123901523611c0155c213dd634eab9030d6a3678466d0f3814fb`;
the lens-rail build was
`0af1091c82470adb5b5cf952e798f9c67d685871de7402faacfe0f3067635534`.

The passive traces narrow the boundary beyond QMP input acceptance:

- `stock-movie-11` observed the original XAVC speed dialog and Sony's
  view callback. The subsequent attempt only highlighted AVCHD and
  did not pass the setting check, so no AVCHD MOVIE pulse was sent.
- `stock-movie-12` confirmed the committed AVCHD setting before closing
  the menu and issuing two independent MOVIE pulses. All four edges
  reached `I-InputRecv` at `libObj+0x8628de` and `I-InputMain` at
  `+0x861f8c` with physical code `0x01f0`. The original transform
  returned `1` and the delivery gate accepted each one (`r6=1`, `r3=0`).
  `viewUnified2+0x2bd52c` received each `0x15010040` event, then
  `InputService::callback_convert` at `libObj+0x3d2d98` ran. This is
  not an input-driver failure.
- The run retained `/proc/<im.elf>/maps`: `libObj.so` at `0x0591d000`,
  `viewUnified2.so` at `0x13f7d000`, and `libmpr.so` at `0x06d49000`.
  The recorder probes required 16 original instruction bytes each.
  `libmpr` (`bb31351cbd546d88e8ff9bef006db75bd1dabfe3d2f67f359f04155cff9ab8af`)
  showed no hits at `PreRecStart` (`+0x88c868`), `EncOpen`
  (`+0x88def0`), `EncStart` (`+0x88df18`), or `RecStart` (`+0x891e44`).
  The observer did see the earlier native `NeutrOn` and a false
  `ModelCamera` readiness request, but this run does not attribute the
  AVCHD result to that readiness bit. It also does not rule out
  unprobed start paths. The 39 SD commands were unchanged from the
  uninstrumented run: six reads, no writes. QMP quit with exit 0.
- `stock-movie-13` traced one MOVIE pulse through the application
  event queue before its QMP screendump timed out and left a truncated
  image. It did not complete the transition control. `stock-movie-14`
  repeated it with Linux-local frame capture: all three press/release
  pairs became `0x15010042`/`0x15010041` at `libObj+0x3d2d42` and
  reached `EventManager::push` at `+0x842528`. The extra pair followed
  ordinary Play and S1 half-press. Neither action visibly restored
  shooting or reached the sampled `NeutrOff` store. There were no
  observed MPR starts or SD writes; the original mount completed and
  QMP quit with exit 0.
- `stock-movie-15` and gated retry `-16` used the distinct [E0650
  shooting-view board and card](../0650-qemu-virtual-media-gates.md#after-the-captured-step-26-fix):
  QEMU `ab62eff98b068f155ca3bc812d8650301c6dee638523725b6104dd77399ec50c`,
  card `581066b0588df7ffc68836143e0d090eb1282d03625648f375901c311498631a`.
  The LCD remained active (`VPH+0x144 = 0x01000233`), but both late
  insertions reached `MMCD_IOCTL_MOUNT` without completing it or
  issuing any SD command. In `-15`, the two AVCHD MOVIE pulses
  reached `EventManager::push`, with no observed recorder starts;
  that run is not an admitted-card recording test. In `-16`, the
  preflight saw SD detect switch to inserted but found no `mmca10p1`,
  so it sent no MOVIE input. Both card images were unchanged.
- `stock-movie-17` added passive queue-insert, dequeue, and view
  probes. The XAVC control displayed the same U3 refusal. Both
  event objects were observed at `libObj+0x842570` and returned
  by the dequeue wrapper at `+0x842524`; the press reached
  `ViewBase::transformByTriggerTableForFuncEvent` at `+0x3ebc20`.
  The guest did not return to the expected menu after closing
  the dialog; its frame check stopped before AVCHD selection.
  The first independent AVCHD launch stalled before a guest receipt.
  After WSL recovered, `stock-movie-18` completed: both AVCHD
  presses and releases were inserted and dequeued; both presses
  reached the original view trigger. The card stayed unchanged.
- The `stock-movie-19` (XAVC) and `-20` (AVCHD) probes matched
  the original `ViewStlrec` object using its live vtable and ELF
  typeinfo, not the nearest symbol name. In both formats its
  trigger callback (`viewUnified2+0x16fac8`) returned `0x12`;
  the original dispatcher returned `0x130a5012` for view ID
  `0xa5`. Only XAVC displayed the U3 refusal. Both AVCHD
  presses reached this same view and produced no file or SD write.
- `stock-movie-21` observed delivery of the transformed event
  into `ViewBase::Main` and the real native action indices `0x263`
  and `0x14f`. They are not the raw `0x12` trigger-table result.
  `stock-movie-22` observed both indices reach the original movie
  command at `viewUnified2+0x1c2fa0`. Its PC Remote and dual-function
  queries returned zero. The result at `+0x1c3048` was `5`, not the
  value `2` that takes the caution path. Both presses called
  `openView("view/MOVIE_REC_PATCH", …)` and reached the original
  `ViewMovieRecPatchToInstance` entry at `+0x6271b8`. No sampled
  MPR start followed. The mounted SD again recorded six reads
  and no writes, and the card hash remained unchanged.
- In `stock-movie-23`, both native factory calls completed
  construction at `viewUnified2+0x6271ce` with an original
  `ViewMovieRecPatch` vtable. The observer checked the vtable's ELF
  address `+0x941870`, the factory's instruction bytes and the actual
  guest mapping `0x137d7000`. Neither construction was followed by
  an observed movie-view `UserEntry` (`+0x5f83c4`), `UserMain`
  (`+0x627e50`), `UserAction` (`+0x62801c`), or movie-view model
  request. Construction did not prove activation. The card was
  mounted; 39 normal SD commands included six reads and no writes.
  The card and source eMMC hashes were unchanged. QMP quit with exit 0.
- In `stock-movie-24`, both instances reached the original
  `ViewBase::onLoad` through the manager's vtable call at
  `libObj+0x846020`. The same objects took the `-10` state branch in
  `onLoad`, reached movie-view load-info at `viewUnified2+0x60d820`,
  and returned false from the boot-enable call at `+0x6102e8`.
  The manager then received `-1` from `onLoad` and took its load
  failure branch at `libObj+0x84604a`. Neither object reached the
  sampled activation delivery or entry. This was an explicit
  pre-entry rejection, not an unobserved callback after successful
  activation. QMP exited 0; the card was mounted and the 39 SD
  commands again included six reads and no writes.
- In `stock-movie-25`, the same boot-enable routine selected media
  ID `1` and the original media-exists check returned `1` at
  `viewUnified2+0x61043e`. An earlier process-data lookup for
  parameter `0x59` left its `-1` default and took the branch that
  requests caution `0x582`; execution continued past that branch.
  The original per-format admission helper at `+0x6100a0` then
  returned `0` at `+0x610610` for both presses. That result was the
  one carried to the final false boot-enable return. This refutes
  a missing card as the immediate reason for the load failure;
  it does not yet identify the failing test inside the helper.
  QMP exited 0 and the mounted card again saw no writes.
- In `stock-movie-26`, the original `ModelMedia` query `0x52`
  returned successfully and supplied parameter `0x57`. The inhibit
  query passed its `0xc8` check and found no matching inhibit.
  The final media helper still returned `0` at
  `viewUnified2+0x6102d2` for both presses.
- In `stock-movie-27`, the `ModelMedia` query `0x55` returned `3`
  for each of parameters `0x4c`, `0x4d`, and `0x64`. The AVCHD
  branch clears its remaining admission flag for `0x4d` in `2..6`,
  including the observed `3`, and returns false before view entry.
  The original AVCHD load completion was logged, but load is not
  recording admission. The card again saw six reads and no writes.

### SD speed-class control

[Sony specifies Class 4 or U1 for AVCHD](https://helpguide.sony.net/ilc/1810/v1/en/contents/TP0002264633.html).
The original QEMU SD memory card reports class code 0 in ACMD13 SD
Status. A task-local, opt-in `sd-card.x-sony-class4=on` experiment
changes only SD Status bits `447:440` (byte 8, class code 2). It
advertises a virtual capability, not measured throughput. No guest
guard was skipped and no production QEMU default changed.

The native SDHCI test reads all 64 bytes of ACMD13 before and after
the four-bit bus setting and again after ejection/reinsertion.
Only the selected speed-class byte and the guest-selected bus width
change. The virtual card stays byte-identical. The three existing
removable-SD and lens-rail qtests also passed on this build.
The relinked QEMU SHA-256 was
`927288a81c44bbeac03702878dae59bc67aa84a13bc2c543f1adeb8206faca35`;
the original board and `sd.c` were unchanged.

The matched `stock-movie-class0-01` and `stock-movie-class4-01` boots
use that one binary and the same FAT16 card, powered input, and lens
reply; only the class bit differs. Both boot mounts completed and
Sony's SD host read SD Status twice during initialization. In both
cases the original AVCHD events reached dequeue and the view trigger,
but no sampled recorder start ran. Both runs issued 39 normal SD
commands (six reads and no writes), left the original card hash
unchanged, and quit through QMP with exit 0. Reporting Class 4 did
not resolve this recording boundary.

### SDHC/FAT32 initialization control

`mod-qualification-stock-movie-sdhc-fat32-01` used the same pinned
Class 4 board and another disposable card: a blank 4 GiB SDHC image
with a FAT32 MBR partition at sector 2048. The fixture generator is
`artifacts/qemu-a6400/mod-qualification-make-sdhc.py` (dosfstools 4.2,
`--invariant`). The before image was
`19ca0d9cdb786c43aa76be6442f63e98d5fdfd873b799120156b08e4b6ff8b23`;
`stock-movie-sdhc-trace` cloned it before launching QEMU.

Sony mounted `mmca10p1` and issued 990 removable-SD commands:
80 CMD17/CMD18 reads and 280 CMD24/CMD25 writes. Read-only inspection
of the changed image found `AVF_INFO` entries and
`PRIVATE/AVCHD/BDMV/{INDEX.BDM,MOVIEOBJ.BDM,STREAM}`, with no video
clip entries. The write trace proves guest card initialization, not
movie recording. The expected AVCHD menu frame never appeared,
so no MOVIE input was sent and this run did not retest the AVCHD
admission decision. The replay stopped its QEMU process (exit 0)
and retained the console, trace, screenshots, and [receipt](../../../artifacts/qemu-a6400/mod-qualification-stock-movie-sdhc-fat32-01/receipt.json).
The receipt identifies the retained after-image
(`8284d198a6d5d32d4fd24027ea631fd654287f31724c7eb537ed2ee8e91d1f9f`).
The source eMMC was unchanged.

The [initialized-card replay](../../../artifacts/qemu-a6400/mod-qualification-stock-movie-sdhc-fat32-02/receipt.json)
booted a fresh guest from a clone of that after-image, with the same
board and passive observer. It dismissed the original NTSC notice,
confirmed stock AVCHD `60i 17M (FH)` in the menu, and sent two
MOVIE pulses. With this initialized SDHC fixture the original
`ModelMedia` parameters `0x4c/0x4d/0x64` were `1/1/3` instead of
`3/3/3`. The AVCHD admission helper and boot-enable both returned
`1`. The original `onLoad` returned `0x17`, and the same movie
view ran `Entry`, `UserEntry`, `UserMain`, and `UserAction`. This
clears the pre-entry media rejection in this replay. It does not
isolate SDHC capacity, FAT format, and Sony database initialization
as separate variables.

The second MOVIE press reached the active movie view (`0x68`) and
transformed to `0x13068005`. Neither press reached the sampled
recorder start APIs or caused an SD write. The snapshot still showed
sequencer step 42 and `1024x570` RC0 geometry. The guest issued 475
normal SD commands (152 reads, zero writes), left the initialized
card and source eMMC byte-identical, and quit through QMP with exit
0. The movie icon and activation are not evidence of recording.

[Replay `stock-movie-sdhc-fat32-03`](../../../artifacts/qemu-a6400/mod-qualification-stock-movie-sdhc-fat32-03/receipt.json)
reproduces the initialized-card result
with the receipt's three native menu frame checks. The recorder
object and model factories run during boot. After the first MOVIE
press the movie view runs its initialization (`viewUnified2+0x6277c4`)
and requests tree `@T0E2`; the press that follows reaches
`ViewBaseProduct::ProductMain` on that same view as `0x13068005`.
No sampled recording start/stop call or SD write follows.

[Replay `stock-movie-sdhc-fat32-idt-01`](../../../artifacts/qemu-a6400/mod-qualification-stock-movie-sdhc-fat32-idt-01/receipt.json)
adds the original, hash-checked
[IDT sidecar](../0650-qemu-virtual-media-gates.md#the-original-idt-sidecar-releases-the-captured-step-42-predicate)
using the public launcher's `IDT_CAM` option. It boots from a new
clone of the same initialized card and reaches the AVCHD setting
shown in the no-sidecar comparison. The view again opens, requests
`@T0E2`, and receives the second MOVIE press at `ProductMain`. This
replay does not independently sample the IDT job's predicate or the
EE callback. Both runs show `1024x570` RC0 geometry, 475 normal SD
commands (152 reads, zero writes), unchanged source eMMC and card
hashes, and QMP exit 0. Neither run proves an active recorder.
Both used observer `3cb885db…ea4b6`.

[Replay `stock-movie-sdhc-fat32-idt-02`](../../../artifacts/qemu-a6400/mod-qualification-stock-movie-sdhc-fat32-idt-02/receipt.json)
repeats with the sidecar, a fresh initialized SDHC clone, and
observer `ac94d85ff023ad73285a11437bed5ffc224482732b674a0129af4087131395d0`.
`ViewBase::requestModel` resolves `@T0E2` to `0xe2` and returns `1`;
that return is not evidence that the tree completed initialization.
After the second MOVIE press, the same view receives `0x13068005`.
At `viewUnified2+0x61aa78` it has state `4` in `r0` and normalized
event `5` in `r8`. All seven sampled predicate calls in that branch
(`0x01`, `0x1f`, `0x20`, `0x21`, `0x28`, `0x29`, `0x9f`) return zero.
This narrows the dispatch boundary but does not identify a missing
completion or show whether a subsequent action was published. No sampled
recorder start follows. The replay still shows `1024x570` RC0 geometry,
475 normal SD commands (152 reads, zero writes), unchanged source eMMC
and cloned SD image hashes, and QMP exit 0.

The local `mod-qualification-powered-media-replay.py` and
`mod-qualification-movie-observe.c` under `artifacts/qemu-a6400/`
retain the replay schedule and passive instruction signatures.
The `-14` through `-16` observer SHA-256 was
`bbb2f1f6344052a4d4f5e6ad4eed1e8a7c2cbaeb67ed9dd2b833c0a614afdde4`.
The `-17`, `-18`, and speed-class observer SHA-256 was
`b839ef90d6c92a07ef919a71aeb524832cdb91738472af118b0eede10dcfbbcc`.
The `-19` and `-20` observer SHA-256 was
`d4e97e4720666ef1275281631f899a0fa594b5dc285e6f90ceb3a0af9d9da562`.
The `-21` observer was `a23b4688…8454`; the `-22` observer was
`ba5b9a15…b25e`; `-23` used
`4b457cb6fda2eb8bfdb59f2249aa9ddd48135a73cdabf9729c8e6dd1b7231e78`.
The load-gate observer in `-24` was
`181e6e4ca9d71668bb6d9d8c2d4f53a58caa6bd85a250f376ebeaa9144bdc0fc`.
The media-admission observer in `-25` was
`cfa454cbddb074fc4e2e8d6eefcb98d2b0502555ecd1ff6a86c3344dbd7a4a25`.
The media-query observer in `-26` was `9cfb818e…8910d`;
`-27` and the SDHC control used `9cf43fa8…c9cc`.
Run receipts retain the full hashes.
The SD patch, isolated relink recipe, and qtest script are retained as
`mod-qualification-sd-class4.patch`, `mod-qualification-build-class4.py`,
and `mod-qualification-sd-status-test.py` in the same directory.
The ACMD13 results are in
`artifacts/tests/mod-qualification-sd-class4-20261120-retry.json`.

On the FAT16 controls, Sony's movie view rejects loading before it
can enter the recording state. The initialized FAT32 SDHC image
clears that gate but reaches no sampled recorder start. Recording
still requires owned source frames, an encoder that fills the
guest's output buffers, normal stop and flush, and a new decodable file.

The [ARM selector test](../../../tests/test_sensor_tables.py) executes
the original code in both the packaged ORIL (`0x63c9d13a`) and the exact
captured ORIL (`0x63caeada`). The packaged entry address does not apply
to the capture. In both tests the selector returns the `1620x1080`
and `3240x2160` buffer rows; a 16:9 case is the control. In the capture,
the original callers at `0x6371b75a`, `0x637202ce`, and `0x63721838`
index distinct, byte-identical copies at `0x63df199c`, `0x63df1a44`,
and `0x63df1aec`. The first loads the pool ID; the others load the
requested and aligned dimensions. Captured mode 10
is `1996x1332`, 12-bit. This proves candidate buffer geometry in
isolation, not a launched sensor mode, encoder, or saved movie. The
current [SA DSP model](../../../qemu-a6400/qemu-overlay/hw/misc/cxd90014_sa.c)
signals completion but does not write encoded bytes into guest RAM.
[E0578](../0578-a6400-open-gate-3-2-recording-feasibility.md) and
[E0620](../0620-a6400-open-gate-4k-and-6k-paths.md) describe feasibility;
no runnable Open Gate mod or playable firmware-originated clip was shown.

### Live-view source qualification

The stock still menu read `3:2` in `open-gate-preview-geometry-01`.
The screenshots named `aspect-16-by-9` and `preview-16-by-9` are from
an attempted switch: the native menu still showed `3:2` afterward.
They are not a 16:9 control. In both shooting snapshots, RC0's output
and VPH channel 51 used `0x400` active bytes, `0x80` bytes of line skip,
and last line `0x239`: **1024x570 Y8 with a 1152-byte pitch**. The still
menu setting did not prove the aspect ratio of the source frames.

The `open-gate-readout-01` replay adds a passive observer to the pinned
lens-rail board `0af1091c…5534`, captured ORIL and powered sub-micro
fixture above, with the virtual card left out. It dismisses the
no-card notice, waits for 48 panel ticks and takes a stopped QMP
snapshot; it does not send MOVIE. The observer
(`e7b25e989905dc35a13f9a15c8dce3ca4e355af86ed5b4c3aa97f9608a9df206`)
saw two calls to the original IMX284 register-programming routine
at `0x637a00cc`, both with table index **4** in `r1`. The captured
lookup at `0x63cef804` scans the keys at `0x63e25514`; key `0x01`
selects row 4 (`1996x570`), whereas key `0x44` selects row 10
(`1996x1332`). Row 10 was not observed at the programming site.
The early logged requests with key `0x11` resolved to `0xff`
(no match) and returned 2. That is not sensor row 17 and, by itself,
does not identify the cause of the recording failure. None of the
three sampled movie-buffer call sites or the original selector ran.

The original store at `0x63d8033e` wrote RC0 channel 3's address at
`0xf38813a0` **136 times**. The first 134 writes used low memory;
the existing luma model filled `0x14e90000` and `0x14fbc000`, and
the original `VphUpdateYuvCall` entry ran 134 times. The final two
writes selected `0xfe060000` and `0xfe16a400`; there were no further
observed callbacks. VPH's stopped readout used the second address
and still reported `1024x570`. At that pitch the end-exclusive
addresses are `0xfe100480` and `0xfe20a880`, respectively. Both
spans exceed the board's modeled 1 MiB window at `0xfe000000`
and fail the current [AIF Y8 DMA range check](../../../qemu-a6400/qemu-overlay/hw/misc/cxd90045_cam.c).
This establishes a model/descriptor mismatch for the sampled preview,
not the hardware backing of those addresses or the cause of Sony's
movie-view activation failure. No high-memory mapping or DMA guard
was changed. QMP exited 0; both source images retained their hashes.

`open-gate-avchd-readout-01` repeated the trace with a stock-mounted SD
card. The original menu confirmed AVCHD `60i 17M (FH)` before the two
MOVIE pulses. The mode lookup observed 451 requests for key `0x01`
(resolved to 4) and 49 for key `0x11` (resolved to `0xff`). It again
observed two programming calls with index 4, none with index 10,
and zero hits at the sampled movie-buffer selector and its three
callers. The firmware did execute the movie-format validation site and
build its completion payload once each during the run, with no hits at
the invalid-format branch. These aggregate counts do not associate
either format operation with a specific keypress or prove acknowledgement
delivery. The optional still-format packet observation could not read
the request through its bounded guest-VA range; its reply-attempt
sample was ORIL packet `0x4036`, not evidence of delivery of the
application-side `0x7541` response described in [E0650](../0650-qemu-virtual-media-gates.md).

In the mounted run the two high RC0 stores came through the same
original call at `0x63a26554` (Thumb LR `0x63a26559`). The original
code takes the base address from word `+4` of a selected descriptor
in the caller's `0x70`-stride configuration array, not from a fixed
literal in the DMA writer. The sampled descriptor VAs were
`0x76436194` and `0x7642d194`; these are the descriptor objects,
not the backing of `0xfe060000` or `0xfe16a400`. The original VPH
update count was 137 for 137 low addresses plus the two high stores.
The guest issued 39 removable-SD commands (six reads, no writes),
the disposable card stayed byte-identical, and QMP exited 0. The
observer SHA-256 was
`74a81d14ac681881b72ce0b8387454d54058c9f7e67b31cb7e12562d3c139b80`.

The replay, observer source, mode trace, and QMP receipts are under
`artifacts/qemu-a6400/`, with `preview-readout-trace OBSERVER` on
`mod-qualification-powered-media-replay.py` for the no-card replay
or `stock-movie-avchd-readout-trace OBSERVER` for the mounted control.
The current guest preview cannot serve as full-field 3:2 source
evidence. The next recording prerequisites are a live, owned 3:2
frame buffer and a working guest encoder path. Resizing or padding
the current preview and encoding it on the host would not qualify
Open Gate.

### Playable recording acceptance

When a firmware-originated file is available, export the same Sony clip
directory (for example `PRIVATE/M4ROOT/CLIP`) before recording and after
the guest stops and flushes it. Verify the new file independently:

```bash
python3 -m tools.research.verify_open_gate --before BEFORE_CLIPS --after AFTER_CLIPS --size 1620 1080
```

The checker requires an exact square-pixel 3:2 raster and an error-free
decode through EOF with more than one frame. It rejects renamed baseline
clips, headers without video, and anamorphic variants. A shorter stream
that still ends cleanly is not proof of an intact recording; preserve the
guest's stop/flush and before/after media receipts. Geometry alone cannot
identify the sensor area actually read. All six tests passed with
FFmpeg/ffprobe 8.0.1 using encoded fixtures (including a truncated MP4
negative control), not firmware recordings.

## Test results

The final Windows `python -m unittest discover -s tests -q` run completed
2,166 tests with 128 skips and no failures
(`artifacts/tests/mod-qualification-full-final-20261119.log`). Earlier
runs with `UC_ERR_NOMEM` in Unicorn abort tests are retained separately.
Standalone loader, touch and clip-check results are under `artifacts/tests/`.
