"""Render orchestration and receipts.

Stages (each recorded; none retried):
  preflight (no side effects) -> tool probe -> create fresh run dir -> input hash
  -> render (ffmpeg) -> inspect (ffprobe) -> output hash -> input re-hash -> receipt
Everything before "create fresh run dir" is rejection-only: no file exists, no
render process has run. After it, the run directory and its bounded `tmp/` are the
only things this module writes.
"""

from __future__ import annotations

import os
import shutil

from . import plan as planmod
from . import finalize, readback, receipt as rc, runner, tools
from .errors import Issue, UnsafePath, ValidationFailed
from .limits import Limits, check_argv_budget
from .paths import check_output_location
from .plan import Plan, materialize, prepare


def _skip_receipt(plan: Plan, reasons: list, tool_records: dict, executions: list) -> dict:
    return {
        "schema": rc.RECEIPT_SCHEMA, "status": rc.STATUS_SKIPPED, "written": False,
        "reasons": reasons, "stage": "tool_probe", "quality_evaluated": False, "note": rc.QUALITY_NOTE,
        "plan_sha256": plan.sha256, "input": {"episode_sha256": plan.episode_sha256},
        "tools": {k: v.to_dict() if v else None for k, v in tool_records.items()},
        "executions": [e.to_dict() for e in executions], "outputs": [],
        "platform": rc.platform_facts(), "finished_utc": rc.utc_now(),
        "note_skip": "No MP4 was produced and no output directory was created.",
    }


def _hash_assets(plan: Plan, limits: Limits) -> dict:
    out = {}
    for a in plan.assets:
        out[a.rel] = tools.sha256_file(a.abspath, limits.max_source_bytes)
    return out


def render(episode_bytes: bytes, *, limits: Limits = Limits(), asset_root: str | None, output_root: str, output_name: str,
           burn_in_font: str | None = None, font_size: int | None = None, ffmpeg: str | None = None,
           ffprobe: str | None = None, envelope=None) -> dict:
    """Render one episode into the fresh directory output_root/output_name.

    `envelope` (optional, default None = nothing changes): a video_core.containment.ResourceEnvelope applied ONLY to the main ffmpeg render process (not to ffprobe/readback).
    A limit this platform cannot apply is rejected before anything is created; a limit the worker fails to apply at launch yields status CONTAINMENT_REFUSED (target not started).

    Raises (before anything is created) ValidationFailed / UnsafePath for rejected input.
    Otherwise returns a receipt dict whose `status` is the honest outcome; the dict is
    also written to receipt.json unless the status is SKIPPED_TOOL_UNAVAILABLE.
    """
    if envelope is not None:
        from .containment import unsupported_limits

        uns = unsupported_limits(envelope)
        if uns:
            raise ValidationFailed("the requested resource envelope cannot be applied on this platform",
                                   [Issue("envelope_unsupported", f"/envelope/{u['limit']}", u["reason"]) for u in uns])
    plan = prepare(episode_bytes, limits=limits, asset_root=asset_root, output_root=output_root, output_name=output_name,
                   burn_in_font=burn_in_font, font_size=font_size, ffmpeg=ffmpeg, ffprobe=ffprobe)
    location = check_output_location(output_root, output_name, asset_root)
    real_assets = os.path.realpath(asset_root) if asset_root else None

    # ---- tool discovery + probes (execute tools, but write nothing) -------------------------
    executions: list = []
    records: dict = {"ffmpeg": None, "ffprobe": None}
    reasons: list = []
    paths = {}
    for name, explicit in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        path, how = tools.locate(name, explicit)
        if path is None:
            reasons.append({"tool": name, "reason": f"{name} not found (discovery: {how})"})
        else:
            paths[name] = (path, how)
    if reasons:
        return _skip_receipt(plan, reasons, records, executions)
    for name, (path, how) in paths.items():
        rec, runs = tools.probe_version(name, path, how, limits, location.root)
        records[name] = rec
        executions.extend(runs)
        if rec.problem:
            reasons.append({"tool": name, "reason": rec.problem})
    if reasons:
        return _skip_receipt(plan, reasons, records, executions)
    caps, runs = tools.missing_capabilities(paths["ffmpeg"][0], plan.canonical["requirements"], limits, location.root)
    executions.extend(runs)
    if caps["missing"] or caps["probe_failed"]:
        reasons.append({"tool": "ffmpeg", "reason": "required capability unavailable", "detail": caps})
        return _skip_receipt(plan, reasons, records, executions)

    # ---- last rejection-only step: materialize + size check --------------------------------
    argv = materialize(plan, ffmpeg_path=paths["ffmpeg"][0], asset_root=real_assets, run_dir=location.run_dir)
    argv_budget_used = check_argv_budget(argv, limits)  # exact materialized argv; Windows bound enforced on Windows
    try:
        pre_hashes = _hash_assets(plan, limits)
    except (OSError, ValueError) as exc:
        raise UnsafePath("cannot read source", [Issue("asset_unreadable", "assets", f"source could not be hashed: {type(exc).__name__}")]) from None

    # ---- create the fresh run directory (the first side effect) ---------------------------
    run_dir = location.run_dir
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        raise UnsafePath("output exists", [Issue("output_exists", "output_name", "output location already exists; overwriting is refused")]) from None
    tmp_dir = os.path.join(run_dir, "tmp")
    receipt: dict = {
        "schema": rc.RECEIPT_SCHEMA, "status": rc.STATUS_IN_PROGRESS, "written": True, "stage": "setup",
        "quality_evaluated": False, "note": rc.QUALITY_NOTE, "started_utc": rc.utc_now(),
        "plan_sha256": plan.sha256, "platform": rc.platform_facts(),
        "tools": {k: v.to_dict() for k, v in records.items()},
        "limits": limits.to_dict(), "argv_budget": argv_budget_used, "executions": [e.to_dict() for e in executions], "outputs": [], "readback": None,
    }
    receipt_path = os.path.join(run_dir, planmod.RECEIPT_NAME)
    rc.write_new(receipt_path, rc.dumps(receipt))  # a stale IN_PROGRESS file means UNKNOWN
    try:
        _run(plan, limits, argv, run_dir, tmp_dir, paths, real_assets, pre_hashes, receipt, envelope)
    except KeyboardInterrupt as exc:  # Ctrl-C / the CLI's SIGTERM translation outside the render step itself (e.g. during the readback probe): the run was cancelled
        if isinstance(exc, runner.RunInterrupted):
            receipt["executions"].append(exc.result.to_dict())  # the interrupted child run is evidence too
        receipt["status"] = rc.STATUS_INTERRUPTED
        _finalize(plan, limits, run_dir, tmp_dir, pre_hashes, receipt, abnormal=True)
    except Exception as exc:  # keep partial state and say so; never retry
        receipt["status"] = rc.STATUS_UNKNOWN
        receipt["internal_error"] = {"type": type(exc).__name__, "message": str(exc)[:300], "stage": receipt.get("stage")}
        _finalize(plan, limits, run_dir, tmp_dir, pre_hashes, receipt, abnormal=True)  # best effort: what is actually on disk, input hashes (null + reason if unreadable), tmp result
    receipt["finished_utc"] = rc.utc_now()
    rc.replace_atomic(receipt_path, rc.dumps(receipt))
    return receipt


def _run(plan: Plan, limits: Limits, argv: list, run_dir: str, tmp_dir: str, paths: dict, real_assets, pre_hashes: dict, receipt: dict, envelope=None) -> None:
    ffmpeg_path, ffprobe_path = paths["ffmpeg"][0], paths["ffprobe"][0]
    rc.write_new(os.path.join(run_dir, planmod.PLAN_NAME), plan.canonical_json)
    rc.write_new(os.path.join(run_dir, planmod.SRT_NAME), plan.srt)
    os.mkdir(tmp_dir)
    for name, data in plan.caption_files.items():
        rc.write_new(os.path.join(tmp_dir, name), data)
    if plan.font_bytes is not None:
        rc.write_new(os.path.join(tmp_dir, planmod.FONT_TMP_NAME), plan.font_bytes)

    receipt["input"] = {
        "episode_sha256": plan.episode_sha256, "episode_bytes": plan.episode_size,
        "assets": [{"path": a.rel, "bytes": a.size, "sha256_before": pre_hashes[a.rel]} for a in plan.assets],
        "font": None if plan.font is None else {"path": plan.font.rel, "sha256": planmod.sha256_bytes(plan.font_bytes)},
    }
    receipt["input"]["input_set_sha256"] = planmod.sha256_bytes(planmod.canonical_bytes({
        "episode": plan.episode_sha256, "assets": pre_hashes,
        "font": receipt["input"]["font"]["sha256"] if plan.font else None}))
    receipt["stage"] = "render"
    rc.replace_atomic(os.path.join(run_dir, planmod.RECEIPT_NAME), rc.dumps(receipt))

    env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp_dir, "XDG_CACHE_HOME": os.path.join(tmp_dir, "cache"), "TMPDIR": tmp_dir}
    half = max(1, limits.max_diag_bytes // 2)
    if envelope is None:
        res = runner.run_bounded(
            argv, phase="render", timeout_s=limits.max_runtime_seconds, stdout_limits=(0, 0), stderr_limits=(half, half),
            max_drain_bytes=limits.max_drain_bytes, cwd=tmp_dir, env=env,
        )
    else:
        from .containment import run_contained

        res = run_contained(argv, envelope, phase="render", timeout_s=limits.max_runtime_seconds, stdout_limits=(0, 0), stderr_limits=(half, half),
                            max_drain_bytes=limits.max_drain_bytes, cwd=tmp_dir, env=env)
        receipt["containment"] = res.containment
    receipt["executions"].append(res.to_dict())

    partial = os.path.join(run_dir, planmod.OUTPUT_PARTIAL)
    final = os.path.join(run_dir, planmod.OUTPUT_FINAL)
    outputs: list = []
    status_by_outcome = {
        runner.NONZERO_EXIT: rc.STATUS_PROCESS_FAILED, runner.TIMEOUT: rc.STATUS_TIMEOUT, runner.SIGNALED: rc.STATUS_SIGNALED,
        runner.FLOOD_KILLED: rc.STATUS_FLOOD, runner.INTERRUPTED: rc.STATUS_INTERRUPTED, runner.LAUNCH_FAILED: rc.STATUS_LAUNCH_FAILED,
        runner.CONTAINMENT_REFUSED: rc.STATUS_CONTAINMENT_REFUSED,
    }
    status = None
    if res.outcome != runner.EXITED_ZERO:
        status = status_by_outcome[res.outcome]
    elif not res.ok:
        status = rc.STATUS_UNKNOWN
        receipt["unknown_reason"] = res.exit0_not_ok_reason("ffmpeg")
    elif not os.path.exists(partial):
        status = rc.STATUS_UNKNOWN
        receipt["unknown_reason"] = "ffmpeg exited 0 but produced no output file"

    if status is None:  # clean exit with an output: promote, then independently read it back
        os.rename(partial, final)
    receipt["stage"] = "inspect" if status is None else "render"

    readback_result = None
    if status is None:
        pres, parsed, raw = readback.run_probe(ffprobe_path, final, "mov", limits, tmp_dir)
        receipt["executions"].append(pres.to_dict())
        if parsed is None:
            status = rc.STATUS_INSPECTION_FAILED
            readback_result = {"error": "ffprobe did not return usable JSON", "ffprobe_outcome": pres.outcome}
        else:
            rc.write_new(os.path.join(run_dir, planmod.PROBE_NAME), raw)
            summary = readback.summarize(parsed)
            exp = plan.canonical["expected_readback"]
            cmp = readback.compare(summary, width=exp["width"], height=exp["height"], fps=plan.episode.fps,
                                   total_frames=exp["frames"], codec="h264", pix_fmt="yuv420p")
            readback_result = {"summary": summary, "comparison": cmp, "ffprobe_json_sha256": planmod.sha256_bytes(raw)}
            status = rc.STATUS_MATCH if cmp["all_ok"] else rc.STATUS_MISMATCH
    receipt["readback"] = readback_result

    unchanged = _finalize(plan, limits, run_dir, tmp_dir, pre_hashes, receipt)
    if not unchanged and status in (rc.STATUS_MATCH, rc.STATUS_MISMATCH):
        receipt["unknown_reason"] = "an input file changed (or became unreadable) while rendering"
        status = rc.STATUS_UNKNOWN
    receipt["status"] = status
    receipt["stage"] = "complete" if status in (rc.STATUS_MATCH, rc.STATUS_MISMATCH) else receipt["stage"]


_KNOWN_OUTPUTS = ((planmod.OUTPUT_FINAL, "video", False), (planmod.OUTPUT_PARTIAL, "video_partial", True), (planmod.SRT_NAME, "srt", False),
                  (planmod.PLAN_NAME, "plan", False), (planmod.PROBE_NAME, "ffprobe_json", False))


def _finalize(plan: Plan, limits: Limits, run_dir: str, tmp_dir: str, pre_hashes: dict, receipt: dict, *, abnormal: bool = False) -> bool:
    """Best-effort and never raises (Exception): inventory the files that actually exist (partial flags, hashes), re-hash the inputs (sha256_after null plus a reason when
    unreadable), remove the owned tmp/ and say what happened. Used on the normal path AND on every error/interrupt path, so a receipt never omits evidence that is on disk.
    Returns True only if every input was re-read and is unchanged."""
    try:
        receipt["outputs"] = finalize.inventory(run_dir, _KNOWN_OUTPUTS, lambda size: max(limits.max_output_bytes, size), include_unrecognised=abnormal)
    except Exception as exc:
        finalize.note_error(receipt, "inventory", exc)
    unchanged = False
    try:
        inp = receipt.get("input")
        if not isinstance(inp, dict):
            inp = receipt["input"] = {"episode_sha256": plan.episode_sha256, "episode_bytes": plan.episode_size}
        if not isinstance(inp.get("assets"), list):
            inp["assets"] = [{"path": a.rel, "bytes": a.size, "sha256_before": pre_hashes.get(a.rel)} for a in plan.assets]
        abspath = {a.rel: a.abspath for a in plan.assets}
        unchanged = finalize.rehash_inputs(inp["assets"], lambda e: tools.sha256_file(abspath[e["path"]], limits.max_source_bytes))
        inp["inputs_unchanged"] = unchanged
    except Exception as exc:
        finalize.note_error(receipt, "input_rehash", exc)
    try:
        receipt["tmp_cleanup"] = finalize.cleanup_tmp(tmp_dir)
    except Exception as exc:
        finalize.note_error(receipt, "tmp_cleanup", exc)
    return unchanged
