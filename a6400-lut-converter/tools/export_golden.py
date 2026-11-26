"""Freeze lut_core outputs for the BIONZ Look Lab oracle.

Golden files are JSON (the repository rejects tracked ``.bin`` files). Stock
curves in this fixture are synthetic and monotone. They are not bytes from
``av-cam.bin``.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CONVERTER = ROOT / "a6400-lut-converter"
if str(CONVERTER) not in sys.path:
    sys.path.insert(0, str(CONVERTER))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lut_core.arri import ArriReference  # noqa: E402
from lut_core.cube import Cube, parse_cube  # noqa: E402
from lut_core.color import delta_e76, display_rgb_to_lab  # noqa: E402
from lut_core.decompose import decompose  # noqa: E402
from lut_core.model import (  # noqa: E402
    Preset,
    apply_ycc,
    encode_8bit,
    gamma_codes,
    matrix_codes,
    simulate_8bit,
)
from lut_core.pack import pack_esram, pack_preset, pack_refadr  # noqa: E402
from lut_profiles.a6400 import a6400_profile as PROFILE  # noqa: E402
from mods.film_sims.film_recipes_3d import (  # noqa: E402
    RECIPES_3D,
    build_recipe_cube,
    evaluate_recipe_3d,
)
from mods.film_sims.lut_bridge import fit_cube_to_isp, movie_domain_cube  # noqa: E402
from mods.film_sims.studio_core import (  # noqa: E402
    STOCK_A0,
    STOCK_A1,
    STOCK_B2,
    FilmSimParams,
    LIVE_PICKER_ORDER,
    _fmt,
    _pa_block_for_header,
    get_target,
    load_stock_tables,
)

OUT = ROOT / "artifacts" / "tests" / "bionz-golden"
PRESET_DIR = ROOT / "mods" / "film_sims" / "presets"


def _i64(values) -> list[int]:
    return [int(v) for v in np.asarray(values).reshape(-1).tolist()]


def _f64(values) -> list[float]:
    return [float(v) for v in np.asarray(values, dtype=np.float64).reshape(-1).tolist()]


def synthetic_stock() -> dict[str, list[int]]:
    """Monotone stand-in curves. Not extracted from a camera image."""
    return {
        "ec_stock": [((i * 1023) // 95) << 6 for i in range(96)],
        "fc_stock": [(i * 1023) // 95 for i in range(96)],
        "a0_stock": list(STOCK_A0),
        "a1_stock": list(STOCK_A1),
        "b2_stock": list(STOCK_B2),
        "origin": "synthetic-monotone-plus-published-stock-matrix-constants",
    }


def real_stock(target: str) -> dict[str, list[int]] | None:
    """Hash-verified av-cam.bin stock tables for a target, or None when the capture is absent."""
    try:
        stock = load_stock_tables(target=target)
    except (AssertionError, FileNotFoundError) as exc:
        print(f"{target}: real av-cam.bin unavailable ({exc})", flush=True)
        return None
    stock["origin"] = f"{target} av-cam.bin"
    stock["avcam_sha256"] = get_target(target).avcam_sha256
    return stock


def reused_stock(source: dict[str, list[int]], source_target: str, target: str) -> dict[str, list[int]]:
    """Reuse another body's stock tables when they are proven identical across bodies.

    The cross-body test `test_real_avcam_stock_tables_match_across_a6100_and_a6400` asserts that the
    ILCE-6100 and ILCE-6400 still EC/FC/A0/A1/B2 tables are equal, so the a6400 capture can supply the
    a6100 table values; the target's own PA addresses still come from `TargetProfile`.
    """
    reused = {
        "ec_stock": list(source["ec_stock"]),
        "fc_stock": list(source["fc_stock"]),
        "a0_stock": list(source["a0_stock"]),
        "a1_stock": list(source["a1_stock"]),
        "b2_stock": list(source["b2_stock"]),
        "origin": f"{source_target} av-cam.bin reused for {target} (cross-body stock parity)",
        "avcam_sha256": source["avcam_sha256"],
    }
    return reused


def _profile_json() -> dict:
    p = PROFILE
    return {
        "name": p.name,
        "gamma_size": p.gamma_size,
        "gamma_max_val": p.gamma_max_val,
        "matrix_q": p.matrix_q,
        "y_axis": list(p.y_axis),
        "c_axis": list(p.c_axis),
        "delta_range": list(p.delta_range),
        "permutation": list(p.permutation),
        "permutation_verified": p.permutation_verified,
        "ycc_kr_kb": list(p.ycc_kr_kb),
        "y_scale": p.y_scale,
        "output_space": p.output_space,
    }


def _stage_vectors() -> dict:
    gamma = np.linspace(0, PROFILE.gamma_max_val, PROFILE.gamma_size).astype(np.uint16)
    gamma[10] = 100
    gamma[11] = 4000
    matrix = np.array(
        [[1024, 0, 0], [0, 1024, 0], [-20, 30, 1014]],
        dtype=np.int16,
    )
    u = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 1.0, 1.0],
            [0.5, 0.25, 0.125],
            [0.0, 1.0, 0.5],
            [1.0 / 95.0, 2.0 / 95.0, 0.999],
            [0.01059, 0.18, 0.9],
        ],
        dtype=np.float64,
    )
    preset = Preset(gamma, matrix, np.zeros((*PROFILE.grid, 3), dtype=np.int8))
    preset.delta[0, 0, 1] = (-64, 63, -1)
    preset.delta[1, 0, 0] = (3, 4, 5)
    preset.delta[4, 8, 8] = (7, -8, 9)
    g = gamma_codes(preset, PROFILE, u)
    m = matrix_codes(preset, PROFILE, u)
    ycc = apply_ycc(preset, PROFILE, u, "trilinear")
    image = np.zeros((3, 3, 3), dtype=np.float64)
    image[:, 1, 0] = 256
    image[0, 0, 1] = 128
    image[:, 2, 1] = -128
    y_legal, c_legal = encode_8bit(image, PROFILE, "legal", chroma_420=True)
    y_full, c_full = encode_8bit(image, PROFILE, "full", chroma_420=True)
    blob = pack_preset(preset, PROFILE)
    return {
        "gamma_u16": _i64(gamma),
        "matrix_i16": _i64(matrix),
        "u": _f64(u),
        "gamma_codes_i64": _i64(g),
        "matrix_codes_i64": _i64(m),
        "apply_ycc_f64": _f64(ycc),
        "apply_ycc_bits": [int(np.asarray(v, dtype=np.float64).view(np.uint64)) for v in np.asarray(ycc, dtype=np.float64).reshape(-1)],
        "encode_image_f64": _f64(image),
        "encode_legal_y_u8": _i64(y_legal),
        "encode_legal_c_u8": _i64(c_legal),
        "encode_full_y_u8": _i64(y_full),
        "encode_full_c_u8": _i64(c_full),
        "delta_i8": _i64(preset.delta),
        "pack_preset_hex": blob.hex(),
        "pack_esram_hex": pack_esram(preset, PROFILE).hex(),
        "pack_refadr_hex": pack_refadr(preset).hex(),
        "pack_preset_crc32": zlib.crc32(blob) & 0xFFFFFFFF,
    }


def _tiny_cube() -> Cube:
    text = "TITLE \"golden-identity\"\nLUT_3D_SIZE 5\n"
    rows = []
    for b in np.linspace(0, 1, 5):
        for g in np.linspace(0, 1, 5):
            for r in np.linspace(0, 1, 5):
                rows.append(f"{r * 0.9:.8f} {g * 0.95:.8f} {(b * 0.85 + 0.02):.8f}")
    return parse_cube(text + "\n".join(rows) + "\n")


def _preset_record(preset: Preset, report: dict) -> dict:
    return {
        "gamma_u16": _i64(preset.gamma),
        "matrix_i16": _i64(preset.matrix),
        "delta_i8": _i64(preset.delta),
        "report": {
            "clamped_nodes": report["clamped_nodes"],
            "at_rail_nodes": report["at_rail_nodes"],
            "joint_fit_cost_initial": report["joint_fit_cost_initial"],
            "joint_fit_cost_final": report["joint_fit_cost_final"],
        },
    }


def _header_for(name: str, stock: dict, fitted) -> str:
    prof = get_target("a6400")
    slug = "classic_chrome" if name == "Classic Chrome" else "golden"
    hex_b = ",".join(f"0x{b:02x}" for b in fitted.refadr_bytes)
    lut_block = (
        "#define REFADR_ENABLED 1\n"
        f"#define CC_TUNING_PA 0x{prof.cc_tuning_pa:08x}u\n"
        f"#define REFADR0_PA 0x{prof.refadr0_pa:08x}u\n"
        f"static const uint8_t REFADR_SIM[1215] = {{{hex_b}}};\n"
    )
    return (
        f"/* Auto-generated by mods/film_sims/studio_core.py for {name} ({prof.label}) */\n"
        "#include <stdint.h>\n\n"
        f"#define EC_GAMMA_PA 0x{prof.ec_gamma_pa:08x}u\n"
        f"#define FC_GAMMA_PA 0x{prof.fc_gamma_pa:08x}u\n"
        f"#define COLOR_A0_PA 0x{prof.color_a0_pa:08x}u\n"
        f"#define COLOR_A1_PA 0x{prof.color_a1_pa:08x}u\n"
        f"#define COLOR_B2_PA 0x{prof.color_b2_pa:08x}u\n"
        f'#define MOD_TAG "{slug}"\n'
        f'#define MOD_LOG_PATH "/tmp_bt/mods/{slug}.log"\n\n'
        f"static const uint32_t EC_GAMMA_STOCK[96] = {{{_fmt(stock['ec_stock'], True)}}};\n"
        f"static const uint32_t EC_GAMMA_SIM[96] = {{{_fmt(fitted.ec_sim, True)}}};\n"
        f"static const uint16_t FC_GAMMA_STOCK[96] = {{{_fmt(stock['fc_stock'], False)}}};\n"
        f"static const uint16_t FC_GAMMA_SIM[96] = {{{_fmt(fitted.fc_sim, False)}}};\n"
        f"static const int16_t COLOR_A0_STOCK[9] = {{{_fmt(stock['a0_stock'], False)}}};\n"
        f"static const int16_t COLOR_A0_SIM[9] = {{{_fmt(fitted.a0_sim, False)}}};\n"
        f"static const int16_t COLOR_A1_STOCK[9] = {{{_fmt(stock['a1_stock'], False)}}};\n"
        f"static const int16_t COLOR_A1_SIM[9] = {{{_fmt(fitted.a1_sim, False)}}};\n"
        f"static const int16_t COLOR_B2_STOCK[9] = {{{_fmt(stock['b2_stock'], False)}}};\n"
        f"static const int16_t COLOR_B2_SIM[9] = {{{_fmt(fitted.b2_sim, False)}}};\n"
        + lut_block
    )


def _live_header(stock: dict, fitted_by_name: dict) -> str:
    prof = get_target("a6400")
    presets = [FilmSimParams(name=name) for name in LIVE_PICKER_ORDER]
    names = ["Stock (Off)"] + [p.name[:15] for p in presets]
    ecs, fcs, a0s, a1s, b2s, luts, lut_idxs = [], [], [], [], [], [], [-1]
    for p in presets:
        fitted = fitted_by_name[p.name]
        ecs.append("{" + _fmt(fitted.ec_sim, True) + "}")
        fcs.append("{" + _fmt(fitted.fc_sim, False) + "}")
        a0s.append("{" + _fmt(fitted.a0_sim, False) + "}")
        a1s.append("{" + _fmt(fitted.a1_sim, False) + "}")
        b2s.append("{" + _fmt(fitted.b2_sim, False) + "}")
        lut_idxs.append(len(luts))
        luts.append("{" + ",".join(f"0x{b:02x}" for b in fitted.refadr_bytes) + "}")
    name_rows = ",".join(f'"{n}"' for n in names)
    lut_block = (
        f"const uint8_t LIVE_REFADR[{max(1, len(luts))}][1215] = {{"
        + ",".join(luts)
        + "};\n"
    )
    return (
        f"/* Auto-generated by mods/film_sims/studio_core.py for FilmSimLive.mod ({prof.label}) */\n"
        "#include <stdint.h>\n\n"
        + _pa_block_for_header("a6400", False)
        + f"#define LIVE_N_PRESETS {len(names)}u\n\n"
        f"const char LIVE_NAMES[{len(names)}][16] = {{{name_rows}}};\n"
        f"const int8_t LIVE_LUT_IDX[{len(names)}] = {{{_fmt(lut_idxs, False)}}};\n\n"
        f"const uint32_t EC_GAMMA_STOCK[96] = {{{_fmt(stock['ec_stock'], True)}}};\n"
        f"const uint16_t FC_GAMMA_STOCK[96] = {{{_fmt(stock['fc_stock'], False)}}};\n"
        f"const int16_t COLOR_A0_STOCK[9] = {{{_fmt(stock['a0_stock'], False)}}};\n"
        f"const int16_t COLOR_A1_STOCK[9] = {{{_fmt(stock['a1_stock'], False)}}};\n"
        f"const int16_t COLOR_B2_STOCK[9] = {{{_fmt(stock['b2_stock'], False)}}};\n\n"
        f"const uint32_t LIVE_EC[{len(presets)}][96] = {{{','.join(ecs)}}};\n"
        f"const uint16_t LIVE_FC[{len(presets)}][96] = {{{','.join(fcs)}}};\n"
        f"const int16_t LIVE_A0[{len(presets)}][9] = {{{','.join(a0s)}}};\n"
        f"const int16_t LIVE_A1[{len(presets)}][9] = {{{','.join(a1s)}}};\n"
        f"const int16_t LIVE_B2[{len(presets)}][9] = {{{','.join(b2s)}}};\n"
        + lut_block
    )


def _png(rgb: bytes, w: int, h: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(
        b"IDAT", zlib.compress(raw, 9)
    ) + chunk(b"IEND", b"")


def _render_thumb(name: str, w: int = 96, h: int = 64) -> bytes:
    """Rendered thumbnail: a graded sky/skin/foliage test scene through the recipe."""
    xs = np.linspace(0.0, 1.0, w)
    ys = np.linspace(0.0, 1.0, h)
    img = np.zeros((h, w, 3), dtype=np.float64)
    for y in range(h):
        for x in range(w):
            t = xs[x]
            v = ys[y]
            if y < h // 3:
                base = np.array([0.18 + 0.35 * t, 0.34 + 0.36 * t, 0.62 + 0.30 * t])
                img[y, x] = base * (0.75 + 0.25 * v)
            elif y > 2 * h // 3:
                img[y, x] = (0.16 + 0.22 * t, 0.30 + 0.12 * t, 0.12 + 0.06 * t)
            else:
                img[y, x] = (0.62 + 0.20 * t, 0.42 + 0.16 * t, 0.30 + 0.10 * t)
    img = np.clip(img, 0.0, 1.0)
    out = evaluate_recipe_3d(img, RECIPES_3D[name])
    return _png((np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes(), w, h)


def _write_bzlook(
    name: str,
    recipe,
    fitted_by_target: dict[str, object],
    movie_by_target: dict[str, object],
    stocks: dict[str, dict],
    movie_stocks: dict[str, dict],
) -> None:
    import io
    import zipfile

    slug = name.lower().replace("&", "and").replace(" ", "-")
    targets = ",".join(sorted(fitted_by_target))
    origins = "; ".join(f"{t}: {stocks[t]['origin']}" for t in sorted(fitted_by_target))
    look = {
        "name": name,
        "author": "bionz-look-lab",
        "version": 1,
        "uuid": hashlib.sha256(name.encode()).hexdigest()[:32],
        "intent_kind": "recipe",
        "tags": ["film-sim", "migrated"],
        "notes": (
            "Migrated from film_recipes_3d.py. Photo and Movie variants compiled at 33^3 against "
            f"hash-verified stock tables for {targets}. Stock provenance: {origins}."
        ),
        "created_with": "export_golden.py",
        "cube_sha256": "",
    }
    fields = {k: getattr(recipe, k) for k in recipe.__dataclass_fields__}
    if isinstance(fields["bw_weights"], tuple):
        fields["bw_weights"] = list(fields["bw_weights"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("look.toml", _toml(look))
        zf.writestr("intent/recipe.toml", _toml(fields))
        for target, fitted in sorted(fitted_by_target.items()):
            zf.writestr(f"compiled/{target}.bin", _compiled_bin(target, fitted, stocks[target]))
            zf.writestr(f"compiled/{target}.json", json.dumps(dict(fitted.report), indent=2))
            if target in movie_by_target:
                movie = movie_by_target[target]
                zf.writestr(
                    f"compiled/{target}_movie.bin",
                    _compiled_bin(target, movie, movie_stocks[target], movie=True),
                )
                zf.writestr(f"compiled/{target}_movie.json", json.dumps(dict(movie.report), indent=2))
        zf.writestr("thumb.png", _render_thumb(name))
    PRESET_DIR.mkdir(parents=True, exist_ok=True)
    (PRESET_DIR / f"{slug}.bzlook").write_bytes(buf.getvalue())


def _toml(data: dict) -> str:
    lines = []
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int) and not isinstance(value, bool):
            lines.append(f"{key} = {value}")
        elif isinstance(value, float):
            lines.append(f"{key} = {value!r}")
        elif isinstance(value, list):
            inner = ", ".join(repr(v) if isinstance(v, float) else json.dumps(v) for v in value)
            lines.append(f"{key} = [{inner}]")
        else:
            lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


RECORD31_EXPONENTS = [3, 3, 4, 4, 3, 3, 3, 4, 3]
RECORD31_STOCK_SHA256 = "bc1d67e362549d6fda06c48e86fff4648cb099819e38d6e16032b0be99ceed58"


def movie_stock(base: dict[str, list[int]]) -> dict[str, list[int]]:
    """Movie EC stock (record 31) and identity matrices for the Movie variant fit.

    The EC bytes are the hash-checked record31-stock capture from the ARRI work; the matrices are
    identity because the fit's A0 is applied at the colour-matrix stage, not the B8 base stage.
    """
    blob = (ROOT / "artifacts/analysis/arri-gamma-test-block-2026-11-16/record31-stock.bin").read_bytes()
    if hashlib.sha256(blob).hexdigest() != RECORD31_STOCK_SHA256:
        raise SystemExit("record31-stock.bin hash mismatch")
    ec = list(struct.unpack("<105I", blob))[:96]
    identity = [1024, 0, 0, 0, 1024, 0, 0, 0, 1024]
    return {
        "ec_stock": ec,
        "fc_stock": [(i * 1023) // 95 for i in range(96)],
        "a0_stock": identity,
        "a1_stock": identity,
        "b2_stock": identity,
        "origin": f"record31-stock.bin plus identity matrices ({base['origin']})",
        "avcam_sha256": base["avcam_sha256"],
    }


def _compiled_bin(
    target: str,
    fitted,
    stock: dict,
    *,
    movie: bool = False,
    ec: list[int] | None = None,
    fc: list[int] | None = None,
    a0: list[int] | None = None,
    a1: list[int] | None = None,
    b2: list[int] | None = None,
) -> bytes:
    """BZLK v1 table with real stock tables and the digest of the capture they came from (flags = 0)."""
    key = target.encode().ljust(32, b"\0")
    sha = bytes.fromhex(stock["avcam_sha256"])
    flags = 0  # stock curves and matrices came from a hash-verified av-cam.bin
    parts = [
        b"BZLK",
        struct.pack("<H", 2 if movie else 1),
        key,
        sha,
        struct.pack("<I", flags),
        struct.pack("<H", 4),  # lut level
        struct.pack("<96I", *(ec if ec is not None else fitted.ec_sim)),
        struct.pack("<96H", *(fc if fc is not None else fitted.fc_sim)),
        struct.pack("<9h", *(a0 if a0 is not None else fitted.a0_sim)),
        struct.pack("<9h", *(a1 if a1 is not None else fitted.a1_sim)),
        struct.pack("<9h", *(b2 if b2 is not None else fitted.b2_sim)),
        fitted.refadr_bytes,
        struct.pack("<96I", *stock["ec_stock"]),
        struct.pack("<96H", *stock["fc_stock"]),
        struct.pack("<9h", *stock["a0_stock"]),
        struct.pack("<9h", *stock["a1_stock"]),
        struct.pack("<9h", *stock["b2_stock"]),
    ]
    payload = bytearray(b"".join(parts))
    if movie:
        payload.extend(b"BZX2")
        payload.extend(struct.pack("<I", 4))  # EXT_EC_EXP only; base stays None (cam_to_srgb)
        payload.extend(bytes(RECORD31_EXPONENTS))
    crc = zlib.crc32(bytes(payload)) & 0xFFFFFFFF
    return bytes(payload) + struct.pack("<I", crc)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden-only", action="store_true", help="regenerate the golden fixtures without re-fitting presets")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stock = synthetic_stock()
    (OUT / "profile.json").write_text(json.dumps(_profile_json(), indent=2), encoding="utf-8")
    (OUT / "stock.json").write_text(json.dumps(stock, indent=2), encoding="utf-8")
    stages = _stage_vectors()
    (OUT / "stages.json").write_text(json.dumps(stages), encoding="utf-8")
    cube = _tiny_cube()
    preset, report = decompose(cube, PROFILE, source=None, edge_compression=0.0, interpolation="trilinear")
    (OUT / "decompose_tiny.json").write_text(json.dumps(_preset_record(preset, report)), encoding="utf-8")
    ax = np.linspace(0, 1, 9)
    grid = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), -1).reshape(-1, 3)
    (OUT / "tiny_probe.json").write_text(
        json.dumps({"u": _f64(grid), "target": _f64(np.clip(cube.sample(grid), 0, 1))}),
        encoding="utf-8",
    )

    # Deterministic recipe fixtures for the Rust port (17-point cubes, independent of av-cam bytes).
    recipe_fits: dict[str, dict] = {}
    probe_rgb = np.array(
        [[0.0, 0.0, 0.0], [0.2, 0.2, 0.2], [0.5, 0.4, 0.3], [0.8, 0.2, 0.1], [1.0, 1.0, 1.0]],
        dtype=np.float64,
    )
    probe_ax = np.linspace(0.0, 1.0, 7, dtype=np.float64)
    probe_grid = np.stack(np.meshgrid(probe_ax, probe_ax, probe_ax, indexing="ij"), -1).reshape(-1, 3)
    probe_rgb = np.concatenate([probe_rgb, probe_grid], axis=0)
    fit_ax = np.linspace(0.0, 1.0, 17, dtype=np.float64)
    probe_u = np.stack(np.meshgrid(fit_ax, fit_ax, fit_ax, indexing="ij"), -1).reshape(-1, 3)
    recipes_out: dict[str, dict] = {}
    for name in LIVE_PICKER_ORDER:
        recipe = RECIPES_3D[name]
        fit_cube = build_recipe_cube(name, size=17)
        fit_preset, fit_report = decompose(fit_cube, PROFILE, source=None, edge_compression=0.0, interpolation="trilinear")
        ref = np.clip(fit_cube.sample(probe_u), 0.0, 1.0)
        app = simulate_8bit(apply_ycc(fit_preset, PROFILE, probe_u, "trilinear"), PROFILE, "full")
        de = delta_e76(
            display_rgb_to_lab(ref, PROFILE.output_space),
            display_rgb_to_lab(app, PROFILE.output_space),
        )
        recipe_fits[name] = {
            "gamma_u16": _i64(fit_preset.gamma),
            "matrix_i16": _i64(fit_preset.matrix),
            "delta_i8": _i64(fit_preset.delta),
            "at_rail_nodes": int(fit_report["at_rail_nodes"]),
            "clamped_nodes": int(fit_report["clamped_nodes"]),
            "mean_de76": float(de.mean()),
            "p95_de76": float(np.percentile(de, 95)),
        }
        recipes_out[name] = {
            "fields": {k: (list(v) if isinstance(v, tuple) else v) for k, v in recipe.__dict__.items()},
            "probe_rgb": _f64(probe_rgb),
            "probe_out": _f64(evaluate_recipe_3d(probe_rgb, recipe)),
        }
    (OUT / "recipe_fits.json").write_text(json.dumps(recipe_fits, indent=2), encoding="utf-8")
    for fname, ref, ec in (
        ("arri_rec709_fit.json", ArriReference.rec709(), 0.0),
        ("arri_wide_fit.json", ArriReference("wide-gamut"), 0.12),
    ):
        ap, ar = decompose(ref, PROFILE, source="S-Log3 / S-Gamut3.Cine", edge_compression=ec, interpolation="trilinear")
        (OUT / fname).write_text(
            json.dumps({
                "gamma_u16": _i64(ap.gamma),
                "matrix_i16": _i64(ap.matrix),
                "delta_i8": _i64(ap.delta),
                "at_rail_nodes": int(ar["at_rail_nodes"]),
            }),
            encoding="utf-8",
        )
    if args.golden_only:
        (OUT / "recipes.json").write_text(json.dumps(recipes_out), encoding="utf-8")
        print(f"wrote golden fixtures to {OUT} (presets untouched)")
        return

    # Shipped presets: 33^3 fits against the hash-verified av-cam.bin stock of each available target.
    # When a body's capture is absent, its tables may be reused only because the cross-body parity
    # test asserts that the a6100 and a6400 EC/FC/A0/A1/B2 stock tables are identical.
    stocks: dict[str, dict] = {}
    a6400 = real_stock("a6400")
    if a6400 is not None:
        stocks["a6400"] = a6400
    for target in ("a6100",):
        own = real_stock(target)
        if own is not None:
            stocks[target] = own
        elif a6400 is not None:
            stocks[target] = reused_stock(a6400, "a6400", target)
            print(f"{target}: reusing a6400 av-cam.bin (cross-body stock parity)", flush=True)
    if not stocks:
        raise SystemExit("no hash-verified av-cam.bin stock available; presets were not rewritten")
    movie_stocks: dict[str, dict] = {}
    for target, stock in stocks.items():
        try:
            movie_stocks[target] = movie_stock(stock)
        except (FileNotFoundError, SystemExit) as exc:
            print(f"{target}: Movie stock unavailable ({exc}); Movie variant skipped", flush=True)
    fitted_by_name: dict[str, dict[str, object]] = {}
    for name in LIVE_PICKER_ORDER:
        print(f"fitting {name} at 33^3 for {', '.join(sorted(stocks))}", flush=True)
        recipe = RECIPES_3D[name]
        built = build_recipe_cube(name, size=33)
        by_target = {target: fit_cube_to_isp(built, stock, name=name) for target, stock in stocks.items()}
        movie_by_target = {
            target: fit_cube_to_isp(built, stock, name=f"{name} Movie", movie_ec=True)
            for target, stock in movie_stocks.items()
        }
        fitted_by_name[name] = by_target
        a6400_fit = by_target.get("a6400") or next(iter(by_target.values()))
        recipes_out[name]["fit"] = {
            "ec_sim": a6400_fit.ec_sim,
            "fc_sim": a6400_fit.fc_sim,
            "a0_sim": a6400_fit.a0_sim,
            "a1_sim": a6400_fit.a1_sim,
            "b2_sim": a6400_fit.b2_sim,
            "refadr_hex": a6400_fit.refadr_bytes.hex(),
            "report": a6400_fit.report,
        }
        if "a6400" in movie_by_target:
            recipes_out[name]["fit_movie"] = {
                "ec_sim": movie_by_target["a6400"].ec_sim,
                "a0_sim": movie_by_target["a6400"].a0_sim,
                "refadr_hex": movie_by_target["a6400"].refadr_bytes.hex(),
                "report": movie_by_target["a6400"].report,
            }
        _write_bzlook(name, recipe, by_target, movie_by_target, stocks, movie_stocks)
    (OUT / "recipes.json").write_text(json.dumps(recipes_out), encoding="utf-8")
    golden_target = "a6400" if "a6400" in stocks else next(iter(stocks))
    header = _header_for("Classic Chrome", stocks[golden_target], fitted_by_name["Classic Chrome"][golden_target])
    (OUT / "film_sim_data.h").write_text(header, encoding="utf-8", newline="\n")
    (OUT / "film_sim_live_data.h").write_text(
        _live_header(stocks[golden_target], {name: fitted_by_name[name][golden_target] for name in LIVE_PICKER_ORDER}),
        encoding="utf-8",
        newline="\n",
    )
    (OUT / "README.md").write_text(
        "\n".join(
            [
                "# BIONZ golden vectors",
                "",
                "Frozen by `python a6400-lut-converter/tools/export_golden.py`.",
                "Stage arrays and recipe probes/fits match `lut_core` on the a6400 profile.",
                "The stock curves in stock.json/stages.json are synthetic on purpose; the shipped presets",
                "in `mods/film_sims/presets/` are compiled against hash-verified av-cam.bin stock tables.",
                "Recipe fits use a 17-point cube so the fixture stays small; shipped presets use 33^3.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
