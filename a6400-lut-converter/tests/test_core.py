import numpy as np
from lut_core import Cube, convert, pack, apply_preset, parse_cube, cube_to_text
from lut_core.color import slog3_encode, slog3_decode, slog2_encode, slog2_decode
from lut_profiles import a6400_profile as P


def make_cube(fn, n=17):
    ax = np.linspace(0, 1, n)
    u = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), -1)
    return Cube(n, np.zeros(3), np.ones(3), fn(u), "t")


SRC = "S-Log3 / S-Gamut3.Cine"


def test_identity():
    r = convert(make_cube(lambda u: u), P, SRC)
    # Separate fixed-point identity tolerance from legal-range YCbCr quantization.
    codes = np.random.default_rng(0).random((200, 3))
    np.testing.assert_allclose(apply_preset(r.preset, P, codes), codes, atol=1 / P.gamma_max_val)
    assert r.metrics["grid"]["max"] < 1.3
    assert np.abs(r.preset.delta).max() <= 1
    assert (r.preset.matrix.sum(1) == 1024).all()


def test_gamma_matrix_only_has_small_residual():
    M = np.array([[1.1, -0.05, -0.05], [-0.04, 1.08, -0.04], [0.0, -0.1, 1.1]])
    r = convert(make_cube(lambda u: np.clip(np.power(np.clip(u, 0, 1), 0.8) @ M.T, 0, 1)), P, SRC)
    assert r.metrics["typical"]["mean"] < 2.0
    assert r.report["clamped_nodes_pct"] < 5


def test_pack_roundtrip_and_sizes():
    rng = np.random.default_rng(1)
    r = convert(make_cube(lambda u: np.clip(u + 0.05 * rng.standard_normal(u.shape), 0, 1)), P, SRC)
    assert len(pack.pack_refadr(r.preset)) == 1215
    assert len(pack.pack_esram(r.preset, P)) == 0x654
    assert len(pack.pack_gamma(r.preset)) == 192
    blob = pack.pack_preset(r.preset, P)
    q = pack.unpack_preset(blob, P)
    assert (q.gamma == r.preset.gamma).all() and (q.matrix == r.preset.matrix).all()
    assert (q.delta == r.preset.delta).all()
    assert 1800 < len(blob) < 2100


def test_permutation_roundtrip():
    from dataclasses import replace
    perm = tuple(np.random.default_rng(2).permutation(405).tolist())
    P2 = replace(P, permutation=perm).validate()
    r = convert(make_cube(lambda u: u ** 1.2), P2, SRC)
    q = pack.unpack_preset(pack.pack_preset(r.preset, P2), P2)
    assert (q.delta == r.preset.delta).all()


def test_log_curves_and_cube_io():
    x = np.array([0.0, 0.01, 0.18, 0.9, 5.0])
    assert np.allclose(slog3_decode(slog3_encode(x)), x, atol=1e-9)
    assert np.allclose(slog2_decode(slog2_encode(x)), x, atol=1e-9)
    assert abs(slog3_encode(0.18) - 0.4106) < 1e-3
    c = make_cube(lambda u: u * 0.9, n=5)
    c2 = parse_cube(cube_to_text(c))
    assert np.allclose(c.table, c2.table, atol=1e-6)

def test_verified_wire_layout_known_offsets():
    from lut_core.model import Preset
    delta = np.zeros((5, 9, 9, 3), dtype=np.int8)
    delta[0, 0, 1] = (-64, 63, -1)
    delta[1, 0, 0] = (3, 4, 5)
    p = Preset(np.zeros(96, np.uint16), np.eye(3, dtype=np.int16) * 1024, delta)
    ref = pack.pack_refadr(p)
    assert (ref[1], ref[405 + 1], ref[810 + 1]) == (192, 63, 255)
    words = np.frombuffer(pack.pack_esram(p, P), dtype="<u4")
    assert (words[75], words[243], words[1]) == (0x403F7F, 0x030405, 0)
    delta[0, 0, 1, 0] = -65
    for encoder in (pack.pack_refadr, lambda p: pack.pack_esram(p, P)):
        try:
            encoder(p)
        except ValueError:
            pass
        else:
            raise AssertionError("out-of-range signed 7-bit value was accepted")


def test_q10_and_legal_and_full_420_codes():
    from lut_core.model import Preset, encode_8bit, matrix_stage, simulate_8bit
    from dataclasses import replace
    profile = replace(P, gamma_size=2, gamma_max_val=8)
    p = Preset(np.array([0, 8]), np.array([[512, 512, 0], [-1024, 2048, 0], [2048, 0, -1024]]), None)
    np.testing.assert_array_equal(matrix_stage(p, profile, np.array([0.5, 0.375, 0])), [0.5, 0.25, 1])
    np.testing.assert_array_equal(matrix_stage(p, profile, np.array([1, 0, 0])), [0.5, 0, 1])
    ycc = np.zeros((3, 3, 3))
    ycc[:, 1, 0] = 256
    ycc[0, 0, 1] = 128
    ycc[:, 2, 1] = -128
    y, c = encode_8bit(ycc, P, "legal", chroma_420=True)
    assert y[0].tolist() == [16, 235, 16]
    assert c[..., 0].tolist() == [[156, 16], [128, 16]]
    y, c = encode_8bit(ycc, P, "full", chroma_420=True)
    assert y[0].tolist() == [0, 255, 0]
    assert c[..., 0].tolist() == [[160, 1], [128, 1]]
    assert simulate_8bit(ycc, P, chroma_420=True).shape == (3, 3, 3)


def test_gamma_tetrahedral_and_vector_projection():
    from lut_core.decompose import _project
    from lut_core.interp import tetrahedral, trilinear
    from lut_core.arri import logc3_decode, logc3_encode
    x = np.array([-0.01, 0.0, 0.01059, 0.18, 16.0])
    np.testing.assert_allclose(logc3_decode(logc3_encode(x)), x, atol=1e-6)
    assert abs(logc3_encode(0.18) - 400 / 1023) < 0.0002
    np.testing.assert_allclose(_project(np.array([[100., 90., -30.], [-100., 24., -96.]]), P),
                               [[63, 63, -21], [-64, 16, -64]])
    table = np.zeros((2, 2, 2, 1))
    table[1, 1, 1, 0] = 8
    coords = [[0.25, 0.5, 0.75], [1, 1, 1]]
    np.testing.assert_allclose(tetrahedral(table, coords).ravel(), [2, 8])
    np.testing.assert_allclose(trilinear(table, coords).ravel(), [0.75, 8])


def test_cube_input_range_in_reference_and_fit():
    cube = parse_cube("LUT_3D_SIZE 2\nLUT_3D_INPUT_RANGE 0.2 0.8\n" +
                      "".join(f"{r} {g} {b}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    np.testing.assert_allclose(cube.sample([[0.2, 0.5, 0.8]]), [[0, 0.5, 1]])
    result = convert(cube, P, SRC)
    np.testing.assert_allclose(apply_preset(result.preset, P, [[0.5, 0.5, 0.5]]), 0.5, atol=.01)


def test_legacy_preset_requires_its_unverified_profile():
    from dataclasses import replace
    legacy = replace(P, permutation=tuple(range(P.n_nodes)), permutation_verified=False)
    result = convert(make_cube(lambda u: u), legacy, SRC)
    blob = pack.pack_preset(result.preset, legacy)
    np.testing.assert_array_equal(pack.unpack_preset(blob, legacy).delta, result.preset.delta)
    try:
        pack.unpack_preset(blob, P)
    except ValueError as error:
        assert "legacy unverified ordering" in str(error)
    else:
        raise AssertionError("legacy preset ordering was silently reinterpreted")


def test_builtin_rec709_shoulder_and_image_input():
    from pathlib import Path
    import tempfile
    from PIL import Image
    from lut_core.arri import ArriReference, classic_rec709, logc3_encode, shoulder, _AWG_TO_REC709
    from lut_core.image_source import load_image
    from lut_core.preview import source_codes

    ramp = shoulder(np.geomspace(1e-4, 40, 48))
    assert np.all(np.diff(ramp) >= -1e-8)
    assert shoulder(np.array([-0.2, 0.0])) .tolist() == [0.0, 0.0]
    assert shoulder(np.array(80.0)) == 1.0

    def through(rgb):
        awg = np.asarray(rgb, float) @ np.linalg.inv(_AWG_TO_REC709).T
        return classic_rec709(np.clip(logc3_encode(awg), 0, 1))

    def chroma(rgb):
        y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        return np.linalg.norm(rgb - y)

    gray = through([0.18, 0.18, 0.18])
    assert abs(gray[0] - 0.396) < 0.015
    assert np.allclose(gray, gray[0], atol=1e-6)
    assert chroma(through([8.0, 0.4, 0.2])) < chroma(through([0.4, 0.02, 0.01]))
    assert classic_rec709(np.zeros(3)).max() == 0
    assert ArriReference().output_space == "logc3-awg3"
    assert ArriReference.rec709().output_space == "gamma24-rec709"
    assert ArriReference.rec709().classic is None

    folder = Path(tempfile.mkdtemp())
    picture = folder / "preview.png"
    Image.fromarray(np.full((6, 8, 3), 140, np.uint8)).save(picture)
    loaded = load_image(picture, 900)
    assert loaded.encoding == "srgb" and loaded.rgb.shape == (6, 8, 3)
    linear = np.full((2, 2, 3), 0.18)
    encoded = source_codes(linear, SRC, "linear-rec709")
    assert np.allclose(encoded[..., 0], encoded[..., 1])
    assert not np.allclose(source_codes(linear, SRC, "srgb"), encoded)
    from lut_core.color import srgb_oetf
    from lut_core.image_source import _limit_edge, exposure_scale
    dark = np.full((24, 32, 3), 0.02)
    jpeg = np.full((24, 32, 3), srgb_oetf(0.12))
    assert abs(exposure_scale(dark, jpeg) - 6.0) < 0.05
    assert abs(exposure_scale(np.full((8, 8, 3), 0.05)) - 9.0) < 1e-6
    wide = np.full((40, 1000, 3), 0.25)
    fitted = _limit_edge(wide, 100)
    assert max(fitted.shape[:2]) <= 100
    assert fitted.shape[-1] == 3
    np.testing.assert_allclose(fitted, 0.25)
    broken = folder / "broken.arw"
    broken.write_bytes(b"not a camera raw")
    try:
        load_image(broken)
    except ValueError as error:
        assert "develop" in str(error).lower() or "rawpy" in str(error).lower()
    else:
        raise AssertionError("a corrupt ARW file was accepted")


def test_xyz_develop_uses_source_primaries_before_exposure():
    from lut_core.color import D65, slog3_encode, source_log_extent, xyz_to_rgb
    from lut_core.preview import source_codes
    white = np.array([D65[0] / D65[1], 1.0, (1.0 - D65[0] - D65[1]) / D65[1]])
    np.testing.assert_allclose(xyz_to_rgb(white, "S-Gamut3.Cine"), 1.0, atol=1e-8)
    mid = np.full((1, 1, 3), white * 0.18)
    np.testing.assert_allclose(source_codes(mid, SRC, "linear-xyz"), slog3_encode(0.18), atol=1e-6)
    np.testing.assert_allclose(source_codes(mid, SRC, "linear-xyz", ev=1), slog3_encode(0.36), atol=1e-6)
    hot = source_codes(np.full((1, 1, 3), white * 1000.0), SRC, "linear-xyz", exposure_scale=4)
    _lo, hi = source_log_extent(SRC)
    assert 30.0 < hi < 45.0
    np.testing.assert_allclose(hot, 1.0, atol=1e-6)


def test_exposure_ev_and_diagnostic_overlay():
    from lut_core.preview import apply_overlay, heatmap_color, scene_linear
    base = scene_linear(np.full((2, 2, 3), 0.2), SRC, "linear-rec709", ev=0)
    plus = scene_linear(np.full((2, 2, 3), 0.2), SRC, "linear-rec709", ev=1)
    np.testing.assert_allclose(plus, base * 2)
    color, alpha = heatmap_color(np.array([1.0, 2.2, 4.5, 7.0]))
    assert alpha[0] == 0 and alpha[1:].min() == 0.75
    assert color[1, 1] > 0.3 and color[1, 2] > 0.3
    assert color[2, 0] > 0.9 and color[2, 1] > 0.4
    assert color[3, 0] > 0.9 and color[3, 2] > 0.5
    image = np.ones((4, 8, 3))
    delta = np.zeros((4, 8))
    delta[:, 4:] = 8
    painted = apply_overlay(image, "de", delta, np.zeros((4, 8), bool), 0.5)
    np.testing.assert_allclose(painted[:, 0], 1)
    assert painted[:, -1, 0].mean() > 0.7 and painted[:, -1, 2].mean() > 0.4
    clamp = np.zeros((4, 8), dtype=bool)
    clamp[:, 4:] = True
    zebra = apply_overlay(np.zeros((4, 8, 3)), "clamp", delta, clamp, 0.5)
    assert zebra[0, 4, 0] > 0.5
    assert zebra[:, :4].max() == 0


def test_arri_linear_reference_bypasses_sony_encoding():
    from lut_core.arri import ArriReference, SONY_TO_AWG3, logc3_encode
    rgb = np.array([[0.0, 0.18, 0.75], [0.18, 0.18, 0.18], [0.5, 4.0, 32.0]])
    for ref in (ArriReference(), ArriReference.rec709()):
        np.testing.assert_allclose(ref.sample_linear(rgb), ref.sample(slog3_encode(rgb)), atol=1e-12)
    expected = np.clip(logc3_encode(rgb @ SONY_TO_AWG3.T), 0, 1)
    np.testing.assert_allclose(ArriReference().sample_linear(rgb), expected, atol=1e-12)

def test_alexa35_transfer_and_neutral_axis():
    from lut_core.alexa35 import SONY_TO_AWG4, Alexa35Reference, logc4_decode, logc4_encode

    np.testing.assert_allclose(logc4_encode([0, 0.18, 117.45]), [95 / 1023, 0.27839584, 0.87042628],
                               atol=1e-8, rtol=0)
    values = np.array([-0.2, -0.01, 0, 0.18, 1, 117.45])
    np.testing.assert_allclose(logc4_decode(logc4_encode(values)), values, atol=1e-12)
    np.testing.assert_allclose(SONY_TO_AWG4 @ np.ones(3), np.ones(3), atol=1e-14)
    np.testing.assert_allclose(Alexa35Reference().sample_linear(np.full(3, 0.18)),
                               np.full(3, logc4_encode(0.18)))


def test_ciede2000_reference_pairs():
    from lut_core.color import delta_e00

    first = [[50, 2.6772, -79.7751], [50, 3.1571, -77.2803], [50, 0, 0]]
    second = [[50, 0, -82.7485], [50, 0, -82.7485], [50, -1, 2]]
    np.testing.assert_allclose(delta_e00(first, second), [2.0425, 2.8615, 2.3669], atol=5e-5)
    np.testing.assert_array_equal(delta_e00(first, first), 0)


def test_movie_gamma_coordinates_and_payload():
    import struct
    from lut_core.model import Preset, gamma_codes
    from lut_core.movie_gamma import METADATA, POSITIONS, STOCK_CODES, movie_profile, pack_movie_gamma

    profile = movie_profile(P).validate()
    assert len(POSITIONS) == 96 and POSITIONS[:3] == (4, 8, 12)
    assert POSITIONS[-3:] == (7168, 7680, 8191)
    assert np.all(np.diff(POSITIONS) > 0)
    preset = Preset(np.asarray(STOCK_CODES, dtype=np.uint16), None, None)
    inputs = np.arange(1024) / 1023
    np.testing.assert_array_equal(gamma_codes(preset, profile, inputs), np.arange(1024).clip(84, 803))
    assert struct.unpack("<105I", pack_movie_gamma(preset)) == STOCK_CODES + METADATA
    try:
        pack.pack_preset(preset, profile)
    except ValueError as error:
        assert "legacy" in str(error)
    else:
        raise AssertionError("Movie payload silently accepted as a legacy A6LT")


def test_alexa35_lut_is_applied_and_digest_checked():
    import hashlib
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from lut_core import alexa35

    fixture = cube_to_text(make_cube(lambda u: u ** 2, n=3)).encode()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.cube"
        path.write_bytes(fixture)
        try:
            alexa35.Alexa35Reference.from_lut(path)
        except ValueError as error:
            assert "official ARRI" in str(error)
        else:
            raise AssertionError("An unrelated LUT passed the official LUT check")
        digest = hashlib.sha256(fixture).hexdigest()
        with patch.object(alexa35, "OFFICIAL_REC709_SHA256", {digest}):
            wide = alexa35.Alexa35Reference.from_lut(path)
            baked = alexa35.Alexa35Reference.from_lut(path, "alexa35-rec709")
        sample = np.asarray([[0.33, 0.43, 0.49]])
        np.testing.assert_allclose(wide.display(wide.sample(sample)), baked.sample(sample))
        assert wide.provenance()["display_lut_sha256"] == digest
