#!/bin/bash
# Headless boot for N seconds, capturing the kernel log.
#
#   tools/bootlog.sh [seconds] [extra qemu args...]
#
# The stock A7C kernel console is ttynull and the UART only carries Sony's
# exception monitor, so the useful log is the in-RAM klog ring
# (klog.addr=0x035A5000, size 0x20000 in the stock cmdline).  It wraps in
# seconds, so tools/klogtap.py samples it continuously over the QEMU monitor.
# Output: out/klog.txt (time-sorted), out/serial.log, out/regs.txt
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SECS="${1:-60}"; shift || true
OUT="$HERE/out"; mkdir -p "$OUT"
MON="$OUT/bootlog-mon.sock"; rm -f "$MON"

A7C_SERIAL="file:$OUT/serial.log" A7C_FRESH="${A7C_FRESH:-1}" timeout $((SECS + 30)) "$HERE/run.sh" \
    -monitor unix:"$MON",server,nowait \
    -d guest_errors,unimp -D "$OUT/qemu-d.log" "$@" \
    < /dev/null > "$OUT/qemu-out.log" 2>&1 &
QPID=$!

python3 "$HERE/tools/klogtap.py" "$MON" "$OUT/klog.txt" "$SECS" 0.25
python3 "$HERE/tools/mon.py" "$OUT/monitor.sock" 'info registers -a' > "$OUT/regs.txt" 2>&1
kill $QPID 2>/dev/null; wait 2>/dev/null
echo "serial: $(wc -c < "$OUT/serial.log") bytes"
grep -q 'register dump\|Oops\|Exception Syndrome' "$OUT/klog.txt" &&
    echo "crash dump present in out/klog.txt"
true
