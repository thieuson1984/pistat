"""Layout regression suite.

Guards the bug that made the process list disappear (and the panel grid spill
off-screen) on short terminals: at every size the list must be on screen, keep
at least four rows, and every panel line must fit its own box.
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

SIZES = [
    (160, 60), (140, 50), (120, 44), (104, 34), (100, 30),
    (80, 26), (70, 24), (62, 20), (62, 16), (50, 14), (40, 10),
]


def expected_shape(cols: int, rows: int) -> tuple[int, int]:
    """The shape the app should choose, derived independently of its code."""
    body = max(4, rows - 2)
    c = 3 if cols >= 104 else 2 if cols >= 62 else 1
    bands = (body - 8) // 9 if body >= 17 else 0
    return c, max(0, min(bands, 2 if c > 1 else 3))


async def check(cols: int, rows: int) -> str:
    app = PistatApp(interval=0.3)
    async with app.run_test(size=(cols, rows)) as pilot:
        await asyncio.sleep(1.1)
        await pilot.pause()

        top = app.query_one("#top")
        procs = app.query_one("#p_procs")
        c, bands = expected_shape(cols, rows)
        label = f"{cols}x{rows} (g{c}{bands})"

        # 1. nothing is drawn past the bottom of the screen
        assert procs.region.bottom <= app.size.height, (
            f"{label}: process panel ends at row {procs.region.bottom} "
            f"of {app.size.height}")

        # 2. the grid is either shaped as expected, or dropped entirely
        if bands:
            assert top.display, f"{label}: panel grid missing"
            assert f"g{c}{bands}" in top.classes, f"{label}: classes {list(top.classes)}"
        else:
            assert not top.display, f"{label}: grid should be hidden"
            assert all(not app.query_one(p).display for p in PistatApp.PANELS)

        # 3. the visible panels match the shape
        if bands:
            vis = [p for p in PistatApp.PANELS if app.query_one(p).display]
            assert tuple(vis) == PistatApp.SHAPES[(c, bands)], f"{label}: panels {vis}"

        # 4. the process list is usable
        assert procs.display, f"{label}: process list hidden"
        assert app._view_h >= 4, f"{label}: only {app._view_h} process rows"

        # 5. no panel line is wider or taller than its box
        for pid in PistatApp.PANELS + ("#p_procs",):
            panel = app.query_one(pid)
            if not panel.display:
                continue
            body = getattr(panel, "content", None)
            if not hasattr(body, "plain"):
                raise AssertionError(f"{label}: {pid} has no rendered text to check")
            lines = body.plain.split("\n")
            cw, ch = panel.content_size
            for i, line in enumerate(lines):
                assert len(line) <= max(1, cw), (
                    f"{label}: {pid} line {i} is {len(line)} cells, box is {cw}")
            assert len(lines) <= max(1, ch), (
                f"{label}: {pid} draws {len(lines)} lines into {ch} rows")
        return label


async def test_layout_sizes() -> None:
    """Every supported terminal size keeps the list on screen and text clipped."""
    for cols, rows in SIZES:
        label = await check(cols, rows)
        print("ok", label)


async def main() -> None:
    await test_layout_sizes()


if __name__ == "__main__":
    asyncio.run(main())
    print(f"all {len(SIZES)} sizes passed")
