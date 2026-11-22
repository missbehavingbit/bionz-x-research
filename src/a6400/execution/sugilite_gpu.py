"""Clean-room DMP SMAPH-S30 ("SUGILITE") GPU execution and IRQ model for Unicorn L3.

Executes original firmware Sugilite submissions against guest physical RAM:
- ``PSC0`` memory fills (``BAR + 0x10..0x1C``, IRQ status ``0x04000000``)
- ``PPF`` tiled/linear display & texture transfers (``BAR + 0xC00..0xC20``,
  IRQ status ``0x40000000``)
- ``P3D`` command lists, unified vertex/fragment programmable shader execution,
  triangle/line/point rasterisation, texture sampling, and per-fragment blend/
  depth/stencil/alpha ops (``BAR + 0x1800..0x1804``, IRQ status ``0x80000000``)
- ``sugilite_register_init`` initial BAR state and ``sugilite_interrupt``
  (``grm_gles.ko + 0x658``, GIC IRQ 76) status read, per-engine ack writes,
  and ``dev + 0xA8`` upper-16-bit accumulation.

Provenance:
- Driver MMIO, open, mmap, ioctl, and ISR contracts verified directly from
  ILCE-6400 ``grm_gles.ko`` (SHA-256 ``a8d1dbe8...``) and ``libObj.so``
  (SHA-256 ``60ffd2b0...``).
- DMP SMAPH-S30 P3D/PPF/PSC register & shader architecture documented in
  ``Oein/a5000-emulator`` (``docs/RE_DISPLAY_GPU.md``, ``docs/RE_GPU_P3D_CMD.md``,
  ``docs/RE_GPU_P3D_SHADER.md``, CC BY 4.0, Park SungHyun / Oein).
"""

from __future__ import annotations

import math
import struct
from typing import Callable, Dict, List, Optional, Tuple

SUGILITE_IRQ_NUMBER = 76
ST_P3D = 0x80000000
ST_PPF = 0x40000000
ST_PSC0 = 0x04000000

SH_MAX_CODE = 4096
SH_MAX_OPDESC = 128
SH_NREG = 256
SH_NV = 16
SH_MAX_STEPS = 20000
SH_STACK = 8

P3D_NREG = 0x1000
P3D_MAX_DEPTH = 4
P3D_MAX_VERTS = 1 << 20
P3D_VS_OUT_POS = 0
P3D_FS_OUT_COLOR = 1
P3D_FMT_RGBA8_GL = 5

_SH_ROM = (0.0, 0.5, 1.0, 2.0)
_PPF_BPP = (4, 3, 2, 2, 2, 4, 4, 4)

_MORTON_XY = tuple(
    (x & 1)
    | ((y & 1) << 1)
    | (((x >> 1) & 1) << 2)
    | (((y >> 1) & 1) << 3)
    | (((x >> 2) & 1) << 4)
    | (((y >> 2) & 1) << 5)
    for y in range(8)
    for x in range(8)
)


def _u32_to_f32(val: int) -> float:
    return struct.unpack('<f', struct.pack('<I', val & 0xFFFFFFFF))[0]


def _tiled_index(x: int, y: int, aligned_w: int) -> int:
    return (((y >> 3) * (aligned_w >> 3) + (x >> 3)) << 6) | _MORTON_XY[((y & 7) << 3) | (x & 7)]


def _px_to_rgba(buf: bytes | bytearray | memoryview, off: int, fmt: int) -> Tuple[int, int, int, int]:
    if fmt == 0:
        return buf[off + 3], buf[off + 2], buf[off + 1], buf[off]
    if fmt == 1:
        return buf[off + 2], buf[off + 1], buf[off], 255
    v = buf[off] | (buf[off + 1] << 8)
    if fmt == 2:
        r5 = (v >> 11) & 0x1F
        g6 = (v >> 5) & 0x3F
        b5 = v & 0x1F
        return (r5 << 3), (g6 << 2), (b5 << 3), 255
    if fmt == 3:
        r5 = (v >> 11) & 0x1F
        g5 = (v >> 6) & 0x1F
        b5 = (v >> 1) & 0x1F
        return (r5 << 3), (g5 << 3), (b5 << 3), (255 if (v & 1) else 0)
    return ((v >> 12) & 0xF) << 4, ((v >> 8) & 0xF) << 4, ((v >> 4) & 0xF) << 4, (v & 0xF) << 4


def _rgba_to_px(r: int, g: int, b: int, a: int, fmt: int, out: bytearray, off: int) -> None:
    if fmt == 0:
        out[off] = a
        out[off + 1] = b
        out[off + 2] = g
        out[off + 3] = r
        return
    if fmt == 1:
        out[off] = b
        out[off + 1] = g
        out[off + 2] = r
        return
    if fmt == 2:
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    elif fmt == 3:
        v = ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1) | (a >> 7)
    else:
        v = ((r >> 4) << 12) | ((g >> 4) << 8) | ((b >> 4) << 4) | (a >> 4)
    out[off] = v & 0xFF
    out[off + 1] = (v >> 8) & 0xFF


def _p3d_px_read(buf: bytearray, off: int, fmt: int) -> Tuple[int, int, int, int]:
    if fmt == P3D_FMT_RGBA8_GL:
        return buf[off], buf[off + 1], buf[off + 2], buf[off + 3]
    ppf_fmt = 3 if fmt == 2 else (2 if fmt == 3 else fmt)
    return _px_to_rgba(buf, off, ppf_fmt)


def _p3d_px_write(r: int, g: int, b: int, a: int, fmt: int, buf: bytearray, off: int) -> None:
    if fmt == P3D_FMT_RGBA8_GL:
        buf[off] = r
        buf[off + 1] = g
        buf[off + 2] = b
        buf[off + 3] = a
        return
    ppf_fmt = 3 if fmt == 2 else (2 if fmt == 3 else fmt)
    _rgba_to_px(r, g, b, a, ppf_fmt, buf, off)


def _p3d_compare(func: int, a: int, ref: int) -> bool:
    f = func & 7
    if f == 0:
        return False
    if f == 1:
        return True
    if f == 2:
        return a == ref
    if f == 3:
        return a != ref
    if f == 4:
        return a < ref
    if f == 5:
        return a <= ref
    if f == 6:
        return a > ref
    return a >= ref


def _p3d_stencil_op(op: int, v: int, ref: int) -> int:
    code = op & 7
    if code == 1:
        return 0
    if code == 2:
        return ref
    if code == 3:
        return v + 1 if v < 255 else 255
    if code == 4:
        return v - 1 if v > 0 else 0
    if code == 5:
        return (~v) & 0xFF
    if code == 6:
        return (v + 1) & 0xFF
    if code == 7:
        return (v - 1) & 0xFF
    return v


def _p3d_blend_factor(
    f: int,
    sc: Tuple[float, float, float, float],
    dc: Tuple[float, float, float, float],
    cc: Tuple[float, float, float, float],
    ch: int,
) -> float:
    if f == 0:
        return 0.0
    if f == 1:
        return 1.0
    if f == 2:
        return sc[ch]
    if f == 3:
        return 1.0 - sc[ch]
    if f == 4:
        return dc[ch]
    if f == 5:
        return 1.0 - dc[ch]
    if f == 6:
        return sc[3]
    if f == 7:
        return 1.0 - sc[3]
    if f == 8:
        return dc[3]
    if f == 9:
        return 1.0 - dc[3]
    if f == 10:
        return cc[ch]
    if f == 11:
        return 1.0 - cc[ch]
    if f == 12:
        return cc[3]
    if f == 13:
        return 1.0 - cc[3]
    if f == 14:
        return 1.0 if ch == 3 else min(sc[3], 1.0 - dc[3])
    return 1.0


def _p3d_blend_eq(eq: int, s: float, d: float) -> float:
    if eq == 1:
        return s - d
    if eq == 2:
        return d - s
    if eq == 3:
        return s if s < d else d
    if eq == 4:
        return s if s > d else d
    return s + d


def _swizzle_vec(src: Tuple[float, float, float, float], swz: int, mod: int) -> Tuple[float, float, float, float]:
    s = _SH_ROM if (mod & 7) == 3 else src
    c0 = s[swz & 3]
    c1 = s[(swz >> 2) & 3]
    c2 = s[(swz >> 4) & 3]
    c3 = s[(swz >> 6) & 3]
    if mod & 0x10:
        c0, c1, c2, c3 = -c0, -c1, -c2, -c3
    if (mod & 7) == 2:
        c3 = 1.0
    return (c0, c1, c2, c3)


class DmpShaderProgram:
    """Unified DMP SMAPH-S30 shader memory (code, opdesc, float uniforms)."""

    def __init__(self) -> None:
        self.code: List[int] = [0] * SH_MAX_CODE
        self.ncode: int = 0
        self.opdesc: List[int] = [0] * SH_MAX_OPDESC
        self.nopdesc: int = 0
        self.funif: List[Tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * SH_NREG
        self.unimplemented: List[Dict[str, int | str]] = []

    def run(
        self,
        is_fragment: bool,
        entry: int,
        inputs: List[Tuple[float, float, float, float]],
        tex_sampler: Optional[Callable[[int, Tuple[float, float, float, float]], Tuple[float, float, float, float]]] = None,
        x_base: Optional[List[List[float]]] = None,
        y_base: Optional[List[List[float]]] = None,
        dirty_xy: Optional[List[int]] = None,
    ) -> Optional[List[List[float]]]:
        """Execute a vertex or fragment shader starting at ``entry``."""
        if x_base is not None and y_base is not None and dirty_xy is not None:
            x = x_base
            y = y_base
            written_xy = dirty_xy
        else:
            x = [list(u) for u in self.funif]
            y = [list(u) for u in self.funif]
            written_xy = None

        v: List[Tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * SH_NV
        nin = min(len(inputs), SH_NV)
        for i in range(nin):
            vi = inputs[i]
            v[i] = vi
            if is_fragment and i >= 1:
                if written_xy is not None:
                    written_xy.append(i)
                x[i] = [vi[0], vi[1], vi[2], vi[3]]
                y[i] = [vi[0], vi[1], vi[2], vi[3]]

        o = [[vi[0], vi[1], vi[2], vi[3]] for vi in v]
        a0 = 0
        cond = [False, False, False, False]
        stack: List[int] = []
        pc = entry
        code = self.code
        opdesc = self.opdesc
        funif = self.funif

        try:
            for _ in range(SH_MAX_STEPS):
                if 2 * pc + 1 >= SH_MAX_CODE:
                    self._note_unimp(pc, 0, 0, 'pc_out_of_range')
                    return None
                w0 = code[2 * pc]
                w1 = code[2 * pc + 1]
                op = (w1 >> 20) & 0xFF
                nxt = pc + 1

                if op == 0:
                    ctrl = (w1 >> 12) & 0xFF
                    if ctrl == 0x0:
                        if w0 == 0 and w1 == 0:
                            pc = nxt
                            continue
                        self._note_unimp(pc, w0, w1, 'control_op')
                        return None
                    if ctrl == 0x1:  # CALL
                        if len(stack) >= SH_STACK:
                            self._note_unimp(pc, w0, w1, 'call_stack_overflow')
                            return None
                        stack.append(nxt)
                        pc = w0
                        continue
                    if ctrl == 0x2:  # RET
                        if not stack:
                            return o
                        pc = stack.pop()
                        continue
                    if ctrl == 0x3:  # JMP
                        pc = w0
                        continue
                    if ctrl == 0x4:  # END
                        return o
                    if ctrl == 0xD:  # block marker
                        pc = nxt
                        continue
                    self._note_unimp(pc, w0, w1, 'control_op')
                    return None

                if op in (0x24, 0x3D):  # conditional branch
                    matched = True
                    for k in range(4):
                        f = (w1 >> (18 - 2 * k)) & 3
                        if not (f & 2) and cond[k] != bool(f & 1):
                            matched = False
                            break
                    if w1 & 0x400:
                        matched = not matched
                    pc = w0 if matched else nxt
                    continue

                d = opdesc[w0 & 0x7F]
                mask = (d >> 26) & 0xF

                # src1
                sel1 = (w0 >> 24) & 0xF
                idx1 = (w0 >> 28) | ((w1 & 7) << 4)
                if sel1 & 8:
                    idx1 += a0
                idx1 &= SH_NREG - 1
                if sel1 & 2:
                    raw1 = v[idx1 & (SH_NV - 1)]
                elif w1 & 0x80:
                    raw1 = funif[idx1]
                elif sel1 & 1:
                    yr = y[idx1]
                    raw1 = (yr[0], yr[1], yr[2], yr[3])
                else:
                    xr = x[idx1]
                    raw1 = (xr[0], xr[1], xr[2], xr[3])
                a = _swizzle_vec(raw1, (d >> 13) & 0xFF, (d >> 21) & 0x1F)

                # src2
                idx2 = ((w0 >> 12) & 0x1FF) & (SH_NREG - 1)
                f2 = (w0 >> 21) & 7
                if f2 == 1:
                    yr2 = y[idx2]
                    raw2 = (yr2[0], yr2[1], yr2[2], yr2[3])
                elif f2 == 2:
                    raw2 = v[idx2 & (SH_NV - 1)]
                elif f2 == 4:
                    raw2 = funif[idx2]
                else:
                    xr2 = x[idx2]
                    raw2 = (xr2[0], xr2[1], xr2[2], xr2[3])
                b = _swizzle_vec(raw2, d & 0xFF, (d >> 8) & 0x1F)

                if op == 0x0C:  # MOV
                    r0, r1, r2, r3 = a
                elif op == 0x01:  # ADD
                    r0, r1, r2, r3 = a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3]
                elif op == 0x03:  # MUL
                    r0, r1, r2, r3 = a[0] * b[0], a[1] * b[1], a[2] * b[2], a[3] * b[3]
                elif op == 0x02:  # DP
                    dp = a[0] + a[1] + a[2] + (0.0 if (((d >> 21) & 7) == 2) else a[3])
                    r0 = r1 = r2 = r3 = dp
                elif op == 0x04:  # RCP
                    val = (1.0 / a[0]) if a[0] != 0.0 else math.copysign(math.inf, a[0])
                    r0 = r1 = r2 = r3 = val
                elif op == 0x05:  # RSQ
                    val = (1.0 / math.sqrt(a[0])) if a[0] > 0.0 else math.inf
                    r0 = r1 = r2 = r3 = val
                elif op == 0x07:  # EX2
                    r0 = r1 = r2 = r3 = math.pow(2.0, max(-126.0, min(127.0, a[0])))
                elif op == 0x08:  # LG2
                    r0 = r1 = r2 = r3 = math.log2(a[0]) if a[0] > 0.0 else -math.inf
                elif op == 0x11:  # MOVA
                    for k in range(4):
                        if mask & (1 << k):
                            a0 = int(a[k]) if math.isfinite(a[k]) else 0
                            break
                    pc = nxt
                    continue
                elif op == 0x1C:  # MAX
                    r0 = a[0] if a[0] > b[0] else b[0]
                    r1 = a[1] if a[1] > b[1] else b[1]
                    r2 = a[2] if a[2] > b[2] else b[2]
                    r3 = a[3] if a[3] > b[3] else b[3]
                elif op == 0x1D:  # MIN
                    r0 = a[0] if a[0] < b[0] else b[0]
                    r1 = a[1] if a[1] < b[1] else b[1]
                    r2 = a[2] if a[2] < b[2] else b[2]
                    r3 = a[3] if a[3] < b[3] else b[3]
                elif op == 0x1E:  # CMP
                    cmode = (w1 >> 16) & 7
                    if cmode > 5:
                        self._note_unimp(pc, w0, w1, 'compare_mode')
                        return None
                    for k in range(4):
                        ak, bk = a[k], b[k]
                        if cmode == 0:
                            cond[k] = ak != bk
                        elif cmode == 1:
                            cond[k] = ak < bk
                        elif cmode == 2:
                            cond[k] = ak <= bk
                        elif cmode == 3:
                            cond[k] = ak > bk
                        elif cmode == 4:
                            cond[k] = ak == bk
                        else:
                            cond[k] = ak >= bk
                    pc = nxt
                    continue
                elif op == 0x20:  # TEX
                    if tex_sampler is not None:
                        r0, r1, r2, r3 = tex_sampler((w1 >> 14) & 7, a)
                    else:
                        r0 = r1 = r2 = r3 = 0.0
                elif op == 0x2B:  # colour out + END
                    dst_o = o[1]
                    if mask & 1:
                        dst_o[0] = a[0]
                    if mask & 2:
                        dst_o[1] = a[1]
                    if mask & 4:
                        dst_o[2] = a[2]
                    if mask & 8:
                        dst_o[3] = a[3]
                    return o
                else:
                    self._note_unimp(pc, w0, w1, f'opcode_0x{op:02x}')
                    return None

                dst_idx = (w1 >> 8) & 0xF
                res = (r0, r1, r2, r3)
                if w1 & 0x00080000:
                    do = o[dst_idx]
                    if mask & 1:
                        do[0] = res[0]
                    if mask & 2:
                        do[1] = res[1]
                    if mask & 4:
                        do[2] = res[2]
                    if mask & 8:
                        do[3] = res[3]
                    if is_fragment:
                        if written_xy is not None:
                            written_xy.append(dst_idx)
                        dx = x[dst_idx]
                        if mask & 1:
                            dx[0] = res[0]
                        if mask & 2:
                            dx[1] = res[1]
                        if mask & 4:
                            dx[2] = res[2]
                        if mask & 8:
                            dx[3] = res[3]
                else:
                    if written_xy is not None:
                        written_xy.append(dst_idx)
                    dx = x[dst_idx]
                    if mask & 1:
                        dx[0] = res[0]
                    if mask & 2:
                        dx[1] = res[1]
                    if mask & 4:
                        dx[2] = res[2]
                    if mask & 8:
                        dx[3] = res[3]
                    if not (w1 & 0x80) and (not (w1 & 0x10000000) or (w1 & 0x20)):
                        dy = y[dst_idx]
                        if mask & 1:
                            dy[0] = res[0]
                        if mask & 2:
                            dy[1] = res[1]
                        if mask & 4:
                            dy[2] = res[2]
                        if mask & 8:
                            dy[3] = res[3]

                pc = nxt

            self._note_unimp(pc, 0, 0, 'step_limit')
            return None
        finally:
            if written_xy is not None and x_base is not None and y_base is not None:
                for idx in written_xy:
                    u = funif[idx]
                    x_base[idx] = [u[0], u[1], u[2], u[3]]
                    y_base[idx] = [u[0], u[1], u[2], u[3]]
                written_xy.clear()

    def _note_unimp(self, pc: int, w0: int, w1: int, reason: str) -> None:
        if len(self.unimplemented) < 32:
            self.unimplemented.append({'pc': pc, 'w0': w0, 'w1': w1, 'reason': reason})


class SugiliteGpuEngine:
    """Sugilite BAR ``0xF1000000`` MMIO state, P3D/PPF/PSC engines, and IRQ 76."""

    def __init__(
        self,
        phys_reader: Optional[Callable[[int, int], Optional[bytes]]] = None,
        phys_writer: Optional[Callable[[int, bytes], object]] = None,
    ) -> None:
        self.phys_reader = phys_reader
        self.phys_writer = phys_writer
        self.reg: List[int] = [0] * (0x4000 // 4)
        self.status: int = 0
        self.p3d: List[int] = [0] * P3D_NREG
        self.prog = DmpShaderProgram()
        self.code_idx: int = 0
        self.opd_idx: int = 0
        self.unif_idx: int = 0
        self.ndraw: int = 0
        self.npix: int = 0
        self.jobs: List[Dict[str, object]] = []
        self.interrupts: List[Dict[str, object]] = []
        self._tex_cache: Dict[Tuple[int, int, int, int, bool], List[Tuple[float, float, float, float]]] = {}
        self.reset_registers()

    def __getstate__(self):
        state = dict(self.__dict__)
        state['phys_reader'] = None
        state['phys_writer'] = None
        state['_tex_cache'] = {}
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.__dict__.setdefault('phys_reader', None)
        self.__dict__.setdefault('phys_writer', None)
        self.__dict__.setdefault('_tex_cache', {})

    def describe(self) -> Dict[str, object]:
        by_engine: Dict[str, int] = {}
        for job in self.jobs:
            eng = str(job.get('engine', 'unknown'))
            by_engine[eng] = by_engine.get(eng, 0) + 1
        return {
            'draws': self.ndraw,
            'pixels_shaded': self.npix,
            'job_count': len(self.jobs),
            'jobs_by_engine': by_engine,
            'jobs': list(self.jobs[:128]),
            'interrupt_count': len(self.interrupts),
            'interrupts': list(self.interrupts[:128]),
            'unimplemented_shader_ops': list(self.prog.unimplemented),
        }

    def reset_registers(self) -> None:
        """Apply ``sugilite_register_init`` (``grm_gles.ko + 0x54``) + HW ID."""
        self.reg = [0] * (0x4000 // 4)
        self.status = 0
        init_words = {
            0x0000: 0x000B0000,
            0x0004: 0x00070100,
            0x001C: 0x00010000,
            0x0050: 0x22222113,
            0x0054: 0x00000080,
            0x00C0: 0x20000000,
            0x1000: 0x00000000,
            0x1080: 0x12345678,
            0x10C0: 0xFFFFFFF0,
            0x10C4: 0xFFFFFFFF,
            0x1100: 0x00000001,
            0x1114: 0x76543210,
            0x1118: 0xFEDCBA98,
        }
        for off, val in init_words.items():
            self.reg[off // 4] = val

    def read_mmio(self, offset: int) -> int:
        if not 0 <= offset <= 0x3FFC or offset & 3:
            return 0
        if offset == 0x34:
            return self.status & 0xFFFFFFFF
        return self.reg[offset // 4] & 0xFFFFFFFF

    def write_mmio(self, offset: int, value: int) -> None:
        if not 0 <= offset <= 0x3FFC:
            return
        val = value & 0xFFFFFFFF
        self.reg[offset // 4] = val
        if offset == 0x1C:
            if val & 1:
                self._psc_fill()
                self.status |= ST_PSC0
            elif val == 0:
                self.status &= ~ST_PSC0
        elif offset == 0xC18:
            if val != 0:
                self._ppf_run()
                self.status |= ST_PPF
            else:
                self.status &= ~ST_PPF
        elif offset == 0x1800:
            if val > 0:
                list_pa = self.reg[0x1804 // 4]
                draws_before = self.ndraw
                pix_before = self.npix
                self._p3d_run_list(list_pa, val, 0)
                if len(self.jobs) < 1024:
                    self.jobs.append({
                        'engine': 'p3d',
                        'list_pa': list_pa,
                        'entries': val,
                        'draws': self.ndraw - draws_before,
                        'pixels': self.npix - pix_before,
                    })
                self.status |= ST_P3D
        elif offset == 0x1000:
            if val == 0:
                self.status &= ~ST_P3D
        elif offset in (0x3C, 0x44):
            self.status &= ~0xF

    def service_interrupt(self) -> int:
        """Model ``sugilite_interrupt`` (``grm_gles.ko + 0x658``, IRQ 76).

        Reads ``BAR + 0x34``, executes the driver's exact per-class MMIO
        acknowledgment writes, re-reads ``BAR + 0x34``, and returns the upper
        16-bit status word accumulated into ``dev + 0xA8``.
        """
        r6 = self.read_mmio(0x34)
        if r6 == 0:
            return 0
        acks: List[Tuple[int, int]] = []
        if r6 & 0x80000000:
            self.write_mmio(0x1000, 0)
            acks.append((0x1000, 0))
        if r6 & 0x40000000:
            self.write_mmio(0xC18, 0)
            acks.append((0xC18, 0))
        if r6 & 0x04000000:
            self.write_mmio(0x1C, 0)
            acks.append((0x1C, 0))
        if r6 & 0xF:
            self.write_mmio(0x3C, 1)
            self.write_mmio(0x44, 1)
            acks.extend(((0x3C, 1), (0x44, 1)))
        r7 = self.read_mmio(0x34)
        accumulated = (r6 & 0xFFFF0000) | (r7 & 0xFFFF0000)
        if len(self.interrupts) < 1024:
            self.interrupts.append({
                'irq': SUGILITE_IRQ_NUMBER,
                'status_before': r6,
                'ack_writes': acks,
                'status_after': r7,
                'dev_a8_added': accumulated,
            })
        return accumulated

    # ---- PSC0 --------------------------------------------------------
    def _psc_fill(self) -> None:
        if self.phys_writer is None:
            return
        start_pa = (self.reg[0x10 // 4] << 3) & 0xFFFFFFFF
        end_pa = (self.reg[0x14 // 4] << 3) & 0xFFFFFFFF
        val = self.reg[0x18 // 4]
        width_code = (self.reg[0x1C // 4] >> 8) & 3
        bpp = 4 if width_code == 2 else (3 if width_code == 1 else 2)
        if end_pa <= start_pa or end_pa - start_pa > 64 * 1024 * 1024:
            return
        length = end_pa - start_pa
        pat = val.to_bytes(4, 'little')[:bpp]
        repeats = length // bpp
        rem = length - repeats * bpp
        data = pat * repeats + (b'\x00' * rem if rem else b'')
        self.phys_writer(start_pa, data)
        self._tex_cache.clear()
        if len(self.jobs) < 1024:
            self.jobs.append({
                'engine': 'psc0',
                'start_pa': start_pa,
                'end_pa': end_pa,
                'length': length,
                'value': val,
                'bpp': bpp,
            })

    # ---- PPF ---------------------------------------------------------
    def _ppf_run(self) -> None:
        if self.phys_reader is None or self.phys_writer is None:
            return
        src_pa = (self.reg[0xC00 // 4] << 2) & 0xFFFFFFFF
        dst_pa = (self.reg[0xC04 // 4] << 2) & 0xFFFFFFFF
        flags = self.reg[0xC10 // 4]
        self._tex_cache.clear()

        if flags & 8:
            size = self.reg[0xC20 // 4]
            if 0 < size < 64 * 1024 * 1024:
                raw = self.phys_reader(src_pa, size)
                if raw is not None and len(raw) >= size:
                    self.phys_writer(dst_pa, raw[:size])
            if len(self.jobs) < 1024:
                self.jobs.append({
                    'engine': 'ppf-copy',
                    'src_pa': src_pa,
                    'dst_pa': dst_pa,
                    'size': size,
                })
            return

        dim0 = self.reg[0xC08 // 4]
        dim1 = self.reg[0xC0C // 4]
        iw, ih = dim0 & 0xFFFF, dim0 >> 16
        ow, oh = dim1 & 0xFFFF, dim1 >> 16
        ifmt = (flags >> 8) & 7
        ofmt = (flags >> 12) & 7
        to_tiled = bool(flags & 2)
        flip = bool(flags & 1)
        w = min(iw, ow)
        h = min(ih, oh)
        if w == 0 or h == 0 or w > 4096 or h > 4096:
            return
        ibpp = _PPF_BPP[ifmt]
        obpp = _PPF_BPP[ofmt]
        ib = iw * ih * ibpp
        ob = ow * oh * obpp
        in_raw = self.phys_reader(src_pa, ib)
        if in_raw is None or len(in_raw) < ib:
            return

        # Fast exact-format detile/tile path (e.g. 640x480 RGBA4444 FBO -> OSD).
        if ifmt == ofmt and iw == ow and ih == oh and (w & 7) == 0 and (h & 7) == 0:
            out = bytearray(ob)
            tiles_x = w >> 3
            tile_stride = 64 * ibpp
            row_stride = w * ibpp
            if not to_tiled:
                for y in range(h):
                    sy = (h - 1 - y) if flip else y
                    tile_row_base = (sy >> 3) * tiles_x * tile_stride
                    ly8 = (sy & 7) << 3
                    dst_row = y * row_stride
                    for tx in range(tiles_x):
                        tbase = tile_row_base + tx * tile_stride
                        dbase = dst_row + (tx << 3) * ibpp
                        for lx in range(8):
                            so = tbase + _MORTON_XY[ly8 | lx] * ibpp
                            do = dbase + lx * ibpp
                            out[do : do + ibpp] = in_raw[so : so + ibpp]
            else:
                for y in range(h):
                    sy = (h - 1 - y) if flip else y
                    src_row = sy * row_stride
                    tile_row_base = (y >> 3) * tiles_x * tile_stride
                    ly8 = (y & 7) << 3
                    for tx in range(tiles_x):
                        tbase = tile_row_base + tx * tile_stride
                        sbase = src_row + (tx << 3) * ibpp
                        for lx in range(8):
                            so = sbase + lx * ibpp
                            do = tbase + _MORTON_XY[ly8 | lx] * ibpp
                            out[do : do + ibpp] = in_raw[so : so + ibpp]
            self.phys_writer(dst_pa, bytes(out))
        else:
            existing = self.phys_reader(dst_pa, ob)
            out = bytearray(existing[:ob]) if existing and len(existing) >= ob else bytearray(ob)
            for y in range(h):
                sy = (h - 1 - y) if flip else y
                for x in range(w):
                    si = (sy * iw + x) if to_tiled else _tiled_index(x, sy, iw)
                    di = _tiled_index(x, y, ow) if to_tiled else (y * ow + x)
                    r, g, b, a = _px_to_rgba(in_raw, si * ibpp, ifmt)
                    _rgba_to_px(r, g, b, a, ofmt, out, di * obpp)
            self.phys_writer(dst_pa, bytes(out))

        if len(self.jobs) < 1024:
            self.jobs.append({
                'engine': 'ppf-transfer',
                'src_pa': src_pa,
                'dst_pa': dst_pa,
                'width': w,
                'height': h,
                'ifmt': ifmt,
                'ofmt': ofmt,
                'to_tiled': to_tiled,
                'flip': flip,
            })

    # ---- P3D command list & rasteriser -------------------------------
    def _p3d_run_list(self, pa: int, count: int, depth: int) -> None:
        if count == 0 or count > 0x100000 or depth > P3D_MAX_DEPTH or self.phys_reader is None:
            return
        raw = self.phys_reader(pa, 8 * count)
        if raw is None or len(raw) < 8 * count:
            return
        i = 0
        while i < count:
            hdr, val = struct.unpack_from('<II', raw, 8 * i)
            reg = (hdr >> 16) & 0xFFF
            mask = (hdr >> 12) & 0xF
            i += 1
            if hdr & 1:
                self._p3d_run_list(val, (hdr >> 3) & 0x1FF, depth + 1)
                continue
            if hdr & 4:
                extra = (hdr >> 3) & 0x1FF
                inc = bool(hdr & 2)
                self._p3d_write(reg, val, mask)
                for k in range(extra):
                    wi = 2 * i + k
                    if wi >= 2 * count:
                        break
                    word = struct.unpack_from('<I', raw, 4 * wi)[0]
                    self._p3d_write((reg + 1 + k) if inc else reg, word, mask)
                i += (extra + 1) // 2
                continue
            if mask:
                self._p3d_write(reg, val, mask)

    def _p3d_write(self, reg: int, val: int, mask: int) -> None:
        if not 0 <= reg < P3D_NREG or mask == 0:
            return
        if mask != 0xF:
            m = 0
            if mask & 1:
                m |= 0x000000FF
            if mask & 2:
                m |= 0x0000FF00
            if mask & 4:
                m |= 0x00FF0000
            if mask & 8:
                m |= 0xFF000000
            val = ((self.p3d[reg] & ~m) | (val & m)) & 0xFFFFFFFF
        else:
            val &= 0xFFFFFFFF
        self.p3d[reg] = val

        if reg == 0x202:
            self._p3d_draw(val)
        elif reg == 0x450:
            self.unif_idx = val
        elif reg == 0x454:
            if self.unif_idx < SH_NREG:
                self.prog.funif[self.unif_idx] = (
                    _u32_to_f32(self.p3d[0x451]),
                    _u32_to_f32(self.p3d[0x452]),
                    _u32_to_f32(self.p3d[0x453]),
                    _u32_to_f32(self.p3d[0x454]),
                )
            self.unif_idx += 1
        elif reg == 0x458:
            self.code_idx = val * 2
        elif reg == 0x459:
            if self.code_idx < SH_MAX_CODE:
                self.prog.code[self.code_idx] = val
                self.code_idx += 1
                if self.code_idx > self.prog.ncode:
                    self.prog.ncode = self.code_idx
        elif reg == 0x460:
            self.opd_idx = val
        elif reg == 0x461:
            if self.opd_idx < SH_MAX_OPDESC:
                self.prog.opdesc[self.opd_idx] = val
                self.opd_idx += 1
                if self.opd_idx > self.prog.nopdesc:
                    self.prog.nopdesc = self.opd_idx

    @staticmethod
    def _nib(r: List[int], base: int, i: int) -> int:
        return (r[base + (i >> 3)] >> (4 * (i & 7))) & 0xF

    @staticmethod
    def _fetch_comp(buf: bytes, off: int, vtype: int) -> float:
        norm = bool(vtype & 8)
        code = vtype & 7
        if code == 0:
            val = struct.unpack_from('<b', buf, off)[0]
            return max(val / 127.0, -1.0) if norm else float(val)
        if code == 1:
            val = buf[off]
            return val / 255.0 if norm else float(val)
        if code == 2:
            val = struct.unpack_from('<h', buf, off)[0]
            return max(val / 32767.0, -1.0) if norm else float(val)
        if code == 7:
            val = struct.unpack_from('<H', buf, off)[0]
            return val / 65535.0 if norm else float(val)
        if code == 3:
            return struct.unpack_from('<f', buf, off)[0]
        return 0.0

    def _fetch_vertex(self, idx: int) -> List[Tuple[float, float, float, float]]:
        r = self.p3d
        nbuf = (r[0x20D] & 0xF) + 1
        vin: List[List[float]] = [[0.0, 0.0, 0.0, 1.0] for _ in range(SH_NV)]
        if self.phys_reader is None:
            return [(c[0], c[1], c[2], c[3]) for c in vin]

        for b in range(nbuf):
            nattr = self._nib(r, 0x20E, b) + 1
            first = self._nib(r, 0x210, b)
            stride = ((r[0x212 + (b >> 2)] >> (8 * (b & 3))) & 0xFF) + 1
            constant = bool(r[0x20D] & (0x10000 << b))
            base = (r[0x216 + b] + (0 if constant else idx * stride)) & 0xFFFFFFFF
            vb = self.phys_reader(base, stride)
            if vb is None or len(vb) < stride:
                continue
            for a in range(nattr):
                slot = first + a
                if slot >= 16:
                    break
                vtype = self._nib(r, 0x206, slot)
                ncomp = ((r[0x208] >> (2 * slot)) & 3) + 1
                off = (r[0x209 + (slot >> 2)] >> (8 * (slot & 3))) & 0xFF
                ireg = self._nib(r, 0x204, slot)
                tcode = vtype & 7
                sz = 1 if tcode in (0, 1) else (2 if tcode in (2, 7) else 4)
                dst = vin[ireg]
                for c in range(ncomp):
                    if off + (c + 1) * sz <= stride:
                        dst[c] = self._fetch_comp(vb, off + c * sz, vtype)
        return [(c[0], c[1], c[2], c[3]) for c in vin]

    def _decode_tex_level(
        self,
        base_pa: int,
        aw: int,
        ah: int,
        fmt: int,
        linear: bool,
    ) -> List[Tuple[float, float, float, float]]:
        key = (base_pa, aw, ah, fmt, linear)
        cached = self._tex_cache.get(key)
        if cached is not None:
            return cached
        bpp = 4 if fmt in (0, 25) else (3 if fmt == 1 else (1 if fmt in (7, 8) else (0 if fmt == 12 else 2)))
        total = aw * ah * bpp
        raw = self.phys_reader(base_pa, total) if (bpp and self.phys_reader) else None
        if raw is None or len(raw) < total:
            empty = [(0.0, 0.0, 0.0, 1.0)] * max(1, aw * ah)
            return empty

        out: List[Tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 1.0)] * (aw * ah)
        inv255 = 1.0 / 255.0
        inv31 = 1.0 / 31.0
        inv63 = 1.0 / 63.0
        inv15 = 1.0 / 15.0
        for y in range(ah):
            row_off = y * aw
            for x in range(aw):
                si = (row_off + x) if linear else _tiled_index(x, y, aw)
                po = si * bpp
                if fmt == 0:
                    px = (raw[po + 3] * inv255, raw[po + 2] * inv255, raw[po + 1] * inv255, raw[po] * inv255)
                elif fmt == 25:
                    px = (raw[po] * inv255, raw[po + 1] * inv255, raw[po + 2] * inv255, raw[po + 3] * inv255)
                elif fmt == 1:
                    px = (raw[po + 2] * inv255, raw[po + 1] * inv255, raw[po] * inv255, 1.0)
                elif fmt == 2:
                    v = raw[po] | (raw[po + 1] << 8)
                    px = ((v >> 11) * inv31, ((v >> 6) & 31) * inv31, ((v >> 1) & 31) * inv31, 1.0 if (v & 1) else 0.0)
                elif fmt == 3:
                    v = raw[po] | (raw[po + 1] << 8)
                    px = ((v >> 11) * inv31, ((v >> 5) & 63) * inv63, (v & 31) * inv31, 1.0)
                elif fmt == 4:
                    v = raw[po] | (raw[po + 1] << 8)
                    px = (((v >> 12) & 15) * inv15, ((v >> 8) & 15) * inv15, ((v >> 4) & 15) * inv15, (v & 15) * inv15)
                elif fmt == 5:
                    lum = raw[po + 1] * inv255
                    px = (lum, lum, lum, raw[po] * inv255)
                elif fmt == 7:
                    lum = raw[po] * inv255
                    px = (lum, lum, lum, 1.0)
                elif fmt == 8:
                    px = (0.0, 0.0, 0.0, raw[po] * inv255)
                else:
                    px = (1.0, 0.0, 1.0, 1.0)
                out[row_off + x] = px
        if len(self._tex_cache) > 64:
            self._tex_cache.clear()
        self._tex_cache[key] = out
        return out

    @staticmethod
    def _wrap(c: int, n: int, mode: int) -> int:
        if n <= 0:
            return 0
        if mode == 1:
            return -1 if (c < 0 or c >= n) else c
        if mode == 2:
            return c % n
        if mode == 3:
            p = c % (2 * n)
            return p if p < n else (2 * n - 1 - p)
        return 0 if c < 0 else (n - 1 if c >= n else c)

    def _sample_tex_unit(self, unit: int, coord: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        if unit >= 8:
            return (0.0, 0.0, 0.0, 0.0)
        r = self.p3d
        tbase = 0x585 + 11 * unit
        t0 = r[tbase]
        t1 = r[tbase + 1]
        t2 = r[tbase + 2]
        t3 = r[tbase + 3]
        t4 = r[tbase + 4]
        t10 = r[tbase + 10]
        smp = r[0x5DD + unit]

        aw = (t1 >> 16) & 0xFFFF
        ah = t1 & 0xFFFF
        if aw == 0 or ah == 0:
            return (0.0, 0.0, 0.0, 0.0)
        w = (t2 >> 16) & 0xFFFF
        h = t2 & 0xFFFF
        if w == 0 or h == 0:
            w, h = aw, ah

        minl = min((t3 >> 16) & 0xF, (t3 >> 24) & 0xF)
        fmt = t10 & 0x1F
        bpp = 4 if fmt in (0, 25) else (3 if fmt == 1 else (1 if fmt in (7, 8) else (0 if fmt == 12 else 2)))
        pa = (t4 << 3) & 0xFFFFFFFF
        cur_aw, cur_ah = aw, ah
        for _ in range(minl):
            if bpp:
                pa = (pa + cur_aw * cur_ah * bpp) & 0xFFFFFFFF
            cur_aw = max(cur_aw >> 1, 8)
            cur_ah = max(cur_ah >> 1, 8)
        w = max(w >> minl, 1)
        h = max(h >> minl, 1)

        texels = self._decode_tex_level(pa, cur_aw, cur_ah, fmt, bool(t10 & 0x1000))
        border = (
            ((t0 >> 24) & 0xFF) / 255.0,
            ((t0 >> 16) & 0xFF) / 255.0,
            ((t0 >> 8) & 0xFF) / 255.0,
            (t0 & 0xFF) / 255.0,
        )
        ws = (smp >> 12) & 3
        wt = (smp >> 8) & 3
        lin = bool(smp & (4 if ((t3 >> 24) & 0xF) else 2))

        u = coord[0] * w if math.isfinite(coord[0]) else 0.0
        v = coord[1] * h if math.isfinite(coord[1]) else 0.0

        def _fetch(ix: int, iy: int) -> Tuple[float, float, float, float]:
            if ix < 0 or iy < 0:
                return border
            cx = ix if ix < cur_aw else (cur_aw - 1)
            cy = iy if iy < cur_ah else (cur_ah - 1)
            return texels[cy * cur_aw + cx]

        if not lin:
            ix = self._wrap(math.floor(u), w, ws)
            iy = self._wrap(math.floor(v), h, wt)
            return _fetch(ix, iy)

        fu = u - 0.5
        fv = v - 0.5
        x0 = math.floor(fu)
        y0 = math.floor(fv)
        ax = fu - x0
        ay = fv - y0
        c00 = _fetch(self._wrap(x0, w, ws), self._wrap(y0, h, wt))
        c10 = _fetch(self._wrap(x0 + 1, w, ws), self._wrap(y0, h, wt))
        c01 = _fetch(self._wrap(x0, w, ws), self._wrap(y0 + 1, h, wt))
        c11 = _fetch(self._wrap(x0 + 1, w, ws), self._wrap(y0 + 1, h, wt))
        return (
            (c00[0] + (c10[0] - c00[0]) * ax) * (1.0 - ay) + (c01[0] + (c11[0] - c01[0]) * ax) * ay,
            (c00[1] + (c10[1] - c00[1]) * ax) * (1.0 - ay) + (c01[1] + (c11[1] - c01[1]) * ax) * ay,
            (c00[2] + (c10[2] - c00[2]) * ax) * (1.0 - ay) + (c01[2] + (c11[2] - c01[2]) * ax) * ay,
            (c00[3] + (c10[3] - c00[3]) * ax) * (1.0 - ay) + (c01[3] + (c11[3] - c01[3]) * ax) * ay,
        )

    def _is_constant_fs(self, entry: int) -> bool:
        """True if the fragment program cannot depend on varyings, textures, or depth output."""
        code = self.prog.code
        pc = entry
        for _ in range(32):
            if 2 * pc + 1 >= SH_MAX_CODE:
                return False
            w0 = code[2 * pc]
            w1 = code[2 * pc + 1]
            op = (w1 >> 20) & 0xFF
            if op == 0:
                ctrl = (w1 >> 12) & 0xFF
                if ctrl == 0x4:  # END
                    return True
                if ctrl in (0x0, 0xD) and (ctrl == 0xD or (w0 == 0 and w1 == 0)):
                    pc += 1
                    continue
                return False
            if op in (0x20, 0x24, 0x3D):  # TEX or branch
                return False
            sel1 = (w0 >> 24) & 0xF
            f2 = (w0 >> 21) & 7
            if (sel1 & 2) or f2 == 2:  # reads V[]
                return False
            if not (w1 & 0x80):
                idx1 = ((w0 >> 28) | ((w1 & 7) << 4)) & (SH_NREG - 1)
                if 1 <= idx1 < SH_NV:
                    return False
            if f2 in (0, 1):
                idx2 = ((w0 >> 12) & 0x1FF) & (SH_NREG - 1)
                if 1 <= idx2 < SH_NV:
                    return False
            if (w1 & 0x00080000) and ((w1 >> 8) & 0xF) == 0:
                return False  # writes depth o0
            if op == 0x2B:
                return True
            pc += 1
        return False

    def _p3d_draw(self, kick: int) -> None:
        if self.phys_reader is None or self.phys_writer is None:
            return
        r = self.p3d
        count = kick & 0x0FFFFFFF
        indexed = bool(kick & 0x80000000)
        isz = 4 if (kick & 0x20000000) else (2 if (kick & 0x10000000) else 1)
        topo = r[0x404] & 3
        prim = r[0x401]
        self.ndraw += 1
        if count == 0 or count > P3D_MAX_VERTS:
            return
        if prim not in (0, 0x10, 0x20, 0x802, 0x3020):
            return

        # Target colour buffer
        t_pa = (r[0x112] << 4) & 0xFFFFFFFF
        t_w = r[0x113] & 0x1FFF
        t_h = (r[0x113] >> 16) & 0x1FFF
        t_fmt = (r[0x110] >> 16) & 7
        t_bpp = 2 if t_fmt in (2, 3, 4) else 4
        if t_fmt not in (0, 2, 3, 4, P3D_FMT_RGBA8_GL):
            t_fmt = 0
        if not t_pa or not t_w or not t_h or t_w > 4096 or t_h > 4096:
            return
        aw = (t_w + 7) & ~7
        ah = (t_h + 7) & ~7
        t_bytes = aw * ah * t_bpp
        raw_t = self.phys_reader(t_pa, t_bytes)
        t_buf = bytearray(raw_t[:t_bytes]) if (raw_t and len(raw_t) >= t_bytes) else bytearray(t_bytes)
        t_dirty = False

        # Scissor
        cx2 = r[0x49] & 0xFFFF
        cy2 = (r[0x49] >> 16) & 0xFFFF
        x0_s = struct.unpack('<h', struct.pack('<H', r[0x47] & 0xFFFF))[0]
        y0_s = struct.unpack('<h', struct.pack('<H', (r[0x47] >> 16) & 0xFFFF))[0]
        x1_s = struct.unpack('<h', struct.pack('<H', r[0x48] & 0xFFFF))[0]
        y1_s = struct.unpack('<h', struct.pack('<H', (r[0x48] >> 16) & 0xFFFF))[0]
        sx0 = max((x0_s + cx2) // 2, 0)
        sy0 = max((y0_s + cy2) // 2, 0)
        sx1 = min((x1_s + cx2) // 2, t_w)
        sy1 = min((y1_s + cy2) // 2, t_h)

        # Depth/stencil buffer
        z_fmt = r[0x10F] & 3
        z_bpp = 2 if z_fmt == 0 else (3 if z_fmt == 2 else 4)
        z_pa = (r[0x111] << 4) & 0xFFFFFFFF
        z_buf: Optional[bytearray] = None
        z_dirty = False
        if z_pa and (r[0x10D] & 3) and ((r[0x107] & 1) or (z_fmt == 3 and (r[0x105] & 1))):
            z_bytes = aw * ah * z_bpp
            raw_z = self.phys_reader(z_pa, z_bytes)
            z_buf = bytearray(raw_z[:z_bytes]) if (raw_z and len(raw_z) >= z_bytes) else bytearray(z_bytes)

        # Index fetch & post-T&L vertex shading
        ib: Optional[bytes] = None
        if indexed:
            ib = self.phys_reader(r[0x203], count * isz)
            if ib is None or len(ib) < count * isz:
                return

        vs_entry = r[0x442]
        fs_entry = r[0x443]
        hw = _u32_to_f32(r[0x42])
        hh = _u32_to_f32(r[0x43])
        cx = cx2 * 0.5
        cy = cy2 * 0.5

        x_base = [list(u) for u in self.prog.funif]
        y_base = [list(u) for u in self.prog.funif]
        dirty_xy: List[int] = []

        vtx_cache: Dict[int, Tuple[bool, float, float, float, float, List[Tuple[float, float, float, float]]]] = {}
        verts: List[Tuple[bool, float, float, float, float, List[Tuple[float, float, float, float]]]] = []
        base_idx = r[0x203]

        for i in range(count):
            if not indexed:
                vidx = base_idx + i
            elif isz == 4:
                vidx = struct.unpack_from('<I', ib, 4 * i)[0]  # type: ignore[arg-type]
            elif isz == 2:
                vidx = struct.unpack_from('<H', ib, 2 * i)[0]  # type: ignore[arg-type]
            else:
                vidx = ib[i]  # type: ignore[index]

            cached_v = vtx_cache.get(vidx)
            if cached_v is not None:
                verts.append(cached_v)
                continue

            vin = self._fetch_vertex(vidx)
            vout = self.prog.run(False, vs_entry, vin, None, x_base, y_base, dirty_xy)
            if vout is None or not (vout[P3D_VS_OUT_POS][3] > 0.0):
                entry_v = (False, 0.0, 0.0, 0.0, 0.0, [])
            else:
                pos = vout[P3D_VS_OUT_POS]
                iw = 1.0 / pos[3]
                wx = cx + pos[0] * iw * hw
                wy = cy + pos[1] * iw * hh
                wz = pos[2] * iw * 0.5 + 0.5
                vary_tuples = [(row[0], row[1], row[2], row[3]) for row in vout]
                entry_v = (True, wx, wy, wz, iw, vary_tuples)
            if len(vtx_cache) < 256:
                vtx_cache[vidx] = entry_v
            verts.append(entry_v)

        # Fragment state
        cull = r[0x41] & 3
        poly_off = (r[0x4A] & 0xFFFFFF) / 16777000.0
        alpha_en = bool(r[0x104] & 1)
        alpha_fn = r[0x104] >> 4
        alpha_ref = (r[0x104] >> 8) & 0xFF
        color_writes_on = bool(r[0x10C] & 0xF)
        wmask = (r[0x107] >> 8) & 0xF
        blend_en = bool(r[0x100] & 0x100)
        bf = r[0x101]
        bc = r[0x103]
        cc_f = (
            (bc & 0xFF) / 255.0,
            ((bc >> 8) & 0xFF) / 255.0,
            ((bc >> 16) & 0xFF) / 255.0,
            ((bc >> 24) & 0xFF) / 255.0,
        )
        eq_rgb = bf & 7
        eq_a = (bf >> 8) & 7
        fs_rgb = (bf >> 16) & 15
        fd_rgb = (bf >> 20) & 15
        fs_a = (bf >> 24) & 15
        fd_a = (bf >> 28) & 15

        st_has = z_fmt == 3
        st_en = st_has and bool(r[0x105] & 1)
        z_en = bool(r[0x107] & 1)
        z_write_en = z_en and bool(r[0x107] & 0x1000) and bool(r[0x10E] & 2)
        z_bits = 16 if z_bpp == 2 else 24
        z_max = (1 << z_bits) - 1

        const_rgba: Optional[Tuple[int, int, int, int]] = None
        if self._is_constant_fs(fs_entry):
            dummy_in = [(0.5, 0.5, 0.0, 1.0)] + [(0.0, 0.0, 0.0, 1.0)] * (SH_NV - 1)
            cout = self.prog.run(True, fs_entry, dummy_in, self._sample_tex_unit, x_base, y_base, dirty_xy)
            if cout is not None:
                fc = cout[P3D_FS_OUT_COLOR]
                const_rgba = (
                    int(min(max(0.0 if math.isnan(fc[0]) else fc[0], 0.0), 1.0) * 255.0 + 0.5),
                    int(min(max(0.0 if math.isnan(fc[1]) else fc[1], 0.0), 1.0) * 255.0 + 0.5),
                    int(min(max(0.0 if math.isnan(fc[2]) else fc[2], 0.0), 1.0) * 255.0 + 0.5),
                    int(min(max(0.0 if math.isnan(fc[3]) else fc[3], 0.0), 1.0) * 255.0 + 0.5),
                )

        def _depth_stencil_ok(pi: int, z_val: float) -> bool:
            nonlocal z_dirty
            if z_buf is None or (not st_en and not z_en):
                return True
            zo = pi * z_bpp
            if z_bpp == 2:
                old = z_buf[zo] | (z_buf[zo + 1] << 8)
            elif z_bpp == 3:
                old = z_buf[zo] | (z_buf[zo + 1] << 8) | (z_buf[zo + 2] << 16)
            else:
                old = struct.unpack_from('<I', z_buf, zo)[0]
            val = old
            zold = old & z_max
            zc = min(max(z_val, 0.0), 1.0)
            zf = int(zc * z_max + 0.5)
            swmask = 0
            sref = 0
            st = 0
            if st_en:
                st = (old >> 24) & 0xFF
                smask = (r[0x105] >> 8) & 0xFF
                sref = (r[0x105] >> 16) & 0xFF
                swmask = ((r[0x105] >> 24) & 0xFF) if (r[0x10E] & 1) else 0
                if not _p3d_compare(r[0x105] >> 4, sref & smask, st & smask):
                    nst = _p3d_stencil_op(r[0x106], st, sref)
                    val = (val & ~(swmask << 24)) | ((nst & swmask) << 24)
                    if val != old:
                        if z_bpp == 2:
                            z_buf[zo] = val & 0xFF
                            z_buf[zo + 1] = (val >> 8) & 0xFF
                        elif z_bpp == 3:
                            z_buf[zo] = val & 0xFF
                            z_buf[zo + 1] = (val >> 8) & 0xFF
                            z_buf[zo + 2] = (val >> 16) & 0xFF
                        else:
                            struct.pack_into('<I', z_buf, zo, val & 0xFFFFFFFF)
                        z_dirty = True
                    return False
            pass_z = (not z_en) or _p3d_compare(r[0x107] >> 4, zf, zold)
            if st_en:
                nst = _p3d_stencil_op(r[0x106] >> (8 if pass_z else 4), st, sref)
                val = (val & ~(swmask << 24)) | ((nst & swmask) << 24)
            if pass_z and z_write_en:
                val = (val & ~z_max) | zf
            if val != old:
                if z_bpp == 2:
                    z_buf[zo] = val & 0xFF
                    z_buf[zo + 1] = (val >> 8) & 0xFF
                elif z_bpp == 3:
                    z_buf[zo] = val & 0xFF
                    z_buf[zo + 1] = (val >> 8) & 0xFF
                    z_buf[zo + 2] = (val >> 16) & 0xFF
                else:
                    struct.pack_into('<I', z_buf, zo, val & 0xFFFFFFFF)
                z_dirty = True
            return pass_z

        def _commit_fragment(x: int, y: int, sr: int, sg: int, sb: int, sa: int, z_val: float) -> None:
            nonlocal t_dirty
            self.npix += 1
            if alpha_en and not _p3d_compare(alpha_fn, sa, alpha_ref):
                return
            pi = _tiled_index(x, y, aw)
            if not _depth_stencil_ok(pi, z_val):
                return
            if not color_writes_on or wmask == 0:
                return
            po = pi * t_bpp
            if not blend_en and wmask == 0xF:
                _p3d_px_write(sr, sg, sb, sa, t_fmt, t_buf, po)
                t_dirty = True
                return
            dr, dg, db, da = _p3d_px_read(t_buf, po, t_fmt)
            if blend_en:
                sc_f = (sr / 255.0, sg / 255.0, sb / 255.0, sa / 255.0)
                dc_f = (dr / 255.0, dg / 255.0, db / 255.0, da / 255.0)
                out_c = [0, 0, 0, 0]
                for c in range(4):
                    eq = eq_rgb if c < 3 else eq_a
                    f_s = fs_rgb if c < 3 else fs_a
                    f_d = fd_rgb if c < 3 else fd_a
                    ov = _p3d_blend_eq(
                        eq,
                        sc_f[c] * _p3d_blend_factor(f_s, sc_f, dc_f, cc_f, c),
                        dc_f[c] * _p3d_blend_factor(f_d, sc_f, dc_f, cc_f, c),
                    )
                    ov = min(max(ov, 0.0), 1.0)
                    out_c[c] = int(ov * 255.0 + 0.5)
                rr, rg, rb, ra = out_c[0], out_c[1], out_c[2], out_c[3]
            else:
                rr, rg, rb, ra = sr, sg, sb, sa
            if not (wmask & 1):
                rr = dr
            if not (wmask & 2):
                rg = dg
            if not (wmask & 4):
                rb = db
            if not (wmask & 8):
                ra = da
            _p3d_px_write(rr, rg, rb, ra, t_fmt, t_buf, po)
            t_dirty = True

        def _shade_vary(x: int, y: int, vary: List[Tuple[float, float, float, float]]) -> None:
            if const_rgba is not None:
                _commit_fragment(
                    x, y, const_rgba[0], const_rgba[1], const_rgba[2], const_rgba[3],
                    vary[0][2] + poly_off,
                )
                return
            fout = self.prog.run(True, fs_entry, vary, self._sample_tex_unit, x_base, y_base, dirty_xy)
            if fout is None:
                return
            fc = fout[P3D_FS_OUT_COLOR]
            sr = int(min(max(0.0 if math.isnan(fc[0]) else fc[0], 0.0), 1.0) * 255.0 + 0.5)
            sg = int(min(max(0.0 if math.isnan(fc[1]) else fc[1], 0.0), 1.0) * 255.0 + 0.5)
            sb = int(min(max(0.0 if math.isnan(fc[2]) else fc[2], 0.0), 1.0) * 255.0 + 0.5)
            sa = int(min(max(0.0 if math.isnan(fc[3]) else fc[3], 0.0), 1.0) * 255.0 + 0.5)
            z_val = fout[P3D_VS_OUT_POS][2] + poly_off
            _commit_fragment(x, y, sr, sg, sb, sa, z_val)

        def _raster_triangle(
            a: Tuple[bool, float, float, float, float, List[Tuple[float, float, float, float]]],
            b: Tuple[bool, float, float, float, float, List[Tuple[float, float, float, float]]],
            c: Tuple[bool, float, float, float, float, List[Tuple[float, float, float, float]]],
        ) -> None:
            if not (a[0] and b[0] and c[0]):
                return
            ax, ay, az, aiw, av = a[1], a[2], a[3], a[4], a[5]
            bx, by, bz, biw, bv = b[1], b[2], b[3], b[4], b[5]
            cx_v, cy_v, cz, ciw, cv = c[1], c[2], c[3], c[4], c[5]
            area = (bx - ax) * (cy_v - ay) - (cx_v - ax) * (by - ay)
            if area == 0.0 or not math.isfinite(area):
                return
            if (cull == 1 and area > 0.0) or (cull == 2 and area < 0.0):
                return
            rx0 = max(int(math.floor(min(ax, bx, cx_v))), sx0)
            rx1 = min(int(math.ceil(max(ax, bx, cx_v))), sx1)
            ry0 = max(int(math.floor(min(ay, by, cy_v))), sy0)
            ry1 = min(int(math.ceil(max(ay, by, cy_v))), sy1)
            if rx0 >= rx1 or ry0 >= ry1:
                return
            inv = 1.0 / area
            vary_buf: List[Tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * SH_NV

            for y in range(ry0, ry1):
                py = y + 0.5
                for x in range(rx0, rx1):
                    px = x + 0.5
                    w0 = ((bx - px) * (cy_v - py) - (cx_v - px) * (by - py)) * inv
                    w1 = ((cx_v - px) * (ay - py) - (ax - px) * (cy_v - py)) * inv
                    w2 = 1.0 - w0 - w1
                    if w0 < 0.0 or w1 < 0.0 or w2 < 0.0:
                        continue
                    if (
                        (w0 == 0.0 and area * (cy_v - by) < 0.0)
                        or (w1 == 0.0 and area * (ay - cy_v) < 0.0)
                        or (w2 == 0.0 and area * (by - ay) < 0.0)
                    ):
                        continue
                    p0 = w0 * aiw
                    p1 = w1 * biw
                    p2 = w2 * ciw
                    ps = p0 + p1 + p2
                    if ps == 0.0 or not math.isfinite(ps):
                        continue
                    wz = w0 * az + w1 * bz + w2 * cz
                    if const_rgba is not None:
                        _commit_fragment(
                            x, y, const_rgba[0], const_rgba[1], const_rgba[2], const_rgba[3],
                            wz + poly_off,
                        )
                        continue
                    inv_ps = 1.0 / ps
                    p0 *= inv_ps
                    p1 *= inv_ps
                    p2 *= inv_ps
                    vary_buf[0] = (px, py, wz, ps)
                    for k in range(1, SH_NV):
                        ak = av[k]
                        bk = bv[k]
                        ck = cv[k]
                        vary_buf[k] = (
                            ak[0] * p0 + bk[0] * p1 + ck[0] * p2,
                            ak[1] * p0 + bk[1] * p1 + ck[1] * p2,
                            ak[2] * p0 + bk[2] * p1 + ck[2] * p2,
                            ak[3] * p0 + bk[3] * p1 + ck[3] * p2,
                        )
                    _shade_vary(x, y, vary_buf)

        if prim == 0:  # GL_POINTS
            for vt in verts:
                if not vt[0]:
                    continue
                ix, iy = int(math.floor(vt[1])), int(math.floor(vt[2]))
                if sx0 <= ix < sx1 and sy0 <= iy < sy1:
                    vlist = list(vt[5])
                    vlist[0] = (ix + 0.5, iy + 0.5, vt[3], vt[4])
                    _shade_vary(ix, iy, vlist)
        elif prim in (0x10, 0x802):  # GL_LINES / GL_LINE_STRIP
            step = 1 if topo == 1 else 2
            for i in range(0, count - 1, step):
                va, vb = verts[i], verts[i + 1]
                if not (va[0] and vb[0]):
                    continue
                dx = vb[1] - va[1]
                dy = vb[2] - va[2]
                n = int(math.ceil(max(abs(dx), abs(dy))))
                if n > 8192:
                    continue
                for s_i in range(max(n, 1)):
                    f = (s_i / n) if n else 0.0
                    px = va[1] + dx * f
                    py = va[2] + dy * f
                    ix, iy = int(math.floor(px)), int(math.floor(py))
                    if not (sx0 <= ix < sx1 and sy0 <= iy < sy1):
                        continue
                    p0 = (1.0 - f) * va[4]
                    p1 = f * vb[4]
                    ps = p0 + p1
                    if ps == 0.0 or not math.isfinite(ps):
                        continue
                    p0 /= ps
                    p1 /= ps
                    vlist = [(ix + 0.5, iy + 0.5, (1.0 - f) * va[3] + f * vb[3], ps)]
                    for k in range(1, SH_NV):
                        vlist.append((
                            va[5][k][0] * p0 + vb[5][k][0] * p1,
                            va[5][k][1] * p0 + vb[5][k][1] * p1,
                            va[5][k][2] * p0 + vb[5][k][2] * p1,
                            va[5][k][3] * p0 + vb[5][k][3] * p1,
                        ))
                    _shade_vary(ix, iy, vlist)
        elif topo == 0:  # GL_TRIANGLES
            for i in range(0, count - 2, 3):
                _raster_triangle(verts[i], verts[i + 1], verts[i + 2])
        elif topo == 1:  # GL_TRIANGLE_STRIP
            for i in range(count - 2):
                if i & 1:
                    _raster_triangle(verts[i + 1], verts[i], verts[i + 2])
                else:
                    _raster_triangle(verts[i], verts[i + 1], verts[i + 2])
        elif topo == 2:  # GL_TRIANGLE_FAN
            for i in range(1, count - 1):
                _raster_triangle(verts[0], verts[i], verts[i + 1])

        if t_dirty:
            self.phys_writer(t_pa, bytes(t_buf))
            self._tex_cache.clear()
        if z_dirty and z_buf is not None:
            self.phys_writer(z_pa, bytes(z_buf))
