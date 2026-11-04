"""Audio manifest v1: parsing and validation (stdlib mirror of schemas/audio-manifest.schema.json).

The manifest is a *caller's* statement. Event ids, source ids, roles and decision/provenance references are opaque caller
assertions that this component preserves verbatim; it neither authenticates them nor treats them as human adoption, and it
does not duplicate or override any external creative/binding authority. Integers must be JSON integers (no floats, bools, NaN).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..episode import INT64_MAX, parse_json_bytes
from ..errors import Issue, ValidationFailed
from ..paths import check_relative_path
from .limits import SAMPLE_RATE, AudioLimits

MANIFEST_VERSION = 1
STATES = ("USE", "OMIT", "INTENTIONAL_SILENCE", "MISSING", "UNKNOWN", "PROXY")
UNRESOLVED = ("MISSING", "UNKNOWN", "PROXY")
GAP_POLICIES = ("ALLOW_ZERO_FILL", "REQUIRE_FULL_COVERAGE")
OVERLAP_POLICIES = ("REJECT", "ALLOW_SUM")
CLIPPING_POLICIES = ("REJECT", "CLAMP")
CHANNEL_RULES = ("COPY", "MONO_TO_STEREO_DUPLICATE", "STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE")
GAIN_MAX_RATIO = 4  # linear gain <= 4 (+12 dB); gain is an exact rational num/den
GAIN_MAX_DEN = 65_536
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_ROLE = re.compile(r"[a-zA-Z0-9_-]{1,32}")
_SHA = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class Asset:
    id: str
    path: str
    sha256: str  # lower-case, caller-provided; verified against the real file before use


@dataclass(frozen=True)
class Event:
    id: str
    source_id: str
    state: str
    asset: str | None = None
    source_in: int | None = None
    source_out: int | None = None
    start_frame: int | None = None
    end_frame: int | None = None
    gain_num: int = 1
    gain_den: int = 1
    channel_rule: str | None = None
    decision_ref: str | None = None
    provenance_ref: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class AudioManifest:
    fps_n: int
    fps_d: int
    total_frames: int
    episode_sha256: str | None
    sample_rate: int
    channels: int
    gap_policy: str
    overlap_policy: str
    clipping_policy: str
    scope: tuple
    assets: tuple
    events: tuple


class _Check:
    def __init__(self):
        self.issues: list[Issue] = []

    def add(self, code, path, message):
        if len(self.issues) < 200:
            self.issues.append(Issue(code, path, message))

    def keys(self, obj, required, optional, path) -> bool:
        if not isinstance(obj, dict):
            self.add("type_error", path, "expected an object")
            return False
        for k in obj:
            if k not in required and k not in optional:
                self.add("unknown_field", f"{path}/{k}", f"unknown field {k!r}")
        ok = True
        for k in required:
            if k not in obj:
                self.add("missing_field", f"{path}/{k}", f"{k} is required")
                ok = False
        return ok

    def integer(self, obj, key, path, lo=None, hi=None, code="out_of_range", hi_code=None):
        p = f"{path}/{key}"
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

    def ident(self, obj, key, path, pattern=_ID):
        v = obj[key]
        if not isinstance(v, str) or not pattern.fullmatch(v):
            self.add("invalid_id", f"{path}/{key}", f"{key} must match {pattern.pattern}")
            return None
        return v

    def opaque(self, obj, key, path):
        v = obj[key]
        if not isinstance(v, str) or not 1 <= len(v) <= 128:
            self.add("invalid_ref", f"{path}/{key}", f"{key} must be a string of 1..128 characters")
            return None
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            self.add("invalid_ref", f"{path}/{key}", f"{key} contains a lone surrogate")
            return None
        if any(unicodedata.category(ch)[0] == "C" for ch in v) or v != v.strip():
            self.add("invalid_ref", f"{path}/{key}", f"{key} must not contain control/format characters or leading/trailing whitespace")
            return None
        return v


def load_manifest(data: bytes, limits: AudioLimits = AudioLimits()) -> AudioManifest:
    """Parse strictly (UTF-8, no BOM, no duplicate keys, no NaN) and validate. Raises ValidationFailed listing every issue."""
    doc = parse_json_bytes(data, limits)  # only reads limits.max_input_bytes
    c = _Check()
    m = _validate(doc, limits, c)
    if c.issues or m is None:
        raise ValidationFailed(f"audio manifest rejected ({len(c.issues)} issue(s))", c.issues)
    return m


def _enum(c, obj, key, allowed, path):
    v = obj[key]
    if not isinstance(v, str) or v not in allowed:
        c.add("invalid_enum", f"{path}/{key}", f"{key} must be one of {list(allowed)}")
        return None
    return v


def _validate(doc, limits: AudioLimits, c: _Check):  # noqa: C901 - one linear pass per section
    top_req = ("manifest_version", "timeline", "output", "scope", "policy", "assets", "events")
    if not c.keys(doc, top_req, (), ""):
        return None
    v = doc["manifest_version"]
    if isinstance(v, bool) or not isinstance(v, int) or v != MANIFEST_VERSION:
        c.add("unsupported_version", "/manifest_version", f"manifest_version must be the integer {MANIFEST_VERSION}")

    fps_n = fps_d = total = None
    episode_sha = None
    tl = doc["timeline"]
    if c.keys(tl, ("fps", "total_frames"), ("episode_sha256",), "/timeline"):
        if c.keys(tl["fps"], ("n", "d"), (), "/timeline/fps"):
            fps_n = c.integer(tl["fps"], "n", "/timeline/fps", 1, 60_000)
            fps_d = c.integer(tl["fps"], "d", "/timeline/fps", 1, 1_001)
        total = c.integer(tl, "total_frames", "/timeline", 1, limits.max_frames, hi_code="limit_frames")
        if "episode_sha256" in tl:
            e = tl["episode_sha256"]
            if not isinstance(e, str) or not _SHA.fullmatch(e):
                c.add("invalid_sha256", "/timeline/episode_sha256", "episode_sha256 must be 64 hex digits")
            else:
                episode_sha = e.lower()

    channels = None
    out = doc["output"]
    if c.keys(out, ("sample_rate", "channels", "bits"), (), "/output"):
        rate = c.integer(out, "sample_rate", "/output")
        if rate is not None and rate != SAMPLE_RATE:
            c.add("unsupported_output", "/output/sample_rate", f"only {SAMPLE_RATE} Hz output is supported (no resampling)")
        channels = c.integer(out, "channels", "/output", 1, 2)
        bits = c.integer(out, "bits", "/output")
        if bits is not None and bits != 16:
            c.add("unsupported_output", "/output/bits", "only 16-bit output is supported")

    gap = overlap = clipping = None
    pol = doc["policy"]
    if c.keys(pol, ("gap",), ("overlap", "clipping"), "/policy"):
        gap = _enum(c, pol, "gap", GAP_POLICIES, "/policy")
        overlap = _enum(c, pol, "overlap", OVERLAP_POLICIES, "/policy") if "overlap" in pol else "REJECT"
        clipping = _enum(c, pol, "clipping", CLIPPING_POLICIES, "/policy") if "clipping" in pol else "REJECT"

    assets = _assets(doc["assets"], limits, c)
    events = _events(doc["events"], total, assets, limits, c)

    scope = None
    sc = doc["scope"]
    if c.keys(sc, ("event_ids",), (), "/scope"):
        ids = sc["event_ids"]
        if not isinstance(ids, list) or not ids:
            c.add("scope_empty", "/scope/event_ids", "scope must explicitly list at least one event id (there is no implicit 'all')")
        elif events is not None:
            known = {e.id for e in events}
            seen = set()
            for i, x in enumerate(ids):
                if not isinstance(x, str) or not _ID.fullmatch(x):
                    c.add("invalid_id", f"/scope/event_ids/{i}", "event id must match [a-zA-Z0-9_-]{1,64}")
                elif x in seen:
                    c.add("scope_duplicate", f"/scope/event_ids/{i}", f"event id {x!r} listed twice")
                elif x not in known:
                    c.add("scope_unknown_event", f"/scope/event_ids/{i}", f"event id {x!r} is not in events")
                else:
                    seen.add(x)
            scope = tuple(x for x in ids if isinstance(x, str) and x in seen)

    if c.issues or None in (fps_n, fps_d, total, channels, gap, overlap, clipping) or assets is None or events is None or scope is None:
        return None
    return AudioManifest(fps_n, fps_d, total, episode_sha, SAMPLE_RATE, channels, gap, overlap, clipping, scope, tuple(assets), tuple(events))


def _assets(raw, limits, c: _Check):
    if not isinstance(raw, list):
        c.add("type_error", "/assets", "assets must be an array")
        return None
    if len(raw) > limits.max_assets:
        c.add("limit_assets", "/assets", f"{len(raw)} assets exceeds the limit {limits.max_assets}")
        return None
    out, ids, ok = [], set(), True
    for i, item in enumerate(raw):
        p = f"/assets/{i}"
        if not c.keys(item, ("id", "path", "sha256"), (), p):
            ok = False
            continue
        aid = c.ident(item, "id", p)
        if aid in ids:
            c.add("duplicate_id", f"{p}/id", f"duplicate asset id {aid!r}")
            ok = False
        elif aid:
            ids.add(aid)
        path, sha = item["path"], item["sha256"]
        if not isinstance(path, str):
            c.add("type_error", f"{p}/path", "path must be a string")
            path_ok = False
        else:
            problems = check_relative_path(path, allowed_ext=(".wav",))
            for code, msg in problems:
                c.add(code, f"{p}/path", msg)
            path_ok = not problems
        sha_ok = isinstance(sha, str) and bool(_SHA.fullmatch(sha))
        if not sha_ok:
            c.add("invalid_sha256", f"{p}/sha256", "sha256 must be 64 hex digits")
        if aid is None or not path_ok or not sha_ok:
            ok = False
            continue
        out.append(Asset(aid, path, sha.lower()))
    return out if ok else None


def _events(raw, total, assets, limits, c: _Check):  # noqa: C901
    if not isinstance(raw, list):
        c.add("type_error", "/events", "events must be an array")
        return None
    if not raw:
        c.add("events_empty", "/events", "at least one event is required")
        return None
    if len(raw) > limits.max_events:
        c.add("limit_events", "/events", f"{len(raw)} events exceeds the limit {limits.max_events}")
        return None
    asset_ids = {a.id for a in assets} if assets is not None else None
    optional_all = ("role", "decision_ref", "provenance_ref", "start_frame", "end_frame")
    use_only = ("asset", "source_in_sample", "source_out_sample", "gain", "channel_rule")
    out, ids = [], set()
    all_ok = True
    for i, item in enumerate(raw):
        p = f"/events/{i}"
        ok = True
        if not c.keys(item, ("id", "source_id", "state"), optional_all + use_only, p):
            all_ok = False
            continue
        eid = c.ident(item, "id", p)
        sid = c.ident(item, "source_id", p)
        if eid in ids:
            c.add("duplicate_id", f"{p}/id", f"duplicate event id {eid!r}")
            ok = False
        elif eid:
            ids.add(eid)
        state = item["state"]
        if not isinstance(state, str) or state not in STATES:
            c.add("invalid_enum", f"{p}/state", f"state must be one of {list(STATES)}")
            all_ok = False
            continue
        if state != "USE":
            for k in use_only:
                if k in item:
                    c.add("field_not_allowed", f"{p}/{k}", f"{k} is only allowed on USE events (state is {state})")
                    ok = False
        refs = {}
        for k in ("decision_ref", "provenance_ref"):
            if k in item:
                refs[k] = c.opaque(item, k, p)
                ok = ok and refs[k] is not None
        role = None
        if "role" in item:
            role = c.ident(item, "role", p, _ROLE)
            ok = ok and role is not None
        has_s, has_e = "start_frame" in item, "end_frame" in item
        sf = ef = None
        if has_s != has_e:
            c.add("range_incomplete", p, "start_frame and end_frame must be given together")
            ok = False
        elif has_s:
            sf = c.integer(item, "start_frame", p, 0)
            ef = c.integer(item, "end_frame", p, 1)
            if sf is None or ef is None:
                ok = False
            elif ef <= sf:
                c.add("event_range_invalid", p, "end_frame must be greater than start_frame")
                ok = False
            elif total is not None and ef > total:
                c.add("event_past_end", f"{p}/end_frame", f"event ends at frame {ef}, after total_frames {total}")
                ok = False
        if state in ("USE", "INTENTIONAL_SILENCE") and not has_s:
            c.add("missing_field", p, f"{state} events require start_frame and end_frame")
            ok = False
        if state in ("INTENTIONAL_SILENCE", "OMIT") and "decision_ref" not in item:
            c.add("missing_field", f"{p}/decision_ref", f"{state} requires a decision_ref (an opaque caller assertion)")
            ok = False
        asset = sin = sout = rule = None
        gn = gd = 1
        if state == "USE":
            for k in ("asset", "source_in_sample", "source_out_sample"):
                if k not in item:
                    c.add("missing_field", f"{p}/{k}", f"USE events require {k} (all technical bindings)")
                    ok = False
            if "asset" in item:
                asset = c.ident(item, "asset", p)
                if asset is None:
                    ok = False
                elif asset_ids is not None and asset not in asset_ids:
                    c.add("unknown_asset", f"{p}/asset", f"asset {asset!r} is not in assets")
                    ok = False
            if "source_in_sample" in item and "source_out_sample" in item:
                sin = c.integer(item, "source_in_sample", p, 0)
                sout = c.integer(item, "source_out_sample", p, 1)
                if sin is None or sout is None:
                    ok = False
                elif sout <= sin:
                    c.add("source_range_invalid", p, "source_out_sample must be greater than source_in_sample")
                    ok = False
            if "gain" in item:
                g = item["gain"]
                if c.keys(g, ("num", "den"), (), f"{p}/gain"):
                    gn = c.integer(g, "num", f"{p}/gain", 0, GAIN_MAX_RATIO * GAIN_MAX_DEN)
                    gd = c.integer(g, "den", f"{p}/gain", 1, GAIN_MAX_DEN)
                    if gn is None or gd is None:
                        ok = False
                    elif gn > GAIN_MAX_RATIO * gd:
                        c.add("gain_too_high", f"{p}/gain", f"linear gain {gn}/{gd} exceeds {GAIN_MAX_RATIO}")
                        ok = False
                else:
                    ok = False
            if "channel_rule" in item:
                rule = _enum(c, item, "channel_rule", CHANNEL_RULES, p)
                ok = ok and rule is not None
        if eid is None or sid is None:
            ok = False
        if ok:
            out.append(Event(eid, sid, state, asset, sin, sout, sf, ef, gn, gd, rule, refs.get("decision_ref"), refs.get("provenance_ref"), role))
        else:
            all_ok = False
    return out if all_ok else None
