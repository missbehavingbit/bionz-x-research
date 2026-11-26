"""Parse QEMU TCG oracle plugin v2 JSONL traces, synthesize MMIO contracts, and run 5ms Unicorn micro-replays."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REG_NAMES = (
    'r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7',
    'r8', 'r9', 'r10', 'r11', 'r12', 'sp', 'lr', 'cpsr',
)


def _parse_hex(value: str | int) -> int:
    if isinstance(value, int):
        return value
    return int(value, 0)


def parse_plugin_jsonl(path: Path, *, window: int | None = None) -> dict:
    """Parse JSONL emitted by qemu-a6400/tools/unicorn_oracle_plugin.c (v1/v2/v3)."""
    insns: list[dict] = []
    mmio: list[dict] = []
    polls: list[dict] = []
    pages: dict[int, bytes] = {}
    windows_seen: set[int] = set()
    summaries: list[dict] = []
    for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        if not line or not line.startswith('{'):
            continue
        event = json.loads(line)
        kind = event.get('type')
        win = int(event['win']) if 'win' in event else 0
        if kind == 'insn':
            windows_seen.add(win)
            if window is not None and win != window:
                continue
            row = {'win': win, 'cpu': int(event['cpu']), 'pc': _parse_hex(event['pc'])}
            if 'pa' in event:
                row['pa'] = _parse_hex(event['pa'])
            if 'bytes' in event:
                row['bytes'] = event['bytes']
            if 'size' in event:
                row['size'] = int(event['size'])
            if 'regs' in event:
                row['regs'] = [int(v) & 0xFFFFFFFF for v in event['regs']]
            insns.append(row)
        elif kind == 'mmio':
            mmio.append({
                'cpu': int(event['cpu']),
                'pc': _parse_hex(event['pc']),
                'op': event['op'],
                'pa': _parse_hex(event['pa']),
                'va': _parse_hex(event['va']),
                'val': _parse_hex(event['val']),
                'dev': event.get('dev', 'unknown'),
            })
        elif kind == 'poll_summary':
            polls.append({
                'cpu': int(event['cpu']),
                'pc': _parse_hex(event['pc']),
                'pa': _parse_hex(event['pa']),
                'dev': event.get('dev', 'unknown'),
                'count': int(event['count']),
                'last_val': _parse_hex(event['last_val']),
            })
        elif kind == 'page':
            if window is not None and win != window:
                continue
            pages[_parse_hex(event['vaddr'])] = bytes.fromhex(event['hex'])
        elif kind == 'summary':
            summaries.append(event)

    read_addrs: dict[int, int] = {}
    for ev in mmio:
        if ev['op'].startswith('R'):
            read_addrs[ev['pa']] = ev['val']
    for poll in polls:
        read_addrs[poll['pa']] = poll['last_val']

    return {
        'insn_count': len(insns),
        'mmio_count': len(mmio),
        'poll_count': len(polls),
        'page_count': len(pages),
        'windows': sorted(windows_seen),
        'insns': insns,
        'mmio': mmio,
        'polls': polls,
        'pages': {f'0x{k:08x}': v for k, v in sorted(pages.items())},
        'summaries': summaries,
        'observed_mmio_reads': {f'0x{pa:08x}': f'0x{val:08x}' for pa, val in sorted(read_addrs.items())},
    }


def synthesize_mmio_contracts(parsed: dict) -> dict:
    """Classify MMIO registers from QEMU plugin captures into candidate contracts."""
    by_pa: dict[int, list[dict]] = defaultdict(list)
    for ev in parsed['mmio']:
        by_pa[ev['pa']].append(ev)
    polls_by_pa: dict[int, list[dict]] = defaultdict(list)
    for poll in parsed['polls']:
        polls_by_pa[poll['pa']].append(poll)

    contracts = []
    for pa in sorted(set(by_pa) | set(polls_by_pa)):
        events = by_pa.get(pa, [])
        reads = [e['val'] for e in events if e['op'].startswith('R')]
        writes = [e['val'] for e in events if e['op'].startswith('W')]
        devs = sorted({e['dev'] for e in events} | {p['dev'] for p in polls_by_pa.get(pa, [])})
        if pa in polls_by_pa:
            classification = 'poll_target'
            suggested = polls_by_pa[pa][-1]['last_val']
        elif writes and reads and reads[-1] == writes[-1]:
            classification = 'readback'
            suggested = reads[-1]
        elif writes and reads and reads[-1] == 0 and writes[-1] != 0:
            classification = 'self_clearing_or_unmodelled'
            suggested = 0
        elif reads:
            classification = 'status_or_const'
            suggested = reads[-1]
        else:
            classification = 'write_only'
            suggested = None
        contracts.append({
            'pa': f'0x{pa:08x}',
            'devices': devs,
            'classification': classification,
            'reads': len(reads),
            'writes': len(writes),
            'polls': sum(p['count'] for p in polls_by_pa.get(pa, [])),
            'suggested_read_value': f'0x{suggested:08x}' if suggested is not None else None,
        })
    return {'register_count': len(contracts), 'contracts': contracts}


def micro_replay(parsed: dict, *, cpu: int = 0) -> dict:
    """Replay captured QEMU working-set pages + initial registers in Unicorn in ~5ms."""
    from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc, UcError, arm_const

    uc_regs = [getattr(arm_const, f'UC_ARM_REG_{name.upper()}') for name in REG_NAMES]
    insns = [row for row in parsed['insns'] if row['cpu'] == cpu]
    if not insns or 'regs' not in insns[0]:
        return {'status': 'incomparable', 'reason': 'trace_missing_registers'}
    if not parsed['pages']:
        return {'status': 'incomparable', 'reason': 'trace_missing_pages'}

    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    try:
        uc.ctl_set_cpu_model(arm_const.UC_CPU_ARM_CORTEX_A9)
    except Exception:
        pass

    for page_hex, data in parsed['pages'].items():
        addr = int(page_hex, 16)
        uc.mem_map(addr, 0x1000, 7)
        uc.mem_write(addr, data)

    init_regs = insns[0]['regs']
    for idx, val in enumerate(init_regs):
        uc.reg_write(uc_regs[idx], val)
    uc.reg_write(arm_const.UC_ARM_REG_PC, insns[0]['pc'])
    cpsr0 = init_regs[15]
    thumb = bool(cpsr0 & 0x20)

    matched = 0
    for idx, row in enumerate(insns):
        pc = row['pc']
        u_pc = uc.reg_read(arm_const.UC_ARM_REG_PC) & ~1
        if u_pc != pc:
            return {
                'status': 'diverged',
                'step': idx,
                'reason': 'pc_mismatch',
                'qemu_pc': f'0x{pc:08x}',
                'unicorn_pc': f'0x{u_pc:08x}',
            }
        u_regs = [uc.reg_read(reg) & 0xFFFFFFFF for reg in uc_regs]
        diffs = {
            REG_NAMES[i]: [f'0x{u_regs[i]:08x}', f'0x{row["regs"][i]:08x}']
            for i in range(16) if u_regs[i] != row['regs'][i]
        }
        if diffs:
            return {
                'status': 'diverged',
                'step': idx,
                'reason': 'register_mismatch',
                'pc': f'0x{pc:08x}',
                'diffs': diffs,
            }
        matched += 1
        if idx + 1 >= len(insns):
            break
        start_addr = pc | (1 if (u_regs[15] & 0x20) or thumb else 0)
        try:
            uc.emu_start(start_addr, 0xFFFFFFFF, count=1)
        except UcError as error:
            return {
                'status': 'unicorn_error',
                'step': idx,
                'pc': f'0x{pc:08x}',
                'error': str(error),
            }

    return {'status': 'matched', 'steps': matched, 'entry_pc': f'0x{insns[0]["pc"]:08x}'}


def generate_unittest(parsed: dict, *, test_name: str = 'GeneratedOracleTest', cpu: int = 0) -> str:
    """Compile a captured QEMU Oracle window into a standalone Python unittest."""
    import base64
    import zlib

    insns = [row for row in parsed['insns'] if row['cpu'] == cpu]
    if not insns or 'regs' not in insns[0]:
        raise ValueError('Trace has no register-annotated instructions')
    if not parsed['pages']:
        raise ValueError('Trace has no captured memory pages')

    pages_b64 = {
        addr: base64.b64encode(zlib.compress(data, level=9)).decode('ascii')
        for addr, data in parsed['pages'].items()
    }
    steps = [{'pc': row['pc'], 'regs': row['regs']} for row in insns]
    lines = [
        f'"""Auto-generated QEMU-to-Unicorn lockstep parity test ({test_name})."""',
        'import base64',
        'import unittest',
        'import zlib',
        'from unicorn import UC_ARCH_ARM, UC_MODE_ARM, Uc, arm_const',
        '',
        f'REG_NAMES = {REG_NAMES!r}',
        f'PAGES_ZLIB_B64 = {pages_b64!r}',
        f'STEPS = {steps!r}',
        '',
        '',
        f'class {test_name}(unittest.TestCase):',
        '    def test_lockstep_parity(self):',
        '        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)',
        '        try:',
        '            uc.ctl_set_cpu_model(arm_const.UC_CPU_ARM_CORTEX_A9)',
        '        except Exception:',
        '            pass',
        '        for addr_hex, b64 in PAGES_ZLIB_B64.items():',
        '            addr = int(addr_hex, 16)',
        '            uc.mem_map(addr, 0x1000, 7)',
        '            uc.mem_write(addr, zlib.decompress(base64.b64decode(b64)))',
        '        uc_regs = [getattr(arm_const, f"UC_ARM_REG_{n.upper()}") for n in REG_NAMES]',
        '        for idx, val in enumerate(STEPS[0]["regs"]):',
        '            uc.reg_write(uc_regs[idx], val)',
        '        uc.reg_write(arm_const.UC_ARM_REG_PC, STEPS[0]["pc"])',
        '        thumb = bool(STEPS[0]["regs"][15] & 0x20)',
        '        for step_idx, expected in enumerate(STEPS):',
        '            self.assertEqual(uc.reg_read(arm_const.UC_ARM_REG_PC) & ~1, expected["pc"], f"step {step_idx} PC")',
        '            for r_idx, exp_val in enumerate(expected["regs"]):',
        '                self.assertEqual(uc.reg_read(uc_regs[r_idx]) & 0xFFFFFFFF, exp_val, f"step {step_idx} {REG_NAMES[r_idx]}")',
        '            if step_idx + 1 < len(STEPS):',
        '                cpsr = uc.reg_read(arm_const.UC_ARM_REG_CPSR)',
        '                uc.emu_start(expected["pc"] | (1 if (cpsr & 0x20) or thumb else 0), 0xFFFFFFFF, count=1)',
        '',
        '',
        'if __name__ == "__main__":',
        '    unittest.main()',
        '',
    ]
    return '\n'.join(lines)


def diff_pc_traces(
    qemu_pcs: list[int],
    unicorn_pcs: list[int],
    *,
    relative: bool = False,
    context: int = 5,
) -> dict:
    """Compare ordered PC sequences from QEMU and Unicorn and locate first divergence."""
    if not qemu_pcs or not unicorn_pcs:
        return {
            'status': 'incomparable',
            'reason': 'empty_trace',
            'qemu_len': len(qemu_pcs),
            'unicorn_len': len(unicorn_pcs),
        }

    q_base = qemu_pcs[0] if relative else 0
    u_base = unicorn_pcs[0] if relative else 0
    q_norm = [pc - q_base for pc in qemu_pcs]
    u_norm = [pc - u_base for pc in unicorn_pcs]

    shared = min(len(q_norm), len(u_norm))
    for idx in range(shared):
        if q_norm[idx] != u_norm[idx]:
            start = max(0, idx - context)
            return {
                'status': 'diverged',
                'relative': relative,
                'first_divergence_index': idx,
                'qemu_entry': f'0x{qemu_pcs[0]:08x}',
                'unicorn_entry': f'0x{unicorn_pcs[0]:08x}',
                'qemu_pc': f'0x{qemu_pcs[idx]:08x}',
                'unicorn_pc': f'0x{unicorn_pcs[idx]:08x}',
                'qemu_offset': f'0x{q_norm[idx]:x}',
                'unicorn_offset': f'0x{u_norm[idx]:x}',
                'preceding_offsets': [f'0x{v:x}' for v in q_norm[start:idx]],
            }

    return {
        'status': 'matched_prefix',
        'relative': relative,
        'matched_count': shared,
        'qemu_len': len(qemu_pcs),
        'unicorn_len': len(unicorn_pcs),
        'qemu_entry': f'0x{qemu_pcs[0]:08x}',
        'unicorn_entry': f'0x{unicorn_pcs[0]:08x}',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_parse = sub.add_parser('parse-plugin', help='Summarize unicorn_oracle_plugin.so JSONL')
    p_parse.add_argument('jsonl', type=Path)
    p_parse.add_argument('--output', type=Path, default=None)

    p_synth = sub.add_parser('synthesize-mmio', help='Synthesize MMIO contracts from QEMU plugin JSONL')
    p_synth.add_argument('jsonl', type=Path)
    p_synth.add_argument('--output', type=Path, default=None)

    p_replay = sub.add_parser('micro-replay', help='Run 5ms Unicorn lockstep replay on QEMU micro-snapshot')
    p_replay.add_argument('jsonl', type=Path)
    p_replay.add_argument('--cpu', type=int, default=0)
    p_replay.add_argument('--window', type=int, default=None)
    p_replay.add_argument('--all-windows', action='store_true')

    p_gen = sub.add_parser('generate-test', help='Compile a QEMU Oracle window into a Python unittest')
    p_gen.add_argument('jsonl', type=Path)
    p_gen.add_argument('--window', type=int, default=0)
    p_gen.add_argument('--cpu', type=int, default=0)
    p_gen.add_argument('--name', default='GeneratedOracleTest')
    p_gen.add_argument('--output', type=Path, required=True)

    p_diff = sub.add_parser('diff-trace', help='Diff QEMU plugin insn JSONL against Unicorn PC JSON')
    p_diff.add_argument('qemu_jsonl', type=Path)
    p_diff.add_argument('unicorn_pcs_json', type=Path)
    p_diff.add_argument('--relative', action='store_true')
    p_diff.add_argument('--cpu', type=int, default=0)
    p_diff.add_argument('--output', type=Path, default=None)

    args = parser.parse_args()
    if args.cmd == 'parse-plugin':
        result = parse_plugin_jsonl(args.jsonl)
        summary = {k: v for k, v in result.items() if k not in ('insns', 'mmio', 'pages')}
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(summary, indent=2))
        return 0

    if args.cmd == 'synthesize-mmio':
        synth = synthesize_mmio_contracts(parse_plugin_jsonl(args.jsonl))
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(synth, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(synth, indent=2))
        return 0

    if args.cmd == 'micro-replay':
        if args.all_windows:
            base = parse_plugin_jsonl(args.jsonl)
            results = {}
            all_ok = True
            for win in base['windows']:
                res = micro_replay(parse_plugin_jsonl(args.jsonl, window=win), cpu=args.cpu)
                results[win] = res
                if res.get('status') != 'matched':
                    all_ok = False
            print(json.dumps(results, indent=2))
            return 0 if all_ok else 2
        res = micro_replay(parse_plugin_jsonl(args.jsonl, window=args.window), cpu=args.cpu)
        print(json.dumps(res, indent=2))
        return 0 if res.get('status') == 'matched' else 2

    if args.cmd == 'generate-test':
        parsed = parse_plugin_jsonl(args.jsonl, window=args.window)
        code = generate_unittest(parsed, test_name=args.name, cpu=args.cpu)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(code, encoding='utf-8')
        print(json.dumps({'generated': str(args.output), 'steps': parsed['insn_count'], 'pages': parsed['page_count']}))
        return 0

    parsed = parse_plugin_jsonl(args.qemu_jsonl)
    qemu_pcs = [row['pc'] for row in parsed['insns'] if row['cpu'] == args.cpu]
    raw_u = json.loads(args.unicorn_pcs_json.read_text(encoding='utf-8'))
    unicorn_pcs = [_parse_hex(v) for v in (raw_u.get('pcs', raw_u) if isinstance(raw_u, dict) else raw_u)]
    diff = diff_pc_traces(qemu_pcs, unicorn_pcs, relative=args.relative)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(diff, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(diff, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
