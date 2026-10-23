# pistat — real-time TUI system dashboard

A single-file Textual/Rich dashboard for a Raspberry Pi (developed on a Pi 4
Model B, Debian 13, aarch64). Reads `/proc`, `/sys/class/thermal`,
`/sys/class/hwmon` and `vcgencmd` directly — no daemon, no network, no root.
The only thing it ever writes is its own preference file (the theme you pick).

```
pistat                  # interactive TUI, 1 s refresh
pistat -i 2             # lighter: 2 s refresh
pistat --once           # one plain-text snapshot, then exit (SSH one-liners)
pistat --once -w 72     # same, forced to 72 columns
pistat --json           # one snapshot as JSON (cron, Prometheus textfile, jq)
pistat --themes         # list themes, * marks the remembered one
pistat --theme nord     # start in nord and remember it
pistat --swatch nord    # paint that theme's colours and exit (no tty needed)
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
PEP 668-safe on Debian/Ubuntu. The launcher holds absolute paths and puts
`~/.local/bin` on your `PATH` (appended once to `~/.bashrc` / `~/.zshrc` /
`~/.profile`, plus a fish `conf.d` entry), so after a new shell `pistat` works
from any directory — no `cd` into the checkout, no `./pistat.py`. It finishes by
running `pistat --version` and a live `--json` snapshot (and proves the launcher
works from `/`), so a broken install fails loudly instead of later — and then
**starts pistat for you**, because installing and running should be one command,
not two. Pass `--no-run` (or `PISTAT_NO_RUN=1`) when you install from a script,
and it exits quietly after the summary.

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
| CPU | load average, governor, **requested clock** with % of the governor ceiling and a 150-sample sparkline, **measured clock** (time-weighted over the sample window) with residency at the top DVFS state and the transition count, **core voltage** to 0.1 mV with the session range and the clock it belongs to, session peak, overall + per-core usage, per-core clock |
| THERMAL · CLOCK · POWER | SoC (`cpu-thermal`) and PMIC temperature, session peak, headroom to the 80 °C soft limit, decoded `vcgencmd get_throttled` state split into **now** vs **seen since boot**, the latched `rpi_volt` under-voltage alarm, live ARM clock + % of max, core/v3d clocks, core rail voltage to 10 µV with a change arrow, SDRAM rail, fan tachometer if one is exposed, board revision, uptime |
| MEMORY · PROCESSES | RAM and swap bars + absolute values, live swap in/out **rates**, free/cached, process and thread counts, peak usage, process state histogram (running / sleeping / idle / zombie …) |
| DISK · I/O | per-mount usage (/, /boot/firmware …), live read/write rates, cumulative I/O, free space, `+N mounts` when more are hidden |
| NETWORK | per-interface link state + IPv4, live up/down rates, cumulative totals, aggregate, `+N ifaces` when hidden |
| HISTORY | 150-sample sparklines for CPU %, SoC temperature, **clock**, **core voltage**, load and network throughput, plus uptime |

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

## Accurate clock and voltage

The clock and the core rail are the two numbers that show whether the governor
is actually doing anything, and both were the easiest to get *wrong*. Three
things changed in 1.4.0:

**The clock now has a measured value, not just a sampled one.** A single
`scaling_cur_freq` read per tick is a point sample: `ondemand` can spend 900 ms
of a second at 600 MHz, serve a 100 ms burst, and be caught at 1.8 GHz — the
panel then reports 1.8 GHz for a second in which the CPU was mostly idle.
`cpufreq/policy0/stats/time_in_state` accumulates USER_HZ ticks per frequency,
so the delta between two samples gives the time-weighted mean, the residency at
each state, and (from `total_trans`) the exact transition count for the window:

```
clk   1.800GHz 100% ▁▁▁▁▁▁██████     ← requested, one instant per tick
avg   1.734GHz  91%@max 12 st        ← measured: time-weighted over the window
```

`cpuinfo_cur_freq` (the clock the silicon reports) is preferred over
`scaling_cur_freq` (what the governor asked for) when the kernel exposes it; on
a Pi it is root-only, so the request is the fallback and the CPU detail screen
says which source answered.

**The voltage is read with the clock it belongs to.** Every fresh rail reading
is paired with the live clock into a v/f table (`1.80G@0.9285V`), the session
min/max is kept, and the regulator's step is derived from the distinct values
seen — 25 mV on this board, which is exactly why the value is printed to four
decimals. The rail is re-read on every tick when the interval allows it (a fork
costs 2–3 ms), so a DVFS transition is not missed between samples.

**The firmware clock is a cross-check, not the source.** `vcgencmd
measure_clock arm` forks for a number sysfs already has, so it stays on a slow
TTL and appears in the CPU detail screen as `firmware arm 1800.00 MHz vs sysfs
1800 MHz (Δ +0.00 MHz)` — a large Δ means the governor's request has not taken
effect yet, which is a genuinely useful signal that one number cannot give.

| Signal | Source | Cost | Refresh |
|---|---|---|---|
| Per-core clock | `cpu*/cpufreq/cpuinfo_cur_freq`, else `scaling_cur_freq` | ≈40 µs for 4 cores | every tick |
| Measured clock, residency, transitions | `policy0/stats/time_in_state`, `total_trans` | ≈30 µs | every tick |
| Governor limits | `policy0/{scaling_min,scaling_max}_freq`, `scaling_governor` | 4 small reads | 10 s |
| Core voltage + v/f pair | `vcgencmd measure_volts core` | ≈2 ms (one fork) | every tick (≥0.2 s) |
| SDRAM rails | `vcgencmd measure_volts sdram_{c,i,p}` | ≈2 ms each | 30 s |
| core / v3d / emmc clock | `vcgencmd measure_clock` | ≈2 ms each | 5 s |
| Throttle state | `vcgencmd get_throttled` | ≈2 ms | 1 s |
| PMIC temperature | `vcgencmd measure_temp pmic` | ≈2.3 ms | 2.5 s |

The panels show the session envelope, not just the instant: a sparkline pinned
to the 600 MHz–1.8 GHz governor range, `volt 0.9285V ▲ 0.8500–0.9500` for the
rail, and `r` resets the peak/floor/step counters. `c` opens the **CPU detail**
screen, which is where the evidence lives: per-core clocks, the requested /
measured / firmware comparison, the residency histogram, the observed v/f pairs
and the SDRAM rails. It repaints on every sample while it is open.

Throttle bits 0–3 mean *now* and are red; bits 16–19 — and the `rpi_volt`
latched alarm, which also survives reboots — are reported as `OK now · seen:
under-voltage seen`. Treating those as a live fault (the old behaviour) painted
the panel red for the rest of the uptime after a single brown-out at boot.

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
| `t` | theme picker — the dashboard previews each theme live; `enter` keeps it, `esc` goes back |
| `c` | CPU detail screen (measured vs requested clock, residency, v/f pairs) |
| `^p` | command palette — **Change theme** also works, without the preview |

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
  "pistat": "1.4.0", "time": "2026-10-23T00:45:32+0700", "uptime_s": 4821.1,
  "cpu":    {"total_pct": 11.6, "per_core_pct": [2.9, 6.1, 28.6, 8.8],
             "per_core_mhz": [1800, 1800, 1800, 1800], "load": [1.41, 1.06, 0.68],
             "governor": "ondemand", "peak_pct": 25.0, "clock_hz": 1800000000,
             "clock": {"current_mhz": 1800.0, "peak_mhz": 1800.0, "floor_mhz": 600.0,
                       "core_min_mhz": 1800.0, "core_max_mhz": 1800.0, "spread_mhz": 0.0,
                       "limit_min_mhz": 600.0, "limit_max_mhz": 1800.0,
                       "pct_of_max": 100.0, "dvfs_steps": 12, "governor": "ondemand",
                       "weighted_mhz": 1734.0, "weighted_ready": true, "window_s": 1.0,
                       "residency_max_pct": 91.0, "residency_min_pct": 2.0,
                       "transitions": 12, "source": "cpufreq"}},
  "memory": {"used": 1230643200, "total": 3979075584, "available": 2748432384,
             "cached": 2127085568, "percent": 30.9, "peak_percent": 30.9},
  "swap":   {"used": 0, "total": 5867823104, "percent": 0.0,
             "in_bps": 0.0, "out_bps": 0.0},
  "thermal":{ "soc_c": 51.1, "peak_c": 51.1, "sensors_c": {"cpu-thermal": 51.1, "pmic": 52.4},
             "fans_rpm": {}, "throttled_hex": "0x0", "throttle_flags": [],
             "core_volts": 0.9285,
             "clocks_hz": {"core": 500000992, "v3d": 500000992,
                            "emmc": 250000496, "arm": 1800000000}},
  "power":  {"core_volts": 0.9285, "core_volts_min": 0.85, "core_volts_max": 0.9285,
             "core_volts_step_mv": 25.0, "vf_pairs": {"600": 0.9, "1800": 0.9285},
             "rails_volts": {"sdram_c": 1.1, "sdram_i": 1.1, "sdram_p": 1.1},
             "throttled_hex": "0x0", "throttle_now": [], "throttle_seen": [],
             "under_voltage_now": false, "under_voltage_seen": false},
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
consumer never has to guess. `cpu.clock` and `power` are the fields to watch for
DVFS and brown-out evidence: `current_mhz` is the sampled governor request,
`weighted_mhz` is the measured mean over `window_s` (with `residency_max_pct` /
`residency_min_pct` for where it spent its time and `transitions` for how often
it moved), `floor_mhz` is the lowest clock seen this run, and
`under_voltage_seen` is the firmware's latched bit (once true it stays true
until reboot). `power.vf_pairs` is `{clock_mhz: volts}` as actually observed
together, and `core_volts_step_mv` is the regulator's step. Example cron entry:

```cron
* * * * * /home/pi/.local/bin/pistat --json > /var/lib/node_exporter/textfile/pistat.json.tmp && mv …
```

## Theming

The chrome is drawn with Textual theme variables (`$background`, `$panel`,
`$primary`, …) and the hand-drawn panels derive their colours from the active
theme, so a theme switch recolours everything — including the bars and
sparklines.

**`t` opens the picker with a live preview.** It docks to the right of the
screen (the dashboard stays visible on the left) and moving the cursor applies
that theme immediately, so what you are choosing is the dashboard as it will
look — not a list of names. The panel shows a swatch of the theme's own palette
for the values the CPU card draws, including a body sample on that theme's
background. `enter` keeps the theme and writes it to the config, `esc` puts the
previous one back; **a preview is never saved** — only a confirmed choice is.
Typing narrows the list by name, and the picker swallows every key while it has
focus (`q` filters, it does not quit).

The choice is written to `$XDG_CONFIG_HOME/pistat/config.json`
(`~/.config/pistat/config.json` by default) and restored on the next run, so it
never has to be re-picked. `pistat --theme NAME` sets and remembers a theme
without opening the picker, `pistat --themes` lists them, and `pistat --swatch
NAME` paints one theme's colours to stdout (no tty required — useful over ssh
and as the non-interactive test for the palette mapping). `pistat` is registered
as the default theme; the 20-odd built-ins (nord, gruvbox, catppuccin,
solarized, dracula, tokyo-night, ansi-light …) are all selectable. A theme name
that disappears (or a corrupt config file) falls back to the default instead of
breaking startup; `PISTAT_CONFIG_DIR` relocates the file, which is how the tests
keep your real preference untouched.

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
- **`vcgencmd` is cached per sensor group, not in one block** — each call forks
  a process (≈2–3 ms). Throttle state is 1 s, core voltage 1.5 s, PMIC
  temperature 2.5 s, core/v3d/emmc clocks 5 s, SDRAM rails 30 s. The ARM clock
  needs no fork at all: `scaling_cur_freq` is ~30 µs for all four cores and is
  read on every tick.
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
  I/O, network, `vcgencmd`, `cpufreq`) is individually guarded and degrades to
  `n/a` — the clock block even survives a missing `sysfs` by returning zeros.
- **Theming**: no panel hard-codes a colour. `palette_from_theme()` maps the
  active Textual theme onto eleven colour roles, `watch_theme()` re-derives them
  and repaints, and the CSS speaks only theme variables — so chrome and body
  can never disagree.
- **The cursor line is reverse video**, not a fixed blue background: readable on
  light and dark themes without any contrast arithmetic.
- **Signals are always confirmed** through a modal that names the pid, user and
  signal; `x`/`X` never fire directly. PID 1 and pistat's own pid are refused.
- **Measured clock comes from counters, not samples**: one `time_in_state` read
  per tick (≈30 µs) replaces a guess that could be a whole DVFS state wrong.
- Measured on this Pi 4 at 120 × 44: **≈12.6 % of one core (≈3.1 % of the whole
  machine)** at the 1 s default — reading the core rail every tick and the
  residency counters costs ~1 pp over 1.3.0; at the 2 s interval it is 6.9 %.
  For comparison `htop` runs at ≈2 % of a core.
- **No writes**: everything is read-only except the terminal and the theme
  preference file.

## Roadmap

The next features are tracked in [ROADMAP.md](ROADMAP.md) — alerts with
thresholds, per-core history, and exporting the JSON schema as a Prometheus
textfile. Anything already shipped is listed there with the release it landed
in, so the file is never a wish list that silently rots.

## Tests

`pytest` is the way in — 63 checks, no plugins required (`tests/conftest.py`
runs the `async def` tests, so the suite works on a bare interpreter):

```sh
pip install -r requirements-dev.txt   # pytest (+ pyte for the PTY harness)
python3 -m pytest tests/ -q
```

| File | Covers |
|---|---|
| `test_layout.py` | 11 terminal sizes: list visible, grid shape, nothing clipped |
| `test_keys.py` | bindings, sort cycle, filter, navigation |
| `test_features.py` | theme persistence, header sorting, kill dialogs |
| `test_cli.py` | `--version/--json/--once/-w/--themes`, interval clamp, no ANSI in a pipe |
| `test_telemetry.py` | clock block, residency/weighted clock, cpuinfo vs scaling source, v/f pairs and the regulator step, volts/rails, throttle now-vs-seen, renderers, JSON |
| `test_theme_detail.py` | swatch colours per theme, picker previews without saving, typing filters without leaking keys, CPU detail renderer |

Each file also runs standalone (`python3 tests/test_layout.py`), and the
harnesses below stay scripts on purpose — they print numbers, they do not
assert:

```sh
python3 tests/headless_probe.py         # full app render → screenshot.svg
python3 tests/probe.py                  # sizes/regions/classes at one or many sizes
python3 tests/pty_capture.py 100 30 3   # real PTY screen dump (needs pyte)
python3 tests/measure_cpu.py 12         # pistat's own CPU in a PTY
python3 tests/bench.py                  # sampling vs render timing
python3 tests/ab_bench.py [panels|procs|frozen]   # A/B harness for repaint cost
```

## Files

```
pistat.py          the whole application (~1 500 lines)
install.sh         one-shot installer / updater / uninstaller
requirements.txt   textual, rich, psutil
requirements-dev.txt  pytest (+ pyte for the PTY harness)
screenshot.svg     last headless render (Textual SVG export)
tests/             pytest suite (39 checks) + development harnesses
  conftest.py        runs the async tests without pytest-asyncio
  test_layout.py     responsive layout + clipping regression
  test_keys.py       key bindings / filter / navigation
  test_features.py   theme switching + persistence, header sorting, kill signals
  test_cli.py        CLI contract (flags, JSON, width, clamping)
  test_telemetry.py  clock / voltage / throttle / renderer / JSON-schema tests
  headless_probe.py  headless render, writes screenshot.svg
  probe.py           layout probe (sizes, regions, classes)
  pty_capture.py     run in a real PTY at a fixed size, dump the screen (pyte)
  measure_cpu.py     measure pistat's own CPU in a PTY
  bench.py           sampling vs rendering timing
  ab_bench.py        A/B harness used to find the repaint hot spots
```

## Non-Pi Linux

On a normal x86 box `vcgencmd` is absent and `/proc/device-tree/model` does not
exist: the thermal panel degrades to the hwmon/thermal zones it can find, the
voltage/throttle lines show `n/a`, and the board line says `unknown board`. The
clock still works — it comes from `cpufreq`, which every Linux box has — and
`pct_of_max` follows that machine's own ceiling. Everything else is unchanged.
