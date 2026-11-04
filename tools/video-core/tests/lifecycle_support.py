"""Support for the lifecycle tests (runner drain/interrupt, orchestrator cancellation, receipt finalization).

Everything here is bounded and owned: children are tiny `python -c` programs or wrappers around the existing FAKE ffmpeg/ffprobe scripts (never real media tools, never real
media); every sleep is finite (<= 10 s); signals are only ever sent to this test process itself (a SIGINT it then handles) or to process groups the runner owns.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
import time

PY = sys.executable
POSIX = os.name == "posix"


@contextlib.contextmanager
def sigint_default():
    """Make the scenario independent of how the suite was launched: a background job started with `&` by a non-interactive shell inherits SIGINT=SIG_IGN, and Python then does
    NOT install its KeyboardInterrupt handler (the signal would be silently dropped and a cancellation test would time out instead). Restores the previous handler."""
    old = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old)


def interrupt_when(marker: str, *, delay: float = 0.15, give_up: float = 8.0) -> threading.Thread:
    """Send SIGINT to THIS process (delivered to the main thread) `delay` seconds after `marker` exists. Never signals anything else."""
    def run():
        end = time.monotonic() + give_up
        while time.monotonic() < end and not os.path.exists(marker):
            time.sleep(0.01)
        if os.path.exists(marker):
            time.sleep(delay)
            os.kill(os.getpid(), signal.SIGINT)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def read_pid(path: str, wait: float = 6.0) -> int:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            with open(path) as fh:
                text = fh.read().strip()
            if text:
                return int(text)
        except (OSError, ValueError):
            pass
        time.sleep(0.02)
    raise AssertionError(f"no pid appeared in {path}")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


_WRAPPER = r'''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "lifecycle_cfg.json")))
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
with open(os.path.join(here, "launches.log"), "a") as log:
    log.write(json.dumps({{"tool": name, "argv": args}}) + "\n")
acted = os.path.join(here, "acted")
if name == cfg["tool"] and all(any(n in a for a in args) for n in cfg["needle"]) and not os.path.exists(acted):
    open(acted, "w").close()  # only the FIRST matching launch acts; later matching launches (e.g. readback of the same file) run the fake normally
    if cfg.get("remove"):
        os.remove(cfg["remove"])
    if cfg.get("marker"):
        with open(cfg["marker"], "w") as fh:
            fh.write(str(os.getpid()))
    if cfg.get("sleep"):
        time.sleep(cfg["sleep"])
        sys.exit(0)
# run the original fake in-process so sys.argv[0] (which names the tool) and the __file__ directory (behavior.json, calls.log) stay exactly as before
real = os.path.join(here, name + ".real")
exec(compile(open(real).read(), real, "exec"), {{"__name__": "__main__", "__file__": real}})
'''


def wrap_tools(bin_dir: str, *, tool: str, needle: list, marker: str | None = None, sleep: float = 0.0, remove: str | None = None) -> None:
    """Put a thin wrapper in front of the existing fake `ffmpeg`/`ffprobe` in bin_dir. Every launch is appended to bin_dir/launches.log. Only the first launch of `tool` whose argv
    contains every `needle` substring optionally deletes `remove`, writes its pid to `marker`, and sleeps `sleep` seconds (<= 10) instead of running; all other launches (and
    non-matching ones) run the original fake unchanged."""
    assert sleep <= 10.0
    for name in ("ffmpeg", "ffprobe"):
        path = os.path.join(bin_dir, name)
        real = path + ".real"
        if not os.path.exists(real):
            os.rename(path, real)
        with open(path, "w") as fh:
            fh.write(_WRAPPER.format(python=PY))
        os.chmod(path, 0o755)
    for stale in ("acted", "launches.log"):  # a new configuration starts from a clean slate
        if os.path.exists(os.path.join(bin_dir, stale)):
            os.remove(os.path.join(bin_dir, stale))
    with open(os.path.join(bin_dir, "lifecycle_cfg.json"), "w") as fh:
        json.dump({"tool": tool, "needle": needle, "marker": marker, "sleep": sleep, "remove": remove}, fh)


def launches(bin_dir: str) -> list:
    path = os.path.join(bin_dir, "launches.log")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]
