"""Workspace MCP policy checks; constructed inputs and no server or device access."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from project_paths import PROJECT_ROOT
from tools.research.mcp_workspace import (
    analysis_environment, main, reverify_request, validate_reverify_call, wait_for_backend,
)


class WorkspaceMcpTests(unittest.TestCase):
    def setUp(self):
        base = PROJECT_ROOT / 'artifacts' / 'tests' / 'mcp-workspace'
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.target = self.inputs / 'fixture.bin'
        self.target.write_bytes(b'\x01\x02\x03\x04')

    def test_byte_claims_are_allowed_without_execution(self):
        validate_reverify_call('re_verify_claim', {
            'file_path': str(self.target),
            'claims': [{'kind': 'bytes_at', 'params': {'offset': 0, 'expected': '0102'}}],
        }, self.inputs)

    def test_mixed_batch_rejects_execution_before_dispatch(self):
        for kind in ('instructions', 'emulate_result', 'behavior_equiv', 'prove_equiv'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'disabled'):
                validate_reverify_call('re_verify_claim', {
                    'file_path': str(self.target),
                    'claims': [{'kind': 'bytes_at'}, {'kind': kind}],
                }, self.inputs)

    def test_parent_traversal_outside_inputs_is_rejected(self):
        outside = self.root / 'outside.bin'
        outside.write_bytes(b'outside')
        with self.assertRaisesRegex(ValueError, 'staged'):
            validate_reverify_call('re_parse', {
                'file_path': str(self.inputs / '..' / outside.name),
            }, self.inputs)

    def test_ledger_deletion_and_unexposed_tools_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Deleting evidence'):
            validate_reverify_call('re_ledger', {
                'file_path': str(self.target), 'action': 'clear',
            }, self.inputs)
        with self.assertRaisesRegex(ValueError, 'not enabled'):
            validate_reverify_call('re_disasm', {}, self.inputs)

    def test_empty_and_oversized_claim_batches_are_rejected(self):
        for claims in ([], [{'kind': 'bytes_at'}] * 65, None):
            with self.subTest(claims=claims), self.assertRaisesRegex(ValueError, '1 and 64'):
                validate_reverify_call('re_verify_claim', {
                    'file_path': str(self.target), 'claims': claims,
                }, self.inputs)

    def test_child_environment_drops_inherited_debugger_and_server_configuration(self):
        inherited = dict(os.environ, GHIDRA_MCP_BIND_ADDRESS='0.0.0.0',
                         GHIDRA_MCP_ALLOW_SCRIPTS='1', GHIDRA_DEBUGGER_TOOLS='1',
                         GHIDRA_SERVER_PASSWORD='fixture-password')
        env = analysis_environment(self.root, inherited)
        self.assertNotIn('GHIDRA_MCP_BIND_ADDRESS', env)
        self.assertNotIn('GHIDRA_SERVER_PASSWORD', env)
        self.assertEqual(env['GHIDRA_MCP_ALLOW_SCRIPTS'], '0')
        self.assertEqual(env['GHIDRA_DEBUGGER_TOOLS'], '0')
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['REVERIFY_LEDGER_DIR'], str(self.root / 'workspace' / 'reverify-ledger'))

    def test_reexec_preserves_configured_ghidra_root_after_home_is_isolated(self):
        installation = (self.root / 'local tools').resolve()
        python = installation / 'envs' / 'ghidra' / 'bin' / 'python'
        python.parent.mkdir(parents=True)
        python.touch()
        profile = {'java': '/private/jdk', 'ghidra': '/private/ghidra'}
        config = {'workspace_mcp': {'root': 'unused', 'ghidra_install_root': '~/local tools'}}
        with patch.dict(os.environ, HOME=str(self.root), USERPROFILE=str(self.root)), \
                patch.object(sys, 'platform', 'linux'), patch('os.umask'), \
                patch('tools.research.mcp_workspace.toolchain_config', return_value=config), \
                patch('tools.research.mcp_workspace.rea_profile', return_value=profile) as resolve, \
                patch('os.execve', side_effect=RuntimeError('intercepted exec')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'intercepted exec'):
                main(['ghidra'])
        resolve.assert_called_once_with('current', tools_root=installation)
        executable, command, env = launch.call_args.args
        self.assertEqual(executable, python)
        self.assertEqual(command[-2:], ['--install-root', str(installation)])
        self.assertEqual(env['HOME'], str(installation / 'home'))
        self.assertEqual(env['GHIDRA_INSTALL_DIR'], profile['ghidra'])

    def test_protocol_distinguishes_null_error_from_actual_failure(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 're_parse', 'arguments': {'file_path': str(self.target)}}}
        for error in (None, 'Malformed fixture'):
            with self.subTest(error=error):
                original = Mock(return_value={'jsonrpc': '2.0', 'id': 1, 'result': {
                    'content': [{'type': 'text', 'text': json.dumps({'error': error})}]}})
                response = reverify_request(original, request, self.inputs)
                self.assertEqual(response['result'].get('isError', False), error is not None)

    def test_protocol_rejects_unsafe_calls_without_dispatching(self):
        original = Mock()
        response = reverify_request(original, {
            'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 're_verify_claim', 'arguments': {
                'file_path': str(self.target), 'claims': [{'kind': 'emulate_result'}],
            }},
        }, self.inputs)
        original.assert_not_called()
        self.assertTrue(response['result']['isError'])
        self.assertEqual(response['id'], 2)

    def test_backend_waits_for_tcp_port_and_analysis_schema_not_just_socket(self):
        response = Mock(status=200)
        response.read.side_effect = [
            json.dumps({'tcp_port': -1}).encode(),
            json.dumps({'tcp_port': 41414}).encode(),
        ]
        uds = Mock()
        uds.return_value.getresponse.return_value = response
        tcp_response = Mock(status=200)
        tcp_response.read.return_value = json.dumps({
            'tools': [{'path': '/get_metadata', 'method': 'GET', 'params': []}],
        }).encode()
        transport = SimpleNamespace(UnixHTTPConnection=uds)
        backend = Mock()
        backend.poll.return_value = None
        with patch.dict(sys.modules, {'bridge_mcp_ghidra.transport': transport}), \
                patch('tools.research.mcp_workspace.http.client.HTTPConnection') as tcp, \
                patch('tools.research.mcp_workspace.time.sleep') as sleep:
            tcp.return_value.getresponse.return_value = tcp_response
            endpoint = wait_for_backend(backend, self.target, 'fixture-token', self.root / 'log')
        self.assertEqual(endpoint, 'http://127.0.0.1:41414')
        self.assertEqual(uds.call_count, 2)
        sleep.assert_called_once_with(0.1)
        tcp.assert_called_once_with('127.0.0.1', 41414, timeout=5)


if __name__ == '__main__':
    unittest.main()
