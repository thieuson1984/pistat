"""Headless layout probe: sizes, display flags, and any captured exceptions."""
import asyncio
import sys

sys.path.insert(0, "/home/kempi/pistat")
from pistat import PistatApp  # noqa: E402


async def probe(cols, rows):
    app = PistatApp(interval=0.4)
    async with app.run_test(size=(cols, rows)) as pilot:
        await asyncio.sleep(1.6)
        await pilot.pause()
        print(f"--- {cols}x{rows}")
        print("  screen", app.size)
        for wid in ("#top", "#procsbox", "#procs", "#filter", "#p_cpu", "#p_mem",
                    "#p_disk", "#p_net", "#p_pi", "#p_hist"):
            w = app.query_one(wid)
            print(f"  {wid:<10} size={w.size} region={w.region} display={w.display} "
                  f"classes={list(w.classes)}")
        print("  table rows:", app.query_one("#procs").row_count)
        attrs = [a for a in dir(app.query_one("#p_cpu")) if "content" in a or "render" in a]
        print("  static attrs:", attrs[:12])


for spec in [(120, 44), (96, 30), (76, 26)]:
    asyncio.run(probe(*spec))
