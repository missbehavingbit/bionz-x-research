# E0538 — QEMU `sony-a6400`: the view asks two finder predicates

Date: 2026-11-16. **Strictly offline.** Builds on E0537.

Labels: [C] confirmed by a binary, [I] inference, [A] assumption.

## 0. Short answer

`viewUnified4.so` imports `CmnViewFocus::isFinderManualAndDeviceEvf()` and `CmnViewFocus::isFinderAutoAndDeviceEvf()`. Both symbols are undefined in that file (value 0) [C]. Those are the two cases that keep the viewfinder skin. The monitor skin is the remaining case [I]. No device was changed.

## 1. What was measured

`viewUnified4.so` is the ELF at eMMC offset `0x357fe400`. Its dynamic symbol table, in the contiguous prefix of that file, has:

| Symbol | Value | Section |
| --- | --- | --- |
| `_ZN12CmnViewFocus26isFinderManualAndDeviceEvfEv` | 0 | undefined |
| `_ZN12CmnViewFocus24isFinderAutoAndDeviceEvfEv` | 0 | undefined |

The same two strings occur once more, in `viewUnified8.so` at eMMC `0x35c57800`. No defined (non-zero) symbol for either name is present in the contiguous prefix of that file [C]. The whole eMMC image contains the mangled name in only those two places [C].

`CmnViewFocus` strings are concentrated at three offsets: `0x320` MiB (`viewUnified7.so`, including `getOutputTypeInfo`), `0x358` MiB (`viewUnified4.so`), and `0x35c` MiB (`viewUnified8.so`) [C].

The path table that names both skins is data, not one of these libraries. At eMMC `0x39ffd265` and `0x39ffd4ed` the records are `../../../usr/share/app/image_DSC_Only_169.uxc` and `../../../usr/share/app/image_EVF.uxc` [C].

## 2. What this does not show

The bodies of the two predicates are not in the contiguous prefix, so the property they read is not yet known. The ext4 inode for `/usr/lib/viewUnified7.so` was not extracted: past the first load, the raw image at the ELF's own file offsets is not that file.

## 3. Next boundary

Read `CmnViewFocus::isFinderManualAndDeviceEvf` from an intact copy of the library that defines it, and see which word it treats as the monitor.
