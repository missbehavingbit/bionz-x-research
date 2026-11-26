# E0627 — ZIMA_AVC 8-bit 4:2:0 ceiling in sabin/ encoders on ILCE-6400

Date: 2026-11-19. Offline binary analysis only. No camera, SD, or USB access.

## Inputs

- `sabin/sa_avce_quality_zma.bin` (`231,044` B, tag `ZM_AVCEQ0x00`)
- `sabin/sa_avce_performance_zma.bin` (`227,332` B, tag `ZM_AVCEP0x00`)
- `sabin/sa_avce_performance_plus_zma.bin` (`228,228` B, tag `ZMAVCEPP0x00`)

## Summary

Scanned all three `ZIMA_AVC` DSP encoder binaries for H.264 `profile_idc` constants (`Baseline=66`, `Main=77`, `High=100`, `High10=110`, `High422=122`, `High444=244`):

- `sa_avce_quality_zma.bin` contains `Baseline (66)` (4 hits), `Main (77)` (7 hits), and `High (100)` (8 hits at `0x22803..0x229BB`).
- `High422 (122)` and `High444 (244)` have `0` hits.
- The two `u32 110` hits (`0x2BB8B`, `0x32C4B`) in `sa_avce_quality_zma.bin` sit inside sequential index tables (`108, 109, 110, 111`), not `profile_idc` checks.
- Consequently, `ZIMA_AVC` is strictly an 8-bit 4:2:0 encoder on ILCE-6400; >8-bit video capture requires either `RCRAW` DDR capture (`AIF0/1 W2` @ `0xF3002480`/`0xF3102480`, [E0623](0623-a6400-aif-w1-w2-rcraw-registers.md)) or full-range `0..1023` `EC` Gamma shaping before 8-bit quantization.

## Verification

- `check_markdown_links`: **PASS**.
