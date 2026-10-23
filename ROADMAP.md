# pistat roadmap

What has shipped, and what is queued. Nothing here is a promise with a date —
each item says what it needs to be worth building (a measurable number, a
failure it prevents, or a question the dashboard cannot answer today).

## Shipped

### 1.4.0 — accurate clock and voltage, theme preview, CPU detail

- **Measured clock** from `cpufreq/stats/time_in_state` deltas: a time-weighted
  mean over the sample window, residency per DVFS state, and exact transition
  counts from `total_trans`. This is the answer to "what clock did it actually
  run at"; the old number was one instant per tick and could be a whole state
  wrong in either direction.
- **Source honesty**: `cpuinfo_cur_freq` (silicon) is preferred over
  `scaling_cur_freq` (governor request); the CPU detail screen names the source
  that answered and shows the firmware's ARM clock beside sysfs with the delta.
- **Voltage with context**: 4-decimal rail, session range, change arrow, and the
  v/f pairs actually observed together (`1.80G@0.9285V`), plus the regulator's
  step derived from the distinct values seen. The rail is re-read every tick at
  intervals ≥ 0.2 s instead of behind a 1.5 s cache.
- **Theme picker with live preview** (`t`): docked right so the dashboard stays
  visible, the highlight applies the theme immediately, a swatch paints the
  theme's own palette (including its background), typing filters, `enter` keeps
  and persists, `esc` restores. Previews are never written to the config.
- **`--swatch NAME|all`**: the same palette painted to stdout with no tty, so
  theme mapping is testable and previewable over ssh.
- **CPU detail screen** (`c`): per-core clocks, requested/measured/firmware
  comparison, residency histogram, v/f table, rails — repainted every sample.
- **Two cores per line** in the CPU card when the band is 9 rows, so the new
  clock/voltage lines cost no per-core detail at 3-column widths.

### 1.3.0 — live telemetry

Live per-core clock from sysfs, core voltage at 4 decimals, throttle state
split into *now* vs *seen since boot*, 150-sample clock and voltage sparklines.

### 1.2.x — interaction

Working theme switching (CSS was hard-coded), persisted theme, click-to-sort
headers, confirmed process killing, installer that puts `pistat` on `PATH`.

### 1.1.0 — shipping

`--once`, `--json`, the one-liner installer, the layout that fits short
terminals, and a test suite that actually runs.

## Next

| Item | Why it is worth doing | What would make it done |
|---|---|---|
| **Threshold alerts** | A dashboard only helps if you are looking at it. A `--alert` set (SoC °C, core volts sag, throttle-now bits) with a notification would catch the brown-out that latched during boot and never cleared. | `--alert temp>78,volt<0.85,throttle` in `--once`/`--json` mode; exit code 1 when tripped; no writes, no daemon. |
| **Per-core history** | The CPU card shows one sparkline for the *average* clock; a single pegged core is invisible. | 4 pinned sparklines (or a heat strip) in the CPU detail screen, fed by the same per-tick `core_freqs`. |
| **Prometheus textfile mode** | `--json` + a jq cron job works but is heavy on a Pi; a `--prom` writer with a stable metric set is a drop-in for node_exporter's textfile collector. | `pistat --prom [PATH]` emitting `pistat_clock_hz{state="measured"}`, `pistat_core_volts`, `pistat_temp_celsius`, …; documented metric list, no extra dependency. |
| **Energy estimate** | "What has this box cost me?" is the question after temperature. RPi firmware exposes no power meter, but a documented model (clock×volts² × leak, calibrated against a USB meter) gives a useful trend. | A `POWER EST.` line marked as an estimate, with the model and calibration constants in the README; never presented as a measurement. |
| **Session recording** | Reproducing "it was slow at 03:10" needs the samples, not a memory of them. | `--record FILE.csv` (1 Hz, bounded size, rotates), and a `--replay FILE` that drives the panels from it — the same renderers, so nothing drifts. |
| **Config beyond the theme** | Interval, sort key, hidden panels and thresholds are all re-set on every start. | A single `config.json` schema (`theme`, `interval`, `sort`, `reverse`, `alerts`), `--set key=value`, migration from the theme-only file. |

## Deliberately not planned

- **A daemon.** pistat is a terminal tool; `--json` plus cron covers the
  headless case without a service to supervise.
- **Historical storage in the tool.** A ring buffer of 150 samples is enough for
  a sparkline; anything longer belongs in Prometheus or a file, not in RAM.
- **Writing to the machine.** No governor changes, no threshold writes, no
  sysfs writes. A monitor that can change what it monitors is a different tool
  with a different risk profile.
