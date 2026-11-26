"""Tests for per-body native USB debugger compatibility registry and checks."""
from pathlib import Path
import unittest

from src.a6400.targets.usb_debugger_compat import (
    get_debugger_target,
    list_debugger_targets,
    load_reference_usb_extcmd,
    verify_camera_debugger_compatibility,
)

ROOT = Path(__file__).resolve().parents[1]
ILCE_7C_PULL = ROOT / "artifacts/hardware/ilce-7c-2.00-pull"
ILCE_6400_UPDATER = (
    ROOT
    / "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked"
)


class TestUsbDebuggerCompat(unittest.TestCase):
    def test_registry_lists_qualified_targets(self):
        targets = list_debugger_targets()
        self.assertIn("ilce_6400", targets)
        self.assertIn("ilce_7c", targets)
        self.assertIn("zv_e10", targets)
        self.assertEqual(get_debugger_target("ILCE-7C").target_id, "ilce_7c")
        self.assertEqual(get_debugger_target("ZV-E10").target_id, "zv_e10")
        self.assertEqual(get_debugger_target("zve10").target_id, "zv_e10")

    def test_unknown_target_raises(self):
        with self.assertRaises(KeyError):
            get_debugger_target("ilce_9999")

    @unittest.skipUnless(
        (ILCE_7C_PULL / "usb_extcmd.ko").is_file()
        and (ILCE_7C_PULL / "libusbcmd.so").is_file(),
        "ILCE-7C 2.00 pull fixtures not present",
    )
    def test_ilce_7c_live_pull_is_compatible_and_rejects_mutations(self):
        spec = get_debugger_target("ilce_7c")
        actual_ko = (ILCE_7C_PULL / "usb_extcmd.ko").read_bytes()
        actual_lib = (ILCE_7C_PULL / "libusbcmd.so").read_bytes()
        _, ref_ko = load_reference_usb_extcmd(spec, ROOT)

        ok = verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, ref_ko)
        self.assertTrue(ok["compatible"], ok)
        self.assertTrue(ok["libusbcmd_match"])
        self.assertTrue(ok["usb_extcmd_match"])
        self.assertTrue(ok["exact_usb_extcmd_match"])
        self.assertIsNone(ok["reason"])

        # Also works without a local reference file (e.g. fresh clone without proprietary pulls)
        ok_no_ref = verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, None)
        self.assertTrue(ok_no_ref["compatible"], ok_no_ref)

        # Mutate a libusbcmd.so byte -> must fail
        bad_lib = bytearray(actual_lib)
        bad_lib[0] ^= 0xFF
        fail_lib = verify_camera_debugger_compatibility(spec, actual_ko, bytes(bad_lib), ref_ko)
        self.assertFalse(fail_lib["compatible"])
        self.assertFalse(fail_lib["libusbcmd_match"])
        self.assertIn("libusbcmd.so mismatch", fail_lib["reason"])

        # Mutate a code/header byte in usb_extcmd.ko -> must fail
        bad_ko = bytearray(actual_ko)
        bad_ko[100] ^= 0xFF
        fail_ko = verify_camera_debugger_compatibility(spec, bytes(bad_ko), actual_lib, ref_ko)
        self.assertFalse(fail_ko["compatible"])
        self.assertFalse(fail_ko["usb_extcmd_match"])
        self.assertIn("usb_extcmd.ko mismatch", fail_ko["reason"])

    def test_accepted_libusbcmd_variants_scoped_per_target(self):
        self.assertEqual(
            len(get_debugger_target("ilce_6400").accepted_libusbcmd_variants()),
            2,
        )
        self.assertEqual(
            len(get_debugger_target("ilce_7c").accepted_libusbcmd_variants()),
            1,
        )
        self.assertEqual(
            len(get_debugger_target("ilce_6400a").accepted_libusbcmd_variants()),
            1,
        )

    @unittest.skipUnless(
        (ILCE_6400_UPDATER / "kmod/usb_extcmd.ko").is_file()
        and (ILCE_6400_UPDATER / "lib/libusbcmd.so").is_file(),
        "Extracted ILCE-6400 FW 2.00 updater image fixtures not present",
    )
    def test_ilce_6400_accepts_official_fw200_updater_libusbcmd_and_rejects_mutations(self):
        spec = get_debugger_target("ilce_6400")
        actual_ko = (ILCE_6400_UPDATER / "kmod/usb_extcmd.ko").read_bytes()
        actual_lib = (ILCE_6400_UPDATER / "lib/libusbcmd.so").read_bytes()
        _, ref_ko = load_reference_usb_extcmd(spec, ROOT)

        ok = verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, ref_ko)
        self.assertTrue(ok["compatible"], ok)
        self.assertTrue(ok["libusbcmd_match"])
        self.assertTrue(ok["usb_extcmd_match"])
        self.assertTrue(ok["exact_usb_extcmd_match"])
        self.assertIsNone(ok["reason"])

        ok_no_ref = verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, None)
        self.assertTrue(ok_no_ref["compatible"], ok_no_ref)

        bad_lib = bytearray(actual_lib)
        bad_lib[0] ^= 0xFF
        fail_lib = verify_camera_debugger_compatibility(spec, actual_ko, bytes(bad_lib), ref_ko)
        self.assertFalse(fail_lib["compatible"])
        self.assertFalse(fail_lib["libusbcmd_match"])
        self.assertIn("libusbcmd.so mismatch", fail_lib["reason"])

        a6400a_spec = get_debugger_target("ilce_6400a")
        fail_a6400a = verify_camera_debugger_compatibility(a6400a_spec, actual_ko, actual_lib, None)
        self.assertFalse(fail_a6400a["compatible"])
        self.assertFalse(fail_a6400a["libusbcmd_match"])


if __name__ == "__main__":
    unittest.main()
