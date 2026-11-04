"""Shared test helpers: sample episode, no-launch guard, and FAKE ffmpeg/ffprobe scripts.

The fake tools are tiny Python scripts that imitate just enough of the ffmpeg/ffprobe
command line for orchestration tests (exit codes, signals, timeouts, noisy stderr).
They produce no real video and are never evidence of a render.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXAMPLE_EPISODE = os.path.join(ROOT, "examples", "episode.synthetic.json")
EXAMPLE_SRT = os.path.join(ROOT, "examples", "captions.synthetic.srt")
POSIX = os.name == "posix"


def sample_doc() -> dict:
    with open(EXAMPLE_EPISODE, encoding="utf-8") as fh:
        return json.load(fh)


def sample_bytes() -> bytes:
    with open(EXAMPLE_EPISODE, "rb") as fh:
        return fh.read()


def to_bytes(doc) -> bytes:
    try:
        return json.dumps(doc, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates can only travel as \uXXXX escapes
        return json.dumps(doc, ensure_ascii=True).encode("ascii")


def mutated(fn) -> dict:
    doc = copy.deepcopy(sample_doc())
    fn(doc)
    return doc


def episode_doc(*, fps=(30, 1), total=180, width=640, height=360, clips=None, captions=None) -> dict:
    doc = sample_doc()
    doc["fps"] = {"n": fps[0], "d": fps[1]}
    doc["total_frames"] = total
    doc["canvas"] = {"width": width, "height": height}
    if clips is not None:
        doc["clips"] = clips
    if captions is not None:
        doc["captions"] = captions
    return doc


def color_clips(durations, colors=("#30506a", "#6a5030")):
    out, cursor = [], 0
    for i, d in enumerate(durations):
        out.append({"id": f"c{i}", "start_frame": cursor, "duration_frames": d,
                    "source": {"kind": "color", "value": colors[i % len(colors)]}})
        cursor += d
    return out


@contextlib.contextmanager
def no_process():
    """Fail loudly if anything tries to launch a subprocess."""
    def boom(*a, **k):
        raise AssertionError("a subprocess was launched but must not be")
    with mock.patch.object(subprocess, "Popen", boom), mock.patch.object(os, "system", boom):
        yield


def tree_snapshot(path: str) -> dict:
    """{relpath: sha256 or 'DIR' or 'LINK'} for everything below path."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(path):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            snap[os.path.relpath(full, path)] = "LINK" if os.path.islink(full) else "DIR"
        for f in filenames:
            full = os.path.join(dirpath, f)
            if os.path.islink(full):
                snap[os.path.relpath(full, path)] = "LINK"
            elif stat.S_ISREG(os.lstat(full).st_mode):
                with open(full, "rb") as fh:
                    snap[os.path.relpath(full, path)] = hashlib.sha256(fh.read()).hexdigest()
            else:
                snap[os.path.relpath(full, path)] = "SPECIAL"
    return snap


def good_probe(width=640, height=360, fps="30/1", frames=180, duration="6.000000", codec="h264", pix_fmt="yuv420p", extra_streams=()):
    streams = [{"index": 0, "codec_type": "video", "codec_name": codec, "width": width, "height": height, "pix_fmt": pix_fmt,
                "r_frame_rate": fps, "avg_frame_rate": fps, "nb_frames": str(frames), "nb_read_frames": str(frames), "duration": duration}]
    streams += list(extra_streams)
    return {"streams": streams, "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "nb_streams": len(streams), "duration": duration}}


_FAKE = r'''#!{python}
import json, os, signal, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
b = json.load(open(os.path.join(here, "behavior.json")))
args = sys.argv[1:]
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps({{"argv": args, "cwd": os.getcwd(), "home": os.environ.get("HOME"), "env_keys": sorted(os.environ)}}) + "\n")
name = os.path.basename(sys.argv[0])
if "-version" in args:
    print(b.get("version_line_" + name, name + " version 0.0-fake Copyright (c) fake"))
    sys.exit(b.get("version_exit", 0))
if name == "ffprobe":
    if b.get("probe_mode") == "fail":
        sys.stderr.write("probe failed"); sys.exit(1)
    if b.get("probe_mode") == "garbage":
        print("not json"); sys.exit(0)
    print(json.dumps(b["probe"])); sys.exit(0)
if "-encoders" in args:
    print(" V....D " + b.get("encoder", "libx264") + "              fake"); sys.exit(0)
def emit(line):
    sys.stdout.write(line + ("\r\n" if b.get("crlf") else "\n"))
if "-filters" in args:
    if b.get("filters_exit"):
        sys.stderr.write("listing failed"); sys.exit(b["filters_exit"])
    layout = b.get("filters_layout", "three")
    if layout == "unknown":
        for line in ("Filters:", "  X = unknown legend", " <color> video source", "color: |->V"):
            emit(line)
        sys.exit(0)
    emit("Filters:")
    for f in b.get("filters", ["color","trim","setpts","scale","format","setsar","concat","fps","pad","drawtext","null"]):
        emit((" .. " if layout == "two" else " ... ") + f + "            V->V       fake")
    sys.exit(0)
if "-demuxers" in args:
    print(" DE mov,mp4,m4a,3gp,3g2,mj2 fake"); print(" DE matroska,webm fake"); print(" D  image2 fake"); sys.exit(0)
# ---- render invocation: output path is the last argument ----
out = args[-1]
run_dir = os.path.dirname(out)
mode = b.get("mode", "ok")
try:
    rec = json.load(open(os.path.join(run_dir, "receipt.json")))
    seen = {{"receipt_status_during_render": rec.get("status"), "stage": rec.get("stage")}}
except Exception as exc:
    seen = {{"receipt_error": repr(exc)}}
seen["pid"] = os.getpid()
if b.get("record_rlimits"):
    import resource
    seen["rlimits"] = {{n: resource.getrlimit(getattr(resource, n)) for n in ("RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_FSIZE")}}
seen["cap_files"] = {{n: open(n, "rb").read().hex() for n in sorted(os.listdir(".")) if n.startswith("cap_")}}
with open(os.path.join(run_dir, "fake_observed.json"), "w") as fh:
    json.dump(seen, fh)
if b.get("touch_input"):
    with open(b["touch_input"], "ab") as fh:
        fh.write(b"tampered")
if b.get("noise"):
    sys.stderr.write("E" * b["noise"] + "TAIL-MARKER")
    sys.stderr.flush()
with open(out, "wb") as fh:
    fh.write(b"FAKE-MP4-BYTES" if mode in ("ok", "nonzero", "sleep", "sigkill", "orphan") else b"")
if mode == "nonzero":
    sys.stderr.write("fatal: simulated failure"); sys.exit(7)
if mode == "sleep":
    time.sleep(60)
if mode == "sigkill":
    os.kill(os.getpid(), signal.SIGKILL)
if mode == "orphan":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
if mode == "noout":
    os.remove(out)
sys.exit(0)
'''


def make_fake_tools(directory: str, **behavior):
    """Create fake ffmpeg/ffprobe in `directory`. Returns (ffmpeg, ffprobe)."""
    behavior.setdefault("probe", good_probe())
    with open(os.path.join(directory, "behavior.json"), "w") as fh:
        json.dump(behavior, fh)
    paths = []
    for name in ("ffmpeg", "ffprobe"):
        p = os.path.join(directory, name)
        with open(p, "w") as fh:
            fh.write(_FAKE.format(python=sys.executable))
        os.chmod(p, 0o755)
        paths.append(p)
    return tuple(paths)


def fake_calls(directory: str) -> list:
    path = os.path.join(directory, "calls.log")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="vc-test-")
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)

    def mkdir(self, *parts):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    def write(self, rel, data=b"x"):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode())
        return p


def rb(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def jl(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def pid_dead(pid, wait=8.0):
    import time
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except OSError:
            return True
        time.sleep(0.05)
    return False
