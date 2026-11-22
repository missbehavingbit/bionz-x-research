"""Explicit adapted ZV-E10 menu experiment on the upstream sony-a6400 board.

Prepared private inputs are declared below artifacts, hash checked on each run,
and never treated as a stock boot or physical rear-panel qualification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import time
import threading

from project_paths import PROJECT_ROOT
from tools.research.inventory import digest
from tools.research.qemu_boot_probe import Qmp

PROFILE = 'zve10-adapted-menu-v1'
CONFIG = PROJECT_ROOT / 'qemu-a6400' / 'zve10-menu.inputs.local.json'
CMDLINE = ('console=ttyAM0,115200n8 cfg.uart=1 mem=1G@0 '
           'memrsv=0x1A5E000@0x02E68000 memrsv=128M@0x20000000:uc '
           'rdinit=/init loglevel=8')
LIMITS = {'kernel': 0x10000000 - 0x138000, 'initrd': 0x10000000, 'source_manifest': 1024 * 1024}


def load_inputs(config: Path = CONFIG, *, root: Path = PROJECT_ROOT) -> dict:
    """Resolve only declared regular inputs under the repository artifacts tree."""
    if config.stat().st_size > 65536:
        raise ValueError('Menu input declaration exceeds 64 KiB')
    data = json.loads(config.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or data.get('profile') != PROFILE:
        raise ValueError('Unsupported adapted menu input profile')
    if data.get('language') not in ('english', 'captured'):
        raise ValueError('Declare english or captured language')
    for field in ('source_manifest_sha256', 'guest_bridge_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', data.get(field, '')):
            raise ValueError(f'Missing provenance pin: {field}')
    resolved = {}
    artifacts = (root / 'artifacts').resolve()
    for name, limit in LIMITS.items():
        item = data['inputs'][name]
        rel = PurePosixPath(item['path'])
        if rel.is_absolute() or '..' in rel.parts or '\\' in item['path'] or ':' in item['path']:
            raise ValueError('Input paths must be repository-relative POSIX paths')
        path = (root / Path(*rel.parts)).resolve(strict=True)
        if not path.is_relative_to(artifacts) or not path.is_file():
            raise ValueError('Menu inputs must be files under artifacts')
        if not 0 < path.stat().st_size <= limit:
            raise ValueError(f'Invalid {name} size')
        expected = item.get('sha256', '')
        if not re.fullmatch('[0-9a-f]{64}', expected) or digest(path) != expected:
            raise ValueError(f'SHA-256 mismatch: {name}')
        resolved[name] = path
    if digest(resolved['source_manifest']) != data['source_manifest_sha256']:
        raise ValueError('Source manifest provenance pin does not match declared input')
    if digest(root / 'qemu-a6400/guest/zve10_menu_osd.h') != data['guest_bridge_sha256']:
        raise ValueError('Guest bridge pin differs from this checkout')
    # -kernel expects a raw Image, never a uImage wrapper.
    with resolved['kernel'].open('rb') as stream:
        if stream.read(4) == bytes.fromhex('27051956'):
            raise ValueError('Use a raw kernel Image, not a uImage wrapper')
    return dict(data, paths=resolved)


def command(qemu: Path, inputs: dict, *, serial_port: int, qmp_port: int) -> list[str]:
    for port in (serial_port, qmp_port):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid loopback port')
    if serial_port == qmp_port:
        raise ValueError('Serial and QMP require separate endpoints')
    size = inputs['paths']['initrd'].stat().st_size
    return [str(qemu), '-M', 'sony-a6400,zve10-menu-boot=on', '-cpu', 'cortex-a9',
            '-smp', '1', '-m', '1024', '-display', 'none', '-monitor', 'none',
            '-nic', 'none', '-no-reboot', '-kernel', str(inputs['paths']['kernel']),
            '-initrd', str(inputs['paths']['initrd']), '-append',
            f'{CMDLINE} initrd=0x10000000,{size}',
            '-serial', f'tcp:127.0.0.1:{serial_port},server=on,wait=on',
            '-qmp', f'tcp:127.0.0.1:{qmp_port},server=on,wait=off']


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NativeMenu:
    """Bounded adapted UART protocol; consumption alone is not frame completion."""
    def __init__(self, uart: socket.socket, log: Path, deadline: float):
        self.uart, self.log, self.deadline = uart, log, deadline
        self.text, self.sequence = '', 0
        self.uart.settimeout(0.2)
        self._reader = None
        self._reader_error = None
        self._stopping = threading.Event()
        self._changed = threading.Condition()

    def start_reader(self):
        # Keep draining while QMP pauses/captures. Otherwise a verbose guest can
        # fill the UART socket and block the emulator during a QMP request.
        if self._reader is not None:
            raise RuntimeError('UART reader already started')
        def receive():
            try:
                while not self._stopping.is_set():
                    self._receive_once()
            except (OSError, RuntimeError) as error:
                self._reader_error = error
                with self._changed:
                    self._changed.notify_all()
        self._reader = threading.Thread(target=receive, name='zve10-menu-uart', daemon=True)
        self._reader.start()

    def close(self):
        self._stopping.set()
        if self._reader is not None:
            self._reader.join(timeout=1)

    def check_status(self):
        for fatal in ('Entering exception monitor.', 'Kernel panic - not syncing:'):
            if fatal in self.text:
                raise RuntimeError(f'Guest stopped at {fatal}')
        if self._reader_error is not None:
            raise self._reader_error

    def _receive_once(self):
        try:
            data = self.uart.recv(65536)
        except socket.timeout:
            return
        if not data:
            raise RuntimeError('UART closed before protocol completion')
        with self.log.open('ab') as stream:
            stream.write(data)
        with self._changed:
            self.text += data.decode('utf-8', errors='replace')
            self._changed.notify_all()
        if len(self.text) > 32 * 1024 * 1024:
            raise RuntimeError('UART exceeded 32 MiB observation bound')

    def send(self, line: str):
        self.uart.sendall((line + '\n').encode('ascii'))

    def wait(self, prefix: str, start: int = 0) -> dict:
        while time.monotonic() < self.deadline:
            self.check_status()
            if prefix == 'NE_STOPPED' and re.search(r'(?m)^NE_STOPPED\r?$', self.text[start:]):
                return {'marker': 'NE_STOPPED'}
            found = re.search(re.escape(prefix) + r'(\{[^\r\n]+\})', self.text[start:])
            if found:
                return json.loads(found[1])
            if self._reader is None:
                self._receive_once()
            else:
                with self._changed:
                    self._changed.wait(timeout=min(0.2, max(0, self.deadline - time.monotonic())))
        raise TimeoutError(f'Menu did not produce {prefix}')

    def state(self) -> dict:
        start = len(self.text)
        self.send('STATE')
        return self.wait('NE_SNAPSHOT ', start)

    def press(self, key: str) -> dict:
        wire = {'DOWN': 'UP', 'UP': 'DOWN', 'LEFT': 'LEFT', 'RIGHT': 'RIGHT'}
        if key not in wire:
            raise ValueError('Only adapted arrow events are supported')
        before = self.state()
        self.sequence += 1
        start = len(self.text)
        self.send(f'KEY {self.sequence} {wire[key]}')
        event = self.wait('NE_INPUT_CONSUMED ', start)
        if event.get('sequence') != self.sequence:
            raise RuntimeError('Unexpected native input sequence')
        while time.monotonic() < self.deadline:
            state = self.state()
            if state.get('last_input', -1) >= self.sequence and state['frame'] > before['frame']:
                return dict(event, completed_frame=state['frame'])
            time.sleep(0.1)
        raise TimeoutError('Input consumed without completed native frame')


def capture_settled(qmp: Qmp, output: Path, deadline: float, *,
                    expected_rgb: str | None = None,
                    expected_title: str | None = None, guest: NativeMenu | None = None) -> dict:
    """Check upstream scanout against guest-owned RGBA4444 over black."""
    import hashlib
    from PIL import Image, ImageChops

    previous, stable_since = None, time.monotonic()
    while time.monotonic() < deadline:
        if guest is not None:
            guest.check_status()
        qmp.request('stop')
        try:
            text = qmp.request('human-monitor-command',
                               {'command-line': 'xp /1wx 0xf3441320'})
            address = int(text.split()[-1], 16)
            if not 0x20000000 <= address <= 0x28000000 - 640 * 480 * 2:
                raise RuntimeError('Guest has not published a bounded OSD framebuffer')
            raw_path = output / 'frame.raw'
            qmp.request('pmemsave', {'val': address, 'size': 640 * 480 * 2,
                                     'filename': str(raw_path)})
            qmp.request('screendump', {'filename': str(output / 'menu.ppm')})
            raw = raw_path.read_bytes()
            rgb = bytearray()
            for offset in range(0, len(raw), 2):
                value = raw[offset] | raw[offset + 1] << 8
                alpha = (value & 15) * 17
                rgb.extend((((value >> shift) & 15) * 17 * alpha // 255)
                           for shift in (12, 8, 4))
            expected = Image.frombytes('RGB', (640, 480), bytes(rgb))
            with Image.open(output / 'menu.ppm') as image:
                screen = image.convert('RGB')
                if screen.size != expected.size or ImageChops.difference(screen, expected).getbbox():
                    raise RuntimeError('Upstream VPH scanout differs from guest memory')
                colors = len(screen.getcolors(640 * 480) or [])
                if colors < 16:
                    raise RuntimeError('Guest scanout is blank or lacks menu detail')
                screen.save(output / 'menu.png')
            current = hashlib.sha256(raw).hexdigest()
            rgb_sha256 = hashlib.sha256(screen.tobytes()).hexdigest()
            title_sha256 = hashlib.sha256(screen.crop((0, 0, 640, 125)).tobytes()).hexdigest()
            matches = ((expected_rgb is None or rgb_sha256 == expected_rgb)
                       and (expected_title is None or title_sha256 == expected_title))
        finally:
            qmp.request('cont')
        if current != previous or not matches:
            stable_since = time.monotonic()
        previous = current
        if matches and time.monotonic() - stable_since >= 3:
            return dict(framebuffer=f'0x{address:08x}', raw_sha256=current,
                        screenshot_sha256=digest(output / 'menu.png'),
                        qmp_matches_guest_memory=True, colors=colors,
                        rgb_sha256=rgb_sha256, title_rgb_sha256=title_sha256)
        time.sleep(0.2)
    raise TimeoutError('Menu framebuffer did not settle at the requested image oracle')


def run(qemu: Path, inputs: dict, output: Path, seconds: int, keys: list[str], *,
        initial_rgb: str | None = None, event_titles: tuple[str, ...] = ()) -> dict:
    if not 10 <= seconds <= 300 or len(keys) > 16:
        raise ValueError('Use 10..300 seconds and at most 16 arrow events')
    if any(key not in ('UP', 'DOWN', 'LEFT', 'RIGHT') for key in keys):
        raise ValueError('Only adapted arrow events are supported')
    if event_titles and len(event_titles) != len(keys):
        raise ValueError('Supply exactly one title oracle per input event')
    for oracle in ([initial_rgb] if initial_rgb is not None else []) + list(event_titles):
        if not re.fullmatch('[0-9a-f]{64}', oracle):
            raise ValueError('Image oracles must be SHA-256 strings')
    output = output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        raise ValueError('Experiment output must remain under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    serial_port, qmp_port = free_port(), free_port()
    while serial_port == qmp_port:
        qmp_port = free_port()
    argv = command(qemu, inputs, serial_port=serial_port, qmp_port=qmp_port)
    receipt = dict(profile=PROFILE, stock_boot=False, physical_panel_qualified=False,
                   language=inputs['language'], presentation_policy='guest-framebuffer-on-upstream-vph',
                   command=argv, qemu_sha256=digest(qemu),
                   inputs=inputs['inputs'], source_manifest_sha256=inputs['source_manifest_sha256'],
                   guest_bridge_sha256=inputs['guest_bridge_sha256'], completed=False,
                   requested_oracles=dict(initial_rgb=initial_rgb, event_titles=event_titles),
                   source_sha256={str(p.relative_to(PROJECT_ROOT)).replace('\\', '/'): digest(p)
                       for p in [PROJECT_ROOT / 'tools/research/qemu_zve10_menu.py',
                                 PROJECT_ROOT / 'tools/research/qemu_boot_probe.py',
                                 PROJECT_ROOT / 'qemu-a6400/qemu-overlay/hw/arm/sony_a6400.c',
                                 PROJECT_ROOT / 'qemu-a6400/qemu-overlay/hw/misc/cxd90014_vph.c']})
    deadline = time.monotonic() + seconds
    process = uart = qmp = menu = None
    try:
        with (output / 'qemu.stderr.log').open('wb') as stderr:
            process = subprocess.Popen(argv, stdout=stderr, stderr=stderr)
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'QEMU exited before UART connection: {process.returncode}')
                try:
                    uart = socket.create_connection(('127.0.0.1', serial_port), timeout=0.2)
                    break
                except OSError:
                    time.sleep(0.1)
            if uart is None:
                raise TimeoutError('QEMU did not open UART')
            menu = NativeMenu(uart, output / 'serial.log', deadline)
            menu.start_reader()
            qmp = Qmp(('127.0.0.1', qmp_port), deadline)
            receipt['qmp_version'] = qmp.greeting['QMP']['version']
            receipt['ready'] = menu.wait('NE_READY ')
            if receipt['ready'].get('native_glyph_images', 0) < 13:
                raise RuntimeError('Initial native glyph images are incomplete')
            receipt['initial_frame'] = capture_settled(qmp, output, deadline, expected_rgb=initial_rgb, guest=menu)
            (output / 'initial-menu.png').write_bytes((output / 'menu.png').read_bytes())
            receipt['events'] = []
            for index, key in enumerate(keys):
                event = menu.press(key)
                event['key'] = key
                event['frame'] = capture_settled(qmp, output, deadline,
                    expected_title=event_titles[index] if event_titles else None, guest=menu)
                image_name = f'event-{index + 1:02d}-{key.lower()}.png'
                (output / image_name).write_bytes((output / 'menu.png').read_bytes())
                event['screenshot'] = image_name
                receipt['events'].append(event)
            receipt['frame'] = (receipt['events'][-1]['frame'] if keys
                                else receipt['initial_frame'])
            start = len(menu.text)
            menu.send('STOP')
            receipt['stopped'] = menu.wait('NE_STOPPED', start)
            receipt['returncode'] = qmp.quit(process)
            receipt['completed'] = receipt['returncode'] == 0
    except Exception as error:
        receipt['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if menu:
            menu.close()
        if qmp:
            qmp.close()
        if uart:
            uart.close()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            receipt['forced_cleanup'] = True
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, default=CONFIG)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--keys', nargs='*', default=[], choices=('UP', 'DOWN', 'LEFT', 'RIGHT'))
    parser.add_argument('--check', action='store_true', help='Verify inputs and print launch command without execution')
    args = parser.parse_args()
    inputs = load_inputs(args.inputs)
    if args.check:
        print(json.dumps(command(args.qemu, inputs, serial_port=4444, qmp_port=4445), indent=2))
    else:
        if args.output is None:
            parser.error('--output is required for a run')
        print(json.dumps(run(args.qemu.resolve(strict=True), inputs, args.output, args.seconds, args.keys), indent=2))


if __name__ == '__main__':
    main()
