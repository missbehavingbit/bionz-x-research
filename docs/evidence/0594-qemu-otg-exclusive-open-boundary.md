# E0594 — QEMU OTG exclusive-open boundary

Date: 2026-11-18. Offline QEMU only; no camera, USB device or physical SD access.

## Question and implementation

Can the E0593 proposed separate guest helper reach the original kernel's
OTG SET_MODE(2) operation while the stock application is running?

`qemu-a6400/tools/otg_mode_probe.c` opens `/dev/usb/otgcore` with O_RDWR,
then issues ioctl `0x4001e30c` with a one-byte value 2 only if open succeeds.
The command and payload come from the original createGadget consumer and
kernel dispatcher retained in E0534 and `guest_usb_otgcore.py`. This does
not reproduce the whole createGadget sequence or register a gadget.
The helper requires `--offline-qemu-only`; this argument is an operator
guard, not hardware detection or permission for camera use.

`qemu_usb_host_probe --otg-mode-probe PATH` stages the helper on the
disposable setting overlay, records its SHA-256, runs it before virtual
VBUS attach and reads DWC3 registers again. Without this option the
E0593 path is unchanged. The helper prints the real open/ioctl result;
no return or event is supplied for the guest. On open failure the final
helper reads `/proc/*/fd` symlinks to locate an existing owner.

## Reproduction and identities

```bash
arm-linux-gnueabi-gcc -static -Os -Wall -Wextra -Werror -march=armv7-a \
  qemu-a6400/tools/otg_mode_probe.c -o NEW_BUILD/otg-mode-probe
python3 -m tools.research.qemu_usb_host_probe \
  --qemu-dir /home/administrator/sony-a6400-qemu-11.1.3 \
  --inputs /home/administrator/a6400-inputs/inputs-moddev-1 \
  --otg-mode-probe NEW_BUILD/otg-mode-probe \
  --plugin artifacts/tests/dwc3-trace-11.1.3/dwc3_trace.so \
  --report-dir artifacts/qemu-a6400/NEW_REPORT
```

QEMU SHA-256 `18000b29a3c42f4abfc413d5fc44bff400c30467663d89a6ba86ae6e4c87996b`;
input manifest SHA-256 `5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`;
trace plugin SHA-256 `6e3d798306142d7a1f38c3ea9ea19fe88c5859b937e67b57c745ac5e7ed5e80f`.
Per-run probe and helper hashes are retained in each report.json.

## Observations and failed receipts

- Run 1: `OTG_MODE open=-1 errno=16` (EBUSY). No ioctl executed.
- Run 2 repeats EBUSY and `helper_exit=1`. Its shell ownership scan fails
  because the captured guest has no `readlink` command. No owner is inferred
  from that failed scan. Run 3 replaces it with libc readlink in the helper.
- Run 3 again reports EBUSY, `helper_exit=1`, and
  `OTG_OWNER=/proc/145/fd/16`. This identifies the descriptor owner, not its
  process name (comm/maps were not captured by this helper). Final helper
  SHA-256: `31d0662501f230910d1f312725314a85e5c124e73f73c9fc8a9232780e0556a9`.
  QMP quit and owned exit are both 0; elapsed time is 60.3 seconds.
- Registers remain halted (`DSTS=0x00400000`), event-ring size/address and
  DCTL.RUN remain zero; no IN reply to GET_DESCRIPTOR. The host observation
  completing with `status=observed` is not gadget-start or enumeration success.
- Receipts: `artifacts/qemu-a6400/e0594-otg-mode-{1,2,3}/`.

## Validation and next boundary

The helper compiles with warnings treated as errors. Four host-probe helper
tests pass. The full suite passes 2113 tests, 29 skipped, in 42.160 seconds
(`artifacts/tests/qemu-otg-mode-e0594/full-suite-native.txt`). The first
restricted-process run failed with 182 errors including an unavailable
Python `_ctypes` DLL; its separate receipt is preserved in `full-suite.txt`.

E0593's separate-open proposal cannot reach SET_MODE in this running guest.
Next: use the original process's USB service/owned descriptor through its
verified command and lifetime contracts, then observe event-ring programming
and RUN before testing EP0 or loading a real UVC mod. Do not close the
original owner's descriptor, stop im.elf or bypass exclusive-open checks.
The E0592 USB Streaming Test still counts lifecycle calls only.
