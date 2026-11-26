"""Deterministic CXD90045 peripheral models ported from QEMU sony_a6400.c."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.a6400.devices.cxd90045_sdhci import CxdSdhciEmmc, EMMC0_BASE, EMMC0_SIZE


LDEC_IN_BASE = 0xF0020000
LDEC_IN_SIZE = 0x10000
LDEC_BASE = 0xF2903000
LDEC_IRQ = 171

XDMAC0_BASE = 0xF0000000
XDMAC0_IRQ_BASE = 189
XDMAC1_BASE = 0xF0010000
XDMAC1_IRQ_BASE = 193

F2A_BASES = (
    0xF2A01000,
    0xF2A11000,
    0xF2A12000,
    0xF2A21000,
    0xF2A22000,
    0xF2A30000,
)

LEO_CLK_BASE = 0xFC021000
DFS_CTRL_BASE = 0xF2916000
AUDIOIP_BASE = 0xF3420000
IMAGER_BASE = 0xFC000000
DWC3_BASE = 0xF0200000
DWC3_SIZE = 0x100000

# QEMU cxd90014_vph.c / cxd90014_evsel.c (E0529): one engine frame is av-cam's
# 0x8257 microseconds. Panel vsync is an independent 60 Hz timer.
ENGINE_FRAME_US = 33367
VSYNC_US = 1_000_000 // 60

SYSV_BASE = 0xF3402000
SYSV_SIZE = 0x1000
VPH_BASE = 0xF3440000
VPH_SIZE = 0x7000
EVSEL_BASE = 0xF3C30000
EVSEL_SIZE = 0x1000
IRQ_SYSV = 98
IRQ_VSYNC_A = 101
IRQ_VSYNC_B = 102
IRQ_EVSEL_A = 83
IRQ_EVSEL_B = 84

SIO_BASE = 0xF2405000
SIO_CHANNELS = 5
SIO_CHANNEL_STRIDE = 0x200
SIO_SIZE = SIO_CHANNEL_STRIDE * SIO_CHANNELS
SIO_IRQ0 = 201
GPIO_BASE = 0xF2910000
GPIO_PORTS = 18
GPIO_PORT_STRIDE = 0x100
GPIO_SIZE = GPIO_PORT_STRIDE * GPIO_PORTS
# a6400 board default, without the rear-controls EVF pin (sony_a6400.c).
GPIO_RESET_HIGH = (
    (0, 1), (0, 3), (0, 4), (0, 7), (0, 10), (0, 11), (0, 13),
    (1, 1), (1, 4), (1, 5),
)
ADC_BASE = 0xF2406000
ADC_UNITS = 2
ADC_STRIDE = 0x1000
ADC_MMIO = 0x100
ADC_SIZE = ADC_STRIDE * ADC_UNITS
ADC_IRQ0 = 215
ADC_CHANNELS = 12
CLKGEN_BASE = 0xF2800000
CLKGEN_SIZE = 0x8000
SA_SIZE = 0x1000
SA_DELAY_US = 100
SA_CORES = (
    (0xF34A0000, (51, 52, 53), b'ZMA0V1.0SA5_V1.0'),
    (0xF34B0000, (54, 55, 56), b'ZMA1V1.0SA5_V1.0'),
    (0xF34C0000, (61, 62, 63), b'SA0_V1.0SA5_V1.0'),
    (0xF35C0000, (64, 65, 66), b'SA1_V1.0SA5_V1.0'),
)
SA_SRC_BASE = (0x100, 0x140, 0x200)


def ldec_lz77_decode(data: bytes, max_out: int) -> bytes | None:
    """Decode a Sony LDEC LZ77 compressed stream (ported from sony_a6400.c)."""
    lengths = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 32, 64)
    pos = 0
    in_len = len(data)
    out = bytearray()
    while pos < in_len and len(out) < max_out:
        kind = data[pos]
        pos += 1
        if kind == 0x0F:
            if pos + 3 > in_len:
                return None
            pos += 1  # skip 1 byte
            count = data[pos] | (data[pos + 1] << 8)
            pos += 2
            if pos + count > in_len or len(out) + count > max_out:
                return None
            out.extend(data[pos:pos + count])
            pos += count
        elif kind == 0xF0:
            done = False
            while not done:
                if pos >= in_len:
                    return None
                flags = data[pos]
                pos += 1
                if flags == 0:
                    if pos + 8 > in_len or len(out) + 8 > max_out:
                        return None
                    out.extend(data[pos:pos + 8])
                    pos += 8
                    continue
                for bit in range(8):
                    if (flags >> bit) & 1:
                        if pos + 2 > in_len:
                            return None
                        a = data[pos]
                        b = data[pos + 1]
                        pos += 2
                        dist = ((a & 0x0F) << 8) | b
                        if dist == 0:
                            done = True
                            break
                        length = lengths[a >> 4]
                        if dist > len(out) or len(out) + length > max_out:
                            return None
                        for _ in range(length):
                            out.append(out[-dist])
                    else:
                        if pos >= in_len or len(out) >= max_out:
                            return None
                        out.append(data[pos])
                        pos += 1
        else:
            return None
    return bytes(out)


class CxdRwStub:
    """Plain read-back register file for unmodelled config blocks."""

    def __init__(self, name: str, base: int, size: int, initial: dict[int, int] | None = None):
        self.name = name
        self.base = base
        self.size = size
        self.regs: dict[int, int] = dict(initial or {})

    def read(self, offset: int) -> int:
        return self.regs.get(offset & ~3, 0)

    def write(self, offset: int, value: int) -> None:
        self.regs[offset & ~3] = value & 0xFFFFFFFF


class CxdImagerWindow(CxdRwStub):
    """Imager register file. MIPI-Tx D-PHY LWAITSTAT at +0x2A080 bit 0 stays set.

    av-cam polls that bit from base 0xFC02A060. A zero there is the
    "PLL LWAITSTAT count max" stop.
    """

    def read(self, offset: int) -> int:
        value = super().read(offset)
        if (offset & ~3) == 0x2A080:
            value |= 1
        return value


class CxdZeroMmio:
    """Unimplemented-device semantics (reads 0, writes dropped) matching QEMU."""

    def __init__(self, name: str, base: int, size: int):
        self.name = name
        self.base = base
        self.size = size

    def read(self, offset: int) -> int:
        return 0

    def write(self, offset: int, value: int) -> None:
        return None


class CxdF2aClk:
    """CXD90045 F2A clock/reset block (+0x04 bit0, +0x2C bit1, +0x30 self-clear)."""

    def __init__(self, base: int):
        self.base = base
        self.size = 0x1000
        self.regs: dict[int, int] = {}

    def read(self, offset: int) -> int:
        off = offset & ~3
        if off == 0x04:
            return self.regs.get(0x04, 0) | 0x1
        if off == 0x2C:
            return self.regs.get(0x2C, 0) | 0x2
        if off == 0x30:
            return 0
        return self.regs.get(off, 0)

    def write(self, offset: int, value: int) -> None:
        off = offset & ~3
        if off == 0x30:
            return
        self.regs[off] = value & 0xFFFFFFFF


class CxdLeoClk:
    """Leo clock generator at 0xFC021000 (+0x50/+0x54/+0x58 bit0 PLL lock)."""

    def __init__(self, base: int = LEO_CLK_BASE):
        self.base = base
        self.size = 0x1000
        self.regs: dict[int, int] = {}

    def read(self, offset: int) -> int:
        off = offset & ~3
        val = self.regs.get(off, 0)
        if off in (0x50, 0x54, 0x58):
            val |= 1
        return val

    def write(self, offset: int, value: int) -> None:
        self.regs[offset & ~3] = value & 0xFFFFFFFF


class CxdP2s:
    """Sensor register writer. A start at +0x908 bit 0 finishes in 50 µs.

    Until that bit clears, the driver refuses every later transfer. Done
    status is +0x904 bit 0 and raises IRQ 38 until the guest writes it 1.
    """

    BASE = 0xF3C00000
    SIZE = 0x1000
    IRQ = 38
    XFER_US = 50

    def __init__(self, set_irq: Callable[[int, bool], None], now: Callable[[], int]):
        self.set_irq = set_irq
        self.now = now
        self.regs = [0] * (self.SIZE // 4)
        self.status = 0
        self.busy = False
        self.due: int | None = None

    def read(self, offset: int) -> int:
        if offset == 0x904:
            return self.status
        if offset == 0x908:
            return (self.regs[offset // 4] & ~1) | (1 if self.busy else 0)
        if 0 <= offset < self.SIZE:
            return self.regs[offset // 4]
        return 0

    def write(self, offset: int, value: int) -> None:
        val32 = value & 0xFFFFFFFF
        if offset == 0x904:
            self.status &= ~val32
            self.set_irq(self.IRQ, bool(self.status & 1))
            return
        if offset == 0x908:
            self.regs[offset // 4] = val32 & ~1
            if (val32 & 1) and not self.busy:
                self.busy = True
                self.due = self.now() + self.XFER_US
            return
        if 0 <= offset < self.SIZE:
            self.regs[offset // 4] = val32

    def complete_if_due(self, now: int) -> None:
        if self.due is None or now < self.due:
            return
        self.due = None
        self.busy = False
        self.status |= 1
        self.set_irq(self.IRQ, True)

    def remaining_us(self, now: int) -> int | None:
        if self.due is None or self.due <= now:
            return None
        return self.due - now

    def irq_level(self) -> bool:
        return bool(self.status & 1)


class CxdCxTimer:
    """16-channel CXD4115 timer. Channel 11 is the imager power wait.

    The Unicorn machine still answers the first 0x200 bytes itself, so
    channels 0 and 1 keep that clocksource path. Channels 2–15, including
    the one-shot at +0xB00, use this model: START runs, a match sets pending,
    and a one-shot clears START.
    """

    BASE = 0xF2403000
    SIZE = 0x1000
    IRQ0 = 153
    NCH = 16

    def __init__(self, set_irq: Callable[[int, bool], None], now: Callable[[], int]):
        self.set_irq = set_irq
        self.now = now
        self.ctrl = [0] * self.NCH
        self.cmp = [0] * self.NCH
        self.base_count = [0] * self.NCH
        self.base_us = [0] * self.NCH
        self.running = [False] * self.NCH
        self.pending = [False] * self.NCH
        self.due: list[int | None] = [None] * self.NCH

    def read(self, offset: int) -> int:
        channel, local = self._split(offset)
        if channel is None:
            return 0
        if local == 0x0:
            return self.ctrl[channel]
        if local == 0x4:
            return 1 if self.pending[channel] else 0
        if local == 0x8:
            return self.cmp[channel]
        if local == 0xC:
            return self._count(channel, self.now())
        return 0

    def write(self, offset: int, value: int) -> None:
        channel, local = self._split(offset)
        if channel is None:
            return
        val32 = value & 0xFFFFFFFF
        now = self.now()
        if local == 0x0:
            self.base_count[channel] = self._count(channel, now)
            self.base_us[channel] = now
            self.ctrl[channel] = val32
            self.running[channel] = bool(val32 & (1 << 12))
            self._update(channel)
            self._rearm(channel, now)
            return
        if local == 0x4:
            if val32 & 1:
                self.pending[channel] = False
            if val32 & 0x10:
                self.base_count[channel] = 0
                self.base_us[channel] = now
                self._rearm(channel, now)
            self._update(channel)
            return
        if local == 0x8:
            self.cmp[channel] = val32
            self._rearm(channel, now)

    def complete_if_due(self, now: int) -> None:
        for channel in range(self.NCH):
            due = self.due[channel]
            if due is None or now < due or not self.running[channel]:
                continue
            self.pending[channel] = True
            ctrl = self.ctrl[channel]
            if (ctrl & (1 << 8)) and not (ctrl & (1 << 5)):
                self.base_count[channel] = 0
                self.base_us[channel] = now
                if ctrl & (1 << 4):
                    self.running[channel] = False
                    self.ctrl[channel] = ctrl & ~(1 << 12)
            self._update(channel)
            self._rearm(channel, now)

    def remaining_us(self, now: int) -> int | None:
        waits = [
            due - now for due in self.due
            if due is not None and due > now
        ]
        return min(waits) if waits else None

    def levels(self) -> tuple[tuple[int, bool], ...]:
        return tuple(
            (self.IRQ0 + channel, self.pending[channel] and bool(self.ctrl[channel] & 2))
            for channel in range(self.NCH)
        )

    def _count(self, channel: int, now: int) -> int:
        if not self.running[channel]:
            return self.base_count[channel]
        hz = 4_000_000 >> (self.ctrl[channel] & 3)
        ticks = (max(0, now - self.base_us[channel]) * hz) // 1_000_000
        return (self.base_count[channel] + ticks) & 0xFFFFFFFF

    def _rearm(self, channel: int, now: int) -> None:
        if not self.running[channel]:
            self.due[channel] = None
            return
        delta = (self.cmp[channel] - self._count(channel, now)) & 0xFFFFFFFF
        if delta == 0:
            ctrl = self.ctrl[channel]
            delta = self.cmp[channel] if (ctrl & (1 << 8)) and not (ctrl & (1 << 5)) else 0xFFFFFFFF
            if delta == 0:
                delta = 1
        hz = 4_000_000 >> (self.ctrl[channel] & 3)
        self.due[channel] = now + max(1, (delta * 1_000_000 + hz - 1) // hz)

    def _update(self, channel: int) -> None:
        irq, level = self.levels()[channel]
        self.set_irq(irq, level)

    def _split(self, offset: int) -> tuple[int | None, int]:
        if offset < 0 or offset >= self.SIZE:
            return None, 0
        channel = offset >> 8
        if channel >= self.NCH or (offset & 0xFF) > 0x0C:
            return None, 0
        return channel, offset & 0xFF


@dataclass
class CxdLdec:
    """CXD90045 LDEC LZ77 hardware decompressor (0xF2903000, IRQ 171)."""

    set_irq: Callable[[int, bool], None]
    irq: int = LDEC_IRQ
    regs: list[int] = field(default_factory=lambda: [0] * (0x100 >> 2))
    in_buf: bytearray = field(default_factory=bytearray)

    def read(self, offset: int) -> int:
        idx = offset >> 2
        return self.regs[idx] if 0 <= idx < len(self.regs) else 0

    def write(self, offset: int, value: int) -> None:
        idx = offset >> 2
        if 0 <= idx < len(self.regs):
            self.regs[idx] = value & 0xFFFFFFFF
        if offset == 0x30:
            self.regs[0x34 >> 2] &= ~(value & 0xFFFFFFFF)
            if (self.regs[0x34 >> 2] & 0x7B) == 0:
                self.set_irq(self.irq, False)

    def feed(
        self,
        dsa_off: int,
        ssa: int,
        length: int,
        mem_read: Callable[[int, int], bytes],
        mem_write: Callable[[int, bytes], None],
    ) -> None:
        if length == 0 or length > 16 * 1024 * 1024 or dsa_off > 16 * 1024 * 1024:
            return
        if dsa_off == 0:
            self.in_buf.clear()
        need = dsa_off + length
        if need > len(self.in_buf):
            self.in_buf.extend(b'\x00' * (need - len(self.in_buf)))
        self.in_buf[dsa_off:need] = mem_read(ssa, length)
        records = self.regs[0x0C >> 2]
        dst_phys = self.regs[0x10 >> 2]
        max_out = records * 4096 if 0 < records <= 4096 else 16 * 1024 * 1024
        if dst_phys != 0:
            decoded = ldec_lz77_decode(bytes(self.in_buf), max_out)
            if decoded is not None and len(decoded) > 0:
                mem_write(dst_phys, decoded)
                self.in_buf.clear()
                self.regs[0x34 >> 2] = 0x1
                self.set_irq(self.irq, True)


class CxdXdmac:
    """CXD90045 4-channel XDMAC DMA engine."""

    def __init__(
        self,
        base: int,
        irq_base: int,
        set_irq: Callable[[int, bool], None],
        mem_read: Callable[[int, int], bytes],
        mem_write: Callable[[int, bytes], None],
        ldec: CxdLdec | None = None,
    ):
        self.base = base
        self.size = 0x10000
        self.irq_base = irq_base
        self.set_irq = set_irq
        self.mem_read = mem_read
        self.mem_write = mem_write
        self.ldec = ldec
        self.regs = [0] * (0x100 >> 2)

    def read(self, offset: int) -> int:
        idx = offset >> 2
        return self.regs[idx] if 0 <= idx < len(self.regs) else 0

    def write(self, offset: int, value: int) -> None:
        val32 = value & 0xFFFFFFFF
        idx = offset >> 2
        if 0 <= idx < len(self.regs):
            self.regs[idx] = val32
        if 0x10 <= offset < 0x10 + 4 * 0x30:
            ch = (offset - 0x10) // 0x30
            ch_off = (offset - 0x10) % 0x30
            ch_base = 0x10 + ch * 0x30
            if ch_off == 0x18 and val32 != 0:
                tbc = self.regs[(ch_base + 0x00) >> 2] + 1
                ssa = self.regs[(ch_base + 0x04) >> 2]
                dsa = self.regs[(ch_base + 0x08) >> 2]
                if (
                    self.ldec is not None
                    and LDEC_IN_BASE <= dsa < LDEC_IN_BASE + LDEC_IN_SIZE
                ):
                    self.ldec.feed(dsa - LDEC_IN_BASE, ssa, tbc, self.mem_read, self.mem_write)
                elif 0 < tbc <= 16 * 1024 * 1024:
                    data = self.mem_read(ssa, tbc)
                    self.mem_write(dsa, data)
                self.regs[(ch_base + 0x20) >> 2] = 0x8
                self.set_irq(self.irq_base + ch, True)
            elif ch_off == 0x20 and val32 == 0:
                self.regs[(ch_base + 0x20) >> 2] = 0
                self.set_irq(self.irq_base + ch, False)


def _aif(channel: int) -> int:
    """AIF channel register base inside the VPH window (ch51 is channel 0)."""
    return 0x1200 + 0x80 * channel


class CxdSysvVph:
    """SYSV master V clock plus VPH scanout registers from cxd90014_vph.c.

    The engine frame and the 60 Hz panel vsync are independent. SYSV ``+0x98``
    is the frame counter and ``+0x28`` bit 0 follows its parity. Each vsync
    clears AIF start bits. IRQ pulses are recorded for the machine to latch.
    """

    def __init__(self, mem_read: Callable[[int, int], bytes] | None = None):
        self.mem_read = mem_read
        self.vph: dict[int, int] = {}
        self.sysv: dict[int, int] = {}
        self.frame = 0
        self.vsyncs = 0
        self._us = 0
        self._next_frame = ENGINE_FRAME_US
        self._next_vsync = VSYNC_US
        self.panel: bytearray | None = None
        self.width = 640
        self.height = 480

    def us_until_event(self) -> int:
        return max(1, min(self._next_frame - self._us, self._next_vsync - self._us))

    def advance_us(self, dt: int, on_frame: Callable[[], None] | None = None) -> set[int]:
        if dt <= 0:
            return set()
        end = self._us + dt
        pulses: set[int] = set()
        while True:
            nxt = min(self._next_frame, self._next_vsync)
            if nxt > end:
                break
            self._us = nxt
            at_vsync = nxt == self._next_vsync
            at_frame = nxt == self._next_frame
            if at_vsync:
                self._on_vsync()
                pulses.add(IRQ_VSYNC_A)
                pulses.add(IRQ_VSYNC_B)
                self._next_vsync += VSYNC_US
            if at_frame:
                self.frame = (self.frame + 1) & 0xFFFFFFFF
                pulses.add(IRQ_SYSV)
                self._next_frame += ENGINE_FRAME_US
                if on_frame is not None:
                    on_frame()
        self._us = end
        return pulses

    def _on_vsync(self) -> None:
        self.vsyncs += 1
        for channel in range(8):
            off = _aif(channel)
            self.vph[off] = self.vph.get(off, 0) & ~1
        if self.mem_read is not None and (
            self.vph.get(_aif(2) + 0x20, 0) or (self.vph.get(0x144, 0) & 1)
        ):
            self._compose_panel()

    def _compose_panel(self) -> None:
        width, height = self.width, self.height
        dst = bytearray(width * height * 4)
        if self.vph.get(0x144, 0) & 1:
            self._blit_luma(dst, width, height, 0)
        bpp = 4 if (self.vph.get(0x404, 0) & 1) else 2
        self._blit_osd(dst, width, height, 2, bpp, False)
        self.panel = dst

    def _blit_luma(self, dst: bytearray, width: int, height: int, channel: int) -> None:
        base = self.vph.get(_aif(channel) + 0x20, 0)
        line_bytes = self.vph.get(_aif(channel) + 0x24, 0)
        pitch = line_bytes + self.vph.get(_aif(channel) + 0x28, 0)
        lines = self.vph.get(_aif(channel) + 0x2C, 0) + 1
        if not base or not line_bytes or line_bytes > 8192 or lines >= 4096 or self.mem_read is None:
            return
        for y in range(height):
            sy = y * lines // height
            try:
                row = self.mem_read(base + sy * pitch, line_bytes)
            except Exception:
                return
            if len(row) < line_bytes:
                return
            for x in range(width):
                luma = row[x * line_bytes // width]
                pixel = y * width + x
                dst[pixel * 4:pixel * 4 + 4] = bytes((luma, luma, luma, 0xFF))

    def _blit_osd(
        self, dst: bytearray, width: int, height: int, channel: int, bpp: int, scale: bool
    ) -> None:
        base = self.vph.get(_aif(channel) + 0x20, 0)
        line_bytes = self.vph.get(_aif(channel) + 0x24, 0)
        lines = self.vph.get(_aif(channel) + 0x2C, 0) + 1
        pitch = self.vph.get(_aif(channel) + 0x48, 0) & 0xFFFF
        if pitch >= 0x8000:
            pitch -= 0x10000
        if pitch == 0:
            pitch = line_bytes
        if (
            not base or not line_bytes or line_bytes > 16384 or lines >= 4096
            or bpp not in (2, 4) or self.mem_read is None
        ):
            return
        source_width = line_bytes // bpp
        out_h = height if scale else min(lines, height)
        out_w = width if scale else min(source_width, width)
        last_row = -1
        row = b''
        for y in range(out_h):
            sy = y * lines // height if scale else y
            if sy != last_row:
                try:
                    row = self.mem_read(base + sy * pitch, line_bytes)
                except Exception:
                    return
                if len(row) < line_bytes:
                    return
                last_row = sy
            for dx in range(out_w):
                x = dx * source_width // width if scale else dx
                if bpp == 4:
                    red, green, blue, alpha = row[4 * x:4 * x + 4]
                else:
                    packed = row[2 * x] | (row[2 * x + 1] << 8)
                    red = (packed >> 12) * 17
                    green = ((packed >> 8) & 0xF) * 17
                    blue = ((packed >> 4) & 0xF) * 17
                    alpha = (packed & 0xF) * 17
                pixel = y * width + dx
                under_b, under_g, under_r = dst[pixel * 4:pixel * 4 + 3]
                dst[pixel * 4:pixel * 4 + 4] = bytes((
                    (blue * alpha + under_b * (255 - alpha)) // 255,
                    (green * alpha + under_g * (255 - alpha)) // 255,
                    (red * alpha + under_r * (255 - alpha)) // 255,
                    0xFF,
                ))

    def read_vph(self, offset: int) -> int:
        off = offset & ~3
        if _aif(0) <= off < _aif(8) and (off & 0x7F) == 0x04:
            return self.vph.get(off, 0) & ~0x80000000
        if off in (0x1004, 0x2004, 0x3004):
            return 0
        return self.vph.get(off, 0)

    def write_vph(self, offset: int, value: int) -> None:
        self.vph[offset & ~3] = value & 0xFFFFFFFF

    def read_sysv(self, offset: int) -> int:
        off = offset & ~3
        if off == 0x98:
            return self.frame
        if off == 0x28:
            return (self.sysv.get(off, 0) & ~1) | (self.frame & 1)
        return self.sysv.get(off, 0)

    def write_sysv(self, offset: int, value: int) -> None:
        self.sysv[offset & ~3] = value & 0xFFFFFFFF


class CxdEvsel:
    """Camera timing-event block. Engine frames arrive from SYSV (ext-vd)."""

    def __init__(self):
        self.regs: dict[int, int] = {}
        self.sta = 0
        self.stb = 0
        self.us = 0

    def set_us(self, us: int) -> None:
        self.us = us & 0xFFFFFFFF
        self._service_comparators()

    def on_engine_frame(self) -> None:
        if self.regs.get(0xEC, 0) & 1:
            enabled = self.regs.get(0x10, 0) & 0xFF
            if enabled:
                self._fire(enabled)

    def irq_a(self) -> bool:
        return bool(self.sta & self.regs.get(0x10, 0))

    def irq_b(self) -> bool:
        return bool(self.stb & self.regs.get(0xF8, 0))

    def _fire(self, mask: int) -> None:
        for channel in range(8):
            if mask & (1 << channel):
                self.regs[0x20 + 4 * channel] = self.us
        self.sta |= mask

    def _service_comparators(self) -> None:
        for channel in range(10):
            arm = self.regs.get(0x110 + 8 * channel, 0)
            if not (arm & 1):
                continue
            target = self.regs.get(0x114 + 8 * channel, 0)
            if self._distance(target) <= 0:
                self.stb |= 1 << channel

    def _distance(self, target: int) -> int:
        return ((target - self.us) & 0xFFFFFFFF) - (
            0x100000000 if ((target - self.us) & 0xFFFFFFFF) >= 0x80000000 else 0
        )

    def read(self, offset: int) -> int:
        off = offset & ~3
        if off == 0x04:
            return self.sta
        if off == 0xE0:
            return self.us
        if off == 0xF4:
            return self.stb
        return self.regs.get(off, 0)

    def write(self, offset: int, value: int) -> None:
        off = offset & ~3
        val32 = value & 0xFFFFFFFF
        if off == 0x08:
            self.sta &= ~val32
            return
        if off == 0xFC:
            self.stb &= ~val32
            return
        self.regs[off] = val32
        if 0x110 <= off < 0x110 + 8 * 10 and ((off - 0x110) & 7) == 0:
            self._service_comparators()


SUBMICRO_PAGE = Path(
    'artifacts/qemu-a6400/e0526/camera-page-0x03443000.bin')
SUBMICRO_PAGE_SHA256 = (
    '14fba4e607b0efdb76ced74e0463355a279146c96bbba0abec89776b8972fb54')
SUBMICRO_SKIP = 0x3A4
SUBMICRO_FRAME_BYTES = 384


def load_submicro_frames(root: Path) -> bytes | None:
    """Return the QEMU default 384-byte sub-micro extract, or None if it is absent."""
    page = root / SUBMICRO_PAGE
    if not page.is_file():
        return None
    data = page.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SUBMICRO_PAGE_SHA256 or len(data) < SUBMICRO_SKIP + SUBMICRO_FRAME_BYTES:
        return None
    return data[SUBMICRO_SKIP:SUBMICRO_SKIP + SUBMICRO_FRAME_BYTES]


def submicro_reply(frames: bytes, tx: bytes, length: int) -> bytes:
    """Replay the captured 384-byte micro->CPU slots the way QEMU's ch3 slave does.

    TX byte 0 selects the slot: ``0x31`` -> C, ``0x61`` -> B, anything else -> A.
    Rear-control, SD, and lens edits stay off, matching the oracle boot.
    """
    if len(frames) != 384 or length < 128:
        return tx[:length]
    slot = 256 if tx[0] == 0x31 else 128 if tx[0] == 0x61 else 0
    out = bytearray(frames[slot:slot + 128])
    copy = 128
    if length >= 256:
        out += frames[256:384]
        copy = 256
    return bytes(out[:copy]) + tx[copy:length]


class CxdSio:
    """Five-channel CXD90045 SIO. A start sets the done bit and completes at once."""

    def __init__(
        self,
        set_irq: Callable[[int, bool], None],
        submicro_frames: bytes | None = None,
    ):
        self.set_irq = set_irq
        self.submicro_frames = submicro_frames
        self.regs = [[0] * (SIO_CHANNEL_STRIDE // 4) for _ in range(SIO_CHANNELS)]

    def read(self, offset: int) -> int:
        channel, index = self._index(offset)
        if channel is None:
            return 0
        return self.regs[channel][index]

    def write(self, offset: int, value: int) -> None:
        channel, index = self._index(offset)
        if channel is None:
            return
        regs = self.regs[channel]
        val32 = value & 0xFFFFFFFF
        if index == 0:
            status = regs[0]
            if val32 & 0x80000000:
                self.set_irq(SIO_IRQ0 + channel, False)
            if val32 & 0x40000000:
                status &= ~0x02000000
            regs[0] = (val32 & 0x1FFFFFFF) | (status & 0x02000000) | (val32 & 0x20000000)
            if (val32 & 0x20000000) and not (status & 0x20000000):
                self._exchange(channel, regs)
                self.set_irq(SIO_IRQ0 + channel, True)
            return
        regs[index] = val32

    def _exchange(self, channel: int, regs: list[int]) -> None:
        if channel != 3 or not self.submicro_frames:
            return
        length = ((regs[0x0C // 4] >> 24) + 1) & 0xFF
        if length == 0:
            length = 256
        fifo = bytearray(256)
        for index in range(64):
            word = regs[0x100 // 4 + index]
            fifo[index * 4:index * 4 + 4] = word.to_bytes(4, 'little')
        reply = submicro_reply(self.submicro_frames, bytes(fifo), length)
        merged = bytearray(fifo)
        merged[:len(reply)] = reply
        for index in range(64):
            regs[0x100 // 4 + index] = int.from_bytes(merged[index * 4:index * 4 + 4], 'little')

    def _index(self, offset: int) -> tuple[int | None, int]:
        if offset < 0 or offset >= SIO_SIZE:
            return None, 0
        return offset // SIO_CHANNEL_STRIDE, (offset % SIO_CHANNEL_STRIDE) // 4


class CxdGpio:
    """18-port GPIO. Input data is the output latch on outputs and the external level on inputs."""

    def __init__(self, set_irq: Callable[[int, bool], None], reset_high=GPIO_RESET_HIGH):
        self.set_irq = set_irq
        self.direction = [0] * GPIO_PORTS
        self.input_enable = [0] * GPIO_PORTS
        self.mode = [0] * GPIO_PORTS
        self.output = [0] * GPIO_PORTS
        self.external = [0] * GPIO_PORTS
        self.misc = [[0] * (GPIO_PORT_STRIDE // 4) for _ in range(GPIO_PORTS)]
        for port, pin in reset_high:
            if 0 <= port < GPIO_PORTS and 0 <= pin < 32:
                self.external[port] |= 1 << pin

    def read(self, offset: int) -> int:
        port, local = self._split(offset)
        if port is None:
            return 0
        if local == 0x10:
            return self._pin_in(port)
        field = self._triple(port, local)
        if field is not None and (local & 0xF) == 0:
            return field[port]
        return self.misc[port][local // 4]

    def write(self, offset: int, value: int) -> None:
        port, local = self._split(offset)
        if port is None:
            return
        val32 = value & 0xFFFFFFFF
        field = self._triple(port, local)
        before = self._pin_in(port)
        if field is None or (local & 0xF) not in (0x0, 0x4, 0x8):
            self.misc[port][local // 4] = val32
            return
        if (local & 0xF) == 0x0:
            field[port] = val32
        elif (local & 0xF) == 0x4:
            field[port] |= val32
        else:
            field[port] &= ~val32
        self._edges(port, before)

    def set_external(self, port: int, pin: int, level: bool) -> None:
        """Drive one external pin the way the board's gpio_set_ext() does."""
        if not (0 <= port < GPIO_PORTS and 0 <= pin < 32):
            return
        before = self._pin_in(port)
        bit = 1 << pin
        if level:
            self.external[port] |= bit
        else:
            self.external[port] &= ~bit
        self._edges(port, before)

    def _edges(self, port: int, before: int) -> None:
        changed = before ^ self._pin_in(port)
        pin = 0
        while changed:
            if changed & 1:
                irq = self._irq(port, pin)
                if irq is not None:
                    self.set_irq(irq, True)
            changed >>= 1
            pin += 1

    def _pin_in(self, port: int) -> int:
        return (self.output[port] & self.direction[port]) | (
            self.external[port] & ~self.direction[port])

    def _triple(self, port: int, local: int) -> list[int] | None:
        return {
            0x00: self.direction,
            0x20: self.input_enable,
            0x30: self.mode,
            0x40: self.output,
        }.get(local & 0xF0)

    def _irq(self, port: int, pin: int) -> int | None:
        if port == 0 and pin <= 15:
            return 121 + pin
        if port == 0 and pin == 16:
            return 144
        if port == 1 and pin <= 6:
            return 137 + pin
        return None

    def _split(self, offset: int) -> tuple[int | None, int]:
        if offset < 0 or offset >= GPIO_SIZE:
            return None, 0
        return offset // GPIO_PORT_STRIDE, offset % GPIO_PORT_STRIDE


class CxdAdc:
    """Two ADC units. A start completes immediately and every channel reads 1023.

    QEMU waits 50 µs. Unicorn's run slice cannot stop on that timer, so the
    start write sets the done bit before the guest polls. Released keys and a
    neutral zoom lever are the reset levels input.ko treats as 1023.
    """

    def __init__(self, set_irq: Callable[[int, bool], None]):
        self.set_irq = set_irq
        self.regs = [[0] * (ADC_MMIO // 4) for _ in range(ADC_UNITS)]
        self.result = [[1023] * ADC_CHANNELS for _ in range(ADC_UNITS)]

    def read(self, offset: int) -> int:
        unit, local = self._split(offset)
        if unit is None:
            return 0
        if 0x14 <= local < 0x14 + 4 * ADC_CHANNELS:
            return self.result[unit][(local - 0x14) // 4]
        if local == 0x10:
            return 0
        return self.regs[unit][local // 4]

    def write(self, offset: int, value: int) -> None:
        unit, local = self._split(offset)
        if unit is None:
            return
        val32 = value & 0xFFFFFFFF
        regs = self.regs[unit]
        if local == 0x0C:
            if val32 & 0x10:
                val32 &= ~0x11
                regs[0x0C // 4] &= ~1
            regs[0x0C // 4] = (regs[0x0C // 4] & 1) | (val32 & ~1)
            self._update(unit)
            return
        regs[local // 4] = val32
        if local == 0x04 and (val32 & 1):
            regs[0x0C // 4] |= 1
            self._update(unit)

    def irq_level(self, unit: int) -> bool:
        return (self.regs[unit][0x0C // 4] & 0x101) == 0x101

    def _update(self, unit: int) -> None:
        self.set_irq(ADC_IRQ0 + unit, self.irq_level(unit))

    def _split(self, offset: int) -> tuple[int | None, int]:
        if offset < 0 or offset >= ADC_SIZE:
            return None, 0
        local = offset % ADC_STRIDE
        if local >= ADC_MMIO:
            return None, 0
        return offset // ADC_STRIDE, local


class CxdSaCore:
    """Null SA DSP core. A GO completes 100 µs later with factor A bit 0.

    The ZMA1 startup-program hash and the JPEG fill stay off, matching QEMU's
    default. The 16-byte core ID is what task 44 compares.
    """

    def __init__(self, index: int, set_irq: Callable[[int, bool], None], now: Callable[[], int]):
        base, irqs, ident = SA_CORES[index]
        self.base = base
        self.irqs = irqs
        self.ident = ident
        self.set_irq = set_irq
        self.now = now
        self.regs = [0] * (SA_SIZE // 4)
        self.factor = [0, 0, 0]
        self.mask = [0, 0, 0]
        self.running = False
        self.due: int | None = None

    def read(self, offset: int, size: int = 4) -> int:
        word = self._word(offset & ~3)
        if size >= 4:
            return word
        shift = (offset & 3) * 8
        return (word >> shift) & ((1 << (size * 8)) - 1)

    def write(self, offset: int, value: int, size: int = 4) -> None:
        shift = (offset & 3) * 8
        mask = 0xFFFFFFFF if size >= 4 else ((1 << (size * 8)) - 1) << shift
        self._write_word(offset & ~3, (value & 0xFFFFFFFF) << shift, mask)

    def complete_if_due(self, now: int) -> None:
        if self.due is None or now < self.due:
            return
        self.due = None
        self.running = False
        self.regs[0x004 // 4] &= ~1
        self.factor[0] = (self.factor[0] | 0x01) & 0xFF
        self._update()

    def remaining_us(self, now: int) -> int | None:
        if self.due is None or self.due <= now:
            return None
        return self.due - now

    def levels(self) -> tuple[tuple[int, bool], ...]:
        return tuple(
            (irq, bool(self.factor[index] & self.mask[index]))
            for index, irq in enumerate(self.irqs)
        )

    def _word(self, offset: int) -> int:
        if offset in (0x084, 0x088):
            return 0
        if 0x0F0 <= offset <= 0x0FC:
            return int.from_bytes(self.ident[offset - 0x0F0:offset - 0x0F0 + 4], 'little')
        source, reg = self._source(offset)
        if source is not None:
            if reg == 0x00 or 0x20 <= reg <= 0x2C:
                return 0
            if reg == 0x04:
                return self.factor[source]
            if reg == 0x08:
                return self.mask[source]
        if 0 <= offset < SA_SIZE:
            return self.regs[offset // 4]
        return 0

    def _write_word(self, offset: int, value: int, mask: int) -> None:
        if offset == 0x004:
            self._merge(offset, value, mask)
            if (mask & 1) and (value & 1) and not self.running and (self.regs[0] & 1):
                self.running = True
                self.due = self.now() + SA_DELAY_US
            return
        if offset == 0x000:
            self._merge(offset, value, mask)
            if not (self.regs[0] & 1):
                self.running = False
                self.due = None
            return
        source, reg = self._source(offset)
        if source is not None and reg == 0x04:
            self.factor[source] = (self.factor[source] & ~(value & mask & 0xFF)) & 0xFF
            self._update()
            return
        if source is not None and reg == 0x08:
            self.mask[source] = (
                (self.mask[source] & ~(mask & 0xFF)) | (value & mask & 0xFF)
            ) & 0xFF
            self._update()
            return
        if source is not None and (reg == 0x00 or 0x20 <= reg <= 0x2C):
            return
        if 0x0F0 <= offset <= 0x0FC or offset in (0x084, 0x088):
            return
        self._merge(offset, value, mask)

    def _merge(self, offset: int, value: int, mask: int) -> None:
        current = self.regs[offset // 4]
        self.regs[offset // 4] = (current & ~mask) | (value & mask)

    def _source(self, offset: int) -> tuple[int | None, int]:
        for index, base in enumerate(SA_SRC_BASE):
            if base <= offset < base + 0x30:
                return index, offset - base
        return None, 0

    def _update(self) -> None:
        for irq, level in self.levels():
            self.set_irq(irq, level)


class CxdPeripheralBus:
    """Unified MMIO dispatcher for ported QEMU CXD90045 peripherals."""

    def __init__(
        self,
        set_irq: Callable[[int, bool], None],
        mem_read: Callable[[int, int], bytes],
        mem_write: Callable[[int, bytes], None],
        *,
        emmc_image: Path | None = None,
        submicro_frames: bytes | None = None,
    ):
        self.ldec = CxdLdec(set_irq=set_irq)
        self.xdmac0 = CxdXdmac(
            XDMAC0_BASE, XDMAC0_IRQ_BASE, set_irq, mem_read, mem_write, ldec=self.ldec
        )
        self.xdmac1 = CxdXdmac(
            XDMAC1_BASE, XDMAC1_IRQ_BASE, set_irq, mem_read, mem_write, ldec=None
        )
        self.leo_clk = CxdLeoClk(LEO_CLK_BASE)
        self.f2a = [CxdF2aClk(base) for base in F2A_BASES]
        self.sdhci = (
            CxdSdhciEmmc(set_irq, mem_read, mem_write, image_path=emmc_image)
            if emmc_image is not None
            else None
        )
        self.rw_stubs = [
            CxdRwStub('dfs_ctrl', DFS_CTRL_BASE, 0x1000),
            CxdRwStub('audioip', AUDIOIP_BASE, 0x8000),
            CxdImagerWindow('imager', IMAGER_BASE, 0x200000),
            CxdRwStub('clkgen', CLKGEN_BASE, CLKGEN_SIZE),
        ]
        self.evsel = CxdEvsel()
        self.vph = CxdSysvVph(mem_read)
        self.sio = CxdSio(set_irq, submicro_frames)
        self.gpio = CxdGpio(set_irq)
        self.adc = CxdAdc(set_irq)
        self.sa = [
            CxdSaCore(index, set_irq, lambda: self.vph._us)
            for index in range(len(SA_CORES))
        ]
        self.cx_timer = CxdCxTimer(set_irq, lambda: self.vph._us)
        self.p2s = CxdP2s(set_irq, lambda: self.vph._us)
        # Sub-micro strobe P0.8: first rising edge at 50 ms, then 1 ms high
        # and 49 ms low. P2.5 (request) stays low.
        self._strobe_high = False
        self._strobe_due = 50_000
        # Ordered from highest priority / most specific to lowest.
        self._devices: list[tuple[int, int, object]] = [
            (VPH_BASE, VPH_SIZE, self.vph),
            (SYSV_BASE, SYSV_SIZE, self.vph),
            (EVSEL_BASE, EVSEL_SIZE, self.evsel),
            (SIO_BASE, SIO_SIZE, self.sio),
            (GPIO_BASE, GPIO_SIZE, self.gpio),
            (ADC_BASE, ADC_SIZE, self.adc),
            (CxdCxTimer.BASE, CxdCxTimer.SIZE, self.cx_timer),
            (CxdP2s.BASE, CxdP2s.SIZE, self.p2s),
            *[(core.base, SA_SIZE, core) for core in self.sa],
            (XDMAC0_BASE, 0x10000, self.xdmac0),
            (XDMAC1_BASE, 0x10000, self.xdmac1),
            (LDEC_BASE, 0x1000, self.ldec),
            (LEO_CLK_BASE, 0x1000, self.leo_clk),
            (DWC3_BASE, DWC3_SIZE, CxdZeroMmio('dwc3_unmodelled', DWC3_BASE, DWC3_SIZE)),
        ]
        if self.sdhci is not None:
            self._devices.append((EMMC0_BASE, EMMC0_SIZE, self.sdhci))
        for dev in self.f2a:
            self._devices.append((dev.base, dev.size, dev))
        for dev in self.rw_stubs:
            self._devices.append((dev.base, dev.size, dev))

    def advance_us(self, dt: int) -> set[int]:
        """Advance the shared 1 MHz clock. SYSV frames call evsel with ext-vd."""
        def on_frame() -> None:
            self.evsel.set_us(self.vph._us)
            self.evsel.on_engine_frame()

        pulses = self.vph.advance_us(dt, on_frame=on_frame)
        self.evsel.set_us(self.vph._us)
        while self.vph._us >= self._strobe_due:
            self._strobe_high = not self._strobe_high
            self.gpio.set_external(0, 8, self._strobe_high)
            self._strobe_due += 1_000 if self._strobe_high else 49_000
        for core in self.sa:
            core.complete_if_due(self.vph._us)
        self.cx_timer.complete_if_due(self.vph._us)
        self.p2s.complete_if_due(self.vph._us)
        return pulses

    def us_until_event(self) -> int:
        waits = [self.vph.us_until_event(), self._strobe_due - self.vph._us]
        for core in self.sa:
            remaining = core.remaining_us(self.vph._us)
            if remaining is not None:
                waits.append(remaining)
        timer_wait = self.cx_timer.remaining_us(self.vph._us)
        if timer_wait is not None:
            waits.append(timer_wait)
        p2s_wait = self.p2s.remaining_us(self.vph._us)
        if p2s_wait is not None:
            waits.append(p2s_wait)
        return max(1, min(waits))

    def level_irqs(self) -> tuple[tuple[int, bool], ...]:
        return (
            (IRQ_EVSEL_A, self.evsel.irq_a()),
            (IRQ_EVSEL_B, self.evsel.irq_b()),
            (ADC_IRQ0, self.adc.irq_level(0)),
            (ADC_IRQ0 + 1, self.adc.irq_level(1)),
            *[level for core in self.sa for level in core.levels()],
            *self.cx_timer.levels(),
            (CxdP2s.IRQ, self.p2s.irq_level()),
        )

    def _lookup(self, address: int) -> tuple[object, int] | None:
        for base, size, dev in self._devices:
            if base <= address < base + size:
                return dev, address - base
        return None

    def read(self, address: int, size: int = 4) -> int | None:
        hit = self._lookup(address)
        if hit is None:
            return None
        dev, offset = hit
        if isinstance(dev, CxdSdhciEmmc):
            return dev.read(offset, size)
        if dev is self.vph:
            if VPH_BASE <= address < VPH_BASE + VPH_SIZE:
                return self.vph.read_vph(offset)
            return self.vph.read_sysv(offset)
        if isinstance(dev, CxdSaCore):
            return dev.read(offset, size)
        return dev.read(offset)

    def write(self, address: int, value: int, size: int = 4) -> bool:
        hit = self._lookup(address)
        if hit is None:
            return False
        dev, offset = hit
        if isinstance(dev, CxdSdhciEmmc):
            dev.write(offset, value, size)
            return True
        if dev is self.vph:
            if VPH_BASE <= address < VPH_BASE + VPH_SIZE:
                self.vph.write_vph(offset, value)
            else:
                self.vph.write_sysv(offset, value)
            return True
        if isinstance(dev, CxdSaCore):
            dev.write(offset, value, size)
        else:
            dev.write(offset, value)
        return True
