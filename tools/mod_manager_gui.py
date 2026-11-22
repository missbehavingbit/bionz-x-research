"""Windows native WebView2 GUI and local web fallback for the BIONZ X Mod Manager."""
from __future__ import annotations

import argparse
import base64
from collections import deque
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
from typing import Any
from urllib.parse import urlparse
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.a6400.mod_manager.camera_service import CameraService
from src.a6400.mod_manager.catalog import (
    build_catalog,
    custom_mods_dir,
    import_custom_mod,
    import_custom_mod_bytes,
    remove_custom_mod,
)
from src.a6400.mod_manager.sd_sync import execute_sd_sync, list_sd_drives, plan_sd_sync
from src.a6400.mod_manager.smod import SMOD_MAX_TOTAL_BYTES
from src.a6400.mod_manager.tools_bundle import (
    bundle_root,
    get_toolchain_status,
    is_frozen,
    load_settings,
    prepare_tools,
    repo_root,
    save_settings,
    user_data_dir,
    validate_mlv2_build,
)

UI_DIR = bundle_root() / "tools" / "mod_manager_ui"


def _apply_windows_dark_titlebar() -> None:
    """Ask Windows 10/11 DWM to paint our WebView2 window titlebar in dark mode."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = user32.GetActiveWindow()
        if not hwnd:
            return
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMUTABLE_DARK_MODE (Win11 / Win10 1903+)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(attr),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
    except Exception:
        pass


class ModManagerApi:
    """JS-to-Python bridge exposed to WebView2 and the local HTTP fallback server."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        data_dir: Path | None = None,
        service: CameraService | None = None,
        drive_provider: Any = None,
    ):
        self.root = repo_root(root)
        self.data_dir = user_data_dir(data_dir, root=self.root)
        self.service = service or CameraService(root=self.root, data_dir=self.data_dir)
        self.drive_provider = drive_provider
        self._window: Any = None

        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=400)
        self._seq = 0
        self._job_active = False
        self._job_kind: str | None = None
        self._cancel_event: threading.Event | None = None
        self._last_camera: dict[str, Any] = {
            "connected": False,
            "state": "offline",
            "transport": "none",
            "debugger_active": False,
            "model_name": None,
            "firmware_version": None,
            "mlv2_hook_state": "none",
            "staged_mods": [],
            "detail": "Click 'Scan Camera' after connecting your Sony camera via USB.",
        }

    def attach_window(self, window: Any) -> None:
        self._window = window

    def _emit_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            self._events.append(
                {
                    "seq": self._seq,
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "kind": kind,
                    **payload,
                }
            )

    def poll_events(self, after_seq: int = 0) -> dict[str, Any]:
        """Return buffered worker events newer than ``after_seq`` plus active job state."""
        with self._lock:
            events = [e for e in self._events if e["seq"] > int(after_seq)]
            return {
                "events": events,
                "last_seq": self._seq,
                "job_active": self._job_active,
                "job_kind": self._job_kind,
            }

    def _toolchain(self) -> dict[str, Any]:
        return get_toolchain_status(root=self.root, data_dir=self.data_dir)

    def _catalog(self) -> dict[str, Any]:
        return build_catalog(root=self.root, data_dir=self.data_dir)

    def get_initial_state(self) -> dict[str, Any]:
        return {
            "catalog": self._catalog(),
            "toolchain": self._toolchain(),
            "camera": self._last_camera,
            "settings": load_settings(self.data_dir),
            "receipts": self.service.list_receipts(),
            "platform": {"os": os.name, "frozen": is_frozen()},
        }

    def scan_camera(self) -> dict[str, Any]:
        toolchain = self._toolchain()
        snapshot = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
        self._last_camera = snapshot
        return {"camera": snapshot, "toolchain": toolchain}

    def read_live_tweaks(self) -> dict[str, Any]:
        return self.service.read_live_tweaks()

    def import_custom_mod_bytes(self, filename: str, base64_data: str) -> dict[str, Any]:
        if len(base64_data) > (SMOD_MAX_TOTAL_BYTES * 2):
            raise ValueError("Encoded .mod file exceeds maximum 1 MiB loader size")
        raw = base64.b64decode(base64_data, validate=True)
        added = import_custom_mod_bytes(filename, raw, self.data_dir)
        return {"added": added, "catalog": self._catalog()}

    def import_custom_mod_dialog(self) -> dict[str, Any]:
        chosen = self._pick_file_dialog("Select Sony .mod binary", ("Sony Mod Files (*.mod)",))
        if not chosen:
            return {"cancelled": True}
        added = import_custom_mod(Path(chosen), self.data_dir)
        return {"cancelled": False, "added": added, "catalog": self._catalog()}

    def remove_custom_mod(self, mod_id: str) -> dict[str, Any]:
        removed = remove_custom_mod(mod_id, self.data_dir)
        return {"removed": removed, "catalog": self._catalog()}

    def preview_install(
        self,
        selection: dict[str, Any],
        known_tweaks: dict[str, bool] | None = None,
        rescan: bool = False,
    ) -> dict[str, Any]:
        toolchain = self._toolchain()
        catalog = self._catalog()
        camera = self._last_camera
        if rescan or not camera.get("connected"):
            camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
            self._last_camera = camera
        plan = self.service.plan_install(
            selection,
            catalog,
            camera,
            toolchain["mlv2"],
            known_tweaks=known_tweaks,
        )
        return {"plan": plan, "camera": camera, "toolchain": toolchain}

    def _start_worker(self, kind: str, target_fn: Any) -> dict[str, Any]:
        with self._lock:
            if self._job_active:
                raise RuntimeError(f"Another operation ({self._job_kind}) is already running")
            self._job_active = True
            self._job_kind = kind
            self._cancel_event = threading.Event()
            cancel_event = self._cancel_event

        def _runner() -> None:
            try:
                target_fn(cancel_event)
            except Exception as exc:
                self._emit_event("job_error", {"job": kind, "error": str(exc)})
            finally:
                with self._lock:
                    self._job_active = False
                    self._job_kind = None
                    self._cancel_event = None

        thread = threading.Thread(target=_runner, name=f"modmgr-{kind}", daemon=True)
        thread.start()
        return {"started": True, "job": kind}

    def start_install(
        self,
        selection: dict[str, Any],
        confirmed_permanent: bool = False,
        known_tweaks: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Launch the 1-Click install pipeline on a background thread and stream progress events."""

        def _run(cancel_event: threading.Event) -> None:
            self._emit_event(
                "progress",
                {"step": "scan", "status": "running", "message": "Scanning camera and verifying tools..."},
            )
            toolchain = self._toolchain()
            catalog = self._catalog()
            camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
            self._last_camera = camera
            plan = self.service.plan_install(
                selection,
                catalog,
                camera,
                toolchain["mlv2"],
                known_tweaks=known_tweaks,
            )
            if plan["blocking_errors"]:
                raise RuntimeError("; ".join(plan["blocking_errors"]))
            if not plan["has_changes"]:
                self._emit_event(
                    "job_done",
                    {
                        "job": "install",
                        "result": {"status": "no_changes", "message": "Camera already matches selected toggles."},
                        "camera": camera,
                        "receipts": self.service.list_receipts(),
                    },
                )
                return

            receipt = self.service.execute_install(
                plan,
                catalog,
                toolchain["mlv2"],
                confirmed_permanent=bool(confirmed_permanent),
                progress_cb=lambda ev: self._emit_event("progress", ev),
                cancel_event=cancel_event,
            )
            refreshed_camera = self.service.detect_camera(mlv2_status=toolchain["mlv2"])
            self._last_camera = refreshed_camera
            self._emit_event(
                "job_done",
                {
                    "job": "install",
                    "result": receipt,
                    "camera": refreshed_camera,
                    "receipts": self.service.list_receipts(),
                },
            )

        return self._start_worker("install", _run)

    def cancel_active_job(self) -> dict[str, Any]:
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
                return {"cancelling": True}
            return {"cancelling": False}

    def list_sd_drives(self) -> dict[str, Any]:
        return {"drives": list_sd_drives(drive_provider=self.drive_provider)}

    def _selected_mod_dicts(self, runtime_selection: dict[str, bool]) -> tuple[list[dict[str, Any]], set[str]]:
        catalog = self._catalog()
        desired: list[dict[str, Any]] = []
        managed: set[str] = set()
        for mod in catalog["runtime_mods"]:
            managed.add(str(mod["filename"]))
            if runtime_selection.get(str(mod["id"])) and mod.get("available"):
                desired.append(mod)
        return desired, managed

    def preview_sd_sync(self, drive_root: str, runtime_selection: dict[str, bool]) -> dict[str, Any]:
        desired, managed = self._selected_mod_dicts(runtime_selection or {})
        plan = plan_sd_sync(drive_root, desired, managed)
        return {"plan": plan}

    def start_sd_sync(self, drive_root: str, runtime_selection: dict[str, bool]) -> dict[str, Any]:
        desired, managed = self._selected_mod_dicts(runtime_selection or {})
        plan = plan_sd_sync(drive_root, desired, managed)
        receipt = execute_sd_sync(drive_root, plan, data_dir=self.data_dir)
        return {
            "receipt": receipt,
            "drives": list_sd_drives(drive_provider=self.drive_provider),
            "receipts": self.service.list_receipts(),
        }

    def prepare_tools(self) -> dict[str, Any]:
        def _run(_cancel: threading.Event) -> None:
            status = prepare_tools(
                root=self.root,
                data_dir=self.data_dir,
                progress_cb=lambda msg: self._emit_event(
                    "progress", {"step": "tools", "status": "running", "message": msg}
                ),
            )
            self._emit_event(
                "job_done",
                {"job": "prepare_tools", "toolchain": status, "catalog": self._catalog()},
            )

        return self._start_worker("prepare_tools", _run)

    def set_mlv2_folder(self, folder_path: str) -> dict[str, Any]:
        validate_mlv2_build(Path(folder_path))
        save_settings(self.data_dir, {"mlv2_dir": str(Path(folder_path).resolve())})
        return {"toolchain": self._toolchain()}

    def pick_mlv2_folder(self) -> dict[str, Any]:
        chosen = self._pick_folder_dialog("Select Mod Loader v2 Build Directory")
        if not chosen:
            return {"cancelled": True}
        return {"cancelled": False, **self.set_mlv2_folder(chosen)}

    def run_debugger_check_only(self) -> dict[str, Any]:
        return self.service.check_debugger_compatibility()

    def collect_onboarding(self) -> dict[str, Any]:
        return self.service.collect_onboarding_bundle()

    def open_folder(self, which: str) -> dict[str, Any]:
        if which == "receipts":
            target = self.data_dir / "receipts"
            target.mkdir(parents=True, exist_ok=True)
        elif which == "custom_mods":
            target = custom_mods_dir(self.data_dir)
        elif which == "mlv2":
            mlv2_path = self._toolchain()["mlv2"].get("path")
            target = Path(mlv2_path) if mlv2_path else self.data_dir
        else:
            raise ValueError(f"Unknown folder key: {which}")

        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        return {"opened": str(target)}

    def _pick_file_dialog(self, title: str, file_types: tuple[str, ...]) -> str | None:
        if self._window is not None:
            import webview

            dialog_type = getattr(getattr(webview, "FileDialog", webview), "OPEN", getattr(webview, "OPEN_DIALOG", 10))
            res = self._window.create_file_dialog(
                dialog_type, allow_multiple=False, file_types=file_types
            )
            return str(res[0]) if res else None
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            chosen = filedialog.askopenfilename(title=title, filetypes=[("Mod files", "*.mod")])
            return chosen or None
        finally:
            root.destroy()

    def _pick_folder_dialog(self, title: str) -> str | None:
        if self._window is not None:
            import webview

            dialog_type = getattr(getattr(webview, "FileDialog", webview), "FOLDER", getattr(webview, "FOLDER_DIALOG", 20))
            res = self._window.create_file_dialog(dialog_type)
            return str(res[0]) if res else None
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(title=title)
            return chosen or None
        finally:
            root.destroy()


API_DISPATCH_METHODS = (
    "get_initial_state",
    "scan_camera",
    "read_live_tweaks",
    "import_custom_mod_bytes",
    "import_custom_mod_dialog",
    "remove_custom_mod",
    "preview_install",
    "start_install",
    "cancel_active_job",
    "list_sd_drives",
    "preview_sd_sync",
    "start_sd_sync",
    "prepare_tools",
    "set_mlv2_folder",
    "pick_mlv2_folder",
    "run_debugger_check_only",
    "collect_onboarding",
    "open_folder",
    "poll_events",
)


_STATIC_MIME = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def create_web_server(
    api: ModManagerApi,
    *,
    ui_dir: Path = UI_DIR,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start an authenticated localhost HTTP server for ``--web`` mode and tests."""
    session_token = token or secrets.token_urlsafe(24)
    resolved_ui = Path(ui_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path).path
            rel = "index.html" if parsed in ("", "/") else parsed.lstrip("/")
            candidate = (resolved_ui / rel).resolve()
            if not candidate.is_relative_to(resolved_ui) or not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = candidate.read_bytes()
            ctype = _STATIC_MIME.get(candidate.suffix.lower()) or mimetypes.guess_type(str(candidate))[0]
            if candidate.name == "index.html":
                html = data.decode("utf-8").replace(
                    "</head>",
                    f'  <meta name="mm-token" content="{session_token}">\n</head>',
                    1,
                )
                data = html.encode("utf-8")
                ctype = "text/html"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            if self.headers.get("X-ModManager-Token") != session_token:
                self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Invalid session token"})
                return
            parsed = urlparse(self.path).path
            if not parsed.startswith("/api/"):
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown endpoint"})
                return
            method_name = parsed[len("/api/") :]
            if method_name not in API_DISPATCH_METHODS:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Unknown API method: {method_name}"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > SMOD_MAX_TOTAL_BYTES * 3:
                    self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Payload too large"})
                    return
                raw = self.rfile.read(length) if length > 0 else b"{}"
                kwargs = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                if not isinstance(kwargs, dict):
                    raise ValueError("JSON body must be an object")
                result = getattr(api, method_name)(**kwargs)
                self._send_json(HTTPStatus.OK, {"ok": True, "data": result})
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

    server = ThreadingHTTPServer((host, port), Handler)
    return server, session_token


def launch_web_mode(api: ModManagerApi, *, port: int = 0, open_browser: bool = True) -> None:
    server, _token = create_web_server(api, port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"BIONZ X Mod Manager web UI listening at {url}")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def launch_desktop(api: ModManagerApi) -> None:
    import webview

    index_url = (UI_DIR / "index.html").resolve().as_uri()
    window = webview.create_window(
        "Sony BIONZ X Mod Manager",
        url=index_url,
        js_api=api,
        width=1140,
        height=780,
        min_size=(940, 640),
        background_color="#0B0D12",
    )
    api.attach_window(window)

    def _on_loaded() -> None:
        _apply_windows_dark_titlebar()

    window.events.loaded += _on_loaded
    webview.start(gui="edgechromium", debug=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sony BIONZ X Mod Manager GUI")
    parser.add_argument("--web", action="store_true", help="Serve UI over localhost HTTP in default browser")
    parser.add_argument("--port", type=int, default=0, help="Port for --web mode (default: ephemeral)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open browser in --web mode")
    args = parser.parse_args(argv)

    api = ModManagerApi()
    if args.web:
        launch_web_mode(api, port=args.port, open_browser=not args.no_open)
        return 0

    try:
        import webview  # noqa: F401
    except ImportError:
        print("pywebview is not installed in this Python environment; starting --web browser fallback.")
        launch_web_mode(api, port=args.port, open_browser=not args.no_open)
        return 0

    launch_desktop(api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
