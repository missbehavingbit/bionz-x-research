# E0573 — Interactive a6400 rear panel in QEMU

Date: 2026-11-17. Offline follow-up to E0572. The physical SD card and
camera were not accessed by the emulator.

## Rear controls

The browser panel uses the rear-side arrangement in
[Sony's ILCE-6400 Help Guide](https://helpguide.sony.net/ilc/1810/v1/en/contents/TP0002280308.html).
The screen is the QEMU VPH head 0 screendump. The local service translates
each click to a QMP key pulse, then the opt-in machine input handler drives
the emulated hardware. Menu selection and rendering run in the firmware.

The key numbers come from the original guest's
`/proc/udm/input` (E0526) and matching key/name pairs in `libObj.so`
around file offset `0x13e18d4`. The [pinned a5000 implementation](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/qemu-overlay/hw/arm/sony_a5000.c)
provides the ADC and sub-micro input approach. Its ADC channel assignments
differ from the a6400.

Input SHA-256 identities:
`libObj.so` `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1`,
`av-cam.bin` `90d515cfec8b936f1ba9a69dcac462816b880297752a652474d67c4bba39125e`.

| Control | Guest-facing signal |
| --- | --- |
| Four directional presses and center | ADC unit 1 channel 3 (global channel `0x0f`), levels 182 / 356 / 520 / 686 / 45 for up / left / down / right / center. |
| Fn and C2/Delete | ADC unit 1 channel 0 (global `0x0c`), levels 520 / 45, codes `0x7095` / `0x1f8`. |
| MENU and playback | Captured slot-A CAkey1 bits 2 / 1 active high. |
| Flash pop-up | Slot-A CAkey1 bit 9 active low. |
| MOVIE | P0.10 active low, code `0x01f0`. |
| AF/MF–AEL lever and press | P0.11 latched toggle, and P0.7 active-low pulse. Firmware catalog names are `KEY_SLI_AF_MF_AEL` and `KEY_OTHER_VER_GRIP_AEL_AFMF`. The P0.7 body mapping is inferred from the combined name and the a6400 GPIO table. |
| Wheel rotation | Host increments/decrements the 16-bit TPU counter at `0xf2404014`. The channel-0 wheel assignment follows the a5000 lead. The a6400 original `ldrh` at `0x63a5ecde` reads `0xf2404014 + (channel << 8)`. The passive plugin sampled channels 0 and 1. |

The slot-A reply changes only the asserted key bits and recalculates the
existing CRC-16/CCITT-FALSE over bytes 0–125. Release uses the original
captured bytes. The E0572 display edit is hash-guarded and applied only to
the run's qcow2 overlay. The relevant input and LCD flags are off in ordinary
`run.sh` runs. The panel enables them by default and accepts `--stock` to
leave display selection to the original firmware.

## Replays

- Browser panel at `http://127.0.0.1:8641/` on the isolated rebuilt
  QEMU binary SHA-256
  `200a3623dfb7580e81f0b777c7956a8b0cfaff4b8f223f8e156a93221502a64d`.
  From the project root using WSL, set `A6400_QEMU_DIR` to this build,
  then run `EVSEL_LATE_US=500 python3 qemu-a6400/tools/rear_panel.py --port 8641 --log NEW_LOG`.
  The phase remains experimental. The flickering/empty frames during
  transitions were captured directly from QEMU.
- MENU opened the original `Quality/Image Size1` menu. The direction
  and center sequence produced different firmware frames, and a MOVIE press
  showed the Sony `No memory card. Cannot record movie.` dialog. Center
  dismissed it. The playback test produced a separate no-card state.
- An attempted `/dev/input/event0` capture failed with `No such device`
  in this boot. The menu proof uses visible firmware frames and the model
  uses the underlying key paths. Separate observations cover the wheel
  counter and lever instead of relying on the static device nodes.
- The follow-up HMP readback changed `0xf2910010` from `0x2c9a` to
  `0x249a` after toggling the lever, which clears bit 11. The wheel word
  read `0x0000 → 0x0001 → 0x0000` after clockwise and counterclockwise
  clicks. The original counter accessor was observed on both channels
  during boot. The bounded run did not observe nonzero firmware reads
  after the host changed the counter, or a visible response to rotation.
  The application-level AF/MF–AEL response also remains unverified.
  The next dial probe is the original `JogTpu` vtable at `0x644e6208`.
  Its `0x63c159b0` method forwards the channel to `0x63d7afb2`, which
  branches to the halfword accessor. Its `0x63c15970` method calculates
  the signed rotation from two counter samples.
- The local build compiled and linked against the existing QEMU checkout
  without editing it. The full Windows suite ran 1225 tests and
  reported 146 errors and 39 skips, matching the previous local totals.

Receipts, original instructions, frames, and verification scripts:
`artifacts/qemu-a6400/rear-controls/`.

The next boundary is the stock selection through the receiver for
`Camera Settings2 → FINDER/MONITOR → Monitor(Manual)`, then the effect of
the two dial channels in original application code.

## 2026-11-18 follow-up

The browser used QMP `send-key` with a 180 ms hold for every control.
On QEMU 11.1.3 this command queues both a release delay and a delay
after release, in guest virtual time (`ui/input.c`). A subsequent detent
or selector toggle could sit behind those delays. The panel now sends
one `input-send-event` containing a down/up pair for each wheel turn or
selector toggle. MENU, the directional keys and the AF/MF–AEL button
retain their timed holds. The native constructed-board test asserts
that these edges take effect while a directional hold remains active.
Re-running that test with the old timed path left the wheel counter at
zero behind the directional hold (`timed-queue-negative.txt`); the
immediate-edge path passes, including 16-bit counter wrap and both
GPIO selector transitions.

The replay used the E0524 prepared capture (`emmc.img`
`18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b`,
`av-cam.bin` `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209`)
and QEMU SHA-256
`f88c1a77c80f636eef82cf20cd9aeb566614bd03f844c10b1a60a71feb5508f4`.
It used `EVSEL_LATE_US=500`, icount and the capture's own `ulogio.bin`.
No camera, USB or physical SD was used.

| Boundary | Observation |
| --- | --- |
| Selector in the original kernel driver | The captured `input.ko` table identifies P0.11 as `0x70c6` (`KEY_SLI_AF_MF_AEL` in the captured `libObj.so`). With the fixed QMP path, the driver's `get 0x70c6` reported `0 → 1 → 0`. Its separate `0x70dc` button entry also tracked press and release. This is not a test of focus or exposure behavior. |
| Wheel in the original `av-cam` | The package PC `0x63a5ecde` above does not apply to the captured image. The captured reader executes `ldrh` at `0x63a6dc62`; its `JogTpu` delta method starts at `0x63c27310`, with the signed result from the delta helper in `r0` at `0x63c2733a`. A passive trace of the original loads observed channel 0 reading `0 → 1 → 2 → 1 → 0` after separated QMP turns. The trial had no observed nonzero delta at the probed return site and no verified application event or menu movement. |
| Original menu | This checkout lacks the E0527 power-on capture. The unmodified captured boot without it reported power-off factor `0x7` at guest time 2.178 s and never reached `SetContentTarget`. The wheel trace used an explicitly synthetic power-input diagnostic, not a substitute capture. Neither that trial nor a retry with the E0572 display overlay produced a usable menu. The E0573 MENU/directional positive control above was not reproduced in these trials. |

Receipts and observation scripts are under
`artifacts/qemu-a6400/controls-no-micro-dkJQs4/`. The inspector uses QMP
for control input, the original driver's proc readout for keys, and a
read-only TCG plugin for the TPU instructions. The earlier timed-key
trial did not see the second selector state within its bound. The
immediate-edge trial saw both states and the button release; a later
directional proc assertion in that trial used the wrong idle value and
is discarded. An independent immediate-edge replay (`native-observation-07`)
repeated both selector and button transitions and exited 0. The synthetic
front-input diagnostic is not enabled or accepted as an E0527 capture by
the launcher.

From the project root in WSL, with the verified prepared inputs copied to
the Linux filesystem (`INPUTS`) and `A6400_QEMU_DIR` pointing at the tested
QEMU source/build tree, the driver check is:

```bash
R=artifacts/qemu-a6400/controls-no-micro-dkJQs4
PYTHONPATH="$PWD" python3 "$R/observe.py" --qemu "$A6400_QEMU_DIR" \
  --inputs "$INPUTS" --key-only --direct-edges --report "$R/NEW-DRIVER-RUN"
```

The original wide wheel trace (`native-observation-03`) used
`--virtual-front-fixture "$R/diagnostic-front-input.bin"` instead of
`--key-only --direct-edges`. The diagnostic sent the documented virtual
power and idle-key bits, a minimal version/status TLV reply and no valid
slot-C reply; its digest is `0739537a9d398fa2e74fb323594b54e06c6d983342c1aa50ed19418397af0bae`.
This trial cannot establish normal startup or full sub-micro behavior.

To finish the application test, boot with the verified E0527 page
(`SUBMICRO_PAGE`) or its original 384-byte A/C/B extract
(`SUBMICRO_FRAMES`). First reproduce MENU and directional navigation.
Then use separated clockwise/counterclockwise turns in a scrollable list
and check opposite changes to the firmware selection. Exercise the
selector in both positions and press its button in a relevant shooting
state; trace the application event or observe the assigned action.
Neither a QMP acknowledgement nor a changed register is a pass.
Touch input is unchanged; [mod hot reload](../../qemu-a6400/MOD_DEVELOPMENT.md#limits)
does not validate the touch handlers.

## Follow-up: stock wheel detents (2026-11-19)

The captured TPU reader at `0x63A6DC62` saw `0 -> 1 -> 0` with
the old one-count binding. The stock `Network2` menu stayed identical
while Down changed the screen (`bringup-stock-wheel-rail-01`).
The original control path normalizes the channel-0 count by four before
the `JogTpu` delta: four CCW edges produced raw `0xFFFC`, normalized
`0x3FFF`, wrapped `-1`, and dispatched `-24` at `0x63C27466`.
Four CW edges returned the count to zero and dispatched `+24`
(`bringup-stock-dial-multi-01`). Both were observed in the original code.

The opt-in rear wheel now changes the 16-bit phase count by four per
host turn. In `bringup-stock-dial-detent-01` (QEMU
`ab62eff98b068f155ca3bc812d8650301c6dee638523725b6104dd77399ec50c`,
captured settings, powered sub-micro, lens rail reply and bars), one CCW
turn moves the highlight from `Bluetooth Rmt Ctrl` to
`Reset Network Set.`; one CW turn returns to the byte-identical initial
screen. All three snapshots are at step 42. The constructed-board
test also checks wrap and immediate edges during a timed key hold.

AF/MF--AEL application behavior is still unproven. In
`bringup-stock-tpu-el-01`, both switch positions and press levels
reached the GPIO bank, but the only visible change was a blinking
`NO CARD` notice. In `bringup-selector-drive-mode-01` the unmodified
custom-key menu assigned `Drive Mode` to the AF/MF button. A button
press in either selector position left the shooting view byte-identical
to the idle control. DISP still changed the shooting layout. The
application trial does not qualify the physical body-button mapping or
prove drive-mode, exposure-lock, or focus input.

The passive `bringup-body-dispatch-01` observer corroborates delivery
through the original `libObj` rather than only the register readbacks.
The `I-InputMain` dispatch and send sites (`+0x8622C6`/`+0x8624B0`)
observe both selector levels (`0x70C6`) and press/release (`0x70DC`).
These events reach original input dispatch, but the assigned view
action remains unverified.

In `bringup-stock-input-events-01`, the original input service receives
the combined button as `0x15010340`. The original filter at
`libObj+0x3D1600` receives `0x15010122`/`0x15010121` in one lever
position and `0x15010132`/`0x15010131` in the other (press/release).
The original branch in captured `viewUnified2.so`
(`fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d`,
`+0x2BD172` through `+0x2BD19E`) selects these distinct pairs.
This proves the two positions are distinguished beyond GPIO dispatch.

`bringup-af-ael-drive-control-01` repeats the native `Drive Mode`
assignment and uses the direct **Left** key as a comparison; Up is
DISP, not Drive Mode. The two selector-position frames and both
post-Left frames are identical, showing the stock shooting layout
with `NO CARD`, not a Drive Mode dialog. The changed blink state
relative to the preceding frame is not a custom-key action.
The direct comparison itself did not open the dialog, so it
does not identify a fault specific to the AF/MF--AEL binding.

With the DFS switch handshake enabled, `bringup-dfs-af-ael-custom-01`
confirms the original AF/MF `Drive Mode` assignment again and the
direct Left key **does** open the stock Drive Mode selector. The two
lever positions still emit the distinct original filter pairs. The
first lever press starts while the direct dialog is open, so this
is not a symmetric application-level comparison. Neither sampled
post-press frame demonstrates that the assigned action ran.

The held-input repeat (`bringup-dfs-af-ael-hold-01`, QEMU
`ba71c8f8e5eb…`) starts with the assigned AF/MF button in the original
shooting view. Pressing and holding it opens the same stock Drive
Mode selector as direct Left. The two PPMs are byte-identical
(`11a1971b90ee…`), and the selector remains after release. After
switching the lever, holding the button shows the stock AEL asterisk
and a fixed ISO readout; release removes the asterisk and restores
`ISO AUTO`. Original press/release pairs are `0x15010122/21` and
`0x15010132/31`. This proves the assigned actions at the stock UI,
not sensor focus or exposure fidelity.
