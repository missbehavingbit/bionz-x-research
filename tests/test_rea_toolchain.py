"""Host profile selection and version guards; no WSL or firmware access."""
import json
import os
import unittest
from unittest.mock import patch

from tools.research.rea_wsl_batch import Session
from tools.research.toolchain import rea_profile


class ReaToolchainTests(unittest.TestCase):
    def test_relocation_keeps_system_java_and_node_paths_absolute(self):
        with patch.dict(os.environ, {'A6400_REA_TOOLS_ROOT': '/opt/analysis tools'}):
            current = rea_profile('current')
            legacy = rea_profile('legacy')
        self.assertTrue(current['entry'].startswith('/opt/analysis tools/'))
        self.assertEqual(legacy['node'], '/usr/bin/node')
        self.assertEqual(legacy['java'], '/usr/lib/jvm/java-21-openjdk-amd64')

    def test_unknown_profile_and_non_linux_root_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown REA'):
            rea_profile('missing')
        with patch.dict(os.environ, {'A6400_REA_TOOLS_ROOT': 'relative/tools'}):
            with self.assertRaisesRegex(ValueError, 'absolute Linux'):
                rea_profile('current')

    def test_session_uses_the_selected_executables_without_shell_interpolation(self):
        with patch.dict(os.environ, {}, clear=True), patch(
                'tools.research.rea_wsl_batch.subprocess.Popen') as popen:
            popen.return_value.stdout = []
            session = Session(None, toolchain='current')
            session.reader.join(timeout=1)
            command = popen.call_args.args[0]
            self.assertIn(session.toolchain['node'], command)
            self.assertIn(session.toolchain['entry'], command)
            self.assertIn('GHIDRA_INSTALL_DIR=' + session.toolchain['ghidra'], command)
            self.assertNotIn('shell', popen.call_args.kwargs)

    def test_initialize_rejects_an_unexpected_rea_server_version(self):
        with patch('tools.research.rea_wsl_batch.subprocess.Popen') as popen:
            popen.return_value.stdout = [
                (json.dumps({'id': 1, 'result': {
                    'serverInfo': {'name': 'rea', 'version': 'unexpected'}}}) + '\n').encode()
            ]
            session = Session(None, toolchain='current')
            with self.assertRaisesRegex(ValueError, 'server version mismatch'):
                session.request('initialize', {})


if __name__ == '__main__':
    unittest.main()
