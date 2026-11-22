"""Bounded virtual-host control transfers; no device replies or buffers are invented."""
import socket
import struct
import time


class Ep0Host:
    def __init__(self, channel, *, deadline=None):
        self.channel = channel
        self.buffer = b''
        self.events = []
        self.deadline = deadline

    def token(self, line, prefix, *, retries=100):
        deadline = time.monotonic() + max(10, retries * 0.05 + 5)
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        for _ in range(retries):
            if time.monotonic() >= deadline:
                break
            self.channel.sendall((line + '\n').encode('ascii'))
            self.events.append({'tx': line})
            while b'\n' not in self.buffer:
                if time.monotonic() >= deadline:
                    raise TimeoutError('No token response: ' + line)
                try:
                    data = self.channel.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise EOFError('Virtual host socket closed')
                self.buffer += data
                if len(self.buffer) > 1048576:
                    raise ValueError('Oversized host reply')
            raw, self.buffer = self.buffer.split(b'\n', 1)
            reply = raw.decode('ascii')
            self.events.append({'rx': reply})
            if reply.startswith('NAK '):
                endpoint = int(line.split()[1]) if line.startswith(('IN ', 'OUT ')) else 0
                if reply != f'NAK {endpoint}':
                    raise ValueError('NAK endpoint mismatch')
                if time.monotonic() >= deadline:
                    break
                time.sleep(.05)
                continue
            if not reply.startswith(prefix):
                raise RuntimeError(f'{line}: {reply}')
            return reply[len(prefix):]
        raise TimeoutError('Host retry budget exhausted: ' + line)

    def control(self, setup, data_out=None):
        raw = bytes.fromhex(setup)
        if len(raw) != 8:
            raise ValueError('Expected an eight-byte SETUP')
        requested = int.from_bytes(raw[6:8], 'little')
        if self.token('SETUP ' + setup, 'ACK 0 8'):
            raise ValueError('Malformed SETUP acknowledgement')
        if not requested:
            if data_out:
                raise ValueError('Unexpected OUT payload for zero-length SETUP')
            if self.token('IN 1', 'IN 1 '):
                raise ValueError('Nonempty status IN')
            return b''
        if not raw[0] & 0x80:
            if data_out is None or len(data_out) != requested:
                raise ValueError('Control-OUT payload length mismatch')
            ack = self.token('OUT 0 ' + data_out.hex(), 'ACK 0 ')
            if int(ack) != requested:
                raise ValueError(f'Control-OUT ACK length mismatch: {ack}')
            if self.token('IN 1', 'IN 1 '):
                raise ValueError('Nonempty status IN')
            return b''
        data = bytes.fromhex(self.token('IN 1', 'IN 1 '))
        if not 0 < len(data) <= requested:
            raise ValueError('Invalid control-IN result length')
        if self.token('OUT 0 -', 'ACK 0 0'):
            raise ValueError('Malformed status acknowledgement')
        return data

    def bulk_in(self, ep_idx: int, *, retries=400) -> bytes:
        if ep_idx < 2 or (ep_idx & 1) == 0:
            raise ValueError(f'Expected odd non-EP0 IN endpoint index, got {ep_idx}')
        prefix = f'IN {ep_idx} '
        return bytes.fromhex(self.token(f'IN {ep_idx}', prefix, retries=retries))

    def bulk_out(self, ep_idx: int, data: bytes):
        if not 2 <= ep_idx < 16 or ep_idx & 1:
            raise ValueError(f'Expected an even non-EP0 OUT endpoint index, got {ep_idx}')
        if not 1 <= len(data) <= 1024:
            raise ValueError('Bulk OUT command must contain 1..1024 bytes')
        if self.token(f'OUT {ep_idx} {data.hex()}', f'ACK {ep_idx} {len(data)}'):
            raise ValueError('Malformed bulk OUT acknowledgement')

    def enumerate_device(self):
        device = self.control('8006000100001200')
        if len(device) != 18 or device[:2] != b'\x12\x01':
            raise ValueError('Invalid DEVICE descriptor')
        self.control('0005010000000000')
        header = self.control('8006000200000900')
        if len(header) != 9 or header[:2] != b'\x09\x02':
            raise ValueError('Invalid CONFIGURATION header')
        total = int.from_bytes(header[2:4], 'little')
        if not 9 <= total <= 1024:
            raise ValueError('Configuration exceeds bounded transfer scope')
        configuration = self.control(struct.pack('<BBHHH', 0x80, 6, 0x200, 0, total).hex())
        if len(configuration) != total or configuration[:9] != header:
            raise ValueError('Incomplete or inconsistent configuration')
        value = configuration[5]
        if value == 0:
            raise ValueError('Zero configuration value')
        self.control(struct.pack('<BBHHH', 0, 9, value, 0, 0).hex())
        selected = self.control('8008000000000100')
        if selected != bytes([value]):
            raise ValueError('Original GET_CONFIGURATION disagrees with selection')
        return {'device_hex': device.hex(), 'configuration_hex': configuration.hex(),
                'address': 1, 'configuration_value': value, 'get_configuration_hex': selected.hex(),
                'control_sequence_completed': True, 'uvc_streaming_verified': False}

    def enumerate_bus_power(self):
        return self.enumerate_device()
