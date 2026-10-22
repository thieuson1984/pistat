"""Headless layout probe: sizes, display flags and the resulting shape.

    python3 tests/probe.py            # a spread of terminal sizes
    python3 tests/probe.py 90 24      # one specific size
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pistat import PistatApp

PANELS = ("#p_cpu", "#p_mem", "#p_disk", "#p_net", "#p_pi", "#p_hist")


async def probe(cols: int, rows: int) -> None:
    app = PistatApp(interval=0.4)
    async with app.run_test(size=(cols, rows)) as pilot:
        await asyncio.sleep(1.4)
        await pilot.pause()
        top = app.query_one("#top")
        procs = app.query_one("#p_procs")
        shown = [p.removeprefix("#p_") for p in PANELS if app.query_one(p).display]
        print(f"--- {cols}x{rows}")
        print(f"  screen   {app.size}")
        print(f"  #top     {top.size} region={top.region} display={top.display} "
              f"classes={list(top.classes)}")
        print(f"  #procs   {procs.size} region={procs.region} view_h={app._view_h} "
              f"rows={len(app._rows)}")
        print(f"  panels   {shown}")
        print(f"  overflow {procs.region.bottom - app.size.height:+d} rows")


async def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2:
        await probe(int(args[0]), int(args[1]))
        return
    for spec in [(140, 50), (120, 44), (100, 30), (80, 26), (62, 20), (45, 14)]:
        await probe(*spec)


if __name__ == "__main__":
    asyncio.run(main())
