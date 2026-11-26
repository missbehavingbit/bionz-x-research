# E0579 — QEMU USB startup and sensor-writer traces

Date 2026-11-18. Offline ILCE-6400 QEMU runs with disposable eMMC overlays.

## USB

The hash-verified E0524-style input set `inputs-moddev-1` has manifest
SHA-256 `5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`.
The original main kernel used for disassembly has SHA-256
`12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707`
and load address `0x60108000`. The baseline QEMU 10.1 binary has SHA-256
`2720703c2b200a1dbaf9ee7c7e4a99e3e6a8c026b21e7517bcd7b836354486a7`.

The guest reported the platform resource `0xF0200000–0xF02FFFFF`, with
the driver claiming `0xF020C100–0xF02FFFFF`. The driver symlink was present
and the guest exposed `/dev/usb/gadgetcore`, `otgcore` and `event`.
GIC lines 229 (DWC3), 230 (VBUS) and 231 (ID) had zero deliveries in the
sample. The serial commands for these observations all returned zero and
QEMU exited via QMP. This establishes driver binding in the baseline.

The opt-in `dwc3_trace.c` observer recorded 15 accesses, all on CPU 0,
with a 256-sample budget per core. Original `fusb_otg_probe` reads and
writes `0xF0220000` at `0x602AD7B8` and `0x602AD7E4`, respectively.
It also writes `0xF020C2C0`, `0xF0231080`, `0xF0231084` and
`0xF020C200`. Original `dwc3_probe` stores `0x40000000` at
`0xF020C704` at PC `0x602AE464` and reads back zero at `0x602AE46C`.
The reset routine at `0x602AE0F4` writes `0x800` to `0xF020C110`
and also reads back zero. These are the actual responses of the existing
unimplemented region. No USB transaction or enumeration was observed.

The VBUS handler reads bit 0 at its companion base plus `0x10`
(`0x602AD2A8–0x602AD2B8`), then calls the notifier at `0x602AD230`.
The ID handler reads bit 1 from the same word. The probe stores this
base at context offset `+0x18`, distinct from the `+0x28` pointer
used when accessing `0xF0220000`. Recover the physical status word and
its GIC edge/level contract before wiring an opt-in virtual cable. The
DWC3 gadget path then needs event-ring and EP0 TRB handling against the
guest's own requests.

The ARM fixture mapped the trace's expected guest PC and issued read,
write `0x5A`, read at `0xF020C120`. All three samples carried the
expected PC and value. The first implementation rejected valid accesses
because QEMU 10.1 omitted the plugin IO flag for these transactions.
The fixture's `-d unimp` output confirmed the accesses reached the
catch-all region. The corrected observer selects by physical window and
original instruction range. It changes no guest registers, memory or IRQs.

## Sensor writer

`P2S_TRACE=1` opts into recording up to 64 accepted starts and their
completion or reset. The trace samples the raw config and mode and all
112 low-byte staging slots. It assumes no transfer-length encoding,
sensor address format or frame geometry. The existing 50 µs model timing
remains a hypothesis.

Native qtest exercised tracing off and on with the current board overlay.
Both runs observed busy on start, a rejected second start while busy,
status zero at 49,999 ns, completion at 50,000 ns, W1C acknowledgment, and
reset cancelling a pending transfer. The opt-in run emitted exact staging
bytes and correctly paired starts with completion or reset. The baseline
emitted zero trace rows.

The 95 s captured-input TCG run reached the original shell and stella,
returned zero from three guest commands and exited via QMP. LIRO task 65
was dormant at the stop. Neither that run nor the bounded legacy-input
retry emitted a P2S start. A fast-clock trial without `-icount` entered
the guest exception monitor at PC zero. To correlate sensor mode with
the E0578 IMX284 tables, first capture the original sensor-writer caller
and a completed P2S transaction during a run that reaches the live-image
path. CamPig completion and PQD Ydet remain the documented boundaries
for the later live-image replay in E0569.

## Reproduction and receipts

- `artifacts/qemu-a6400/e0579-usb-baseline/` holds ARM disassembly and
  the failed first boot, which tried to add a duplicate `ulogio.bin`.
  `e0579-usb-retry/` holds the shell baseline, plugin builds, fixture,
  failed IO-flag trials and final trace. Run the baseline with
  `env ULOGIO= python3 -u -m tools.research.qemu_boot_probe --inputs artifacts/qemu-a6400/inputs-moddev-1 --report-dir artifacts/qemu-a6400/NEW-USB-PROBE --seconds 65 --wait-for 'Created HeapAllocator:' --guest-command 'cat /proc/iomem' --guest-command 'cat /proc/interrupts' --guest-command 'ls -l /sys/bus/platform/devices/dwc3.0/driver /dev/usb'`.
  The final plugin run uses the hash-verified Linux-local copy of those
  inputs, `ULOGIO=`, `TIMEOUT=70` and
  `QEMU_EXTRA="-icount shift=0,sleep=on -net none -no-reboot -plugin PATH"`.
  Exit 124 records its wall-clock stop.
- `artifacts/qemu-a6400/e0579-p2s/` holds the initial build, which lacked
  the latest board options, plus the isolated current-overlay build and
  qtest under `current/`. Run `build.py` and `qtest.py` there in the
  same WSL session. The captured replay adds `ULOGIO= P2S_TRACE=1
  EVSEL_LATE_US=500 CAMPIG_ENABLE_READBACK=1 SGC1_EVENT_A=1` and uses
  the boot observer with `--seconds 95 --wait-for 'Created HeapAllocator:'
  --guest-command 'cat /proc/interrupts' --liro-task 65`.
- Windows discovery ran 1,239 tests and reported 146 errors and 39 skips,
  including the existing `_ctypes` load failure. Linux eMMC qtests passed
  independently (2/2). The Linux combined run could not import the
  boot-input test module because its Python environment lacks Unicorn.
