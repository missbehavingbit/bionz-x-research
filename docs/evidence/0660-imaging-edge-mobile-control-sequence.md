# E0660 — Sony's own app drives this camera with a sequence a naive client misses

Date: 2026-11-14. **Offline.** No camera was contacted for this record. The input is the shipped
Android package, decompiled locally.
Rebased onto upstream `main` on 2026-11-20 and numbered after the records published there; the measurements are unchanged.

This record answers a question E0659 raises but cannot answer from the camera: the body advertises
47 methods while the firmware knows 221, so *how does Sony's own application get a shutter to work
at all?* The answer is in the shipped client, and it is a specific ordering plus two fallbacks that
a hand-written client will not invent.

## 0. Short answer

1. **The package.** `Imaging Edge Mobile` 7.6.0, `com.sony.playmemories.mobile`, version code
   760211020, 39,386,547 bytes, MD5 `43d2f144f1d938cf212f82181231b6cf` — the catalog value and the
   measured value are equal. Signed certificate SHA-1
   `84:E6:C9:BB:87:54:93:9E:C5:D0:69:60:85:2E:6C:C7:DB:98:35:95`, owner `CN=Hiroyuki Tamaki`.
   Obtained through Aptoide's mirror of Google Play, which records the signature as validated
   against the market on 2026-07-03.
2. **The app enumerates 312 web-API names, 221 of them camera methods** — the same 221 the packaged
   `orb-server` carries (E0659). The client holds the whole standardized surface and narrows itself
   at run time from the camera's own answer.
3. **The shutter sequence has four steps and one recovery**, described in §2.
4. **Discovery uses the URL the camera publishes.** The client parses
   `X_ScalarWebAPI_ActionList_URL` and `X_ScalarWebAPI_LiveView_URL` out of the device description
   and never hard-codes a port, so a camera that moved the service would still be found.

## 1. Identity and how it was obtained

| field | value |
| --- | --- |
| package | `com.sony.playmemories.mobile` |
| version name / code | `7.6.0` / `760211020` |
| size | 39,386,547 bytes |
| MD5 (catalog) | `43d2f144f1d938cf212f82181231b6cf` |
| MD5 (measured) | `43d2f144f1d938cf212f82181231b6cf` |
| SHA-256 (measured) | `b03e1ebe7e08d9e158ea9388d900c80010e3331107824e170a4c28bbd2e295e0` |
| signing certificate SHA-1 | `84:E6:C9:BB:87:54:93:9E:C5:D0:69:60:85:2E:6C:C7:DB:98:35:95` |
| signing owner | `CN=Hiroyuki Tamaki` |
| decompiled Java files | 8,575 |

The package itself is **not** committed: it is third-party content and 39 MB. Everything this
record states is reproducible from the recorded metadata in
`docs/evidence/cross_model/imaging-edge-mobile-control.json` and from the public catalog, and
`tests/test_imaging_edge_mobile_record.py` fails if the record's own measured digest stops matching
its catalog digest.

## 2. The shutter sequence, read from the classes

`TakePicture` (`webapi/camera/operation/oneshot/TakePicture.java`) is the operation. Its ordering:

1. **The event handler runs `startRecMode` first, when the camera offers it.**
   `GetEvent.returnCb` (`webapi/camera/event/GetEvent.java`) checks
   `mAvailableApiList.contains(EnumWebApi.startRecMode)` and, if so and it has not been called yet,
   executes `StartRecMode` **before** marking the session started.
2. **The shutter refuses to send itself unless the camera advertised it.**
   `TakePicture.canExecute()` returns `false` unless `mAvailableApiList.contains(actTakePicture)`.
3. **It then calls the method and reads the returned URLs.**
   `WebApiExecuter.mWebApiClient.get(EnumWebApiService.CAMERA).actTakePicture(callback)`; the
   callback takes the last element of the returned URL array as the photograph.
4. **A busy shutter is a signal, not a failure.** On error code `StillCaturingNotFinished` the
   callback checks whether `awaitTakePicture` is advertised and, if so, calls it instead of
   reporting the failure. A client that treats that code as an error will report a photograph that
   is merely not finished yet as lost.

This is precisely the sequence a hand-written client gets wrong. E0659 measured that a naive call
to `actTakePicture` on this body returns error `1`; §3 explains what that means for the ordering.

## 3. What this says about the 47-method list on this body

The app's step 1 is conditional on `startRecMode` being advertised. On the owner's unit that method
is **not** advertised and answers error `12` (E0659), and step 2's guard would therefore refuse the
shutter before sending it. So on this unit Sony's own client would also decline to shoot, and the
refusal is consistent rather than an artefact of this project's client.

Two readings follow and this record does not choose between them, because the deciding observation
was not made:

- the running application registers a deliberately narrow set, and no client reaches the shutter
  while it does; or
- the shutter is gated by state the list reflects, and the empty storage list recorded in E0659 is
  the gate.

E0659 §7 names the one experiment that separates them: a card inserted, then the same call.

## 4. Alternative explanations

- **"This APK is a repack."** Its certificate is Sony's own publishing identity `CN=Hiroyuki
  Tamaki`, the length matches the catalog byte for byte, and the MD5 matches the catalog value.
  A repack would not carry that certificate.
- **"The app targets a newer API version than this body speaks."** It does discover versions: the
  `scalar.webapi.service.camera.v1_0` packages it imports match the `1.0` this body answers, and
  the client reads the version rather than assuming it.
- **"A newer app would behave differently."** 7.6.0 is the version the public catalog serves as of
  this date; a newer build may add `awaitTakePicture` handling or a different order. This record is
  exact for 7.6.0.

## 4a. Sony's own error vocabulary, which replaces this project's earlier guess

The decompiled `EnumErrorCode` names every code the client knows. The two this project had been
describing loosely:

| code | Sony's name | meaning |
| --- | --- | --- |
| `1` | `Any` | an unclassified refusal; the name carries no diagnosis at all |
| `5` | `Illegal Request` | the request is not legal in the camera's current mode |
| `12` | `No Such Method` | the name does not exist in the running application |

This matters because E0659 originally read code `1` as "refused in its current state", which
implies a state that could change. Sony's own name says only "Any". The measured facts are
unaffected — the shutter is absent from the 47 and answers `1` — but the interpretation was
stronger than the evidence, and the error table in `tools/research/scalarwebapi.py` now carries
Sony's names rather than this project's paraphrase.

## 5. Confidence and limitations

- **Verified:** the package identity and its digest agreement; the four-step sequence and the busy
  recovery path, both read from the decompiled classes; the count of 312 enumerated names and 221
  camera methods; discovery through the published description URL.
- **Not verified:** that the app works against this body today — it was not installed or run, and
  E0659's camera session is the only live evidence. Nothing here is a claim about the app's
  behaviour on hardware.
- **Bounded:** one version of one app. Decompiler output is a representation, not original source;
  jadx reported 34 files with errors out of 5,919 units, and the classes cited here are not among
  the failures.

## 6. Dependent implementation

No behaviour changed in `tools/`. The sequence above is what `tools/research/camera_remote.py`
already obeys in the one way that matters offline: it reads the camera's advertised list and
refuses a command that is not in it, rather than sending and reporting a state refusal as a fault.
Record: `docs/evidence/cross_model/imaging-edge-mobile-control.json`. Tests:
`tests/test_imaging_edge_mobile_record.py`.

## 7. Next experiment

Install 7.6.0 on an Android phone, join the camera's access point, and record its traffic while
pressing the shutter in the app's remote-shooting screen. Two questions are settled by one capture:
whether the app sends anything this project's client does not, and what it does when the shutter
is refused. A passive capture on the owner's own device needs his approval and is not started here.
