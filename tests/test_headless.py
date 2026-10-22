"""Headless render check for pistat: drives the real app, dumps rendered text."""
import asyncio
import re
import sys

sys.path.insert(0, "/home/kempi/pistat")
from pistat import PistatApp  # noqa: E402

W, H = (int(sys.argv[1]) if len(sys.argv) > 1 else 120,
        int(sys.argv[2]) if len(sys.argv) > 2 else 40)


def svg_text(svg: str) -> str:
    """Extract readable rows from a textual SVG screenshot."""
    rows = {}
    for m in re.finditer(r'<text[^>]*class="[^"]*"[^>]*>(.*?)</text>', svg):
        pass
    # simpler: pull every <text ...>...</text> with y coordinate
    for m in re.finditer(r'<text\b[^>]*\by="([\d.]+)"[^>]*>(.*?)</text>', svg, re.S):
        y = float(m.group(1))
        raw = m.group(2)
        txt = re.sub(r"<[^>]+>", "", raw)
        txt = (txt.replace("&#160;", " ").replace("&amp;", "&")
                  .replace("&lt;", "<").replace("&gt;", ">").replace("&#8212;", "—"))
        rows.setdefault(round(y, 1), []).append((float(re.search(r'\bx="([\d.]+)"', m.group(0)).group(1)), txt))
    out = []
    for y in sorted(rows):
        out.append("".join(t for _, t in sorted(rows[y])))
    return "\n".join(out)


async def main() -> None:
    app = PistatApp(interval=0.5)
    async with app.run_test(size=(W, H)) as pilot:
        await asyncio.sleep(2.2)
        await pilot.pause()
        # exercise the key bindings too
        await pilot.press("s")
        await asyncio.sleep(0.8)
        await pilot.press("p")
        await asyncio.sleep(0.3)
        await pilot.press("p")
        await asyncio.sleep(0.8)
        svg = app.export_screenshot()
        open("/home/kempi/pistat/screenshot.svg", "w").write(svg)
        print(svg_text(svg))
        table = app.query_one("#procs")
        print("\n--- row_count:", table.row_count, "size:", app.size)


asyncio.run(main())
