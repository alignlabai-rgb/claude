"""Explicit PCM WAV mixing into a fresh output directory.

Stages (none retried): plan (verifies the selected inputs) -> create fresh ROOT/NAME -> chunked mix into `stem.wav.partial`
-> promote to `stem.wav` -> read the output back with the strict WAV reader and hash it -> re-hash the inputs -> receipt.

* Integer arithmetic only. Per-event sample = round-half-away-from-zero(sample * num / den); channel rules are exact integer
  maps; events are summed in Python ints; there is no normalization, resampling, stretching or dithering.
* Clipping (any summed sample outside int16): policy REJECT (default) stops at the first clipped chunk and keeps the partial file;
  policy CLAMP saturates and reports every clipped sample count.
* Work is done in chunks of `limits.chunk_samples` output samples; cancellation and the runtime bound are checked before each chunk.
* Statuses are honest outcomes, not quality verdicts. `quality_evaluated` is always false: nothing here judges audio quality.
"""

from __future__ import annotations

import os
import time

from .. import finalize, receipt as rc
from ..errors import Issue, UnsafePath
from ..tools import sha256_file
from . import plan as planmod
from . import wav
from .limits import AudioLimits
from .plan import AudioPlan, prepare_plan

RECEIPT_SCHEMA = "video_core.audio_receipt/1"
S_VERIFIED = "MIXED_VERIFIED"
S_CLAMPED = "MIXED_WITH_CLAMPING"
S_CLIP_REJECTED = "CLIPPING_REJECTED"
S_TIMEOUT = "TIMEOUT"
S_CANCELLED = "CANCELLED"
S_INTERRUPTED = "INTERRUPTED"
S_MISMATCH = "READBACK_MISMATCH"
S_UNKNOWN = "UNKNOWN"
S_HOLD = "HOLD"
NOTE = ("Structural readback only (RIFF/WAV fields, sample count, peaks, hashes). No audio, speech or creative quality was evaluated; "
        "a verified stem is not a quality or acceptance verdict.")
INT16_MIN, INT16_MAX = -32768, 32767


def scale(v: int, num: int, den: int) -> int:
    """round-half-away-from-zero(v * num / den) with integers only."""
    if num == den:
        return v
    q, r = divmod(abs(v) * num, den)
    if 2 * r >= den:
        q += 1
    return q if v >= 0 else -q


def _avg(a: int, b: int) -> int:
    s = a + b
    q, r = divmod(abs(s), 2)
    q += r  # exact half rounds away from zero
    return q if s >= 0 else -q


def mono_frames(pcm, src_ch: int, rule: str) -> list:
    """Source interleaved int16 -> list of per-frame values for mono output, or (l, r) tuples for stereo output."""
    if rule == "COPY":
        return list(pcm) if src_ch == 1 else [(pcm[i], pcm[i + 1]) for i in range(0, len(pcm), 2)]
    if rule == "MONO_TO_STEREO_DUPLICATE":
        return [(v, v) for v in pcm]
    if rule == "STEREO_TO_MONO_LEFT":
        return [pcm[i] for i in range(0, len(pcm), 2)]
    if rule == "STEREO_TO_MONO_RIGHT":
        return [pcm[i + 1] for i in range(0, len(pcm), 2)]
    if rule == "STEREO_TO_MONO_AVERAGE":
        return [_avg(pcm[i], pcm[i + 1]) for i in range(0, len(pcm), 2)]
    raise ValueError(rule)


def inspect_stem(path: str, limits: AudioLimits = AudioLimits()) -> dict:
    """Strict readback of a WAV file with the same parser used for inputs, plus its hash."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        info = wav.parse_wav(fh, size, limits, ptr="stem")
    return {**info.to_dict(), "sha256": sha256_file(path, size)}


def mix_plan(plan: AudioPlan, *, limits: AudioLimits, run_dir: str, cancel=None, clock=time.monotonic) -> tuple[dict, dict]:
    """Chunked mix of a READY plan into run_dir/stem.wav.partial. Returns (stats, outcome) where outcome has 'stop' = None or a status."""
    m = plan.manifest
    oc, total = m.channels, plan.total_samples
    partial = os.path.join(run_dir, planmod.STEM_PARTIAL)
    stats = {"samples_written": 0, "chunks": 0, "chunk_samples": limits.chunk_samples, "peak_before_clip": 0, "peak_output": 0,
             "peak_output_per_channel": [0] * oc, "clipped_samples": 0, "first_clipped_sample": None, "work_sample_channels_done": 0}
    deadline = clock() + limits.max_runtime_seconds
    stop = None
    with open(partial, "xb") as out:
        out.write(wav.header_bytes(oc, total))
        pos = 0
        while pos < total:
            if cancel is not None and cancel():
                stop = S_CANCELLED
                break
            if clock() > deadline:
                stop = S_TIMEOUT
                break
            n = min(limits.chunk_samples, total - pos)
            acc = [0] * (n * oc)
            for ev in plan.mix_events:
                lo, hi = max(ev.sample_start, pos), min(ev.sample_end, pos + n)
                if hi <= lo:
                    continue
                with open(ev.abspath, "rb") as fh:
                    info = wav.parse_wav(fh, os.fstat(fh.fileno()).st_size, limits, ptr=ev.asset_id)
                    pcm = wav.read_samples(fh, info, ev.source_in + (lo - ev.sample_start), hi - lo)
                frames = mono_frames(pcm, ev.src_channels, ev.rule)
                base = (lo - pos) * oc
                gn, gd = ev.gain_num, ev.gain_den
                if oc == 1:
                    if gn == gd:
                        for k, v in enumerate(frames):
                            acc[base + k] += v
                    else:
                        for k, v in enumerate(frames):
                            acc[base + k] += scale(v, gn, gd)
                else:
                    for k, (l, r) in enumerate(frames):
                        acc[base + 2 * k] += scale(l, gn, gd)
                        acc[base + 2 * k + 1] += scale(r, gn, gd)
                stats["work_sample_channels_done"] += (hi - lo) * oc
            mx, mn = (max(acc), min(acc)) if acc else (0, 0)
            stats["peak_before_clip"] = max(stats["peak_before_clip"], mx, -mn)
            if mx > INT16_MAX or mn < INT16_MIN:
                clipped = sum(1 for v in acc if v > INT16_MAX or v < INT16_MIN)
                if stats["first_clipped_sample"] is None:
                    first = next(i for i, v in enumerate(acc) if v > INT16_MAX or v < INT16_MIN)
                    stats["first_clipped_sample"] = pos + first // oc
                stats["clipped_samples"] += clipped
                if m.clipping_policy == "REJECT":
                    stop = S_CLIP_REJECTED
                    break
                acc = [INT16_MAX if v > INT16_MAX else INT16_MIN if v < INT16_MIN else v for v in acc]
            for ch in range(oc):
                chan = acc[ch::oc]
                stats["peak_output_per_channel"][ch] = max(stats["peak_output_per_channel"][ch], max(chan), -min(chan))
            out.write(wav.pack_samples(acc))
            pos += n
            stats["samples_written"] = pos
            stats["chunks"] += 1
        out.flush()
        os.fsync(out.fileno())
    stats["peak_output"] = max(stats["peak_output_per_channel"])
    return stats, {"stop": stop}


def mix(manifest_bytes: bytes, *, limits: AudioLimits = AudioLimits(), asset_root: str | None, output_root: str, output_name: str,
        episode_bytes: bytes | None = None, cancel=None, clock=time.monotonic) -> dict:
    """Plan, then (if READY) mix into the fresh directory output_root/output_name.

    Raises (before anything is created) for rejected input. Returns a HOLD result without creating anything when the plan is HOLD.
    Otherwise returns the receipt dict, also written to receipt.json.
    """
    plan = prepare_plan(manifest_bytes, limits=limits, asset_root=asset_root, output_root=output_root, output_name=output_name, episode_bytes=episode_bytes)
    if plan.status != "READY":
        return {"schema": RECEIPT_SCHEMA, "status": S_HOLD, "written": False, "quality_evaluated": False, "note": NOTE, "plan_sha256": plan.sha256,
                "hold": plan.hold, "unresolved": plan.canonical["unresolved"],
                "note_hold": "No output directory or audio was created: HOLD is not silence and nothing was repaired or guessed."}
    run_dir = os.path.join(os.path.realpath(output_root), output_name)
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        raise UnsafePath("output exists", [Issue("output_exists", "output_name", "output location already exists; overwriting is refused")]) from None
    receipt: dict = {
        "schema": RECEIPT_SCHEMA, "status": rc.STATUS_IN_PROGRESS, "written": True, "stage": "setup", "quality_evaluated": False, "note": NOTE,
        "started_utc": rc.utc_now(), "plan_sha256": plan.sha256, "platform": rc.platform_facts(), "limits": limits.to_dict(),
        "input": {"manifest_sha256": plan.manifest_sha256, "manifest_bytes": plan.manifest_bytes,
                  "assets": [{"id": k, "path": plan.asset_files[k].rel, "bytes": plan.asset_files[k].size, "sha256_before": v} for k, v in plan.asset_hashes.items()]},
        "caller_assertions": planmod.ASSERTION_NOTE, "unresolved_report": plan.canonical["unresolved"], "warnings": plan.canonical["warnings"],
        "outputs": [], "mix": None, "readback": None,
    }
    receipt_path = os.path.join(run_dir, planmod.RECEIPT_NAME)
    rc.write_new(receipt_path, rc.dumps(receipt))  # a stale IN_PROGRESS receipt means UNKNOWN
    try:
        _run(plan, limits, run_dir, receipt_path, receipt, cancel, clock)
    except KeyboardInterrupt:
        receipt["status"] = S_INTERRUPTED
        _finalize(plan, limits, run_dir, receipt, abnormal=True)
    except Exception as exc:  # keep partial state, say so, never retry
        receipt["status"] = S_UNKNOWN
        receipt["internal_error"] = {"type": type(exc).__name__, "message": str(exc)[:300], "stage": receipt.get("stage")}
        _finalize(plan, limits, run_dir, receipt, abnormal=True)  # best effort: what is actually on disk + input hashes (null and a reason if unreadable)
    receipt["finished_utc"] = rc.utc_now()
    rc.replace_atomic(receipt_path, rc.dumps(receipt))
    return receipt


def _run(plan: AudioPlan, limits, run_dir, receipt_path, receipt, cancel, clock) -> None:
    rc.write_new(os.path.join(run_dir, planmod.PLAN_NAME), plan.canonical_json)
    receipt["stage"] = "mix"
    rc.replace_atomic(receipt_path, rc.dumps(receipt))
    t0 = clock()
    stats, outcome = mix_plan(plan, limits=limits, run_dir=run_dir, cancel=cancel, clock=clock)
    stats["wall_seconds"] = round(clock() - t0, 3)
    receipt["mix"] = stats
    partial, final = os.path.join(run_dir, planmod.STEM_PARTIAL), os.path.join(run_dir, planmod.STEM_NAME)
    status = outcome["stop"]
    if status is None and stats["samples_written"] != plan.total_samples:
        status = S_UNKNOWN
        receipt["unknown_reason"] = "mix finished without writing the planned number of samples"
    if status is None:
        os.rename(partial, final)
        receipt["stage"] = "readback"
        try:
            rb = inspect_stem(final, limits)
        except Exception as exc:  # the strict parser refusing our own output is a mismatch, not a success
            rb = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        exp = plan.canonical["output"]
        checks = []
        if "error" not in rb:
            for name, expected, observed in (("channels", plan.manifest.channels, rb["channels"]), ("sample_rate", 48000, rb["sample_rate"]), ("bits", 16, rb["bits"]),
                                             ("sample_count", exp["samples"], rb["sample_count"]), ("file_bytes", exp["bytes"], rb["file_bytes"])):
                checks.append({"name": name, "expected": expected, "observed": observed, "ok": expected == observed})
        receipt["readback"] = {"wav": rb, "checks": checks, "all_ok": bool(checks) and all(c["ok"] for c in checks)}
        if not receipt["readback"]["all_ok"]:
            status = S_MISMATCH
        else:
            status = S_CLAMPED if stats["clipped_samples"] else S_VERIFIED
    unchanged = _finalize(plan, limits, run_dir, receipt)
    if not unchanged and status in (S_VERIFIED, S_CLAMPED, S_MISMATCH):
        receipt["unknown_reason"] = "an input file changed (or became unreadable) while mixing"
        status = S_UNKNOWN
    receipt["status"] = status
    receipt["stage"] = "complete" if status in (S_VERIFIED, S_CLAMPED, S_MISMATCH) else receipt["stage"]


_KNOWN_OUTPUTS = ((planmod.STEM_NAME, "stem", False), (planmod.STEM_PARTIAL, "stem_partial", True), (planmod.PLAN_NAME, "plan", False))


def _finalize(plan: AudioPlan, limits, run_dir: str, receipt: dict, *, abnormal: bool = False) -> bool:
    """Best-effort and never raises (Exception): inventory the files that actually exist (partial flags, hashes) and re-hash the inputs (sha256_after null plus a reason when
    unreadable). Used on the normal path AND on every error/interrupt path. Returns True only if every input was re-read and is unchanged."""
    try:
        receipt["outputs"] = finalize.inventory(run_dir, _KNOWN_OUTPUTS, lambda size: size, include_unrecognised=abnormal)
    except Exception as exc:
        finalize.note_error(receipt, "inventory", exc)
    unchanged = False
    try:
        inp = receipt.get("input")
        if not isinstance(inp, dict):
            inp = receipt["input"] = {"manifest_sha256": plan.manifest_sha256, "manifest_bytes": plan.manifest_bytes}
        if not isinstance(inp.get("assets"), list):
            inp["assets"] = [{"id": k, "path": plan.asset_files[k].rel, "bytes": plan.asset_files[k].size, "sha256_before": v} for k, v in plan.asset_hashes.items()]
        unchanged = finalize.rehash_inputs(inp["assets"], lambda e: sha256_file(plan.asset_files[e["id"]].abspath, limits.max_source_bytes))
        inp["inputs_unchanged"] = unchanged
    except Exception as exc:
        finalize.note_error(receipt, "input_rehash", exc)
    return unchanged
