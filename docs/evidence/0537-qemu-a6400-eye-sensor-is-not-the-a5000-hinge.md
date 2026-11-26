# E0537 — QEMU `sony-a6400`: the eye sensor is not the a5000 hinge pin

Date: 2026-11-16. **Strictly offline.** Builds on E0536.

Labels: [C] confirmed by a run receipt, a binary, or disassembly, [I] inference, [A] assumption.

## 0. Short answer

The guest key table has no eye-sensor line, and a6400 `input.ko` does not contain keycode `0x70A6` [C]. On the a5000 that code is `PANEL_ARM_DET`, GPIO P5.2, the LCD hinge on a body with no viewfinder. It is not the a6400 infrared sensor. No GPIO was added.

## 1. What was measured

`/proc/udm/input` at `SetContentTarget` (`/home/administrator/a6400-runs/e0540-input.log`) lists ADC ports `c`, `f`, `0x10`, `0x11`; GPIO P0.3 `70fd`, P0.4 `70fe`, P0.7 `70dc`, P0.10 `1f0`, P0.11 `70c6`, P1.1 `70ef`, P9.22 `4c`/`4b`; front CAkey1 bits 0, 1, 2, 5, 9. The VGRIP section is empty. No `70a6`, no eye, no finder [C].

The module that contains `key table size check error` is the ELF at eMMC offset `0x31167c00`. The following 512 KiB contain no halfword `0x70A6` [C].

`/usr/lib/libObj.so` (ELF at eMMC `0x321c0400`) has the catalog names `KEY_OTHER_EYE_CONTACT`, `KEY_SLI_FINDER_LCD`, `KEY_TACT_PANEL_EVF`, `KEY_EXTEVF_EYE_CONTACT`, and `KEY_PANEL_ARM_DET`. No absolute word and no PC-relative load in that ELF points at those strings [C]. `KEY_EXTEVF_EYE_CONTACT` is the external viewfinder. The other three names are the body catalog; this boot's input table does not emit them [C].

Message ids `0x6109` and `0x11001` are not unique to the eye command. `libObj.so` has 13 `MOVW #0x6109` sites and 32 executable copies of the word `0x11001`, most of them comparisons in other commands [C]. The eye command is the pair together with parameter word `0x1000` (E0536).

## 2. What this does not show

The saved Finder/Monitor value is still not located. The resource strings `STRID_FUNC_FINDER_MONITOR`, `STRID_FUNC_FINDER_MONITOR_MANUAL_FINDER`, and `STRID_FUNC_FINDER_MONITOR_MANUAL_MONITOR` are in the eMMC image at `0x2e5fcdd8` [C]. `image_EVF.uxc` and `image_DSC_Only_169.uxc` are named together at `0x399e53b0` [C]. Neither site is a code reference that shows which value is selected.

## 3. Next boundary

Which backup property or view-library flag selects `image_DSC_Only_169.uxc` instead of `image_EVF.uxc`. The infrared pin stays unidentified, and P5.2 stays out of the model.
