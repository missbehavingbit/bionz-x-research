#!/usr/bin/env python3
"""Talk to the A7C console (UART0) exposed as a unix socket by run.sh.

    A7C_SERIAL=unix:out/console.sock,server,nowait ./run.sh &
    tools/a7cshell.py out/console.sock                 # interactive
    tools/a7cshell.py out/console.sock run 'uname -a'  # one command

The stock init starts /bin/ash on the console when the cmdline carries
console=ttyAM0 (run.sh sets it plus amba2.console=1).  Kernel messages are
interleaved with shell output; `run` wraps the command in markers and
prints only what is between them.
"""
import os
import select
import socket
import sys
import time
import uuid


def connect(path, wait=120):
    end = time.time() + wait
    while True:
        try:
            s = socket.socket(socket.AF_UNIX)
            s.connect(path)
            return s
        except OSError:
            if time.time() > end:
                raise
            time.sleep(0.5)


def run(s, cmd, timeout=30):
    tag = uuid.uuid4().hex[:8]
    s.sendall(('echo B%s; %s; echo E%s\n' % (tag, cmd, tag)).encode())
    buf, end = b'', time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([s], [], [], 0.5)
        if r:
            buf += s.recv(65536)
            t = buf.decode(errors='replace')
            b, e = t.find('\nB' + tag), t.find('\nE' + tag)
            if b >= 0 and e > b:
                body = t[b + 2 + len(tag):e]
                return '\n'.join(l for l in body.splitlines()
                                 if l.strip() and not l.startswith('[ '))
    return None


def interactive(s):
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        while True:
            r, _, _ = select.select([s, fd], [], [])
            if s in r:
                d = s.recv(65536)
                if not d:
                    break
                os.write(1, d)
            if fd in r:
                d = os.read(fd, 1024)
                if b'\x1d' in d:          # Ctrl-]
                    break
                s.sendall(d)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    s = connect(sys.argv[1])
    if len(sys.argv) > 3 and sys.argv[2] == 'run':
        out = run(s, sys.argv[3], float(os.environ.get('A7C_TIMEOUT', '30')))
        print(out if out is not None else '(no answer - shell not up yet?)')
    else:
        interactive(s)


if __name__ == '__main__':
    main()
