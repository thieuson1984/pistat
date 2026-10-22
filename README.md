# pistat — real-time TUI system dashboard

A single-file Textual/Rich dashboard for a Raspberry Pi (developed on a Pi 4
Model B, Debian 13, aarch64). Reads `/proc`, `/sys/class/thermal`,
`/sys/class/hwmon` and `vcgencmd` directly — no daemon, no config file, no
network, no writes.

```
pistat                  # interactive TUI, 1 s refresh
pistat -i 2             # lighter: 2 s refresh
pistat --once           # one plain-text snapshot, then exit (SSH one-liners)
pistat --once -w 72     # same, forced to 72 columns
pistat --json           # one snapshot as JSON (cron, Prometheus textfile, jq)
pistat --version
```

## Install

One line, no root, updates in place:

```sh
curl -fsSL https://raw.githubusercontent.com/thieuson1984/pistat/main/install.sh | bash
```

It drops the app in `~/.local/lib/pistat`, the launcher in `~/.local/bin/pistat`,
and installs `textual`/`rich`/`psutil` only if the system Python cannot already
import a new enough `textual` — otherwise it builds a private venv, so it is
PEP 668-safe on Debian/Ubuntu. It finishes by running `pistat --version` and a
live `--json` snapshot, so a broken install fails loudly instead of later — and
then **starts pistat for you**, because installing and running should be one
command, not two. Pass `--no-run` (or `PISTAT_NO_RUN=1`) when you install from a
script, and it exits quietly after the summary.

```sh
# variants (from the checkout, or: curl … | bash -s -- --system)
./install.sh --system            # /usr/local for every user (sudo)
./install.sh --prefix ~/opt      # custom prefix
./install.sh --ref v1.2.0        # pin a tag/branch/commit
./install.sh --python /usr/bin/python3
./install.sh --no-run            # install only, do not launch
./install.sh --uninstall
```

Manual install, if you prefer:

```sh
pip install -r requirements.txt     # or: pipx install --spec . pistat
python3 pistat.py
```

Runtime: Python ≥ 3.9, `textual ≥ 0.47`, `rich ≥ 13`, `psutil ≥ 5.9`, Linux.

## Panels

| Panel | Contents |
|---|---|
| CPU | load average, governor, current clock, session peak, overall + per-core usage bars, per-core clock |
| THERMAL · CLOCK · POWER | SoC (`cpu-thermal`) and PMIC temperature, session peak, headroom to the 80 °C soft limit, decoded `vcgencmd get_throttled` flags (under-voltage / freq cap / throttle / soft limit, now and "seen"), the latched `rpi_volt` under-voltage alarm, arm/core/v3d clocks, core voltage, fan tachometer if one is exposed, board revision, uptime |
| MEMORY · PROCESSES | RAM and swap bars + absolute values, live swap in/out **rates**, free/cached, process and thread counts, peak usage, process state histogram (running / sleeping / idle / zombie …) |
| DISK · I/O | per-mount usage (/, /boot/firmware …), live read/write rates, cumulative I/O, free space, `+N mounts` when more are hidden |
| NETWORK | per-interface link state + IPv4, live up/down rates, cumulative totals, aggregate, `+N ifaces` when hidden |
| HISTORY | 150-sample sparklines for CPU %, SoC temperature, load, network throughput and process count, plus uptime |

The bottom panel is a sortable, scrollable process table: PID, user, nice,
CPU %, memory %, RSS, threads, state, CPU time, full command line. Columns are
shed one at a time as the terminal narrows — PID, CPU % and COMMAND always stay.

**Sorting**: click any column header to sort by it; click the same header again
to flip the direction. The active column carries an `↑`/`↓` and is underlined.
`s` cycles the common keys, `S` reverses. Commands (`COMMAND` header) sort by
process name. Keys available: cpu, mem, rss, pid, name, user, state, threads,
nice, cputime.

**Killing**: `x` sends SIGTERM to the selected process, `X` sends SIGKILL.
Both open a confirmation dialog (`y` / `n`) showing the pid, user and signal —
nothing is ever signalled without it. PID 1 and pistat itself are refused, and
signalling another user's process needs the matching privilege (root), which
the notification will tell you.

`--once` prints the same panels plus the top 10 processes by CPU.

## Keys

| Key | Action |
|---|---|
| `q` | quit |
| `p` | pause / resume sampling |
| `s` | cycle sort: cpu → mem → pid → name → user → cputime |
| `S` | reverse sort order |
| `x` | SIGTERM the selected process (asks first) |
| `X` | SIGKILL the selected process (asks first) |
| *click a header* | sort by that column; click again to reverse |
| `/` | filter processes by name, user or PID (`esc` clears) |
| `↑` `↓` / `k` `j` | move the selection |
| `pgup` `pgdn` `home` `end` | page / jump |
| `1` `2` `5` | sampling interval in seconds |
| `r` | reset peak trackers |
| `^p` | command palette — **Change theme** switches the whole colour scheme |

## Responsive layout

Panels live in bands of 9 rows. The number of bands is chosen from the
*available height* first and the width second, so the process list is never
clipped; when not even one band fits, the grid is dropped and the list gets the
whole window.

| Terminal | Layout |
|---|---|
| ≥ 104 cols, room for 2 bands | 3 × 2 — everything |
| ≥ 104 cols, room for 1 band | 3 × 1 — CPU, disk, thermal |
| ≥ 62 cols, room for 2 bands | 2 × 2 — CPU, disk, thermal, memory |
| ≥ 62 cols, room for 1 band | 2 × 1 — CPU + thermal side by side |
| < 62 cols, taller | 1 column: CPU, thermal, memory as they fit |
| too short for any band | process list only, full height |

## JSON output

`pistat --json` prints one snapshot and exits; nothing is sampled after that.

```json
{
  "host": "pi.kapp.vn", "board": "Raspberry Pi 4 Model B Rev 1.2",
  "pistat": "1.1.0", "time": "2026-10-23T00:45:32+0700", "uptime_s": 4821.1,
  "cpu":    {"total_pct": 11.6, "per_core_pct": [2.9, 6.1, 28.6, 8.8],
             "per_core_mhz": [1800, 1800, 1800, 1800], "load": [1.41, 1.06, 0.68],
             "governor": "ondemand", "peak_pct": 25.0},
  "memory": {"used": 1230643200, "total": 3979075584, "available": 2748432384,
             "cached": 2127085568, "percent": 30.9, "peak_percent": 30.9},
  "swap":   {"used": 0, "total": 5867823104, "percent": 0.0,
             "in_bps": 0.0, "out_bps": 0.0},
  "thermal":{"soc_c": 51.1, "peak_c": 51.1, "sensors_c": {"cpu-thermal": 51.1, "pmic": 52.4},
             "fans_rpm": {}, "throttled_hex": "0x0", "throttle_flags": [],
             "core_volts": 0.935, "clocks_hz": {"arm": 1800000000}},
  "disk":   {"mounts": ["…"], "io_read_bps": 0.0, "io_write_bps": 0.0,
             "io_read_total": 2147483648, "io_write_total": 687865856},
  "network":{"interfaces": ["…"], "rx_bps": 392.0, "tx_bps": 985.0},
  "processes": {"count": 191, "threads": 446, "states": {"sleeping": 118},
                "top": [{"pid": 892, "user": "kempi", "name": "python3",
                         "cpu_pct": 3.0, "mem_pct": 14.1, "rss": 533872640,
                         "threads": 53, "state": "sleeping", "cputime_s": 823.0}]}
}
```

Every block is present even when a sensor is missing (`null`, `{}` or `0`), so a
consumer never has to guess. Example cron entry:

```cron
* * * * * /home/pi/.local/bin/pistat --json > /var/lib/node_exporter/textfile/pistat.json.tmp && mv …
```

## Theming

The chrome is drawn with Textual theme variables (`$background`, `$panel`,
`$primary`, …) and the hand-drawn panels derive their colours from the active
theme, so **`^p` → Change theme** recolours everything — including the bars and
sparklines — and the choice survives for the session. `pistat` is registered as
the default theme; the 20-odd built-ins (nord, gruvbox, catppuccin, solarized,
dracula, tokyo-night, ansi-light …) are all selectable.

Two details worth knowing: the `text-*` theme variables are used because they
are contrast-corrected for text on the theme background, and any 8-digit
`#RRGGBBAA` value is flattened onto the theme surface before it reaches Rich,
which cannot parse alpha. `--once` has no Textual app, so it always uses the
slate palette.

## Design notes

- **Sampling** parses `/proc/<pid>/stat` directly (≈14 ms for ~200 processes,
  vs ≈80 ms through psutil's per-process API) and caches UID / username /
  command line per PID, invalidating them when a PID is recycled. Command lines
  are fetched lazily, only for rows that are actually drawn.
- **`vcgencmd` is cached** for 2.5 s — each call forks a process, and clock,
  voltage and throttle state change slowly.
- **Swap and disk rates are deltas**, not psutil's cumulative counters
  (`swap_memory().sin` is bytes since boot, not a rate).
- **Uptime** comes from `/proc/uptime` (exact, free); `boot_time()` is the
  fallback.
- **The process list is hand-drawn** into one `rich.Text` (fixed-width columns,
  htop-style selection line) instead of a `DataTable`, which cost ~13 % of a
  core per second on its own.
- **Panels are only repainted when their text *or styling* changes**, so the
  tool stays quiet on an idle box — and a bar crossing a colour threshold still
  repaints.
- **Failing sensors never kill the UI**: every optional source (temps, disks,
  I/O, network, `vcgencmd`, `psutil.cpu_freq`) is individually guarded and
  degrades to `n/a`.
- **Theming**: no panel hard-codes a colour. `palette_from_theme()` maps the
  active Textual theme onto eleven colour roles, `watch_theme()` re-derives them
  and repaints, and the CSS speaks only theme variables — so chrome and body
  can never disagree.
- **The cursor line is reverse video**, not a fixed blue background: readable on
  light and dark themes without any contrast arithmetic.
- **Signals are always confirmed** through a modal that names the pid, user and
  signal; `x`/`X` never fire directly. PID 1 and pistat's own pid are refused.
- Measured on this Pi 4 at 120 × 44: **≈11 % of one core (≈3 % of the whole
  machine)** at the 1 s default, ≈8 % at `-i 2`. For comparison `htop` runs at
  ≈2 % of a core.
- No writes: everything is read-only except the terminal.

## Tests

They are plain scripts, no framework, and they exit non-zero on the first
failure. Run them from anywhere:

```sh
python3 tests/test_layout.py     # every terminal size: list visible, nothing clipped
python3 tests/test_keys.py       # bindings, sort cycle, filter, navigation
python3 tests/test_features.py   # theme switching, header sorting, kill (SIGTERM/SIGKILL)
python3 tests/test_cli.py        # --version/--json/--once/-w, interval clamp, no ANSI in a pipe
python3 tests/test_headless.py   # full app render → screenshot.svg
python3 tests/probe.py           # sizes/regions/classes at one or many sizes
python3 tests/pty_capture.py 100 30 3   # real PTY screen dump (needs pyte)
python3 tests/measure_cpu.py 12        # pistat's own CPU in a PTY
python3 tests/bench.py                 # sampling vs render timing
python3 tests/ab_bench.py [panels|procs|frozen]   # A/B harness for repaint cost
```

## Files

```
pistat.py          the whole application (~1 500 lines)
install.sh         one-shot installer / updater / uninstaller
requirements.txt   textual, rich, psutil
screenshot.svg     last headless render (Textual SVG export)
tests/             development harnesses and regression suites
  test_layout.py     responsive layout + clipping regression
  test_keys.py       key bindings / filter / navigation
  test_features.py   theme switching, header sorting, kill signals
  test_cli.py        CLI contract (flags, JSON, width, clamping)
  test_headless.py   headless render, writes screenshot.svg
  probe.py           layout probe (sizes, regions, classes)
  pty_capture.py     run in a real PTY at a fixed size, dump the screen (pyte)
  measure_cpu.py     measure pistat's own CPU in a PTY
  bench.py           sampling vs rendering timing
  ab_bench.py        A/B harness used to find the repaint hot spots
```

## Non-Pi Linux

On a normal x86 box `vcgencmd` is absent and `/proc/device-tree/model` does not
exist: the thermal panel degrades to the hwmon/thermal zones it can find, the
clock/voltage/throttle lines show `n/a`, and the board line says
`unknown board`. Everything else works unchanged.
