"""Host-side protocol validation without a guest, USB or physical media."""
import struct
import unittest
from unittest import mock
from tools.research.qemu_ep0_host import Ep0Host
from tools.research.qemu_ptp_device_info import get_device_info

DEVICE='12010002000000404c056f09000101020301'
CONFIG='0902220001010080fa09040000010300000009211101000122130007058103200001'
PTP_CONFIG='0902270001010080fa090400000306010100070581020002000705020200020007058303200007'
PTP_DEVICE=DEVICE[:20]+'aa0c'+DEVICE[24:]

class Channel:
    def __init__(self,replies):self.replies=[r.encode()+b'\n' for r in replies];self.sent=[]
    def sendall(self,data):self.sent.append(data.decode().strip())
    def recv(self,size):return self.replies.pop(0) if self.replies else b''

def enumeration_replies():
    return ['ACK 0 8','IN 1 '+DEVICE,'ACK 0 0','ACK 0 8','IN 1 ',
            'ACK 0 8','IN 1 '+CONFIG[:18],'ACK 0 0',
            'ACK 0 8','IN 1 '+CONFIG,'ACK 0 0','ACK 0 8','IN 1 ',
            'ACK 0 8','IN 1 01','ACK 0 0']

class Ep0HostTests(unittest.TestCase):
    def test_all_control_phases_are_required(self):
        channel=Channel(enumeration_replies())
        result=Ep0Host(channel).enumerate_bus_power()
        self.assertTrue(result['control_sequence_completed'])
        self.assertFalse(result['uvc_streaming_verified'])
        self.assertEqual(channel.sent.count('OUT 0 -'),4)
        self.assertIn('SETUP 0005010000000000',channel.sent)
        self.assertIn('SETUP 0009010000000000',channel.sent)
        self.assertEqual(len(channel.sent),16)
        self.assertEqual(channel.replies,[])

    @mock.patch('tools.research.qemu_ep0_host.time.sleep')
    def test_nak_retries_the_same_token(self,sleep):
        channel=Channel(['NAK 1','IN 1 '])
        self.assertEqual(Ep0Host(channel).token('IN 1','IN 1 '),'')
        self.assertEqual(channel.sent,['IN 1','IN 1'])

    def test_wrong_endpoint_nak_is_an_error(self):
        with self.assertRaises(ValueError):Ep0Host(Channel(['NAK 0'])).token('IN 1','IN 1 ')

    def test_superset_ack_is_not_success(self):
        with self.assertRaises(ValueError):Ep0Host(Channel(['ACK 0 80'])).control('0005010000000000')

    def test_empty_device_data_is_not_success(self):
        with self.assertRaises(ValueError):
            Ep0Host(Channel(['ACK 0 8','IN 1 '])).control('8006000100001200')

    def test_original_configuration_readback_must_agree(self):
        replies=enumeration_replies();replies[-2]='IN 1 02'
        with self.assertRaises(ValueError):Ep0Host(Channel(replies)).enumerate_bus_power()

    def test_stall_and_eof_are_boundaries(self):
        with self.assertRaises(RuntimeError):Ep0Host(Channel(['STALL 1'])).token('IN 1','IN 1 ')
        with self.assertRaises(EOFError):Ep0Host(Channel([])).token('IN 1','IN 1 ')


def ptp_container(kind, code, payload=b'', transaction=0):
    return struct.pack('<IHHI', 12 + len(payload), kind, code, transaction) + payload


def ptp_string(value):
    return bytes([len(value) + 1]) + value.encode('utf-16le') + b'\0\0'


def device_info_dataset():
    return (struct.pack('<HIH', 100, 0x11, 100) + ptp_string('Sony PTP Extensions') +
            struct.pack('<HIHIIII', 0, 1, 0x1001, 0, 0, 0, 0) +
            ptp_string('Sony Corporation') + ptp_string('ILCE-6400') + b'\0\0')


class QemuPtpInfoTests(unittest.TestCase):
    def setUp(self):
        self.enumeration = {'device_hex': PTP_DEVICE, 'configuration_hex': PTP_CONFIG}
        self.data = ptp_container(2, 0x1001, device_info_dataset())
        self.response = ptp_container(3, 0x2001)

    @mock.patch('tools.research.qemu_ep0_host.time.sleep')
    def test_device_info_requires_bulk_command_fragmented_data_and_ok(self, sleep):
        channel = Channel(['NAK 4', 'ACK 4 12', 'IN 3 ' + self.data[:20].hex(),
                           'IN 3 ' + self.data[20:].hex(), 'IN 3 ' + self.response.hex()])
        result = get_device_info(Ep0Host(channel), self.enumeration)
        self.assertEqual(result['model'], 'ILCE-6400')
        self.assertEqual(result['data_bytes'], len(self.data))
        self.assertEqual(channel.sent, ['OUT 4 0c0000000100011000000000'] * 2 + ['IN 3'] * 3)

    def test_diagnostic_hid_never_receives_the_ptp_command(self):
        channel = Channel([])
        with self.assertRaisesRegex(ValueError, 'still-image'):
            get_device_info(Ep0Host(channel), {**self.enumeration, 'configuration_hex': CONFIG})
        with self.assertRaisesRegex(ValueError, 'device identity'):
            get_device_info(Ep0Host(channel), {**self.enumeration, 'device_hex': DEVICE})
        self.assertEqual(channel.sent, [])

    def test_data_without_matching_response_is_not_a_pass(self):
        for reply in ('IN 3 ' + ptp_container(3, 0x2001, transaction=1).hex(),
                      'IN 3 ' + ptp_container(3, 0x2002).hex()):
            channel = Channel(['ACK 4 12', 'IN 3 ' + self.data.hex(), reply])
            with self.assertRaisesRegex(ValueError, 'container.*mismatch'):
                get_device_info(Ep0Host(channel), self.enumeration)

    def test_other_device_identity_is_not_sony_proof(self):
        other = device_info_dataset().replace('ILCE-6400'.encode('utf-16le'),
                                              'ILCE-6100'.encode('utf-16le'))
        channel = Channel(['ACK 4 12', 'IN 3 ' + ptp_container(2, 0x1001, other).hex(),
                           'IN 3 ' + self.response.hex()])
        with self.assertRaisesRegex(ValueError, 'does not identify'):
            get_device_info(Ep0Host(channel), self.enumeration)

if __name__=='__main__':unittest.main()
