# E9101 — Where Picture Profile is gated on an ILCE-6100 (a6100), and the opt-in unlock

- **Date:** 2026-11-21 (supersedes the same-day first pass, see §8)
- **Scope:** offline, read-only analysis of one captured a6100 partition dump, one
  comparison against the vendor ILCE-6100A package and an a6400 package, and two local
  QEMU boots (stock and unlocked). No camera, USB, PTP/UVC, `/dev/mem`, service mode, SD
  or flash access. No firmware bytes are published; only names, sizes, hashes, addresses
  and instruction encodings.
- **Declared stimulus:** none. The unlocked boot is an explicit opt-in
  (`PP_UNLOCK=1`) that writes four bytes into the run's disposable qcow2 overlay.
- **Tool:** [tools/research/a6100_picture_profile.py](../../tools/research/a6100_picture_profile.py)
  (`report`, `unlock-offset`); tests
  [tests/test_a6100_picture_profile.py](../../tests/test_a6100_picture_profile.py).

## 1. Inputs

Dump: `a6100-recovery/local-only/dumps/20261120-130512` (read-only; never modified).
`sha256sum -c SHA256SUMS`: **19/19 OK**. No mismatch; no stored hash was refreshed.

| Role | Dump file | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Main firmware FAT (kernel, initrd, av-cam) | `nflasha3.img` | 50,331,648 | `08379a20…a269d8a1df` (stored) |
| Setting FAT (`Backup.bin`, `kemco.txt`) | `nflasha2.img` | 20,971,520 | `c3c5c592…ba64d5b9cb` (stored) |
| Root filesystem (ext2, the camera application) | `nflasha15.img` | 314,572,800 | `63d8f460…b0b1de` (stored, verified) |

Carved for analysis (kept only under the gitignored `artifacts/`):

| Input | Bytes | SHA-256 / MD5 |
| --- | ---: | --- |
| `av-cam.bin` | 16,803,048 | `58b2118d…6ff9e` |
| `Backup.bin` (BK4 preset) | 1,171,224 | `3b4b6664…ae3a590` |
| `bin/im.elf` | 21,820 | `ab7eed01…de8d93e` |
| `lib/viewUnified2.so` | 11,535,076 | `7bb0f245…39c28bce` |
| `lib/CautionConfig.so` | 12,078,652 | `a0ed06e7…5fde166` / md5 `a19110bd3d3dc7c45b4437a0cbba4839` |
| `share/app/string_english.uxc` | 375,160 | `b62c5852…1a94b2921` |

Offline references used for the differential (all local, none from the research loop):

| Reference | Identity | What it establishes |
| --- | --- | --- |
| Stock `CautionConfig.so.bak` / `.bak2` (pulled 2026-11-10, double pull) | md5 `a19110bd…` | the dump's file is **byte-identical to the stock pull** |
| `CautionConfig.so.patched` (validated offline 2026-11-10) | md5 `a91f2f28…`, 2 bytes differ | reference for the unlock |
| ILCE-6100A v1.01 package rootfs | `CautionConfig.so` `18695a08…`, 12,078,712 B | vendor build, 60 B larger, same node set |
| a6400 v2.00 package rootfs | `viewUnified2.so` `1e2867b6…`, `CautionConfig.so` `bfd1bd7b…` | matches E0515; used as the model differential |

## 2. The gate

Two separable facts, both established here:

1. **The data is complete.** On this image Picture Profile is not hidden by removing
   anything: the shared string resource, the setting-node classes, the model's root
   registry, the menu-tree references, the setting units and the ISP command handler are
   all present (§3). So the "UI entry" is not compiled out and the pipeline is not
   missing.
2. **The firmware decides availability at run time, from model data.** The UI sets a
   one-byte flag with `CmnForUseMultipleViewStatus::setPictureProfileValidInfo(bool)`
   (`viewUnified2.so` `0x00413940`, store `0x0041394C`), computed from
   `CmnViewModelIfWrapper::getModelInfo` over backup/model data, not from a compile-time
   constant. The earlier offline study of this camera recorded that chain in
   `a6100-fw-recovery/docs/pp-gate-findings.md` (2026-11-07); this record confirms the
   flag exists in this build and that the item data it would gate is present.

That is why the smallest honest unlock is **not** a settings write: the model-data value
that selects "PP invalid" lives in the backup/EEPROM area and needs Sony service keys to
write (documented blocked route). The mechanism that works without touching model data is
the menu-node redirect below.

## 3. Layer-by-layer result

| Layer | Evidence | a6100 | a6400 |
| --- | --- | --- | --- |
| Strings | `string_english.uxc` has `Picture Profile`, `PP1`–`PP10`, `S-Log2/3`, `S-Gamut3`, `HLG1`–`HLG3`, `Cine1`–`Cine4`, `ITU709` | present; **byte-identical** file | present |
| Node objects | `CautionConfig.so` `.dynsym` | 13 `cmnViewSettingNodeRootPictureProfile*` exports | same |
| Root registry | REL `R_ARM_ABS32` array ending `.data` | 416 entries, PP at **index 251**, 13 PP entries | 416, index 251 |
| Menu tree | `viewUnified2.so` relocations | **21** PP references, **15** S-Log Display Assist | 21 / 15 |
| Settings | BK4 unit table | 30,762 units | 30,746 |
| Pipeline | `av-cam.bin` | `SET_PICTUREPROFILE[%d…`, `CheckSetPictureProfile`, `PP_GammaType`, `PP_ColorMode`, `S-LOG2`, `ITU709` | present |

Settings differential: a6400 ⊆ a6100, **0 missing**, 16 units only in the a6100
(subsystems `0x0092`, `0x00f9`, `0x010d`).

## 4. Provenance: mtime differs, content is stock

`debugfs stat /lib/CautionConfig.so` on the dumped rootfs reports
`mtime Thu Nov 19 19:55:03 2026`, mode `0644`, while sibling app files are
`2021-04-05`, mode `0555`; the dump is from 2026-11-20. The **bytes**, however, are
identical to the double-pulled stock backup (md5 `a19110bd…`), and the same md5 was
recorded on 2026-11-10 before the dump. So the file was rewritten in place on 2026-11-19
with the stock content (a copy-back), and the availability table is not a modified one.

The tool encodes this: it flags the late mtime and, when the content matches a known
stock pull, says so explicitly instead of leaving the alarm open. Nothing was rewritten
and no hash was refreshed.

## 5. The opt-in unlock

**Mechanism.** The camera resolves the *Soft Skin* menu node through the dynamic symbol
`cmnViewSettingNodeRootSoftSkin` (imported by `viewUnified2.so`, `viewUnified4.so`,
`viewUnified7.so`). Pointing that symbol's `st_value` at the Picture Profile node makes
every importer bind the PP node in the Soft Skin slot. It is a symbol redirect, not a new
menu and not a code patch.

**Plan for this build** (`unlock-offset`):

```
patch_offset        1280844  (0x138b4c, .dynsym entry 49587, st_value field)
old_st_value        0xc5b5c8  cmnViewSettingNodeRootSoftSkin
new_st_value        0xc5d530  cmnViewSettingNodeRootPictureProfile
changed bytes       30 d5      (two of the four st_value bytes)
block_size          1024      ext2 block size of nflasha15
image_offset        44060492  inside the rootfs partition
source_offset       815812428 inside emmc.img (partition at 0x2e000000)
```

**Verification.** Patching the stock file with this tool reproduces the reference
offline-validated patch **byte for byte** (`apply_unlock(stock) == CautionConfig.so.patched`,
md5 `a91f2f28…`), changing only `0x138b4c`/`0x138b4d`. The disk mapping round-trips: the
four bytes read at `image_offset` from the rootfs image equal the file bytes at
`patch_offset`.

**Emulator opt-in** (`qemu-a6400/run.sh`): `PP_UNLOCK=1` (default off) calls
`unlock-offset` against the prepared `emmc.img` and writes the four bytes into the run's
disposable qcow2 overlay, exactly like the existing `REAR_LCD_DIAGNOSTIC` diagnostic.
The dump, `emmc.img` and `/setting` are never written.

Run receipt (`RUN_DIR=artifacts/a6100-pp/runs/boot2`, 60 s, same options as the stock
run):

```
pp-unlock.json: old_st_value 0xc5b5c8 -> new_st_value 0xc5d530
qemu-io readback at 815812428: 30 d5 c5 00      <- the guest reads the redirected symbol
boot console tail: "[VPH] YUV layer still off at vsync 600 en144=0x3000232"
```

Boot comparison against the stock run (`runs/boot1`): 2,057 vs 2,058 normalized serial
lines; the only line that differs is the same `Replaced log-hook` message with a different
runtime address. No boot-path change, no new fault.

**What this does and does not prove.** It proves the guest's `CautionConfig.so` now
resolves the Soft Skin node to the Picture Profile node, that the patch is the same one
already validated for this build, and that the rest of the boot is unchanged. It also
proves the effect on the drawn menu, with an A/B pair captured from two QEMU instances of
the same prepared inputs:

| Run | Camera Settings 1, page 9/11 (`Color/WB/Img. Processing`) |
| --- | --- |
| `PP_UNLOCK` unset (stock) | `White Balance`, `Priority Set in AWB`, `DRO/Auto HDR`, `Creative Style`, `Picture Effect`, **`Soft Skin Effect  Off`** |
| `PP_UNLOCK=1` | same page, same row: **`Picture Profile  PP7`** |

The row is the Soft Skin slot; the redirect turns it into the Picture Profile item, which
opens with the current profile value from the backup data. This is the first on-screen
confirmation in this record, and it also corrects the first pass's assumption that the
a6100's own tree already carried PP: that tree has the Soft Skin slot, and the 21 PP menu
references live in the other model trees inside the same `viewUnified2.so`.

Side effect, stated plainly: Soft Skin Effect is not reachable while the redirect is on.
Toggle `PP_UNLOCK` off for a stock camera.

## 6. Reproducing

```bash
# audit (hashes, six layers, provenance, unlock plan)
python3 -m tools.research.a6100_picture_profile report \
    --dump <dump> --rootfs-image <nflasha15.img> --defs-dir <extracted rootfs> \
    --a6400-backup <a6400 Backup.bin> --json report.json

# opt-in unlock boot
A6400_QEMU_DIR=$HOME/sony-a6400-qemu-mt A6400_QEMU_DATA_DIR=$PWD/artifacts/a6100-inputs \
RUN_DIR=$PWD/artifacts/a6100-pp/runs/boot2 TIMEOUT=60 ULOGIO= PP_UNLOCK=1 \
SERIAL=file:$PWD/artifacts/a6100-pp/runs/boot2-serial.log nice -n 10 bash ./qemu-a6400/run.sh

# tests
A6100_DUMP=<dump> python3 -m unittest tests.test_a6100_picture_profile
```

## 7. Failures, corrections and the boundary

- A first pass counted PP relocations through a shell pattern that PowerShell had mangled
  and reported **0** ("PP compiled out"). Re-parsing the ELF in-process gave 21, the same
  as the a6400; that correction is what §3 uses. Shell quoting is not a disassembler.
- A first provenance pass called the dump's `CautionConfig.so` "not stock" from its mtime
  alone. The 2026-11-10 double-pulled backup has the same md5, so the content is stock;
  §4 states both facts.
- The validity byte's object address is still unresolved (the setter loads it through a
  GOT slot); the unlock does not touch it.
- **Boundary:** the emulator reaches the shooting screen and the menu under the interactive
  rear panel with a 60 s+ wall-clock budget (icount makes it ~5x slower than real time),
  and the A/B pair in §5 was captured there. What is still not established: the PP screen's
  own contents (gamma/colour rows), and any behaviour on a physical camera. The camera was
  not written to.

## 8. Assumptions

- `nflasha15.img` is the root filesystem (ext2 superblock, `debugfs` lists `bin/`,
  `lib/`, `share/app/`); `nflasha3`/`nflasha2` are the FAT images the boot-preparation
  tool already parses.
- The community mechanism (Soft Skin → PP redirect) is the accepted unlock for this
  model; this record verifies the mechanism and its exact bytes, not the resulting
  on-screen behaviour.
- The a6400 references are the same files E0515 analysed (hashes match).
