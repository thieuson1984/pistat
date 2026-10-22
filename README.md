# pistat — real-time TUI system dashboard

A single-file Textual/Rich dashboard for this tower (Raspberry Pi 4 Model B,
Debian 13, aarch64). Reads `/proc`, `/sys/class/thermal`, `/sys/class/hwmon`
and `vcgencmd` directly — no daemon, no config, no network.

```
pistat              # interactive TUI, 1 s refresh
pistat -i 2         # lighter: 2 s refresh
pistat --once       # one plain-text snapshot, then exit (good for SSH one-liners)
pistat --help
```

Installed launcher: `/home/kempi/bin/pistat` → `/home/kempi/pistat/pistat.py`.

## Panels

| Panel | Contents |
|---|---|
| CPU | load average, governor, current clock, peak, overall + per-core usage bars, per-core clock |
| THERMAL · CLOCK · POWER | SoC (`cpu-thermal`) and PMIC temperature, session peak, headroom to the 80 °C soft limit, decoded `vcgencmd get_throttled` flags (under-voltage / freq cap / throttle / soft limit, now and "seen"), arm/core/v3d clocks, core voltage, board revision, uptime |
| MEMORY · PROCESSES | RAM and swap bars + absolute values, swap in/out rates, free/cached, process and thread counts, peak usage, process state histogram (running / sleeping / idle / zombie …) |
| DISK · I/O | per-mount usage (/, /boot/firmware …), live read/write rates, cumulative I/O, free space |
| NETWORK | per-interface link state + IPv4, live up/down rates, cumulative totals, aggregate |
| HISTORY | 150-sample sparklines for CPU %, SoC temperature, load, network throughput and process count, plus uptime |

The bottom panel is a sortable, scrollable process table: PID, user, nice,
CPU %, memory %, RSS, threads, state, CPU time, full command line.

## Keys

| Key | Action |
|---|---|
| `q` | quit |
| `p` | pause / resume sampling |
| `s` | cycle sort: cpu → mem → pid → name → cputime |
| `S` | reverse sort order |
| `/` | filter processes by name, user or PID (`esc` clears) |
| `↑` `↓` / `k` `j` | move the selection |
| `pgup` `pgdn` `home` `end` | page / jump |
| `1` `2` `5` | sampling interval in seconds |
| `r` | reset peak trackers |

## Responsive layout

The grid re-flows with the terminal size; panels are dropped, never squeezed:

| Terminal | Layout |
|---|---|
| ≥ 104 × 34 | 3 × 2 panels (everything) |
| ≥ 80 × 26 | 2 × 2 — CPU, disk, thermal, memory (network + history hidden) |
| wider than 62 but short | 2 × 1 — CPU + thermal side by side, so the process list still gets rows |
| anything smaller | single column: CPU, thermal |

## Design notes

- **Sampling** parses `/proc/<pid>/stat` directly (≈14 ms for ~200 processes,
  vs ≈80 ms through psutil's per-process API) and caches UID / username /
  command line per PID. Command lines are fetched lazily, only for rows that
  are actually drawn.
- **`vcgencmd` is cached** for 2.5 s — each call forks a process, and clock,
  voltage and throttle state change slowly.
- **The process list is hand-drawn** into one `rich.Text` (fixed-width columns,
  htop-style selection line) instead of a `DataTable`, which cost ~13 % of a
  core per second on its own.
- **Panels are only repainted when their text changes**, so the tool stays
  quiet on an idle box.
- Measured on this Pi 4 at 120 × 44: **≈11 % of one core (≈3 % of the whole
  machine)** at the 1 s default, ≈8 % at `-i 2`. For comparison `htop` runs at
  ≈2 % of a core.
- No writes: everything is read-only except the terminal.

## Files

```
pistat.py       the whole application (~1 200 lines)
screenshot.svg  last headless render (Textual SVG export)
tests/          development harnesses
  probe.py         headless layout probe (widget sizes, layout tier)
  test_keys.py     key binding / filter / navigation test
  pty_capture.py   run in a real PTY at a fixed size, dump the screen (pyte)
  measure_cpu.py   measure pistat's own CPU in a PTY
  bench.py         sampling vs rendering timing
  ab_bench.py      A/B harness used to find the repaint hot spots
```
