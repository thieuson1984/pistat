"""Exercise pistat's key bindings, filter and list navigation.

Exits non-zero on the first failed assertion, so it doubles as a smoke test.
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

from pistat import PistatApp


async def main() -> None:
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.4)
        assert app._rows, "no processes sampled"
        print("rows at start:", len(app._rows), "| selected:", app.sel,
              "| top:", app._rows[0]["name"])

        # pause first: with sampling stopped the list cannot change under us,
        # so the navigation assertions below are exact
        await pilot.press("p")
        await asyncio.sleep(0.3)
        assert app.paused

        await pilot.press("down", "down", "down")
        await asyncio.sleep(0.3)
        assert app.sel == 3, app.sel
        print("after 3x down -> sel", app.sel, "pid", app._rows[app.sel]["pid"])

        await pilot.press("end")
        await asyncio.sleep(0.3)
        assert app.sel == len(app._rows) - 1, (app.sel, len(app._rows))
        print("after end -> sel", app.sel, "of", len(app._rows), "| offset", app.offset)

        await pilot.press("pageup")
        await asyncio.sleep(0.3)
        assert app.sel < len(app._rows) - 1
        print("after pageup -> sel", app.sel, "offset", app.offset)

        await pilot.press("home")
        await asyncio.sleep(0.3)
        assert app.sel == 0
        print("after home -> sel", app.sel)

        seen = [app.sort_key]
        for _ in range(len(app.SORTS)):
            await pilot.press("s")
            await asyncio.sleep(0.25)
            seen.append(app.sort_key)
        assert seen[1:] == list(app.SORTS[1:]) + [app.SORTS[0]], seen
        print("sort cycle:", " -> ".join(seen))

        await pilot.press("S")
        await asyncio.sleep(0.3)
        print("reverse after 'S':", app.reverse)

        await pilot.press("p")
        await asyncio.sleep(0.3)
        assert not app.paused
        print("resumed")

        await pilot.press("2")
        await asyncio.sleep(0.4)
        assert app.interval == 2.0
        print("interval:", app.interval)

        await pilot.press("slash")
        await asyncio.sleep(0.3)
        box = app.query_one("#filter")
        assert box.has_focus, "filter did not take focus"
        print("filter visible:", box.display, "focused:", box.has_focus)
        for ch in "dockerd":
            await pilot.press(ch)
        await asyncio.sleep(0.6)
        print("filter text:", box.value, "| matching rows:", len(app._rows))
        assert app.last is not None
        assert len(app._rows) < len(app.last["procs"]), "filter did not narrow the list"
        assert all("dockerd" in r["name"].lower() for r in app._rows), "filter leaked rows"

        await pilot.press("escape")
        await asyncio.sleep(0.6)
        assert app.filter_text == "" and not box.display
        print("after esc -> filter:", repr(app.filter_text), "rows:", len(app._rows),
              "| input displayed:", box.display)

        print("paint cache entries:", len(app._panel_cache),
              "| procs panel:", app.query_one("#p_procs").content_size)


if __name__ == "__main__":
    asyncio.run(main())
