"""Re-inspection of an EXISTING muxed file's audio packet side data. Read-only on the media; never muxes, remuxes, transcodes or regenerates anything.

Produces a NEW correction receipt in a fresh directory (existing evidence and receipts are never touched or overwritten). The receipt pins the media's
SHA-256 (verified before and after), references the original receipt by SHA-256, states `reinspection_only`, and lists, per field, how the corrected
packet records differ from what the original receipt's summary said.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from .. import receipt as rc
from .. import runner, tools
from ..errors import Issue, UnsafePath, ValidationFailed
from ..limits import Limits
from ..paths import check_media_for_inspect, check_output_location
from . import checks as ck

SCHEMA = "video_core.avmux_reinspection/1"
_SHA = re.compile(r"[0-9a-fA-F]{64}")
NOTE = ("Re-inspection only: the media file was read (hashed and probed with ffprobe) and not modified; nothing was muxed, remuxed, re-encoded or regenerated. "
        "This receipt corrects how packet side data is reported; it does not change the original technical verdict and evaluates no quality.")


def _reject(code, ptr, msg, cls=ValidationFailed):
    raise cls(msg, [Issue(code, ptr, msg)])


def _probe(ffprobe, argv_tail, phase, limits, cwd, executions):
    res = runner.run_bounded([ffprobe, "-v", "error", "-hide_banner", "-protocol_whitelist", "file", *argv_tail], phase=phase, timeout_s=limits.inspect_timeout_seconds,
                             stdout_limits=(4 * 1024 * 1024, 0), stderr_limits=(limits.max_diag_bytes // 2, limits.max_diag_bytes // 2), max_drain_bytes=limits.max_drain_bytes,
                             cwd=cwd, env=tools.probe_env())
    executions.append(res)
    runner.raise_if_interrupted(res)
    if not res.ok or res.stdout.summary()["truncated"]:
        return None, b""
    raw = res.stdout_bytes()
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (ValueError, UnicodeDecodeError):
        return None, raw


def _differences(original: dict | None, new: dict) -> list:
    """How the corrected records compare with the original receipt's (old-schema) summary. Never guesses what the old code meant beyond what its fields show."""
    if not original:
        return [{"note": "no original packet summary was supplied or found; nothing to compare"}]
    if original.get("schema") == ck.PACKET_SCHEMA:
        return [{"note": "the original summary already uses the record-based schema; compared fields below are identical in kind", "original_schema": original.get("schema")}]
    out = []
    for f in ck.SKIP_FIELDS:
        old = original.get(f, "<key not present>")
        s = new[f]
        entry = {"field": f, "original_summary_value": old,
                 "corrected": {"reported_in_records": s["reported_in_records"], "absent_in_records": s["absent_in_records"],
                               "observed_zero_in_records": s["observed_zero_in_records"], "nonzero": s["nonzero"]}}
        if old is None and s["reported_in_records"] and not s["nonzero"]:
            entry["explanation"] = (f"the original summary showed null, which conflated 'no non-zero value' with absence; ffprobe actually printed {f} (value 0) in "
                                    f"{s['observed_zero_in_records']} record(s)")
        elif old is None and not s["reported_in_records"]:
            entry["explanation"] = f"consistent: ffprobe printed no {f} in any record"
        elif old is not None and any(n["value"] == old for n in s["nonzero"]):
            entry["explanation"] = "the original value is one of the corrected non-zero records (the original kept only the first non-zero value)"
        else:
            entry["explanation"] = "the original value does not match any corrected record; compare side_data_records manually"
        out.append(entry)
    return out


def reinspect_audio_packets(media_path: str, *, expected_sha256: str, output_root: str, output_name: str, ffprobe: str | None = None,
                            original_receipt_path: str | None = None, limits: Limits = Limits()) -> dict:
    """Re-inspect `media_path` (which must hash to `expected_sha256`). Raises before writing anything on bad input; otherwise writes a fresh `output_root/output_name/`."""
    if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
        _reject("invalid_sha256", "expected_sha256", "expected_sha256 must be 64 hex digits")
    expected = expected_sha256.lower()
    media, demuxer = check_media_for_inspect(media_path)
    location = check_output_location(output_root, output_name, None)
    if os.path.commonpath([os.path.dirname(media), location.run_dir]) == location.run_dir:
        _reject("output_overlaps_media", "output_name", "the output directory must not contain the media", UnsafePath)
    size = os.path.getsize(media)
    before = tools.sha256_file(media, max(limits.max_source_bytes, size))
    if before != expected:
        _reject("media_sha256_mismatch", "media", f"expected sha256 {expected} but the file hashes to {before}; nothing was inspected")

    original = None
    if original_receipt_path:
        rp = os.path.abspath(original_receipt_path)
        if not os.path.isfile(rp) or os.path.islink(rp):
            _reject("original_receipt_unreadable", "original_receipt", "the original receipt is not a regular file", UnsafePath)
        with open(rp, "rb") as fh:
            blob = fh.read(limits.max_input_bytes + 1)
        if len(blob) > limits.max_input_bytes:
            _reject("limit_input_bytes", "original_receipt", "the original receipt is too large")
        try:
            doc = json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _reject("original_receipt_unreadable", "original_receipt", "the original receipt is not valid JSON")
        media_outputs = [o for o in doc.get("outputs", []) if o.get("role") == "media"]
        if len(media_outputs) != 1 or media_outputs[0].get("sha256") != expected:
            _reject("original_receipt_media_mismatch", "original_receipt", "the original receipt does not describe a media output with this sha256")
        original = {"name": os.path.basename(rp), "sha256": tools.sha256_file(rp, limits.max_input_bytes + 1), "status": doc.get("status"), "route": doc.get("route"),
                    "plan_sha256": doc.get("plan_sha256"), "claimed_audio_packets": (doc.get("observations") or {}).get("audio_packets")}

    path, how = tools.locate("ffprobe", ffprobe)
    executions: list = []
    if path is None:
        return {"schema": SCHEMA, "status": rc.STATUS_SKIPPED, "written": False, "reasons": [{"tool": "ffprobe", "reason": f"ffprobe not found (discovery: {how})"}],
                "reinspection_only": True, "note": NOTE}
    rec, runs = tools.probe_version("ffprobe", path, how, limits, os.path.dirname(media))
    executions.extend(runs)
    if rec.problem:
        return {"schema": SCHEMA, "status": rc.STATUS_SKIPPED, "written": False, "reasons": [{"tool": "ffprobe", "reason": rec.problem}], "reinspection_only": True, "note": NOTE}

    cwd = os.path.dirname(media)
    base = ["-f", demuxer, "-of", "json", "-i", media]
    streams, _raw_s = _probe(path, ["-show_streams", "-show_format", *base], "reinspect_streams", limits, cwd, executions)
    packets, raw_p = _probe(path, ["-select_streams", "a:0", "-show_packets", *base], "reinspect_audio_packets", limits, cwd, executions)
    if streams is None or packets is None:
        return {"schema": SCHEMA, "status": "INSPECTION_FAILED", "written": False, "reinspection_only": True, "note": NOTE,
                "executions": [e.to_dict() for e in executions], "reasons": [{"tool": "ffprobe", "reason": "ffprobe did not return usable JSON for the streams or the audio packets"}]}
    after = tools.sha256_file(media, max(limits.max_source_bytes, size))
    summary = ck.packet_summary(packets)
    audio = (ck.streams_of(streams, "audio") or [{}])[0]
    changed = after != before
    os.mkdir(location.run_dir)
    receipt = {
        "schema": SCHEMA, "status": "UNKNOWN" if changed else "REINSPECTED", "written": True, "reinspection_only": True, "regenerated_or_remuxed": False, "quality_evaluated": False,
        "note": NOTE, "created_utc": rc.utc_now(),
        "media": {"name": os.path.basename(media), "bytes": size, "sha256_expected": expected, "sha256_before": before, "sha256_after": after, "unchanged": not changed},
        "original_receipt": original, "audio_stream": {k: audio.get(k) for k in ("codec_name", "profile", "sample_rate", "channels", "start_pts", "start_time", "time_base", "duration_ts", "duration", "nb_frames")},
        "format": {k: (streams.get("format") or {}).get(k) for k in ("format_name", "start_time", "duration", "nb_streams")},
        "audio_packets": summary, "differences_from_original_summary": _differences((original or {}).get("claimed_audio_packets"), summary),
        "tool": rec.to_dict(), "executions": [e.to_dict() for e in executions], "platform": rc.platform_facts(),
        "ffprobe_packets_json_sha256": hashlib.sha256(raw_p).hexdigest(),
    }
    if changed:
        receipt["unknown_reason"] = "the media file changed while it was being inspected"
    rc.write_new(os.path.join(location.run_dir, "ffprobe.packets.json"), raw_p)
    rc.write_new(os.path.join(location.run_dir, "receipt.json"), rc.dumps(receipt))
    return receipt
