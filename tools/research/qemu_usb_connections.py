"""Virtual-USB host drivers and validators for Sony CXD90045 connection modes.

Exercises the QEMU ``cxd90045-dwc3`` virtual host socket across the five USB
device identities documented in local evidence:

- Mass Storage Bulk-Only Transport (``054C:0CA8`` / ``054C:0D9D``, ``08/06/50``;
  E0084, E0509, E0646)
- PC Remote / Still Image PTP (``054C:0CAA``, ``06/01/01``; E0152, E0258,
  E0315, E0655)
- Composite UVC + PTP (``054C:0322``, ``EF/02/01`` IAD + PTP + UVC VC/VS;
  E0154, E0258, E0347)
- Bus-Power / HID (``054C:096F``, ``03/00/00``; E0597, E0598)
- Jiritsu Service (``054C:0336``, ``FF/00/00``; E0525, E0643)

No physical USB handle is opened, and no device response is synthesized on the
host side: malformed descriptors, truncated transfers, mismatched tags or
transactions, and unexpected stalls raise explicit errors.
"""
from __future__ import annotations

import hashlib
import struct

from src.a6400.execution.guest_usb_otgcore import parse_usb_configuration_descriptor
from tools.research.qemu_ep0_host import Ep0Host
from tools.research.qemu_ptp_device_info import parse_device_info

SONY_VID = 0x054C
MSC_PIDS = (0x0CA8, 0x0D9D, 0x0F0F)
PTP_PIDS = (0x0CAA, 0x0D9F)
COMPOSITE_UVC_PTP_PID = 0x0322
BUS_POWER_HID_PID = 0x096F
JIRITSU_SERVICE_PID = 0x0336

# MSC Bulk-Only Transport (USB MSC BOT 1.0)
CBW_SIGNATURE = 0x43425355  # 'USBC'
CSW_SIGNATURE = 0x53425355  # 'USBS'
CBW_STRUCT = struct.Struct('<IIIBBB16s')
CSW_STRUCT = struct.Struct('<IIIB')

# PTP (ISO 15740 + Sony SDIO extensions)
PTP_HEADER = struct.Struct('<IHHI')
PTP_CONTAINER_COMMAND = 1
PTP_CONTAINER_DATA = 2
PTP_CONTAINER_RESPONSE = 3
PTP_CONTAINER_EVENT = 4
PTP_MAX_DATA_BYTES = 256 * 1024
PTP_MAX_FRAGMENTS = 512

PTP_OC_GET_DEVICE_INFO = 0x1001
PTP_OC_OPEN_SESSION = 0x1002
PTP_OC_CLOSE_SESSION = 0x1003
PTP_OC_GET_STORAGE_IDS = 0x1004
PTP_OC_GET_OBJECT = 0x1009
PTP_OC_SDIO_CONNECT = 0x9201
PTP_OC_SDIO_GET_EXT_DEVICE_INFO = 0x9202
PTP_OC_SDIO_CONTROL_DEVICE = 0x9205

PTP_RC_OK = 0x2001
PTP_RC_DEVICE_BUSY = 0x2019
SONY_HANDLE_LIVEVIEW = 0xFFFFC002

PTP_EVENT_NAMES = {
    0x4001: 'CancelTransaction',
    0x4002: 'ObjectAdded',
    0x4003: 'ObjectRemoved',
    0x4004: 'StoreAdded',
    0x4005: 'StoreRemoved',
    0x4006: 'DevicePropChanged',
    0xC201: 'Sony_ObjectAdded',
    0xC202: 'Sony_PropertyChanged',
}

# UVC 1.1 Streaming Control & Payload Header
UVC_CS_VS_PROBE_CONTROL = 0x01
UVC_CS_VS_COMMIT_CONTROL = 0x02
UVC_SET_CUR = 0x01
UVC_GET_CUR = 0x81
UVC_STREAM_CTRL_STRUCT = struct.Struct('<HBBIHHHHHII')  # 26 bytes
UVC_BFH_FID = 0x01
UVC_BFH_EOF = 0x02
UVC_BFH_PTS = 0x04
UVC_BFH_SCR = 0x08
UVC_BFH_ERR = 0x40
UVC_BFH_EOH = 0x80

# Jiritsu / Senser framing (E0525, E0643)
SENSER_HEADER = struct.Struct('<IHHBBBB')
JIRITSU_CATEGORY = 0x1001
JIRITSU_CMD_PING = 0x0002
JIRITSU_CMD_GET_FILE = 0x0008


class MscBotError(RuntimeError):
    """Base error for USB Mass Storage Bulk-Only Transport failures."""


class MscStallError(MscBotError):
    """Raised when an MSC Bulk-IN or Bulk-OUT endpoint stalls."""

    def __init__(self, message: str, *, ep_idx: int, tag: int):
        super().__init__(message)
        self.ep_idx = ep_idx
        self.tag = tag


class MscPhaseError(MscBotError):
    """Raised when CSW reports bCSWStatus == 2 (Phase Error)."""

    def __init__(self, message: str, *, tag: int, residue: int):
        super().__init__(message)
        self.tag = tag
        self.residue = residue


class MscCommandFailedError(MscBotError):
    """Raised when a high-level SCSI command completes with bCSWStatus == 1."""

    def __init__(self, message: str, *, status: int, residue: int, tag: int):
        super().__init__(message)
        self.status = status
        self.residue = residue
        self.tag = tag


class PtpResponseError(RuntimeError):
    """Raised when a PTP Response container reports a non-OK response code."""

    def __init__(self, response_code: int, *, opcode: int, transaction_id: int,
                 params: tuple[int, ...] = ()):
        super().__init__(
            f'PTP opcode 0x{opcode:04x} failed with response 0x{response_code:04x} '
            f'(tx={transaction_id})'
        )
        self.response_code = response_code
        self.opcode = opcode
        self.transaction_id = transaction_id
        self.params = params


class JiritsuReplyError(RuntimeError):
    """Raised when a 054C:0336 Jiritsu reply reports a non-zero status word."""

    def __init__(self, status: int, *, sequence: int):
        super().__init__(f'Jiritsu service returned status 0x{status:08x} (seq={sequence})')
        self.status = status
        self.sequence = sequence


def usb_ep_addr_to_dwc3_idx(ep_address: int) -> int:
    """Map a USB endpoint address (e.g. ``0x81``, ``0x02``) to DWC3 logical ``ep_idx``."""
    if type(ep_address) is not int or ep_address & ~0x8F:
        raise ValueError(f'Invalid USB endpoint address: {ep_address!r}')
    ep_num = ep_address & 0x0F
    if not 1 <= ep_num <= 7:
        raise ValueError(f'Unsupported non-EP0 endpoint number: {ep_num}')
    dir_in = 1 if (ep_address & 0x80) else 0
    return (ep_num << 1) | dir_in


def _as_bytes(raw_or_hex: bytes | bytearray | str) -> bytes:
    if isinstance(raw_or_hex, str):
        return bytes.fromhex(raw_or_hex)
    return bytes(raw_or_hex)


def parse_usb_device_descriptor(raw_or_hex: bytes | bytearray | str) -> dict:
    """Validate and unpack an 18-byte USB Device Descriptor."""
    raw = _as_bytes(raw_or_hex)
    if len(raw) != 18 or raw[0] != 18 or raw[1] != 0x01:
        raise ValueError('Invalid 18-byte USB DEVICE descriptor')
    bcd_usb, dev_class, dev_sub, dev_proto, mps0, vid, pid, bcd_dev, i_mfg, i_prod, i_ser, num_cfg = (
        struct.unpack('<HBBBBHHHBBBB', raw[2:18])
    )
    if mps0 not in (8, 16, 32, 64) or num_cfg < 1:
        raise ValueError('Unsupported EP0 max packet size or configuration count')
    return {
        'bcdUSB': bcd_usb,
        'bDeviceClass': dev_class,
        'bDeviceSubClass': dev_sub,
        'bDeviceProtocol': dev_proto,
        'bMaxPacketSize0': mps0,
        'idVendor': vid,
        'idProduct': pid,
        'bcdDevice': bcd_dev,
        'iManufacturer': i_mfg,
        'iProduct': i_prod,
        'iSerialNumber': i_ser,
        'bNumConfigurations': num_cfg,
    }


def parse_usb_config_topology(raw_or_hex: bytes | bytearray | str) -> dict:
    """Parse and strictly validate a complete USB Configuration Descriptor blob."""
    raw = _as_bytes(raw_or_hex)
    entries = parse_usb_configuration_descriptor(raw)
    if (len(raw) < 9 or raw[0] != 9 or raw[1] != 0x02 or
            int.from_bytes(raw[2:4], 'little') != len(raw) or
            sum(item[1] for item in entries) != len(raw)):
        raise ValueError('Malformed or truncated USB CONFIGURATION descriptor')

    config_hdr = {
        'wTotalLength': int.from_bytes(raw[2:4], 'little'),
        'bNumInterfaces': raw[4],
        'bConfigurationValue': raw[5],
        'bmAttributes': raw[7],
        'bMaxPower': raw[8],
    }
    interfaces: list[dict] = []
    iads: list[dict] = []
    current_intf: dict | None = None

    for offset, length, dtype, blob in entries[1:]:
        if dtype == 0x0B:  # Interface Association Descriptor
            if length != 8:
                raise ValueError(f'Malformed IAD length {length} at offset {offset}')
            iads.append({
                'offset': offset,
                'bFirstInterface': blob[2],
                'bInterfaceCount': blob[3],
                'bFunctionClass': blob[4],
                'bFunctionSubClass': blob[5],
                'bFunctionProtocol': blob[6],
                'iFunction': blob[7],
            })
        elif dtype == 0x04:  # Interface
            if length != 9:
                raise ValueError(f'Malformed INTERFACE descriptor length {length}')
            current_intf = {
                'offset': offset,
                'bInterfaceNumber': blob[2],
                'bAlternateSetting': blob[3],
                'bNumEndpoints': blob[4],
                'bInterfaceClass': blob[5],
                'bInterfaceSubClass': blob[6],
                'bInterfaceProtocol': blob[7],
                'iInterface': blob[8],
                'endpoints': [],
                'hid_descriptors': [],
                'class_descriptors': [],
            }
            interfaces.append(current_intf)
        elif dtype == 0x05:  # Endpoint
            if length < 7 or current_intf is None:
                raise ValueError('Orphan or truncated ENDPOINT descriptor')
            addr = blob[2]
            attrs = blob[3]
            mps = int.from_bytes(blob[4:6], 'little')
            current_intf['endpoints'].append({
                'bEndpointAddress': addr,
                'bmAttributes': attrs,
                'transfer_type': attrs & 0x03,
                'wMaxPacketSize': mps,
                'bInterval': blob[6],
                'dwc3_ep_idx': usb_ep_addr_to_dwc3_idx(addr),
            })
        elif dtype == 0x21:  # HID descriptor
            if length < 9 or current_intf is None:
                raise ValueError('Orphan or truncated HID descriptor')
            current_intf['hid_descriptors'].append({
                'bcdHID': int.from_bytes(blob[2:4], 'little'),
                'bCountryCode': blob[4],
                'bNumDescriptors': blob[5],
                'bReportDescriptorType': blob[6],
                'wDescriptorLength': int.from_bytes(blob[7:9], 'little'),
            })
        elif current_intf is not None:
            current_intf['class_descriptors'].append({
                'offset': offset,
                'type': dtype,
                'bytes': blob,
            })

    for intf in interfaces:
        if len(intf['endpoints']) != intf['bNumEndpoints']:
            raise ValueError(
                f'Interface {intf["bInterfaceNumber"]} declared '
                f'{intf["bNumEndpoints"]} endpoints, found {len(intf["endpoints"])}'
            )

    return {
        'header': config_hdr,
        'iads': iads,
        'interfaces': interfaces,
    }


def extract_embedded_jpeg(payload: bytes) -> dict:
    """Locate and validate a JPEG frame (``FFD8..FFD9`` + optional ``SOF0``)."""
    soi = payload.find(b'\xff\xd8')
    if soi < 0:
        raise ValueError('Missing JPEG SOI marker (FFD8)')
    eoi = payload.rfind(b'\xff\xd9')
    if eoi <= soi:
        raise ValueError('Missing or out-of-order JPEG EOI marker (FFD9)')
    jpeg = payload[soi:eoi + 2]
    width = 0
    height = 0
    cursor = 2
    while cursor + 4 <= len(jpeg) - 2:
        if jpeg[cursor] != 0xFF:
            cursor += 1
            continue
        while cursor < len(jpeg) and jpeg[cursor] == 0xFF:
            cursor += 1
        if cursor >= len(jpeg):
            break
        marker = jpeg[cursor]
        cursor += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x00:
            continue
        if cursor + 2 > len(jpeg):
            break
        seg_len = int.from_bytes(jpeg[cursor:cursor + 2], 'big')
        if seg_len < 2 or cursor + seg_len > len(jpeg):
            break
        if marker in (0xC0, 0xC1, 0xC2) and seg_len >= 7:
            height, width = struct.unpack('>HH', jpeg[cursor + 3:cursor + 7])
            break
        if marker == 0xDA:  # Start of Scan; entropy-coded data follows
            break
        cursor += seg_len
    return {
        'jpeg_offset': soi,
        'jpeg_bytes': len(jpeg),
        'jpeg': jpeg,
        'width': width,
        'height': height,
        'jpeg_sha256': hashlib.sha256(jpeg).hexdigest(),
    }


def _is_stall_exception(exc: RuntimeError) -> int | None:
    text = str(exc)
    if ': STALL ' not in text:
        return None
    tail = text.rsplit(': STALL ', 1)[1].strip()
    return int(tail) if tail.isdigit() else None


class MscBotClient:
    """Sony USB Mass Storage Bulk-Only Transport client (``054C:0CA8`` / ``054C:0D9D``)."""

    def __init__(self, host: Ep0Host, enumeration: dict | None = None, *,
                 allowed_pids: tuple[int, ...] = MSC_PIDS):
        self.host = host
        self.allowed_pids = tuple(allowed_pids)
        self.ep_in_addr = 0x81
        self.ep_out_addr = 0x02
        self.ep_in = 3
        self.ep_out = 4
        self.max_packet = 512
        self.interface_number = 0
        self.next_tag = 1
        self.descriptors: dict | None = None
        if enumeration is not None:
            self.validate_descriptors(enumeration)

    def enumerate(self) -> dict:
        enumeration = self.host.enumerate_device()
        self.validate_descriptors(enumeration)
        return enumeration

    def validate_descriptors(self, enumeration: dict) -> dict:
        dev = parse_usb_device_descriptor(enumeration['device_hex'])
        if dev['idVendor'] != SONY_VID or dev['idProduct'] not in self.allowed_pids:
            raise ValueError(
                f'Expected Sony MSC VID:PID in {[hex(p) for p in self.allowed_pids]}, '
                f'got {dev["idVendor"]:04x}:{dev["idProduct"]:04x}'
            )
        if (dev['bDeviceClass'], dev['bDeviceSubClass'], dev['bDeviceProtocol']) not in (
            (0x00, 0x00, 0x00),
            (0x08, 0x06, 0x50),
        ):
            raise ValueError('Unexpected USB device class triple for MSC BOT')

        topo = parse_usb_config_topology(enumeration['configuration_hex'])
        msc_intfs = [
            i for i in topo['interfaces']
            if i['bAlternateSetting'] == 0
            and (i['bInterfaceClass'], i['bInterfaceSubClass'], i['bInterfaceProtocol'])
            == (0x08, 0x06, 0x50)
        ]
        if len(msc_intfs) != 1:
            raise ValueError('Expected exactly one MSC 08/06/50 Bulk-Only interface')
        intf = msc_intfs[0]
        bulk_eps = [ep for ep in intf['endpoints'] if ep['transfer_type'] == 2]
        in_eps = [ep for ep in bulk_eps if ep['bEndpointAddress'] & 0x80]
        out_eps = [ep for ep in bulk_eps if not (ep['bEndpointAddress'] & 0x80)]
        if len(in_eps) != 1 or len(out_eps) != 1:
            raise ValueError('MSC BOT interface requires exactly one Bulk-IN and one Bulk-OUT endpoint')
        if (in_eps[0]['bEndpointAddress'] != 0x81 or out_eps[0]['bEndpointAddress'] != 0x02 or
                in_eps[0]['wMaxPacketSize'] != 512 or out_eps[0]['wMaxPacketSize'] != 512):
            raise ValueError('Expected Sony MSC endpoints 0x81 IN (512B) and 0x02 OUT (512B)')

        self.interface_number = intf['bInterfaceNumber']
        self.ep_in_addr = in_eps[0]['bEndpointAddress']
        self.ep_out_addr = out_eps[0]['bEndpointAddress']
        self.ep_in = in_eps[0]['dwc3_ep_idx']
        self.ep_out = out_eps[0]['dwc3_ep_idx']
        self.max_packet = in_eps[0]['wMaxPacketSize']
        self.descriptors = {'device': dev, 'topology': topo}
        return self.descriptors

    def get_max_lun(self, interface: int | None = None) -> int:
        intf = self.interface_number if interface is None else interface
        setup = struct.pack('<BBHHH', 0xA1, 0xFE, 0, intf, 1).hex()
        reply = self.host.control(setup)
        if len(reply) != 1 or reply[0] > 15:
            raise ValueError(f'Invalid GET_MAX_LUN response: {reply.hex()}')
        return reply[0]

    def bulk_only_reset(self, interface: int | None = None) -> None:
        intf = self.interface_number if interface is None else interface
        setup = struct.pack('<BBHHH', 0x21, 0xFF, 0, intf, 0).hex()
        self.host.control(setup)

    def recover_from_stall(self) -> None:
        """Execute USB MSC BOT 1.0 section 5.3.4 Reset Recovery (BOMSR + ClearFeature HALT)."""
        self.bulk_only_reset()
        self.host.clear_halt(self.ep_in_addr)
        self.host.clear_halt(self.ep_out_addr)

    def scsi_command(self, cdb: bytes, data_in_len: int = 0, data_out: bytes = b'', *,
                     lun: int = 0, tag: int | None = None) -> dict:
        raw_cdb = bytes(cdb)
        raw_out = bytes(data_out)
        if not 1 <= len(raw_cdb) <= 16:
            raise ValueError('SCSI CDB length must be 1..16 bytes')
        if not 0 <= lun <= 15:
            raise ValueError('MSC LUN must be 0..15')
        if data_in_len < 0 or (data_in_len > 0 and raw_out):
            raise ValueError('Specify either data_in_len >= 0 or data_out, not both')

        if tag is None:
            tag = self.next_tag
            self.next_tag = (self.next_tag + 1) & 0xFFFFFFFF or 1

        xfer_len = data_in_len if data_in_len > 0 else len(raw_out)
        flags = 0x80 if data_in_len > 0 else 0x00
        cbw = CBW_STRUCT.pack(
            CBW_SIGNATURE,
            tag,
            xfer_len,
            flags,
            lun & 0x0F,
            len(raw_cdb),
            raw_cdb.ljust(16, b'\x00'),
        )

        try:
            self.host.bulk_out(self.ep_out, cbw)
        except RuntimeError as exc:
            stalled_ep = _is_stall_exception(exc)
            if stalled_ep is not None:
                raise MscStallError(str(exc), ep_idx=stalled_ep, tag=tag) from exc
            raise

        rx_data = bytearray()
        tx_bytes = 0
        try:
            if raw_out:
                tx_bytes = self.host.bulk_out_transfer(
                    self.ep_out, raw_out, max_packet=self.max_packet
                )
            elif data_in_len > 0:
                while len(rx_data) < data_in_len:
                    chunk = self.host.bulk_in(self.ep_in)
                    if len(chunk) > data_in_len - len(rx_data):
                        raise ValueError('MSC Data-IN exceeded requested transfer length')
                    rx_data.extend(chunk)
                    if len(chunk) < self.max_packet:
                        break

            csw = self.host.bulk_in(self.ep_in)
        except RuntimeError as exc:
            stalled_ep = _is_stall_exception(exc)
            if stalled_ep is not None:
                raise MscStallError(str(exc), ep_idx=stalled_ep, tag=tag) from exc
            raise

        if len(csw) != CSW_STRUCT.size:
            raise ValueError(f'Expected 13-byte CSW, got {len(csw)} bytes')
        sig, rx_tag, residue, status = CSW_STRUCT.unpack(csw)
        if sig != CSW_SIGNATURE:
            raise ValueError(f'Invalid CSW signature: 0x{sig:08x}')
        if rx_tag != tag:
            raise ValueError(f'CSW tag mismatch: expected 0x{tag:08x}, got 0x{rx_tag:08x}')
        if status == 2:
            raise MscPhaseError(f'MSC Phase Error on tag 0x{tag:08x}', tag=tag, residue=residue)
        if status not in (0, 1):
            raise ValueError(f'Reserved CSW status value: {status}')
        if residue > xfer_len:
            raise ValueError(f'CSW residue {residue} exceeds transfer length {xfer_len}')
        actual = len(rx_data) if data_in_len > 0 else tx_bytes
        if status == 0 and residue != xfer_len - actual:
            raise ValueError(
                f'CSW status OK residue mismatch: residue={residue}, '
                f'expected={xfer_len - actual}'
            )

        return {
            'tag': tag,
            'status': status,
            'residue': residue,
            'data': bytes(rx_data),
        }

    def test_unit_ready(self, *, lun: int = 0) -> bool:
        res = self.scsi_command(bytes(6), data_in_len=0, lun=lun)
        return res['status'] == 0

    def inquiry(self, *, lun: int = 0, alloc_len: int = 36) -> dict:
        if alloc_len < 36 or alloc_len > 255:
            raise ValueError('INQUIRY allocation length must be 36..255')
        cdb = struct.pack('BBBBBB', 0x12, 0x00, 0x00, 0x00, alloc_len, 0x00)
        res = self.scsi_command(cdb, data_in_len=alloc_len, lun=lun)
        if res['status'] != 0 or len(res['data']) < 36:
            raise MscCommandFailedError(
                'INQUIRY failed or returned short payload',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        raw = res['data']
        vendor = raw[8:16].decode('ascii', errors='strict').rstrip(' \x00')
        product = raw[16:32].decode('ascii', errors='strict').rstrip(' \x00')
        revision = raw[32:36].decode('ascii', errors='strict').rstrip(' \x00')
        if vendor.upper() != 'SONY' or product != 'DSC':
            raise ValueError(f'INQUIRY does not identify Sony DSC: {vendor!r} / {product!r}')
        return {
            'device_type': raw[0] & 0x1F,
            'removable': bool(raw[1] & 0x80),
            'vendor': vendor,
            'product': product,
            'revision': revision,
            'raw': raw,
        }

    def read_capacity_10(self, *, lun: int = 0) -> dict:
        res = self.scsi_command(b'\x25' + bytes(9), data_in_len=8, lun=lun)
        if res['status'] != 0 or len(res['data']) != 8:
            raise MscCommandFailedError(
                'READ CAPACITY(10) failed',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        last_lba, block_size = struct.unpack('>II', res['data'])
        if block_size != 512:
            raise ValueError(f'Expected 512-byte sectors, got {block_size}')
        block_count = last_lba + 1
        return {
            'last_lba': last_lba,
            'block_count': block_count,
            'block_size': block_size,
            'capacity_bytes': block_count * block_size,
        }

    def mode_sense_6(self, page_code: int = 0x3F, *, lun: int = 0, alloc_len: int = 4) -> dict:
        if not 4 <= alloc_len <= 255:
            raise ValueError('MODE SENSE(6) allocation length must be 4..255')
        cdb = struct.pack('BBBBBB', 0x1A, 0x00, page_code & 0x3F, 0x00, alloc_len, 0x00)
        res = self.scsi_command(cdb, data_in_len=alloc_len, lun=lun)
        if res['status'] != 0 or len(res['data']) < 4:
            raise MscCommandFailedError(
                'MODE SENSE(6) failed',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        raw = res['data']
        return {
            'mode_data_length': raw[0],
            'medium_type': raw[1],
            'device_specific': raw[2],
            'write_protected': bool(raw[2] & 0x80),
            'block_descriptor_length': raw[3],
            'raw': raw,
        }

    def request_sense(self, *, lun: int = 0, alloc_len: int = 18) -> dict:
        if not 18 <= alloc_len <= 252:
            raise ValueError('REQUEST SENSE allocation length must be 18..252')
        cdb = struct.pack('BBBBBB', 0x03, 0x00, 0x00, 0x00, alloc_len, 0x00)
        res = self.scsi_command(cdb, data_in_len=alloc_len, lun=lun)
        if res['status'] != 0 or len(res['data']) < 18:
            raise MscCommandFailedError(
                'REQUEST SENSE failed',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        raw = res['data']
        resp_code = raw[0] & 0x7F
        if resp_code not in (0x70, 0x71):
            raise ValueError(f'Unexpected SCSI sense response code: 0x{resp_code:02x}')
        sense_key = raw[2] & 0x0F
        asc = raw[12]
        ascq = raw[13]
        return {
            'response_code': resp_code,
            'sense_key': sense_key,
            'asc': asc,
            'ascq': ascq,
            'data_protect': sense_key == 0x07,
            'raw': raw,
        }

    def read_10(self, lba: int, blocks: int, *, lun: int = 0, block_size: int = 512) -> bytes:
        if lba < 0 or not 1 <= blocks <= 0xFFFF:
            raise ValueError('Invalid READ(10) LBA or block count')
        cdb = struct.pack('>BBIBHB', 0x28, 0x00, lba, 0x00, blocks, 0x00)
        expected = blocks * block_size
        res = self.scsi_command(cdb, data_in_len=expected, lun=lun)
        if res['status'] != 0:
            raise MscCommandFailedError(
                f'READ(10) failed with CSW status={res["status"]}',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        if len(res['data']) != expected:
            raise ValueError(f'Short READ(10): expected {expected} bytes, got {len(res["data"])}')
        return res['data']

    def write_10(self, lba: int, data: bytes, *, lun: int = 0, block_size: int = 512,
                 check: bool = True) -> dict:
        raw = bytes(data)
        if lba < 0 or not raw or len(raw) % block_size != 0:
            raise ValueError('WRITE(10) payload must be a positive multiple of block_size')
        blocks = len(raw) // block_size
        if blocks > 0xFFFF:
            raise ValueError('WRITE(10) block count exceeds uint16')
        cdb = struct.pack('>BBIBHB', 0x2A, 0x00, lba, 0x00, blocks, 0x00)
        res = self.scsi_command(cdb, data_out=raw, lun=lun)
        if check and res['status'] != 0:
            raise MscCommandFailedError(
                f'WRITE(10) failed with CSW status={res["status"]}',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        return {
            'tag': res['tag'],
            'status': res['status'],
            'residue': res['residue'],
            'blocks': blocks,
            'bytes_written': len(raw) if res['status'] == 0 else 0,
        }

    def sony_ext_model_info(self, *, opcode: int = 0xFF, subcommand: int = 0x01,
                            alloc_len: int = 512, expected_model: str | None = 'ILCE-6400',
                            lun: int = 0) -> dict:
        """Execute read-only Sony vendor model-info query (CDB ``0xFF`` / ``0x7A``)."""
        if opcode not in (0xFF, 0x7A) or not 64 <= alloc_len <= 8192:
            raise ValueError('Unsupported vendor opcode or allocation length')
        cdb = bytes([opcode, subcommand & 0xFF]) + bytes(10)
        res = self.scsi_command(cdb, data_in_len=alloc_len, lun=lun)
        if res['status'] != 0:
            raise MscCommandFailedError(
                f'Sony vendor CDB 0x{opcode:02x} failed with CSW status={res["status"]}',
                status=res['status'], residue=res['residue'], tag=res['tag'],
            )
        raw = res['data']
        if len(raw) < 16:
            raise ValueError('Short Sony external-command header')
        size, command, direction = struct.unpack_from('<IHH', raw, 0)
        if command != 1 or direction != 1:
            raise ValueError('Unexpected Sony model-info reply command/direction')
        if not 13 <= size <= len(raw) - 16:
            raise ValueError('Invalid Sony model-info payload extent')
        payload = raw[16:16 + size]
        plist_size = struct.unpack_from('<I', payload, 0)[0]
        offset = 4 + plist_size + 4
        if offset >= len(payload):
            raise ValueError('Truncated Sony model-info metadata')
        model_len = payload[offset]
        offset += 1
        if not model_len or offset + model_len + 5 + 4 > len(payload):
            raise ValueError('Truncated Sony model-info identity')
        model = payload[offset:offset + model_len].decode('ascii')
        if expected_model is not None and model != expected_model:
            raise ValueError(f'Model identity mismatch: expected {expected_model!r}, got {model!r}')
        product_code = payload[offset + model_len:offset + model_len + 5]
        return {
            'opcode': opcode,
            'model': model,
            'product_code_wire_hex': product_code.hex(),
            'payload_bytes': size,
            'response_bytes': len(raw),
            'reply_command': command,
            'reply_direction': direction,
        }


class PcRemotePtpClient:
    """Sony PC Remote Still Image / PTP client (``054C:0CAA``, ``06/01/01``)."""

    def __init__(self, host: Ep0Host, enumeration: dict | None = None, *,
                 allowed_pids: tuple[int, ...] = PTP_PIDS, interface_number: int = 0):
        self.host = host
        self.allowed_pids = tuple(allowed_pids)
        self.interface_number = interface_number
        self.ep_in = 3
        self.ep_out = 4
        self.ep_intr = 7
        self.max_packet = 512
        self.intr_max_packet = 32
        self.session_open = False
        self.session_id = 0
        self._next_tx_id = 0
        self.descriptors: dict | None = None
        if enumeration is not None:
            self.validate_descriptors(enumeration)

    def enumerate(self) -> dict:
        enumeration = self.host.enumerate_device()
        self.validate_descriptors(enumeration)
        return enumeration

    def validate_descriptors(self, enumeration: dict) -> dict:
        dev = parse_usb_device_descriptor(enumeration['device_hex'])
        if dev['idVendor'] != SONY_VID or dev['idProduct'] not in self.allowed_pids:
            raise ValueError(
                f'Expected Sony PTP VID:PID in {[hex(p) for p in self.allowed_pids]}, '
                f'got {dev["idVendor"]:04x}:{dev["idProduct"]:04x}'
            )
        topo = parse_usb_config_topology(enumeration['configuration_hex'])
        matches = [
            i for i in topo['interfaces']
            if i['bInterfaceNumber'] == self.interface_number
            and i['bAlternateSetting'] == 0
            and (i['bInterfaceClass'], i['bInterfaceSubClass'], i['bInterfaceProtocol'])
            == (0x06, 0x01, 0x01)
        ]
        if len(matches) != 1:
            raise ValueError(
                f'Expected Still Image interface {self.interface_number} (06/01/01)'
            )
        intf = matches[0]
        bulk_in = [
            ep for ep in intf['endpoints']
            if ep['transfer_type'] == 2 and (ep['bEndpointAddress'] & 0x80)
        ]
        bulk_out = [
            ep for ep in intf['endpoints']
            if ep['transfer_type'] == 2 and not (ep['bEndpointAddress'] & 0x80)
        ]
        intr_in = [
            ep for ep in intf['endpoints']
            if ep['transfer_type'] == 3 and (ep['bEndpointAddress'] & 0x80)
        ]
        if len(bulk_in) != 1 or len(bulk_out) != 1 or len(intr_in) != 1:
            raise ValueError('Sony PTP interface requires Bulk-IN, Bulk-OUT, and Interrupt-IN')
        if (bulk_in[0]['bEndpointAddress'] != 0x81 or bulk_in[0]['wMaxPacketSize'] != 512 or
                bulk_out[0]['bEndpointAddress'] != 0x02 or bulk_out[0]['wMaxPacketSize'] != 512 or
                intr_in[0]['bEndpointAddress'] != 0x83 or intr_in[0]['wMaxPacketSize'] != 32):
            raise ValueError(
                'Expected Sony PTP endpoints 0x81 (512B), 0x02 (512B), 0x83 (32B)'
            )
        self.ep_in = bulk_in[0]['dwc3_ep_idx']
        self.ep_out = bulk_out[0]['dwc3_ep_idx']
        self.ep_intr = intr_in[0]['dwc3_ep_idx']
        self.max_packet = bulk_in[0]['wMaxPacketSize']
        self.intr_max_packet = intr_in[0]['wMaxPacketSize']
        self.descriptors = {'device': dev, 'topology': topo, 'interface': intf}
        return self.descriptors

    def get_device_status(self, *, max_length: int = 4) -> dict:
        """Issue Still Image class ``GET_DEVICE_STATUS`` (``0xA1 0x67``) on EP0."""
        if not 4 <= max_length <= 64:
            raise ValueError('GET_DEVICE_STATUS max_length must be 4..64')
        setup = struct.pack('<BBHHH', 0xA1, 0x67, 0, self.interface_number, max_length).hex()
        raw = self.host.control(setup)
        if len(raw) < 4:
            raise ValueError('Truncated Still Image Device Status block')
        length, code = struct.unpack_from('<HH', raw, 0)
        if length != len(raw) or (length - 4) % 4 != 0:
            raise ValueError('Malformed Still Image Device Status length')
        endpoints = tuple(ep for (ep,) in struct.iter_unpack('<I', raw[4:]))
        return {
            'length': length,
            'code': code,
            'ok': code == PTP_RC_OK,
            'busy': code == PTP_RC_DEVICE_BUSY,
            'endpoints': endpoints,
        }

    def cancel_transaction(self, tx_id: int) -> None:
        """Issue Still Image class ``CANCEL`` (``0x21 0x64``) with 6-byte ``0x4001`` payload."""
        if type(tx_id) is not int or not 0 <= tx_id <= 0xFFFFFFFF:
            raise ValueError('tx_id must be a uint32 value')
        setup = struct.pack('<BBHHH', 0x21, 0x64, 0, self.interface_number, 6).hex()
        payload = struct.pack('<HI', 0x4001, tx_id)
        self.host.control(setup, data_out=payload)

    def _read_data_container(self, expected_opcode: int, expected_tx: int) -> tuple[bytes, int]:
        first = self.host.bulk_in(self.ep_in)
        if len(first) < PTP_HEADER.size:
            raise ValueError('Truncated PTP Data container header')
        total_len, kind, code, rx_tx = PTP_HEADER.unpack_from(first, 0)
        if (kind != PTP_CONTAINER_DATA or code != expected_opcode or rx_tx != expected_tx or
                not PTP_HEADER.size <= total_len <= PTP_MAX_DATA_BYTES or
                len(first) > total_len):
            raise ValueError(
                f'PTP Data container mismatch: len={total_len}, kind={kind}, '
                f'code=0x{code:04x}, tx={rx_tx} (expected opcode=0x{expected_opcode:04x}, '
                f'tx={expected_tx})'
            )
        raw = bytearray(first)
        fragments = 1
        for _ in range(PTP_MAX_FRAGMENTS):
            if len(raw) == total_len:
                return bytes(raw[PTP_HEADER.size:]), fragments
            chunk = self.host.bulk_in(self.ep_in)
            if not chunk or len(chunk) > total_len - len(raw):
                raise ValueError('Truncated or oversized PTP Data fragment')
            raw.extend(chunk)
            fragments += 1
        raise ValueError('PTP Data fragment budget exhausted')

    def _read_response_container(self, opcode: int, expected_tx: int) -> dict:
        raw = self.host.bulk_in(self.ep_in)
        if len(raw) < PTP_HEADER.size:
            raise ValueError('Truncated PTP Response container')
        total_len, kind, resp_code, rx_tx = PTP_HEADER.unpack_from(raw, 0)
        if (total_len != len(raw) or (total_len - PTP_HEADER.size) % 4 != 0 or
                (total_len - PTP_HEADER.size) // 4 > 5 or
                kind != PTP_CONTAINER_RESPONSE or rx_tx != expected_tx):
            raise ValueError(
                f'PTP Response container mismatch: len={total_len}/{len(raw)}, '
                f'kind={kind}, code=0x{resp_code:04x}, tx={rx_tx} (expected tx={expected_tx})'
            )
        params = tuple(p for (p,) in struct.iter_unpack('<I', raw[PTP_HEADER.size:]))
        if resp_code != PTP_RC_OK:
            raise PtpResponseError(
                resp_code, opcode=opcode, transaction_id=expected_tx, params=params
            )
        return {
            'response_code': resp_code,
            'transaction_id': rx_tx,
            'params': params,
        }

    def transact(self, opcode: int, params: tuple[int, ...] = (), *,
                 data_out: bytes | None = None, expect_data: bool = False,
                 transaction_id: int | None = None) -> dict:
        if len(params) > 5:
            raise ValueError('PTP Command container supports at most 5 uint32 parameters')
        if data_out is not None and expect_data:
            raise ValueError('PTP operation cannot have both Data-OUT and Data-IN')
        if transaction_id is None:
            tx_id = self._next_tx_id
            self._next_tx_id = (self._next_tx_id + 1) & 0xFFFFFFFF
        else:
            tx_id = transaction_id

        cmd_body = b''.join(struct.pack('<I', p & 0xFFFFFFFF) for p in params)
        cmd = PTP_HEADER.pack(PTP_HEADER.size + len(cmd_body), PTP_CONTAINER_COMMAND,
                              opcode, tx_id) + cmd_body
        self.host.bulk_out(self.ep_out, cmd)

        if data_out is not None:
            raw_out = bytes(data_out)
            out_container = PTP_HEADER.pack(
                PTP_HEADER.size + len(raw_out), PTP_CONTAINER_DATA, opcode, tx_id
            ) + raw_out
            self.host.bulk_out_transfer(self.ep_out, out_container, max_packet=self.max_packet)

        payload = b''
        fragments = 0
        if expect_data:
            payload, fragments = self._read_data_container(opcode, tx_id)

        resp = self._read_response_container(opcode, tx_id)
        return {
            'opcode': opcode,
            'transaction_id': tx_id,
            'data': payload,
            'fragments': fragments,
            'response_code': resp['response_code'],
            'response_params': resp['params'],
        }

    def get_device_info(self) -> dict:
        res = self.transact(PTP_OC_GET_DEVICE_INFO, expect_data=True)
        info = parse_device_info(res['data'])
        return {
            **info,
            'payload_bytes': len(res['data']),
            'fragments': res['fragments'],
            'transaction_id': res['transaction_id'],
            'response_code': res['response_code'],
        }

    def open_session(self, session_id: int = 1) -> dict:
        if type(session_id) is not int or session_id <= 0 or session_id > 0xFFFFFFFF:
            raise ValueError('PTP session_id must be a non-zero uint32')
        res = self.transact(PTP_OC_OPEN_SESSION, (session_id,))
        self.session_open = True
        self.session_id = session_id
        return {'session_id': session_id, 'transaction_id': res['transaction_id']}

    def close_session(self) -> dict:
        res = self.transact(PTP_OC_CLOSE_SESSION)
        self.session_open = False
        return {'transaction_id': res['transaction_id']}

    def get_storage_ids(self) -> list[int]:
        res = self.transact(PTP_OC_GET_STORAGE_IDS, expect_data=True)
        raw = res['data']
        if len(raw) < 4:
            raise ValueError('Truncated PTP StorageIDArray')
        count = struct.unpack_from('<I', raw, 0)[0]
        if len(raw) != 4 + 4 * count:
            raise ValueError('PTP StorageIDArray length mismatch')
        return [sid for (sid,) in struct.iter_unpack('<I', raw[4:])]

    def sdio_connect(self, phase: int, code1: int = 0, code2: int = 0) -> dict:
        if phase not in (1, 2, 3):
            raise ValueError(f'Sony SDIO_Connect phase must be 1, 2, or 3, got {phase!r}')
        res = self.transact(PTP_OC_SDIO_CONNECT, (phase, code1, code2))
        return {
            'phase': phase,
            'transaction_id': res['transaction_id'],
            'response_code': res['response_code'],
        }

    def sdio_get_ext_device_info(self, initiator_version: int = 0x012C) -> dict:
        if type(initiator_version) is not int or not 0 < initiator_version <= 0xFFFF:
            raise ValueError('initiator_version must be a non-zero uint16')
        res = self.transact(
            PTP_OC_SDIO_GET_EXT_DEVICE_INFO, (initiator_version,), expect_data=True
        )
        raw = res['data']
        if len(raw) < 6:
            raise ValueError('Truncated Sony SDIO_GetExtDeviceInfo dataset')
        proto_version, prop_count = struct.unpack_from('<HI', raw, 0)
        if len(raw) < 6 + 2 * prop_count:
            raise ValueError('Truncated Sony SDIO property code table')
        props = tuple(
            code for (code,) in struct.iter_unpack('<H', raw[6:6 + 2 * prop_count])
        )
        return {
            'protocol_version': proto_version,
            'supported_properties': props,
            'payload_bytes': len(raw),
            'transaction_id': res['transaction_id'],
        }

    def get_liveview_object(self, handle: int = SONY_HANDLE_LIVEVIEW) -> dict:
        res = self.transact(PTP_OC_GET_OBJECT, (handle,), expect_data=True)
        jpeg_info = extract_embedded_jpeg(res['data'])
        return {
            'handle': handle,
            'object_bytes': len(res['data']),
            'fragments': res['fragments'],
            'transaction_id': res['transaction_id'],
            **jpeg_info,
        }

    def sdio_control_device(self, control_code: int, value: int) -> dict:
        if (type(control_code) is not int or not 0 <= control_code <= 0xFFFF or
                type(value) is not int or not 0 <= value <= 0xFFFF):
            raise ValueError('control_code and value must be uint16 values')
        res = self.transact(
            PTP_OC_SDIO_CONTROL_DEVICE,
            (control_code,),
            data_out=struct.pack('<H', value),
        )
        return {
            'control_code': control_code,
            'value': value,
            'transaction_id': res['transaction_id'],
            'response_code': res['response_code'],
        }

    def poll_event(self, *, retries: int = 200) -> dict:
        """Read and validate one PTP Event container (type 4) on Interrupt-IN ``0x83``."""
        raw = self.host.interrupt_in(self.ep_intr, retries=retries)
        if len(raw) < PTP_HEADER.size:
            raise ValueError('Truncated PTP Event container')
        total_len, kind, event_code, tx_id = PTP_HEADER.unpack_from(raw, 0)
        if (total_len != len(raw) or kind != PTP_CONTAINER_EVENT or
                (total_len - PTP_HEADER.size) % 4 != 0 or
                (total_len - PTP_HEADER.size) // 4 > 3):
            raise ValueError(
                f'Malformed PTP Event container: len={total_len}/{len(raw)}, '
                f'kind={kind}, code=0x{event_code:04x}'
            )
        params = tuple(p for (p,) in struct.iter_unpack('<I', raw[PTP_HEADER.size:]))
        return {
            'code': event_code,
            'name': PTP_EVENT_NAMES.get(event_code, f'0x{event_code:04x}'),
            'transaction_id': tx_id,
            'params': params,
            'raw': raw,
        }


class CompositeUvcPtpClient:
    """Composite UVC 1.1 + Sony PTP client (``054C:0322``, ``EF/02/01``; E0258, E0347)."""

    def __init__(self, host: Ep0Host, enumeration: dict | None = None):
        self.host = host
        self.vc_interface = 1
        self.vs_interface = 2
        self.ep_uvc_addr = 0x84
        self.ep_uvc_in = 9
        self.uvc_max_packet = 512
        self.ptp: PcRemotePtpClient | None = None
        self.descriptors: dict | None = None
        self._last_fid: int | None = None
        if enumeration is not None:
            self.validate_descriptors(enumeration)

    def enumerate(self) -> dict:
        enumeration = self.host.enumerate_device()
        self.validate_descriptors(enumeration)
        return enumeration

    def validate_descriptors(self, enumeration: dict) -> dict:
        dev = parse_usb_device_descriptor(enumeration['device_hex'])
        if (dev['idVendor'] != SONY_VID or dev['idProduct'] != COMPOSITE_UVC_PTP_PID or
                (dev['bDeviceClass'], dev['bDeviceSubClass'], dev['bDeviceProtocol'])
                != (0xEF, 0x02, 0x01)):
            raise ValueError(
                'Expected Composite UVC+PTP device 054C:0322 with IAD class EF/02/01'
            )

        topo = parse_usb_config_topology(enumeration['configuration_hex'])
        if topo['header']['bNumInterfaces'] != 3:
            raise ValueError(
                f'Expected 3-interface composite configuration, got '
                f'{topo["header"]["bNumInterfaces"]}'
            )
        uvc_iads = [
            iad for iad in topo['iads']
            if (iad['bFirstInterface'], iad['bInterfaceCount'],
                iad['bFunctionClass'], iad['bFunctionSubClass'])
            == (1, 2, 0x0E, 0x03)
        ]
        if len(uvc_iads) != 1:
            raise ValueError('Missing UVC Video Interface Collection IAD (first=1, count=2)')

        by_num = {
            (i['bInterfaceNumber'], i['bAlternateSetting']): i
            for i in topo['interfaces']
        }
        if (1, 0) not in by_num or (2, 0) not in by_num:
            raise ValueError('Composite configuration lacks UVC VC (1) or VS (2) interface')
        vc = by_num[(1, 0)]
        vs = by_num[(2, 0)]
        if (vc['bInterfaceClass'], vc['bInterfaceSubClass']) != (0x0E, 0x01):
            raise ValueError('Interface 1 is not UVC VideoControl (0E/01)')
        if (vs['bInterfaceClass'], vs['bInterfaceSubClass']) != (0x0E, 0x02):
            raise ValueError('Interface 2 is not UVC VideoStreaming (0E/02)')
        if not vc['class_descriptors'] or not vs['class_descriptors']:
            raise ValueError('UVC VC/VS class-specific descriptors are missing')

        vs_bulk_in = [
            ep for ep in vs['endpoints']
            if ep['transfer_type'] == 2 and (ep['bEndpointAddress'] & 0x80)
        ]
        if (len(vs_bulk_in) != 1 or vs_bulk_in[0]['bEndpointAddress'] != 0x84 or
                vs_bulk_in[0]['wMaxPacketSize'] != 512):
            raise ValueError('Expected UVC VS Bulk-IN endpoint 0x84 (512B)')

        self.ptp = PcRemotePtpClient(
            self.host, enumeration, allowed_pids=(COMPOSITE_UVC_PTP_PID,), interface_number=0
        )
        self.ep_uvc_addr = vs_bulk_in[0]['bEndpointAddress']
        self.ep_uvc_in = vs_bulk_in[0]['dwc3_ep_idx']
        self.uvc_max_packet = vs_bulk_in[0]['wMaxPacketSize']
        self.descriptors = {'device': dev, 'topology': topo}
        return self.descriptors

    @staticmethod
    def pack_streaming_control(*, bm_hint: int = 1, format_index: int = 1,
                               frame_index: int = 1, frame_interval: int = 333333,
                               key_frame_rate: int = 0, p_frame_rate: int = 0,
                               comp_quality: int = 0, comp_window_size: int = 0,
                               delay: int = 0, max_video_frame_size: int = 0,
                               max_payload_transfer_size: int = 0) -> bytes:
        return UVC_STREAM_CTRL_STRUCT.pack(
            bm_hint,
            format_index,
            frame_index,
            frame_interval,
            key_frame_rate,
            p_frame_rate,
            comp_quality,
            comp_window_size,
            delay,
            max_video_frame_size,
            max_payload_transfer_size,
        )

    @staticmethod
    def parse_streaming_control(raw: bytes) -> dict:
        if len(raw) != UVC_STREAM_CTRL_STRUCT.size:
            raise ValueError(
                f'Expected 26-byte UVC streaming control block, got {len(raw)} bytes'
            )
        (bm_hint, fmt_idx, frm_idx, interval, key_rate, p_rate, quality,
         win_size, delay, max_frame, max_payload) = UVC_STREAM_CTRL_STRUCT.unpack(raw)
        return {
            'bmHint': bm_hint,
            'bFormatIndex': fmt_idx,
            'bFrameIndex': frm_idx,
            'dwFrameInterval': interval,
            'wKeyFrameRate': key_rate,
            'wPFrameRate': p_rate,
            'wCompQuality': quality,
            'wCompWindowSize': win_size,
            'wDelay': delay,
            'dwMaxVideoFrameSize': max_frame,
            'dwMaxPayloadTransferSize': max_payload,
            'raw': bytes(raw),
        }

    def uvc_set_cur(self, cs: int, payload: bytes, *, interface: int | None = None) -> None:
        intf = self.vs_interface if interface is None else interface
        raw = bytes(payload)
        if len(raw) != UVC_STREAM_CTRL_STRUCT.size:
            raise ValueError('UVC VS SET_CUR expects a 26-byte payload')
        setup = struct.pack('<BBHHH', 0x21, UVC_SET_CUR, (cs & 0xFF) << 8, intf, len(raw)).hex()
        self.host.control(setup, data_out=raw)

    def uvc_get_cur(self, cs: int, *, interface: int | None = None,
                    length: int = UVC_STREAM_CTRL_STRUCT.size) -> dict:
        intf = self.vs_interface if interface is None else interface
        setup = struct.pack('<BBHHH', 0xA1, UVC_GET_CUR, (cs & 0xFF) << 8, intf, length).hex()
        raw = self.host.control(setup)
        return self.parse_streaming_control(raw)

    def negotiate_mjpeg_stream(self, *, format_index: int = 1, frame_index: int = 1,
                               frame_interval: int = 333333) -> dict:
        """Execute UVC 1.1 ``VS_PROBE_CONTROL`` + ``VS_COMMIT_CONTROL`` on Interface 2."""
        probe_req = self.pack_streaming_control(
            bm_hint=1,
            format_index=format_index,
            frame_index=frame_index,
            frame_interval=frame_interval,
        )
        self.uvc_set_cur(UVC_CS_VS_PROBE_CONTROL, probe_req)
        probe_cur = self.uvc_get_cur(UVC_CS_VS_PROBE_CONTROL)
        if (probe_cur['bFormatIndex'] != format_index or
                probe_cur['bFrameIndex'] != frame_index or
                probe_cur['dwFrameInterval'] != frame_interval or
                probe_cur['dwMaxVideoFrameSize'] <= 0 or
                probe_cur['dwMaxPayloadTransferSize'] <= 0):
            raise ValueError(f'UVC PROBE reply failed negotiation checks: {probe_cur}')

        self.uvc_set_cur(UVC_CS_VS_COMMIT_CONTROL, probe_cur['raw'])
        commit_cur = self.uvc_get_cur(UVC_CS_VS_COMMIT_CONTROL)
        if (commit_cur['bFormatIndex'] != format_index or
                commit_cur['bFrameIndex'] != frame_index or
                commit_cur['dwFrameInterval'] != frame_interval or
                commit_cur['dwMaxVideoFrameSize'] != probe_cur['dwMaxVideoFrameSize'] or
                commit_cur['dwMaxPayloadTransferSize'] != probe_cur['dwMaxPayloadTransferSize']):
            raise ValueError('UVC COMMIT readback disagrees with negotiated PROBE parameters')
        self._last_fid = None
        return {'probe': probe_cur, 'commit': commit_cur}

    def read_uvc_mjpeg_frame(self, *, max_packets: int = 128) -> dict:
        """Reassemble a multi-TRB UVC MJPEG frame from Bulk-IN ``0x84`` (EP 9)."""
        if max_packets < 1:
            raise ValueError('max_packets must be positive')
        frame_buf = bytearray()
        frame_fid: int | None = None
        pts: int | None = None
        packets = 0
        eof_seen = False

        for _ in range(max_packets):
            pkt = self.host.bulk_in(self.ep_uvc_in)
            if len(pkt) < 12:
                raise ValueError(f'Truncated UVC payload packet ({len(pkt)} bytes < 12)')
            hdr_len = pkt[0]
            bfh = pkt[1]
            if hdr_len != 12 or hdr_len > len(pkt):
                raise ValueError(f'Expected 12-byte UVC payload header, got {hdr_len}')
            if bfh & UVC_BFH_ERR:
                raise ValueError('UVC payload header reported BFH_ERR')
            pkt_fid = bfh & UVC_BFH_FID
            if frame_fid is None:
                if self._last_fid is not None and pkt_fid == self._last_fid:
                    raise ValueError(
                        f'UVC BFH_FID bit did not toggle across consecutive frames (fid={pkt_fid})'
                    )
                frame_fid = pkt_fid
                if bfh & UVC_BFH_PTS:
                    pts = struct.unpack_from('<I', pkt, 2)[0]
            elif pkt_fid != frame_fid:
                raise ValueError('UVC BFH_FID changed mid-frame before BFH_EOF')

            frame_buf.extend(pkt[hdr_len:])
            packets += 1
            if bfh & UVC_BFH_EOF:
                eof_seen = True
                break

        if not eof_seen:
            raise ValueError('UVC frame exceeded packet budget without BFH_EOF')
        if len(frame_buf) < 4 or frame_buf[:2] != b'\xff\xd8' or frame_buf[-2:] != b'\xff\xd9':
            raise ValueError('Reassembled UVC MJPEG payload lacks valid FFD8..FFD9 bounds')

        jpeg_info = extract_embedded_jpeg(bytes(frame_buf))
        if jpeg_info['jpeg_offset'] != 0 or jpeg_info['jpeg_bytes'] != len(frame_buf):
            raise ValueError('Unexpected leading or trailing bytes outside UVC MJPEG frame')
        self._last_fid = frame_fid
        return {
            'fid': frame_fid,
            'packets': packets,
            'pts': pts,
            **jpeg_info,
        }


class BusPowerHidClient:
    """Sony Bus-Power / HID client (``054C:096F``, ``03/00/00``; E0597, E0598)."""

    def __init__(self, host: Ep0Host, enumeration: dict | None = None):
        self.host = host
        self.interface_number = 0
        self.ep_in = 3
        self.report_descriptor_length = 19
        self.max_packet = 32
        self.descriptors: dict | None = None
        if enumeration is not None:
            self.validate_descriptors(enumeration)

    def enumerate(self) -> dict:
        enumeration = self.host.enumerate_device()
        self.validate_descriptors(enumeration)
        return enumeration

    def validate_descriptors(self, enumeration: dict) -> dict:
        dev = parse_usb_device_descriptor(enumeration['device_hex'])
        if dev['idVendor'] != SONY_VID or dev['idProduct'] != BUS_POWER_HID_PID:
            raise ValueError(
                f'Expected Sony Bus-Power HID 054C:096F, got '
                f'{dev["idVendor"]:04x}:{dev["idProduct"]:04x}'
            )
        topo = parse_usb_config_topology(enumeration['configuration_hex'])
        if len(topo['interfaces']) != 1:
            raise ValueError('Expected single-interface Bus-Power HID configuration')
        intf = topo['interfaces'][0]
        if (intf['bInterfaceClass'], intf['bInterfaceSubClass'], intf['bInterfaceProtocol']) != (
            0x03, 0x00, 0x00
        ):
            raise ValueError('Expected HID interface class 03/00/00')
        if len(intf['hid_descriptors']) != 1:
            raise ValueError('Missing class-specific HID descriptor (0x21)')
        hid = intf['hid_descriptors'][0]
        if hid['bReportDescriptorType'] != 0x22 or hid['wDescriptorLength'] <= 0:
            raise ValueError('Invalid HID Report descriptor metadata')
        if len(intf['endpoints']) != 1:
            raise ValueError('Expected single Interrupt-IN endpoint on HID interface')
        ep = intf['endpoints'][0]
        if (ep['bEndpointAddress'] != 0x81 or ep['transfer_type'] != 3 or
                ep['wMaxPacketSize'] != 32):
            raise ValueError('Expected HID Interrupt-IN endpoint 0x81 (32B)')

        self.interface_number = intf['bInterfaceNumber']
        self.ep_in = ep['dwc3_ep_idx']
        self.max_packet = ep['wMaxPacketSize']
        self.report_descriptor_length = hid['wDescriptorLength']
        self.descriptors = {'device': dev, 'topology': topo, 'hid': hid}
        return self.descriptors

    def get_report_descriptor(self, *, length: int | None = None) -> bytes:
        req_len = self.report_descriptor_length if length is None else length
        if not 1 <= req_len <= 1024:
            raise ValueError('HID report descriptor length must be 1..1024')
        setup = struct.pack('<BBHHH', 0x81, 0x06, 0x2200, self.interface_number, req_len).hex()
        report_desc = self.host.control(setup)
        if len(report_desc) != req_len or report_desc[-1] != 0xC0:
            raise ValueError('Malformed or incomplete HID Report descriptor')
        return report_desc

    def read_input_report(self, *, retries: int = 200) -> bytes:
        report = self.host.interrupt_in(self.ep_in, retries=retries)
        if not 1 <= len(report) <= self.max_packet:
            raise ValueError(f'Invalid HID input report length: {len(report)}')
        return report


class JiritsuServiceClient:
    """Sony Jiritsu Service bulk client (``054C:0336``, ``FF/00/00``; E0525, E0643)."""

    def __init__(self, host: Ep0Host, enumeration: dict | None = None):
        self.host = host
        self.ep_in = 3
        self.ep_out = 4
        self.max_packet = 512
        self.sequence = 0
        self.descriptors: dict | None = None
        if enumeration is not None:
            self.validate_descriptors(enumeration)

    def enumerate(self) -> dict:
        enumeration = self.host.enumerate_device()
        self.validate_descriptors(enumeration)
        return enumeration

    def validate_descriptors(self, enumeration: dict) -> dict:
        dev = parse_usb_device_descriptor(enumeration['device_hex'])
        if dev['idVendor'] != SONY_VID or dev['idProduct'] != JIRITSU_SERVICE_PID:
            raise ValueError(
                f'Expected Sony Jiritsu service 054C:0336, got '
                f'{dev["idVendor"]:04x}:{dev["idProduct"]:04x}'
            )
        topo = parse_usb_config_topology(enumeration['configuration_hex'])
        vendor_intfs = [
            i for i in topo['interfaces']
            if i['bInterfaceNumber'] == 0
            and i['bAlternateSetting'] == 0
            and i['bInterfaceClass'] == 0xFF
        ]
        if len(vendor_intfs) != 1:
            raise ValueError('Expected single vendor-specific (0xFF) service interface 0')
        intf = vendor_intfs[0]
        bulk_in = [
            ep for ep in intf['endpoints']
            if ep['transfer_type'] == 2 and (ep['bEndpointAddress'] & 0x80)
        ]
        bulk_out = [
            ep for ep in intf['endpoints']
            if ep['transfer_type'] == 2 and not (ep['bEndpointAddress'] & 0x80)
        ]
        if (len(bulk_in) != 1 or len(bulk_out) != 1 or
                bulk_in[0]['bEndpointAddress'] != 0x81 or bulk_in[0]['wMaxPacketSize'] != 512 or
                bulk_out[0]['bEndpointAddress'] != 0x02 or bulk_out[0]['wMaxPacketSize'] != 512):
            raise ValueError(
                'Expected Jiritsu primary bulk endpoints 0x81 IN (512B) and 0x02 OUT (512B)'
            )
        self.ep_in = bulk_in[0]['dwc3_ep_idx']
        self.ep_out = bulk_out[0]['dwc3_ep_idx']
        self.max_packet = bulk_in[0]['wMaxPacketSize']
        self.descriptors = {'device': dev, 'topology': topo}
        return self.descriptors

    def exchange(self, command: int, payload: bytes = b'', *,
                 category: int = JIRITSU_CATEGORY, limit: int = 65536) -> dict:
        raw_payload = bytes(payload)
        if not 0 <= command <= 0xFFFF or not 0 <= category <= 0xFFFF:
            raise ValueError('category and command must be uint16 values')
        if not 0 <= limit <= 512 * 1024:
            raise ValueError('limit exceeds bounded 512 KiB chunk profile')
        if self.sequence >= 0xFFFF:
            raise ValueError('Jiritsu sequence space exhausted')
        self.sequence += 1
        seq = self.sequence

        body = struct.pack('<HH', category, command) + raw_payload
        request = SENSER_HEADER.pack(len(body), 0x40, seq, 0, 0, 0, 0) + body
        self.host.bulk_out_transfer(self.ep_out, request, max_packet=self.max_packet)

        rx = bytearray(self.host.bulk_in(self.ep_in))
        if len(rx) < SENSER_HEADER.size:
            raise ValueError('Truncated Jiritsu reply header')
        size, func, rx_seq, version, micon, offset, response = SENSER_HEADER.unpack_from(rx, 0)
        if (func, rx_seq, version, micon, offset) != (0x40, seq, 3, 0, 0):
            raise ValueError(
                f'Jiritsu reply header mismatch: {(func, rx_seq, version, micon, offset)!r}'
            )
        if response != 0 or not 4 <= size <= limit + 4:
            raise ValueError(f'Jiritsu reply envelope error/size out of bounds: resp={response}, size={size}')

        expected_total = SENSER_HEADER.size + size
        if len(rx) > expected_total:
            raise ValueError('Jiritsu reply exceeded declared body length')
        for _ in range(1024):
            if len(rx) == expected_total:
                break
            chunk = self.host.bulk_in(self.ep_in)
            if not chunk or len(rx) + len(chunk) > expected_total:
                raise ValueError('Truncated or oversized Jiritsu reply fragment')
            rx.extend(chunk)
        else:
            raise ValueError('Jiritsu reply fragment budget exhausted')

        reply_body = bytes(rx[SENSER_HEADER.size:])
        status = struct.unpack_from('<I', reply_body, 0)[0]
        if status != 0:
            raise JiritsuReplyError(status, sequence=seq)
        return {
            'sequence': seq,
            'version': version,
            'status': status,
            'data': reply_body[4:],
        }

    def ping(self, marker: bytes = b'JIRITSU_PING') -> dict:
        raw_marker = bytes(marker)
        res = self.exchange(JIRITSU_CMD_PING, raw_marker, limit=256)
        if not res['data'].endswith(raw_marker):
            raise ValueError('Jiritsu ping reply did not echo marker')
        return res

    def read_chunk(self, offset: int, length: int) -> bytes:
        if offset < 0 or not 1 <= length <= 512 * 1024:
            raise ValueError('Chunk read requires non-negative offset and 1..524288 byte length')
        res = self.exchange(
            JIRITSU_CMD_GET_FILE, struct.pack('<II', offset, length), limit=length
        )
        if len(res['data']) != length:
            raise ValueError(f'Expected {length} chunk bytes, got {len(res["data"])}')
        return res['data']
