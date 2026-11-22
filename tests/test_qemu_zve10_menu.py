"""Constructed input namespace and adapted native protocol regressions."""
import hashlib
import json
from pathlib import Path
import shutil
import socket
import time
import unittest
from unittest.mock import Mock
import uuid

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import Qmp, QmpDisconnected
from tools.research.qemu_zve10_menu import NativeMenu, PROFILE, command, load_inputs


class MenuInputsTests(unittest.TestCase):
    def setUp(self):
        self.root = PROJECT_ROOT / 'artifacts' / 'tests' / ('menu-' + uuid.uuid4().hex)
        self.addCleanup(lambda: shutil.rmtree(self.root))
        (self.root / 'artifacts').mkdir(parents=True)
        self.config = self.root / 'config.json'
        self.data = dict(schema_version=1, profile=PROFILE, language='english',
                         source_manifest_sha256='a' * 64, guest_bridge_sha256='b' * 64, inputs={})
        for name, content in [('kernel', b'raw Image'), ('initrd', b'070701fixture'), ('source_manifest', b'{"schema":1}')]:
            (self.root / 'artifacts' / name).write_bytes(content)
            self.data['inputs'][name] = dict(path='artifacts/' + name,
                                           sha256=hashlib.sha256(content).hexdigest())

        self.data['source_manifest_sha256'] = self.data['inputs']['source_manifest']['sha256']
        bridge = self.root / 'qemu-a6400/guest/zve10_menu_osd.h'
        bridge.parent.mkdir(parents=True)
        bridge.write_bytes(b'constructed bridge')
        self.data['guest_bridge_sha256'] = hashlib.sha256(bridge.read_bytes()).hexdigest()

    def load(self):
        self.config.write_text(json.dumps(self.data), encoding='utf-8')
        return load_inputs(self.config, root=self.root)

    def test_declared_inputs_build_explicit_single_cpu_profile(self):
        inputs = self.load()
        argv = command(Path('qemu'), inputs, serial_port=4444, qmp_port=4445)
        self.assertEqual(argv[argv.index('-M') + 1], 'sony-a6400,zve10-menu-boot=on')
        self.assertIn('initrd=0x10000000,13', argv[argv.index('-append') + 1])
        self.assertNotIn('-drive', argv)
        self.assertNotIn('-device', argv)

    def test_mutated_inputs_are_not_rehashed_automatically(self):
        self.load()
        (self.root / 'artifacts' / 'initrd').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.load()

    def test_path_escape_and_absolute_paths_refused(self):
        for name in ('../kernel', '/kernel', 'C:/kernel', 'artifacts\\kernel'):
            with self.subTest(name=name):
                self.data['inputs']['kernel']['path'] = name
                with self.assertRaises(ValueError):
                    self.load()

    def test_missing_provenance_and_unknown_language_refused(self):
        self.data['source_manifest_sha256'] = ''
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.load()
        self.data['source_manifest_sha256'] = 'a' * 64
        self.data['language'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'language'):
            self.load()

    def test_source_manifest_and_bridge_must_match_declared_provenance(self):
        self.data['source_manifest_sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'Source manifest'):
            self.load()
        self.data['source_manifest_sha256'] = self.data['inputs']['source_manifest']['sha256']
        self.data['guest_bridge_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'Guest bridge'):
            self.load()

    def test_uimage_refused_even_with_matching_pin(self):
        content = bytes.fromhex('27051956') + b'header'
        (self.root / 'artifacts' / 'kernel').write_bytes(content)
        self.data['inputs']['kernel']['sha256'] = hashlib.sha256(content).hexdigest()
        with self.assertRaisesRegex(ValueError, 'raw kernel'):
            self.load()

    def test_symlink_escape_refused(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'raw Image')
        linked = self.root / 'artifacts' / 'link'
        try:
            linked.symlink_to(outside)
        except OSError:
            self.skipTest('Symlink creation requires host privileges')
        self.data['inputs']['kernel']['path'] = 'artifacts/link'
        with self.assertRaisesRegex(ValueError, 'under artifacts'):
            self.load()


class FakeUART:
    def __init__(self, chunks):
        self.chunks, self.sent = iter(chunks), []

    def settimeout(self, timeout):
        pass

    def recv(self, size):
        return next(self.chunks, b'')

    def sendall(self, data):
        self.sent.append(data)


class MenuProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = PROJECT_ROOT / 'artifacts' / 'tests' / ('menu-uart-' + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.directory))

    def test_consumed_input_requires_later_retired_frame(self):
        uart = FakeUART([
            b'NE_SNAPSHOT {"frame":10,"last_input":0}\n',
            b'NE_INPUT_CONSUMED {"sequence":1,"native_action":3}\n',
            b'NE_SNAPSHOT {"frame":10,"last_input":1}\n',
            b'NE_SNAPSHOT {"frame":11,"last_input":0}\n',
            b'NE_SNAPSHOT {"frame":12,"last_input":1}\n'])
        menu = NativeMenu(uart, self.directory / 'serial.log', time.monotonic() + 5)
        self.assertEqual(menu.press('DOWN')['completed_frame'], 12)
        self.assertIn(b'KEY 1 UP\n', uart.sent)
        self.assertEqual(uart.sent.count(b'STATE\n'), 4)

    def test_unsupported_keys_send_nothing(self):
        uart = FakeUART([])
        menu = NativeMenu(uart, self.directory / 'serial.log', time.monotonic() + 5)
        with self.assertRaises(ValueError):
            menu.press('ENTER')
        self.assertFalse(uart.sent)

    def test_background_reader_drains_while_foreground_is_busy(self):
        host, peer = socket.socketpair()
        self.addCleanup(host.close)
        self.addCleanup(peer.close)
        peer.settimeout(5)
        menu = NativeMenu(host, self.directory / 'serial.log', time.monotonic() + 10)
        self.addCleanup(menu.close)
        menu.start_reader()
        payload = b'guest diagnostic line\n' * 65536
        peer.sendall(payload)
        deadline = time.monotonic() + 5
        while len(menu.text) < len(payload) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(menu.text.encode(), payload)
        self.assertEqual((self.directory / 'serial.log').read_bytes(), payload)
        peer.sendall(b'NE_READY {"frame":27}\n')
        self.assertEqual(menu.wait('NE_READY ')['frame'], 27)

    def test_guest_fault_is_reported_without_waiting_for_deadline(self):
        uart = FakeUART([b'Entering exception monitor.\nException> '])
        menu = NativeMenu(uart, self.directory / 'serial.log', time.monotonic() + 180)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'Guest stopped'):
            menu.wait('NE_READY ')
        self.assertLess(time.monotonic() - started, 1)

    def test_out_of_order_consumption_is_failure(self):
        uart = FakeUART([b'NE_SNAPSHOT {"frame":10,"last_input":0}\n',
                         b'NE_INPUT_CONSUMED {"sequence":7}\n'])
        menu = NativeMenu(uart, self.directory / 'serial.log', time.monotonic() + 5)
        with self.assertRaisesRegex(RuntimeError, 'sequence'):
            menu.press('RIGHT')

    def test_qmp_quit_requires_zero_exit_even_after_read_disconnect(self):
        qmp = object.__new__(Qmp)
        qmp.request = Mock(side_effect=QmpDisconnected('peer closed'))
        process = Mock()
        process.wait.return_value = 0
        self.assertEqual(qmp.quit(process), 0)
        qmp.request.assert_called_once_with('quit')
        process.wait.return_value = 7
        with self.assertRaisesRegex(RuntimeError, 'nonzero status: 7'):
            qmp.quit(process)

    def test_qmp_quit_preserves_protocol_and_send_failures(self):
        qmp = object.__new__(Qmp)
        for error in (RuntimeError('QMP error'), ConnectionResetError('send failed')):
            with self.subTest(error=type(error).__name__):
                qmp.request = Mock(side_effect=error)
                process = Mock()
                with self.assertRaises(type(error)):
                    qmp.quit(process)
                process.wait.assert_not_called()

    def test_qmp_read_reports_eof_and_reset_separately_from_protocol_errors(self):
        qmp = object.__new__(Qmp)
        qmp.deadline = time.monotonic() + 10
        qmp.socket, qmp.stream = Mock(), Mock()
        for response in (b'', ConnectionResetError('peer reset')):
            with self.subTest(response=response):
                qmp.stream.readline.side_effect = response if isinstance(response, Exception) else None
                qmp.stream.readline.return_value = response
                with self.assertRaises(QmpDisconnected):
                    qmp.read()

    def test_non_loopback_qmp_refused_before_connect(self):
        with self.assertRaisesRegex(ValueError, 'loopback'):
            Qmp(('example.com', 4444), time.monotonic() + 1)


if __name__ == '__main__':
    unittest.main()
