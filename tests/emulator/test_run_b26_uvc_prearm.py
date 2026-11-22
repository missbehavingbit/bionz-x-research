"""Offline unit tests for the Run B26 (Phase 4) E0476 pre-arm harness."""
from unittest import mock
import unittest

from tools.research.run_b26_uvc_prearm import (
    DAEMON_PATH,
    MODULE_PATH,
    composite_state,
    parse_b26_log_evidence,
    verify_local_artifacts,
)


class RunB26HarnessTests(unittest.TestCase):
    @unittest.skipUnless(
        MODULE_PATH.is_file() and DAEMON_PATH.is_file(),
        "local E0476 prearm/daemon build artifacts not present",
    )
    def test_verify_local_artifacts_pins_e0476_module_and_daemon(self):
        result = verify_local_artifacts()
        self.assertTrue(result["module"]["ok"], result["module"])
        self.assertTrue(result["daemon"]["ok"], result["daemon"])
        self.assertTrue(result["all_ok"])

    def test_verify_local_artifacts_fails_closed_when_missing(self):
        with mock.patch("tools.research.run_b26_uvc_prearm.MODULE_PATH", MODULE_PATH.with_name("missing.ko")):
            with mock.patch("tools.research.run_b26_uvc_prearm.DAEMON_PATH", DAEMON_PATH.with_name("missing-lived")):
                result = verify_local_artifacts()
        self.assertFalse(result["module"]["ok"])
        self.assertFalse(result["daemon"]["ok"])
        self.assertFalse(result["all_ok"])

    def test_parse_b26_log_evidence_accepts_prearm_r1(self):
        sample = (
            "[    7.368051] [a6400_uvc] PREARM d=0 st=2 nx=3 r=1\n"
            "[    7.436067] [a6400_uvc] PREARM d=1 st=2 nx=3 r=1\n"
            "[   10.000000] [a6400_uvc] RAMRING n=20 dropped=0 start=1 class=3 "
            "get=1 set=2 queue=3 complete=3 prearm=1/1/0 st=2 nx=3\n"
        )
        verdict = parse_b26_log_evidence(sample)
        self.assertTrue(verdict["has_get_cur_prearm_r1"])
        self.assertTrue(verdict["accepted"])
        self.assertEqual(verdict["ramring"]["prearm_armed"], 1)
        self.assertEqual(verdict["ramring"]["prearm_skipped"], 0)

    def test_parse_b26_log_evidence_rejects_missing_or_skipped_prearm(self):
        sample = (
            "[    7.368051] [a6400_uvc] PREARM d=0 st=1 nx=1 r=0\n"
            "[   10.000000] [a6400_uvc] RAMRING n=10 dropped=0 start=1 class=2 "
            "get=1 set=1 queue=2 complete=2 prearm=1/0/1 st=1 nx=1\n"
        )
        verdict = parse_b26_log_evidence(sample)
        self.assertFalse(verdict["has_get_cur_prearm_r1"])
        self.assertFalse(verdict["accepted"])

    def test_composite_state_detects_0322_and_0ca8(self):
        c0322 = [
            {"status": "OK", "instance": r"USB\VID_054C&PID_0322\1234"},
            {"status": "OK", "instance": r"USB\VID_054C&PID_0322&MI_00\1234"},
            {"status": "OK", "instance": r"USB\VID_054C&PID_0322&MI_01\1234"},
        ]
        self.assertEqual(composite_state(c0322), (True, True, True, False))
        msc = [{"status": "OK", "instance": r"USB\VID_054C&PID_0CA8\1234"}]
        self.assertEqual(composite_state(msc), (False, False, False, True))


if __name__ == "__main__":
    unittest.main()
