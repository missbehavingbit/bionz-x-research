# E0610 — Live ILCE-6400 `Arrivederci v4` A/B Verification (`C0029` ON vs `C0030` OFF)

- **Date:** 2026-11-19
- **Targets:**
  - `E:\PRIVATE\M4ROOT\CLIP\C0029.MP4` (`Arrivederci v4 ON`: `arrivederci on=1 gamma=w color=a sabin=m`)
  - `E:\PRIVATE\M4ROOT\CLIP\C0030.MP4` (`Arrivederci v4 OFF`: `arrivederci on=0 gamma=w color=w sabin=u`)
- **Preserved artifacts:**
  - `artifacts/analysis/arri-sdk-pipeline-e0610/C0029_mid.png` (`3840x2160`, `4.25 s`)
  - `artifacts/analysis/arri-sdk-pipeline-e0610/C0030_mid.png` (`3840x2160`, `1.75 s`)
  - `artifacts/analysis/arri-sdk-pipeline-e0610/zebra_ab_comparison.png`

---

## 1. Matched-Space A/B Comparison (`C0030` transformed via `classic_rec709`)

Transforming `C0030` (`S-Log3 / S-Gamut3.Cine`) through `a6400-lut-converter`'s `slog3_decode` -> `SONY_TO_AWG3` -> `logc3_encode` -> `classic_rec709` places both crops in the same ARRI Classic Rec.709 color and contrast space over the motherboard diagonal zebra stripes:

| Sample | `luma_lap_p99` | `chroma_lap_p99` |
| --- | ---: | ---: |
| `C0029` (`Arrivederci v4 ON` in-camera) | `0.3930` (**-39.1%**) | `0.0745` (**-26.5%**) |
| `C0030` (`OFF` + ARRI Classic Rec.709) | `0.6452` | `0.1014` |

Visually (`zebra_ab_comparison.png`), `C0029` removes the grainy edge ringing and chroma edge noise on the diagonal PCB stripes and SMD labels (`UAF5`, `FU3UD6`, `nuvoTon`) while preserving fine detail and smooth highlight roll-off.

---

## 2. Future Work Note

- **Test `spica_musashi_mov_4k_y_tap_step1/2.bin` at `0.00x` (instead of `0.15x`) paired with `4–5%` symmetric `dcv_psf`:** evaluate whether zeroing the remaining 6K->4K luma delta peaking yields an even cleaner downsampled edge texture without losing micro-detail.
