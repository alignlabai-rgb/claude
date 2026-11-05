"""Test-only portable harness for the orchestrator lifecycle scenarios (Windows and Linux). Production code is not touched.

What is REAL, what is MAPPED, what is INJECTED (keep these apart when reading a result):

* REAL  - every child is a tiny Python process started by the real `runner.run_bounded` (own session / process group, real pipes, real deadlines, real termination and cleanup).
          The orchestrators (`render`, `avmux.mux`) run unmodified, including their receipts, hashing, inventories and temp-directory handling.
* MAPPED (tool argv) - the orchestrators are given paths to the existing FAKE ffmpeg / ffprobe scripts (tests/helpers.py, tests/avmux_fakes.py). A script cannot be executed by path on
          Windows (no shebang, no suffix magic, and nothing here is disguised as an executable by a suffix), so `owned_python_tools` maps `[tool_path, *args]` to
          `[sys.executable, -c, DRIVER, tool_path, *args]` for exactly the registered tool paths and delegates to the REAL `run_bounded` with every keyword unchanged. Receipts therefore record
          the actual driver argv, not an `ffmpeg` command line. This is NOT FFmpeg, NOT media, and NOT acceptance of any production executable-path handling.
* TEXT MODE - with `crlf=True` (default) the driver reconfigures the child's stdout/stderr to write "\\r\\n", emulating Windows text mode on Linux so the parsers see CRLF there too.
* INJECTED - cancellation is delivered with `_thread.interrupt_main()` (Python's "simulate SIGINT arrived" for the main thread; no OS signal, no console event, no other process) or by raising
          `runner.RunInterrupted` at a named pipeline boundary. Real OS signal / Ctrl-C / SIGTERM delivery to a CLI process is NOT exercised by this harness: UNVERIFIED on Windows here.
"""

from __future__ import annotations

import _thread
import contextlib
import os
import sys
import threading
import time
from unittest import mock

from video_core import runner

PY = sys.executable

_DRIVER_TEXT = ("import sys; sys.stdout.reconfigure(newline='\\r\\n'); sys.stderr.reconfigure(newline='\\r\\n'); path = sys.argv[1]; sys.argv = [path] + sys.argv[2:]; "
                "exec(compile(open(path).read(), path, 'exec'), {'__name__': '__main__', '__file__': path})")
_DRIVER_PLAIN = ("import sys; path = sys.argv[1]; sys.argv = [path] + sys.argv[2:]; "
                 "exec(compile(open(path).read(), path, 'exec'), {'__name__': '__main__', '__file__': path})")


@contextlib.contextmanager
def owned_python_tools(*tool_paths: str, crlf: bool = True):
    """Run the registered fake tool scripts as real owned Python children through the REAL run_bounded. Yields the list of mapped invocations (tool_path, tool_argv).
    Anything not registered (and every keyword argument) is passed to the real run_bounded untouched."""
    registry = {os.path.abspath(p) for p in tool_paths}
    for p in registry:
        if not os.path.isfile(p):
            raise AssertionError(f"not a regular file: {p}")
    real = runner.run_bounded
    seen: list = []

    def adapted(argv, **kw):
        argv = list(argv)
        if argv and os.path.abspath(argv[0]) in registry:
            seen.append({"tool_path": os.path.abspath(argv[0]), "tool_argv": argv[1:]})
            return real([PY, "-c", _DRIVER_TEXT if crlf else _DRIVER_PLAIN, os.path.abspath(argv[0]), *argv[1:]], **kw)
        return real(argv, **kw)

    with mock.patch.object(runner, "run_bounded", adapted):
        yield seen


def interrupt_main_when(marker: str, *, delay: float = 0.15, give_up: float = 8.0) -> threading.Thread:
    """`delay` seconds after `marker` exists, simulate SIGINT for the MAIN thread of this process (`_thread.interrupt_main`). Needs Python's default SIGINT handler
    (tests.lifecycle_support.sigint_default). No OS signal is sent anywhere and no other process or console is involved."""
    def run():
        end = time.monotonic() + give_up
        while time.monotonic() < end and not os.path.exists(marker):
            time.sleep(0.01)
        if os.path.exists(marker):
            time.sleep(delay)
            _thread.interrupt_main()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
