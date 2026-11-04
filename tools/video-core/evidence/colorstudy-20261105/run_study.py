#!/usr/bin/env python3
"""Bounded driver for the 2026-11-05 colour-conversion study (stdlib only; launches only the system ffmpeg/ffprobe).

Hard rules enforced here, not just promised:
  * at most MAX_EXPERIMENTS (12) numbered experiments exist in total; every experiment is exactly ONE ffmpeg process that
    emits ONE frame per output (plus logged read-back processes: ffprobe and one ffmpeg decode of the mp4);
  * an experiment id can be started once.  A `records/Exx.started.json` marker is written BEFORE the process launches, so a crash
    still counts as the attempt; re-running the same id is refused.  A harness bug is answered by a NEW numbered experiment;
  * every process: <= 10 s wall clock (killed otherwise), CPU affinity = 2 CPUs, RLIMIT_CPU, RLIMIT_FSIZE (4 MiB), RLIMIT_AS (6 GiB),
    ffmpeg -threads 2 / -filter_complex_threads 1 / x264 threads=2, no stdin, no network option, minimal environment;
  * output geometry <= 256x256.
Usage:  run_study.py probes            # non-frame metadata probes (version, local filter help) -> docs/
        run_study.py list              # print the sealed experiment table
        run_study.py run E01 [E02 ...] # run the not-yet-started experiments, in the given order
        run_study.py status
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_lib as L  # noqa: E402

MAX_EXPERIMENTS = 12
TIMEOUT_S = 10
LIMITS = {"wall_seconds": TIMEOUT_S, "cpus": [0, 1], "rlimit_cpu_s": 10, "rlimit_fsize_bytes": 4 * 2 ** 20,
          "rlimit_as_bytes": 6 * 2 ** 30, "ffmpeg_threads": 2, "filter_complex_threads": 1, "max_dim": 256}
RAW, REC, DOCS = (os.path.join(HERE, d) for d in ("raw", "records", "docs"))

TAGS_709 = {"colorspace": "bt709", "color_primaries": "bt709", "color_trc": "bt709", "color_range": "tv"}
TAGS_170M = {"colorspace": "smpte170m", "color_primaries": "smpte170m", "color_trc": "smpte170m", "color_range": "tv"}

# ---- filter-chain variants.  `pre` is inserted after setpts and before the scaler (where the author proposed format=gbrp).
SCALE_709 = "scale=out_color_matrix=bt709:out_range=tv"
VARIANTS = {
    "baseline": {"pre": "", "scaler": SCALE_709},
    "gbrp": {"pre": "format=gbrp,", "scaler": SCALE_709},
    "rgb24": {"pre": "format=rgb24,", "scaler": SCALE_709},
    "gbrp_accurate": {"pre": "format=gbrp,", "scaler": SCALE_709 + ":flags=accurate_rnd+full_chroma_int"},
    "gbrp_zscale": {"pre": "format=gbrp,", "scaler": "zscale=matrixin=gbr:rangein=full:matrix=709:range=limited"},
    # amendment 1 (E12): diagnostic tap, the baseline chain WITHOUT the scale step, to separate what the colour source draws from what scale does
    "noscale": {"pre": "", "scaler": None},
}


def color_clip_chain(hex6: str, w: int, h: int, variant: str, fps="30/1", n_frames=1) -> str:
    """Exactly plan._clip_filter's colour branch (minus the output label), with a variant hook.  variant=baseline, n_frames=90,
    640x360 reproduces the committed evidence/plan.canonical.json text byte for byte (checked by verify_scope.py)."""
    v = VARIANTS[variant]
    scaler = f"{v['scaler']}," if v["scaler"] else ""
    return (f"color=c=0x{hex6}:s={w}x{h}:r={fps},trim=end_frame={n_frames},setpts=PTS-STARTPTS,{v['pre']}{scaler}"
            f"setsar=1,format=yuv420p")


def file_clip_chain(variant: str, w: int, h: int, fps="30/1", n_frames=1, input_index=0) -> str:
    """plan._clip_filter's file branch with the same hook (image/video inputs)."""
    v = VARIANTS[variant]
    scaler = v["scaler"].replace("scale=", f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:", 1)
    return (f"[{input_index}:v:0]fps=fps={fps},trim=end_frame={n_frames},setpts=PTS-STARTPTS,{v['pre']}{scaler},"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p")


def _geometry_chart():
    P = L.patch_table()
    return {"w": L.W, "h": L.H, "pw": L.PATCH, "ph": L.PATCH, "cols": L.COLS, "rows": L.ROWS, "patches": P}


def _geometry_flat():
    return {"w": 256, "h": 144, "pw": 256, "ph": 144, "cols": 1, "rows": 1,
            "patches": [{"index": 0, "name": "reported_30506a", "rgb": [0x30, 0x50, 0x6A], "hex": "#30506a", "x": 0, "y": 0, "w": 256, "h": 144}]}


def graph_chart_color(variant):
    P = L.patch_table()
    parts = [color_clip_chain(p["hex"][1:], L.PATCH, L.PATCH, variant) + f"[p{p['index']}]" for p in P]
    layout = "|".join(f"{p['x']}_{p['y']}" for p in P)
    parts.append("".join(f"[p{p['index']}]" for p in P) + f"xstack=inputs={len(P)}:layout={layout}:fill=black[vcat]")
    parts.append("[vcat]split=2[raw][enc]")
    return ";".join(parts), []


def graph_chart_png(variant):
    chain = file_clip_chain(variant, L.W, L.H)
    inp = ["-protocol_whitelist", "file", "-f", "image2", "-pattern_type", "none", "-loop", "1", "-framerate", "30/1",
           "-i", "fixtures/chart.png"]
    return chain + ",split=2[raw][enc]", inp


def graph_flat(variant):
    return color_clip_chain("30506a", 256, 144, variant) + ",split=2[raw][enc]", []


def graph_yuv_counterexample(_variant):
    tag = "setparams=range=tv:colorspace=bt709"
    inp = ["-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", f"{L.W}x{L.H}", "-framerate", "30/1",
           "-i", "fixtures/chart_bt709_ideal.yuv420p"]
    g = (f"[0:v:0]fps=fps=30/1,trim=end_frame=1,setpts=PTS-STARTPTS,{tag},split=2[a][b];"
         f"[a]{SCALE_709},setsar=1,format=yuv420p[rawbase];"
         f"[b]format=gbrp,{SCALE_709},setsar=1,format=yuv420p[rawgbrp]")
    return g, inp


EXPERIMENTS = {
    "E01": dict(title="chart of 32 colour sources, BASELINE chain (as committed)", kind="chart_color", variant="baseline", tags="709", geom="chart"),
    "E02": dict(title="chart of 32 colour sources, +format=gbrp before scale (proposed)", kind="chart_color", variant="gbrp", tags="709", geom="chart"),
    "E03": dict(title="chart of 32 colour sources, +format=rgb24 before scale (alternative intermediate)", kind="chart_color", variant="rgb24", tags="709", geom="chart"),
    "E04": dict(title="chart of 32 colour sources, gbrp + scale flags accurate_rnd+full_chroma_int", kind="chart_color", variant="gbrp_accurate", tags="709", geom="chart"),
    "E05": dict(title="chart of 32 colour sources, gbrp + zscale (libzimg) reference engine", kind="chart_color", variant="gbrp_zscale", tags="709", geom="chart"),
    "E06": dict(title="PNG rgb24 chart through the FILE branch, BASELINE chain", kind="chart_png", variant="baseline", tags="709", geom="chart"),
    "E07": dict(title="PNG rgb24 chart through the FILE branch, +format=gbrp", kind="chart_png", variant="gbrp", tags="709", geom="chart"),
    "E08": dict(title="TAG-ONLY: E01 chain unchanged, mp4 tagged smpte170m/bt470bg-class instead of bt709", kind="chart_color", variant="baseline", tags="170m", geom="chart"),
    "E09": dict(title="flat 256x144 #30506a, BASELINE chain (reported colour, single full frame)", kind="flat", variant="baseline", tags="709", geom="flat"),
    "E10": dict(title="flat 256x144 #30506a, +format=gbrp", kind="flat", variant="gbrp", tags="709", geom="flat"),
    "E11": dict(title="COUNTEREXAMPLE: exact BT.709 yuv420p *file* source through baseline vs. gbrp-misapplied", kind="yuv_counterexample", variant="both", tags="none", geom="chart"),
    # E12 was reserved at pre-registration.  Amendment 1 (committed before E12 ran, after E01-E11 were recorded): used for a diagnostic tap.
    "E12": dict(title="DIAGNOSTIC TAP (amendment 1): E01 chain with the scale step removed (colour source -> setsar -> format=yuv420p)", kind="chart_color", variant="noscale", tags="709", geom="chart"),
}
BUILDERS = {"chart_color": graph_chart_color, "chart_png": graph_chart_png, "flat": graph_flat, "yuv_counterexample": graph_yuv_counterexample}
GEOM = {"chart": _geometry_chart, "flat": _geometry_flat}


# ---------------------------------------------------------------- process plumbing
def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 16), b""):
            h.update(blk)
    return h.hexdigest()


def _preexec():
    os.sched_setaffinity(0, set(LIMITS["cpus"]))
    resource.setrlimit(resource.RLIMIT_CPU, (LIMITS["rlimit_cpu_s"], LIMITS["rlimit_cpu_s"] + 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMITS["rlimit_fsize_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_AS, (LIMITS["rlimit_as_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_bounded(argv, stderr_path, stdout_path=None):
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "OMP_NUM_THREADS": "1"}
    t0, started = time.monotonic(), utc_now()
    out = open(stdout_path, "wb") if stdout_path else subprocess.DEVNULL
    with open(stderr_path, "wb") as err:
        p = subprocess.Popen(argv, cwd=HERE, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                             preexec_fn=_preexec, start_new_session=True)
        timed_out = False
        try:
            p.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, 9)
            p.wait()
    if stdout_path:
        out.close()
    rc = p.returncode
    return {"argv": argv, "cwd": "<study directory>", "started_utc": started, "wall_seconds": round(time.monotonic() - t0, 3),
            "returncode": rc if rc >= 0 else None, "signal": -rc if rc < 0 else None, "timed_out": timed_out,
            "stderr_file": os.path.relpath(stderr_path, HERE), "stderr_bytes": os.path.getsize(stderr_path),
            "stderr_sha256": sha256_file(stderr_path),
            "stdout_file": os.path.relpath(stdout_path, HERE) if stdout_path else None}


def file_fact(path):
    return {"path": os.path.relpath(path, HERE), "bytes": os.path.getsize(path), "sha256": sha256_file(path)} if os.path.exists(path) else None


def tool_facts():
    ff = shutil.which("ffmpeg", path="/usr/bin:/bin")
    fp = shutil.which("ffprobe", path="/usr/bin:/bin")
    ver = lambda exe: subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=TIMEOUT_S).stdout.splitlines()[0]
    return {"ffmpeg": ff, "ffmpeg_realpath": os.path.realpath(ff), "ffmpeg_version": ver(ff), "ffmpeg_sha256": sha256_file(os.path.realpath(ff)),
            "ffprobe": fp, "ffprobe_version": ver(fp)}


def scale_lines(stderr_path, cap=12):
    """unique negotiated-format lines of scale/zscale instances (instance addresses stripped)"""
    seen, out = set(), []
    with open(stderr_path, "r", errors="replace") as fh:
        for line in fh:
            if re.search(r"Parsed_(scale|zscale|format)_\d+", line) and ("fmt:" in line or "->" in line):
                key = re.sub(r"\[Parsed_(\w+?)_\d+ @ 0x[0-9a-f]+\]", r"[\1]", line.strip())
                key = re.sub(r"flags:0x[0-9a-f]+", "", key)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out[:cap], len(seen)


def build_main_argv(eid, spec):
    graph, inputs = BUILDERS[spec["kind"]](spec["variant"])
    argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "verbose", "-nostats", "-n", "-filter_complex_threads", str(LIMITS["filter_complex_threads"])]
    argv += inputs + ["-filter_complex", graph]
    outs = {}
    if spec["kind"] == "yuv_counterexample":
        for lab, name in (("rawbase", "raw_baseline"), ("rawgbrp", "raw_gbrp")):
            path = f"raw/{eid}.{name}.yuv"
            argv += ["-map", f"[{lab}]", "-an", "-sn", "-dn", "-frames:v", "1", "-threads", "2", "-f", "rawvideo", "-pix_fmt", "yuv420p", path]
            outs[name] = path
        return argv, graph, outs
    tags = TAGS_709 if spec["tags"] == "709" else TAGS_170M
    outs["raw"], outs["mp4"] = f"raw/{eid}.yuv", f"raw/{eid}.mp4"
    argv += ["-map", "[raw]", "-an", "-sn", "-dn", "-frames:v", "1", "-threads", "2", "-f", "rawvideo", "-pix_fmt", "yuv420p", outs["raw"]]
    argv += ["-map", "[enc]", "-an", "-sn", "-dn", "-frames:v", "1", "-threads", "2", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-x264-params", "threads=2:lookahead-threads=1", "-pix_fmt", "yuv420p",
             "-colorspace", tags["colorspace"], "-color_primaries", tags["color_primaries"], "-color_trc", tags["color_trc"], "-color_range", tags["color_range"],
             "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart", "-f", "mp4", outs["mp4"]]
    return argv, graph, outs


def run_experiment(eid):
    spec = EXPERIMENTS[eid]
    if spec["kind"] == "reserved":
        sys.exit(f"{eid} is a reserved slot: it can only be used by editing this table in a separate, committed, labelled change")
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(REC, exist_ok=True)
    started_marker = os.path.join(REC, f"{eid}.started.json")
    if os.path.exists(started_marker) or os.path.exists(os.path.join(REC, f"{eid}.json")):
        sys.exit(f"{eid} was already started; it is not repeated (a harness bug gets a NEW numbered experiment)")
    used = {f[:3] for f in os.listdir(REC) if re.match(r"E\d\d\.", f)}
    if len(used) >= MAX_EXPERIMENTS:
        sys.exit("experiment budget (12) exhausted")
    geom = GEOM[spec["geom"]]()
    assert geom["w"] <= 256 and geom["h"] <= 256
    argv, graph, outs = build_main_argv(eid, spec)
    tools = tool_facts()
    argv[0] = tools["ffmpeg"]
    with open(started_marker, "w") as fh:
        json.dump({"id": eid, "started_utc": utc_now(), "title": spec["title"], "argv": argv}, fh, indent=1)
        fh.write("\n")
    main = run_bounded(argv, os.path.join(RAW, f"{eid}.ffmpeg.log"))
    rec = {"id": eid, "title": spec["title"], "spec": {k: v for k, v in spec.items()}, "tools": tools, "limits": LIMITS,
           "geometry": {k: v for k, v in geom.items() if k != "patches"}, "filter_graph": graph, "main": main,
           "outputs": {k: file_fact(os.path.join(HERE, v)) for k, v in outs.items()}, "readbacks": []}
    lines, n = scale_lines(os.path.join(RAW, f"{eid}.ffmpeg.log"))
    rec["negotiated_scaler_lines"] = {"unique_count": n, "first": lines}
    if main["returncode"] == 0 and "mp4" in outs and os.path.exists(os.path.join(HERE, outs["mp4"])):
        probe_json = f"raw/{eid}.ffprobe.json"
        pr = run_bounded([tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", outs["mp4"]],
                         os.path.join(RAW, f"{eid}.ffprobe.err"), os.path.join(HERE, probe_json))
        pr["name"], pr["output"] = "ffprobe -show_streams -show_format", file_fact(os.path.join(HERE, probe_json))
        rec["readbacks"].append(pr)
        rgb = f"raw/{eid}.mp4.rgb24"
        dc = run_bounded([tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "verbose", "-nostats", "-n", "-threads", "2",
                          "-i", outs["mp4"], "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", rgb],
                         os.path.join(RAW, f"{eid}.decode.log"))
        dc["name"], dc["output"] = "ffmpeg decode first frame -> rgb24 (default swscale, stream tags honoured)", file_fact(os.path.join(HERE, rgb))
        dl, dn = scale_lines(os.path.join(RAW, f"{eid}.decode.log"))
        dc["negotiated_scaler_lines"] = {"unique_count": dn, "first": dl}
        rec["readbacks"].append(dc)
    rec["finished_utc"] = utc_now()
    with open(os.path.join(REC, f"{eid}.json"), "w") as fh:
        json.dump(rec, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print(f"{eid}: rc={main['returncode']} signal={main['signal']} timeout={main['timed_out']} wall={main['wall_seconds']}s outputs={ {k: (v or {}).get('sha256', '')[:12] for k, v in rec['outputs'].items()} }")
    return rec


def probes():
    """Non-frame metadata probes: tool versions and the installed binary's own documentation for the filters involved."""
    os.makedirs(DOCS, exist_ok=True)
    tools = tool_facts()
    out = []
    jobs = [("P01", [tools["ffmpeg"], "-hide_banner", "-version"], "ffmpeg-version.txt"),
            ("P02", [tools["ffprobe"], "-hide_banner", "-version"], "ffprobe-version.txt")]
    for i, f in enumerate(("color", "scale", "zscale", "xstack", "setparams", "format"), start=3):
        jobs.append((f"P{i:02d}", [tools["ffmpeg"], "-hide_banner", "-h", f"filter={f}"], f"local-help-filter-{f}.txt"))
    for pid, argv, name in jobs:
        res = run_bounded(argv, os.path.join(DOCS, name + ".stderr"), os.path.join(DOCS, name))
        if os.path.getsize(os.path.join(DOCS, name + ".stderr")) == 0:
            os.remove(os.path.join(DOCS, name + ".stderr"))
            res["stderr_file"] = None
        res["id"], res["output"] = pid, file_fact(os.path.join(DOCS, name))
        out.append(res)
    with open(os.path.join(DOCS, "probes.json"), "w") as fh:
        json.dump({"note": "non-frame metadata probes (no media decoded or encoded)", "tools": tools, "probes": out}, fh, indent=1)
        fh.write("\n")
    print("probes:", [(p["id"], p["returncode"]) for p in out])


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "probes":
        probes()
    elif cmd == "list":
        for k, v in EXPERIMENTS.items():
            print(k, v["kind"], v["variant"], "|", v["title"])
    elif cmd == "status":
        os.makedirs(REC, exist_ok=True)
        print(sorted(os.listdir(REC)))
    elif cmd == "run":
        for eid in argv[2:]:
            if eid not in EXPERIMENTS:
                sys.exit(f"unknown experiment {eid}")
            run_experiment(eid)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
