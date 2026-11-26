# E0513 — Offline `uxc\x07` Container Format & `CmnViewSettingProperties` String Resolution

Date: 2026-11-14. **Strictly offline.** No camera access, no USB, no writes to firmware or hardware.
Continuation of [E0511](0511-viewsettingmenu-uxc-and-node-architecture.md) and [E0512](0512-cmnviewsettingproperties-and-setting-node-layout.md).

## 0. Short Answer

Disassembly of the `uxc\x07` parser in `libObj.so` (`0x0079d4b4..0x0079de00`) decodes the binary `.uxc` container format and connects `CautionConfig.so` `CmnViewSettingProperties` resource IDs directly to `share/app/string_english.uxc`:

1. **`uxc\x07` Container Header & Offset Table (`libObj.so` `0x0079d4b4..0x0079d5c2`) [C]:**
   - File bytes `0x00..0x03` = `"uxc\x07"` (`0x07637875`, checked at `0x0079dc76`).
   - Body pointer `r0 = file + 8` (`0x0079ddde`):
     - `header_size = u8[0] & 0x7c` (`0x0079d4c6`)
     - `table_mode = u8[0] & 0x03` (`0x0079d4d2`: `0` = 16-bit `<< 2`, `1` = 32-bit, `3` = mixed 16/32-bit with `0x8000` extension and threshold `u16[4]` at `r0 + 8`)
     - `data_offset = (u32[1] >> 11) & 0x7ffc` (`0x0079d4e6`)
     - `entry_count = u32[1] & 0x1fff` (`0x0079d4f6`)
     - `table_ptr = r0 + header_size`; `data_base = table_ptr + data_offset`
2. **String Table Decoding (`string_english.uxc`) [C]:**
   - `string_english.uxc` (`375,160 B`, `sha256: b62c58521eca7b0443c5e570aaf8554ae5b05d94f46940dce7d9bc01a94b2921`) has `hdr_sz = 12`, `mode = 3`, `count = 7,778`, `data_off = 0x6378` (`data_base = 0x638c`), `thresh = 2,825`, yielding **5,742** valid entries (**5,318** unique lower-24-bit IDs).
   - Each string record begins with a 4-byte `uint32` ID (`0x1e04xxxx`) followed by a null-terminated UTF-8 string.
3. **End-to-End Resolution (`CautionConfig.so` -> `string_english.uxc`) [C]:**
   - Masking `CautionConfig.so` `type = 0` ResIDs (`0x0204xxxx`) to their lower 24 bits (`rid & 0x00ffffff`) matches `string_english.uxc` records (`0x1e04xxxx`) 1:1.
   - Out of **1,548** `_settingProperty` arrays in `CautionConfig.so` `.rodata`, **1,365** `prop_id = 0` labels resolve directly to English menu strings (e.g., `0x0204f096` -> `"M: 9.0M"` with `prop_id = 6` `0x0204e1e1` -> `"Shoots at M (medium) size."`; `"ISO AUTO"`, `"Manual Exposure"`, `"Extra fine"`).

## 1. Verified Inputs & Hashes

| File (under `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/`) | Size (bytes) | SHA-256 | Role |
| --- | ---: | --- | --- |
| `lib/libObj.so` | 20,860,436 | `60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1` | `.uxc` parser (`0x0079d4b4..0x0079de00`) & `ux::resmgr::ResourceManager` (E0483) [C] |
| `lib/CautionConfig.so` | 12,070,800 | `bfd1bd7bad0ab3478b894da5dbb51c15464b1bedc4201240ccb61cd743150ef7` | `_settingProperty` arrays (E0512) [C] |
| `share/app/string_english.uxc` | 375,160 | `b62c58521eca7b0443c5e570aaf8554ae5b05d94f46940dce7d9bc01a94b2921` | English UTF-8 string table (`uxc\x07`, `type=0x000f`, `id=0x0027`) [C] |

## 2. Verified `libObj.so` Disassembly Observations

- `0x005ea0dc` (`ResourceManager::setDatabase`) and `0x005ea334` (`ResourceManager::syncPrefetchTextImage`): thin wrappers delegating to `libra`/`alcor` functions at `0x0079ec10+` and `0x007a1a68+`. [C]
- `0x0079dc76` / `0x0079dc90`: compares `[r4, #0]` against `0x07637875` (`"uxc\x07"`). [C]
- `0x0079dd38`: checks `ldrb r3, [r0, #2]` against `0x63` (`'c'`) / `0x64` (`'d'`), then sets payload pointer `r6 = r0 + 8` (`0x0079ddde`). [C]
- `0x0079d526` / `0x0079d5a4`: computes `data_base + offset(index)` across modes `0`, `1`, and `3`, returning `0` when `0xffff` or `index >= entry_count`. [C]

## 3. Scope Boundary & Next Step

- **Boundary [I]:** `0x0205xxxx` ResIDs (`prop_id = 1..5`) do not appear in `string_english.uxc` and correspond to image/icon resources (e.g., `image_*.uxc`), whose internal sprite/bitmap payload format has not yet been decoded. The remaining `183` (`1548 - 1365`) `prop_id = 0` entries either use non-string ResIDs or reside in another resource namespace.
- **Next offline step [I]:** Walk the static `CmnViewSettingNode` constructor calls in `viewUnified2.so` (`0x00214000+`) and `CautionConfig.so` to dump the full hierarchical menu tree (Tab -> Page -> Item -> Option) with resolved English/Croatian labels and `ProcId`/`ProcValId` values.
