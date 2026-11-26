#!/usr/bin/env python3
"""What of Sony's own client can be ported to this camera, decided by the camera's own answers.

"Port the whole app" is not one task and cannot be answered with a yes. Imaging Edge Mobile is
2,664 source files across remote shooting, image transfer, a content browser, connection setup,
QR pairing, Bluetooth, NFC and location tagging. Only part of that reaches a camera over Wi-Fi,
and a smaller part again is reachable on a body whose running application advertises 47 methods
(E0659).

So this module answers the question that can be answered offline and exactly: for every web-API
name the shipped client enumerates, is it callable on this body? The output is a port map —
*portable now*, *blocked by the camera's advertised surface*, or *not a camera-API feature at
all* — and every row carries the method name, so a decision can be checked instead of believed.

Inputs are two committed artifacts: the method list from the client (E0660) and the body's own
``getAvailableApiList`` answer (E0659). No camera and no APK are needed to run it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: Features of the shipped client that are not a single web-API call, with the reason. Anything
#: not listed here and not in the client's method enum is not modelled at all.
NON_API_FEATURES: dict[str, str] = {
    "transfer": "copies files off the camera; needs avContent or PTP, not the camera service",
    "contentviewer": "browses and plays what is on the card; avContent methods",
    "qr": "reads the access-point QR code with the phone's camera; no camera API involved",
    "bluetooth": "pairs over BLE before Wi-Fi; system Bluetooth, not ScalarWebAPI",
    "nfc": "one-touch pairing on the phone's NFC radio",
    "location": "writes the phone's GPS into saved files; needs the transfer path",
    "connect": "joins the access point; an operating-system action",
    "wifi": "enumerates and joins networks; an operating-system action",
    "setup": "first-run pairing flow built from the above",
    "cameralist": "lists paired bodies from local storage",
    "settings": "app preferences stored on the phone",
}

#: The client's method enum groups, which map onto the shooting screen. A group is portable when
#: the body advertises the methods behind it.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "live view": ("startLiveview", "stopLiveview"),
    "half-press focus/AE": ("actHalfPressShutter", "cancelHalfPressShutter"),
    "single shot": ("actTakePicture",),
    "continuous shooting": ("startContShooting", "stopContShooting"),
    "ISO": ("getIsoSpeedRate", "setIsoSpeedRate"),
    "aperture": ("getFNumber", "setFNumber"),
    "shutter speed": ("getShutterSpeed", "setShutterSpeed"),
    "white balance": ("getWhiteBalance", "setWhiteBalance"),
    "exposure compensation": ("getExposureCompensation", "setExposureCompensation"),
    "exposure mode": ("getExposureMode", "setExposureMode"),
    "focus mode": ("getFocusMode", "setFocusMode"),
    "zoom": ("actZoom", "getZoomSetting", "setZoomSetting"),
    "still size": ("getStillSize", "setStillSize"),
    "still quality": ("getStillQuality", "setStillQuality"),
    "movie recording": ("startMovieRec", "stopMovieRec"),
    "self timer": ("getSelfTimer", "setSelfTimer"),
    "flash mode": ("getFlashMode", "setFlashMode"),
    "drive mode": ("getContShootingMode", "setContShootingMode"),
    "touch focus": ("setTouchAFPosition", "getTouchAFPosition", "cancelTouchAFPosition"),
    "tracking focus": ("setTrackingFocus", "getTrackingFocus", "cancelTrackingFocus"),
    "manual focus": ("setManualFocus", "actFocalPosition"),
    "creative style": ("getCreativeStyle", "setCreativeStyle"),
    "picture effect": ("getPictureEffect", "setPictureEffect"),
    "colour setting": ("getColorSetting", "setColorSetting"),
    "scene selection": ("getSceneSelection", "setSceneSelection"),
    "steady mode": ("getSteadyMode", "setSteadyMode"),
    "view angle": ("getViewAngle", "setViewAngle"),
    "live view size": ("getLiveviewSize", "setLiveviewSize"),
    "storage": ("getStorageInformation",),
}


def load_methods(client_record: Path) -> set[str]:
    """The web-API names the shipped client enumerates."""
    record = json.loads(client_record.read_text(encoding="utf-8"))
    methods = record.get("what_the_app_does", {}).get("methods")
    if isinstance(methods, list) and methods:
        return set(methods)
    raise SystemExit(f"{client_record} carries no method list")


def load_live(session_record: Path) -> list[str]:
    """The methods this body advertises, from its own recorded answer."""
    record = json.loads(session_record.read_text(encoding="utf-8"))
    return list(record.get("api_list", []))


def port_map(live: list[str], client_methods: set[str]) -> dict[str, Any]:
    """Three buckets: portable now, blocked by the body, and not a camera-API feature."""
    advertised = set(live)
    features: list[dict[str, Any]] = []
    for label, methods in FEATURE_GROUPS.items():
        present = [m for m in methods if m in advertised]
        missing = [m for m in methods if m not in advertised]
        if not missing:
            state = "portable now"
        elif not present:
            state = "blocked by the body"
        else:
            state = "partly portable"
        features.append({
            "feature": label,
            "state": state,
            "advertised": present,
            "missing": missing,
        })
    blocked_client_methods = sorted(client_methods - advertised)
    return {
        "body_advertises": len(advertised),
        "client_enumerates": len(client_methods),
        "callable_of_client": len(client_methods & advertised),
        "features": features,
        "blocked_client_methods": blocked_client_methods,
        "non_api_features": NON_API_FEATURES,
    }


def summarise(result: dict[str, Any]) -> str:
    lines = []
    portable = [f for f in result["features"] if f["state"] == "portable now"]
    partial = [f for f in result["features"] if f["state"] == "partly portable"]
    blocked = [f for f in result["features"] if f["state"] == "blocked by the body"]
    lines.append(
        f"{result['callable_of_client']} of the {result['client_enumerates']} methods the shipped "
        f"client enumerates are callable on this body, which advertises "
        f"{result['body_advertises']}:"
    )
    lines.append(f"  portable now ({len(portable)}): " + ", ".join(f["feature"] for f in portable))
    if partial:
        lines.append(f"  partly portable ({len(partial)}): " + ", ".join(
            f"{f['feature']} (missing {', '.join(f['missing'])})" for f in partial))
    lines.append(f"  blocked by the body ({len(blocked)}): " + ", ".join(
        f["feature"] for f in blocked))
    lines.append(
        "A blocked feature is not a missing piece of this port: the camera is not serving it "
        "over the network while its current application is registered."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client", type=Path,
                        default=Path("docs/evidence/cross_model/imaging-edge-mobile-control.json"))
    parser.add_argument("--session", type=Path,
                        default=Path("docs/evidence/cross_model/a6400-scalarwebapi-session.json"))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    result = port_map(load_live(args.session), load_methods(args.client))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(summarise(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
