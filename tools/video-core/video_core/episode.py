"""Episode v1 parsing and validation (stdlib only; mirrors schemas/episode.schema.json).

Stricter than the schema where that avoids silent reinterpretation:
  * integers must be JSON integers (no 30.0, no true/false)
  * unknown keys, duplicate JSON keys, NaN/Infinity, BOM and invalid UTF-8 are rejected
  * clips must be listed in timeline order and tile [0, total_frames) exactly
  * caption text may contain '\\n' but no other control characters, no blank lines,
    and no lone surrogates (so UTF-8 SRT export is always exact)
Validation never touches the filesystem or launches a process.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction

from . import timebase
from .errors import Issue, ValidationFailed
from .limits import Limits
from .paths import SOURCE_EXTENSIONS, check_relative_path

INT64_MAX = 2**63 - 1
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


@dataclass(frozen=True)
class ColorSource:
    value: str  # lower-case '#rrggbb'

    kind = "color"


@dataclass(frozen=True)
class FileSource:
    path: str

    kind = "file"


@dataclass(frozen=True)
class Clip:
    id: str
    start_frame: int
    duration_frames: int
    source: object

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames


@dataclass(frozen=True)
class Caption:
    id: str
    start_frame: int
    end_frame: int
    text: str


@dataclass(frozen=True)
class Episode:
    fps_n: int
    fps_d: int
    width: int
    height: int
    total_frames: int
    clips: tuple
    captions: tuple

    @property
    def fps(self) -> Fraction:
        return Fraction(self.fps_n, self.fps_d)

    @property
    def duration(self) -> Fraction:
        return timebase.duration_seconds(self.total_frames, self.fps)


class _JsonProblem(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _no_duplicate_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise _JsonProblem("json_duplicate_key", f"duplicate JSON key {k!r}")
        seen[k] = v
    return seen


def _reject_constant(name):
    raise _JsonProblem("json_non_finite", f"{name} is not allowed")


def parse_json_bytes(data: bytes, limits: Limits = Limits()):
    """Decode and parse episode JSON strictly. Raises ValidationFailed."""

    def fail(code, msg):
        raise ValidationFailed(msg, [Issue(code, "", msg)])

    if len(data) > limits.max_input_bytes:
        fail("limit_input_bytes", f"episode is {len(data)} bytes; limit is {limits.max_input_bytes}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("invalid_utf8", "episode is not valid UTF-8")
    if text.startswith("﻿"):
        fail("bom_not_allowed", "UTF-8 BOM is not allowed")
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except _JsonProblem as exc:
        fail(exc.code, str(exc))
    except RecursionError:
        fail("json_too_deep", "JSON nesting is too deep")
    except ValueError as exc:  # JSONDecodeError and oversized integer literals
        fail("json_invalid", f"invalid JSON: {str(exc)[:160]}")


class _Collector:
    def __init__(self):
        self.issues: list[Issue] = []

    def add(self, code, path, message):
        if len(self.issues) < 200:
            self.issues.append(Issue(code, path, message))

    def int_field(self, obj, key, path, lo=None, hi=None, code="out_of_range", hi_code=None):
        """Return a validated int or None (after recording an issue)."""
        p = f"{path}/{key}"
        if key not in obj:
            self.add("missing_field", p, f"{key} is required")
            return None
        v = obj[key]
        if isinstance(v, bool) or not isinstance(v, int):
            self.add("type_error", p, f"{key} must be an integer")
            return None
        if abs(v) > INT64_MAX:
            self.add("int_overflow", p, f"{key} exceeds the 64-bit range")
            return None
        if lo is not None and v < lo:
            self.add(code, p, f"{key} must be >= {lo}")
            return None
        if hi is not None and v > hi:
            self.add(hi_code or code, p, f"{key} must be <= {hi}")
            return None
        return v

    def keys(self, obj, required, path):
        if not isinstance(obj, dict):
            self.add("type_error", path, "expected an object")
            return False
        for k in obj:
            if k not in required:
                self.add("unknown_field", f"{path}/{k}", f"unknown field {k!r}")
        missing = [k for k in required if k not in obj]
        for k in missing:
            self.add("missing_field", f"{path}/{k}", f"{k} is required")
        return not missing


def _check_text(c: _Collector, text, path: str, limits: Limits):
    if not isinstance(text, str):
        c.add("type_error", path, "text must be a string")
        return None
    if len(text) < 1 or len(text) > limits.max_caption_chars:
        c.add("text_length", path, f"text must be 1..{limits.max_caption_chars} characters")
        return None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        c.add("text_invalid_unicode", path, "text contains a lone surrogate and cannot be encoded as UTF-8")
        return None
    for ch in text:
        if ch != "\n" and unicodedata.category(ch) == "Cc":
            c.add("text_control_char", path, f"control character U+{ord(ch):04X} is not allowed (only '\\n')")
            return None
    if any(line.strip() == "" for line in text.split("\n")):
        c.add("text_blank_line", path, "text must not be blank or contain empty/whitespace-only lines (they would end an SRT cue)")
        return None
    return text


def validate_episode(doc, limits: Limits = Limits()) -> Episode:
    """Validate a parsed document. Raises ValidationFailed listing every issue found."""
    c = _Collector()
    ep = _validate(doc, limits, c)
    if c.issues or ep is None:
        raise ValidationFailed(f"episode rejected ({len(c.issues)} issue(s))", c.issues)
    return ep


def load_episode(data: bytes, limits: Limits = Limits()) -> Episode:
    return validate_episode(parse_json_bytes(data, limits), limits)


def _validate(doc, limits: Limits, c: _Collector):  # noqa: C901 - one linear pass per section
    top = ("version", "fps", "canvas", "total_frames", "clips", "captions")
    if not c.keys(doc, top, ""):
        return None

    version = doc["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        c.add("unsupported_version", "/version", "version must be the integer 1")

    fps_n = fps_d = None
    if c.keys(doc["fps"], ("n", "d"), "/fps"):
        fps_n = c.int_field(doc["fps"], "n", "/fps", 1, 60_000)
        fps_d = c.int_field(doc["fps"], "d", "/fps", 1, 1_001)

    width = height = None
    if c.keys(doc["canvas"], ("width", "height"), "/canvas"):
        width = c.int_field(doc["canvas"], "width", "/canvas", 16, limits.max_dimension, hi_code="limit_dimension")
        height = c.int_field(doc["canvas"], "height", "/canvas", 16, limits.max_dimension, hi_code="limit_dimension")
        for name, v in (("width", width), ("height", height)):
            if v is not None and v % 2:
                c.add("dimension_odd", f"/canvas/{name}", f"{name} must be even (yuv420p)")

    total = c.int_field(doc, "total_frames", "", 1, limits.max_frames, hi_code="limit_frames")

    fps = Fraction(fps_n, fps_d) if fps_n and fps_d else None
    if fps is not None and fps > limits.max_fps:
        c.add("limit_fps", "/fps", f"frame rate {timebase.ratio_str(fps)} exceeds the limit {limits.max_fps}")
    if fps is not None and total is not None and timebase.duration_seconds(total, fps) > limits.max_duration():
        c.add("limit_duration", "/total_frames", f"duration exceeds {limits.max_duration_seconds} s")
    if None not in (width, height, total) and width * height * total > limits.max_pixel_volume:
        c.add("limit_pixel_volume", "/canvas", f"width*height*frames = {width * height * total} exceeds {limits.max_pixel_volume}")

    clips = _validate_clips(doc["clips"], total, limits, c)
    captions = _validate_captions(doc["captions"], total, limits, c)

    if c.issues or None in (fps_n, fps_d, width, height, total) or clips is None or captions is None:
        return None
    ep = Episode(fps_n, fps_d, width, height, total, tuple(clips), tuple(captions))
    for i, cap in enumerate(ep.captions):  # SRT must be representable (>=1 ms) at this rate
        if timebase.frame_to_ms(cap.end_frame, ep.fps) <= timebase.frame_to_ms(cap.start_frame, ep.fps):
            c.add("caption_srt_resolution", f"/captions/{i}", "caption spans less than one millisecond at this frame rate")
    return None if c.issues else ep


def _validate_clips(raw, total, limits, c: _Collector):
    if not isinstance(raw, list):
        c.add("type_error", "/clips", "clips must be an array")
        return None
    if not raw:
        c.add("clips_empty", "/clips", "at least one clip is required")
        return None
    if len(raw) > limits.max_clips:
        c.add("limit_clips", "/clips", f"{len(raw)} clips exceeds the limit {limits.max_clips}")
        return None
    out, ids, cursor, prev_start, ok = [], set(), 0, -1, True
    for i, item in enumerate(raw):
        p = f"/clips/{i}"
        if not c.keys(item, ("id", "start_frame", "duration_frames", "source"), p):
            ok = False
            continue
        cid = item["id"]
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            c.add("invalid_id", f"{p}/id", "id must match [a-zA-Z0-9_-]{1,64}")
            ok = False
        elif cid in ids:
            c.add("duplicate_id", f"{p}/id", f"duplicate clip id {cid!r}")
            ok = False
        else:
            ids.add(cid)
        start = c.int_field(item, "start_frame", p, 0)
        dur = c.int_field(item, "duration_frames", p, 1, code="duration_not_positive")
        source = _validate_source(item["source"], f"{p}/source", c)
        if source is None:
            ok = False  # keep going: coverage is still checked so one bad source does not cascade
        if start is None or dur is None:
            ok = False
            continue
        if start < prev_start:
            c.add("clip_order", f"{p}/start_frame", "clips must be listed in timeline order")
            ok = False
        elif start < cursor:
            c.add("clip_overlap", f"{p}/start_frame", f"clip starts at {start} but the previous clip ends at {cursor}")
            ok = False
        elif start > cursor:
            c.add("coverage_gap", f"{p}/start_frame", f"frames {cursor}..{start - 1} are not covered by any clip")
            ok = False
        prev_start = start
        cursor = max(cursor, start + dur)
        if total is not None and start + dur > total:
            c.add("clip_past_end", f"{p}/duration_frames", f"clip ends at frame {start + dur}, after total_frames {total}")
            ok = False
        if isinstance(cid, str) and source is not None:
            out.append(Clip(cid, start, dur, source))
    if total is not None and ok and cursor < total:
        c.add("coverage_incomplete", "/clips", f"clips end at frame {cursor} but total_frames is {total}")
        ok = False
    return out if ok else None


def _validate_source(src, p, c: _Collector):
    if not isinstance(src, dict) or "kind" not in src:
        c.add("type_error", p, "source must be an object with a kind")
        return None
    kind = src["kind"]
    if kind == "color":
        if not c.keys(src, ("kind", "value"), p):
            return None
        v = src["value"]
        if not isinstance(v, str) or not _COLOR.fullmatch(v):
            c.add("invalid_color", f"{p}/value", "color must be '#' followed by six hex digits")
            return None
        return ColorSource(v.lower())
    if kind == "file":
        if not c.keys(src, ("kind", "path"), p):
            return None
        v = src["path"]
        if not isinstance(v, str):
            c.add("type_error", f"{p}/path", "path must be a string")
            return None
        problems = check_relative_path(v, allowed_ext=SOURCE_EXTENSIONS)
        for code, msg in problems:
            c.add(code, f"{p}/path", msg)
        return None if problems else FileSource(v)
    c.add("unknown_source_kind", f"{p}/kind", "kind must be 'color' or 'file'")
    return None


def _validate_captions(raw, total, limits, c: _Collector):
    if not isinstance(raw, list):
        c.add("type_error", "/captions", "captions must be an array")
        return None
    if len(raw) > limits.max_captions:
        c.add("limit_captions", "/captions", f"{len(raw)} captions exceeds the limit {limits.max_captions}")
        return None
    out, ids, ok = [], set(), True
    for i, item in enumerate(raw):
        p = f"/captions/{i}"
        if not c.keys(item, ("id", "start_frame", "end_frame", "text"), p):
            ok = False
            continue
        cid = item["id"]
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            c.add("invalid_id", f"{p}/id", "id must match [a-zA-Z0-9_-]{1,64}")
            ok = False
        elif cid in ids:
            c.add("duplicate_id", f"{p}/id", f"duplicate caption id {cid!r}")
            ok = False
        else:
            ids.add(cid)
        start = c.int_field(item, "start_frame", p, 0)
        end = c.int_field(item, "end_frame", p, 1)
        text = _check_text(c, item["text"], f"{p}/text", limits)
        if start is None or end is None or text is None:
            ok = False
            continue
        if end <= start:
            c.add("caption_not_positive", f"{p}/end_frame", "end_frame must be greater than start_frame")
            ok = False
            continue
        if total is not None and end > total:
            c.add("caption_past_end", f"{p}/end_frame", f"caption ends at frame {end}, after total_frames {total}")
            ok = False
            continue
        if isinstance(cid, str):
            out.append(Caption(cid, start, end, text))
    return out if ok else None
