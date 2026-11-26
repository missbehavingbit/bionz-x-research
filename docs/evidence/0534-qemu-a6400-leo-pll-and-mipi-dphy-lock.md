# E0534 — QEMU `sony-a6400`: Leo PLL lock and MIPI D-PHY init complete; the sensor writer is still idle

Date: 2026-11-16. **Strictly offline.** Builds on E0533.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

1. **The imager was failing its own clock init, before any sensor transfer [C].**
   Ulog RAM (`PA 0x03085000`, 0x478000 bytes, saved with monitor `pmemsave` at
   `SetContentTarget`) contained `IMG:Timeout:Leo Clkgen PLL Wait` (4) and
   `Wait4` (2), then `IMG:MIPI-Tx D-PHY 0` init errors for `set_mptx_fbmode`,
   `set_mptx_pll_rstx`, `set_mptx_phy_rstx` and `set_mptx_csi2_rst_n`, then
   `PLL LWAITSTAT count max`. No `P2S`, `ZIT`, `PowerOn` or `CalcStart` line.
2. **Leo lock is three status bits at `0xFC021000` [C].** av-cam `0x63782020`
   reads `+0x050` / `+0x054` / `+0x058` bit0 (clocks 0, 1, 2) up to 1001 times
   after setting enable bits 0, 4 and 8 of `+0x000`. The literal pool word is
   `0xFC021000`. That page was unmapped, so every read was 0. The page now
   stores writes and reports those three bits set. The Leo timeout leaves the log.
3. **D-PHY config is ordinary read-back; LWAITSTAT is one status bit [C].**
   av-cam `0x637852a0` writes a value and polls it back (`+0x00c` bits 0–6 must
   read `0x7c`, `+0x008` bit0 must read 1, `+0x000` bit0 must read 1, `+0x004`
   must read `0x1111`). A read-back window at `0xFC000000` size `0x200000`
   clears those four errors. The following poll is av-cam `0x63cd9504`:
   `[base+0x20] & 1 == 1`, 1000 times. The address the guest spins on is
   `0xFC02A080` (base `0xFC02A060`). That word now reads with bit0 set, and
   `PLL LWAITSTAT count max` leaves the log. `IMG:[Err][DFE_INT] already set int 6`
   leaves with it.
4. **Live view is unchanged [C].** After those bits read as locked, the SA log
   is still the sixteen ID reads and no GO, there is no `[P2S]` line, and
   `en144` still ends at `0x3000232` (bit0 clear, ch51 base 0). Two IMG lines
   remain: `GetImagerTemperature` (the caller logs and continues unless the
   helper returns 0, 3 or 5; av-cam `0x63be4ad6`) and the lens ODL
   `LoadLensAberrationData` error `0x2` ("wrong message received"). Neither
   is a sensor-writer kick.

## 1. Commands

Ulog save. A filename that starts with `/` is parsed as division
(`invalid char 'h' in expression` on `/home/...`). A relative name is written
in the `qemu-a6400` working directory:

```bash
python3 tools/a6400_probe.py --until SetContentTarget --timeout 420 --poll 2 \
  --monitor "pmemsave 0x03085000 4685824 e0537-ulog.bin"
```

The run that cleared `LWAITSTAT` set bit0 of `0xFC02A080` after 64 identical
reads and logged `[imager] status poll 0xfc02a080 was 0x0`. Checkpoint wall
time on that run was 75.8 s (earlier runs that still timed out the PLL were
about 49 s). The kept model sets the bit on every read, which satisfies the
same compare (the loop accepts bit0 on any of the 1000 tries).

## 2. What this does not show

- `GetImagerTemperature` returning a code other than 0, 3 or 5 is a logged
  error, not an early return [C]. It is not shown to be the reason P2S stays idle.
- Lens aberration load error `0x2` is logged from `Camera::Lens::Initialize`.
  a5000 `lens=none` still ran the imager (`RE_LIVEVIEW.md`). Whether the a6400
  sequencer treats this ODL failure as fatal is open.
- The 75.8 s checkpoint is one run. It is not a measured guest sleep.

## 3. Next boundary

Why av-cam still does not reach imager `PowerOn` (the P2S kick at `0xF3C00908`)
or ZIT `CalcStart` (SA GO). Clock and D-PHY init now get past the polls that
were failing. a5000 `RE_LIVEVIEW_REQUEST.md` step 12 (the mode sequencer never
posts imager request `0x10`) is the matching earlier stall.
