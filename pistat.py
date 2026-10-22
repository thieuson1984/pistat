#!/usr/bin/env python3
"""
pistat — a real-time TUI dashboard for this machine (Raspberry Pi 4 / Debian).

Panels: CPU (per-core load, clock, governor), memory + swap, disks + I/O rates,
network throughput, Raspberry Pi thermal / clock / throttle / power telemetry,
rolling sparklines, and a sortable process table with state statistics.

  pistat.py               interactive TUI
  pistat.py --once        one plain-text snapshot, then exit
  pistat.py -i 0.5        sampling interval in seconds (default 1.0)

Keys: q quit · p pause · s cycle sort · S reverse · 1/2/5 interval · r reset
      peaks · / filter · j/k or arrows move · PgUp/PgDn · Home/End
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import ClassVar

import psutil
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Static

__version__ = "1.1.0"

MIN_INTERVAL = 0.1          # below this a 1 s timer would busy-loop the CPU
DEFAULT_WIDTH = 100         # --once fallback width when stdout is a pipe
NO_COLOR = bool(os.environ.get("NO_COLOR"))

BAR_FULL = "█"
BAR_EMPTY = "░"
SPARK = "▁▂▃▄▅▆▇█"

C_TITLE = "bold #7dd3fc"
C_LABEL = "#94a3b8"
C_DIM = "#64748b"
C_VAL = "#e2e8f0"
C_ACCENT = "#38bdf8"
C_OK = "#4ade80"
C_WARN = "#facc15"
C_HOT = "#fb923c"
C_BAD = "#f87171"
C_MAGENTA = "#e879f9"
C_TRACK = "#334155"

MOUNT_ALIAS = {
    "/": "/",
    "/boot/firmware": "boot/fw",
    "/var/lib/docker": "docker",
    "/var/lib/containers": "pods",
}

# /proc/<pid>/stat state letter -> psutil-style name
STATE_MAP = {
    "R": "running",
    "S": "sleeping",
    "D": "disk-sleep",
    "I": "idle",
    "T": "stopped",
    "t": "tracing-stop",
    "Z": "zombie",
    "X": "dead",
    "x": "dead",
    "W": "waking",
    "P": "parked",
}

THROTTLE_BITS = [
    (0, "under-voltage NOW"),
    (1, "arm freq capped NOW"),
    (2, "throttled NOW"),
    (3, "soft temp limit NOW"),
    (16, "under-voltage seen"),
    (17, "freq cap seen"),
    (18, "throttling seen"),
    (19, "soft temp limit seen"),
]


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #
def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024.0 or unit == "T":
            return f"{n:.0f}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}T"


def human_short(n: float) -> str:
    """Compact size for tight columns: 31G, 510M, 66M."""
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024.0 or unit == "T":
            return f"{n:.0f}{unit}"
        n /= 1024.0
    return f"{n:.0f}T"


def human_rate(bps: float) -> str:
    return human_bytes(bps) + "/s"


def temp_style(t: float | None) -> str:
    if t is None:
        return C_DIM
    if t >= 80:
        return f"bold {C_BAD}"
    if t >= 70:
        return f"bold {C_HOT}"
    if t >= 60:
        return C_WARN
    return C_OK


def usage_style(pct: float) -> str:
    if pct >= 90:
        return C_BAD
    if pct >= 70:
        return C_WARN
    if pct >= 40:
        return C_ACCENT
    return C_OK


def bar(pct: float, width: int, color: str = C_ACCENT) -> Text:
    pct = max(0.0, min(100.0, float(pct)))
    width = max(1, width)
    filled = round(width * pct / 100.0)
    t = Text()
    t.append(BAR_FULL * filled, style=color)
    t.append(BAR_EMPTY * (width - filled), style=C_TRACK)
    return t


def sparkline(values, width: int, color: str = C_ACCENT) -> Text:
    """Fixed-width block sparkline, scaled to its own peak."""
    width = max(0, width)
    t = Text()
    vals = list(values)[-width:] if width else []
    pad = width - len(vals)
    if pad > 0:
        t.append(SPARK[0] * pad, style=C_TRACK)
    peak = max(vals) if vals else 1.0
    peak = peak if peak > 0 else 1.0
    for v in vals:
        ratio = max(0.0, v) / peak
        idx = min(len(SPARK) - 1, int(ratio * (len(SPARK) - 1)))
        t.append(SPARK[idx], style=color if ratio > 0.5 else C_DIM)
    return t


def fit(text: Text, width: int) -> Text:
    """Clip every line to `width` cells so panels never wrap awkwardly."""
    if width <= 0:
        return text
    lines = text.split("\n")
    # drop blank tail lines (trailing "\n") without repeatedly copying the Text
    while len(lines) > 1 and not lines[-1].plain.strip():
        lines.pop()
    out = Text()
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        line.truncate(width)
        out.append_text(line)
    return out


def pad(text: Text, width: int) -> Text:
    """Right-pad a Text to `width` cells so a background style covers the row."""
    gap = width - text.cell_len
    if gap > 0:
        text.append(" " * gap)
    return text


def kv(label: str, value, style: str = C_VAL, width: int = 8, pad: int = 0) -> Text:
    t = Text()
    t.append(f"{label:<{width}}", style=C_LABEL)
    if isinstance(value, Text):
        t.append_text(value)
    else:
        t.append(f"{value!s:<{pad}}" if pad else str(value), style=style)
    return t


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def norm(name: str) -> str:
    """Normalise a sysfs sensor name: cpu-thermal == cpu_thermal == CPUThermal."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def fmt_uptime(sec: float) -> str:
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h:02d}h {m:02d}m" if d else f"{h:02d}h {m:02d}m"


def fmt_cpu_time(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --------------------------------------------------------------------------- #
# Raspberry Pi / sysfs probing
# --------------------------------------------------------------------------- #
def vcgencmd(cmd: str) -> str | None:
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(
            ["vcgencmd", cmd], capture_output=True, text=True, timeout=3, check=False
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def parse_vcg(out: str | None) -> float | None:
    if not out or "=" not in out:
        return None
    raw = out.split("=", 1)[1].strip().strip("'V°C")
    try:
        return float(raw)
    except ValueError:
        return None


def decode_throttled(raw: str | None) -> tuple[bool, list[str], str]:
    if not raw or "=" not in raw:
        return False, [], raw or "n/a"
    try:
        val = int(raw.split("=", 1)[1], 16)
    except ValueError:
        return False, [], raw
    flags = [name for bit, name in THROTTLE_BITS if val & (1 << bit)]
    return val == 0, flags, f"0x{val:x}"


# --------------------------------------------------------------------------- #
# sampling
# --------------------------------------------------------------------------- #
class Collector:
    """Produces one coherent snapshot per call. Blocking but cheap (~50-120 ms)."""

    def __init__(self, history: int = 150):
        self._last_t: float | None = None
        self._net: dict[str, tuple[float, float]] = {}
        self._io: tuple[float, float] | None = None
        self._swap: tuple[float, float] | None = None
        self._swap_rate = (0.0, 0.0)
        self._procs: dict[int, psutil.Process] = {}
        self._users: dict[int, str] = {}
        self._uids: dict[int, int] = {}
        self._names: dict[int, str] = {}
        self._cpu_ticks: dict[int, tuple[int, int]] = {}
        self.has_proc = Path("/proc/self/stat").exists()
        self._clk = self._sysconf("SC_CLK_TCK", 100)
        self._page = self._sysconf("SC_PAGE_SIZE", 4096)
        self._cmdlines: dict[int, str] = {}
        self._pi_cache: dict = {}
        self._pi_ts: float = 0.0
        self._io_totals: tuple[float, float] = (0.0, 0.0)
        self.cpu_hist: deque[float] = deque(maxlen=history)
        self.temp_hist: deque[float] = deque(maxlen=history)
        self.load_hist: deque[float] = deque(maxlen=history)
        self.net_hist: deque[float] = deque(maxlen=history)
        self.procs_hist: deque[float] = deque(maxlen=history)
        self.peak_temp = 0.0
        self.peak_cpu = 0.0
        self.peak_mem = 0.0
        self.board = (read_text("/proc/device-tree/model") or "").replace("\x00", "")
        self.ncpu = psutil.cpu_count(logical=True) or 1
        # prime the CPU deltas so the first sample is meaningful
        try:
            psutil.cpu_percent(interval=0.15, percpu=True)
        except (psutil.Error, OSError, NotImplementedError):
            pass
        self._processes(1.0, 0)

    @staticmethod
    def _sysconf(name: str, fallback: int) -> int:
        try:
            return int(os.sysconf(name))
        except (ValueError, OSError, AttributeError):
            return fallback

    # -- sensors ---------------------------------------------------------- #
    def _temperatures(self) -> dict[str, float]:
        temps: dict[str, float] = {}
        zones: set[str] = set()
        try:
            for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
                raw = read_text(str(zone / "temp"))
                kind = read_text(str(zone / "type")) or zone.name
                if raw and raw.lstrip("-").isdigit():
                    temps[kind] = int(raw) / 1000.0
                    zones.add(norm(kind))
        except OSError:
            pass
        try:
            for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
                name = read_text(str(hw / "name")) or hw.name
                same_die = norm(name) in zones
                for f in sorted(hw.glob("temp*_input")):
                    raw = read_text(str(f))
                    if not (raw and raw.lstrip("-").isdigit()):
                        continue
                    # the die sensor is exported through both thermal_zone and
                    # hwmon — skip the duplicate only when the driver names match,
                    # so two genuinely distinct sensors at the same °C both survive
                    if same_die:
                        continue
                    label = read_text(str(f)[: -len("_input")] + "_label")
                    temps[label or f"{name}/temp"] = int(raw) / 1000.0
        except OSError:
            pass
        pmic = parse_vcg(vcgencmd("measure_temp pmic"))
        if pmic is not None:
            temps["pmic"] = pmic
        return temps

    def _fans(self) -> dict[str, int]:
        """Fan tachometers, if the case/HAT exposes any (Pi 4 has no SoC fan)."""
        fans: dict[str, int] = {}
        try:
            for hw in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
                name = read_text(str(hw / "name")) or hw.name
                for f in sorted(hw.glob("fan*_input")):
                    raw = read_text(str(f))
                    if raw and raw.isdigit():
                        label = read_text(str(f)[: -len("_input")] + "_label")
                        fans[label or name] = int(raw)
        except OSError:
            pass
        return fans

    def _core_freqs(self) -> list[float]:
        freqs = []
        for i in range(self.ncpu):
            raw = read_text(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq")
            freqs.append(int(raw) / 1000.0 if raw and raw.isdigit() else 0.0)
        return freqs

    def _uv_alarm(self) -> bool:
        """rpi_volt hwmon latches an under-voltage alarm that survives reboots."""
        try:
            for hw in Path("/sys/class/hwmon").glob("hwmon*"):
                if (read_text(str(hw / "name")) or "") == "rpi_volt":
                    return read_text(str(hw / "in0_lcrit_alarm")) == "1"
        except OSError:
            pass
        return False

    def _pi_block(self, ttl: float = 2.5) -> dict:
        """vcgencmd forks a process per call — refresh at most every `ttl` s."""
        now = time.monotonic()
        if self._pi_cache and now - self._pi_ts < ttl:
            return self._pi_cache
        ok, flags, raw = decode_throttled(vcgencmd("get_throttled"))
        if self._uv_alarm():
            flags = [*flags, "UV alarm latched (in0_lcrit)"]
            ok = False
        clocks = {}
        for name in ("arm", "core", "v3d"):
            v = parse_vcg(vcgencmd(f"measure_clock {name}"))
            if v:
                clocks[name] = v
        self._pi_cache = {
            "throttle_ok": ok,
            "throttle_flags": flags,
            "throttle_raw": raw,
            "clocks": clocks,
            "volts": parse_vcg(vcgencmd("measure_volts core")),
            "governor": read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        }
        self._pi_ts = now
        return self._pi_cache

    # -- processes -------------------------------------------------------- #
    def _username(self, uid: int) -> str:
        name = self._users.get(uid)
        if name is None:
            try:
                name = pwd.getpwuid(uid).pw_name
            except KeyError:
                name = str(uid)
            self._users[uid] = name
        return name

    def cmdline(self, pid: int) -> str:
        """Full command line, fetched lazily and cached (only visible rows need it)."""
        cached = self._cmdlines.get(pid)
        if cached is not None:
            return cached
        text = ""
        if self.has_proc:
            try:
                text = Path(f"/proc/{pid}/cmdline").read_bytes().replace(
                    b"\x00", b" "
                ).decode("utf-8", "replace").strip()
            except OSError:
                text = ""
        else:
            try:
                text = " ".join(psutil.Process(pid).cmdline()).strip()
            except psutil.Error:
                text = ""
        if not text:
            text = self._names.get(pid, "?")
        self._cmdlines[pid] = text
        return text

    # -- fast path: parse /proc directly (7x cheaper than psutil per-process) -- #
    def _scan_proc(self, dt: float, mem_total: int) -> tuple[list[dict], dict, int]:
        rows, states, threads = [], {}, 0
        seen = set()
        clk = self._clk
        page = self._page
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                with open(f"/proc/{pid}/stat", "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            lp = raw.find(b"(")
            rp = raw.rfind(b")")
            if lp < 0 or rp < 0:
                continue
            try:
                name = raw[lp + 1:rp].decode("utf-8", "replace")
                f2 = raw[rp + 2:].split()
                state = STATE_MAP.get(f2[0].decode("ascii", "replace"), "unknown")
                ticks = int(f2[11]) + int(f2[12])
                nice = int(f2[16])
                nthreads = int(f2[17])
                starttime = int(f2[19])
                rss = int(f2[21]) * page
            except (IndexError, ValueError):
                continue
            seen.add(pid)
            prev = self._cpu_ticks.get(pid)
            if prev is not None and prev[0] != starttime:
                # pid recycled: cached user / name / cmdline belong to the old process
                self._uids.pop(pid, None)
                self._names.pop(pid, None)
                self._cmdlines.pop(pid, None)
            if prev is None or prev[0] != starttime:
                cpu = 0.0                      # new (or recycled) pid: no baseline yet
            else:
                cpu = max(0.0, (ticks - prev[1]) * 100.0 / (clk * dt))
            self._cpu_ticks[pid] = (starttime, ticks)

            uid = self._uids.get(pid)
            if uid is None:
                try:
                    uid = os.stat(f"/proc/{pid}").st_uid
                except OSError:
                    uid = 0
                self._uids[pid] = uid

            self._names[pid] = name
            states[state] = states.get(state, 0) + 1
            threads += nthreads
            rows.append(
                {
                    "pid": pid,
                    "user": self._username(uid)[:9],
                    "name": name,
                    "cpu": cpu,
                    "mem": (100.0 * rss / mem_total) if mem_total else 0.0,
                    "rss": rss,
                    "threads": nthreads,
                    "state": state,
                    "nice": nice,
                    "cputime": ticks / clk,
                }
            )
        for cache in (self._cpu_ticks, self._uids, self._names, self._cmdlines):
            for pid in [k for k in cache if k not in seen]:
                cache.pop(pid, None)
        return rows, states, threads

    # -- portable fallback via psutil (non-Linux hosts) ------------------- #
    def _scan_psutil(self, dt: float, mem_total: int) -> tuple[list[dict], dict, int]:
        rows, states, threads = [], {}, 0
        alive = set()
        for pid in psutil.pids():
            alive.add(pid)
            proc = self._procs.get(pid)
            if proc is None:
                try:
                    proc = psutil.Process(pid)
                    proc.cpu_percent(None)
                    self._uids[pid] = proc.uids().real
                except psutil.Error:
                    continue
                self._procs[pid] = proc
            try:
                with proc.oneshot():
                    name = proc.name()
                    st = proc.status() or "?"
                    nthreads = proc.num_threads() or 0
                    rss = proc.memory_info().rss
                    cpu = proc.cpu_percent(None) or 0.0
                    nice = proc.nice() or 0
                    ct = proc.cpu_times()
            except psutil.Error:
                self._procs.pop(pid, None)
                continue
            self._names[pid] = name
            states[st] = states.get(st, 0) + 1
            threads += nthreads
            rows.append(
                {
                    "pid": pid,
                    "user": self._username(self._uids.get(pid, 0))[:9],
                    "name": name or "?",
                    "cpu": cpu,
                    "mem": (100.0 * rss / mem_total) if mem_total else 0.0,
                    "rss": rss,
                    "threads": nthreads,
                    "state": st,
                    "nice": nice,
                    "cputime": ct.user + ct.system,
                }
            )
        for pid in [p for p in self._procs if p not in alive]:
            self._procs.pop(pid, None)
            self._cmdlines.pop(pid, None)
            self._uids.pop(pid, None)
        return rows, states, threads

    def _processes(self, dt: float, mem_total: int) -> tuple[list[dict], dict, int]:
        if self.has_proc:
            return self._scan_proc(dt, mem_total)
        return self._scan_psutil(dt, mem_total)

    # -- one snapshot ----------------------------------------------------- #
    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(1e-3, now - self._last_t) if self._last_t else 1.0
        self._last_t = now
        wall = time.time()

        try:
            per_core = psutil.cpu_percent(interval=None, percpu=True) or [0.0]
        except (psutil.Error, NotImplementedError):
            per_core = [0.0] * self.ncpu
        cpu_total = sum(per_core) / len(per_core)
        try:
            load = os.getloadavg()
        except OSError:
            load = (0.0, 0.0, 0.0)

        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        temps = self._temperatures()
        soc_temp = temps.get("cpu-thermal") or temps.get("cpu_thermal")
        if soc_temp is None:
            # never mistake the PMIC (board regulator) for the SoC
            die = {k: v for k, v in temps.items() if k != "pmic"}
            if die:
                soc_temp = max(die.values())

        # psutil's sin/sout are cumulative bytes since boot — derive real rates
        if self._swap is not None:
            self._swap_rate = (
                max(0.0, (sw.sin - self._swap[0]) / dt),
                max(0.0, (sw.sout - self._swap[1]) / dt),
            )
        self._swap = (sw.sin, sw.sout)

        mounts, seen = [], set()
        try:
            parts = psutil.disk_partitions(all=False)
        except (psutil.Error, OSError):
            parts = []
        for p in parts:
            if p.device in seen or not p.device.startswith("/dev"):
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except (psutil.Error, OSError):
                continue
            mounts.append(
                {
                    "mount": p.mountpoint,
                    "device": os.path.basename(p.device),
                    "fstype": p.fstype,
                    "total": u.total,
                    "used": u.used,
                    "pct": u.percent,
                }
            )
        mounts.sort(key=lambda m: (m["mount"] != "/", m["mount"]))

        io_rate = {"read": 0.0, "write": 0.0}
        try:
            io = psutil.disk_io_counters()
        except Exception:  # noqa: BLE001 — disk stats are optional, never fatal
            io = None
        if io is not None:
            if self._io is not None:
                io_rate["read"] = max(0.0, (io.read_bytes - self._io[0]) / dt)
                io_rate["write"] = max(0.0, (io.write_bytes - self._io[1]) / dt)
            self._io = (io.read_bytes, io.write_bytes)
            self._io_totals = (io.read_bytes, io.write_bytes)

        nets = []
        try:
            nic = psutil.net_io_counters(pernic=True)
            addrs = psutil.net_if_addrs()
        except Exception:  # noqa: BLE001 — network stats are optional, never fatal
            nic, addrs = {}, {}
        for name, c in nic.items():
            if name == "lo" or name.startswith(("veth", "br-", "docker", "virbr")):
                continue
            prev = self._net.get(name)
            rx = max(0.0, (c.bytes_recv - prev[0]) / dt) if prev else 0.0
            tx = max(0.0, (c.bytes_sent - prev[1]) / dt) if prev else 0.0
            self._net[name] = (c.bytes_recv, c.bytes_sent)
            ip = next(
                (a.address for a in addrs.get(name, [])
                 if getattr(a, "family", None) == socket.AF_INET),
                "",
            )
            nets.append(
                {
                    "name": name,
                    "ip": ip,
                    "state": read_text(f"/sys/class/net/{name}/operstate") or "?",
                    "rx": rx,
                    "tx": tx,
                    "rx_total": c.bytes_recv,
                    "tx_total": c.bytes_sent,
                }
            )
        nets.sort(key=lambda n: n["name"])

        rows, states, threads = self._processes(dt, vm.total)

        self.cpu_hist.append(cpu_total)
        self.load_hist.append(load[0])
        self.net_hist.append(sum(n["rx"] + n["tx"] for n in nets))
        self.procs_hist.append(float(len(rows)))
        if soc_temp is not None:
            self.temp_hist.append(soc_temp)
        self.peak_cpu = max(self.peak_cpu, cpu_total)
        self.peak_mem = max(self.peak_mem, vm.percent)
        if soc_temp is not None:
            self.peak_temp = max(self.peak_temp, soc_temp)

        try:
            freq = psutil.cpu_freq()
        except (psutil.Error, NotImplementedError):
            freq = None
        return {
            "ts": wall,
            "uptime": self._uptime(wall),
            "cpu_total": cpu_total,
            "per_core": per_core,
            "core_freqs": self._core_freqs(),
            "load": load,
            "freq": freq.current if freq else 0.0,
            "mem": vm,
            "swap": sw,
            "swap_rate": self._swap_rate,
            "temps": temps,
            "fans": self._fans(),
            "soc_temp": soc_temp,
            "mounts": mounts[:4],
            "mounts_total": len(mounts),
            "io": io_rate,
            "io_totals": self._io_totals,
            "nets": nets[:3],
            "nets_total": len(nets),
            "procs": rows,
            "states": states,
            "threads": threads,
            "pi": self._pi_block(),
            "board": self.board,
            "host": socket.gethostname(),
            "peak_cpu": self.peak_cpu,
            "peak_mem": self.peak_mem,
            "peak_temp": self.peak_temp,
        }

    @staticmethod
    def _uptime(wall: float) -> float:
        """/proc/uptime is exact and free; psutil.boot_time() is the fallback."""
        raw = read_text("/proc/uptime")
        if raw:
            try:
                return float(raw.split()[0])
            except (IndexError, ValueError):
                pass
        try:
            return wall - psutil.boot_time()
        except (psutil.Error, OSError):
            return 0.0


# --------------------------------------------------------------------------- #
# renderers — pure (snapshot, width) -> rich Text; shared by TUI and --once
# --------------------------------------------------------------------------- #
def render_cpu(s: dict, width: int = 46, height: int | None = None) -> Text:
    t = Text()
    bw = max(8, min(12, width - 20))
    load, gov = s["load"], s["pi"].get("governor") or "n/a"
    clock = s["freq"] or max(s["core_freqs"] or [0]) or 0

    t.append("load   ", style=C_LABEL)
    t.append(f"{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}", style=C_VAL)
    t.append(f"  {gov}\n", style=C_DIM)

    t.append("clock  ", style=C_LABEL)
    t.append(f"{clock/1000:.2f}GHz" if clock else "n/a", style=C_ACCENT)
    t.append(f"  peak {s['peak_cpu']:.0f}%   {len(s['per_core'])} cores\n", style=C_WARN)

    total = s["cpu_total"]
    tc = usage_style(total)
    t.append("all    ", style=C_LABEL)
    t.append_text(bar(total, bw, tc))
    t.append(f" {total:5.1f}%\n", style=tc)

    cores = s["per_core"]
    # three header lines + one line per core; keep the "+N more" note visible
    room = len(cores) if height is None else max(1, height - 3)
    if len(cores) > room:
        room = max(1, room - 1)
    for i, pct in enumerate(cores[:room]):
        c = usage_style(pct)
        f = s["core_freqs"][i] if i < len(s["core_freqs"]) else 0
        t.append(f"cpu{i:<2}  ", style=C_LABEL)
        t.append_text(bar(pct, bw, c))
        t.append(f" {pct:5.1f}%", style=c)
        t.append(f" {f/1000:4.2f}G\n" if f else "\n", style=C_DIM)
    if len(cores) > room:
        t.append(f"… {len(cores) - room} more cores\n", style=C_DIM)
    return t


def render_mem(s: dict, width: int = 46) -> Text:
    vm, sw = s["mem"], s["swap"]
    t = Text()
    bw = max(8, min(10, width - 24))

    mc = usage_style(vm.percent)
    t.append("ram    ", style=C_LABEL)
    t.append_text(bar(vm.percent, bw, mc))
    t.append(f" {vm.percent:4.1f}%", style=mc)
    t.append(f" {human_short(vm.used)}/{human_short(vm.total)}\n", style=C_VAL)

    if not sw.total:
        t.append("swap   ", style=C_LABEL)
        t.append("disabled\n", style=C_DIM)
        t.append("swapio ", style=C_LABEL)
        t.append("—\n", style=C_DIM)
    else:
        sc = C_OK if sw.percent < 25 else (C_WARN if sw.percent < 60 else C_BAD)
        t.append("swap   ", style=C_LABEL)
        t.append_text(bar(sw.percent, bw, sc))
        t.append(f" {sw.percent:4.1f}%", style=sc)
        t.append(f" {human_short(sw.used)}/{human_short(sw.total)}\n", style=C_VAL)

        sin, sout = s.get("swap_rate", (0.0, 0.0))
        t.append("swapio ", style=C_LABEL)
        t.append(f"in {human_rate(sin)}", style=C_ACCENT)
        t.append(f"  out {human_rate(sout)}\n", style=C_WARN)
    t.append_text(kv("free", human_short(vm.available), C_OK, 7, pad=7))
    t.append_text(kv("cache", human_short(getattr(vm, "cached", 0)), C_DIM, 7))
    t.append("\n")
    t.append_text(kv("procs", f"{len(s['procs'])}", C_VAL, 7, pad=6))
    t.append_text(kv("thr", f"{s['threads']}", C_VAL, 6, pad=6))
    t.append(f"peak {s['peak_mem']:.0f}%\n", style=C_WARN)
    t.append("states ", style=C_LABEL)
    t.append(state_summary(s["states"]), style=C_DIM)
    return t


def state_summary(states: dict) -> str:
    short = {"running": "run", "sleeping": "slp", "idle": "idle", "disk-sleep": "dslp",
             "stopped": "stop", "zombie": "zomb", "tracing-stop": "trce",
             "waking": "wake", "parked": "park", "dead": "dead"}
    order = ["running", "sleeping", "idle", "disk-sleep", "stopped", "zombie"]
    bits = [f"{short.get(k, k[:4])} {states[k]}" for k in order if states.get(k)]
    bits += [f"{short.get(k, k[:4])} {v}" for k, v in sorted(states.items()) if k not in order]
    return " ".join(bits)


def render_disk(s: dict, width: int = 46) -> Text:
    t = Text()
    bw = max(5, min(8, width - 26))
    for m in s["mounts"]:
        c = usage_style(m["pct"])
        label = MOUNT_ALIAS.get(m["mount"], m["mount"])
        if len(label) > 10:
            label = "…" + label[-9:]
        t.append(f"{label:<11}", style=C_LABEL)
        t.append_text(bar(m["pct"], bw, c))
        t.append(f" {m['pct']:3.0f}% ", style=c)
        t.append(f"{human_short(m['used'])}/{human_short(m['total'])}\n", style=C_DIM)
    io, tot = s["io"], s.get("io_totals", (0, 0))
    t.append("r ", style=C_LABEL)
    t.append(f"{human_rate(io['read']):>9}", style=C_ACCENT)
    t.append("   w ", style=C_LABEL)
    t.append(f"{human_rate(io['write']):>9}\n", style=C_WARN)
    t.append("tot ", style=C_LABEL)
    t.append(f"r {human_short(tot[0])}", style=C_DIM)
    t.append(f"  w {human_short(tot[1])}", style=C_DIM)
    hidden = s.get("mounts_total", len(s["mounts"])) - len(s["mounts"])
    if hidden > 0:
        t.append(f"  +{hidden} mount{'s' if hidden != 1 else ''}", style=C_WARN)
    t.append("\n")
    if s["mounts"]:
        root = s["mounts"][0]
        t.append("free ", style=C_LABEL)
        t.append(f"{human_short(root['total'] - root['used'])}", style=C_OK)
        t.append(f" on {root['mount']} ({root['fstype']})", style=C_DIM)
    return t


def render_net(s: dict, width: int = 46) -> Text:
    t = Text()
    nets = s["nets"]
    if not nets:
        return Text("no active interfaces", style=C_DIM)
    for n in nets:
        up = n["state"] == "up"
        t.append_text(Text("● ", style=C_OK if up else C_BAD))
        t.append(f"{n['name']:<7}", style=C_VAL)
        t.append(f"{n['ip'] or 'link down'}", style=C_DIM)
        t.append("\n")
        t.append("  down ", style=C_LABEL)
        t.append(f"{human_rate(n['rx']):>9}", style=C_ACCENT)
        t.append("   up ", style=C_LABEL)
        t.append(f"{human_rate(n['tx']):>9}\n", style=C_WARN)
        t.append("  tot  ", style=C_DIM)
        t.append(f"{human_short(n['rx_total'])}", style=C_DIM)
        t.append(f" / {human_short(n['tx_total'])}\n", style=C_DIM)
    t.append("total  ", style=C_LABEL)
    t.append(f"rx {human_short(sum(n['rx_total'] for n in nets))}", style=C_DIM)
    t.append(f"  tx {human_short(sum(n['tx_total'] for n in nets))}", style=C_DIM)
    hidden = s.get("nets_total", len(nets)) - len(nets)
    if hidden > 0:
        t.append(f"  +{hidden} iface{'s' if hidden != 1 else ''}", style=C_WARN)
    return t


def short_board(board: str) -> str:
    if not board:
        return "unknown board"
    return board.replace("Raspberry Pi ", "RPi ").replace(" Model ", " ")


def render_pi(s: dict, width: int = 46) -> Text:
    pi, temps = s["pi"], s["temps"]
    t = Text()
    temp = s["soc_temp"]
    pmic = temps.get("pmic")

    t.append("soc    ", style=C_LABEL)
    t.append(f"{temp:5.1f}°C" if temp is not None else "  n/a ", style=temp_style(temp))
    t.append("   pmic ", style=C_LABEL)
    t.append(f"{pmic:4.1f}°C\n" if pmic else " n/a \n", style=temp_style(pmic))

    t.append("peak   ", style=C_LABEL)
    t.append(f"{s['peak_temp']:5.1f}°C", style=C_WARN)
    t.append("   up ", style=C_LABEL)
    t.append(f"{fmt_uptime(s['uptime'])}\n", style=C_VAL)

    t.append("flags  ", style=C_LABEL)
    if pi["throttle_ok"]:
        t.append("OK · no throttle\n", style=f"bold {C_OK}")
    else:
        t.append(" ".join(pi["throttle_flags"]) or pi["throttle_raw"], style=f"bold {C_BAD}")
        t.append(f" {pi['throttle_raw']}\n", style=C_DIM)

    cl = pi["clocks"]
    t.append("clk    ", style=C_LABEL)
    for k in ("arm", "core"):
        if k in cl:
            v = cl[k]
            t.append(f"{k} ", style=C_DIM)
            t.append(f"{v/1e9:.2f}G " if v >= 1e8 else f"{v/1e6:.0f}M ", style=C_VAL)
    t.append("\n")

    t.append("       ", style=C_LABEL)
    if "v3d" in cl:
        t.append("v3d ", style=C_DIM)
        t.append(f"{cl['v3d']/1e9:.2f}G  ", style=C_VAL)
    t.append("volt ", style=C_DIM)
    t.append(f"{pi['volts']:.3f}V" if pi["volts"] else "n/a", style=C_VAL)
    fans = s.get("fans") or {}
    if fans:
        t.append("  fan ", style=C_LABEL)
        t.append(f"{max(fans.values())}rpm", style=C_VAL)
    t.append("\n")

    t.append("board  ", style=C_LABEL)
    t.append(short_board(s["board"])[:24] + "\n", style=C_DIM)

    t.append("head   ", style=C_LABEL)
    if temp is not None:
        head = 80.0 - temp
        t.append(f"{head:4.1f}°C", style=C_OK if head > 20 else (C_WARN if head > 8 else C_BAD))
        t.append(" to 80°C soft limit", style=C_DIM)
    else:
        t.append("n/a", style=C_DIM)
    return t


def render_hist(s: dict, c: Collector, width: int = 46) -> Text:
    w = max(10, width - 16)
    t = Text()
    t.append("cpu   ", style=C_LABEL)
    t.append_text(sparkline(c.cpu_hist, w, C_ACCENT))
    t.append(f" {s['cpu_total']:4.1f}%\n", style=C_ACCENT)
    t.append("temp  ", style=C_LABEL)
    t.append_text(sparkline(c.temp_hist, w, C_HOT))
    t.append(f" {(s['soc_temp'] or 0):4.1f}°\n", style=C_HOT)
    t.append("load  ", style=C_LABEL)
    t.append_text(sparkline(c.load_hist, w, C_OK))
    t.append(f" {s['load'][0]:4.2f}\n", style=C_OK)
    t.append("net   ", style=C_LABEL)
    t.append_text(sparkline(c.net_hist, w, C_WARN))
    t.append(f" {human_rate(sum(n['rx']+n['tx'] for n in s['nets'])):>7}\n", style=C_WARN)
    t.append("procs ", style=C_LABEL)
    t.append_text(sparkline(c.procs_hist, w, C_MAGENTA))
    t.append(f" {len(s['procs']):>7}\n", style=C_MAGENTA)
    t.append("uptime ", style=C_LABEL)
    t.append(f"{fmt_uptime(s['uptime'])}", style=C_VAL)
    t.append("  sampled ", style=C_LABEL)
    t.append(time.strftime("%H:%M:%S", time.localtime(s["ts"])), style=C_DIM)
    return t


# --------------------------------------------------------------------------- #
# process table — one column model shared by the TUI and --once
# --------------------------------------------------------------------------- #
# key: (header, width, alignment)
PROC_COLS: dict[str, tuple[str, int, str]] = {
    "pid": ("PID", 7, ">"),
    "user": ("USER", 9, "<"),
    "ni": ("NI", 2, ">"),
    "cpu": ("CPU%", 5, ">"),
    "mem": ("MEM%", 5, ">"),
    "rss": ("RSS", 6, ">"),
    "thr": ("THR", 4, ">"),
    "state": ("STATE", 9, "<"),
    "time": ("TIME+", 7, ">"),
}
PROC_ORDER = ("pid", "user", "ni", "cpu", "mem", "rss", "thr", "state", "time")
# shed in this order as the terminal narrows: PID, CPU% and COMMAND always stay
PROC_DROP = ("ni", "thr", "time", "user", "state", "mem", "rss")
MIN_CMD = 12


def pick_cols(width: int) -> tuple[list[str], int]:
    """Which columns fit in `width` cells, and how wide COMMAND can be."""
    cols = list(PROC_ORDER)

    def used(cs: list[str]) -> int:
        return sum(PROC_COLS[c][1] + 1 for c in cs) + 2   # gutters + gap before COMMAND

    for c in PROC_DROP:
        if used(cols) + MIN_CMD <= width:
            break
        if c in cols:
            cols.remove(c)
    return cols, max(6, width - used(cols))


def proc_header(cols: list[str], cmd_w: int) -> Text:
    t = Text(style=C_TITLE)
    for c in cols:
        head, w, al = PROC_COLS[c]
        t.append(f"{head:{al}{w}} ")
    t.append(f"{'COMMAND':<{cmd_w}}")
    return t


def proc_row(r: dict, cols: list[str], cmd_w: int, command: str,
             selected: bool = False) -> Text:
    cpu, mem = r["cpu"], r["mem"]
    cstyle = C_DIM if cpu < 1 else (C_ACCENT if cpu < 40 else C_WARN if cpu < 90 else C_BAD)
    mstyle = C_DIM if mem < 1 else (C_ACCENT if mem < 25 else C_WARN)
    sstyle = {"running": C_OK, "zombie": C_BAD, "stopped": C_WARN}.get(r["state"], C_DIM)
    rss = human_bytes(r["rss"])
    if len(rss) > PROC_COLS["rss"][1]:
        rss = human_short(r["rss"])
    cells = {
        "pid": (str(r["pid"]), C_VAL),
        "user": (r["user"], C_DIM),
        "ni": (str(r["nice"]), C_DIM),
        "cpu": (f"{cpu:.1f}", cstyle),
        "mem": (f"{mem:.1f}", mstyle),
        "rss": (rss, C_VAL),
        "thr": (str(r["threads"]), C_DIM),
        "state": (r["state"], sstyle),
        "time": (fmt_cpu_time(r["cputime"]), C_DIM),
    }
    line = Text(style="on #1d4ed8" if selected else "")
    for c in cols:
        _head, w, al = PROC_COLS[c]
        text, style = cells[c]
        line.append(f"{text[:w]:{al}{w}} ", style=style)
    line.append(command[:cmd_w], style=C_VAL)
    return line


# --------------------------------------------------------------------------- #
# Textual UI
# --------------------------------------------------------------------------- #
CSS = """
Screen { background: #0b1220; }
Header { background: #111c33; color: #7dd3fc; }
Footer { background: #111c33; }
Footer > .footer--key { background: #1e293b; color: #7dd3fc; text-style: bold; }
Footer > .footer--description { color: #94a3b8; }
#body { height: 1fr; padding: 0 1; }
#top { layout: grid; grid-gutter: 0 1; height: 18; overflow: hidden; }
/* g<cols><bands> — every band is 9 rows tall, so the grid always matches its height */
#top.g31 { height: 9;  grid-size: 3 1; grid-columns: 1fr 1fr 1fr; grid-rows: 9; }
#top.g32 { height: 18; grid-size: 3 2; grid-columns: 1fr 1fr 1fr; grid-rows: 9 9; }
#top.g21 { height: 9;  grid-size: 2 1; grid-columns: 1fr 1fr; grid-rows: 9; }
#top.g22 { height: 18; grid-size: 2 2; grid-columns: 1fr 1fr; grid-rows: 9 9; }
#top.g11 { height: 9;  grid-size: 1 1; grid-columns: 1fr; grid-rows: 9; }
#top.g12 { height: 18; grid-size: 1 2; grid-columns: 1fr; grid-rows: 9 9; }
#top.g13 { height: 27; grid-size: 1 3; grid-columns: 1fr; grid-rows: 9 9 9; }

Panel {
    border: round #1e3a5f;
    background: #0e1626;
    color: #e2e8f0;
    padding: 0 1;
    height: 1fr;
    width: 1fr;
    text-wrap: nowrap;
    text-overflow: ellipsis;
    border-title-color: #7dd3fc;
    border-title-style: bold;
}

#p_cpu { border-title-color: #38bdf8; }
#p_mem { border-title-color: #a78bfa; }
#p_disk { border-title-color: #22d3ee; }
#p_net { border-title-color: #34d399; }
#p_pi { border-title-color: #fb923c; }
#p_hist { border-title-color: #f472b6; }

#procsbox { height: 1fr; min-height: 5; margin: 0; }
#filter {
    height: 1;
    border: none;
    padding: 0 1;
    background: #0e1626;
    color: #e2e8f0;
    display: none;
}
#filter.visible { display: block; }
#filter:focus { background: #132038; border: none; }
#filter.-valid { border: none; }
#p_procs {
    height: 1fr;
    background: #0e1626;
    border: round #1e3a5f;
    margin: 0 1 0 0;
    padding: 0 1;
    border-title-color: #7dd3fc;
    border-title-style: bold;
    scrollbar-size: 0 0;
}
"""


class Panel(Static):
    """Bordered dashboard panel with a border title."""

    def __init__(self, title: str, id: str | None = None):
        self.panel_title = title
        super().__init__(id=id)

    def on_mount(self) -> None:
        self.border_title = self.panel_title


class PistatApp(App):
    CSS = CSS
    TITLE = "pistat"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("p", "toggle_pause", "Pause"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("S", "reverse_sort", "Rev"),
        Binding("1", "interval('1')", "1s", show=False),
        Binding("2", "interval('2')", "2s", show=False),
        Binding("5", "interval('5')", "5s", show=False),
        Binding("r", "reset_peaks", "Reset", show=False),
        Binding("slash", "focus_filter", "Filter"),
        Binding("up", "move('-1')", "Up", show=False),
        Binding("down", "move('1')", "Down", show=False),
        Binding("k", "move('-1')", "Up", show=False),
        Binding("j", "move('1')", "Down", show=False),
        Binding("pageup", "page('-1')", "Page up", show=False),
        Binding("pagedown", "page('1')", "Page down", show=False),
        Binding("home", "home", "Top", show=False),
        Binding("end", "end", "Bottom", show=False),
        Binding("escape", "blur_filter", "Close", show=False),
    ]
    SORTS: ClassVar[tuple[str, ...]] = ("cpu", "mem", "pid", "name", "cputime")
    ROW_H: ClassVar[int] = 9          # height of one panel band
    MIN_PROC_H: ClassVar[int] = 8     # border + header + status + 4 process rows
    PANELS: ClassVar[tuple[str, ...]] = (
        "#p_cpu", "#p_disk", "#p_pi", "#p_mem", "#p_net", "#p_hist",
    )
    # (columns, bands) -> panels kept, in compose order
    SHAPES: ClassVar[dict[tuple[int, int], tuple[str, ...]]] = {
        (3, 2): ("#p_cpu", "#p_disk", "#p_pi", "#p_mem", "#p_net", "#p_hist"),
        (3, 1): ("#p_cpu", "#p_disk", "#p_pi"),
        (2, 2): ("#p_cpu", "#p_disk", "#p_pi", "#p_mem"),
        (2, 1): ("#p_cpu", "#p_pi"),
        (1, 3): ("#p_cpu", "#p_pi", "#p_mem"),
        (1, 2): ("#p_cpu", "#p_pi"),
        (1, 1): ("#p_cpu",),
    }

    def __init__(self, interval: float = 1.0):
        super().__init__()
        self.interval = interval
        self.collector = Collector()
        self.paused = False
        self.sort_key = "cpu"
        self.reverse = True
        self.last: dict | None = None
        self.filter_text = ""
        self._timer = None
        self._panel_cache: dict[str, tuple[str, tuple]] = {}
        self.sel = 0
        self.offset = 0
        self._rows: list[dict] = []
        self._view_h = 10

    # -- layout ----------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="body"):
            with Vertical(id="top"):
                yield Panel("CPU", id="p_cpu")
                yield Panel("DISK · I/O", id="p_disk")
                yield Panel("THERMAL · CLOCK · POWER", id="p_pi")
                yield Panel("MEMORY · PROCESSES", id="p_mem")
                yield Panel("NETWORK", id="p_net")
                yield Panel("HISTORY", id="p_hist")
            with Vertical(id="procsbox"):
                yield Input(placeholder="filter: name / user / pid   ( / to focus · esc to clear )",
                            id="filter")
                yield Panel("PROCESSES", id="p_procs")
        yield Footer()

    def on_mount(self) -> None:
        self.set_timer(0.05, self.tick)
        self._start_timer()
        self.responsive()
        # keep keyboard focus off the (hidden) filter Input so app bindings fire
        procs = self.query_one("#p_procs", Panel)
        procs.can_focus = True
        procs.focus()

    def _start_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_interval(self.interval, self.tick)
        self.sub_title = f"{socket.gethostname()} · {self.interval:g}s sample"

    def on_resize(self) -> None:
        self.responsive()

    def responsive(self) -> None:
        """Pick the panel grid from the *available* height, not just the width.

        Every band is ROW_H rows tall. Bands are only added while the process
        list keeps MIN_PROC_H rows; when nothing fits, the grid is dropped
        entirely so the list gets the whole window instead of being clipped.
        """
        h, w = self.size.height, self.size.width
        body = max(4, h - 2)                       # Header + Footer take two rows
        cols = 3 if w >= 104 else 2 if w >= 62 else 1
        bands = 0
        if body >= self.ROW_H + self.MIN_PROC_H:
            bands = (body - self.MIN_PROC_H) // self.ROW_H
        bands = max(0, min(bands, 2 if cols > 1 else 3))

        top = self.query_one("#top", Vertical)
        shape = self.SHAPES.get((cols, bands))
        if shape is None:                          # nothing usable fits
            top.display = False
            for panel_id in self.PANELS:
                self.query_one(panel_id, Panel).display = False
        else:
            top.display = True
            top.set_classes(f"g{cols}{bands}")
            for panel_id in self.PANELS:
                self.query_one(panel_id, Panel).display = panel_id in shape
        self._panel_cache.clear()
        if self.last:
            self.render_all(self.last)

    # -- actions ---------------------------------------------------------- #
    def action_interval(self, seconds: str) -> None:
        self.interval = float(seconds)
        self._start_timer()
        self.notify(f"sample interval → {self.interval:g}s", timeout=1.5)
        if self.last:
            self.render_all(self.last)

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.notify("paused — press p to resume" if self.paused else "live", timeout=1.5)
        if self.last:
            self.render_all(self.last)

    def action_cycle_sort(self) -> None:
        i = self.SORTS.index(self.sort_key)
        self.sort_key = self.SORTS[(i + 1) % len(self.SORTS)]
        self.sel = self.offset = 0
        self.notify(f"sort: {self.sort_key} {'desc' if self.reverse else 'asc'}", timeout=1.5)
        if self.last:
            self.render_procs(self.last)

    def action_reverse_sort(self) -> None:
        self.reverse = not self.reverse
        if self.last:
            self.render_procs(self.last)

    def action_focus_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.add_class("visible")
        box.focus()

    def action_blur_filter(self) -> None:
        """esc always clears the filter and hides the box — focused or not."""
        box = self.query_one("#filter", Input)
        if box.value or self.filter_text:
            box.value = ""            # fires on_input_changed -> clears + repaints
            self.filter_text = ""
            if self.last:
                self.render_procs(self.last)
        box.remove_class("visible")
        self.query_one("#p_procs", Panel).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value.strip().lower()
        self.sel = self.offset = 0
        if self.last:
            self.render_procs(self.last)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#filter", Input).remove_class("visible")
        self.query_one("#p_procs", Panel).focus()

    def action_reset_peaks(self) -> None:
        c = self.collector
        c.peak_cpu = c.peak_mem = c.peak_temp = 0.0
        self.notify("peaks reset", timeout=1.5)

    # -- data loop -------------------------------------------------------- #
    async def tick(self) -> None:
        if self.paused:
            return
        try:
            snap = await asyncio.to_thread(self.collector.sample)
        except Exception as err:  # noqa: BLE001 — a bad sample must not kill the UI
            self.notify(f"sample error: {err}", severity="error", timeout=4)
            return
        self.last = snap
        self.render_all(snap)

    # -- painting --------------------------------------------------------- #
    def _width(self, panel_id: str, fallback: int) -> int:
        panel = self.query_one(panel_id, Panel)
        w = panel.content_size.width or panel.size.width
        return w if w > 12 else fallback

    def render_all(self, s: dict) -> None:
        guess = max(30, self.size.width // 3 - 4)
        painters = {
            "#p_cpu": lambda snap, w, h: render_cpu(snap, w, h),
            "#p_mem": lambda snap, w, _h: render_mem(snap, w),
            "#p_disk": lambda snap, w, _h: render_disk(snap, w),
            "#p_net": lambda snap, w, _h: render_net(snap, w),
            "#p_pi": lambda snap, w, _h: render_pi(snap, w),
            "#p_hist": lambda snap, w, _h: render_hist(snap, self.collector, w),
        }
        for panel_id, render in painters.items():
            panel = self.query_one(panel_id, Panel)
            if not panel.display:
                continue
            w = self._width(panel_id, guess)
            h = panel.content_size.height or 6
            body = fit(render(s, w, h), w)
            # repaint when the text *or* the styling changes: a bar can cross a colour
            # threshold while the percentage string stays identical
            key = (body.plain, tuple(body.spans))
            if self._panel_cache.get(panel_id) != key:
                self._panel_cache[panel_id] = key
                panel.update(body)
        self.sub_title = (
            f"{s['host']} · {self.interval:g}s · up {fmt_uptime(s['uptime'])}"
            f"{' · PAUSED' if self.paused else ''}"
        )
        self.render_procs(s)

    def _sorted_rows(self, s: dict) -> list[dict]:
        rows = list(s["procs"])
        if self.filter_text:
            needle = self.filter_text
            rows = [r for r in rows
                    if needle in r["name"].lower()
                    or needle in r["user"].lower()
                    or needle in str(r["pid"])]
        key = self.sort_key
        if key == "cpu":
            rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=self.reverse)
        elif key == "mem":
            rows.sort(key=lambda r: r["rss"], reverse=self.reverse)
        elif key == "pid":
            rows.sort(key=lambda r: r["pid"], reverse=self.reverse)
        elif key == "name":
            rows.sort(key=lambda r: r["name"].lower(), reverse=self.reverse)
        elif key == "cputime":
            rows.sort(key=lambda r: r["cputime"], reverse=self.reverse)
        return rows

    def render_procs(self, s: dict) -> None:
        """The process list is drawn by hand: scrolling + highlight, no widget churn."""
        rows = self._sorted_rows(s)
        self._rows = rows
        panel = self.query_one("#p_procs", Panel)
        w = panel.content_size.width or 80
        h = panel.content_size.height or 12
        view_h = max(1, h - 2)   # header line + status line

        # selection stays on its screen line (htop-style); the process under it may move
        self.sel = max(0, min(self.sel, max(0, len(rows) - 1)))
        if self.sel < self.offset:
            self.offset = self.sel
        elif self.sel >= self.offset + view_h:
            self.offset = self.sel - view_h + 1
        self.offset = max(0, min(self.offset, max(0, len(rows) - view_h)))
        self._view_h = view_h

        cols, cmd_w = pick_cols(w)
        out = proc_header(cols, cmd_w)
        out.append("\n")
        for i in range(self.offset, min(len(rows), self.offset + view_h)):
            r = rows[i]
            line = proc_row(r, cols, cmd_w, self.collector.cmdline(r["pid"]),
                            selected=(i == self.sel))
            if i == self.sel:
                pad(line, w)
            out.append_text(line)
            out.append("\n")

        status = Text()
        if self.filter_text:
            status.append(f"filter '{self.filter_text}' · {len(rows)} match(es)", style=C_WARN)
        elif len(rows) > view_h:
            status.append(f"{self.offset + 1}-{min(len(rows), self.offset + view_h)}"
                          f" of {len(rows)} processes", style=C_DIM)
        else:
            status.append(f"{len(rows)} processes", style=C_DIM)
        status.append(f"  ·  sort {self.sort_key} {'desc' if self.reverse else 'asc'}", style=C_DIM)
        out.append_text(status)
        panel.update(fit(out, w))

    # -- list navigation -------------------------------------------------- #
    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        self.sel = max(0, min(len(self._rows) - 1, self.sel + delta))
        if self.last:
            self.render_procs(self.last)

    def action_move(self, delta: int) -> None:
        self._move(int(delta))

    def action_page(self, direction: int) -> None:
        self._move(int(direction) * max(1, self._view_h - 1))

    def action_home(self) -> None:
        if self._rows:
            self.sel = 0
            if self.last:
                self.render_procs(self.last)

    def action_end(self) -> None:
        if self._rows:
            self.sel = len(self._rows) - 1
            if self.last:
                self.render_procs(self.last)


# --------------------------------------------------------------------------- #
# one-shot mode (handy for scripts, cron, or SSH one-liners)
# --------------------------------------------------------------------------- #
def term_width(explicit: int | None = None) -> int:
    """Output width for --once/--json: explicit flag, terminal size, or default."""
    if explicit:
        return max(40, explicit)
    try:
        cols = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    except OSError:                                    # pragma: no cover
        cols = DEFAULT_WIDTH
    return max(40, min(240, cols))


def snapshot_json(s: dict, col: Collector, top: int = 10) -> dict:
    """Stable, script-friendly view of one snapshot (no rich objects inside)."""
    vm, sw = s["mem"], s["swap"]
    io_read, io_write = s["io"]["read"], s["io"]["write"]
    net_rx = sum(n["rx"] for n in s["nets"])
    net_tx = sum(n["tx"] for n in s["nets"])
    rows = sorted(s["procs"], key=lambda r: r["cpu"], reverse=True)[:top]
    return {
        "host": s["host"],
        "board": s["board"],
        "pistat": __version__,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(s["ts"])),
        "timestamp": round(s["ts"], 3),
        "uptime_s": round(s["uptime"], 1),
        "cpu": {
            "total_pct": round(s["cpu_total"], 1),
            "per_core_pct": [round(c, 1) for c in s["per_core"]],
            "per_core_mhz": [round(f, 0) for f in s["core_freqs"]],
            "load": [round(x, 2) for x in s["load"]],
            "governor": s["pi"].get("governor"),
            "peak_pct": round(s["peak_cpu"], 1),
        },
        "memory": {
            "used": vm.used, "total": vm.total, "available": vm.available,
            "cached": getattr(vm, "cached", 0), "percent": round(vm.percent, 1),
            "peak_percent": round(s["peak_mem"], 1),
        },
        "swap": {
            "used": sw.used, "total": sw.total, "percent": round(sw.percent, 1),
            "in_bps": round(s["swap_rate"][0], 1), "out_bps": round(s["swap_rate"][1], 1),
        },
        "thermal": {
            "soc_c": round(s["soc_temp"], 1) if s["soc_temp"] is not None else None,
            "peak_c": round(s["peak_temp"], 1),
            "sensors_c": {k: round(v, 1) for k, v in s["temps"].items()},
            "fans_rpm": s["fans"],
            "throttled_hex": s["pi"]["throttle_raw"],
            "throttle_flags": s["pi"]["throttle_flags"],
            "core_volts": s["pi"]["volts"],
            "clocks_hz": s["pi"]["clocks"],
        },
        "disk": {
            "mounts": [
                {
                    "mount": m["mount"], "device": m["device"], "fstype": m["fstype"],
                    "total": m["total"], "used": m["used"], "percent": round(m["pct"], 1),
                }
                for m in s["mounts"]
            ],
            "io_read_bps": round(io_read, 1), "io_write_bps": round(io_write, 1),
            "io_read_total": s["io_totals"][0], "io_write_total": s["io_totals"][1],
        },
        "network": {
            "interfaces": [
                {
                    "name": n["name"], "ip": n["ip"], "state": n["state"],
                    "rx_bps": round(n["rx"], 1), "tx_bps": round(n["tx"], 1),
                    "rx_total": n["rx_total"], "tx_total": n["tx_total"],
                }
                for n in s["nets"]
            ],
            "rx_bps": round(net_rx, 1), "tx_bps": round(net_tx, 1),
        },
        "processes": {
            "count": len(s["procs"]), "threads": s["threads"], "states": s["states"],
            "top": [
                {
                    "pid": r["pid"], "user": r["user"], "name": r["name"],
                    "cpu_pct": round(r["cpu"], 1), "mem_pct": round(r["mem"], 1),
                    "rss": r["rss"], "threads": r["threads"], "state": r["state"],
                    "cputime_s": round(r["cputime"], 1),
                }
                for r in rows
            ],
        },
    }


def once(interval: float, width: int | None = None, as_json: bool = False) -> None:
    from rich.console import Console

    col = Collector()
    col.sample()                       # prime the counters
    time.sleep(max(0.2, interval))     # ...measure over one interval
    s = col.sample()

    if as_json:
        print(json.dumps(snapshot_json(s, col), indent=2))
        return

    from rich.columns import Columns
    from rich.panel import Panel as RPanel

    width = width or term_width()
    console = Console(width=width, no_color=NO_COLOR)

    head = Text(f"{s['host']}  ·  {short_board(s['board'])}  ·  up {fmt_uptime(s['uptime'])}")
    head.append(f"  ·  {len(s['procs'])} procs · {s['threads']} threads", style=C_LABEL)
    console.print(RPanel(head, border_style="#1e3a5f"))

    def p(title, body):
        return RPanel(body, title=title, border_style="#1e3a5f", title_align="left")

    inner = max(30, width // 2 - 8)
    pairs = [
        ("CPU", render_cpu(s, inner), "MEMORY", render_mem(s, inner)),
        ("DISK · I/O", render_disk(s, inner), "NETWORK", render_net(s, inner)),
        ("THERMAL · CLOCK · POWER", render_pi(s, inner), "HISTORY", render_hist(s, col, inner)),
    ]
    for left, lbody, right, rbody in pairs:
        console.print(Columns([p(left, lbody), p(right, rbody)], equal=True))

    tbl_w = max(40, width - 4)         # the panel border eats two cells each side
    cols, cmd_w = pick_cols(tbl_w)
    table = proc_header(cols, cmd_w)
    for r in sorted(s["procs"], key=lambda r: r["cpu"], reverse=True)[:10]:
        table.append("\n")
        table.append_text(proc_row(r, cols, cmd_w, col.cmdline(r["pid"])))
    console.print(p("TOP PROCESSES (by CPU)", table))
    console.print(Text(
        f"sampled {time.strftime('%Y-%m-%d %H:%M:%S')} · interval {max(0.2, interval):g}s"
        f" · pistat {__version__}", style=C_DIM))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pistat",
        description="pistat — real-time TUI system dashboard (Raspberry Pi friendly)",
        epilog="Keys in the TUI: q quit · p pause · s sort · S reverse · 1/2/5 interval "
               "· r reset peaks · / filter · j/k move · PgUp/PgDn · Home/End",
    )
    ap.add_argument("-i", "--interval", type=float, default=1.0,
                    help=f"sampling interval in seconds (default 1.0, minimum {MIN_INTERVAL:.1f})")
    ap.add_argument("--once", action="store_true",
                    help="print one plain-text snapshot and exit")
    ap.add_argument("--json", action="store_true",
                    help="print one snapshot as JSON and exit (implies --once)")
    ap.add_argument("-w", "--width", type=int, default=None,
                    help="output width for --once/--json (default: terminal width)")
    ap.add_argument("-V", "--version", action="version", version=f"pistat {__version__}")
    args = ap.parse_args(argv)

    interval = max(MIN_INTERVAL, args.interval)
    if interval != args.interval:
        print(f"pistat: interval raised to the {MIN_INTERVAL:g}s minimum", file=sys.stderr)

    try:
        if args.once or args.json:
            once(interval, term_width(args.width), as_json=args.json)
        else:
            PistatApp(interval=interval).run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

