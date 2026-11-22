"""Offline checks for the QEMU mod development tools (no QEMU, guest or camera access)."""
import os
import re
import struct
import sys
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qemu-a6400" / "tools"))

import moddev  # noqa: E402
from tools.research.pack_mod_v2 import read_layout, reloc_table  # noqa: E402

LAYOUT = ROOT / "tools/native_camera/mlv2/reference_layout_ilce6400.txt"
MTF_ELF = ROOT / "artifacts/tests/mtf-v2-reloc-build-1/mtf.elf"


def printf_decode(line: str) -> bytes:
    """What the ash printf builtin writes for one generated line."""
    m = re.fullmatch(r"printf '((?:\\[0-7]{3})*)' >>? \S+", line)
    assert m, line
    return bytes(int(o, 8) for o in re.findall(r"\\([0-7]{3})", m.group(1)))


class PushEncoding(unittest.TestCase):
    def test_round_trip_and_line_limit(self):
        data = bytes(range(256)) * 5 + b"%'\\\n"
        lines = moddev.push_commands("/tmp_bt/mods/.X.mod.part", data)
        self.assertEqual(b"".join(printf_decode(line) for line in lines), data)
        self.assertTrue(lines[0].endswith("> /tmp_bt/mods/.X.mod.part"))
        self.assertTrue(all(" >> " in line for line in lines[1:]))
        self.assertLess(max(map(len, lines)), 900)       # guest tty truncates near 1 KiB

    def test_refuses_unsafe_paths(self):
        for bad in ("relative", "/tmp/a b", "/tmp/a'b", "/tmp/$x"):
            with self.assertRaises(ValueError):
                moddev.push_commands(bad, b"x")

    def test_fnv_and_staging(self):
        self.assertEqual(moddev.fnv1a_32(b""), 0x811C9DC5)
        self.assertEqual(moddev.parse_fnv("noise\r\nFNV size=12 fnv=0a1b2c3d\r\n"), (12, 0x0A1B2C3D))
        self.assertIsNone(moddev.parse_fnv("FNV missing"))
        self.assertEqual(moddev.setting_files(PurePosixPath("/tmp/g"), PurePosixPath("/tmp/r")), "/tmp/g:MLV2GRD.ELF /tmp/r:MLV2.REQ")
        with self.assertRaises(ValueError):
            moddev.setting_files(PurePosixPath("/a b/g"), PurePosixPath("/tmp/r"))
        # one existing /setting directory level, e.g. bootin's mode file
        self.assertEqual(moddev.setting_files(PurePosixPath("/tmp/g"), PurePosixPath("/tmp/r"),
                                              [(PurePosixPath("/tmp/d"), "mode/dmode")]),
                         "/tmp/g:MLV2GRD.ELF /tmp/r:MLV2.REQ /tmp/d:mode/dmode")
        for bad in ("a/b/c", "../dmode", "mode/dm de", "toolongdir/x"):
            with self.assertRaises(ValueError):
                moddev.setting_files(PurePosixPath("/tmp/g"), PurePosixPath("/tmp/r"), [(PurePosixPath("/tmp/d"), bad)])


class ToolbarActions(unittest.TestCase):
    def test_windows_paths_map_to_wsl(self):
        self.assertEqual(moddev.host_path(r'"C:\Users\A b\x.mod"').as_posix(), "/mnt/c/Users/A b/x.mod")
        self.assertEqual(moddev.host_path("/home/u/y.mod").as_posix(), "/home/u/y.mod")

    def test_request_validation_and_queue(self):
        dev = moddev.ModDev(console=None, loader=Path("loader"), mods=[Path("a/MenuTouchFull.mod")])
        self.assertEqual([m["name"] for m in dev.status["mods"]], ["MenuTouchFull.mod"])
        dev.request("reload", "MenuTouchFull.mod")
        dev.request("add", Path("b/Second.mod"))
        dev.request("rescan")
        self.assertEqual(dev.status["queued"], 3)
        for bad in (("reload", "Missing.mod"), ("remove", "Missing.mod"), ("add", Path("../x.txt")), ("format", None)):
            with self.assertRaises(ValueError):
                dev.request(*bad)

    def test_upload_check(self):
        import rear_panel
        rear_panel.check_smod(b"SMOD" + struct.pack("<I", 2) + bytes(60))
        for bad in (b"junk", b"SMOD" + struct.pack("<I", 3) + bytes(60)):
            with self.assertRaises(ValueError):
                rear_panel.check_smod(bad)


class RearControls(unittest.TestCase):
    def test_wheel_and_lever_bypass_the_timed_key_queue(self):
        from rear_panel import press_control
        for name, qcode in (("wheel-left", "bracket_left"), ("wheel-right", "bracket_right"),
                            ("af-lever", "b"), ("eye-sensor", "e")):
            with self.subTest(control=name):
                qmp = Mock()
                press_control(qmp, name)
                key = {"type": "qcode", "data": qcode}
                qmp.command.assert_called_once_with("input-send-event", events=[
                    {"type": "key", "data": {"key": key, "down": True}},
                    {"type": "key", "data": {"key": key, "down": False}},
                ])

    def test_menu_direction_and_af_button_keep_the_debounce_hold(self):
        from rear_panel import press_control
        for name, qcode in (("menu", "m"), ("exposure", "down"), ("af-button", "a")):
            with self.subTest(control=name):
                qmp = Mock()
                press_control(qmp, name)
                qmp.command.assert_called_once_with(
                    "send-key", keys=[{"type": "qcode", "data": qcode}], **{"hold-time": 180})

    def test_panel_uses_stock_display_selection_unless_diagnostic_lcd_requested(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        import rear_panel

        captured_envs = []

        class DummyProc:
            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs["env"])
            return DummyProc()

        for diagnostic_lcd, expected, timing in (
            (False, "0", None),
            (True, "1", "-accel tcg,thread=multi"),
        ):
            with self.subTest(diagnostic_lcd=diagnostic_lcd), tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp)
                args = SimpleNamespace(
                    log=scratch / "panel.log",
                    stock=False,
                    diagnostic_lcd=diagnostic_lcd,
                    uvc_test=False,
                    inputs=None,
                    loader=None,
                    mod=[],
                )
                server = SimpleNamespace(server_port=8640)
                with patch.dict(os.environ), \
                        patch.object(rear_panel.subprocess, "Popen", side_effect=fake_popen), \
                        patch.object(rear_panel, "Qmp", return_value=SimpleNamespace(close=lambda: None)):
                    os.environ.pop("A6400_TIMING", None)
                    if timing is not None:
                        os.environ["A6400_TIMING"] = timing
                    rear_panel.serve(args, scratch, server)
                self.assertEqual(captured_envs[-1]["REAR_LCD_DIAGNOSTIC"], expected)
                self.assertEqual(
                    captured_envs[-1]["QEMU_EXTRA"].partition(" -qmp ")[0],
                    timing or "-icount shift=0,sleep=on",
                )


class RelocationTable(unittest.TestCase):
    def test_reference_layout_ranges(self):
        layout = read_layout(LAYOUT)
        self.assertEqual(layout["viewUnified2.so"], (0x155E6000, 0x1615F000))   # includes its bss
        self.assertEqual(layout["libObj.so"], (0x05D18000, 0x07144000))
        self.assertEqual(layout["libtestcmd.so"][0], 0x05660000)

    @unittest.skipUnless(MTF_ELF.exists(), "relocatable MenuTouchFull build not present")
    def test_menu_touch_full_words(self):
        from tools.research.pack_mod import extract_elf_text
        elf = MTF_ELF.read_bytes()
        _, code, _ = extract_elf_text(elf, expected_code_addr=0)
        blob, report, libs = reloc_table(code, elf, read_layout(LAYOUT), [0x06309D48, 0x06309E54])
        self.assertEqual(set(libs), {"libObj.so", "viewUnified2.so", "CautionConfig.so"})
        values = {int(r["value"], 16) for r in report}
        self.assertIn(0x6309D55, values)                  # trampoline return into libObj
        self.assertIn(0x15B92045, values)                 # viewUnified2 enter_selected
        self.assertNotIn(0x15010102, values)              # event code in anonymous memory
        self.assertNotIn(0x956F0, values)                 # heap object
        magic, nlib, nrel = struct.unpack_from("<IHH", blob)
        self.assertEqual((magic, nlib, nrel), (0x31434C52, 3, len(report)))
        self.assertEqual(struct.unpack_from("<I", blob, len(blob) - 4)[0], moddev.fnv1a_32(blob[:-4]))


MTL_ELF = ROOT / "artifacts/tests/mtl-v2-build-1/mtl.elf"


class EmbeddedData(unittest.TestCase):
    @unittest.skipUnless(MTL_ELF.exists(), "relocatable ModsTabLook build not present")
    def test_icon_blob_needs_data_symbol(self):
        from tools.research.pack_mod import extract_elf_text
        elf = MTL_ELF.read_bytes()
        _, code, _ = extract_elf_text(elf, expected_code_addr=0)
        layout = read_layout(LAYOUT)
        with self.assertRaises(ValueError):          # icon bytes look like addresses in a large region
            reloc_table(code, elf, layout, [0x064B54B6])
        _, report, libs = reloc_table(code, elf, layout, [0x064B54B6], data_symbols=["mtl_icon_rle"])
        self.assertEqual([int(r["value"], 16) for r in report], [0x064B5487, 0x064B54C3])
        self.assertEqual(set(libs), {"libObj.so"})


class RearLcdOverlay(unittest.TestCase):
    def test_both_backings_patch_the_same_instruction(self):
        sys.path.insert(0, str(ROOT / "qemu-a6400" / "tools"))
        import rear_lcd_overlay
        offsets = [o for o, _ in rear_lcd_overlay.KNOWN]
        self.assertEqual(offsets, [0x360EAA00, 0x37EA5200])
        self.assertTrue(all(o % 512 == 0 for o in offsets))


if __name__ == "__main__":
    unittest.main()
