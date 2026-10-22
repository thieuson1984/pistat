"""Feature tests: theme switching, header sorting, and killing a process.

Every check drives the real app through Textual's test harness; the kill test
uses a throwaway `sleep` child so nothing important can be signalled.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# keep the developer's real ~/.config/pistat out of the tests
os.environ["PISTAT_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pistat-test-cfg-")

import pistat
from pistat import Confirm, PistatApp


async def test_theme_switch() -> None:
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)):
        await asyncio.sleep(1.2)
        assert app.theme == "pistat", app.theme
        slate = (pistat.C_TITLE, pistat.C_LABEL)
        assert "-theme-pistat" in app.classes, list(app.classes)

        app.theme = "nord"
        await asyncio.sleep(0.8)
        nord = (pistat.C_TITLE, pistat.C_LABEL)
        assert nord != slate, "palette did not follow the theme"
        assert "-theme-nord" in app.classes, list(app.classes)

        app.theme = "solarized-light"
        await asyncio.sleep(0.8)
        assert (pistat.C_TITLE, pistat.C_LABEL) != nord
        # the panel text itself must be repainted with the new colours too
        body = app.query_one("#p_cpu").content
        styles = {str(span.style).upper() for span in body.spans}
        assert any("195B8A" in s for s in styles), styles

        app.theme = "pistat"
        await asyncio.sleep(0.5)
        assert (pistat.C_TITLE, pistat.C_LABEL) == slate, "did not restore the slate palette"


async def test_theme_persist() -> None:
    """A theme chosen in one run comes back in the next (XDG config file)."""
    cfg = pistat.config_path()
    cfg.unlink(missing_ok=True)

    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)):
        await asyncio.sleep(0.6)
        assert app.theme == "pistat", app.theme
        app.theme = "gruvbox"
        await asyncio.sleep(0.6)
        assert json.loads(cfg.read_text())["theme"] == "gruvbox", cfg.read_text()

    # a fresh app instance — as in a new `pistat` run — starts in gruvbox
    again = PistatApp(interval=0.3)
    async with again.run_test(size=(120, 44)):
        await asyncio.sleep(0.8)
        assert again.theme == "gruvbox", again.theme
        title = pistat.palette_from_theme(again.get_theme("gruvbox"))["title"]
        assert pistat.C_TITLE == f"bold {title}", (pistat.C_TITLE, title)

    # an explicit --theme wins and becomes the remembered default
    flagged = PistatApp(interval=0.3, theme="tokyo-night")
    async with flagged.run_test(size=(120, 44)):
        await asyncio.sleep(0.6)
        assert flagged.theme == "tokyo-night", flagged.theme
    assert json.loads(cfg.read_text())["theme"] == "tokyo-night"

    # a theme that no longer exists must not brick startup: fall back and clean up
    cfg.write_text(json.dumps({"theme": "no-such-theme"}))
    fallback = PistatApp(interval=0.3)
    async with fallback.run_test(size=(120, 44)):
        await asyncio.sleep(0.6)
        assert fallback.theme == "pistat", fallback.theme
    assert json.loads(cfg.read_text())["theme"] == "pistat", cfg.read_text()

    # a corrupt config file is treated as empty, not fatal
    cfg.write_text("{not json")
    assert PistatApp(interval=0.3).theme == "pistat"


async def test_sort() -> None:
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.2)
        await pilot.press("p")                     # freeze the sample
        await asyncio.sleep(0.3)
        assert app.sort_key == "cpu" and app.reverse is True

        # click the MEM% header
        span = next(s for s in app._col_spans if s[0] == "mem")
        app.header_click(span[1] + 1, 0)
        await asyncio.sleep(0.3)
        assert app.sort_key == "mem", app.sort_key
        assert app.reverse is True
        rss = [r["rss"] for r in app._rows]
        assert rss == sorted(rss, reverse=True), "rows are not sorted by memory"

        # clicking the same header flips the direction
        app.header_click(span[1] + 1, 0)
        await asyncio.sleep(0.3)
        assert app.reverse is False
        rss = [r["rss"] for r in app._rows]
        assert rss == sorted(rss), "reverse toggle did not re-sort"

        # clicking COMMAND sorts by process name, ascending first
        cmd_span = app._col_spans[-1]
        assert cmd_span[0] == "name"
        app.header_click(cmd_span[1] + 1, 0)
        await asyncio.sleep(0.3)
        assert app.sort_key == "name" and app.reverse is False
        names = [r["name"].lower() for r in app._rows]
        assert names == sorted(names)

        # a click below the header must not change anything
        app.header_click(span[1] + 1, 3)
        await asyncio.sleep(0.2)
        assert app.sort_key == "name"

        # the header marks the active column and its direction
        head = app.query_one("#p_procs").content.plain.split("\n")[0]
        assert "↑COMMAND" in head, head

        # keyboard cycling still works from a clicked key
        await pilot.press("s")
        await asyncio.sleep(0.3)
        assert app.sort_key != "name"


async def test_kill() -> None:
    victim = await asyncio.create_subprocess_exec("sleep", "120")
    victim2 = await asyncio.create_subprocess_exec("sleep", "120")
    try:
        app = PistatApp(interval=0.3)
        async with app.run_test(size=(120, 44)) as pilot:
            await asyncio.sleep(1.2)

            def find(pid: int) -> int | None:
                return next((i for i, r in enumerate(app._rows) if r["pid"] == pid), None)

            def select(pid: int) -> None:
                i = find(pid)
                assert i is not None, f"pid {pid} is not in the process list"
                app.sel = i

            idx: int | None = None
            for _ in range(15):
                idx = find(victim.pid)
                if idx is not None:
                    break
                await asyncio.sleep(0.3)
            assert idx is not None, "victim never appeared in the process list"

            await pilot.press("p")                 # freeze so indices stay put
            await asyncio.sleep(0.3)

            # -- cancel leaves it alone
            select(victim.pid)
            await pilot.press("x")
            await asyncio.sleep(0.3)
            assert isinstance(app.screen, Confirm), app.screen
            await pilot.press("n")
            await asyncio.sleep(0.3)
            assert not isinstance(app.screen, Confirm)
            assert victim.returncode is None, "cancel still killed the process"

            # -- confirm with SIGTERM
            select(victim.pid)
            await pilot.press("x")
            await asyncio.sleep(0.3)
            assert isinstance(app.screen, Confirm)
            await pilot.press("y")
            await asyncio.sleep(0.8)
            assert victim.returncode is not None, "SIGTERM did not kill the process"

            # -- SIGKILL path
            select(victim2.pid)
            await pilot.press("X")
            await asyncio.sleep(0.3)
            assert isinstance(app.screen, Confirm)
            await pilot.press("y")
            await asyncio.sleep(0.8)
            assert victim2.returncode is not None, "SIGKILL did not kill the process"

            # -- protected targets never open a dialog
            select(1)
            app.action_kill("term")
            await asyncio.sleep(0.3)
            assert not isinstance(app.screen, Confirm), "pid 1 should be refused"
            select(os.getpid())
            app.action_kill("term")
            await asyncio.sleep(0.3)
            assert not isinstance(app.screen, Confirm), "self should be refused"
    finally:
        for p in (victim, victim2):
            if p.returncode is None:
                p.kill()
            await p.wait()


async def main() -> None:
    for fn in (test_theme_switch, test_theme_persist, test_sort, test_kill):
        await fn()
        print("ok", fn.__name__)
    print("4 feature checks passed")


if __name__ == "__main__":
    asyncio.run(main())
