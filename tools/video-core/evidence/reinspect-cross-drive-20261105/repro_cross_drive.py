#!/usr/bin/env python3
"""Short reproduction of the cross-drive ValueError in avmux.reinspect, runnable on Linux. Observation only; run from tools/video-core of any commit.

The Windows report (independent, native): media on C:, output root on D: (or the reverse) -> `ValueError: Paths don't have the same drive` from os.path.commonpath inside
reinspect_audio_packets, untyped, before any process or output. Here the SAME code path is exercised on Linux by (a) giving reinspect Windows-style paths through the two path
checkers it calls and (b) pointing os.path.commonpath at ntpath.commonpath for the duration. This is a SIMULATION of the path arithmetic, not a Windows run; no file is read or written.
"""
import json, ntpath, os, sys
from unittest import mock
sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())
from video_core.avmux import reinspect
from video_core.errors import VideoCoreError
from video_core.paths import OutputLocation


class Reached(Exception):
    """Raised by the patched getsize: proves the containment check was passed."""


def attempt(media_dir, run_dir):
    loc = OutputLocation(root=ntpath.dirname(run_dir), name=ntpath.basename(run_dir), run_dir=run_dir)
    with mock.patch.object(reinspect, "check_media_for_inspect", lambda p: (ntpath.join(media_dir, "m.mp4"), "mov")), \
         mock.patch.object(reinspect, "check_output_location", lambda *a, **k: loc), \
         mock.patch.object(os.path, "commonpath", ntpath.commonpath), \
         mock.patch.object(os.path, "dirname", ntpath.dirname), \
         mock.patch.object(os.path, "getsize", lambda p: (_ for _ in ()).throw(Reached())):
        try:
            reinspect.reinspect_audio_packets("ignored", expected_sha256="0" * 64, output_root="ignored", output_name="ignored")
        except Reached:
            return "PASSED the containment check (reached the next stage)"
        except VideoCoreError as exc:
            return "typed rejection: " + ",".join(i.code for i in exc.issues)
        except BaseException as exc:  # noqa: BLE001
            return f"UNTYPED {type(exc).__name__}: {exc}"


cases = {"media C:, output D: (different drives)": (r"C:\Temp\media", r"D:\ws\out\run1"), "media D:, output C: (different drives)": (r"D:\ws\media", r"C:\Temp\out\run1"),
         "same drive, media elsewhere": (r"D:\media", r"D:\ws\out\run1"), "same drive, media inside the output dir": (r"D:\ws\out\run1\sub", r"D:\ws\out\run1"),
         "same drive, media dir == output dir": (r"D:\ws\out\run1", r"D:\ws\out\run1"), "same drive, output dir inside media dir": (r"D:\media", r"D:\media\out\run1")}
try:
    ntpath.commonpath([r"C:\a", r"D:\b"])
    stdlib = "no error"
except ValueError as exc:
    stdlib = f"ValueError: {exc}"
print(json.dumps({"stdlib_fact_ntpath.commonpath(C:,D:)": stdlib, "python": sys.version.split()[0], **{k: attempt(*v) for k, v in cases.items()}}, indent=1))
