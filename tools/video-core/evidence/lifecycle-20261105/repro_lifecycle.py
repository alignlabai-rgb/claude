#!/usr/bin/env python3
"""Before/after reproducers for the runner / orchestrator lifecycle findings (F-RUN-1, F-RUN-2, F-INT-1, F-RCPT-1).

    python3 repro_lifecycle.py --tree <tools/video-core under test> --out <result.json>

The same script runs against the pre-fix tree (an export of f42c2db) and the fixed tree; it uses only observables that exist in both (new attributes via getattr).
Bounds: each scenario <= 10 s outer; children are tiny `python -c` programs or the owned FAKE ffmpeg/ffprobe scripts of the test suite (never a real tool, never media);
a pipe flood is <= 2 MB and discarded; no file larger than a few KB is written; no memory beyond a few MB. Linux/POSIX only; nothing here is Windows evidence.
Signals go only to this process (SIGINT it handles) or to process groups the runner owns."""

import argparse
import importlib.util
import json
import os
import signal
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True)
ap.add_argument("--out", required=True)
ns = ap.parse_args()
TREE = os.path.abspath(ns.tree)
sys.path.insert(0, TREE)
sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("lifecycle_support", os.path.join(HERE, "..", "..", "tests", "lifecycle_support.py"))
LS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LS)

from tests.avmux_fakes import av_manifest, default_behavior, make_av_tools, seed_inputs  # noqa: E402
from tests.audio_fixtures import dumps  # noqa: E402
from video_core import runner  # noqa: E402
from video_core.avmux import mux as M  # noqa: E402

PY = sys.executable
results = {"tree": TREE, "python": sys.version.split()[0], "scenarios": {}}


def py(code):
    return [PY, "-c", code]


def g(obj, name, default="<absent in this version>"):
    return getattr(obj, name, default)


# ------------------------------------------------------------------------------------------------ F-RUN-1
rows = []
for i in range(5):
    r = runner.run_bounded(py("import sys; sys.stdout.buffer.write(b'x'*2000000)"), phase="q", timeout_s=6, stdout_limits=(8, 8), max_drain_bytes=1000)
    rows.append({"outcome": r.outcome, "ok": r.ok, "exit_code": r.exit_code, "kill_reason": r.kill_reason, "total_bytes": r.stdout.total,
                 "drain_limit_exceeded": g(r, "drain_limit_exceeded"), "kill_attempted": g(r, "kill_attempted")})
slow = runner.run_bounded(py("import sys, time; sys.stdout.buffer.write(b'x'*2000000); sys.stdout.flush(); time.sleep(1)"), phase="s", timeout_s=6, stdout_limits=(8, 8), max_drain_bytes=1000)
results["scenarios"]["F-RUN-1"] = {
    "trigger": "child writes 2,000,000 bytes to stdout and exits at once; max_drain_bytes=1000 (control: same child lingers 1 s)",
    "quick_exit_x5": rows,
    "lingering_control": {"outcome": slow.outcome, "ok": slow.ok, "kill_reason": slow.kill_reason, "drain_limit_exceeded": g(slow, "drain_limit_exceeded"),
                          "kill_attempted": g(slow, "kill_attempted"), "kill_delivered": g(slow, "kill_delivered")}}

# ------------------------------------------------------------------------------------------------ F-RUN-2
tmp = tempfile.mkdtemp(prefix="vc-repro-")
marker = os.path.join(tmp, "gc.pid")
child = ("import subprocess, sys, time, os\n"
         "subprocess.Popen([sys.executable, '-c', \"import os,time; open(%r,'w').write(str(os.getpid())); time.sleep(5)\"])\n"
         "while not os.path.exists(%r):\n    time.sleep(0.01)\n" % (marker, marker))
outcome = {}
t0 = time.monotonic()
with LS.sigint_default():
    LS.interrupt_when(marker, delay=0.6)
    try:
        r = runner.run_bounded(py(child), phase="t", timeout_s=8, drain_timeout_s=3.0, grace_s=1.0)
        outcome = {"returned": True, "outcome": r.outcome, "kill_reason": r.kill_reason, "exit_code": r.exit_code, "kill_attempted": g(r, "kill_attempted"), "kill_delivered": g(r, "kill_delivered")}
    except KeyboardInterrupt:
        outcome = {"returned": False, "escaped": "KeyboardInterrupt"}
gc = LS.read_pid(marker)
alive = LS.pid_alive(gc)
outcome.update(wall_seconds=round(time.monotonic() - t0, 2), grandchild_alive_after_run_bounded_left=alive)
if alive:
    os.kill(gc, signal.SIGKILL)  # our own test grandchild: clean up after ourselves
results["scenarios"]["F-RUN-2"] = {"trigger": "leader exits 0 leaving a 5 s pipe-holding grandchild; SIGINT 0.6 s later, while run_bounded waits for EOF", **outcome}

# ------------------------------------------------------------------------------------------------ F-INT-1
def avmux_case(label, tool, needle):
    d = tempfile.mkdtemp(prefix="vc-repro-av-")
    assets, out, bin_ = (os.path.join(d, x) for x in ("assets", "out", "bin"))
    for x in (assets, out, bin_):
        os.makedirs(x)
    seed_inputs(assets)
    ff, fp = make_av_tools(bin_, **default_behavior("MOV_PCM_S16LE"))
    mk = os.path.join(d, "marker")
    LS.wrap_tools(bin_, tool=tool, needle=needle, marker=mk, sleep=8)
    row = {"stage": label}
    with LS.sigint_default():
        LS.interrupt_when(mk, delay=0.15)
        try:
            r = M.mux(dumps(av_manifest()), asset_root=assets, output_root=out, output_name="r1", ffmpeg=ff, ffprobe=fp)
            row.update(returned=True, status=r["status"], reasons=[x["reason"][:80] for x in r.get("reasons", [])])
        except KeyboardInterrupt:
            row.update(returned=False, escaped="KeyboardInterrupt")
        except BaseException as exc:
            row.update(returned=False, raised=f"{type(exc).__name__}: {str(exc)[:100]}", issues=[i.code for i in getattr(exc, "issues", [])])
    all_ = LS.launches(bin_)
    cancelled_index = next((i for i, c in enumerate(all_) if c["tool"] == tool and all(any(n in a for a in c["argv"]) for n in needle)), None)
    row["children_launched_after_the_cancelled_one"] = (len(all_) - 1 - cancelled_index) if cancelled_index is not None else None
    rp = os.path.join(out, "r1", "receipt.json")
    row["receipt_on_disk_status"] = json.load(open(rp)).get("status") if os.path.exists(rp) else None
    return row


results["scenarios"]["F-INT-1"] = {
    "trigger": "SIGINT delivered while the owned fake tool sleeps at the named stage (the CLIs translate SIGTERM into the same KeyboardInterrupt)",
    "stages": [avmux_case("ffmpeg -version", "ffmpeg", ["-version"]), avmux_case("preflight ffprobe of the video", "ffprobe", ["clip.mp4"]),
               avmux_case("readback decode", "ffmpeg", ["framemd5"])]}

# ------------------------------------------------------------------------------------------------ F-RCPT-1
d = tempfile.mkdtemp(prefix="vc-repro-rc-")
assets, out, bin_ = (os.path.join(d, x) for x in ("assets", "out", "bin"))
for x in (assets, out, bin_):
    os.makedirs(x)
seed_inputs(assets)
ff, fp = make_av_tools(bin_, **default_behavior("MOV_PCM_S16LE"))
orig = M._readback


def boom(*a, **k):
    raise RuntimeError("synthetic internal error after the mux")


M._readback = boom
try:
    r = M.mux(dumps(av_manifest()), asset_root=assets, output_root=out, output_name="r1", ffmpeg=ff, ffprobe=fp)
finally:
    M._readback = orig
on_disk = sorted(os.listdir(os.path.join(out, "r1")))
results["scenarios"]["F-RCPT-1"] = {
    "trigger": "unexpected exception raised inside the avmux readback (after the mux produced output.mov.partial)",
    "status": r["status"], "internal_error": r.get("internal_error"), "files_on_disk": on_disk, "receipt_outputs": [o["name"] for o in r["outputs"]],
    "input_sha256_after_present": {k: ("sha256_after" in v) for k, v in r["input"].items() if isinstance(v, dict)}, "tmp_cleanup": r.get("tmp_cleanup", "<absent>"),
    "tmp_dir_still_on_disk": "tmp" in on_disk, "finalize_errors": r.get("finalize_errors", "<absent in this version>")}

with open(ns.out, "w") as fh:
    json.dump(results, fh, indent=1, sort_keys=True)
print(json.dumps(results, indent=1, sort_keys=True))
