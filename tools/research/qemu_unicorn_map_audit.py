"""Audit memory-map and peripheral parity between QEMU sony_a6400.c and UnicornMachine."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.a6400.machine import soc_cxd90045  # noqa: E402


# Verified peripheral blocks in QEMU sony_a6400.c with their physical base, size,
# IRQ list, and current UnicornMachine parity status.
QEMU_PERIPHERAL_CATALOG = (
    {
        'name': 'a9mpcore_gic',
        'base': 0xF8000000,
        'size': 0x2000,
        'irqs': [],
        'model': 'a9mpcore_priv (GIC CPU 0xF8000100, Dist 0xF8001000)',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'l2c_stub',
        'base': 0xF8002000,
        'size': 0x1000,
        'irqs': [],
        'model': 'cxd90045_l2c_stub (+0x77C -> 0x100)',
        'unicorn_status': 'stubbed',
    },
    {
        'name': 'uart0_pl011',
        'base': 0xF2000000,
        'size': 0x1000,
        'irqs': [150],
        'model': 'pl011',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'cxd4115_timer',
        'base': 0xF2403000,
        'size': 0x1000,
        'irqs': list(range(153, 169)),
        'model': 'cxd4115-timer (16 channels)',
        'unicorn_status': 'partial',
    },
    {
        'name': 'crcdc',
        'base': 0xF2A31000,
        'size': 0x1000,
        'irqs': [],
        'model': 'cxd90045_crcdc (+0x30 bit0 ready)',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'gpes_smaph_s30',
        'base': 0xF1000000,
        'size': 0x4000,
        'irqs': [76],
        'model': 'dmp-smaph-s30 (PSC/PPF/P3D)',
        'unicorn_status': 'partial',
    },
    {
        'name': 'xdmac0',
        'base': 0xF0000000,
        'size': 0x10000,
        'irqs': [189, 190, 191, 192],
        'model': 'cxd90045_xdmac (4-ch DMA + LDEC feed)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'xdmac1',
        'base': 0xF0010000,
        'size': 0x10000,
        'irqs': [193, 194, 195, 196],
        'model': 'cxd90045_xdmac (4-ch DMA)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'ldec_in',
        'base': 0xF0020000,
        'size': 0x10000,
        'irqs': [],
        'model': 'cxd90045_ldec DMA target aperture',
        'unicorn_status': 'missing',
    },
    {
        'name': 'ldec',
        'base': 0xF2903000,
        'size': 0x1000,
        'irqs': [171],
        'model': 'cxd90045_ldec (LZ77 hardware decompressor)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'emmc0_sdhci',
        'base': 0xF0304000,
        'size': 0x1000,
        'irqs': [227],
        'model': 'sysbus-sdhci + emmc',
        'unicorn_status': 'missing',
    },
    {
        'name': 'sio',
        'base': 0xF2405000,
        'size': 0x1000,
        'irqs': [201, 202, 203, 204, 205],
        'model': 'cxd90014-sio (5 channels, ch3 sub-micro)',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'adc0_1',
        'base': 0xF2406000,
        'size': 0x2000,
        'irqs': [215, 216],
        'model': 'cxd90014-adc (units 0 and 1)',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'gpio',
        'base': 0xF2910000,
        'size': 0x1000,
        'irqs': list(range(121, 145)),
        'model': 'cxd90014-gpio',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'dfs_ctrl',
        'base': 0xF2916000,
        'size': 0x1000,
        'irqs': [],
        'model': 'cxd90045_rw_stub',
        'unicorn_status': 'missing',
    },
    {
        'name': 'usb_vbus_status',
        'base': 0xF2920010,
        'size': 0x4,
        'irqs': [230],
        'model': 'sony_usb_vbus (opt-in)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'f2a_clk_blocks',
        'base': 0xF2A01000,
        'size': 0x30000,
        'irqs': [],
        'model': 'cxd90045_f2a01 (0xF2A01000/11000/12000/21000/22000/30000)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'sgc1_event_a',
        'base': 0xF3120000,
        'size': 0x300,
        'irqs': [35],
        'model': 'sony_sgc1 (opt-in)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'sugi_clk',
        'base': 0xF3400000,
        'size': 0x10000,
        'irqs': [],
        'model': 'cxd90045_rw_stub (+0x80 = 0x15)',
        'unicorn_status': 'missing',
    },
    {
        'name': 'sysv_vph',
        'base': 0xF3402000,
        'size': 0x40000,
        'irqs': [98, 101, 102],
        'model': 'cxd90014-vph (SYSV 0xF3402000, VPH 0xF3440000)',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'audioip',
        'base': 0xF3420000,
        'size': 0x8000,
        'irqs': [],
        'model': 'cxd90014-regfile',
        'unicorn_status': 'missing',
    },
    {
        'name': 'sa_cores',
        'base': 0xF34A0000,
        'size': 0x130000,
        'irqs': [51, 52, 53, 54, 55, 56, 61, 62, 63, 64, 65, 66],
        'model': 'cxd90014-sa (4 DSP cores)',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'p2s',
        'base': 0xF3C00000,
        'size': 0x1000,
        'irqs': [38],
        'model': 'cxd90014-p2s',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'evsel',
        'base': 0xF3C30000,
        'size': 0x1000,
        'irqs': [83, 84],
        'model': 'cxd90014-evsel',
        'unicorn_status': 'modeled',
    },
    {
        'name': 'imager_leo_clk',
        'base': 0xFC000000,
        'size': 0x200000,
        'irqs': [],
        'model': 'cxd90014-regfile + leo_clk (0xFC021000)',
        'unicorn_status': 'missing',
    },
)


def extract_qemu_defines(source: str) -> dict[str, int]:
    """Extract hex constants from sony_a6400.c."""
    defines = {}
    for name, val in re.findall(r'#define\s+([A-Z0-9_]+)\s+(0x[0-9A-Fa-f]+)(?:ULL)?', source):
        defines[name] = int(val, 16)
    return defines


def build_audit(sony_c_path: Path) -> dict:
    """Build a structured parity report between sony_a6400.c and soc_cxd90045.py."""
    text = sony_c_path.read_text(encoding='utf-8')
    defines = extract_qemu_defines(text)
    counts = {'modeled': 0, 'partial': 0, 'stubbed': 0, 'missing': 0}
    entries = []
    for item in QEMU_PERIPHERAL_CATALOG:
        counts[item['unicorn_status']] += 1
        entries.append({
            'name': item['name'],
            'base': f"0x{item['base']:08x}",
            'size': f"0x{item['size']:x}",
            'irqs': item['irqs'],
            'model': item['model'],
            'unicorn_status': item['unicorn_status'],
        })
    return {
        'qemu_source': str(sony_c_path.relative_to(ROOT).as_posix()),
        'unicorn_soc': 'src/a6400/machine/soc_cxd90045.py',
        'qemu_defines_count': len(defines),
        'soc_constants': {
            'UART0_BASE': f'0x{soc_cxd90045.UART0_BASE:08x}',
            'GIC_DIST_BASE': f'0x{soc_cxd90045.GIC_DIST_BASE:08x}',
            'GIC_CPU_BASE': f'0x{soc_cxd90045.GIC_CPU_BASE:08x}',
            'TIMER_BASE': f'0x{soc_cxd90045.TIMER_BASE:08x}',
            'CRCDC_BASE': f'0x{soc_cxd90045.CRCDC_BASE:08x}',
            'GPES_BAR_BASE': f'0x{soc_cxd90045.GPES_BAR_BASE:08x}',
        },
        'status_counts': counts,
        'peripherals': entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--sony-c',
        type=Path,
        default=ROOT / 'qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c',
    )
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args()
    report = build_audit(args.sony_c)
    rendered = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(json.dumps(report['status_counts'], indent=2))
    for item in report['peripherals']:
        print(f"  {item['unicorn_status']:8s} {item['base']} ({item['size']:>8s}) {item['name']}: {item['model']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
