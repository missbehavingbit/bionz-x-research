#!/bin/bash
# Boot for N seconds and report which unmodelled hardware the firmware touches.
#   tools/hwscan.sh [seconds] [extra qemu args...]
# Output: out/unimp.txt (register table, hot polls first), out/klog.txt
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SECS="${1:-90}"; shift || true
OUT="$HERE/out"; mkdir -p "$OUT"
FIFO="$OUT/unimp.fifo"; rm -f "$FIFO"; mkfifo "$FIFO"
MON="$OUT/hwscan-mon.sock"; rm -f "$MON"

python3 "$HERE/tools/unimp_stats.py" "$FIFO" "$OUT/unimp.txt" &
SPID=$!
A7C_SERIAL="file:$OUT/serial.log" A7C_FRESH="${A7C_FRESH:-1}" timeout $((SECS + 30)) "$HERE/run.sh" \
    -monitor unix:"$MON",server,nowait -d unimp -D "$FIFO" "$@" \
    < /dev/null > "$OUT/qemu-out.log" 2>&1 &
QPID=$!
python3 "$HERE/tools/klogtap.py" "$MON" "$OUT/klog.txt" "$SECS" 0.25
python3 "$HERE/tools/mon.py" "$OUT/monitor.sock" 'info registers -a' > "$OUT/regs.txt" 2>&1
kill $QPID 2>/dev/null; wait $QPID 2>/dev/null; wait $SPID
rm -f "$FIFO"
sed -n '1,20p' "$OUT/unimp.txt"
