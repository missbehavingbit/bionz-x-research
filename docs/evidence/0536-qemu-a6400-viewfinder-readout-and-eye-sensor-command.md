# E0536 — QEMU `sony-a6400`: console 0 is the viewfinder readout

Date: 2026-11-16. **Strictly offline.** Builds on E0531 and E0535.

Labels: [C] confirmed by a run receipt or disassembly, [I] inference, [A] assumption.

## 0. Short answer

The 640×480 frame on graphic console 0 is the viewfinder readout (program mark `P`, battery `100%`, exposure scale, black centre), compared by eye with a photo of the real rear LCD [C]. The a5000 host viewer stretches 640×480 to 853×480 for the rear panel only (`tools/panelview.py`); that stretch is the rear LCD, not the viewfinder, so this console stays 640×480 [C].

The switch lives in `/usr/lib/libObj.so`. `SequenceDetectEyeSensorCmd` returns 0 only when the message is `0x6109` / `0x11001` and the payload word `eyesensor_on` is 0. The caller then applies the output device only on that return [C]. A ulog taken at `SetContentTarget` contains neither `eyesensor_on` nor `output_device_sensor_on`. Those logs are behind a level check, so the missing lines do not prove the command was skipped [C].

## 1. What was measured

`libObj.so` is the ELF at eMMC image offset `0x321c0400` (soname `libObj.so`, ARM ET_DYN, first LOAD vaddr 0). Offsets below are file offsets in that ELF.

| Site | What it does |
| --- | --- |
| `0xee4ee4` `SequenceDetectEyeSensorCmd` | Reads a message. At `0xee4fc2` `r6` must be `0x6109`, at `0xee4fca` `r5` must be `0x11001`, and `r4 = [r7,#0x10]`. `cbz r4` at `0xee4fd2` takes the path that logs `eyesensor_on = [%x]` and returns 0. A non-zero word returns 1 [C]. |
| `0xee54c8` `SequenceAppSetOutputDeviceWithEyeSensorCmd` | Calls the detect command at `0xee553a`. `cmp r4,#0` / `bne` at `0xee55a0` skips the rest when detect returns non-zero. When the following word is also 0 it calls `0xee5350` [C]. |
| `0xee5350` | Requires message `0x6109` and sub-id `0x11000` (or pool value `0xa1003`), reads parameter `0x1001`, and posts `0x3400` / `0x2705` / `0x2706` [C]. |

The log calls go through `0x1d2e4c` and are skipped when that returns ≤ 0 [C].

Guest probe `tools/a6400_probe.py --until SetContentTarget` (default `run.sh`, `-icount shift=0,sleep=on`): shell 4.4 s, checkpoint 51.8 s, status 0. Ulog `pmemsave 0x03085000 4685824 e0542-ulog.bin`, deleted after the scan. No `eyesensor`, `EyeSensor`, `output_device`, `EVF_DD_EN`, or `Finder`. `EVF ON` sits in the same name list as `ExtEvf` and `PANEL ON`, so it is a catalog entry, not a stored mode [C].

A menu resource in the eMMC image (offset `0x2e375b9b`) names `Finder/Monitor`, `Monitor(Manual)`, and `Sel. Finder/Monitor` [C]. `Backup.bin` has no eye-sensor name string [C].

## 2. What this does not show

No GPIO or ADC read of the body infrared sensor was found in this command. The value arrives already stored in the message [C]. `OUTSIDE_EVF_EYE_SENSOR_READ` in the same library is the external-EVF accessory, not the built-in sensor [C]. The pin is not identified. Nothing in the device model was changed, and the 853-wide stretch was not applied.

The centre of the rear LCD is still the older gap: VPH `0x144` bit0 stays clear (E0535). Selecting the monitor skin does not start the imager.

## 3. Next boundary

Who posts message `0x6109` / `0x11001`, and which byte it copies into `eyesensor_on`. Until that word is 0, or the Finder/Monitor setting is the monitor, the firmware keeps drawing the viewfinder readout into the only framebuffer console 0 scans.
