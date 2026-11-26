"""Deprecated Windows Tkinter front-end.

Use ``isp-studio`` (``bionz-look-lab``). This module only calls ``lut_core``
and contains no colour math. ``lut_core`` remains the numeric oracle.
"""
from dataclasses import replace
import math
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image, ImageTk

import lut_core
from lut_core import pack
from lut_core.arri import ArriReference, SONY_SOURCE
from lut_core.alexa35 import Alexa35Reference
from lut_core.movie_gamma import movie_profile
from lut_core.color import SOURCES, colorchecker_image
from lut_core.image_source import load_image
from lut_core.preview import apply_overlay, compose_preview
from lut_profiles import PROFILES

MAX_PREVIEW = 6000


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("a6400 LUT converter")
        self.cube = self.result = self.image = None
        self.image_encoding = "srgb"
        self.image_note = ""
        self.base_exposure_scale = 1.0
        self.frame_report = None
        self.cube_report = None
        self._views = None
        self._arri_cache = {}
        self._ev_job = None
        self._ev_applied = 0.0
        self.image_is_log = tk.BooleanVar(value=False)
        self.source = tk.StringVar(value=list(SOURCES)[0])
        self.profile_name = tk.StringVar(value="a6400")
        self.split = tk.DoubleVar(value=0.5)
        self.ev = tk.DoubleVar(value=0.0)
        self.ev_readout = tk.StringVar(value="+0.0 EV")
        self.split_mode = tk.StringVar(value="Ideal .cube vs. ISP Sim")
        self.overlay_on = tk.BooleanVar(value=False)
        self.overlay_kind = tk.StringVar(value="Off")
        self.spatial_mode = tk.StringVar(value="Off (Color Only)")
        self.zoom_mode = tk.StringVar(value="Fit")
        self.cube_path = tk.StringVar(value="(no .cube loaded)")
        self._photo = None
        self._ref = self._app = None
        self._zoom_scale = None
        self._pan_x = 0.5
        self._pan_y = 0.5
        self._drag_start = None
        self._last_view = None

        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Button(top, text="Open .cube...", command=self.open_cube).grid(row=0, column=0, padx=2)
        ttk.Label(top, textvariable=self.cube_path, width=38).grid(row=0, column=1, sticky="w")
        ttk.Label(top, text="Source:").grid(row=0, column=2, padx=(10, 2))
        cb = ttk.Combobox(top, textvariable=self.source, values=list(SOURCES), state="readonly", width=24)
        cb.grid(row=0, column=3)
        cb.bind("<<ComboboxSelected>>", lambda e: self.convert())
        ttk.Label(top, text="Profile:").grid(row=0, column=4, padx=(10, 2))
        ttk.Combobox(top, textvariable=self.profile_name, values=list(PROFILES), state="readonly",
                     width=8).grid(row=0, column=5)

        ttk.Button(top, text="ARRI Wide Gamut", command=self.open_logc3).grid(row=1, column=0, padx=2)
        ttk.Button(top, text="ARRI Rec.709", command=self.open_rec709).grid(
            row=1, column=1, sticky="w", padx=2)

        ttk.Button(top, text="Alexa 35 LogC4", command=lambda: self.open_alexa35("logc4")).grid(
            row=2, column=0, padx=2)
        ttk.Button(top, text="Alexa 35 Rec.709", command=lambda: self.open_alexa35("alexa35-rec709")).grid(
            row=2, column=1, sticky="w", padx=2)
        self.movie_gamma = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Movie gamma (Alexa 35)", variable=self.movie_gamma,
                        command=self.convert).grid(row=2, column=2, columnspan=2, sticky="w")

        mid = ttk.Frame(self, padding=(6, 0))
        mid.pack(fill="x")
        ttk.Button(mid, text="Open image or RAW...", command=self.open_image).pack(side="left", padx=2)
        ttk.Button(mid, text="ColorChecker", command=self.use_colorchecker).pack(side="left", padx=2)
        ttk.Checkbutton(mid, text="image is already log-encoded", variable=self.image_is_log,
                        command=self.refresh).pack(side="left", padx=8)

        tools = ttk.Frame(self, padding=(6, 4))
        tools.pack(fill="x")
        ttk.Label(tools, text="Exposure").pack(side="left")
        self.ev_scale = ttk.Scale(tools, from_=-3, to=3, variable=self.ev, length=180,
                                  command=self._on_ev)
        self.ev_scale.pack(side="left", padx=4)
        self.ev_scale.bind("<Double-Button-1>", lambda _e: self.reset_ev())
        ttk.Label(tools, textvariable=self.ev_readout, width=8).pack(side="left")
        ttk.Button(tools, text="0.0", width=4, command=self.reset_ev).pack(side="left", padx=(0, 10))
        ttk.Label(tools, text="Split").pack(side="left")
        ttk.Scale(tools, from_=0, to=1, variable=self.split, length=120,
                  command=lambda v: self.draw()).pack(side="left", padx=4)
        ttk.Label(tools, text="Mode").pack(side="left", padx=(8, 2))
        mode = ttk.Combobox(tools, textvariable=self.split_mode, state="readonly", width=42, values=(
            "Source vs. ISP Sim", "Ideal .cube vs. ISP Sim",
            "ARRI Rec.709 vs. ARRI Wide Gamut"))
        mode.pack(side="left")
        mode.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        overlay = ttk.Frame(self, padding=(6, 0))
        overlay.pack(fill="x")
        ttk.Checkbutton(overlay, text="Overlay", variable=self.overlay_on,
                        command=self.draw).pack(side="left")
        overlay_mode = ttk.Combobox(overlay, textvariable=self.overlay_kind, state="readonly", width=24,
                                    values=("Off", "dE Heatmap", "5x9x9 Clamp Warning"))
        overlay_mode.pack(side="left", padx=4)
        overlay_mode.bind("<<ComboboxSelected>>", lambda e: self.draw())
        ttk.Label(overlay, text="Spatial / DCV:").pack(side="left", padx=(12, 2))
        spatial_cb = ttk.Combobox(overlay, textvariable=self.spatial_mode, state="readonly", width=32, values=(
            "Off (Color Only)",
            "Sony Stock (DCV 100%)",
            "ARRI Fix (DCV 25% + Chroma Smooth)",
            "ARRI Fix (DCV 0% Flat + Chroma Smooth)",
            "ARRI Full (AFA09T + Chroma Smooth)",
        ))
        spatial_cb.pack(side="left", padx=4)
        spatial_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(overlay, text="Zoom:").pack(side="left", padx=(12, 2))
        zoom_cb = ttk.Combobox(overlay, textvariable=self.zoom_mode, state="readonly", width=7,
                               values=("Fit", "50%", "100%", "200%", "400%", "800%"))
        zoom_cb.pack(side="left", padx=4)
        zoom_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_zoom_select())
        ttk.Button(overlay, text="Reset View", command=self.reset_view).pack(side="left", padx=4)

        self.view_note = tk.StringVar(value="Left: reference. Right: simulated 8-bit 4:2:0.")
        self.view_label = tk.Label(self, textvariable=self.view_note, justify="left", anchor="w",
                                   font=("Segoe UI", 9), wraplength=760)
        self.view_label.pack(fill="x", padx=8, pady=(4, 0))
        self._wrap(self.view_label)

        self.canvas = tk.Canvas(self, width=640, height=420, bg="#222", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self._canvas_size = (0, 0)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_step(1.25, e.x, e.y))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_step(0.8, e.x, e.y))
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_pan_end)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.reset_view())

        self.stats = tk.Label(self, text="", justify="left", anchor="w", font=("Segoe UI", 9), wraplength=760)
        self.stats.pack(fill="x", padx=8)
        self._wrap(self.stats)
        self.warn = tk.Label(self, text="", justify="left", anchor="w", fg="#b45309", wraplength=760)
        self.warn.pack(fill="x", padx=8)
        self._wrap(self.warn)
        self.note = tk.Label(self, text="", justify="left", anchor="w", fg="#444", wraplength=760)
        self.note.pack(fill="x", padx=8)
        self._wrap(self.note)

        bot = ttk.Frame(self, padding=6)
        bot.pack(fill="x")
        self.raw_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bot, text="also export raw tables (gamma/matrix/RefAdr/eSRAM .bin)",
                        variable=self.raw_var).pack(side="left")
        ttk.Button(bot, text="Export preset...", command=self.export).pack(side="right")
        self.use_colorchecker()

    @staticmethod
    def _wrap(label):
        def fit(event):
            label.configure(wraplength=max(240, event.width - 8))
        label.bind("<Configure>", fit)

    def _ev_value(self):
        return round(float(self.ev.get()) * 10) / 10

    def _on_ev(self, _value):
        snapped = self._ev_value()
        self.ev_readout.set(f"{snapped:+.1f} EV")
        if abs(snapped - self._ev_applied) < 1e-6:
            return
        if self._ev_job is not None:
            self.after_cancel(self._ev_job)
        self._ev_job = self.after(160, self.refresh)

    def reset_ev(self):
        if self._ev_job is not None:
            self.after_cancel(self._ev_job)
            self._ev_job = None
        self.ev.set(0.0)
        self.ev_readout.set("+0.0 EV")
        self._ev_applied = 0.0
        self.refresh()

    def _spatial_key(self):
        return {
            "Off (Color Only)": "off",
            "Sony Stock (DCV 100%)": "sony_stock",
            "ARRI Fix (DCV 25% + Chroma Smooth)": "arri_25pct",
            "ARRI Fix (DCV 0% Flat + Chroma Smooth)": "arri_00pct",
            "ARRI Full (AFA09T + Chroma Smooth)": "arri_full",
        }.get(self.spatial_mode.get(), "off")

    def _split_key(self):
        label = self.split_mode.get()
        if label.startswith("Source"):
            return "source"
        if label.startswith("ARRI"):
            return "arri"
        return "ideal"

    def _overlay_key(self):
        if not self.overlay_on.get():
            return "off"
        return {"Off": "off", "dE Heatmap": "de", "5x9x9 Clamp Warning": "clamp"}[self.overlay_kind.get()]

    @property
    def profile(self):
        profile = PROFILES[self.profile_name.get()]
        if isinstance(self.cube, ArriReference):
            profile = replace(profile, output_space=self.cube.output_space)
        if isinstance(self.cube, Alexa35Reference) and self.movie_gamma.get():
            profile = movie_profile(profile)
        return profile

    @property
    def video_range(self):
        return self.cube.video_range if isinstance(self.cube, ArriReference) else "legal"

    @property
    def interpolation(self):
        return self.cube.interpolation if isinstance(self.cube, ArriReference) else "trilinear"

    def open_logc3(self):
        self.cube = ArriReference()
        self.cube_path.set("arri-logc3-awg3")
        self.convert()

    def open_rec709(self):
        self.cube = ArriReference.rec709()
        self.cube_path.set("arri-rec709")
        self.convert()

    def open_alexa35(self, kind):
        path = filedialog.askopenfilename(title="Select official ARRI LogC4 Rec.709 display LUT",
                                          filetypes=[("3D LUT", "*.cube")])
        if not path:
            return
        try:
            self.cube = Alexa35Reference.from_lut(path, kind)
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot load ARRI LUT", str(error))
            return
        self.cube_path.set("arri-logc4-awg4" if kind == "logc4" else "alexa35-rec709")
        self.convert()

    def open_cube(self):
        p = filedialog.askopenfilename(filetypes=[("3D LUT", "*.cube"), ("All files", "*.*")])
        if not p:
            return
        try:
            self.cube = lut_core.load_cube(p)
        except Exception as e:
            messagebox.showerror("Cannot load .cube", str(e))
            return
        self.cube_path.set(os.path.basename(p))
        self.convert()

    def convert(self):
        if self.cube is None:
            return
        self.result = self._ref = self._app = None
        if isinstance(self.cube, ArriReference):
            self.source.set(SONY_SOURCE)
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            edge = self.cube.edge_compression if isinstance(self.cube, ArriReference) else 0.0
            self.result = lut_core.convert(self.cube, self.profile, self.source.get(),
                                           video_range=self.video_range, edge_compression=edge,
                                           interpolation=self.interpolation)
        except Exception as e:
            self.stats.config(text="")
            self.warn.config(text="")
            self.draw()
            messagebox.showerror("Conversion failed", str(e))
            return
        finally:
            self.config(cursor="")
        m, r = self.result.metrics, self.result.report
        typical = m["typical"] or m["grid"]
        metric = "Delta E00" if isinstance(self.cube, Alexa35Reference) else "Delta E76"
        self.stats.config(text=(
            f"{metric} typical colours: mean {typical['mean']:.2f}  p95 {typical['p95']:.2f}  "
            f"max {typical['max']:.2f}   |   ColorChecker: mean {m['colorchecker']['mean']:.2f}  "
            f"max {m['colorchecker']['max']:.2f}\n"
            f"Skin mean {m['skin_tones']['mean']:.2f} max {m['skin_tones']['max']:.2f}   |   "
            f"Highlights mean {m['saturated_highlights']['mean']:.2f} max {m['saturated_highlights']['max']:.2f}\n"
            f"Delta clipping: {r['clamped_nodes_pct']:.1f}% of nodes ({r['clamped_nodes']}/{r['nodes']}), "
            f"{r['at_rail_nodes_pct']:.1f}% exported on a signed rail   |   "
            f"full-cube dE mean {m['grid']['mean']:.1f}"))
        self._set_view_note()
        self.warn.config(text="\n".join("WARNING: " + w for w in self.result.warnings))
        self.refresh()

    def _exposure_line(self):
        ev = self._ev_value()
        gain = 2.0 ** ev
        combined = self.base_exposure_scale * gain
        return (f"Exposure {ev:+.1f} EV. Base scale {self.base_exposure_scale:.2f} "
                f"x {gain:.3f} = {combined:.2f}.")

    def _set_view_note(self):
        mode = self._split_key()
        if mode == "source":
            text = "Left: exposed source as sRGB. Right: ISP simulation, 8-bit 4:2:0."
        elif mode == "arri":
            text = ("Left: ARRI Rec.709 ISP. Right: ARRI Wide Gamut ISP. "
                    "Wide gamut stays LogC3 and is shown through the same Rec.709 view.")
        elif isinstance(self.cube, Alexa35Reference):
            text = "Left: ideal Alexa 35 reference. Right: ISP simulation, 8-bit 4:2:0. Both use the same Rec.709 view."
        elif isinstance(self.cube, ArriReference) and self.cube.kind == "wide-gamut":
            text = ("Left: ideal reference. Right: ISP simulation. "
                    "Wide gamut stays LogC3. Both sides use the built-in Rec.709 view.")
        elif isinstance(self.cube, ArriReference):
            text = "Left: ideal Rec.709 reference. Right: ISP simulation. Gamma 2.4 is converted to sRGB."
        else:
            text = "Left: ideal .cube reference. Right: ISP simulation, 8-bit 4:2:0. Both sides share one view."
        text += " " + self._exposure_line()
        frame = self.frame_report
        if frame is not None:
            metric = "Delta E00" if isinstance(self.cube, Alexa35Reference) and mode == "ideal" else "CIE76"
            text += f" This frame {metric} mean {frame['mean']:.2f} max {frame['max']:.2f}."
        if self.cube_report is not None and mode != "ideal":
            metric = "Delta E00" if isinstance(self.cube, Alexa35Reference) and mode != "arri" else "CIE76"
            text += (f" Cube vs ISP {metric} mean {self.cube_report['mean']:.2f} "
                     f"max {self.cube_report['max']:.2f}.")
        self.view_note.set(text)

    def open_image(self):
        p = filedialog.askopenfilename(filetypes=[
            ("Images and camera raw", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.arw *.dng *.nef *.cr2 *.cr3 *.raf"),
            ("All files", "*.*"),
        ])
        if not p:
            return
        try:
            loaded = load_image(p, MAX_PREVIEW)
        except (OSError, ValueError, TypeError) as error:
            messagebox.showerror("Cannot open image", str(error))
            return
        self.image_is_log.set(False)
        self.image_encoding = loaded.encoding
        self.base_exposure_scale = loaded.exposure_scale
        self.image_note = loaded.note
        self.image = loaded.rgb
        self.note.config(text=loaded.note)
        self.reset_view(redraw=False)
        self.refresh()

    def use_colorchecker(self):
        self.image_is_log.set(False)
        self.image_encoding = "srgb"
        self.base_exposure_scale = 1.0
        self.image_note = "ColorChecker patches are sRGB values, inverse-encoded into the selected source log."
        self.image = colorchecker_image()
        self.note.config(text=self.image_note)
        self.refresh()

    def _encoding(self):
        return "log" if self.image_is_log.get() else self.image_encoding

    def _show_note(self):
        note = self.image_note
        if self.image_is_log.get() and self.image_encoding != "log":
            note = "The log checkbox overrides the file and reads these values as source log codes. " + note
        note = f"{note} {self._exposure_line()}".strip()
        self.note.config(text=note)

    def _arri_bundle(self, kind):
        key = (kind, self.profile_name.get())
        if key in self._arri_cache:
            return self._arri_cache[key]
        ref = ArriReference.rec709() if kind == "rec709" else ArriReference()
        if (isinstance(self.cube, ArriReference) and self.cube.classic is None
                and self.cube.kind == ref.kind and self.result is not None):
            bundle = (ref, self.result.preset, self.profile, self.video_range)
        else:
            profile = replace(PROFILES[self.profile_name.get()], output_space=ref.output_space)
            fitted = lut_core.convert(ref, profile, SONY_SOURCE, video_range=ref.video_range,
                                      edge_compression=ref.edge_compression, interpolation=ref.interpolation)
            bundle = (ref, fitted.preset, profile, ref.video_range)
        self._arri_cache[key] = bundle
        return bundle

    def refresh(self):
        self._ev_job = None
        self._ev_applied = self._ev_value()
        self._show_note()
        if self.image is None or (self._split_key() != "arri" and (self.cube is None or self.result is None)):
            self.frame_report = self.cube_report = None
            self._views = None
            self._ref = self._app = None
            self._set_view_note()
            self.draw()
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            rec709 = wide = None
            if self._split_key() == "arri":
                rec709 = self._arri_bundle("rec709")
                wide = self._arri_bundle("wide-gamut")
                cube, preset, profile, video_range = wide
                interpolation = cube.interpolation
                source_name = SONY_SOURCE
            else:
                cube, preset, profile, video_range = self.cube, self.result.preset, self.profile, self.video_range
                interpolation = self.interpolation
                source_name = self.source.get()
            self._views = compose_preview(
                self._split_key(), cube, preset, profile, source_name, self.image,
                encoding=self._encoding(), video_range=video_range, interpolation=interpolation,
                ev=self._ev_value(), exposure_scale=self.base_exposure_scale,
                rec709=rec709, wide=wide, spatial_mode=self._spatial_key())
            self._ref = self._views["left"]
            self._app = self._views["right"]
            self.cube_report = self._views["cube_report"]
            self.frame_report = self.cube_report if self._split_key() == "ideal" else self._views["split_report"]
        except Exception as error:
            self._views = None
            self._ref = self._app = None
            self.frame_report = self.cube_report = None
            messagebox.showerror("Preview failed", str(error))
        finally:
            self.config(cursor="")
        self._set_view_note()
        self.draw()

    def _on_canvas_resize(self, event):
        size = (event.width, event.height)
        if size == self._canvas_size or event.width < 20 or event.height < 20:
            return
        self._canvas_size = size
        if self._ref is not None or self.image is not None:
            self.draw()

    def reset_view(self, redraw=True):
        """Reset zoom to Fit and center pan."""
        self._zoom_scale = None
        self._pan_x = 0.5
        self._pan_y = 0.5
        self.zoom_mode.set("Fit")
        if redraw:
            self.draw()

    def _on_zoom_select(self):
        label = self.zoom_mode.get()
        if label == "Fit":
            self._zoom_scale = None
            self._pan_x = 0.5
            self._pan_y = 0.5
        else:
            self._zoom_scale = float(label.rstrip("%")) / 100.0
        self.draw()

    def _on_mousewheel(self, event):
        if event.delta == 0:
            return
        factor = 1.25 if event.delta > 0 else 0.8
        self._zoom_step(factor, event.x, event.y)

    def _zoom_step(self, factor, cx, cy):
        if self._last_view is None:
            return
        img_w, img_h, old_scale, src_x0, src_y0, canvas_x, canvas_y, cw, ch = self._last_view
        # Image coordinate currently under cursor (cx, cy).
        img_x = src_x0 + (cx - canvas_x) / old_scale
        img_y = src_y0 + (cy - canvas_y) / old_scale
        new_scale = max(0.05, min(16.0, old_scale * factor))
        self._zoom_scale = new_scale
        self.zoom_mode.set(f"{int(round(new_scale * 100))}%")
        if 0 <= img_x <= img_w and 0 <= img_y <= img_h:
            new_cx = img_x - (cx - cw / 2.0) / new_scale
            new_cy = img_y - (cy - ch / 2.0) / new_scale
            self._pan_x = max(0.0, min(1.0, new_cx / img_w))
            self._pan_y = max(0.0, min(1.0, new_cy / img_h))
        self.draw()

    def _on_pan_start(self, event):
        self._drag_start = (event.x, event.y, self._pan_x, self._pan_y)

    def _on_pan_move(self, event):
        if self._drag_start is None or self._last_view is None:
            return
        sx, sy, px0, py0 = self._drag_start
        img_w, img_h, scale, *_ = self._last_view
        dx = (event.x - sx) / (scale * img_w)
        dy = (event.y - sy) / (scale * img_h)
        self._pan_x = max(0.0, min(1.0, px0 - dx))
        self._pan_y = max(0.0, min(1.0, py0 - dy))
        self.draw()

    def _on_pan_end(self, _event):
        self._drag_start = None

    def _show(self, rgb, split_x_frac=None):
        h, w = rgb.shape[:2]
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        fit_scale = min(cw / w, ch / h) if (cw > 20 and ch > 20) else min(640.0 / w, 420.0 / h)
        scale = fit_scale if self._zoom_scale is None else self._zoom_scale

        vis_w = cw / scale
        vis_h = ch / scale
        if vis_w >= w:
            src_x0 = 0.0
            ix0, ix1 = 0, w
            self._pan_x = 0.5
            disp_w = max(1, int(round(w * scale)))
            canvas_x = max(0, (cw - disp_w) // 2)
        else:
            src_x0 = max(0.0, min(w - vis_w, self._pan_x * w - vis_w / 2.0))
            ix0 = int(math.floor(src_x0))
            ix1 = min(w, int(math.ceil(src_x0 + vis_w)))
            self._pan_x = (src_x0 + vis_w / 2.0) / w
            disp_w = max(1, int(round((ix1 - ix0) * scale)))
            canvas_x = int(round((ix0 - src_x0) * scale))

        if vis_h >= h:
            src_y0 = 0.0
            iy0, iy1 = 0, h
            self._pan_y = 0.5
            disp_h = max(1, int(round(h * scale)))
            canvas_y = max(0, (ch - disp_h) // 2)
        else:
            src_y0 = max(0.0, min(h - vis_h, self._pan_y * h - vis_h / 2.0))
            iy0 = int(math.floor(src_y0))
            iy1 = min(h, int(math.ceil(src_y0 + vis_h)))
            self._pan_y = (src_y0 + vis_h / 2.0) / h
            disp_h = max(1, int(round((iy1 - iy0) * scale)))
            canvas_y = int(round((iy0 - src_y0) * scale))

        crop = rgb[iy0:iy1, ix0:ix1]
        im = Image.fromarray(np.clip(np.rint(np.clip(crop, 0, 1) * 255), 0, 255).astype(np.uint8))
        if (disp_w, disp_h) != im.size:
            resample = Image.Resampling.NEAREST if scale >= 1.5 else Image.Resampling.BILINEAR
            im = im.resize((disp_w, disp_h), resample)
        self._photo = ImageTk.PhotoImage(im)
        self.canvas.delete("all")
        self.canvas.create_image(canvas_x, canvas_y, anchor="nw", image=self._photo)
        self._last_view = (w, h, scale, float(ix0), float(iy0), canvas_x, canvas_y, cw, ch)

        if split_x_frac is not None:
            k = int(w * split_x_frac)
            split_canvas_x = canvas_x + (k - ix0) * scale
            if 0 <= split_canvas_x <= cw:
                y0 = max(0, canvas_y)
                y1 = min(ch, canvas_y + disp_h)
                self.canvas.create_line(split_canvas_x, y0, split_canvas_x, y1, fill="white")
        return im, canvas_x, canvas_y

    def draw(self):
        if self._ref is None:
            if self.image is not None:
                self._show(self.image)
                self.canvas.create_text(10, 10, anchor="nw", fill="white",
                                        text="Choose ARRI Wide Gamut, ARRI Rec.709, or a .cube")
            else:
                self.canvas.delete("all")
                self._last_view = None
            return
        h, w, _ = self._ref.shape
        k = int(w * self.split.get())
        out = self._app.copy()
        out[:, :k] = self._ref[:, :k]
        if self._views is not None:
            out = apply_overlay(out, self._overlay_key(), self._views["cube_delta"],
                                self._views["clamp"], self.split.get())
        self._show(out, split_x_frac=self.split.get())

    def export(self):
        if self.result is None:
            messagebox.showinfo("Nothing to export", "Load a .cube first.")
            return
        base = os.path.splitext(self.cube_path.get())[0]
        if isinstance(self.cube, Alexa35Reference):
            from lut_cli.__main__ import _export
            from pathlib import Path

            if self.profile.gamma_positions:
                base += "-movie"
            path = filedialog.askdirectory(title="Export Alexa 35 preset and report")
            if path:
                try:
                    _export(Path(path) / base, self.result, self.profile, self.raw_var.get(),
                            {**self.cube.provenance(), "source": self.source.get()})
                except (OSError, ValueError) as error:
                    messagebox.showerror("Export failed", str(error))
                    return
                messagebox.showinfo("Exported", path)
            return
        p = filedialog.asksaveasfilename(defaultextension=".a6lt", initialfile=base + ".a6lt",
                                         filetypes=[("a6400 preset", "*.a6lt")])
        if not p:
            return
        with open(p, "wb") as f:
            f.write(pack.pack_preset(self.result.preset, self.profile))
        if self.raw_var.get():
            stem = os.path.splitext(p)[0]
            for ext, blob in (("gamma", pack.pack_gamma(self.result.preset)),
                              ("matrix", pack.pack_matrix(self.result.preset)),
                              ("refadr", pack.pack_refadr(self.result.preset)),
                              ("esram", pack.pack_esram(self.result.preset, self.profile))):
                with open(f"{stem}.{ext}.bin", "wb") as f:
                    f.write(blob)
        messagebox.showinfo("Exported", p)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
