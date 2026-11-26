"""Independent playback controls for the Open Gate clip verifier."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tools.research.verify_open_gate import verify_open_gate


class OpenGateTests(unittest.TestCase):
    def setUp(self):
        artifacts = PROJECT_ROOT / 'artifacts/tests'
        artifacts.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(tmp.cleanup)
        self.before, self.after = Path(tmp.name) / 'before', Path(tmp.name) / 'after'
        self.before.mkdir()
        self.after.mkdir()
        self.clip = self.after / 'C0002.MP4'

    def test_requires_a_new_clip(self):
        (self.before / 'C0001.MP4').write_bytes(b'old recording')
        (self.after / 'c0001.mp4').write_bytes(b'old recording')
        with self.assertRaisesRegex(ValueError, 'found 0'):
            verify_open_gate(self.before, self.after, 96, 64)
        self.clip.write_bytes(b'old recording')
        with self.assertRaisesRegex(ValueError, 'unchanged baseline'):
            verify_open_gate(self.before, self.after, 96, 64)
        (self.after / 'C0003.MP4').write_bytes(b'another recording')
        with self.assertRaisesRegex(ValueError, 'found 2'):
            verify_open_gate(self.before, self.after, 96, 64)

    def test_rejects_non_open_gate_target(self):
        with self.assertRaisesRegex(ValueError, 'exact 3:2'):
            verify_open_gate(self.before, self.after, 1920, 1080)

    def fixture(self, size='96x64', sar='1/1'):
        if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
            self.skipTest('ffmpeg and ffprobe are not installed')
        subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
             f'testsrc2=size={size}:rate=8:duration=1', '-vf', f'setsar={sar}',
             '-c:v', 'mpeg4', '-movflags', '+faststart', str(self.clip)],
            capture_output=True, check=True, timeout=15)

    def test_decodes_all_frames_of_a_square_pixel_3_to_2_clip(self):
        self.fixture()
        result = verify_open_gate(self.before, self.after, 96, 64)
        self.assertEqual((result['decoded_frames'], result['decoded_time_us']), (8, 1_000_000))

    def test_rejects_3_to_2_metadata_on_a_different_raster(self):
        self.fixture(size='112x64', sar='6/7')
        with self.assertRaisesRegex(ValueError, 'got 112x64'):
            verify_open_gate(self.before, self.after, 96, 64)

    def test_rejects_non_square_pixels_on_the_right_raster(self):
        self.fixture(sar='4/3')
        with self.assertRaisesRegex(ValueError, 'square-pixel'):
            verify_open_gate(self.before, self.after, 96, 64)

    def test_rejects_truncated_video_with_intact_mp4_metadata(self):
        self.fixture()
        self.clip.write_bytes(self.clip.read_bytes()[:self.clip.stat().st_size * 3 // 4])
        with self.assertRaisesRegex(ValueError, 'could not read/decode|declares'):
            verify_open_gate(self.before, self.after, 96, 64)


if __name__ == '__main__':
    unittest.main()
