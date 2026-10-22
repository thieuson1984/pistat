"""Headless render check: drive the real app, dump the screen as text.

    python3 tests/test_headless.py [cols] [rows] [out.svg]

Writes the SVG screenshot next to the repo (default: screenshot.svg).
"""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# never read/write the developer's real ~/.config/pistat from a test run
os.environ["PISTAT_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pistat-test-cfg-")

from pistat import PistatApp

W = int(sys.argv[1]) if len(sys.argv) > 1 else 120
H = int(sys.argv[2]) if len(sys.argv) > 2 else 40
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "screenshot.svg"


def svg_text(svg: str) -> str:
    """Extract readable rows from a textual SVG screenshot."""
    rows: dict[float, list[tuple[float, str]]] = {}
    for m in re.finditer(r'<text\b[^>]*\by="([\d.]+)"[^>]*>(.*?)</text>', svg, re.DOTALL):
        y = float(m.group(1))
        txt = re.sub(r"<[^>]+>", "", m.group(2))
        txt = (txt.replace("&#160;", " ").replace("&amp;", "&")
                  .replace("&lt;", "<").replace("&gt;", ">").replace("&#8212;", "—"))
        xm = re.search(r'\bx="([\d.]+)"', m.group(0))
        rows.setdefault(round(y, 1), []).append((float(xm.group(1)) if xm else 0.0, txt))
    return "\n".join("".join(t for _, t in sorted(rows[y])) for y in sorted(rows))


async def main() -> None:
    app = PistatApp(interval=0.5)
    async with app.run_test(size=(W, H)) as pilot:
        await asyncio.sleep(2.2)
        await pilot.pause()
        # exercise the bindings too: sort, pause, resume, filter
        await pilot.press("s")
        await asyncio.sleep(0.6)
        await pilot.press("p")
        await asyncio.sleep(0.3)
        await pilot.press("p")
        await asyncio.sleep(0.6)
        svg = app.export_screenshot()
        OUT.write_text(svg)
        print(svg_text(svg))
        print(f"\n--- {len(app._rows)} rows · view_h {app._view_h} · "
              f"size {app.size} · svg {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
