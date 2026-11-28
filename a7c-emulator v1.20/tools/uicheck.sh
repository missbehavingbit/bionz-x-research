#!/bin/bash
# Boot for N seconds, then report how far the camera UI got:
#   * open (never finished) im.elf boot steps from /proc/snsc_boot_time
#   * GPU job counts, and a screenshot of the screen console
#   tools/uicheck.sh [seconds] [extra qemu args...]
# Output: out/console.log, out/boot_time.txt, out/gpu.log, out/screen.png
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SECS="${1:-200}"; shift || true
OUT="$HERE/out"; mkdir -p "$OUT"
rm -f "$OUT/console.sock" "$OUT/console.log" "$OUT/gpu.log" "$OUT/screen.png"

A7C_SERIAL=chardev:con A7C_FRESH="${A7C_FRESH:-1}" timeout $((SECS + 120)) "$HERE/run.sh" \
    -chardev socket,id=con,path="$OUT/console.sock",server=on,wait=off,logfile="$OUT/console.log" \
    -trace dmp_smaph_job -D "$OUT/gpu.log" "$@" < /dev/null > "$OUT/qemu-out.log" 2>&1 &
QPID=$!
sleep "$SECS"

python3 "$HERE/tools/a7cshell.py" "$OUT/console.sock" run 'cat /proc/snsc_boot_time' \
    | grep -v -E 'hwtimer|caught up' > "$OUT/boot_time.txt"
python3 "$HERE/tools/mon.py" "$OUT/monitor.sock" "screendump \"$OUT/screen.png\" -f png" > /dev/null
kill $QPID 2>/dev/null; wait $QPID 2>/dev/null

echo "== open boot steps (B without E), last 12:"
python3 - "$OUT/boot_time.txt" <<'EOF'
import re, sys
open_ = {}
for l in open(sys.argv[1], errors='replace'):
    m = re.match(r'\s*\d+:\s+(\w+)\s+([BE])\s+(.*?)\s*:\s*(\d+) \[us\]', l)
    if not m:
        continue
    who, be, msg, t = m.groups()
    if who == '97':                       # per-object EV START/END pairs: noise
        continue
    key = (who, msg.strip())
    if be == 'B':
        open_[key] = int(t)
    else:
        open_.pop(key, None)
for (who, msg), t in sorted(open_.items(), key=lambda x: x[1])[-12:]:
    print('  %9.3f s  [%s] %s' % (t / 1e6, who, msg))
EOF
echo "== GPU jobs:"; awk '{print $2}' "$OUT/gpu.log" 2>/dev/null | sort | uniq -c
echo "== screen: $OUT/screen.png"
