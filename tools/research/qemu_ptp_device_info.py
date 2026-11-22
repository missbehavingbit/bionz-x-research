"""Verify one read-only Sony PTP exchange over QEMU's virtual host socket."""
import hashlib
import struct

from src.a6400.execution.guest_usb_otgcore import parse_usb_configuration_descriptor

GET_DEVICE_INFO = 0x1001
PTP_OK = 0x2001
HEADER = struct.Struct('<IHHI')
MAX_CONTAINER = 64 * 1024


def still_image_endpoints(enumeration):
    device = bytes.fromhex(enumeration['device_hex'])
    if len(device) != 18 or device[:2] != b'\x12\x01' or device[8:12] != b'\x4c\x05\xaa\x0c':
        raise ValueError('Expected the captured Sony PC Remote device identity (054C:0CAA)')
    interfaces = []
    current = None
    config = bytes.fromhex(enumeration['configuration_hex'])
    entries = parse_usb_configuration_descriptor(config)
    if (len(config) < 9 or config[:2] != b'\x09\x02' or
            int.from_bytes(config[2:4], 'little') != len(config) or
            sum(row[1] for row in entries) != len(config)):
        raise ValueError('Malformed PTP configuration descriptor')
    for _, length, kind, entry in entries:
        if kind == 4:
            if length != 9:
                raise ValueError('Malformed interface descriptor')
            current = None
            if entry[3] == 0 and entry[5:8] == b'\x06\x01\x01':
                current = (entry[4], [])
                interfaces.append(current)
        elif kind == 5 and current is not None:
            if length != 7:
                raise ValueError('Malformed PTP endpoint descriptor')
            current[1].append(entry)
    if len(interfaces) != 1 or len(interfaces[0][1]) != interfaces[0][0]:
        raise ValueError('Expected one complete Sony still-image interface')
    bulk = {}
    for entry in interfaces[0][1]:
        address = entry[2]
        if entry[3] & 3 != 2:
            continue
        number, direction = address & 0x0f, address >> 7
        packet = int.from_bytes(entry[4:6], 'little')
        if not 1 <= number <= 7 or address & 0x70 or packet not in (8, 16, 32, 64, 512, 1024):
            raise ValueError('Unsupported Sony bulk endpoint')
        if direction in bulk:
            raise ValueError('Ambiguous Sony bulk endpoints')
        bulk[direction] = number * 2 + direction
    if set(bulk) != {0, 1}:
        raise ValueError('The Sony still-image interface lacks a bulk endpoint pair')
    return bulk[0], bulk[1]


def read_container(host, endpoint, expected_kind, expected_code):
    raw = bytearray(host.bulk_in(endpoint))
    if len(raw) < HEADER.size:
        raise ValueError('Truncated PTP container header')
    length, kind, code, transaction = HEADER.unpack_from(raw)
    if (not len(raw) <= length <= MAX_CONTAINER or kind != expected_kind or
            code != expected_code or transaction != 0):
        raise ValueError('PTP container length, kind, code or transaction mismatch')
    for _ in range(32):
        if len(raw) == length:
            return bytes(raw)
        chunk = host.bulk_in(endpoint)
        if not chunk or len(chunk) > length - len(raw):
            raise ValueError('Truncated or coalesced PTP container')
        raw.extend(chunk)
    raise ValueError('PTP fragment budget exhausted')


def parse_device_info(payload):
    cursor = 0

    def take(size):
        nonlocal cursor
        if size > len(payload) - cursor:
            raise ValueError('Truncated PTP DeviceInfo dataset')
        result = payload[cursor:cursor + size]
        cursor += size
        return result

    def string():
        length = take(1)[0]
        if not length:
            return ''
        value = take(length * 2)
        if value[-2:] != b'\x00\x00':
            raise ValueError('Unterminated PTP string')
        return value[:-2].decode('utf-16le')

    version, extension, extension_version = struct.unpack('<HIH', take(8))
    description = string()
    take(2)  # FunctionalMode
    for index in range(5):
        count = int.from_bytes(take(4), 'little')
        values = take(2 * count)
        if index == 0 and GET_DEVICE_INFO not in (op for (op,) in struct.iter_unpack('<H', values)):
            raise ValueError('GetDeviceInfo is not present in the device operation table')
    manufacturer, model = string(), string()
    string()  # DeviceVersion
    string()  # SerialNumber; do not include it in the summary.
    if (cursor != len(payload) or not version or
            manufacturer.casefold() not in ('sony', 'sony corporation') or model != 'ILCE-6400'):
        raise ValueError('DeviceInfo does not identify a Sony ILCE-6400')
    return {'manufacturer': manufacturer, 'model': model, 'standard_version': version,
            'vendor_extension_id': extension, 'vendor_extension_version': extension_version,
            'vendor_extension_description': description}


def get_device_info(host, enumeration):
    out_endpoint, in_endpoint = still_image_endpoints(enumeration)
    host.bulk_out(out_endpoint, HEADER.pack(HEADER.size, 1, GET_DEVICE_INFO, 0))
    data = read_container(host, in_endpoint, 2, GET_DEVICE_INFO)
    response = read_container(host, in_endpoint, 3, PTP_OK)
    if len(response) != HEADER.size:
        raise ValueError('Unexpected GetDeviceInfo response parameters')
    info = parse_device_info(data[HEADER.size:])
    return {**info, 'data_bytes': len(data), 'data_sha256': hashlib.sha256(data).hexdigest(),
            'response_code': PTP_OK, 'transaction': 0}
