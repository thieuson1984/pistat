"""Exercise pistat's key bindings, filter and list navigation."""
import asyncio
import sys

sys.path.insert(0, "/home/kempi/pistat")
from pistat import PistatApp  # noqa: E402


async def main() -> None:
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(120, 44)) as pilot:
        await asyncio.sleep(1.2)
        print("rows at start:", len(app._rows), "| selected:", app.sel, "| top:",
              app._rows[0]["name"] if app._rows else None)

        await pilot.press("down", "down", "down")
        await asyncio.sleep(0.3)
        print("after 3x down -> sel", app.sel, "pid", app._rows[app.sel]["pid"])
        await pilot.press("end")
        await asyncio.sleep(0.3)
        print("after end -> sel", app.sel, "of", len(app._rows),
              "| offset", app.offset)
        await pilot.press("pageup")
        await asyncio.sleep(0.3)
        print("after pageup -> sel", app.sel, "offset", app.offset)
        await pilot.press("home")
        await asyncio.sleep(0.3)
        print("after home -> sel", app.sel)

        await pilot.press("s")
        await asyncio.sleep(0.3)
        print("sort key after 's':", app.sort_key)
        await pilot.press("S")
        await asyncio.sleep(0.3)
        print("reverse after 'S':", app.reverse)

        await pilot.press("p")
        await asyncio.sleep(0.4)
        print("paused:", app.paused)
        await pilot.press("p")

        await pilot.press("2")
        await asyncio.sleep(0.4)
        print("interval:", app.interval)

        await pilot.press("slash")
        await asyncio.sleep(0.3)
        box = app.query_one("#filter")
        print("filter visible:", box.display, "focused:", box.has_focus)
        for ch in "dockerd":
            await pilot.press(ch)
        await asyncio.sleep(0.6)
        print("filter text:", box.value, "| matching rows:", len(app._rows))
        await pilot.press("escape")
        await asyncio.sleep(0.6)
        print("after esc -> filter:", repr(app.filter_text), "rows:", len(app._rows),
              "| input displayed:", box.display)

        print("paint cache entries:", len(app._panel_cache),
              "| procs panel:", app.query_one("#p_procs").content_size)


asyncio.run(main())
