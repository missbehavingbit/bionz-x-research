"""Generate still_detail_data.h from a designed dcv_psf_table_still.bin."""
import hashlib
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"Usage: {sys.argv[0]} OUT_HEADER STILL_DCV_BIN")
    out_h = Path(sys.argv[1])
    src = Path(sys.argv[2])
    data = src.read_bytes()
    if len(data) != 57848:
        raise SystemExit(f"Expected 57848-byte still DCV table, got {len(data)}")
    digest = hashlib.sha256(data).hexdigest()
    lines = [
        f"/* Generated from {src.name} (sha256 {digest}) */",
        f"#define STILL_DCV_LEN {len(data)}U",
        "static const unsigned char STILL_DCV_TABLE[STILL_DCV_LEN] = {",
    ]
    for i in range(0, len(data), 16):
        chunk = ", ".join(f"0x{b:02x}" for b in data[i : i + 16])
        lines.append(f"    {chunk},")
    lines.append("};")
    out_h.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
