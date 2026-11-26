#!/usr/bin/env python3
"""A client for the Sony ScalarWebAPI the camera actually runs.

The camera's Wi-Fi control channel is JSON over HTTP on port 10000. This module is the
transport, and only the transport: it opens a connection, posts one method call and returns
the decoded answer. It deliberately holds no policy, so a caller can be written against it
without this file deciding what is possible.

Two behaviours are load-bearing and are tested rather than assumed:

* A method the camera does not expose answers ``{"error": [<code>, <message>]}``. The
  decoder returns that unchanged as :class:`ApiError` instead of raising on the HTTP level,
  because a rejected method is a fact about the camera, not a fault in the client.
* A method name is sent exactly as given. This module never guesses an alternative spelling
  and never retries with a different method, so a caller that gets :class:`ApiError` knows
  the camera refused that name.

Not established here: which methods a given body exposes. That is read from the camera with
:meth:`ScalarWebApi.available_api_list` and recorded, never hard-coded.

Measured against the owner's ILCE-6400 (firmware 2.00, E0659): 47 methods exposed out of the
221 camera methods present in the packaged ``orb-server``.
"""
from __future__ import annotations

import base64
import json
import socket
import time
from pathlib import Path
from typing import Any

DEFAULT_PORT = 10000
DEFAULT_HOST = "192.168.122.1"
DEFAULT_TIMEOUT = 20.0

#: Error codes the camera returns in the ``error`` array. Sony documents the numbers; the
#: ones seen on this body are named here so callers do not repeat the mapping.
# Names are Sony's own, read from EnumErrorCode in the shipped 7.6.0 client (E0660), not
# guessed from the number. Code 1 is literally "Any", an unclassified refusal; code 12 is
# "No Such Method", which is how the camera answers a name it does not run at all.
ERROR_CODES = {
    0: "OK",
    1: "Any: the camera refused without a classified reason",
    2: "Timeout: the call took too long inside the camera",
    3: "Illegal Argument: a parameter was rejected",
    4: "Illegal Data Format",
    5: "Illegal Request: the request is not legal in the camera's current mode",
    6: "Illegal Response",
    7: "Illegal State",
    8: "Illegal Type",
    9: "Index Out Of Bounds",
    10: "No Such Element",
    11: "No Such Field",
    12: "No Such Method: this name does not exist in the running application",
    13: "Null Pointer",
    14: "Unsupported Version",
    15: "Unsupported Operation",
    16: "Transport Error",
    17: "Client Internal Error",
    18: "Response Timeout",
    40400: "Shooting Fail: the camera failed to photograph (seen without a half-press in the dark)",
    40401: "Camera is not ready",
    40402: "Already running polling API",
    40403: "Still Capturing Not Finished: call awaitTakePicture for the result",
    40404: "Camera API is not prepared",
}


class ApiError(Exception):
    """The camera answered with an ``error`` array rather than a ``result``."""

    def __init__(self, code: int, message: str, method: str | None = None) -> None:
        self.code = code
        self.message = message
        self.method = method
        detail = ERROR_CODES.get(code, "undocumented code")
        super().__init__(f"{method or 'call'} -> error {code} ({detail}){': ' + message if message else ''}")


class TransportError(Exception):
    """No HTTP answer was received at all."""


def build_request(method: str, params: list[Any] | None = None, *, request_id: int = 1,
                  version: str = "1.0") -> bytes:
    """The JSON body of a ScalarWebAPI call, exactly as the camera expects it."""
    payload = {"method": method, "params": params or [], "id": request_id, "version": version}
    return json.dumps(payload).encode("utf-8")


def _dechunk(body: bytes) -> bytes:
    """The payload of an HTTP/1.1 chunked body; a truncated body is a transport fault."""
    out = bytearray()
    position = 0
    while True:
        line_end = body.find(b"\r\n", position)
        if line_end < 0:
            raise TransportError("chunked body ends inside a chunk header")
        try:
            size = int(body[position:line_end].split(b";")[0].strip(), 16)
        except ValueError as exc:
            raise TransportError("chunked body has a malformed chunk size") from exc
        if size == 0:
            return bytes(out)
        start = line_end + 2
        if start + size > len(body):
            raise TransportError("chunked body is shorter than its chunk sizes")
        out += body[start:start + size]
        position = start + size + 2


def parse_response(raw: bytes, method: str | None = None) -> dict[str, Any]:
    """Decode one HTTP answer into ``{"result": ...}`` or raise :class:`ApiError`.

    The HTTP status is not interpreted: this camera answers ``200`` even when the body is an
    error array, so reading the body is the only reliable signal.
    """
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise TransportError("no header/body separator in the answer")
    if b"transfer-encoding: chunked" in head.lower():
        body = _dechunk(body)
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TransportError(f"body is not JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise TransportError("body is not a JSON object")
    if "error" in decoded:
        err = decoded["error"]
        if isinstance(err, list) and err:
            code = err[0] if isinstance(err[0], int) else -1
            message = err[1] if len(err) > 1 and isinstance(err[1], str) else ""
            raise ApiError(code, message, method)
        raise ApiError(-1, str(err), method)
    if "result" in decoded:
        return decoded
    if "results" in decoded:
        return decoded
    raise TransportError(f"answer has neither result nor error: {str(decoded)[:120]}")


class ScalarWebApi:
    """A minimal HTTP/1.1 client for one camera, one call per connection."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = DEFAULT_TIMEOUT, transcript: Path | str | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        #: When set, every call is appended here as one JSON line with the camera's answer
        #: kept byte for byte, so a recorded session can be checked against the camera later.
        self.transcript = Path(transcript) if transcript is not None else None

    def _post(self, service: str, method: str, params: list[Any] | None,
              version: str) -> bytes:
        body = build_request(method, params, version=version)
        request = (
            f"POST /sony/{service} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8") + body
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.host, self.port))
            sock.sendall(request)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError as exc:
            raise TransportError(f"{self.host}:{self.port} {exc}") from exc
        finally:
            sock.close()

    def call(self, method: str, params: list[Any] | None = None, *,
             service: str = "camera", version: str = "1.0") -> Any:
        """Call one method and return its result, or raise :class:`ApiError`."""
        try:
            raw = self._post(service, method, params, version)
        except TransportError as exc:
            self._record(service, method, params, version, None, str(exc))
            raise
        # Recorded before decoding, so a refusal or a malformed answer is kept too.
        self._record(service, method, params, version, raw, None)
        decoded = parse_response(raw, method)
        return decoded["result"] if "result" in decoded else decoded["results"]

    def _record(self, service: str, method: str, params: list[Any] | None, version: str,
                raw: bytes | None, failure: str | None) -> None:
        """Append one exchange to the transcript: the request as sent and the raw answer."""
        if self.transcript is None:
            return
        entry: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": self.host, "port": self.port, "service": service,
            "method": method, "params": params if params is not None else [],
            "version": version,
            "request": build_request(method, params, version=version).decode("utf-8"),
        }
        if raw is not None:
            entry["response_b64"] = base64.b64encode(raw).decode("ascii")
            entry["response_text"] = raw.decode("utf-8", errors="replace")
        if failure is not None:
            entry["transport_error"] = failure
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def available_api_list(self, service: str = "camera") -> list[str]:
        """The methods this camera exposes for a service, as the camera reports them."""
        result = self.call("getAvailableApiList", service=service)
        if isinstance(result, list) and result and isinstance(result[0], list):
            return list(result[0])
        if isinstance(result, list):
            return list(result)
        raise TransportError("getAvailableApiList returned an unexpected shape")

    def liveview_url(self) -> str:
        """The MJPEG stream URL, obtained by calling ``startLiveview`` with no arguments.

        This camera rejects ``startLiveview`` when it is given a version string; the working
        call passes no parameters at all, which was measured rather than assumed (E0659).
        """
        result = self.call("startLiveview")
        if isinstance(result, list) and result and isinstance(result[0], str):
            return result[0]
        raise TransportError("startLiveview returned no URL")