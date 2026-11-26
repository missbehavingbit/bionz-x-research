"""Prepare SD card (`MLV2.TAR`) and install/uninstall Option B standalone SD auto-boot on ILCE-6400A 1.01.

Modes:
  - ``prep-card``: Copy verified ``MLV2.TAR`` to the root of a FAT32/exFAT SD card and verify readback.
  - ``dry-run``: Offline validation of companion binaries, ``MLV2.TAR`` digest pinning, and worker scripts.
  - ``install`` / ``uninstall``: Supervised one-time ``/setting`` + ``/etc/ld.so.preload`` setup over
    authenticated ``054C:0336`` Jiritsu service mode (closed by default unless explicit owner flags
    and fresh baseline/identity/loader receipts are supplied).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import uuid

from project_paths import PROJECT_ROOT
from tools.research import a6400a_senser_acquire as senser
from tools.research.a6400a_fixed_acquire import admit_receipts, load_receipt
from tools.research.a6400a_jiritsu import JiritsuStatusError
from tools.research.a6400a_jiritsu_worker import (
    JiritsuFiles,
    LIBRARY_SHA,
    SOURCE as LOADER_SOURCE,
    VERSION,
    WORKSPACE_FILES,
    WorkerUsbTransport,
    qualify_loader,
)

DEFAULT_SD_BUILD = PROJECT_ROOT / "artifacts" / "tests" / "mlv2-ilce6400a-sd-1"
HOOK_NAME = "mlv2_sd_boot_hook.so"
SCRIPT_NAME = "mlv2_sd_boot.sh"
READER_NAME = "mlv2_card_read.elf"
PRELOAD_ENTRY = f"/setting/{HOOK_NAME}"
COMPANION_DESTS = {
    "hook": f"/setting/{HOOK_NAME}",
    "sh": f"/setting/{SCRIPT_NAME}",
    "read": f"/setting/{READER_NAME}",
}
DISABLE_MARKERS = ("MLV2.OFF", "disable.txt", "disable_hook")
MAX_CONTROL_UPLOAD = 16000
MAX_CARD_ARCHIVE = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_sd_build(build_dir: Path, *, allow_arrivederci: bool = False) -> dict[str, object]:
    """Validate an ILCE-6400A Option B build directory and its pinned archive digest."""
    build_dir = build_dir.resolve()
    info = json.loads((build_dir / "build.json").read_text(encoding="utf-8"))
    shim_addr = int(str(info["shim"]["addr"]), 16)
    if not (0x05D76000 <= shim_addr < 0x05D77000):
        raise ValueError(
            f"Build at {build_dir} has shim addr {hex(shim_addr)}, expected ILCE-6400A 0x05d76800 cave"
        )

    hook_path = build_dir / "companion" / HOOK_NAME
    sh_path = build_dir / "companion" / SCRIPT_NAME
    read_path = build_dir / "companion" / READER_NAME
    tar_path = build_dir / "card" / "MLV2.TAR"
    for p in (hook_path, sh_path, read_path, tar_path):
        if not p.is_file():
            raise FileNotFoundError(f"Missing required Option B artifact: {p}")

    hook_bytes = hook_path.read_bytes()
    sh_bytes = sh_path.read_bytes()
    read_bytes = read_path.read_bytes()
    tar_bytes = tar_path.read_bytes()

    for label, blob in ((HOOK_NAME, hook_bytes), (SCRIPT_NAME, sh_bytes), (READER_NAME, read_bytes)):
        if not (0 < len(blob) <= MAX_CONTROL_UPLOAD):
            raise ValueError(
                f"{label} is {len(blob)} bytes; must be 1..{MAX_CONTROL_UPLOAD} bytes for single-packet Jiritsu upload"
            )
    if hook_bytes[:4] != b"\x7fELF" or read_bytes[:4] != b"\x7fELF":
        raise ValueError("Companion hook/reader binaries must be valid ARM ELF files")
    if not (0 < len(tar_bytes) <= MAX_CARD_ARCHIVE):
        raise ValueError(f"MLV2.TAR size {len(tar_bytes)} exceeds 1 MiB card_read limit")

    tar_sha = sha256_bytes(tar_bytes)
    sh_text = sh_bytes.decode("ascii")
    m = re.search(r"^set -- ([0-9a-f]{64})$", sh_text, re.M)
    if m is None or m.group(1) != tar_sha:
        raise ValueError("mlv2_sd_boot.sh pinned digest does not match card/MLV2.TAR SHA-256")

    with tarfile.open(tar_path, mode="r:*") as tf:
        names = [m_info.name for m_info in tf.getmembers()]
    for req_member in ("guard", "mlv2.req", "core.bin", "MenuTouchFull.mod"):
        if req_member not in names:
            raise ValueError(f"MLV2.TAR is missing required member {req_member!r}")
    if "UsbStreamingTest.mod" in names:
        raise ValueError("QEMU-only UsbStreamingTest.mod must not be bundled in physical MLV2.TAR")
    if not allow_arrivederci and "Arrivederci.mod" in names:
        raise ValueError(
            "Arrivederci.mod must not be bundled in default physical MLV2.TAR "
            "(color matrix persists in Backup.bin if powered off while ON; pass --allow-arrivederci to override)"
        )

    return {
        "build_dir": str(build_dir),
        "shim_addr": hex(shim_addr),
        "tar_path": str(tar_path),
        "tar_bytes": len(tar_bytes),
        "tar_sha256": tar_sha,
        "tar_members": names,
        "companions": {
            "hook": {"name": HOOK_NAME, "path": str(hook_path), "bytes": len(hook_bytes), "sha256": sha256_bytes(hook_bytes)},
            "sh": {"name": SCRIPT_NAME, "path": str(sh_path), "bytes": len(sh_bytes), "sha256": sha256_bytes(sh_bytes)},
            "read": {"name": READER_NAME, "path": str(read_path), "bytes": len(read_bytes), "sha256": sha256_bytes(read_bytes)},
        },
    }


def build_worker_script(
    *,
    mode: str,
    nonce: str,
    companions: dict[str, dict[str, object]] | None = None,
) -> bytes:
    """Build the bounded `/bin/sh` worker executed once by `libJit.so` in 054C:0336 mode."""
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("Worker nonce must be 32 lowercase hex characters")
    if mode not in ("install", "uninstall"):
        raise ValueError(f"Unsupported worker mode: {mode!r}")

    done_path = f"/tmp/jiritsu/sdboot_{nonce}.done"
    common_head = f"""#!/bin/sh
set -u
bb=""
if [ -x /bin/busybox ]; then bb=/bin/busybox
elif command -v busybox >/dev/null 2>&1; then bb=busybox
fi
run_bb() {{ if [ -n "$bb" ]; then "$bb" "$@"; else "$@"; fi; }}
OUT_LOG="${{MLV2_TEST_OUT_LOG:-/tmp/jiritsu/out.txt}}"
exec >"$OUT_LOG" 2>&1
printf '__A6400A_WORKER_LOG__:{nonce}\\n'
SETTING_ROOT="${{MLV2_TEST_SETTING_ROOT:-/setting}}"
ETC_ROOT="${{MLV2_TEST_ETC_ROOT:-/etc}}"
STAGE_DIR="${{MLV2_TEST_STAGE_DIR:-/tmp/jiritsu}}"
DONE_FILE="${{MLV2_TEST_DONE_FILE:-{done_path}}}"

fail() {{
    printf '__A6400A_SDBOOT_ERROR__:%s\\n' "$1"
    printf '__A6400A_SDBOOT_FAIL__:{nonce}:%s\\n' "$1" >"$DONE_FILE.tmp"
    run_bb mv -f "$DONE_FILE.tmp" "$DONE_FILE"
    exit 1
}}

if [ -z "${{MLV2_TEST_SKIP_MOUNT_CHECK:-}}" ]; then
    run_bb grep -q ' /setting vfat rw[, ]' /proc/mounts || fail "setting_not_mounted_rw"
    run_bb grep -q ' /etc vfat rw[, ]' /proc/mounts || fail "etc_not_mounted_rw"
fi
[ -f "$SETTING_ROOT/Backup.bin" ] || fail "setting_backup_bin_missing"
[ -f "$ETC_ROOT/toprc" ] || fail "etc_toprc_missing"
"""
    if mode == "install":
        if companions is None:
            raise ValueError("install script requires validated companion metadata")
        h_sha = companions["hook"]["sha256"]
        s_sha = companions["sh"]["sha256"]
        r_sha = companions["read"]["sha256"]
        body = f"""
stage_hook="$STAGE_DIR/sdboot_{nonce}_hook"
stage_sh="$STAGE_DIR/sdboot_{nonce}_sh"
stage_read="$STAGE_DIR/sdboot_{nonce}_read"

printf '%s  %s\\n' '{h_sha}' "$stage_hook" | run_bb sha256sum -c - || fail "stage_hook_sha"
printf '%s  %s\\n' '{s_sha}' "$stage_sh" | run_bb sha256sum -c - || fail "stage_sh_sha"
printf '%s  %s\\n' '{r_sha}' "$stage_read" | run_bb sha256sum -c - || fail "stage_read_sha"

install_one() {{
    src=$1; dst=$2; expected=$3
    run_bb cp -f "$src" "$dst.new" || fail "cp_failed_$dst"
    run_bb chmod 755 "$dst.new" || fail "chmod_failed_$dst"
    printf '%s  %s\\n' "$expected" "$dst.new" | run_bb sha256sum -c - || fail "verify_new_$dst"
    run_bb mv -f "$dst.new" "$dst" || fail "mv_failed_$dst"
    printf '%s  %s\\n' "$expected" "$dst" | run_bb sha256sum -c - || fail "verify_final_$dst"
}}

install_one "$stage_hook" "$SETTING_ROOT/{HOOK_NAME}" '{h_sha}'
install_one "$stage_sh" "$SETTING_ROOT/{SCRIPT_NAME}" '{s_sha}'
install_one "$stage_read" "$SETTING_ROOT/{READER_NAME}" '{r_sha}'
run_bb rm -f "$SETTING_ROOT/disable_sd_boot"

preload="$ETC_ROOT/ld.so.preload"
if [ -f "$preload" ] && [ ! -f "$ETC_ROOT/ld.so.preload.stock_bak" ]; then
    run_bb cp -f "$preload" "$ETC_ROOT/ld.so.preload.stock_bak" || fail "backup_preload_failed"
fi
: > "$preload.new" || fail "create_preload_failed"
if [ -f "$preload" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$line" ] || continue
        [ "$line" = "{PRELOAD_ENTRY}" ] && continue
        printf '%s\\n' "$line" >> "$preload.new" || fail "append_preload_failed"
    done < "$preload"
fi
printf '%s\\n' "{PRELOAD_ENTRY}" >> "$preload.new" || fail "write_preload_entry_failed"
run_bb mv -f "$preload.new" "$preload" || fail "mv_preload_failed"
run_bb grep -qx '{PRELOAD_ENTRY}' "$preload" || fail "verify_preload_failed"
run_bb sync
printf '__A6400A_SDBOOT_DONE__:{nonce}:INSTALLED\\n' > "$DONE_FILE.tmp"
run_bb mv -f "$DONE_FILE.tmp" "$DONE_FILE"
"""
    else:
        body = f"""
run_bb rm -f "$SETTING_ROOT/{HOOK_NAME}" "$SETTING_ROOT/{SCRIPT_NAME}" \\
    "$SETTING_ROOT/{READER_NAME}" "$SETTING_ROOT/disable_sd_boot" \\
    "$SETTING_ROOT/{HOOK_NAME}.new" "$SETTING_ROOT/{SCRIPT_NAME}.new" "$SETTING_ROOT/{READER_NAME}.new"

preload="$ETC_ROOT/ld.so.preload"
if [ -f "$preload" ]; then
    : > "$preload.new" || fail "create_preload_uninstall_failed"
    kept=0
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$line" ] || continue
        [ "$line" = "{PRELOAD_ENTRY}" ] && continue
        printf '%s\\n' "$line" >> "$preload.new" || fail "keep_preload_line_failed"
        kept=$((kept + 1))
    done < "$preload"
    if [ "$kept" -eq 0 ]; then
        run_bb rm -f "$preload.new" "$preload" "$ETC_ROOT/ld.so.preload.stock_bak" || fail "rm_empty_preload_failed"
    else
        run_bb mv -f "$preload.new" "$preload" || fail "mv_kept_preload_failed"
    fi
fi
[ ! -e "$SETTING_ROOT/{HOOK_NAME}" ] || fail "hook_still_present"
[ ! -e "$SETTING_ROOT/{SCRIPT_NAME}" ] || fail "script_still_present"
[ ! -e "$SETTING_ROOT/{READER_NAME}" ] || fail "reader_still_present"
run_bb sync
printf '__A6400A_SDBOOT_DONE__:{nonce}:UNINSTALLED\\n' > "$DONE_FILE.tmp"
run_bb mv -f "$DONE_FILE.tmp" "$DONE_FILE"
"""
    encoded = (common_head + body).encode("ascii")
    if len(encoded) > MAX_CONTROL_UPLOAD:
        raise ValueError("Generated worker script exceeds single-packet Jiritsu limit")
    return encoded


def prep_sd_card(
    sd_root: Path,
    build_info: dict[str, object],
    *,
    clear_disable_markers: bool = False,
) -> dict[str, object]:
    """Copy verified MLV2.TAR to the root of an SD card without touching DCIM or existing media."""
    root = sd_root.resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"SD card root directory does not exist: {root}")
    if root == PROJECT_ROOT.resolve() or PROJECT_ROOT.resolve().is_relative_to(root):
        raise ValueError(f"Refusing to treat repository/system drive {root} as an SD card root")
    if os.name == "nt" and root.drive.upper() == os.environ.get("SystemDrive", "C:").upper():
        raise ValueError(f"Refusing to write to Windows system drive {root}")

    removed_markers: list[str] = []
    present_markers: list[str] = []
    for marker in DISABLE_MARKERS:
        mp = root / marker
        if mp.exists():
            if clear_disable_markers and mp.is_file():
                mp.unlink()
                removed_markers.append(marker)
            else:
                present_markers.append(marker)

    tar_src = Path(str(build_info["tar_path"]))
    payload = tar_src.read_bytes()
    expected_sha = str(build_info["tar_sha256"])
    if sha256_bytes(payload) != expected_sha:
        raise ValueError("MLV2.TAR changed on disk before SD copy")

    dst = root / "MLV2.TAR"
    tmp = root / "MLV2.TAR.tmp"
    with tmp.open("wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)

    readback = dst.read_bytes()
    readback_sha = sha256_bytes(readback)
    if readback_sha != expected_sha:
        raise RuntimeError(f"SD card MLV2.TAR readback SHA-256 mismatch: {readback_sha} != {expected_sha}")

    return {
        "status": "sd_card_prepared",
        "sd_root": str(root),
        "mlv2_tar_path": str(dst),
        "mlv2_tar_bytes": len(readback),
        "mlv2_tar_sha256": readback_sha,
        "tar_members": build_info["tar_members"],
        "removed_disable_markers": removed_markers,
        "remaining_disable_markers": present_markers,
    }


class SdBootJiritsuFiles(JiritsuFiles):
    """Scoped Jiritsu file client permitting only Option B staging and readback verification."""

    def __init__(self, transport: WorkerUsbTransport, nonce: str, events: list):
        if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
            raise ValueError("Invalid Option B worker nonce")
        super().__init__(transport, f"/tmp_network/a6400a-{nonce}", events)
        self.sd_nonce = nonce
        self.stage_paths = {
            "hook": f"/tmp/jiritsu/sdboot_{nonce}_hook",
            "sh": f"/tmp/jiritsu/sdboot_{nonce}_sh",
            "read": f"/tmp/jiritsu/sdboot_{nonce}_read",
        }
        self.done_path = f"/tmp/jiritsu/sdboot_{nonce}.done"

    def read(self, path: str, limit: int) -> bytes:
        allowed = {
            "/version.txt",
            "/usr/lib/libJiritsu.so",
            "/tmp/jmode",
            "/tmp/jbusy",
            *WORKSPACE_FILES,
            *self.stage_paths.values(),
            self.done_path,
            *COMPANION_DESTS.values(),
            "/etc/ld.so.preload",
        }
        if path not in allowed:
            raise ValueError(f"File read outside Option B Jiritsu allowlist: {path}")
        return self.exchange(8, b"\x01\x00\x00/.." + path.encode("ascii") + b"\0", limit)

    def write_staged(self, path: str, data: bytes) -> None:
        allowed = {WORKSPACE_FILES[0], *self.stage_paths.values()}
        if path not in allowed:
            raise ValueError(f"File write outside Option B staging allowlist: {path}")
        if not (0 < len(data) <= MAX_CONTROL_UPLOAD):
            raise ValueError(f"Staging upload size {len(data)} outside 1..{MAX_CONTROL_UPLOAD}")
        self.exchange(15, b"\0" + path.encode("ascii") + b"\0", 0)
        self.exchange(16, b"\x00\xff\x00" + data, 0)
        if self.read(path, len(data)) != data:
            raise ValueError(f"USB readback mismatch on staged file {path}")

    def remove_staged(self, path: str, expected: bytes | None = None) -> None:
        allowed = {*WORKSPACE_FILES, *self.stage_paths.values(), self.done_path}
        if path not in allowed:
            raise ValueError(f"Refusing deletion outside Option B staging allowlist: {path}")
        try:
            actual = self.read(path, max(len(expected) if expected is not None else 4096, 64))
        except JiritsuStatusError as err:
            if err.status == 0x10000006:
                return
            raise
        if expected is not None and actual != expected:
            raise ValueError(f"Staged file {path} content changed; refusing deletion")
        if path == WORKSPACE_FILES[1]:
            self.exchange(9, b"\x00\x02\x00", 0)
        else:
            self.exchange(15, b"\0" + path.encode("ascii") + b"\0", 0)
            self.exchange(18, b"\x00\xff", 0)
        self.require_absent(path)


def run_jiritsu_session(args: argparse.Namespace, build_info: dict[str, object] | None) -> int:
    if (not args.owner_authorized_sd_boot
            or not args.confirm_firmware_101
            or not args.permit_control_pipe
            or args.baseline is None
            or args.identity_receipt is None
            or args.loader_manifest is None
            or args.output_dir is None):
        print(
            f"{args.mode}: Camera entry is closed by default. Explicit --owner-authorized-sd-boot, "
            "--confirm-firmware-101, --permit-control-pipe, --baseline, --identity-receipt, "
            "--loader-manifest, and --output-dir are required.",
            file=sys.stderr,
        )
        return 2

    output = args.output_dir.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("Output directory must stay under repository artifacts/")
    output.mkdir(parents=True, exist_ok=False)

    with senser.host_lock():
        return _run_jiritsu_locked(args, build_info, output)


def _run_jiritsu_locked(
    args: argparse.Namespace,
    build_info: dict[str, object] | None,
    output: Path,
) -> int:
    nonce = uuid.uuid4().hex
    report: dict[str, object] = {
        "status": "in_progress",
        "mode": args.mode,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "worker_nonce": nonce,
        "events": [],
        "camera_file_writes": False,
        "power_removal_required": False,
    }
    modules = device = driver = files = None
    protocol_error = RuntimeError
    script = loader_bytes = b""
    staged_blobs: dict[str, bytes] = {}
    done_bytes: bytes | None = None

    try:
        baseline_record, report["baseline_sha256"] = load_receipt(args.baseline)
        identity_record, report["identity_receipt_sha256"] = load_receipt(args.identity_receipt)
        baseline, report["identity"] = admit_receipts(baseline_record, identity_record)

        manifest, report["loader_manifest_sha256"] = load_receipt(args.loader_manifest)
        loader_path = args.loader_manifest.resolve().parent / "libJit.so"
        loader_bytes = loader_path.read_bytes()
        if (manifest.get("status") != "built_not_deployed"
                or manifest.get("source_sha256") != senser.host_digest(LOADER_SOURCE)
                or manifest.get("sha256") != sha256_bytes(loader_bytes)
                or not (0 < len(loader_bytes) < 32700)):
            raise ValueError("Loader manifest does not match a verified bounded libJit.so build")
        report["loader_qualification"] = qualify_loader(loader_path)

        companions = build_info["companions"] if build_info is not None else None
        script = build_worker_script(mode=args.mode, nonce=nonce, companions=companions)
        (output / "worker.sh").write_bytes(script)
        report["worker_script_sha256"] = sha256_bytes(script)
        if build_info is not None:
            report["sd_build"] = build_info

        metadata = report["metadata_before"] = senser.discover()
        senser.admit_device(metadata, baseline, "read")
        if metadata["pid"] != 0x0336:
            raise ValueError("Camera must be in 054C:0336 Jiritsu service mode before running install/uninstall")

        modules = senser.load_pmca()
        protocol_error = modules["generic"].GenericUsbException
        device, raw_driver = senser.connect(metadata, modules)
        transport = WorkerUsbTransport(device, raw_driver)
        driver = senser.BoundedDriver(raw_driver, report["events"])
        report["start_control_attempted"] = True
        senser.control_request(
            driver, senser.START, protocol_error, modules["core"].USBError, args.permit_control_pipe, report
        )
        driver.begin("auth")
        senser.authenticate(driver, modules["constants"], modules["crypto"])
        report["authentication_accepted"] = True

        files = SdBootJiritsuFiles(transport, nonce, report["events"])
        if files.read("/version.txt", 64) != VERSION:
            raise ValueError("Live camera /version.txt does not match ILCE-6400A 1.01")
        live_lib = files.read("/usr/lib/libJiritsu.so", 162568)
        if sha256_bytes(live_lib) != LIBRARY_SHA:
            raise ValueError("Live /usr/lib/libJiritsu.so SHA-256 mismatch")

        for busy_marker in ("/tmp/jmode", "/tmp/jbusy"):
            files.require_absent(busy_marker)
        files.exchange(2, b"\x00\x00", 0)
        for p in (*WORKSPACE_FILES, *files.stage_paths.values(), files.done_path):
            files.require_absent(p)

        report["camera_file_writes"] = True
        if args.mode == "install":
            assert companions is not None
            for key in ("hook", "sh", "read"):
                blob = Path(str(companions[key]["path"])).read_bytes()
                staged_blobs[files.stage_paths[key]] = blob
                files.write_staged(files.stage_paths[key], blob)

        files.write_staged(WORKSPACE_FILES[0], script)
        report["power_removal_required"] = True
        files.exchange(7, b"\x00\x02\x00" + loader_bytes, 0)
        if files.read(WORKSPACE_FILES[1], len(loader_bytes)) != loader_bytes:
            raise ValueError("libJit.so readback mismatch")

        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            try:
                done_bytes = files.read(files.done_path, 512)
                break
            except JiritsuStatusError as err:
                if err.status != 0x10000006:
                    raise
                time.sleep(0.4)
        if done_bytes is None:
            raise TimeoutError("Timed out waiting for Option B worker completion marker")

        done_text = done_bytes.decode("ascii", errors="replace").strip()
        report["worker_done"] = done_text
        expected_tag = f"__A6400A_SDBOOT_DONE__:{nonce}:{'INSTALLED' if args.mode == 'install' else 'UNINSTALLED'}"
        if done_text != expected_tag:
            raise RuntimeError(f"Worker reported failure: {done_text}")

        # Independent host-side USB GET_FILE readback of /setting/mlv2_* and /etc/ld.so.preload!
        if args.mode == "install":
            assert companions is not None
            verified_host_readback: dict[str, str] = {}
            for key, remote_dst in COMPANION_DESTS.items():
                expected_blob = Path(str(companions[key]["path"])).read_bytes()
                actual_blob = files.read(remote_dst, len(expected_blob) + 64)
                if actual_blob != expected_blob:
                    raise RuntimeError(f"Host USB readback mismatch on installed {remote_dst}")
                verified_host_readback[remote_dst] = sha256_bytes(actual_blob)
            preload_raw = files.read("/etc/ld.so.preload", 4096).decode("ascii", errors="replace")
            if PRELOAD_ENTRY not in preload_raw.splitlines():
                raise RuntimeError("Host USB readback of /etc/ld.so.preload missing preload entry")
            report["host_verified_installed_sha256"] = verified_host_readback
            report["host_verified_preload"] = preload_raw.splitlines()
            report["status"] = "sd_boot_installed_power_cycle_required"
        else:
            for remote_dst in COMPANION_DESTS.values():
                files.require_absent(remote_dst)
            try:
                preload_after = files.read("/etc/ld.so.preload", 4096).decode("ascii", errors="replace")
                if PRELOAD_ENTRY in preload_after.splitlines():
                    raise RuntimeError("/etc/ld.so.preload still references mlv2_sd_boot_hook.so")
                report["host_verified_preload"] = preload_after.splitlines()
            except JiritsuStatusError as err:
                if err.status != 0x10000006:
                    raise
                report["host_verified_preload"] = []
            report["status"] = "sd_boot_uninstalled_power_cycle_required"

    except Exception as exc:
        report.update(status="blocked", error_type=type(exc).__name__, error=str(exc))
    finally:
        if files is not None and files.in_sync and report.get("camera_file_writes"):
            try:
                try:
                    out_log = files.read(WORKSPACE_FILES[2], 16000)
                    (output / "worker-out.txt").write_bytes(out_log)
                    files.remove_staged(WORKSPACE_FILES[2], out_log)
                except JiritsuStatusError as err:
                    if err.status != 0x10000006:
                        raise
                if done_bytes is not None:
                    files.remove_staged(files.done_path, done_bytes)
                for st_path, blob in staged_blobs.items():
                    files.remove_staged(st_path, blob)
                if script:
                    files.remove_staged(WORKSPACE_FILES[0], script)
                if loader_bytes:
                    files.remove_staged(WORKSPACE_FILES[1], loader_bytes)
                report["owned_staging_cleaned"] = True
            except Exception as clean_exc:
                report["cleanup_error"] = f"{type(clean_exc).__name__}: {clean_exc}"

        if driver is not None and report.get("start_control_attempted") and (files is None or files.in_sync):
            try:
                driver.begin("control")
                senser.control_request(
                    driver, senser.STOP, protocol_error, modules["core"].USBError, args.permit_control_pipe, report
                )
                report["exit_control_attempted"] = True
            except Exception as stop_exc:
                report["stop_error"] = f"{type(stop_exc).__name__}: {stop_exc}"
        if device is not None and modules is not None:
            try:
                modules["util"].dispose_resources(device)
                report["host_handle_released"] = True
            except Exception:
                pass
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "events"}, indent=2))
    return 0 if str(report["status"]).startswith("sd_boot_") else 2


def run_full_orchestration(args: argparse.Namespace) -> int:
    """Turnkey supervised sequence from normal USB Mass Storage (054C:0F0F) through USBSTOR restoration."""
    sub_mode = "install" if args.mode == "full-install" else "uninstall"
    if (not args.owner_authorized_sd_boot
            or not args.confirm_firmware_101
            or not args.permit_control_pipe):
        print(
            f"{args.mode}: Camera entry is closed by default. Pass --owner-authorized-sd-boot "
            "--confirm-firmware-101 --permit-control-pipe from an Administrator PowerShell.",
            file=sys.stderr,
        )
        return 2

    build_info = validate_sd_build(args.build, allow_arrivederci=args.allow_arrivederci) if sub_mode == "install" else None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else (PROJECT_ROOT / "artifacts" / "camera-sessions" / f"a6400a-sd-boot-{sub_mode}-{stamp}")
    )
    if not session_dir.is_relative_to(PROJECT_ROOT / "artifacts"):
        raise ValueError("Session directory must stay under repository artifacts/")
    session_dir.mkdir(parents=True, exist_ok=False)

    binding_script = PROJECT_ROOT / "artifacts" / "analysis" / "e0520-a6400a-sencore-handlers" / "camera_binding.py"
    if not binding_script.is_file():
        raise FileNotFoundError(
            f"Missing camera WinUSB/USBSTOR binding helper at {binding_script}; bind WinUSB manually and use --mode {sub_mode}."
        )

    env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
    baseline_dir = session_dir / "01-baseline"
    identity_dir = session_dir / "02-identity"
    loader_dir = session_dir / "03-loader-build"
    winusb_dir = session_dir / "04-winusb"
    enter_dir = session_dir / "05-service-entry"
    op_dir = session_dir / f"06-{sub_mode}"
    usbstor_dir = session_dir / "07-usbstor-restore"

    def run_step(label: str, cmd: list[str], log_name: str) -> int:
        print(f"[{label}] Running: {' '.join(cmd)}", flush=True)
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
        (session_dir / log_name).write_text(
            f"EXIT={res.returncode}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}\n",
            encoding="utf-8",
        )
        if res.returncode != 0:
            print(res.stdout, flush=True)
            print(res.stderr, file=sys.stderr, flush=True)
        return res.returncode

    winusb_bound = False
    op_rc = 2
    try:
        if run_step(
            "1/7 baseline",
            [sys.executable, "-m", "tools.research.a6400a_senser_acquire", "--action", "inspect", "--output-dir", str(baseline_dir)],
            "01-baseline.log",
        ) != 0:
            raise RuntimeError("Step 1 (USBSTOR baseline inspection) failed. Ensure camera is ON in USB Mass Storage mode.")

        if run_step(
            "2/7 identity",
            [sys.executable, "-m", "tools.research.a6400a_native_readonly", "--inquiry", "--model-info", "--output-dir", str(identity_dir)],
            "02-identity.log",
        ) != 0:
            raise RuntimeError("Step 2 (Native ILCE-6400A GetModelInfo check) failed.")

        if run_step(
            "3/7 build-loader",
            [sys.executable, "-m", "tools.research.a6400a_jiritsu_bootstrap", "--mode", "build-loader", "--output-dir", str(loader_dir)],
            "03-loader-build.log",
        ) != 0:
            raise RuntimeError("Step 3 (ARM libJit.so loader build via WSL) failed.")

        if run_step(
            "4/7 bind-winusb",
            [
                sys.executable, str(binding_script),
                "--driver", "WinUSB",
                "--baseline", str(baseline_dir / "receipt.json"),
                "--identity-receipt", str(identity_dir / "receipt.json"),
                "--output-dir", str(winusb_dir),
            ],
            "04-winusb.log",
        ) != 0:
            raise RuntimeError("Step 4 (WinUSB binding) failed. Run PowerShell as Administrator.")
        winusb_bound = True

        if run_step(
            "5/7 enter-service",
            [
                sys.executable, "-m", "tools.research.a6400a_senser_acquire",
                "--action", "enter", "--permit-control-pipe",
                "--baseline", str(baseline_dir / "receipt.json"),
                "--output-dir", str(enter_dir),
            ],
            "05-service-entry.log",
        ) != 0:
            raise RuntimeError("Step 5 (Enter 054C:0336 Jiritsu service mode) failed.")

        sub_ns = argparse.Namespace(
            mode=sub_mode,
            build=args.build,
            output_dir=op_dir,
            baseline=baseline_dir / "receipt.json",
            identity_receipt=identity_dir / "receipt.json",
            loader_manifest=loader_dir / "manifest.json",
            owner_authorized_sd_boot=True,
            confirm_firmware_101=True,
            permit_control_pipe=True,
        )
        print(f"[6/7 {sub_mode}] Deploying Option B via 054C:0336 Jiritsu...", flush=True)
        op_rc = run_jiritsu_session(sub_ns, build_info)
    finally:
        if winusb_bound:
            # Give Windows PnP up to 8s to re-enumerate 054C:0F0F after senser STOP before restoring USBSTOR.
            time.sleep(2.5)
            restore_rc = run_step(
                "7/7 restore-usbstor",
                [
                    sys.executable, str(binding_script),
                    "--driver", "USBSTOR",
                    "--baseline", str(baseline_dir / "receipt.json"),
                    "--identity-receipt", str(identity_dir / "receipt.json"),
                    "--output-dir", str(usbstor_dir),
                ],
                "07-usbstor-restore.log",
            )
            if restore_rc != 0:
                print(
                    "NOTE: Camera stayed in service mode until battery pull; once you power-cycle the camera "
                    "and reconnect USB, re-run camera_binding.py --driver USBSTOR if Mass Storage does not mount.",
                    file=sys.stderr,
                )
    print(f"\nSession receipts saved under: {session_dir}", flush=True)
    if op_rc == 0:
        print(
            "\nSUCCESS! Next step on camera:\n"
            "  1. Unplug USB, turn camera OFF, remove battery for ~5 seconds, reinsert battery.\n"
            "  2. Insert your prepared SD card (containing MLV2.TAR at root) and turn camera ON.\n"
            "  3. Wait ~5 seconds, press MENU, and navigate to Tab 7 (MODS)!",
            flush=True,
        )
    return op_rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("prep-card", "dry-run", "install", "uninstall", "full-install", "full-uninstall"),
    )
    parser.add_argument("--build", type=Path, default=DEFAULT_SD_BUILD)
    parser.add_argument("--sd-root", type=Path, help="Mounted SD card root directory (e.g. E:\\) for prep-card")
    parser.add_argument("--clear-disable-markers", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--identity-receipt", type=Path)
    parser.add_argument("--loader-manifest", type=Path)
    parser.add_argument("--owner-authorized-sd-boot", action="store_true")
    parser.add_argument("--confirm-firmware-101", action="store_true")
    parser.add_argument("--permit-control-pipe", action="store_true")
    parser.add_argument(
        "--allow-arrivederci",
        action="store_true",
        help="Allow MLV2.TAR builds that explicitly opted in to bundling Arrivederci.mod.",
    )
    args = parser.parse_args(argv)

    if args.mode == "prep-card":
        if args.sd_root is None:
            parser.error("--mode prep-card requires --sd-root")
        build_info = validate_sd_build(args.build, allow_arrivederci=args.allow_arrivederci)
        res = prep_sd_card(args.sd_root, build_info, clear_disable_markers=args.clear_disable_markers)
        print(json.dumps(res, indent=2))
        return 0

    if args.mode == "dry-run":
        if args.output_dir is None:
            parser.error("--mode dry-run requires --output-dir under artifacts/")
        out = args.output_dir.resolve()
        if not out.is_relative_to(PROJECT_ROOT / "artifacts"):
            raise ValueError("--output-dir must stay under artifacts/")
        out.mkdir(parents=True, exist_ok=False)
        build_info = validate_sd_build(args.build, allow_arrivederci=args.allow_arrivederci)
        nonce = "0" * 32
        inst_sh = build_worker_script(mode="install", nonce=nonce, companions=build_info["companions"])
        uninst_sh = build_worker_script(mode="uninstall", nonce=nonce)
        (out / "install_worker.sh").write_bytes(inst_sh)
        (out / "uninstall_worker.sh").write_bytes(uninst_sh)
        manifest = {
            "status": "dry_run_verified",
            "usb_contacted": False,
            "sd_build": build_info,
            "install_script_bytes": len(inst_sh),
            "install_script_sha256": sha256_bytes(inst_sh),
            "uninstall_script_bytes": len(uninst_sh),
            "uninstall_script_sha256": sha256_bytes(uninst_sh),
        }
        (out / "dry-run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2))
        return 0

    if args.mode in ("full-install", "full-uninstall"):
        return run_full_orchestration(args)

    build_info = (
        validate_sd_build(args.build, allow_arrivederci=args.allow_arrivederci)
        if args.mode == "install"
        else None
    )
    return run_jiritsu_session(args, build_info)


if __name__ == "__main__":
    raise SystemExit(main())
