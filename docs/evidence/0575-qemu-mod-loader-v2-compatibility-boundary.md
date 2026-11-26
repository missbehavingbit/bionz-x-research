# E0575 — Mod Loader v2 target identity in QEMU

Date: 2026-11-17. Offline. No physical camera or SD access.

## Inputs and results

Re-ran the retained Mod Loader v2 and MenuTouchFull v2 harnesses using
the builds named in [the Mod Loader design](../MOD_LOADER_V2_DESIGN.md).
All ten loader scenarios and the touch hook checks at two payload bases
passed. These tests use original ARM mod code and modeled firmware
callees. They do not qualify live menu gestures in QEMU.

```powershell
.\venv\Scripts\python.exe -m tools.native_camera.mlv2.test_mlv2_unicorn artifacts/tests/mlv2-sd-build-3 artifacts/tests/arrivederci-v3-build-1/Arrivederci.mod artifacts/tests/mtf-v2-build-1
.\venv\Scripts\python.exe -m mods.menu_touch_full.test_mtf_v2_unicorn artifacts/tests/mtf-v2-build-1
```

The main firmware reached `SetContentTarget` in a fresh QEMU overlay
with `EXTRA=norandmaps`, `EVSEL_LATE_US=500` and default icount.
The guest reported `randomize_va_space=0`. The original executable
`/usr/bin/im.elf` was PID 144. PID 149 was `InfraOnDmdLdr`.

| Library | QEMU executable mapping start |
| --- | --- |
| `libObj.so` | `0x055d4000` |
| `libtestcmd.so` | `0x07dba000` |
| `viewUnified2.so` | `0x11cc8000` |

Each original guard hook byte sequence occurs once in the identity-matched
`viewUnified2.so` from E0571. ELF offsets `0x5ab654`, `0x5aba80` and
`0x5ac044` correspond to a physical-build load bias of `0x155e6000`.
With the observed QEMU load bias they resolve to `0x12273654`,
`0x12273a80` and `0x12274044`. No hooks were written. The physical
shim target `0x05662760` is outside the QEMU `libtestcmd.so` mapping.
Disabling address randomization did not make the guard's PID or addresses
match. SMOD v2 payload relocation and Sony target address qualification
are separate.

The guest's static `/dev/mmca` and `/dev/mmca1` nodes were present,
but its partition table listed only internal `nflasha` devices.
No removable controller was instantiated. An intermediate input fixture
added `MLV2.TAR` to the guest `/setting` FAT copy in qcow2, solely to
transport original guard and request bytes. The first check attempt
stopped at the read-only `/tmp_bt` directory before running the guard.
The next staging trial mounted a guest `tmpfs` at `/tmp_bt` and extracted
the archive, then stopped because this guest BusyBox lacked
`sha256sum`. The physical SD worker uses that applet.
The first map query also used an unsupported BusyBox grep option.
The next query used `pidof` and read the maps directly.
Another attempt ended at an incomplete shell line. The final replay
issued one guest sleep before the queries, used the temporary guest
`tmpfs` and ran the unchanged ARM guard with `check mlv2.req`.
The guard returned `MLV2_RESULT=-5 identity/maps` and exit 5 after
accepting the request format. The outer probe's exit 0 indicates that
its serial commands completed. The guard performed no memory writes.

Original guard SHA-256
`1d84f2535acc0c40eee11ae44446366a9bfb54418420e39b657fc86818402c9d`.
Request SHA-256
`fac109bb7bbd269c5ac552c973438ca6d0cb23269dccfb3c683c3e6cb200d49e`.
Receipts and the replay recipe are under
`artifacts/qemu-a6400/mod-compatibility/`.

Next: qualify the same-process mapping and shim offsets before making a
target-aware guard and payload build. See
[Developing camera mods in QEMU](../../qemu-a6400/MOD_DEVELOPMENT.md).
