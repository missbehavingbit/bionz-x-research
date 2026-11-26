# E0628 — Callers of RCRAW CALC (0x63767774) and 0xF30317C8 hardware write

Date: 2026-11-19. Offline firmware analysis only. No camera, SD, or USB access.

## Inputs

- `av-cam.bin` (`16,674,456` B, SHA-256 `90d515cfec8b936f…`, `ORIL v4`, linked at `0x635C6000`)

## Summary

Scanning `av-cam.bin` (`0x635C6000..0x63DD0000`) for Thumb-2 `bl 0x63767774` (`RCRAW CALC`, [E0624](0624-a6400-rcraw-calc-unicorn.md)) identifies three exact callers:

1. **`0x637686C4` (`bl` at `0x63768702`):**
   - Validates mode byte `[r0, #0x42]` (`0`, `1`, or `2`), invokes `0x63767774`, copies the 11-word (`0x2C` B) output block to `r8`, and writes `([r6] << 16) | [r6+2]` directly to hardware register **`0xF30317C8`** (`0x6376872C` literal).
2. **`0x63B3BD70` and `0x63B3BF66`:**
   - Both validate `[r7, #0x42]` (`0`, `1`, or `2`), invoke `0x63767774`, check `0x63DB250E & 0x40000` (`0x63B398C4`), and forward `([r7, #0x42], [r6], [r6+2])` to `0x63B340F8`.

## Verification

- Static Thumb-2 `bl` scan and Capstone disassembly of `0x637686C0..0x6376872C`, `0x63B3BD40..0x63B3BD9A`, and `0x63B3BF30..0x63B3BF8C` in `av-cam.bin`.
- `check_markdown_links`: **PASS**.
