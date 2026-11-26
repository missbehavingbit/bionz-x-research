import os
import unittest
from unittest import mock

from tools.research.rea_wsl_batch import session_command
from tools.research.toolchain import rea_profile


class ReaWslLauncherTests(unittest.TestCase):
    def test_explicit_distro_and_launcher_preserve_argument_boundaries(self):
        values = {
            'A6400_REA_LAUNCHER': '/home/analyst/analysis tools/bin/rea',
            'A6400_REA_WSL_DISTRO': 'Dedicated Analysis',
        }
        with mock.patch.dict(os.environ, values, clear=True), mock.patch(
                'tools.research.rea_wsl_batch.os.name', 'nt'):
            self.assertEqual(session_command(), [
                'wsl.exe', '--distribution', 'Dedicated Analysis', '--exec',
                '/home/analyst/analysis tools/bin/rea', 'mcp',
            ])

    def test_linux_runs_the_selected_launcher_without_nesting_wsl(self):
        with mock.patch.dict(os.environ, {
            'A6400_REA_LAUNCHER': '/home/analyst/bin/rea',
            'A6400_REA_WSL_DISTRO': 'NotUsedOnLinux',
        }, clear=True), mock.patch('tools.research.rea_wsl_batch.os.name', 'posix'):
            self.assertEqual(session_command(), ['/home/analyst/bin/rea', 'mcp'])

    def test_invalid_configured_launcher_does_not_fall_back_to_legacy_runtime(self):
        for value in ('', 'rea', r'C:\tools\rea.cmd'):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {'A6400_REA_LAUNCHER': value}, clear=True,
            ), self.assertRaisesRegex(ValueError, 'absolute Linux executable'):
                session_command()

    def test_blank_distribution_is_not_silently_replaced_by_the_default(self):
        with mock.patch.dict(os.environ, {
            'A6400_REA_LAUNCHER': '/home/analyst/bin/rea',
            'A6400_REA_WSL_DISTRO': ' ',
        }, clear=True), mock.patch(
                'tools.research.rea_wsl_batch.os.name', 'nt'), self.assertRaisesRegex(
                ValueError, 'installed WSL distribution'):
            session_command()

    def test_legacy_linux_launch_remains_available_when_no_override_is_supplied(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                'tools.research.rea_wsl_batch.os.name', 'posix'):
            command = session_command(rea_profile('legacy'))
        self.assertEqual(command[0], 'env')
        self.assertIn('GHIDRA_INSTALL_DIR=/home/administrator/a6400-rea-tools/ghidra_12.1.4_PUBLIC',
                      command)
        self.assertEqual(command[-1], '--mcp')


if __name__ == '__main__':
    unittest.main()
