# E0658 — Camera Wi-Fi works and exposes ScalarWebAPI, which has no Creative Style

Date: 2026-11-13. **Physical, read-only**: the owner's own a6400 over its own Wi-Fi access point.
No settings were written, no mode changed by this record's commands.
Rebased onto upstream `main` on 2026-11-20 and numbered after the records published there; the measurements are unchanged.

This settles two open questions at once. Wi-Fi on this camera works, contrary to what this project
had recorded from a single failing unit; and the network path, while real and well documented by
Sony, does not reach the settings another project's film-recipe app depends on.

## 0. Short answer

1. **Wi-Fi works on this camera.** The camera raised its own access point, a host joined it, and
   the camera answered on `192.168.122.1`. No reboot, no crash. E0409's "Wi-Fi reboots the camera"
   describes a different unit with a failing SDIO bring-up, not the model.
2. **The port is HTTP, not PTP/IP.** `64321/tcp` answers `Server: UPnP/1.0 SonyImagingDevice/1.0`,
   and `/dd.xml` on that port is a UPnP device description. A PTP/IP handshake to it times out.
3. **Sony ScalarWebAPI runs on port 10000** and answered `200 OK`. It exposes **47 methods** across
   the `camera` service; `system`, `guide` and `accessControl` reject `getAvailableApiList`.
4. **The API writes white balance and exposure, and has no Creative Style.** Asked directly, the
   camera returns error `12` for `getCreativeStyle`/`setCreativeStyle`, `getContrast`/`setContrast`
   and `getPictureEffect`. Absence is proven by request, not inferred from a list.

## 1. The evidence

The camera's own screen showed `SSID DIRECT-mRE1:ILCE-6400` and a QR code. The code contains the
credentials in Sony's own format:

```
W01:S:mRE1;P:ft67rvUx;C:ILCE-6400;M:707414B1EFA7
```

`S` is the SSID suffix, `P` the password, `C` the model, `M` a hardware id that matches the UDN in
`/dd.xml` (`uuid:00000000-0005-0010-8000-707414b1efa7`).

| probe | result |
| --- | --- |
| full TCP scan of the camera | **only `64321` open**; 15740, 80, 8080, 443, 1900 all closed |
| `GET /` on `:64321` | `404`, `Server: UPnP/1.0 SonyImagingDevice/1.0` |
| `GET /dd.xml` on `:64321` | `200`, UPnP description, `friendlyName ILCE-6400`, `modelDescription SonyRemoteCamera` |
| services declared | `ScalarWebAPI`, `DigitalImaging` |
| ScalarWebAPI service types | `guide`, `system`, `accessControl`, `camera` |
| `POST /sony/camera` `getApplicationInfo` | `["Smart Remote Control __SAK__", "2.1.7"]` |
| `getAvailableApiList` on `/sony/camera` | **47 methods** |
| SSDP `M-SEARCH` | no reply |
| mDNS `_services._dns-sd` | no reply |

## 2. The 47 methods, and what is missing

Everything the camera offers over this path:

```
actHalfPressShutter  cancelHalfPressShutter  getApplicationInfo  getAvailableApiList
getAvailableContShootingMode  getAvailableExposureCompensation  getAvailableExposureMode
getAvailableFNumber  getAvailableIsoSpeedRate  getAvailableShootMode
getAvailableShutterSpeed  getAvailableWhiteBalance  getContShootingMode  getEvent
getExposureCompensation  getExposureMode  getFNumber  getIsoSpeedRate  getShootMode
getShutterSpeed  getSupportedContShootingMode  getSupportedExposureCompensation
getSupportedExposureMode  getSupportedFNumber  getSupportedFlashMode
getSupportedIsoSpeedRate  getSupportedMovieFileFormat  getSupportedMovieQuality
getSupportedNearModeInPF  getSupportedProgramShift  getSupportedSelfTimer
getSupportedShootMode  getSupportedShutterSpeed  getSupportedViewAngleMode
getSupportedWhiteBalance  getSupportedWirelessFlashSetting
getTemporarilyUnavailableApiList  getWhiteBalance  setContShootingMode
setExposureCompensation  setFNumber  setIsoSpeedRate  setWhiteBalance
startContShooting  startLiveview  stopContShooting  stopLiveview
```

Present and writable: white balance, exposure compensation, f-number, ISO, continuous-shooting
mode, half-press, live view.

Absent, each refused with error `12` when asked by name:

| method | answer |
| --- | --- |
| `getCreativeStyle` | `{"error":[12,"getCreativeStyle"]}` |
| `setCreativeStyle` | `{"error":[12,"setCreativeStyle"]}` |
| `getContrast` | `{"error":[12,"getContrast"]}` |
| `setContrast` | `{"error":[12,"setContrast"]}` |
| `getPictureEffect` | `{"error":[12,"getPictureEffect"]}` |

## 3. Why this matters for the film-recipe app

Recipe Lab builds every recipe from Creative Style and its saturation, contrast and sharpness
sliders, plus Picture Effect. Reading its source: it reaches those through `libbackup.so`'s
`Backup_write` inside the camera, not over the network. This record shows why that choice was
forced rather than preferred: the network API does not expose them.

So the network is a real control channel for shooting, and it is not a replacement for writing
settings. A port of that app to this camera still needs code running on the camera, which means USB.

## 4. Alternative explanations

- **"The Wi-Fi failure in E0409 applies to this camera too."** It does not: this unit raised its
  access point and answered HTTP with no reboot, repeatedly. E0409's own text attributes its crash
  to a failing SDIO card bring-up on one unit ("likely cause is the BCM4339/SDIO power/reset path
  or the module itself"), and E0412 already narrowed that diagnosis. The model-level claim that
  "a6400 Wi-Fi does not work" is refuted here.
- **"Creative Style is available but under another service."** `system`, `guide` and
  `accessControl` all reject even `getAvailableApiList` with error `12`, so only `camera` is
  callable, and it was asked by name.
- **"The API set depends on the mode."** True in general: this is the set in Smart Remote Control
  2.1.7 with shoot mode `still`. A different mode could expose a different list, and this record
  does not claim otherwise for the other two modes the camera reports (`movie`, `slow and quick`).
- **"A newer firmware would expose more."** Possible, and untested here. This unit reports the
  packaged 8.2.x build; the record is exact for this unit.

## 5. Confidence and limitations

- **Verified on hardware:** the port scan, the HTTP server identity, `/dd.xml`, the 47-method list,
  the two reads that returned data (`getWhiteBalance`, `getShootMode`), and the five error-`12`
  refusals.
- **Not verified:** the other two shoot modes; any event subscription (`getEvent` was not
  exercised); whether `DigitalImaging` (the UPnP control URL) offers anything beyond this; a
  different firmware.
- **Not claimed:** that no other network path exists on another body. This is one unit.

## 6. Dependent implementation

No code in this repository changed. The API list is preserved at
`docs/evidence/cross_model/scalarwebapi-apis.json` for a future client. The finding constrains the
design of any port: the settings a film recipe needs are not reachable over HTTP.

## 7. Next experiment

Read the same two lists in shoot mode `movie` and in `slow and quick`, and ask `getEvent` for one
event cycle. If Creative Style appears in any of them, this record is wrong and a network-only port
becomes possible; if it does not, USB is settled as the only route to those settings.
