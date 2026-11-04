"""Explicit AV muxing: preflight -> fresh directory -> bounded `-c:v copy` mux -> full independent readback -> receipt.

Order (nothing retried; one owned temporary directory; shell=False; only our own children are ever signalled):
  1. pure plan (hashes, WAV parse, declared-vs-timeline rules)               -- may raise; nothing exists yet
  2. tool discovery, version and capability probes (encoder/decoder/muxer/demuxer)    -- missing => SKIPPED_TOOL_UNAVAILABLE, nothing created
  3. bounded ffprobe of the REAL video and WAV; measured properties must equal the declared ones -- mismatch => rejection, nothing created
  4. argv materialized and bounded (UTF-8 bytes, Windows UTF-16 units)                -- excess => rejection, nothing created
  5. fresh ROOT/NAME + tmp/, receipt IN_PROGRESS, mux into output.<ext>.partial
  6. readback: ffprobe of the output, decoded video frame hashes (source vs output), copied packet hashes (source vs output),
     decoded audio (byte count + SHA-256), route-specific contract, input re-hash
The technical verdict is reported separately from quality: `quality_evaluated` is always false and creative acceptance is never evaluated.
"""

from __future__ import annotations

import os
import shutil
from fractions import Fraction

from .. import readback, receipt as rc, runner, tools
from ..errors import Issue, UnsafePath, ValidationFailed
from ..limits import Limits, check_argv_budget
from ..paths import check_output_location
from ..plan import materialize
from . import checks as ck
from . import plan as planmod
from .limits import HASH_TEXT_CAP, PCM_DRAIN_SLACK
from .plan import AAC_TOLERANCE_SAMPLES, AvPlan, prepare_plan

RECEIPT_SCHEMA = "video_core.avmux_receipt/1"
S_MOV_OK = "MUXED_VERIFIED_SAMPLE_EXACT"
S_MP4_OK = "MUXED_TECHNICAL_WITHIN_TOLERANCE"
S_MISMATCH = "READBACK_MISMATCH"
S_INSPECT_FAILED = "INSPECTION_FAILED"
OK_STATUSES = (S_MOV_OK, S_MP4_OK)
QUALITY_NOTE = ("Technical verdict only (container/stream properties, decoded frame/packet hashes, decoded audio counts). No audio, visual, sync-perception or creative "
                "quality was evaluated and nothing here is creative acceptance.")


def _skip(plan: AvPlan, reasons, records, executions) -> dict:
    return {"schema": RECEIPT_SCHEMA, "status": rc.STATUS_SKIPPED, "written": False, "reasons": reasons, "stage": "tool_probe", "quality_evaluated": False,
            "creative_acceptance": "NOT_EVALUATED", "note": QUALITY_NOTE, "plan_sha256": plan.sha256, "route": plan.manifest.route,
            "tools": {k: v.to_dict() if v else None for k, v in records.items()}, "executions": [e.to_dict() for e in executions], "outputs": [],
            "platform": rc.platform_facts(), "finished_utc": rc.utc_now(), "note_skip": "No output directory was created and no mux was launched."}


def _probe(ffprobe, path, demuxer, limits, cwd, phase, executions):
    res, parsed, raw = readback.run_probe(ffprobe, path, demuxer, limits, cwd, phase=phase)
    executions.append(res)
    return res, parsed, raw


def _reject_checks(checks, ptr):
    bad = [c for c in checks if not c["ok"]]
    if bad:
        raise ValidationFailed("measured input properties do not match the declaration",
                               [Issue(ck.CODES.get(c["name"], "input_property_mismatch"), ptr, f"{c['name']}: expected {c['expected']!r}, measured {c['observed']!r}") for c in bad])


def _ffmpeg_in(ffmpeg, path):
    return [ffmpeg, "-v", "error", "-nostdin", "-protocol_whitelist", "file", "-f", "mov", "-i", path]


def _run(argv, phase, limits, cwd, env, executions, *, timeout=None, stdout=(0, 0), drain=None):
    half = max(1, limits.max_diag_bytes // 2)
    res = runner.run_bounded(argv, phase=phase, timeout_s=timeout or limits.inspect_timeout_seconds, stdout_limits=stdout, stderr_limits=(half, half),
                             max_drain_bytes=drain or limits.max_drain_bytes, cwd=cwd, env=env)
    executions.append(res)
    return res


def mux(manifest_bytes: bytes, *, limits: Limits = Limits(), asset_root: str | None, output_root: str, output_name: str,
        ffmpeg: str | None = None, ffprobe: str | None = None) -> dict:
    """Mux into the fresh directory output_root/output_name. Raises (before anything is created) for rejected input;
    otherwise returns the receipt dict (also written to receipt.json unless the status is SKIPPED_TOOL_UNAVAILABLE)."""
    plan = prepare_plan(manifest_bytes, limits=limits, asset_root=asset_root, output_root=output_root, output_name=output_name, ffmpeg=ffmpeg, ffprobe=ffprobe)
    m = plan.manifest
    location = check_output_location(output_root, output_name, asset_root)
    real_assets = os.path.realpath(asset_root)

    executions: list = []
    records: dict = {"ffmpeg": None, "ffprobe": None}
    reasons, paths = [], {}
    for name, explicit in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        path, how = tools.locate(name, explicit)
        if path is None:
            reasons.append({"tool": name, "reason": f"{name} not found (discovery: {how})"})
        else:
            paths[name] = (path, how)
    if reasons:
        return _skip(plan, reasons, records, executions)
    for name, (path, how) in paths.items():
        rec, runs = tools.probe_version(name, path, how, limits, location.root)
        records[name] = rec
        executions.extend(runs)
        if rec.problem:
            reasons.append({"tool": name, "reason": rec.problem})
    if reasons:
        return _skip(plan, reasons, records, executions)
    caps, runs = tools.missing_capabilities(paths["ffmpeg"][0], plan.canonical["requirements"], limits, location.root)
    executions.extend(runs)
    if caps["missing"] or caps["probe_failed"]:
        return _skip(plan, [{"tool": "ffmpeg", "reason": "required capability unavailable", "detail": caps}], records, executions)

    # ---- measured properties of the real inputs (bounded ffprobe); mismatch => rejection before anything exists ----
    ff, fp = paths["ffmpeg"][0], paths["ffprobe"][0]
    fps = Fraction(m.fps_n, m.fps_d)
    vres, vparsed, _ = _probe(fp, plan.video_file.abspath, "mov", limits, location.root, "preflight_probe_video", executions)
    ares, aparsed, _ = _probe(fp, plan.audio_file.abspath, "wav", limits, location.root, "preflight_probe_audio", executions)
    for what, res, parsed in (("video", vres, vparsed), ("audio", ares, aparsed)):
        if not res.ok or parsed is None:
            raise ValidationFailed(f"ffprobe could not measure the {what} input", [Issue("preflight_probe_failed", f"/{what}", f"ffprobe on the {what} input ended {res.outcome} or returned no usable JSON")])
    src_checks = (ck.check_layout_single_video(vparsed) + ck.check_video(vparsed, width=m.width, height=m.height, fps=fps, frames=m.total_frames)
                  + ck.check_audio_source(aparsed, channels=m.audio_channels, samples=plan.expected_samples))
    _reject_checks(src_checks, "inputs")

    argv = materialize(plan, ffmpeg_path=ff, asset_root=real_assets, run_dir=location.run_dir)
    argv_budget = check_argv_budget(argv, limits)
    pre = {"video": plan.canonical["video"]["sha256"], "audio": plan.canonical["audio"]["sha256"]}

    run_dir, tmp_dir = location.run_dir, os.path.join(location.run_dir, "tmp")
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        raise UnsafePath("output exists", [Issue("output_exists", "output_name", "output location already exists; overwriting is refused")]) from None
    receipt: dict = {
        "schema": RECEIPT_SCHEMA, "status": rc.STATUS_IN_PROGRESS, "written": True, "stage": "setup", "route": m.route, "quality_evaluated": False,
        "creative_acceptance": "NOT_EVALUATED", "note": QUALITY_NOTE, "started_utc": rc.utc_now(), "plan_sha256": plan.sha256, "platform": rc.platform_facts(),
        "tools": {k: v.to_dict() for k, v in records.items()}, "limits": limits.to_dict(), "argv_budget": argv_budget,
        "selection": plan.canonical["selection"], "contract": plan.canonical["contract"],
        "preflight": {"source_checks": src_checks, "video_properties_basis": "MEASURED_BY_FFPROBE_AT_PREFLIGHT"},
        "input": {"video": {"path": m.video_path, "bytes": plan.video_file.size, "sha256_before": pre["video"]},
                  "audio": {"path": m.audio_path, "bytes": plan.audio_file.size, "sha256_before": pre["audio"], "pcm_sha256": plan.pcm_sha256}},
        "executions": [e.to_dict() for e in executions], "outputs": [], "checks": [], "observations": {}, "technical_verdict": None,
    }
    receipt_path = os.path.join(run_dir, planmod.RECEIPT_NAME)
    rc.write_new(receipt_path, rc.dumps(receipt))  # a stale IN_PROGRESS receipt means UNKNOWN
    try:
        _run_mux(plan, limits, argv, run_dir, tmp_dir, ff, fp, executions, receipt, receipt_path, location.root)
    except Exception as exc:  # preserve partial state and say so; never retry
        receipt["status"] = rc.STATUS_UNKNOWN
        receipt["internal_error"] = {"type": type(exc).__name__, "message": str(exc)[:300], "stage": receipt.get("stage")}
    receipt["executions"] = [e.to_dict() for e in executions]
    receipt["finished_utc"] = rc.utc_now()
    rc.replace_atomic(receipt_path, rc.dumps(receipt))
    return receipt


def _file_record(path, name, role, limits, partial=False):
    size = os.path.getsize(path)
    return {"name": name, "role": role, "bytes": size, "sha256": tools.sha256_file(path, max(limits.max_output_bytes, size)), "partial": partial}


def _hash_text(ff, path, mode, limits, tmp, env, executions, phase):
    """Per-frame hash lines of the source/output video. Returns (rows | None, RunResult)."""
    tail = ["-map", "0:v:0", "-f", "framemd5", "-"] if mode == "decoded" else ["-map", "0:v:0", "-c:v", "copy", "-f", "framehash", "-hash", "sha256", "-"]
    res = _run(_ffmpeg_in(ff, path) + tail, phase, limits, tmp, env, executions, stdout=(HASH_TEXT_CAP, 0))
    if not res.ok or res.stdout.summary()["truncated"]:
        return None, res
    try:
        return ck.parse_hash_lines(res.stdout_bytes().decode("utf-8", "replace")), res
    except ValueError:
        return None, res


def _run_mux(plan: AvPlan, limits, argv, run_dir, tmp_dir, ff, fp, executions, receipt, receipt_path, probe_cwd) -> None:  # noqa: C901
    m = plan.manifest
    final_name, partial_name, mismatch_name = planmod.output_names(m.route)
    partial, final, mismatch = (os.path.join(run_dir, n) for n in (partial_name, final_name, mismatch_name))
    rc.write_new(os.path.join(run_dir, planmod.PLAN_NAME), plan.canonical_json)
    os.mkdir(tmp_dir)
    receipt["stage"] = "mux"
    receipt["executions"] = [e.to_dict() for e in executions]
    rc.replace_atomic(receipt_path, rc.dumps(receipt))
    env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp_dir, "XDG_CACHE_HOME": os.path.join(tmp_dir, "cache"), "TMPDIR": tmp_dir}
    half = max(1, limits.max_diag_bytes // 2)
    res = runner.run_bounded(argv, phase="mux", timeout_s=limits.max_runtime_seconds, stdout_limits=(0, 0), stderr_limits=(half, half),
                             max_drain_bytes=limits.max_drain_bytes, cwd=tmp_dir, env=env)
    executions.append(res)
    by_outcome = {runner.NONZERO_EXIT: rc.STATUS_PROCESS_FAILED, runner.TIMEOUT: rc.STATUS_TIMEOUT, runner.SIGNALED: rc.STATUS_SIGNALED, runner.FLOOD_KILLED: rc.STATUS_FLOOD,
                  runner.INTERRUPTED: rc.STATUS_INTERRUPTED, runner.LAUNCH_FAILED: rc.STATUS_LAUNCH_FAILED}
    status = None
    if res.outcome != runner.EXITED_ZERO:
        status = by_outcome[res.outcome]
    elif not res.ok:
        status, receipt["unknown_reason"] = rc.STATUS_UNKNOWN, "ffmpeg exited 0 but its output pipes did not drain cleanly (possible surviving child process)"
    elif not os.path.exists(partial):
        status, receipt["unknown_reason"] = rc.STATUS_UNKNOWN, "ffmpeg exited 0 but produced no output file"
    receipt["stage"] = "mux" if status else "readback"

    checks: list = []
    obs: dict = {}
    if status is None:
        status = _readback(plan, limits, partial, ff, fp, tmp_dir, env, executions, checks, obs, run_dir)
    receipt["checks"], receipt["observations"] = checks, obs

    # input re-hash decides the status BEFORE any file is promoted
    post = {"video": tools.sha256_file(plan.video_file.abspath, limits.max_source_bytes), "audio": tools.sha256_file(plan.audio_file.abspath, limits.max_source_bytes)}
    receipt["input"]["video"]["sha256_after"], receipt["input"]["audio"]["sha256_after"] = post["video"], post["audio"]
    unchanged = post["video"] == receipt["input"]["video"]["sha256_before"] and post["audio"] == receipt["input"]["audio"]["sha256_before"]
    receipt["input"]["inputs_unchanged"] = unchanged
    if not unchanged and status in OK_STATUSES + (S_MISMATCH,):
        receipt["unknown_reason"] = "an input file changed while muxing"
        status = rc.STATUS_UNKNOWN

    receipt["technical_verdict"] = {"contract": plan.canonical["contract"]["id"], "met": status in OK_STATUSES, "sample_exact": status == S_MOV_OK,
                                    "tolerance": ({"samples": AAC_TOLERANCE_SAMPLES} if m.route == "MP4_AAC" else None),
                                    "quality_evaluated": False, "creative_acceptance": "NOT_EVALUATED", "perceptual_sync_evaluated": False}
    if status in OK_STATUSES:
        os.rename(partial, final)
    elif os.path.exists(partial) and receipt["stage"] != "mux" and status in (S_MISMATCH, S_INSPECT_FAILED, rc.STATUS_UNKNOWN):
        os.rename(partial, mismatch)  # a file that failed its contract must not look like a deliverable

    outputs = []
    for name, role, part in ((final_name, "media", False), (partial_name, "media_partial", True), (mismatch_name, "media_unverified", True),
                             (planmod.PLAN_NAME, "plan", False), (planmod.PROBE_OUT_NAME, "ffprobe_output", False)):
        p = os.path.join(run_dir, name)
        if os.path.isfile(p):
            outputs.append(_file_record(p, name, role, limits, partial=part))
    receipt["outputs"] = outputs
    try:
        shutil.rmtree(tmp_dir)
        receipt["tmp_cleanup"] = "removed"
    except OSError as exc:
        receipt["tmp_cleanup"] = f"left in place: {type(exc).__name__}"
    receipt["status"] = status
    receipt["stage"] = "complete" if status in OK_STATUSES + (S_MISMATCH,) else receipt["stage"]


def _readback(plan: AvPlan, limits, out_path, ff, fp, tmp_dir, env, executions, checks, obs, run_dir) -> str:  # noqa: C901
    m = plan.manifest
    fps = Fraction(m.fps_n, m.fps_d)
    exp_samples = plan.expected_samples
    pcm_bytes = exp_samples * m.audio_channels * 2
    # 1. ffprobe of the output
    ores, oparsed, oraw = _probe(fp, out_path, "mov", limits, tmp_dir, "readback_probe_output", executions)
    if not ores.ok or oparsed is None:
        obs["error"] = "ffprobe could not read the muxed output"
        return S_INSPECT_FAILED
    rc.write_new(os.path.join(run_dir, planmod.PROBE_OUT_NAME), oraw)
    checks += ck.check_output_layout(oparsed)
    checks += ck.check_video(oparsed, width=m.width, height=m.height, fps=fps, frames=m.total_frames, prefix="output_video")
    achecks, aobs = ck.check_output_audio(oparsed, route=m.route, channels=m.audio_channels, samples=exp_samples)
    checks += achecks
    obs["output_audio_stream"] = aobs
    # 2. decoded video frames and copied packets: source vs output
    src = os.path.join(os.path.realpath(os.path.dirname(plan.video_file.abspath)), os.path.basename(plan.video_file.abspath))
    for mode, label in (("decoded", "decoded_video_frames"), ("packets", "copied_video_packets")):
        a, ra = _hash_text(ff, src, mode, limits, tmp_dir, env, executions, f"readback_{mode}_source")
        b, rb = _hash_text(ff, out_path, mode, limits, tmp_dir, env, executions, f"readback_{mode}_output")
        if a is None or b is None:
            obs[f"{label}_error"] = "hash listing unavailable (decode failed, errors on stderr, or output truncated)"
            return S_INSPECT_FAILED
        checks.append({"name": f"{label}_equal", "expected": {"frames": m.total_frames, "hashes": "equal to source"}, "observed": {"source_frames": len(a), "output_frames": len(b), "equal": a == b},
                       "ok": a == b and len(a) == m.total_frames})
        for r in (ra, rb):
            checks.append({"name": f"{r.phase}_no_decode_errors", "expected": 0, "observed": r.stderr.summary()["total_bytes"], "ok": r.stderr.summary()["total_bytes"] == 0})
    # 3. decoded audio of the output (count + SHA-256 of the whole decoded stream; bytes are drained, not stored)
    ares = _run(_ffmpeg_in(ff, out_path) + ["-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-"], "readback_decode_audio_output", limits, tmp_dir, env, executions,
                stdout=(0, 0), drain=pcm_bytes + PCM_DRAIN_SLACK)
    if not ares.ok:
        obs["decoded_audio_error"] = f"audio decode ended {ares.outcome}"
        return S_INSPECT_FAILED
    summ = ares.stdout.summary()
    decoded_bytes, decoded_sha = summ["total_bytes"], summ["sha256_full_stream"]
    block = m.audio_channels * 2
    decoded_samples = decoded_bytes // block
    checks.append({"name": "audio_decode_no_errors", "expected": 0, "observed": ares.stderr.summary()["total_bytes"], "ok": ares.stderr.summary()["total_bytes"] == 0})
    if m.route == "MOV_PCM_S16LE":
        checks.append({"name": "decoded_pcm_bytes_exact", "expected": pcm_bytes, "observed": decoded_bytes, "ok": decoded_bytes == pcm_bytes})
        checks.append({"name": "decoded_pcm_sha256_equals_wav_pcm", "expected": plan.pcm_sha256, "observed": decoded_sha, "ok": decoded_sha == plan.pcm_sha256})
    else:
        checks += ck.tolerance_checks(decoded_samples, exp_samples, AAC_TOLERANCE_SAMPLES)
        pres = _run([fp, "-v", "error", "-protocol_whitelist", "file", "-f", "mov", "-select_streams", "a:0", "-show_packets", "-of", "json", "-i", out_path], "readback_audio_packets",
                    limits, tmp_dir, env, executions, stdout=(1024 * 1024, 0))
        pk = None
        if pres.ok and not pres.stdout.summary()["truncated"]:
            try:
                import json
                pk = ck.packet_summary(json.loads(pres.stdout_bytes().decode("utf-8")))
            except ValueError:
                pk = None
        obs["audio_packets"] = pk if pk is not None else {"unavailable": "ffprobe -show_packets did not return usable JSON"}
        obs["aac_observations"] = {"decoded_samples": decoded_samples, "expected_samples": exp_samples, "delta_samples": decoded_samples - exp_samples,
                                   "exact_sample_count": decoded_samples == exp_samples, "decoded_pcm_sha256": decoded_sha,
                                   "audio_minus_video_duration_seconds_from_container": _dur_delta(oparsed),
                                   "sample_exact_claimed": False, "perceptual_sync_evaluated": False,
                                   "note": "AAC is lossy and carries encoder delay/padding; counts are observations against the documented tolerance, not a sample-exact claim"}
    obs["decoded_audio"] = {"bytes": decoded_bytes, "samples": decoded_samples, "sha256": decoded_sha}
    if all(c["ok"] for c in checks):
        return S_MOV_OK if m.route == "MOV_PCM_S16LE" else S_MP4_OK
    return S_MISMATCH


def _dur_delta(parsed):
    try:
        a = ck.streams_of(parsed, "audio")[0].get("duration")
        v = ck.streams_of(parsed, "video")[0].get("duration")
        return str(Fraction(str(a)) - Fraction(str(v)))
    except (IndexError, ValueError, ZeroDivisionError, TypeError):
        return None
