# E0659 — The camera ships 221 camera methods and advertises 47; the shutter is among the missing

Date: 2026-11-14. **Physical, read-only** on the owner's ILCE-6400 (firmware 2.00) over its own
access point, plus offline analysis of the packaged `orb-server`. No settings were written, no
mode changed, no photograph was taken by this record's commands.
Rebased onto upstream `main` on 2026-11-20 and numbered after the records published there; the measurements are unchanged.

> Superseded shutter claim: [E0664](0664-shutter-works-drive-mode-decides.md) took a photograph
> with `actTakePicture` on this body. The 47-method list is the camera at drive mode
> `Continuous`, where it offers `startContShooting` instead of the shutter; at `Single` it offers
> `actTakePicture`. §0 items 3 and 5, §3, and §4's "no card" inference are wrong. `[[]]` from
> `getStorageInformation` is served with a card inserted. The 221-method count stands.

Supersedes nothing. E0658 measured which methods this camera exposes; this record measures *why*
that list is short, and separates three things E0658 left together: what the firmware can do, what
the running application advertises, and what therefore answers.

## 0. Short answer

1. **The packaged firmware knows 221 camera methods.** `bin/orb-server`
   (SHA-256 `d65b995b2e9ff7c52da73a61603841a06e3010836ac64979ffcf5e2c7503de92`, 5,976,936
   bytes) contains the whole standardized method list — `actTakePicture`, `awaitTakePicture`,
   `actZoom`, `setFocusMode`, `setExposureMode`, `setStillSize`, `setStillQuality`,
   `setColorSetting`, `startMovieRec` and 212 more.
2. **The running application advertises 47.** `camera/getAvailableApiList` returns 47 names, and
   the set is stable across repeated `getEvent` cycles.
3. **The shutter is one of the 174 not advertised.** `actTakePicture` is absent from the 47 and
   answers error `1` (empty message) when sent. `actHalfPressShutter` is present and answers
   `200 {}`. The difference is state, not a missing firmware routine.
4. **The handler exists.** `WebApiUser::WebApi::wa_job_actTakePicture_cmd` is a real function in
   `libInfraWebApi.so` (SHA-256 `4677098775548c9b…`), confirmed by REA/Ghidra 12.1.4, and it does
   exactly one thing: `sendMsgToOrb` — hand the request to `orb-server`.
5. **Therefore the short list is the active application's registration, not a firmware limit.**
   The application running on this unit is `Smart Remote Control __SAK__` 2.1.7, read from
   `camera/getApplicationInfo`.

## 1. What works over the network on this body

Measured by call, not inferred from a list:

| capability | method | result |
| --- | --- | --- |
| live view | `startLiveview` **with no parameters** | URL, then MJPEG 640×424, ~38 KB/frame, 52 complete frames in 2,031,616 bytes |
| stop live view | `stopLiveview` | `200 [0]` |
| half-press | `actHalfPressShutter` | `200 {}` |
| release half-press | `cancelHalfPressShutter` | `200 {}` |
| continuous shooting | `startContShooting` / `stopContShooting` | `200 {}` both |
| read/write ISO, aperture, shutter, WB, EV | `get`/`setIsoSpeedRate` etc. | present in the 47 |
| shutter | `actTakePicture` | **error `1`**, and absent from the 47 |

`startLiveview` is worth stating precisely because it is a trap: called with a version string it
answers error `3` (illegal argument); called with nothing it returns the stream URL. That was
measured both ways.

## 2. The gap, counted two ways

`strings bin/orb-server` yields one line listing the standardized `service/method` pairs. Split on
the service prefix, it names **221** distinct `camera/` methods. The camera's own
`getAvailableApiList` names **47**. The difference is **174**, and a run-time set difference is
reproducible:

```bash
strings bin/orb-server | tr ':' '\n' | grep -o 'camera/[A-Za-z0-9_]*' | sort -u   # 221
python3 -I tools/research/camera_remote.py --offline \
  docs/evidence/cross_model/a6400-scalarwebapi-session.json                        # 47
```

The 174 include capabilities that would matter for the owner's work: `actZoom`,
`setFocusMode`, `setExposureMode`, `setStillSize`, `setStillQuality`, `setColorSetting`,
`setSceneSelection`, `startMovieRec`, `actTrackingFocus`, `setTouchAFPosition`.

## 3. Why this is not a firmware limitation

Three independent observations point the same way:

1. **The handler is compiled in.** `wa_job_actTakePicture_cmd` exists and runs; it forwards.
2. **The server knows the method.** `orb-server` contains `camera/actTakePicture` in its own
   dispatch list.
3. **The refusal is a state answer.** `actTakePicture` returns error `1`, documented by its own
   handler path as a state refusal, not error `12` (no such method). A method the camera did not
   know at all would be error `12`, as `getCreativeStyle` is.

So the shutter is unreachable *while this application is the one registered*, and the reason
lives in the application, which is not in the packaged firmware image: no `__SAK__` directory and
no `.apk` exist anywhere in the unpacked tree, so the application is installed on a partition the
updater image does not carry.

## 4. Alternative explanations

- **"Error 1 means the camera is busy; wait and retry."** Retried across more than ten event
  cycles and after `stopLiveview`; the answer stayed error `1` and the method stayed absent from
  the 47. A transient state would have changed at least once. The same code is what Sony's own
  client calls "Any" — an unclassified refusal — so nothing about the number itself says "busy";
  `Illegal Request` is `5` and `No Such Method` is `12` (E0660).
- **"`startRecMode` would widen the list."** It answers error `12` on this body, so the method is
  not present at all, and `getAvailableShootMode` returns only `still`. `setShootMode("movie")`
  answers error `1`. The camera is locked to still capture under this application.
- **"No memory card, so the shutter refuses."** `getStorageInformation` answered `200 [[]]` for
  the whole session, so no card was present. **This confound is not eliminated**, but the
  evidence points against it: `startContShooting` answered `200 {}` in the same state, and a
  body that refused to expose the shutter for lack of storage would not accept a continuous
  shooting request either. Both facts are true and neither settles it, so the limitation stands
  and the deciding call is still the same one with a card inserted.
- **"The owner's unit differs from the packaged firmware."** The unit reports firmware 2.00
  (`DigitalImagingDesc.xml`, `X_FirmwareVersion`), while the packaged updater analysed here is a
  different point release. Addresses quoted are the packaged image's, as the project requires.

## 5. Confidence and limitations

- **Verified on hardware:** the 47-method list and its stability, the live-view call shape and
  frame format, half-press and continuous shooting answering `200`, the shutter answering error
  `1`, `startRecMode` and `setShootMode` being refused, the empty storage list.
- **Verified offline:** 221 camera methods in `orb-server`; `wa_job_actTakePicture_cmd` existing
  and forwarding; the absence of the application package from the unpacked image.
- **Not verified:** whether a memory card makes the shutter answer differently — the storage list
  was empty and this is the one confound not eliminated. Whether a different on-camera
  application registers a wider list. Whether `movie` mode widens anything.
- **Not claimed:** that a modified client can force the shutter. Error `1` is a refusal by the
  application; attacking it is a separate question and is not attempted here.

## 6. Dependent implementation

`tools/research/scalarwebapi.py` is the transport and `tools/research/camera_remote.py` is the
remote built on it. The remote **discovers** its verbs from `getAvailableApiList` and refuses a
command the camera did not advertise, printing the reason, so a refusal is never reported as a
malfunction. Contract tests: `tests/test_scalarwebapi.py`, `tests/test_camera_remote.py`.

A recorded session is committed at
`docs/evidence/cross_model/a6400-scalarwebapi-session.json` so the remote can be exercised
`--offline` without a camera.

## 7. Next experiment

Insert a memory card and re-ask `actTakePicture` once, then `getStorageInformation`. If the
shutter still answers error `1` with storage present, the application registration is the whole
answer; if it starts working, this record's third claim is wrong and the storage check is the real
gate. Either outcome is decisive, and it needs one camera and one card.

## 8. Provenance of the recorded session (added 2026-11-20)

The committed session file is a per-method summary written after the session: one answer per
method, without request bodies, request ids, timestamps or HTTP framing. The raw exchange was not
kept, in the repository or outside it. That matters because four of its 31 answers disagree with
the return types `orb-server` itself declares in its method-type table, the strings
`getMethodTypes` serves:

| method | declared by `orb-server` (version 1.0) | recorded |
| --- | --- | --- |
| `getExposureCompensation` | `["int"]` | `["0"]`, a string |
| `getSupportedExposureCompensation` | `["int*","int*","int*"]` | one list of EV strings, `"-5.0"` … `"5.0"` |
| `startContShooting`, `stopContShooting` | `["string*"]` | `[]` |
| `getEvent` | an array of typed slots, one per event kind | one object, the `availableApiList` slot |

Reproduce the declarations with
`strings -n 8 bin/orb-server | grep -F '["getSupportedExposureCompensation",'` (and the same for
each method). Sony's own client 7.6.0 also reads `getSupportedExposureCompensation` as three
integer arrays (E0660). Section 1 writes `200 {}` for half-press and continuous shooting, while
the file holds `{"result": []}`; the file is the more specific of the two.

Two explanations fit, and nothing that was kept separates them: the running application
(`Smart Remote Control __SAK__` 2.1.7, absent from the packaged image) serves shapes other than
the ones `orb-server` declares, or the summary was normalized when it was written. Until a raw
capture exists:

- **The answer shapes in the file are not measurements.** The remote and the panel accept both
  the declared and the recorded exposure shape, so neither depends on which one is right.
- **The 47-method list and the error codes rest on the same summary.** The `availableApiList`
  slot of the recorded `getEvent` names the same 47 methods, but it is in the same file, so it
  is consistency, not independent confirmation.

The transport now keeps the raw exchange: `--transcript FILE` on `camera_remote.py`,
`camera_transfer.py` and `camera_panel.py` appends every call as one JSON line with the request
body as sent and the answer byte for byte (base64). The next hardware session (section 7) should
run with it and should include `getSupportedExposureCompensation`, `getExposureCompensation` and
one full `getEvent`, so the served shapes are recorded rather than summarized. `getMethodTypes`
is not among the 47; asking it directly through the transport is still worth one call, since
either its table or its refusal is data.
