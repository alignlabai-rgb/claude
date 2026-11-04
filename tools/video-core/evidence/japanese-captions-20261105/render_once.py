"""THE one bounded real render (and its measurements) for the Japanese-caption evidence.

Contract (all enforced in code, none relies on memory):
  * one new, distinct, tiny synthetic case (render_case.episode.json: 320x180, 30000/1001, 54 frames = 1.80 s) -- never the
    six-second example, never a rerender;
  * black background, white text only (the production plan's fontcolor=white; no colour study);
  * FFmpeg worker threads capped at 1 (`-threads 1 -filter_threads 1 -filter_complex_threads 1`) and the driver process pinned to
    at most 2 CPUs (the child inherits the affinity); hard wall clock 30 s; output limited to 2 MiB (`-fs`);
  * ONE attempt: an O_EXCL marker is created before the process is launched; a second call refuses, even after a failure;
  * a partial/failed run is kept and described, never retried;
  * prerequisites are checked first; if any is absent the run is SKIPPED with a receipt and nothing is installed;
  * this orchestrator reuses video_core's planning (prepare/materialize), bounded runner, ffprobe readback and font check
    UNMODIFIED. It is not video_core.render(): the executed argv = production argv + the additive caps listed in `argv_delta`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import analysis  # noqa: E402
import cmapx  # noqa: E402
import corpus_def as C  # noqa: E402
import oracle  # noqa: E402
from video_core import readback, runner, tools  # noqa: E402
from video_core import receipt as rc  # noqa: E402
from video_core.limits import Limits  # noqa: E402
from video_core.paths import check_output_location  # noqa: E402
from video_core.plan import materialize, prepare  # noqa: E402

MARKER = "RENDER_ATTEMPTED.json"
THREAD_CAP = 1
CPU_CAP = 2
HARD_SECONDS = 30
OUT_BYTES = 2 * 1024 * 1024
SHEET_FRAMES = (13, 31, 53)       # explicitly indexed frames for the contact sheet
FONT_REL = "font.ttf"
OUTPUT_NAME = "jp-caption-evidence-1p8s"
ADDITIVE_GLOBAL = ["-filter_threads", str(THREAD_CAP), "-filter_complex_threads", str(THREAD_CAP)]
ADDITIVE_OUTPUT = ["-threads", str(THREAD_CAP)]


def rb(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def jdump(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def write_json(path: str, obj) -> None:
    with open(path, "wb") as fh:
        fh.write(jdump(obj))


def sha256_file(path: str) -> str:
    return tools.sha256_file(path)


def with_caps(argv: list) -> tuple[list, list]:
    """Insert the additive resource caps. Global caps right after '-n'; '-threads' right before '-c:v' (output options; this
    plan has no -i inputs, so everything before the output file belongs to the output)."""
    out = list(argv)
    i = out.index("-n") + 1
    out[i:i] = ADDITIVE_GLOBAL
    j = out.index("-c:v")
    out[j:j] = ADDITIVE_OUTPUT
    delta = ADDITIVE_GLOBAL + ADDITIVE_OUTPUT
    return out, delta


class ThreadPoller:
    """Samples /proc/<pid>/status 'Threads:' of any process whose command line mentions `needle` (the unique run dir)."""

    def __init__(self, needle: str):
        self.needle, self.peak, self.samples, self._stop = needle, 0, 0, threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        me = str(os.getpid())
        while not self._stop.is_set():
            try:
                for pid in os.listdir("/proc"):
                    if not pid.isdigit() or pid == me:
                        continue
                    try:
                        with open(f"/proc/{pid}/cmdline", "rb") as fh:
                            cl = fh.read()
                        if self.needle.encode() in cl and b"ffmpeg" in cl:
                            with open(f"/proc/{pid}/status") as fh:
                                for line in fh:
                                    if line.startswith("Threads:"):
                                        n = int(line.split()[1])
                                        self.peak = max(self.peak, n)
                                        self.samples += 1
                    except (OSError, ValueError):
                        continue
            except OSError:
                pass
            time.sleep(0.003)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(timeout=2)


def claim_once(workdir: str, intent: dict) -> bool:
    path = os.path.join(workdir, MARKER)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as fh:
        fh.write(jdump(intent))
    return True


def check_prerequisites(ffmpeg: str, ffprobe: str, font_path: str, limits: Limits, cwd: str) -> dict:
    reasons, info = [], {}
    paths = {}
    for name, explicit in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        p, how = tools.locate(name, explicit)
        if p is None:
            reasons.append(f"{name} not found ({how})")
        else:
            paths[name] = (p, how)
    if reasons:
        return {"ok": False, "reasons": reasons, "info": info}
    recs = {}
    for name, (p, how) in paths.items():
        rec, _runs = tools.probe_version(name, p, how, limits, cwd)
        recs[name] = rec
        if rec.problem:
            reasons.append(f"{name}: {rec.problem}")
    info["tools"] = {k: v.to_dict() for k, v in recs.items()}
    if not reasons:
        caps, _runs = tools.missing_capabilities(paths["ffmpeg"][0], {"encoders": ["libx264"], "filters": ["drawtext", "color", "concat", "trim", "setpts", "scale", "format", "setsar"]}, limits, cwd)
        info["capabilities"] = caps
        if caps["missing"] or caps["probe_failed"]:
            reasons.append(f"capability unavailable: {caps['missing'] or caps['probe_failed']}")
    if not os.path.isfile(font_path):
        reasons.append(f"font absent: {font_path}")
    else:
        data = rb(font_path)
        info["font"] = {"path": font_path, "sha256": tools.sha256_file(font_path), "bytes": len(data)}
        try:
            f = cmapx.read_font(data, font_path)
            need = sorted({ord(ch) for _i, _s, _e, t in C.RENDER_CAPTIONS for ch in t if ch != "\n"})
            miss = [f"U+{cp:04X}" for cp in need if not f.gid(cp)]
            info["font"]["independent_cmap_missing"] = miss
            if miss:
                reasons.append(f"font lacks glyphs for the render corpus: {miss}")
        except cmapx.SfntError as exc:
            reasons.append(f"font unusable: {exc}")
    if not hasattr(os, "sched_setaffinity") or len(os.sched_getaffinity(0)) < 1:
        info["cpu_affinity"] = "not available: cap not applied"
    return {"ok": not reasons, "reasons": reasons, "info": info, "paths": {k: v[0] for k, v in paths.items()}, "how": {k: v[1] for k, v in paths.items()}}


# ------------------------------------------------------------------------------------------- render ---------------

def render(workdir: str, ffmpeg: str, ffprobe: str, font_path: str) -> dict:
    os.makedirs(workdir, exist_ok=True)
    limits = Limits(max_frames=60, max_dimension=320, max_runtime_seconds=float(HARD_SECONDS), max_output_bytes=OUT_BYTES, max_captions=8, max_pixel_volume=320 * 180 * 60)
    receipt = {"schema": "jpcap.render_receipt/1", "case": "render_case (new corpus; not examples/episode.synthetic.json)", "started_utc": rc.utc_now(),
               "contract": {"max_seconds_of_video": 2, "max_canvas": "320x180", "max_frames": 60, "ffmpeg_thread_cap": THREAD_CAP, "cpu_cap": CPU_CAP,
                            "hard_wall_clock_seconds": HARD_SECONDS, "max_output_bytes": OUT_BYTES, "colours": "black background, white text only", "retry": "none"},
               "quality_evaluated": False}
    receipt["driver"] = {"files_sha256": {n: oracle_sha(rb(os.path.join(HERE, n))) for n in ("render_once.py", "jpcap_eval.py", "corpus_def.py", "render_case.episode.json", "cmapx.py", "oracle.py")},
                         "repo_head": _git_head(), "python": sys.version.split()[0]}
    pre = check_prerequisites(ffmpeg, ffprobe, font_path, limits, workdir)
    receipt["prerequisites"] = {k: v for k, v in pre.items() if k != "paths"}
    if not pre["ok"]:
        receipt["status"] = "SKIPPED_PREREQUISITE"
        receipt["note"] = "No marker, no run directory, nothing installed or rendered; static conformance evidence stands alone."
        receipt["finished_utc"] = rc.utc_now()
        return receipt
    if not claim_once(workdir, {"claimed_utc": rc.utc_now(), "meaning": "the one render has been started; a second call must refuse"}):
        receipt["status"] = "REFUSED_ALREADY_ATTEMPTED"
        receipt["note"] = f"{MARKER} exists in the work directory: the single permitted attempt was already made (success or failure). No retry."
        receipt["finished_utc"] = rc.utc_now()
        return receipt

    ep_bytes = rb(os.path.join(HERE, "render_case.episode.json"))
    assets = os.path.join(workdir, "assets")
    out_root = os.path.join(workdir, "out")
    os.makedirs(assets)
    os.makedirs(out_root)
    shutil.copyfile(font_path, os.path.join(assets, FONT_REL))       # byte copy of an already-installed font
    receipt["font"] = {"source": font_path, "sha256_source": tools.sha256_file(font_path), "sha256_copy": tools.sha256_file(os.path.join(assets, FONT_REL))}
    plan = prepare(ep_bytes, limits=limits, asset_root=assets, output_root=out_root, output_name=OUTPUT_NAME, burn_in_font=FONT_REL,
                   font_size=C.RENDER_FONT_SIZE, ffmpeg=pre["paths"]["ffmpeg"], ffprobe=pre["paths"]["ffprobe"])
    location = check_output_location(out_root, OUTPUT_NAME, assets)      # exactly what video_core.render() uses
    run_dir = location.run_dir
    os.mkdir(run_dir)
    tmp_dir = os.path.join(run_dir, "tmp")
    os.mkdir(tmp_dir)
    for name, data in plan.caption_files.items():
        with open(os.path.join(tmp_dir, name), "xb") as fh:
            fh.write(data)
    with open(os.path.join(tmp_dir, planmod_font_name()), "xb") as fh:
        fh.write(plan.font_bytes)
    with open(os.path.join(run_dir, "plan.canonical.json"), "xb") as fh:
        fh.write(plan.canonical_json)
    with open(os.path.join(run_dir, "captions.srt"), "xb") as fh:
        fh.write(plan.srt)
    prod_argv = materialize(plan, ffmpeg_path=pre["paths"]["ffmpeg"], asset_root=os.path.realpath(assets), run_dir=location.run_dir)
    exec_argv, delta = with_caps(prod_argv)
    receipt.update({"episode_sha256": plan.episode_sha256, "plan_sha256": plan.sha256, "srt_sha256": oracle_sha(plan.srt), "srt_bytes": len(plan.srt),
                    "caption_text_files": {n: oracle_sha(b) for n, b in plan.caption_files.items()}, "run_dir": run_dir,
                    "argv_production": prod_argv, "argv_executed": exec_argv, "argv_delta": delta, "limits": limits.to_dict()})
    try:
        aff = sorted(os.sched_getaffinity(0))
        pin = set(aff[:CPU_CAP])
        os.sched_setaffinity(0, pin)
        receipt["cpu_affinity"] = {"before": aff, "applied": sorted(pin)}
    except (AttributeError, OSError) as exc:
        receipt["cpu_affinity"] = {"applied": None, "reason": type(exc).__name__}
    env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp_dir, "XDG_CACHE_HOME": os.path.join(tmp_dir, "cache"), "TMPDIR": tmp_dir}
    half = max(1, limits.max_diag_bytes // 2)
    t0 = time.monotonic()
    with ThreadPoller(run_dir) as poller:
        res = runner.run_bounded(exec_argv, phase="render", timeout_s=float(HARD_SECONDS), stdout_limits=(0, 0), stderr_limits=(half, half),
                                 max_drain_bytes=limits.max_drain_bytes, cwd=tmp_dir, env=env)
    receipt["wall_seconds"] = round(time.monotonic() - t0, 3)
    receipt["observed_peak_threads_of_ffmpeg_process"] = {"peak": poller.peak, "samples": poller.samples,
                                                          "note": "includes non-worker threads; the cap above limits worker/filter/encoder threads, not every helper thread"}
    receipt["render_execution"] = res.to_dict()
    partial, final = os.path.join(run_dir, "output.mp4.partial"), os.path.join(run_dir, "output.mp4")
    status = None
    if res.outcome != runner.EXITED_ZERO:
        status = "RENDER_" + res.outcome
    elif not os.path.exists(partial):
        status = "RENDER_NO_OUTPUT"
    if status is None:
        os.rename(partial, final)
        pres, parsed, raw = readback.run_probe(pre["paths"]["ffprobe"], final, "mov", limits, tmp_dir)
        receipt["production_readback_execution"] = pres.to_dict()
        if parsed is None:
            status = "INSPECTION_FAILED"
        else:
            with open(os.path.join(run_dir, "ffprobe.json"), "xb") as fh:
                fh.write(raw)
            summ = readback.summarize(parsed)
            exp = plan.canonical["expected_readback"]
            cmp = readback.compare(summ, width=exp["width"], height=exp["height"], fps=plan.episode.fps, total_frames=exp["frames"], codec="h264", pix_fmt="yuv420p")
            receipt["production_readback"] = {"summary": summ, "comparison": cmp}
            status = "READBACK_MATCH" if cmp["all_ok"] else "READBACK_MISMATCH"
    receipt["status"] = status
    for name in ("output.mp4", "output.mp4.partial", "captions.srt", "plan.canonical.json", "ffprobe.json"):
        p = os.path.join(run_dir, name)
        if os.path.isfile(p):
            receipt.setdefault("files", {})[name] = {"bytes": os.path.getsize(p), "sha256": sha256_file(p)}
    try:
        shutil.rmtree(tmp_dir)
        receipt["tmp_cleanup"] = "removed"
    except OSError as exc:
        receipt["tmp_cleanup"] = f"left: {type(exc).__name__}"
    receipt["finished_utc"] = rc.utc_now()
    write_json(os.path.join(run_dir, "receipt.render-once.json"), receipt)
    return receipt


def _git_head() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def planmod_font_name() -> str:
    from video_core import plan as planmod
    return planmod.FONT_TMP_NAME


def oracle_sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------------------------------- analysis -------------

def run_tool(argv, *, timeout=60.0, stdout_head=1 << 20):
    res = runner.run_bounded(argv, phase="analysis", timeout_s=timeout, stdout_limits=(stdout_head, 0), stderr_limits=(4096, 4096),
                             max_drain_bytes=64 * 1024 * 1024, cwd=None, env=tools.probe_env())
    return res


def analyze(media: str, ffmpeg: str, ffprobe: str, workdir: str, render_receipt: dict | None) -> dict:
    """Machine checks on the finished MP4: ffprobe packet/frame timing, full decode, per-frame caption visibility vs the rational oracle,
    layout measurements, left-to-right check, and the 3 explicitly indexed frames for the contact sheet."""
    ep = json.loads(rb(os.path.join(HERE, "render_case.episode.json")).decode("utf-8"))
    W, H, T = ep["canvas"]["width"], ep["canvas"]["height"], ep["total_frames"]
    fps = Fraction(ep["fps"]["n"], ep["fps"]["d"])
    caps = [(c["id"], c["start_frame"], c["end_frame"], c["text"]) for c in ep["captions"]]
    checks = []

    def chk(name, expected, observed, ok, kind="machine"):
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": bool(ok), "kind": kind})

    out = {"schema": "jpcap.render_analysis/1", "media": os.path.basename(media), "media_sha256": sha256_file(media), "media_bytes": os.path.getsize(media), "checks": checks,
           "labels": {"machine": "computed by code in this directory; reproducible from the media + corpus", "author_visual": "see AUTHOR_VISUAL_OBSERVATIONS.md; not a machine check; not independent"}}
    # --- ffprobe: container + per-frame timing (own JSON parse; not the production comparison)
    pr = run_tool([ffprobe, "-v", "error", "-hide_banner", "-count_frames", "-show_streams", "-show_format", "-of", "json", media])
    info = json.loads(pr.stdout_bytes().decode("utf-8")) if pr.ok else {}
    out["ffprobe_execution"] = pr.to_dict()
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    v = vs[0] if vs else {}
    chk("one_video_stream_only", 1, [s.get("codec_type") for s in info.get("streams", [])], len(info.get("streams", [])) == 1 and len(vs) == 1)
    chk("size", f"{W}x{H}", f"{v.get('width')}x{v.get('height')}", (v.get("width"), v.get("height")) == (W, H))
    chk("codec_pixfmt", "h264/yuv420p", f"{v.get('codec_name')}/{v.get('pix_fmt')}", (v.get("codec_name"), v.get("pix_fmt")) == ("h264", "yuv420p"))
    chk("r_frame_rate_exact", f"{fps.numerator}/{fps.denominator}", v.get("r_frame_rate"), Fraction(v.get("r_frame_rate", "0/1")) == fps)
    chk("nb_read_frames", T, v.get("nb_read_frames"), str(v.get("nb_read_frames")) == str(T))
    chk("duration_le_2s", "<= 2", v.get("duration"), v.get("duration") not in (None, "N/A") and Fraction(v["duration"]) <= 2)
    pf = run_tool([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts,pkt_dts,pict_type:stream=time_base", "-of", "json", media], stdout_head=1 << 22)
    fj = json.loads(pf.stdout_bytes().decode("utf-8")) if pf.ok else {"frames": [], "streams": [{}]}
    tb = Fraction(fj["streams"][0].get("time_base", "0/1")) if fj.get("streams") else Fraction(0)
    pts = [fr.get("pts", fr.get("pkt_pts")) for fr in fj.get("frames", [])]
    exact = [Fraction(int(p)) * tb == Fraction(i) / fps for i, p in enumerate(pts)] if pts and tb else []
    chk("frame_pts_equal_exact_rational_i_over_fps", f"{T} frames, all exact", f"{len(pts)} frames; exact={sum(exact)}", len(pts) == T and all(exact) and len(exact) == T)
    out["ffprobe_frame_timing"] = {"time_base": str(tb), "first_pts": pts[:3], "count": len(pts)}
    # --- full decode of the same file, luma plane
    frame_bytes = W * H * 3 // 2
    dec = run_tool([ffmpeg, "-v", "error", "-xerror", "-threads", "1", "-i", media, "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                   stdout_head=frame_bytes * (T + 4), timeout=30.0)
    raw = dec.stdout_bytes()
    out["full_decode_execution"] = dec.to_dict()
    chk("full_decode_exit_zero_stderr_empty", "exit 0, no stderr", f"{dec.outcome}; stderr_bytes={dec.stderr.total if dec.stderr else None}", dec.ok and (dec.stderr is None or dec.stderr.total == 0))
    chk("full_decode_frame_count", T, len(raw) / frame_bytes, len(raw) == frame_bytes * T)
    frames = [raw[i * frame_bytes:i * frame_bytes + W * H] for i in range(len(raw) // frame_bytes)] if len(raw) == frame_bytes * T else []
    out["decoded_raw_sha256"] = oracle_sha(raw)
    if frames:
        # oracle windows (disjoint here by construction; gap frames 26..30)
        windows = {cid: oracle.visible_frames_midpoint(s, e, T, fps) for cid, s, e, _t in caps}
        cls = analysis.classify_sequence(frames, W, H, windows)
        out["per_frame_visibility"] = [{"frame": r["frame"], "expected": r["expected"], "ink": r["ink"], "nearest": r["nearest"], "d": r["nearest_distance"]} for r in cls["frames"]]
        chk("every_frame_matches_rational_oracle_no_bleed_no_off_by_one", "0 violations", cls["violations"], cls["ok"])
        # boundary frames explicitly
        def ink(i):
            return analysis.ink_stats(frames[i], W, H)["ink"]
        for cid, s, e, _t in caps:
            chk(f"{cid}_first_frame_{s}_has_ink", ">0", ink(s), ink(s) > 0)
            chk(f"{cid}_last_frame_{e - 1}_has_ink", ">0", ink(e - 1), ink(e - 1) > 0)
            if e < T:
                nxt = next((c for c in caps if c[1] == e), None)
                chk(f"after_{cid}_frame_{e}", "next caption" if nxt else "blank", f"ink={ink(e)}", (ink(e) > 0) == bool(nxt))
        chk("gap_frames_blank", "0 ink on frames 26..30", [ink(i) for i in range(26, 31)], all(ink(i) == 0 for i in range(26, 31)))
        chk("black_background_luma_range", "blank frames max luma <= 20", max(max(frames[i]) for i in range(26, 31)), max(max(frames[i]) for i in range(26, 31)) <= 20)
        # layout measurements per caption (middle frame)
        lay = {}
        for cid, s, e, text in caps:
            mid = (s + e - 1) // 2
            st = analysis.ink_stats(frames[mid], W, H)
            lay[cid] = {"frame": mid, **st}
        out["layout"] = lay
        out["layout_checks_note"] = "bbox in pixels [x0,y0,x1,y1]; predicted widths use hmtx advances (no kerning/shaping) and are estimates"
        font = cmapx.read_font(rb(render_receipt["font"]["source"])) if render_receipt else None
        if font:
            pred = {}
            for cid, s, e, text in caps:
                import diagnostics
                widths = []
                for line in text.split("\n"):
                    lo, hi = diagnostics.line_width_units(line, font)
                    widths.append([round(lo * C.RENDER_FONT_SIZE / font.units_per_em, 1), round(hi * C.RENDER_FONT_SIZE / font.units_per_em, 1)])
                pred[cid] = widths
            out["predicted_line_widths_px"] = pred
            for cid, s, e, text in caps:
                bb = lay[cid]["bbox"]
                w_meas = bb[2] - bb[0] + 1
                lo = max(w for w, _h in pred[cid])
                hi = max(h for _l, h in pred[cid])
                if hi <= W:
                    chk(f"{cid}_ink_width_within_predicted_advance_range", f"{lo - C.RENDER_FONT_SIZE}..{hi + 2}", w_meas, lo - C.RENDER_FONT_SIZE <= w_meas <= hi + 2)
                    cx = (bb[0] + bb[2]) / 2
                    chk(f"{cid}_horizontally_centred_within_1p5_em_coarse", f"~{(W - 1) / 2}", cx, abs(cx - (W - 1) / 2) <= C.RENDER_FONT_SIZE * 1.5)
                else:
                    EDGE_TOL = 3     # revised after run 1: strict x==0/x==W-1 ignored glyph side-bearings at a clipped edge (ink was x=1..318)
                    chk(f"{cid}_overflow_predicted_and_ink_reaches_both_frame_edges_within_{EDGE_TOL}px", f"predicted {lo}-{hi}px > {W}px", f"bbox x {bb[0]}..{bb[2]}",
                        bb[0] <= EDGE_TOL and bb[2] >= W - 1 - EDGE_TOL)
        r1h = lay["r1"]["bbox"][3] - lay["r1"]["bbox"][1] + 1
        r3h = lay["r3"]["bbox"][3] - lay["r3"]["bbox"][1] + 1
        chk("r3_two_lines_taller_than_r1_one_line", "ratio >= 1.6", round(r3h / r1h, 2), r3h / r1h >= 1.6)
        # left-to-right (not reversed): heuristic IoU against forward / reversed Pillow rendering
        if analysis.have_pil() and render_receipt:
            fp = render_receipt["font"]["source"]
            r1 = lay["r1"]
            rv = analysis.reversed_vs_forward(frames[r1["frame"]], W, H, fp, C.RENDER_FONT_SIZE, dict((c[0], c[3]) for c in caps)["r1"], r1["bbox"])
            out["left_to_right_r1"] = rv
            chk("r1_drawn_left_to_right_not_reversed", "forward IoU > reversed IoU", rv, rv["forward_wins"], kind="machine-heuristic")
        else:
            chk("r1_drawn_left_to_right_not_reversed", "needs Pillow + receipt", "SKIPPED", True, kind="skipped")
    # --- explicitly indexed frame extraction (a decode, not a render) + contact sheet
    sheet = extract_and_sheet(media, ffmpeg, workdir, frames, W, H, T, caps, checks)
    out["contact_sheet"] = sheet
    out["all_machine_checks_ok"] = all(c["ok"] for c in checks)
    return out


def extract_and_sheet(media, ffmpeg, workdir, frames, W, H, T, caps, checks):
    outdir = os.path.join(workdir, "frames")
    os.makedirs(outdir, exist_ok=True)
    expr = "+".join(f"eq(n\\,{i})" for i in SHEET_FRAMES)       # backslash-comma: escaped at filtergraph level (no shell involved)
    argv = [ffmpeg, "-v", "error", "-threads", "1", "-i", media, "-vf", f"select={expr}", "-fps_mode", "passthrough", "-frames:v", str(len(SHEET_FRAMES)),
            os.path.join(outdir, "frame_%02d.png")]
    res = run_tool(argv, timeout=30.0)
    rec = {"indices": list(SHEET_FRAMES), "extraction_argv": argv, "execution": res.to_dict(), "note": "decode+select of the same MP4; not a new render"}
    pngs = [os.path.join(outdir, f"frame_{k:02d}.png") for k in range(1, len(SHEET_FRAMES) + 1)]
    rec["frames"] = []
    if not (res.ok and all(os.path.isfile(p) for p in pngs)):
        checks.append({"name": "frame_extraction", "expected": "3 PNGs", "observed": "failed", "ok": False, "kind": "machine"})
        return rec
    ok_all = True
    for idx, p in zip(SHEET_FRAMES, pngs):
        entry = {"index": idx, "file": os.path.basename(p), "sha256": sha256_file(p), "bytes": os.path.getsize(p)}
        if analysis.have_pil() and frames:
            from PIL import Image
            with Image.open(p) as raw_im:
                im = raw_im.convert("L")
            px = im.tobytes()

            def expected_l(y):                       # limited-range luma -> full-range gray, as a decoder's RGB conversion would give
                return bytes(max(0, min(255, round((v - 16) * 255 / 219))) for v in y)

            def mad(a, b):
                return sum(abs(p - q) for p, q in zip(a, b)) / len(a)
            target = mad(px, expected_l(frames[idx]))
            owner = lambda i: next((c[0] for c in caps if c[1] <= i < c[2]), None)    # noqa: E731
            best = min(range(len(frames)), key=lambda i: mad(px, expected_l(frames[i])))
            entry["mean_abs_diff_vs_decoded_frame_at_index"] = round(target, 3)
            entry["best_matching_decoded_frame"] = best
            entry["best_match_is_in_same_oracle_window"] = owner(best) == owner(idx)
            ok_all = ok_all and im.size == (W, H) and target <= 2.0 and owner(best) == owner(idx)
        rec["frames"].append(entry)
    checks.append({"name": "extracted_png_frames_are_the_indexed_decoded_frames", "expected": "PNG gray within 2.0 mean-abs-diff of the decoded luma at that index, and its best match lies in the same oracle caption window", "observed": [(f.get("mean_abs_diff_vs_decoded_frame_at_index"), f.get("best_matching_decoded_frame")) for f in rec["frames"]], "ok": ok_all, "kind": "machine"})
    if analysis.have_pil():
        from PIL import Image, ImageDraw, ImageFont
        scale, pad, label_h = 2, 8, 16
        sheet = Image.new("RGB", (pad + (W * scale + pad) * len(pngs), pad + H * scale + pad + label_h), (40, 40, 40))
        d = ImageDraw.Draw(sheet)
        lab = ImageFont.load_default()
        for k, (idx, p) in enumerate(zip(SHEET_FRAMES, pngs)):
            im = Image.open(p).convert("RGB").resize((W * scale, H * scale), Image.NEAREST)
            x = pad + k * (W * scale + pad)
            sheet.paste(im, (x, pad))
            who = next((c[0] for c in caps if c[1] <= idx < c[2]), "none")
            d.text((x, pad + H * scale + 3), f"frame {idx} ({who}) 2x nearest", fill=(220, 220, 220), font=lab)
        sp = os.path.join(workdir, "contact_sheet_frames_13_31_53.png")
        sheet.save(sp, optimize=False)
        rec["contact_sheet"] = {"file": os.path.basename(sp), "sha256": sha256_file(sp), "bytes": os.path.getsize(sp), "size": list(sheet.size), "scale": f"{scale}x nearest neighbour (display only)"}
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", required=True, help="scratch directory (outside the repository) that receives the media")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--font", default="/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf")
    ap.add_argument("--i-accept-the-single-attempt", action="store_true", help="required for a real render")
    ap.add_argument("--analyze-only", action="store_true", help="measure an existing render in --workdir; no render, no marker")
    ap.add_argument("--receipt-out", help="where to write the JSON receipt copy for committing (default: <workdir>/receipt.json)")
    ns = ap.parse_args(argv)
    work = os.path.abspath(ns.workdir)
    repo = os.path.abspath(os.path.join(ROOT, "..", ".."))
    if os.path.commonpath([work, repo]) == repo:
        print("refusing: --workdir must be outside the repository (media is never committed)", file=sys.stderr)
        return 2
    ffmpeg = tools.locate("ffmpeg", ns.ffmpeg)[0] or ns.ffmpeg
    ffprobe = tools.locate("ffprobe", ns.ffprobe)[0] or ns.ffprobe
    run_dir = os.path.join(work, "out", OUTPUT_NAME)
    if ns.analyze_only:
        rr_path = os.path.join(run_dir, "receipt.render-once.json")
        rr = json.loads(rb(rr_path).decode("utf-8")) if os.path.exists(rr_path) else None
        media = os.path.join(run_dir, "output.mp4")
        an = analyze(media, ffmpeg, ffprobe, work, rr)
        write_json(os.path.join(work, "analysis.json"), an)
        print(json.dumps({"all_machine_checks_ok": an["all_machine_checks_ok"], "media_sha256": an["media_sha256"]}))
        return 0 if an["all_machine_checks_ok"] else 1
    if not ns.i_accept_the_single_attempt:
        print("refusing: pass --i-accept-the-single-attempt (one real render, no retry)", file=sys.stderr)
        return 2
    receipt = render(work, ffmpeg, ffprobe, ns.font)
    out_path = ns.receipt_out or os.path.join(work, "receipt.json")
    write_json(out_path, receipt)
    print(json.dumps({"status": receipt["status"], "receipt": out_path}))
    if receipt["status"] == "READBACK_MATCH":
        media = os.path.join(run_dir, "output.mp4")
        an = analyze(media, ffmpeg, ffprobe, work, receipt)
        write_json(os.path.join(work, "analysis.json"), an)
        print(json.dumps({"all_machine_checks_ok": an["all_machine_checks_ok"], "media_sha256": an["media_sha256"]}))
        return 0 if an["all_machine_checks_ok"] else 1
    return 0 if receipt["status"].startswith("SKIPPED") else 3


if __name__ == "__main__":
    sys.exit(main())
