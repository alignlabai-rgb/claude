#!/usr/bin/env python3
"""Before/after reproducer for `avmux reinspect --original-receipt` input handling. Records observations only (never fails on a defect), so the same
file runs on the unfixed (03ad6928) and the fixed tree:

    python3 repro_reinspect.py OUT.json

Part A drives the REAL public CLI (`python -m video_core.avmux reinspect ... --reinspect`) in an interpreter child. `--ffprobe` points at a MARKER script
that only records that it was started (and exits 9); any start is a failure for a rejection case. Part B uses the repo's owned FAKE ffprobe (tests/avmux_fakes.py,
a Python script, never real media) in-process, to show what happens when a bad receipt gets past validation: the fake runs, a run directory is created, then the
receipt writer fails. Everything is synthetic, bounded, <= 10 s per child, no network, no real ffmpeg/ffprobe.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

MEDIA = b"ORIGINAL-AAC-OUTPUT-BYTES"
SHA = hashlib.sha256(MEDIA).hexdigest()


def good(**over):
    d = {"schema": "video_core.avmux_receipt/1", "status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "route": "MP4_AAC", "plan_sha256": "ab" * 32,
         "outputs": [{"role": "media", "name": "output.mp4", "sha256": SHA, "bytes": len(MEDIA), "partial": False}],
         "observations": {"audio_packets": {"packet_count": 6, "skip_samples": 1024, "discard_padding": None}}}
    d.update(over)
    return d


def raw(d) -> bytes:
    return json.dumps(d).encode()


def nested(n: int, leaf="x"):
    d = leaf
    for _ in range(n):
        d = {"a": d}
    return d


def cases():
    g = good()
    s = json.dumps(g)
    return {
        "[]": b"[]", "null": b"null", '"x"': b'"x"', "1": b"1", "{}": b"{}", "empty file": b"", "UTF-8 BOM": b"\xef\xbb\xbf" + raw(g), "invalid UTF-8": b"\xff\xfe{}",
        "truncated JSON": s.encode()[:-3],
        "outputs: \"x\"": raw(good(outputs="x")), "outputs: [1]": raw(good(outputs=[1])), "outputs: null": raw(good(outputs=None)),
        "observations: \"x\"": raw(good(observations="x")), "observations: [1]": raw(good(observations=[1])),
        "observations.audio_packets: \"x\"": raw(good(observations={"audio_packets": "x"})), "observations.audio_packets: [1]": raw(good(observations={"audio_packets": [1]})),
        "observations.audio_packets: 850 levels deep": json.dumps(good(observations={"audio_packets": nested(850)})).encode(),
        "status: list": raw(good(status=["MUXED"])), "status: 5": raw(good(status=5)), "status missing": raw({k: v for k, v in g.items() if k != "status"}),
        "route: 5": raw(good(route=5)), "plan_sha256: 5": raw(good(plan_sha256=5)),
        "NaN": s.replace('"packet_count": 6', '"packet_count": NaN').encode(), "Infinity": s.replace('"packet_count": 6', '"packet_count": Infinity').encode(),
        "duplicate top-level key": s.replace('"route": "MP4_AAC"', '"route": "MOV_PCM_S16LE", "route": "MP4_AAC"').encode(),
        "duplicate key in outputs[0]": s.replace('"role": "media"', '"role": "plan", "role": "media"').encode(),
        "100000 nested arrays": b"[" * 100000 + b"]" * 100000, "huge integer literal": s.replace('"packet_count": 6', '"packet_count": ' + "9" * 5000).encode(),
        "claims a final media output but status IN_PROGRESS": raw(good(status="IN_PROGRESS")), "claims a final media output but status INTERRUPTED": raw(good(status="INTERRUPTED")),
        "schema of the video render receipt": raw(good(schema="video_core.receipt/1")),
        # controls that must keep working
        "CONTROL genuine-shaped receipt": raw(g), "CONTROL without schema (hand-written legacy)": raw({k: v for k, v in g.items() if k != "schema"}),
        "CONTROL non-ASCII extra field": json.dumps({**g, "note": "日本語の注記"}, ensure_ascii=False).encode(),
    }


def run_cli(work, receipt_bytes):
    media_dir, out, bindir = os.path.join(work, "media"), os.path.join(work, "out"), os.path.join(work, "bin")
    for d in (media_dir, out, bindir):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    marker = os.path.join(work, "LAUNCHED")
    if os.path.exists(marker):
        os.remove(marker)
    probe = os.path.join(bindir, "ffprobe")
    with open(probe, "w") as fh:
        fh.write(f"#!/bin/sh\necho started >> '{marker}'\nexit 9\n")
    os.chmod(probe, 0o755)
    with open(os.path.join(media_dir, "output.mp4"), "wb") as fh:
        fh.write(MEDIA)
    rpath = os.path.join(media_dir, "original.receipt.json")
    with open(rpath, "wb") as fh:
        fh.write(receipt_bytes)
    env = {"PATH": bindir + os.pathsep + "/usr/bin:/bin", "PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, "-m", "video_core.avmux", "reinspect", os.path.join(media_dir, "output.mp4"), "--expect-sha256", SHA, "--original-receipt", rpath,
            "--output-root", out, "--output-name", "fix1", "--ffprobe", probe, "--reinspect"]
    try:
        p = subprocess.run(argv, capture_output=True, env=env, timeout=10)
        rc, so, se, to = p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired:
        rc, so, se, to = None, b"", b"", True
    res = {"exit": rc, "timed_out": to, "traceback_on_stderr": b"Traceback" in se, "stderr_last_line": (se.decode("utf-8", "replace").strip().splitlines() or [""])[-1][:140],
           "ffprobe_started": os.path.exists(marker), "output_entries": sorted(os.listdir(out)), "media_and_receipt_unchanged": open(os.path.join(media_dir, "output.mp4"), "rb").read() == MEDIA
           and open(rpath, "rb").read() == receipt_bytes}
    try:
        doc = json.loads(so)
        res["stdout_json"] = True
        res["ok"] = doc.get("ok")
        if isinstance(doc.get("error"), dict):
            res["error_kind"], res["issue_codes"] = doc["error"].get("kind"), [i["code"] for i in doc["error"].get("issues", [])]
        else:
            res["status"] = doc.get("status")
            oc = doc.get("original_receipt")
            if isinstance(oc, dict):
                res["original_receipt_keys"] = sorted(oc)
                res["original_status"] = oc.get("status")
    except ValueError:
        res["stdout_json"] = False
    return res


def fake_tool_leaks(work):
    """Part B: bad receipt shapes that the unfixed code lets past parsing. Owned fake ffprobe; in-process library call."""
    from tests.avmux_fakes import calls, default_behavior, make_av_tools, out_probe, packets_json
    from video_core.avmux.reinspect import reinspect_audio_packets
    from video_core.errors import VideoCoreError

    out = {}
    for label, rec in (("observations.audio_packets: \"x\"", good(observations={"audio_packets": "x"})),
                       ("observations.audio_packets: 850 levels deep", good(observations={"audio_packets": nested(850)})),
                       ("CONTROL genuine-shaped receipt", good())):
        shutil.rmtree(os.path.join(work, "b"), ignore_errors=True)
        b, o, m = (os.path.join(work, "b", n) for n in ("bin", "out", "media"))
        for d in (b, o, m):
            os.makedirs(d)
        with open(os.path.join(m, "output.mp4"), "wb") as fh:
            fh.write(MEDIA)
        ff, fp = make_av_tools(b, **default_behavior("MP4_AAC", packets=packets_json(skip=1024, padding=None), probe_out=out_probe("MP4_AAC")))
        rp = os.path.join(m, "r.json")
        with open(rp, "w") as fh:
            json.dump(rec, fh)
        try:
            r = reinspect_audio_packets(os.path.join(m, "output.mp4"), expected_sha256=SHA, output_root=o, output_name="fix1", ffprobe=fp, original_receipt_path=rp)
            res = {"result": "OK", "status": r["status"]}
        except VideoCoreError as exc:
            res = {"result": "REJECTED", "codes": [i.code for i in exc.issues]}
        except BaseException as exc:  # noqa: BLE001 - recorded
            res = {"result": "LEAK", "exception": type(exc).__name__}
        res["fake_ffprobe_invocations"] = len(calls(b))
        res["run_directory_entries"] = sorted(os.listdir(os.path.join(o, "fix1"))) if os.path.isdir(os.path.join(o, "fix1")) else None
        out[label] = res
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    work = tempfile.mkdtemp(prefix="vc-reinspect-")
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, cwd=ROOT, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "video_core", "tests"], capture_output=True, cwd=ROOT, text=True).stdout.strip())
        res = {"schema": "video_core.reinspect_repro/1", "source_head": git, "production_or_tests_dirty": dirty, "python": platform.python_version(), "platform": platform.system(),
               "A_public_cli": {k: run_cli(work, v) for k, v in cases().items()}, "B_bad_receipts_past_parsing_with_owned_fake_ffprobe": fake_tool_leaks(work)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    text = json.dumps(res, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
