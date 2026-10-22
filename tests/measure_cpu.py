"""Measure pistat's own CPU load in a real PTY over N seconds."""
import os
import pty
import select
import signal
import struct
import sys
import termios
import fcntl
import time

COLS, ROWS, SECS = 120, 44, float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
INTERVAL = sys.argv[2] if len(sys.argv) > 2 else "1"

CLK = os.sysconf("SC_CLK_TCK")

pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.execvp("python3", ["python3", "/home/kempi/pistat/pistat.py", "-i", INTERVAL])

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))


def ticks(p):
    with open(f"/proc/{p}/stat", "rb") as fh:
        raw = fh.read()
    rp = raw.rfind(b")")
    f2 = raw[rp + 2:].split()
    return int(f2[11]) + int(f2[12])


# drain output so the app never blocks on a full pty buffer
def drain(t):
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if fd in r:
            try:
                if not os.read(fd, 1 << 16):
                    return
            except OSError:
                return


drain(3.0)
t0, c0 = time.time(), ticks(pid)
drain(SECS)
t1, c1 = time.time(), ticks(pid)
os.kill(pid, signal.SIGKILL)
os.waitpid(pid, 0)

wall = t1 - t0
cpu = (c1 - c0) / CLK
print(f"wall {wall:.1f}s  cpu {cpu:.2f}s  -> {100*cpu/wall:.1f}% of one core "
      f"({100*cpu/wall/4:.1f}% of this 4-core Pi)  interval={INTERVAL}s")
