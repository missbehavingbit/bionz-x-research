# E9005 - REA deep analysis is unavailable for the ARM libObj on this host; the 20 ms constant has six libObj sites

Date: 2026-11-21. Offline static analysis only; no camera access and no firmware
modification.

ID: E9005
Claim: On this Windows host, REA cannot deep-analyze the A6400 ARM ELF: Ghidra P0
accepts native PE or raw 6502 only, Hopper is macOS-only, IDA and Kuna are not
configured, and the Rizin path returns ERR_REA_WINDOWS_NATIVE. REA still opens
the read-only file and confirms its SHA-256, but provides no pseudocode or
cross-references. A direct capstone/symbol pass instead finds six Thumb
"movw #0x4e20" (20,000 us) sites in libObj.so. Only libObj+0x862CB4 was observed
executing in the 200-202M window (two hits); the loop rate ramps up around 203M,
so the steady-state construction site and the polled condition remain open.
Status: verified for the provider availability and the six sites; open for the
steady-state site and the wait condition.

Firmware hash / camera configuration:
- libObj.so 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1
- Target: ILCE-6400 V2.00 (CXD90045), im.elf debug s g r u l i a lazy-global.

Source:
- REA open_binary on the read-only libObj.so (same SHA-256).
- REA provider diagnostics for Ghidra, Hopper, IDA, Kuna and Rizin.
- Thumb MOVW T3 decoding of libObj.so and dynsym neighbours at each site.

Method:
- Open libObj.so with REA and request binary_overview; then attempt
  execute_rizin_command with aa and axt at the 20 ms helper.
- Scan every halfword for MOVW with imm16 = 0x4e20 (six hits), then run the
  emulator to 202,016,386 instructions with a single-address code hook on each
  runtime address and count post-200M hits.

Observation:
1. REA open_binary accepted the file and reported SHA-256 60ffd2b0... and
   architecture arm. binary_overview then failed with provider_unavailable:
   Ghidra P0 rejects ELF/arm on win32, Hopper is unsupported on win32, and IDA
   and Kuna are not configured.
2. execute_rizin_command failed with capability_unavailable and reason
   ERR_REA_WINDOWS_NATIVE, so REA cannot supply ARM disassembly or xrefs here.
3. Six MOVW #0x4e20 sites exist in libObj: 0x3d00dc and 0x3d0134 (InputService
   region), 0x85eb72 (infra_indctr/InfraInput region), 0x862cb4 (InfraInput
   region), 0x928bee and 0x929a5e (unnamed region before ObjPlayer_Create).
4. A 202,016,386-instruction run with one hook per site recorded two hits at
   0x862cb4 and none at the other five. The timer observer shows the 20 ms arm
   rate rising to 40 per 1M only from about 203M, after this window, so this
   count does not identify the steady-state site.

Alternative explanations:
- The steady-state timer could come from another module (libosal_uipc or
  libmpr) that also builds a 20 ms deadline; only libObj was scanned.
- Two hits may be startup calls at CreateFramebuffer, not the wait loop.

Confidence and limitations:
- High confidence in the provider diagnostics and in the six libObj sites.
- The steady-state construction site and the polled flag are not identified.
- This record is about tool availability and static candidates, not a new
  runtime claim about the UI.

Next experiment:
- Run REA on a Linux or macOS host, or configure IDA/Kuna, to get ARM pseudocode
  and xrefs; otherwise use a frame-pointer unwind from the timer wrapper.
