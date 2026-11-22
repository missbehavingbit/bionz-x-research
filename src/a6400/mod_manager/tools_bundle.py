"""Asset, toolchain, and PyInstaller-frozen environment resolver for the Mod Manager."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
from typing import Callable

from project_paths import PROJECT_ROOT
from src.a6400.targets.usb_debugger_compat import PAYLOAD_ASSETS, get_debugger_target
from tools.setup_usb_debugger import _download_bytes, ensure_payload_binaries, ensure_pmca_on_path

from .smod import parse_smod

RELOAD_MARKER = b"/tmp_bt/mlv2/reload"
# Official ILCE-6400 FW 2.00 libtestcmd.so ends at 0x2790 (vs 0x275c in CE Linux 8.2.6.0).
FW200_SAFE_MIN_SHIM_ADDR = 0x05662790

MLV2_RELEASE_REPO = "ivica00/bionz-x-research"
MLV2_RELEASE_TAG = "mlv2-layout-build-4"
MLV2_RELEASE_BASE_URL = f"https://github.com/{MLV2_RELEASE_REPO}/releases/download/{MLV2_RELEASE_TAG}"
MLV2_RELEASE_ASSETS: dict[str, dict[str, object]] = {
    "build.json": {
        "size": 1018,
        "sha256": "c3edf987e026e56a0f7b9b15ce6c1684419768acda6b5cf6eaefd4c35587cd3e",
        "url": f"{MLV2_RELEASE_BASE_URL}/build.json",
    },
    "core.bin": {
        "size": 7852,
        "sha256": "f58335566dd7f26552ca3e05b0fbf8407406b4fbd6354bde4e38d1bc5ee971ab",
        "url": f"{MLV2_RELEASE_BASE_URL}/core.bin",
    },
    "mlv2.req": {
        "size": 1124,
        "sha256": "99d255c58dc8547b38e1c2a835f122fa88f28a86666e11384b25187b25512f85",
        "url": f"{MLV2_RELEASE_BASE_URL}/mlv2.req",
    },
    "mlv2_guard_arm": {
        "size": 692532,
        "sha256": "740c480cbd676a128fafa728a6b8396d33662a0fff9166505b076e589b3436c7",
        "url": f"{MLV2_RELEASE_BASE_URL}/mlv2_guard_arm",
    },
    "shim.bin": {
        "size": 1000,
        "sha256": "87b9f1c546c375fd4ea22048b53d8131003d4a6ad362f243ea25367f569bd5fc",
        "url": f"{MLV2_RELEASE_BASE_URL}/shim.bin",
    },
}
ARRIVEDERCI_V5_ASSET: dict[str, object] = {
    "filename": "Arrivederci.mod",
    "version": "v5",
    "release_tag": MLV2_RELEASE_TAG,
    "size": 129444,
    "sha256": "46c41981e7b37231a6cadb0d033f514c5b05a17ecb38c85f68d909d64a0b64b7",
    "url": f"{MLV2_RELEASE_BASE_URL}/Arrivederci.mod",
}


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def bundle_root() -> Path:
    """Return the read-only resource root (PyInstaller ``_MEIPASS`` or repo root)."""
    if is_frozen():
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return PROJECT_ROOT


def repo_root(override: Path | None = None) -> Path:
    """Return the active workspace root (or bundle root when frozen)."""
    if override is not None:
        return Path(override).resolve()
    return bundle_root() if is_frozen() else PROJECT_ROOT


def user_data_dir(override: Path | None = None, *, root: Path | None = None) -> Path:
    """Return the writable directory for custom mods, settings, and install receipts."""
    if override is not None:
        path = Path(override)
    elif is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "BionzXModManager"
    else:
        path = repo_root(root) / "artifacts" / "mod-manager"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_settings(data_dir: Path) -> dict:
    """Load persisted manager settings without failing on corrupt JSON."""
    settings_path = Path(data_dir) / "settings.json"
    if not settings_path.is_file():
        return {}
    try:
        loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def save_settings(data_dir: Path, updates: dict) -> dict:
    """Persist settings updates atomically."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    current = load_settings(data_dir)
    current.update(updates)
    settings_path = data_dir / "settings.json"
    tmp_path = data_dir / "settings.json.tmp"
    tmp_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, settings_path)
    return current


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_pmca_dir(
    root: Path | None = None,
    data_dir: Path | None = None,
    bundle_dir: Path | None = None,
) -> Path | None:
    """Locate an existing ``Sony-PMCA-RE`` checkout or bundled ``pmca`` package."""
    base_root = repo_root(root)
    b_root = Path(bundle_dir).resolve() if bundle_dir is not None else bundle_root()
    candidates: list[Path] = [
        b_root,
        b_root / "Sony-PMCA-RE",
        b_root / "bundled_tools" / "Sony-PMCA-RE",
        base_root / "Sony-PMCA-RE",
        base_root.parent / "Sony-PMCA-RE",
        base_root.parent / "Sony-PMCA-RE-master",
        base_root / ".cache" / "Sony-PMCA-RE",
    ]
    if data_dir is not None:
        candidates.append(Path(data_dir) / ".cache" / "Sony-PMCA-RE")
        candidates.append(Path(data_dir) / "cache" / "Sony-PMCA-RE")
    for candidate in candidates:
        if (candidate / "pmca" / "usb" / "sony.py").is_file() and (candidate / "config.py").is_file():
            resolved = candidate.resolve()
            if str(resolved) not in sys.path:
                sys.path.insert(0, str(resolved))
            return resolved
    return None


def ensure_pmca(
    root: Path | None = None,
    data_dir: Path | None = None,
    fetcher: Callable[[str], bytes] | None = None,
) -> Path:
    """Ensure ``pmca`` is on ``sys.path``, downloading ``Sony-PMCA-RE`` if absent."""
    existing = find_pmca_dir(root=root, data_dir=data_dir)
    if existing is not None:
        return existing
    target_root = Path(data_dir) if is_frozen() and data_dir is not None else repo_root(root)
    if fetcher is None:
        return ensure_pmca_on_path(root=target_root)
    return ensure_pmca_on_path(root=target_root, fetcher=fetcher)


def configure_libusb_backend() -> dict[str, object]:
    """Check Python USB/crypto dependencies and register bundled libusb if available."""
    pyusb_ok = (
        importlib.util.find_spec("usb") is not None
        and importlib.util.find_spec("usb.core") is not None
    )
    crypto_ok = importlib.util.find_spec("Cryptodome") is not None
    elftools_ok = importlib.util.find_spec("elftools") is not None

    libusb_ok = False
    backend_source = None
    detail = None
    if pyusb_ok:
        try:
            import usb.backend.libusb1 as libusb1

            backend = None
            try:
                import libusb_package

                backend = libusb_package.get_libusb1_backend()
                if backend is not None:
                    backend_source = "libusb_package"
            except Exception:
                backend = None

            if backend is None:
                bundled_dll = bundle_root() / "libusb-1.0.dll"
                if bundled_dll.is_file():
                    backend = libusb1.get_backend(find_library=lambda _name: str(bundled_dll))
                    if backend is not None:
                        backend_source = str(bundled_dll)

            if backend is None:
                backend = libusb1.get_backend()
                if backend is not None:
                    backend_source = "system"

            libusb_ok = backend is not None
            if not libusb_ok:
                detail = "pyusb installed, but no libusb-1.0 backend DLL found"
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = "pyusb package not installed"

    missing = []
    if not pyusb_ok:
        missing.append("pyusb")
    if not libusb_ok and pyusb_ok:
        missing.append("libusb-1.0")
    if not crypto_ok:
        missing.append("pycryptodomex")
    if not elftools_ok:
        missing.append("pyelftools")

    pmca_deps: list[tuple[str, str]] = [
        ("axmlparserpy", "axmlparserpy"),
        ("tlslite", "tlslite-ng"),
        ("yaml", "PyYAML"),
        ("asn1crypto", "asn1crypto"),
        ("certifi", "certifi"),
    ]
    if os.name == "nt":
        pmca_deps.extend((("win32file", "pywin32"), ("comtypes", "comtypes")))
    missing_pmca = [pkg for mod, pkg in pmca_deps if importlib.util.find_spec(mod) is None]
    missing.extend(missing_pmca)
    if missing_pmca and not detail:
        detail = "Missing PMCA runtime package(s): " + ", ".join(missing_pmca)
    elif not detail and missing:
        detail = "Missing: " + ", ".join(missing)

    pmca_deps_ok = not missing_pmca
    return {
        "pyusb_ok": pyusb_ok,
        "libusb_ok": libusb_ok,
        "crypto_ok": crypto_ok,
        "elftools_ok": elftools_ok,
        "pmca_deps_ok": pmca_deps_ok,
        "backend_source": backend_source,
        "ready": bool(pyusb_ok and libusb_ok and crypto_ok and elftools_ok and pmca_deps_ok),
        "detail": detail or "USB transport, Windows MSC/WPD & cryptography ready",
    }


def is_official_mlv2_release(verified: dict[str, object]) -> bool:
    """Return True when verified Mod Loader v2 hashes match the pinned official release."""
    return (
        verified.get("core_sha256") == MLV2_RELEASE_ASSETS["core.bin"]["sha256"]
        and verified.get("request_sha256") == MLV2_RELEASE_ASSETS["mlv2.req"]["sha256"]
        and verified.get("guard_sha256") == MLV2_RELEASE_ASSETS["mlv2_guard_arm"]["sha256"]
    )


def validate_mlv2_build(build_dir: Path) -> dict[str, object]:
    """Strictly verify a Mod Loader v2 build directory against its ``build.json`` pins."""
    build_dir = Path(build_dir).resolve()
    required = ("build.json", "core.bin", "mlv2.req", "mlv2_guard_arm")
    missing = [name for name in required if not (build_dir / name).is_file()]
    if missing:
        raise ValueError(f"Missing Mod Loader v2 file(s): {', '.join(missing)}")

    info = json.loads((build_dir / "build.json").read_text(encoding="utf-8"))
    core_bytes = (build_dir / "core.bin").read_bytes()
    req_bytes = (build_dir / "mlv2.req").read_bytes()
    guard_bytes = (build_dir / "mlv2_guard_arm").read_bytes()

    core_sha = _sha256_bytes(core_bytes)
    req_sha = _sha256_bytes(req_bytes)
    guard_sha = _sha256_bytes(guard_bytes)

    expected_core = info.get("core", {}).get("sha256")
    expected_req = info.get("request", {}).get("sha256")
    if not expected_core or core_sha != expected_core:
        raise ValueError("core.bin SHA-256 does not match build.json")
    if not expected_req or req_sha != expected_req:
        raise ValueError("mlv2.req SHA-256 does not match build.json")
    if len(req_bytes) < 28:
        raise ValueError("mlv2.req is truncated")

    shim_addr = struct.unpack_from("<I", req_bytes, 8)[0]
    entries = struct.unpack_from("<3I", req_bytes, 16)
    result: dict[str, object] = {
        "available": True,
        "path": str(build_dir),
        "core_sha256": core_sha,
        "request_sha256": req_sha,
        "guard_sha256": guard_sha,
        "shim_addr": shim_addr,
        "shim_addr_hex": f"{shim_addr:#010x}",
        "entries": [f"{addr:#010x}" for addr in entries],
        "entry_ints": list(entries),
        "reload_capable": RELOAD_MARKER in core_bytes,
        "error": None,
    }
    official = is_official_mlv2_release(result)
    result["is_official_release"] = official
    result["release_tag"] = MLV2_RELEASE_TAG if official else None
    return result


def resolve_mlv2_build(
    override_dir: Path | str | None = None,
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    bundle_dir: Path | None = None,
) -> dict[str, object]:
    """Locate and verify the active Mod Loader v2 build (override -> saved -> bundled -> release -> artifacts)."""
    base_root = repo_root(root)
    b_root = Path(bundle_dir).resolve() if bundle_dir is not None else bundle_root()
    candidates: list[tuple[str, Path]] = []

    if override_dir:
        candidates.append(("override", Path(override_dir)))
    if data_dir is not None:
        saved_dir = load_settings(data_dir).get("mlv2_dir")
        if saved_dir:
            candidates.append(("saved", Path(saved_dir)))

    candidates.append(("bundled", b_root / "bundled_tools" / "mlv2"))
    candidates.append(("release", base_root / ".cache" / MLV2_RELEASE_TAG))
    if data_dir is not None:
        candidates.append(("release", Path(data_dir) / "cache" / MLV2_RELEASE_TAG))

    tests_dir = base_root / "artifacts" / "tests"
    if tests_dir.is_dir():
        mlv2_dirs = sorted(
            (p for p in tests_dir.iterdir() if p.is_dir() and p.name.startswith("mlv2-")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for item in mlv2_dirs:
            candidates.append(("artifacts", item))

    first_explicit_error: str | None = None
    auto_valid: list[dict[str, object]] = []
    for source, candidate in candidates:
        if not candidate.is_dir():
            if source in ("override", "saved") and first_explicit_error is None:
                first_explicit_error = f"Configured MLv2 directory not found: {candidate}"
            continue
        try:
            verified = validate_mlv2_build(candidate)
            verified["source"] = source
            if source in ("override", "saved", "bundled"):
                return verified
            auto_valid.append(verified)
        except Exception as exc:
            if source in ("override", "saved") and first_explicit_error is None:
                first_explicit_error = f"Configured MLv2 build invalid ({candidate}): {exc}"

    if auto_valid:
        for verified in auto_valid:
            if int(verified.get("shim_addr") or 0) >= FW200_SAFE_MIN_SHIM_ADDR:
                return verified
        return auto_valid[0]

    return {
        "available": False,
        "path": None,
        "source": None,
        "core_sha256": None,
        "request_sha256": None,
        "guard_sha256": None,
        "shim_addr": None,
        "shim_addr_hex": None,
        "entries": [],
        "entry_ints": [],
        "reload_capable": False,
        "is_official_release": False,
        "release_tag": None,
        "error": first_explicit_error
        or "No verified Mod Loader v2 build (core.bin, mlv2.req, mlv2_guard_arm, build.json) found.",
    }


def _atomic_write_verified(dest: Path, data: bytes, expected_size: int, expected_sha: str, label: str) -> None:
    if len(data) != expected_size or _sha256_bytes(data) != expected_sha:
        raise RuntimeError(
            f"{label} failed integrity verification "
            f"(got {len(data)} B / {_sha256_bytes(data)}, expected {expected_size} B / {expected_sha})"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def ensure_mlv2_release(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Download and SHA-256 verify the pinned official Mod Loader v2 release."""
    base_root = repo_root(root)
    d_dir = user_data_dir(data_dir, root=base_root)
    dest_dir = (d_dir / "cache" / MLV2_RELEASE_TAG) if is_frozen() else (base_root / ".cache" / MLV2_RELEASE_TAG)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dl = fetcher or _download_bytes

    for filename, meta in MLV2_RELEASE_ASSETS.items():
        expected_size = int(meta["size"])
        expected_sha = str(meta["sha256"])
        target = dest_dir / filename
        if target.is_file():
            existing = target.read_bytes()
            if len(existing) == expected_size and _sha256_bytes(existing) == expected_sha:
                continue
        if progress_cb:
            progress_cb(f"Downloading {MLV2_RELEASE_TAG}/{filename}...")
        raw = dl(str(meta["url"]))
        _atomic_write_verified(target, raw, expected_size, expected_sha, f"{MLV2_RELEASE_TAG}/{filename}")

    verified = validate_mlv2_build(dest_dir)
    verified["source"] = "release"
    return verified


def find_arrivederci_v5(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    bundle_dir: Path | None = None,
) -> Path | None:
    """Locate a local SHA-256-verified Arrivederci v5 binary if present."""
    base_root = repo_root(root)
    b_root = Path(bundle_dir).resolve() if bundle_dir is not None else bundle_root()
    expected_size = int(ARRIVEDERCI_V5_ASSET["size"])
    expected_sha = str(ARRIVEDERCI_V5_ASSET["sha256"])

    candidates: list[Path] = [
        b_root / "bundled_mods" / "Arrivederci.mod",
        base_root / ".cache" / "mods" / "Arrivederci.mod",
        base_root / "mods" / "arri_isp" / "arrivederci" / "Arrivederci.mod",
    ]
    if data_dir is not None:
        candidates.insert(1, Path(data_dir) / "cache" / "mods" / "Arrivederci.mod")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        raw = candidate.read_bytes()
        if len(raw) == expected_size and _sha256_bytes(raw) == expected_sha:
            return candidate.resolve()
    return None


def ensure_arrivederci_v5(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> Path:
    """Ensure official Arrivederci v5 (.mod) exists locally and matches its pinned SHA-256."""
    base_root = repo_root(root)
    d_dir = user_data_dir(data_dir, root=base_root)
    existing = find_arrivederci_v5(root=base_root, data_dir=d_dir)
    if existing is not None:
        return existing

    if progress_cb:
        progress_cb(f"Downloading {MLV2_RELEASE_TAG}/Arrivederci.mod (v5)...")
    dl = fetcher or _download_bytes
    raw = dl(str(ARRIVEDERCI_V5_ASSET["url"]))
    expected_size = int(ARRIVEDERCI_V5_ASSET["size"])
    expected_sha = str(ARRIVEDERCI_V5_ASSET["sha256"])
    if len(raw) != expected_size or _sha256_bytes(raw) != expected_sha:
        raise RuntimeError(
            f"Arrivederci.mod v5 failed integrity check "
            f"(got {len(raw)} B / {_sha256_bytes(raw)}, expected {expected_size} B / {expected_sha})"
        )
    parse_smod(raw, filename="Arrivederci.mod")

    dest = (
        (d_dir / "cache" / "mods" / "Arrivederci.mod")
        if is_frozen()
        else (base_root / ".cache" / "mods" / "Arrivederci.mod")
    )
    _atomic_write_verified(dest, raw, expected_size, expected_sha, "Arrivederci.mod (v5)")
    return dest.resolve()


def debugger_payload_status(
    root: Path | None = None,
    data_dir: Path | None = None,
    bundle_dir: Path | None = None,
) -> dict[str, object]:
    """Check whether pinned USB debugger payloads are present and SHA-256 verified locally."""
    base_root = repo_root(root)
    b_root = Path(bundle_dir).resolve() if bundle_dir is not None else bundle_root()
    search_roots: list[Path] = [base_root, b_root]
    if data_dir is not None:
        search_roots.append(Path(data_dir))
    spec = get_debugger_target("ilce_6400")
    files_info: list[dict[str, object]] = []
    all_ready = True
    for rel in (spec.payload_daemon, spec.payload_launcher):
        meta = PAYLOAD_ASSETS[rel]
        expected_sha = str(meta["sha256"])
        expected_size = int(meta["size"])
        found_path = None
        verified = False
        error = None
        for probe_root in search_roots:
            probe = probe_root / rel
            if probe.is_file():
                found_path = probe
                data = probe.read_bytes()
                if len(data) == expected_size and _sha256_bytes(data) == expected_sha:
                    verified = True
                else:
                    error = "SHA-256 mismatch"
                break
        if not verified:
            all_ready = False
        files_info.append(
            {
                "name": str(meta["filename"]),
                "rel": rel,
                "present": found_path is not None,
                "verified": verified,
                "path": str(found_path) if found_path else None,
                "sha256": expected_sha,
                "error": error,
            }
        )
    return {"ready": all_ready, "files": files_info}


def resolve_debugger_payload_root(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    bundle_dir: Path | None = None,
) -> Path:
    """Return the root containing verified debugger payloads, preferring writable ``data_dir`` if frozen."""
    base_root = repo_root(root)
    b_root = Path(bundle_dir).resolve() if bundle_dir is not None else bundle_root()
    candidates = [base_root, b_root]
    if data_dir is not None:
        candidates.append(Path(data_dir))
    spec = get_debugger_target("ilce_6400")
    for candidate in candidates:
        if all((candidate / rel).is_file() for rel in (spec.payload_daemon, spec.payload_launcher)):
            return candidate
    return Path(data_dir) if is_frozen() and data_dir is not None else base_root


def prepare_tools(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Download/verify PMCA, USB debugger binaries, official MLv2 release, and Arrivederci v5."""
    base_root = repo_root(root)
    d_dir = user_data_dir(data_dir, root=base_root)
    if progress_cb:
        progress_cb("Ensuring Sony-PMCA-RE is available...")
    ensure_pmca(root=base_root, data_dir=d_dir, fetcher=fetcher)

    spec = get_debugger_target("ilce_6400")
    payload_root = resolve_debugger_payload_root(root=base_root, data_dir=d_dir)
    if progress_cb:
        progress_cb("Verifying/downloading pinned USB debugger binaries...")
    if fetcher is None:
        ensure_payload_binaries(spec, root=payload_root)
    else:
        ensure_payload_binaries(spec, root=payload_root, fetcher=fetcher)

    if progress_cb:
        progress_cb(f"Ensuring official Mod Loader v2 ({MLV2_RELEASE_TAG})...")
    ensure_mlv2_release(root=base_root, data_dir=d_dir, fetcher=fetcher, progress_cb=progress_cb)

    if progress_cb:
        progress_cb(f"Ensuring official Arrivederci v5 ({MLV2_RELEASE_TAG})...")
    ensure_arrivederci_v5(root=base_root, data_dir=d_dir, fetcher=fetcher, progress_cb=progress_cb)
    return get_toolchain_status(root=base_root, data_dir=d_dir)


def get_toolchain_status(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    mlv2_override: Path | str | None = None,
    bundle_dir: Path | None = None,
) -> dict[str, object]:
    """Collect a UI-ready status summary for every prerequisite component."""
    base_root = repo_root(root)
    d_dir = user_data_dir(data_dir, root=base_root)
    pmca_path = find_pmca_dir(root=base_root, data_dir=d_dir, bundle_dir=bundle_dir)
    usb_info = configure_libusb_backend()
    payloads = debugger_payload_status(root=base_root, data_dir=d_dir, bundle_dir=bundle_dir)
    mlv2 = resolve_mlv2_build(
        override_dir=mlv2_override,
        root=base_root,
        data_dir=d_dir,
        bundle_dir=bundle_dir,
    )
    v5_path = find_arrivederci_v5(root=base_root, data_dir=d_dir, bundle_dir=bundle_dir)
    return {
        "frozen": is_frozen(),
        "pmca": {
            "ready": pmca_path is not None,
            "path": str(pmca_path) if pmca_path else None,
        },
        "usb": usb_info,
        "debugger_payloads": payloads,
        "mlv2": mlv2,
        "arrivederci_v5": {
            "ready": v5_path is not None,
            "version": "v5",
            "release_tag": MLV2_RELEASE_TAG,
            "path": str(v5_path) if v5_path else None,
            "sha256": ARRIVEDERCI_V5_ASSET["sha256"],
        },
        "data_dir": str(d_dir),
    }
