import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

import lut_core
from lut_core import pack
from lut_core.arri import ArriReference, CLASSIC_SHA256, SONY_SOURCE
from lut_core.color import SOURCES
from lut_core.alexa35 import Alexa35Reference
from lut_core.movie_gamma import movie_profile, pack_movie_gamma
from lut_core.profile import Profile
from lut_profiles import PROFILES


def _export(base, result, profile, raw, reference_info=None):
    movie = bool(profile.gamma_positions)
    if movie:
        base.with_name(f"{base.name}.movie-gamma.bin").write_bytes(pack_movie_gamma(result.preset))
    else:
        base.with_name(f"{base.name}.a6lt").write_bytes(pack.pack_preset(result.preset, profile))
    if raw:
        gamma = () if movie else (("gamma", pack.pack_gamma(result.preset)),)
        blobs = gamma + (("matrix", pack.pack_matrix(result.preset)),
                 ("refadr", pack.pack_refadr(result.preset)), ("esram", pack.pack_esram(result.preset, profile)))
        for ext, blob in blobs:
            base.with_name(f"{base.name}.{ext}.bin").write_bytes(blob)
    receipt = {"report": result.report, "metrics": result.metrics, "warnings": result.warnings}
    if reference_info is not None:
        receipt["reference"] = reference_info
    base.with_name(f"{base.name}.report.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    m = result.metrics
    print(f"{base.name}: {m['metric']} validation mean {m['validation_combined']['mean']:.2f} "
          f"max {m['validation_combined']['max']:.2f} | "
          f"ColorChecker mean {m['colorchecker']['mean']:.2f} max {m['colorchecker']['max']:.2f} | "
          f"clamped nodes {result.report['clamped_nodes_pct']:.1f}%")
    for warning in result.warnings:
        print("  WARNING:", warning)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lut_cli", description=".cube to a6400 ISP layers")
    ap.add_argument("cube", nargs="*", help="input .cube file(s)")
    ap.add_argument("-s", "--source", default=SONY_SOURCE, choices=list(SOURCES))
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("out"))
    ap.add_argument("-p", "--profile", default="a6400", choices=list(PROFILES))
    ap.add_argument("--profile-json", help="override the hardware profile")
    ap.add_argument("--raw", action="store_true", help="also write gamma/matrix/refadr/esram .bin")
    ap.add_argument("--arri", choices=("logc3", "classic", "both", "logc4", "alexa35-rec709", "alexa35"), help="generate ARRI preset(s)")
    ap.add_argument("--classic-cube", type=Path,
                    help="optional official Classic cube, used instead of the built-in Rec.709 model")
    ap.add_argument("--logc4-display-cube", type=Path, help="official ARRI LogC4-to-Gamma24_Rec709-D65 v1 LUT")
    ap.add_argument("--gamma-model", choices=("uniform", "movie", "compare"), default="uniform")
    ap.add_argument("--charts", action="store_true", help="write reference and 4:2:0 validation charts (requires Pillow)")
    ap.add_argument("--spatial-test", action="store_true", help="run offline ARRI vs Sony debayer/DCV spatial comparison")
    ap.add_argument("--interpolation", choices=("trilinear", "tetrahedral"),
                    help="override the preset interpolation model")
    ap.add_argument("--video-range", choices=("legal", "full"), help="override the default output range")
    a = ap.parse_args(argv)
    if a.spatial_test:
        from lut_core.spatial_test import run_spatial_comparison
        out_png = run_spatial_comparison(a.out_dir)
        print(f"Wrote spatial comparison chart: {out_png}")
        return 0
    if bool(a.cube) == bool(a.arri):
        ap.error("supply input .cube files or choose --arri")
    if a.arri and a.source != SONY_SOURCE:
        ap.error(f"ARRI presets require {SONY_SOURCE}")
    if a.classic_cube is not None and a.arri not in ("classic", "both"):
        ap.error("--classic-cube applies only to --arri classic or --arri both")
    alexa = a.arri in ("logc4", "alexa35-rec709", "alexa35")
    if alexa != bool(a.logc4_display_cube):
        ap.error("Alexa 35 presets require --logc4-display-cube")
    if a.gamma_model != "uniform" and not alexa:
        ap.error("Movie gamma comparison applies to Alexa 35 presets")
    profile = Profile.from_json(a.profile_json) if a.profile_json else PROFILES[a.profile]
    a.out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(Path(path).stem, path) for path in a.cube]
    if a.arri in ("logc3", "both"):
        jobs.append(("arri-logc3-awg3", None))
    if a.arri in ("classic", "both"):
        jobs.append(("arri-classic-rec709", a.classic_cube))
    if a.arri in ("logc4", "alexa35"):
        jobs.append(("arri-logc4-awg4", a.logc4_display_cube))
    if a.arri in ("alexa35-rec709", "alexa35"):
        jobs.append(("alexa35-rec709", a.logc4_display_cube))
    models = ("uniform", "movie") if a.gamma_model == "compare" else (a.gamma_model,)
    rc = 0
    for name, path, model in ((name, path, model) for name, path in jobs for model in models):
        base = a.out_dir / (name + ("-movie" if model == "movie" else ""))
        try:
            reference_info = None
            edge = 0.0
            interpolation = a.interpolation or "trilinear"
            job_profile = profile
            if not a.arri:
                cube = lut_core.load_cube(path)
                video_range = a.video_range or "legal"
            elif alexa:
                kind = "logc4" if name == "arri-logc4-awg4" else "alexa35-rec709"
                cube = Alexa35Reference.from_lut(path, kind)
                job_profile = replace(profile, output_space=cube.output_space)
                if model == "movie":
                    job_profile = movie_profile(job_profile)
                video_range = a.video_range or cube.video_range
                reference_info = {**cube.provenance(), "source": a.source}
            else:
                if name == "arri-classic-rec709":
                    cube = ArriReference.rec709() if path is None else ArriReference.from_classic(path)
                else:
                    cube = ArriReference()
                job_profile = replace(profile, output_space=cube.output_space)
                video_range = a.video_range or cube.video_range
                edge = cube.edge_compression
                interpolation = a.interpolation or cube.interpolation
                reference_info = {"source": a.source, "logc3_ei": 800, "model": cube.kind}
                if cube.kind == "rec709":
                    reference_info["rec709_model"] = "official-classic-cube" if cube.classic is not None else "builtin"
                    if cube.classic is not None:
                        reference_info["classic_sha256"] = CLASSIC_SHA256
            result = lut_core.convert(cube, job_profile, a.source, video_range=video_range,
                                      interpolation=interpolation, edge_compression=edge)
            if a.charts:
                from lut_core.preview import write_charts
                write_charts(base, cube, result.preset, job_profile, a.source, video_range, interpolation)
            _export(base, result, job_profile, a.raw, reference_info)
        except (OSError, ValueError, ImportError) as error:
            print(f"{name}: ERROR {error}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

