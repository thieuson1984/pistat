"""Measure where pistat spends its time: sampling vs rendering."""
import asyncio
import sys
import time

sys.path.insert(0, "/home/kempi/pistat")
import pistat

stats = {"sample": 0.0, "procs": 0.0, "render_all": 0.0, "n": 0}

orig_sample = pistat.Collector.sample
orig_procs = pistat.Collector._processes
orig_render = pistat.PistatApp.render_all


def timed_sample(self):
    t0 = time.perf_counter()
    out = orig_sample(self)
    stats["sample"] += time.perf_counter() - t0
    return out


def timed_procs(self, dt, mem_total):
    t0 = time.perf_counter()
    out = orig_procs(self, dt, mem_total)
    stats["procs"] += time.perf_counter() - t0
    return out


def timed_render(self, s):
    t0 = time.perf_counter()
    orig_render(self, s)
    stats["render_all"] += time.perf_counter() - t0
    stats["n"] += 1


pistat.Collector.sample = timed_sample
pistat.Collector._processes = timed_procs
pistat.PistatApp.render_all = timed_render


async def main() -> None:
    app = pistat.PistatApp(interval=1.0)
    async with app.run_test(size=(120, 44)):
        await asyncio.sleep(1.2)
        stats.update(sample=0.0, procs=0.0, render_all=0.0, n=0)
        await asyncio.sleep(10)
        n = max(1, stats["n"])
        print(f"ticks={stats['n']}")
        print(f"sample  : {1000*stats['sample']/n:6.1f} ms/tick")
        print(f"  procs : {1000*stats['procs']/n:6.1f} ms/tick (inside sample)")
        print(f"render  : {1000*stats['render_all']/n:6.1f} ms/tick")
        total = stats["sample"] + stats["render_all"]
        print(f"total   : {1000*total/n:6.1f} ms/tick  ->  {100*total/(stats['n']*1.0):4.1f}% of one core")


asyncio.run(main())
