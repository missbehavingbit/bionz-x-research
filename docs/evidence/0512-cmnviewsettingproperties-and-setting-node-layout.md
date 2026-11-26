# E0512 — Offline `CmnViewSettingNode` & `CmnViewSettingProperties` Binary Layout

Date: 2026-11-14. **Strictly offline.** No camera access, no USB, no writes to firmware or hardware.
Continuation of [E0511](0511-viewsettingmenu-uxc-and-node-architecture.md).

## 0. Short Answer

Disassembly of `CautionConfig.so` (`sha256: bfd1bd7bad0ab3478b894da5dbb51c15464b1bedc4201240ccb61cd743150ef7`) and `viewUnified2.so` (`sha256: 1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`) establishes the exact memory layout of menu nodes and their property tables:

1. **`CmnViewSettingNode` (base header, 16 bytes) [C]:**
   - `+0x00`: `vtable`
   - `+0x04`: `CmnViewSettingNode** children`
   - `+0x08`: `int child_count`
   - `+0x0c`: `CmnViewSettingProperties const* props`
2. **`CmnViewSettingProperties` (8 bytes) & `_settingProperty` (12 bytes) [C]:**
   - `CmnViewSettingProperties` stores `{ _settingProperty const* entries; int max_index; }` (`0x007c782c`).
   - `_settingProperty` stores `{ int prop_id; int type; uint32_t value; }` (`0x007c7836`, `0x007c786e`, `0x007c789e..0x007c791c`), sorted ascending by `prop_id` for binary search in `_getPropertyIndex`.
3. **Property Inventory (`1,548` arrays in `CautionConfig.so` `.rodata`) [C]:**
   - `type = 0`: Resource IDs (`0x02xxxxxx`, e.g., `prop_id = 0` main label/title ResID, `prop_id = 6` guide/help ResID).
   - `type = 1`: Integers/enums/flags (e.g., `prop_id = 11` node/display flag queried by `PAS_MenuListTab`, `prop_id = 14` `ProcId`, `prop_id = 15`/`16` `ProcValId`).

## 1. Verified Disassembly Observations (`CautionConfig.so`)

### 1.1 `CmnViewSettingNode::CmnViewSettingNode` (`0x007c7610`)

- Stores `r1` (`children`) at `[r0, #4]`, `r2` (`child_count`) at `[r0, #8]`, `r3` (`props`) at `[r0, #12]`, and installs the `CmnViewSettingNode` vtable at `[r0, #0]`. [C]
- `CmnViewSettingNode::getProperties` (`0x007c7634`) reads `[r0, #12]` (`props`) and returns `1` when non-null. [C]
- `CmnViewSettingNode::getResidProperty` (`0x007c74e2`), `getCharProperty` (`0x007c74f4`), `getBoolProperty` (`0x007c7506`), and `getIntProperty` (`0x007c7518`) each load `r0 = [r0, #12]` and tail-call the corresponding `CmnViewSettingProperties::get*Property` method. [C]

### 1.2 `CmnViewSettingProperties` & `_settingProperty` (`0x007c782c..0x007c791c`)

- `CmnViewSettingProperties::CmnViewSettingProperties(_settingProperty const*, int)` (`0x007c782c`): `stmia.w r0, {r1, r2}` (`+0x00 = entries`, `+0x04 = max_index`). [C]
- `CmnViewSettingProperties::_getPropertyIndex(int prop_id, int& out_index)` (`0x007c7836`): binary-searches `0..max_index` with stride `12` (`movs r6, #12; muls r6, r3; ldr.w r6, [ip, r6]`), comparing `prop_id` against `[entry + 0]`. [C]
- `CmnViewSettingProperties::getProperty(int prop_id, _settingProperty const** out)` (`0x007c786e`): returns `entries + 12 * index`. [C]
- Value readers (`0x007c789e..0x007c791c`):
  - `getResidProperty` / `getIntProperty`: `ldr r3, [r3, #8]` (32-bit value at `_settingProperty + 8`). [C]
  - `getCharProperty` / `getBoolProperty`: `ldrb r3, [r3, #8]` (8-bit value at `_settingProperty + 8`). [C]

## 2. `CautionConfig.so` `.rodata` Property Distribution (`1,548` Arrays)

Scanning `CautionConfig.so` `.rodata` for sorted 12-byte `_settingProperty` arrays starting at `prop_id = 0, 1` yields **1,548** arrays (sample `@ 0x0095e594`), with the following `(prop_id, type)` pairs:

| `prop_id` | Hex | `type` | Array count | Verified / Observed Role |
| ---: | --- | ---: | ---: | --- |
| `0` | `0x00` | `0` | 1,548 | Title / label ResID (`getGuideTitleText` `0x002fed78`, `PAS_MenuListTab` `0x005ab720`/`0x005abb84`) [C] |
| `1` | `0x01` | `0` (1 `type=2`) | 1,548 | Resource ID (every node has `prop_id=0` and `prop_id=1`) [C] |
| `2` | `0x02` | `0` | 606 | Resource ID [C] |
| `3` | `0x03` | `0` | 46 | Resource ID [C] |
| `4` | `0x04` | `0` | 377 | Resource ID [C] |
| `5` | `0x05` | `0` | 412 | Resource ID [C] |
| `6` | `0x06` | `0` | 1,509 | Guide / help text ResID (`getGuideText` `0x002fedde` -> `vtable+0x80(6, &out)`) [C] |
| `11` | `0x0b` | `1` | 1,533 | Integer property queried by `PAS_MenuListTab` (`0x005abb94` -> `vtable+0x5c(11, &out)`) [C] |
| `12` | `0x0c` | `1` | 1,487 | Integer property [C] |
| `14` | `0x0e` | `1` | 61 | `ProcId` (`CmnSettingNodeUtil::getProcIdForItem` `0x002fe9ea`) [C] |
| `15` | `0x0f` | `1` | 1,383 | `ProcValId` fallback (`CmnSettingNodeUtil::getProcValIdForItem` `0x002fea3a`) [C] |
| `16` | `0x10` | `1` | 1,493 | `ProcValId` primary (`CmnSettingNodeUtil::getProcValIdForItem` `0x002fea3a`) [C] |
| `17`–`21`, `24`, `28`, `32`, `36` | `0x11`–`0x24` | `1` | 2–1,384 | Integer/enum properties [C] |
| `25`, `26`, `34` | `0x19`, `0x1a`, `0x22` | `0` | 9–176 | Resource IDs [C] |

Note: `viewUnified2.so` `.rodata` contains **0** `_settingProperty` arrays; its static initializers (`0x00214000+`) reference `CmnViewSettingProperties` objects imported from `CautionConfig.so` via GOT relocations. [C]

## 3. Scope Boundary & Next Step

- **Boundary [I]:** `prop_id`s `2..5`, `12`, `17..36` are observed in `.rodata` and typed (`0` = ResID, `1` = int), but their individual semantic names require tracing their specific callers in `viewUnified2.so` / `CautionConfig.so`. Likewise, resolving `0x02xxxxxx` ResIDs to `string_english.uxc` / `image_*.uxc` entries requires decoding the `ux::resmgr::ResourceManager` lookup in `libObj.so`.
- **Next offline step [I]:** Map `ux::resmgr::ResourceManager` in `libObj.so` to resolve `0x02xxxxxx` ResIDs directly against `string_english.uxc` so any `CmnViewSettingNode` entry in `CautionConfig.so` can be decoded to human-readable menu labels offline.
