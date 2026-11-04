"""Audio plan: pure, side-effect-free analysis of a manifest plus read-only verification of the selected local assets.

`prepare_plan` creates no file and launches no process (this package never launches processes at all). It reads the
manifest bytes it is given, and -- only for assets bound by a SELECTED `USE` event -- stats, parses and hashes the WAV.

Frame -> sample policy (one rule, applied to every absolute boundary, so nothing accumulates):
    sample(frame) = floor( (2 * frame * fps_d * 48000 + fps_n) / (2 * fps_n) )      # exact integers, round half up
An event spans [sample(start_frame), sample(end_frame)). That length is never adjusted: for a USE event it must equal
source_out_sample - source_in_sample exactly (no trimming, padding or stretching is invented).

Selection: only events listed in scope.event_ids are consumed. Everything else stays in the report. A selected event whose
state is MISSING / UNKNOWN / PROXY yields status HOLD (no output), never silence. OMIT and INTENTIONAL_SILENCE are explicit
caller states. Rejections (bad manifest, unsafe path, bad hash, bad WAV, conflicting ranges, limits) raise ValidationFailed /
UnsafePath; HOLD is for incomplete-but-well-formed input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from fractions import Fraction

from ..episode import load_episode
from ..errors import Issue, UnsafePath, ValidationFailed
from ..paths import check_output_location, resolve_asset
from ..tools import sha256_file
from . import wav
from .limits import SAMPLE_RATE, AudioLimits
from .manifest import CHANNEL_RULES, UNRESOLVED, AudioManifest, load_manifest

PLAN_SCHEMA = "video_core.audio_plan/1"
STEM_NAME = "stem.wav"
STEM_PARTIAL = "stem.wav.partial"
PLAN_NAME = "plan.canonical.json"
RECEIPT_NAME = "receipt.json"
MAX_REPORTED_GAPS = 100

ASSERTION_NOTE = ("source_id, role, decision_ref and provenance_ref are opaque caller assertions, preserved verbatim; they are not "
                  "authenticated and are not evidence of human adoption or creative approval")


def frame_to_sample(frame: int, fps_n: int, fps_d: int, rate: int = SAMPLE_RATE) -> int:
    return (2 * frame * fps_d * rate + fps_n) // (2 * fps_n)


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class MixEvent:
    event_id: str
    sample_start: int
    sample_end: int
    asset_id: str
    abspath: str
    source_in: int
    gain_num: int
    gain_den: int
    rule: str
    src_channels: int


@dataclass
class AudioPlan:
    manifest: AudioManifest
    manifest_sha256: str
    manifest_bytes: int
    canonical: dict
    canonical_json: bytes
    sha256: str
    status: str  # READY | HOLD
    hold: list
    total_samples: int
    mix_events: list = field(default_factory=list)
    asset_hashes: dict = field(default_factory=dict)  # asset id -> sha256 verified at plan time
    asset_files: dict = field(default_factory=dict)  # asset id -> ResolvedFile
    observations: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"mode": "plan", "status": self.status, "plan_sha256": self.sha256, "plan": self.canonical, "observations": self.observations}


def _reject(code: str, ptr: str, msg: str, cls=ValidationFailed):
    raise cls(msg, [Issue(code, ptr, msg)])


def _merge(intervals):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def analyze(m: AudioManifest, limits: AudioLimits) -> dict:  # noqa: C901
    """Manifest-only analysis (no files). Raises on conflicts; returns a dict used by prepare_plan."""
    total_samples = frame_to_sample(m.total_frames, m.fps_n, m.fps_d)
    if total_samples > limits.max_output_samples:
        _reject("limit_output_samples", "/timeline/total_frames", f"timeline is {total_samples} samples; limit is {limits.max_output_samples}")
    scope = set(m.scope)
    infos, hold = [], []
    for idx, e in enumerate(m.events):
        sel = e.id in scope
        info = {"index": idx, "event": e, "selected": sel, "s0": None, "s1": None}
        if e.start_frame is not None:
            info["s0"] = frame_to_sample(e.start_frame, m.fps_n, m.fps_d)
            info["s1"] = frame_to_sample(e.end_frame, m.fps_n, m.fps_d)
            # Only an event that is actually consumed (or that decides HOLD) needs a non-empty span. At frame rates above 48 kHz a
            # one-frame range can legitimately span zero samples; AUDIO_SPEC says unselected events are never verified and never block a
            # valid selected scope, and OMIT consumes nothing. Selected USE / INTENTIONAL_SILENCE (and selected unresolved) events
            # with an empty span are still rejected, exactly as before.
            if info["s1"] <= info["s0"] and sel and e.state != "OMIT":
                _reject("event_zero_samples", f"/events/{idx}", "event spans less than one sample at this frame rate")
        if e.state in UNRESOLVED:
            info["disposition"] = "UNRESOLVED_SELECTED" if sel else "UNRESOLVED_NOT_SELECTED"
            if sel:
                hold.append({"code": "SELECTED_UNRESOLVED", "event_id": e.id, "state": e.state,
                             "detail": f"selected event is {e.state}: no audio is produced (not silence)"})
        elif not sel:
            info["disposition"] = "NOT_SELECTED"
        else:
            info["disposition"] = {"USE": "MIX", "INTENTIONAL_SILENCE": "SILENCE", "OMIT": "OMITTED"}[e.state]
        if sel and e.state == "USE":
            length = info["s1"] - info["s0"]
            if e.source_out - e.source_in != length:
                _reject("range_length_mismatch", f"/events/{idx}",
                        f"source range is {e.source_out - e.source_in} samples but the timeline range [{e.start_frame},{e.end_frame}) is {length} samples "
                        f"at this frame rate; lengths are never adjusted")
        infos.append(info)

    sel_use = [i for i in infos if i["selected"] and i["event"].state == "USE"]
    sel_sil = [i for i in infos if i["selected"] and i["event"].state == "INTENTIONAL_SILENCE"]
    # silence may never overlap anything consumed
    ranges = sorted([(i["s0"], i["s1"], i) for i in sel_use + sel_sil], key=lambda t: (t[0], t[1], t[2]["index"]))
    for a in range(len(ranges)):
        for b in range(a + 1, len(ranges)):
            if ranges[b][0] >= ranges[a][1]:
                break
            ia, ib = ranges[a][2]["event"], ranges[b][2]["event"]
            if ia.state == "INTENTIONAL_SILENCE" or ib.state == "INTENTIONAL_SILENCE":
                _reject("silence_overlap", f"/events/{ranges[b][2]['index']}", f"events {ia.id!r} and {ib.id!r} overlap and one is INTENTIONAL_SILENCE")
            if m.overlap_policy == "REJECT":
                _reject("event_overlap", f"/events/{ranges[b][2]['index']}", f"events {ia.id!r} and {ib.id!r} overlap and policy.overlap is REJECT")
    marks = sorted([(i["s0"], 1) for i in sel_use] + [(i["s1"], -1) for i in sel_use])
    cur = peak = 0
    for _pos, d in marks:  # at equal positions -1 sorts first, so touching events are not counted as concurrent
        cur += d
        peak = max(peak, cur)
    if peak > limits.max_concurrent:
        _reject("limit_concurrent", "/events", f"{peak} selected USE events overlap at once; limit is {limits.max_concurrent}")
    work = sum((i["s1"] - i["s0"]) * m.channels for i in sel_use)
    if work > limits.max_mix_work:
        _reject("limit_mix_work", "/events", f"mixing work {work} sample-channels exceeds the limit {limits.max_mix_work}")

    covered = _merge([(i["s0"], i["s1"]) for i in sel_use + sel_sil])
    gaps, pos = [], 0
    for s, e in covered:
        if s > pos:
            gaps.append((pos, s))
        pos = max(pos, e)
    if pos < total_samples:
        gaps.append((pos, total_samples))
    covered_samples = total_samples - sum(e - s for s, e in gaps)
    if gaps and m.gap_policy == "REQUIRE_FULL_COVERAGE":
        hold.append({"code": "INCOMPLETE_SELECTED_INPUT", "event_id": None, "state": None,
                     "detail": f"policy.gap is REQUIRE_FULL_COVERAGE but {len(gaps)} gap(s) / {total_samples - covered_samples} sample(s) are not covered by selected USE or INTENTIONAL_SILENCE events"})
    warnings = []
    if not sel_use:
        warnings.append({"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "no selected USE event: a mix would contain no source audio"})
    if gaps and m.gap_policy == "ALLOW_ZERO_FILL":
        warnings.append({"code": "ZERO_FILL_APPLIED", "detail": f"{len(gaps)} gap(s) will be zero-filled by explicit policy"})
    return {"total_samples": total_samples, "infos": infos, "hold": hold, "gaps": gaps, "covered_samples": covered_samples,
            "warnings": warnings, "peak_concurrent": peak, "work": work}


def _channel_rule(e, src_ch: int, out_ch: int, idx: int) -> str:
    if src_ch == out_ch:
        allowed = ("COPY",)
    elif src_ch == 1:
        allowed = ("MONO_TO_STEREO_DUPLICATE",)
    else:
        allowed = ("STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE")
    if e.channel_rule is None:
        if src_ch != out_ch:
            _reject("channel_rule_required", f"/events/{idx}/channel_rule",
                    f"source has {src_ch} channel(s) and output {out_ch}: an explicit channel_rule from {list(allowed)} is required")
        return "COPY"
    if e.channel_rule not in allowed:
        _reject("channel_rule_incompatible", f"/events/{idx}/channel_rule",
                f"{e.channel_rule} cannot map {src_ch} channel(s) to {out_ch}; compatible: {list(allowed)}")
    return e.channel_rule


def cross_check_episode(m: AudioManifest, episode_bytes: bytes) -> None:
    """Optional: the manifest timeline must equal the video episode's fps and frame count (and its sha256 if asserted)."""
    ep = load_episode(episode_bytes)
    if Fraction(m.fps_n, m.fps_d) != ep.fps:
        _reject("episode_fps_mismatch", "/timeline/fps", f"manifest fps {m.fps_n}/{m.fps_d} differs from the episode's {ep.fps_n}/{ep.fps_d}")
    if m.total_frames != ep.total_frames:
        _reject("episode_frames_mismatch", "/timeline/total_frames", f"manifest has {m.total_frames} frames, the episode {ep.total_frames}")
    if m.episode_sha256 is not None and m.episode_sha256 != hashlib.sha256(episode_bytes).hexdigest():
        _reject("episode_sha256_mismatch", "/timeline/episode_sha256", "episode_sha256 does not match the supplied episode bytes")


def prepare_plan(manifest_bytes: bytes, *, limits: AudioLimits = AudioLimits(), asset_root: str | None = None,
                 output_root: str | None = None, output_name: str | None = None, episode_bytes: bytes | None = None) -> AudioPlan:
    m = load_manifest(manifest_bytes, limits)
    msha = hashlib.sha256(manifest_bytes).hexdigest()
    if episode_bytes is not None:
        cross_check_episode(m, episode_bytes)

    a = analyze(m, limits)
    infos, hold = a["infos"], list(a["hold"])
    assets_by_id = {x.id: x for x in m.assets}
    used = []  # asset ids bound by selected USE events, first-use order
    for i in infos:
        if i["selected"] and i["event"].state == "USE" and i["event"].asset not in used:
            used.append(i["event"].asset)

    location = None
    if output_root is not None or output_name is not None:
        if output_root is None or output_name is None:
            _reject("output_incomplete", "output_root", "output_root and output_name must be given together")
        location = check_output_location(output_root, output_name, asset_root)

    resolved, facts, hashes, total_bytes = {}, {}, {}, 0
    if used:
        if asset_root is None:
            _reject("asset_root_required", "asset_root", "selected USE events need a caller-selected asset root")
        for aid in used:
            asset = assets_by_id[aid]
            ptr = f"/assets/{[x.id for x in m.assets].index(aid)}/path"
            try:
                rf = resolve_asset(asset_root, asset.path, ptr=ptr, allowed_ext=(".wav",), max_bytes=limits.max_source_bytes)
            except UnsafePath as exc:
                if [i.code for i in exc.issues] == ["asset_missing"]:
                    hold.append({"code": "ASSET_FILE_MISSING", "event_id": None, "state": None, "asset": aid,
                                 "detail": f"asset {aid!r}: the local file does not exist; selected events using it cannot be mixed"})
                    continue
                raise
            total_bytes += rf.size
            if total_bytes > limits.max_total_source_bytes:
                _reject("limit_total_source_bytes", "/assets", f"selected assets total {total_bytes} bytes; limit is {limits.max_total_source_bytes}", UnsafePath)
            with open(rf.abspath, "rb") as fh:
                info = wav.parse_wav(fh, rf.size, limits, ptr=ptr)
            try:
                actual = sha256_file(rf.abspath, limits.max_source_bytes)
            except (OSError, ValueError) as exc:
                _reject("asset_unreadable", ptr, f"asset {aid!r} could not be hashed: {type(exc).__name__}", UnsafePath)
            if actual != asset.sha256:
                _reject("asset_sha256_mismatch", ptr, f"asset {aid!r}: caller-provided sha256 {asset.sha256} but the file hashes to {actual}")
            resolved[aid], hashes[aid] = rf, actual
            facts[aid] = {"bytes": rf.size, "sample_count": info.sample_count, "channels": info.channels}

    mix_events = []
    for i in infos:
        e = i["event"]
        if not (i["selected"] and e.state == "USE") or e.asset not in facts:
            continue
        f = facts[e.asset]
        if e.source_out > f["sample_count"]:
            _reject("source_range_out_of_bounds", f"/events/{i['index']}",
                    f"event {e.id!r} reads source samples [{e.source_in},{e.source_out}) but asset {e.asset!r} has only {f['sample_count']} samples")
        rule = _channel_rule(e, f["channels"], m.channels, i["index"])
        mix_events.append(MixEvent(e.id, i["s0"], i["s1"], e.asset, resolved[e.asset].abspath, e.source_in, e.gain_num, e.gain_den, rule, f["channels"]))

    status = "HOLD" if hold else "READY"
    total_samples = a["total_samples"]
    canon_assets = []
    for x in m.assets:
        d = {"id": x.id, "path": x.path, "sha256": x.sha256}
        if x.id in facts:
            d.update(verified=True, bytes=facts[x.id]["bytes"], sample_count=facts[x.id]["sample_count"], channels=facts[x.id]["channels"])
        else:
            d.update(verified=False, reason=("the file is missing" if x.id in used else "not verified: no selected USE event is bound to it"))
        canon_assets.append(d)
    canon_events, unresolved = [], []
    for i in infos:
        e = i["event"]
        d = {"id": e.id, "source_id": e.source_id, "state": e.state, "selected": i["selected"], "disposition": i["disposition"]}
        for k in ("role", "decision_ref", "provenance_ref"):
            if getattr(e, k) is not None:
                d[k] = getattr(e, k)
        if e.start_frame is not None:
            d.update(start_frame=e.start_frame, end_frame=e.end_frame, sample_start=i["s0"], sample_end=i["s1"], samples=i["s1"] - i["s0"])
        if e.state == "USE":
            d.update(asset=e.asset, source_in_sample=e.source_in, source_out_sample=e.source_out, gain={"num": e.gain_num, "den": e.gain_den})
            me = next((x for x in mix_events if x.event_id == e.id), None)
            if me:
                d["channel_rule"] = me.rule
        if e.state in UNRESOLVED:
            unresolved.append({"event_id": e.id, "source_id": e.source_id, "state": e.state, "selected": i["selected"]})
        canon_events.append(d)
    fps = Fraction(m.fps_n, m.fps_d)
    canonical = {
        "schema": PLAN_SCHEMA,
        "timeline": {"fps": {"n": m.fps_n, "d": m.fps_d, "rational": f"{fps.numerator}/{fps.denominator}"}, "total_frames": m.total_frames,
                     "total_samples": total_samples,
                     "frame_to_sample": "floor((2*frame*fps_d*48000 + fps_n) / (2*fps_n)); exact integers, round half up, applied to each absolute boundary"},
        "output": {"file": STEM_NAME, "sample_rate": SAMPLE_RATE, "channels": m.channels, "bits": 16, "samples": total_samples,
                   "bytes": wav.HEADER_BYTES + total_samples * m.channels * 2},
        "policy": {"gap": m.gap_policy, "overlap": m.overlap_policy, "clipping": m.clipping_policy,
                   "gain": "integer rational num/den; per-event sample = round-half-away-from-zero(sample*num/den); no automatic normalization",
                   "resampling": "none", "stretching": "none"},
        "scope": list(m.scope),
        "status": status,
        "hold": hold,
        "warnings": a["warnings"],
        "assets": canon_assets,
        "events": canon_events,
        "unresolved": unresolved,
        "coverage": {"covered_samples": a["covered_samples"], "gap_count": len(a["gaps"]),
                     "gaps": [{"sample_start": s, "sample_end": e} for s, e in a["gaps"][:MAX_REPORTED_GAPS]], "gaps_truncated": len(a["gaps"]) > MAX_REPORTED_GAPS},
        "mix": {"events": len(mix_events), "work_sample_channels": a["work"], "peak_concurrent": a["peak_concurrent"]},
        "caller_assertions": ASSERTION_NOTE,
        "mux_interface": {"note": "no mux is performed by this component; a later, separately validated mux stage may consume the stem",
                          "stem": STEM_NAME, "container": "RIFF/WAVE PCM", "sample_rate": SAMPLE_RATE, "channels": m.channels, "bits": 16,
                          "start_offset_samples": 0, "duration_samples": total_samples, "timeline_fps": f"{fps.numerator}/{fps.denominator}",
                          "timeline_frames": m.total_frames, "duration_seconds": f"{Fraction(total_samples, SAMPLE_RATE).numerator}/{Fraction(total_samples, SAMPLE_RATE).denominator}"},
    }
    cj = canonical_bytes(canonical)
    plan = AudioPlan(manifest=m, manifest_sha256=msha, manifest_bytes=len(manifest_bytes), canonical=canonical, canonical_json=cj,
                     sha256=hashlib.sha256(cj).hexdigest(), status=status, hold=hold, total_samples=total_samples, mix_events=mix_events,
                     asset_hashes=hashes, asset_files=resolved)
    plan.observations = {
        "input": {"manifest_sha256": msha, "manifest_bytes": len(manifest_bytes)},
        "assets_verified": {k: {"sha256": hashes[k], **facts[k]} for k in hashes},
        "output": None if location is None else {"root": location.root, "name": location.name, "fresh": True},
        "limits": limits.to_dict(),
        "side_effects": "none (no file created, no process launched)",
    }
    return plan
