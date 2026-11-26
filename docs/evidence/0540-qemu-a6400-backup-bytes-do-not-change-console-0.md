# E0540 — QEMU `sony-a6400`: the two backup bytes do not change console 0

Date: 2026-11-16. **Strictly offline.** Builds on E0539.

Labels: [C] confirmed by a run receipt, [I] inference, [A] assumption.

## 0. Short answer

Changing the two bytes from E0539 does not change graphic console 0. Both boots drew the same viewfinder frame as E0531. The rear-LCD image set is chosen somewhere else.

## 1. What was measured

`Backup.bin` from `qemu-a6400/data/setting.tar` is a BK4 preset (691 subsystems). The userspace ids are kernel unit ids `(subsystem << 16) | property` [C]:

| Unit | File offset | Shipped byte | Size |
| --- | --- | --- | --- |
| `0x01070795` | `0xB96BE` (reset copy `0xB96BF`) | 0 | 1 |
| `0x010708e0` | `0xBA53A` (reset copy `0xBA53B`) | 2 | 1 |

Each run extracted that file, changed the named bytes, recomputed the BK4 checksum, and loaded it at the header PA `0x02E80000`. Probe: `tools/a6400_probe.py --until SetContentTarget`, then `usleep 3000000` and `screendump`. Default `-icount shift=0,sleep=on`.

| Run | Edit | Byte at the dump | Checkpoint | Frame SHA-256 |
| --- | --- | --- | --- | --- |
| e0548 | device live and reset set to 0 | live 1, reset 0 | 46.2 s, status 0 | `deec46fe3f50268e26c66ae6293e6722e840f9501fcc3cc9a5cabcc8471fe341` |
| e0549 | finder live and reset set to 2 | live 2, reset 2 | status 0 | the same digest |

That digest is the viewfinder frame from E0531 [C]. `viewUnified2.so` `0x41596c` calls `ResetBackup::resetBackupOutputDeviceInAuto` and then `BackupManager::Bkup_Write` of unit `0x010708e0`. A stored 0 is replaced by 1 before the dump. A stored 2 is left alone [C].

## 2. What this does not show

The predicates in E0539 still test these bytes. This frame does not. No model change, and the 853-wide stretch was not applied.

## 3. Next boundary

The code that opens `image_DSC_Only_169.uxc` instead of `image_EVF.uxc`.
