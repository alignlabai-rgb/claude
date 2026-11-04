"""Independent rational-frame timing oracle + strict SRT reader. Imports nothing from video_core.

Frame i is displayed over [i/fps, (i+1)/fps). A caption [s, e) (start inclusive, end exclusive, in frames) is visible
on frame i  <=>  the frame's *midpoint* (i + 1/2)/fps lies inside [s/fps, e/fps).  Everything is Fraction arithmetic;
nothing is rounded, so there is no frame-length drift (30000/1001 etc.).

Consumer models answer: "given integer-millisecond SRT cue times, which frames does a player show?" They are MODELS of
common behaviours, not measurements of any real player.
"""

from __future__ import annotations

import re
from fractions import Fraction

# ---------------------------------------------------------------- exact frame arithmetic ---------------------------


def floor_frac(x: Fraction) -> int:
    return x.numerator // x.denominator


def frame_time_ms(i: int, fps: Fraction) -> Fraction:
    """Exact presentation time of frame i in milliseconds."""
    return Fraction(1000 * i) / fps


def round_half_up_ms(i_or_frame_boundary: int, fps: Fraction) -> int:
    return floor_frac(frame_time_ms(i_or_frame_boundary, fps) + Fraction(1, 2))


def floor_ms(boundary: int, fps: Fraction) -> int:
    return floor_frac(frame_time_ms(boundary, fps))


def visible_frames_midpoint(start: int, end: int, total: int, fps: Fraction) -> frozenset:
    """Oracle: frames of [0,total) whose midpoint lies in [start/fps, end/fps)."""
    lo, hi = Fraction(start) / fps, Fraction(end) / fps
    return frozenset(i for i in range(total) if lo <= (Fraction(2 * i + 1, 2)) / fps < hi)


def visible_frames_integer(start: int, end: int, total: int) -> frozenset:
    """Second formulation, pure integer: start <= i < end."""
    return frozenset(i for i in range(total) if start <= i < end)


# ---------------------------------------------------------------- strict SRT reader --------------------------------

_TS = r"(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})"
_TIMING = re.compile(rf"^{_TS} --> {_TS}$")


class SrtError(ValueError):
    pass


def ts_to_ms(h, m, s, ms) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def parse_srt_strict(data: bytes) -> list:
    """Strict reader for the format the exporter promises: UTF-8 (no BOM), LF only, blank-line separated cues, indices 1..N.
    Returns [{"index", "start_ms", "end_ms", "text"}]. Raises SrtError on any deviation. Empty bytes -> []."""
    if not data:
        return []
    if data.startswith(b"\xef\xbb\xbf"):
        raise SrtError("BOM")
    if b"\r" in data:
        raise SrtError("CR present")
    text = data.decode("utf-8")  # strict: UnicodeDecodeError propagates
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise SrtError("must end with exactly one LF")
    blocks = text[:-1].split("\n\n")
    cues = []
    for n, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 3:
            raise SrtError(f"cue {n}: fewer than 3 lines")
        if lines[0] != str(n):
            raise SrtError(f"cue {n}: bad index line {lines[0]!r}")
        m = _TIMING.match(lines[1])
        if not m:
            raise SrtError(f"cue {n}: bad timing line {lines[1]!r}")
        g = m.groups()
        start_ms, end_ms = ts_to_ms(*g[:4]), ts_to_ms(*g[4:])
        if end_ms <= start_ms:
            raise SrtError(f"cue {n}: end <= start")
        cues.append({"index": n, "start_ms": start_ms, "end_ms": end_ms, "text": "\n".join(lines[2:])})
    return cues


def parse_srt_splitlines(data: bytes) -> list:
    """A deliberately *naive* consumer that uses str.splitlines() (which also breaks on U+2028/U+2029/U+0085/VT/FF/FS/GS/RS).
    Used only to show consumer sensitivity; it is not the reference reader."""
    text = data.decode("utf-8")
    cues, cur = [], []
    for line in text.splitlines() + [""]:
        if line == "":
            if cur:
                cues.append(cur)
                cur = []
        else:
            cur.append(line)
    return cues


# ---------------------------------------------------------------- consumer models ----------------------------------

def _q_exact(t: Fraction):
    return t


def _q_round(t: Fraction):
    return floor_frac(t + Fraction(1, 2))


def _q_floor(t: Fraction):
    return floor_frac(t)


def _q_ceil(t: Fraction):
    return -floor_frac(-t)


CONSUMER_MODELS = {
    "exact_pts_vs_ms": _q_exact,       # compares the exact rational frame time with integer-ms cue bounds
    "round_half_up_ms": _q_round,      # first rounds the frame time to ms (half up), then compares
    "floor_ms": _q_floor,
    "ceil_ms": _q_ceil,
}


def consumer_visible(cue_start_ms: int, cue_end_ms: int, total: int, fps: Fraction, model: str) -> frozenset:
    q = CONSUMER_MODELS[model]
    return frozenset(i for i in range(total) if cue_start_ms <= q(frame_time_ms(i, fps)) < cue_end_ms)


def boundary_ms(frame: int, fps: Fraction, policy: str) -> int:
    """SRT millisecond for a frame boundary under a rounding policy: 'half_up' (production's documented rule) or 'floor'."""
    if policy == "half_up":
        return round_half_up_ms(frame, fps)
    if policy == "floor":
        return floor_ms(frame, fps)
    raise ValueError(policy)


def quantised_times(total: int, fps: Fraction) -> dict:
    """{model: [q(t_i) for i in range(total)]} computed once (Fractions for the exact model, ints otherwise)."""
    ts = [frame_time_ms(i, fps) for i in range(total)]
    return {m: [q(t) for t in ts] for m, q in CONSUMER_MODELS.items()}


def compare_policy(fps: Fraction, policy: str, max_frame: int) -> dict:
    """Exhaustively compare every caption [s,e) with 0<=s<e<=max_frame against the oracle under every consumer model.
    Returns {model: {"windows": n, "mismatching_windows": k, "first": [s,e,extra_frames,missing_frames] or None}}."""
    out = {m: {"windows": 0, "mismatching_windows": 0, "first": None} for m in CONSUMER_MODELS}
    total = max_frame + 1
    qt = quantised_times(total, fps)
    bms = [boundary_ms(f, fps, policy) for f in range(total)]
    for s in range(0, max_frame):
        for e in range(s + 1, max_frame + 1):
            sm, em = bms[s], bms[e]
            for m, qs in qt.items():
                rec = out[m]
                rec["windows"] += 1
                # visible frames under the model vs the oracle's [s, e)
                bad = False
                extra, missing = [], []
                for i in range(total):
                    g = sm <= qs[i] < em
                    w = s <= i < e                       # integer form of the midpoint oracle (asserted equal in the tests)
                    if g and not w:
                        extra.append(i)
                        bad = True
                    elif w and not g:
                        missing.append(i)
                        bad = True
                if bad:
                    rec["mismatching_windows"] += 1
                    if rec["first"] is None:
                        rec["first"] = [s, e, extra, missing]
    return out


# ---------------------------------------------------------------- drawtext enable evaluation -----------------------

_BETWEEN = re.compile(r"^between\(n,(\d+),(\d+)\)$")


def enable_frames(expr: str, total: int) -> frozenset:
    """Evaluate the one expression shape the plan emits, `between(n,A,B)`, with FFmpeg's documented semantics
    (A <= n <= B, inclusive both ends). Any other shape raises: the oracle never guesses."""
    m = _BETWEEN.match(expr)
    if not m:
        raise ValueError(f"unrecognised enable expression: {expr!r}")
    a, b = int(m.group(1)), int(m.group(2))
    return frozenset(n for n in range(total) if a <= n <= b)


def adjacent_pairs(caps: list) -> list:
    """Pairs (A,B) of [start,end) windows with A.end == B.start (touching)."""
    return [(a, b) for a in caps for b in caps if a is not b and a[1] == b[0]]
