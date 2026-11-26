"""Isolated DWC3 relink and native qtest; leave the upstream build unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu-dir', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.report_dir.resolve()
    if not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Report must stay under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    original = args.qemu_dir.resolve() / 'build'
    build = output / 'build'
    build.mkdir()
    source = ROOT / 'qemu-a6400/qemu-overlay/hw/usb/cxd90045_dwc3.c'
    obj = 'libsystem_arm.a.p/hw_usb_cxd90045_dwc3.c.o'
    rows = json.loads(subprocess.check_output(['ninja', '-t', 'compdb', '-x'], cwd=original))
    compile_args = shlex.split(next(r['command'] for r in rows if r.get('output') == obj))
    link_args = shlex.split(next(r['command'] for r in rows if r.get('output') == 'qemu-system-arm'))
    for flag, value in (('-c', source), ('-o', build / 'dwc3.o'), ('-MF', build / 'dwc3.d')):
        compile_args[compile_args.index(flag) + 1] = str(value)
    if link_args.count(obj) != 1:
        raise ValueError('Unexpected QEMU link layout')
    link_args[link_args.index(obj)] = str(build / 'dwc3.o')
    link_args[link_args.index('-o') + 1] = str(build / 'qemu-system-arm')
    (output / 'commands.json').write_text(json.dumps([compile_args, link_args], indent=2))
    with (output / 'build.log').open('wb') as log:
        subprocess.run(compile_args, cwd=original, stdout=log, stderr=log, check=True)
        subprocess.run(link_args, cwd=original, stdout=log, stderr=log, check=True)
    for name in ('qemu-img', 'qemu-io'):
        (build / name).symlink_to(original / name)
    expected = [0x5533290a, 0x4020404a, 0x0160c93b, 1, 0x06a4d085,
                0x47822020, 0x04204108, 0x0a0a8020, 0x04881472, 0x00000a0a]
    legacy = [0x5533290a, 0x20204008, 0x00241008, 0, 0x10420089,
              0, 0x04020200, 0x400, 0, 0]
    offsets = [0x120, *range(0x140, 0x160, 4), 0x600]
    report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'binary_sha256': hashlib.sha256((build / 'qemu-system-arm').read_bytes()).hexdigest(),
              'upstream_writes': False, 'checks': []}
    for captured, gate in ((False, False), (True, False), (False, True), (True, True)):
        values = expected if captured else legacy
        with tempfile.TemporaryDirectory(prefix='dwc3-identity-qtest-') as scratch:
            path = Path(scratch) / 'qtest'
            command = [str(build / 'qemu-system-arm'), '-M', 'sony-a6400,dwc3-gadget=on',
                       '-accel', 'qtest', '-display', 'none', '-qtest', f'unix:{path},server=on,wait=off']
            if captured:
                command += ['-global', 'cxd90045-dwc3.captured-hwparams=on']
            if gate:
                command += ['-global', 'cxd90045-dwc3.captured-start-gate=on']
            with (output / f'qtest-{captured}-{gate}.log').open('wb') as log:
                proc = subprocess.Popen(command, stdout=log, stderr=log)
                try:
                    with socket.socket(socket.AF_UNIX) as client:
                        for _ in range(100):
                            try:
                                client.connect(str(path))
                                break
                            except (FileNotFoundError, ConnectionRefusedError):
                                if proc.poll() is not None:
                                    raise RuntimeError('QEMU exited before qtest')
                                time.sleep(.05)
                        client.settimeout(5)
                        with client.makefile('rwb', buffering=0) as stream:
                            for offset, value in zip(offsets, values):
                                stream.write(f'readl {0xf020c000+offset:#x}\n'.encode())
                                reply = stream.readline().decode().strip()
                                if reply != f'OK 0x{value:016x}':
                                    raise AssertionError((captured, offset, reply, value))
                                report['checks'].append({'captured': captured, 'offset': hex(offset), 'reply': reply})
                            stream.write(b'readl 0xf0200024\n')
                            reply = stream.readline().decode().strip()
                            if reply != f'OK 0x{int(gate):016x}':
                                raise AssertionError((captured, gate, reply))
                            report['checks'].append({'captured': captured, 'gate': gate, 'offset': '0xf0200024', 'reply': reply})
                finally:
                    proc.terminate()
                    proc.wait(10)
    report['status'] = 'passed'
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(report["checks"])} native MMIO identity checks; {output}')


if __name__ == '__main__':
    main()
