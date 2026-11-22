"""Opt-in QEMU sony-a6400 CamPig/PQD_Ydet/RC0 live-view pipeline tests."""

import hashlib
import os
from pathlib import Path
import select
import struct
import subprocess
import tempfile
import time
import unittest

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU
from tools.research.qemu_boot_probe import Qmp


CAMPIG0 = 0xF3000000
YDET_STAT = 0xF3015700
YDET_WIN = 0xF3015848
SGC1 = 0xF3120000
GPIO_P0 = 0xF2910010
RC0_AIF = 0xF3881000
DRAM_BUF = 0x14E90000
DDRB = 0xF0105000
DFS = 0xF2916000
ZMA1 = 0xF34B0000


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuLiveViewPipelineTests(unittest.TestCase):
    def start_qtest(self, machine='sony-a6400', *extra):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        log = (root / 'qemu.log').open('wb')
        self.addCleanup(log.close)
        proc = subprocess.Popen(
            [str(QEMU), '-M', machine, '-accel', 'qtest', '-display', 'none',
             '-monitor', 'none', '-serial', 'none', '-net', 'none',
             '-qtest', 'stdio', '-qtest-log', str(root / 'qtest.log'), *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0)
        self.addCleanup(self._stop_proc, proc)
        return proc, root

    def _stop_proc(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                self.fail('Owned QEMU process required a forced kill')
        proc.stdin.close()
        proc.stdout.close()

    def qtest(self, proc, pending, text):
        proc.stdin.write((text + '\n').encode('ascii'))
        deadline = time.monotonic() + 5
        while b'\n' not in pending[0]:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([proc.stdout], [], [], remaining)[0]:
                self.fail(f'qtest timed out: {text}')
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                self.fail(f'qtest exited before replying: {text}')
            pending[0] += chunk
        line, pending[0] = pending[0].split(b'\n', 1)
        self.assertTrue(line.startswith(b'OK'), f'{text}: {line!r}')
        fields = line.split()
        return int(fields[1], 0) if len(fields) > 1 else None

    def test_default_machine_keeps_liveview_blocks_unmodelled(self):
        proc, _ = self.start_qtest('sony-a6400')
        pending = [b'']
        self.assertEqual(self.qtest(proc, pending, f'readl {GPIO_P0:#x}') & 4, 0)
        self.qtest(proc, pending, f'writel {YDET_WIN:#x} 14')
        self.qtest(proc, pending, f'writel {YDET_WIN + 4:#x} 1600')
        self.qtest(proc, pending, f'writel {YDET_WIN + 8:#x} 2')
        self.qtest(proc, pending, f'writel {YDET_WIN + 12:#x} 570')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x50:#x} 0x0c000000')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x10:#x} 0x0c')
        self.qtest(proc, pending, 'clock_step 40000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {YDET_STAT:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x08:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {DDRB + 0xA0:#x}'), 0)
        self.qtest(proc, pending, f'writel {DFS + 0x620:#x} 0x101')
        self.qtest(proc, pending, f'writel {DFS + 0x720:#x} 0x12')
        self.qtest(proc, pending, f'writel {DFS + 0x700:#x} 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {DFS + 0x610:#x}'), 0)

    def test_dfs_completion_requires_a_new_clock_switch(self):
        proc, _ = self.start_qtest('sony-a6400,dfs-control=on')
        pending = [b'']

        def read(off):
            return self.qtest(proc, pending, f'readl {DFS + off:#x}')

        def write(off, value):
            self.qtest(proc, pending, f'writel {DFS + off:#x} {value:#x}')

        write(0x610, 1)
        write(0x720, 0x12)
        write(0x620, 0x101)
        write(0x700, 1)
        self.assertEqual(read(0x610), 0)

        write(0x720, 0x12)
        write(0x700, 2)
        self.assertEqual(read(0x610), 0)
        write(0x700, 1)
        self.assertEqual(read(0x610) & 0x101, 1)
        self.assertEqual(read(0x720), 0x12)
        write(0x610, 0x100)
        self.assertEqual(read(0x610) & 0x101, 1)
        write(0x610, 1)
        write(0x700, 1)
        self.assertEqual(read(0x610), 0)

        write(0x620, 0x101)
        write(0x720, 0)
        write(0x700, 1)
        self.assertEqual(read(0x610) & 0x101, 1)

    def test_body_shutter_irq_remains_pending_after_pulse_only_with_opt_in(self):
        # A tiny ARM loop, with IRQ delivery still masked: read the two
        # distributor pending words, then publish them with an iteration count
        # in RAM. Reading the GIC directly from QMP has no current vCPU.
        guest = struct.pack(
            '<8I', 0xe59f0014, 0xe3a01a02, 0xe3a03000, 0xe8900030,
            0xe2833001, 0xe8810038, 0xeafffffb, 0xf800120c,
        )
        fixtures = PROJECT_ROOT / 'artifacts/tests'
        fixtures.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixtures) as directory:
            kernel = Path(directory) / 'pending-irq.bin'
            kernel.write_bytes(guest)
            for edge in (False, True):
                with self.subTest(edge=edge), \
                        tempfile.TemporaryDirectory(prefix='a6400-irq-') as runtime:
                    endpoint = Path(runtime) / 'qmp.sock'
                    log_path = Path(directory) / f'qemu-edge-{int(edge)}.log'
                    machine = 'sony-a6400,rear-controls=on'
                    if edge:
                        machine += ',shutter-edge-irq=on'
                    with log_path.open('wb') as log:
                        proc = subprocess.Popen(
                            [str(QEMU), '-M', machine, '-accel', 'tcg', '-smp', '1',
                             '-kernel', str(kernel), '-S', '-display', 'none',
                             '-monitor', 'none', '-serial', 'none', '-net', 'none',
                             '-icount', 'shift=0,sleep=on', '-qmp',
                             f'unix:{endpoint},server=on,wait=off'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                        )
                        qmp = None
                        try:
                            deadline = time.monotonic() + 10
                            while not endpoint.exists():
                                self.assertIsNone(proc.poll(), log_path.read_text())
                                self.assertLess(time.monotonic(), deadline, 'QMP startup timed out')
                                time.sleep(0.01)
                            qmp = Qmp(endpoint, deadline)
                            qmp.request('cont')

                            def sample():
                                result = qmp.request('human-monitor-command', {
                                    'command-line': 'xp /3wx 0x2000'})
                                words = result.partition(':')[2].split()
                                self.assertEqual(len(words), 3, result)
                                return tuple(int(value, 16) for value in words)

                            def pending_after(iteration):
                                while time.monotonic() < deadline:
                                    count, first, second = sample()
                                    if count > iteration:
                                        return first, second
                                    time.sleep(0.01)
                                self.fail('Guest IRQ samples did not advance')

                            s1, s2 = 1 << (122 % 32), 1 << (142 % 32)
                            before = pending_after(0)
                            self.assertEqual((before[0] & s1, before[1] & s2), (0, 0))
                            qmp.request('input-send-event', {'events': [{
                                'type': 'key', 'data': {
                                    'key': {'type': 'qcode', 'data': 'spc'}, 'down': True}}]})
                            count, _, _ = sample()
                            # The loop takes four instructions. At shift=0,
                            # 25,000 iterations outlast the 50-us GPIO pulses.
                            after = pending_after(count + 25_000)
                            self.assertEqual((after[0] & s1, after[1] & s2),
                                             (s1, s2) if edge else (0, 0))
                            qmp.request('quit')
                            self.assertEqual(proc.wait(timeout=5), 0)
                        finally:
                            if qmp is not None:
                                qmp.close()
                            self._stop_proc(proc)

    def test_zma1_startup_refuses_an_unrecognized_program(self):
        proc, _ = self.start_qtest('sony-a6400', '-global',
                                   'cxd90014-sa.zma1-startup=on')
        pending = [b'']

        def write(off, value):
            self.qtest(proc, pending, f'writel {ZMA1 + off:#x} {value:#x}')

        # Idle RAM is not the original startup code, even when armed for boot.
        write(0x000, 1)
        write(0x008, 0x80000000)
        write(0x030, 0x23FEA200)
        write(0x108, 0x80)
        write(0x004, 1)
        self.qtest(proc, pending, 'clock_step 100000')
        self.assertEqual(self.qtest(proc, pending, f'readl {ZMA1 + 0x104:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {ZMA1 + 0x004:#x}'), 0)

        # Ordinary jobs retain the existing null device behavior.
        write(0x108, 0x01)
        write(0x004, 1)
        self.qtest(proc, pending, 'clock_step 100000')
        self.assertEqual(self.qtest(proc, pending, f'readl {ZMA1 + 0x104:#x}'), 1)

    @unittest.skipUnless(os.environ.get('A6400_ZMA1_STARTUP_SIDECAR'),
                         'Requires a declared, checked ZMA1 startup sidecar')
    def test_zma1_fill_requires_supported_descriptor_and_delays_completion(self):
        sidecar = Path(os.environ['A6400_ZMA1_STARTUP_SIDECAR'])
        self.assertTrue(sidecar.is_file())
        self.assertEqual(sidecar.stat().st_size, 3204)
        image = sidecar.read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(),
                         'fc668233ba100ef04d8f8aa46336c78bb5baa53805e030b137f9fc9dd7e17ed0')
        program = image[0x200:]
        proc, _ = self.start_qtest('sony-a6400', '-global',
                                   'cxd90014-sa.zma1-startup=on', '-global',
                                   'cxd90014-sa.zma1-jpeg-fill=on')
        pending = [b'']

        def q(command):
            return self.qtest(proc, pending, command)

        program_pa, desc_pa, dst = 0x23fea200, 0x15ef8100, 0x3c001000
        result_pa = desc_pa + 0x400
        width, height = 64, 3
        extent = 2 * width * 16
        descriptor = bytearray(0x180)
        for offset, word in {
            0x18: width, 0x1c: height, 0x20: width, 0x44: dst,
            0x5c: result_pa, 0x68: 0x00800080,
            0x110: 1, 0x118: dst, 0x120: extent | 1, 0x124: dst,
        }.items():
            struct.pack_into('<I', descriptor, offset, word)

        q(f'write {program_pa:#x} {len(program):#x} 0x{program.hex()}')
        for offset, value in ((0, 1), (8, 0x80000000), (0x30, program_pa),
                              (0x108, 0x80), (4, 1)):
            q(f'writel {ZMA1 + offset:#x} {value:#x}')
        q('clock_step 100000')
        self.assertEqual(q(f'readl {ZMA1 + 0x104:#x}'), 0x80)
        q(f'writel {ZMA1 + 0x104:#x} 0x80')
        q(f'writel {ZMA1 + 0x108:#x} 0xff')
        q(f'writel {ZMA1 + 0x110:#x} 0x01001300')
        q(f'writel {ZMA1 + 0x114:#x} {desc_pa:#x}')

        q(f'write {desc_pa:#x} {len(descriptor):#x} 0x{descriptor.hex()}')
        q(f'writel {desc_pa + 0x120:#x} 1')
        q(f'writel {dst:#x} 0xa5a5a5a5')
        q(f'writel {dst + 2 * width * height:#x} 0xa5a5a5a5')
        q(f'writel {ZMA1 + 0x00c:#x} 1')
        q('clock_step 100000')
        self.assertEqual(q(f'readl {dst:#x}'), 0xa5a5a5a5)
        self.assertEqual(q(f'readl {result_pa + 4:#x}'), 0)
        self.assertEqual(q(f'readl {ZMA1 + 0x104:#x}'), 0)

        q(f'writel {desc_pa + 0x120:#x} {extent | 1:#x}')
        q(f'writel {ZMA1 + 0x00c:#x} 1')
        q('clock_step 99999')
        self.assertEqual(q(f'readl {result_pa + 4:#x}'), 0)
        q('clock_step 1')
        self.assertEqual(q(f'readl {dst:#x}'), 0x00800080)
        self.assertEqual(q(f'readl {dst + 2 * width * height - 4:#x}'), 0x00800080)
        self.assertEqual(q(f'readl {dst + 2 * width * height:#x}'), 0xa5a5a5a5)
        self.assertEqual(q(f'readl {result_pa + 4:#x}'), 1)
        self.assertEqual(q(f'readl {ZMA1 + 0x104:#x}'), 1)
        self.assertEqual(q(f'readl {ZMA1 + 0x00c:#x}'), 0)

    def test_ddrb_status_follows_supported_transition_only(self):
        proc, _ = self.start_qtest('sony-a6400,ddrb-control=on')
        pending = [b'']

        def read(off):
            return self.qtest(proc, pending, f'readl {DDRB + off:#x}')

        def write(off, value):
            self.qtest(proc, pending, f'writel {DDRB + off:#x} {value:#x}')

        # Native read-modify-write changes rank configuration, not idle bits.
        write(0xA0, (read(0xA0) & ~0x30300) | 0x20000)
        write(0xA4, 0)
        self.assertEqual(read(0xA0), 0x01020000)
        self.assertEqual(read(0xA4) & 1, 1)

        write(0x138, 0x100)
        write(0x90, 0x26)
        self.assertEqual(read(0x148), 0)
        write(0x138, read(0x138) | 0x300)
        self.assertEqual(read(0x148), 0x20200000)
        write(0x90, 0x26)
        self.assertEqual(read(0x90), 0)
        write(0x14C, 0x1FFFFFFF)
        write(0x90, 0xEA)
        self.assertEqual(read(0x148), 0x20000000)
        write(0x90, 0x26)
        self.assertEqual(read(0x148), 0x20000010)
        self.assertEqual((read(0x90) >> 16) & 0x3F, 0x24)
        write(0x14C, 0x10)
        self.assertEqual(read(0x148), 0x20000000)

        # A duplicate active control write must not create another rank event.
        write(0x138, 0x10300)
        self.assertEqual(read(0x148), 0x20000000)
        write(0x138, 0x10000)
        self.assertEqual(read(0x138), 0x10300)

        # The original idle sequence, unlike a duplicate, re-arms the transition.
        write(0x90, 1)
        write(0x138, read(0x138) & ~0x300)
        write(0x68, read(0x68) | 0x10000)
        self.assertEqual(read(0x148), 0)
        write(0x138, read(0x138) | 0x300)
        self.assertEqual(read(0x148), 0x20200000)
        write(0x14C, 0x1FFFFFFF)
        write(0x90, 0x26)
        self.assertEqual(read(0x148), 0x20000000)
        write(0x68, read(0x68) & ~0x10000)
        write(0x90, 0x26)
        self.assertEqual(read(0x148), 0x20000010)

    def test_campig_ydet_sgc1_and_rc0_dma_contracts(self):
        proc, _ = self.start_qtest('sony-a6400,liveview-source=bars,rear-controls=on')
        pending = [b'']
        self.assertEqual(self.qtest(proc, pending, f'readl {GPIO_P0:#x}') & 4, 4)

        # Sgc1 stopped latch returns 0x3fff3fff; continuous mode orders Event A before B.
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x218:#x}'), 0x3FFF3FFF)
        self.qtest(proc, pending, f'writel {SGC1 + 0x50:#x} 1')
        self.qtest(proc, pending, f'writel {SGC1 + 0x54:#x} 1')
        self.qtest(proc, pending, f'writel {SGC1 + 0x220:#x} 0x03470595')
        self.qtest(proc, pending, f'writel {SGC1 + 0x224:#x} 0x2e3')
        self.qtest(proc, pending, f'writel {SGC1 + 0x210:#x} 0x101')
        self.qtest(proc, pending, f'writel {SGC1 + 0x214:#x} 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x214:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x218:#x}'), 0x00800080)

        self.qtest(proc, pending, 'clock_step 29354722')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 0)
        self.qtest(proc, pending, 'clock_step 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 1)

        # Advance past frame end while Event A is unacked: Event B stays held until A is acked.
        self.qtest(proc, pending, 'clock_step 4012000')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x4c:#x}'), 0)
        self.qtest(proc, pending, f'writel {SGC1 + 0x48:#x} 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x4c:#x}'), 1)

        # Sgc1::Stop (0x6376a814) clears +0x200 and polls (+0x210 & 1) == 0.
        self.qtest(proc, pending, f'writel {SGC1 + 0x200:#x} 0')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x210:#x}') & 1, 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x218:#x}'), 0x3FFF3FFF)

        # Program PQD_Ydet window (14, 1600, 2, 570) and arm CamPig paths 2 & 3.
        self.qtest(proc, pending, f'writel {YDET_WIN:#x} 14')
        self.qtest(proc, pending, f'writel {YDET_WIN + 4:#x} 1600')
        self.qtest(proc, pending, f'writel {YDET_WIN + 8:#x} 2')
        self.qtest(proc, pending, f'writel {YDET_WIN + 12:#x} 570')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x50:#x} 0x0c000000')
        self.qtest(proc, pending, f'writel {CAMPIG0 + 0x10:#x} 0x0c')
        self.qtest(proc, pending, 'clock_step 33366667')
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {YDET_STAT:#x}'), 0)

        # A compare interrupt is not a completed input raster.
        self.qtest(proc, pending, f'writel {SGC1 + 0x210:#x} 1')
        self.qtest(proc, pending, 'clock_step 33366666')
        self.assertEqual(self.qtest(proc, pending, f'readl {SGC1 + 0x48:#x}'), 1)
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {YDET_STAT:#x}'), 0)
        self.qtest(proc, pending, 'clock_step 1')
        self.assertEqual(self.qtest(proc, pending, f'readl {CAMPIG0 + 0x48:#x}'), 0x0C000000)
        row0 = [self.qtest(proc, pending, f'readl {YDET_STAT + 4 * i:#x}') for i in range(8)]
        self.assertTrue(all(v > 0 for v in row0))
        self.assertEqual(row0, sorted(row0, reverse=True))
        self.assertGreater(row0[0], row0[-1] * 8)

        # An armed writer alone must not modify RAM or post completion.
        self.qtest(proc, pending, f'writel {DRAM_BUF + 0x400:#x} 0xdeadbeef')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3a0:#x} {DRAM_BUF:#x}')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3a4:#x} 0x400')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3a8:#x} 0x80')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x3ac:#x} 3')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x380:#x} 0x2')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x220:#x} 0xff010000')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x200:#x} 0x10802')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x08:#x} 0xf00f')
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x380:#x} 0x3')
        self.qtest(proc, pending, 'clock_step 2000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF:#x}'), 0)

        # RC0 AIF ch0 (IIB reader) completes the pair, asynchronously.
        self.qtest(proc, pending, f'writel {RC0_AIF + 0x200:#x} 0x10803')
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x380:#x}') & 1, 1)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x384:#x}'), 0x80000000)

        self.qtest(proc, pending, 'clock_step 2000000')
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF:#x}'), 0x1001)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x380:#x}') & 1, 0)
        self.assertEqual(self.qtest(proc, pending, f'readl {RC0_AIF + 0x384:#x}'), 0)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF:#x}'), 235)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF + 0x3ff:#x}'), 16)
        # Pitch skip region [0x400..0x47f] must remain untouched; line 1 starts at +0x480.
        self.assertEqual(self.qtest(proc, pending, f'readl {DRAM_BUF + 0x400:#x}'), 0xDEADBEEF)
        self.assertEqual(self.qtest(proc, pending, f'readb {DRAM_BUF + 0x480:#x}'), 235)

    def test_pgm_source_loads_external_image_and_rejects_bad_header(self):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            root = Path(tmp)
            bad = root / 'bad.pgm'
            bad.write_bytes(b'NOT_A_PGM')
            bad_run = subprocess.run(
                [str(QEMU), '-M', f'sony-a6400,liveview-source=pgm:{bad}',
                 '-accel', 'qtest', '-display', 'none', '-qtest', 'stdio'],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
            self.assertNotEqual(bad_run.returncode, 0)
            self.assertIn(b'not a binary P5 PGM', bad_run.stderr)

            pgm = root / 'scene.pgm'
            row_vals = bytes([20, 60, 100, 140, 180, 200, 220, 250])
            pgm.write_bytes(b'P5\n# deterministic test chart\n8 5\n255\n' + row_vals * 5)
            proc, _ = self.start_qtest(f'sony-a6400,liveview-source=pgm:{pgm}')
            pending = [b'']
            self.qtest(proc, pending, f'writel {YDET_WIN:#x} 0')
            self.qtest(proc, pending, f'writel {YDET_WIN + 4:#x} 1628')
            self.qtest(proc, pending, f'writel {YDET_WIN + 8:#x} 0')
            self.qtest(proc, pending, f'writel {YDET_WIN + 12:#x} 574')
            self.qtest(proc, pending, f'writel {CAMPIG0 + 0x10:#x} 0x0c')
            self.qtest(proc, pending, f'writel {SGC1 + 0x220:#x} 0x03470595')
            self.qtest(proc, pending, f'writel {SGC1 + 0x210:#x} 1')
            self.qtest(proc, pending, 'clock_step 33366667')
            zones = [self.qtest(proc, pending, f'readl {YDET_STAT + 4 * i:#x}') for i in range(8)]
            self.assertEqual(zones, sorted(zones))
            self.assertGreater(zones[-1], zones[0] * 10)
