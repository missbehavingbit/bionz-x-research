#!/usr/bin/env python3
"""Decode Sony's klog ring (raw pmemsave of klog.addr) into time-ordered lines."""
import re, sys
NOISE = ('Software Tuning is forbiden', 'hwtimer', 'caught up', 'expire(')
lines, seen = [], set()
raw = []
for f in sys.argv[1:]:
    raw += re.sub(rb'[^\x09\x0a\x20-\x7e]', b'\n', open(f, 'rb').read()).decode().split('\n')
for ln in raw:
    m = re.match(r'\s*\[\s*(\d+\.\d+)\]', ln)
    if not m or any(n in ln for n in NOISE) or ln in seen:
        continue
    seen.add(ln); lines.append((float(m.group(1)), ln))
for _, ln in sorted(lines, key=lambda x: x[0]):
    print(ln)
