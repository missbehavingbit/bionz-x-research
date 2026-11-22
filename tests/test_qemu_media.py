"""Opt-in removable-media scenarios; no physical SD or USB devices are used."""

from binascii import crc_hqx
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import unittest

from project_paths import PROJECT_ROOT
from tests.test_qemu_emmc import NATIVE_AVAILABLE, QEMU, SDHCI as EMMC
from tools.research.qemu_boot_probe import Qmp, digest
from tools.research.qemu_media import sd_blockdev, sd_event, verify_photo, verify_playback


TEST_OUTPUT = PROJECT_ROOT / 'artifacts/tests'
MMC0 = 0xF0300000
SD_MODEREAD = 0xF2915010
SD_CD_N = 1 << 24
MS_CD_N = 1 << 16
SIO3 = 0xF2405600
CARD_BYTES = 16 * 1024**2


def submicro_crc(frame):
    return frame[:126] + crc_hqx(frame[:126], 0xffff).to_bytes(2, 'little')


def sio3_exchange(io, outgoing):
    io.write(SIO3, 0xc0000000)
    io.write(SIO3 + 0x0c, (len(outgoing) - 1) << 24)
    for offset, (word,) in enumerate(struct.iter_unpack('<I', outgoing)):
        io.write(SIO3 + 0x100 + 4 * offset, word)
    io.write(SIO3, 0x20000000)
    return b''.join(struct.pack('<I', io.read(SIO3 + 0x100 + offset))
                    for offset in range(0, len(outgoing), 4))


@contextmanager
def temporary_media():
    TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
        yield Path(directory)


def create_image(path, size, offset, data):
    with path.open('xb') as stream:
        stream.truncate(size)
        stream.seek(offset)
        stream.write(data)


class MediaInputTests(unittest.TestCase):
    def test_sd_only_accepts_a_separate_hash_checked_file(self):
        with temporary_media() as root:
            inputs = root / 'firmware'
            inputs.mkdir()
            original = inputs / 'emmc.img'
            card = root / 'sd, fixture.img'
            create_image(original, CARD_BYTES, 0, b'eMMC')
            create_image(card, CARD_BYTES, 0, b'SD')
            run = root / 'run'
            with self.assertRaisesRegex(ValueError, 'outside firmware'):
                sd_blockdev(original, digest(original), inputs, run)
            alias = root / 'emmc-alias.img'
            os.link(original, alias)
            with self.assertRaisesRegex(ValueError, 'non-hardlinked'):
                sd_blockdev(alias, digest(original), inputs, run)
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                sd_blockdev(card, '0' * 64, inputs, run)
            info = sd_blockdev(card, digest(card), inputs, run)
            self.assertEqual(info['file']['filename'], str(card.resolve()))

    def test_capture_requires_new_decodable_bytes_not_a_renamed_fixture(self):
        from PIL import Image, UnidentifiedImageError

        with temporary_media() as root:
            old = root / 'before/DCIM/100MSDCF'
            new = root / 'after/DCIM/100MSDCF'
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            fixture = old / 'DSC00001.JPG'
            Image.new('RGB', (32, 24), 'blue').save(fixture)
            (new / fixture.name).write_bytes(fixture.read_bytes())
            with self.assertRaisesRegex(ValueError, 'found 0'):
                verify_photo(root / 'before', root / 'after')
            saved = new / 'DSC00002.JPG'
            saved.write_bytes(fixture.read_bytes())
            with self.assertRaisesRegex(ValueError, 'unchanged baseline'):
                verify_photo(root / 'before', root / 'after')
            saved.write_bytes(b'Not a JPEG')
            with self.assertRaises(UnidentifiedImageError):
                verify_photo(root / 'before', root / 'after')
            Image.new('RGB', (96, 72), 'orange').save(saved)
            result = verify_photo(root / 'before', root / 'after')
            self.assertEqual((result['width'], result['height'], result['sha256']),
                             (96, 72, digest(saved)))

    def test_capture_restart_requires_same_file_and_bytes_in_separate_export(self):
        from PIL import Image

        with temporary_media() as root:
            before, after, reopened = (root / label for label in ('before', 'after', 'reopened'))
            for tree in (before, after, reopened):
                (tree / 'DCIM/100MSDCF').mkdir(parents=True)
            photo = Path('DCIM/100MSDCF/DSC00001.JPG')
            Image.new('RGB', (32, 24), 'blue').save(after / photo)
            with self.assertRaisesRegex(ValueError, 'separate'):
                verify_photo(before, after, reopened=after)
            with self.assertRaisesRegex(ValueError, 'has no DCIM/100MSDCF/DSC00001'):
                verify_photo(before, after, reopened=reopened)
            Image.new('RGB', (32, 24), 'orange').save(reopened / photo)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                verify_photo(before, after, reopened=reopened)
            (reopened / photo).write_bytes((after / photo).read_bytes())
            result = verify_photo(before, after, reopened=reopened)
            self.assertEqual(result['reopened_sha256'], result['sha256'])

    def test_playback_requires_the_seeded_image_not_an_osd_change(self):
        from PIL import Image

        with temporary_media() as root:
            fixture, empty, inserted = (root / name for name in ('card.jpg', 'empty.ppm', 'play.ppm'))
            reference = Image.new('RGB', (160, 120), 'blue')
            reference.paste('yellow', (80, 0, 160, 120))
            reference.save(fixture)
            display = Image.new('RGB', (180, 140), 'black')
            box = (10, 10, 170, 130)
            display.save(empty)
            display.paste('gray', (0, 0, 180, 8))
            display.save(inserted)
            with self.assertRaisesRegex(ValueError, 'does not show the fixture'):
                verify_playback(fixture, empty, inserted, box)
            with Image.open(fixture) as decoded:
                display.paste(decoded, box)
            display.save(inserted)
            result = verify_playback(fixture, empty, inserted, box)
            self.assertEqual(result['inserted_head_sha256'], digest(inserted))
            with self.assertRaisesRegex(ValueError, 'already matches'):
                verify_playback(fixture, inserted, inserted, box)


class Qtest:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(5)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile('rwb', buffering=0)

    def close(self):
        self.stream.close()
        self.socket.close()

    def command(self, command):
        self.socket.sendall((command + '\n').encode('ascii'))
        reply = self.stream.readline(8192).decode('ascii').strip()
        status, _, value = reply.partition(' ')
        if status != 'OK':
            raise RuntimeError(f'qtest {command[:100]}: {reply!r}')
        return value

    def read(self, address, width='l'):
        return int(self.command(f'read{width} {address:#x}'), 0)

    def write(self, address, value, width='l'):
        self.command(f'write{width} {address:#x} {value:#x}')

    def read_memory(self, address, length):
        return bytes.fromhex(self.command(f'read {address:#x} {length:#x}')[2:])

    def write_memory(self, address, data):
        self.command(f'write {address:#x} {len(data):#x} 0x{data.hex()}')

    def step(self, ns):
        self.command(f'clock_step {ns}')


@contextmanager
def qtest_machine(root, machine, *args):
    log_path = root / f'{machine.split(",", 1)[0]}.log'
    with tempfile.TemporaryDirectory(prefix='a6400-media-') as runtime, \
            log_path.open('wb') as log:
        qtest_path = Path(runtime) / 'qtest'
        qmp_path = Path(runtime) / 'qmp'
        process = subprocess.Popen(
            [str(QEMU), '-M', machine, '-accel', 'qtest', '-S', '-display', 'none',
             '-monitor', 'none', '-serial', 'none', '-net', 'none',
             '-qtest', f'unix:{qtest_path},server=on,wait=off', '-qtest-log', '/dev/null',
             '-qmp', f'unix:{qmp_path},server=on,wait=off', *args],
            stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while not (qmp_path.exists() and qtest_path.exists()):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(log_path.read_text(errors='replace'))
                time.sleep(0.01)
            qmp = Qmp(qmp_path, time.monotonic() + 30)
            try:
                # The qtest accelerator executes no CPUs, but timers need RUNNING.
                qmp.request('cont')
                io = Qtest(qtest_path)
                try:
                    yield io, qmp
                    qmp.request('quit')
                    if process.wait(timeout=5) != 0:
                        raise RuntimeError(log_path.read_text(errors='replace'))
                finally:
                    io.close()
            finally:
                qmp.close()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


class SdHost:
    def __init__(self, io, base):
        self.io, self.base = io, base

    def read(self, offset, width='l'):
        return self.io.read(self.base + offset, width)

    def write(self, offset, value, width='l'):
        self.io.write(self.base + offset, value, width)

    def enable(self):
        self.write(0x29, 0x0f, 'b')
        self.write(0x2c, 7, 'w')
        self.write(0x34, 0xffffffff)

    def command(self, index, argument, flags, transfer=0):
        self.write(0x30, 0xffffffff)
        self.write(0x04, (1 << 16) | 512 if transfer else 0)
        self.write(0x08, argument)
        self.write(0x0c, ((index << 8) | flags) << 16 | transfer)
        for _ in range(20):
            status = self.read(0x30)
            if status & 0x8000:
                raise RuntimeError(f'SDHCI {self.base:#x} CMD{index} failed: {status:#x}')
            if status & 1:
                return self.read(0x10)
            self.io.step(100_000)
        raise TimeoutError(f'SDHCI {self.base:#x} CMD{index} did not complete')

    def select_sd(self):
        self.command(0, 0, 0)
        if self.command(8, 0x1aa, 0x1a) != 0x1aa:
            raise RuntimeError('SD CMD8 did not echo the host voltage check')
        for _ in range(10):
            self.command(55, 0, 0x1a)
            if self.command(41, 0x00ff8000, 2) & 0x80000000:
                break
            self.io.step(1_000_000)
        else:
            raise TimeoutError('SD ACMD41 did not power up')
        self.command(2, 0, 9)
        rca = self.command(3, 0, 0x1a) & 0xffff0000
        self.command(7, rca, 0x1b)

    def select_sector_addressed_emmc(self):
        self.command(0, 0, 0)
        if self.command(1, 0x40000080, 2) & 0xc0000000 != 0xc0000000:
            raise RuntimeError('eMMC did not accept sector addressing')
        self.command(2, 0, 9)
        self.command(3, 0x10000, 0x1a)
        self.command(7, 0x10000, 0x1b)

    def read_sector(self, address):
        self.command(17, address, 0x3a, 0x12)
        return struct.pack('<128I', *(self.read(0x20) for _ in range(128)))

    def write_sector(self, byte_address, data):
        descriptor, buffer = 0x00100000, 0x00101000
        self.io.write_memory(buffer, data)
        self.io.write_memory(descriptor, struct.pack('<HHI', 0x23, len(data), buffer))
        self.write(0x28, 0x10, 'b')  # 32-bit ADMA2
        self.write(0x58, descriptor)
        self.command(24, byte_address, 0x3a, 3)
        if not self.read(0x30) & 2:
            raise RuntimeError('SD ADMA2 write did not raise transfer completion')


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuRemovableSdTests(unittest.TestCase):
    def test_submicro_slot_a_reports_the_same_hotplug_as_moderead(self):
        with temporary_media() as root:
            card, fixture = root / 'sd.img', root / 'micro.bin'
            create_image(card, CARD_BYTES, 0, bytes(512))
            frames = b''.join(submicro_crc(bytes([kind]) + bytes(127))
                              for kind in (0x20, 0x60, 0x30))
            fixture.write_bytes(frames)
            node = sd_blockdev(card, digest(card), root / 'inputs', root / 'run')
            with qtest_machine(root, f'sony-a6400,submicro-frames={fixture}',
                               '-drive', 'if=none,id=a6400-sd',
                               '-blockdev', json.dumps(node)) as (io, qmp):
                for event, present in ((None, False), ('insert', True), ('eject', False)):
                    if event:
                        sd_event(qmp, event)
                    packet = sio3_exchange(io, b'\x21' + bytes(127))
                    self.assertEqual(packet[0], 0x20)
                    self.assertEqual(packet[2] & 0x10, 0x10 if present else 0)
                    self.assertEqual(packet, submicro_crc(packet))
                    self.assertEqual(io.read(SD_MODEREAD) & (MS_CD_N | SD_CD_N),
                                     MS_CD_N if present else MS_CD_N | SD_CD_N)
                self.assertEqual(fixture.read_bytes(), frames)

    def test_insert_eject_and_persist_without_writing_emmc(self):
        with temporary_media() as root:
            card, emmc = root / 'sd.img', root / 'emmc.img'
            before, after = b'S' * 512, b'W' * 512
            create_image(card, CARD_BYTES, 512, before)
            create_image(emmc, CARD_BYTES, 8 * 1024**2 + 512, b'E' * 512)
            emmc_hash = digest(emmc)
            for restart in (False, True):
                node = sd_blockdev(card, digest(card), root / 'inputs', root / 'run')
                with qtest_machine(root, 'sony-a6400', '-drive', f'if=sd,format=raw,file={emmc}',
                                   '-drive', 'if=none,id=a6400-sd', '-blockdev', json.dumps(node)) as (io, qmp):
                    sd, internal = SdHost(io, MMC0), SdHost(io, EMMC)
                    sd.enable()
                    self.assertFalse(sd.read(0x24) & (1 << 16))
                    self.assertTrue(io.read(SD_MODEREAD) & SD_CD_N)
                    self.assertTrue(internal.read(0x24) & (1 << 16))
                    sd_event(qmp, 'insert')
                    io.step(1_000_000)
                    self.assertTrue(sd.read(0x24) & (1 << 16))
                    self.assertFalse(io.read(SD_MODEREAD) & SD_CD_N)
                    self.assertTrue(sd.read(0x30) & (1 << 6))  # insert status
                    sd.select_sd()
                    self.assertEqual(sd.read_sector(512), after if restart else before)
                    if not restart:
                        sd.write_sector(512, after)
                        self.assertEqual(sd.read_sector(512), after)
                    internal.enable()
                    internal.select_sector_addressed_emmc()
                    self.assertEqual(internal.read_sector(1), b'E' * 512)
                    sd_event(qmp, 'eject')
                    self.assertFalse(sd.read(0x24) & (1 << 16))
                    self.assertTrue(io.read(SD_MODEREAD) & SD_CD_N)
                    self.assertTrue(sd.read(0x30) & (1 << 7))  # remove status
                    self.assertTrue(internal.read(0x24) & (1 << 16))
                self.assertEqual(digest(emmc), emmc_hash)
            with card.open('rb') as image:
                image.seek(512)
                self.assertEqual(image.read(512), after)


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux sony-a6400 QEMU build absent')
class QemuSubmicroLensTests(unittest.TestCase):
    def test_slot_b_rail_reply_leaves_other_bytes_and_invalid_requests_alone(self):
        def packet(kind, tlvs):
            return submicro_crc(struct.pack('<BBH', kind, 0, len(tlvs)) + tlvs
                                + bytes(124 - len(tlvs)))

        a = packet(0x20, b'')
        # A leading TLV makes both the request and status offsets non-fixed.
        version = b'\x00\x04\x00\x00\x01\x02\x01\x38'
        b_on = packet(0x30, version + b'\x03\x04\x00\x00\x00\x00\x00\x03')
        b_off = packet(0x30, version + b'\x03\x04\x00\x00\x00\x00\x00\x02')
        tx_prefix = version + b'\x0a\x02\x00\x00\x00'
        tx_on = packet(0x31, tx_prefix + b'\x21')
        tx_off = packet(0x31, tx_prefix + b'\x20')
        short = packet(0x31, version + b'\x0a\x01\x00\x00\x00')
        overrun = packet(0x31, version + b'\x0a\x03\x00\x00\x00\x20')
        long_header = submicro_crc(tx_off[:2] + b'\x7b\x00' + tx_off[4:])

        with temporary_media() as root:
            fixture = root / 'micro.bin'
            fixture.write_bytes(a + packet(0x60, b'') + b_on)
            machine = f'sony-a6400,submicro-frames={fixture}'
            with qtest_machine(root, machine) as (io, _):
                self.assertEqual(sio3_exchange(io, tx_off), b_on)
            with qtest_machine(root, machine + ',submicro-lens-power=on') as (io, _):
                self.assertEqual(sio3_exchange(io, tx_off), b_off)
                self.assertEqual(sio3_exchange(io, tx_on), b_on)
                for invalid in (packet(0x31, version), short, overrun, long_header):
                    with self.subTest(outgoing=invalid[:18].hex()):
                        self.assertEqual(sio3_exchange(io, invalid), b_on)
                self.assertEqual(sio3_exchange(io, tx_off + tx_on), b_off + b_on)
                self.assertEqual(sio3_exchange(io, b'\x21' + bytes(127) + tx_off),
                                 a + b_off)


class OhciHost:
    """Bounded OHCI guest-DMA host for the explicit non-Sony USB fixture."""

    base = 0x01c1a400  # Orange Pi PC USB0 OHCI
    hcca, ed, td, data = 0x40100000, 0x40101000, 0x40101100, 0x40102000

    def __init__(self, io):
        self.io = io
        self.address = 0
        self.bulk_carry = {}
        self.io.write_memory(self.hcca, bytes(256))
        self.reg_write(0x18, self.hcca)

    def reg_read(self, offset):
        return self.io.read(self.base + offset)

    def reg_write(self, offset, value):
        self.io.write(self.base + offset, value)

    def transfer(self, endpoint, max_packet, stages, *, bulk=False):
        end = self.td + len(stages) * 16
        addresses = []
        for index, (pid, toggle, payload) in enumerate(stages):
            td = self.td + index * 16
            address = self.data + index * 0x200
            length = payload if isinstance(payload, int) else len(payload)
            if length:
                self.io.write_memory(address, bytes(length) if isinstance(payload, int) else payload)
            addresses.append((address, length))
            self.io.write_memory(td, struct.pack('<4I', (15 << 28) | (toggle << 24) | (pid << 19),
                                                 address if length else 0, td + 16,
                                                 address + length - 1 if length else 0))
        flags = self.address | (endpoint << 7) | (max_packet << 16)
        carry = self.bulk_carry.get(endpoint, 0) if bulk else 0
        self.io.write_memory(self.ed, struct.pack('<4I', flags, end, self.td | carry, 0))
        self.reg_write(0x28 if bulk else 0x20, self.ed)
        self.reg_write(0x04, (2 << 6) | (1 << (5 if bulk else 4)))
        self.reg_write(0x08, 1 << (2 if bulk else 1))
        for _ in range(200):
            self.io.step(1_000_000)
            head = self.io.read(self.ed + 8)
            if (head & ~0xf) == end or head & 1:
                break
        else:
            raise TimeoutError(f'OHCI endpoint {endpoint} did not complete')
        completions = [self.io.read(self.td + 16 * i) >> 28 for i in range(len(stages))]
        if head & 1 or any(completions):
            raise RuntimeError(f'OHCI endpoint {endpoint} completion codes: {completions}')
        if bulk:
            self.bulk_carry[endpoint] = head & 2
        self.reg_write(0x0c, 1 << 1)  # acknowledge the HCCA done writeback
        self.io.step(1_000_000)
        return [self.io.read_memory(address, length) if length else b''
                for address, length in addresses]

    def control(self, request_type, request, value, length, max_packet=64):
        stages = [(0, 2, struct.pack('<BBHHH', request_type, request, value, 0, length))]
        if length:
            stages.append((2, 3, length))
        stages.append((1 if length else 2, 3, 0))
        return self.transfer(0, max_packet, stages)[1]

    def scsi(self, tag, cdb, length, endpoints):
        ep_in, in_packet, ep_out, out_packet = endpoints
        cbw = struct.pack('<IIIBBB16s', 0x43425355, tag, length,
                          0x80 if length else 0, 0, len(cdb), cdb)
        self.transfer(ep_out, out_packet, [(1, 0, cbw)], bulk=True)
        data = b''
        if length:
            data, = self.transfer(ep_in, in_packet, [(2, 0, length)], bulk=True)
        csw, = self.transfer(ep_in, in_packet, [(2, 0, 13)], bulk=True)
        signature, reply_tag, residue, status = struct.unpack('<IIIB', csw)
        if signature != 0x53425355 or reply_tag != tag or residue or status > 1:
            raise RuntimeError(f'Mass Storage command {cdb[0]:#x} returned CSW {csw.hex()}')
        return data, status


@unittest.skipUnless(NATIVE_AVAILABLE, 'Optional WSL/Linux QEMU build absent')
class QemuUsbMassStorageTests(unittest.TestCase):
    def test_virtual_cable_enum_and_read_on_usb_only_fixture(self):
        with temporary_media() as root:
            image = root / 'usb.img'
            marker = bytes(range(256)) + bytes(range(255, -1, -1))
            create_image(image, CARD_BYTES, 512, marker)
            before = digest(image)
            with qtest_machine(root, 'orangepi-pc', '-drive',
                               f'if=none,id=msc,format=raw,readonly=on,file={image}') as (io, qmp):
                host = OhciHost(io)
                self.assertFalse(host.reg_read(0x54) & 1)
                qmp.request('device_add', {'driver': 'usb-storage', 'id': 'msc-fixture',
                                           'bus': 'usb-bus.4', 'port': '1', 'drive': 'msc'})
                self.assertTrue(host.reg_read(0x54) & 1)
                host.reg_write(0x54, 1 << 4)
                self.assertTrue(host.reg_read(0x54) & 2)

                max_packet = host.control(0x80, 6, 0x0100, 8, 8)[7]
                self.assertIn(max_packet, (8, 16, 32, 64))
                device = host.control(0x80, 6, 0x0100, 18, max_packet)
                self.assertEqual(device[:2], b'\x12\x01')
                self.assertEqual(struct.unpack_from('<HH', device, 8), (0x46f4, 1))
                host.control(0, 5, 5, 0, max_packet)
                host.address = 5
                header = host.control(0x80, 6, 0x0200, 9, max_packet)
                length = struct.unpack_from('<H', header, 2)[0]
                self.assertLessEqual(length, 256)
                config = host.control(0x80, 6, 0x0200, length, max_packet)
                self.assertEqual(config[14:17], b'\x08\x06\x50')  # MSC, SCSI, BOT
                host.control(0, 9, config[5], 0, max_packet)
                self.assertEqual(host.control(0xa1, 0xfe, 0, 1, max_packet), b'\0')
                endpoints = {}
                offset = 0
                while offset < len(config):
                    size, kind = config[offset:offset + 2]
                    self.assertGreaterEqual(size, 2)
                    self.assertLessEqual(offset + size, len(config))
                    if kind == 5:
                        address, attrs, packet = struct.unpack_from('<BBH', config, offset + 2)
                        self.assertEqual(attrs, 2)
                        endpoints[bool(address & 0x80)] = (address & 0xf, packet)
                    offset += size
                pipes = (*endpoints[True], *endpoints[False])
                tag = 1
                # Reset/hotplug can queue Unit Attention before any media IO.
                for _ in range(3):
                    _, status = host.scsi(tag, bytes(6), 0, pipes)  # TEST UNIT READY
                    tag += 1
                    if status == 0:
                        break
                    sense, status = host.scsi(tag, b'\x03\0\0\0\x12\0', 18, pipes)
                    self.assertEqual(status, 0)
                    self.assertEqual(sense[2] & 0xf, 6)  # Unit Attention
                    tag += 1
                else:
                    self.fail('Mass Storage LUN did not become ready')
                capacity, status = host.scsi(tag, b'\x25' + bytes(9), 8, pipes)
                self.assertEqual(status, 0)
                self.assertEqual(struct.unpack('>II', capacity), (CARD_BYTES // 512 - 1, 512))
                read10 = struct.pack('>BBIBHB', 0x28, 0, 1, 0, 1, 0)
                self.assertEqual(host.scsi(tag + 1, read10, 512, pipes), (marker, 0))
                qmp.request('device_del', {'id': 'msc-fixture'})
                self.assertFalse(host.reg_read(0x54) & 1)
            self.assertEqual(digest(image), before)
