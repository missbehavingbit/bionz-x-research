"""Emit hash-checked gamma and color data for Arrivederci (offline).

Usage:
  make_data.py OUT_HEADER [MATRIX_FIT_JSON]
  make_data.py OUT_HEADER --from-v5-mod [ARRIVEDERCI_MOD_PATH]
Without a fit, COLOR_ARRI is the camera-tested Rec.709 primary matrix (2026-11-16). With the
output of fit_matrix.py, COLOR_ARRI is its "classic_fit" candidate and the earlier matrix is
emitted as COLOR_LEGACY, so ON and OFF also recognize a B8 that still holds the old values.
With --from-v5-mod, extracts the identical verified tables from the tracked v5 Arrivederci.mod
when local intermediate analysis directories are absent.
"""
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
V5_MOD_SHA256 = "46c41981e7b37231a6cadb0d033f514c5b05a17ecb38c85f68d909d64a0b64b7"


def fmt(values, as_hex):
    return ",".join(hex(v) if as_hex else str(v) for v in values)


def _c_bytes(name: str, data: bytes) -> str:
    hex_bytes = ",".join(f"0x{b:02x}" for b in data)
    return f"static const uint8_t {name}[{len(data)}] = {{{hex_bytes}}};\n"


if len(sys.argv) > 2 and sys.argv[2] == "--from-v5-mod":
    mod_path = Path(sys.argv[3]) if len(sys.argv) > 3 else (root / "mods/arri_isp/arrivederci/Arrivederci.mod")
    blob = mod_path.read_bytes()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != V5_MOD_SHA256:
        raise SystemExit(f"Unexpected Arrivederci.mod SHA-256: {actual}")
    code_size = struct.unpack_from("<I", blob, 44)[0]
    code = blob[68 : 68 + code_size]
    gamma_stock = struct.unpack_from("<96I", code, 0x0790)
    gamma_arri = struct.unpack_from("<96I", code, 0x0910)
    gamma_legacy = struct.unpack_from("<96I", code, 0x0A90)
    color_stock = struct.unpack_from("<9h", code, 0x0C10)
    color_arri = struct.unpack_from("<9h", code, 0x0C22)
    color_legacy = struct.unpack_from("<9h", code, 0x0C34)
    refadr_bytes = code[0x0C46 : 0x0C46 + 1215]
    sabin_y_tap_2 = code[0x1108 : 0x1108 + 48000]
    sabin_y_tap_1 = code[0xCC88 : 0xCC88 + 48000]
    sabin_dcv_4k = code[0x18808 : 0x18808 + 10728]
    sabin_dcv_bayer = code[0x1B1F0 : 0x1B1F0 + 18288]
    assert len(sabin_dcv_bayer) == 18288 and 0x1B1F0 + 18288 == code_size
    sabin_hdr = (
        "#define SABIN_EMBEDDED 1\n"
        + _c_bytes("SABIN_DCV_BAYER", sabin_dcv_bayer)
        + _c_bytes("SABIN_DCV_4K", sabin_dcv_4k)
        + _c_bytes("SABIN_Y_TAP_1", sabin_y_tap_1)
        + _c_bytes("SABIN_Y_TAP_2", sabin_y_tap_2)
    )
    refadr_hdr = "#define REFADR_EMBEDDED 1\n" + _c_bytes("REFADR_ARRI", refadr_bytes)
    legacy = (
        "#define GAMMA_HAS_LEGACY 1\n"
        f"static const uint32_t GAMMA_LEGACY[96] = {{{fmt(gamma_legacy, True)}}};\n"
        "#define COLOR_HAS_LEGACY 1\n"
        f"static const int16_t COLOR_LEGACY[9] = {{{fmt(color_legacy, False)}}};\n"
    )
    Path(sys.argv[1]).write_text(
        "#include <stdint.h>\n"
        f"static const uint32_t GAMMA_STOCK[96] = {{{fmt(gamma_stock, True)}}};\n"
        f"static const uint32_t GAMMA_ARRI[96] = {{{fmt(gamma_arri, True)}}};\n"
        f"static const int16_t COLOR_STOCK[9] = {{{fmt(color_stock, False)}}};\n"
        f"static const int16_t COLOR_ARRI[9] = {{{fmt(color_arri, False)}}};\n"
        + legacy + sabin_hdr + refadr_hdr
    )
    raise SystemExit(0)

g = root / "artifacts/analysis/arri-gamma-test-block-2026-11-16"
SHA = {"record31-stock.bin": "bc1d67e362549d6fda06c48e86fff4648cb099819e38d6e16032b0be99ceed58",
       "record31-arri-classic-rec709.bin": "93d69d065c0547abdf680bc104a80780360111b2e312163e99a74f34659de744"}
words = {}
for name, digest in SHA.items():
    blob = (g / name).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == digest, name
    words[name] = struct.unpack("<105I", blob)
c = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/arri-matrix-candidates.json").read_text())
live = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/colormode-matrix-table.json").read_text())
color_stock, color_arri = sum(c["B8_stock"], []), sum(c["candidates"]["rec709"]["q10"], [])
assert color_stock == live["live"]["B8"], "stock must equal the live table entry B8"
color_legacy = None
if len(sys.argv) > 2:
    fit = json.loads(Path(sys.argv[2]).read_text())
    assert fit["candidates"]["primaries_current"]["q10"] == c["candidates"]["rec709"]["q10"]
    color_legacy, color_arri = color_arri, sum(fit["candidates"]["classic_fit"]["q10"], [])
    assert color_arri not in (color_stock, color_legacy)
assert all(sum(color_arri[i:i + 3]) == 1024 for i in (0, 3, 6))
assert all(-32768 <= v <= 32767 for v in color_arri)


def _filmic_highlight_shoulder(base_curve: tuple[int, ...]) -> list[int]:
    """Extend upper highlights (knots 64..95) with a C1-smooth roll-off to 0xf780."""
    out = list(base_curve[:64])
    y63 = out[63]
    span = 0xF780 - y63
    k = 2.65
    denom = 1.0 - math.exp(-k)
    for i in range(64, 96):
        t = (i - 63) / 32.0
        v = y63 + span * (1.0 - math.exp(-k * t)) / denom
        out.append(int(round(v / 64.0)) * 64)
    return out


gamma_legacy = list(words["record31-arri-classic-rec709.bin"][:96])
gamma_arri = _filmic_highlight_shoulder(words["record31-arri-classic-rec709.bin"][:96])
assert len(gamma_arri) == 96 and all(gamma_arri[i] >= gamma_arri[i - 1] for i in range(1, 96))

sabin_dir = root / "artifacts/analysis/arri-v4-sabin"
sabin_hdr = ""
sabin_files = {
    "SABIN_DCV_BAYER": "dcv_psf_table_movie_bayer.bin",
    "SABIN_DCV_4K": "dcv_psf_table_movie_4k.bin",
    "SABIN_Y_TAP_1": "spica_musashi_mov_4k_y_tap_step1.bin",
    "SABIN_Y_TAP_2": "spica_musashi_mov_4k_y_tap_step2.bin",
}
if (sabin_dir / "dcv_psf_table_movie_bayer.bin").exists():
    sabin_hdr = "#define SABIN_EMBEDDED 1\n" + "".join(
        _c_bytes(sym, (sabin_dir / fname).read_bytes()) for sym, fname in sabin_files.items()
    )

refadr_path = root / "artifacts/analysis/arri-v5-3dlut/refadr_arri_v5.bin"
refadr_hdr = ""
if refadr_path.exists():
    refadr_bytes = refadr_path.read_bytes()
    assert len(refadr_bytes) == 1215
    refadr_hdr = "#define REFADR_EMBEDDED 1\n" + _c_bytes("REFADR_ARRI", refadr_bytes)

legacy = ("#define GAMMA_HAS_LEGACY 1\n"
          f"static const uint32_t GAMMA_LEGACY[96] = {{{fmt(gamma_legacy, True)}}};\n")
if color_legacy:
    legacy += ("#define COLOR_HAS_LEGACY 1\n"
               f"static const int16_t COLOR_LEGACY[9] = {{{fmt(color_legacy, False)}}};\n")
Path(sys.argv[1]).write_text(
    "#include <stdint.h>\n"
    f"static const uint32_t GAMMA_STOCK[96] = {{{fmt(words['record31-stock.bin'][:96], True)}}};\n"
    f"static const uint32_t GAMMA_ARRI[96] = {{{fmt(gamma_arri, True)}}};\n"
    f"static const int16_t COLOR_STOCK[9] = {{{fmt(color_stock, False)}}};\n"
    f"static const int16_t COLOR_ARRI[9] = {{{fmt(color_arri, False)}}};\n"
    + legacy + sabin_hdr + refadr_hdr)
