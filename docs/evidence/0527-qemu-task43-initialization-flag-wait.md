# E0527 - QEMU: Task 43 waits for the first display event on flag 10

Date: 2026-11-17. No camera command, USB operation, firmware patch or injected
completion. Labels: [O] stopped QEMU observation, [S] original-instruction
support, [I] inference, [A] retained machine assumption.

## Result

**The qualified display-startup blocker is LIRO Task 43, still inside
`0x63A67D80`, waiting on event flag 10 at `0x645248DC`:**

```text
twai_flg(10, 0x3, TWF_ORW, result_pointer, -1)
observed flag pattern = 0
original call = 0x63A67DBE; saved Thumb return = 0x63A67DC3
```

This is the **first display-initialization wait**, not the later main-loop
wait with mask `0x3373`. The saved kernel locals, object linkage and original
caller all agree [O/S]. Reading only the task entry's pseudocode would assign
the wrong mask and phase.

Two independent 180-second host observations reproduce these values. IRQs
101/102 are enabled but have **zero input assertions** in both native GIC
traces [O]. Their registered original handlers have conditional paths setting
flag-10 bits 1/2 [S]. The missing display-event producer is therefore a concrete
next hardware boundary [I], not permission to signal the flag or invent a timer.

Task 65 is dormant (`0x10`, zero queued activations), not sleeping in a
demonstrated sub-micro transaction [O]. Task 1 has a separate semaphore wait.
**No sole global deadlock, cleared startup stall, GUI or completed camera boot
is claimed.**

## Input and tool identities

These are the **captured** a6400 inputs, not E0521's mixed packaged input set.
Do not transfer these coordinates to packaged av-cam `90d515cf...`.

| Input/tool | SHA-256 or identity |
| --- | --- |
| Captured kernel | `d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618` |
| Captured initrd | `db95861f6c56418a216c4805f1f76f2cf2a0e74fcbe0d3350b87cf6296254673` |
| Captured av-cam, 16,803,048 bytes | `dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209` |
| Captured `liro.ko` | `ca5fa9ecc990c2ceda755887563dcd2291fb49f56a4260d895818e7f6782f535` |
| Captured `/usr` partition | `70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064` |
| Prepared eMMC | `18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b` |
| Prepared manifest | `bf12cfa60d64560d6db6327e42e81fd5d81b96c883c90879228ed67364cdde6d` |
| Native QEMU 11.1.3 | `bec5f52b4028acd0961a48b17406470508edb728609abbaf6f65e4176f5425f0` |
| QEMU base commit | `005c61c0ef6f6ae615283440b4bb7be51b2c6ddd` |
| Native checkout `sony_a6400.c` | `1db6ca8f8354c32319a9b36b82d71945d0a9d13dc88c41394fa17496ae1d1e08` |
| Native checkout `cxd4115_timer.c` | `a994c4a6d8110daf7968da3911e450004cbc06eb70171eafeb5138031141e6db` |
| Retained boot observer | `79ae8163d3bdcb1c09780be3e3097646e2d8c3dbdaafd4b65c2604472f6433cc` |
| Passive observation extension | `71af9e17c2e382bcbedae3c21ba994b33887958fdda1c3821ac519260284a870` |
| New offline wait decoder | `74775a03c29f57cd23051bd9d237d3ffe337e62b81adb8092a9af2c84e097cf7` |

The captured-boot observer/launcher comes from retained worktree base
`c29f0324e14c002fddd2fa4c8962add4ce9256b9`. Its source-reference hashes in the
raw reports are **not** identities of the native 11.1.3 compilation inputs;
the actual native identities above are recorded separately in
`artifacts/qemu-a6400/startup-20261117/native-identities.log`.
No later worktree peripheral models were imported into this branch.

The launcher verifies the prepared manifest, preloads the original boot set,
and writes only a new per-run qcow2 overlay. `ULOGIO=` and `SUBMICRO_PAGE=`
disable cross-body logging additions and sub-micro frame injection. The
captured filesystem itself already supplies logging consumers: queue `0x13A`
has zero pending messages and two consumers in these receipts [O]. The older
packaged-image logging-starvation diagnosis is not this captured run.

## Runtime binding and exact wait

All CPUs were paused before the RAM reads. Addresses below are guest virtual
addresses translated by QEMU CPU0, not physical-camera reads.

| Observation | Value in both corrected runs |
| --- | --- |
| Current-run LIRO `.text` / `.bss` | `0x5F4E0000` / `0x5F4E96E0` |
| `tsks` table | `.bss + 0x780 = 0x5F4E9E60` |
| Task 43 entry / control | `0x63D8B805` / `0x6452620C` |
| Task state / wait kind / wait ID | `4` / `8` / `10` |
| Original ARM sleeping return | `.text + 0x3BE8 = 0x5F4E3BE8` |
| Flag object / pattern word | `0x645248DC` / `0x645248E8 = 0` |
| Requested mask / mode / timeout | `3` / `1` (`TWF_ORW`) / `-1` |
| Saved application return | `0x63A67DC3` |
| Task 65 entry / state / queued activations | `0x63D4BA5F` / `0x10` / `0` |
| Task 1 checkpoint at `0x641A96F8` | `10010400 43010000 02000000` |

The checkpoint writer stores line `0x143` and step `2` before Task 1's wait at
`0x6361BB48`. That call loads semaphore `0x00BF04EE`; the earlier semaphore is
`0x00BF04EF` [S]. Its lifecycle role and relation to whole-application progress
remain unproven.

The Task-43 stack bases differ (`0x609C6000` and `0x60A22000`), so the second
result is not a replay of the first stack. The flag's queue points to the
respective saved stack waiter in each run [O].

### Why the decoded arguments are the actual arguments

Original `liro.ko` ARM instructions establish [S]:

- `twai_flg + 0x24` loads its fifth argument from `fp + 4`;
- locals at `fp - 0x3C`, `fp - 0x38`, `fp - 0x34` retain the object, mask and mode;
- control `+0x90` points to the flag's wait-list head at object `+0x10`;
- control `+0x94` points to `.bss + 0xF78 + 8*(flag_id-1)`;
- `ref_flg` reads the current pattern from object `+0x0C`;
- helper `0x3668` implements AND for mode 0 and OR for mode 1.

The Thumb veneer at `0x635DCFF0` pushes six words, copies the original timeout
to its outgoing stack and calls API slot `+0xA4`. Its original caller's return
is consequently at the ARM frame's `fp + 0x18`, not the frame-pointer
unwinder's next application-looking PC [S/O].

The decoder reads these original locals and checks the independent task
linkage. It does not substitute a constant mask from the task entry.

## Original producers and static analysis

Registered IRQ 101/102 records contain Thumb handlers `0x63D8929F` and
`0x63D8935D`, requesting CPU 1 [O]. Conditional calls at `0x63D89304` and
`0x63D8931E` set flag 10 bit 1; `0x63D893C4` sets bit 2 [S].
For example, the first IRQ-101 path tests `0x63A4311C() == 0` and
`0x63D86830(0) == 0` before signalling. IRQ assertion alone is not proof that
the handler's readiness and state predicates permit a wake.

Five bounded Ghidra queries completed in
`artifacts/analysis/qemu-startup-20261117/targeted.json`
(`c647a2994f85d868312506a7cb58c67dde2f8a5a8032a52c68a5ebf7f65b0930`).
They cover `0x63A67D80`, both registered handlers, `0x63A4311C` and the
checkpoint writer `0x6361BCE8`. The original bytes were also checked with
Capstone. Ghidra 12.1.4 / JDK 21 reused the hash-matched retained project in
**read-only**, no-auto-analysis mode, explicitly `ARM:LE:32:v7`, with Thumb
entry contexts. External tail calls remain external; this is not execution.
The existing PyGhidra MCP project was locked; its locks were left untouched.

Prior art consulted: [related repositories](../RELATED_REPOSITORIES.md) and
`Oein/a5000-emulator` commit
`6ad36bd7b0411d55a5920447ff51e3de01f8cb9b`,
[`docs/RE_AVCAM_IRQ_BOOT.md`](https://github.com/Oein/a5000-emulator/blob/6ad36bd7b0411d55a5920447ff51e3de01f8cb9b/docs/RE_AVCAM_IRQ_BOOT.md),
sections 0 and 3. That body's Task-65 hypothesis was not transferred.

## Reproduction and retained receipts

Implementation: [liro_flag_wait.py](../../tools/research/liro_flag_wait.py).
The decoder is offline: it checks source identities, the run's manifest and
console hashes, each RAM page's hash, overlapping observations and saved-frame
bounds. Foreign packaged inputs, missing bytes and incomplete probes fail.

From the repository root in PowerShell, use a new output directory:

```powershell
.\venv\Scripts\python.exe -m tools.research.liro_flag_wait --probe artifacts\qemu-a6400\startup-20261117\startup-20261117-flag-object-d\probe.json --inputs artifacts\qemu-a6400\inputs-pr-preflight-20261116-1555 --liro firmware\captured_a6400_20261113_analysis\kmod\liro.ko --avcam firmware\captured_a6400_20261113_analysis\av-cam.bin --report-dir artifacts\qemu-a6400\NEW-WAIT-DECODE
```

Changing the probe selection to run `e` gives the same phase, object, mask,
mode, timeout and zero pattern. Exact verified outputs remain under
`artifacts/qemu-a6400/startup-20261117/decoded-d-final/` and `decoded-e-final/`.

The local capture extension is retained at
`artifacts/tests/avcam_startup_capture.py`. The exact policy for each fresh run
was `A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu-11.1.3 ULOGIO=
SUBMICRO_PAGE= python3 -u -m artifacts.tests.avcam_startup_capture
--baseline-root /mnt/c/sources/sony-a6400-mode-sequencer-20261117
--inputs <absolute-prepared-input-directory> --output <new-baseline-artifacts-directory>
--seconds 180`, from this repository root inside WSL. It composes the retained
observer, adding only stopped-RAM reads of the linked flag and checkpoint.
It sends `dmesg -n 1`, `/proc/uptime`, `/proc/osal/uipc` and LIRO sysfs section
queries over the emulated serial shell. No task, flag, IRQ or device is written.

| Receipt | SHA-256 |
| --- | --- |
| Run d `probe.json` | `9700061af5e799e847fb5205acf6623d987d1592dd2ebd50a006ec15c28bbe10` |
| Run d `console.log` | `c0448612a32b4ef36b46553073649e5ef469c85cff65bc648fd7ebd5ab9e9f67` |
| Run e `probe.json` | `42f7f20be5ccf4251cf1b5ccf6631ad3f1a1390e4d03b2979385865de15cc732` |
| Run e `console.log` | `81d7895f7e176de59cf12424bcec880214619d3f86a6727fd9067ed9cea025f0` |

Both owned QEMU instances acknowledge QMP stop, report `running=false`, and
exit 0 after QMP quit. Their original receipt directories were archived without
rewriting any reports, private RAM or inputs.

## Validation and failures retained

| Exact command/check | Result |
| --- | --- |
| `.\venv\Scripts\python.exe -m unittest tests.test_liro_flag_wait tests.test_qemu_emmc -v` | PASS: 7 decoder cases, 7 Linux-native checks skipped on Windows |
| WSL `A6400_QEMU_DIR=/home/administrator/sony-a6400-qemu-11.1.3 python3 -m unittest tests.test_qemu_emmc -v` | PASS: all 7 constructed-disk native checks |
| Offline decoder commands for archived runs d/e | PASS twice; exact initialization phase and unsatisfied flag predicate |
| Five bounded original-image Ghidra queries | PASS; external tail calls retained |
| `.\venv\Scripts\python.exe -m unittest discover -s tests -q` | Runner reports OK: 1965 collected, 131 skipped; safety qualification below |
| `.\venv\Scripts\python.exe -m artifacts.tests.avcam_offline_suite` | 1997 collected, 115 skipped, one unrelated `test_heap_ownership_trace` error; physical-source construction denied once before open |
| `.\venv\Scripts\python.exe -m unittest tests.test_heap_ownership_trace.HeapOwnershipTraceTests.test_return_is_thread_stack_and_mode_specific -v` | PASS in isolation; full-suite failure remains recorded |

The worktree changed concurrently, including additional allocator tests.
The later full run's `KeyError: boundary` is outside the new decoder and is not
silently treated as a green full suite. No allocator code was changed here.

The first default full-suite command exposed an existing SD report test opening
the unavailable physical `E:` path before falling back to constructed memory.
The open failed with `ENOENT`; it is **not** an offline-safety qualification.
The repeated suite uses a local validation fixture denying that constructor
before open, without changing unrelated production code or tests. Both logs
are preserved.

Initial 90-second run `a` exceeded the serial deadline. Run `b` completed but
did not include the flag object, so it cannot establish the current pattern.
Run `c` completed serial commands but failed the older observer's line-anchored
sysfs-marker parser because UART output interleaved with command echo. These
receipts remain under `artifacts/qemu-a6400/startup-20261117/`; none is promoted
to a complete flag-object observation.

## Next boundary

Recover the captured image's actual display/VPH register, acknowledgement and
frame-timing producer for enabled IRQs 101/102, respecting the original handler
guards. Then verify that Task 43 returns naturally from `0x63A67DBE` and reaches
its later initialization phases. Do not patch the wait, release Task 1's
semaphore or turn a periodic interrupt into an assumed successful frame.

Retained CPU0-default SPI routing, ignored unmapped MMIO and
`-icount shift=0,sleep=on` remain assumptions [A]. A host observation duration
is not an equivalent amount of guest time. No physical deployment safety or
complete application-startup claim follows from these bounded observations.
