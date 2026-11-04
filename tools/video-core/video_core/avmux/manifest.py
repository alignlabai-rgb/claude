"""AV mux manifest v1 (stdlib mirror of schemas/avmux-manifest.schema.json).

The manifest is the caller's explicit selection of ONE local video and ONE qualified PCM WAV stem, bound by SHA-256 and by declared
properties. Nothing is inferred: no voice, no timing, no silence, no creative adoption. `selection.ref` and every `declared` value are
caller assertions; the planner only verifies what it can read itself (hashes, the WAV) and says plainly what it cannot (video metadata
stays CALLER_STATED_UNVERIFIED until the explicit --mux preflight measures it with ffprobe).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..audiomix.manifest import _Check
from ..errors import Issue, ValidationFailed
from ..paths import check_relative_path

AVMUX_VERSION = 1
ROUTES = ("MOV_PCM_S16LE", "MP4_AAC")
INPUT_STATES = ("USE", "MISSING", "UNKNOWN", "PROXY", "HOLD")
VIDEO_EXT = {".mp4": "mov", ".m4v": "mov", ".mov": "mov"}
SUPPORTED_VIDEO_CODEC = "h264"
_SHA = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class AvManifest:
    route: str
    selection_ref: str
    video_path: str
    video_sha256: str
    width: int
    height: int
    fps_n: int
    fps_d: int
    total_frames: int
    audio_path: str
    audio_sha256: str
    audio_channels: int
    audio_samples: int
    receipt_path: str | None = None
    receipt_sha256: str | None = None


def load_manifest(data: bytes, limits) -> AvManifest:
    from ..episode import parse_json_bytes

    doc = parse_json_bytes(data, limits)  # strict: UTF-8, no BOM, no duplicate keys, no NaN; reads limits.max_input_bytes only when present
    c = _Check()
    m = _validate(doc, limits, c)
    if c.issues or m is None:
        raise ValidationFailed(f"avmux manifest rejected ({len(c.issues)} issue(s))", c.issues)
    return m


def _sha(c, obj, key, path):
    v = obj[key]
    if not isinstance(v, str) or not _SHA.fullmatch(v):
        c.add("invalid_sha256", f"{path}/{key}", "sha256 must be 64 hex digits")
        return None
    return v.lower()


def _path(c, obj, path, allowed):
    v = obj["path"]
    if not isinstance(v, str):
        c.add("type_error", f"{path}/path", "path must be a string")
        return None
    problems = check_relative_path(v, allowed_ext=allowed)
    for code, msg in problems:
        c.add(code, f"{path}/path", msg)
    return None if problems else v


def _state(c, obj, path):
    v = obj["state"]
    if not isinstance(v, str) or v not in INPUT_STATES:
        c.add("invalid_enum", f"{path}/state", f"state must be one of {list(INPUT_STATES)}")
    elif v != "USE":
        c.add("input_state_not_use", f"{path}/state", f"input state is {v}: only USE inputs can be muxed (missing/unknown/proxy/hold inputs are rejected, never substituted)")


def _validate(doc, limits, c: _Check):  # noqa: C901
    if not c.keys(doc, ("avmux_version", "route", "selection", "video", "audio"), (), ""):
        return None
    v = doc["avmux_version"]
    if isinstance(v, bool) or not isinstance(v, int) or v != AVMUX_VERSION:
        c.add("unsupported_version", "/avmux_version", f"avmux_version must be the integer {AVMUX_VERSION}")
    route = doc["route"]
    if not isinstance(route, str) or route not in ROUTES:
        c.add("invalid_enum", "/route", f"route must be one of {list(ROUTES)}")
        route = None
    ref = None
    if c.keys(doc["selection"], ("ref",), (), "/selection"):
        ref = c.opaque(doc["selection"], "ref", "/selection")

    vpath = vsha = None
    w = h = fn = fd = total = None
    vd = doc["video"]
    if c.keys(vd, ("state", "path", "sha256", "declared"), (), "/video"):
        _state(c, vd, "/video")
        vpath = _path(c, vd, "/video", VIDEO_EXT)
        vsha = _sha(c, vd, "sha256", "/video")
        dd = vd["declared"]
        if c.keys(dd, ("codec", "width", "height", "fps", "total_frames"), (), "/video/declared"):
            if dd["codec"] != SUPPORTED_VIDEO_CODEC:
                c.add("video_codec_unsupported", "/video/declared/codec", f"only {SUPPORTED_VIDEO_CODEC} (stream copy) is supported")
            w = c.integer(dd, "width", "/video/declared", 16, 7680)
            h = c.integer(dd, "height", "/video/declared", 16, 7680)
            if c.keys(dd["fps"], ("n", "d"), (), "/video/declared/fps"):
                fn = c.integer(dd["fps"], "n", "/video/declared/fps", 1, 60_000)
                fd = c.integer(dd["fps"], "d", "/video/declared/fps", 1, 1_001)
            total = c.integer(dd, "total_frames", "/video/declared", 1, limits.max_frames, hi_code="limit_frames")

    apath = asha = None
    ach = asamples = None
    rpath = rsha = None
    ad = doc["audio"]
    if c.keys(ad, ("state", "path", "sha256", "declared"), ("receipt",), "/audio"):
        _state(c, ad, "/audio")
        apath = _path(c, ad, "/audio", (".wav",))
        asha = _sha(c, ad, "sha256", "/audio")
        dd = ad["declared"]
        if c.keys(dd, ("sample_rate", "channels", "sample_count"), (), "/audio/declared"):
            rate = c.integer(dd, "sample_rate", "/audio/declared")
            if rate is not None and rate != 48_000:
                c.add("unsupported_audio", "/audio/declared/sample_rate", "only 48000 Hz is supported (no resampling)")
            ach = c.integer(dd, "channels", "/audio/declared", 1, 2)
            asamples = c.integer(dd, "sample_count", "/audio/declared", 1)
        if "receipt" in ad:
            r = ad["receipt"]
            if c.keys(r, ("path", "sha256"), (), "/audio/receipt"):
                rpath = _path(c, r, "/audio/receipt", (".json",))
                rsha = _sha(c, r, "sha256", "/audio/receipt")

    if c.issues or None in (route, ref, vpath, vsha, w, h, fn, fd, total, apath, asha, ach, asamples):
        return None
    if (rpath is None) != (rsha is None):
        return None
    return AvManifest(route, ref, vpath, vsha, w, h, fn, fd, total, apath, asha, ach, asamples, rpath, rsha)
