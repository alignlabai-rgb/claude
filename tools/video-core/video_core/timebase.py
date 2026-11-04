"""Exact frame arithmetic. Frames are integers; time is Fraction or exact integer math.

Nothing here accumulates: every conversion starts from the absolute frame index.
"""

from __future__ import annotations

from fractions import Fraction


def fps_fraction(n: int, d: int) -> Fraction:
    return Fraction(n, d)


def frame_to_seconds(frame: int, fps: Fraction) -> Fraction:
    """Exact start time of `frame` in seconds."""
    return Fraction(frame) / fps


def duration_seconds(total_frames: int, fps: Fraction) -> Fraction:
    return frame_to_seconds(total_frames, fps)


def frame_to_ms(frame: int, fps: Fraction) -> int:
    """Round-half-up millisecond of a frame boundary, using only integers.

    ms = floor(frame * 1000 * d / n + 1/2); never derived from a rounded
    per-frame duration, so 30000/1001 does not drift.
    """
    num = frame * 1000 * fps.denominator
    den = fps.numerator
    return (2 * num + den) // (2 * den)


def ms_to_srt(ms: int) -> str:
    if ms < 0:
        raise ValueError("negative timestamp")
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def frame_to_srt(frame: int, fps: Fraction) -> str:
    return ms_to_srt(frame_to_ms(frame, fps))


def ratio_str(value: Fraction) -> str:
    """Canonical text for a rational: always 'num/den' (reduced, den > 0)."""
    return f"{value.numerator}/{value.denominator}"


def parse_ratio(text: str) -> Fraction:
    """Parse ffprobe-style 'num/den' or a decimal; raises ValueError on 0/0 etc."""
    try:
        value = Fraction(text)
    except (ZeroDivisionError, ValueError) as exc:
        raise ValueError(f"not a rational: {text!r}") from exc
    return value
