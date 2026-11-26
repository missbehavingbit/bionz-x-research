"""Emit stock and ARRI matrices for entry B8 as a C header (offline, from receipts)."""
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[3]
c = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/arri-matrix-candidates.json").read_text())
stock = sum(c["B8_stock"], []); new = sum(c["candidates"][sys.argv[1]]["q10"], [])
live = json.loads((root / "artifacts/analysis/arri-movie-color-2026-11-16/colormode-matrix-table.json").read_text())
assert stock == live["live"]["B8"], "stock must equal the live table entry B8"   # Sony row sums may be 1025
assert len(new) == 9 and all(sum(new[i:i + 3]) == 1024 for i in (0, 3, 6)) and max(map(abs, new)) < 32768
f = lambda m: ",".join(map(str, m))
Path(sys.argv[2]).write_text("#include <stdint.h>\n"
    f"static const int16_t COLOR_STOCK[9] = {{{f(stock)}}};\nstatic const int16_t COLOR_NEW[9] = {{{f(new)}}};\n")
