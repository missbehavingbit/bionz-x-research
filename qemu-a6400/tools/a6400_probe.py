#!/usr/bin/env python3
"""Event-driven probe runner for the sony-a6400 QEMU machine.

Starts run.sh, waits for the guest shell, polls /proc/snsc_boot_time until a
checkpoint string appears (or a guest-side deadline passes), runs the given
shell commands, collects their output between unique markers, and stops QEMU.
No fixed sleeps: a probe ends as soon as its answer exists.

Every stage gets a wall-clock timestamp, so speed options can be compared:
  start -> shell prompt -> checkpoint seen -> commands done.

usage (WSL, from qemu-a6400/):
  python3 tools/a6400_probe.py --until 'Og 001_ObjMic' \\
      --cmd 'cat /proc/interrupts' --log ~/a6400-runs/probe.log [--timeout 300]
Extra environment (QEMU_EXTRA, EXTRA, SUBMICRO_PAGE, ...) passes to run.sh.
Exit status: 0 = checkpoint found, 2 = timeout, 3 = QEMU exited early.
"""

import argparse
import os
import select
import signal
import subprocess
import sys
import time

SHELL_BANNER = b'built-in shell (ash)'


class Guest:
    def __init__(self, cmd, log_path, env):
        self.t0 = time.monotonic()
        self.log = open(log_path, 'wb')
        self.buf = bytearray()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=env,
                                     start_new_session=True)
        os.set_blocking(self.proc.stdout.fileno(), False)

    def elapsed(self):
        return time.monotonic() - self.t0

    def pump(self, timeout):
        """Read available output for up to `timeout` seconds."""
        fd = self.proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if ready:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                data = b''
            if data:
                self.buf += data
                self.log.write(data)
                self.log.flush()
                return True
            if self.proc.poll() is not None:
                raise EOFError
        return False

    def wait_for(self, needle, deadline, start=0):
        while True:
            i = self.buf.find(needle, start)
            if i >= 0:
                return i
            if time.monotonic() > deadline:
                return -1
            self.pump(0.5)

    def send(self, line):
        self.proc.stdin.write(line.encode() + b'\r')
        self.proc.stdin.flush()

    def run(self, command, deadline, tag):
        """Run one shell command; return its output text (between markers)."""
        begin, end = f'@@B{tag}@@', f'@@E{tag}@@'
        start = len(self.buf)
        self.send(f'echo {begin[:3]}""{begin[3:]}; {command}; '
                  f'echo {end[:3]}""{end[3:]}')
        b = self.wait_for(begin.encode(), deadline, start)
        if b < 0:
            return None
        e = self.wait_for(end.encode(), deadline, b)
        if e < 0:
            return None
        return self.buf[b + len(begin):e].decode('latin1').replace('\r', '')

    def monitor(self, commands):
        """Run HMP commands through the mon:stdio mux (Ctrl-A c)."""
        start = len(self.buf)
        self.proc.stdin.write(b'\x01c')
        self.proc.stdin.flush()
        for c in commands:
            end = time.monotonic() + 2
            self.send(c)
            while time.monotonic() < end:
                self.pump(0.2)
        self.proc.stdin.write(b'\x01c')
        self.proc.stdin.flush()
        self.pump(0.5)
        return self.buf[start:].decode('latin1').replace('\r', '')

    def stop(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--until', default='',
                    help='checkpoint text to wait for in /proc/snsc_boot_time')
    ap.add_argument('--cmd', action='append', default=[],
                    help='shell command to run once the checkpoint is seen')
    ap.add_argument('--timeout', type=float, default=300,
                    help='wall-clock limit in seconds')
    ap.add_argument('--poll', type=float, default=3,
                    help='seconds between checkpoint polls')
    ap.add_argument('--monitor', action='append', default=[],
                    help='HMP command to run after the shell commands '
                         '(e.g. "screendump /path/out.ppm")')
    ap.add_argument('--answer-timeout', type=float, default=120,
                    help='seconds without an answer before the shell counts '
                         'as silent (fail fast, then dump vCPU state).  A '
                         '/proc/snsc_boot_time read can block for over 30 s '
                         'during heavy boot steps (all vCPUs idle meanwhile), '
                         'then answer (E0530)')
    ap.add_argument('--log', default=os.path.expanduser('~/a6400-runs/probe.log'))
    ap.add_argument('--run', default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'run.sh'))
    args = ap.parse_args()

    env = dict(os.environ)
    env['TIMEOUT'] = '0'
    g = Guest(['bash', args.run], args.log, env)
    deadline = time.monotonic() + args.timeout
    status = 2
    stamps = {}
    try:
        if g.wait_for(SHELL_BANNER, deadline) < 0:
            print('timeout before the guest shell', file=sys.stderr)
            return 2
        stamps['shell'] = g.elapsed()
        g.wait_for(b'# ', deadline, g.buf.find(SHELL_BANNER))
        n = 0
        if args.until:
            pat = args.until.replace("'", "'\\''")
            while time.monotonic() < deadline:
                n += 1
                out = g.run(f"echo RES=$(grep -c '{pat}' /proc/snsc_boot_time)",
                            min(deadline, time.monotonic() + args.answer_timeout),
                            f'P{n}')
                if out is None:
                    # the shell stopped answering: fail fast and record where
                    # the vCPUs are (CPU state only, no device registers)
                    stamps['shell_silent'] = g.elapsed()
                    print(f'shell silent after {n - 1} answered polls',
                          flush=True)
                    print('==== monitor: vCPU PC/CPSR, two samples 2 s apart')
                    dump = g.monitor(['info registers -a', 'info registers -a'])
                    for ln in dump.splitlines():
                        if 'R15=' in ln or 'PSR=' in ln or 'CPU#' in ln:
                            print(ln.strip(), flush=True)
                    break
                # kernel printk lines interleave with the answer: parse the
                # tagged result only
                hits = [ln.split('RES=', 1)[1].strip()
                        for ln in (out or '').split('\n') if 'RES=' in ln
                        and not ln.lstrip().startswith('echo')]
                if hits and hits[-1].isdigit() and int(hits[-1]) > 0:
                    stamps['checkpoint'] = g.elapsed()
                    status = 0
                    break
                end = time.monotonic() + args.poll
                while time.monotonic() < end:
                    g.pump(0.2)
        else:
            status = 0
        for k, c in enumerate(args.cmd):
            # commands still get their own budget after a checkpoint timeout,
            # so a probe that timed out reports the state it timed out in
            out = g.run(c, max(deadline, time.monotonic() + args.answer_timeout),
                        f'C{k}')
            print(f'==== {c}')
            print(out if out is not None else '(no answer before timeout)')
        if args.monitor:
            print('==== monitor')
            print(g.monitor(args.monitor), flush=True)
        stamps['done'] = g.elapsed()
    except EOFError:
        print('QEMU exited early', file=sys.stderr)
        status = 3
    finally:
        g.stop()
    print('timing: ' + ', '.join(f'{k}={v:.1f}s' for k, v in stamps.items())
          + f' status={status}', flush=True)
    return status


if __name__ == '__main__':
    sys.exit(main())
