"""Test-only adapter that lets the repo's FAKE ffmpeg/ffprobe scripts run on every platform (the scripts are POSIX files with a shebang, which Windows cannot execute by path).

Idea and wording of the scope labels follow `tests/portable_lifecycle.py` on the separate core branch c7da5ac9 (read only; nothing is copied from it, nothing from that branch
is merged, and no runtime fix of that branch is assumed). This is a smaller, independent helper.

SCOPE - read before relying on a result that uses it:
* REAL: the child is a real Python process started by the real `runner.run_bounded` (own session/process group, real pipes, deadlines, drain and cleanup). The code under test
  (`reinspect`, `avmux.mux`, `tools.probe_version`) runs unmodified, including its receipts and hashing.
* MAPPED: for exactly the registered tool paths, `[tool, *args]` becomes `[sys.executable, -c, DRIVER, tool, *args]`; every keyword argument is passed through untouched. Receipts
  therefore record the driver argv, not an `ffprobe` command line.
* NOT COVERED: real ffprobe/ffmpeg, real media, Windows executable-path/PATHEXT handling, console/OS-signal delivery, resource-limit enforcement. A result obtained with this adapter is
  evidence about the orchestration and parsing only.
"""

from __future__ import annotations

import contextlib
import os
import sys
from unittest import mock

from video_core import runner

PY = sys.executable
_DRIVER = ("import sys; path = sys.argv[1]; sys.argv = [path] + sys.argv[2:]; "
           "exec(compile(open(path).read(), path, 'exec'), {'__name__': '__main__', '__file__': path})")


@contextlib.contextmanager
def owned_python_tools(*tool_paths: str):
    """Yield the list of mapped invocations ({'tool_path', 'tool_argv'}); anything not registered goes to the real run_bounded unchanged."""
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
            return real([PY, "-c", _DRIVER, os.path.abspath(argv[0]), *argv[1:]], **kw)
        return real(argv, **kw)

    with mock.patch.object(runner, "run_bounded", adapted):
        yield seen
