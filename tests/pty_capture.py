"""Run pistat in a real PTY at a fixed size and dump the final screen via pyte."""
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

import pyte

COLS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
SECS = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
CMD = sys.argv[4:] or ["python3", "/home/kempi/pistat/pistat.py", "-i", "0.5"]

screen = pyte.Screen(COLS, ROWS)
stream = pyte.ByteStream(screen)

pid, fd = pty.fork()
if pid == 0:  # child
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLUMNS"] = str(COLS)
    os.environ["LINES"] = str(ROWS)
    os.execvp(CMD[0], CMD)

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

deadline = time.time() + SECS
while time.time() < deadline:
    r, _, _ = select.select([fd], [], [], 0.2)
    if fd in r:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        stream.feed(data)

os.kill(pid, signal.SIGKILL)
os.waitpid(pid, 0)

print("\n".join(line.rstrip() for line in screen.display))
print(f"\n[cursor at {screen.cursor.y},{screen.cursor.x}]")
