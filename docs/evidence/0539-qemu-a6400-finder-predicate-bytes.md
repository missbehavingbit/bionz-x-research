# E0539 — QEMU `sony-a6400`: the two finder predicates

Date: 2026-11-16. **Strictly offline.** Builds on E0538.

Labels: [C] confirmed by a binary, [I] inference, [A] assumption.

## 0. Short answer

`viewUnified2.so` defines both predicates. Each one reads two stored bytes. The viewfinder skin is used when the device byte is 1. The rear-LCD case is the other value of that byte [I]. No device was changed, and console 0 was not stretched.

## 1. Where the code is

`/usr` is the ext2 filesystem at eMMC offset `0x2e000000` (1 KiB blocks). The guest file sizes match these inodes [C]:

| File | Inode | Size | First block |
| --- | --- | --- | --- |
| `viewUnified2.so` | 14273 | 11530552 | 127602 |
| `viewUnified4.so` | 14245 | 2614628 | 122873 |
| `viewUnified7.so` | 14154 | 541024 | 66217 |

The copy of this filesystem image at `0x31800000` repeats the same inode numbers' block lists but does not hold these files. Reading `viewUnified2.so` through the `0x2e000000` block list produces an ELF whose dynamic symbols include [C]:

| Symbol | Value | Size |
| --- | --- | --- |
| `CmnViewFocus::isFinderManualAndDeviceEvf()` | `0x2d0845` | `0x18` |
| `CmnViewFocus::isFinderAutoAndDeviceEvf()` | `0x2d09e5` | `0x18` |

`viewUnified4.so` and `viewUnified7.so` import those names with value 0, as E0538 said [C].

## 2. What the predicates test

Both functions take the object in r0, call one reader, and return 0 when that reader returns 0. Otherwise they tail-call a shared reader [C].

The readers pass an id to one getter and test the byte it writes back [C]:

| Reader | Id | True when the byte is |
| --- | --- | --- |
| manual (`0x2d07e4`) | `0x01070795` | 1 |
| auto (`0x2d09b8`) | `0x01070795` | 0 |
| device (`0x2d0814`) | `0x010708e0` | 1 |

So `isFinderManualAndDeviceEvf` is true only when byte `0x01070795` is 1 and byte `0x010708e0` is 1. `isFinderAutoAndDeviceEvf` is true only when byte `0x01070795` is 0 and byte `0x010708e0` is 1 [C]. The same library names a menu item `MDT_0151_02_EVF_AND_LCD_FINDER_SWITCH` and `ResetBackup::resetBackupOutputDeviceInAuto` [C]. Those strings are not yet tied to these two ids.

## 3. Next boundary

Find where id `0x010708e0` is stored. The rear-LCD readout is the case where that byte is not 1. The live-view plane stays off until the imager starts (E0535).
