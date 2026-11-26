# E0649 — Live ILCE-6400 with Sigma 18-50 F2.8 DC DN: lens capability data is not readable from the Linux side

- **Date:** 2026-11-20
- **Target:** live ILCE-6400 in USB MSC mode with a Sigma 18-50 mm F2.8 DC DN attached; `tools/camera_debugger.py` (system Python); `im.elf` pid 149
- **Static input:** `libObj.so` SHA-256 `60ffd2b0f31f4bc1…`
- **Scope:** read-only. Commands used: `exec` (cat/ls of `/proc`, mounts, `/lens`, `/log`), `maps 149`, and `mem 149` reads of two regions. No `/dev/mem` access, no writes, no mode switches.
- **Local artifacts (gitignored):** `artifacts/analysis/e0653-lens-info/`: `im_elf_maps.txt`, `libobj_data_bss.bin` (`0x07016000–0x07144000`, SHA-256 `4f86f591…`), `heap.bin` (`0x00015000–0x00333000`, SHA-256 `8fbce45b…`), `manifest.json`, `read_regions.py`
- **Responsiveness:** `/proc/uptime` 3915.11 → 3940.50 → 4154.16 → 4338.80 (monotonic). `/tmp_bt` held only pre-existing entries before and after. One `exec` returned output and then reported error −110; the next call succeeded.

## Observations [C]

1. `/proc/iomem` lists System RAM `0x00000000–0x3FFFFFFF` and `0x80000000–0x8FFFFFFF`. No process maps the ORIL window (`0x6xxxxxxx`). The body–lens data block (`BL_LENS_DATA`, E0648) therefore cannot be reached through process memory. Reading it through DRAM `/dev/mem` is forbidden by `tools/devmem_policy.py` (E0647) and was not attempted.
2. `libObj` converts lens messages in `0xA35258(this, msg)` (msg byte 0 = category 0–6). For example, `msg[+0x76]` (FocusActuatorType, values 0/1) becomes key `0xC06A` with value `0x2285`/`0x2286` in a temporary map (`+0x650`), which is then sent on (`0xA2AFEC`). Callers (`0xA42666`, `0xA426A0`, `0xA42920`) pass the message through; no persistent copy was found.
3. The captured `libObj` data/bss and `[heap]` contain no lens name strings (`Sigma`, `18-50`, `F2.8`, `DC DN`) and no key `0xC06A` paired with `0x2285`/`0x2286`.
4. `/lens` (`/dev/nflasha18`) holds only Sony `VX*_lensfile.bin` correction files. `/log` holds only `feeder.log`. The SD card is not mounted on the Linux side in MSC mode, and Windows showed no drive letter for it in this session.

## Conclusion

On this body, the lens command bitmap and the lens capability record live only in ORIL memory. Linux sees transient converted values, and their store (the receiver of key `0xC06A`) is still unknown. A passive mount-bus capture remains the read-only route to the Sigma data.

## Next boundary

- Find the receiver or store for the `0xA2AFEC` notifications (candidates: `libIMDB`, application objects in the 430 MB of anonymous `im.elf` mappings), then read it for a Sigma versus TTArtisan comparison.
- Alternatively read MakerNote `LensE-mountVersion` / lens firmware fields from JPEGs taken with each lens, using a host-side copy of the files.
