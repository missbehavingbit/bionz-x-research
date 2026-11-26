# E0542 — QEMU `sony-a6400`: monitor layouts sit beside the EVF layouts

Date: 2026-11-16. **Strictly offline.** Builds on E0541. No new boot.

Labels: [C] confirmed by disassembly of `viewUnified2.so`, [I] inference.

## 0. Short answer

The still-shooting screen has a monitor layout class next to each EVF layout class. `isOutputEvf` moves coordinates inside a layout. It does not choose which of those classes is built.

## 1. What was measured

`viewUnified2.so` (11,530,552 bytes, inode 14273 of the `/usr` filesystem) contains these type names, 0xB0 bytes apart in one table [C]:

| File offset of the name pointer | Type |
| --- | --- |
| `0x8d6534` | `LayoutST_NORMAL_EVF_GRAPH_LR` |
| `0x8d65e4` | `LayoutST_NORMAL_EVF_HISTOGRAM_LR` |
| `0x8d6694` | `LayoutST_NORMAL_EVF_INFO_OFF_LR` |
| `0x8d6744` | `LayoutST_NORMAL_EVF_LEVEL_LR` |
| `0x8d67f4` | `LayoutST_NORMAL_EVF_WITH_INFO_LR` |
| `0x8d68a4` | `LayoutST_NORMAL_GRAPH_LR` |
| `0x8d6954` | `LayoutST_NORMAL_HISTOGRAM_LR` |
| `0x8d6a04` | `LayoutST_NORMAL_INFO_OFF_LR` |
| `0x8d6ab4` | `LayoutST_NORMAL_LEVEL_LR` |
| `0x8d6c14` | `LayoutST_NORMAL_WITH_INFO_LR` |

`CmnViewFocus::isOutputEvf` is reached through the PLT stub at `0x150bc0`. One caller, `0x60f5c4`, adds `0x3c` to a coordinate when the predicate is true and adds `0x1a` plus `0x23` when it is false [C]. That is the same class of change as the 765 focus-mark pixels in E0541 [I].

`ResourceManager::setDatabase` has one caller in this library, at `0x3b4d5c`. The pointer it builds lands on the trailing space of `cannot find key: `, immediately before `global.xdb` [C]. It is not a call with `image_EVF.uxc` or `image_DSC_Only_169.uxc`.

## 2. What this does not show

No boot, no model change, and no new console dump. The vtable slots next to these names (`0x16ea45` for the EVF info-off layout and `0x16f04d` for the monitor info-off layout) have no direct `BL` in this library.

## 3. Next boundary

The code that constructs `LayoutST_NORMAL_WITH_INFO_LR` or `LayoutST_NORMAL_INFO_OFF_LR` instead of the matching `LayoutST_NORMAL_EVF_*` class.
