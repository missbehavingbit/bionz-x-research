# E0634 — Real ILCE-6400 post-recording check with RCRAW_EN = 1

Date: 2026-11-19. Read-only physical `ILCE-6400` observation in USB MSC mode after a brief video recording with `RCRAW_Enable_Test.mod` active (`PA 0x043D1810 = 1`).

## Summary

After a brief video recording on the physical `ILCE-6400` (with `RCRAW_Enable_Test.mod` enabled in RAM) and returning to USB MSC, `ro_rcraw_snap` (`1860` B, removed after execution) read:

- `RCRAW_EN(043D1810) = 0x00000001` (persisted across recording)
- `RDRAW_EN(043D1BCC) = 0x00000000`
- `D1534(043D1534)    = 0x00000000` (meaning `[0x643D152C + 9]` at `PA 0x043D1535` remained `0`)
- `RAW_EXT(044080EC)  = 0x00000000`
- `AIF0_W1_SZ(2424)   = 0x00000F60`, `AIF0_W2_SZ(24A4) = 0x00000148`, `RCRAW_7C8(F30317C8) = 0x018000D0` (identical to the pre-mod `E0632` baseline)
- Camera responsiveness verified: `/proc/uptime` `518.51 1743.65`.

## Conclusion & Next Boundary

Setting `PA 0x043D1810 = 1` alone does not re-program `0xF30317C8` or `AIF0 W2` during a stock Movie recording. The next static/Unicorn step is to inspect `0x6376873C` and the `[0x643D152C + 9]` (`PA 0x043D1535`) / Movie-vs-Still callers of `0x63B38168` and `0x63B386CA` in `av-cam.bin` to identify the remaining gate(s).

## Verification

- `ro_rcraw_snap` via `tools/camera_debugger.py`: **PASS** (`readback_verified: true`, `/proc/uptime` `518.51`).
- `check_markdown_links`: **PASS**.
