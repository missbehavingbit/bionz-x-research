"""Camera detection, install planning, 1-click deployment, and receipt engine."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shlex
import struct
import threading
import time
from typing import Any, Callable, Iterator

from src.a6400.targets.usb_debugger_compat import (
    find_debugger_target_by_model,
    load_reference_usb_extcmd,
    verify_camera_debugger_compatibility,
)
from tools import camera_debugger as cd
from tools.deploy_native_usb_debugger import read_preload_safe, upload_and_verify_debugger
from tools.research.reload_mod import IM_PID, REQ as RELOAD_REQ, RESULT as RELOAD_RESULT
from tools.research.reload_mod import hook_bytes, parse_result, parse_smod_v2
from tools.setup_usb_debugger import build_onboarding_bundle, detect_msc_camera

from .smod import parse_smod, validate_mod_filename
from .tools_bundle import (
    ensure_pmca,
    repo_root,
    resolve_debugger_payload_root,
    user_data_dir,
    validate_mlv2_build,
)

REF_VIEW_BASE = 0x155E6000
REF_TESTCMD_BASE = 0x05660000
MLV2_HOOKS = {
    "set_root": (0x15B91654, 16),
    "refresh": (0x15B91A80, 16),
    "open_item": (0x15B92044, 12),
}
GUARD_REMOTE = "/tmp_bt/mlv2_guard"
REQ_REMOTE = "/tmp_bt/mlv2.req"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_receipt_path(receipts_dir: Path, prefix: str) -> Path:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = receipts_dir / f"{prefix}-{stamp}.json"
    counter = 1
    while candidate.exists():
        candidate = receipts_dir / f"{prefix}-{stamp}-{counter}.json"
        counter += 1
    return candidate


class LiveCameraTransport:
    """Default hardware transport wrapping ``camera_debugger``, ``setup_usb_debugger``, and ``pmca``."""

    def __init__(self, root: Path, data_dir: Path):
        self.root = Path(root)
        self.data_dir = Path(data_dir)

    @contextmanager
    def debugger_session(self) -> Iterator[Any]:
        with cd.connect("auto") as dbg:
            yield dbg

    def detect_msc_identity(self) -> dict[str, str]:
        ensure_pmca(root=self.root, data_dir=self.data_dir)
        return detect_msc_camera(driver_name=None)

    def run_senser(self, callback: Callable[[Any], None]) -> None:
        ensure_pmca(root=self.root, data_dir=self.data_dir)
        from pmca.commands.usb import senserShellCommand

        try:
            senserShellCommand(driverName="libusb", complete=callback)
        except Exception as exc:
            raise RuntimeError(
                f"Senser USB operation failed ({exc}). "
                "Ensure libusb driver is available if switching to service mode."
            ) from exc

    def query_tweaks(self, cam: Any) -> list[dict[str, object]]:
        ensure_pmca(root=self.root, data_dir=self.data_dir)
        from pmca.platform.backend.senser import SenserPlatformBackend
        from pmca.platform.tweaks import TweakInterface

        backend = SenserPlatformBackend(cam)
        backend.start()
        try:
            mgr = TweakInterface(backend)
            if not getattr(mgr, "backupPatch", None):
                raise RuntimeError("PMCA backup patch interface could not be initialized on this camera")
            rows: list[dict[str, object]] = []
            for name, desc, enabled, value in mgr.getTweaks():
                rows.append(
                    {
                        "id": str(name),
                        "description": str(desc),
                        "enabled": bool(enabled),
                        "value": str(value),
                        "available": bool(mgr.available(name)),
                    }
                )
            return rows
        finally:
            stop = getattr(backend, "stop", None)
            if callable(stop):
                stop()

    def apply_tweaks(self, cam: Any, desired: dict[str, bool]) -> list[dict[str, object]]:
        ensure_pmca(root=self.root, data_dir=self.data_dir)
        from pmca.platform.backend.senser import SenserPlatformBackend
        from pmca.platform.tweaks import TweakInterface

        backend = SenserPlatformBackend(cam)
        backend.start()
        try:
            mgr = TweakInterface(backend)
            if not getattr(mgr, "backupPatch", None):
                raise RuntimeError("PMCA backup patch interface could not be initialized on this camera")
            for tweak_id, target_enabled in desired.items():
                if not mgr.available(tweak_id):
                    raise RuntimeError(f"PMCA tweak {tweak_id!r} is not available on this model/region")
                mgr.setEnabled(tweak_id, bool(target_enabled))
            mgr.apply()

            # Fresh readback via a new TweakInterface instance to confirm persisted state.
            verify_mgr = TweakInterface(backend)
            readback = {
                str(name): {"enabled": bool(enabled), "value": str(value)}
                for name, _desc, enabled, value in verify_mgr.getTweaks()
            }
            for tweak_id, target_enabled in desired.items():
                actual = readback.get(tweak_id, {}).get("enabled")
                if actual is not bool(target_enabled):
                    raise RuntimeError(
                        f"PMCA tweak {tweak_id!r} read-back mismatch: expected {bool(target_enabled)}, got {actual}"
                    )
            return [
                {"id": k, "enabled": v["enabled"], "value": v["value"]}
                for k, v in readback.items()
                if k in desired
            ]
        finally:
            stop = getattr(backend, "stop", None)
            if callable(stop):
                stop()


class CameraService:
    """High-level detection, planning, 1-click deployment, and receipt service."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        data_dir: Path | None = None,
        transport: Any | None = None,
    ):
        self.root = repo_root(root)
        self.data_dir = user_data_dir(data_dir, root=self.root)
        self.transport = transport or LiveCameraTransport(self.root, self.data_dir)

    def _exec(self, dbg: Any, cmd: str) -> str:
        res = dbg.request(cd.OP_EXEC, cmd.encode("utf-8"))
        return res["payload"].decode("utf-8", errors="replace")

    def _probe_debugger_info(self, dbg: Any) -> str:
        """Query debugger info via OP_INFO (supported on both v1 and v2 daemons)."""
        return dbg.request(cd.OP_INFO)["payload"].decode("utf-8", errors="replace").strip()

    def _find_im_pid(self, dbg: Any) -> int:
        """Locate the single running ``im.elf`` PID dynamically (fail-closed if 0 or >1)."""
        ps_raw = dbg.request(cd.OP_PS)["payload"].decode("utf-8", errors="replace")
        pids: list[int] = []
        for line in ps_raw.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and parts[1].strip() == "im.elf":
                pids.append(int(parts[0]))
        if not pids:
            raise RuntimeError("No running 'im.elf' process found on camera; aborting safely.")
        if len(pids) > 1:
            raise RuntimeError(f"Multiple 'im.elf' processes found ({pids}); aborting safely.")
        pid = pids[0]
        comm = self._exec(dbg, f"cat /proc/{pid}/comm").strip()
        if comm != "im.elf":
            raise RuntimeError(f"PID {pid} is {comm!r}, expected 'im.elf'; aborting safely.")
        return pid

    def _read_im_layout(self, dbg: Any, im_pid: int) -> dict[str, int | None]:
        """Resolve ``viewUnified2.so`` and ``libtestcmd.so`` runtime bases from ``/proc/<pid>/maps``."""
        maps_bytes, _ = dbg.read_all(f"/proc/{im_pid}/maps", limit=512 * 1024)
        view_base: int | None = None
        testcmd_base: int | None = None
        for line in maps_bytes.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 6 or parts[2] != "00000000":
                continue
            addr_text = parts[0].split("-", 1)[0]
            try:
                base = int(addr_text, 16)
            except ValueError:
                continue
            path = parts[5]
            if path.endswith("/viewUnified2.so") and view_base is None:
                view_base = base
            elif path.endswith("/libtestcmd.so") and testcmd_base is None:
                testcmd_base = base
        return {
            "im_pid": im_pid,
            "view_base": view_base,
            "testcmd_base": testcmd_base,
            "view_bias": (view_base - REF_VIEW_BASE) if view_base is not None else None,
            "testcmd_bias": (testcmd_base - REF_TESTCMD_BASE) if testcmd_base is not None else None,
        }

    def _read_hooks(self, dbg: Any, im_pid: int = IM_PID, view_bias: int = 0) -> dict[str, str]:
        return {
            name: dbg.memory(im_pid, (addr + view_bias) & 0xFFFFFFFF, size).hex()
            for name, (addr, size) in MLV2_HOOKS.items()
        }

    @staticmethod
    def _expected_v2_branches(entries: list[int], testcmd_bias: int = 0) -> list[bytes]:
        return [
            b"\xdf\xf8\x00\xf0" + struct.pack("<I", (e + testcmd_bias) & 0xFFFFFFFF)
            for e in entries
        ]

    def _inspect_debugger_state(self, dbg: Any) -> dict[str, object]:
        info = self._probe_debugger_info(dbg)
        uptime_parts = self._exec(dbg, "cat /proc/uptime").strip().split()
        uptime = uptime_parts[0] if uptime_parts else ""
        im_pid: int | None = None
        comm = ""
        view_bias: int | None = None
        testcmd_bias: int | None = None
        hooks: dict[str, str] = {}
        try:
            im_pid = self._find_im_pid(dbg)
            comm = "im.elf"
            layout = self._read_im_layout(dbg, im_pid)
            view_bias = layout["view_bias"]
            testcmd_bias = layout["testcmd_bias"]
            if view_bias is not None:
                hooks = self._read_hooks(dbg, im_pid, view_bias)
        except RuntimeError:
            pass
        core_present = (
            self._exec(dbg, "test -f /tmp_bt/mlv2/core.bin && echo YES").strip() == "YES"
        )
        raw_mods = self._exec(
            dbg, 'for f in /tmp_bt/mods/*.mod; do [ -f "$f" ] && echo "$f"; done'
        )
        staged_mods: list[dict[str, object]] = []
        for line in raw_mods.splitlines():
            remote_path = line.strip()
            if not remote_path.endswith(".mod"):
                continue
            fname = Path(remote_path).name
            try:
                blob, truncated = dbg.read_all(remote_path, limit=1024 * 1024)
                digest = None if truncated else _sha256_bytes(blob)
                size = len(blob)
            except Exception:
                digest = None
                size = None
            staged_mods.append(
                {
                    "filename": fname,
                    "remote_path": remote_path,
                    "bytes": size,
                    "sha256": digest,
                }
            )
        log_tail = self._exec(
            dbg, "tail -n 20 /tmp_bt/mods/mod_loader.log 2>/dev/null"
        ).strip()
        return {
            "debugger_info": info,
            "uptime": uptime,
            "im_pid": im_pid,
            "im_comm": comm,
            "view_bias": view_bias,
            "testcmd_bias": testcmd_bias,
            "hooks": hooks,
            "core_present": core_present,
            "staged_mods": staged_mods,
            "log_tail": log_tail,
        }

    def detect_camera(self, *, mlv2_status: dict[str, object] | None = None) -> dict[str, object]:
        """Passively detect connected camera state without forcing any USB mode switch."""
        dbg_details: dict[str, object] | None = None
        dbg_error: str | None = None
        try:
            with self.transport.debugger_session() as dbg:
                dbg_details = self._inspect_debugger_state(dbg)
        except Exception as exc:
            dbg_error = f"{type(exc).__name__}: {exc}"

        msc_identity: dict[str, str] | None = None
        msc_error: str | None = None
        try:
            msc_identity = self.transport.detect_msc_identity()
        except Exception as exc:
            msc_error = f"{type(exc).__name__}: {exc}"

        if dbg_details is not None:
            model_name = (msc_identity or {}).get("model_name") or "ILCE-6400"
            model_code = (msc_identity or {}).get("model_code") or ""
            firmware_version = (msc_identity or {}).get("firmware_version") or ""
            spec = find_debugger_target_by_model(model_name, firmware_version or None)
            hook_state = "unknown"
            hooks = dbg_details.get("hooks", {})
            testcmd_bias = dbg_details.get("testcmd_bias")
            if mlv2_status and mlv2_status.get("available") and hooks and testcmd_bias is not None:
                entries = mlv2_status.get("entry_ints") or []
                expect = self._expected_v2_branches(entries, int(testcmd_bias))
                if len(entries) == 3 and all(
                    bytes.fromhex(hooks[k])[:8] == e
                    for k, e in zip(MLV2_HOOKS, expect)
                    if k in hooks
                ):
                    hook_state = "v2"
            if hook_state == "unknown" and dbg_details.get("core_present"):
                hook_state = "v2_core_staged"
            return {
                "connected": True,
                "state": "debugger_ready",
                "transport": "native_debugger",
                "debugger_active": True,
                "model_name": model_name,
                "model_code": model_code,
                "firmware_version": firmware_version,
                "qualification": spec.qualification if spec else "verified",
                "bootstrap_mode": spec.bootstrap_mode if spec else "senser_ff01",
                "mlv2_hook_state": hook_state,
                "staged_mods": dbg_details["staged_mods"],
                "uptime": dbg_details["uptime"],
                "im_pid": dbg_details["im_pid"],
                "im_comm": dbg_details["im_comm"],
                "debugger_info": dbg_details["debugger_info"],
                "log_tail": dbg_details["log_tail"],
                "detail": "Native USB debugger active in normal Mass Storage mode.",
            }

        if msc_identity is not None:
            model_name = msc_identity.get("model_name") or "Unknown Sony Camera"
            firmware_version = msc_identity.get("firmware_version") or ""
            spec = find_debugger_target_by_model(model_name, firmware_version or None)
            if spec is not None and spec.bootstrap_mode != "senser_ff01":
                return {
                    "connected": True,
                    "state": "msc_blocked",
                    "transport": "msc",
                    "debugger_active": False,
                    "model_name": model_name,
                    "model_code": msc_identity.get("model_code", ""),
                    "firmware_version": firmware_version,
                    "qualification": spec.qualification,
                    "bootstrap_mode": spec.bootstrap_mode,
                    "mlv2_hook_state": "none",
                    "staged_mods": [],
                    "detail": (
                        f"{spec.model_name} uses {spec.bootstrap_mode} and does not allow the "
                        "Senser FileControl debugger installer. Runtime mod deployment is blocked."
                    ),
                }
            if spec is not None:
                return {
                    "connected": True,
                    "state": "msc_verified",
                    "transport": "msc",
                    "debugger_active": False,
                    "model_name": model_name,
                    "model_code": msc_identity.get("model_code", ""),
                    "firmware_version": firmware_version,
                    "qualification": spec.qualification,
                    "bootstrap_mode": spec.bootstrap_mode,
                    "mlv2_hook_state": "none",
                    "staged_mods": [],
                    "detail": (
                        f"{model_name} detected in Mass Storage mode. 1-Click Install will bootstrap "
                        "the native USB debugger (once) and deploy Mod Loader v2."
                    ),
                }
            return {
                "connected": True,
                "state": "msc_unsupported",
                "transport": "msc",
                "debugger_active": False,
                "model_name": model_name,
                "model_code": msc_identity.get("model_code", ""),
                "firmware_version": firmware_version,
                "qualification": "unverified",
                "bootstrap_mode": "unknown",
                "mlv2_hook_state": "none",
                "staged_mods": [],
                "detail": (
                    f"{model_name} is not yet qualified for automatic debugger install. "
                    "Use Tools -> Collect Onboarding Bundle for a safe read-only dump."
                ),
            }

        return {
            "connected": False,
            "state": "offline",
            "transport": "none",
            "debugger_active": False,
            "model_name": None,
            "model_code": None,
            "firmware_version": None,
            "qualification": None,
            "bootstrap_mode": None,
            "mlv2_hook_state": "none",
            "staged_mods": [],
            "detail": msc_error or dbg_error or "No Sony camera detected. Connect via USB in Mass Storage mode.",
        }

    def read_live_tweaks(self) -> dict[str, object]:
        """Enter Senser once to read current PMCA tweak states without modifying anything."""
        identity = self.transport.detect_msc_identity()
        model_name = identity.get("model_name") or ""
        fw = identity.get("firmware_version") or None
        spec = find_debugger_target_by_model(model_name, fw)
        if spec is not None and spec.bootstrap_mode != "senser_ff01":
            raise RuntimeError(
                f"{spec.model_name} uses {spec.bootstrap_mode}; Senser tweak readout is blocked."
            )

        captured: dict[str, object] = {}

        def _on_senser(cam: Any) -> None:
            captured["tweaks"] = self.transport.query_tweaks(cam)

        self.transport.run_senser(_on_senser)
        return {
            "identity": identity,
            "tweaks": captured.get("tweaks", []),
        }

    def plan_install(
        self,
        selection: dict[str, Any],
        catalog: dict[str, object],
        camera: dict[str, object],
        mlv2_status: dict[str, object],
        *,
        known_tweaks: dict[str, bool] | None = None,
    ) -> dict[str, object]:
        """Compute an explicit, fail-closed diff and execution plan from user toggles."""
        runtime_sel: dict[str, bool] = {
            str(k): bool(v) for k, v in (selection.get("runtime_mods") or {}).items()
        }
        tweak_sel: dict[str, bool | None] = dict(selection.get("pmca_tweaks") or {})

        catalog_mods = {str(m["id"]): m for m in catalog.get("runtime_mods", [])}
        catalog_tweaks = {str(t["id"]): t for t in catalog.get("pmca_tweaks", [])}

        blocking_errors: list[str] = []
        warnings: list[str] = []
        confirmation_items: list[dict[str, str]] = []

        if not camera.get("connected"):
            blocking_errors.append("No Sony camera detected in USB Mass Storage mode.")
        elif camera.get("state") == "msc_blocked":
            blocking_errors.append(str(camera.get("detail") or "Target model blocked from Senser install."))
        elif camera.get("state") == "msc_unsupported":
            blocking_errors.append(
                f"{camera.get('model_name')} is not yet qualified for automated installation. "
                "Export a read-only Onboarding Bundle from Tools & Setup instead."
            )

        enabled_mods: list[dict[str, object]] = []
        exclusive_seen: dict[str, str] = {}
        for mod_id, is_on in runtime_sel.items():
            if not is_on:
                continue
            mod = catalog_mods.get(mod_id)
            if mod is None:
                blocking_errors.append(f"Unknown mod ID selected: {mod_id}")
                continue
            if not mod.get("available"):
                blocking_errors.append(str(mod.get("unavailable_reason") or f"{mod['title']} is unavailable"))
                continue
            group = mod.get("exclusive_group")
            if group:
                if group in exclusive_seen:
                    blocking_errors.append(
                        f"Mutually exclusive mods cannot both be ON: {exclusive_seen[str(group)]} and {mod['title']}"
                    )
                else:
                    exclusive_seen[str(group)] = str(mod["title"])
            enabled_mods.append(mod)
            if mod.get("warning"):
                warnings.append(f"{mod['title']}: {mod['warning']}")
                confirmation_items.append(
                    {"kind": "mod_warning", "title": str(mod["title"]), "message": str(mod["warning"])}
                )

        max_mods = int(catalog.get("max_active_runtime_mods", 12))
        if len(enabled_mods) > max_mods:
            blocking_errors.append(f"Too many runtime mods enabled ({len(enabled_mods)} > {max_mods}).")

        staged_by_name = {
            str(item["filename"]).lower(): item for item in camera.get("staged_mods", [])
        }
        managed_filenames = {str(m["filename"]).lower() for m in catalog_mods.values()}

        mods_to_add: list[dict[str, object]] = []
        mods_to_update: list[dict[str, object]] = []
        mods_to_keep: list[dict[str, object]] = []
        for mod in enabled_mods:
            fname = str(mod["filename"])
            sha = (mod.get("smod") or {}).get("sha256")
            existing = staged_by_name.get(fname.lower())
            entry = {
                "id": mod["id"],
                "title": mod["title"],
                "filename": fname,
                "path": mod["path"],
                "sha256": sha,
            }
            if existing is None:
                mods_to_add.append(entry)
            elif existing.get("sha256") and existing.get("sha256") == sha:
                mods_to_keep.append(entry)
            else:
                mods_to_update.append(entry)

        enabled_lower = {str(m["filename"]).lower() for m in enabled_mods}
        mods_to_remove = [
            {"filename": item["filename"], "remote_path": item["remote_path"]}
            for fname_lower, item in staged_by_name.items()
            if fname_lower not in enabled_lower and fname_lower in managed_filenames
        ]

        tweak_changes: list[dict[str, object]] = []
        known_map = known_tweaks or {}
        for tweak_id, desired in tweak_sel.items():
            if desired is None or tweak_id not in catalog_tweaks:
                continue
            target_bool = bool(desired)
            if tweak_id in known_map and known_map[tweak_id] is target_bool:
                continue
            tweak_info = catalog_tweaks[tweak_id]
            tweak_changes.append(
                {
                    "id": tweak_id,
                    "title": tweak_info["title"],
                    "enabled": target_bool,
                    "previous": known_map.get(tweak_id),
                }
            )
            action_word = "ENABLE" if target_bool else "DISABLE"
            confirmation_items.append(
                {
                    "kind": "permanent_flash",
                    "title": f"PMCA Flash Tweak: {tweak_info['title']} -> {action_word}",
                    "message": "Writes directly to Sony backup flash via Senser TweakInterface.",
                }
            )

        wants_runtime_ops = bool(mods_to_add or mods_to_update or mods_to_remove or enabled_mods)
        hook_state = camera.get("mlv2_hook_state")
        needs_runtime_sync = bool(
            mods_to_add
            or mods_to_update
            or mods_to_remove
            or (enabled_mods and hook_state != "v2")
        )
        needs_debugger_bootstrap = bool(
            wants_runtime_ops and camera.get("connected") and not camera.get("debugger_active")
        )

        if needs_debugger_bootstrap:
            confirmation_items.append(
                {
                    "kind": "debugger_bootstrap",
                    "title": "One-Time Native USB Debugger Setup (/setting)",
                    "message": (
                        "Verifies usb_extcmd.ko & libusbcmd.so via Senser, writes debugger binaries to "
                        "/setting and /etc/ld.so.preload, then asks you to power-cycle the camera once."
                    ),
                }
            )

        if needs_runtime_sync and not mlv2_status.get("available"):
            blocking_errors.append(
                str(
                    mlv2_status.get("error")
                    or "No verified Mod Loader v2 build (core.bin, mlv2.req, mlv2_guard_arm) available."
                )
            )

        has_changes = bool(
            tweak_changes
            or needs_debugger_bootstrap
            or mods_to_add
            or mods_to_update
            or mods_to_remove
            or (enabled_mods and hook_state != "v2")
        )

        return {
            "has_changes": has_changes,
            "blocking_errors": blocking_errors,
            "warnings": warnings,
            "requires_confirmation": bool(confirmation_items),
            "confirmation_items": confirmation_items,
            "needs_debugger_bootstrap": needs_debugger_bootstrap,
            "needs_runtime_sync": needs_runtime_sync,
            "mods_to_add": mods_to_add,
            "mods_to_update": mods_to_update,
            "mods_to_keep": mods_to_keep,
            "mods_to_remove": mods_to_remove,
            "tweak_changes": tweak_changes,
            "camera_model": camera.get("model_name"),
            "camera_state": camera.get("state"),
        }

    def _bootstrap_debugger(
        self,
        *,
        emit: Callable[[str, str, str], None],
        wait_for_power_cycle_cb: Callable[[Callable[[], bool], threading.Event | None], bool] | None,
        cancel_event: threading.Event | None,
    ) -> dict[str, object]:
        identity = self.transport.detect_msc_identity()
        model_name = identity.get("model_name") or ""
        fw = identity.get("firmware_version") or None
        spec = find_debugger_target_by_model(model_name, fw)
        if spec is not None and spec.bootstrap_mode != "senser_ff01":
            raise RuntimeError(f"{spec.model_name} does not use Senser FileControl.")

        payload_root = resolve_debugger_payload_root(root=self.root, data_dir=self.data_dir)
        outcome: dict[str, object] = {}

        def _on_senser(cam: Any) -> None:
            previous_preload, preload_existed = read_preload_safe(cam)
            actual_ko = cam.readFile("/usr/kmod/usb_extcmd.ko")
            actual_lib = cam.readFile("/usr/lib/libusbcmd.so")

            compat = None
            if spec is not None:
                _, ref_ko = load_reference_usb_extcmd(spec, self.root)
                compat = verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, ref_ko)

            if spec is not None and compat is not None and compat["compatible"]:
                dest = self.data_dir / "bootstrap-manifests" / (
                    f"native-usb-debug-deploy-{spec.target_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                )
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "ld.so.preload.before").write_bytes(previous_preload)
                (dest / "kmod__usb_extcmd.ko").write_bytes(actual_ko)
                (dest / "lib__libusbcmd.so").write_bytes(actual_lib)
                manifest = {
                    "target_id": spec.target_id,
                    "model_name": spec.model_name,
                    "qualification": spec.qualification,
                    "preload_existed_before": preload_existed,
                    "bootstrap_mode": "Senser",
                    "operating_transport": "native Sony USB external command",
                    "feature": 35,
                    "network_listeners": False,
                    "compatibility": compat,
                    "files": {},
                }
                # Resolve payload directory dynamically because frozen builds read from _MEIPASS or data_dir.
                for rel in (spec.payload_daemon, spec.payload_launcher):
                    if not (payload_root / rel).is_file():
                        raise FileNotFoundError(
                            f"Debugger binary missing ({rel}). Run 'Prepare Tools' first."
                        )
                old_root = None
                import tools.deploy_native_usb_debugger as dnu

                old_root = dnu.ROOT
                dnu.ROOT = payload_root
                try:
                    upload_and_verify_debugger(cam, spec, previous_preload, dest, manifest)
                finally:
                    dnu.ROOT = old_root
                outcome["branch"] = "supported"
                outcome["manifest_dir"] = str(dest)
                return

            bundle_path = build_onboarding_bundle(
                cam,
                identity,
                actual_ko,
                actual_lib,
                previous_preload,
                preload_existed,
                compat_result=compat,
                root=self.data_dir,
            )
            reason = (
                str(compat["reason"])
                if isinstance(compat, dict) and compat.get("reason")
                else "Camera model or firmware is not in the verified target registry."
            )
            outcome["branch"] = "onboarding"
            outcome["bundle_path"] = str(bundle_path)
            outcome["reason"] = reason
            outcome["compatibility"] = compat
            emit("bootstrap", "error", f"Compatibility check stopped: {reason}")

        emit("bootstrap", "running", "Inspecting camera ABI and usb_extcmd.ko via Senser...")
        self.transport.run_senser(_on_senser)

        if outcome.get("branch") == "onboarding":
            return outcome

        emit(
            "bootstrap",
            "waiting_power_cycle",
            "Debugger installed to /setting. Ensure Bluetooth is ON (Airplane Mode OFF), then power-cycle (OFF -> unplug USB -> wait 3s -> ON -> replug MSC).",
        )

        probe_state: dict[str, object] = {"last_hint_ts": 0.0}

        def _probe_debugger() -> bool:
            try:
                with self.transport.debugger_session() as dbg:
                    # Probe OP_INFO instead of OP_PING because deployed usb-debugger-binaries-v1
                    # ships A6400_USB_DEBUG_V1 which does not implement OP_PING.
                    self._probe_debugger_info(dbg)
                return True
            except Exception as exc:
                now = time.monotonic()
                if now - float(probe_state["last_hint_ts"]) >= 12.0:
                    probe_state["last_hint_ts"] = now
                    err_text = str(exc)
                    if "Sense 0x5 0x20 0x0" in err_text or "InvalidCommandException" in type(exc).__name__:
                        emit(
                            "bootstrap",
                            "waiting_power_cycle",
                            "Camera is in MSC mode, but debugger feature 0x23 is not registered yet. Enable Bluetooth (Airplane Mode OFF), unplug USB, power OFF for 3s, power ON, then replug.",
                        )
                return False

        if wait_for_power_cycle_cb is not None:
            reconnected = wait_for_power_cycle_cb(_probe_debugger, cancel_event)
        else:
            reconnected = self._default_wait_for_debugger(_probe_debugger, cancel_event)

        if not reconnected:
            raise RuntimeError("Cancelled or timed out waiting for camera power-cycle reconnect.")
        emit("bootstrap", "done", "Native USB debugger active after power-cycle.")
        return outcome

    def _default_wait_for_debugger(
        self,
        probe_fn: Callable[[], bool],
        cancel_event: threading.Event | None,
        timeout: float = 180.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return False
            if probe_fn():
                return True
            time.sleep(1.2)
        return False

    def _sync_mlv2_and_mods(
        self,
        plan: dict[str, object],
        mlv2_status: dict[str, object],
        emit: Callable[[str, str, str], None],
    ) -> dict[str, object]:
        build_dir = Path(str(mlv2_status["path"]))
        verified_mlv2 = validate_mlv2_build(build_dir)
        core_bytes = (build_dir / "core.bin").read_bytes()
        req_bytes = (build_dir / "mlv2.req").read_bytes()
        guard_bytes = (build_dir / "mlv2_guard_arm").read_bytes()
        entries = verified_mlv2["entry_ints"]

        receipt: dict[str, object] = {
            "mlv2_build": str(build_dir),
            "core_sha256": verified_mlv2["core_sha256"],
            "mods_pushed": [],
            "mods_reloaded": [],
            "mods_removed": [],
        }

        with self.transport.debugger_session() as dbg:
            receipt["uptime_before"] = self._exec(dbg, "cat /proc/uptime").strip()
            im_pid = self._find_im_pid(dbg)
            layout = self._read_im_layout(dbg, im_pid)
            if layout["view_bias"] is None or layout["testcmd_bias"] is None:
                raise RuntimeError(
                    f"Could not locate viewUnified2.so / libtestcmd.so in /proc/{im_pid}/maps; aborting safely."
                )
            view_bias = int(layout["view_bias"])
            testcmd_bias = int(layout["testcmd_bias"])
            receipt["im_pid"] = im_pid
            receipt["comm"] = "im.elf"
            receipt["view_base"] = hex(int(layout["view_base"]))
            receipt["testcmd_base"] = hex(int(layout["testcmd_base"]))
            receipt["view_bias"] = view_bias
            receipt["testcmd_bias"] = testcmd_bias

            receipt["hooks_before"] = self._read_hooks(dbg, im_pid, view_bias)
            writable_check = self._exec(
                dbg, "mkdir -p /tmp_bt/mlv2 /tmp_bt/mods 2>/dev/null && echo OK"
            )
            if "OK" not in writable_check:
                writable_check = self._exec(
                    dbg,
                    "mount -t tmpfs -o size=4096k tmpfs /tmp_bt 2>/dev/null && "
                    "mkdir -p /tmp_bt/mlv2 /tmp_bt/mods 2>/dev/null && echo OK",
                )
            if "OK" not in writable_check:
                raise RuntimeError(
                    "/tmp_bt is not writable on camera (enable Bluetooth / disable Airplane Mode and retry)."
                )

            emit("mlv2_sync", "running", "Running mlv2_guard check against live firmware hooks...")
            dbg.push(GUARD_REMOTE, guard_bytes)
            dbg.push(REQ_REMOTE, req_bytes)
            self._exec(dbg, f"chmod 700 {GUARD_REMOTE}")
            try:
                check_out = self._exec(dbg, f"{GUARD_REMOTE} check {REQ_REMOTE}; echo GUARD_EXIT=$?")
                state_line = next((ln for ln in check_out.splitlines() if ln.startswith("STATE ")), "")
                receipt["guard_check_state"] = state_line

                if "hooks=stock" in state_line:
                    guard_op = "install"
                elif "hooks=v1" in state_line:
                    guard_op = "upgrade"
                elif "hooks=v2" in state_line and "shim=shim" in state_line:
                    guard_op = "already"
                else:
                    raise RuntimeError(
                        f"Refusing install: hook state is unrecognized ({state_line or check_out.strip()!r})."
                    )

                emit("mlv2_sync", "running", "Staging Mod Loader v2 core.bin atomically...")
                dbg.push("/tmp_bt/mlv2/core.bin.tmp", core_bytes)
                self._exec(dbg, "mv -f /tmp_bt/mlv2/core.bin.tmp /tmp_bt/mlv2/core.bin")

                if guard_op != "already":
                    emit("mlv2_sync", "running", f"Applying Mod Loader v2 hooks ({guard_op})...")
                    op_out = self._exec(dbg, f"{GUARD_REMOTE} {guard_op} {REQ_REMOTE}; echo GUARD_EXIT=$?")
                    guard_res = next((ln for ln in op_out.splitlines() if ln.startswith("MLV2_RESULT")), "")
                    receipt["guard_result"] = guard_res
                    ok = "GUARD_EXIT=0" in op_out and ("INSTALLED" in op_out or "UPGRADED" in op_out)
                    if not ok:
                        raise RuntimeError(f"mlv2_guard {guard_op} failed: {op_out.strip()}")
                else:
                    receipt["guard_result"] = "already_v2"
            finally:
                # Delete ~678 KiB mlv2_guard + mlv2.req before staging .mod files into /tmp_bt.
                self._exec(dbg, f"rm -f {GUARD_REMOTE} {REQ_REMOTE}")

            # Push newly added mods (and updated mods when loader is not yet active).
            to_push = list(plan["mods_to_add"])
            reload_queue: list[dict[str, object]] = []
            for item in plan["mods_to_update"]:
                if guard_op == "already" and verified_mlv2["reload_capable"]:
                    reload_queue.append(item)
                else:
                    to_push.append(item)

            for item in to_push:
                fname = validate_mod_filename(str(item["filename"]))
                mod_bytes = Path(str(item["path"])).read_bytes()
                part = f"/tmp_bt/mods/.{fname}.part"
                dst = f"/tmp_bt/mods/{fname}"
                emit("mlv2_sync", "running", f"Pushing {fname} with read-back verification...")
                dbg.push(part, mod_bytes)
                self._exec(dbg, f"mv -f {shlex.quote(part)} {shlex.quote(dst)}")
                receipt["mods_pushed"].append({"filename": fname, "sha256": _sha256_bytes(mod_bytes)})

            for item in plan["mods_to_remove"]:
                fname = validate_mod_filename(str(item["filename"]))
                dst = f"/tmp_bt/mods/{fname}"
                emit("mlv2_sync", "running", f"Removing toggled-OFF mod {fname} from /tmp_bt/mods/...")
                self._exec(dbg, f"rm -f {shlex.quote(dst)}")
                receipt["mods_removed"].append(fname)

            for item in reload_queue:
                fname = validate_mod_filename(str(item["filename"]))
                mod_bytes = Path(str(item["path"])).read_bytes()
                smod_info = parse_smod(mod_bytes, filename=fname)
                part = f"/tmp_bt/mods/.{fname}.part"
                dst = f"/tmp_bt/mods/{fname}"
                emit("mlv2_sync", "running", f"Hot-reloading {fname} (open camera MENU if prompted)...")
                dbg.push(part, mod_bytes)
                self._exec(dbg, f"mv -f {shlex.quote(part)} {shlex.quote(dst)}")
                self._exec(dbg, f"rm -f {RELOAD_RESULT}")
                dbg.push(RELOAD_REQ + ".part", fname.encode("ascii") + b"\n")
                self._exec(dbg, f"mv -f {RELOAD_REQ}.part {RELOAD_REQ}")

                deadline = time.monotonic() + 30.0
                result = None
                while time.monotonic() < deadline:
                    out = self._exec(dbg, f"cat {RELOAD_RESULT} 2>/dev/null; test -f {RELOAD_REQ} && echo PENDING")
                    result = parse_result(out.splitlines()[0]) if out.strip() else None
                    if result:
                        break
                    time.sleep(0.5)
                if result is None or result[0] != "reload_ok":
                    raise RuntimeError(f"Hot-reload failed or timed out for {fname}: {result}")
                base = result[1]
                if base == 0:
                    raise RuntimeError(f"Hot-reload returned invalid base 0x0 for {fname}")
                if smod_info.has_reloc:
                    if len(dbg.memory(im_pid, base, 4)) != 4:
                        raise RuntimeError(f"Hot-reload could not read relocated code at {hex(base)} for {fname}")
                else:
                    smod_meta = parse_smod_v2(mod_bytes)
                    hooks_ok = all(
                        dbg.memory(im_pid, h["target"], h["size"]) == hook_bytes(h, base)
                        for h in smod_meta["hooks"]
                    )
                    if not hooks_ok:
                        raise RuntimeError(f"Hot-reload hook verification mismatch for {fname}")
                receipt["mods_reloaded"].append({"filename": fname, "base": hex(base)})

            receipt["hooks_after"] = self._read_hooks(dbg, im_pid, view_bias)
            expect = self._expected_v2_branches(entries, testcmd_bias)
            receipt["hooks_point_to_v2"] = all(
                bytes.fromhex(receipt["hooks_after"][k])[:8] == e
                for k, e in zip(MLV2_HOOKS, expect)
            )
            if not receipt["hooks_point_to_v2"]:
                raise RuntimeError("Post-install hook verification failed: branches do not point to MLv2 shim.")
            receipt["uptime_after"] = self._exec(dbg, "cat /proc/uptime").strip()
            try:
                receipt["ping"] = dbg.ping()
            except cd.DebuggerError:
                # A6400_USB_DEBUG_V1 returns ENOSYS on OP_PING; verify transport via OP_INFO.
                receipt["ping"] = self._probe_debugger_info(dbg)
        return receipt

    def execute_install(
        self,
        plan: dict[str, object],
        catalog: dict[str, object],
        mlv2_status: dict[str, object],
        *,
        confirmed_permanent: bool = False,
        progress_cb: Callable[[dict[str, object]], None] | None = None,
        wait_for_power_cycle_cb: Callable[[Callable[[], bool], threading.Event | None], bool] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, object]:
        """Execute a validated install plan step-by-step and save a JSON receipt."""
        del catalog  # plan already references verified catalog paths & hashes
        if plan.get("blocking_errors"):
            raise RuntimeError("; ".join(str(e) for e in plan["blocking_errors"]))
        if plan.get("requires_confirmation") and not confirmed_permanent:
            raise RuntimeError("Explicit user confirmation is required before permanent writes or risky mods.")

        logs: list[str] = []

        def emit(step: str, status: str, message: str) -> None:
            logs.append(f"[{step}] {message}")
            if progress_cb:
                progress_cb({"step": step, "status": status, "message": message})

        receipt: dict[str, object] = {
            "operation": "one_click_install",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "camera_model": plan.get("camera_model"),
            "status": "started",
            "tweaks": None,
            "bootstrap": None,
            "mlv2_sync": None,
            "logs": logs,
        }

        emit("verify", "running", "Verifying local mod binaries and checksums...")
        for item in [*plan["mods_to_add"], *plan["mods_to_update"]]:
            mod_path = Path(str(item["path"]))
            data = mod_path.read_bytes()
            if _sha256_bytes(data) != item.get("sha256"):
                raise RuntimeError(f"SHA-256 mismatch on {item['filename']} before upload.")
        emit("verify", "done", "All selected mod binaries passed integrity checks.")

        tweak_changes = list(plan.get("tweak_changes") or [])
        if tweak_changes:
            desired_map = {str(t["id"]): bool(t["enabled"]) for t in tweak_changes}
            emit("tweaks", "running", f"Applying {len(desired_map)} PMCA tweak(s) via Senser...")
            tweak_result: dict[str, object] = {}

            def _apply_senser(cam: Any) -> None:
                tweak_result["readback"] = self.transport.apply_tweaks(cam, desired_map)

            self.transport.run_senser(_apply_senser)
            receipt["tweaks"] = tweak_result["readback"]
            emit("tweaks", "done", "PMCA firmware tweaks applied and verified by read-back.")

        if plan.get("needs_debugger_bootstrap"):
            boot_outcome = self._bootstrap_debugger(
                emit=emit,
                wait_for_power_cycle_cb=wait_for_power_cycle_cb,
                cancel_event=cancel_event,
            )
            receipt["bootstrap"] = boot_outcome
            if boot_outcome.get("branch") == "onboarding":
                receipt["status"] = "onboarding_collected"
                out_path = _unique_receipt_path(self.data_dir / "receipts", "install-receipt")
                out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
                receipt["receipt_path"] = str(out_path)
                return receipt

        if plan.get("needs_runtime_sync"):
            emit("mlv2_sync", "running", "Syncing Mod Loader v2 and toggled runtime mods (RAM-only)...")
            receipt["mlv2_sync"] = self._sync_mlv2_and_mods(plan, mlv2_status, emit)
            emit("mlv2_sync", "done", "Mod Loader v2 and runtime mods synced and verified.")

        receipt["status"] = "success"
        out_path = _unique_receipt_path(self.data_dir / "receipts", "install-receipt")
        out_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        receipt["receipt_path"] = str(out_path)
        emit("receipt", "done", f"Receipt saved to {out_path}")
        return receipt

    def check_debugger_compatibility(
        self,
        progress_cb: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Run a strictly read-only Senser compatibility check (writes nothing to camera)."""
        if progress_cb:
            progress_cb("Reading camera identity in Mass Storage mode...")
        identity = self.transport.detect_msc_identity()
        model_name = identity.get("model_name") or ""
        fw = identity.get("firmware_version") or None
        spec = find_debugger_target_by_model(model_name, fw)
        if spec is None:
            return {
                "compatible": False,
                "identity": identity,
                "reason": f"{model_name or 'Unknown model'} is not registered in USB_DEBUGGER_TARGETS.",
            }
        if spec.bootstrap_mode != "senser_ff01":
            return {
                "compatible": False,
                "identity": identity,
                "reason": f"{spec.model_name} uses {spec.bootstrap_mode}; Senser FileControl does not apply.",
            }

        compat_out: dict[str, object] = {}

        def _on_senser(cam: Any) -> None:
            if progress_cb:
                progress_cb("Reading usb_extcmd.ko and libusbcmd.so (read-only)...")
            actual_ko = cam.readFile("/usr/kmod/usb_extcmd.ko")
            actual_lib = cam.readFile("/usr/lib/libusbcmd.so")
            _, ref_ko = load_reference_usb_extcmd(spec, self.root)
            compat_out.update(verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, ref_ko))

        self.transport.run_senser(_on_senser)
        compat_out["identity"] = identity
        return compat_out

    def collect_onboarding_bundle(
        self,
        progress_cb: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Collect a read-only onboarding diagnostic zip for an unsupported camera body."""
        if progress_cb:
            progress_cb("Reading MSC camera identity...")
        identity = self.transport.detect_msc_identity()
        model_name = identity.get("model_name") or ""
        fw = identity.get("firmware_version") or None
        spec = find_debugger_target_by_model(model_name, fw)
        if spec is not None and spec.bootstrap_mode != "senser_ff01":
            raise RuntimeError(f"{spec.model_name} uses {spec.bootstrap_mode}; Senser probe skipped.")

        result: dict[str, object] = {}

        def _on_senser(cam: Any) -> None:
            if progress_cb:
                progress_cb("Reading kernel/libc metadata (read-only)...")
            previous_preload, preload_existed = read_preload_safe(cam)
            actual_ko = cam.readFile("/usr/kmod/usb_extcmd.ko")
            actual_lib = cam.readFile("/usr/lib/libusbcmd.so")
            compat = None
            if spec is not None:
                _, ref_ko = load_reference_usb_extcmd(spec, self.root)
                compat = verify_camera_debugger_compatibility(spec, actual_ko, actual_lib, ref_ko)
            bundle = build_onboarding_bundle(
                cam,
                identity,
                actual_ko,
                actual_lib,
                previous_preload,
                preload_existed,
                compat_result=compat,
                root=self.data_dir,
            )
            result["bundle_path"] = str(bundle)
            result["identity"] = identity

        self.transport.run_senser(_on_senser)
        return result

    def list_receipts(self, limit: int = 15) -> list[dict[str, object]]:
        """List recent install and SD sync receipts newest-first."""
        receipts_dir = self.data_dir / "receipts"
        if not receipts_dir.is_dir():
            return []
        files = sorted(receipts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        items: list[dict[str, object]] = []
        for path in files[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(
                    {
                        "filename": path.name,
                        "path": str(path),
                        "operation": data.get("operation", "unknown"),
                        "timestamp": data.get("timestamp", ""),
                        "status": data.get("status", "ok"),
                        "camera_model": data.get("camera_model"),
                    }
                )
            except Exception:
                continue
        return items
