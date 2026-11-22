"""Ultra-low-latency native Win32/OpenCV monitor for Sony ILCE-6400 5 GHz Wi-Fi LiveView.

Connects directly to ``a6400-lived --wifi-stream`` TCP :9640, decodes hardware
JPEGs in native C++ (cv2.imdecode / libjpeg-turbo) and blits directly to a
native window without any HTTP server or browser compositor overhead.

Hotkeys inside the monitor window:
  F          Toggle fullscreen
  H          Toggle minimal HUD stats overlay (FPS, res, decode/rx ms, KB)
  G          Toggle rule-of-thirds grid
  P          Toggle focus peaking overlay (red high-contrast edges)
  Z          Toggle exposure zebra overlay (>= 95% luma)
  Q / ESC    Quit
"""
from __future__ import annotations

import argparse
import pathlib
import socket
import struct
import sys
import threading
import time
from collections import deque

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VENV_SITE = _ROOT / "venv" / "Lib" / "site-packages"
if _VENV_SITE.is_dir() and str(_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE))

import cv2
import numpy as np

WIFI_FRAME_MAGIC = 0xA6405646
WIFI_FRAME_HDR_FMT = "<IIIIQ"
WIFI_FRAME_HDR_SIZE = struct.calcsize(WIFI_FRAME_HDR_FMT)  # 24 bytes


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


class LatestFrameSlot:
    """Depth-1 lock-protected frame handoff; never queues stale frames."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._jpeg: bytes | None = None
        self._seq: int = 0
        self._rx_ns: int = 0
        self._rx_ms: float = 0.0
        self._dropped: int = 0
        self._connected: bool = False
        self._status: str = "Connecting..."

    def set_status(self, connected: bool, text: str) -> None:
        with self._lock:
            self._connected = connected
            self._status = text
        self._event.set()

    def put(self, jpeg: bytes, seq: int, rx_ns: int, rx_ms: float) -> None:
        with self._lock:
            if self._jpeg is not None:
                self._dropped += 1
            self._jpeg = jpeg
            self._seq = seq
            self._rx_ns = rx_ns
            self._rx_ms = rx_ms
            self._connected = True
            self._status = ""
        self._event.set()

    def take(self, timeout: float = 0.05):
        self._event.wait(timeout)
        with self._lock:
            self._event.clear()
            jpeg = self._jpeg
            self._jpeg = None
            return jpeg, self._seq, self._rx_ns, self._rx_ms, self._dropped, self._connected, self._status


def _reader_thread(host: str, port: int, slot: LatestFrameSlot, stop_evt: threading.Event) -> None:
    while not stop_evt.is_set():
        sock: socket.socket | None = None
        try:
            slot.set_status(False, f"Connecting to {host}:{port}...")
            sock = socket.create_connection((host, port), timeout=3.0)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
            except OSError:
                pass
            sock.settimeout(3.0)
            slot.set_status(True, "Connected — waiting for frame...")
            while not stop_evt.is_set():
                t0 = time.perf_counter_ns()
                hdr = _recv_exact(sock, WIFI_FRAME_HDR_SIZE)
                magic, seq, jpeg_bytes, _flags, _cap_ns = struct.unpack(WIFI_FRAME_HDR_FMT, hdr)
                if magic != WIFI_FRAME_MAGIC or jpeg_bytes == 0 or jpeg_bytes > 2 * 1024 * 1024:
                    raise ValueError(f"bad frame header magic=0x{magic:08x} len={jpeg_bytes}")
                payload = _recv_exact(sock, jpeg_bytes)
                t1 = time.perf_counter_ns()
                slot.put(payload, seq, t1, (t1 - t0) / 1e6)
        except Exception as exc:
            if not stop_evt.is_set():
                slot.set_status(False, f"Reconnecting ({exc})...")
                stop_evt.wait(0.5)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def _apply_overlays(
    img: np.ndarray,
    show_hud: bool,
    show_grid: bool,
    show_peaking: bool,
    show_zebra: bool,
    fps: float,
    kb: float,
    dec_ms: float,
    rx_ms: float,
    dropped: int,
) -> np.ndarray:
    h, w = img.shape[:2]

    if show_peaking:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
        edges = np.abs(lap) > 55
        img[edges] = (0, 30, 255)  # Red peaking highlights (BGR)

    if show_zebra:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        over = gray >= 242
        if np.any(over):
            yy, xx = np.indices((h, w))
            stripes = ((xx + yy) & 7) < 3
            mask = over & stripes
            img[mask] = (255, 255, 255)
            img[over & ~stripes] = (30, 30, 30)

    if show_grid:
        color = (180, 180, 180)
        for frac in (1 / 3, 2 / 3):
            x = int(w * frac)
            y = int(h * frac)
            cv2.line(img, (x, 0), (x, h), color, 1, cv2.LINE_AA)
            cv2.line(img, (0, y), (w, y), color, 1, cv2.LINE_AA)

    if show_hud:
        flags = []
        if show_peaking:
            flags.append("PEAK")
        if show_zebra:
            flags.append("ZEBRA")
        if show_grid:
            flags.append("GRID")
        flag_str = ("  [" + " ".join(flags) + "]") if flags else ""
        text = (
            f"a6400 5GHz  {w}x{h}  {fps:4.1f} fps  {kb:4.1f} KB  "
            f"dec {dec_ms:3.1f} ms  drop {dropped}{flag_str}"
        )
        cv2.rectangle(img, (8, 8), (min(w - 8, 8 + len(text) * 9 + 16), 34), (15, 15, 15), -1)
        cv2.putText(img, text, (16, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (235, 245, 235), 1, cv2.LINE_AA)

    return img


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera-ip", default="192.168.100.17")
    p.add_argument("--stream-port", type=int, default=9640)
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--no-hud", action="store_true")
    args = p.parse_args()

    win = "Sony a6400 Native 5 GHz Monitor  [F:Full  H:HUD  G:Grid  P:Peak  Z:Zebra  Q:Quit]"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 1280, 720)
    blank = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(blank, f"Connecting to {args.camera_ip}:{args.stream_port}...", (24, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1, cv2.LINE_AA)
    cv2.imshow(win, blank)
    cv2.waitKey(1)
    fullscreen = args.fullscreen
    if fullscreen:
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    show_hud = not args.no_hud
    show_grid = False
    show_peaking = False
    show_zebra = False

    slot = LatestFrameSlot()
    stop_evt = threading.Event()
    th = threading.Thread(
        target=_reader_thread,
        args=(args.camera_ip, args.stream_port, slot, stop_evt),
        daemon=True,
    )
    th.start()

    frame_times: deque[float] = deque(maxlen=45)
    last_img: np.ndarray | None = None

    try:
        while True:
            jpeg, _seq, _rx_ns, rx_ms, dropped, connected, status = slot.take(timeout=0.05)
            if jpeg is not None:
                t_dec0 = time.perf_counter_ns()
                arr = np.frombuffer(jpeg, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                dec_ms = (time.perf_counter_ns() - t_dec0) / 1e6
                if img is not None:
                    now = time.monotonic()
                    frame_times.append(now)
                    fps = 0.0
                    if len(frame_times) >= 2:
                        dt = frame_times[-1] - frame_times[0]
                        if dt > 0:
                            fps = (len(frame_times) - 1) / dt
                    last_img = img
                    disp = _apply_overlays(
                        img.copy(),
                        show_hud,
                        show_grid,
                        show_peaking,
                        show_zebra,
                        fps,
                        len(jpeg) / 1024.0,
                        dec_ms,
                        rx_ms,
                        dropped,
                    )
                    cv2.imshow(win, disp)
            elif last_img is None and status:
                blank = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(blank, status, (24, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1, cv2.LINE_AA)
                cv2.imshow(win, blank)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            elif key in (ord("f"), ord("F")):
                fullscreen = not fullscreen
                cv2.setWindowProperty(
                    win,
                    cv2.WND_PROP_FULLSCREEN,
                    cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL,
                )
            elif key in (ord("h"), ord("H")):
                show_hud = not show_hud
            elif key in (ord("g"), ord("G")):
                show_grid = not show_grid
            elif key in (ord("p"), ord("P")):
                show_peaking = not show_peaking
            elif key in (ord("z"), ord("Z")):
                show_zebra = not show_zebra

            if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        stop_evt.set()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
