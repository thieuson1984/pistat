"""A/B harness: run pistat with parts of the repaint disabled to find hot spots."""
import sys

sys.path.insert(0, "/home/kempi/pistat")
import pistat

mode = sys.argv[1] if len(sys.argv) > 1 else "all"

orig_render_all = pistat.PistatApp.render_all
orig_render_procs = pistat.PistatApp.render_procs


def panels_only(self, s):
    orig_render_procs_orig = pistat.PistatApp.render_procs
    pistat.PistatApp.render_procs = lambda *a, **k: None
    try:
        orig_render_all(self, s)
    finally:
        pistat.PistatApp.render_procs = orig_render_procs_orig


def procs_only(self, s):
    saved = {}
    for pid in ("#p_cpu", "#p_mem", "#p_disk", "#p_net", "#p_pi", "#p_hist"):
        panel = self.query_one(pid, pistat.Panel)
        saved[pid] = panel
    # temporarily no-op the panel updates
    orig_update = pistat.Panel.update
    pistat.Panel.update = lambda *a, **k: None
    try:
        orig_render_all(self, s)
    finally:
        pistat.Panel.update = orig_update


def frozen(self, s):
    """Render once, then never again: baseline Textual idle cost."""
    if getattr(self, "_done_once", False):
        return
    self._done_once = True
    orig_render_all(self, s)


if mode == "panels":
    pistat.PistatApp.render_all = panels_only
elif mode == "procs":
    pistat.PistatApp.render_all = procs_only
elif mode == "frozen":
    pistat.PistatApp.render_all = frozen

pistat.PistatApp(interval=1.0).run()
