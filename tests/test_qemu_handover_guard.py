"""QEMU handover guard (E0638): ARMv7 cache/MMU teardown checks before a kexec-style jump.

Builds qemu-a6400/tools/handover_guard.c (TCG plugin) and the bare-metal fixture
qemu-a6400/tools/handover_payload.S in variants that each drop one teardown step,
runs them on the sony-a6400 machine, and checks that exactly the expected guard
items fail.  Needs the optional WSL/Linux QEMU build and arm-linux-gnueabi-gcc.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import QEMU as _DEFAULT_QEMU

TOOLS = PROJECT_ROOT / 'qemu-a6400/tools'
_FALLBACK_CROSS = Path(
    '/home/administrator/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin/arm-linux-gnueabi-gcc'
)
_ENV_QEMU = os.environ.get('BIONZX_QEMU_TEST_BINARY')
QEMU = Path(_ENV_QEMU) if _ENV_QEMU else _DEFAULT_QEMU
NATIVE_AVAILABLE = os.name == 'posix' and QEMU.is_file()
CROSS = shutil.which('arm-linux-gnueabi-gcc') or (
    str(_FALLBACK_CROSS) if os.name == 'posix' and _FALLBACK_CROSS.is_file() else None
)


def _resolve_objcopy(cross):
    found = shutil.which('arm-linux-gnueabi-objcopy')
    if found:
        return found
    if cross:
        sibling = Path(cross).with_name(Path(cross).name.replace('gcc', 'objcopy'))
        if sibling.is_file():
            return str(sibling)
    return shutil.which('objcopy')


OBJCOPY = _resolve_objcopy(CROSS)
NEW_ENTRY = 0x00600000
DMA_CONTRACTS = TOOLS / 'handover_dma_contracts.txt'

# variant -> guard items expected to FAIL (empty = PASS)
VARIANTS = {
    'GOOD': set(),
    'KEEP_MMU': {'G1'},
    'KEEP_DCACHE': {'G2', 'G4'},
    'KEEP_ICACHE': {'G3'},
    'NO_CLEAN': {'G4', 'G5'},
    'NO_ICINV': {'G5'},
    'NO_DSB': {'G6'},
    'NO_ISB': {'G7'},
    'IRQ_ON': {'G8'},
    'ROGUE_CPU1': {'G9'},
}


@unittest.skipUnless(NATIVE_AVAILABLE and CROSS and OBJCOPY and shutil.which('pkg-config'),
                     'Optional WSL/Linux QEMU build or ARM cross compiler absent')
class QemuHandoverGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = PROJECT_ROOT / 'artifacts/tests'
        directory.mkdir(parents=True, exist_ok=True)
        cls._tmp = tempfile.TemporaryDirectory(dir=directory)
        cls.root = Path(cls._tmp.name)
        include = QEMU.parent.parent / 'include/plugins'
        if not (include / 'qemu-plugin.h').is_file():
            include = _DEFAULT_QEMU.parent.parent / 'include/plugins'
        glib = subprocess.run(['pkg-config', '--cflags', 'glib-2.0'], check=True,
                              capture_output=True, text=True).stdout.split()
        cls.plugin = cls.root / 'handover_guard.so'
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fPIC', '-shared', *glib,
                        '-I', str(include), str(TOOLS / 'handover_guard.c'),
                        '-o', str(cls.plugin)], check=True)
        help_out = subprocess.run(
            [str(QEMU), '-M', 'sony-a6400,help'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
        cls.has_dwc3_gadget = 'dwc3-gadget' in (help_out.stdout + help_out.stderr)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def build_payload(self, variant):
        binary = self.root / f'{variant}.bin'
        if binary.is_file():
            return binary
        elf = self.root / f'{variant}.elf'
        binutils = (['-B/usr/arm-linux-gnueabi/bin/']
                    if Path('/usr/arm-linux-gnueabi/bin').is_dir() else [])
        defsym = [f'-Wa,--defsym,{name}=1' for name in variant.split('+') if name != 'GOOD']
        subprocess.run([CROSS, *binutils, '-mcpu=cortex-a5', '-marm', '-nostdlib',
                        '-static', '-Wl,-Ttext=0x108000', '-Wl,--build-id=none',
                        *defsym, str(TOOLS / 'handover_payload.S'), '-o', str(elf)],
                       check=True)
        subprocess.run([OBJCOPY, '-O', 'binary',
                        str(elf), str(binary)], check=True)
        return binary

    def run_guard(self, variant, machine='sony-a6400', dma=False,
                  tlb=False, gic=False, l2=False, strict_kernel=False):
        binary = self.build_payload(variant)
        self._log_seq = getattr(self, '_log_seq', 0) + 1
        log = self.root / f'{self._log_seq:03d}_{variant}.log'
        plugin = f'{self.plugin},entry={NEW_ENTRY:#x},exit=1'
        if dma:
            plugin += f',dma={DMA_CONTRACTS}'
        if tlb:
            plugin += ',tlb=1'
        if gic:
            plugin += ',gic=1'
        if l2:
            plugin += ',l2=1'
        if strict_kernel:
            plugin += ',strict_kernel=1'
        proc = subprocess.run(
            [str(QEMU), '-M', machine, '-m', '1G', '-kernel', str(binary),
             '-display', 'none', '-serial', 'none', '-monitor', 'none', '-net', 'none',
             '-plugin', plugin, '-d', 'plugin', '-D', str(log)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        text = log.read_text(errors='replace') if log.exists() else ''
        return proc.returncode, text

    def check(self, variant, expected=None, **kwargs):
        code, text = self.run_guard(variant, **kwargs)
        if expected is None:
            expected = VARIANTS[variant]
        self.assertIn('entry 0x00600000 reached on cpu0', text, text[-2000:])
        failed = set(re.findall(r'handover-guard: (G\d+) FAIL', text))
        self.assertEqual(failed, expected, text[-3000:])
        self.assertEqual(code, 3 if expected else 0, text[-2000:])
        return text

    def test_good_mainline_sequence_passes(self):
        text = self.check('GOOD')
        self.assertIn('SCTLR=gdb sysreg', text)
        self.assertIn('DCCISW after C-off=1024', text)
        self.assertIn('RESULT PASS', text)

    def test_dma_contracts(self):
        dwc3 = 'sony-a6400,dwc3-gadget=on'
        cases = {
            # variant: (machine, expected failing items, expected G10 line fragment)
            'GOOD': ('sony-a6400', set(), 'G10 ok   dwc3-run: never programmed'),
            'DWC3': (dwc3, set(), 'G10 ok   dwc3-run: stopped, idle confirmed'),
            'DWC3+DWC3_KEEP_RUNNING': (dwc3, {'G10'}, 'G10 BAD  dwc3-run: STILL RUNNING'),
            'DWC3+DWC3_NO_POLL': (dwc3, {'G10'},
                                  'G10 BAD  dwc3-run: stop NOT confirmed by an idle status read'),
            'DWC3_SOFTRESET': (dwc3, set(),
                               'G10 ok   dwc3-run: never started (stores never set the run bits)'),
            'WORK_DMA': ('sony-a6400', {'G10'},
                         'G10 BAD  work-dma-chx: UNVERIFIED (no stop contract)'),
            'WDMA': ('sony-a6400', set(), 'G10 ok   work-dma-ch6: stopped, idle confirmed'),
            'WDMA+WDMA_NO_POLL': ('sony-a6400', {'G10'},
                                  'G10 BAD  work-dma-ch6: stop NOT confirmed'),
            'WDMA+WDMA_KEEP_ENABLED': ('sony-a6400', {'G10'},
                                       'G10 BAD  work-dma-en: STILL RUNNING'),
        }
        for variant, (machine, expected, fragment) in cases.items():
            with self.subTest(variant=variant):
                if 'dwc3-gadget=on' in machine and not self.has_dwc3_gadget:
                    self.skipTest('QEMU build predates dwc3-gadget machine property')
                text = self.check(variant, expected, machine=machine, dma=True)
                self.assertIn(fragment, text)
                self.assertIn('contracts from', text)

    def test_without_contract_file_g10_is_reported_unconfigured(self):
        text = self.check('GOOD')
        self.assertIn('G10 not configured', text)
        self.assertIn('DMA quiescence', text)

    def test_each_missing_step_is_caught(self):
        for variant in VARIANTS:
            if variant == 'GOOD':
                continue
            with self.subTest(variant=variant):
                self.check(variant)

    def test_xdmac0_dma_quiescence(self):
        cases = {
            'XDMAC0_COPY': (set(), 'G10 ok   xdmac0-ch0: stopped, idle confirmed'),
            'XDMAC0_COPY+XDMAC0_NO_POLL': (
                {'G10'}, 'G10 BAD  xdmac0-ch0: stop NOT confirmed by an idle status read'
            ),
            'XDMAC0_COPY+XDMAC0_KEEP_PENDING': (
                {'G10'}, 'G10 BAD  xdmac0-ch0: stop NOT confirmed by an idle status read'
            ),
        }
        for variant, (expected, fragment) in cases.items():
            with self.subTest(variant=variant):
                text = self.check(variant, expected, dma=True)
                self.assertIn(fragment, text)
                self.assertIn('G10 ok   xdmac1-ch0: never programmed', text)
                self.assertIn('G10 ok   ldec-lz77: never programmed', text)
                self.assertIn('G10 ok   f30a-fill: never programmed', text)
                self.assertIn('G10 ok   emmc0-sdhci: never programmed', text)
                self.assertIn('G10 ok   mmc0-sdhci: never programmed', text)

    def test_g11_tlb_invalidation(self):
        text = self.check('TEARDOWN_TLBI', set(), tlb=True)
        self.assertIn('G11 PASS', text)
        self.assertIn('not checked: outer L2 cache, DMA quiescence, GIC state\n', text)

        text_t32 = self.check('THUMB_TEARDOWN_TLBI', set(), tlb=True)
        self.assertIn('G11 PASS', text_t32)

        text_missing = self.check('GOOD', {'G11'}, tlb=True)
        self.assertIn('G11 FAIL', text_missing)

        text_after_dsb = self.check('TLBI_AFTER_DSB', {'G11'}, tlb=True)
        self.assertIn('G11 FAIL', text_after_dsb)

    def test_g12_gic_quiescence(self):
        text_never = self.check('GOOD', set(), gic=True)
        self.assertIn('G12 PASS GIC never enabled', text_never)
        self.assertIn('not checked: outer L2 cache, DMA quiescence, TLB\n', text_never)

        text_quiesced = self.check('GIC_ENABLE+GIC_DISABLE', set(), gic=True)
        self.assertIn('G12 PASS GIC quiesced', text_quiesced)

        for variant in (
            'GIC_ENABLE',
            'GIC_ENABLE+GIC_DISABLE+GIC_KEEP_GICC',
            'GIC_ENABLE+GIC_DISABLE+GIC_KEEP_GICD',
        ):
            with self.subTest(variant=variant):
                text_fail = self.check(variant, {'G12'}, gic=True)
                self.assertIn('G12 FAIL', text_fail)

    def test_g13_pl310_teardown(self):
        text_never = self.check('GOOD', set(), l2=True)
        self.assertIn('G13 PASS PL310 never enabled', text_never)
        self.assertIn('not checked: DMA quiescence, GIC state, TLB\n', text_never)

        text_ok = self.check('PL310_ENABLE+PL310_TEARDOWN', set(), l2=True)
        self.assertIn('G13 PASS PL310 outer cache cleaned, synced and disabled', text_ok)

        for variant in (
            'PL310_ENABLE',
            'PL310_ENABLE+PL310_TEARDOWN+PL310_NO_CLEAN',
            'PL310_ENABLE+PL310_TEARDOWN+PL310_NO_SYNC',
            'PL310_ENABLE+PL310_TEARDOWN+PL310_KEEP_ON',
        ):
            with self.subTest(variant=variant):
                text_fail = self.check(variant, {'G13'}, l2=True)
                self.assertIn('G13 FAIL', text_fail)

    def test_strict_kernel_combined(self):
        full_ok = (
            'TEARDOWN_TLBI+GIC_ENABLE+GIC_DISABLE+PL310_ENABLE+PL310_TEARDOWN+XDMAC0_COPY'
        )
        text = self.check(full_ok, set(), dma=True, strict_kernel=True)
        self.assertIn('G10 PASS', text)
        self.assertIn('G11 PASS', text)
        self.assertIn('G12 PASS', text)
        self.assertIn('G13 PASS', text)
        self.assertIn('not checked: DMA engines outside the contract file\n', text)

        text_no_dma = self.check(
            'TEARDOWN_TLBI+GIC_ENABLE+GIC_DISABLE+PL310_ENABLE+PL310_TEARDOWN',
            set(), strict_kernel=True,
        )
        self.assertIn('not checked: DMA quiescence\n', text_no_dma)

        self.check('GIC_ENABLE+PL310_ENABLE', {'G11', 'G12', 'G13'}, strict_kernel=True)
        self.check(
            'TEARDOWN_TLBI+GIC_ENABLE+GIC_DISABLE+PL310_ENABLE+PL310_TEARDOWN+PL310_NO_SYNC',
            {'G13'}, strict_kernel=True,
        )


if __name__ == '__main__':
    unittest.main()
