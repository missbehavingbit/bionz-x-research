# E0526 — QEMU `sony-a6400`: SIO, GPIO and ADC models; the sub-micro link runs on SIO ch3 and its RX/TX frames live at fixed PAs `0x034434A4` / `0x034436C8`

Date: 2026-11-15. **Strictly offline.** No camera access, no USB (`camera_debugger ping`: no camera
attached). `emmc.img` is unchanged. Builds on E0521/E0522/E0525.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

1. **POWER is a sub-micro ("front") key on the a6400 [C].** The guest's `/proc/udm/input` key table
   has `FRONT(CA) port 6000 bit 0 L1 key 0x2`: POWER = CAkey1 bit 0, active low. That matches the
   a5000 (`RE_INPUT_I2C_POWER.md` §7, where CAkey1 = sub-micro slot-A bytes 28/29). The same
   table lists active-low GPIO keys P0.3, P0.4, P0.7, P0.10 (MOVIE 0x1F0), P0.11 and P1.1, plus a
   two-position switch P9.22 (H key 0x4C / L key 0x4B), and ADC ladders on channels 0xC/0xF/0x10/0x11
   whose pressed bands all end at ≤ 0x304.
2. **Models ported from `Oein/a5000-emulator` (GPL-2.0, attributed in each file) [C].**
   - `cxd90014_sio.c`: 5 channels at `0xF2405000 + 0x200·ch`, IRQ 201+ch. The guest `/proc/udm/devices`
     lists the same block. The a6400 driver's register sequence (`+0x00` bit31/30 clears, `+0x04` mode,
     `+0x0C` len−1, `+0x10 = 0x01000000`, `+0x00` bit29 start) matches the a5000 contract.
   - `cxd90014_gpio.c`: 18 ports at `0xF2910000`, pin IRQs p0.0–15 → 121+, p0.16 → 144, p1.0–6 → 137+.
     The a5000 reset levels are replaced by a `reset-high` property, set by the board from the a6400
     key table.
   - `cxd90014_adc.c`: units at `0xF2406000/7000`, IRQ 215/216, all results 1023 (released).
3. **a6400 link pins from this `Backup.bin` (CX62600_UC2) [C]:**
   - request F904CA/CB = **P2.5** (a5000 p3.17)
   - strobe F90465/66 = **P0.8**, IRQ 129 (a5000 p0.12)
   - release-lock F9045F/60 = **P0.9**, IRQ 130 (a5000 p0.7)
   - exposure F90200/01 = **P1.2**, IRQ 139
   - S2REQ F90455/56 = P7.15 (CPU output)
   - CS F900B1/6D = P5.14
   - lens detect F9050C/0D = **P1.4**, IRQ 141

   Each pin IRQ appears as a `liro-*` registration in `/proc/interrupts`.
4. **Results [C].**
   - ADC unit 1 scans at about 100 Hz (`liro-216`).
   - SIO ch1 now completes. Its one transfer is mode `0x61000000`, 2 bytes `b0 9e`, to an unidentified peer.
   - With the a5000 strobe recipe (request low, 1 ms strobe every 50 ms on P0.8), the sub-micro link runs.
     A 180 s run logged **3,408 full-duplex 128-byte exchanges on SIO ch3** (a5000 had guessed ch0 [?]).
   - Every CPU frame is a type-0x31 TLV frame: `31 00 3c 00`, then TLVs 03(12), 06(4), 08(12),
     0a(8, data[1] = 0x20), 12(4: `01 02 02 38`), with the CRC LE at 126.
   - No slot-A (task-48) exchange was seen.
   - Off factor 7 remains: a reply that echoes the CPU's own frame carries no valid micro data.
5. **Link buffer location [C].** A diagnostic slave (`A6400_SIO_MARKER=1`) answers with a
   CRC-valid type-0x30 frame whose TLV 0x13 holds `A6400RXMARK!`. RAM dumps (`pmemsave` of every DDR
   region) show it at **PA `0x034434A4`** (VA `0x634434A4`). The CPU TX frame sits at **PA `0x034436C8`**.
   Both are 128-byte blocks from av-cam's `RBUF` allocator inside `wbimem=0x500000@0x03085000`, and
   their addresses are identical in all five runs. A non-CRC marker was seen only there too, and
   nowhere else in 1.9 GiB of dumps.
6. **Capture plan for the real micro frames [I].** av-cam loads at the same PA on the camera
   (`liro.ko adrs=035c6000`), and its allocator is deterministic. A **single 4 KiB read-only
   `/dev/mem` window at `0x03443000`** on the live a6400 should therefore contain the real micro's
   latest slot-B frame at `+0x4A4` and the CPU frame at `+0x6C8`. This is the a5000 method
   (frames from a real RAM snapshot) applied to the a6400. It is within the standing read-only
   authorization: one bounded page, no sweep. The existing `probes/uvc_dma_probe.c --devmem`
   implements exactly one ≤ 4096-byte window. **Not run: no camera attached.**

## 1. Machine changes

- `sony_a6400.c`: SIO, GPIO and ADC instantiation. GPIO `reset-high = 0.1,0.3,0.4,0.7,0.10,0.11,0.13,1.1,1.4,1.5`:
  - the key-table active-low keys [C];
  - the a5000 shutter lines P0.1/P0.13/P1.5 [I];
  - the lens detect P1.4 at "empty mount" [I for the polarity];
  - P9.22 left at 0 [A].
- Sub-micro strobe timer (a5000 values) [A for the timing].
- SIO logging slave (`-d unimp`: "sony-a6400 sio chN mode=… len=… tx: …").
- Marker diagnostic, environment-gated.
- `patches/…patch`: `hw/arm/meson.build` now also builds `../misc/cxd90014_{sio,gpio,adc}.c`.

## 2. Receipts

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/qemu-a6400/e0525/proc-udm-input.txt` | `b399cc927cc978fadb452f5500238029facdc0f91e9f10174635b0a53afc2198` |
| `artifacts/qemu-a6400/e0525/sio-transfers-echo.log` | `16369cdd348a531f4d1a093ec9e93fa7236caa1a500aa1bf61778464582d260e` |
| `artifacts/qemu-a6400/e0525/page-0x03443000-marker-run.bin` | `bab626cf010647e7f086013de1238e5235b81dc325ee27154e29d90995d395ad` |
| `artifacts/qemu-a6400/e0525/console-strobe-run.log` | `acac1d2c0fd8291652b493ec7a4704a42015faa41a4016db62693e66be68c69c` |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `76463c38ab0cbb14c2161948bae0c94aef7eadb848e5d86016e9458587f6e008` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_sio.c` | `8303b212cba18b349df00c34047e3ce559cf214f20b78d2b69f24972802797d6` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_gpio.c` | `2b415b9ba7eb92ed2868a9abb84659fd0c08828188b9159b50154e49f8aa5e0e` |
| `qemu-a6400/qemu-overlay/hw/misc/cxd90014_adc.c` | `d050b5a570c34ecb1cf14650f26bfc923caf6ebbe06f36ac7683fdbe9ac6fa7c` |
| `qemu-a6400/patches/qemu-v10.1.0-sony-a6400.patch` | `d002ea08dd60fcf04f6fe883aa40114292bba3ae042c224fbd2c0d7ec49b3b2c` |

Commands:
- `TIMEOUT=100..180 ./run.sh`, plus `QEMU_EXTRA='-icount shift=0,sleep=on -d unimp -D …'`, with timed
  serial scripts (`cat /proc/udm/input`, `/proc/snsc_boot_time`, `/proc/interrupts`).
- RAM dumps via the monitor: `pmemsave ADDR SIZE "file"`. Quote the file name, or HMP parses `SIZE /path` as a division.

## 3. Failures and cautions

- **IRQ 129 storm [C].** The ported GPIO holds a pulsed pin IRQ high for 1 ms. At level trigger the
  av-cam ISR re-enters for the whole pulse: 2,674,968 `liro-129` interrupts in one run. The real GPIO
  interrupt latch/ack is not modelled [?]. An edge-configured GICD_ICFGR or a latched model is the fix.
- My first assumption that P0.9 is the lens detect (from the a5000) was wrong. The a6400 Backup puts
  lens detect on P1.4 and the release-lock line on P0.9. Corrected before the strobe runs [C].
- A non-CRC-valid marker frame is dropped by av-cam (only a logged copy survives), so the first
  marker hunts found nothing [C].

## 4. Next boundary

1. **Capture the real frames** (camera attached): one 4 KiB `/dev/mem` read of PA `0x03443000`,
   then replay the real type-0x30 frame like the a5000's `submicro_frames.bin`. Check for a slot A
   (task-48 packet carrying CAkey1) next to it. If the page holds no CRC-valid frame, the
   allocation differs on hardware, and the static route (step 2) applies.
2. **Static route, if no capture:** decode where av-cam's "front" sync handler (queue 0x280132)
   takes CAkey1 from on the a6400. On the a5000 it is slot A, which this link has not exercised yet
   (no event-4 engine tick). Then build the minimal reply labelled as an assumption.
3. Fix the IRQ 129 storm; identify the SIO ch1 peer.
