"""qemu-a6400/tools/fat_add_file.py: root and one-level subdirectory placement on a constructed FAT12 image."""
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "qemu-a6400" / "tools" / "fat_add_file.py"

BPS, SPC, RESERVED, NFATS, ROOT_ENTRIES, TOTAL, FAT_SECTORS = 512, 1, 1, 2, 16, 64, 1


def make_image(path: Path) -> None:
    img = bytearray(TOTAL * BPS)
    struct.pack_into("<HBHBHHBH", img, 11, BPS, SPC, RESERVED, NFATS, ROOT_ENTRIES, TOTAL, 0xF8, FAT_SECTORS)
    fat = bytearray(FAT_SECTORS * BPS)
    fat[0:3] = b"\xF8\xFF\xFF"
    # cluster 2 = directory MODE, end of chain (FAT12 entry 2 = 0xFFF)
    fat[3] = 0xFF
    fat[4] = (fat[4] & 0xF0) | 0x0F
    for f in range(NFATS):
        img[(RESERVED + f * FAT_SECTORS) * BPS:(RESERVED + (f + 1) * FAT_SECTORS) * BPS] = fat
    root_off = (RESERVED + NFATS * FAT_SECTORS) * BPS
    entry = bytearray(32)
    entry[0:11] = b"MODE       "
    entry[11] = 0x10
    struct.pack_into("<H", entry, 26, 2)
    img[root_off:root_off + 32] = entry
    path.write_bytes(img)


def layout():
    root_off = (RESERVED + NFATS * FAT_SECTORS) * BPS
    data_start = root_off + ROOT_ENTRIES * 32
    return root_off, data_start


class FatAddFileTest(unittest.TestCase):
    def run_tool(self, image, src, name):
        return subprocess.run([sys.executable, str(TOOL), str(image), str(src), name], capture_output=True, text=True)

    def test_subdirectory_file_lands_in_directory_cluster(self):
        with tempfile.TemporaryDirectory() as d:
            image, src = Path(d) / "set.img", Path(d) / "dmode"
            make_image(image)
            src.write_bytes(b"2\n")
            r = self.run_tool(image, src, "mode/dmode")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("mode/ slot", r.stdout)
            img = image.read_bytes()
            root_off, data_start = layout()
            entry = img[data_start:data_start + 32]          # first slot of cluster 2 (MODE)
            self.assertEqual(entry[0:11], b"DMODE      ")
            self.assertEqual(entry[12], 0x08)                # lowercase base name
            first = struct.unpack_from("<H", entry, 26)[0]
            self.assertEqual(struct.unpack_from("<I", entry, 28)[0], 2)
            off = data_start + (first - 2) * BPS * SPC
            self.assertEqual(img[off:off + 2], b"2\n")
            self.assertEqual(img[root_off + 32], 0)          # root untouched beyond MODE

    def test_refuses_overwrite_and_missing_directory(self):
        with tempfile.TemporaryDirectory() as d:
            image, src = Path(d) / "set.img", Path(d) / "dmode"
            make_image(image)
            src.write_bytes(b"2\n")
            self.assertEqual(self.run_tool(image, src, "mode/dmode").returncode, 0)
            again = self.run_tool(image, src, "mode/dmode")
            self.assertNotEqual(again.returncode, 0)
            self.assertIn("already exists", again.stderr)
            missing = self.run_tool(image, src, "nope/dmode")
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("not found", missing.stderr)

    def test_root_placement_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            image, src = Path(d) / "set.img", Path(d) / "a.bin"
            make_image(image)
            src.write_bytes(b"x" * 700)
            r = self.run_tool(image, src, "STG00.BIN")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("root slot 1", r.stdout)

    def test_if_absent_accepts_same_size_and_refuses_a_different_one(self):
        with tempfile.TemporaryDirectory() as d:
            image, src = Path(d) / "set.img", Path(d) / "ulogio.bin"
            make_image(image)
            src.write_bytes(b"u" * 300)
            self.assertEqual(self.run_tool(image, src, "ulogio.bin").returncode, 0)
            same = subprocess.run([sys.executable, str(TOOL), "--if-absent", str(image), str(src),
                                   "ulogio.bin"], capture_output=True, text=True)
            self.assertEqual(same.returncode, 0, same.stderr)
            self.assertIn("already present (300 B)", same.stdout)
            src.write_bytes(b"u" * 301)
            other = subprocess.run([sys.executable, str(TOOL), "--if-absent", str(image), str(src),
                                    "ulogio.bin"], capture_output=True, text=True)
            self.assertNotEqual(other.returncode, 0)
            self.assertIn("already exists", other.stderr)
            # A plain add is still strict about an existing name.
            strict = self.run_tool(image, src, "ulogio.bin")
            self.assertNotEqual(strict.returncode, 0)
            self.assertIn("already exists", strict.stderr)


if __name__ == "__main__":
    unittest.main()
