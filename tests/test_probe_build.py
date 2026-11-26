"""Probe subprocess routing checks without WSL, compilers or hardware access."""
import subprocess
import unittest
from unittest.mock import patch

from probes import build_probe


class ProbeBuildRoutingTests(unittest.TestCase):
    def test_windows_uses_the_installed_default_wsl_distribution(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b'gcc version\n', stderr=b'')
        with patch.object(build_probe.sys, 'platform', 'win32'), \
                patch.object(build_probe.subprocess, 'run', return_value=completed) as run:
            result = build_probe.wsl(['gcc', '--version'])
        self.assertEqual(run.call_args.args[0], ['wsl.exe', '--exec', 'gcc', '--version'])
        self.assertEqual(result.stdout_text, 'gcc version\n')


if __name__ == '__main__':
    unittest.main()
