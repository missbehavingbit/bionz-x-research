"""Removable SD card staging and SHA-256 verified mod synchronization."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable

from .smod import validate_mod_filename

DRIVE_REMOVABLE = 2
SONY_MARKERS = ("DCIM", "PRIVATE", "AVF_INFO")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _detect_windows_removable_drives() -> list[dict[str, object]]:
    drives: list[dict[str, object]] = []
    if os.name != "nt":
        return drives
    import ctypes

    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    for idx in range(26):
        if not (bitmask & (1 << idx)):
            continue
        letter = chr(ord("A") + idx)
        root_str = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root_str) != DRIVE_REMOVABLE:
            continue
        root_path = Path(root_str)
        if not root_path.exists():
            continue
        vol_buf = ctypes.create_unicode_buffer(261)
        fs_buf = ctypes.create_unicode_buffer(64)
        ok = kernel32.GetVolumeInformationW(
            root_str, vol_buf, len(vol_buf), None, None, None, fs_buf, len(fs_buf)
        )
        label = vol_buf.value if ok else ""
        fs_type = fs_buf.value if ok else ""
        try:
            usage = shutil.disk_usage(root_path)
            total_mb = round(usage.total / (1024 * 1024))
            free_mb = round(usage.free / (1024 * 1024))
        except OSError:
            total_mb = 0
            free_mb = 0
        drives.append(
            {
                "root": str(root_path),
                "letter": letter,
                "label": label or "Removable Drive",
                "fs_type": fs_type or "Unknown",
                "total_mb": total_mb,
                "free_mb": free_mb,
            }
        )
    return drives


def _inspect_sd_root(entry: dict[str, object]) -> dict[str, object]:
    root = Path(str(entry["root"]))
    markers = [m for m in SONY_MARKERS if (root / m).is_dir()]
    mods_dir = root / "mods"
    existing_mods: list[dict[str, object]] = []
    if mods_dir.is_dir():
        for mod_file in sorted(mods_dir.glob("*.mod"), key=lambda p: p.name.lower()):
            try:
                data = mod_file.read_bytes()
                existing_mods.append(
                    {
                        "filename": mod_file.name,
                        "bytes": len(data),
                        "sha256": _sha256_bytes(data),
                    }
                )
            except OSError:
                continue
    return {
        **entry,
        "sony_markers": markers,
        "looks_like_sony_sd": bool(markers),
        "has_mods_dir": mods_dir.is_dir(),
        "existing_mods": existing_mods,
    }


def list_sd_drives(
    drive_provider: Callable[[], list[dict[str, object]]] | None = None,
) -> list[dict[str, object]]:
    """List removable drives safe for SD staging (never returns fixed system disks)."""
    raw = drive_provider() if drive_provider is not None else _detect_windows_removable_drives()
    return [_inspect_sd_root(item) for item in raw]


def _resolve_mods_dir(drive_root: Path) -> Path:
    root = Path(drive_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"SD drive root does not exist: {drive_root}")
    mods_dir = (root / "mods").resolve()
    if not mods_dir.is_relative_to(root):
        raise ValueError("Invalid SD mods directory path")
    return mods_dir


def plan_sd_sync(
    drive_root: Path | str,
    desired_mods: list[dict[str, object]],
    managed_filenames: set[str] | frozenset[str],
) -> dict[str, object]:
    """Compute the exact diff required to sync toggled-ON mods to ``<drive>/mods/``."""
    root = Path(drive_root).resolve()
    mods_dir = _resolve_mods_dir(root)
    managed_lower = {name.lower() for name in managed_filenames}

    desired_by_lower: dict[str, dict[str, object]] = {}
    for mod in desired_mods:
        fname = validate_mod_filename(str(mod["filename"]))
        src = Path(str(mod["path"])).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Mod binary missing on host: {src}")
        sha = _sha256_bytes(src.read_bytes())
        desired_by_lower[fname.lower()] = {
            "id": mod["id"],
            "title": mod["title"],
            "filename": fname,
            "source_path": str(src),
            "sha256": sha,
            "bytes": src.stat().st_size,
        }

    existing_by_lower: dict[str, tuple[Path, str]] = {}
    if mods_dir.is_dir():
        for existing in sorted(mods_dir.glob("*.mod"), key=lambda p: p.name.lower()):
            existing_by_lower[existing.name.lower()] = (
                existing,
                _sha256_bytes(existing.read_bytes()),
            )

    to_add: list[dict[str, object]] = []
    to_update: list[dict[str, object]] = []
    to_keep: list[dict[str, object]] = []
    to_remove: list[dict[str, object]] = []

    for key, item in desired_by_lower.items():
        if key not in existing_by_lower:
            to_add.append(item)
        else:
            _existing_path, existing_sha = existing_by_lower[key]
            if existing_sha == item["sha256"]:
                to_keep.append(item)
            else:
                to_update.append(item)

    for key, (existing_path, existing_sha) in existing_by_lower.items():
        if key not in desired_by_lower and key in managed_lower:
            to_remove.append(
                {
                    "filename": existing_path.name,
                    "path": str(existing_path),
                    "sha256": existing_sha,
                }
            )

    return {
        "drive_root": str(root),
        "mods_dir": str(mods_dir),
        "to_add": to_add,
        "to_update": to_update,
        "to_keep": to_keep,
        "to_remove": to_remove,
        "has_changes": bool(to_add or to_update or to_remove),
    }


def execute_sd_sync(
    drive_root: Path | str,
    plan: dict[str, object],
    *,
    data_dir: Path | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Apply an SD sync plan atomically under ``<drive>/mods/`` with SHA-256 readback."""
    root = Path(drive_root).resolve()
    mods_dir = _resolve_mods_dir(root)
    mods_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, object]] = []
    removed: list[str] = []

    for item in [*plan["to_add"], *plan["to_update"]]:
        fname = validate_mod_filename(str(item["filename"]))
        src = Path(str(item["source_path"]))
        payload = src.read_bytes()
        expected_sha = _sha256_bytes(payload)
        dest = (mods_dir / fname).resolve()
        if dest.parent != mods_dir:
            raise ValueError(f"Refusing SD write outside mods/: {dest}")
        tmp = mods_dir / f".{fname}.tmp"
        if progress_cb:
            progress_cb(f"Copying {fname} to {mods_dir}...")
        tmp.write_bytes(payload)
        readback = tmp.read_bytes()
        if _sha256_bytes(readback) != expected_sha:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"SD card read-back SHA-256 mismatch for {fname}")
        os.replace(tmp, dest)
        if _sha256_bytes(dest.read_bytes()) != expected_sha:
            raise RuntimeError(f"SD card final verification failed for {fname}")
        copied.append({"filename": fname, "bytes": len(payload), "sha256": expected_sha})

    for item in plan["to_remove"]:
        fname = validate_mod_filename(str(item["filename"]))
        target = (mods_dir / fname).resolve()
        if target.parent != mods_dir:
            raise ValueError(f"Refusing SD removal outside mods/: {target}")
        if target.is_file():
            if progress_cb:
                progress_cb(f"Removing toggled-OFF mod {fname} from {mods_dir}...")
            target.unlink()
            removed.append(fname)

    # Refresh SHA256SUMS.txt inside mods/ for deterministic inspection.
    sum_lines: list[str] = []
    for mod_file in sorted(mods_dir.glob("*.mod"), key=lambda p: p.name.lower()):
        digest = _sha256_bytes(mod_file.read_bytes())
        sum_lines.append(f"{digest}  {mod_file.name}")
    sums_path = mods_dir / "SHA256SUMS.txt"
    sums_tmp = mods_dir / ".SHA256SUMS.txt.tmp"
    sums_tmp.write_text("\n".join(sum_lines) + ("\n" if sum_lines else ""), encoding="utf-8")
    os.replace(sums_tmp, sums_path)

    receipt: dict[str, object] = {
        "operation": "sd_sync",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "drive_root": str(root),
        "mods_dir": str(mods_dir),
        "copied": copied,
        "kept": [item["filename"] for item in plan["to_keep"]],
        "removed": removed,
        "sha256sums": sum_lines,
    }
    if data_dir is not None:
        receipts_dir = Path(data_dir) / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        receipt_path = receipts_dir / f"sd-sync-{stamp}.json"
        counter = 1
        while receipt_path.exists():
            receipt_path = receipts_dir / f"sd-sync-{stamp}-{counter}.json"
            counter += 1
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        receipt["receipt_path"] = str(receipt_path)

    return receipt
