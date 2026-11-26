# E0648 — ILCE-6400 body–lens ("BL") focus command IDs and capability gate

- **Date:** 2026-11-20
- **Target:** `firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/av-cam.bin` (SHA-256 `90d515cfec8b936f…`, ORIL linked at `0x635C6000`)
- **Method:** static only. Capstone Thumb disassembly; strings resolved through `ldr rX,=off; add rX,pc`; byte search for `movs r2,#imm; bl 0x63D1244E`. No camera access, no emulation.
- **Labels:** [C] = read directly from code, [I] = inference.

## Frame model

- A received BL frame is stored in a 0x320-byte slot. The slot holds an item count at `+0x18` and item pointers from `+0x1C`. Each item starts with its ID byte. `0x63828518(out, slot, id)` returns the item with that ID [C].
- `0x63828568` looks inside container items `0x05`/`0x06` (sub-item count at `+0x11C`, entries at `(0x24+k)*8`) [C]. `0x638285DC` does the same for a container type passed in (callers pass `0x50`) [C].
- `0x63D1244E` / `0x63D1246A` / `0x63D12486` wrap these three lookups [C].

## Focus command IDs

| ID | Name in strings | Evidence |
|---|---|---|
| `0x41` | `ID_BL_CMD_POS_CYCLE_A` / `CallBack_AsyncBL_Pos_Cycle` | `0x638385C6` `movs r2,#0x41`; reply byte 2 bit0 = error [C] |
| `0x43` | `ID_BL_CMD_MOVE_FC_2` / `CallBack_AsyncBL_MoveFc2` | `0x63838EEA`; reply byte 4 bit0 = error, byte 1 = echoed sequence [C] |
| `0x44` | `ID_BL_CMD_FC_DRIVE_CYCLE` / `CallBack_AsyncBL_Focus_Drive_Cycle` | `0x63838580`; reply byte 3 bit0 = error [C] |
| `0x45` | `CallBack_AsyncBL_search2` | `0x63838550` [C] |
| `0x49` | `CallBack_AsyncBL_MoveFcVel2` | `0x63838662`; request built at `0x63D0F1DC`: `[0]=0x49, [1]=bit fields, [2..3]=u16` [C] |
| `0x4A` | `ID_BL_CMD_MOVE_FC_CAP2` / `CallBack_AsyncBL_MoveFcCap` | `0x63838FE4` [C] |
| `0x5B` | `CallBack_AsyncBL_IS_LENS_SHIFT_CYCLE_A` | `0x63856158` [C] |
| `0x3A` / `0x3B` | DividedInfo transfer (CamCurveData, Eclipse, FnoCorrect, IrisDrivability, SteadyShotInfo) | `0x63859A60…0x6385AE14` [C] |
| `0x1C`, `0x1D`, `0x1F`, `0x3C` | older (first-generation) focus items | builders at `0x63D0F158`, `0x63D0F1AE`, `0x63D0F20C`, `0x63D0F254`; queue at `0x63831DE4` accepts only these four [C] |

### MOVE_FC_2 request (`0x63831F48`, 12 bytes) [C]

`[0]=0x43`, `[1]=sequence`, `[2..3]=u16`, `[4..5]=u16`, `[6]=u8`, `[7]` and `[8]` = packed 2/3-bit fields, `[9]` = global byte. `[10..11]=u16` is sent and `[8]|=0x80` set only when `0x63856A8C()>1` and lens flag `+0x162` bit0 is set.

### First-generation move item `0x1D` (`0x63D0F158`, 5 bytes) [C]

`[0]=0x1D`, `[1..2]=u16 value`, `[3]=u8`, `[4]` = fields `{0:1 mode, 2 flag, 3 flag, 4:5, 6:7}`. The `0x43` wrappers pass mode `3` or `2` and use either field `[2..3]` or `[4..5]` of MOVE_FC_2 for the value. [I] This most likely selects absolute versus relative targets.

## Second-generation request and reply formats [C]

Requests are built on the stack and sent with `0x63857EA0(item, callback)`. Replies come back as items with the same ID.

| ID | Request (camera → lens) | Reply as the camera reads it |
|---|---|---|
| `0x41` POS_CYCLE_A | 4 B, `0x6383869C(en, div, send)`: `[1]` bits0-2 = SendComNum, bits3-5 = DividedPosNum, bit6 = enable, bit7 = lens flag `+0x162` bit1. Rejected with `PosCycleSendError` when DividedPosNum < SendComNum. Calls seen: `(0,0xFF,0xFF)` = stop, `(1,4,1)` (state `0x11`), and a generic `(1,div,send)` | `[1]` → stored value; `[2]` bit0 = error (state reset to `0xFF,0xFF`), `[2]` bit1 = flag |
| `0x43` MOVE_FC_2 | 12 B (see above) | `[1]` must equal the request sequence; `[4]` bit0 = error; `[2]` = **frame (VD) counter value at which the move will finish**, `[3]` = extra **milliseconds**. `0x63838E48` turns `[2]`,`[3]` into a timer (`1000*[3]` µs plus whole frame periods) whose callback `0x63D0FE3D` marks the move complete |
| `0x44` FC_DRIVE_CYCLE | 4 B, `0x638387E8`: `[1]` = 7-bit value plus a flag in bit7, `[2]`, `[3]` | `[3]` bit0 = error |
| `0x45` search2 | 17 B, `0x63838850`: packed mode fields, a 6-bit field, three u16 fields, two bytes from camera state | presence only |
| `0x46` | 3 B, `0x638387C4`: `[1]`, `[2]` | — |
| `0x49` MoveFcVel2 | 4 B, `0x63D0F1DC`: `[1]` bits0-1 = mode, bits2-3 = 2-bit sequence, bits4-5 = extra; `[2..3]` = u16 (velocity) | `[1]&3` must equal the sequence, then `+0x16` is set (accepted) |
| `0x4A` MOVE_FC_CAP2 | built at `0x63831E00…0x63831F20` | `[2]` ≠ 0 = error |

Messages the lens sends without a request (`0x6383875C`): `0x42` (u16 value), `0x47` and `0x48` (2 bytes each), `0x5C` (7 bytes copied).

### Cyclic per-frame containers [C]

`0x63835D20` copies sub-items into a 32-entry ring of 0x68-byte records (`0x642FB180`, getter `0x638355E0(n)` = n frames back).

- Container `0x05` (first generation): `0x04` 10 B, `0x08` 1 B, `0x09` 2 B, `0x0B` / `0x0C` / `0x0D` 6 B each, `0x12` 4 B.
- Container `0x06` (second generation): `0x40` 2 B (status bits; accessors read bits 5 and 6 of byte `+0x2F`), `0x41` **11 B** (position cycle), `0x42` 9 B, `0x43` 4 B, `0x44` 6 B, `0x45` 7 B, `0x47` 4 B, `0x48` 8 B.

The only reader found in this image (`0x63D0F044`) hands these records on through `0x637A3440` (cache or shared-memory handoff). The byte meaning of the 11-byte `0x41` sample block is therefore used outside this image section and is **not decoded**.

## Capability gate [C]

- Each MOVE_FC_2 wrapper (`0x638343CC`, `0x63D0F596`, `0x63D0F68A`) first calls `0x63D12860(0x43)` → `0x63855F80`. When that returns 0, the wrapper builds the first-generation `0x1D` item instead.
- `0x63855F94` tests bit `(id-1)` of a bitmap at `BL_LENS_DATA + 1`, where `BL_LENS_DATA` is the lens-supplied data pointer. IDs `0x41…0x49` additionally require a capability byte (`[+0x20FC]+0x20`) and a clear inhibit byte (`0x6448B4A0`). A camera-side mask table at `0x6448B366` gives the final answer.
- [I] The lens therefore decides through its startup data whether the body drives it with the second-generation commands (`0x41/0x43/0x44/0x49/0x4A`) or with the first-generation items.

## Third-party lens comparison (outside this repository) [I]

In a static read of a third-party AF lens image (TTArtisan AF 35 mm F1.8 II E, not stored here), the lens parses only the first-generation items `0x1C/0x1D/0x1F/0x3C` (with the same lengths of 1, 5, 14 and 8 bytes) plus `0x22/0x2E/0x2F/0x3D`. No second-generation ID appears. Its `0x1D` handler reads only the u16 value, not byte `[3]` or the mode bits in `[4]`.

## Not established

- The meaning of each MOVE_FC_2 and `0x1D` field (position versus amount, speed units), and the units of the `0x49` velocity.
- The inner layout of the 11-byte `0x41` position block and the 6-byte `0x44` block.
- The lens-side bitmap that a given third-party lens reports.
- Whether a lens that claims `0x41…0x4A` gets better AF behaviour on the body. No physical test was done.

## Next boundary

Decode the producer of `BL_LENS_DATA` (STARTUP_INFO, ID `0x09`, `0x6385CA80`) to read the bitmap layout. Then compare with a passive mount-bus capture of a lens that is known to use the second-generation commands.
