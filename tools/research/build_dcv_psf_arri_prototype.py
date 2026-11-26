"""Generate attenuated offline dcv_psf_table prototypes for ARRI-style halo suppression."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

SABIN_DIR = Path("firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/sabin")
OUT_DIR = Path("artifacts/analysis/arri-sdk-pipeline-e0604")


def scale_dcv_body(src_bytes: bytes, header_size: int, gain: float) -> bytes:
    """Scale int16 deconvolution delta body coefficients by gain while preserving header."""
    out = bytearray(src_bytes)
    n_i16 = (len(src_bytes) - header_size) // 2
    vals = struct.unpack_from(f"<{n_i16}h", src_bytes, header_size)
    scaled = [max(-32768, min(32767, int(round(v * gain)))) for v in vals]
    struct.pack_into(f"<{n_i16}h", out, header_size, *scaled)
    return bytes(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    specs = [
        ("dcv_psf_table_movie_bayer.bin", 0xEC),
        ("dcv_psf_table_movie_4k.bin", 0xEC),
    ]
    manifest = {"prototypes": []}
    for fname, hdr_sz in specs:
        src = (SABIN_DIR / fname).read_bytes()
        for label, gain in (("25pct", 0.25), ("00pct_flat", 0.0)):
            mod = scale_dcv_body(src, hdr_sz, gain)
            out_name = fname.replace(".bin", f"_arri_{label}.bin")
            out_path = OUT_DIR / out_name
            out_path.write_bytes(mod)
            manifest["prototypes"].append({
                "source": fname,
                "source_sha256": hashlib.sha256(src).hexdigest(),
                "output": out_name,
                "output_sha256": hashlib.sha256(mod).hexdigest(),
                "header_bytes_preserved": hdr_sz,
                "gain": gain,
                "size": len(mod),
            })
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Generated", len(manifest["prototypes"]), "prototypes in", OUT_DIR)


if __name__ == "__main__":
    main()
