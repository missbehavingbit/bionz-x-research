#!/usr/bin/env python3
"""A command-line remote for the camera over its own Wi-Fi.

Built on :mod:`scalarwebapi`, which is only the transport. This file is the policy: what the
remote offers, what it refuses to pretend, and how it reports a method the camera does not
expose.

The honest part matters more than the feature list. What the camera advertises follows its own
state (E0664): at drive mode ``Continuous`` the owner's ILCE-6400 lists 47 methods with
``startContShooting`` and without the shutter (``actTakePicture``); at ``Single`` it lists 50,
with the shutter. So this remote:

* discovers its own verbs from the camera at start-up instead of assuming a fixed set, and
* refuses a command the camera did not advertise, saying so, rather than sending it and
  reporting a failure that reads like a bug.

Two facts measured on hardware shape the stream handling: ``startLiveview`` must be called
with no parameters, and the stream is MJPEG at 640x424, roughly 38 KB per frame.

Offline mode exists so the formatting is testable and so the tool is useful before a camera is
attached: ``--offline`` prints a recorded session and contacts nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scalarwebapi import (  # noqa: E402
    DEFAULT_HOST,
    DEFAULT_PORT,
    ApiError,
    ScalarWebApi,
    TransportError,
)

#: Commands this remote can perform, and the camera method each one needs. A command is
#: offered only when its method appears in the camera's own advertised list, so the remote
#: cannot claim a capability the camera is not currently registering.
COMMANDS: dict[str, str] = {
    "shoot": "actTakePicture",
    "half-press": "actHalfPressShutter",
    "half-release": "cancelHalfPressShutter",
    "burst-start": "startContShooting",
    "burst-stop": "stopContShooting",
    "live-start": "startLiveview",
    "live-stop": "stopLiveview",
}

#: Settable settings: command name -> (getter, setter, argument name).
SETTINGS: dict[str, tuple[str, str, str]] = {
    "iso": ("getIsoSpeedRate", "setIsoSpeedRate", "iso"),
    "aperture": ("getFNumber", "setFNumber", "fnumber"),
    "shutter": ("getShutterSpeed", "setShutterSpeed", "shutterspeed"),
    "wb": ("getWhiteBalance", "setWhiteBalance", "whitebalance"),
    "ev": ("getExposureCompensation", "setExposureCompensation", "exposurecompensation"),
    "drive": ("getContShootingMode", "setContShootingMode", "contShootingMode"),
}


def shutter_hint(api_list: Iterable[str]) -> str | None:
    """Why the shutter is missing, in the camera's own terms, or ``None`` when it is offered.

    Measured on the owner's body (E0664): the drive mode picks the shutter. At ``Continuous``
    the camera offers ``startContShooting`` and withholds ``actTakePicture``; setting the drive
    mode to ``Single`` brings the shutter back within a second. Sending ``actTakePicture``
    while it is withheld answers error 1, which says nothing about the cause.
    """
    advertised = set(api_list)
    if COMMANDS["shoot"] in advertised:
        return None
    if COMMANDS["burst-start"] in advertised:
        hint = ("the camera is in a continuous drive mode, where it offers burst-start instead "
                "of the shutter")
        if "setContShootingMode" in advertised:
            hint += "; 'drive Single' brings the shutter back"
        return hint
    return ("the camera does not offer the shutter in its current state; sending it anyway "
            "returns error 1 (state refusal), not a success")


def setter_params(setter: str, value: str) -> list[Any]:
    """The ``params`` array a setter takes, built from one value as a person picks it.

    The shapes are Sony's own, read from ``CameraClient`` in the shipped 7.6.0 client (E0660);
    sending every setter ``[value]`` is wrong for three of them:

    * ``setWhiteBalance`` takes three positional values: the mode, whether a colour
      temperature follows, and the temperature (``-1`` when it does not).
    * ``setContShootingMode`` takes one object, ``{"contShootingMode": value}``.
    * ``setExposureCompensation`` takes an integer *index*, not an EV string. An EV value such
      as ``"0.7"`` is converted at the 1/3 EV step that the a6400's recorded value list shows;
      that the body accepts the index form is the client's contract, not yet a hardware
      measurement.

    Raises ``ValueError`` for an exposure value that is not a number.
    """
    if setter == "setWhiteBalance":
        return [value, False, -1]
    if setter == "setContShootingMode":
        return [{"contShootingMode": value}]
    if setter == "setExposureCompensation":
        try:
            return [int(value)]
        except ValueError:
            return [round(float(value) * 3)]
    return [value]


def value_from_setter_params(setter: str, params: Any) -> str:
    """The inverse of :func:`setter_params`: the value a well-formed params array carries.

    Raises ``ValueError`` for any other shape, which is how a replayed camera refuses a call the
    real one would answer with error 3 (Illegal Argument).
    """
    if not isinstance(params, list):
        raise ValueError("params is not a list")
    if setter == "setWhiteBalance":
        if (len(params) == 3 and isinstance(params[0], str) and isinstance(params[1], bool)
                and isinstance(params[2], int) and not isinstance(params[2], bool)):
            return params[0]
        raise ValueError("setWhiteBalance takes [mode, bool, int]")
    if setter == "setContShootingMode":
        if (len(params) == 1 and isinstance(params[0], dict)
                and isinstance(params[0].get("contShootingMode"), str)):
            return params[0]["contShootingMode"]
        raise ValueError('setContShootingMode takes [{"contShootingMode": value}]')
    if setter == "setExposureCompensation":
        if len(params) == 1 and isinstance(params[0], int) and not isinstance(params[0], bool):
            return str(params[0])
        raise ValueError("setExposureCompensation takes [int index]")
    if len(params) == 1 and isinstance(params[0], str):
        return params[0]
    raise ValueError(f"{setter} takes [value]")


def supported_commands(api_list: Iterable[str]) -> list[str]:
    """The commands whose method the camera actually advertises."""
    advertised = set(api_list)
    return [name for name, method in COMMANDS.items() if method in advertised]


def unsupported_commands(api_list: Iterable[str]) -> list[str]:
    """The commands the camera cannot serve right now, named so the gap is visible."""
    advertised = set(api_list)
    return [name for name, method in COMMANDS.items() if method not in advertised]


def describe_capability(api_list: list[str]) -> str:
    """One sentence stating what this camera exposes and what is therefore refused."""
    have = supported_commands(api_list)
    lack = unsupported_commands(api_list)
    lines = [f"camera exposes {len(api_list)} methods"]
    lines.append("available: " + (", ".join(have) if have else "none"))
    if lack:
        lines.append("refused (method not advertised): " + ", ".join(lack))
    hint = shutter_hint(api_list)
    if hint:
        lines.append(hint)
    return "\n".join(lines)


def jpeg_frames(stream: bytes) -> list[bytes]:
    """Every complete JPEG in a byte buffer, in order.

    The stream is a concatenation of JPEGs with no length prefix, so a frame is delimited by
    its own markers. An unterminated tail is dropped rather than returned half-written.
    """
    frames: list[bytes] = []
    position = 0
    while True:
        start = stream.find(b"\xff\xd8\xff", position)
        if start < 0:
            break
        end = stream.find(b"\xff\xd9", start)
        if end < 0:
            break
        frames.append(stream[start:end + 2])
        position = end + 2
    return frames


def read_stream(url: str, *, frames: int = 1, timeout: float = 15.0) -> bytes:
    """Read until ``frames`` complete JPEGs have arrived, then stop."""
    request = urllib.request.Request(url, headers={"User-Agent": "a6400-remote"})
    buffer = b""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while len(jpeg_frames(buffer)) < frames:
            chunk = response.read(65536)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 8 * 1024 * 1024:
                break
    return buffer


def run_offline(path: Path, command: str) -> int:
    """Format a recorded session so the report shape is testable without a camera."""
    recorded = json.loads(path.read_text(encoding="utf-8"))
    api_list = recorded.get("api_list", [])
    print(describe_capability(api_list))
    if command:
        answer = recorded.get("answers", {}).get(command)
        print(f"\n{command}: {json.dumps(answer) if answer is not None else 'not recorded'}")
    return 0


def run_live(host: str, port: int, command: str, argument: str | None,
             transcript: Path | None = None) -> int:
    api = ScalarWebApi(host=host, port=port, transcript=transcript)
    try:
        api_list = api.available_api_list()
    except (TransportError, ApiError) as exc:
        print(f"cannot reach the camera at {host}:{port}: {exc}", file=sys.stderr)
        return 2

    print(describe_capability(api_list))

    if not command:
        return 0

    if command in SETTINGS:
        getter, setter, _param = SETTINGS[command]
        needed = getter if argument is None else setter
        if needed not in set(api_list):
            print(f"{command}: refused before sending. The camera does not advertise {needed}.",
                  file=sys.stderr)
            return 1
        try:
            if argument is None:
                print(f"{command}: {json.dumps(api.call(getter))}")
                return 0
            api.call(setter, setter_params(setter, argument))
            print(f"{command} set to {argument}; now {json.dumps(api.call(getter))}")
            return 0
        except ValueError as exc:
            print(f"{command}: {argument!r} is not a valid value: {exc}", file=sys.stderr)
            return 2
        except (ApiError, TransportError) as exc:
            print(f"{command}: {exc}", file=sys.stderr)
            return 1

    method = COMMANDS.get(command)
    if method is None:
        print(f"unknown command {command!r}; known: {', '.join(sorted(COMMANDS) + sorted(SETTINGS))}",
              file=sys.stderr)
        return 2

    if method not in set(api_list):
        print(
            f"{command}: refused before sending. The camera does not advertise {method} "
            f"among its {len(api_list)} methods, so it cannot be performed right now. "
            "Sending it anyway returns error 1, which reads like a fault in the remote and is not.",
            file=sys.stderr,
        )
        if command == "shoot":
            print(shutter_hint(api_list), file=sys.stderr)
        return 1

    try:
        result = api.call(method)
    except (ApiError, TransportError) as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 1

    if command == "live-start" and isinstance(result, list) and result:
        url = result[0]
        print(f"live view: {url}")
        try:
            frame = read_stream(url)
        except OSError as exc:      # URLError and socket timeouts are OSError
            print(f"live view stream failed: {exc}", file=sys.stderr)
            return 1
        found = jpeg_frames(frame)
        if found:
            print(f"received {len(found)} frame(s), first {len(found[0])} bytes")
        else:
            print("stream opened but no complete frame arrived")
        return 0

    print(f"{command}: {json.dumps(result)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="",
                        help="one of: " + ", ".join(sorted(COMMANDS) + sorted(SETTINGS)))
    parser.add_argument("argument", nargs="?", default=None,
                        help="new value for a setting, e.g. 400 for iso")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--offline", type=Path, default=None,
                        help="format a recorded session instead of contacting a camera")
    parser.add_argument("--transcript", type=Path, default=None,
                        help="append every call and the camera's raw answer to this JSONL file")
    args = parser.parse_args(argv)

    if args.offline:
        return run_offline(args.offline, args.command)
    return run_live(args.host, args.port, args.command, args.argument, args.transcript)


if __name__ == "__main__":
    sys.exit(main())