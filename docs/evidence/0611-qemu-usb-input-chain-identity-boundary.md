# E0611 — USB attach-chain discovery and captured-library identity boundary

Date: 2026-11-19. Scope: start Phase 1 of the
[USB attach-chain plan](../plans/qemu-usb-attach-chain-plan.md), offline only.
No physical camera, USB device, SD, persistent setting or firmware input was modified.

## Result

Two bounded QEMU 11.1.3 observations, with `LIVEVIEW_SOURCE=none` and `bars`,
reach a visually verified original P shooting display after `send-key ret`.
Both observe FUSB handler `0x602AD2AC` reading `0xF2920010=3` on attach and
`=2` on detach. Repeating attach while already attached produces no extra
handler row. Both owned QEMU processes exit 0 on QMP quit.
The bars screenshot contains the source chart beneath original firmware OSD.
This is not proof of stock USB mode selection or enumeration.

The initial function probes were generated from packaged libraries, following
E0589. Qualification against the saved captured filesystem finds that **both
libObj and viewUnified2 have different original-code sections**. Therefore the
logged zero counts at historical offsets cannot establish named function
absence, dropped ORIL messages, or the responsible gate. Phase 1 remains open;
no new hardware response or firmware workaround was implemented.

## Input and tool identities

Full identities and commands are retained in
`artifacts/qemu-a6400/e0611-usb-input-chain/`:
`input-discovery.json`, `library-qualification.json`, both `vbus-report.json`
receipts, generated `fsm-trace.c`, scripts and console logs.

| Input | SHA-256 |
| --- | --- |
| Prepared kernel | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` |
| Captured av-cam | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| Prepared Backup.bin | `13345a908433d7130adb382ee01a951ac8e691d091defd666d4d8a8af081232d` |
| Saved nflasha15 filesystem | `70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064` |
| QEMU executable | `e4fa5e6814b7522309c77843a87ec1ecbd9cedda98a99b27d80a0dcd4d9e6104` |

`qualify-libraries.py` uses `debugfs -R 'dump /lib/NAME NAME'` without `-w`,
checking the saved filesystem hash before and after. Complete library and
`.text` hashes are in its receipt. Captured libUsbControl `.text` matches the
packaged code, independently agreeing with E0596. Captured libObj and
viewUnified2 `.text` do not match. Input files were not silently rehashed into
an existing allowlist.

Captured libObj dynamic symbols identify these entry coordinates as reported
by `arm-linux-gnueabi-nm -D -C` (not runtime process addresses):

| Function | Captured value | Plan value |
| --- | --- | --- |
| snd_InfraInputEvent_to_InputServant | `0x3CF664` | `0x3CF6DC` |
| InputService::callback_direct | `0x3D2C8E` | `0x3D2D06` |
| InputService::createEvent | `0x3CF8D2` | `0x3CF94A` |
| InputService::getInputEventStatus(ParamList*, ParamList const*) | `0x3D5644` | `0x3D56BC` |

These are symbol observations, not verified IPC execution or permission to
translate all view offsets by a common delta.

## Policies, observations and original instruction support

`replay-vbus.py` uses verified prepared inputs, fresh disposable overlays,
`REAR_CONTROLS=1`, `USB_VBUS_TEST=1`, `USB_CID_HIGH=1`, `EVSEL_LATE_US=500`,
`ULOGIO=''`, `-icount shift=0,sleep=on -net none -no-reboot`, and a passive
TCG plugin. It bounds observation to 240 seconds, sends only a normal rear
center key and QMP VBUS input transitions, reads guest process maps and
captures the actual LCD. It does not send StartGadget or manufacture input
messages. Neither diagnostic USB-service mod nor DWC3 start-gate policy is enabled.

Both correlations retain maps and instruction-signature matches. Historical
libObj offsets log zero selected rows; historical view offsets log one each
at `2AC00A`, `2AC044`, `2B271A`, and zero at `2ACBB0`, `2ADFB8`, `2A524C`.
The qualified unchanged UsbControl code logs seven SetJackStatus entries and
zero StartGadget entries per run. Logs cap user rows at 128 per CPU/site;
counts are logged rows, not generally uncapped totals. The differing view
and libObj code prevents interpreting their historical labels as verified
captured functions. Return values and the ORIL receive handler remain unobserved.

The existing checksum-validating BK4 parser reads key `0x0107001F`: attribute
64, size 1, descriptor offset 141388, data offset 778118, payload `04`.
Interpreting 4 as PC Remote follows E0589's packaged dispatch analysis; this
is not a runtime selection observation. Smartphone-control state is unknown.

`oril-candidates.py` pins the captured raw hash and scans Thumb16 PC-relative
literal loads within 1 KiB before aligned `0x007A0020` literals. It retains
28 candidates and bounded original-instruction listings (64 bytes per candidate).
These are discovery candidates, not verified receive handlers. Thumb32/ARM
references and code/data classification remain open. Mere `0x07000001` byte
presence, especially unaligned, does not establish a USB dispatch branch.

## Failures and verification

- Initial plugin compilation used the obsolete header location, then exposed
  QEMU 11 callback/API changes. Preparation now uses the installed plugin
  headers, QEMU 11 callback signatures and boolean register-read result plus
  an explicit four-byte length check. No build succeeded under the old API.
- `failed-01/` and `failed-02/` preserve console/report receipts for map-marker
  recognition failures before USB toggles. Kernel logging interleaves with
  serial output; the corrected observer requires an actual libObj mapping
  and the completion token. These failures are not firmware USB failures.
- Guest `sha256sum` is unavailable (retained in the bars console); captured
  library qualification instead uses the pinned saved filesystem read-only.
- Windows Pillow failed to load its imaging DLL; WSL has no Pillow. The PPM
  screenshots were converted to PNG with Python stdlib and visually inspected.
- All research scripts pass Python syntax compilation. Two QEMU observations
  complete with expected VBUS edge assertions. This is not completed Phase 1
  verification or a hardware contract change; the emulator/full test suite
  was not rerun for artifact-only research additions.

## Reproduction and next boundary

From the repository root in WSL, `PYTHONPATH=.`:

```bash
D=artifacts/qemu-a6400/e0611-usb-input-chain
python3 "$D/inspect-inputs.py"
python3 "$D/oril-candidates.py"
# qualify-libraries.py requires a fresh output directory; do not overwrite receipts.
python3 "$D/prepare.py"
gcc -shared -fPIC -O2 -I"$HOME/sony-a6400-qemu-11.1.3/include/plugins" $(pkg-config --cflags glib-2.0) "$D/fsm-trace.c" -o "$D/vbus-trace.so" $(pkg-config --libs glib-2.0)
# Use a new copied receipt directory for replays; preserve the existing outputs.
python3 "$D/replay-vbus.py"
python3 "$D/correlate.py"
LIVEVIEW_SOURCE=bars E0611_RUN_LABEL=bars python3 "$D/replay-vbus.py"
E0611_RUN_LABEL=bars python3 "$D/correlate.py"
```

Next: use captured library identities to resolve view entries and original
ORIL receive/forward instructions, rebuild exact probes including returns,
and repeat both policies. Only then choose the missing-hardware branch.
E0589's assertion that readiness explains the entire stop is not established
by these receipts; retain it as a hypothesis pending that measurement.
