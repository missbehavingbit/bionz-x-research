# E0632 — Real ILCE-6400 MSC read-only baseline of RCRAW_EN, AIF0/1 W1/W2, and 0xF30317C8

Date: 2026-11-19. Read-only physical ILCE-6400 observation in USB MSC mode via `tools/camera_debugger.py` (no camera writes, mode switches, or SD changes).

## Summary

Executed a strictly read-only (`O_RDONLY | O_SYNC`, single-page 4 KiB `PROT_READ` `mmap2`) native probe (`ro_rcraw_snap`, `1860` B, SHA-256 `18c59bfec0cfc839…`) on the live `ILCE-6400` in USB MSC mode and removed the temporary `/tmp_bt/ro_rcraw_snap` binary immediately afterward:

- **LIRO RAM (`av-cam.bin`) flags:**
  - `RCRAW_EN` (`PA 0x043D1810`): `0x00000000`
  - `RDRAW_EN` (`PA 0x043D1BCC`): `0x00000000`
  - `PA 0x043D1534`: `0x00000000`
  - `SET_RAW_EXTERNAL_OUTPUT` (`PA 0x044080EC` / `0x044080F4`): `0x00000000` / `0x00000000`
- **Live `AIF` and `RCRAW` hardware registers (unlike QEMU `0x00000000` baseline in [E0631](0631-rcraw-enable-test-qemu-reload-and-aif-baseline.md)):**
  - `AIF0 W1` (`PA 0xF3002400..0xF3002424`): `EN=0x00000802`, `RUN=0x00000000`, `ADR=0x00000000`, `SZ=0x00000F60`
  - `AIF0 W2` (`PA 0xF3002480..0xF30024A4`): `EN=0x00000002`, `RUN=0x00000000`, `ADR=0x00000000`, `SZ=0x00000148`
  - `AIF1 W2` (`PA 0xF3102480`): `EN=0x00000002`
  - `RCRAW` register `0xF30317C8`: `0x018000D0`
- **Camera responsiveness & cleanup:** `/proc/uptime` progressed monotonically (`48.41 -> 81.01`), `/tmp_bt` verified clean.

## Verification

- `python -m tools.camera_debugger push ... /tmp_bt/ro_rcraw_snap`: **PASS** (`1860` B, `readback_verified: true`).
- `python -m tools.camera_debugger exec "chmod 755 /tmp_bt/ro_rcraw_snap; /tmp_bt/ro_rcraw_snap; rm -f /tmp_bt/ro_rcraw_snap; cat /proc/uptime; ls -la /tmp_bt"`: **PASS** (`48.41 -> 81.01`, `/tmp_bt` clean).
- `check_markdown_links`: **PASS**.
