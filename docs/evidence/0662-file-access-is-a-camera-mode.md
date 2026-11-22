# E0662 — File access is a different camera mode, not a different method

Date: 2026-11-14. **Physical, read-only** on the owner's ILCE-6400 (firmware 2.00) over its own
access point, plus offline reading of the shipped client.
Rebased onto upstream `main` on 2026-11-20 and numbered after the records published there; the measurements are unchanged.

Answers the question E0661 left open: whether copying photographs off the camera over Wi-Fi is
merely unported, or unavailable. It is unavailable **in the mode the camera was in**, and the
reason is not an error code that a better client could get past.

## 0. Short answer

1. **The `avContent` service is present.** It answers `getVersions` with `1.0`, `1.1`, `1.2`,
   `1.3`.
2. **It exposes no methods at all.** `getAvailableApiList`, `getSchemeList`, `getSourceList`,
   `getContentList` and `getContentCount` each answer error `12` — Sony's `No Such Method`.
   A service that names four versions and serves none of them is not a client problem.
3. **The mode is the gate, and the camera says so itself.** `/DigitalImagingDesc.xml` on
   `64321/tcp` reports `X_ServerType = Control with Smartphone`. Sony's own client branches on
   this exact string: `DeviceInfo.isSendToSmartphone()` is true only for
   `"Send to Smartphone"`, and its file browser is reached only in that branch.
4. **No network route can switch it.** `setCameraFunction` exists but refuses:
   `"Remote Shooting"` answers `5` (Illegal Request) and `"Send to Smartphone"` answers `3`
   (Illegal Argument), while `getCameraFunction` answers `40401` (Camera is not ready). The mode
   is a menu action on the camera.

## 1. The measurements

| probe | answer |
| --- | --- |
| full TCP scan of the camera | only `64321` and `10000` open |
| `avContent/getVersions` | `200`, four versions `1.0`–`1.3` |
| `avContent/getAvailableApiList` | `error [12, ...]` |
| `avContent/getSchemeList` | `error [12, ...]` |
| `avContent/getSourceList {"scheme":"storage"}` | `error [12, ...]` |
| `avContent/getContentList` | `error [12, ...]` |
| `camera/getStorageInformation` | `200 [[]]` — an empty list |
| `X_ServerType` (`DigitalImagingDesc.xml`) | `Control with Smartphone` |
| `SOAP` to the declared `DigitalImaging` control URL | `404` |
| `camera/setCameraFunction ["Remote Shooting"]` | `error [5, ""]` |
| `camera/setCameraFunction ["Send to Smartphone"]` | `error [3, ""]` |
| `camera/getCameraFunction` | `error [40401, ""]` |

## 2. Why this is a mode and not a per-method grant

E0661 measured that the `camera` service serves 16 `getSupported*` lists and withholds setters.
That is a narrowed *grant* inside a working service. This is different: `avContent` does not
serve **any** method, while still answering the version question. A service with no methods is
not a service with restricted methods, and no ordering of calls changes it.

The two modes are named by the camera itself and by Sony's client:

- `Control with Smartphone` — remote shooting. The `camera` service runs, `avContent` does not.
- `Send to Smartphone` — card browsing and transfer. The reverse.

The body is in the first. That was true for the whole session in which E0658–E0661 were
measured, which is why the transfer question could not be answered then.

## 3. Alternative explanations

- **"A newer API version would serve the methods."** The versions are advertised up to `1.3` and
  requests were made with `1.3`; the answers were still error `12`.
- **"The card is missing, so there is nothing to list."** `getStorageInformation` answered an
  empty list, so this is possible for a *listing*, but it cannot explain `getSchemeList`
  answering `12`: a camera with no card would answer a scheme list with `storage` present and a
  source list with a count of zero. Error `12` is "this name does not exist here".
- **"The `DigitalImaging` UPnP service is the file path."** Its declared control URL answers
  `404`, so the device description advertises a service it does not serve in this mode.

## 4. Confidence and limitations

- **Verified on hardware:** every row of the table in §1, in one session, on one body.
- **Verified offline:** that Sony's own client gates its file browser on the same
  `ServerType` string, from `DeviceInfo.java`.
- **Not verified:** that switching the camera to `Send to Smartphone` makes `avContent` serve
  its methods — that is the next experiment and needs the owner's hands on the menu. The
  expectation is strong (Sony's client is written for exactly that mode) but it was not observed.
- **Not claimed:** that transfer is impossible by any route. PTP over USB is a separate path this
  record does not touch.

## 5. Dependent implementation

`tools/research/camera_transfer.py` is the transfer client, built on the shapes Sony's own
classes define (`ContentInfo.original[].url`, `ContentListSource.cnt`, a 24-item page ceiling
taken from the client's own guard). It reads `X_ServerType` **before** calling anything and
refuses in words:

```
The camera reports ServerType='Control with Smartphone'. File access exists only in
'Send to Smartphone' mode; switch the camera to it and run this again (E0662).
```

`safe_join` keeps only the last path component of a name the camera sends, because a FAT file
name is untrusted input and `../..` must not write outside the chosen directory.
Tests: `tests/test_camera_transfer.py` (18 tests).

## 6. Next experiment

Switch the camera to **Send to Smartphone**, join its access point, then:

```bash
python3 -I tools/research/camera_transfer.py --out /path/to/card-copy
```

If `getSchemeList` answers with `storage`, the mode was the whole gate and the port gains the
transfer half of the app. If it still answers `12`, then file access on this body needs a
different channel entirely — PTP over USB — and the Wi-Fi port is shooting-only. One menu
action, one command, decisive either way.
