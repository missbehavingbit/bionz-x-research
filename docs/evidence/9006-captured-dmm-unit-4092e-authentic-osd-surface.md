# E9006 - Captured DMM unit 0x4092e is OSD A/B; an opt-in mapping opens the authentic 640x480 surface

Date: 2026-11-21. Offline process and static analysis only; no camera access.

ID: E9006
Claim: The preserved nflasha5 DMM unit table contains two records for unit
0x4092e (indices 28 and 32). Their extents are exactly OSD A/B: parent
0x20200000 plus 0x3880000 and 0x3916000, size 0x96000, extent id 0x62. The
handles follow the captured MakeHandle rule (0x400 | record index), giving
0x041C and 0x0420. A new opt-in GRM policy, captured-e0044-dmm, answers the
opcode-1 open for 0x4092e with the first captured record. On resume from a
checkpoint built under that policy, the open returns (0, 0x23A80000, 0x96000,
0x041C), the app allocates the full 0x96000 from that handle, maps /dev/stream
at 0x1BCD0000 to physical 0x23A80000 with length 0x96000, writes the whole
614,400-byte surface once, loads viewUnified2.so, and then holds the 20 ms
timer wait. The default and captured-e0044 policies still refuse the unit.
Status: verified for the captured record values, the open reply, the allocation,
the mapping and the full-surface write. The surface is zero at 200M, so the
write is a clear/black frame; UI content and the draw trigger are not established.

Firmware hash / camera configuration:
- libObj.so 60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1
- kmod/dmm.ko c5cb62579f4719bed9248b8e3dabe4d95c6fa57481e07908f10b68098e54a74f
- setting/DmmConfig.bin 3e68b4f482ba24fceea1b572c8e2c5ee132f3fdd93ade64e634df47448bd3abd
- WBI memory image nflasha5 19a702b030306a32f4287131da314e5c89d66163997f0f9a23b0967981b0001b
- Target: ILCE-6400 V2.00 (CXD90045), im.elf debug s g r u l i a lazy-global.
- Checkpoint gemini-runs/pre-malloc-dmm-osd.ckpt
  55d1e85928ed49dec0ff19d170dcae08ee0a97a8f64ff3cb40765d3bb96b89f8
  (141,980,014 instructions, captured-e0044-dmm).
All inputs are hash-checked against artifacts/inventory/baseline.json.

Source:
- tools/research/wbi_dmm_handle.py: captured-text locator, VMALLOC alias and
  unit-table pointer discovery (table = 0x5f1709e0 in this image).
- Captured records 28 and 32 at table + index * 336.
- src/a6400/execution/grm_service.py: new captured-e0044-dmm policy.
- src/a6400/execution/guest_display.py: camera-e0074-e0044 display contract
  (OSD A/B 0x23A80000/0x23B16000, 640x480 RGBA4444, 614400 bytes).
- Reports: gemini-runs/boot-30-osd-open.json, boot-31-display-contract.json,
  resume-10-uipc-4092e-trace.json.

Method:
- Read the captured unit table with the existing PackageMemory/translate/read_virtual
  helpers (read-only) and print records for 0x4092e and 0x280.
- Add the opt-in policy and unit tests.
- Fresh boot under captured-e0044-dmm with --stop-instruction 141980000 and
  --save-checkpoint, then resume with --uipc-timer-service emulated.
- Repeat with --display-contract camera-e0074-e0044 to count surface writes.

Observation:
1. Captured unit table, calibration unit 0x280 record 23: extent
   [0x28, 0x20200000, 0xe0000, 0x2000000, 0x1000001, ..., 0x35d, ...];
   base 0x20200000 + 0xe0000 = 0x202E0000, size 0x2000000, extent id 0x35d,
   handle 0x0417. This matches E0035 and E0047.
2. Captured unit 0x4092e record 28: extent
   [0x2b, 0x20200000, 0x3880000, 0x96000, 0x1000001, ..., 0x62, ...];
   base 0x23A80000, size 0x96000, extent id 0x62, handle 0x041C.
   Record 32: extent base 0x20200000 + 0x3916000 = 0x23B16000, same size and
   extent id, handle 0x0420. These are OSD A and OSD B.
3. On resume, the opcode-1 open at instruction 195,754,532 replies
   (0, 0x23A80000, 0x96000, 0x041C) and is labelled
   "captured DMM unit 0x4092e extent 0x23a80000 size 614400 (record 28)".
4. At instruction 195,763,557 the app allocates size 0x96000 from handle 0x041C
   and receives 0x23A80000. One allocation from 0x041C appears in the trace;
   the other 82 allocations use gl_common 0x68a96220.
5. The /dev/stream mmap is VA 0x1BCD0000 -> physical 0x23A80000, length 0x96000.
6. With the camera-e0074-e0044 display contract, OSD A is mapped and records
   153,600 four-byte writes (614,400 bytes, the full surface) from TID 1087
   between instructions 195,765,033 and 196,125,861, PC 0x5025FA40..0x5025FA58 . OSD B is
   not mapped. The surface reads as all zero at 200M and is unchanged at 205.5M.
7. viewUnified2.so is in the process mappings. TID 1087 is runnable at
   300,038,329 instructions; the 20 ms wait continues with no new module,
   thread or descriptor.

Alternative explanations:
- The 153,600 writes are a clear to black, not a rendered UI: the surface is
  zero at 200M, so this is the most likely reading.
- The app could allocate a new unit rather than reuse the captured record:
  the opt-in policy deliberately answers with the first captured instance,
  which is an explicit approximation of the allocator's choice, not a captured
  allocation sequence.
- viewUnified2 is loaded because CreateFramebuffer now passes; it also loads
  under the legacy gl-common-only policy, so it is not unique to this mapping.

Confidence and limitations:
- High confidence in the captured record values, the open reply, the allocation,
  the stream mapping and the full-surface write; each is read directly.
- The captured table is a preserved memory image, not a live camera state. The
  two 0x4092e records are OSD A/B; selecting record 28 is explicit.
- UI content and the draw trigger are not established. The app is idle in the
  20 ms wait with a zeroed surface.

Dependent implementation/scenarios:
- New opt-in policy captured-e0044-dmm; default and captured-e0044 still refuse
  unit 0x4092e. Tests cover the reply, allocation and transaction label.
- Next contract: whatever event or display completion makes Appframework draw a
  non-blank frame or advance beyond the 20 ms wait.

Next experiment:
- Identify the draw/display event (input, display completion or service reply)
  and supply it as an explicit opt-in contract; then extract the OSD A frame.

Relation to the mainline policy:
- The trace used an opt-in ``captured-e0044-dmm`` policy in the investigating
  checkout. The mainline tree independently implements the same ``0x4092e`` ->
  OSD A/B mapping as ``captured-wbi-dmm``, whose test opens the unit twice and
  selects OSD A then OSD B. This record is therefore independent confirmation of
  that mapping, not a new mode; the captured table values and the 20 ms /
  black-frame observations do not depend on the policy name.
