"""Unit tests for tools.research.qemu_unicorn_oracle."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.research.qemu_unicorn_oracle import (
    diff_pc_traces,
    micro_replay,
    parse_plugin_jsonl,
    synthesize_mmio_contracts,
)


class QemuUnicornOracleTests(unittest.TestCase):
    def test_parse_plugin_jsonl_extracts_insns_mmio_and_polls(self):
        lines = [
            json.dumps({'type': 'init', 'status': 'installed'}),
            json.dumps({'type': 'insn', 'cpu': 0, 'sample': 1, 'pc': '0x00108000'}),
            json.dumps({'type': 'insn', 'cpu': 0, 'sample': 2, 'pc': '0x00108004'}),
            json.dumps({'type': 'mmio', 'cpu': 0, 'sample': 1, 'pc': '0x00001010',
                        'op': 'W4', 'pa': '0xf8001820', 'va': '0xf8001820', 'val': '0x01010101'}),
            json.dumps({'type': 'poll_summary', 'cpu': 0, 'pc': '0x60100100',
                        'pa': '0xf2a31030', 'count': 12, 'last_val': '0x00000001'}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'oracle.jsonl'
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            parsed = parse_plugin_jsonl(path)
        self.assertEqual(parsed['insn_count'], 2)
        self.assertEqual(parsed['mmio_count'], 1)
        self.assertEqual(parsed['poll_count'], 1)
        self.assertEqual(parsed['observed_mmio_reads'], {'0xf2a31030': '0x00000001'})

    def test_diff_pc_traces_relative_detects_branch_divergence(self):
        qemu_pcs = [0x04040790, 0x04040794, 0x04040798, 0x04043AA8]
        unicorn_pcs = [0x0400C790, 0x0400C794, 0x0400C798, 0x0400C7A0]
        diff = diff_pc_traces(qemu_pcs, unicorn_pcs, relative=True)
        self.assertEqual(diff['status'], 'diverged')
        self.assertEqual(diff['first_divergence_index'], 3)
        self.assertEqual(diff['qemu_offset'], '0x3318')
        self.assertEqual(diff['unicorn_offset'], '0x10')

    def test_diff_pc_traces_matched_prefix(self):
        diff = diff_pc_traces([0x100, 0x104, 0x108], [0x100, 0x104])
        self.assertEqual(diff['status'], 'matched_prefix')
        self.assertEqual(diff['matched_count'], 2)

    def test_synthesize_mmio_contracts_classifies_readback_and_poll(self):
        parsed = {
            'mmio': [
                {'cpu': 0, 'pc': 0x100, 'op': 'W4', 'pa': 0xF2A01004, 'va': 0xF2A01004, 'val': 0x2, 'dev': 'f2a_clk'},
                {'cpu': 0, 'pc': 0x104, 'op': 'R4', 'pa': 0xF2A01004, 'va': 0xF2A01004, 'val': 0x2, 'dev': 'f2a_clk'},
                {'cpu': 0, 'pc': 0x108, 'op': 'W4', 'pa': 0xF020C110, 'va': 0xF020C110, 'val': 0x800, 'dev': 'unmodelled'},
                {'cpu': 0, 'pc': 0x10C, 'op': 'R4', 'pa': 0xF020C110, 'va': 0xF020C110, 'val': 0x0, 'dev': 'unmodelled'},
            ],
            'polls': [
                {'cpu': 0, 'pc': 0x200, 'pa': 0xFC021050, 'dev': 'leo_clk', 'count': 50, 'last_val': 0x1},
            ],
        }
        synth = synthesize_mmio_contracts(parsed)
        by_pa = {c['pa']: c for c in synth['contracts']}
        self.assertEqual(by_pa['0xf2a01004']['classification'], 'readback')
        self.assertEqual(by_pa['0xf020c110']['classification'], 'self_clearing_or_unmodelled')
        self.assertEqual(by_pa['0xfc021050']['classification'], 'poll_target')
        self.assertEqual(by_pa['0xfc021050']['suggested_read_value'], '0x00000001')

    def test_micro_replay_lockstep_matches_mov_add(self):
        # mov r0, #1 (0xe3a00001); add r0, r0, #2 (0xe2800002)
        page = bytearray(4096)
        page[0:8] = bytes.fromhex('0100a0e3020080e2')
        regs0 = [0] * 16
        regs0[15] = 0x13  # SVC ARM
        regs1 = list(regs0)
        regs1[0] = 1
        parsed = {
            'pages': {'0x00108000': bytes(page)},
            'insns': [
                {'cpu': 0, 'pc': 0x00108000, 'regs': regs0},
                {'cpu': 0, 'pc': 0x00108004, 'regs': regs1},
            ],
        }
        res = micro_replay(parsed)
        self.assertEqual(res['status'], 'matched')
        self.assertEqual(res['steps'], 2)


if __name__ == '__main__':
    unittest.main()
