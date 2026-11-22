"""Bounded WSL/Linux QEMU boot observation with serial receipts and QMP snapshots.

Only the emulator is contacted. The optional preboot SD action attaches a
launcher-qualified virtual image. No host USB/device access or debugger writes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import socket
import subprocess
import time
import uuid

from project_paths import PROJECT_ROOT
from tools.research.inventory import digest
from tools.research.liro_task_snapshot import decode_gic_trace, snapshot_interrupts, snapshot_tasks, task_table_address
from tools.research.qemu_media import sd_event

DEFAULT_COMMANDS = ('cat /proc/version', 'cat /proc/interrupts', 'ps', 'ls /proc/udm')


def source_reference_hashes(greeting: dict) -> dict[str, str]:
    version = greeting['QMP']['version']['qemu']
    parts = tuple(version[name] for name in ('major', 'minor', 'micro'))
    if any(type(part) is not int or part < 0 for part in parts):
        raise ValueError('QMP version requires nonnegative integer components')
    label = '.'.join(map(str, parts))
    patch = f'qemu-a6400/patches/qemu-v{label}-sony-a6400.patch'
    if not (PROJECT_ROOT / patch).is_file():
        raise ValueError(f'No pinned Sony source patch for QEMU {label}')
    return {name: digest(PROJECT_ROOT / name) for name in (
        'qemu-a6400/run.sh', 'qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c',
        'qemu-a6400/qemu-overlay/hw/timer/cxd4115_timer.c', patch)}


def physical_range(text: str) -> tuple[int, int]:
    try:
        address, size = (int(value, 0) for value in text.split(':'))
    except ValueError as error:
        raise argparse.ArgumentTypeError('Expected PHYSICAL_ADDRESS:BYTES') from error
    if (address < 0 or address % 4 or size <= 0 or size > 4096 or size % 4
            or address // 4096 != (address + size - 1) // 4096
            or not (address + size <= 0x40000000
                    or 0x80000000 <= address < address + size <= 0xBF800000)):
        raise argparse.ArgumentTypeError('Read must be aligned, within one RAM page, and at most 4 KiB')
    return address, size


def console_snapshot(data: bytes) -> str:
    """Decode the whole bounded console log. A tail window can hide an earlier status."""
    if len(data) > 32 * 1024 * 1024:
        raise RuntimeError('Console output exceeded the 32 MiB observation bound')
    return data.decode('utf-8', errors='replace')


def split_qmp_reply(messages: list, ident: int):
    """Return the matching reply and keep every other QMP object from the same batch."""
    events = []
    result = None
    for response in messages:
        if not isinstance(response, dict):
            raise RuntimeError('QMP response is not an object')
        if result is not None or response.get('id') != ident:
            events.append(response)
            continue
        if 'error' in response:
            raise RuntimeError(f'QMP error: {response["error"]}')
        if 'return' not in response:
            raise RuntimeError('QMP response has no return')
        result = response['return']
    if result is None:
        raise RuntimeError('QMP response message bound exceeded')
    return result, events


def guest_statuses(text: str) -> dict[int, int]:
    return {int(index): int(status) for index, status in re.findall(
        r'^__QEMU_STATUS_(\d+)__:(\d+)\r?$', text, re.MULTILINE)}


def serial_readiness(text: str, shell_seen: bool, marker_seen: bool,
                     wait_for: str | None) -> tuple[bool, bool]:
    shell_seen = shell_seen or 'built-in shell (ash)' in text.lower() or re.search(r'(?:^|\n)/ # $', text) is not None
    marker_seen = marker_seen or wait_for is None or wait_for in text
    return shell_seen, marker_seen


class QmpDisconnected(RuntimeError):
    """The peer closed the read side; ordinary QMP requests still fail."""


class Qmp:
    def __init__(self, path: Path | tuple[str, int], deadline: float):
        self.deadline, self.ident = deadline, 0
        if isinstance(path, tuple):
            host, port = path
            if host != '127.0.0.1' or type(port) is not int or not 1 <= port <= 65535:
                raise ValueError('TCP QMP must use a numeric loopback endpoint')
            self.socket = socket.socket(socket.AF_INET)
            endpoint = path
        else:
            self.socket = socket.socket(socket.AF_UNIX)
            endpoint = str(path)
        self.socket.settimeout(5)
        try:
            self.socket.connect(endpoint)
        except OSError:
            self.socket.close()
            raise
        self.stream = self.socket.makefile('rwb', buffering=0)
        self.events = []
        self.greeting = self.read()
        if 'QMP' not in self.greeting:
            raise RuntimeError('QMP greeting is missing')
        self.request('qmp_capabilities')

    def read(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('QMP observation deadline exceeded')
        self.socket.settimeout(min(5, remaining))
        try:
            line = self.stream.readline(1024 * 1024 + 1)
        except ConnectionResetError as error:
            raise QmpDisconnected('QMP peer reset the connection before its reply') from error
        if not line:
            raise QmpDisconnected('QMP response is absent')
        if len(line) > 1024 * 1024:
            raise RuntimeError('QMP response is absent or exceeds 1 MiB')
        return json.loads(line)

    def request(self, command, arguments=None):
        self.ident += 1
        message = dict(execute=command, id=self.ident)
        if arguments is not None:
            message['arguments'] = arguments
        self.socket.sendall((json.dumps(message) + '\n').encode('utf-8'))
        pending = []
        for _ in range(128):
            pending.append(self.read())
            if any(isinstance(item, dict) and item.get('id') == self.ident for item in pending):
                self.events.extend(item for item in pending
                                   if not (isinstance(item, dict) and item.get('id') == self.ident))
                result, _events = split_qmp_reply(pending, self.ident)
                return result
        self.events.extend(item for item in pending if not (isinstance(item, dict) and item.get('id') == self.ident))
        raise RuntimeError('QMP response message bound exceeded')

    def quit(self, process: subprocess.Popen, *, timeout: float = 10) -> int:
        # QEMU qapi/control.json @quit explicitly permits EOF before its reply.
        # Accept read-side closure only for quit, and require a normal exit.
        # A send failure, protocol error, timeout or nonzero exit still fails.
        try:
            self.request('quit')
        except QmpDisconnected:
            pass
        returncode = process.wait(timeout=timeout)
        if returncode != 0:
            raise RuntimeError(f'QEMU quit with nonzero status: {returncode}')
        return returncode

    def close(self):
        self.stream.close()
        self.socket.close()


def run(inputs: Path, output: Path, seconds: int, commands: tuple[str, ...],
        reads: tuple[tuple[int, int], ...], *, trace_emmc: bool = False,
        wait_for: str | None = None, liro_tasks: tuple[int, ...] = (),
        liro_irqs: tuple[int, ...] = (), sd_at_boot: bool = False) -> dict:
    if os.name != 'posix':
        raise ValueError('Run this module with python3 inside WSL/Linux')
    if liro_tasks or liro_irqs:
        commands += ('b=$(cat /sys/module/liro/sections/.bss) && echo "__LIRO_BSS__:$b"',
                     't=$(cat /sys/module/liro/sections/.text) && echo "__LIRO_TEXT__:$t"')
    if not 10 <= seconds <= 300 or not 1 <= len(commands) <= 16 or len(reads) > 16:
        raise ValueError('Use 10..300 seconds, 1..16 commands, and at most 16 RAM reads')
    if any(not command or len(command) > 512 or any(char in command for char in '\r\n\0\x01')
           for command in commands):
        raise ValueError('Guest commands must be nonempty bounded single lines')
    if wait_for is not None and (not wait_for or len(wait_for) > 160 or '\0' in wait_for):
        raise ValueError('Serial gate must be a nonempty marker of at most 160 characters')
    if sd_at_boot and not (os.environ.get('SD_IMAGE') and os.environ.get('SD_SHA256')):
        raise ValueError('--sd-at-boot requires both SD_IMAGE and SD_SHA256')
    output = output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        raise ValueError('Probe reports must remain under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    session_dir = Path.home() / 'a6400-runs' / ('probe-' + uuid.uuid4().hex[:16])
    session_dir.mkdir(parents=True, exist_ok=False)
    run_dir = session_dir / 'machine'
    qmp_path = run_dir / 'qmp.sock'
    environment = dict(os.environ, A6400_QEMU_DATA_DIR=str(inputs.resolve()),
                       RUN_DIR=str(run_dir), TIMEOUT='0',
                       QEMU_EXTRA=f'-icount shift=0,sleep=on -net none -no-reboot '
                                  f'-qmp unix:{qmp_path},server=on,wait=off')
    if sd_at_boot:
        environment['QEMU_EXTRA'] += ' -S'
    trace_path = session_dir / ('device.trace' if liro_irqs else 'emmc.trace')
    if trace_emmc or liro_irqs:
        environment['QEMU_EXTRA'] += f' -D {trace_path}'
    if trace_emmc:
        environment['QEMU_EXTRA'] += (' -trace enable=sdcard_normal_command'
                                      ' -trace enable=sdcard_req_addr')
    if liro_irqs:
        environment['QEMU_EXTRA'] += (' -trace enable=gic_enable_irq -trace enable=gic_disable_irq'
                                      ' -trace enable=gic_set_irq')
    report = dict(status='running', observation_seconds=seconds, run_dir=str(run_dir),
                  inputs_manifest_sha256=digest(inputs / 'boot-manifest.json'),
                  tool_sha256=digest(Path(__file__)), guest_commands=list(commands),
                  emmc_trace_requested=trace_emmc,
                  wait_for=wait_for,
                  requested_liro_tasks=list(liro_tasks),
                  requested_liro_irqs=list(liro_irqs),
                  serial_input_policy='100 characters/second; one command in flight; no retries',
                  limitations=['QEMU model observations, not physical hardware verification.',
                               'Existing QEMU peripheral stubs and ignored unmapped MMIO remain assumptions.'])
    process, qmp = None, None
    started = time.monotonic()
    deadline = started + seconds
    control_deadline = deadline + 20
    # Keep concurrent console reads/writes on Linux, not a cloud-backed DrvFS file.
    log_path = session_dir / 'console.log'
    sent = False
    next_command = 0
    observed_statuses = {}
    end_sent = False
    shell_seen, marker_seen = False, False

    def send_serial(text):
        for value in text.encode('utf-8'):
            if time.monotonic() >= deadline:
                raise TimeoutError('Serial command exceeded the observation deadline')
            process.stdin.write(bytes([value]))
            process.stdin.flush()
            time.sleep(0.01)

    try:
        with log_path.open('xb') as log:
            process = subprocess.Popen(
                ['bash', str(PROJECT_ROOT / 'qemu-a6400/run.sh')],
                cwd=PROJECT_ROOT, env=environment, stdin=subprocess.PIPE, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
            report['owned_pid'] = process.pid
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'QEMU exited before the observation ended: {process.returncode}')
                if qmp is None and qmp_path.exists():
                    qmp = Qmp(qmp_path, control_deadline)
                    report['qmp_greeting'] = qmp.greeting
                    executable = Path(os.readlink(f'/proc/{process.pid}/exe'))
                    report['qemu_binary'] = dict(path=str(executable), sha256=digest(executable))
                    report['qemu_source_inputs'] = source_reference_hashes(qmp.greeting)
                    report['qemu_source_inputs_kind'] = (
                        'repository references, not identities of the native compilation inputs')
                    if sd_at_boot:
                        sd_event(qmp, 'insert')
                        report['sd_inserted_before_boot'] = True
                        report['sd_initial_sha256'] = environment['SD_SHA256']
                        qmp.request('cont')
                    process.stdin.write(b'\n')
                    process.stdin.flush()
                if log_path.stat().st_size > 32 * 1024 * 1024:
                    raise RuntimeError('Console output exceeded the 32 MiB observation bound')
                with log_path.open('rb') as current:
                    observed = console_snapshot(current.read())
                shell_seen, marker_seen = serial_readiness(observed, shell_seen, marker_seen, wait_for)
                observed_statuses.update(guest_statuses(observed))
                if not sent and shell_seen and marker_seen:
                    send_serial('echo __QEMU_PROBE_BEGIN__\n')
                    sent = True
                if sent and next_command < len(commands) and (
                        next_command == 0 or next_command - 1 in observed_statuses):
                    send_serial(commands[next_command] + '\n'
                                + f'echo __QEMU_STATUS_{next_command}__:$?\n')
                    next_command += 1
                if sent and next_command == len(commands) and len(observed_statuses) == len(commands) and not end_sent:
                    send_serial('echo __QEMU_PROBE_END__\n')
                    end_sent = True
                if trace_path.exists() and trace_path.stat().st_size > 16 * 1024 * 1024:
                    raise RuntimeError('Device trace exceeded the 16 MiB observation bound')
                time.sleep(0.2)
            if qmp is None:
                raise RuntimeError('QMP did not become available before the deadline')
            qmp.request('stop')
            state = qmp.request('query-status')
            if state['running']:
                raise RuntimeError('QEMU did not stop for the register/RAM snapshot')
            report['stopped_state'] = state
            report['registers'] = qmp.request('human-monitor-command', {'command-line': 'info registers -a'})
            report['ram_reads'] = []
            for address, size in reads:
                text = qmp.request('human-monitor-command', {
                    'command-line': f'xp /{size // 4}wx 0x{address:x}'})
                if 'Cannot access' in text:
                    raise RuntimeError(f'QEMU RAM read failed at 0x{address:x}')
                report['ram_reads'].append(dict(address=address, bytes=size, result=text))
            transcript = log_path.read_text(encoding='utf-8', errors='replace')
            if liro_tasks or liro_irqs:
                manifest = json.loads((inputs / 'boot-manifest.json').read_text(encoding='utf-8'))
                usr = [row for row in manifest['inputs'] if PureWindowsPath(row['path']).name == 'nflasha15.bin']
                if len(usr) != 1:
                    raise ValueError('Prepared input manifest has no unique captured usr image')
                qmp.request('human-monitor-command', {'command-line': 'cpu 0'})
                memory_dir = output / 'liro-memory'
                memory_dir.mkdir()
                memory_reads = []
                report['liro_memory_reads'] = memory_reads

                def read_virtual(address, size):
                    if size > 8192 or address % 4 or size % 4:
                        raise ValueError('LIRO virtual read exceeds its object bound')
                    data = bytearray()
                    while len(data) < size:
                        current = address + len(data)
                        chunk = min(size - len(data), 4096 - current % 4096)
                        text = qmp.request('human-monitor-command', {
                            'command-line': f'x /{chunk // 4}wx 0x{current:x}'})
                        words = []
                        for line in text.splitlines():
                            words.extend(int(word, 16) for word in
                                         re.findall(r'0x[0-9a-fA-F]{8}\b', line.partition(':')[2]))
                        if len(words) != chunk // 4:
                            raise ValueError(f'QEMU did not provide the requested LIRO RAM at 0x{current:x}')
                        import struct
                        raw = struct.pack('<' + 'I' * len(words), *words)
                        path = memory_dir / f'{len(memory_reads):02d}.bin'
                        with path.open('xb') as saved:
                            saved.write(raw)
                        memory_reads.append(dict(address=current, bytes=chunk,
                                                 space='guest-virtual-cpu0',
                                                 file=path.relative_to(output).as_posix(), sha256=digest(path)))
                        data.extend(raw)
                    return bytes(data)

                if liro_tasks:
                    report['liro_tasks'] = snapshot_tasks(
                        read_virtual, task_table_address(transcript), liro_tasks,
                        kernel_sha256=manifest['outputs']['nflasha3-Image']['sha256'],
                        usr_sha256=usr[0]['sha256'])
                if liro_irqs:
                    report['liro_interrupts'] = snapshot_interrupts(
                        read_virtual, task_table_address(transcript), liro_irqs,
                        kernel_sha256=manifest['outputs']['nflasha3-Image']['sha256'],
                        usr_sha256=usr[0]['sha256'])
                report['liro_table_binding'] = dict(
                    table=task_table_address(transcript),
                    source='Current-run /sys/module/liro/sections/.bss + captured ELF tsks offset 0x780')
                report['liro_snapshot_limit'] = ('CPU0 debug translation; all CPUs paused. '
                                                  'This is not proof of a permanent wait or a completed higher-level transaction.')
            statuses = guest_statuses(transcript)
            complete = re.search(r'^__QEMU_PROBE_END__\r?$', transcript, re.MULTILINE) is not None
            report['guest_statuses'] = {str(index): statuses.get(index) for index in range(len(commands))}
            report['serial_roundtrip_complete'] = complete and len(statuses) == len(commands)
            report['serial_gate_observed'] = marker_seen
            report['serial_commands_sent'] = next_command
            clean = report['serial_roundtrip_complete'] and all(status == 0 for status in statuses.values())
            report.update(status='completed' if clean else 'boundary',
                          stop_reason='bounded_observation_complete' if clean else 'serial_or_guest_command_boundary')
            qmp.request('quit')
            process.wait(timeout=10)
            if process.returncode:
                raise RuntimeError(f'QEMU did not exit cleanly after QMP quit: {process.returncode}')
            if liro_irqs:
                report['gic_from_native_trace'] = decode_gic_trace(
                    trace_path.read_text(encoding='utf-8'), liro_irqs)
                report['gic_trace_scope'] = 'Native enable/disable/input events from launch until stopped; no GIC MMIO debug reads'
            report['qemu_exit_code'] = process.returncode
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        if qmp is not None:
            report['qmp_events'] = list(qmp.events)
            qmp.close()
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                report['forced_kill'] = True
        report['owned_process_exited'] = process is not None and process.poll() is not None
        report['qemu_exit_code'] = process.returncode if process is not None else None
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['console_sha256'] = digest(log_path) if log_path.exists() else None
        if log_path.exists():
            saved_log = output / 'console.log'
            shutil.copyfile(log_path, saved_log)
            if digest(saved_log) != report['console_sha256']:
                raise RuntimeError('Archived console log differs from the Linux receipt')
        if trace_path.exists():
            saved_trace = output / trace_path.name
            shutil.copyfile(trace_path, saved_trace)
            report['device_trace_sha256'] = digest(trace_path)
            if digest(saved_trace) != report['device_trace_sha256']:
                raise RuntimeError('Archived device trace differs from the Linux receipt')
        (output / 'probe.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--guest-command', action='append')
    parser.add_argument('--read-physical', type=physical_range, action='append', default=[])
    parser.add_argument('--trace-emmc', action='store_true')
    parser.add_argument('--sd-at-boot', action='store_true',
                        help='Insert the hash-checked SD_IMAGE through QMP before releasing the CPUs')
    parser.add_argument('--wait-for', help='Do not send commands before this original serial marker')
    parser.add_argument('--liro-task', type=int, action='append', default=[],
                        help='Read one task from the hash-qualified captured LIRO layout (at most eight)')
    parser.add_argument('--liro-irq', type=int, action='append', default=[],
                        help='Read one registered LIRO IRQ handler record without signalling it')
    args = parser.parse_args()
    report = run(args.inputs, args.report_dir, args.seconds,
                 tuple(args.guest_command or DEFAULT_COMMANDS), tuple(args.read_physical),
                 trace_emmc=args.trace_emmc, wait_for=args.wait_for, liro_tasks=tuple(args.liro_task),
                 liro_irqs=tuple(args.liro_irq), sd_at_boot=args.sd_at_boot)
    print(json.dumps({key: report[key] for key in
                      ('status', 'stop_reason', 'guest_statuses', 'owned_process_exited', 'elapsed_seconds')},
                     indent=2))
    if report['status'] != 'completed':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
