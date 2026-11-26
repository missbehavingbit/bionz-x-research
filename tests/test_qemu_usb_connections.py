"""Unit and optional native QEMU TCG tests for Sony CXD90045 USB connection modes.

Group A runs on both Windows and Linux with deterministic scripted channels (no
external binary or USB device). Group B compiles ``usb_gadget_modes_fw.c`` and
exercises ``cxd90045-dwc3`` end-to-end on the ``sony-a6400`` QEMU machine when
both the patched QEMU binary and ``arm-linux-gnueabi-gcc`` are available.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from project_paths import PROJECT_ROOT
from tools.research.qemu_boot_probe import Qmp
from tools.research.qemu_ep0_host import Ep0Host
from tools.research.qemu_usb_connections import (
    BUS_POWER_HID_PID,
    CBW_SIGNATURE,
    CBW_STRUCT,
    COMPOSITE_UVC_PTP_PID,
    CSW_SIGNATURE,
    CSW_STRUCT,
    JIRITSU_SERVICE_PID,
    MscBotClient,
    MscCommandFailedError,
    MscPhaseError,
    MscStallError,
    PTP_CONTAINER_DATA,
    PTP_CONTAINER_EVENT,
    PTP_CONTAINER_RESPONSE,
    PTP_HEADER,
    PcRemotePtpClient,
    PtpResponseError,
    CompositeUvcPtpClient,
    BusPowerHidClient,
    JiritsuReplyError,
    JiritsuServiceClient,
    SENSER_HEADER,
    usb_ep_addr_to_dwc3_idx,
)
from tools.research.toolchain import toolchain_config

VERSION = toolchain_config()['qemu']['version']
_ENV_QEMU = os.environ.get('BIONZX_QEMU_TEST_BINARY')
QEMU = (
    Path(_ENV_QEMU)
    if _ENV_QEMU and Path(_ENV_QEMU).is_file()
    else Path(
        os.environ.get('A6400_QEMU_DIR', str(Path.home() / f'sony-a6400-qemu-{VERSION}'))
    ) / 'build/qemu-system-arm'
)
_FALLBACK_CROSS = Path(
    '/home/administrator/a6400-rea-tools/arm-gcc-15.2.0-prepush/bin/arm-linux-gnueabi-gcc'
)
CROSS = shutil.which('arm-linux-gnueabi-gcc') or (
    str(_FALLBACK_CROSS) if os.name == 'posix' and _FALLBACK_CROSS.is_file() else None
)


def _resolve_objcopy(cross: str | None) -> str | None:
    found = shutil.which('arm-linux-gnueabi-objcopy')
    if found:
        return found
    if cross:
        sibling = Path(cross).with_name(Path(cross).name.replace('gcc', 'objcopy'))
        if sibling.is_file():
            return str(sibling)
    return shutil.which('objcopy')


OBJCOPY = _resolve_objcopy(CROSS)
FW_SOURCE = PROJECT_ROOT / 'qemu-a6400/tools/usb_gadget_modes_fw.c'
NATIVE_QEMU_AND_CROSS_AVAILABLE = bool(
    os.name == 'posix' and QEMU.is_file() and CROSS and OBJCOPY and FW_SOURCE.is_file()
)

# Descriptor fixtures matching E0084, E0152, E0154, E0258, E0347, E0598, E0643
MSC_DEVICE_HEX = '12010002000000404c05a80c000101020301'
MSC_7RM4A_DEVICE_HEX = '12010002000000404c059d0d000101020301'
MSC_CONFIG_HEX = (
    '09022000010100c0fa'
    '090400000208065000'
    '07058102000200'
    '07050202000200'
)

PTP_DEVICE_HEX = '12010002000000404c05aa0c000101020301'
PTP_CONFIG_HEX = (
    '0902270001010080fa'
    '090400000306010100'
    '07058102000200'
    '07050202000200'
    '07058303200007'
)

COMP_DEVICE_HEX = '12010002ef0201404c052203000101020301'
COMP_CONFIG_HEX = (
    '0902ad0003010080fa'
    '090400000306010100'
    '07058102000200'
    '07050202000200'
    '07058303200007'
    '080b01020e030000'
    '09040100000e010000'
    '0d240100012800006cdc020102'
    '122402010102000000000000000003000000'
    '092403020101000100'
    '09040200010e020000'
    '0e2401013d008400020000000100'
    '0b24060101010100000000'
    '1e240701000005d00200'
    '005e1a00005e1a0000e100151605000115160500'
    '06240d010104'
    '07058402000200'
)

HID_DEVICE_HEX = '12010002000000404c056f09000101020301'
HID_CONFIG_HEX = (
    '0902220001010080fa'
    '090400000103000000'
    '092111010001221300'
    '07058103200001'
)
HID_REPORT_DESC = bytes.fromhex(
    '05010900a10109011500257f750895088102c0'
)

JIRITSU_DEVICE_HEX = '12010002000000404c053603000101020301'
JIRITSU_CONFIG_HEX = (
    '09022000010100c0fa'
    '0904000002ff000000'
    '07058102000200'
    '07050202000200'
)


class _ScriptedChannel:
    def __init__(self, replies: list[str] | None = None):
        self.replies = [r.encode('ascii') + b'\n' for r in (replies or [])]
        self.sent: list[str] = []

    def sendall(self, data: bytes) -> None:
        for line in data.decode('ascii').splitlines():
            if line:
                self.sent.append(line)

    def recv(self, _size: int) -> bytes:
        return self.replies.pop(0) if self.replies else b''


def _enum_dict(dev_hex: str, cfg_hex: str) -> dict:
    return {
        'device_hex': dev_hex,
        'configuration_hex': cfg_hex,
        'address': 1,
        'configuration_value': 1,
        'get_configuration_hex': '01',
        'control_sequence_completed': True,
        'uvc_streaming_verified': False,
    }


def _enum_replies(dev_hex: str, cfg_hex: str) -> list[str]:
    return [
        'ACK 0 8', 'IN 1 ' + dev_hex, 'ACK 0 0',
        'ACK 0 8', 'IN 1 ',
        'ACK 0 8', 'IN 1 ' + cfg_hex[:18], 'ACK 0 0',
        'ACK 0 8', 'IN 1 ' + cfg_hex, 'ACK 0 0',
        'ACK 0 8', 'IN 1 ',
        'ACK 0 8', 'IN 1 01', 'ACK 0 0',
    ]


def _make_synthetic_jpeg(width: int = 1280, height: int = 720, body_bytes: int = 1060) -> bytes:
    sof0 = b'\xff\xc0\x00\x0b\x08' + struct.pack('>HH', height, width) + b'\x01\x01\x11\x00'
    sos = b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00'
    scan = bytes(0x21 + (i & 0x3F) for i in range(body_bytes))
    return b'\xff\xd8' + sof0 + sos + scan + b'\xff\xd9'


def _ptp_str(text: str) -> bytes:
    if not text:
        return b'\x00'
    return bytes([len(text) + 1]) + text.encode('utf-16le') + b'\x00\x00'


def _ptp_device_info_payload() -> bytes:
    ops = (0x1001, 0x1002, 0x1003, 0x1004, 0x1009, 0x9201, 0x9202, 0x9205)
    evs = (0x4002, 0xC201)
    props = (0xD25A, 0xD2C2)
    fmts = (0x3801,)

    def u16_arr(vals):
        return struct.pack('<I', len(vals)) + b''.join(struct.pack('<H', v) for v in vals)

    return (
        struct.pack('<HIH', 100, 0x11, 100)
        + _ptp_str('Sony PTP Extensions')
        + struct.pack('<H', 0)
        + u16_arr(ops)
        + u16_arr(evs)
        + u16_arr(props)
        + u16_arr(fmts)
        + u16_arr(fmts)
        + _ptp_str('Sony Corporation')
        + _ptp_str('ILCE-6400')
        + _ptp_str('2.00')
        + _ptp_str('')
    )


def _ptp_pkt(kind: int, code: int, tx: int, payload: bytes = b'') -> bytes:
    return PTP_HEADER.pack(12 + len(payload), kind, code, tx) + payload


def _csw_hex(tag: int, residue: int = 0, status: int = 0) -> str:
    return CSW_STRUCT.pack(CSW_SIGNATURE, tag, residue, status).hex()


# ===========================================================================
# GROUP A: Deterministic Scripted Unit Tests (Always run on Windows & Linux)
# ===========================================================================


class Ep0HostExtensionsTests(unittest.TestCase):
    def test_connection_lifecycle_commands_emit_no_extra_tokens(self):
        ch = _ScriptedChannel()
        host = Ep0Host(ch)
        host.connect()
        host.bus_reset()
        host.disconnect()
        self.assertEqual(ch.sent, ['CONN', 'RESET', 'DISC'])
        self.assertEqual(
            host.events,
            [{'tx': 'CONN'}, {'tx': 'RESET'}, {'tx': 'DISC'}],
        )

    def test_usb_endpoint_to_dwc3_logical_index_mapping(self):
        self.assertEqual(usb_ep_addr_to_dwc3_idx(0x81), 3)
        self.assertEqual(usb_ep_addr_to_dwc3_idx(0x02), 4)
        self.assertEqual(usb_ep_addr_to_dwc3_idx(0x83), 7)
        self.assertEqual(usb_ep_addr_to_dwc3_idx(0x84), 9)
        for bad in (0x00, 0x80, 0x11, 0x91, -1):
            with self.assertRaises(ValueError):
                usb_ep_addr_to_dwc3_idx(bad)

    def test_bulk_out_zlp_and_multi_packet_transfer(self):
        payload = bytes(i & 0xFF for i in range(1024))
        ch = _ScriptedChannel(['ACK 4 512', 'ACK 4 512', 'ACK 4 0'])
        host = Ep0Host(ch)
        sent = host.bulk_out_transfer(4, payload, max_packet=512, send_zlp=True)
        self.assertEqual(sent, 1024)
        self.assertEqual(len(ch.sent), 3)
        self.assertTrue(ch.sent[0].startswith('OUT 4 '))
        self.assertTrue(ch.sent[1].startswith('OUT 4 '))
        self.assertEqual(ch.sent[2], 'OUT 4 -')

    def test_bulk_out_transfer_short_tail_skips_zlp(self):
        payload = b'A' * 600
        ch = _ScriptedChannel(['ACK 4 512', 'ACK 4 88'])
        host = Ep0Host(ch)
        self.assertEqual(host.bulk_out_transfer(4, payload, max_packet=512, send_zlp=True), 600)
        self.assertEqual(len(ch.sent), 2)

    def test_bulk_out_validation_boundaries(self):
        host = Ep0Host(_ScriptedChannel())
        with self.assertRaises(ValueError):
            host.bulk_out_zlp(3)  # odd IN endpoint
        with self.assertRaises(ValueError):
            host.bulk_out_zlp(0)  # EP0
        with self.assertRaises(ValueError):
            host.bulk_out_transfer(4, b'', send_zlp=False)
        with self.assertRaises(ValueError):
            host.bulk_out_transfer(4, b'x', max_packet=2048)

    def test_interrupt_in_clear_halt_and_set_interface(self):
        ch = _ScriptedChannel([
            'IN 7 10000000040002400100000001c0ffff',
            'ACK 0 8', 'IN 1 ',
            'ACK 0 8', 'IN 1 ',
        ])
        host = Ep0Host(ch)
        ev = host.interrupt_in(7, retries=10)
        self.assertEqual(len(ev), 16)
        host.clear_halt(0x81)
        host.set_interface(2, 1)
        self.assertEqual(
            ch.sent,
            [
                'IN 7',
                'SETUP 0201000081000000', 'IN 1',
                'SETUP 010b010002000000', 'IN 1',
            ],
        )
        for bad_ep in (0x00, 0x80, 0x31):
            with self.assertRaises(ValueError):
                host.clear_halt(bad_ep)
        with self.assertRaises(ValueError):
            host.set_interface(256, 0)


class MscBotClientUnitTests(unittest.TestCase):
    def test_descriptor_validation_accepts_a6400_and_7rm4a_and_rejects_non_msc(self):
        for dev_hex in (MSC_DEVICE_HEX, MSC_7RM4A_DEVICE_HEX):
            client = MscBotClient(Ep0Host(_ScriptedChannel()), _enum_dict(dev_hex, MSC_CONFIG_HEX))
            self.assertEqual((client.ep_in, client.ep_out), (3, 4))
        with self.assertRaisesRegex(ValueError, 'VID:PID'):
            MscBotClient(Ep0Host(_ScriptedChannel()), _enum_dict(HID_DEVICE_HEX, MSC_CONFIG_HEX))
        with self.assertRaisesRegex(ValueError, '08/06/50'):
            MscBotClient(Ep0Host(_ScriptedChannel()), _enum_dict(MSC_DEVICE_HEX, PTP_CONFIG_HEX))

    def test_full_scsi_bot_commands_and_multi_sector_transfers(self):
        inq = (
            b'\x00\x80\x04\x02\x1f\x00\x00\x00'
            + b'Sony    '
            + b'DSC             '
            + b'1.00'
        )
        cap = struct.pack('>II', 63, 512)
        ms6 = b'\x03\x00\x00\x00'
        read_payload = bytes((i * 7) & 0xFF for i in range(2048))
        write_payload = bytes((255 - (i & 0xFF)) for i in range(2048))

        ext_body = struct.pack('<II', 0, 0) + bytes([9]) + b'ILCE-6400' + bytes.fromhex('0032858730') + bytes(4)
        ext_reply = (struct.pack('<IHH8x', len(ext_body), 1, 1) + ext_body).ljust(512, b'\x00')

        replies = [
            *_enum_replies(MSC_DEVICE_HEX, MSC_CONFIG_HEX),
            # get_max_lun
            'ACK 0 8', 'IN 1 00', 'ACK 0 0',
            # test_unit_ready (tag 1)
            'ACK 4 31', 'IN 3 ' + _csw_hex(1, 0, 0),
            # inquiry (tag 2)
            'ACK 4 31', 'IN 3 ' + inq.hex(), 'IN 3 ' + _csw_hex(2, 0, 0),
            # read_capacity_10 (tag 3)
            'ACK 4 31', 'IN 3 ' + cap.hex(), 'IN 3 ' + _csw_hex(3, 0, 0),
            # mode_sense_6 (tag 4)
            'ACK 4 31', 'IN 3 ' + ms6.hex(), 'IN 3 ' + _csw_hex(4, 0, 0),
            # read_10 4 sectors = 2048 B (tag 5)
            'ACK 4 31',
            'IN 3 ' + read_payload[0:512].hex(),
            'IN 3 ' + read_payload[512:1024].hex(),
            'IN 3 ' + read_payload[1024:1536].hex(),
            'IN 3 ' + read_payload[1536:2048].hex(),
            'IN 3 ' + _csw_hex(5, 0, 0),
            # write_10 4 sectors = 2048 B (tag 6)
            'ACK 4 31',
            'ACK 4 512', 'ACK 4 512', 'ACK 4 512', 'ACK 4 512',
            'IN 3 ' + _csw_hex(6, 0, 0),
            # sony_ext_model_info (tag 7)
            'ACK 4 31', 'IN 3 ' + ext_reply.hex(), 'IN 3 ' + _csw_hex(7, 0, 0),
        ]
        ch = _ScriptedChannel(replies)
        client = MscBotClient(Ep0Host(ch))
        client.enumerate()
        self.assertEqual(client.get_max_lun(), 0)
        self.assertTrue(client.test_unit_ready())
        info = client.inquiry()
        self.assertEqual((info['vendor'], info['product'], info['revision']), ('Sony', 'DSC', '1.00'))
        cap_res = client.read_capacity_10()
        self.assertEqual((cap_res['block_count'], cap_res['block_size']), (64, 512))
        self.assertFalse(client.mode_sense_6()['write_protected'])
        self.assertEqual(client.read_10(2, 4), read_payload)
        wr = client.write_10(6, write_payload)
        self.assertEqual((wr['status'], wr['bytes_written']), (0, 2048))
        ext = client.sony_ext_model_info()
        self.assertEqual(ext['model'], 'ILCE-6400')
        self.assertEqual(ext['product_code_wire_hex'], '0032858730')
        self.assertEqual(ch.replies, [])

    def test_write_protect_sense_and_stall_recovery(self):
        sense_wp = bytearray(18)
        sense_wp[0] = 0x70
        sense_wp[2] = 0x07  # DATA PROTECT
        sense_wp[7] = 10
        sense_wp[12] = 0x27  # WRITE PROTECTED

        replies = [
            # mode_sense_6 with WP bit 0x80 (tag 1)
            'ACK 4 31', 'IN 3 03008000', 'IN 3 ' + _csw_hex(1, 0, 0),
            # write_10 1 sector rejected with CSW status=1 (tag 2)
            'ACK 4 31', 'ACK 4 512', 'IN 3 ' + _csw_hex(2, 512, 1),
            # request_sense (tag 3)
            'ACK 4 31', 'IN 3 ' + sense_wp.hex(), 'IN 3 ' + _csw_hex(3, 0, 0),
            # invalid CDB 0xEE -> Bulk-IN STALL (tag 4)
            'ACK 4 31', 'STALL 3',
            # recover_from_stall: BOMSR + clear_halt(0x81) + clear_halt(0x02)
            'ACK 0 8', 'IN 1 ',
            'ACK 0 8', 'IN 1 ',
            'ACK 0 8', 'IN 1 ',
            # test_unit_ready after recovery (tag 5)
            'ACK 4 31', 'IN 3 ' + _csw_hex(5, 0, 0),
        ]
        ch = _ScriptedChannel(replies)
        client = MscBotClient(Ep0Host(ch), _enum_dict(MSC_DEVICE_HEX, MSC_CONFIG_HEX))
        self.assertTrue(client.mode_sense_6()['write_protected'])
        with self.assertRaises(MscCommandFailedError) as wp_ctx:
            client.write_10(0, b'X' * 512)
        self.assertEqual(wp_ctx.exception.status, 1)
        sense = client.request_sense()
        self.assertTrue(sense['data_protect'])
        self.assertEqual((sense['sense_key'], sense['asc'], sense['ascq']), (0x07, 0x27, 0x00))

        with self.assertRaises(MscStallError) as stall_ctx:
            client.scsi_command(b'\xee\x00\x00\x00\x10\x00', data_in_len=16)
        self.assertEqual(stall_ctx.exception.ep_idx, 3)
        client.recover_from_stall()
        self.assertTrue(client.test_unit_ready())
        self.assertEqual(ch.replies, [])

    def test_bad_csw_signature_tag_and_phase_error_are_rejected(self):
        enum = _enum_dict(MSC_DEVICE_HEX, MSC_CONFIG_HEX)
        bad_sig = CSW_STRUCT.pack(0xDEADBEEF, 1, 0, 0).hex()
        bad_tag = _csw_hex(99, 0, 0)
        phase_err = _csw_hex(1, 0, 2)

        for reply, exc_type in (
            (bad_sig, ValueError),
            (bad_tag, ValueError),
            (phase_err, MscPhaseError),
        ):
            client = MscBotClient(Ep0Host(_ScriptedChannel(['ACK 4 31', 'IN 3 ' + reply])), enum)
            with self.assertRaises(exc_type):
                client.test_unit_ready()


class PcRemotePtpClientUnitTests(unittest.TestCase):
    def test_ptp_handshake_fragmented_liveview_and_interrupt_events(self):
        jpeg = _make_synthetic_jpeg(1280, 720, 1050)
        lv_payload = b'\xff\x01\x00\x01' + bytes(4) + b'\x24\x35\x68\x79' + bytes(124) + jpeg
        lv_data = _ptp_pkt(PTP_CONTAINER_DATA, 0x1009, 7, lv_payload)

        ext_info = struct.pack('<HI5H', 0x012C, 5, 0xD25A, 0xD2C1, 0xD2C2, 0xD20D, 0xD21D)
        storage = struct.pack('<II', 1, 0x00010001)
        dev_info = _ptp_device_info_payload()

        replies = [
            # get_device_status (A1 67)
            'ACK 0 8', 'IN 1 04000120', 'ACK 0 0',
            # tx 0: get_device_info
            'ACK 4 12',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_DATA, 0x1001, 0, dev_info).hex(),
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 0).hex(),
            # tx 1: open_session(1)
            'ACK 4 16',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 1).hex(),
            # tx 2..4: sdio_connect(1, 2) + sdio_get_ext_device_info + sdio_connect(3)
            'ACK 4 24', 'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 2).hex(),
            'ACK 4 24', 'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 3).hex(),
            'ACK 4 16',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_DATA, 0x9202, 4, ext_info).hex(),
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 4).hex(),
            'ACK 4 24', 'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 5).hex(),
            # tx 6: get_storage_ids
            'ACK 4 12',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_DATA, 0x1004, 6, storage).hex(),
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 6).hex(),
            # tx 7: get_liveview_object fragmented across 3 Bulk-IN packets
            'ACK 4 16',
            'IN 3 ' + lv_data[0:512].hex(),
            'IN 3 ' + lv_data[512:1024].hex(),
            'IN 3 ' + lv_data[1024:].hex(),
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 7).hex(),
            # tx 8: sdio_control_device(0xD2C2, 2) -> cmd 16B + data 14B + resp 12B
            'ACK 4 16', 'ACK 4 14',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 8).hex(),
            # poll_event on EP 7 (0x83): 0x4002 ObjectAdded + 0xC201 Sony_ObjectAdded
            'IN 7 ' + _ptp_pkt(PTP_CONTAINER_EVENT, 0x4002, 8, struct.pack('<I', 0xFFFFC001)).hex(),
            'IN 7 ' + _ptp_pkt(PTP_CONTAINER_EVENT, 0xC201, 8, struct.pack('<I', 0xFFFFC001)).hex(),
            # cancel_transaction(8) on EP0 (6B OUT)
            'ACK 0 8', 'ACK 0 6', 'IN 1 ',
            # tx 9: close_session
            'ACK 4 12', 'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 9).hex(),
        ]
        ch = _ScriptedChannel(replies)
        client = PcRemotePtpClient(Ep0Host(ch), _enum_dict(PTP_DEVICE_HEX, PTP_CONFIG_HEX))
        st = client.get_device_status()
        self.assertTrue(st['ok'])
        info = client.get_device_info()
        self.assertEqual(info['model'], 'ILCE-6400')
        client.open_session(1)
        client.sdio_connect(1)
        client.sdio_connect(2)
        ext = client.sdio_get_ext_device_info(0x012C)
        self.assertEqual(ext['protocol_version'], 0x012C)
        self.assertIn(0xD2C2, ext['supported_properties'])
        client.sdio_connect(3)
        self.assertEqual(client.get_storage_ids(), [0x00010001])

        lv = client.get_liveview_object()
        self.assertEqual(lv['fragments'], 3)
        self.assertEqual((lv['width'], lv['height']), (1280, 720))
        self.assertEqual(lv['jpeg'], jpeg)

        client.sdio_control_device(0xD2C2, 2)
        ev1 = client.poll_event()
        self.assertEqual((ev1['code'], ev1['name'], ev1['params']), (0x4002, 'ObjectAdded', (0xFFFFC001,)))
        ev2 = client.poll_event()
        self.assertEqual((ev2['code'], ev2['name']), (0xC201, 'Sony_ObjectAdded'))

        client.cancel_transaction(8)
        client.close_session()
        self.assertFalse(client.session_open)
        self.assertEqual(ch.replies, [])

    def test_ptp_rejects_mismatched_containers_bad_phases_and_error_responses(self):
        enum = _enum_dict(PTP_DEVICE_HEX, PTP_CONFIG_HEX)
        with self.assertRaises(ValueError):
            PcRemotePtpClient(Ep0Host(_ScriptedChannel()), enum).sdio_connect(4)

        # Non-OK PTP response raises PtpResponseError
        ch_err = _ScriptedChannel([
            'ACK 4 16',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x201E, 0).hex(),
        ])
        with self.assertRaises(PtpResponseError) as ctx:
            PcRemotePtpClient(Ep0Host(ch_err), enum).open_session(1)
        self.assertEqual(ctx.exception.response_code, 0x201E)

        # Truncated PTP container header
        ch_trunc = _ScriptedChannel(['ACK 4 12', 'IN 3 04000200'])
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            PcRemotePtpClient(Ep0Host(ch_trunc), enum).get_device_info()


class CompositeUvcPtpClientUnitTests(unittest.TestCase):
    def _uvc_packets_for_jpeg(self, jpeg: bytes, fid: int, pts: int) -> list[str]:
        out = []
        for off in range(0, len(jpeg), 500):
            chunk = jpeg[off:off + 500]
            eof = 0x02 if (off + len(chunk) >= len(jpeg)) else 0x00
            hdr = struct.pack('<BBIIH', 12, 0x80 | 0x0C | (fid & 1) | eof, pts, pts, 0)
            out.append('IN 9 ' + (hdr + chunk).hex())
        return out

    def test_composite_topology_probe_commit_and_interleaved_uvc_ptp(self):
        jpeg = _make_synthetic_jpeg(1280, 720, 920)
        negotiated = CompositeUvcPtpClient.pack_streaming_control(
            bm_hint=1,
            format_index=1,
            frame_index=1,
            frame_interval=333333,
            max_video_frame_size=1280 * 720 * 2,
            max_payload_transfer_size=16384,
        )
        dev_info = _ptp_device_info_payload()

        replies = [
            # PROBE SET_CUR + GET_CUR
            'ACK 0 8', 'ACK 0 26', 'IN 1 ',
            'ACK 0 8', 'IN 1 ' + negotiated.hex(), 'ACK 0 0',
            # COMMIT SET_CUR + GET_CUR
            'ACK 0 8', 'ACK 0 26', 'IN 1 ',
            'ACK 0 8', 'IN 1 ' + negotiated.hex(), 'ACK 0 0',
            # UVC Frame 0 (FID=0)
            *self._uvc_packets_for_jpeg(jpeg, fid=0, pts=1000),
            # Interleaved PTP GetDeviceInfo + OpenSession on EP 4 / EP 3
            'ACK 4 12',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_DATA, 0x1001, 0, dev_info).hex(),
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 0).hex(),
            'ACK 4 16',
            'IN 3 ' + _ptp_pkt(PTP_CONTAINER_RESPONSE, 0x2001, 1).hex(),
            # UVC Frame 1 (FID=1)
            *self._uvc_packets_for_jpeg(jpeg, fid=1, pts=4000),
        ]
        ch = _ScriptedChannel(replies)
        comp = CompositeUvcPtpClient(Ep0Host(ch), _enum_dict(COMP_DEVICE_HEX, COMP_CONFIG_HEX))
        self.assertEqual(comp.ep_uvc_in, 9)
        neg = comp.negotiate_mjpeg_stream()
        self.assertEqual(neg['commit']['dwFrameInterval'], 333333)

        f0 = comp.read_uvc_mjpeg_frame()
        self.assertEqual((f0['fid'], f0['width'], f0['height']), (0, 1280, 720))
        self.assertGreaterEqual(f0['packets'], 2)

        self.assertEqual(comp.ptp.get_device_info()['model'], 'ILCE-6400')
        comp.ptp.open_session(1)

        f1 = comp.read_uvc_mjpeg_frame()
        self.assertEqual((f1['fid'], f1['jpeg']), (1, jpeg))
        self.assertEqual(ch.replies, [])

    def test_uvc_rejects_missing_iad_non_toggling_fid_err_bit_and_missing_eof(self):
        with self.assertRaisesRegex(ValueError, '054C:0322'):
            CompositeUvcPtpClient(Ep0Host(_ScriptedChannel()), _enum_dict(PTP_DEVICE_HEX, COMP_CONFIG_HEX))
        with self.assertRaisesRegex(ValueError, '3-interface'):
            CompositeUvcPtpClient(Ep0Host(_ScriptedChannel()), _enum_dict(COMP_DEVICE_HEX, PTP_CONFIG_HEX))

        enum = _enum_dict(COMP_DEVICE_HEX, COMP_CONFIG_HEX)
        jpeg = _make_synthetic_jpeg(640, 480, 120)

        # Non-toggling FID across consecutive frames (both FID=0)
        pkts = self._uvc_packets_for_jpeg(jpeg, fid=0, pts=100) + self._uvc_packets_for_jpeg(jpeg, fid=0, pts=200)
        comp = CompositeUvcPtpClient(Ep0Host(_ScriptedChannel(pkts)), enum)
        self.assertEqual(comp.read_uvc_mjpeg_frame()['fid'], 0)
        with self.assertRaisesRegex(ValueError, 'BFH_FID'):
            comp.read_uvc_mjpeg_frame()

        # BFH_ERR bit set (0x40)
        err_hdr = struct.pack('<BBIIH', 12, 0x80 | 0x40 | 0x02, 0, 0, 0) + jpeg
        comp_err = CompositeUvcPtpClient(Ep0Host(_ScriptedChannel(['IN 9 ' + err_hdr.hex()])), enum)
        with self.assertRaisesRegex(ValueError, 'BFH_ERR'):
            comp_err.read_uvc_mjpeg_frame()

        # Missing BFH_EOF
        no_eof = struct.pack('<BBIIH', 12, 0x80, 0, 0, 0) + jpeg
        comp_eof = CompositeUvcPtpClient(Ep0Host(_ScriptedChannel(['IN 9 ' + no_eof.hex()])), enum)
        with self.assertRaisesRegex(ValueError, 'BFH_EOF'):
            comp_eof.read_uvc_mjpeg_frame(max_packets=1)


class BusPowerHidAndJiritsuUnitTests(unittest.TestCase):
    def test_bus_power_hid_descriptors_report_descriptor_and_input_report(self):
        replies = [
            *_enum_replies(HID_DEVICE_HEX, HID_CONFIG_HEX),
            'ACK 0 8', 'IN 1 ' + HID_REPORT_DESC.hex(), 'ACK 0 0',
            'IN 3 4250575201016f09',
        ]
        ch = _ScriptedChannel(replies)
        hid = BusPowerHidClient(Ep0Host(ch))
        hid.enumerate()
        self.assertEqual(hid.report_descriptor_length, 19)
        self.assertEqual(hid.get_report_descriptor(), HID_REPORT_DESC)
        self.assertEqual(hid.read_input_report()[:4], b'BPWR')
        self.assertEqual(ch.replies, [])

    @mock.patch('tools.research.qemu_ep0_host.time.sleep')
    def test_setup_abort_recovery_over_scripted_channel(self, _sleep):
        # First SETUP starts SET_ADDRESS without status IN; host aborts with fresh enumeration
        replies = [
            'ACK 0 8',
            'NAK 0',
            *_enum_replies(HID_DEVICE_HEX, HID_CONFIG_HEX),
        ]
        ch = _ScriptedChannel(replies)
        host = Ep0Host(ch)
        self.assertEqual(host.token('SETUP 0005010000000000', 'ACK 0 8'), '')
        enum = BusPowerHidClient(host).enumerate()
        self.assertTrue(enum['control_sequence_completed'])
        self.assertEqual(ch.replies, [])

    def test_jiritsu_service_ping_chunk_read_and_error_status(self):
        ping_body = struct.pack('<I', 0) + b'38117_03.2026041003:PING_OK'
        ping_pkt = SENSER_HEADER.pack(len(ping_body), 0x40, 1, 3, 0, 0, 0) + ping_body

        chunk_bytes = bytes((i * 13) & 0xFF for i in range(1024))
        chunk_body = struct.pack('<I', 0) + chunk_bytes
        chunk_pkt = SENSER_HEADER.pack(len(chunk_body), 0x40, 2, 3, 0, 0, 0) + chunk_body

        err_body = struct.pack('<I', 0x80000002)
        err_pkt = SENSER_HEADER.pack(len(err_body), 0x40, 3, 3, 0, 0, 0) + err_body

        replies = [
            # seq 1: ping
            'ACK 4 23',
            'IN 3 ' + ping_pkt.hex(),
            # seq 2: 1024B chunk read across 3 fragments (512 + 512 + 16)
            'ACK 4 24',
            'IN 3 ' + chunk_pkt[0:512].hex(),
            'IN 3 ' + chunk_pkt[512:1024].hex(),
            'IN 3 ' + chunk_pkt[1024:].hex(),
            # seq 3: status error
            'ACK 4 24',
            'IN 3 ' + err_pkt.hex(),
        ]
        ch = _ScriptedChannel(replies)
        jir = JiritsuServiceClient(
            Ep0Host(ch), _enum_dict(JIRITSU_DEVICE_HEX, JIRITSU_CONFIG_HEX)
        )
        res = jir.ping(b'PING_OK')
        self.assertEqual(res['version'], 3)
        self.assertEqual(jir.read_chunk(0, 1024), chunk_bytes)
        with self.assertRaises(JiritsuReplyError) as ctx:
            jir.read_chunk(65536, 512)
        self.assertEqual(ctx.exception.status, 0x80000002)
        self.assertEqual(ch.replies, [])


# ===========================================================================
# GROUP B: End-to-End Native QEMU TCG + Bare-Metal ARM DWC3 Firmware Tests
# ===========================================================================

SRAM_MBOX = 0xFE006000
DWC3_DCTL = 0xF020C704
DWC3_DSTS = 0xF020C70C
DSTS_DEVCTRLHLT = 1 << 22


@unittest.skipUnless(
    NATIVE_QEMU_AND_CROSS_AVAILABLE,
    'Optional WSL/Linux QEMU build or arm-linux-gnueabi-gcc absent',
)
class QemuUsbConnectionsNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dev_help = subprocess.run(
            [str(QEMU), '-M', 'none', '-device', 'cxd90045-dwc3,help'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
        )
        if 'allow-setup-abort' not in (dev_help.stdout + dev_help.stderr):
            raise unittest.SkipTest(
                'QEMU binary predates multi-mode cxd90045-dwc3 contracts; '
                'set BIONZX_QEMU_TEST_BINARY to the rebuilt binary'
            )

        cls._tmp = tempfile.TemporaryDirectory(prefix='a6400-usbconn-')
        cls.scratch = Path(cls._tmp.name)
        elf = cls.scratch / 'usb_gadget_modes_fw.elf'
        cls.fw_bin = cls.scratch / 'usb_gadget_modes_fw.bin'
        b_flag = (
            ['-B/usr/arm-linux-gnueabi/bin/']
            if Path('/usr/arm-linux-gnueabi/bin').is_dir()
            else []
        )
        subprocess.run(
            [
                CROSS, *b_flag,
                '-mcpu=cortex-a5', '-marm', '-mno-unaligned-access',
                '-ffreestanding', '-nostdlib',
                '-O2', '-Wall', '-Wextra', '-Werror',
                '-Wl,-Ttext=0x00108000', '-Wl,--build-id=none',
                str(FW_SOURCE), '-o', str(elf),
            ],
            check=True,
        )
        subprocess.run([OBJCOPY, '-O', 'binary', str(elf), str(cls.fw_bin)], check=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, '_tmp'):
            cls._tmp.cleanup()

    def _start_vm(self, boot_mode: str, *, allow_setup_abort: bool = False):
        run_dir = Path(tempfile.mkdtemp(prefix=f'vm-{boot_mode}-', dir=self.scratch))
        qmp_sock = run_dir / 'qmp.sock'
        usb_sock = run_dir / 'usb.sock'
        log_file = (run_dir / 'qemu.log').open('wb')
        self.addCleanup(log_file.close)

        cmd = [
            str(QEMU),
            '-M', 'sony-a6400,dwc3-gadget=on,usb-vbus-test=on,usb-cid-high=on',
            '-m', '1G',
            '-smp', '1',
            '-kernel', str(self.fw_bin),
            '-append', f'mode={boot_mode}',
            '-display', 'none',
            '-monitor', 'none',
            '-serial', 'stdio',
            '-net', 'none',
            '-no-reboot',
            '-qmp', f'unix:{qmp_sock},server=on,wait=off',
            '-chardev', f'socket,id=host,path={usb_sock},server=on,wait=off',
            '-global', 'cxd90045-dwc3.chardev=host',
            '-global', 'cxd90045-dwc3.strict-ep0=on',
            '-global', 'cxd90045-dwc3.strict-bulk=on',
            '-global', 'cxd90045-dwc3.captured-hwparams=on',
            '-global', 'cxd90045-dwc3.captured-start-gate=on',
        ]
        if allow_setup_abort:
            cmd.extend(['-global', 'cxd90045-dwc3.allow-setup-abort=on'])

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

        def cleanup_vm():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()

        self.addCleanup(cleanup_vm)

        deadline = time.monotonic() + 25
        while not (qmp_sock.exists() and usb_sock.exists()):
            self.assertIsNone(proc.poll(), 'QEMU exited before creating sockets')
            self.assertLess(time.monotonic(), deadline, 'QEMU socket startup timed out')
            time.sleep(0.01)

        qmp = Qmp(qmp_sock, deadline + 30)
        self.addCleanup(qmp.socket.close)

        usb = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        usb.settimeout(0.5)
        usb.connect(str(usb_sock))
        self.addCleanup(usb.close)

        # Wait for bare-metal firmware to initialize SRAM mailbox ('USBM')
        self._wait_for(lambda: self._read_u32(qmp, SRAM_MBOX) == 0x5553424D,
                       timeout=10, label='SRAM mailbox magic')
        return proc, qmp, Ep0Host(usb, deadline=time.monotonic() + 45)

    def _read_u32(self, qmp: Qmp, pa: int) -> int:
        out = qmp.request('human-monitor-command', {'command-line': f'xp /1wx {pa:#x}'})
        match = re.search(r':\s*(0x[0-9a-fA-F]+)', str(out))
        self.assertIsNotNone(match, f'Unexpected xp output for {pa:#x}: {out!r}')
        return int(match.group(1), 16)

    def _set_vbus(self, qmp: Qmp, present: bool) -> None:
        qmp.request('qom-set', {'path': '/machine', 'property': 'usb-vbus', 'value': present})
        self.assertIs(
            qmp.request('qom-get', {'path': '/machine', 'property': 'usb-vbus'}),
            present,
        )

    def _wait_for(self, predicate, *, timeout: float = 10.0, label: str = 'condition'):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.01)
        self.fail(f'Timed out waiting for {label}')

    def _connect_bus(self, qmp: Qmp, host: Ep0Host) -> None:
        resets_before = self._read_u32(qmp, SRAM_MBOX + 0x2C)
        host.connect()
        self._wait_for(
            lambda: self._read_u32(qmp, SRAM_MBOX + 0x2C) > resets_before,
            label='DWC3 USBRESET after CONN',
        )

    def test_msc_bot_read_write_wp_and_stall_recovery(self):
        proc, qmp, host = self._start_vm('msc')
        self.assertEqual(self._read_u32(qmp, SRAM_MBOX + 0x10), 0x4020404A)
        self.assertEqual(self._read_u32(qmp, SRAM_MBOX + 0x14), 0x5533290A)
        self.assertEqual(self._read_u32(qmp, SRAM_MBOX + 0x18), 0x02)  # CID high, VBUS=0

        self._set_vbus(qmp, True)
        self._wait_for(lambda: self._read_u32(qmp, SRAM_MBOX + 0x08) == 1, label='MSC active')
        self._connect_bus(qmp, host)

        msc = MscBotClient(host)
        msc.enumerate()
        self.assertEqual(msc.get_max_lun(), 0)
        inq = msc.inquiry()
        self.assertEqual((inq['vendor'], inq['product'], inq['revision']), ('Sony', 'DSC', '1.00'))
        cap = msc.read_capacity_10()
        self.assertEqual((cap['block_count'], cap['block_size']), (64, 512))
        self.assertFalse(msc.mode_sense_6()['write_protected'])

        ext = msc.sony_ext_model_info()
        self.assertEqual((ext['model'], ext['product_code_wire_hex']), ('ILCE-6400', '0032858730'))

        # Multi-sector 2048 B WRITE(10) = four 512B Bulk OUT packets into one 2048B TRB
        pattern = bytes(((i * 29 + 11) & 0xFF) for i in range(2048))
        msc.write_10(8, pattern)
        self.assertEqual(msc.read_10(8, 4), pattern)

        # Toggle write-protect flag in SRAM mailbox via UART command
        proc.stdin.write(b'W 1\n')
        self._wait_for(
            lambda: bool(self._read_u32(qmp, SRAM_MBOX + 0x0C) & 1),
            label='SRAM write-protect bit',
        )
        self.assertTrue(msc.mode_sense_6()['write_protected'])
        with self.assertRaises(MscCommandFailedError) as wp_err:
            msc.write_10(8, b'\xAA' * 2048)
        self.assertEqual(wp_err.exception.status, 1)
        sense = msc.request_sense()
        self.assertTrue(sense['data_protect'])
        self.assertEqual((sense['sense_key'], sense['asc']), (0x07, 0x27))
        self.assertEqual(msc.read_10(8, 4), pattern)

        # Unsupported CDB 0xEE -> Bulk-IN STALL -> BOT Reset Recovery -> TUR succeeds
        with self.assertRaises(MscStallError):
            msc.scsi_command(b'\xee\x00\x00\x00\x10\x00', data_in_len=16)
        msc.recover_from_stall()
        self.assertTrue(msc.test_unit_ready())

    def test_pc_remote_ptp_handshake_liveview_and_interrupt_event(self):
        _proc, qmp, host = self._start_vm('ptp')
        self._set_vbus(qmp, True)
        self._wait_for(lambda: self._read_u32(qmp, SRAM_MBOX + 0x08) == 2, label='PTP active')
        self._connect_bus(qmp, host)

        ptp = PcRemotePtpClient(host)
        ptp.enumerate()
        self.assertTrue(ptp.get_device_status()['ok'])
        info = ptp.get_device_info()
        self.assertEqual(info['model'], 'ILCE-6400')

        ptp.open_session(1)
        for phase in (1, 2, 3):
            ptp.sdio_connect(phase)
        ext = ptp.sdio_get_ext_device_info(0x012C)
        self.assertEqual(ext['protocol_version'], 0x012C)
        self.assertEqual(ptp.get_storage_ids(), [0x00010001])

        lv = ptp.get_liveview_object(0xFFFFC002)
        self.assertGreaterEqual(lv['fragments'], 3)
        self.assertEqual((lv['width'], lv['height']), (1280, 720))
        self.assertEqual(lv['jpeg'][:2], b'\xff\xd8')
        self.assertEqual(lv['jpeg'][-2:], b'\xff\xd9')

        ptp.sdio_control_device(0xD2C2, 2)
        ev = ptp.poll_event()
        self.assertEqual((ev['code'], ev['name'], ev['params']), (0x4002, 'ObjectAdded', (0xFFFFC001,)))
        ptp.cancel_transaction(1)
        ptp.close_session()

    def test_composite_uvc_ptp_concurrent_streaming_and_control(self):
        _proc, qmp, host = self._start_vm('uvc')
        self._set_vbus(qmp, True)
        self._wait_for(
            lambda: self._read_u32(qmp, SRAM_MBOX + 0x08) == 3,
            label='Composite UVC+PTP active',
        )
        self._connect_bus(qmp, host)

        comp = CompositeUvcPtpClient(host)
        comp.enumerate()
        neg = comp.negotiate_mjpeg_stream()
        self.assertEqual(neg['commit']['dwFrameInterval'], 333333)

        frame0 = comp.read_uvc_mjpeg_frame()
        self.assertEqual((frame0['fid'], frame0['width'], frame0['height']), (0, 1280, 720))
        self.assertGreaterEqual(frame0['packets'], 3)

        # Interleave PTP operations on 0x81/0x02 while UVC stream is active on 0x84
        self.assertEqual(comp.ptp.get_device_info()['model'], 'ILCE-6400')
        comp.ptp.open_session(1)
        comp.ptp.sdio_connect(1)

        frame1 = comp.read_uvc_mjpeg_frame()
        self.assertEqual((frame1['fid'], frame1['width'], frame1['height']), (1, 1280, 720))
        self.assertGreater(frame1['pts'], frame0['pts'])
        comp.ptp.close_session()

    def test_hid_setup_abort_jiritsu_and_dynamic_mode_switching(self):
        proc, qmp, host = self._start_vm('hid', allow_setup_abort=True)
        self._set_vbus(qmp, True)
        self._wait_for(lambda: self._read_u32(qmp, SRAM_MBOX + 0x08) == 4, label='HID active')
        self._connect_bus(qmp, host)

        # Issue SETUP without completing status IN, wait for guest SETUP handler,
        # then abort with fresh enumeration SETUP (allow-setup-abort=on)
        setups_before = self._read_u32(qmp, SRAM_MBOX + 0x30)
        self.assertEqual(host.token('SETUP 0005010000000000', 'ACK 0 8'), '')
        self._wait_for(
            lambda: self._read_u32(qmp, SRAM_MBOX + 0x30) > setups_before,
            label='aborted SETUP handled by guest',
        )
        hid = BusPowerHidClient(host)
        hid.enumerate()
        self.assertEqual(hid.get_report_descriptor(), HID_REPORT_DESC)
        self.assertEqual(hid.read_input_report()[:4], b'BPWR')

        # Host bus RESET re-initializes address/configuration cleanly
        resets_before = self._read_u32(qmp, SRAM_MBOX + 0x2C)
        host.bus_reset()
        self._wait_for(
            lambda: self._read_u32(qmp, SRAM_MBOX + 0x2C) > resets_before,
            label='USB bus reset event',
        )
        hid.enumerate()

        # Switch to Mode 5: Jiritsu Service (054C:0336)
        host.disconnect()
        proc.stdin.write(b'M 5\n')
        self._wait_for(lambda: self._read_u32(qmp, SRAM_MBOX + 0x08) == 5, label='Jiritsu active')
        self._connect_bus(qmp, host)
        jir = JiritsuServiceClient(host)
        jir.enumerate()
        self.assertIn(b'JIRITSU_OK', jir.ping(b'JIRITSU_OK')['data'])
        chunk = jir.read_chunk(0, 1024)
        self.assertEqual(chunk[0:4], b'SONY')
        self.assertEqual(chunk[512:516], b'SONY')

        # Dynamic chain: MSC (1) -> PC Remote (2) -> Composite UVC+PTP (3) -> VBUS=0 detach
        for mode_id in (1, 2, 3):
            switches_before = self._read_u32(qmp, SRAM_MBOX + 0x24)
            halts_before = self._read_u32(qmp, SRAM_MBOX + 0x28)
            host.disconnect()
            proc.stdin.write(f'M {mode_id}\n'.encode('ascii'))
            self._wait_for(
                lambda m=mode_id, s=switches_before: (
                    self._read_u32(qmp, SRAM_MBOX + 0x08) == m
                    and self._read_u32(qmp, SRAM_MBOX + 0x24) > s
                ),
                label=f'mode switch to {mode_id}',
            )
            self.assertGreater(self._read_u32(qmp, SRAM_MBOX + 0x28), halts_before)
            self.assertTrue(self._read_u32(qmp, SRAM_MBOX + 0x34) & DSTS_DEVCTRLHLT)
            self._connect_bus(qmp, host)
            if mode_id == 1:
                m_cli = MscBotClient(host)
                m_cli.enumerate()
                self.assertEqual(m_cli.inquiry()['vendor'], 'Sony')
            elif mode_id == 2:
                p_cli = PcRemotePtpClient(host)
                p_cli.enumerate()
                self.assertEqual(p_cli.get_device_info()['model'], 'ILCE-6400')
            else:
                c_cli = CompositeUvcPtpClient(host)
                c_cli.enumerate()
                c_cli.negotiate_mjpeg_stream()
                self.assertEqual(c_cli.read_uvc_mjpeg_frame()['fid'], 0)

        # VBUS=0 detach -> unconfigure-first soft-disconnect + DSTS.DEVCTRLHLT confirmed
        host.disconnect()
        self._set_vbus(qmp, False)
        self._wait_for(
            lambda: (
                self._read_u32(qmp, SRAM_MBOX + 0x08) == 0
                and self._read_u32(qmp, SRAM_MBOX + 0x20) >= 1
            ),
            label='VBUS detach & controller halt',
        )
        self.assertEqual(self._read_u32(qmp, DWC3_DCTL) & (1 << 31), 0)
        self.assertTrue(self._read_u32(qmp, DWC3_DSTS) & DSTS_DEVCTRLHLT)
        self.assertEqual(self._read_u32(qmp, SRAM_MBOX + 0x10), 0x4020404A)


if __name__ == '__main__':
    unittest.main()
