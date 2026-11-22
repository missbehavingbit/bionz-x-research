# a6100 Picture Profiles — status

**Verdict: Picture Profile is present in every data layer; the firmware gates it at run
time via model data. The smallest honest unlock is the documented 2-byte menu-node
redirect, now implemented as an opt-in emulator overlay and verified byte-for-byte
against the existing validated patch.** Details:
[E9101](../docs/evidence/9101-a6100-picture-profile-gate.md).

## The gate

| Layer | Result |
| --- | --- |
| Menu text (`string_english.uxc`) | byte-identical to the a6400 file; PP1–PP10, S-Log2/3, S-Gamut3, HLG1–3, Cine1–4 |
| Node classes (`CautionConfig.so`) | 13 `cmnViewSettingNodeRootPictureProfile*` exports |
| Root registry | PP at index 251 of 416 (same index as a6400) |
| Menu trees (`viewUnified2.so`) | 21 PP references, 15 S-Log Display Assist (same counts) |
| Settings (`Backup.bin`, BK4) | 30,762 units, strict superset of the a6400 (0 missing) |
| Pipeline (`av-cam.bin`) | `SET_PICTUREPROFILE`, `CheckSetPictureProfile`, `PP_GammaType`, S-LOG2, ITU709 |

Nothing is missing in the data, so the gate is the runtime availability decision:
`CmnForUseMultipleViewStatus::setPictureProfileValidInfo(bool)` (`viewUnified2.so`
`0x00413940`), fed from the camera's backup/model data. Writing that model data needs
Sony service keys, which is a documented blocked route.

## The unlock (opt-in, default off)

The camera resolves the *Soft Skin* menu node by dynamic symbol, so pointing that symbol
at the Picture Profile node puts PP in that slot. Two bytes of `.dynsym` `st_value`,
no code change, no new menu:

```
0x138b4c   c8 b5 c5 00  (0xc5b5c8 SoftSkin)  ->  30 d5 c5 00  (0xc5d530 PP node)
```

`PP_UNLOCK=1` writes exactly those bytes into the run's **disposable qcow2 overlay**
(`qemu-a6400/run.sh`, same pattern as `REAR_LCD_DIAGNOSTIC`). The dump, `emmc.img` and
`/setting` are never written; a stock run is unaffected.

Proof:

- generated patch is **byte-identical** to the previously validated
  `CautionConfig.so.patched` (md5 `a91f2f28…`), only offsets `0x138b4c`/`0x138b4d` change;
- disk mapping round-trips (the four bytes at image offset 44,060,492 / eMMC 815,812,428
  equal the file's st_value bytes);
- unlock boot readback: `30 d5 c5 00` at 815,812,428 in the overlay;
- boot unchanged: 2,057 vs 2,058 normalized serial lines, the only difference is one log
  line carrying a different runtime address.

Not proven: the PP screen's own contents. The menu row is proven: with the redirect off
the same page 9/11 row reads `Soft Skin Effect  Off`; with `PP_UNLOCK=1` it reads
`Picture Profile  PP7`. Soft Skin is not reachable while the redirect is on.

## Provenance note

The dump's `/lib/CautionConfig.so` has a 2026-11-19 mtime (siblings are 2021-04-05), but
its bytes match the 2026-11-10 double-pulled stock backup (md5 `a19110bd…`). The file was
rewritten with stock content; the availability table is not a modified one.

## Files

- `tools/research/a6100_picture_profile.py` — audit + `unlock-offset` planner.
- `tests/test_a6100_picture_profile.py` — 18 cases (hash integrity, six layers, registry
  and menu indices, settings superset, provenance, unlock bytes/refusal/round-trip).
- `qemu-a6400/run.sh` — `PP_UNLOCK=1` opt-in.
- Evidence: `docs/evidence/9101-a6100-picture-profile-gate.md`.
