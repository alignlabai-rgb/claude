"""Independent arithmetic / format oracle for the adversarial review.

HARD RULE: this module imports NOTHING from `video_core` (only the standard library) and re-derives every rule from the written
specifications (SPEC.md, AUDIO_SPEC.md, AVMUX_SPEC.md, LIMITATIONS.md), not from the implementation's helper functions.
If an oracle value and an implementation value disagree, the disagreement is examined by hand: it is either an
implementation defect, a spec ambiguity, or an oracle bug. Which one is recorded in FINDINGS.json.
"""

from __future__ import annotations

import struct
from fractions import Fraction

RATE = 48_000
INT64_MAX = 2**63 - 1


# ---------------------------------------------------------------------------------------------------------------- rational time
def floor_div(a: int, b: int) -> int:
    """Mathematical floor of a/b for b > 0 using Fractions (never `//`)."""
    q = Fraction(a, b)
    return q.numerator // q.denominator  # Fraction is reduced; numerator // denominator IS floor for a positive denominator


def round_half_up(x: Fraction) -> int:
    """floor(x + 1/2) using only Fractions."""
    y = x + Fraction(1, 2)
    return y.numerator // y.denominator


def sample_at(frame: int, n: int, d: int) -> int:
    """48 kHz sample boundary of an absolute frame boundary at fps n/d: round-half-up(frame * d * 48000 / n)."""
    return round_half_up(Fraction(frame * d * RATE, n))


def ms_at(frame: int, n: int, d: int) -> int:
    """Millisecond of an absolute frame boundary, round half up."""
    return round_half_up(Fraction(frame * d * 1000, n))


def srt_stamp(ms: int) -> str:
    assert ms >= 0
    hh, rest = divmod(ms, 3_600_000)
    mm, rest = divmod(rest, 60_000)
    ss, mmm = divmod(rest, 1000)
    return "%02d:%02d:%02d,%03d" % (hh, mm, ss, mmm)


def expected_srt(doc: dict) -> bytes:
    n, d = doc["fps"]["n"], doc["fps"]["d"]
    caps = list(enumerate(doc["captions"]))
    caps.sort(key=lambda t: (t[1]["start_frame"], t[1]["end_frame"], t[0]))
    blocks = []
    for k, (_i, c) in enumerate(caps, start=1):
        blocks.append("%d\n%s --> %s\n%s" % (k, srt_stamp(ms_at(c["start_frame"], n, d)), srt_stamp(ms_at(c["end_frame"], n, d)), c["text"]))
    return b"" if not blocks else ("\n\n".join(blocks) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------------------------- gain / channel math
def round_half_away(x: Fraction) -> int:
    """Round to nearest integer, exact halves away from zero."""
    if x >= 0:
        return round_half_up(x)
    return -round_half_up(-x)


def gain(sample: int, num: int, den: int) -> int:
    return round_half_away(Fraction(sample * num, den))


def avg2(a: int, b: int) -> int:
    return round_half_away(Fraction(a + b, 2))


def clamp16(v: int) -> int:
    return max(-32768, min(32767, v))


# ---------------------------------------------------------------------------------------------------------------- tiny deterministic PRNG
class Rng:
    """xorshift64*: identical sequence on every CPython (unlike random.* helpers whose internals changed between versions)."""

    MASK = (1 << 64) - 1

    def __init__(self, seed: int):
        self.seed = seed
        self.s = (seed * 0x9E3779B97F4A7C15 + 0x1234567DEADBEEF) & self.MASK or 0x1

    def next(self) -> int:
        x = self.s
        x ^= x >> 12
        x ^= (x << 25) & self.MASK
        x ^= x >> 27
        self.s = x
        return (x * 0x2545F4914F6CDD1D) & self.MASK

    def below(self, n: int) -> int:
        assert n > 0
        return (self.next() >> 11) % n

    def between(self, lo: int, hi: int) -> int:
        return lo + self.below(hi - lo + 1)

    def choice(self, seq):
        return seq[self.below(len(seq))]

    def chance(self, num: int, den: int) -> bool:
        return self.below(den) < num


# ---------------------------------------------------------------------------------------------------------------- WAV (strict) oracle
def wav_oracle(data: bytes, max_chunks: int = 64, max_header_bytes: int = 4096):
    """Return ('ok', info) or ('reject', reason). Derived from the AUDIO_SPEC 'Inputs' paragraph."""
    n = len(data)
    if n < 12:
        return "reject", "short"
    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return "reject", "magic"
    riff = struct.unpack_from("<I", data, 4)[0]
    if riff == 0xFFFFFFFF:
        return "reject", "streaming"
    if riff + 8 != n:
        return "reject", "riff_size"
    pos, fmt, dat, chunks = 12, None, None, 0
    while pos < n:
        chunks += 1
        if chunks > max_chunks or 12 + 8 * chunks > max_header_bytes:
            return "reject", "too_many_chunks"
        if pos + 8 > n:
            return "reject", "chunk_header_past_end"
        tag = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if size == 0xFFFFFFFF:
            return "reject", "streaming_chunk"
        if body + size > n:
            return "reject", "chunk_past_end"
        if tag == b"fmt ":
            if fmt is not None:
                return "reject", "dup_fmt"
            if dat is not None:
                return "reject", "fmt_after_data"
            if size not in (16, 18):
                return "reject", "fmt_size"
            f_tag, ch, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", data, body)
            if size == 18 and struct.unpack_from("<H", data, body + 16)[0] != 0:
                return "reject", "cbsize"
            if f_tag != 1:
                return "reject", "format_tag"
            if bits != 16 or ch not in (1, 2) or rate != RATE:
                return "reject", "unsupported"
            if align != ch * 2 or byte_rate != rate * align:
                return "reject", "header_mismatch"
            fmt = (ch, rate, bits)
        elif tag == b"data":
            if fmt is None:
                return "reject", "data_before_fmt"
            if dat is not None:
                return "reject", "dup_data"
            if size % (fmt[0] * 2):
                return "reject", "partial_frame"
            dat = (body, size)
        pos = body + size + (size % 2)
        if pos > n:
            return "reject", "pad_past_end"
    if fmt is None:
        return "reject", "no_fmt"
    if dat is None:
        return "reject", "no_data"
    return "ok", {"channels": fmt[0], "data_offset": dat[0], "data_size": dat[1], "samples": dat[1] // (fmt[0] * 2)}


# ---------------------------------------------------------------------------------------------------------------- audio plan oracle
def audio_plan_oracle(doc: dict, asset_samples: dict, asset_channels: dict):
    """Expected outcome of the audio planner for a manifest `doc` (dict, already syntactically valid).

    Returns {"verdict": "REJECT"|"HOLD"|"READY", "reason": str, "gaps": [(s0, s1)], "covered": int, "total": int}.
    Mirrors AUDIO_SPEC.md: only SELECTED events are consumed; selected MISSING/UNKNOWN/PROXY => HOLD (never silence);
    INTENTIONAL_SILENCE counts as coverage; OMIT does not; USE length must equal timeline length; overlap/silence rules.
    Zero-sample events are rejected for EVERY event with a frame range (this is what the implementation does; see FINDINGS F-AUD-1
    for why that is questionable for unselected/OMIT events) -- the oracle takes the strict reading so the finding is tested separately.
    """
    n, d = doc["timeline"]["fps"]["n"], doc["timeline"]["fps"]["d"]
    total = sample_at(doc["timeline"]["total_frames"], n, d)
    scope = set(doc["scope"]["event_ids"])
    pol = doc["policy"]
    overlap_policy = pol.get("overlap", "REJECT")
    out_ch = doc["output"]["channels"]
    spans = {}
    for e in doc["events"]:
        if "start_frame" in e:
            s0, s1 = sample_at(e["start_frame"], n, d), sample_at(e["end_frame"], n, d)
            if s1 <= s0:
                return {"verdict": "REJECT", "reason": "zero_samples:" + e["id"], "total": total}
            spans[e["id"]] = (s0, s1)
    holds = []
    consumed = []
    for e in doc["events"]:
        if e["id"] not in scope:
            continue
        st = e["state"]
        if st in ("MISSING", "UNKNOWN", "PROXY"):
            holds.append("unresolved:" + e["id"])
        elif st == "USE":
            s0, s1 = spans[e["id"]]
            if e["source_out_sample"] - e["source_in_sample"] != s1 - s0:
                return {"verdict": "REJECT", "reason": "length_mismatch:" + e["id"], "total": total}
            consumed.append(e)
        elif st == "INTENTIONAL_SILENCE":
            consumed.append(e)
    # overlap analysis in sample space (touching is not overlapping)
    for i in range(len(consumed)):
        for j in range(i + 1, len(consumed)):
            a, b = consumed[i], consumed[j]
            (a0, a1), (b0, b1) = spans[a["id"]], spans[b["id"]]
            if a0 < b1 and b0 < a1:
                if a["state"] == "INTENTIONAL_SILENCE" or b["state"] == "INTENTIONAL_SILENCE":
                    return {"verdict": "REJECT", "reason": "silence_overlap", "total": total}
                if overlap_policy == "REJECT":
                    return {"verdict": "REJECT", "reason": "overlap", "total": total}
    use = [e for e in consumed if e["state"] == "USE"]
    # concurrency
    cuts = sorted({spans[e["id"]][0] for e in use} | {spans[e["id"]][1] for e in use})
    peak = 0
    for c in cuts:
        peak = max(peak, sum(1 for e in use if spans[e["id"]][0] <= c < spans[e["id"]][1]))
    if peak > doc.get("_max_concurrent", 8):
        return {"verdict": "REJECT", "reason": "concurrency", "total": total}
    # coverage by brute-force sample marking (small totals only)
    assert total <= 400_000, "oracle coverage is brute force: keep timelines small"
    mark = bytearray(total)
    for e in consumed:
        s0, s1 = spans[e["id"]]
        for k in range(s0, s1):
            mark[k] = 1
    gaps, k = [], 0
    while k < total:
        if mark[k]:
            k += 1
            continue
        j = k
        while j < total and not mark[j]:
            j += 1
        gaps.append((k, j))
        k = j
    covered = total - sum(b - a for a, b in gaps)
    if gaps and pol["gap"] == "REQUIRE_FULL_COVERAGE":
        holds.append("incomplete_coverage")
    return {"verdict": "HOLD" if holds else "READY", "reason": ",".join(holds), "gaps": gaps, "covered": covered, "total": total}


def mix_oracle(doc: dict, pcm_of: dict, channels_of: dict) -> tuple[bytes, dict]:
    """Expected stem PCM bytes (interleaved little-endian int16) and stats for a READY manifest.

    pcm_of[asset_id] = list of per-frame tuples (1 or 2 ints). Uses Fractions for every rounding."""
    n, d = doc["timeline"]["fps"]["n"], doc["timeline"]["fps"]["d"]
    total = sample_at(doc["timeline"]["total_frames"], n, d)
    oc = doc["output"]["channels"]
    scope = set(doc["scope"]["event_ids"])
    acc = [[0] * oc for _ in range(total)]
    for e in doc["events"]:
        if e["id"] not in scope or e["state"] != "USE":
            continue
        s0, s1 = sample_at(e["start_frame"], n, d), sample_at(e["end_frame"], n, d)
        g = e.get("gain", {"num": 1, "den": 1})
        src_ch = channels_of[e["asset"]]
        rule = e.get("channel_rule", "COPY")
        for k in range(s1 - s0):
            fr = pcm_of[e["asset"]][e["source_in_sample"] + k]
            if rule == "COPY":
                vals = list(fr)
            elif rule == "MONO_TO_STEREO_DUPLICATE":
                vals = [fr[0], fr[0]]
            elif rule == "STEREO_TO_MONO_LEFT":
                vals = [fr[0]]
            elif rule == "STEREO_TO_MONO_RIGHT":
                vals = [fr[1]]
            elif rule == "STEREO_TO_MONO_AVERAGE":
                vals = [avg2(fr[0], fr[1])]
            else:
                raise AssertionError(rule)
            assert len(vals) == oc and src_ch in (1, 2)
            for c in range(oc):
                acc[s0 + k][c] += gain(vals[c], g["num"], g["den"])
    clipped = sum(1 for fr in acc for v in fr if v > 32767 or v < -32768)
    first = next((i for i, fr in enumerate(acc) if any(v > 32767 or v < -32768 for v in fr)), None)
    peak_before = max((abs(v) for fr in acc for v in fr), default=0)
    out = bytearray()
    for fr in acc:
        for v in fr:
            out += struct.pack("<h", clamp16(v))
    return bytes(out), {"clipped": clipped, "first_clipped_sample": first, "peak_before_clip": peak_before, "total": total}


# ---------------------------------------------------------------------------------------------------------------- episode validity oracle
def episode_oracle(doc, *, max_frames=18000, max_dim=7680, max_fps=120, max_pixel_volume=1920 * 1080 * 18000, max_duration=3600,
                   max_clips=256, max_captions=512, max_chars=512):
    """Return None when the episode is acceptable, otherwise a short reason. Rules from SPEC.md + LIMITATIONS.md ('deliberately stricter')."""
    def isint(v):
        return isinstance(v, int) and not isinstance(v, bool)
    try:
        fps, canvas, total = doc["fps"], doc["canvas"], doc["total_frames"]
        if doc["version"] != 1 or isinstance(doc["version"], bool):
            return "version"
        n, d = fps["n"], fps["d"]
        w, h = canvas["width"], canvas["height"]
        if not all(isint(x) for x in (n, d, w, h, total)):
            return "type"
        if not (1 <= n <= 60000 and 1 <= d <= 1001):
            return "fps_range"
        if Fraction(n, d) > max_fps:
            return "fps_limit"
        if not (1 <= total <= max_frames):
            return "frames"
        if Fraction(total * d, n) > max_duration:
            return "duration"
        if not (16 <= w <= max_dim and 16 <= h <= max_dim) or w % 2 or h % 2:
            return "canvas"
        if w * h * total > max_pixel_volume:
            return "pixel_volume"
        clips, caps = doc["clips"], doc["captions"]
        if not clips or len(clips) > max_clips or len(caps) > max_captions:
            return "counts"
        cursor, seen = 0, set()
        for c in clips:
            if not (isint(c["start_frame"]) and isint(c["duration_frames"])):
                return "type"
            if c["id"] in seen:
                return "dup_clip_id"
            seen.add(c["id"])
            if c["duration_frames"] < 1 or c["start_frame"] != cursor:
                return "clip_tiling"
            cursor += c["duration_frames"]
            if cursor > total:
                return "clip_past_end"
        if cursor != total:
            return "coverage"
        seen = set()
        for c in caps:
            if c["id"] in seen:
                return "dup_caption_id"
            seen.add(c["id"])
            if not (isint(c["start_frame"]) and isint(c["end_frame"])):
                return "type"
            if c["start_frame"] < 0 or c["end_frame"] <= c["start_frame"] or c["end_frame"] > total:
                return "caption_range"
            if ms_at(c["end_frame"], n, d) <= ms_at(c["start_frame"], n, d):
                return "srt_resolution"
        return None
    except (KeyError, TypeError):
        return "shape"
