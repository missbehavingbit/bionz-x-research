"""Constructed offline controls for the stella isolation and guarded padding trial."""
import hashlib
import struct
import unittest
from types import SimpleNamespace
from unittest import mock

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM

from project_paths import PROJECT_ROOT
from tools.research import stella_allocator_probe as stella


def _library():
    ident = b'\x7fELF' + bytes([1, 1, 1]) + bytes(9)
    header = struct.pack('<HHIIIIIHHHHHH', 3, 40, 1, 0x79F001,
                         52, 0, 0x5000000, 52, 32, 1, 40, 0, 0)
    segment = struct.pack('<IIIIIIII', 1, 0x1000, 0x79F000, 0,
                          0x1000, 0x1000, 5, 0x1000)
    raw = bytearray((ident + header + segment).ljust(0x2000, b'\0'))
    raw[0x1E16:0x1E18] = bytes.fromhex('4c20')
    return bytes(raw)


class StellaPatchTests(unittest.TestCase):
    def setUp(self):
        self.raw = _library()
        self.sha256 = hashlib.sha256(self.raw).hexdigest()
        folder = PROJECT_ROOT / 'artifacts/tests/stella-allocator'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'constructed-libObj.so').write_bytes(self.raw)

    def _probe(self, base):
        mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.mem_map(base + 0x79F000, 4096, 5)
        mu.mem_write(base + 0x79F000, self.raw[0x1000:])
        mapping = {'path': '/usr/lib/libObj.so', 'address': base + 0x79F000,
                   'offset': 0x1000, 'length': 4096, 'prot': 5}
        return SimpleNamespace(
            mu=mu, mmap=SimpleNamespace(mappings=[mapping]),
            filesystem=SimpleNamespace(cache={'/usr/lib/libObj.so': self.raw}),
            report={})

    def test_patch_uses_verified_source_and_resolved_load_bias(self):
        for base in (0x10000000, 0x40000000):
            with self.subTest(base=base):
                probe = self._probe(base)
                before = bytes(probe.mu.mem_read(base + 0x79F000, 4096))
                self.assertEqual(probe.report, {})
                with mock.patch.object(stella, 'LIBOBJ_SHA256', self.sha256):
                    entry = stella.apply_logalloc_patch(probe)
                after = bytes(probe.mu.mem_read(base + 0x79F000, 4096))
                expected = bytearray(before)
                expected[0xE16:0xE18] = bytes.fromhex('5020')
                self.assertEqual(after, bytes(expected))
                self.assertEqual(entry['va'], base + stella.LOGALLOC_VA)
                self.assertEqual(entry['source_sha256'], self.sha256)
                self.assertEqual(probe.filesystem.cache['/usr/lib/libObj.so'], self.raw)
                self.assertEqual(probe.report['guest_patches'], [entry])
                self.assertFalse(probe.report['camera_fidelity_validated'])

    def test_patch_rejects_identity_preimage_and_mapping_mismatches_before_writing(self):
        for invalid in ('hash', 'source_preimage', 'loaded_preimage', 'unmapped',
                        'ambiguous_path', 'nonexecuting', 'mapping_too_short'):
            with self.subTest(invalid=invalid):
                base = 0x10000000
                probe = self._probe(base)
                expected_hash = self.sha256
                if invalid == 'hash':
                    expected_hash = '0' * 64
                elif invalid == 'source_preimage':
                    changed = bytearray(self.raw)
                    changed[0x1E16:0x1E18] = bytes.fromhex('5020')
                    probe.filesystem.cache['/usr/lib/libObj.so'] = bytes(changed)
                    expected_hash = hashlib.sha256(changed).hexdigest()
                elif invalid == 'loaded_preimage':
                    probe.mu.mem_write(base + stella.LOGALLOC_VA, bytes.fromhex('5020'))
                elif invalid == 'unmapped':
                    probe.mmap.mappings.clear()
                elif invalid == 'ambiguous_path':
                    probe.mmap.mappings.append(
                        {**probe.mmap.mappings[0], 'path': '/other/libObj.so'})
                elif invalid == 'nonexecuting':
                    probe.mmap.mappings[0]['prot'] = 3
                elif invalid == 'mapping_too_short':
                    probe.mmap.mappings[0]['length'] = 0xE17
                before = bytes(probe.mu.mem_read(base + 0x79F000, 4096))
                with mock.patch.object(stella, 'LIBOBJ_SHA256', expected_hash):
                    with self.assertRaises(ValueError):
                        stella.apply_logalloc_patch(probe)
                self.assertEqual(bytes(probe.mu.mem_read(base + 0x79F000, 4096)), before)
                self.assertEqual(probe.report, {})

    def test_patch_is_not_silently_reapplied(self):
        probe = self._probe(0x10000000)
        with mock.patch.object(stella, 'LIBOBJ_SHA256', self.sha256):
            stella.apply_logalloc_patch(probe)
            with self.assertRaisesRegex(ValueError, 'loaded preimage'):
                stella.apply_logalloc_patch(probe)
        self.assertEqual(len(probe.report['guest_patches']), 1)


class StellaFragmentTests(unittest.TestCase):
    def test_constructed_code_returns_only_with_supplied_reads_and_owned_writes(self):
        # ldr r0,[r1]; str r0,[r2]; bx lr.
        fragment = stella._Fragment(
            [(0x1000, bytes.fromhex('086810607047'))],
            [(stella.STATE, struct.pack('<I', 0x12345678)),
             (stella.STATE + 4, bytes(4))],
            [(stella.STATE + 4, stella.STATE + 8)])
        report = fragment.run(
            0x1000, {'r1': stella.STATE, 'r2': stella.STATE + 4})
        self.assertEqual(report['status'], 'returned')
        self.assertEqual(report['registers']['r0'], 0x12345678)
        self.assertEqual(bytes(fragment.mu.mem_read(stella.STATE + 4, 4)),
                         struct.pack('<I', 0x12345678))

    def test_unsupplied_read_is_not_zero_filled_success(self):
        fragment = stella._Fragment(
            [(0x1000, bytes.fromhex('08687047'))],
            [(stella.STATE, bytes(4))])
        report = fragment.run(0x1000, {'r1': stella.STATE + 4})
        self.assertEqual(report['status'], 'unsupplied_read')
        self.assertEqual(report['boundary']['address'], stella.STATE + 4)

    def test_write_outside_payload_stops_at_exact_store(self):
        fragment = stella._Fragment(
            [(0x1000, bytes.fromhex('08607047'))],
            [(stella.STATE, bytes(8))],
            [(stella.STATE + 4, stella.STATE + 8)])
        report = fragment.run(0x1000, {'r0': 1, 'r1': stella.STATE})
        self.assertEqual(report['status'], 'unsupported_write')
        self.assertEqual(report['boundary']['pc'], 0x1000)
        self.assertEqual(report['boundary']['address'], stella.STATE)

    def test_unmapped_memory_and_unprovided_code_stop_explicitly(self):
        fragment = stella._Fragment([(0x1000, bytes.fromhex('08687047'))])
        report = fragment.run(0x1000, {'r1': stella.STATE})
        self.assertEqual(report['status'], 'unmapped_memory')
        fragment = stella._Fragment([(0x1000, bytes.fromhex('00bf'))])
        report = fragment.run(0x1000, {})
        self.assertEqual(report['status'], 'unsupported_code')

    def test_instruction_limit_is_not_a_completed_contract(self):
        fragment = stella._Fragment([(0x1000, bytes.fromhex('fee7'))])
        report = fragment.run(0x1000, {}, limit=8)
        self.assertEqual(report['status'], 'instruction_limit')
        self.assertEqual(len(report['trace']), 8)

    @unittest.skipUnless(
        (PROJECT_ROOT / stella.LIBOBJ).exists() and (PROJECT_ROOT / stella.LIBC).exists(),
        'Firmware images not available')
    def test_firmware_isolation_binds_elf_vtables_and_mutex_nothrow_branch(self):
        libobj = (PROJECT_ROOT / stella.LIBOBJ).read_bytes()
        libc = (PROJECT_ROOT / stella.LIBC).read_bytes()
        stock = stella.isolate(libobj, libc, patch_logalloc=False)
        padded = stella.isolate(libobj, libc, patch_logalloc=True)
        self.assertEqual(stock['status'], 'isolation_complete')
        self.assertEqual(padded['status'], 'isolation_complete')
        self.assertTrue(all(stock['checks'].values()))
        self.assertTrue(all(padded['checks'].values()))
        self.assertEqual(stock['arithmetic']['requested'], 0x4C)
        self.assertEqual(padded['arithmetic']['requested'], 0x50)
        self.assertEqual(stock['mutex_allocation']['operator_new_request'], 0x24)
        self.assertEqual(stock['mutex_allocation']['return_offset'], 0x7AF492)
        self.assertEqual(stock['mutex_allocation']['constructor_call_return_offset'], 0x7AF49D)


if __name__ == '__main__':
    unittest.main()
