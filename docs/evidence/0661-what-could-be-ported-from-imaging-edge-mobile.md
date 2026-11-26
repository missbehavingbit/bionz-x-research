# E0661 — What of Imaging Edge Mobile can be ported, decided by the camera's own answers

Date: 2026-11-14. **Offline.** Two committed artifacts and no camera: the method list from the
shipped client (E0660) and the body's own `getAvailableApiList` answer (E0659).
Rebased onto upstream `main` on 2026-11-20 and numbered after the records published there; the measurements are unchanged.

> Superseded reading: [E0664](0664-shutter-works-drive-mode-decides.md) shows that the advertised
> list follows body state. Drive mode `Single` adds the shutter and the self-timer methods, and a
> lens the body does not read removes `setFNumber`. The 47 counted here are one state, drive mode
> `Continuous` in aperture priority, not the application's whole grant. The counts below stand
> for that state.

"Port the whole app" cannot be answered with a yes, and this record answers it with a number
instead. Imaging Edge Mobile is **2,664 source files**; of the 311 web-API names it enumerates,
**46 are callable on this body**. The rest are not missing work — the camera declines to serve
them while its current application is registered. The two are different things and conflating them
is how a port turns into a promise.

## 0. Short answer

1. **The app is much larger than its camera API.** By source file: `remotecontrol` 229,
   `multi` 241, `transfer` 140, `settings` 56, `contentviewer` 36, `bluetooth` 27,
   `wificonnection` 22, `wifi` 21, `qr` 16, `setup` 11, `service` 11, `connect` 10,
   `guide` 7, `location` 5, `cameralist` 3. Six of those areas are not a camera-API feature at
   all: joining a network, reading a QR code with the phone's camera, pairing over Bluetooth,
   one-touch NFC, phone preferences, and a list of previously paired bodies.
2. **The shooting screen is 43 camera properties** (`remotecontrol/controller/menu/property/
   camera/`) plus 11 screen properties (`EnumAppProperty`). Those 43 are what a port would have
   to cover to replace remote shooting.
3. **On this body, 8 feature groups are portable now, 2 are partly portable, 19 are blocked**
   (reproducible with `tools/research/port_map.py`, §2).
4. **The blocking is not a client limitation**, and one measurement makes that sharp: this body
   serves **16** `getSupported*` lists but only **5** matching setters (§3). It tells a client
   what values exist and withholds the right to choose them.

## 1. Inventory, measured from the decompiled client

| area | files | what it is |
| --- | ---: | --- |
| `remotecontrol` | 229 | the shooting screen: live view, menu, settings, shoot, postview |
| `multi` | 241 | multi-camera and multi-body coordination |
| `transfer` | 140 | copying images and video off the camera |
| `settings` | 56 | app preferences |
| `contentviewer` | 36 | browsing what is on the card |
| `bluetooth` / `nfc` / `wifi` / `wificonnection` / `qr` / `connect` | 97 | getting connected |
| `setup`, `service`, `guide`, `location`, `cameralist` | 37 | first run, background service, help, GPS tagging, paired-body list |

The feature surface a photographer would name is concentrated in two places: the 43 camera
properties under `menu/property/camera/` (ISO, aperture, shutter, white balance, focus mode,
still size and quality, drive, self-timer, flash, zoom, scene, colour, steady, view angle, movie
quality and format, and more), and the 11 screen properties (live view quality and rotation,
grid line, selfie, touch shutter, postview display and saving, saving destination, location,
remote power on/off, group edit).

## 2. The port map, and how to reproduce it

```bash
python3 -I tools/research/port_map.py
```

| state | feature groups | why |
| --- | --- | --- |
| **portable now** (8) | live view, half-press focus/AE, continuous shooting, ISO, aperture, white balance, exposure compensation, drive mode | every method behind the feature is advertised |
| **partly portable** (2) | shutter speed, exposure mode | the getter is advertised, the setter is not: the value can be read and not changed |
| **blocked by the body** (19) | single shot, focus mode, zoom, still size, still quality, movie recording, self timer, flash mode, touch focus, tracking focus, manual focus, creative style, picture effect, colour setting, scene selection, steady mode, view angle, live view size, storage | no method behind the feature is advertised |

`port_map.py` prints the same table from the two artifacts, and `tests/test_port_map.py` fails if
a feature is graded portable while any method behind it is unadvertised.

## 3. The measurement that explains the blocking

Sixteen `getSupported*` methods answer on this body. Only five of them have a live setter:

| list served | setter | setter advertised? |
| --- | --- | --- |
| `getSupportedContShootingMode` | `setContShootingMode` | yes |
| `getSupportedExposureCompensation` | `setExposureCompensation` | yes |
| `getSupportedFNumber` | `setFNumber` | yes |
| `getSupportedIsoSpeedRate` | `setIsoSpeedRate` | yes |
| `getSupportedWhiteBalance` | `setWhiteBalance` | yes |
| `getSupportedExposureMode` | `setExposureMode` | no |
| `getSupportedShutterSpeed` | `setShutterSpeed` | no |
| `getSupportedSelfTimer` | `setSelfTimer` | no |
| `getSupportedShootMode` | `setShootMode` | no |
| `getSupportedFlashMode`, `getSupportedMovieQuality`, `getSupportedMovieFileFormat`, `getSupportedNearModeInPF`, `getSupportedProgramShift`, `getSupportedViewAngleMode`, `getSupportedWirelessFlashSetting` | their setters | no |

A camera that served no such lists would look simply limited. One that serves them and withholds
the setters is telling a client exactly which controls it is not granting. That is the shape of a
deliberately narrowed registration, consistent with E0659's finding that the missing handlers
exist in the firmware and only the advertised list is short.

## 4. Alternative explanations

- **"The panel is simply calling the wrong methods."** The names come from Sony's own client
  (E0660), not from this project's reading, and `getSupportedExposureMode` answering while
  `setExposureMode` is refused shows the names are right and the grant is missing.
- **"A different shoot mode would widen the list."** `getAvailableShootMode` returns only
  `still` and `setShootMode` is refused (E0659), so no other mode is reachable to test.
- **"The card is missing, so setters are withheld."** Possible for the storage-dependent
  features, and untested: it is the same one-camera experiment E0659 §7 names. It cannot explain
  `actTakePicture` alone, because `startContShooting` is accepted in the same state.

## 5. Confidence and limitations

- **Verified offline:** the file counts, the 43 + 11 property lists, the port grading, and the
  16-lists-versus-5-setters count, all from committed records.
- **Verified on hardware earlier:** the 47-method list itself (E0659), so the grading input is a
  measurement, not an assumption.
- **Not verified:** whether the withheld setters become available with a card inserted, in another
  shoot mode, or under another registered application. All three are camera experiments.
- **Not claimed:** that the 19 blocked groups are permanently unreachable, or that porting the
  remaining eleven areas (transfer, content browser, connection flow) would face the same gate —
  those use `avContent` and PTP, which this record did not measure.

## 6. Dependent implementation

- `tools/research/port_map.py` — the grading and its printed table.
- `tools/research/camera_panel.py` — the browser panel, now covering every portable setting:
  ISO, aperture, shutter speed, white balance, exposure compensation and drive mode, plus live
  view, half-press, burst, and an event stream (`/api/events`, one long poll of `getEvent`,
  reduced to the fields a person can use rather than the 47-name array the camera re-sends).
- `tests/test_port_map.py` (11 tests), `tests/test_camera_panel.py` (11 tests).

Two defects the panel work found and fixed, both pinned by tests: white-balance and drive-mode
values arrive as objects and were rendered as Python reprs, and the offline replay answered a
getter with the pre-set value, which would have been a silent lie after a successful change.

## 7. Next experiment

The same one E0659 named, now with a second question attached: insert a card, then call
`setSelfTimer` and `setShutterSpeed` once each. If either is accepted, the withheld setters are
storage- or state-gated and the port can grow by 19 groups; if both are refused with code `12`,
the registration is the whole answer and the portable eight are the ceiling over Wi-Fi. One
camera, one card, two calls.
