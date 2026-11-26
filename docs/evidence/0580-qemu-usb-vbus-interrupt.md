# E0580 — QEMU delivers USB cable changes to the original FUSB handler

Date 2026-11-18. Offline ILCE-6400 QEMU replay. No physical camera or USB access.

## Register and interrupt evidence

The experiment uses the same main kernel SHA-256
`12eeec829407595e28265f7d0db3b88c56f63a9f0fdd725c9c53c66a49643707`
and captured boot manifest SHA-256
`5aa4c878cd8214c5b503388f44d3d4f4512482d7ef37050beaadbe8b24906147`
as [E0579](0579-qemu-usb-and-sensor-observation.md).

- A broader passive driver trace recorded the FUSB probe accessing
  `0xF2920040` and `0xF2920050`. At PCs `0x602AD5B8–0x602AD5EC`
  the driver loads context `+0x18` and reads those offsets from the
  resulting base. The kernel's UDIF resource also names `0xF2920000`
  at `0x604DA474`. The original startup later reads `0xF2920010` at
  `0x602ADB74` and `0x602ADB80`.
- The original `fusb_interrupt_usb_vbus` reads bit 0 at that address
  (PC `0x602AD2AC`) and passes it to `fusb_notify_vbus`.
  The inline descriptor `fusb_usb_vbus` at `0x604DA6AC` has IRQ 230
  at `+0x10` and trigger flags value 1 at `+0x14`.
  `__udif_request_irq` ORs those flags into the Linux IRQ request at
  `0x602DBA9C`, establishing the rising-edge guest contract.
- The opt-in model maps a read-only 32-bit status word with bit 0
  controlled by the QMP cable state. A change sets the bit before
  pulsing IRQ 230. The pulse on each cable transition is an emulator
  stimulus. Physical pulse duration, companion enable registers, ID
  handling and downstream USB transfer timing remain unverified.

## Verification

The rebuilt board and trace plugin have SHA-256
`8bcef2b519915f88c92a768d929007c80fb8d31d03a8c87590ab0c67c0fe9fd1`
and `7fff1a8caaae77439b8526157834238bbbd1d8448112517f7093718bf4a61ea3`,
respectively. Native qtest passed with the experiment disabled and
enabled. It checked startup at zero, switching in both directions,
idempotent writes, rejection of guest MMIO writes to the status word
and clearing status on reset. Both native eMMC regressions also passed.
The 39 status and documentation checks passed after regeneration.
The Windows discovery run executed 1,239 tests and reported 146 errors
and 39 skips, matching E0579's counts.

The captured-input replay used a disposable overlay, `ULOGIO=`,
`USB_VBUS_TEST=1`, `-icount shift=0,sleep=on -net none -no-reboot`
and the updated FUSB/DWC3 observer. Once the firmware emitted
`Created HeapAllocator:`, QMP set `usb-vbus` to true, repeated true,
then set false. The original interrupt handler ran once for each
transition and read the values 1 and 0 in order. The duplicate request
produced no additional handler visit over its observation window. QEMU
quit via QMP with exit 0. The trace shows DWC3 reset at startup and
no later EP0 register programming in this bounded window.

The first replay captured the attach read but the observer missed the
line because QEMU interleaved it with serial output. The retained retry
matches the trace marker within each line. An earlier HMP attempt to
read the GIC distributor directly lost its monitor connection. The
static descriptor and the two original handler visits establish the
guest trigger contract.

A separate bounded instruction trace repeated both transitions and
QMP shutdown. For each transition the guest executed
`fusb_interrupt_usb_vbus` (`0x602AD290`), `fusb_notify_vbus`
(`0x602AD230`), `fusb_signal_event_interrupt` (`0x602AD15C`),
`fusb_signal_event` (`0x602ACF10`) and `event_vbus_exec`
(`0x602B6288`). The monitored host start, gadget start, and DWC3
pull-up entries did not run during the attach/detach window.
The event consumer's original code checks the port table, records the
per-port state byte at `0x602B6300`, then looks for registered callbacks
at `0x602B631C–0x602B6374`. Which branch and callbacks the replay used
remain to be measured.

## Reproduction and next boundary

Receipts are under `artifacts/qemu-a6400/e0580-vbus-address/`, including
`resource-words.txt`, `vbus-id-descriptors.txt`, `udif-request-irq.txt`,
`build-commands.txt`, `qtest.log`, `vbus-console.log`, and
`vbus-report.json`. The retained `build.py`, `qtest-vbus.py`, and
`replay-vbus.py` use the existing Linux QEMU compile plan and the
verified Linux-local `inputs-moddev-1` copy. Build the updated observer
as described in [the QEMU workflow](../../qemu-a6400/README.md).

The follow-up receipts are under `artifacts/qemu-a6400/e0580-vbus-role/`,
including the instrumented PC list, disassembly and replay command.
Next, trace the port-table decision and registered callbacks in
`event_vbus_exec` through gadget start, then qualify the DWC3 event
ring and EP0 transfer requests.
