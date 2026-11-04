"""ffprobe readback: independent re-measurement of an output file.

This reads container/stream *metadata and a decoded frame count*. It does not and
cannot judge audio, visual or creative quality; receipts say so explicitly.
"""

from __future__ import annotations

import json
from fractions import Fraction

from . import runner, timebase
from .limits import Limits
from .tools import probe_env


def ffprobe_argv(ffprobe: str, media_abs: str, demuxer: str) -> list[str]:
    return [
        ffprobe, "-v", "error", "-hide_banner", "-protocol_whitelist", "file", "-f", demuxer,
        "-count_frames", "-show_format", "-show_streams", "-of", "json", "-i", media_abs,
    ]


def run_probe(ffprobe: str, media_abs: str, demuxer: str, limits: Limits, cwd: str, phase: str = "inspect"):
    """Returns (RunResult, parsed JSON or None, raw stdout bytes)."""
    res = runner.run_bounded(
        ffprobe_argv(ffprobe, media_abs, demuxer), phase=phase, timeout_s=limits.inspect_timeout_seconds,
        stdout_limits=(1024 * 1024, 0), stderr_limits=(limits.max_diag_bytes // 2, limits.max_diag_bytes // 2),
        max_drain_bytes=limits.max_drain_bytes, cwd=cwd, env=probe_env(),
    )
    raw = res.stdout_bytes()
    parsed = None
    if res.ok and res.stdout is not None and not res.stdout.summary()["truncated"]:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
    return res, parsed, raw


def _frac(text):
    try:
        v = timebase.parse_ratio(str(text))
    except ValueError:
        return None
    return v


def summarize(parsed: dict) -> dict:
    """Reduce ffprobe JSON to the fields compared and recorded in receipts."""
    streams = parsed.get("streams") or []
    fmt = parsed.get("format") or {}
    out_streams = []
    for s in streams:
        out_streams.append({k: s.get(k) for k in (
            "index", "codec_type", "codec_name", "profile", "width", "height", "pix_fmt", "r_frame_rate",
            "avg_frame_rate", "time_base", "nb_frames", "nb_read_frames", "duration", "start_time",
            "color_space", "color_range", "sample_aspect_ratio")})
    return {
        "format_name": fmt.get("format_name"),
        "nb_streams": fmt.get("nb_streams"),
        "duration": fmt.get("duration"),
        "size": fmt.get("size"),
        "streams": out_streams,
    }


def compare(summary: dict, *, width: int, height: int, fps: Fraction, total_frames: int,
            codec: str | None = None, pix_fmt: str | None = None) -> dict:
    """Compare a summary against an expectation. Every check is recorded, pass or fail."""
    checks = []

    def check(name, expected, observed, ok):
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": bool(ok)})

    streams = summary.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    others = [s for s in streams if s.get("codec_type") != "video"]
    check("video_stream_count", 1, len(video), len(video) == 1)
    check("non_video_stream_count", 0, len(others), not others)
    v = video[0] if video else {}
    check("width", width, v.get("width"), v.get("width") == width)
    check("height", height, v.get("height"), v.get("height") == height)
    for key in ("r_frame_rate", "avg_frame_rate"):
        observed = _frac(v.get(key))
        check(key, timebase.ratio_str(fps), v.get(key), observed is not None and observed == fps)
    read = v.get("nb_read_frames")
    check("decoded_frame_count", total_frames, read, str(read) == str(total_frames))
    if v.get("nb_frames") not in (None, "N/A"):
        check("container_frame_count", total_frames, v.get("nb_frames"), str(v.get("nb_frames")) == str(total_frames))
    expected_dur = timebase.duration_seconds(total_frames, fps)
    tolerance = Fraction(1) / fps  # one frame
    for label, raw in (("format_duration", summary.get("duration")), ("video_duration", v.get("duration"))):
        observed = _frac(raw) if raw not in (None, "N/A") else None
        check(label, timebase.ratio_str(expected_dur), raw, observed is not None and abs(observed - expected_dur) <= tolerance)
    if codec:
        check("codec_name", codec, v.get("codec_name"), v.get("codec_name") == codec)
    if pix_fmt:
        check("pix_fmt", pix_fmt, v.get("pix_fmt"), v.get("pix_fmt") == pix_fmt)
    return {"all_ok": all(c["ok"] for c in checks), "checks": checks}
