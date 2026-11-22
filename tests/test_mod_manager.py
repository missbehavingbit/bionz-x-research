"""Offline unit tests for the BIONZ X Mod Manager engine, SD sync, and WebView2/HTTP bridge."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from project_paths import PROJECT_ROOT
from src.a6400.mod_manager.camera_service import (
    MLV2_HOOKS,
    REF_TESTCMD_BASE,
    REF_VIEW_BASE,
    CameraService,
)
from src.a6400.mod_manager.catalog import (
    MAX_ACTIVE_RUNTIME_MODS,
    build_catalog,
    import_custom_mod_bytes,
    remove_custom_mod,
)
from src.a6400.mod_manager.sd_sync import execute_sd_sync, list_sd_drives, plan_sd_sync
from src.a6400.mod_manager.smod import (
    SMOD_MAGIC,
    SMOD_V2_NO_FIXUP,
    SmodError,
    fnv1a_32,
    parse_smod,
    validate_mod_filename,
)
from src.a6400.mod_manager import tools_bundle
from src.a6400.mod_manager.tools_bundle import (
    ensure_arrivederci_v5,
    ensure_mlv2_release,
    find_pmca_dir,
    resolve_mlv2_build,
    validate_mlv2_build,
)
from tools.mod_manager_gui import ModManagerApi, create_web_server


def _make_smod_v2(
    name: str = "Test Mod",
    code: bytes = b"\x70\x47\x00\xbf",
    hooks: list[tuple[int, bytes, bytes]] | None = None,
    flags: int = 0,
) -> bytes:
    hooks = hooks or []
    hdr = struct.pack(
        "<III32s6I",
        SMOD_MAGIC,
        2,
        flags,
        name.encode("ascii")[:31],
        len(code),
        fnv1a_32(code),
        0,
        0,
        0,
        len(hooks),
    )
    blob = bytearray(hdr)
    for target, orig, tmpl in hooks:
        blob += struct.pack(
            "<IBB2x16s16s",
            target,
            len(orig),
            SMOD_V2_NO_FIXUP,
            orig.ljust(16, b"\x00"),
            tmpl.ljust(16, b"\x00"),
        )
    return bytes(blob) + code


def _create_fake_mlv2_dir(path: Path, *, reload_capable: bool = True, corrupt: bool = False) -> dict[str, object]:
    path.mkdir(parents=True, exist_ok=True)
    core = b"MLCR" + (b"/tmp_bt/mlv2/reload\x00" if reload_capable else b"\x00" * 24)
    entries = (0x05662A19, 0x05662A69, 0x05662A79)
    req = struct.pack("<4I3I2I", 0x5132484D, 2, 0x05662760, 912, *entries, 0x12345678, 0)
    guard = b"GUARDARM"
    (path / "core.bin").write_bytes(core)
    (path / "mlv2.req").write_bytes(req)
    (path / "mlv2_guard_arm").write_bytes(guard)
    core_sha = hashlib.sha256(core).hexdigest()
    req_sha = hashlib.sha256(req).hexdigest()
    if corrupt:
        core_sha = "0" * 64
    build_json = {
        "core": {"sha256": core_sha},
        "request": {"sha256": req_sha},
    }
    (path / "build.json").write_text(json.dumps(build_json), encoding="utf-8")
    return {"entries": entries, "core_sha": core_sha, "req_sha": req_sha}


class _FakeDebugger:
    def __init__(
        self,
        *,
        hook_state: str = "stock",
        comm: str = "im.elf",
        im_pid: int = 149,
        view_base: int = REF_VIEW_BASE,
        testcmd_base: int = REF_TESTCMD_BASE,
        extra_procs: list[tuple[int, str]] | None = None,
        mlv2_entries=(0x05662A19, 0x05662A69, 0x05662A79),
        supports_ping: bool = True,
    ):
        self.files: dict[str, bytes] = {}
        self.comm = comm
        self.im_pid = im_pid
        self.view_base = view_base
        self.testcmd_base = testcmd_base
        self.procs: list[tuple[int, str]] = list(extra_procs or []) + [(im_pid, comm)]
        self.hook_state = hook_state
        self.mlv2_entries = mlv2_entries
        self.supports_ping = supports_ping
        self.commands: list[str] = []

    def request(self, op: int, payload: bytes = b"", arg0: int = 0, arg1: int = 0) -> dict[str, object]:
        del arg0, arg1
        if op == 1:  # OP_INFO
            return {"status": 0, "flags": 0, "payload": b"pid=1793 uid=0 proto=2"}
        if op == 3:  # OP_EXEC
            cmd = payload.decode("utf-8", errors="replace")
            self.commands.append(cmd)
            out = self._run_cmd(cmd)
            return {"status": 0, "flags": 0, "payload": out.encode("utf-8")}
        if op == 6:  # OP_PS
            ps_text = "".join(f"{pid} {name}\n" for pid, name in self.procs)
            return {"status": 0, "flags": 0, "payload": ps_text.encode("utf-8")}
        raise AssertionError(f"Unexpected debugger op {op}")

    def _run_cmd(self, cmd: str) -> str:
        if cmd == "cat /proc/uptime":
            return "123.45 120.00\n"
        if cmd.startswith("cat /proc/") and "/comm" in cmd:
            pid_str = cmd.split("/proc/", 1)[1].split("/comm", 1)[0]
            if pid_str.isdigit():
                pid = int(pid_str)
                for p, name in self.procs:
                    if p == pid:
                        return f"{name}\n"
            return ""
        if cmd.startswith("test -f /tmp_bt/mlv2/core.bin"):
            return "YES\n" if "/tmp_bt/mlv2/core.bin" in self.files else ""
        if cmd.startswith("for f in /tmp_bt/mods/*.mod"):
            return "\n".join(sorted(k for k in self.files if k.startswith("/tmp_bt/mods/") and k.endswith(".mod")))
        if cmd.startswith("mv -f "):
            parts = cmd.split()
            src, dst = parts[2].strip("'"), parts[3].strip("'")
            self.files[dst] = self.files.pop(src, b"")
            if dst == "/tmp_bt/mlv2/reload":
                fname = self.files[dst].decode("ascii", errors="replace").strip()
                self.files["/tmp_bt/mlv2/reload.result"] = b"reload_ok 040d0000\n"
                del fname
            return ""
        if cmd.startswith("rm -f "):
            for target in cmd.split()[2:]:
                self.files.pop(target.strip("'"), None)
            return ""
        if " check " in cmd:
            return f"STATE hooks={self.hook_state} shim={'shim' if self.hook_state == 'v2' else 'zero'}\nGUARD_EXIT=0\n"
        if " install " in cmd:
            if self.hook_state != "stock":
                return "MLV2_RESULT=-1\nGUARD_EXIT=1\n"
            self.hook_state = "v2"
            return "MLV2_RESULT=0 changed=1 INSTALLED\nGUARD_EXIT=0\n"
        if " upgrade " in cmd:
            if self.hook_state != "v1":
                return "MLV2_RESULT=-1\nGUARD_EXIT=1\n"
            self.hook_state = "v2"
            return "MLV2_RESULT=0 changed=1 UPGRADED\nGUARD_EXIT=0\n"
        if cmd.startswith("mkdir -p /tmp_bt/mlv2 /tmp_bt/mods"):
            return "OK\n"
        if cmd.startswith("cat /tmp_bt/mlv2/reload.result"):
            res = self.files.get("/tmp_bt/mlv2/reload.result", b"").decode("utf-8", errors="replace")
            return res
        return ""

    def memory(self, pid: int, address: int, size: int) -> bytes:
        del pid
        view_bias = self.view_base - REF_VIEW_BASE
        testcmd_bias = self.testcmd_base - REF_TESTCMD_BASE
        for idx, (_name, (hook_addr, hook_size)) in enumerate(MLV2_HOOKS.items()):
            reloc_hook = (hook_addr + view_bias) & 0xFFFFFFFF
            if address == reloc_hook:
                if self.hook_state == "v2":
                    entry = (self.mlv2_entries[idx] + testcmd_bias) & 0xFFFFFFFF
                    return (b"\xdf\xf8\x00\xf0" + struct.pack("<I", entry)).ljust(hook_size, b"\x00")
                if self.hook_state == "v1":
                    return b"\x01\x02\x03\x04".ljust(hook_size, b"\x00")
                if self.hook_state == "unknown":
                    return b"\xde\xad\xbe\xef".ljust(hook_size, b"\x00")
                return b"\x00" * hook_size
        return b"\x00" * size

    def push(self, path: str, data: bytes) -> dict[str, object]:
        self.files[path] = data
        return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "readback_verified": True}

    def read_all(self, path: str, limit: int = 2 * 1024 * 1024) -> tuple[bytes, bool]:
        del limit
        if path == f"/proc/{self.im_pid}/maps":
            maps_text = (
                f"{self.testcmd_base:08x}-{(self.testcmd_base + 0x3000):08x} r-xp 00000000 fc:0f 22470 /usr/lib/libtestcmd.so\n"
                f"{self.view_base:08x}-{(self.view_base + 0x8ac000):08x} r-xp 00000000 fc:0f 14273 /usr/lib/viewUnified2.so\n"
            )
            return maps_text.encode("utf-8"), False
        return self.files.get(path, b""), False

    def ping(self) -> str:
        if not self.supports_ping:
            from tools.camera_debugger import DebuggerError

            raise DebuggerError(-38, b"ENOSYS")
        return "PONG"


class _FakeTransport:
    def __init__(
        self,
        *,
        debugger: _FakeDebugger | None = None,
        msc_identity: dict[str, str] | None = None,
        tweaks: dict[str, bool] | None = None,
    ):
        self.debugger = debugger
        self.msc_identity = msc_identity
        self.tweaks = dict(
            tweaks
            or {
                "recLimit": False,
                "recLimit4k": False,
                "language": False,
                "palNtscSelector": False,
            }
        )
        self.senser_calls = 0
        self.applied_tweaks: dict[str, bool] = {}

    def debugger_session(self):
        class _Ctx:
            def __init__(self, dbg):
                self.dbg = dbg

            def __enter__(self):
                if self.dbg is None:
                    raise RuntimeError("Debugger unreachable")
                return self.dbg

            def __exit__(self, *_args):
                return False

        return _Ctx(self.debugger)

    def detect_msc_identity(self) -> dict[str, str]:
        if self.msc_identity is None:
            raise RuntimeError("No Sony camera found in Mass Storage mode")
        return dict(self.msc_identity)

    def run_senser(self, callback):
        self.senser_calls += 1
        minimal_arm_elf = struct.pack(
            "<16sHHIIIIIHHHHHH",
            b"\x7fELF\x01\x01\x01" + b"\x00" * 9,
            1, 40, 1, 0, 0, 0, 0, 52, 0, 0, 40, 0, 0,
        )
        fake_cam = mock.MagicMock()
        fake_cam.readFile.side_effect = (
            lambda p: b"PRELOAD" if p == "/etc/ld.so.preload" else minimal_arm_elf
        )
        callback(fake_cam)

    def query_tweaks(self, cam):
        del cam
        return [
            {"id": k, "description": k, "enabled": v, "value": "1" if v else "0", "available": True}
            for k, v in self.tweaks.items()
        ]

    def apply_tweaks(self, cam, desired: dict[str, bool]):
        del cam
        self.tweaks.update(desired)
        self.applied_tweaks.update(desired)
        return [{"id": k, "enabled": bool(v), "value": "1" if v else "0"} for k, v in desired.items()]


class TestSmodParser(unittest.TestCase):
    def test_parses_all_shipped_mods(self):
        shipped = [
            PROJECT_ROOT / "mods/menu_touch_full/MenuTouchFull.mod",
            PROJECT_ROOT / "mods/arri_isp/arrivederci/Arrivederci.mod",
        ]
        for path in shipped:
            info = parse_smod(path.read_bytes(), filename=path.name)
            self.assertIn(info.version, (1, 2))
            self.assertGreater(info.code_size, 0)
            self.assertEqual(len(info.sha256), 64)

    def test_rejects_corrupt_and_unsafe_inputs(self):
        good = _make_smod_v2("Good Mod")
        self.assertEqual(parse_smod(good, filename="GoodMod.mod").name, "Good Mod")

        with self.assertRaises(SmodError):
            parse_smod(b"BAD!" + good[4:])
        with self.assertRaises(SmodError):
            parse_smod(good[:-1] + bytes([good[-1] ^ 0xFF]))
        with self.assertRaises(SmodError):
            validate_mod_filename("../evil.mod")
        with self.assertRaises(SmodError):
            validate_mod_filename(".hidden.mod")
        with self.assertRaises(SmodError):
            validate_mod_filename("A" * 45 + ".mod")


class TestCatalog(unittest.TestCase):
    def test_builtin_catalog_and_custom_import_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            cat = build_catalog(root=PROJECT_ROOT, data_dir=data_dir)
            mods_by_id = {m["id"]: m for m in cat["runtime_mods"]}
            self.assertTrue(mods_by_id["menu_touch_full"]["available"])
            self.assertTrue(mods_by_id["arrivederci"]["available"])
            self.assertIn("Backup.bin", mods_by_id["arrivederci"]["warning"])
            self.assertEqual(mods_by_id["open_gate_sensor"]["exclusive_group"], "open_gate")
            self.assertEqual(mods_by_id["open_gate_32"]["exclusive_group"], "open_gate")
            self.assertEqual(len(cat["pmca_tweaks"]), 4)

            custom_bytes = _make_smod_v2("Custom Overlay")
            added = import_custom_mod_bytes("CustomOverlay.mod", custom_bytes, data_dir)
            self.assertEqual(added["id"], "custom:CustomOverlay.mod")

            cat2 = build_catalog(root=PROJECT_ROOT, data_dir=data_dir)
            self.assertIn("custom:CustomOverlay.mod", {m["id"] for m in cat2["runtime_mods"]})

            with self.assertRaises(SmodError):
                import_custom_mod_bytes("MenuTouchFull.mod", custom_bytes, data_dir)

            self.assertTrue(remove_custom_mod("custom:CustomOverlay.mod", data_dir))
            self.assertFalse(remove_custom_mod("custom:CustomOverlay.mod", data_dir))


class TestToolsBundle(unittest.TestCase):
    def test_validate_and_resolve_mlv2_and_frozen_pmca(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            good_dir = tmp_path / "artifacts" / "tests" / "mlv2-good"
            bad_dir = tmp_path / "artifacts" / "tests" / "mlv2-bad"
            _create_fake_mlv2_dir(good_dir)
            _create_fake_mlv2_dir(bad_dir, corrupt=True)

            verified = validate_mlv2_build(good_dir)
            self.assertTrue(verified["reload_capable"])
            self.assertEqual(len(verified["entry_ints"]), 3)

            with self.assertRaises(ValueError):
                validate_mlv2_build(bad_dir)

            resolved = resolve_mlv2_build(root=tmp_path, data_dir=tmp_path / "data")
            self.assertTrue(resolved["available"])
            self.assertEqual(Path(resolved["path"]), good_dir.resolve())

            bundle_dir = tmp_path / "meipass"
            (bundle_dir / "pmca" / "usb").mkdir(parents=True)
            (bundle_dir / "pmca" / "usb" / "sony.py").write_text("# stub\n", encoding="utf-8")
            # Incomplete PMCA tree missing root config.py must be rejected.
            self.assertIsNone(find_pmca_dir(root=tmp_path, bundle_dir=bundle_dir))
            (bundle_dir / "config.py").write_text("githubAppListUser = 'ma1co'\n", encoding="utf-8")
            self.assertEqual(find_pmca_dir(root=tmp_path, bundle_dir=bundle_dir), bundle_dir.resolve())

    def test_ensure_pmca_on_path_extracts_config_pmca_and_certs(self):
        import io
        import zipfile
        from tools.setup_usb_debugger import ensure_pmca_on_path

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Sony-PMCA-RE-master/config.py", "githubAppListUser = 'ma1co'\n")
            zf.writestr("Sony-PMCA-RE-master/pmca/__init__.py", "")
            zf.writestr("Sony-PMCA-RE-master/pmca/usb/sony.py", "# stub\n")
            zf.writestr("Sony-PMCA-RE-master/certs/localtest.me.pem", "CERT")
        archive = buf.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "repo"
            fake_root.mkdir()
            with mock.patch("tools.setup_usb_debugger.sys.path", []):
                extracted = ensure_pmca_on_path(root=fake_root, fetcher=lambda _url: archive)
            self.assertTrue((extracted / "config.py").is_file())
            self.assertTrue((extracted / "pmca" / "usb" / "sony.py").is_file())
            self.assertTrue((extracted / "certs" / "localtest.me.pem").is_file())

    def test_ensure_mlv2_release_and_arrivederci_v5_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stage_dir = tmp_path / "stage"
            _create_fake_mlv2_dir(stage_dir)
            (stage_dir / "shim.bin").write_bytes(b"SHIM")
            fake_files = {
                name: (stage_dir / name).read_bytes()
                for name in ("build.json", "core.bin", "mlv2.req", "mlv2_guard_arm", "shim.bin")
            }
            fake_release_assets = {
                name: {
                    "size": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "url": f"https://example.invalid/{name}",
                }
                for name, blob in fake_files.items()
            }
            arri_blob = _make_smod_v2("Arrivederci")
            fake_v5_asset = {
                "filename": "Arrivederci.mod",
                "version": "v5",
                "release_tag": tools_bundle.MLV2_RELEASE_TAG,
                "size": len(arri_blob),
                "sha256": hashlib.sha256(arri_blob).hexdigest(),
                "url": "https://example.invalid/Arrivederci.mod",
            }
            url_map = {meta["url"]: fake_files[name] for name, meta in fake_release_assets.items()}
            url_map[fake_v5_asset["url"]] = arri_blob

            with mock.patch.dict(tools_bundle.MLV2_RELEASE_ASSETS, fake_release_assets, clear=True), \
                 mock.patch.dict(tools_bundle.ARRIVEDERCI_V5_ASSET, fake_v5_asset, clear=True):
                mlv2 = ensure_mlv2_release(
                    root=tmp_path,
                    data_dir=tmp_path / "data",
                    fetcher=lambda url: url_map[url],
                )
                self.assertTrue(mlv2["available"])
                self.assertTrue(mlv2["is_official_release"])
                self.assertEqual(mlv2["release_tag"], tools_bundle.MLV2_RELEASE_TAG)

                # Local cache release takes priority over older artifacts builds.
                resolved = resolve_mlv2_build(root=tmp_path, data_dir=tmp_path / "data")
                self.assertEqual(resolved["source"], "release")

                v5_path = ensure_arrivederci_v5(
                    root=tmp_path,
                    data_dir=tmp_path / "data",
                    fetcher=lambda url: url_map[url],
                )
                self.assertTrue(v5_path.is_file())
                cat = build_catalog(root=tmp_path, data_dir=tmp_path / "data")
                arri_entry = next(m for m in cat["runtime_mods"] if m["id"] == "arrivederci")
                self.assertEqual(arri_entry["release_version"], "v5")
                self.assertEqual(arri_entry["status"], "verified")

                # Tampered download must fail closed.
                with self.assertRaises(RuntimeError):
                    ensure_mlv2_release(
                        root=tmp_path / "tamper",
                        data_dir=tmp_path / "tamper_data",
                        fetcher=lambda _url: b"CORRUPT",
                    )
                with self.assertRaises(RuntimeError):
                    ensure_arrivederci_v5(
                        root=tmp_path / "tamper",
                        data_dir=tmp_path / "tamper_data",
                        fetcher=lambda _url: b"CORRUPT",
                    )

    def test_resolve_mlv2_build_prefers_fw200_safe_shim_over_older_release_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_rel = tmp_path / ".cache" / tools_bundle.MLV2_RELEASE_TAG
            _create_fake_mlv2_dir(old_rel)
            rel_verified = validate_mlv2_build(old_rel)
            self.assertEqual(rel_verified["shim_addr"], 0x05662760)

            safe_art = tmp_path / "artifacts" / "tests" / "mlv2-cave2800"
            _create_fake_mlv2_dir(safe_art)
            # Rewrite mlv2.req + build.json in safe_art with shim_addr = 0x05662800
            entries = (0x05662B15, 0x05662B65, 0x05662B75)
            req = struct.pack("<4I3I2I", 0x5132484D, 2, 0x05662800, 1028, *entries, 0x12345678, 0)
            (safe_art / "mlv2.req").write_bytes(req)
            info = json.loads((safe_art / "build.json").read_text(encoding="utf-8"))
            info["request"]["sha256"] = hashlib.sha256(req).hexdigest()
            (safe_art / "build.json").write_text(json.dumps(info), encoding="utf-8")

            resolved = resolve_mlv2_build(root=tmp_path, data_dir=tmp_path / "data")
            self.assertTrue(resolved["available"])
            self.assertEqual(resolved["source"], "artifacts")
            self.assertEqual(resolved["shim_addr"], 0x05662800)


class TestCameraService(unittest.TestCase):
    def test_debugger_ready_install_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mlv2_dir = tmp_path / "mlv2"
            meta = _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)

            dbg = _FakeDebugger(hook_state="stock", mlv2_entries=meta["entries"])
            dbg.files["/tmp_bt/mods/OpenGateSensor.mod"] = b"OLD_STAGED"
            transport = _FakeTransport(
                debugger=dbg,
                msc_identity={"model_name": "ILCE-6400", "model_code": "1", "firmware_version": "2.00"},
            )
            svc = CameraService(root=PROJECT_ROOT, data_dir=tmp_path / "data", transport=transport)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)

            cam = svc.detect_camera(mlv2_status=mlv2_status)
            self.assertEqual(cam["state"], "debugger_ready")

            plan = svc.plan_install(
                {"runtime_mods": {"menu_touch_full": True, "open_gate_sensor": False}, "pmca_tweaks": {}},
                cat,
                cam,
                mlv2_status,
            )
            self.assertEqual([m["filename"] for m in plan["mods_to_add"]], ["MenuTouchFull.mod"])
            self.assertEqual([m["filename"] for m in plan["mods_to_remove"]], ["OpenGateSensor.mod"])
            self.assertEqual(transport.senser_calls, 0)

            receipt = svc.execute_install(plan, cat, mlv2_status, confirmed_permanent=False)
            self.assertEqual(receipt["status"], "success")
            self.assertIn("/tmp_bt/mods/MenuTouchFull.mod", dbg.files)
            self.assertNotIn("/tmp_bt/mods/OpenGateSensor.mod", dbg.files)
            self.assertEqual(dbg.hook_state, "v2")
            self.assertEqual(transport.senser_calls, 0)
            self.assertTrue(Path(receipt["receipt_path"]).is_file())

    def test_a6400a_blocked_and_unknown_model_onboarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)
            mlv2_dir = tmp_path / "mlv2"
            _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)

            blocked_svc = CameraService(
                root=PROJECT_ROOT,
                data_dir=tmp_path / "data",
                transport=_FakeTransport(msc_identity={"model_name": "ILCE-6400A", "model_code": "2", "firmware_version": "1.01"}),
            )
            cam_a = blocked_svc.detect_camera(mlv2_status=mlv2_status)
            self.assertEqual(cam_a["state"], "msc_blocked")
            plan_a = blocked_svc.plan_install({"runtime_mods": {"menu_touch_full": True}, "pmca_tweaks": {}}, cat, cam_a, mlv2_status)
            self.assertTrue(plan_a["blocking_errors"])

            unk_svc = CameraService(
                root=PROJECT_ROOT,
                data_dir=tmp_path / "data",
                transport=_FakeTransport(msc_identity={"model_name": "ILCE-9M2", "model_code": "9", "firmware_version": "3.00"}),
            )
            cam_u = unk_svc.detect_camera(mlv2_status=mlv2_status)
            self.assertEqual(cam_u["state"], "msc_unsupported")
            bundle_res = unk_svc.collect_onboarding_bundle()
            self.assertTrue(Path(bundle_res["bundle_path"]).is_file())

    def test_fresh_msc_bootstrap_and_pmca_tweaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mlv2_dir = tmp_path / "mlv2"
            meta = _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)

            dbg = _FakeDebugger(hook_state="stock", mlv2_entries=meta["entries"])
            transport = _FakeTransport(
                debugger=None,
                msc_identity={"model_name": "ILCE-6400", "model_code": "1", "firmware_version": "2.00"},
            )
            svc = CameraService(root=PROJECT_ROOT, data_dir=tmp_path / "data", transport=transport)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)

            cam = svc.detect_camera(mlv2_status=mlv2_status)
            self.assertEqual(cam["state"], "msc_verified")

            plan = svc.plan_install(
                {"runtime_mods": {"menu_touch_full": True}, "pmca_tweaks": {"recLimit": True}},
                cat,
                cam,
                mlv2_status,
                known_tweaks={"recLimit": False},
            )
            self.assertTrue(plan["requires_confirmation"])
            self.assertTrue(plan["needs_debugger_bootstrap"])

            with self.assertRaises(RuntimeError):
                svc.execute_install(plan, cat, mlv2_status, confirmed_permanent=False)

            # Simulate supported driver compat + upload + power-cycle bringing debugger online.
            payload_dir = tmp_path / "payloads"
            for rel in ("devcrashday_a6400/a6400_usb_debugd.elf", "devcrashday_a6400/hook/usb_debug_launcher.so"):
                p = payload_dir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"ELF")

            def _after_power_cycle(probe_fn, _cancel):
                transport.debugger = dbg
                return probe_fn()

            with mock.patch("src.a6400.mod_manager.camera_service.resolve_debugger_payload_root", return_value=payload_dir), \
                 mock.patch("src.a6400.mod_manager.camera_service.verify_camera_debugger_compatibility", return_value={"compatible": True}), \
                 mock.patch("src.a6400.mod_manager.camera_service.upload_and_verify_debugger"):
                receipt = svc.execute_install(
                    plan,
                    cat,
                    mlv2_status,
                    confirmed_permanent=True,
                    wait_for_power_cycle_cb=_after_power_cycle,
                )

            self.assertEqual(receipt["status"], "success")
            self.assertTrue(transport.applied_tweaks["recLimit"])
            self.assertIn("/tmp_bt/mods/MenuTouchFull.mod", dbg.files)

    def test_fail_closed_wrong_pid_and_exclusive_open_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mlv2_dir = tmp_path / "mlv2"
            meta = _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)

            dbg = _FakeDebugger(hook_state="unknown", comm="ash", mlv2_entries=meta["entries"])
            svc = CameraService(
                root=PROJECT_ROOT,
                data_dir=tmp_path / "data",
                transport=_FakeTransport(
                    debugger=dbg,
                    msc_identity={"model_name": "ILCE-6400", "model_code": "1", "firmware_version": "2.00"},
                ),
            )
            cam = svc.detect_camera(mlv2_status=mlv2_status)

            excl_plan = svc.plan_install(
                {"runtime_mods": {"open_gate_sensor": True, "open_gate_32": True}, "pmca_tweaks": {}},
                cat,
                cam,
                mlv2_status,
            )
            self.assertTrue(any("Mutually exclusive" in e for e in excl_plan["blocking_errors"]))

            ok_plan = svc.plan_install(
                {"runtime_mods": {"menu_touch_full": True}, "pmca_tweaks": {}},
                cat,
                cam,
                mlv2_status,
            )
            with self.assertRaises(RuntimeError):
                svc.execute_install(ok_plan, cat, mlv2_status, confirmed_permanent=False)
            self.assertEqual(len(dbg.files), 0)

            too_many = {f"m{i}": True for i in range(MAX_ACTIVE_RUNTIME_MODS + 1)}
            cap_plan = svc.plan_install({"runtime_mods": too_many, "pmca_tweaks": {}}, cat, cam, mlv2_status)
            self.assertTrue(cap_plan["blocking_errors"])

    def test_bootstrap_onboarding_includes_reason_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mlv2_dir = tmp_path / "mlv2"
            _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)
            transport = _FakeTransport(
                debugger=None,
                msc_identity={"model_name": "ILCE-6400", "model_code": "1", "firmware_version": "2.00"},
            )
            svc = CameraService(root=PROJECT_ROOT, data_dir=tmp_path / "data", transport=transport)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)
            cam = svc.detect_camera(mlv2_status=mlv2_status)
            plan = svc.plan_install(
                {"runtime_mods": {"menu_touch_full": True}, "pmca_tweaks": {}},
                cat,
                cam,
                mlv2_status,
            )
            payload_dir = tmp_path / "payloads"
            for rel in ("devcrashday_a6400/a6400_usb_debugd.elf", "devcrashday_a6400/hook/usb_debug_launcher.so"):
                p = payload_dir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"ELF")

            events: list[dict[str, object]] = []
            with mock.patch("src.a6400.mod_manager.camera_service.resolve_debugger_payload_root", return_value=payload_dir), \
                 mock.patch(
                     "src.a6400.mod_manager.camera_service.verify_camera_debugger_compatibility",
                     return_value={"compatible": False, "reason": "libusbcmd.so mismatch (fake test)"},
                 ):
                receipt = svc.execute_install(
                    plan,
                    cat,
                    mlv2_status,
                    confirmed_permanent=True,
                    progress_cb=events.append,
                )

            self.assertEqual(receipt["status"], "onboarding_collected")
            self.assertEqual(receipt["bootstrap"]["reason"], "libusbcmd.so mismatch (fake test)")
            self.assertTrue(Path(receipt["bootstrap"]["bundle_path"]).is_file())
            self.assertTrue(any(e["step"] == "bootstrap" and e["status"] == "error" for e in events))

    def test_v1_debugger_without_ping_and_sense_hint_complete_bootstrap_and_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mlv2_dir = tmp_path / "mlv2"
            meta = _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)

            dbg_v1 = _FakeDebugger(hook_state="stock", mlv2_entries=meta["entries"], supports_ping=False)
            transport = _FakeTransport(
                debugger=None,
                msc_identity={"model_name": "ILCE-6400", "model_code": "1", "firmware_version": "2.00"},
            )

            class _SenseErr(Exception):
                pass

            probe_calls = {"n": 0}

            def _flaky_session():
                probe_calls["n"] += 1
                if probe_calls["n"] == 1:
                    raise _SenseErr("Mass storage error: Sense 0x5 0x20 0x0")
                transport.debugger = dbg_v1
                return _FakeTransport.debugger_session(transport)

            svc = CameraService(root=PROJECT_ROOT, data_dir=tmp_path / "data", transport=transport)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)
            cam = svc.detect_camera(mlv2_status=mlv2_status)
            plan = svc.plan_install(
                {"runtime_mods": {"menu_touch_full": True}, "pmca_tweaks": {}},
                cat,
                cam,
                mlv2_status,
            )

            payload_dir = tmp_path / "payloads"
            for rel in ("devcrashday_a6400/a6400_usb_debugd.elf", "devcrashday_a6400/hook/usb_debug_launcher.so"):
                p = payload_dir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"ELF")

            events: list[dict[str, object]] = []

            def _wait_two_polls(probe_fn, _cancel):
                self.assertFalse(probe_fn())  # 1st poll gets Sense 0x5 0x20 0x0 -> logs hint
                self.assertTrue(probe_fn())   # 2nd poll sees v1 OP_INFO without OP_PING
                return True

            with mock.patch("src.a6400.mod_manager.camera_service.resolve_debugger_payload_root", return_value=payload_dir), \
                 mock.patch("src.a6400.mod_manager.camera_service.verify_camera_debugger_compatibility", return_value={"compatible": True}), \
                 mock.patch("src.a6400.mod_manager.camera_service.upload_and_verify_debugger"), \
                 mock.patch.object(transport, "debugger_session", side_effect=_flaky_session):
                receipt = svc.execute_install(
                    plan,
                    cat,
                    mlv2_status,
                    confirmed_permanent=True,
                    progress_cb=events.append,
                    wait_for_power_cycle_cb=_wait_two_polls,
                )

            self.assertEqual(receipt["status"], "success")
            self.assertIn("pid=1793", receipt["mlv2_sync"]["ping"])
            self.assertTrue(
                any("Enable Bluetooth (Airplane Mode OFF)" in str(e["message"]) for e in events)
            )

    def test_relocated_im_pid_and_library_bases_sync_and_detect(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mlv2_dir = tmp_path / "mlv2"
            meta = _create_fake_mlv2_dir(mlv2_dir)
            mlv2_status = validate_mlv2_build(mlv2_dir)
            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)

            dbg = _FakeDebugger(
                hook_state="stock",
                im_pid=152,
                view_base=0x15585000,
                testcmd_base=0x07E18000,
                extra_procs=[(149, "uipc_proxy_1")],
                mlv2_entries=meta["entries"],
                supports_ping=False,
            )
            svc = CameraService(
                root=PROJECT_ROOT,
                data_dir=tmp_path / "data",
                transport=_FakeTransport(
                    debugger=dbg,
                    msc_identity={"model_name": "ILCE-6400", "model_code": "1", "firmware_version": "2.00"},
                ),
            )
            cam_before = svc.detect_camera(mlv2_status=mlv2_status)
            self.assertEqual(cam_before["state"], "debugger_ready")
            self.assertEqual(cam_before["im_pid"], 152)
            self.assertEqual(cam_before["im_comm"], "im.elf")

            plan = svc.plan_install(
                {"runtime_mods": {"menu_touch_full": True}, "pmca_tweaks": {}},
                cat,
                cam_before,
                mlv2_status,
            )
            receipt = svc.execute_install(plan, cat, mlv2_status, confirmed_permanent=True)
            self.assertEqual(receipt["status"], "success")
            self.assertEqual(receipt["mlv2_sync"]["im_pid"], 152)
            self.assertTrue(receipt["mlv2_sync"]["hooks_point_to_v2"])

            cam_after = svc.detect_camera(mlv2_status=mlv2_status)
            self.assertEqual(cam_after["mlv2_hook_state"], "v2")

            # Fail closed on 0 or >1 im.elf processes
            dbg.procs = [(149, "uipc_proxy_1")]
            with self.assertRaises(RuntimeError):
                svc._find_im_pid(dbg)
            dbg.procs = [(152, "im.elf"), (252, "im.elf")]
            with self.assertRaises(RuntimeError):
                svc._find_im_pid(dbg)


class TestCameraDebuggerPush(unittest.TestCase):
    def test_push_uses_busybox_echo_ne_across_all_byte_values_and_chunks(self):
        import re
        import shlex
        from tools.camera_debugger import Debugger, FRAME_SIZE, HEADER, MAX_DATA, OP_EXEC, OP_READ, encode

        fs: dict[str, bytes] = {}
        seen_cmds: list[str] = []

        def fake_exchange(req_frame: bytes) -> bytes:
            _magic, _ver, op, req_id, _st, length, _fl, arg0, arg1 = HEADER.unpack_from(req_frame)
            payload = req_frame[HEADER.size : HEADER.size + length]
            reply = b""
            if op == OP_EXEC:
                cmd = payload.decode("utf-8")
                seen_cmds.append(cmd)
                self.assertNotIn("printf", cmd)
                self.assertLessEqual(len(payload), MAX_DATA)
                parts = shlex.split(cmd)
                self.assertEqual(parts[:2], ["echo", "-ne"])
                escaped, redir, dst = parts[2], parts[3], parts[4]
                raw = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), escaped).encode("latin1")
                if redir == ">":
                    fs[dst] = raw
                elif redir == ">>":
                    fs[dst] = fs.get(dst, b"") + raw
                else:
                    raise AssertionError(f"Unexpected redirect {redir}")
            elif op == OP_READ:
                path = payload.decode("utf-8")
                reply = fs.get(path, b"")[arg0 : arg0 + arg1]
            return (HEADER.pack(0x44343641, 1, op, req_id, 0, len(reply), 0, 0, 0) + reply).ljust(FRAME_SIZE, b"\0")

        dbg = Debugger(fake_exchange)
        for sample in (b"", bytes(range(256)), bytes(range(256)) * 5 + b"\x00\xffXYZ"):
            seen_cmds.clear()
            res = dbg.push("/tmp_bt/test_push.bin", sample)
            self.assertTrue(res["readback_verified"])
            self.assertEqual(fs["/tmp_bt/test_push.bin"], sample)
            self.assertGreaterEqual(len(seen_cmds), 1)


class TestSdSync(unittest.TestCase):
    def test_sd_sync_adds_verifies_removes_and_preserves_dcim(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sd_root = tmp_path / "SD_E"
            (sd_root / "DCIM" / "100MSDCF").mkdir(parents=True)
            (sd_root / "DCIM" / "100MSDCF" / "DSC00001.JPG").write_bytes(b"PHOTO")
            (sd_root / "mods").mkdir()
            (sd_root / "mods" / "Arrivederci.mod").write_bytes(b"OLD")

            drives = list_sd_drives(
                drive_provider=lambda: [
                    {"root": str(sd_root), "letter": "E", "label": "SONY", "fs_type": "FAT32", "total_mb": 32000, "free_mb": 16000}
                ]
            )
            self.assertTrue(drives[0]["looks_like_sony_sd"])

            cat = build_catalog(root=PROJECT_ROOT, data_dir=tmp_path / "data")
            for mod in cat["runtime_mods"]:
                if mod["id"].startswith("open_gate_"):
                    fake_mod = tmp_path / mod["filename"]
                    fake_mod.write_bytes(_make_smod_v2(mod["title"]))
                    mod["available"] = True
                    mod["path"] = str(fake_mod)
            mtf = next(m for m in cat["runtime_mods"] if m["id"] == "menu_touch_full")
            plan = plan_sd_sync(sd_root, [mtf], {m["filename"] for m in cat["runtime_mods"]})
            self.assertEqual(len(plan["to_add"]), 1)
            self.assertEqual(len(plan["to_remove"]), 1)

            receipt = execute_sd_sync(sd_root, plan, data_dir=tmp_path / "data")
            self.assertTrue((sd_root / "mods" / "MenuTouchFull.mod").is_file())
            self.assertFalse((sd_root / "mods" / "Arrivederci.mod").exists())
            self.assertTrue((sd_root / "mods" / "SHA256SUMS.txt").is_file())
            self.assertEqual((sd_root / "DCIM" / "100MSDCF" / "DSC00001.JPG").read_bytes(), b"PHOTO")
            self.assertTrue(Path(receipt["receipt_path"]).is_file())


class TestGuiBridgeAndWebServer(unittest.TestCase):
    def test_api_bridge_and_authenticated_web_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            svc = CameraService(
                root=PROJECT_ROOT,
                data_dir=tmp_path / "data",
                transport=_FakeTransport(),
            )
            api = ModManagerApi(root=PROJECT_ROOT, data_dir=tmp_path / "data", service=svc, drive_provider=lambda: [])
            init = api.get_initial_state()
            self.assertIn("catalog", init)
            self.assertIn("toolchain", init)

            b64 = base64.b64encode(_make_smod_v2("Bridge Mod")).decode("ascii")
            imported = api.import_custom_mod_bytes("BridgeMod.mod", b64)
            self.assertEqual(imported["added"]["filename"], "BridgeMod.mod")

            server, token = create_web_server(api)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode("utf-8")
                self.assertIn(token, html)

                bad_req = urllib.request.Request(f"http://127.0.0.1:{port}/api/get_initial_state", data=b"{}", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(bad_req)
                self.assertEqual(ctx.exception.code, 403)

                good_req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/get_initial_state",
                    data=b"{}",
                    method="POST",
                    headers={"X-ModManager-Token": token, "Content-Type": "application/json"},
                )
                resp = json.loads(urllib.request.urlopen(good_req).read().decode("utf-8"))
                self.assertTrue(resp["ok"])
                self.assertIn("runtime_mods", resp["data"]["catalog"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
