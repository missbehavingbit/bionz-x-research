#!/usr/bin/env python3
"""Count guest breakpoint hits through QEMU's gdb stub (no gdb needed).

Read-only: it inserts TCG breakpoints (Z0) and write watchpoints (Z2),
records registers at each hit,
single-steps the stopped thread past the breakpoint, and continues.  It never
writes guest memory or registers.  It detaches after --seconds or when every
breakpoint has reached --max-hits.

usage (WSL, while run.sh runs with QEMU_EXTRA containing -gdb tcp::PORT):
  python3 tools/rsp_hitcount.py --port 12345 --seconds 90 \\
      --bp 63923874:vdseq --bp 63923a1a:publish --log ~/a6400-runs/x-rsp.log
"""

import argparse
import socket
import sys
import time


class RSP:
    def __init__(self, host, port, timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.s = socket.create_connection((host, port), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        self.s.settimeout(None)
        self.buf = b''

    def _read(self):
        while True:
            i = self.buf.find(b'$')
            if i >= 0:
                j = self.buf.find(b'#', i)
                if j >= 0 and len(self.buf) >= j + 3:
                    pkt = self.buf[i + 1:j]
                    self.buf = self.buf[j + 3:]
                    self.s.sendall(b'+')
                    return pkt.decode('latin-1')
            data = self.s.recv(65536)
            if not data:
                raise EOFError('gdb stub closed')
            self.buf += data

    def send(self, payload, want_reply=True):
        csum = sum(payload.encode('latin-1')) & 0xff
        self.s.sendall(b'$' + payload.encode('latin-1') + b'#%02x' % csum)
        return self._read() if want_reply else None

    def interrupt(self):
        self.s.sendall(b'\x03')
        return self._read()


def regs(rsp):
    g = rsp.send('g')
    vals = []
    for k in range(16):
        w = g[8 * k:8 * k + 8]
        vals.append(int.from_bytes(bytes.fromhex(w), 'little'))
    return vals


def stop_thread(reply):
    for part in reply[3:].split(';'):
        if part.startswith('thread:'):
            return part[7:]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=12345)
    ap.add_argument('--bp', action='append', default=[],
                    help='hexaddr[:label]')
    ap.add_argument('--wp', action='append', default=[],
                    help='write watchpoint hexaddr:len[:label]; logs the '
                         'writing PC and the value after the store')
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--max-hits', type=int, default=400)
    ap.add_argument('--samples', type=int, default=8,
                    help='register samples logged per breakpoint')
    ap.add_argument('--stack', type=int, default=0,
                    help='also log this many 32-bit words at sp per sample')
    ap.add_argument('--connect-timeout', type=float, default=60)
    ap.add_argument('--log', default='-')
    a = ap.parse_args()

    out = sys.stdout if a.log == '-' else open(a.log, 'w', buffering=1)
    bps = {}
    for spec in a.bp:
        addr, _, label = spec.partition(':')
        bps[int(addr, 16)] = {'label': label or addr, 'n': 0, 'on': True}

    wps = {}
    for spec in a.wp:
        f = spec.split(':')
        wps[int(f[0], 16)] = {'len': int(f[1]), 'label': f[2] if len(f) > 2
                              else f[0], 'pcs': {}, 'on': True}

    rsp = RSP(a.host, a.port, a.connect_timeout)
    t0 = time.monotonic()
    print('attach', rsp.send('?'), file=out)
    for addr in bps:
        r = rsp.send('Z0,%x,2' % addr)
        print('Z0 %08x %s -> %s' % (addr, bps[addr]['label'], r), file=out)
    for addr, w in wps.items():
        r = rsp.send('Z2,%x,%x' % (addr, w['len']))
        print('Z2 %08x %s -> %s' % (addr, w['label'], r), file=out)

    order = []
    try:
        while time.monotonic() - t0 < a.seconds:
            if not any(b['on'] for b in bps.values()) and                     not any(w['on'] for w in wps.values()):
                break
            reply = rsp.send('c')
            if not reply.startswith('T') and not reply.startswith('S'):
                print('stop', reply, file=out)
                break
            tid = stop_thread(reply)
            if tid:
                rsp.send('Hg' + tid)
            r = regs(rsp)
            pc = r[15] & ~1
            if 'watch:' in reply:
                waddr = int(reply.split('watch:')[1].split(';')[0], 16)
                w = wps.get(waddr) or next(
                    (v for k, v in wps.items()
                     if k <= waddr < k + v['len']), None)
                wa = next(k for k, v in wps.items() if v is w)
                rsp.send('z2,%x,%x' % (wa, w['len']))
                rsp.send('vCont;s:%s' % tid if tid else 's')
                val = rsp.send('m%x,%x' % (wa, w['len']))
                n = w['pcs'].get(pc, 0) + 1
                w['pcs'][pc] = n
                if len(order) < 4000:
                    order.append('%s@%08x=%s' % (w['label'], pc, val))
                if n <= a.samples:
                    print('%7.2fs W %-8s pc=%08x n=%d tid=%s new=%s ' % (
                        time.monotonic() - t0, w['label'], pc, n, tid, val) +
                        ' '.join('r%d=%08x' % (k, r[k]) for k in range(13)) +
                        ' lr=%08x' % r[14], file=out)
                if sum(w['pcs'].values()) >= a.max_hits:
                    w['on'] = False
                else:
                    rsp.send('Z2,%x,%x' % (wa, w['len']))
                continue
            b = bps.get(pc)
            if b is None:
                print('unexpected stop pc=%08x %s' % (pc, reply), file=out)
            else:
                b['n'] += 1
                if len(order) < 4000:
                    order.append(b['label'])
                if b['n'] <= a.samples:
                    print('%7.2fs %-10s n=%d tid=%s ' % (
                        time.monotonic() - t0, b['label'], b['n'], tid) +
                        ' '.join('r%d=%08x' % (k, r[k]) for k in range(13)) +
                        ' lr=%08x sp=%08x' % (r[14], r[13]), file=out)
                    if a.stack:
                        m = rsp.send('m%x,%x' % (r[13], 4 * a.stack))
                        words = [int.from_bytes(bytes.fromhex(m[8*i:8*i+8]),
                                                'little')
                                 for i in range(len(m) // 8)]
                        print('          stack ' + ' '.join(
                            '%08x' % w for w in words), file=out)
            # step past the breakpoint on this thread only
            rsp.send('z0,%x,2' % pc)
            if tid:
                rsp.send('vCont;s:%s' % tid)
            else:
                rsp.send('s')
            if b is not None and b['n'] >= a.max_hits:
                b['on'] = False
                print('%7.2fs %s reached max hits' % (
                    time.monotonic() - t0, b['label']), file=out)
            else:
                rsp.send('Z0,%x,2' % pc)
    except EOFError as e:
        print('stub closed:', e, file=out)
    finally:
        try:
            print('elapsed %.1fs' % (time.monotonic() - t0), file=out)
            for addr, b in bps.items():
                print('hits %08x %-10s %d' % (addr, b['label'], b['n']),
                      file=out)
            for addr, w in wps.items():
                for pc, n in sorted(w['pcs'].items()):
                    print('writes %08x %-8s pc=%08x %d' % (
                        addr, w['label'], pc, n), file=out)
            # compress the hit order into runs, e.g. "intHS x7"
            runs = []
            for lab in order:
                if runs and runs[-1][0] == lab:
                    runs[-1][1] += 1
                else:
                    runs.append([lab, 1])
            print('order ' + ' '.join('%s%s' % (l, '' if n == 1 else 'x%d' % n)
                                      for l, n in runs[:600]), file=out)
            for addr, b in bps.items():
                if b['on']:
                    rsp.send('z0,%x,2' % addr)
            for addr, w in wps.items():
                if w['on']:
                    rsp.send('z2,%x,%x' % (addr, w['len']))
            rsp.send('D', want_reply=True)
        except Exception as e:  # stub may already be gone
            print('detach error', e, file=out)


if __name__ == '__main__':
    main()
