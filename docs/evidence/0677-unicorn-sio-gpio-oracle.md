# E0677 — Unicorn SIO and GPIO from the QEMU oracle

Date: 2026-11-20. Offline. The QEMU capture used the existing `sony-a6400` machine. No camera or USB was touched.

## Observation

A 75-second `mmio` capture of QEMU 11.1.3 (`artifacts/analysis/oracle-e0677.jsonl`, 64,000 MMIO records, plugin `unicorn_oracle.so`) puts the two hottest device windows that Unicorn still answered as zeros at:

- `cxd-sio` `0xF2405000`, especially channel 3 status `0xF2405600`. The first guest writes are `0x80000000`, `0x40000000`, then `0x18000000`. After the config write the same address reads back `0x18000000`. A later start write reads back `0x38000000` (config bits 28 and 27 plus done bit 29).
- `cxd-gpio` `0xF2910000`. Port 1 input `0xF2910110` polls as `0x32` from the first sample.

## Contract now on `--qemu-peripherals`

`CxdSio` follows the QEMU channel-3 start path: bit 31 drops the IRQ, bit 30 drops the error bit, a rising bit 29 completes the transfer immediately and raises IRQ 204. When the hash-checked E0526 page `camera-page-0x03443000.bin` is present, channel 3 replays its 384-byte sub-micro extract (`skip 0x3A4`). TX byte `0x31` selects slot C, `0x61` selects slot B, and any other byte selects slot A. Other channels complete with the FIFO unchanged. Replaying the capture's SIO reads through this model matches 9,436 of 9,506. The 70 misses are a done bit still set where the merged four-CPU log has already dropped the clearing write.

`CxdGpio` uses the board reset-high pins `0.1, 0.3, 0.4, 0.7, 0.10, 0.11, 0.13, 1.1, 1.4, 1.5`. Port 1 input data is therefore `0x32`. Direction, input-enable, mode, and output use the value/set/clear triples. A pin edge latches the QEMU pin IRQ (port 0 pins 0–15 → 121+pin, pin 16 → 144, port 1 pins 0–6 → 137+pin). Replaying the capture against this model matches 10,674 GPIO reads. The other 237 are port 0 pin 8, which QEMU pulses for 1 ms every 50 ms (first edge at 50 ms, IRQ 129). That strobe is now on the same 1 MHz clock. The capture has no timestamps, so those 237 samples are not a value mismatch.

P2S at `0xF3C00000` does not appear in this capture. QEMU still completes a start: `+0x908` bit 0 is busy for 50 µs, then clears, `+0x904` bit 0 sets, and IRQ 38 stays asserted until that bit is written 1. Leaving busy set makes the driver refuse every later transfer. That path is on the bus. This capture does not exercise it.

Channel 11 of the CXD4115 timer (`0xF2403B00`) is the imager power wait. The capture shows one-shot control `0x1112`. A match sets the pending bit and clears START, and IRQ 164 follows the enable bit. The machine's existing clocksource still owns the first `0x200` bytes, so channels 0 and 1 are unchanged.

The imager window read at `0xFC02A080` is MIPI-Tx D-PHY LWAITSTAT. QEMU forces bit 0, because a stored zero is the `PLL LWAITSTAT count max` stop. The Unicorn imager register file does the same.

The same capture reads the four SA core IDs once each (`0xF34A00F0` starts `ZMA0`). `CxdSaCore` returns those 16-byte strings. With run enabled, GO completes 100 µs later: GO bit 0 clears, factor A becomes 1, and IRQ 51/54/61/64 follows the enable mask. QEMU's ZMA1 startup-program check and JPEG fill stay off, which is that machine's default.

The same capture's next polled device is the ADC. Unit 1 status `0xF240700C` is read as `0x100` before the conversion and `0x101` after it, and channel results `+0x14`, `+0x18`, and `+0x1C` read `0x3FF`. `CxdAdc` uses those reset levels and sets the done bit on the start write, because a Unicorn slice cannot stop for QEMU's 50 µs conversion timer. IRQ 216 follows bit 0 and bit 8. The clock-generator window `0xF2800000` (32 KiB) reads back what was written, which is what the divider read-modify-write needs.

## What this does not show

No Unicorn `nflasha3` replay was run for this record. The E0414 boot-set image that [E0656](0656-unicorn-main-firmware-boot.md) used is not the input of this check, so nothing here shows `av-cam` leaving the `ObjMic` wait or a rear-LCD frame. Rear-control, SD, and lens edits inside the QEMU sub-micro slave stay off, matching the oracle boot.

## Check

```powershell
.\venv\Scripts\python.exe -m unittest tests.emulator.test_cxd_peripherals tests.emulator.test_timer_gic_model -q
```

That run includes the oracle status-word sequence, the port-1 `0x32` input, slot selection, and the hash-checked 384-byte extract (`20002200` at byte 0).
