"""BIONZ X Film Sim Studio — Visual tone-curve & color-matrix editor with live split preview
and one-click SMOD v2 .mod build & USB push for Sony ILCE-6100 and ILCE-6400.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageOps, ImageTk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mods.film_sims.studio_core import (
    PRESETS,
    TARGETS,
    FilmSimParams,
    _slug,
    build_mod,
    compute_curves,
    detect_available_target,
    get_target,
    image_metrics,
    load_stock_tables,
    push_mod_to_camera,
    read_camera_logs,
    simulate_image,
)

BG = "#1b1d21"
BG_PANEL = "#24272d"
FG = "#e8eaed"
FG_DIM = "#9aa0a6"
ACCENT = "#ff8c2b"


class FilmSimStudio(tk.Tk):
    def __init__(self, initial_image: Path | None = None, target: str | None = None):
        super().__init__()
        init_target = target or detect_available_target("a6100")
        self.title("BIONZ X Film Sim Studio (ILCE-6100 / ILCE-6400)")
        self.geometry("1260x840")
        self.minsize(980, 700)
        self.configure(bg=BG)

        self._configure_styles()
        self.target_var = tk.StringVar(value=init_target)
        self.stock = self._load_stock_for_target(init_target)
        self.src_full: np.ndarray | None = None
        self.sim_full: np.ndarray | None = None
        self.src_preview: np.ndarray | None = None
        self.sim_preview: np.ndarray | None = None
        self.src_metrics: dict[str, float] | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._render_job: str | None = None
        self._full_res_job: str | None = None
        self._wndproc_ref = None
        self._old_wndproc = None
        self._zoom = 1.0
        self._fit_mode = True
        self._pan_x = 0.5
        self._pan_y = 0.5
        self._drag_xy: tuple[int, int] | None = None

        self.preset_var = tk.StringVar(value="Classic Chrome")
        self.mod_name_var = tk.StringVar(value="Classic Chrome")
        self.split_var = tk.DoubleVar(value=0.5)
        self.zoom_var = tk.StringVar(value="Fit")
        self.metrics_var = tk.StringVar(value="Open or drag & drop a camera JPEG/PNG to preview.")
        self.status_var = tk.StringVar(value=f"Ready ({get_target(init_target).label}).")

        self.vars: dict[str, tk.DoubleVar | tk.IntVar] = {}
        self._val_labels: dict[str, tk.Label] = {}

        self._build_ui()
        self._enable_windows_dnd()
        self.apply_preset("Classic Chrome")

        if initial_image and initial_image.is_file():
            self.load_image_file(initial_image)

    def _load_stock_for_target(self, target: str) -> dict[str, list[int]]:
        prof = get_target(target)
        if prof.avcam_default_path.is_file():
            return load_stock_tables(target=target)
        fallback = detect_available_target(target)
        if get_target(fallback).avcam_default_path.is_file():
            return load_stock_tables(target=fallback)
        raise FileNotFoundError(
            f"Missing av-cam.bin for {prof.label} ({prof.avcam_default_path})"
        )

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG_PANEL, foreground=FG, fieldbackground="#181a1f")
        style.configure("TFrame", background=BG_PANEL)
        style.configure("Dark.TFrame", background=BG)
        style.configure("TLabel", background=BG_PANEL, foreground=FG, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=BG_PANEL, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        style.configure("TButton", background="#343842", foreground=FG, padding=(8, 4))
        style.map("TButton", background=[("active", "#454b58")])
        style.configure("Accent.TButton", background="#c96514", foreground="#ffffff", font=("Segoe UI", 9, "bold"), padding=(10, 5))
        style.map("Accent.TButton", background=[("active", "#e0741b")])
        style.configure("TCombobox", fieldbackground="#181a1f", background="#343842", foreground=FG)
        style.configure("Horizontal.TScale", background=BG_PANEL, troughcolor="#15171b")

    def _build_ui(self) -> None:
        main = ttk.Frame(self, style="Dark.TFrame", padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=0)
        main.rowconfigure(0, weight=1)

        # ---- Left Pane: Image Preview ----
        left = ttk.Frame(main, padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        bar = ttk.Frame(left)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="Open Image...", command=self.open_image).pack(side="left", padx=(0, 8))
        ttk.Label(bar, text="Split:").pack(side="left")
        ttk.Scale(bar, from_=0.0, to=1.0, variable=self.split_var, length=160, command=lambda _e: self.draw_preview()).pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="Zoom:").pack(side="left")
        zcb = ttk.Combobox(bar, textvariable=self.zoom_var, state="readonly", width=7, values=("Fit", "100%", "200%", "400%"))
        zcb.pack(side="left", padx=4)
        zcb.bind("<<ComboboxSelected>>", lambda _e: self._on_zoom_change())
        ttk.Button(bar, text="Reset View", command=self.reset_view).pack(side="left", padx=4)

        self.canvas = tk.Canvas(left, bg="#121316", highlightthickness=1, highlightbackground="#363a42")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _e: self.draw_preview())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag_xy", None))

        ttk.Label(left, textvariable=self.metrics_var, font=("Consolas", 9)).grid(row=2, column=0, sticky="ew", pady=(6, 0))

        # ---- Right Pane: Controls ----
        right = ttk.Frame(main, padding=10, width=370)
        right.grid(row=0, column=1, sticky="ns")
        right.grid_propagate(False)

        # Target & Presets & Recipe
        t_row = ttk.Frame(right)
        t_row.pack(fill="x", pady=(0, 4))
        ttk.Label(t_row, text="Camera:", font=("Segoe UI", 9, "bold")).pack(side="left")
        tcb = ttk.Combobox(t_row, textvariable=self.target_var, state="readonly", width=10, values=list(TARGETS))
        tcb.pack(side="left", padx=6)
        tcb.bind("<<ComboboxSelected>>", lambda _e: self._on_target_change())
        self._target_label = ttk.Label(t_row, text=get_target(self.target_var.get()).label, foreground=FG_DIM)
        self._target_label.pack(side="left", padx=4)

        p_row = ttk.Frame(right)
        p_row.pack(fill="x", pady=(0, 6))
        ttk.Label(p_row, text="Preset:", font=("Segoe UI", 9, "bold")).pack(side="left")
        pcb = ttk.Combobox(p_row, textvariable=self.preset_var, state="readonly", width=18, values=list(PRESETS))
        pcb.pack(side="left", padx=6)
        pcb.bind("<<ComboboxSelected>>", lambda _e: self.apply_preset(self.preset_var.get()))
        ttk.Button(p_row, text="Save", width=5, command=self.save_recipe).pack(side="right", padx=(2, 0))
        ttk.Button(p_row, text="Load", width=5, command=self.load_recipe).pack(side="right")

        # 96-knot Curve Graph
        self.curve_canvas = tk.Canvas(right, width=340, height=150, bg="#14161a", highlightthickness=1, highlightbackground="#363a42")
        self.curve_canvas.pack(fill="x", pady=(2, 8))

        # Tone Sliders
        ttk.Label(right, text="Tone Curve (96-Knot EC + FC)", style="Header.TLabel").pack(anchor="w", pady=(0, 4))
        self._add_slider(right, "shadow_toe", "Shadow Toe", -0.40, 0.40, 0.01, "{:+.2f}")
        self._add_slider(right, "mid_contrast", "Midtone Contrast", 0.60, 1.50, 0.01, "{:.2f}x")
        self._add_slider(right, "crossover", "Crossover Point", 0.12, 0.65, 0.01, "{:.2f}")
        self._add_slider(right, "highlight_rolloff", "Highlight Roll-off", 0.00, 1.00, 0.01, "{:.2f}")
        self._add_slider(right, "black_fade", "Black Lift", 0, 40, 1, "{:.0f}", is_int=True)
        self._add_slider(right, "white_ceiling", "White Ceiling", 920, 1023, 1, "{:.0f}", is_int=True)

        # Color Sliders
        ttk.Label(right, text="Color Matrix (Q10 A0 + A1)", style="Header.TLabel").pack(anchor="w", pady=(8, 3))
        self._add_slider(right, "saturation", "Saturation", 0.00, 1.50, 0.01, "{:.2f}x")
        self._add_slider(right, "red_amber", "Red -> Amber", -0.25, 0.25, 0.01, "{:+.2f}")
        self._add_slider(right, "green_olive", "Green -> Olive", -0.25, 0.25, 0.01, "{:+.2f}")
        self._add_slider(right, "blue_cyan", "Blue -> Cyan", -0.25, 0.25, 0.01, "{:+.2f}")
        self._add_slider(right, "warmth", "Warmth (R/B)", -0.15, 0.15, 0.01, "{:+.2f}")

        # 5x9x9 3D LUT Split-Toning & Hue Sliders
        ttk.Label(right, text="Split-Tone & Hue (5x9x9 3D LUT)", style="Header.TLabel").pack(anchor="w", pady=(8, 3))
        self._add_slider(right, "shadow_cb", "Shadow Blue/Warm", -16.0, 16.0, 0.5, "{:+.1f}")
        self._add_slider(right, "shadow_cr", "Shadow Cyan/Mag", -16.0, 16.0, 0.5, "{:+.1f}")
        self._add_slider(right, "highlight_cb", "High Warm/Blue", -16.0, 16.0, 0.5, "{:+.1f}")
        self._add_slider(right, "highlight_cr", "High Green/Red", -16.0, 16.0, 0.5, "{:+.1f}")
        self._add_slider(right, "hue_shift", "Hue Twist (deg)", -20.0, 20.0, 0.5, "{:+.1f}")

        # Export & Camera Push
        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=10)
        name_row = ttk.Frame(right)
        name_row.pack(fill="x", pady=(0, 6))
        ttk.Label(name_row, text="Mod Name:").pack(side="left")
        ttk.Entry(name_row, textvariable=self.mod_name_var, width=26).pack(side="right", fill="x", expand=True, padx=(8, 0))

        btn_row = ttk.Frame(right)
        btn_row.pack(fill="x", pady=(2, 4))
        ttk.Button(btn_row, text="Build .mod", command=self.on_build_only).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(btn_row, text="Build & Push to Camera", style="Accent.TButton", command=self.on_build_and_push).pack(side="left", fill="x", expand=True)

        ttk.Button(right, text="Read Camera Log", command=self.on_read_log).pack(fill="x", pady=(2, 6))
        ttk.Label(right, textvariable=self.status_var, foreground=FG_DIM, wraplength=340, justify="left").pack(anchor="w")

    def _add_slider(self, parent: ttk.Frame, key: str, label: str, lo: float, hi: float, res: float, fmt: str, is_int: bool = False) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=16).pack(side="left")
        var: tk.DoubleVar | tk.IntVar = tk.IntVar(value=0) if is_int else tk.DoubleVar(value=0.0)
        self.vars[key] = var
        val_lbl = ttk.Label(row, text="", width=7, anchor="e")
        val_lbl.pack(side="right")
        self._val_labels[key] = val_lbl

        def _on_slide(_v: str) -> None:
            v = int(round(float(var.get()))) if is_int else round(float(var.get()) / res) * res
            var.set(v)
            val_lbl.config(text=fmt.format(v))
            self.schedule_update()

        sc = ttk.Scale(row, from_=lo, to=hi, variable=var, command=_on_slide)
        sc.pack(side="left", fill="x", expand=True, padx=4)
        sc._fmt = fmt  # type: ignore[attr-defined]
        sc._is_int = is_int  # type: ignore[attr-defined]
        var._fmt = fmt  # type: ignore[attr-defined]

    def current_params(self) -> FilmSimParams:
        return FilmSimParams(
            name=self.mod_name_var.get().strip() or "Custom Sim",
            shadow_toe=float(self.vars["shadow_toe"].get()),
            mid_contrast=float(self.vars["mid_contrast"].get()),
            crossover=float(self.vars["crossover"].get()),
            highlight_rolloff=float(self.vars["highlight_rolloff"].get()),
            black_fade=int(self.vars["black_fade"].get()),
            white_ceiling=int(self.vars["white_ceiling"].get()),
            saturation=float(self.vars["saturation"].get()),
            red_amber=float(self.vars["red_amber"].get()),
            green_olive=float(self.vars["green_olive"].get()),
            blue_cyan=float(self.vars["blue_cyan"].get()),
            warmth=float(self.vars["warmth"].get()),
            shadow_cb=float(self.vars["shadow_cb"].get()),
            shadow_cr=float(self.vars["shadow_cr"].get()),
            highlight_cb=float(self.vars["highlight_cb"].get()),
            highlight_cr=float(self.vars["highlight_cr"].get()),
            hue_shift=float(self.vars["hue_shift"].get()),
        )

    def apply_preset(self, name: str) -> None:
        p = PRESETS[name]
        if name != "Stock (Reset)":
            self.mod_name_var.set(p.name)
        d = p.to_dict()
        for k, var in self.vars.items():
            var.set(d[k])
            self._val_labels[k].config(text=var._fmt.format(d[k]))  # type: ignore[attr-defined]
        self.schedule_update(immediate=True)

    def _on_target_change(self) -> None:
        key = self.target_var.get()
        prof = get_target(key)
        self._target_label.config(text=prof.label)
        try:
            self.stock = self._load_stock_for_target(key)
        except Exception as exc:
            messagebox.showerror("Film Sim Studio", str(exc))
            return
        self.status_var.set(f"Switched target to {prof.label}.")
        self.schedule_update(immediate=True)

    def open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Open Camera JPEG / PNG / TIFF",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"), ("All files", "*.*")],
        )
        if path:
            self.load_image_file(Path(path))

    def load_image_file(self, path: Path) -> None:
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        self.src_full = np.asarray(im, dtype=np.uint8)
        self.sim_full = None
        proxy = im.copy()
        proxy.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
        self.src_preview = np.asarray(proxy, dtype=np.uint8)
        self.src_metrics = image_metrics(self.src_preview)
        self.reset_view()
        self.status_var.set(f"Loaded {path.name} ({im.width}x{im.height} full res)")
        self.schedule_update(immediate=True)

    def schedule_update(self, immediate: bool = False) -> None:
        if self._render_job is not None:
            self.after_cancel(self._render_job)
        if self._full_res_job is not None:
            self.after_cancel(self._full_res_job)
            self._full_res_job = None
        if immediate:
            self._recompute(full_res=True)
        else:
            self._render_job = self.after(20, lambda: self._recompute(full_res=False))

    def _recompute(self, full_res: bool = False) -> None:
        self._render_job = None
        p = self.current_params()
        self.draw_curve_graph(p)
        if self.src_preview is None or self.src_full is None:
            return
        self.sim_preview = simulate_image(self.src_preview, self.stock, p)
        if full_res or self.src_full.shape == self.src_preview.shape:
            self.sim_full = self.sim_preview if self.src_full.shape == self.src_preview.shape else simulate_image(self.src_full, self.stock, p)
        else:
            self.sim_full = None
            self._full_res_job = self.after(180, self._recompute_full_res)
        m0 = self.src_metrics or image_metrics(self.src_preview)
        m1 = image_metrics(self.sim_preview)
        dg_pct = ((m1["detail_grad"] - m0["detail_grad"]) / max(1e-4, m0["detail_grad"])) * 100.0
        self.metrics_var.set(
            f"Stock: Luma {m0['luma']:.1f} | Sat {m0['sat_pct']:.1f}% | Detail {m0['detail_grad']:.2f}   ||   "
            f"Sim: Luma {m1['luma']:.1f} | Sat {m1['sat_pct']:.1f}% | Detail {m1['detail_grad']:.2f} ({dg_pct:+.1f}%)"
        )
        self.draw_preview()

    def _recompute_full_res(self) -> None:
        self._full_res_job = None
        if self.src_full is None:
            return
        p = self.current_params()
        self.sim_full = simulate_image(self.src_full, self.stock, p)
        self.draw_preview()

    def draw_curve_graph(self, p: FilmSimParams) -> None:
        c = self.curve_canvas
        c.delete("all")
        w = int(c.cget("width"))
        h = int(c.cget("height"))
        pad = 12
        gw, gh = w - 2 * pad, h - 2 * pad

        # Grid
        for frac in (0.25, 0.5, 0.75):
            gx, gy = pad + frac * gw, pad + frac * gh
            c.create_line(gx, pad, gx, h - pad, fill="#262930")
            c.create_line(pad, gy, w - pad, gy, fill="#262930")
        c.create_rectangle(pad, pad, w - pad, h - pad, outline="#3a3f4a")

        fc_stock = self.stock["fc_stock"]
        _, fc_sim = compute_curves(self.stock, p)

        # Crossover marker
        cx = pad + p.crossover * gw
        c.create_line(cx, pad, cx, h - pad, fill="#3f4b5e", dash=(2, 3))

        # Plot Stock diagonal (x=fc_stock, y=fc_stock) and Sim (x=fc_stock, y=fc_sim)
        pts_stock, pts_sim = [], []
        for s_val, sim_val in zip(fc_stock, fc_sim):
            px = pad + (s_val / 1023.0) * gw
            py_s = h - pad - (s_val / 1023.0) * gh
            py_m = h - pad - (sim_val / 1023.0) * gh
            pts_stock.extend((px, py_s))
            pts_sim.extend((px, py_m))
        c.create_line(*pts_stock, fill="#6c7380", dash=(4, 3), width=1.5)
        c.create_line(*pts_sim, fill=ACCENT, width=2.2, smooth=True)

    def draw_preview(self) -> None:
        use_full = self.src_full is not None and self.sim_full is not None
        src_arr = self.src_full if use_full else self.src_preview
        sim_arr = self.sim_full if use_full else self.sim_preview
        if src_arr is None or sim_arr is None:
            return
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        ih, iw, _ = src_arr.shape

        if self._fit_mode:
            scale = min(cw / iw, ch / ih)
        else:
            scale = self._zoom

        disp_w = max(1, int(round(iw * scale)))
        disp_h = max(1, int(round(ih * scale)))

        # Crop/pan region if larger than canvas
        src_im = Image.fromarray(src_arr)
        sim_im = Image.fromarray(sim_arr)
        if disp_w <= cw and disp_h <= ch:
            left_s = src_im.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
            right_s = sim_im.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
            ox, oy = (cw - disp_w) // 2, (ch - disp_h) // 2
        else:
            view_w = min(iw, max(1, int(round(cw / scale))))
            view_h = min(ih, max(1, int(round(ch / scale))))
            x0 = int(round(max(0, min(iw - view_w, self._pan_x * iw - view_w / 2))))
            y0 = int(round(max(0, min(ih - view_h, self._pan_y * ih - view_h / 2))))
            tw = min(cw, int(round(view_w * scale)))
            th = min(ch, int(round(view_h * scale)))
            resample = Image.Resampling.NEAREST if scale >= 2.0 else Image.Resampling.BILINEAR
            left_s = src_im.crop((x0, y0, x0 + view_w, y0 + view_h)).resize((tw, th), resample)
            right_s = sim_im.crop((x0, y0, x0 + view_w, y0 + view_h)).resize((tw, th), resample)
            ox, oy = (cw - tw) // 2, (ch - th) // 2

        comp = np.asarray(right_s, dtype=np.uint8).copy()
        lw = comp.shape[1]
        split_x = int(round(self.split_var.get() * lw))
        if split_x > 0:
            comp[:, :split_x] = np.asarray(left_s, dtype=np.uint8)[:, :split_x]

        self._photo = ImageTk.PhotoImage(Image.fromarray(comp))
        self.canvas.delete("all")
        self.canvas.create_image(ox, oy, anchor="nw", image=self._photo)
        sx = ox + split_x
        self.canvas.create_line(sx, oy, sx, oy + comp.shape[0], fill=ACCENT, width=2)
        self.canvas.create_text(ox + 10, oy + 10, anchor="nw", text="STOCK", fill="#ffffff", font=("Segoe UI", 9, "bold"))
        self.canvas.create_text(ox + lw - 10, oy + 10, anchor="ne", text="PREVIEW", fill=ACCENT, font=("Segoe UI", 9, "bold"))

    def _on_zoom_change(self) -> None:
        z = self.zoom_var.get()
        if z == "Fit":
            self._fit_mode = True
        else:
            self._fit_mode = False
            self._zoom = int(z.rstrip("%")) / 100.0
        self.draw_preview()

    def _on_wheel(self, e: tk.Event) -> None:
        self._fit_mode = False
        self._zoom = max(0.25, min(6.0, self._zoom * (1.15 if e.delta > 0 else 0.87)))
        self.zoom_var.set(f"{int(round(self._zoom * 100))}%")
        self.draw_preview()

    def _on_pan_start(self, e: tk.Event) -> None:
        self._drag_xy = (e.x, e.y)

    def _on_pan_move(self, e: tk.Event) -> None:
        src = self.src_full if self.src_full is not None else self.src_preview
        if self._drag_xy is None or src is None or self._fit_mode:
            return
        dx, dy = e.x - self._drag_xy[0], e.y - self._drag_xy[1]
        self._drag_xy = (e.x, e.y)
        ih, iw, _ = src.shape
        self._pan_x = max(0.0, min(1.0, self._pan_x - dx / max(1.0, iw * self._zoom)))
        self._pan_y = max(0.0, min(1.0, self._pan_y - dy / max(1.0, ih * self._zoom)))
        self.draw_preview()

    def _enable_windows_dnd(self) -> None:
        """Accept dragged image files via Windows WM_DROPFILES without external deps."""
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes
            self.update_idletasks()
            hwnd = int(self.wm_frame(), 16)
            shell32 = ctypes.windll.shell32
            user32 = ctypes.windll.user32
            GWLP_WNDPROC = -4
            WM_DROPFILES = 0x0233
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallWindowProcW.restype = LRESULT
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            def _wndproc(h, msg, wparam, lparam):
                if msg == WM_DROPFILES:
                    hdrop = wintypes.HANDLE(wparam)
                    buf = ctypes.create_unicode_buffer(1024)
                    if shell32.DragQueryFileW(hdrop, 0, buf, 1024) > 0:
                        dropped = Path(buf.value)
                        self.after(0, lambda p=dropped: self.load_image_file(p))
                    shell32.DragFinish(hdrop)
                    return 0
                return user32.CallWindowProcW(self._old_wndproc, h, msg, wparam, lparam)

            shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
            self._wndproc_ref = WNDPROC(_wndproc)
            self._old_wndproc = user32.SetWindowLongPtrW(
                wintypes.HWND(hwnd), GWLP_WNDPROC, ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            )
        except Exception:
            pass

    def reset_view(self) -> None:
        self._fit_mode = True
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        self.zoom_var.set("Fit")
        self.draw_preview()

    def save_recipe(self) -> None:
        p = self.current_params()
        path = filedialog.asksaveasfilename(
            title="Save Film Sim Recipe",
            defaultextension=".json",
            initialfile=f"{_slug(p.name)}.json",
            filetypes=[("JSON recipe", "*.json")],
        )
        if path:
            Path(path).write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
            self.status_var.set(f"Saved recipe to {Path(path).name}")

    def load_recipe(self) -> None:
        path = filedialog.askopenfilename(title="Load Film Sim Recipe", filetypes=[("JSON recipe", "*.json")])
        if not path:
            return
        p = FilmSimParams.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        self.mod_name_var.set(p.name)
        d = p.to_dict()
        for k, var in self.vars.items():
            var.set(d[k])
            self._val_labels[k].config(text=var._fmt.format(d[k]))  # type: ignore[attr-defined]
        self.schedule_update(immediate=True)
        self.status_var.set(f"Loaded recipe {Path(path).name}")

    def on_build_only(self) -> None:
        target = self.target_var.get()
        self._run_bg(f"Building {target} .mod...", lambda p: build_mod(p, target=target, stock=self.stock), push=False)

    def on_build_and_push(self) -> None:
        target = self.target_var.get()
        self._run_bg(f"Building {target} .mod & pushing to camera...", lambda p: self._build_and_push(p, target), push=True)

    def _build_and_push(self, p: FilmSimParams, target: str) -> dict:
        b = build_mod(p, target=target, stock=self.stock)
        u = push_mod_to_camera(Path(b["local_path"]), b["mod_filename"], target=target)
        return {**b, **u}

    def _run_bg(self, msg: str, fn, push: bool) -> None:
        p = self.current_params()
        self.status_var.set(msg)

        def worker():
            try:
                res = fn(p)
                if push:
                    txt = f"Pushed {res['remote']} ({res['bytes']} B) to {res['drive']}!"
                else:
                    txt = f"Built {res['local_path']} ({res['size']} B)"
                self.after(0, lambda: self.status_var.set(txt))
            except Exception as exc:
                self.after(0, lambda e=exc: (self.status_var.set(f"Error: {e}"), messagebox.showerror("Film Sim Studio", str(e))))

        threading.Thread(target=worker, daemon=True).start()

    def on_read_log(self) -> None:
        slug = _slug(self.mod_name_var.get())
        target = self.target_var.get()
        self.status_var.set("Reading camera logs...")

        def worker():
            try:
                txt = read_camera_logs(slug, target=target)
                self.after(0, lambda: (self.status_var.set("Camera logs read."), messagebox.showinfo("Camera Logs", txt)))
            except Exception as exc:
                self.after(0, lambda e=exc: (self.status_var.set(f"Error: {e}"), messagebox.showerror("Camera Logs", str(e))))

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description="BIONZ X Film Sim Studio (ILCE-6100 / ILCE-6400)")
    ap.add_argument("image", nargs="?", type=Path, help="Optional JPEG/PNG/TIFF preview image")
    ap.add_argument("--target", choices=tuple(TARGETS), default=None, help="Camera target profile (default: auto-detect)")
    args = ap.parse_args()
    app = FilmSimStudio(args.image, target=args.target)
    app.mainloop()


if __name__ == "__main__":
    main()
