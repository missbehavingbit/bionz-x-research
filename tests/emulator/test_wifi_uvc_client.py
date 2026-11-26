"""End-to-end loopback tests for Wi-Fi LiveView + Control client and launcher."""
from __future__ import annotations

import socket
import struct
import threading
import unittest

from tools.research.ptp_liveview_client import frame_hub
from tools.research.wifi_uvc_client import (
    CTL_MAGIC,
    CTL_PING,
    CTL_VERSION,
    WIFI_FRAME_HDR_FMT,
    WIFI_FRAME_MAGIC,
    WifiStreamClient,
    ctl_crc16,
)

# Minimal valid 1280x720 SOF0 JPEG fixture (SOI + SOF0(1280x720) + EOI).
FAKE_720P_JPEG = (
    b"\xff\xd8\xff\xc0\x00\x0b\x08\x02\xd0\x05\x00\x01\x01\x11\x00\xff\xd9"
)


class WifiUvcClientTests(unittest.TestCase):
    def test_loopback_frame_and_control_roundtrip(self) -> None:
        stream_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stream_srv.bind(("127.0.0.1", 0))
        stream_srv.listen(1)
        ctrl_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ctrl_srv.bind(("127.0.0.1", 0))
        ctrl_srv.listen(1)

        s_port = stream_srv.getsockname()[1]
        c_port = ctrl_srv.getsockname()[1]

        def serve_stream() -> None:
            conn, _ = stream_srv.accept()
            with conn:
                hdr = struct.pack(
                    WIFI_FRAME_HDR_FMT,
                    WIFI_FRAME_MAGIC,
                    11,
                    len(FAKE_720P_JPEG),
                    1,
                    987654321,
                )
                conn.sendall(hdr + FAKE_720P_JPEG)

        def serve_ctrl() -> None:
            conn, _ = ctrl_srv.accept()
            with conn:
                req = conn.recv(64)
                magic, ver, op, seq, plen, _flags, crc = struct.unpack("<HBBHHHH", req[:12])
                self.assertEqual(magic, CTL_MAGIC)
                self.assertEqual(ver, CTL_VERSION)
                self.assertEqual(op, CTL_PING)
                self.assertEqual(crc, ctl_crc16(req[:10] + req[12:12 + plen]))
                ack = struct.pack("<HBBHHHIHH", CTL_MAGIC, CTL_VERSION, op, seq, 0, 0, 1, 0, 0)
                conn.sendall(ack)

        t1 = threading.Thread(target=serve_stream, daemon=True)
        t2 = threading.Thread(target=serve_ctrl, daemon=True)
        t1.start()
        t2.start()

        client = WifiStreamClient("127.0.0.1", stream_port=s_port, ctrl_port=c_port)
        try:
            client.connect()
            frame = client.get_frame()
            self.assertIsNotNone(frame)
            w, h, sz, jpg, seq, cap_ns, flags = frame
            self.assertEqual((w, h), (1280, 720))
            self.assertEqual(sz, len(FAKE_720P_JPEG))
            self.assertEqual(jpg, FAKE_720P_JPEG)
            self.assertEqual(seq, 11)
            self.assertEqual(cap_ns, 987654321)
            self.assertEqual(flags, 1)

            pub = frame_hub.publish(jpg, 1.5)
            self.assertGreater(pub.sequence, 0)

            ack = client.send_control(CTL_PING)
            self.assertEqual(ack.magic, CTL_MAGIC)
            self.assertEqual(ack.status, 0)
            self.assertEqual(ack.value, 1)
        finally:
            client.close()
            stream_srv.close()
            ctrl_srv.close()
            t1.join(timeout=2)
            t2.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
