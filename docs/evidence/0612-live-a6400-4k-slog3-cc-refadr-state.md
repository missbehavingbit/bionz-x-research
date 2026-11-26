# E0612 — Live ILCE-6400 4K Movie S-Log3 Read-Only Check of `CC:` `RefAdr` Buffers

- **Date:** 2026-11-19
- **Target:** Live ILCE-6400 in 4K Movie S-Log3 mode via `tools/camera_debugger.py` (system Python)
- **Preserved artifacts:**
  - `artifacts/analysis/arri-sdk-pipeline-e0612/e0612_page_04490000.bin` (4 KiB page at `PA 0x04490000`)
  - `artifacts/analysis/arri-sdk-pipeline-e0612/e0612_page_04491000.bin` (4 KiB page at `PA 0x04491000`)
- **Safety & responsiveness receipts:**
  - `/proc/uptime`: `86.94` → `89.30` (monotonic, camera responsive)
  - `/tmp_bt`: temporary page dumps `e0612_r0.bin`, `e0612_r1.bin` removed after `read`; `/tmp_bt` verified clean.

---

## 1. Observations (4K Movie S-Log3 Mode)

1. `RefAdr0` (`PA 0x04490025`, `1215` B): **`nonzero=0`** (all zeros).
2. `RefAdr1` (`PA 0x04490659`, `1215` B): **`nonzero=0`** (all zeros).
3. `RefAdr2` (`PA 0x04490cf9`, `775` B to page end): **`nonzero=0`** (all zeros).
4. `2DLUT` (`PA 0x04491461`, `162` B): **`nonzero=0`** (all zeros).

---

## 2. Conclusion & Next Boundary

In live 4K Movie S-Log3 mode, the static `.bss` `RefAdr` / `2DLUT` staging buffers (`PA 0x04490025..0x04491461`) remain zero-filled, confirming that Sony's S-Log3 Movie preset bypasses the `CC:` 3D LUT (`LutMode = 0xFF`). Enabling `5×9×9` 3D LUT in S-Log3 (`Arrivederci v5`) therefore requires **Varijanta B**: a small RAM hook at `FUN_63ad0fc8` / `FUN_63ad0930` (or patching the active `CC` tuning table's `LutMode` + `RefAdr` pointer) rather than a passive `RefAdr` data swap alone.
