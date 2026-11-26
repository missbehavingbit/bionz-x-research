# E0664 — The a6400 shutter works over Wi-Fi; the drive mode decides whether it is offered

Date: 2026-11-21. **Physical** on the owner's ILCE-6400 (firmware 2.00, `Smart Remote Control
__SAK__` 2.1.7) over its own access point, 2026-11-20 19:44–20:52 UTC, plus offline reading of
the packaged `orb-server`. Unlike E0659 this session **changed one setting and took one
photograph**: the drive mode was set to `Single` twice and returned to `Continuous` both
times, and `actTakePicture` took one frame. The owner reseated the lens and pressed the body's
own shutter once during the session.

Supersedes E0659 §0 items 3 and 5, §3 and §4's storage bullet; see §8 for the full list.

## 0. Short answer

1. **The shutter works over Wi-Fi.** With the drive mode at `Single`, the camera advertises
   `actTakePicture`, and after a half-press that reached `Focused` the call returned a postview
   URL; the postview is a real 1616×1080 JPEG of 422,913 bytes.
2. **Drive mode decides which shutter the camera offers.** At `Continuous` the list has 47
   methods with `startContShooting`/`stopContShooting` and no `actTakePicture`; at `Single` it
   has 50, with `actTakePicture`, `awaitTakePicture` and the three self-timer methods, and without
   the continuous pair. Switching back restores the 47 exactly.
3. **E0659's 47 are this camera in `Continuous`.** Today's 47-method list is identical, name for
   name, to the one recorded in E0659. Error `1` for `actTakePicture` was the answer to a method
   the current drive mode does not offer, not an application registration.
4. **`getStorageInformation` answering `[[]]` does not mean "no card".** It answered `[[]]`
   all session with a card in the body; the body's own `No Media` error never appeared.
5. **The served shapes are the declared ones.** `getSupportedExposureCompensation` is
   `[[15],[-15],[1]]` and `getExposureCompensation` is `[0]`, the integer types `orb-server`
   declares, which closes E0659 §8 against the recorded summary.

## 1. Provenance

Every call was made through `tools/research/scalarwebapi.py` with `--transcript` semantics: one
JSON line per call with the request as sent and the answer byte for byte, HTTP framing included.

- `docs/evidence/cross_model/a6400-scalarwebapi-2026-11-20-transcript.jsonl`: 72 calls, SHA-256 `248939dc0fda10439901f5800e5c664aec07f57e003442000176d1f3b960562f`.
- Device description: `X_FirmwareVersion` `2.00`, `X_ServerType` `Control with Smartphone`.
- `getApplicationInfo`: `["Smart Remote Control __SAK__","2.1.7"]`; `camera/getVersions`
  `1.0`–`1.8`.

Three rows between 20:11:50 and 20:13:10 are transport failures: the access point went down while
the lens was reseated, and the client timed out. They are kept as recorded.

## 2. The list follows the body's state

Each distinct `getAvailableApiList` answer, compared with the 47-method list:

| methods | times (UTC) | body state | added | removed |
| --- | --- | --- | --- | --- |
| 47 | 20:18:15, 20:50:11, 20:50:55, 20:52:14 | lens read (F4.0), drive `Continuous` | — | — |
| 46 | 19:44:57, 20:03:18, 20:06:27 | lens not read (`getFNumber` → `"--"`), drive `Continuous` | — | `setFNumber` |
| 50 | 20:50:51, 20:52:04 | drive `Single` | `actTakePicture`, `awaitTakePicture`, `getSelfTimer`, `setSelfTimer`, `getAvailableSelfTimer` | `startContShooting`, `stopContShooting` |
| 33 | 20:18:04 | 11 s after the access point came up | `actTakePicture`, `awaitTakePicture` | 14 getters and setters for drive mode, aperture, ISO, shutter speed and white balance, and the continuous pair |

The drive mode row is the controlled one: `setContShootingMode [{"contShootingMode":"Single"}]`
answered `[]`, `getContShootingMode` then answered `Single`, and the list changed within 0.8 s;
the reverse call restored the 47. The lens row is an observation: the owner reported the lens
attached while the body read `"--"`, and after reseating it the body read `4.0` and
`setFNumber` returned. The 33-method row is a start-up state seen once, before the body reported
its drive mode.

## 3. The photograph

| time (UTC) | call | answer |
| --- | --- | --- |
| 20:50:52 | `actTakePicture`, no half-press, dark room | error `40400` |
| 20:52:04 | `actHalfPressShutter` | `[]`; `focusStatus` `Not Focusing`, then `Focused` at the 1.0 s poll |
| 20:52:11 | `actTakePicture` | `[["http://192.168.122.1:60152/pict261120_2249060000.JPG?…"]]` |
| 20:52:13 | `cancelHalfPressShutter`, `setContShootingMode` → `Continuous` | `[]`, `[]` |

Exposure was f/4.0, 4", ISO 100 in aperture priority. Sony's client names `40400`
`ShootingFail`, "Failed to photograph." (`EnumErrorCode`, 7.6.0, E0660). The likeliest reading
is that focus did not lock in the dark without a half-press. That is an inference: the session
recorded no focus status before that call.

## 4. Storage and the body's own errors

- **`getStorageInformation`** answered `[[]]` at 19:44:57, 19:45:34, 20:03:19, 20:06:27,
  20:18:04 and 20:18:26, and `getEvent` slot 10 (`storageInformation`) was `[]` every time,
  while the owner confirmed a card was inserted and took a photograph on it with the body's
  button. `orb-server` declares the slot as `{storageID, recordTarget,
  numberOfRecordableImages, recordableTime, storageDescription}*`; this application does not fill
  it.
- **`triggeredError`** (slot 7) never carried a value. Its strings in `orb-server` form a
  value-to-name table at `0x4dadb4` (packaged image), codes 1–15, including `No Media` (4),
  `Shooting Impossible` (9) and `Overheating` (7). The body therefore never reported a missing card.
- **`continuousError`** (slot 6) carried `Overheating Warning` and `Remote Control Not
  Currently Available`, both `isContinued: false`, in the first `getEvent` after each
  connection (19:44:57, 20:18:15) and never later. They are codes 1 and 2 of a second table at
  `0x4dae3c`. The body showed no overheating indicator. The shutter worked in the same session, so
  neither message blocked it. What raises them on connection is not established: no direct code
  reference to either table was found.

## 5. Served shapes

| method | `orb-server` declares (version 1.0) | served |
| --- | --- | --- |
| `getExposureCompensation` | `["int"]` | `[0]` |
| `getSupportedExposureCompensation` | `["int*","int*","int*"]` | `[[15],[-15],[1]]` |
| `getAvailableExposureCompensation` | `["int","int","int","int"]` | `[0,15,-15,1]` |
| `getEvent` | typed slots | 35 slots at version 1.0, 60 at 1.2; slot 25 `{current 0, max 15, min -15, stepIndex 1}` |
| `actTakePicture` | `["string*"]` | one URL in one array |
| `setContShootingMode` | — | takes `[{"contShootingMode": …}]`, answers `[]` |

The range is an index at 1/3 EV (±15 = ±5.0 EV), as Sony's client reads it.
`getMethodTypes` (`["1.0"]`) answers although it is not in any advertised list: 72 rows
under a `results` key, 68 distinct names.

## 6. Alternative explanations

- **"The shutter appeared because of the reseated lens, not the drive mode."** After reseating,
  the lens was read (`getFNumber` `4.0` at 20:18:23; `setFNumber` advertised at 20:18:15 and
  20:50:11), and the list was 47 without `actTakePicture` both times. The
  shutter appeared only after `setContShootingMode` and left after the reverse call, twice.
- **"The card made the difference."** The card was in the body all session, and the drive mode
  switched the shutter on and off with the card unchanged.
- **"`40400` means the remote shutter is refused."** The same call succeeded 79 s later in the
  same drive mode, after a half-press.
- **"E0659's body was in a different drive mode."** Its recorded summary gives
  `getContShootingMode` as `Single`, yet its list is today's `Continuous` list exactly,
  including `startContShooting` and excluding the three self-timer methods. The list is the more
  specific of the two; this is another inconsistency of that summary (E0659 §8).

## 7. Confidence and limitations

- **Verified on hardware:** the four list states and their differences; the drive-mode switch in
  both directions, twice; one photograph through `actTakePicture`; `40400` without a half-press in
  the dark; `getStorageInformation` `[[]]` with a card present; the served exposure-compensation
  shapes; `getMethodTypes` answering.
- **Verified offline:** the two error tables in `orb-server` and their codes; `40400`'s name in
  Sony's client.
- **Not verified:** that `startContShooting` shoots a burst (it was not called today); whether the
  other withheld setters (`setShutterSpeed`, `setExposureMode`) follow the body's exposure mode
  the way the shutter follows the drive mode (the body was in aperture priority throughout); what
  raises the two `continuousError` messages; why the lens read `"--"` before it was reseated.

## 8. What this changes in earlier records

- **E0659 §0 item 3 and §3**, "the shutter is unreachable while this application is the one
  registered": wrong. The shutter is offered at drive mode `Single` and works.
- **E0659 §0 item 5**, "the short list is the active application's registration": the list
  follows body state (drive mode, lens). The 221-versus-47 count stands as a count.
- **E0659 §4**, "no memory card … `getStorageInformation` answered `200 [[]]`, so no card was
  present": the inference is wrong, because `[[]]` is served with a card inserted.
- **E0659 §4**, "the camera is locked to still capture under this application": not re-tested.
  `shootModeCandidates` in `getEvent` lists `still`, `movie` and `slow and quick`, while
  `getAvailableShootMode` offers only `still`.
- **E0659 §8** is answered: the summary's shapes were normalized, and the camera serves the types
  `orb-server` declares.
- **E0660 §2** stands. Its guard, "refuse unless `actTakePicture` is advertised", is the behaviour
  that made a client in `Continuous` look broken.
- **E0661 §4**, "it cannot explain `actTakePicture` alone, because `startContShooting` is
  accepted in the same state": the two are alternatives selected by the drive mode. E0661's
  reading of the withheld setters as a narrowed registration is open again (§7).

## 9. Dependent implementation

`tools/research/scalarwebapi.py` names `40400`, `40403` and `40404` with Sony's own words.
`tools/research/camera_remote.py` gains a `drive` setting, and its capability text says that a
missing shutter next to an offered burst means drive mode `Continuous`. Contract tests:
`tests/test_camera_remote.py`, `tests/test_scalarwebapi.py`.

## 10. Next experiment

Turn the mode dial to M and read `getAvailableApiList` once. If `setShutterSpeed` and
`setExposureMode` appear, the remaining gaps in E0661 are body state as well. In the same session,
call `startContShooting`/`stopContShooting` once at `Continuous` with the transcript on, so the
burst's served shape is recorded.
