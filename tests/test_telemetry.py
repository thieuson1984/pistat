"""Telemetry tests: clock, voltage, throttle splitting and their renderers.

Everything here runs without a Raspberry Pi — the sysfs/firmware reads are
faked at the module boundary, and the renderers are fed synthetic snapshots.
That is the point: the clock/volt logic must stay testable on any Linux box.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pistat
from pistat import Collector


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def fake_sysfs(clock_mhz: dict[int, int], limits=(600, 1800), governor="ondemand"):
    """A read_text() stand-in serving cpufreq files for `clock_mhz`."""
    real = pistat.read_text
    core_re = re.compile(r"/cpu(\d+)/cpufreq/")

    def read(path: str):
        p = str(path)
        if "/cpufreq/policy0/" in p:
            if p.endswith(("scaling_min_freq", "cpuinfo_min_freq")):
                return str(limits[0] * 1000)
            if p.endswith(("scaling_max_freq", "cpuinfo_max_freq")):
                return str(limits[1] * 1000)
            if p.endswith("scaling_governor"):
                return governor
        match = core_re.search(p)
        if match and p.endswith("scaling_cur_freq"):
            mhz = clock_mhz.get(int(match.group(1)))
            return str(mhz * 1000) if mhz is not None else None
        return real(path)

    return read


class FakeVcgencmd:
    """Stands in for the firmware mailbox; records every command it is asked."""

    def __init__(self, volts=0.9285, throttled="0x0", clocks=None, pmic=52.0):
        self.volts = volts
        self.throttled = throttled
        self.clocks = clocks or {"core": 500_000_992, "v3d": 500_000_992,
                                 "emmc": 250_000_496, "arm": 1_800_000_000}
        self.pmic = pmic
        self.calls: list[str] = []

    def __call__(self, cmd: str):
        self.calls.append(cmd)
        if cmd == "measure_volts core":
            return f"volt={self.volts:.4f}V"
        if cmd.startswith("measure_volts "):
            return "volt=1.1000V"
        if cmd == "measure_temp pmic":
            return f"temp={self.pmic:.1f}'C"
        if cmd.startswith("measure_clock "):
            return f"frequency(48)={self.clocks.get(cmd.split()[-1], 0)}"
        if cmd == "get_throttled":
            return f"throttled={self.throttled}"
        return None


# --------------------------------------------------------------------------- #
# throttle decoding
# --------------------------------------------------------------------------- #
def test_decode_throttle_clean():
    d = pistat.decode_throttle("throttled=0x0")
    assert d["now"] == [] and d["seen"] == [] and d["raw"] == "0x0"


def test_decode_throttle_separates_now_from_seen():
    # bit 2 = throttled NOW, bit 18 = throttling seen since boot
    d = pistat.decode_throttle("throttled=0x40004")
    assert d["now"] == ["throttled NOW"]
    assert d["seen"] == ["throttling seen"]


def test_decode_throttle_seen_only_is_not_a_live_fault():
    """A brown-out at boot latches bit 16 until reboot — it must not look live."""
    d = pistat.decode_throttle("throttled=0x10000")
    assert d["now"] == []
    assert d["seen"] == ["under-voltage seen"]


def test_decode_throttle_garbage():
    assert pistat.decode_throttle(None)["now"] == []
    assert pistat.decode_throttle("nonsense")["now"] == []
    assert pistat.decode_throttle("throttled=zzz")["now"] == []


# --------------------------------------------------------------------------- #
# collector: clock
# --------------------------------------------------------------------------- #
def test_clock_block_reads_sysfs_and_computes_percent(monkeypatch):
    monkeypatch.setattr(pistat, "read_text", fake_sysfs({i: 1200 for i in range(4)}))
    c = Collector()
    ck = c._clock_block()
    assert ck["mhz"] == 1200.0
    assert abs(ck["pct_of_max"] - 100.0 * 1200 / 1800) < 0.1
    assert ck["limit_min_mhz"] == 600.0 and ck["limit_max_mhz"] == 1800.0
    assert ck["governor"] == "ondemand"
    assert ck["spread_mhz"] == 0.0


def test_clock_block_tracks_floor_peak_and_dvfs_steps(monkeypatch):
    c = Collector()
    for mhz in (1800, 1800, 600, 900):
        monkeypatch.setattr(pistat, "read_text", fake_sysfs({i: mhz for i in range(4)}))
        ck = c._clock_block()
    assert ck["peak_mhz"] == 1800.0
    assert ck["min_mhz"] == 600.0
    # 1800 -> 600 -> 900: two real steps (the first sample is a baseline)
    assert ck["dvfs_steps"] == 2


def test_clock_block_reports_per_core_spread(monkeypatch):
    monkeypatch.setattr(pistat, "read_text",
                        fake_sysfs({0: 1800, 1: 1800, 2: 600, 3: 1200}))
    c = Collector()
    ck = c._clock_block()
    assert ck["per_core_mhz"] == [1800.0, 1800.0, 600.0, 1200.0]
    assert ck["core_min_mhz"] == 600.0 and ck["core_max_mhz"] == 1800.0
    assert ck["spread_mhz"] == 1200.0
    assert ck["mhz"] == 1350.0


def test_clock_block_survives_missing_sysfs(monkeypatch):
    monkeypatch.setattr(pistat, "read_text", lambda path: None)
    c = Collector()
    ck = c._clock_block()
    assert ck["mhz"] == 0.0 and ck["pct_of_max"] == 0.0
    assert ck["governor"] == "n/a"


# --------------------------------------------------------------------------- #
# collector: voltage
# --------------------------------------------------------------------------- #
def test_volts_block_reports_core_and_rails(monkeypatch):
    monkeypatch.setattr(pistat, "vcgencmd", FakeVcgencmd(volts=0.9285))
    c = Collector()
    v = c._volts_block()
    assert v["core"] == 0.9285
    assert v["rails"] == {"sdram_c": 1.1, "sdram_i": 1.1, "sdram_p": 1.1}
    assert v["min"] == 0.9285 and v["max"] == 0.9285


def test_volts_block_tracks_min_max_and_delta(monkeypatch):
    c = Collector()
    monkeypatch.setattr(pistat, "vcgencmd", FakeVcgencmd(volts=0.9285))
    c._volts_block(ttl=0.0)
    monkeypatch.setattr(pistat, "vcgencmd", FakeVcgencmd(volts=0.8500))
    v = c._volts_block(ttl=0.0)
    assert v["core"] == 0.85
    assert v["min"] == 0.85 and v["max"] == 0.9285
    assert v["delta"] < 0, "a voltage drop must be visible as a negative delta"


def test_volts_block_caches_within_ttl(monkeypatch):
    vcg = FakeVcgencmd()
    monkeypatch.setattr(pistat, "vcgencmd", vcg)
    c = Collector()
    c._volts_block(ttl=60.0)
    first = len(vcg.calls)
    c._volts_block(ttl=60.0)
    assert len(vcg.calls) == first, "volts were re-read inside the TTL"


def test_latched_uv_alarm_is_evidence_not_a_live_fault(monkeypatch):
    """rpi_volt's alarm survives reboots, so it belongs with the seen bits."""
    monkeypatch.setattr(pistat, "vcgencmd", FakeVcgencmd(throttled="0x0"))
    monkeypatch.setattr(pistat.Collector, "_uv_alarm", lambda self: True)
    c = Collector()
    thr = c._throttle_block(ttl=0.0)
    assert thr["now"] == []
    assert thr["ok"] is True
    assert any("rpi_volt" in f for f in thr["seen"])


def test_pmic_temp_is_cached(monkeypatch):
    vcg = FakeVcgencmd(pmic=48.5)
    monkeypatch.setattr(pistat, "vcgencmd", vcg)
    c = Collector()
    assert c._pmic_temp(ttl=60.0) == 48.5
    c._pmic_temp(ttl=60.0)
    assert vcg.calls.count("measure_temp pmic") == 1


def test_volts_block_without_firmware(monkeypatch):
    """A non-Pi box has no vcgencmd at all: the block must stay JSON-safe."""
    monkeypatch.setattr(pistat, "vcgencmd", lambda cmd: None)
    c = Collector()
    v = c._volts_block()
    assert v["core"] is None and v["rails"] == {}
    s = snapshot()
    s["pi"].update({"volts": None, "rails": {}, "volts_min": None, "volts_max": None})
    assert pistat._power_json(s)["core_volts"] is None
    assert "n/a" in pistat.render_pi(s, width=36).plain


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #
def snapshot(clock_mhz=1800, pct=100.0, volts=0.9285, throttle=("0x0", [], [])):
    """Minimal snapshot with every key the CPU/PI/HISTORY renderers touch."""
    raw, now, seen = throttle
    clock = {
        "per_core_mhz": [float(clock_mhz)] * 4, "mhz": float(clock_mhz),
        "peak_mhz": 1800.0, "min_mhz": 600.0, "core_max_mhz": float(clock_mhz),
        "core_min_mhz": float(clock_mhz), "spread_mhz": 0.0,
        "limit_min_mhz": 600.0, "limit_max_mhz": 1800.0, "pct_of_max": pct,
        "governor": "ondemand", "dvfs_steps": 3,
    }
    return {
        "ts": 1700000000.0, "uptime": 27804.0,
        "cpu_total": 17.9, "per_core": [15.3, 20.2, 29.0, 7.0],
        "core_freqs": [float(clock_mhz)] * 4, "freq": float(clock_mhz),
        "clock": clock, "clock_hist": [600.0, 900.0, float(clock_mhz)],
        "volt_hist": [0.85, 0.9, volts],
        "load": [0.64, 0.51, 0.41],
        "temps": {"cpu-thermal": 52.6, "pmic": 54.3}, "soc_temp": 52.6,
        "fans": {}, "board": "Raspberry Pi 4 Model B Rev 1.2", "host": "pi",
        "peak_cpu": 35.0, "peak_mem": 43.0, "peak_temp": 55.0,
        "nets": [{"name": "eth0", "rx": 1000.0, "tx": 2000.0}],
        "procs": [], "states": {}, "threads": 0,
        "pi": {
            "throttle_ok": not now, "throttle_flags": [*now, *seen],
            "throttle_now": now, "throttle_seen": seen, "throttle_raw": raw,
            "clocks": {"arm": float(clock_mhz) * 1e6, "core": 5e8, "v3d": 5e8,
                       "emmc": 2.5e8},
            "volts": volts, "rails": {"sdram_c": 1.1, "sdram_i": 1.1, "sdram_p": 1.1},
            "volts_delta": 0.0, "volts_min": 0.85, "volts_max": volts,
            "governor": "ondemand",
        },
    }


def lines_of(text):
    return [line for line in text.plain.split("\n") if line]


def test_render_cpu_shows_realtime_clock_and_percent():
    t = pistat.render_cpu(snapshot(clock_mhz=1200, pct=66.7), width=36, height=7)
    flat = t.plain
    assert "1.200GHz" in flat, flat
    assert " 67%" in flat, flat
    for line in lines_of(t):
        assert len(line) <= 36, (len(line), line)


def test_render_cpu_clock_never_wraps_when_narrow():
    for width in (30, 34, 40, 60):
        t = pistat.render_cpu(snapshot(), width=width, height=7)
        for line in lines_of(t):
            assert len(line) <= width, (width, len(line), line)


def test_render_cpu_survives_missing_clock():
    s = snapshot()
    s["clock"] = {}
    s["freq"] = 0.0
    s["core_freqs"] = []
    t = pistat.render_cpu(s, width=36, height=7)
    assert "n/a" in t.plain


def test_render_pi_volts_keep_four_decimals():
    t = pistat.render_pi(snapshot(volts=0.9285), width=36)
    assert "0.9285V" in t.plain, t.plain
    assert "1.100V" in t.plain, "the SDRAM rail is missing"


def test_render_pi_seen_only_throttle_is_not_red():
    t = pistat.render_pi(snapshot(throttle=("0x10000", [],
                                           ["under-voltage seen"])), width=36)
    flat = t.plain
    assert "OK now" in flat, flat
    assert "seen: under-voltage seen" in flat, flat


def test_render_pi_live_throttle_is_reported():
    t = pistat.render_pi(snapshot(throttle=("0x1", ["under-voltage NOW"], [])), width=36)
    assert "under-voltage NOW" in t.plain


def test_render_pi_lines_fit():
    for width in (30, 34, 36, 46):
        for line in lines_of(pistat.render_pi(snapshot(), width=width)):
            assert len(line) <= width, (width, len(line), line)


def test_render_hist_has_clock_and_voltage():
    c = Collector()
    t = pistat.render_hist(snapshot(), c, width=36)
    flat = t.plain
    assert "clk" in flat and "1.80G" in flat, flat
    assert "volt" in flat and "0.928V" in flat, flat
    assert len(lines_of(t)) <= 7, lines_of(t)


def test_sparkline_pinned_scale_is_not_flat():
    """A clock pinned at max must sit at the top of a 600..1800 MHz scale."""
    low = pistat.sparkline([600.0], 1, lo=600.0, hi=1800.0).plain
    high = pistat.sparkline([1800.0], 1, lo=600.0, hi=1800.0).plain
    mid = pistat.sparkline([1200.0], 1, lo=600.0, hi=1800.0).plain
    assert low == pistat.SPARK[0]
    assert high == pistat.SPARK[-1]
    assert pistat.SPARK[0] < mid < pistat.SPARK[-1]


def test_sparkline_default_scale_still_self_relative():
    assert pistat.sparkline([5.0], 1).plain == pistat.SPARK[-1]
    assert pistat.sparkline([0.0], 1).plain == pistat.SPARK[0]


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def test_clock_json_is_null_safe():
    out = pistat._clock_json({})
    assert out["current_mhz"] is None
    assert out["pct_of_max"] == 0.0
    assert out["dvfs_steps"] == 0
    assert set(out) >= {"current_mhz", "peak_mhz", "floor_mhz", "limit_max_mhz",
                        "pct_of_max", "governor"}


def test_clock_json_keeps_zero_spread():
    """spread 0 means "all cores equal" — it is data, not a missing value."""
    out = pistat._clock_json({"mhz": 1800.0, "spread_mhz": 0.0})
    assert out["spread_mhz"] == 0.0


def test_power_json_flags_under_voltage():
    s = snapshot(throttle=("0x10000", [], ["under-voltage seen"]))
    p = pistat._power_json(s)
    assert p["under_voltage_seen"] is True
    assert p["under_voltage_now"] is False
    assert p["rails_volts"]["sdram_c"] == 1.1
    assert p["core_volts"] == 0.9285
