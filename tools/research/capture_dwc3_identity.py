"""Read fixed DWC3 identity words within one page in MSC; never write MMIO."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from tools.camera_debugger import connect, OP_EXEC, OP_INFO

ROOT = Path(__file__).resolve().parents[2]
COMMAND = 'dd if=/dev/mem bs=256 skip=15737025 count=1 2>/dev/null'
REGISTERS = {'GSNPSID': 0x120, **{f'GHWPARAMS{i}': 0x140 + 4*i for i in range(8)},
             'GHWPARAMS8': 0x600}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--helper', type=Path, help='fixed read-only ARM identity helper')
    parser.add_argument('--gadget-gate', action='store_true',
                        help='use the fixed one-word F0200024 gate helper instead of identity words')
    args = parser.parse_args()
    if args.gadget_gate and not args.helper:
        raise ValueError('The gadget-gate observation requires its fixed helper')
    output = args.report_dir.resolve()
    if not output.is_relative_to((ROOT / 'artifacts').resolve()):
        raise ValueError('Reports must stay under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    report = {'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'physical_page': '0xf0200000' if args.gadget_gate else '0xf020c000',
              'mapped_bytes': 4096 if args.helper else 0,
              'output_bytes': 4 if args.gadget_gate else (40 if args.helper else 256), 'mmio_writes': False,
              'ram_helper_staging': bool(args.helper),
              'mode_switches': False, 'commands': [], 'status': 'started'}
    try:
        with connect() as debugger:
            def request(op, command='', name=''):
                result = debugger.request(op, command.encode())
                raw = result['payload']
                (output / name).write_bytes(raw)
                report['commands'].append({'op': op, 'command': command, 'file': name,
                    'status': result['status'], 'flags': result['flags'], 'bytes': len(raw),
                    'sha256': hashlib.sha256(raw).hexdigest()})
                if result['status'] or result['flags']:
                    raise RuntimeError(f'Incomplete or failed camera request: {name}')
                return raw

            request(OP_INFO, name='info.txt')
            before = request(OP_EXEC, 'cat /proc/uptime', 'uptime-before.txt')
            request(OP_EXEC, 'cat /proc/version; cat /proc/cmdline; ls -la /tmp_bt; '
                    'for f in /sys/class/udc/*/state; do echo $f; cat $f; done', 'state-before.txt')
            try:
                if args.helper:
                    payload = args.helper.read_bytes()
                    if len(payload) > 8192:
                        raise ValueError('Identity helper must be at most 8 KiB')
                    helper_hash = hashlib.sha256(payload).hexdigest()
                    remote = '/tmp_bt/dwc3_identity_' + helper_hash[:16]
                    request(OP_EXEC, f'test ! -e {remote}', 'helper-absence.txt')
                    try:
                        report['helper'] = debugger.push(remote, payload)
                        request(OP_EXEC, f'chmod 700 {remote}', 'helper-permission.txt')
                        raw = request(OP_EXEC, remote, 'identity.bin')
                    finally:
                        request(OP_EXEC, f'rm -f {remote}', 'helper-cleanup.txt')
                    if len(raw) != (4 if args.gadget_gate else 40):
                        raise RuntimeError('Short DWC3 identity words')
                    report['registers'] = (
                        {'F0200024': f'0x{struct.unpack("<I",raw)[0]:08x}'} if args.gadget_gate else
                        {name: f'0x{word:08x}' for name, word in zip(REGISTERS, struct.unpack('<10I', raw))})
                else:
                    raw = request(OP_EXEC, COMMAND, 'identity.bin')
                    if len(raw) != 256:
                        raise RuntimeError('Short DWC3 identity window')
                    report['registers'] = {name: f'0x{struct.unpack_from("<I", raw, offset-0x100)[0]:08x}'
                                           for name, offset in REGISTERS.items() if name != 'GHWPARAMS8'}
                report['status'] = 'captured'
            finally:
                after = request(OP_EXEC, 'cat /proc/uptime', 'uptime-after.txt')
                report['uptime_monotonic'] = float(after.split()[0]) >= float(before.split()[0])
                request(OP_EXEC, 'ls -la /tmp_bt', 'tmp-after.txt')
                request(OP_INFO, name='info-after.txt')
                if not report['uptime_monotonic']:
                    raise RuntimeError('Camera uptime decreased')
    except Exception as error:
        report.update(status='stopped', stop_reason=f'{type(error).__name__}: {error}')
    finally:
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'captured' else 2


if __name__ == '__main__':
    raise SystemExit(main())
