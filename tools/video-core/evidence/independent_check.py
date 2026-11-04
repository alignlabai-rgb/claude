#!/usr/bin/env python3
"""Independent re-measurement of an MP4 -- does NOT import video_core.

Runs its own ffprobe/ffmpeg commands (structured argv, no shell) and prints JSON:
stream properties, decoded frame count, per-frame PTS regularity, and the colour of
one corner pixel in a few frames that carry no caption. This is objective measurement
of a synthetic case. It is NOT an assessment of visual, audio or creative quality.

usage: independent_check.py MEDIA --ffprobe PATH --ffmpeg PATH \
           --width 640 --height 360 --fps 30/1 --frames 180 --sample N:#rrggbb ...
"""
import argparse
import json
import subprocess
import sys
from fractions import Fraction


def run(argv, binary=False):
    p = subprocess.run(argv, capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout if binary else p.stdout.decode("utf-8", "replace")), p.stderr.decode("utf-8", "replace")[:500]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("--ffprobe", required=True)
    ap.add_argument("--ffmpeg", required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--fps", required=True)
    ap.add_argument("--frames", type=int, required=True)
    ap.add_argument("--sample", action="append", default=[], help="FRAME:#rrggbb expected colour of pixel (8,8) in that frame")
    ap.add_argument("--tolerance", type=int, default=6)
    a = ap.parse_args()
    fps = Fraction(a.fps)
    out = {"media": a.media.rsplit("/", 1)[-1], "checks": []}

    def check(name, expected, observed, ok):
        out["checks"].append({"name": name, "expected": expected, "observed": observed, "ok": bool(ok)})

    rc, so, se = run([a.ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                      "stream=codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate,time_base,nb_read_frames,nb_frames,duration:format=nb_streams,duration,format_name",
                      "-of", "json", a.media])
    out["ffprobe_stream_exit"] = rc
    info = json.loads(so)
    s = info["streams"][0]
    check("width", a.width, s["width"], s["width"] == a.width)
    check("height", a.height, s["height"], s["height"] == a.height)
    check("r_frame_rate", str(fps), s["r_frame_rate"], Fraction(s["r_frame_rate"]) == fps)
    check("avg_frame_rate", str(fps), s["avg_frame_rate"], Fraction(s["avg_frame_rate"]) == fps)
    check("nb_read_frames (decoded)", a.frames, s["nb_read_frames"], int(s["nb_read_frames"]) == a.frames)
    check("nb_streams", 1, info["format"]["nb_streams"], info["format"]["nb_streams"] == 1)
    expected_dur = Fraction(a.frames) / fps
    check("format duration within 1 frame", f"{float(expected_dur):.6f}", info["format"]["duration"], abs(Fraction(info["format"]["duration"]) - expected_dur) <= 1 / fps)

    # per-frame presentation timestamps: constant step == one frame, no gaps or repeats
    rc, so, se = run([a.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts", "-of", "csv=p=0", a.media])
    # csv rows can carry trailing commas (e.g. "0,"); take the first field of every non-empty row
    pts = [int(row.split(",")[0]) for row in so.splitlines() if row.strip() and row.split(",")[0].lstrip("-").isdigit()]
    tb = Fraction(s["time_base"])
    step = (1 / fps) / tb
    steps = {Fraction(b - a_) for a_, b in zip(pts, pts[1:])}
    check("frame pts count", a.frames, len(pts), len(pts) == a.frames)
    check("pts step is constant one frame", str(step), sorted(str(x) for x in steps), steps == {step})
    check("first pts is 0", 0, pts[0] if pts else None, bool(pts) and pts[0] == 0)

    samples = []
    for spec in a.sample:
        n, color = spec.split(":", 1)
        want = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        rc, raw, se = run([a.ffmpeg, "-v", "error", "-nostdin", "-i", a.media, "-vf", f"select=eq(n\\,{int(n)})", "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], binary=True)
        off = (8 * a.width + 8) * 3
        got = tuple(raw[off:off + 3]) if len(raw) >= off + 3 else None
        ok = got is not None and all(abs(g - w) <= a.tolerance for g, w in zip(got, want))
        samples.append({"frame": int(n), "expected_rgb": want, "observed_rgb": got, "tolerance": a.tolerance, "ok": ok})
    out["pixel_samples"] = samples
    out["all_ok"] = all(c["ok"] for c in out["checks"]) and all(x["ok"] for x in samples)
    out["note"] = "Objective metadata/pixel measurement of a synthetic colour case only; no visual, audio or creative quality judgement."
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0 if out["all_ok"] else 4


if __name__ == "__main__":
    sys.exit(main())
