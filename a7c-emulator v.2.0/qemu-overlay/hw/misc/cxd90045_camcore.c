/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 CamCore and peripheral register blocks.
 *
 * This is a generic per-register rule file: every instance selects one rule
 * table with the "base" property and maps that table's block at a region of
 * its own.  The "window" property (default 0) says where the mapped sub-window
 * starts inside the block, so one block table can cover several uncovered
 * sub-windows (for example the 0xF2910000 block outside the GPIO model).
 * With "window" 0 the region offset is the block offset, which is how the
 * three CamCore instances below keep their original behaviour.
 *
 * The av-cam core maps the CamCore windows 1:1 (virtual 0xF30x0000 =
 * physical, see docs/evidence/0564-qemu-ydet-window-configuration-reaches-
 * camcore.md).  Three instances share this device, each selected by the
 * "base" property:
 *
 *   0xF3010000 (64 KiB)  L0027: 1012 distinct (kind, offset) pairs,
 *                        28 read offsets, 984 write-only.
 *   0xF3000000 (64 KiB)  L0028: 789 distinct pairs, 57 read offsets,
 *                        732 write-only.
 *   0xF3120000 (64 KiB)  L0028: 100 distinct pairs, 13 read offsets,
 *                        87 write-only.
 *
 * L0031 adds two more block tables selected by the same "base" property.
 * Only the uncovered sub-windows are mapped; the GPIO, P2S, timer, SIO and
 * ADC models keep their own regions:
 *
 *   0xF2400000 is NOT modelled here: 23 of its 35 read offsets are written by
 *                        the guest and +0x1038 is compared with 3 and never
 *                        satisfied, so a 0-returning table would hide a real
 *                        status register (L0031 red team).  It stays unmodelled.
 *   0xF3C00000 (64 KiB)  L0031: 23 read offsets, all RF.  Every baseline read
 *                        is immediately followed by a write and every reader
 *                        is ldr/bfi/str on the same word.
 *   0xF2910000 (64 KiB)  L0031: 16 read offsets outside the 0x1200-byte GPIO
 *                        model, all Z0.  The readers copy the word to memory
 *                        or test one status bit; +0x2548/+0x2554 are written
 *                        by other code but read by that copy getter.
 *
 * The per-block rule tables come from the L0026 spec
 * (loop/scratch/L0026/spec.md, 70/70 reading PCs verified by its red team)
 * and this lead's recount on the fresh v11 baseline log
 * loop/runs/L0028/base/qemu-d.log (classify-base.txt):
 *
 *  - RF   read back the last value written.  An offset is RF only when every
 *         baseline read of it is immediately followed by a write to the same
 *         offset (a true read-modify-write word).
 *  - Z0   return 0: every read offset that is not such a read-modify-write
 *         word.  That covers status words the guest never writes and the
 *         guest-written words whose reads are copies/one-shot getters, for
 *         which returning 0 is the baseline (catch-all) behaviour.  No value
 *         is invented.
 *
 * Rule counts: 0xF3010000 17 RF + 11 Z0 (unchanged L0027); 0xF3000000
 * 23 RF + 34 Z0; 0xF3120000 5 RF + 8 Z0.  0xF3120000 +0x0410 is Z0, not RF:
 * the guest writes 1 and its boolean caller (0x63cd493b subs/cmp) takes a
 * different branch when the read returns 1, enabling channels 6/12/13/15
 * whose unmodelled status then stalls the YUV path.  The baseline zero path
 * is the observed one, so this one-shot gate holds 0; no value is invented.
 *
 * All observed reads are in the table, so no read offset depends on a
 * blanket fallback.  An unlisted read returns 0 and is logged with LOG_UNIMP,
 * so a new read offset is never silent.
 *
 * The off-by-default "trace" property logs every access (kind, offset,
 * size, value) with qemu_log, so the guest write stream can be compared
 * against the baseline unmodelled-access log without adding oracle lines.
 */

#include "qemu/osdep.h"
#include "hw/sysbus.h"
#include "hw/qdev-properties.h"
#include "qemu/log.h"
#include "qemu/cutils.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_CAMCORE "cxd90045-camcore"
OBJECT_DECLARE_SIMPLE_TYPE(CamCoreState, CXD90045_CAMCORE)

enum CamCoreRuleKind {
    CAM_RF,     /* read back the last write */
    CAM_ZERO,   /* always read 0 (status the guest never writes) */
};

typedef struct CamCoreRule {
    uint32_t off;
    enum CamCoreRuleKind kind;
    uint32_t mask;      /* guest getter mask, informational for Z0 */
    const char *why;    /* reading PC / reason */
} CamCoreRule;

/*
 * 0xF3010000 (L0027, unchanged): one entry per observed read offset (28).
 * PCs are the Thumb load instructions; the gdbstub watchpoint reports the
 * following instruction.
 */
static const CamCoreRule camcore_rules_f301[] = {
    { 0x4b00, CAM_RF,   0xffffffff, "RMW at 0x63cdf21a: ldr/bfi/str same word" },
    { 0x4b04, CAM_RF,   0xffffffff, "RMW at 0x63cdf25e: ldr/bfi/str same word" },
    { 0x5020, CAM_RF,   0xffffffff, "read back guest write 0x01030001; "
                                    "nibble getters 0x63cd0a1c/0x63cd0a28" },
    { 0x5600, CAM_ZERO, 0x00001fff, "status, never written; getter 0x63cd6c72" },
    { 0x5604, CAM_ZERO, 0x00000fff, "status, never written; getter 0x63cd6c82" },
    { 0x5a04, CAM_RF,   0xffffffff, "RMW at 0x63cd7cfc: ldr/bfi/str same word" },
    { 0x5a14, CAM_RF,   0xffffffff, "RMW at 0x63cd7d56: ldr/bfi/str same word" },
    { 0x5a18, CAM_RF,   0xffffffff, "RMW at 0x63cd7d7a: ldr/bfi/str same word" },
    { 0x5a1c, CAM_RF,   0xffffffff, "RMW at 0x63cd7d9e: ldr/bfi/str same word" },
    { 0x5a28, CAM_RF,   0xffffffff, "RMW at 0x63cd7dda: ldr/bfi/str same word" },
    { 0x5a2c, CAM_RF,   0xffffffff, "RMW at 0x63cd7dfe: ldr/bfi/str same word" },
    { 0x5a30, CAM_RF,   0xffffffff, "RMW at 0x63cd7e22: ldr/bfi/str same word" },
    { 0x5a48, CAM_RF,   0xffffffff, "RMW at 0x63cd7e76: ldr/bfi/str same word" },
    { 0x5a4c, CAM_RF,   0xffffffff, "RMW at 0x63cd7e9a: ldr/bfi/str same word" },
    { 0x5ac8, CAM_RF,   0xffffffff, "RMW at 0x63cd800e: ldr/bfi/str same word" },
    { 0x5acc, CAM_RF,   0xffffffff, "RMW at 0x63cd8032: ldr/bfi/str same word" },
    { 0x5ad0, CAM_RF,   0xffffffff, "RMW at 0x63cd8058: ldr/bfi/str same word" },
    { 0x5ad4, CAM_RF,   0xffffffff, "RMW at 0x63cd807e: ldr/bfi/str same word" },
    { 0x5b80, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd8102" },
    { 0x5b84, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd8112" },
    { 0x5b8c, CAM_ZERO, 0x000007ff, "status, never written; getter 0x63cd8132" },
    { 0x5b90, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd8142" },
    { 0x5b94, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd8152" },
    { 0x5b98, CAM_ZERO, 0x0fffffff, "status, never written; getter 0x63cd815e" },
    { 0x5b9c, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd816e" },
    { 0x5ba0, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd817e" },
    { 0x5ba4, CAM_ZERO, 0x0fffffff, "status, never written; getter 0x63cd818a" },
    { 0x5e00, CAM_RF,   0xffffffff, "RMW at 0x63cd9396: ldr/bfi/str same word" },
};

/*
 * 0xF3000000: one entry per observed read offset (57).  Counts are from
 * loop/scratch/L0028/classify-base.txt (fresh v11 base, 60 s) and are the
 * same in the 100 s base-long log (5.43 s guest): 23 RF offsets, every read
 * followed by a write (ldr/bfi/str RMW); 34 Z0 offsets (never-written
 * status plus the 4 offsets whose reads are mostly not followed by a write
 * and the guest-written one-shot words, all 0 on the baseline path).
 */
static const CamCoreRule camcore_rules_f300[] = {
    { 0x0038, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd5530" },
    { 0x01C0, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd0dac" },
    { 0x0408, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd0e06" },
    { 0x0410, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x63cd0e52" },
    { 0x0438, CAM_ZERO, 0x3fff3fff, "not RMW (no read->write); 0 at 0x63cd0f70" },
    { 0x043C, CAM_ZERO, 0x3fff3fff, "not RMW (no read->write); 0 at 0x63cd0f9c" },
    { 0x0458, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x63cd1032" },
    { 0x0710, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x63cd44ce" },
    { 0x0810, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x63ccff94" },
    { 0x0904, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd0152" },
    { 0x0910, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x63cd0198" },
    { 0x0E10, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd040e" },
    { 0x2008, CAM_ZERO, 0xffffffff, "status: 6/12 reads not -> write; "
                                    "getter 0x639be844" },
    { 0x2280, CAM_ZERO, 0xffffffff, "status: 854/1067 reads not -> write; "
                                    "getter 0x639be9e8" },
    { 0x2284, CAM_ZERO, 0x80000000, "status, never written; getter 0x639bea4e" },
    { 0x229C, CAM_ZERO, 0xffffffff, "status, never written; getter 0x639beb5a" },
    { 0x22C4, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x639be90a" },
    { 0x22DC, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x639be95e" },
    { 0x2400, CAM_ZERO, 0xffffffff, "status: 858/1072 reads not -> write; "
                                    "getter 0x639be9e8" },
    { 0x2404, CAM_ZERO, 0xffffffff, "status, never written; getter 0x639be77c" },
    { 0x241C, CAM_ZERO, 0xffffffff, "status, never written; getter 0x639beb5a" },
    { 0x2444, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x639be90a" },
    { 0x245C, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x639be95e" },
    { 0x2480, CAM_ZERO, 0xffffffff, "status: 858/1072 reads not -> write; "
                                    "getter 0x639be9e8" },
    { 0x2484, CAM_ZERO, 0xffffffff, "status, never written; getter 0x639be77c" },
    { 0x249C, CAM_ZERO, 0xffffffff, "status, never written; getter 0x639beb5a" },
    { 0x24C4, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x639be90a" },
    { 0x24DC, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x639be95e" },
    { 0x3300, CAM_ZERO, 0x00000001, "status, never written; getter 0x63cd1098" },
    { 0x3418, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cda7be" },
    { 0x3500, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd9baa" },
    { 0x3504, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd9bfe" },
    { 0x350C, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd9c4c" },
    { 0x3510, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd9c68" },
    { 0x3518, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd9c8c" },
    { 0x351C, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd9c9a" },
    { 0x3538, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd9cf4" },
    { 0x353C, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd9d00" },
    { 0x3540, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd9d0c" },
    { 0x3544, CAM_ZERO, 0x000001ff, "status, never written; getter 0x63cd9d18" },
    { 0x3548, CAM_ZERO, 0x000001ff, "status, never written; getter 0x63cd9d30" },
    { 0x354C, CAM_ZERO, 0x000001ff, "status, never written; getter 0x63cd9d48" },
    { 0x3550, CAM_ZERO, 0x000001ff, "status, never written; getter 0x63cd9d60" },
    { 0x3554, CAM_ZERO, 0x000001ff, "status, never written; getter 0x63cd9d78" },
    { 0x3558, CAM_ZERO, 0x000001ff, "status, never written; getter 0x63cd9d90" },
    { 0x3C00, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdad66" },
    { 0x3C04, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdad74" },
    { 0x3C08, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdadba" },
    { 0x3C0C, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdadd6" },
    { 0x3C10, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdadf2" },
    { 0x3C14, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdae0e" },
    { 0x3C18, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdae2a" },
    { 0x3C1C, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdae46" },
    { 0x3C20, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdae62" },
    { 0x3C24, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdae7e" },
    { 0x3C28, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdae9a" },
    { 0x3C2C, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cdafb2" },
};

/*
 * 0xF3120000: one entry per observed read offset (13): 5 RF offsets (every
 * read followed by a write) and 8 Z0.  The SGC1 words at +0x48, +0x50,
 * +0x210, +0x220, +0x224 stay owned by sony_sgc1_create (map priority -500
 * above this block's -600), so they are not listed here.
 */
static const CamCoreRule camcore_rules_f312[] = {
    { 0x0038, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd5556" },
    { 0x003C, CAM_ZERO, 0x00000003, "status, never written; getter 0x6376a756" },
    { 0x004C, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd4bd2" },
    { 0x0054, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd4bf2" },
    { 0x0208, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd46da" },
    { 0x0230, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cd563a" },
    { 0x032C, CAM_ZERO, 0x0003ffff, "not RMW (no read->write); 0 at 0x63cd475e" },
    { 0x0410, CAM_ZERO, 0xffffffff, "RMW but read-back=1 enables extra channels and stalls YUV; 0 is the baseline path (0x63cd482c)" },
    { 0x042C, CAM_ZERO, 0xffffffff, "not RMW (no read->write); 0 at 0x63cd4868" },
    { 0x0438, CAM_ZERO, 0x3fff3fff, "not RMW (no read->write); 0 at 0x63cd48a2" },
    { 0x043C, CAM_ZERO, 0x3fff3fff, "not RMW (no read->write); 0 at 0x63cd48ce" },
    { 0x047C, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd4902" },
    { 0x2160, CAM_RF,   0xffffffff, "RMW, every read -> write; 0x63cef180" },
};

/*
 * 0xF3C00000: one entry per observed read offset (23).  Every baseline read
 * is immediately followed by a write to the same offset (viol 0) and the
 * reader is ldr/bfi/str on the same word, so all are RF.  PCs are the Thumb
 * load instructions (disasm.txt).
 */
static const CamCoreRule camcore_rules_f3c0[] = {
    { 0x4100, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a562; every read -> write" },
    { 0x4104, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a5a2; every read -> write" },
    { 0x4108, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a5d4; every read -> write" },
    { 0x410C, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a5fa; every read -> write" },
    { 0x4200, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a3b6; every read -> write" },
    { 0x4204, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a416; every read -> write" },
    { 0x4220, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a4ee; every read -> write" },
    { 0x4230, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a552; every read -> write" },
    { 0x4300, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a1be; every read -> write" },
    { 0x4304, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a1fe; every read -> write" },
    { 0x4308, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a230; every read -> write" },
    { 0x430C, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a256; every read -> write" },
    { 0x4400, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a266; every read -> write" },
    { 0x4404, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a2a6; every read -> write" },
    { 0x4408, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a2d8; every read -> write" },
    { 0x440C, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a2fe; every read -> write" },
    { 0x4500, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a30e; every read -> write" },
    { 0x4504, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a34e; every read -> write" },
    { 0x4508, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a380; every read -> write" },
    { 0x450C, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x63b0a3a6; every read -> write" },
    { 0x8000, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x6384967e; every read -> write" },
    { 0x8270, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x6384a00c; every read -> write" },
    { 0x8274, CAM_RF,   0xffffffff, "RMW ldr/bfi/str at 0x6384a036; every read -> write" },
};

/*
 * 0xF2910000 outside the 0x1200-byte GPIO model: one entry per observed read
 * offset (16).  The readers at 0x63a69ca4 copy the word to memory, the two
 * never-written +0x2540/+0x2550 use the bit getter 0x63a69e78/8c, and no
 * baseline read is immediately followed by a write; all are Z0.  +0x5010 has
 * two baseline reads but no CPU reader was seen by rwatch on any of the four
 * vCPUs (the XDMAC model can read a source address as a bus master); it is
 * never written and stays 0 as in the baseline.
 */
static const CamCoreRule camcore_rules_f291[] = {
    { 0x2204, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2214, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2244, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2248, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2258, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2404, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2408, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2444, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2448, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2458, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x2540, CAM_ZERO, 0x00000001, "never written; bit getter 0x63a69e78 (and #1)" },
    { 0x2548, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4; written by other code" },
    { 0x2550, CAM_ZERO, 0x00000001, "never written; bit getter 0x63a69e8c (and #1)" },
    { 0x2554, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4; written by other code" },
    { 0x2F04, CAM_ZERO, 0xffffffff, "copy getter 0x63a69ca4 (ldr, str to memory)" },
    { 0x5010, CAM_ZERO, 0xffffffff, "never written; 2 reads, no CPU reader (rwatch all 4 vCPUs)" },
};

struct CamCoreState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    GHashTable *regs;
    uint64_t size;
    uint64_t base;      /* selects the per-block rule table */
    uint64_t window;    /* mapped sub-window start, 0 = same as base */
    char *name;
    bool trace;
    const CamCoreRule *rules;
    size_t nrules;
};

/* Block-relative offset of a region access (rule tables are block-relative). */
static uint64_t camcore_key(const CamCoreState *s, hwaddr off)
{
    if (s->window) {
        return (s->window - s->base) + off;
    }
    return off;
}

static const CamCoreRule *camcore_rule(const CamCoreState *s, uint32_t off)
{
    size_t i;

    for (i = 0; i < s->nrules; i++) {
        if (s->rules[i].off == off) {
            return &s->rules[i];
        }
    }
    return NULL;
}

static uint64_t camcore_read(void *opaque, hwaddr off, unsigned size)
{
    CamCoreState *s = opaque;
    uint64_t key = camcore_key(s, off);
    hwaddr w = key & ~3ULL;
    const CamCoreRule *r = camcore_rule(s, w);
    uint32_t v = 0;

    if (!r) {
        qemu_log_mask(LOG_UNIMP, "%s: unlisted read offset 0x%05x size %u\n",
                      s->name ? s->name : "cxd90045-camcore", (unsigned)off,
                      size);
    } else if (r->kind == CAM_RF) {
        v = GPOINTER_TO_UINT(g_hash_table_lookup(s->regs,
                              GUINT_TO_POINTER((guint)w)));
    }
    /* CAM_ZERO and unlisted offsets read 0. */
    v >>= (off & 3) * 8;
    if (size < 4) {
        v &= (1u << (size * 8)) - 1;
    }
    if (s->trace) {
        qemu_log("camcore-trace: %s read off 0x%05x size %u value 0x%08x "
                 "pa 0x%08x\n",
                 s->name ? s->name : "cxd90045-camcore", (unsigned)key, size,
                 v, (unsigned)((s->window ? s->window : s->base) + off));
    }
    return v;
}

static void camcore_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    CamCoreState *s = opaque;
    uint64_t key = camcore_key(s, off);
    hwaddr w = key & ~3ULL;
    uint32_t old = GPOINTER_TO_UINT(g_hash_table_lookup(s->regs,
                                 GUINT_TO_POINTER((guint)w)));
    unsigned sh = (off & 3) * 8;
    uint32_t mask = size >= 4 ? 0xffffffffu : ((1u << (size * 8)) - 1) << sh;
    uint32_t nv = (old & ~mask) | (((uint32_t)v << sh) & mask);

    if (s->trace) {
        qemu_log("camcore-trace: %s write off 0x%05x size %u value 0x%08x "
                 "pa 0x%08x\n",
                 s->name ? s->name : "cxd90045-camcore", (unsigned)key, size,
                 (uint32_t)v, (unsigned)((s->window ? s->window : s->base) + off));
    }
    g_hash_table_insert(s->regs, GUINT_TO_POINTER((guint)w),
                        GUINT_TO_POINTER(nv));
}

static const MemoryRegionOps camcore_ops = {
    .read = camcore_read,
    .write = camcore_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void camcore_reset(DeviceState *dev)
{
    CamCoreState *s = CXD90045_CAMCORE(dev);
    g_hash_table_remove_all(s->regs);
}

static void camcore_realize(DeviceState *dev, Error **errp)
{
    CamCoreState *s = CXD90045_CAMCORE(dev);

    switch (s->base) {
    case 0xF3000000ULL:
        s->rules = camcore_rules_f300;
        s->nrules = ARRAY_SIZE(camcore_rules_f300);
        break;
    case 0xF3120000ULL:
        s->rules = camcore_rules_f312;
        s->nrules = ARRAY_SIZE(camcore_rules_f312);
        break;
    case 0xF3C00000ULL:
        s->rules = camcore_rules_f3c0;
        s->nrules = ARRAY_SIZE(camcore_rules_f3c0);
        break;
    case 0xF2910000ULL:
        s->rules = camcore_rules_f291;
        s->nrules = ARRAY_SIZE(camcore_rules_f291);
        break;
    default:
        /* 0xF3010000 (the original L0027 instance) and any other base. */
        s->rules = camcore_rules_f301;
        s->nrules = ARRAY_SIZE(camcore_rules_f301);
        break;
    }

    s->regs = g_hash_table_new(g_direct_hash, g_direct_equal);
    memory_region_init_io(&s->mr, OBJECT(dev), &camcore_ops, s,
                          s->name ? s->name : "cxd90045-camcore", s->size);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mr);
}

static const Property camcore_props[] = {
    DEFINE_PROP_UINT64("size", CamCoreState, size, 0x10000),
    DEFINE_PROP_UINT64("base", CamCoreState, base, 0),
    DEFINE_PROP_UINT64("window", CamCoreState, window, 0),
    DEFINE_PROP_STRING("name", CamCoreState, name),
    DEFINE_PROP_BOOL("trace", CamCoreState, trace, false),
};

static void camcore_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = camcore_realize;
    device_class_set_legacy_reset(dc, camcore_reset);
    device_class_set_props(dc, camcore_props);
}

static const TypeInfo camcore_info = {
    .name = TYPE_CXD90045_CAMCORE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(CamCoreState),
    .class_init = camcore_class_init,
};

static void camcore_register(void)
{
    type_register_static(&camcore_info);
}
type_init(camcore_register)
