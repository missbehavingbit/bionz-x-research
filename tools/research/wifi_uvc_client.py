"""5 GHz Wi-Fi LiveView + Control client for Sony ILCE-6400 CinemaHUD.

Connects to ``a6400-lived --wifi-stream`` on the camera:
  - TCP :9640 for 24-byte framed 1280x720 MJPEG frames
  - TCP :9641 for 12-byte/18-byte binary a6400_control requests/acks
and publishes frames directly into CinemaHUD (tools.research.ptp_liveview_client).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import socket
import struct
import sys
import time
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover
    np = None
    Image = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research.ptp_liveview_client import (
    camera_state,
    frame_hub,
    parse_jpeg,
    start_http_server,
)

WIFI_FRAME_MAGIC = 0xA6405646
WIFI_FRAME_HDR_FMT = "<IIIIQ"
WIFI_FRAME_HDR_SIZE = struct.calcsize(WIFI_FRAME_HDR_FMT)  # 24 bytes

CTL_MAGIC = 0xA640
CTL_VERSION = 1
CTL_HDR_FMT = "<HBBHHHH"
CTL_HDR_SIZE = struct.calcsize(CTL_HDR_FMT)  # 12 bytes
CTL_ACK_FMT = "<HBBHHHII"
CTL_ACK_SIZE = struct.calcsize(CTL_ACK_FMT)  # 18 bytes

CTL_PING = 0x00
CTL_STREAM_START = 0x20
CTL_STREAM_STOP = 0x21
CTL_GET_STATE = 0x30
CTL_GET_STATS = 0x31


def ctl_crc16(data: bytes) -> int:
    """CCITT-FALSE CRC-16 (poly 0x1021, init 0xffff) matching a6400_control.h."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def build_ctl_request(opcode: int, seq: int, payload: bytes = b"") -> bytes:
    body_hdr = struct.pack("<HBBHHH", CTL_MAGIC, CTL_VERSION, opcode, seq, len(payload), 0)
    crc = ctl_crc16(body_hdr + payload)
    return body_hdr + struct.pack("<H", crc) + payload


def decode_uyvy_to_jpeg(raw: bytes, width: int = 640, quality: int = 85) -> tuple[int, int, int, bytes] | None:
    """Convert packed UYVY (2 bytes/pixel, U0 Y0 V0 Y1) into a clean JPEG."""
    if np is None or Image is None or width <= 0 or (width & 1):
        return None
    row_bytes = width * 2
    height = len(raw) // row_bytes
    if height <= 0:
        return None
    arr = np.frombuffer(raw, dtype=np.uint8, count=height * row_bytes).reshape(height, width, 2)
    y = arr[:, :, 1].astype(np.float32)
    u = np.repeat(arr[:, 0::2, 0].astype(np.float32) - 128.0, 2, axis=1)
    v = np.repeat(arr[:, 1::2, 0].astype(np.float32) - 128.0, 2, axis=1)
    r = np.clip(y + 1.402 * v, 0.0, 255.0).astype(np.uint8)
    g = np.clip(y - 0.344136 * u - 0.714136 * v, 0.0, 255.0).astype(np.uint8)
    b = np.clip(y + 1.772 * u, 0.0, 255.0).astype(np.uint8)
    rgb = np.stack((r, g, b), axis=2)
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="JPEG", quality=quality)
    jpeg = buf.getvalue()
    return width, height, len(jpeg), jpeg


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        r = sock.recv_into(view[got:], n - got)
        if r == 0:
            raise ConnectionError("socket closed")
        got += r
    return bytes(buf)


@dataclass
class WifiAck:
    magic: int
    version: int
    opcode: int
    seq: int
    status: int
    detail: int
    value: int
    crc_state: int = 0


class WifiStreamClient:
    """TCP client for a6400-lived --wifi-stream (:9640 stream, :9641 control)."""

    def __init__(self, host: str, stream_port: int = 9640, ctrl_port: int = 9641, timeout: float = 5.0):
        self.host = host
        self.stream_port = stream_port
        self.ctrl_port = ctrl_port
        self.timeout = timeout
        self._stream_sock: socket.socket | None = None
        self._ctrl_sock: socket.socket | None = None
        self._seq = 0

    def connect(self) -> None:
        self._stream_sock = socket.create_connection((self.host, self.stream_port), timeout=self.timeout)
        self._stream_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self._stream_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
        except OSError:
            pass
        if self.ctrl_port:
            self._ctrl_sock = socket.create_connection((self.host, self.ctrl_port), timeout=self.timeout)
            self._ctrl_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self) -> None:
        for attr in ("_stream_sock", "_ctrl_sock"):
            sock = getattr(self, attr)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    def get_frame(self):
        if self._stream_sock is None:
            raise RuntimeError("not connected")
        try:
            hdr = _recv_exact(self._stream_sock, WIFI_FRAME_HDR_SIZE)
        except TimeoutError:
            return None
        magic, frame_seq, jpeg_bytes, flags, capture_ns = struct.unpack(WIFI_FRAME_HDR_FMT, hdr)
        if magic != WIFI_FRAME_MAGIC or jpeg_bytes == 0 or jpeg_bytes > 4 * 1024 * 1024:
            raise ValueError(f"invalid wifi frame header magic=0x{magic:08x} size={jpeg_bytes}")
        payload = _recv_exact(self._stream_sock, jpeg_bytes)
        if flags & 0x10:
            decoded = decode_uyvy_to_jpeg(payload)
            if decoded is None:
                return None
            w, h, sz, clean = decoded
            return w, h, sz, clean, frame_seq, capture_ns, flags
        parsed = parse_jpeg(payload)
        if parsed is None:
            return None
        w, h, sz, clean = parsed
        return w, h, sz, clean, frame_seq, capture_ns, flags

    def send_control(self, opcode: int, payload: bytes = b"") -> WifiAck:
        if self._ctrl_sock is None:
            raise RuntimeError("control socket not connected")
        self._seq = (self._seq + 1) & 0xFFFF
        pkt = build_ctl_request(opcode, self._seq, payload)
        self._ctrl_sock.sendall(pkt)
        # a6400_ctl_ack is packed 18 bytes: magic(H) version(B) opcode(B) seq(H) status(H) detail(H) value(I) crc_state(I)
        raw_ack = _recv_exact(self._ctrl_sock, CTL_ACK_SIZE)
        magic, version, op, seq, status, detail, value, crc_state = struct.unpack(CTL_ACK_FMT, raw_ack)
        return WifiAck(magic, version, op, seq, status, detail, value, crc_state)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera-ip", default="192.168.122.1")
    p.add_argument("--stream-port", type=int, default=9640)
    p.add_argument("--ctrl-port", type=int, default=9641)
    p.add_argument("--hud-port", type=int, default=8080)
    p.add_argument("--frames", type=int, default=0, help="0 = serve CinemaHUD continuously")
    p.add_argument("--no-hud", action="store_true")
    p.add_argument("--no-start-stream", action="store_true", help="Do not send CTL_STREAM_START on connect")
    args = p.parse_args()

    http_server = None if args.no_hud else start_http_server(args.hud_port)
    if http_server is not None:
        print(f"CinemaHUD active at http://127.0.0.1:{args.hud_port}/", flush=True)

    client = WifiStreamClient(args.camera_ip, args.stream_port, args.ctrl_port)
    count = 0
    t0 = time.monotonic()
    try:
        while args.frames == 0 or count < args.frames:
            try:
                client.connect()
                print(f"Connected to {args.camera_ip}:{args.stream_port}/{args.ctrl_port}", flush=True)
                if not args.no_start_stream and args.ctrl_port:
                    try:
                        ack = client.send_control(CTL_STREAM_START)
                        print(f"CTL_STREAM_START status={ack.status} value={ack.value}", flush=True)
                    except (OSError, ConnectionError) as exc:
                        print(f"CTL_STREAM_START error: {exc}", flush=True)
                while args.frames == 0 or count < args.frames:
                    fetch_t0 = time.perf_counter_ns()
                    res = client.get_frame()
                    fetch_ms = (time.perf_counter_ns() - fetch_t0) / 1e6
                    if res is None:
                        continue
                    w, h, sz, jpeg_bytes, _seq, _cap_ns, _flags = res
                    count += 1
                    published = frame_hub.publish(jpeg_bytes, fetch_ms)
                    elapsed = max(time.monotonic() - t0, 1e-6)
                    camera_state.update(
                        frame_sequence=published.sequence,
                        ptp_fetch_ms=round(fetch_ms, 3),
                        input_width=w,
                        input_height=h,
                        fps=round(count / elapsed, 1),
                    )
            except (OSError, ConnectionError) as exc:
                client.close()
                if args.frames > 0:
                    raise
                print(f"Stream disconnected ({exc}); reconnecting in 1s...", flush=True)
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
