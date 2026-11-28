"""Sony A7C Emulator — GUI Launcher (no CMD window, progress bar only)."""

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import ttk

HERE = Path(__file__).resolve().parent
PORT = 8642
PANEL_URL = f"http://localhost:{PORT}"
CREATE_NO_WINDOW = 0x08000000

CARD_MODES = {
    "Photo Card (sdcard_photo.img)": "out/sdcard_photo.img",
    "No Card (Menus / Fast)": "none",
    "Blank 4 GB Card (sdcard.img)": "out/sdcard.img",
}


def run_wsl_hidden(bash_cmd: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl.exe", "-e", "bash", "-lc", bash_cmd],
        capture_output=capture,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )


class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Sony α7C Emulator")
        self.root.geometry("480x250")
        self.root.resizable(False, False)
        self.root.configure(bg="#141416")

        self.proc: subprocess.Popen | None = None
        self.stopping = False
        self.opened_browser = False
        self.boot_start = 0.0

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Auto-start immediately on double-click
        self.root.after(150, self.start_emulator)

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Sony.Horizontal.TProgressbar",
            troughcolor="#26262b",
            background="#e06c1f",
            bordercolor="#26262b",
            lightcolor="#e06c1f",
            darkcolor="#e06c1f",
            thickness=14,
        )

        pad = {"padx": 22}
        tk.Label(
            self.root,
            text="SONY α7C EMULATOR",
            font=("Segoe UI", 14, "bold"),
            fg="#f4f4f6",
            bg="#141416",
        ).pack(anchor="w", pady=(18, 2), **pad)

        self.subtitle = tk.Label(
            self.root,
            text="Preparing emulator environment...",
            font=("Segoe UI", 10),
            fg="#a1a1aa",
            bg="#141416",
        )
        self.subtitle.pack(anchor="w", pady=(0, 14), **pad)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.pbar = ttk.Progressbar(
            self.root,
            style="Sony.Horizontal.TProgressbar",
            variable=self.progress_var,
            maximum=100.0,
            mode="determinate",
        )
        self.pbar.pack(fill="x", **pad)

        self.detail_var = tk.StringVar(value="0%")
        tk.Label(
            self.root,
            textvariable=self.detail_var,
            font=("Segoe UI", 9),
            fg="#71717a",
            bg="#141416",
        ).pack(anchor="e", pady=(4, 14), **pad)

        # Card selector row
        row = tk.Frame(self.root, bg="#141416")
        row.pack(fill="x", pady=(0, 14), **pad)
        tk.Label(
            row,
            text="SD Card:",
            font=("Segoe UI", 9),
            fg="#a1a1aa",
            bg="#141416",
        ).pack(side="left", padx=(0, 8))
        self.card_var = tk.StringVar(value="Photo Card (sdcard_photo.img)")
        self.card_menu = ttk.Combobox(
            row,
            textvariable=self.card_var,
            values=list(CARD_MODES.keys()),
            state="readonly",
            width=30,
        )
        self.card_menu.pack(side="left", fill="x", expand=True)

        # Action buttons
        btns = tk.Frame(self.root, bg="#141416")
        btns.pack(fill="x", **pad)
        self.open_btn = tk.Button(
            btns,
            text="Open Camera GUI",
            command=lambda: webbrowser.open(PANEL_URL),
            bg="#e06c1f",
            fg="#ffffff",
            activebackground="#c95b14",
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=6,
            state="disabled",
            cursor="hand2",
        )
        self.open_btn.pack(side="left")

        self.restart_btn = tk.Button(
            btns,
            text="Restart",
            command=self.restart_emulator,
            bg="#27272a",
            fg="#e4e4e7",
            activebackground="#3f3f46",
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI", 9),
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.restart_btn.pack(side="left", padx=(8, 0))

        self.quit_btn = tk.Button(
            btns,
            text="Stop & Quit",
            command=self.on_close,
            bg="#27272a",
            fg="#e4e4e7",
            activebackground="#7f1d1d",
            activeforeground="#ffffff",
            relief="flat",
            font=("Segoe UI", 9),
            padx=12,
            pady=6,
            cursor="hand2",
        )
        self.quit_btn.pack(side="right")

    def set_status(self, text: str, pct: float, detail: str | None = None):
        def _apply():
            self.subtitle.config(text=text)
            self.progress_var.set(pct)
            self.detail_var.set(detail if detail is not None else f"{int(pct)}%")

        self.root.after(0, _apply)

    def start_emulator(self):
        self.stopping = False
        self.opened_browser = False
        self.boot_start = time.monotonic()
        self.open_btn.config(state="disabled")
        threading.Thread(target=self._boot_worker, daemon=True).start()

    def restart_emulator(self):
        self.set_status("Restarting emulator...", 5)
        threading.Thread(target=self._restart_worker, daemon=True).start()

    def _restart_worker(self):
        self._stop_backend()
        self.root.after(0, self.start_emulator)

    def _boot_worker(self):
        try:
            self.set_status("Checking WSL & QEMU build...", 5)
            chk = run_wsl_hidden("test -x ~/a7c-emulator/qemu/build/qemu-system-arm")
            if chk.returncode != 0:
                self.set_status("Building QEMU in WSL (first-time setup)...", 10)
                win_dir = str(HERE).replace("\\", "/")
                bld = run_wsl_hidden(
                    f"cd \"$(wslpath '{win_dir}')\" && bash tools/rebuild_wsl.sh"
                )
                if bld.returncode != 0:
                    self.set_status("Build failed. Check WSL setup.", 0, "Error")
                    return

            self.set_status("Cleaning up previous session...", 15)
            run_wsl_hidden("fuser -k 8642/tcp || true; pkill -x qemu-system-arm || true")
            time.sleep(0.8)

            card_arg = CARD_MODES.get(self.card_var.get(), "none")
            if card_arg == "out/sdcard.img":
                run_wsl_hidden(
                    "cd ~/a7c-emulator && mkdir -p out && "
                    "[ -e out/sdcard.img ] || truncate -s 4G out/sdcard.img"
                )
            elif card_arg == "out/sdcard_photo.img":
                run_wsl_hidden(
                    "cd ~/a7c-emulator && mkdir -p out && "
                    "[ -e out/sdcard_photo.img ] || python3 tools/make_sdcard.py out/sdcard_photo.img"
                )

            self.set_status("Launching Sony α7C firmware...", 22)
            cmd = (
                f"cd ~/a7c-emulator && "
                f"A7C_FRESH=1 python3 tools/a7c_panel.py --port {PORT} "
                f"--card '{card_arg}' --env A7C_LIVEVIEW=1"
            )
            self.proc = subprocess.Popen(
                ["wsl.exe", "-e", "bash", "-lc", cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )

            self._poll_boot()
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Launch error: {exc}", 0, "Error")

    def _poll_boot(self):
        # Typical boot to first screen takes ~240-290s host time.
        while not self.stopping:
            if self.proc and self.proc.poll() is not None:
                self.set_status("Emulator process exited.", 0, "Stopped")
                return

            elapsed = time.monotonic() - self.boot_start
            status = self._get_json(f"{PANEL_URL}/status")
            if status and status.get("running"):
                self.root.after(0, lambda: self.open_btn.config(state="normal"))
                line = status.get("console", "")
                lcd_visible = self._check_lcd_visible()
                if lcd_visible:
                    self.set_status("Camera ready! Opening GUI...", 100, "100% — Ready")
                    if not self.opened_browser:
                        self.opened_browser = True
                        self.root.after(0, lambda: webbrowser.open(PANEL_URL))
                    return

                # Smoothly map boot progress (25% -> 95%) without showing log spam
                stage = "Booting Linux kernel & drivers..."
                pct = min(95.0, 25.0 + (elapsed / 270.0) * 70.0)
                if "LIRO" in line or "entry_main" in line:
                    stage = "Starting av-cam RTOS..."
                    pct = max(pct, 45.0)
                elif "HeapAllocator" in line or "udtr" in line:
                    stage = "Initializing camera application (im.elf)..."
                    pct = max(pct, 65.0)
                elif "BTM_SetTraceLevel" in line or "BSA_trace" in line:
                    stage = "Finalizing UI & display..."
                    pct = max(pct, 85.0)

                mins, secs = divmod(int(elapsed), 60)
                self.set_status(stage, pct, f"{int(pct)}% ({mins}:{secs:02d})")
            else:
                self.set_status("Waiting for web panel...", 20)

            time.sleep(1.5)

    @staticmethod
    def _get_json(url: str):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _check_lcd_visible() -> bool:
        try:
            req = urllib.request.Request(f"{PANEL_URL}/lcd.png", method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.headers.get("X-A7C-Lcd") == "visible"
        except Exception:  # noqa: BLE001
            return False

    def _stop_backend(self):
        try:
            req = urllib.request.Request(
                f"{PANEL_URL}/emu/pause",
                method="POST",
                headers={"X-A7C-Panel": "1"},
            )
            urllib.request.urlopen(req, timeout=1).close()
        except Exception:  # noqa: BLE001
            pass
        run_wsl_hidden("fuser -k 8642/tcp || true; pkill -x qemu-system-arm || true")
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
        self.proc = None

    def on_close(self):
        self.stopping = True
        self.set_status("Stopping emulator cleanly...", 0, "Closing")
        threading.Thread(target=self._close_worker, daemon=True).start()

    def _close_worker(self):
        self._stop_backend()
        self.root.after(0, self.root.destroy)


def main():
    root = tk.Tk()
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
