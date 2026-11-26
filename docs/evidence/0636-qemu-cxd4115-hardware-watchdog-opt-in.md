# E0636 — Opt-in CXD4115 hardware watchdog at 0xF2402000 (Custom OS prerequisite 3)

Date: 2026-11-19. Offline only: static disassembly of the stock kernel, QEMU
qtest, and two bounded real-firmware QEMU replays. No camera access.

## Why

[ROADMAP](../ROADMAP.md) "Custom OS prerequisites" item 3 asks for the hardware
watchdog to be modelled so a missed heartbeat resets the machine as on hardware.
Until now `0xF2402000` fell into the `cxd90045.unmodelled` catch-all: writes were
dropped and an expiry could never happen (E0525: 7,527 writes, 0 reads per run).

## Original contract [C]

Input: `qemu-a6400/data/nflasha3-Image`, SHA-256
`d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618`, symbols from
`artifacts/qemu-a6400/e0521/nflasha3-syms.json`, Image offset 0 = VA `0x60108000`.
Disassembly (capstone 5.0.7, ARM): `artifacts/qemu-a6400/e0636-kernel-wdt/wdt_dis.txt`
(SHA-256 `d49e36a8…728799`), script `wdt_dis.py` (`0801c8bb…6f3465`).

| Function | VA | Effect on `0xF2402000` |
| --- | --- | --- |
| `cxd900x0_wdt_setup` | `0x60120050` | `+0x0 = 0x80000012`; `+0x8 = *(0x604F84AC)` |
| `wdt_setup_counts` | `0x60120124` | `*(0x604F84AC) = 1001 * timeout`, `+4 = 1031 * timeout`, `+8 = 0x100` |
| `cxd900x0_wdt_start` | `0x60120080` | `+0x0 = 0x80001012` (adds START bit 12) |
| `cxd900x0_wdt_stop` | `0x601200A4` | `+0x0 = 0`; `+0x4 = 0x11` |
| `watchdog_touch` / `watchdog_tick` | `0x601204B0` / `0x601204E8` | kick: `+0x4 = 0x10` (clear counter) |
| `wdt_intr` | `0x60120438` | reads GPIO `0xF2915050`, not this channel |

The main-boot cmdline carries `wdt.mode=1 wdt.timeout=500`, so CMP =
`1001 * 500 = 0x7A314`, which is the value observed in the E0524 and E0636 logs.
This is the CXD4115 timer-channel layout already used at `0xF2403000` (CTRL / CLR / CMP / COUNT).

Inferences [I]: bit 31 means "reset on match" (inherited from the a5000 model's
`watchdog=true`); the channel clock is 4 MHz >> prescaler (bits[1:0] = 2), i.e.
1 MHz and a 500.5 ms timeout, as for the timer block (E0522). Neither has been
checked on hardware. The real reset scope (SoC only, or PMIC rails) is unknown.

## Change

- `cxd4115_timer.c`: on a watchdog-channel match the device now reports
  `cxd4115-wdt: chN expired (ctrl=… cmp=…)` with `error_report` (always visible)
  and calls `watchdog_perform_action()`, so `-action watchdog=` selects reset
  (default, same as before), pause, shutdown, poweroff, debug, none or inject-nmi.
- `sony_a6400.c`: new startup property `watchdog` (default **off**). When on, a
  one-channel `cxd4115-timer` with `watchdog=true` is mapped at `0xF2402000`.
  No IRQ is wired. With it off, the earlier write-dropped baseline is unchanged.
- `run.sh`: `WATCHDOG=1` appends `watchdog=on`.
- `tests/test_qemu_watchdog.py`: replays the kernel's exact register sequence.

| File | SHA-256 |
| --- | --- |
| `qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c` | `25230120816c682e122e31918a24d819dc96a2520921d9ee0070abee882e0677` |
| `qemu-a6400/qemu-overlay/hw/timer/cxd4115_timer.c` | `d1f476232184e892936c2c5f0b5a0939bbd54099bf9812a5310c3b77018ca45d` |
| `qemu-a6400/run.sh` | `b22c41746c989947dbbcf697b8d6ce522e9e35239cc880c8ed90cb0f896c7548` |
| `tests/test_qemu_watchdog.py` | `349de8a37e79e5d2d6feec8762dc6009330897c0fcbe369cd4e7ecd20863fe75` |

## Verification

qtest (WSL, QEMU 11.1.3 tree rebuilt with the overlay):
`python3 -m unittest tests.test_qemu_watchdog tests.test_qemu_liveview tests.test_qemu_emmc`
→ 14 tests OK (first on the pre-#42 tree; repeated after the upstream sync to `0419ce4`, CamCore E0637
included, binary SHA-256 `7a8caef8…68b70f`). The real-firmware replays below used the pre-#42 tree. The watchdog cases show: default machine drops the writes and never
expires; with `watchdog=on`, two 400 ms intervals separated by a kick stay alive,
the machine is still running 1 µs before 500.5 ms after the last kick, and 1 µs
after it the expiry line is logged and the device reads 0 after the machine reset;
after `cxd900x0_wdt_stop` it never expires (3 × timeout).

Paired real-firmware replays, `TIMEOUT=150`,
`QEMU_EXTRA="-icount shift=0,sleep=on -action watchdog=none -d unimp -D …"`:

| Run | Exit | Console lines | Last kernel stamp | `0xF2402xxx` unimp writes | Expiries |
| --- | --- | --- | --- | --- | --- |
| `e0636-baseline-2` | 124 (timeout) | 1,955 | 2.845 s | 5,239 (setup + 5,234 kicks) | n/a |
| `e0636-watchdog-2` (`WATCHDOG=1`) | 124 (timeout) | 1,973 | 3.229 s | **0** (all reach the model) | **0** |

Both runs end at the same userspace stage (same `ulogio`/`nflasha1` messages and the
same 4 pre-existing oops/BUG lines). Under `-icount` the original kernel keeps
kicking within 500 ms of virtual time for the whole bounded run.
Receipts: `artifacts/qemu-a6400/e0636-{baseline,watchdog}-2/` (console, stderr,
`unimp.log.gz`, filtered `wdt-unimp.log`, exit code).

## Failures preserved

`e0636-{baseline,watchdog}-1`: QEMU refused to start because the unquoted
`QEMU_EXTRA` split the `-D` log path at the space in the project path. Script
error, not a model result; rerun as `-2` with logs in a space-free WSL path.

## Not established

- Hardware reset scope, real clock rate, and the meaning of the second count
  (`1031 * timeout`) and the `0x100` field.
- Behaviour without `-icount` (wall-clock virtual time may starve kicks and
  produce expiries that hardware would not).
- Long runs past 150 s and the pre-expiry GPIO interrupt path (`wdt_intr`).

## Next boundary

Remaining Custom OS prerequisites: 1 (replace plain read-back windows
`0xF2800000`/`0xF3500000`/`0xF3880000` with verified models), 2 (DMA and
secondary-core quiesce guards before a `kexec` jump), 4 (ARMv7 cache/MMU teardown
sequence check). For watchdog item 3, a one-page read-only `/dev/mem` read of
`0xF2402000` on the camera would confirm CTRL/CMP live values.
