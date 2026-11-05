"""Synthetic, public fixture values for the caller-admission reference candidate. No story, voice, user path, media or real file exists behind any value here.

The packets are PRODUCER-SHAPED: key names, nesting and value types follow what the core library (c7da5ac9f6444b954958ce9f587de2249fe06754) emits for the audio plan
(`video_core.audio_plan/1`), the audio receipt (`video_core.audio_receipt/1`) and the video render receipt (`video_core.receipt/1`), reduced to a representative subset plus a few
keys the candidate ignores (so ignoring is exercised). The shapes were read from the producer code; no producer was run and no file was created to make them.
"""

from __future__ import annotations

import copy
import hashlib

import caller_admission as ca


def H(label: str) -> str:
    return hashlib.sha256(("synthetic-public-value:" + label).encode("ascii")).hexdigest()


PINS = {"manifest_sha256": H("manifest"), "plan_sha256": H("plan"), "receipt_sha256": H("audio-receipt"), "stem_sha256": H("stem"), "video_sha256": H("video")}
IDENT = {"job_id": "job-001", "work_id": "work-A", "shot_id": "shot-07", "attempt_id": "attempt-0001", "selection_snapshot_ref": "selsnap-A"}
STEM_BYTES = 44 + 9600 * 2  # default geometry (mono, 6 frames at 30/1)

DEFAULT_ROWS = (
    {"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"},
    {"id": "e2", "state": "INTENTIONAL_SILENCE", "selected": True, "decision": "dec-silence-e2"},
)

_DISP = {"USE": "MIX", "INTENTIONAL_SILENCE": "SILENCE", "OMIT": "OMITTED"}


def _disposition(row) -> str:
    if row["state"] in ca.UNRESOLVED_STATES:
        return "UNRESOLVED_SELECTED" if row["selected"] else "UNRESOLVED_NOT_SELECTED"
    return _DISP[row["state"]] if row["selected"] else "NOT_SELECTED"


def world(rows=DEFAULT_ROWS, *, warnings=(), gap_count=0, plan_status=None, plan_exit=None, hold=(), gap_policy="REQUIRE_FULL_COVERAGE", total_frames=6, total_samples=9600,
          pins=None) -> tuple[dict, dict]:
    """(expected, observed) that agree with each other completely (a conforming world) unless parameters say otherwise. Fresh objects on every call."""
    pins = dict(PINS if pins is None else pins)
    rows = [dict(r) for r in rows]
    status = plan_status or ("HOLD" if (hold or any(r["selected"] and r["state"] in ca.UNRESOLVED_STATES for r in rows)) else "READY")
    exit_plan = plan_exit if plan_exit is not None else (0 if status == "READY" else 5)

    exp_rows, events, unresolved = [], [], []
    for r in rows:
        er = {"row_id": r["id"], "selected": r["selected"], "state": r["state"]}
        ev = {"id": r["id"], "source_id": "src-" + r["id"], "state": r["state"], "selected": r["selected"], "disposition": _disposition(r)}
        if "binding" in r:
            er["binding_ref"] = r["binding"]
            ev["provenance_ref"] = r["binding"]
        if "decision" in r:
            er["decision_ref"] = r["decision"]
            ev["decision_ref"] = r["decision"]
        if r["state"] == "USE":
            ev.update(asset="a-" + r["id"], source_in_sample=0, source_out_sample=4800, gain={"num": 1, "den": 1}, channel_rule="COPY")
        if r["state"] in ca.UNRESOLVED_STATES:
            unresolved.append({"event_id": r["id"], "source_id": "src-" + r["id"], "state": r["state"], "selected": r["selected"]})
        exp_rows.append(er)
        events.append(ev)
    expected = {"snapshot_ref": "owner-snapshot-A", "identity": dict(IDENT), "pins_source": "owner_record", "pins": dict(pins), "rows": exp_rows}

    gaps = [{"sample_start": total_samples - 100 * (i + 1), "sample_end": total_samples - 100 * i} for i in reversed(range(gap_count))] if gap_count else []
    covered = total_samples - sum(g["sample_end"] - g["sample_start"] for g in gaps)
    plan = {"schema": "video_core.audio_plan/1", "timeline": {"fps": {"n": 30, "d": 1, "rational": "30/1"}, "total_frames": total_frames, "total_samples": total_samples},
            "output": {"file": "stem.wav", "sample_rate": 48000, "channels": 1, "bits": 16, "samples": total_samples, "bytes": 44 + total_samples * 2},
            "policy": {"gap": gap_policy, "overlap": "REJECT", "clipping": "REJECT"}, "scope": [r["id"] for r in rows if r["selected"]], "status": status,
            "hold": [dict(h) for h in hold], "warnings": [dict(w) for w in warnings], "events": events, "unresolved": unresolved,
            "coverage": {"covered_samples": covered, "gap_count": gap_count, "gaps": gaps, "gaps_truncated": False},
            "caller_assertions": "opaque references are caller assertions"}
    plan_stage = {"identity": dict(IDENT), "exit_code": exit_plan,
                  "response": {"mode": "plan", "status": status, "plan_sha256": pins["plan_sha256"], "plan": plan, "observations": {"side_effects": "none"}}}
    stem_bytes = 44 + total_samples * 2
    audio_receipt = {"schema": "video_core.audio_receipt/1", "status": "MIXED_VERIFIED", "written": True, "stage": "complete", "quality_evaluated": False,
                     "plan_sha256": pins["plan_sha256"], "input": {"manifest_sha256": pins["manifest_sha256"], "inputs_unchanged": True, "assets": []},
                     "warnings": [dict(w) for w in warnings], "unresolved_report": [dict(u) for u in unresolved],
                     # the plan output file IS the canonical plan bytes, so its SHA-256 equals plan_sha256 (core: sha256(canonical_json) == plan.sha256)
                     "outputs": [{"name": "stem.wav", "role": "stem", "bytes": stem_bytes, "sha256": pins["stem_sha256"], "partial": False},
                                 {"name": "plan.canonical.json", "role": "plan", "bytes": 100, "sha256": pins["plan_sha256"], "partial": False}],
                     "mix": {"samples_written": total_samples, "clipped_samples": 0},
                     "readback": {"wav": {"sample_rate": 48000, "channels": 1, "bits": 16, "sample_count": total_samples, "file_bytes": stem_bytes},
                                  "checks": [{"name": "channels", "expected": 1, "observed": 1, "ok": True}, {"name": "sample_rate", "expected": 48000, "observed": 48000, "ok": True},
                                             {"name": "bits", "expected": 16, "observed": 16, "ok": True}, {"name": "sample_count", "expected": total_samples, "observed": total_samples, "ok": True},
                                             {"name": "file_bytes", "expected": stem_bytes, "observed": stem_bytes, "ok": True}], "all_ok": True}}
    # video readback: the producer's compare() records these checks; counts' `observed` is the ffprobe string (nb_read_frames), expected the plan's integer
    vchecks = [{"name": "video_stream_count", "expected": 1, "observed": 1, "ok": True}, {"name": "non_video_stream_count", "expected": 0, "observed": 0, "ok": True},
               {"name": "width", "expected": 192, "observed": 192, "ok": True}, {"name": "height", "expected": 108, "observed": 108, "ok": True},
               {"name": "r_frame_rate", "expected": "30/1", "observed": "30/1", "ok": True}, {"name": "avg_frame_rate", "expected": "30/1", "observed": "30/1", "ok": True},
               {"name": "decoded_frame_count", "expected": total_frames, "observed": str(total_frames), "ok": True},
               {"name": "container_frame_count", "expected": total_frames, "observed": str(total_frames), "ok": True},
               {"name": "format_duration", "expected": "0.200000", "observed": "0.200000", "ok": True}, {"name": "video_duration", "expected": "0.200000", "observed": "0.200000", "ok": True},
               {"name": "codec_name", "expected": "h264", "observed": "h264", "ok": True}, {"name": "pix_fmt", "expected": "yuv420p", "observed": "yuv420p", "ok": True}]
    video_receipt = {"schema": "video_core.receipt/1", "status": "READBACK_MATCH", "written": True, "stage": "complete", "quality_evaluated": False, "plan_sha256": H("video-plan"),
                     "input": {"inputs_unchanged": True},
                     "outputs": [{"name": "output.mp4", "role": "video", "bytes": 1889, "sha256": pins["video_sha256"], "partial": False},
                                 {"name": "captions.srt", "role": "srt", "bytes": 10, "sha256": H("srt"), "partial": False},
                                 {"name": "plan.canonical.json", "role": "plan", "bytes": 100, "sha256": H("video-plan"), "partial": False},
                                 {"name": "ffprobe.json", "role": "ffprobe_json", "bytes": 500, "sha256": H("ffprobe-json"), "partial": False}],
                     "readback": {"comparison": {"all_ok": True, "checks": vchecks}}}
    observed = {"stages": {
        "audio_plan": plan_stage,
        "audio_receipt": {"identity": dict(IDENT), "exit_code": 0, "receipt_sha256": pins["receipt_sha256"], "response": audio_receipt},
        "video_receipt": {"identity": dict(IDENT), "exit_code": 0, "response": video_receipt},
        "avmux_manifest_view": {"identity": dict(IDENT), "selection_ref": IDENT["selection_snapshot_ref"], "video_sha256": pins["video_sha256"], "audio_sha256": pins["stem_sha256"]},
    }}
    return expected, observed


def profile(**over) -> dict:
    p = copy.deepcopy(ca.PROFILE_V0_UNREVIEWED)
    p.update(over)
    return p
