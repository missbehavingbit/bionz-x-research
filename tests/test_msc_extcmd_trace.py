import struct
import unittest

from tools.research.msc_extcmd_trace import (
    DRIVER_SHA, KMOD, ReadinessControl, debugger_abi_controls, link_arm_call,
    linked_window, list_delete_control, original_text, original_thumb,
    registration_fields_control,
)


class OriginalInputTests(unittest.TestCase):
    def test_changed_binary_is_rejected_before_module_or_library_execution(self):
        for load in (lambda: original_text(b"changed input", "0" * 64),
                     lambda: original_thumb(b"changed input", "0" * 64, "function")):
            with self.subTest(load=load), self.assertRaisesRegex(ValueError, "identity mismatch"):
                load()


class ClosedCallGraphTests(unittest.TestCase):
    def test_binding_requires_the_exact_original_relocation_target(self):
        text = struct.pack("<I", 0xEBFFFFFE)
        for relocation in ({}, {0: {"type": 2, "symbol": "known"}},
                           {0: {"type": 28, "symbol": "other"}}):
            image = {"text": text, "relocations": relocation}
            with self.subTest(relocation=relocation), self.assertRaisesRegex(ValueError, "identity"):
                linked_window(image, 0x1000000, {0: ("known", 0x1001000)})

    def test_linked_arm_call_reaches_forward_and_backward_targets(self):
        place = 0x1001000
        for target in (0x1000100, 0x1800000):
            word = link_arm_call(0xEBFFFFFE, place, target)
            displacement = (word & 0xFFFFFF) << 2
            if displacement & 0x2000000:
                displacement -= 0x4000000
            self.assertEqual(place + 8 + displacement, target)

    def test_noncall_unaligned_and_out_of_range_bindings_are_rejected(self):
        for word, place, target in ((0xE1A00000, 0x1000, 0x2000),
                                     (0xEBFFFFFE, 0x1000, 0x2001),
                                     (0xEBFFFFFE, 0x1000, 0x4000000)):
            with self.subTest(target=target), self.assertRaises(ValueError):
                link_arm_call(word, place, target)


@unittest.skipUnless((KMOD / "usb_extcmd.ko").is_file(), "Private a6400A driver input unavailable")
class DebuggerAbiControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = original_text((KMOD / "usb_extcmd.ko").read_bytes(), DRIVER_SHA)

    def test_debugger_and_model_feature_keys_do_not_alias(self):
        for node_feature, expected_state in ((1, 0), (0x23, 2)):
            with self.subTest(node_feature=node_feature):
                result = ReadinessControl(self.driver, mask=1, feature=node_feature,
                                          command_feature=0x23).status(1)
                self.assertEqual(result["original_state_result"], expected_state)
                self.assertEqual(result["lookup_fixtures"][0]["feature"], 0x23)
                self.assertEqual(result["readiness_writes"], [])

    def test_registration_preserves_full_feature_and_distinct_buffer_sizes(self):
        result = registration_fields_control(self.driver, feature=0x1234,
                                             receive_size=512, send_size=2048)
        self.assertEqual(result["observed"]["context_feature"], 0x1234)
        self.assertEqual(result["observed"]["node_feature"], 0x1234)
        self.assertEqual(result["observed"]["receive_size"], 512)
        self.assertEqual(result["observed"]["send_size"], 2048)
        self.assertEqual(result["observed"]["readiness_mask"], 0)
        self.assertEqual(result["stop"], "create_fields_before_mutex_initialization")

    def test_deleting_debugger_node_preserves_other_registered_feature(self):
        result = list_delete_control(self.driver)
        self.assertEqual(result["surviving_feature"], 1)
        self.assertEqual([row["callee_fixture"] for row in result["helper_fixtures"]],
                         ["get_extcmd_this", "_mutex_lock", "get_extcmd_this", "_mutex_unlock"])

    def test_debugger_ioctls_reach_their_original_routes_without_qualifying_deployment(self):
        result = debugger_abi_controls(self.driver)
        self.assertEqual([row["operation"] for row in result["ioctl_routes"]],
                         ["create", "destroy", "receive", "send"])
        self.assertEqual(len({row["pc"] for row in result["ioctl_routes"]}), 4)
        self.assertEqual(len(result["feature_0x23_status_cases"]), 12)
        self.assertFalse(result["deployment_qualified"])
