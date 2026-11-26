# E0546 — A later return-window probe restores the stopped QEMU VM

Date: 2026-11-16. **Strictly offline.** Same captured `nflasha1` updater inputs as [E0545](0545-unicorn-qemu-return-window-cpsr-a.md). No camera, USB, disk, or network.

Labels: [C] confirmed by a run receipt.

## 0. Short answer

The first probe boots QEMU and stops at `0x6010DF34`, then saves that stopped VM. The next probe restores the snapshot. It does not start from the reset PC. The sixteen return-window rows match the create run and the earlier E0545 capture [C].

## 1. Why the first restore aborted

GDB leaves the VM in the `debug` runstate. QEMU 10.1 allows `inmigrate` to become `paused` or `running`, and rejects `inmigrate` to `debug`. Restores `thumb-qemu-return-05` and `thumb-qemu-return-06` died with `invalid runstate transition: 'inmigrate' -> 'debug'` (process status -6) [C].

The Sony patch now allows that one transition. The snapshot remains the GDB-stopped VM. A later `stop` is not sent on restore, because `debug` cannot become `paused`.

## 2. Receipts

| Run | Role | Result |
| --- | --- | --- |
| `artifacts/analysis/thumb-qemu-return-07` | Create `artifacts/analysis/thumb-checkpoint-02` | `resumed_from_checkpoint` false, stop `sample_limit` |
| `artifacts/analysis/thumb-qemu-return-08` | Restore that snapshot | `resumed_from_checkpoint` true, migration `completed`, stop `sample_limit` |

`qemu-system-arm` SHA-256 `78da642848ed5ce4ce1762dbfb4212f262663c956d45412607846506a742cfe5`. This is the binary after the runstate transition. E0545's binary hash stays the hash of that earlier run. The snapshot is 83,668,002 bytes. `checkpoint.json` records PC `0x6010DF34` and this binary hash. A snapshot from another binary is rejected.

The restore command uses `-incoming defer` and does not use `-S`. GDB prints `BOUNDARY_RESUME {"pc": 1611718452}` and the log does not contain reset PC `0x00001000` [C].

Rows from `thumb-qemu-return-04`, `thumb-qemu-return-07`, and `thumb-qemu-return-08` are identical. Row 0 is still PC `0x6010DF34`, bytes `f32500eb`, CPSR `0x40000153` [C].

## 3. What this does not change

The CPSR.A difference from E0545 is still present. The snapshot is taken at the return window, so a loader-window sample from it still has to continue until that boot's user PC. User-instruction bytes at that PC are still unread.
