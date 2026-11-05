#!/usr/bin/env python3
"""LINUX SIMULATION of the one Windows property that broke tests/test_reinspect_original_receipt.py in 91ec2d4: a fake ffprobe that is a text script cannot be started by
path. Two patches, applied before the tests run:
  * os.chmod becomes a no-op, so the fake tool files are created NON-executable (direct execution then fails with PermissionError -> LAUNCH_FAILED);
  * os.access(path, X_OK) answers True for existing regular files, as it does on Windows (so tools.locate still finds the tool, then the launch fails).
This is a simulation on Linux. It is NOT a Windows run, NOT evidence about real ffprobe, and proves nothing about Windows signals or PATHEXT handling.

    python3 windows_like_exec_simulation.py tests.test_reinspect_original_receipt [more test ids...]   (run from tools/video-core)
"""
import os
import stat
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())
_real_access = os.access
os.chmod = lambda *a, **k: None


def _access(path, mode, **kw):
    if mode == os.X_OK:
        try:
            return stat.S_ISREG(os.stat(path).st_mode)
        except OSError:
            return False
    return _real_access(path, mode, **kw)


os.access = _access
unittest.main(module=None, argv=["sim", "-v", *sys.argv[1:]], verbosity=2)
