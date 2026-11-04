"""Pure comparison logic for avmux: canned ffprobe/hash text in, expected-vs-observed checks out. No I/O, no processes."""

from __future__ import annotations

import json
from fractions import Fraction

from ..timebase import parse_ratio

# check name -> machine-readable rejection code used by the pre-mux preflight
CODES = {
    "video_stream_count": "video_stream_layout", "video_codec_type": "video_stream_layout", "video_codec": "video_codec_mismatch",
    "video_width": "video_size_mismatch", "video_height": "video_size_mismatch", "video_r_frame_rate": "video_fps_mismatch",
    "video_avg_frame_rate": "video_fps_mismatch", "video_frames": "video_frame_count_mismatch", "video_start": "video_start_nonzero",
    "audio_stream_count": "audio_stream_layout", "audio_codec_type": "audio_stream_layout", "audio_codec": "audio_probe_mismatch",
    "audio_sample_rate": "audio_probe_mismatch", "audio_channels": "audio_probe_mismatch", "audio_duration_samples": "audio_probe_mismatch",
    "audio_start": "audio_probe_mismatch",
}


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fr(v):
    if v in (None, "N/A", ""):
        return None
    try:
        return parse_ratio(str(v))
    except ValueError:
        return None


def _chk(out, name, expected, observed, ok):
    out.append({"name": name, "expected": expected, "observed": observed, "ok": bool(ok)})


def streams_of(parsed: dict, kind: str) -> list:
    return [s for s in (parsed.get("streams") or []) if s.get("codec_type") == kind]


def check_video(parsed: dict, *, width: int, height: int, fps: Fraction, frames: int, prefix: str = "video") -> list:
    """The single H.264 video stream: layout, codec, size, rate, decoded frame count, start at 0."""
    out, all_streams = [], parsed.get("streams") or []
    vs = streams_of(parsed, "video")
    _chk(out, f"{prefix}_stream_count", 1, len(vs), len(vs) == 1)
    v = vs[0] if vs else {}
    _chk(out, f"{prefix}_codec", "h264", v.get("codec_name"), v.get("codec_name") == "h264")
    _chk(out, f"{prefix}_width", width, v.get("width"), v.get("width") == width)
    _chk(out, f"{prefix}_height", height, v.get("height"), v.get("height") == height)
    for k in ("r_frame_rate", "avg_frame_rate"):
        o = _fr(v.get(k))
        _chk(out, f"{prefix}_{k}", f"{fps.numerator}/{fps.denominator}", v.get(k), o is not None and o == fps)
    _chk(out, f"{prefix}_frames", frames, v.get("nb_read_frames"), str(v.get("nb_read_frames")) == str(frames))
    sp, st = _num(v.get("start_pts")), _fr(v.get("start_time"))
    fmt_start = _fr((parsed.get("format") or {}).get("start_time"))
    _chk(out, f"{prefix}_start", {"start_pts": 0, "start_time": "0"}, {"start_pts": v.get("start_pts"), "start_time": v.get("start_time"), "format_start_time": (parsed.get("format") or {}).get("start_time")},
         sp == 0 and st == 0 and (fmt_start is None or fmt_start == 0))
    return out


def check_layout_single_video(parsed: dict) -> list:
    out = []
    n = len(parsed.get("streams") or [])
    _chk(out, "video_stream_count", 1, len(streams_of(parsed, "video")), len(streams_of(parsed, "video")) == 1)
    _chk(out, "source_total_streams", 1, n, n == 1)
    return out


def check_audio_source(parsed: dict, *, channels: int, samples: int) -> list:
    """ffprobe's own view of the WAV: one pcm_s16le stream of the declared rate/channels/length, starting at 0."""
    out, a = [], streams_of(parsed, "audio")
    n = len(parsed.get("streams") or [])
    _chk(out, "audio_stream_count", 1, n, n == 1 and len(a) == 1)
    s = a[0] if a else {}
    _chk(out, "audio_codec", "pcm_s16le", s.get("codec_name"), s.get("codec_name") == "pcm_s16le")
    _chk(out, "audio_sample_rate", 48000, s.get("sample_rate"), str(s.get("sample_rate")) == "48000")
    _chk(out, "audio_channels", channels, s.get("channels"), s.get("channels") == channels)
    dts, tb = _num(s.get("duration_ts")), _fr(s.get("time_base"))
    ok = dts is not None and tb is not None and Fraction(dts) * tb == Fraction(samples, 48000)
    _chk(out, "audio_duration_samples", samples, {"duration_ts": s.get("duration_ts"), "time_base": s.get("time_base")}, ok)
    # a plain WAV has no start offset and ffprobe then omits start_pts/start_time (observed, FFmpeg 6.1.1): absent is fine, an explicit non-zero start is not
    sp, st = s.get("start_pts"), _fr(s.get("start_time"))
    ok = (sp in (None, "N/A") or _num(sp) == 0) and (s.get("start_time") in (None, "N/A") or st == 0)
    _chk(out, "audio_start", {"start": "0 or not reported (plain WAV)"}, {"start_pts": sp, "start_time": s.get("start_time")}, ok)
    return out


def check_output_layout(parsed: dict) -> list:
    out, streams = [], parsed.get("streams") or []
    kinds = sorted(s.get("codec_type") for s in streams)
    _chk(out, "output_streams", ["audio", "video"], kinds, kinds == ["audio", "video"])
    return out


def check_output_audio(parsed: dict, *, route: str, channels: int, samples: int) -> tuple[list, dict]:
    """Container-level audio checks of the muxed file. Returns (checks, observations).
    MOV/PCM must start at pts 0 and last exactly `samples`; MP4/AAC start is only bounded by the documented tolerance and the rest is observation."""
    from .plan import AAC_TOLERANCE_SAMPLES

    out, a = [], streams_of(parsed, "audio")
    s = a[0] if a else {}
    want_codec = "pcm_s16le" if route == "MOV_PCM_S16LE" else "aac"
    _chk(out, "output_audio_codec", want_codec, s.get("codec_name"), s.get("codec_name") == want_codec)
    _chk(out, "output_audio_sample_rate", 48000, s.get("sample_rate"), str(s.get("sample_rate")) == "48000")
    _chk(out, "output_audio_channels", channels, s.get("channels"), s.get("channels") == channels)
    sp, st, tb, dts = _num(s.get("start_pts")), _fr(s.get("start_time")), _fr(s.get("time_base")), _num(s.get("duration_ts"))
    obs = {"start_pts": s.get("start_pts"), "start_time": s.get("start_time"), "time_base": s.get("time_base"), "duration_ts": s.get("duration_ts"),
           "duration": s.get("duration"), "nb_frames": s.get("nb_frames"), "profile": s.get("profile"), "bit_rate": s.get("bit_rate")}
    if route == "MOV_PCM_S16LE":
        _chk(out, "output_audio_start_pts_zero", 0, s.get("start_pts"), sp == 0 and st == 0)
        ok = dts is not None and tb is not None and Fraction(dts) * tb == Fraction(samples, 48000)
        _chk(out, "output_audio_duration_samples_exact", samples, {"duration_ts": s.get("duration_ts"), "time_base": s.get("time_base")}, ok)
    else:
        tol = Fraction(AAC_TOLERANCE_SAMPLES, 48000)
        _chk(out, "output_audio_start_within_tolerance", {"abs_seconds_at_most": f"{tol.numerator}/{tol.denominator}"}, s.get("start_time"), st is not None and abs(st) <= tol)
        if dts is not None and tb is not None:
            obs["duration_samples_from_container"] = float(Fraction(dts) * tb * 48000)
    return out, obs


def parse_hash_lines(text: str) -> list[tuple]:
    """framemd5 / framehash text -> [(size, hash), ...]. Raises ValueError on a malformed data line."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6 or not parts[4].isdigit() or not parts[5]:
            raise ValueError(f"unrecognised hash line: {line[:60]!r}")
        rows.append((int(parts[4]), parts[5]))
    return rows


def packet_summary(parsed: dict) -> dict:
    """Observations from `ffprobe -show_packets` on the audio stream (AAC delay/padding evidence). Never inferred if absent."""
    pk = parsed.get("packets") or []
    skip = pad = None
    side_seen = False
    for p in pk:
        for sd in p.get("side_data_list") or []:
            if "skip_samples" in sd or "discard_padding" in sd or sd.get("side_data_type") == "Skip Samples":
                side_seen = True
                skip = sd.get("skip_samples") if skip is None and sd.get("skip_samples") else skip
                if sd.get("discard_padding"):
                    pad = sd.get("discard_padding")
    first, last = (pk[0], pk[-1]) if pk else ({}, {})
    return {"packet_count": len(pk), "first_packet": {k: first.get(k) for k in ("pts", "dts", "duration", "size")},
            "last_packet": {k: last.get(k) for k in ("pts", "dts", "duration", "size")},
            "skip_samples_side_data_seen": side_seen, "skip_samples": skip, "discard_padding": pad,
            "note": "skip/padding values are reported only if ffprobe exposes them as packet side data; absence is not evidence of zero"}


def tolerance_checks(decoded_samples: int, expected: int, tolerance: int) -> list:
    out = []
    _chk(out, "aac_decoded_samples_within_tolerance", {"expected": expected, "tolerance_samples": tolerance}, {"decoded": decoded_samples, "delta": decoded_samples - expected},
         abs(decoded_samples - expected) <= tolerance)
    return out
