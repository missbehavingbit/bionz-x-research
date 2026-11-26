# E0571 — QEMU a6400 rear-monitor selector

Date: 2026-11-17. Offline static analysis following E0540–E0542.

The QEMU eMMC copy of `/usr/lib/viewUnified2.so` and the update extraction
match byte for byte by SHA-256
`1e2867b6bff2d4fd4d3b93bacf8c7da0b9a86f266b4ba1763230e33badb6e7f2`.
The SDM entry for nflasha15 starts at 763363328 in the user area.
The backing file adds 8388608 bytes. Read 314572800 bytes at backing
offset 771751936 into scratch with `dd`, then used read-only
`debugfs -R 'stat <14273>'` and `debugfs -R 'cat <14273>'`.
The inode size is 11530552 bytes. Static symbols and instructions came
from `arm-linux-gnueabi-nm -DC` and `arm-linux-gnueabi-objdump -dC` on
the matching update library. Addresses below are ELF virtual offsets.

| Site | Original instruction behavior |
| --- | --- |
| `0x40a188` | Exported `CmnViewDisplayDeviceControl::LcdEvfToggle()` exchanges device codes 1 and 2, calls the backup-update helper, then stores the result at object offsets 17 and 18. |
| `0x409750`, `0x409788` | Read backup unit `0x010708E0`, normalize values outside 1–2 to 2, and write the same unit only when the requested device differs. |
| `0x40af34`, `0x40b194`, `0x40af62` | A separate firmware path computes the two object fields, then calls the same backup-update helper. E0540's boot-time backup edits alone do not establish the runtime values on this route. |
| `0x40af8e`, `0x40af98`, `0x40b036` | The path converts the two fields through the identity table at `0x7aba24` and passes the results to `0x2dc3ae`, which computes display-routing outputs using additional inputs and mode branches. |
| `0x40b168`, `0x40aa60`, `0x40aa86` | The downstream display handler calls a helper that tests for device code 2. One branch queries `LkmLayoutModeMngrIF::GetLayoutMode(int)`. |

The namespace distinguishes two layers of device codes. E0539 tests for
stored code 1 in the finder predicates, while the converted scanout code 2
is tested in the output handler. The incoming backup byte and final output
value need independent runtime observations.

The earlier a5000 panel-width handling and hinge input from E0536/E0537
cover the sibling model. For this body, the selector above comes from the
hash-matched a6400 library. The QEMU display model and firmware inputs were
untouched. The disassembly does not establish that the native toggle is
invoked in the existing boot, which class is instantiated, or which
console receives the real LCD output.

Next replay: record the incoming device byte and both object fields,
the output-device argument at `0x40b168`, and the result of the layout
query on the same boot. Check the selected layout at its original vtable
and capture the corresponding output surface. The black center work is
parked at [E0569](0569-qemu-sgc1-event-a.md).

Raw local receipts: `artifacts/qemu-a6400/rear-lcd-selector/` contains the
inode identity, selector, routing, and display-handler disassembly.
