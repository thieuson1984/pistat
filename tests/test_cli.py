"""CLI contract: --version, --json, --once, width handling and interval clamping."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pistat.py"
sys.path.insert(0, str(ROOT))

# never read/write the developer's real ~/.config/pistat from a test run
os.environ["PISTAT_CONFIG_DIR"] = tempfile.mkdtemp(prefix="pistat-test-cfg-")

import pistat


def run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(SCRIPT), *args], check=False,
                          capture_output=True, text=True, timeout=timeout, env=env)


def test_version() -> None:
    out = run("--version")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"pistat {pistat.__version__}", out.stdout


def test_help() -> None:
    out = run("--help")
    assert out.returncode == 0
    for flag in ("--once", "--json", "--interval", "--width", "--version",
                 "--theme", "--themes"):
        assert flag in out.stdout, f"{flag} missing from --help"


def test_themes_listing() -> None:
    out = run("--themes")
    assert out.returncode == 0, out.stderr
    names = [line.lstrip("* ").strip() for line in out.stdout.splitlines() if line.strip()]
    for name in ("pistat", "nord", "gruvbox", "solarized-light"):
        assert name in names, f"{name} missing from --themes"
    assert any(line.startswith("*") for line in out.stdout.splitlines()), out.stdout


def test_unknown_theme_rejected() -> None:
    out = run("--theme", "definitely-not-a-theme")
    assert out.returncode == 2, out.returncode
    assert "unknown theme" in out.stderr.lower(), out.stderr


def test_json() -> None:
    out = run("--json", "-i", "0.2")
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["pistat"] == pistat.__version__
    for key in ("cpu", "memory", "swap", "thermal", "disk", "network", "processes"):
        assert key in data, key
    assert data["processes"]["count"] > 0
    assert data["cpu"]["per_core_pct"], "no per-core data"
    assert data["memory"]["total"] > 0
    assert data["thermal"]["soc_c"] is None or data["thermal"]["soc_c"] > 0
    # clock and power blocks are part of the contract, not an afterthought
    clock = data["cpu"]["clock"]
    for key in ("current_mhz", "peak_mhz", "floor_mhz", "limit_max_mhz",
                "pct_of_max", "dvfs_steps", "governor"):
        assert key in clock, key
    assert isinstance(data["cpu"]["clock_hz"], int)
    power = data["power"]
    for key in ("core_volts", "rails_volts", "throttle_now", "throttle_seen",
                "under_voltage_now", "under_voltage_seen"):
        assert key in power, key
    assert isinstance(power["throttle_now"], list)


def test_once_width() -> None:
    out = run("--once", "-i", "0.2", "-w", "72")
    assert out.returncode == 0, out.stderr
    assert "PROCESSES" in out.stdout
    widest = max(len(line) for line in out.stdout.splitlines())
    assert widest <= 72, f"--once produced {widest} columns with -w 72"


def test_interval_clamped() -> None:
    out = run("--json", "-i", "0")
    assert out.returncode == 0, out.stderr
    assert "minimum" in out.stderr, out.stderr
    json.loads(out.stdout)


def test_no_color_when_piped() -> None:
    """A pipe is not a terminal: rich must emit no ANSI escapes."""
    out = run("--once", "-i", "0.2", "-w", "80")
    assert out.returncode == 0, out.stderr
    assert "\x1b[" not in out.stdout, "ANSI escapes leaked into a piped run"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok", fn.__name__)
    print(f"{len(tests)} CLI checks passed")
