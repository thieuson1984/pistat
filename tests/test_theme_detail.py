"""Theme preview + CPU detail: the tests that prove the new surfaces work.

The picker is only a "preview" if moving the cursor really changes the painted
dashboard *and* leaves the saved preference alone until you confirm — both are
asserted here, through the real app, not through a mock.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# never read/write the developer's real ~/.config/pistat from a test run
os.environ["PISTAT_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pistat-test-cfg-")

import pistat
from pistat import CpuDetail, PistatApp, ThemePicker


# --------------------------------------------------------------------------- #
# swatch: the preview must actually be built from the theme
# --------------------------------------------------------------------------- #
def test_swatch_paints_theme_colours():
    """Same sample values, different colours: that is what a preview must show."""
    mine = pistat.theme_swatch("pistat")
    light = pistat.theme_swatch("solarized-light")
    mine_styles = [str(span.style) for span in mine.spans]
    light_styles = [str(span.style) for span in light.spans]
    assert mine_styles != light_styles, "every theme rendered the same swatch"
    assert any("on #" in s for s in light_styles), light_styles  # body background
    assert any("#EEE8D5" in s.upper() for s in light_styles), light_styles
    assert len([ln for ln in mine.plain.split("\n") if ln.strip()]) >= 4


def test_swatch_handles_an_unknown_theme():
    """A typo in a config file must not crash the picker or --swatch."""
    t = pistat.theme_swatch("no-such-theme")
    assert "1.800GHz" in t.plain


def test_theme_names_lists_ours_first_then_sorted():
    names = pistat.theme_names()
    assert names[0] == "pistat"
    assert names[1:] == sorted(names[1:])
    assert "nord" in names and "solarized-light" in names


# --------------------------------------------------------------------------- #
# picker behaviour inside the real app
# --------------------------------------------------------------------------- #
async def test_theme_picker_previews_live_without_saving():
    app = PistatApp(interval=0.3)
    start_theme = app.theme
    # the theme that was in effect when the picker opened is already saved, so
    # that is the value a preview must leave alone
    started = pistat.load_config().get("theme")
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.0)
        await pilot.press("t")
        await asyncio.sleep(0.3)
        assert isinstance(app.screen, ThemePicker), app.screen

        await pilot.press("down")
        await asyncio.sleep(0.3)
        previewed = app.theme
        assert previewed != start_theme, "cursor move did not change the theme"
        assert app._previewing is True
        print("previewed:", previewed)
        # a preview is not a choice: the preference file must be untouched
        assert pistat.load_config().get("theme") == started, pistat.load_config()

        await pilot.press("escape")
        await asyncio.sleep(0.3)
        assert app.theme == start_theme, app.theme
        assert not isinstance(app.screen, ThemePicker)
        assert pistat.load_config().get("theme") == started


async def test_theme_picker_enter_keeps_and_persists():
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.0)
        await pilot.press("t")
        await asyncio.sleep(0.3)
        await pilot.press("down", "down")
        await asyncio.sleep(0.3)
        chosen = app.theme
        await pilot.press("enter")
        await asyncio.sleep(0.4)
        assert app.theme == chosen
        assert pistat.load_config().get("theme") == chosen, pistat.load_config()
        # and the picker is gone, so the dashboard takes the keys again
        assert not isinstance(app.screen, ThemePicker)


async def test_typing_in_the_picker_filters_and_never_touches_app_keys():
    """`q` in a theme picker has to filter, not quit; same for the other keys."""
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.0)
        await pilot.press("t")
        await asyncio.sleep(0.3)
        picker = app.screen
        assert isinstance(picker, ThemePicker)

        for ch in "gruv":
            await pilot.press(ch)
            await asyncio.sleep(0.1)
        assert picker.needle == "gruv", picker.needle
        assert picker._visible() == ["gruvbox"], picker._visible()
        assert isinstance(app.screen, ThemePicker), "a typed key escaped the picker"

        await pilot.press("enter")
        await asyncio.sleep(0.4)
        assert app.theme == "gruvbox", app.theme
        assert pistat.load_config().get("theme") == "gruvbox"


async def test_picker_survives_a_filter_with_no_match():
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.0)
        await pilot.press("t")
        await asyncio.sleep(0.3)
        picker = app.screen
        for ch in "zzzz":
            await pilot.press(ch)
            await asyncio.sleep(0.05)
        assert picker._visible() == []
        # enter with nothing selected must close, not raise
        await pilot.press("enter")
        await asyncio.sleep(0.3)
        assert not isinstance(app.screen, ThemePicker)


# --------------------------------------------------------------------------- #
# CPU detail
# --------------------------------------------------------------------------- #
async def test_cpu_detail_opens_shows_evidence_and_closes():
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.4)
        await pilot.press("c")
        await asyncio.sleep(0.5)
        assert isinstance(app.screen, CpuDetail), app.screen
        body = str(app.screen.query_one("#detail_body").content)
        print(body.split("\n")[0])
        for needle in ("requested", "measured", "limits", "RESIDENCY", "CORE RAIL"):
            assert needle in body, (needle, body)
        await pilot.press("escape")
        await asyncio.sleep(0.3)
        assert not isinstance(app.screen, CpuDetail)


def test_cpu_detail_renderer_fits_and_shows_the_three_clock_sources():
    clock = {
        "per_core_mhz": [1800.0] * 4, "mhz": 1800.0, "weighted_mhz": 1723.4,
        "weighted_ready": True, "window_s": 1.0, "residency_max": 91.0,
        "residency_min": 2.0, "states": [(1800.0, 91.0), (1500.0, 7.0), (600.0, 2.0)],
        "transitions": 12, "peak_mhz": 1800.0, "min_mhz": 600.0,
        "core_max_mhz": 1800.0, "core_min_mhz": 1800.0, "spread_mhz": 0.0,
        "limit_min_mhz": 600.0, "limit_max_mhz": 1800.0, "pct_of_max": 100.0,
        "governor": "ondemand", "dvfs_steps": 42, "source": "cpufreq",
    }
    snap = {
        "clock": clock, "per_core": [50.0, 10.0, 5.0, 1.0],
        "core_freqs": [1800.0] * 4,
        "pi": {
            "volts": 0.9285, "volts_min": 0.85, "volts_max": 0.95,
            "volts_delta": -0.0035, "volt_step_mv": 25.0,
            "vf": {600: 0.9, 1800: 0.9285}, "rails": {"sdram_c": 1.1},
            "clocks": {"arm": 1.7995e9},
        },
    }
    t = pistat.render_cpu_detail(snap, pistat.Collector(), width=64)
    flat = t.plain
    assert "1.723 GHz" in flat, flat                 # measured, not requested
    assert "12 transitions this session" in flat or "42 transitions this session" in flat
    assert "0.9285 V" in flat and "step 25 mV" in flat
    assert "0.93G@0.9285V" in flat or "1.80G@0.9285V" in flat
    for line in flat.split("\n"):
        assert len(line) <= 64, (len(line), line)


def test_cpu_detail_survives_a_non_pi_box():
    snap = {"clock": {}, "per_core": [1.0], "core_freqs": [0.0], "pi": {}}
    flat = pistat.render_cpu_detail(snap, pistat.Collector(), width=60).plain
    assert "n/a" in flat
    assert "no cpufreq stats" in flat
