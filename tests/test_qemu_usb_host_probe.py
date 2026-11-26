"""Pure helpers of tools.research.qemu_usb_host_probe (no QEMU is started)."""
import argparse
import unittest
from unittest import mock

from tools.research.qemu_usb_host_probe import (
    DWC3_REGISTERS, GET_DEVICE_DESCRIPTOR, STATE_COMMAND, parse_setup, section,
    stock_osd_armed)


class QemuUsbHostProbeHelpers(unittest.TestCase):
    def test_setup_packet_must_be_eight_bytes(self):
        self.assertEqual(parse_setup("80 06 00 01 00 00 12 00"), GET_DEVICE_DESCRIPTOR)
        for bad in ("8006", "800600010000120000", "zz06000100001200"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_setup(bad)

    def test_section_takes_the_last_complete_marker_pair(self):
        console = (b"echo __USB_STATE_BEFORE__; ...\r\n__USB_STATE_BEFORE__\r\nold\r\n"
                   b"__USB_STATE_BEFORE_END__\r\n__USB_STATE_BEFORE__\r\ndwc3.0\r\n__USB_STATE_BEFORE_END__\r\n")
        self.assertEqual(section(console, "BEFORE").strip(), "dwc3.0")
        self.assertEqual(section(b"__USB_STATE_X__\nno end", "X"), "")

    def test_state_command_is_a_single_line_with_both_markers(self):
        command = STATE_COMMAND.format(tag="ATTACHED")
        self.assertNotIn("\n", command)
        self.assertTrue(command.startswith("echo __USB_STATE_ATTACHED__;"))
        self.assertTrue(command.endswith("echo __USB_STATE_ATTACHED_END__"))

    def test_register_addresses_follow_the_model_window(self):
        for name, address in DWC3_REGISTERS.items():
            self.assertTrue(0xF020C100 <= address < 0xF0210000, name)
        self.assertEqual(DWC3_REGISTERS["DCTL"], 0xF020C704)
        self.assertEqual(DWC3_REGISTERS["GCTL"], 0xF020C110)

    def test_powered_osd_gate_does_not_require_luma_or_accept_an_empty_plane(self):
        qmp = mock.Mock()
        for enable, base, expected in ((0x3000232, 0x23A80000, True),
                                       (0x3000231, 0x23A80000, False),
                                       (0x3000232, 0, False)):
            with self.subTest(enable=enable, base=base):
                qmp.request.side_effect = [f"f3440144: {enable:#x}", f"f3441320: {base:#x}"]
                self.assertEqual(stock_osd_armed(qmp), expected)


if __name__ == "__main__":
    unittest.main()
