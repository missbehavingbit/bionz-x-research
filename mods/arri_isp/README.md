# ARRI ISP staging component

This repository currently supports an offline build for the conditional ISP staging logic.
The build consumes the two previously exported A6LT presets as test inputs and emits a
relocatable Cortex-A5 Thumb object. The object has no entry trampoline, fixed hook
address, device I/O, or SMOD package.

```powershell
wsl.exe --exec bash mods/arri_isp/build_wsl.sh artifacts/tests/arri-stage-new artifacts/tests/arri-isp-final-2026-11-16
```

`arri_load` validates an exact 1,864-byte A6LT v1 container in a control context. It checks the
header, verified-layout flag, CRC32, 12-bit monotone gamma, signed Q10 row sums, and reserved
eSRAM bits. The preset ID is supplied by the caller because A6LT v1 carries no preset name.
Treat the CRC as an integrity check. The final converter receipt separately records SHA-256
for both inputs. A future control adapter can load the preset once into an unpublished
static slot and publish it after validation. Keep that slot immutable until readers drain.

`arri_stage` requires an enabled flag, exact target PP match, Movie without Playback, S-Log3,
S-Gamut3.Cine, Knee Off and neutral Saturation and Color Phase. These are semantic state values.
Their offsets and representations in Sony's ABI still require verification. The function copies
all three layers to a single caller-owned private staging aggregate. The caller must hold
exclusive ownership and keep the aggregate private until Sony's verified commit path consumes it.
The staged result alone cannot establish that any ISP register or DMA bank has changed.

The existing Mods loader applies hooks within `im.elf` at `viewUnified2.so` addresses. On the
tested MSC boot, `liro` is loaded and the ISP virtual region is absent from `im.elf` maps.
`av-cam.bin` runs under LIRO. Shipping a SMOD entry with an ISP address would send the existing
loader outside its verified target. The loader's ON label reflects its own patches. A future
Mods UI should show ARRI_709 or ARRI_AWG3 and report ISP Hook ACTIVE only after the LIRO side
acknowledges a committed generation. Suspended or bypassed state should come from that same
side. See the evidence supplement for observed addresses and the next verification boundary.

[Physical MSC preflight and ARM observations](../../docs/evidence/0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md#2026-11-16-supplement--physical-msc-preflight-for-a-conditional-arri-hook)

The next firmware trace reached an EC-to-FC gamma descriptor. A 96-word fallback on that
path tops out at 1023, and the exported presets use a modeled 4095 scale. The active Movie
gamma consumer and its scale remain unverified. Keep preset staging offline until the
recording path and its relationship to the CC bank switch are established.

A later trace found Sony's external-gamma control fields and an EC gamma work block with
96 samples plus nine additional words. This work block has a different layout from the
A6LT gamma section. The external pointer's payload format and the consumer that commits
Movie settings remain open.

The Movie-candidate gamma path now ends at a linked 420-byte buffer (`0x64427840` in the
retained image). EC fills it from its work block, `EC_SetExtInfo` can overwrite it with an
external block, and `EC::PQD_CamE` publishes it to CamCore. A future adapter would need to
supply that 420-byte layout instead of the A6LT 192-byte gamma section, after a live trace
confirms the path.

CC refills all eight CamCore stage blocks, matrix and 3D LUT included, from the VD sequence
(`0x63addc7c` at `0x63934b5c`). A persistent override has to run after that refill on every
pass. The matrix stage applies its own Q8 gain (256 = neutral), which must be accounted for.

`gamma_poke/` holds a freestanding guarded single-page `/dev/mem` writer (raw syscalls,
no libc). On the camera an mmap write to the Movie gamma buffer landed, but EC refilled the
buffer within 20 ms. The persistent target is stock EC record 31 (S-Log3) at
PA `0x03e5363c`. Prepared replacement blocks are in
`artifacts/analysis/arri-gamma-test-block-2026-11-16/`. Writing the record needs explicit
approval. A Mods-tab entry could later run the same guarded swap on toggle.
