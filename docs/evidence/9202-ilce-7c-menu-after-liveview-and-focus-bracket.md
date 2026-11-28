# E9202 — ILCE-7C QEMU: MENU after live view, S-Ele panel crash, black-LCD cause, and FocusBracket.mod

- **Date:** 2026-11-27
- **Scope:** Sony ILCE-7C (`sony-a7c`) QEMU machine in `qemu-a7c`. Covers sub-micro front keys (`CAkey1`), the av-cam panel-refresh exception, the eMMC overlay state behind the black LCD, the moddev install timing, the post-boot still-capture handshake port, and the A7C `FocusBracket.mod` (SMOD v2).
- **Code:**
  - [qemu-a7c/qemu-overlay/hw/arm/sony_a7c.c](../../qemu-a7c/qemu-overlay/hw/arm/sony_a7c.c)
  - [qemu-a7c/qemu-overlay/hw/display/cxd90014_vph.c](../../qemu-a7c/qemu-overlay/hw/display/cxd90014_vph.c)
  - [qemu-a7c/tools/moddev.py](../../qemu-a7c/tools/moddev.py)
  - [qemu-a7c/tools/a7c_panel.py](../../qemu-a7c/tools/a7c_panel.py)
  - [mods/focus_bracket_a7c/focus_bracket.c](../../mods/focus_bracket_a7c/focus_bracket.c)
- **Builds on:** [E9201](9201-ilce-7c-qemu-and-mlv2-port.md) (board, hostwin, mlv2 `--target a7c`).

## Question

E9201 left two A7C QEMU boundaries open:

1. `MENU` and `PLAY` stop working once live view runs.
2. Focus bracketing has no implementation.

What blocks them, and what can be shown in QEMU?

## Results

### 1. `MENU`/`PLAY` after live view — cause located, emulator workaround verified in QEMU

- **Front keys.** `MENU` and `PLAY` are `CAkey1` bits 2 and 1. They arrive only in sub-micro slot-A frames (TX type `0x21`, RX byte 28; byte 29 is active low).
- **Slot A stops at live-view start.** Until live view starts (guest about 65 s), av-cam alternates slot A and slot B. From then on it exchanges only slot B (`0x31`), about 40 times per second. Measured with `A7C_SM_XLOG=1`: every 128-byte frame is `31 …`, and no `21 …` frame follows.
- **Two values change at the same moment:**
  - The link state word at `0x6427E2CC` goes `0x100` → `0x118`. The writer is `strh r6,[r3,#36]` at `0x6376E716`.
  - TX TLV `0x03` data[4] becomes `3`. The routine at `0x6376C060..` copies it from a camera state message at `msg+0x578`. In the user's live-memory dump of the physical camera (2026-11-17) the same byte is `0`.
- **What does not restart slot A:**
  - periodic pulses on P0.9, P1.2 or P2.5;
  - a correct lens-rail reply.

  A non-zero 8-byte header reply crashes the guest.
- **The key path is intact.** `input.ko` reads front keys from av-cam through the `front_key` UIPC request. With `MENU` held before live view:
  - `echo front > /proc/udm/input` shows `CAkey1:00000605`;
  - `echo get 0x1ab > /proc/udm/input` reports the `MENU` code pressed.

  After slot A stops, av-cam keeps answering from stale copies.
- **Copies found.** Guest RAM was diffed with `PLAY` held and released. Besides the RX buffer `0x034433A4+28`, av-cam keeps slot-A key bytes at PA `0x04447B30`, `0x044E90C2`, `0x0454B51B`, `0x04613544` and `0x04613674`.
- **Emulator workaround.** `A7C_CAKEY_POKE=1` (default on in `run.sh`) writes `CAkey1` into all six places on every key change.
- **Verified in QEMU (screenshots at guest ≈ 90 s, after live-view start):**
  - `MENU` opens the stock menu, closes it and opens it again;
  - `Fn` opens the quick navigation.
- **Boundary.** This is an emulator-side write into av-cam data at fixed physical addresses. It is valid for this firmware build only. The real trigger that makes av-cam fetch slot A in live view is **not** known yet.

### 2. `FW: S-Ele Refresh ng force exception` — cause located

- **What happens.** With `A7C_LIVEVIEW=0` there is no CamCore model. av-cam logs `CamCore ParamSetError`, then `CamCore Start Error` again and again. It requests a panel refresh each time.
  - The refresh flag at `0x64547231` is set by `0x6393CB0C`.
  - That function is called from `0x6393CE30` ("Lost CAMOUT_V"), `0x63947120`, `0x63948A96` and `0x639492EE`.
  - The calls were confirmed with `-d exec` and `-dfilter` on the four sites.
- **The crash.** After more than 10 refreshes, `0x6393C808` writes to address 0 (`str r7,[r7]`) on purpose. That kernel oops kills the IRQ-101 thread (`liro-kliro_101`), and OSD flips stop for good.
- **Workaround.** `A7C_LIVEVIEW=1` (the default) gives a single `Start Error`, and the oops does not happen.
- **Boundary.** This is unrelated to the a6400A slot-C offset fix in E0715: the A7C exchanges 128-byte frames only.

### 3. Black LCD with the mod loader — persisted eMMC overlay state, not the loader

- **Symptom.** Live view stops after 4–7 CamPig frames (`en50=0`). VPH ch53 then stays on the empty OSD buffer `0x22AB2000`, the LCD is black, and `MENU` appears dead.
- **Not the cause:**
  - the loader;
  - the hostwin window;
  - pushes;
  - the guard.

  A boot with no loader at all is black with the same `out/emmc.qcow2`.
- **The cause.** The same boot with `A7C_FRESH=1` (a new overlay made from the user's own `img/a7c_emmc.img`) shows the shooting screen. The overlay persists camera settings between runs, and a setting saved during earlier sessions reproduces the stall. Which setting it is has not been determined.
- **moddev fix.** The "wait for live view" check never waited, because the `/proc/uptime` parse always fell through to the default. It now waits before **any** push until uptime ≥ `A7C_MOD_INSTALL_AFTER` (default 120 s).
- **hostwin.** Guard (525,404 B), request, `core.bin` and a mod arrive with `fnv ok`.

### 4. Lens reply moved to slot B

`a7c_submicro_lens_reply()` was applied to slot A, where it never matched anything. It now applies to slot B, using the a6400 semantics: RX TLV `0x03` data[3] bit0 follows TX TLV `0x0a` data[1] bit0. This alone does not restore slot A.

### 5. Post-boot still-capture handshake (opt-in, built, not run)

`A7C_STILL_CAPTURE=1` ports the a6400 sub-micro CAIF capture frames (E0715). The micro side answers the CPU like this:

| CPU line | Micro reply |
| --- | --- |
| S2REQ P7.15, falling edge | (3,0) `CAP_START`, 20 B |
| P5.29 / P5.30 | two exposure pulses on P1.2, then (3,3) `START_END`, 10 B |
| P5.23 / P5.24 | (3,5) `CAP_DATA`, 1544 B, and (3,12) `EBC`, 14 B |

- **Message transport.** Each message raises P2.5, pulses the strobe, then sends an 8-byte header and a zero payload.
- **Pin assumption.** The pins are assumed equal to the a6400 `[?]`.
- **Not ported.** JPEG/RAW encode (`cxd90045_jpeg.c`, RCRAW IDT `0xF3030000`).

### 6. `FocusBracket.mod` for the ILCE-7C (built, not run)

SMOD v2 mod for the Mods tab, with six SMNU items:

| Item | Values |
| --- | --- |
| Mode | Landscape (step 8, 5 shots) / Macro (step 2, 40 shots, 1 s interval) / Custom |
| Step Width | 1–10 |
| Shots | 5–100 |
| Order | `0 → +` / `0 → − → +` |
| Exp Smooth | Off / On |
| Interval | Shortest – 10 s |

**libObj.so entry points** (reference base `0x20000000`, 16 RLC1 relocations):

| Purpose | Entry point |
| --- | --- |
| Focus step | `MSGID_SYNC_STEP_FOCUS_CMD` through `MWF::ObjIf::IssueCommandSync` (`0x10b2a4`) on `ObjCamera` (`get_obj_handle(8)`, `0x34d60d`), message `(0x3100, 0x6530)`, `PARAMID_STEP_FOCUS_DIR 0x9c40` = `0x68c` near / `0x68d` far |
| Shutter | `EventManager::push` S2 press/release `0x15010032` / `0x15010031` |
| Save sync | hook on `WriteStillComp` (`0x902548`), which counts saved stills |

- **Hook fix.** The `WriteStillComp` trampoline replays the overwritten prologue `0x902548..0x902557` and resumes at `0x902558`. The first build missed `add r7, sp, #16` (`0x902556`); fixed in this record.
- **Boundaries.**
  - Not run in QEMU: no still capture completes yet, and the A7C machine has no lens model, so focus steps have nothing to drive.
  - Not run on a camera.
  - The series starts when the item is switched on, not on a shutter press.
  - Stock ILCE-7C `viewUnified2.so` has no `BracketFocus` node, so the mod lives in the Mods tab.

## Reproduce

```sh
cd qemu-a7c
python3 tools/a7c_panel.py --card none --mod out/mlv2-a7c/FocusBracket.mod \
  --env A7C_FRESH=1 --env A7C_STILL_CAPTURE=1 --env A7C_CAIF_LOG=1
# after "installed": MENU (works after live view), Mods tab -> Focus Bracket
```

## Remaining boundaries

- **Slot A in live view.** The trigger that makes av-cam fetch slot A during live view is unknown, so `A7C_CAKEY_POKE` is still needed.
- **Black-LCD setting.** Which saved eMMC setting stalls live view has not been determined.
- **Capture and focus.** Still capture to card (JPEG/RAW encode), the E-mount lens model and the FocusBracket run are all open.
