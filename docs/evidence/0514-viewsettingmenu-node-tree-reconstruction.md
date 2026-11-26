# E0514 — Offline `ViewSettingMenu` Hierarchical Node Tree Reconstruction

Date: 2026-11-14. **Strictly offline.** No camera access, no USB, no writes to firmware or hardware.
Continuation of [E0511](0511-viewsettingmenu-uxc-and-node-architecture.md)–[E0513](0513-uxc-container-and-string-resource-resolution.md).

## 0. Short Answer

Static parsing of the `ViewSettingMenu` initializer (`0x00213b8c..0x00214810`) in `viewUnified2.so` (`sha256: 1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`) reconstructs **85** `CmnViewSettingNode` container instances (tabs, pages, and sub-groups) and their exact child arrays, linking directly to the **415** `cmnViewSettingNodeRoot*` item symbols exported by `CautionConfig.so` (`sha256: bfd1bd7bad0ab3478b894da5dbb51c15464b1bedc4201240ccb61cd743150ef7`).

## 1. Reconstructed Tree Structure (`artifacts/analysis/e0514_viewsettingmenu_nodes.json`)

- **Initializer bounds [C]:** `0x00213b8c..0x00214810` in `viewUnified2.so` (GOT base `r5 = 0x009446a0` loaded at `0x00213b9c`).
- **Extracted nodes [C]:** **85** `CmnViewSettingNode` instances, each constructed with `(node_bss_addr, children_arr_addr, child_count, got_slot_props)`.
- **Container roots within `0x00213b8c..0x00214810` [C]:** **14** nodes not referenced as children inside the same block:
  - **Setup Tab Pages (`0x00b11c54..0x00b11d1c`):** 6 pages (`6, 6, 6, 5, 6, 4` items), e.g., `SetupLCDBrightness`, `SetupViewFinderBright`, `SetupEvfAdjustment`, `SetupVolumeSettings`, `ShowTopMenu`, `SetupModeDialGuide`, `SetupLanguage`, `ClockDateTimeSetup`, `MemoryCardFormat`, `SetupVersion`, `CertificationLogo`, `Initialize`. [C]
  - **Playback Tab Pages (`0x00b196e0`, `0x00b19708`):** 2 pages (`6, 4` items), e.g., `PlaybackDelete`, `PlaybackViewModeSelect`, `PlaybackImageIndex`, `PlaybackDisplay`, `EnlargeImage`, `Output4kMode`, `PlaybackProtect`, `PlaybackSpecifyPrinting`. [C]
  - **Application / Help (`0x00b0fcc4`):** 2 items (`cmnViewSettingNodeRootApplicationList`, `cmnViewSettingNodeRootIntroduction`). [C]
  - **Camera Settings Pages (e.g., `0x00b0d768..0x00b0d858`, `0x00b17174..0x00b171c4`):** e.g., `StillImageSize`, `StlImageAspectRatio`, `StlImageQuality`, `MovieFileFormat`, `MovieRecordingSetting`, `FlashMode`, `DriveMode`, `FocusMode`, `PhaseDetectionAF`, `ISOSensitivity`, `MeteringMode`, `WhiteBalance`, `CustomGridLine`, `CustomAutoReview`, `DispButton`, `AelWShutter`. [C]
  - **Higher-level Tab/Root Containers (`0x00b13ae4` [8], `0x00b13b0c` [7], `0x00b1354c` [5], `0x00b188f0` [4], `0x00b162f4` [2]):** reference page/tab nodes in `0x00b0xxxx..0x00b1xxxx`. [C]

## 2. Verification & Boundaries

- **Retained outputs [C]:**
  - Disassembly slice: `artifacts/analysis/e0514_init_dis.txt`
  - Reconstructed node graph: `artifacts/analysis/e0514_viewsettingmenu_nodes.json` (`85` nodes)
- **Boundary [I]:**
  - `got_slot_props` values in `0x00213b8c..0x00214810` point into `.bss` (`0x00b08858+`, `R_ARM_RELATIVE`), where `CmnViewSettingProperties` instances are populated by `viewUnified2.so` static constructors prior to `CmnViewSettingNodeC1`; they were not dereferenced statically in this pass.
  - Additional `CmnViewSettingNode` container nodes (e.g., `0x00b0c490`, `0x00b0d198`) are constructed in sibling initializer blocks outside `0x00213b8c..0x00214810`.
- **Customization takeaway [I]:**
  - Reordering, hiding, or adding tabs/pages/items in `ViewSettingMenu` is governed by the `children[]` pointer arrays and `child_count` arguments in `viewUnified2.so` (`0x00213b8c..0x00214810` and sibling initializers), plus the 75-case mode selector (`0x00208648`), while item labels, icons, `ProcId`/`ProcValId`, and greyout rules are defined in `CautionConfig.so` (`cmnViewSettingNodeRoot*`) and `/usr/share/app/*.uxc`.
