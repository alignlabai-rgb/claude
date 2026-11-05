"""TEST-ONLY caller-admission REFERENCE CANDIDATE (UNREVIEWED v0). Not an application, binder, executor or production integration; nothing here is adopted by anyone.

`evaluate(expected, observed, profile)` is a pure function of synthetic VALUES: an owner-expected snapshot, observed stage packets and an EXPLICIT proposed profile. It reads no file,
environment variable, clock, process, or network; mutates none of its arguments; and has no action / execute flag. It returns a deterministic technical status (READY / HOLD /
REJECTED), structured reasons and the evidence of which comparisons were actually reached.

READY means exactly: "the supplied synthetic values are mutually consistent under the supplied UNREVIEWED profile". It is NOT production execution permission, NOT creative acceptance,
NOT authentication of any input (a self-consistent forgery passes: see not_claimed), and NOT verification of any real file (hashes are compared as values, never recomputed).

Precedence (documented, deterministic): any REJECT reason -> REJECTED (structural contradiction, wrong-typed data, pin / identity / selection / version mismatch);
else any HOLD reason -> HOLD (missing, unknown or unqualified state, untreated warning, not-ready status); else READY. Unresolved policy is never defaulted: a profile missing a
required field is HOLD, not silently completed.

Pinned sources (public, read-only): CONTRACT_JA.md and GAPS_AND_TASKS.md at integration branch a0acfdaf3409ff09e172865210e2878ad4962680; producer schemas at core
c7da5ac9f6444b954958ce9f587de2249fe06754 and input 91ec2d40f360e8019f357132a43c370ab66bb673 (see PINS.json). Source-contract ambiguities are listed in README.md instead of being decided.
"""

from __future__ import annotations

import re

SCHEMA = "caller_admission_reference/v0-unreviewed"
PROFILE_ID = "caller-admission-reference-v0"
PROFILE_VERSION = 0

STATES = ("USE", "OMIT", "INTENTIONAL_SILENCE", "MISSING", "UNKNOWN", "PROXY")
UNRESOLVED_STATES = ("MISSING", "UNKNOWN", "PROXY")
SELECTED_STATES_OK = ("USE", "OMIT", "INTENTIONAL_SILENCE")
IDENTITY_FIELDS = ("job_id", "work_id", "shot_id", "attempt_id", "selection_snapshot_ref")
PIN_KINDS = ("manifest_sha256", "plan_sha256", "receipt_sha256", "stem_sha256", "video_sha256")
STAGES = ("audio_plan", "audio_receipt", "video_receipt", "avmux_manifest_view")
NO_AUDIO_WARNING = "NO_AUDIO_EVENTS_SELECTED"
ZERO_FILL_WARNING = "ZERO_FILL_APPLIED"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_DEC = re.compile(r"^(0|[1-9][0-9]{0,11})$")
_OPAQUE_MAX = 128
MAX_DEPTH, MAX_NODES, MAX_STR, MAX_LIST, MAX_INT_BITS = 16, 100_000, 4096, 5000, 63
MAX_REPORTED_GAPS = 100  # producer constant (core audiomix/plan.py): the plan reports at most this many gaps and sets gaps_truncated
AUDIO_ROLES = ("stem", "stem_partial", "plan")
VIDEO_ROLES = ("video", "video_partial", "srt", "plan", "ffprobe_json")
PARTIAL_ROLES = ("stem_partial", "video_partial")
AUDIO_READBACK_CHECKS = ("channels", "sample_rate", "bits", "sample_count", "file_bytes")
VIDEO_READBACK_CHECKS = ("video_stream_count", "non_video_stream_count", "width", "height", "r_frame_rate", "avg_frame_rate", "decoded_frame_count", "format_duration", "video_duration", "codec_name", "pix_fmt")
VIDEO_EXACT_INT_CHECKS = ("video_stream_count", "non_video_stream_count", "width", "height", "decoded_frame_count", "container_frame_count")
VIDEO_EXACT_STR_CHECKS = ("codec_name", "pix_fmt")

# The proposed profile. It is a VALUE the caller must pass explicitly; evaluate() has no default for it.
PROFILE_V0_UNREVIEWED = {
    "profile_id": PROFILE_ID,
    "profile_version": PROFILE_VERSION,
    "review_status": "UNREVIEWED",
    "required_stages": ["audio_plan", "audio_receipt", "video_receipt", "avmux_manifest_view"],
    "required_pins": list(PIN_KINDS),
    "allowed_pin_sources": ["owner_record"],
    "known_schemas": {"audio_plan": ["video_core.audio_plan/1"], "audio_receipt": ["video_core.audio_receipt/1"], "video_receipt": ["video_core.receipt/1"]},
    "plan_ready_statuses": ["READY"],
    "receipt_accepted_statuses": {"audio_receipt": ["MIXED_VERIFIED"], "video_receipt": ["READBACK_MATCH"]},
    "receipt_known_statuses": {
        "audio_receipt": ["MIXED_VERIFIED", "MIXED_WITH_CLAMPING", "CLIPPING_REJECTED", "TIMEOUT", "CANCELLED", "INTERRUPTED", "READBACK_MISMATCH", "UNKNOWN", "HOLD", "IN_PROGRESS"],
        "video_receipt": ["READBACK_MATCH", "READBACK_MISMATCH", "INSPECTION_FAILED", "PROCESS_FAILED", "TIMEOUT", "SIGNALED", "FLOOD_KILLED", "INTERRUPTED", "LAUNCH_FAILED",
                          "CONTAINMENT_REFUSED", "UNKNOWN", "IN_PROGRESS", "SKIPPED_TOOL_UNAVAILABLE"]},
    "exit_codes_ok": {"audio_plan": [0], "audio_receipt": [0], "video_receipt": [0]},
    "treated_warning_codes": [],
    "require_decision_ref_for_silence": True,
    "require_binding_for_use": True,
    "required_gap_policy": "REQUIRE_FULL_COVERAGE",
    "empty_effective_scope": "HOLD",
    "required_audio_readback_checks": list(AUDIO_READBACK_CHECKS),
    "required_video_readback_checks": list(VIDEO_READBACK_CHECKS),
}

_PROFILE_FIELDS = tuple(PROFILE_V0_UNREVIEWED)
_NOT_CLAIMED = (
    "production execution permission (no execute flag exists; permission is an owner record outside this function)",
    "creative or quality acceptance (quality_evaluated stays false in every producer receipt)",
    "authenticity or provenance of any input: a self-consistent forged set of values is indistinguishable from a genuine one by value comparison",
    "verification of any real file: hashes are compared as supplied values and never recomputed; no file was read",
    "binding adoption, selection authority, or timing/phrase-boundary qualification (GAP-06 has no carrier)",
)
CHECKED_V0 = (
    "profile equals the exact bounded v0 profile (any changed policy under the same id/version is REJECTED, not honoured)",
    "input is bounded JSON-only data (depth, size, string and integer bounds; no NaN/inf, no lone surrogates)",
    "owner identity (job/work/shot/attempt/selection snapshot) equals the identity envelope of every stage packet",
    "audio plan: schema, status vs exit code, row structure for ALL rows, scope = selected rows, selected-row states and bindings vs the owner snapshot, warnings, hold, unresolved list, timeline (fps, frames, exact sample formula), output geometry, coverage consistency",
    "audio receipt: schema, status vs exit code, written/stage/quality flags, inputs_unchanged, plan/manifest links, outputs (whole list: shape, roles, bytes, sha256, partial flags, plan output == plan_sha256, stem bytes == plan geometry), readback checks and wav facts, counts, warnings, unresolved report == plan unresolved",
    "video receipt: schema, status vs exit code, flags, plan link, outputs (whole list; plan output == plan_sha256), required readback checks present/ok/consistent (decoded_frame_count observed string must equal expected), frame count == audio timeline frames",
    "pins: manifest, plan, receipt, stem, video SHA-256 values compared with every observed source of that kind",
)
NOT_CHECKED_V0 = (
    {"id": "producer_diagnostics", "what": "audio plan mode/observations/caller_assertions, receipt note/platform/limits/timestamps/executions", "why": "diagnostic text and environment facts; not part of the v0 comparison contract"},
    {"id": "event_mix_detail", "what": "audio plan row mix detail beyond the selected USE row's own field shapes: that source_in/out lengths match the row's sample range, frame and sample ranges, the opaque source_id value, plan.policy.overlap/clipping", "why": "producer-verified mixing geometry; the candidate trusts the producer's coverage/gap summary only after internal-consistency checks (it does not recompute mixing)"},
    {"id": "asset_level_hashes", "what": "audio receipt input.assets[] per-asset hashes and the plan's asset table", "why": "the owner pins five artefacts only (manifest, plan, receipt, stem, video); per-asset pins are an owner decision (SIT-04)"},
    {"id": "video_tolerance_checks", "what": "video fps and duration comparison semantics (r_frame_rate, avg_frame_rate, format_duration, video_duration) and readback.summary", "why": "the producer's own rational / tolerance logic is trusted: only presence, scalar shape and ok=true are checked"},
    {"id": "video_side_outputs", "what": "video srt / ffprobe_json output hashes", "why": "not pinned by the owner; their entries are still structurally validated"},
    {"id": "other_output_sizes_and_names", "what": "byte sizes and file names of every output except the stem (sizes of the plan output, the video, srt and ffprobe_json; their names)", "why": "nothing independent to compare them with; each is still validated as a non-negative integer / bounded string"},
    {"id": "file_reality", "what": "existence, content or hash of any real file", "why": "pure value comparison: no file is read or hashed"},
    {"id": "authenticity", "what": "who produced the values; self-consistent forgery", "why": "cannot be established by comparing values (GAP-04)"},
    {"id": "timing_qualification", "what": "phrase-boundary / sample-timing qualification", "why": "no carrier exists (GAP-06)"},
    {"id": "owner_snapshot_label", "what": "expected.snapshot_ref", "why": "informational owner label echoed in evidence; nothing observed carries it to compare with"},
    {"id": "unknown_producer_keys", "what": "producer response keys this candidate does not name", "why": "forward compatibility; only the named fields above are interpreted"},
)
READY_SCOPE = "READY applies only to the synthetic snapshot values supplied to this call under the supplied UNREVIEWED profile"


# ----------------------------------------------------------------------------------------------------------------------------------- value helpers
def _is_int(v) -> bool:
    return type(v) is int  # bool and float are not integer counts


def _is_str(v, lo: int = 1, hi: int = _OPAQUE_MAX) -> bool:
    return type(v) is str and lo <= len(v) <= hi


def _is_sha(v) -> bool:
    return type(v) is str and bool(_SHA.match(v))


def _dict(v) -> bool:
    return type(v) is dict


def _list(v) -> bool:
    return type(v) is list


def _dig(obj, *path):
    """Value at path or the sentinel _ABSENT; never raises."""
    cur = obj
    for key in path:
        if not _dict(cur) or key not in cur:
            return _ABSENT
        cur = cur[key]
    return cur


class _Absent:
    def __repr__(self):
        return "<absent>"


_ABSENT = _Absent()


def _show(v):
    """A bounded, JSON-safe rendering of a value for evidence (never echoes huge or exotic objects)."""
    if v is _ABSENT:
        return "<absent>"
    if v is None or type(v) in (bool, int, float):
        return v
    if type(v) is str:
        return v if len(v) <= 80 else v[:77] + "..."
    return f"<{type(v).__name__}>"


class _Ctx:
    def __init__(self):
        self.reasons: list = []
        self.checks: list = []
        self.evidence: dict = {}

    def add(self, severity, code, path, detail, expected=_ABSENT, observed=_ABSENT):
        r = {"severity": severity, "code": code, "path": path, "detail": detail}
        if expected is not _ABSENT:
            r["expected"] = _show(expected)
        if observed is not _ABSENT:
            r["observed"] = _show(observed)
        self.reasons.append(r)

    def reject(self, code, path, detail, **kw):
        self.add("REJECT", code, path, detail, **kw)

    def hold(self, code, path, detail, **kw):
        self.add("HOLD", code, path, detail, **kw)

    def run(self, check_id, fn, *args):
        before = len(self.reasons)
        fn(*args)
        self.checks.append({"id": check_id, "outcome": "FAIL" if len(self.reasons) > before else "PASS"})

    def skip(self, check_id, why):
        self.checks.append({"id": check_id, "outcome": "SKIPPED", "why": why})


# ----------------------------------------------------------------------------------------------------------------------------------- profile
def _k(key) -> str:
    """A user-controlled dict key as it may appear in a path: bounded."""
    return key if len(key) <= 64 else key[:61] + "..."


def _check_bounds(ctx: _Ctx, **roots) -> bool:
    """Every input must be bounded JSON-only data, so that evaluation terminates, the output is JSON-safe and nothing exotic (tuples, sets, objects, NaN, lone surrogates, cycles) is ever interpreted."""
    for name, root in roots.items():
        stack, nodes = [(root, 0)], 0
        while stack:
            v, depth = stack.pop()
            nodes += 1
            if nodes > MAX_NODES:
                ctx.reject("INPUT_TOO_LARGE", name, f"more than {MAX_NODES} values")
                return False
            if depth > MAX_DEPTH:
                ctx.reject("INPUT_TOO_DEEP", name, f"nesting deeper than {MAX_DEPTH} (also how cycles are caught)")
                return False
            t = type(v)
            if v is None or t is bool:
                continue
            if t is int:
                if v.bit_length() > MAX_INT_BITS:
                    ctx.reject("SHAPE_NOT_JSON", name, "integer outside the 64-bit range")
                    return False
            elif t is float:
                if not (v == v and v - v == 0.0):
                    ctx.reject("SHAPE_NOT_JSON", name, "NaN and infinity are not JSON values")
                    return False
            elif t is str:
                if len(v) > MAX_STR:
                    ctx.reject("INPUT_TOO_LARGE", name, f"string longer than {MAX_STR} characters")
                    return False
                try:
                    v.encode("utf-8")
                except UnicodeEncodeError:
                    ctx.reject("SHAPE_NOT_JSON", name, "string is not valid Unicode text (lone surrogate)")
                    return False
            elif t is list:
                if len(v) > MAX_LIST:
                    ctx.reject("INPUT_TOO_LARGE", name, f"list longer than {MAX_LIST}")
                    return False
                stack.extend((x, depth + 1) for x in v)
            elif t is dict:
                if len(v) > MAX_LIST:
                    ctx.reject("INPUT_TOO_LARGE", name, f"object with more than {MAX_LIST} keys")
                    return False
                for key in v:
                    if type(key) is not str or len(key) > MAX_STR:
                        ctx.reject("SHAPE_NOT_JSON", name, "object keys must be strings")
                        return False
                    try:
                        key.encode("utf-8")
                    except UnicodeEncodeError:
                        ctx.reject("SHAPE_NOT_JSON", name, "object key is not valid Unicode text")
                        return False
                stack.extend((x, depth + 1) for x in v.values())
            else:
                ctx.reject("SHAPE_NOT_JSON", name, f"{t.__name__} is not a JSON value")
                return False
    return True


def _strict_equal(a, b) -> bool:
    """Deep equality that does not conflate True/1/1.0 or list/tuple."""
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(_strict_equal(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(_strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def _check_profile(ctx: _Ctx, profile) -> bool:
    """The only profile this reference honours is the exact bounded v0 value. Same id and version with ANY changed, extra or malformed policy value is REJECTED, never honoured:
    a caller cannot relax policy (treat a warning, drop a required receipt, ...) under a known identity. Future profiles are owner decisions with their own ids."""
    if not _dict(profile):
        ctx.hold("PROFILE_NOT_SUPPLIED", "profile", "no explicit profile object was supplied; policy is never defaulted", observed=profile)
        return False
    pid, ver = profile.get("profile_id", _ABSENT), profile.get("profile_version", _ABSENT)
    if pid is not _ABSENT and pid != PROFILE_ID:
        ctx.reject("PROFILE_ID_UNKNOWN", "profile.profile_id", "this reference knows exactly one proposed profile", expected=PROFILE_ID, observed=pid)
        return False
    if ver is not _ABSENT and (not _is_int(ver) or ver != PROFILE_VERSION):
        ctx.reject("PROFILE_VERSION_MISMATCH", "profile.profile_version", "profile version differs from the one this reference implements", expected=PROFILE_VERSION, observed=ver)
        return False
    missing = [k for k in _PROFILE_FIELDS if k not in profile]
    if missing:
        ctx.hold("PROFILE_INCOMPLETE", "profile", f"unresolved policy fields: {', '.join(missing)}; none is defaulted")
        return False
    changed = sorted([k for k in _PROFILE_FIELDS if not _strict_equal(profile[k], PROFILE_V0_UNREVIEWED[k])] + [_k(k) for k in profile if k not in PROFILE_V0_UNREVIEWED])
    if changed:
        ctx.reject("PROFILE_POLICY_MODIFIED", "profile", f"policy differs from the exact v0 profile under the same id/version (changed or unsupported: {', '.join(changed)}); "
                   "this reference does not honour caller-modified policy")
        return False
    ctx.evidence["profile"] = {"profile_id": profile["profile_id"], "profile_version": profile["profile_version"], "review_status": profile["review_status"]}
    return True


# ----------------------------------------------------------------------------------------------------------------------------------- expected snapshot
def _identity_ok(ctx: _Ctx, ident, path: str) -> bool:
    if not _dict(ident):
        ctx.reject("SHAPE_IDENTITY", path, "identity must be an object", observed=ident)
        return False
    ok = True
    for f in IDENTITY_FIELDS:
        if f not in ident:
            ctx.hold("IDENTITY_FIELD_MISSING", f"{path}.{f}", "identity field absent")
            ok = False
        elif not _is_str(ident[f]):
            ctx.reject("SHAPE_IDENTITY", f"{path}.{f}", "identity field must be a 1..128 character string", observed=ident[f])
            ok = False
    for extra in sorted(k for k in ident if k not in IDENTITY_FIELDS):
        ctx.reject("SHAPE_IDENTITY", f"{path}.{_k(extra)}", "unknown identity field", observed=extra)
        ok = False
    return ok


def _check_expected(ctx: _Ctx, profile, expected) -> dict | None:
    """Returns a normalized view or None when the snapshot cannot be compared."""
    if expected is None:
        ctx.hold("EXPECTED_MISSING", "expected", "no owner-expected snapshot was supplied: there is nothing to compare against")
        return None
    if not _dict(expected):
        ctx.reject("SHAPE_EXPECTED", "expected", "owner-expected snapshot must be an object", observed=expected)
        return None
    ok = True
    for extra in sorted(k for k in expected if k not in ("snapshot_ref", "identity", "pins_source", "pins", "rows")):
        ctx.reject("SHAPE_EXPECTED", f"expected.{_k(extra)}", "unknown field in the owner snapshot", observed=extra)
        ok = False
    if "snapshot_ref" in expected:
        if not _is_str(expected["snapshot_ref"]):
            ctx.reject("SHAPE_EXPECTED", "expected.snapshot_ref", "owner snapshot label must be a 1..128 character string", observed=expected["snapshot_ref"])
            ok = False
        else:
            ctx.evidence["owner_snapshot_ref"] = expected["snapshot_ref"]
    if "identity" not in expected:
        ctx.hold("EXPECTED_IDENTITY_MISSING", "expected.identity", "owner snapshot carries no identity")
        ok = False
    else:
        ok = _identity_ok(ctx, expected["identity"], "expected.identity") and ok
    pins = expected.get("pins", _ABSENT)
    pins_ok = True
    if pins is _ABSENT:
        ctx.hold("EXPECTED_PIN_MISSING", "expected.pins", "owner snapshot carries no pins")
        pins_ok = False
    elif not _dict(pins):
        ctx.reject("SHAPE_PINS", "expected.pins", "pins must be an object", observed=pins)
        pins_ok = False
    else:
        for k in sorted(pins):
            if k not in PIN_KINDS:
                ctx.reject("SHAPE_PINS", f"expected.pins.{_k(k)}", "unknown pin kind", observed=k)
                pins_ok = False
            elif not _is_sha(pins[k]):
                ctx.reject("SHAPE_PINS", f"expected.pins.{k}", "pin must be a 64-character lowercase hex SHA-256 value", observed=pins[k])
                pins_ok = False
        for k in profile["required_pins"]:
            if k not in pins:
                ctx.hold("EXPECTED_PIN_MISSING", f"expected.pins.{k}", "a pin required by the profile is absent from the owner snapshot")
                pins_ok = False
    src = expected.get("pins_source", _ABSENT)
    if src is _ABSENT:
        ctx.hold("PIN_SOURCE_NOT_INDEPENDENT", "expected.pins_source", "the owner snapshot does not declare where its pins came from (declaration only; not provable by value comparison)")
    elif src not in profile["allowed_pin_sources"]:
        ctx.hold("PIN_SOURCE_NOT_INDEPENDENT", "expected.pins_source", "pins declared as not coming from an independent owner record", expected=profile["allowed_pin_sources"], observed=src)
    rows = expected.get("rows", _ABSENT)
    row_view = None
    if rows is _ABSENT:
        ctx.hold("EXPECTED_ROWS_MISSING", "expected.rows", "owner snapshot carries no row set")
    elif not _list(rows):
        ctx.reject("SHAPE_ROWS", "expected.rows", "rows must be a list", observed=rows)
    else:
        row_view, seen = {}, set()
        for i, row in enumerate(rows):
            p = f"expected.rows[{i}]"
            if not _dict(row) or not _is_str(row.get("row_id")) or type(row.get("selected")) is not bool or row.get("state") not in STATES \
                    or any(k not in ("row_id", "selected", "state", "binding_ref", "decision_ref") for k in row):
                ctx.reject("SHAPE_ROWS", p, "row needs a string row_id, a boolean selected, a known state and no unknown fields", observed=row)
                row_view = None
                break
            if row["row_id"] in seen:
                ctx.reject("SHAPE_ROWS", p, "duplicate row_id", observed=row["row_id"])
                row_view = None
                break
            for opt in ("binding_ref", "decision_ref"):
                if opt in row and not _is_str(row[opt]):
                    ctx.reject("SHAPE_ROWS", f"{p}.{opt}", "opaque reference must be a 1..128 character string", observed=row[opt])
                    row_view = None
            if row_view is None:
                break
            seen.add(row["row_id"])
            row_view[row["row_id"]] = row
    if not (ok and pins_ok) or row_view is None:
        return None
    return {"identity": expected["identity"], "pins": expected["pins"], "rows": row_view}


# ----------------------------------------------------------------------------------------------------------------------------------- observed packets
def _stage_packet(ctx: _Ctx, name: str, packet, needs_exit: bool):
    """Common envelope checks. Returns the packet when its envelope is usable."""
    path = f"observed.stages.{name}"
    if not _dict(packet):
        ctx.reject("SHAPE_STAGE", path, "stage packet must be an object", observed=packet)
        return None
    ok = True
    allowed = {"audio_plan": ("identity", "exit_code", "response"), "audio_receipt": ("identity", "exit_code", "response", "receipt_sha256"),
               "video_receipt": ("identity", "exit_code", "response"), "avmux_manifest_view": ("identity", "selection_ref", "video_sha256", "audio_sha256")}[name]
    for extra in sorted(k for k in packet if k not in allowed):
        ctx.reject("SHAPE_STAGE", f"{path}.{_k(extra)}", "unknown field in the stage packet envelope", observed=extra)
        ok = False
    if "identity" not in packet:
        ctx.hold("IDENTITY_UNBOUND", f"{path}.identity", "the packet carries no caller identity envelope: it cannot be tied to this job/work/shot/attempt")
    elif not _identity_ok(ctx, packet["identity"], f"{path}.identity"):
        ok = False
    if needs_exit:
        if not _is_int(packet.get("exit_code")):
            ctx.reject("SHAPE_EXIT_CODE", f"{path}.exit_code", "exit_code must be an integer (bool and float are not accepted)", observed=packet.get("exit_code", _ABSENT))
            ok = False
        if not _dict(packet.get("response")) or not packet.get("response"):
            ctx.reject("RESPONSE_UNSTRUCTURED", f"{path}.response", "response is empty or not a JSON object: exit code alone is never readiness", observed=packet.get("response", _ABSENT))
            ok = False
    return packet if ok else None


def _compare_identity(ctx: _Ctx, name: str, packet, expected_identity) -> None:
    ident = packet.get("identity") if _dict(packet) else None
    if not _dict(ident):
        return
    for f in IDENTITY_FIELDS:
        if f in ident and _is_str(ident[f]) and ident[f] != expected_identity[f]:
            ctx.reject("IDENTITY_MISMATCH", f"observed.stages.{name}.identity.{f}", "stage packet belongs to a different job/work/shot/attempt/selection snapshot",
                       expected=expected_identity[f], observed=ident[f])


def _schema_check(ctx: _Ctx, profile, name: str, schema, path: str) -> None:
    known = profile["known_schemas"].get(name, [])
    if schema in known:
        return
    if _is_str(schema, 1, 200):
        fam = schema.rsplit("/", 1)[0]
        if any(k.rsplit("/", 1)[0] == fam for k in known):
            ctx.reject("SCHEMA_VERSION_MISMATCH", path, "known schema family, different version", expected=known, observed=schema)
            return
    ctx.hold("SCHEMA_UNKNOWN", path, "schema is not one this profile knows", expected=known, observed=schema)


def _status_and_exit(ctx: _Ctx, profile, name: str, packet, status, ready_list, known_list) -> None:
    """Readiness is the status, not the exit code; contradictions between the two are REJECT, a parseable non-ready status is HOLD."""
    path = f"observed.stages.{name}"
    if not _is_str(status, 1, 80):
        ctx.reject("SHAPE_STATUS", f"{path}.response.status", "status must be a string", observed=status)
        return
    ok_codes = profile["exit_codes_ok"].get(name, [0])
    code_ok = packet["exit_code"] in ok_codes
    ready = status in ready_list
    if ready and not code_ok:
        ctx.reject("CODE_STATUS_CONTRADICTION", path, "a ready status with a non-accepted exit code", expected=ok_codes, observed=packet["exit_code"])
    elif not ready:
        known = status in known_list
        ctx.hold("STATUS_NOT_ACCEPTED" if known else "STATUS_UNKNOWN", f"{path}.response.status",
                 "parseable status is not an accepted ready status" if known else "status string is not one this profile knows", expected=ready_list, observed=status)
        if not code_ok:
            ctx.hold("EXIT_CODE_NOT_OK", f"{path}.exit_code", "non-accepted exit code", expected=ok_codes, observed=packet["exit_code"])


# ----------------------------------------------------------------------------------------------------------------------------------- audio plan
def _check_audio_plan(ctx: _Ctx, profile, expected, packet) -> dict | None:
    name, path = "audio_plan", "observed.stages.audio_plan"
    resp = packet["response"]
    plan = resp.get("plan", _ABSENT)
    if not _dict(plan):
        ctx.reject("SHAPE_PLAN", f"{path}.response.plan", "plan object missing or not an object", observed=plan)
        return None
    _schema_check(ctx, profile, name, plan.get("schema"), f"{path}.response.plan.schema")
    _status_and_exit(ctx, profile, name, packet, resp.get("status"), profile["plan_ready_statuses"], ["READY", "HOLD"])
    if resp.get("status") != plan.get("status"):
        ctx.reject("INCONSISTENT_PLAN", f"{path}.response.status", "outer and inner plan status disagree", expected=plan.get("status", _ABSENT), observed=resp.get("status", _ABSENT))
    if not _is_sha(resp.get("plan_sha256")):
        ctx.reject("SHAPE_PIN", f"{path}.response.plan_sha256", "plan_sha256 must be a 64-character lowercase hex value", observed=resp.get("plan_sha256", _ABSENT))
    total = _dig(plan, "timeline", "total_samples")
    frames = _dig(plan, "timeline", "total_frames")
    for label, v in (("timeline.total_samples", total), ("timeline.total_frames", frames)):
        if not _is_int(v) or v < 1:
            ctx.reject("SHAPE_COUNT", f"{path}.response.plan.{label}", "count must be a positive integer (bool and float are not accepted)", observed=v)
    events, scope, hold, warns, unres = (plan.get(k, _ABSENT) for k in ("events", "scope", "hold", "warnings", "unresolved"))
    for label, v in (("events", events), ("scope", scope), ("hold", hold), ("warnings", warns), ("unresolved", unres)):
        if not _list(v):
            ctx.reject("SHAPE_PLAN", f"{path}.response.plan.{label}", f"{label} must be a list", observed=v)
    if not all(_list(v) for v in (events, scope, hold, warns, unres)):
        return None
    # whole-row structural validation: EVERY row, selected or not
    rows, seen = {}, set()
    for i, e in enumerate(events):
        p = f"{path}.response.plan.events[{i}]"
        if (not _dict(e) or not _is_str(e.get("id"), 1, 64) or not _is_str(e.get("source_id"), 1, 64) or e.get("state") not in STATES or type(e.get("selected")) is not bool
                or not _is_str(e.get("disposition"), 1, 40)):
            ctx.reject("SHAPE_ROW", p, "row needs an id, a source_id, a known state, a boolean selected and a disposition", observed=e)
            return None
        if e["id"] in seen:
            ctx.reject("SHAPE_ROW", p, "duplicate row id", observed=e["id"])
            return None
        if e["state"] in UNRESOLVED_STATES:
            want_disp = "UNRESOLVED_SELECTED" if e["selected"] else "UNRESOLVED_NOT_SELECTED"
        else:
            want_disp = None if e["selected"] else "NOT_SELECTED"  # selected resolved rows are checked in SELECTION (MIX / SILENCE / OMITTED)
        if want_disp is not None and e["disposition"] != want_disp:
            ctx.reject("ROW_DISPOSITION_MISMATCH", p, "disposition contradicts the row's state / selection", expected=want_disp, observed=e["disposition"])
            return None
        for opt in ("decision_ref", "provenance_ref"):
            if opt in e and not _is_str(e[opt]):
                ctx.reject("SHAPE_ROW", f"{p}.{opt}", "opaque reference must be a 1..128 character string", observed=e[opt])
                return None
        seen.add(e["id"])
        rows[e["id"]] = e
    if not all(_is_str(s, 1, 64) for s in scope) or len(set(scope)) != len(scope):
        ctx.reject("SHAPE_PLAN", f"{path}.response.plan.scope", "scope must be a list of unique row ids", observed=scope)
        return None
    selected_ids = {i for i, e in rows.items() if e["selected"]}
    if set(scope) != selected_ids:
        ctx.reject("INCONSISTENT_PLAN", f"{path}.response.plan.scope", "scope and the rows flagged selected disagree", expected=sorted(selected_ids), observed=sorted(scope))
    return {"rows": rows, "scope": set(scope), "selected": selected_ids, "hold": hold, "warnings": warns, "unresolved": unres, "total_samples": total, "total_frames": frames,
            "plan": plan, "sha": resp.get("plan_sha256"), "status": resp.get("status")}


def _reduced(n: int, d: int) -> str:
    a, b = n, d
    while b:
        a, b = b, a % b
    return f"{n // a}/{d // a}"


def _check_plan_geometry(ctx: _Ctx, view, warning_codes, policy) -> None:
    """Timeline arithmetic, output geometry and coverage evidence: the plan's own numbers must agree with each other and with the producer's documented formulas."""
    path = "observed.stages.audio_plan.response.plan"
    plan, total, frames = view["plan"], view["total_samples"], view["total_frames"]
    n, d = _dig(plan, "timeline", "fps", "n"), _dig(plan, "timeline", "fps", "d")
    if not (_is_int(n) and _is_int(d) and n >= 1 and d >= 1):
        ctx.reject("SHAPE_COUNT", f"{path}.timeline.fps", "fps needs integer n >= 1 and d >= 1", observed=_dig(plan, "timeline", "fps"))
    elif _dig(plan, "timeline", "fps", "rational") != _reduced(n, d):
        ctx.reject("TIMELINE_INCONSISTENT", f"{path}.timeline.fps.rational", "rational must be the reduced n/d", expected=_reduced(n, d), observed=_dig(plan, "timeline", "fps", "rational"))
    elif _is_int(total) and _is_int(frames) and total != (2 * frames * d * 48000 + n) // (2 * n):
        ctx.reject("TIMELINE_INCONSISTENT", f"{path}.timeline.total_samples", "total_samples differs from floor((2*frames*d*48000+n)/(2*n)) for the stated fps and frames",
                   expected=(2 * frames * d * 48000 + n) // (2 * n), observed=total)
    out = plan.get("output", _ABSENT)
    view["out"] = None
    if out is _ABSENT or out is None:
        ctx.hold("OUTPUT_GEOMETRY_MISSING", f"{path}.output", "the plan carries no output geometry")
    elif not _dict(out) or not _is_str(out.get("file"), 1, 64) or not all(_is_int(out.get(k)) for k in ("sample_rate", "channels", "bits", "samples", "bytes")):
        ctx.reject("SHAPE_PLAN", f"{path}.output", "output needs a file name and integer sample_rate/channels/bits/samples/bytes", observed=out)
    elif out["sample_rate"] != 48000 or out["bits"] != 16 or out["channels"] not in (1, 2):
        ctx.reject("OUTPUT_GEOMETRY_INCONSISTENT", f"{path}.output", "v0 geometry is 48000 Hz, 16 bit, 1 or 2 channels", observed=[out["sample_rate"], out["bits"], out["channels"]])
    elif _is_int(total) and (out["samples"] != total or out["bytes"] != 44 + total * out["channels"] * 2):
        ctx.reject("OUTPUT_GEOMETRY_INCONSISTENT", f"{path}.output", "output samples/bytes disagree with total_samples (bytes = 44 + samples*channels*2)",
                   expected={"samples": total, "bytes": 44 + total * out["channels"] * 2}, observed={"samples": out["samples"], "bytes": out["bytes"]})
    else:
        view["out"] = out
    cov = plan.get("coverage", _ABSENT)
    if cov is _ABSENT or cov is None:
        ctx.hold("COVERAGE_EVIDENCE_MISSING", f"{path}.coverage", "the plan carries no coverage evidence")
        return
    covered, gap_count, gaps, trunc = (cov.get(k, _ABSENT) if _dict(cov) else _ABSENT for k in ("covered_samples", "gap_count", "gaps", "gaps_truncated"))
    if not (_is_int(covered) and _is_int(gap_count) and _list(gaps) and type(trunc) is bool) or covered < 0 or gap_count < 0 or len(gaps) > MAX_REPORTED_GAPS:
        ctx.reject("SHAPE_COVERAGE", f"{path}.coverage", "coverage needs integer covered_samples/gap_count >= 0, a gaps list (<= 100 entries) and a boolean gaps_truncated", observed=cov)
        return
    listed, prev_end = 0, 0
    for i, g in enumerate(gaps):
        if (not _dict(g) or set(g) != {"sample_start", "sample_end"} or not _is_int(g["sample_start"]) or not _is_int(g["sample_end"]) or g["sample_start"] < prev_end
                or g["sample_end"] <= g["sample_start"] or (_is_int(total) and g["sample_end"] > total)):
            ctx.reject("SHAPE_COVERAGE", f"{path}.coverage.gaps[{i}]", "gaps must be ordered, non-overlapping integer ranges inside the timeline", observed=g)
            return
        listed += g["sample_end"] - g["sample_start"]
        prev_end = g["sample_end"]
    if not _is_int(total):
        return
    problems = []
    if covered > total:
        problems.append("covered_samples exceeds total_samples")
    if gap_count == 0 and covered != total:
        problems.append("gap_count is 0 but covered_samples is not total_samples")
    if gap_count > 0 and covered >= total:
        problems.append("gaps are reported but covered_samples is not below total_samples")
    if trunc != (gap_count > len(gaps)) or len(gaps) != min(gap_count, MAX_REPORTED_GAPS):
        problems.append("gap list length / gaps_truncated disagree with gap_count")
    elif not trunc and total - listed != covered:
        problems.append("covered_samples plus the listed gaps is not total_samples")
    elif trunc and covered > total - listed:
        problems.append("covered_samples exceeds what the listed gaps leave uncovered")
    if problems:
        ctx.reject("COVERAGE_INCONSISTENT", f"{path}.coverage", "; ".join(problems), expected={"total_samples": total}, observed={"covered_samples": covered, "gap_count": gap_count})
        return
    view["gap_count"] = gap_count
    if gap_count > 0:
        ctx.hold("INCOMPLETE_COVERAGE", f"{path}.coverage.gap_count", "selected rows do not cover the timeline", observed=gap_count)
        if policy == "ALLOW_ZERO_FILL" and ZERO_FILL_WARNING not in warning_codes:
            ctx.reject("INCONSISTENT_PLAN", f"{path}.warnings", "gaps under ALLOW_ZERO_FILL without the producer's ZERO_FILL_APPLIED warning")


def _check_plan_hold_and_warnings(ctx: _Ctx, profile, view) -> None:
    path = "observed.stages.audio_plan.response.plan"
    plan = view["plan"]
    if view["hold"]:
        if view["status"] == "READY":
            ctx.reject("INCONSISTENT_PLAN", f"{path}.hold", "status READY while the plan lists hold entries", observed=len(view["hold"]))
        for h in view["hold"]:
            ctx.hold("PLAN_HOLD_PRESERVED", f"{path}.hold", "producer hold entry preserved", observed=h.get("code", _ABSENT) if _dict(h) else h)
    for i, h in enumerate(view["hold"]):
        if not _dict(h) or not _is_str(h.get("code"), 1, 80):
            ctx.reject("SHAPE_HOLD", f"{path}.hold[{i}]", "hold entry needs a string code", observed=h)
    for i, w in enumerate(view["warnings"]):
        if not _dict(w) or not _is_str(w.get("code"), 1, 80):
            ctx.reject("SHAPE_WARNING", f"{path}.warnings[{i}]", "warning needs a string code", observed=w)
            continue
        if w["code"] not in profile["treated_warning_codes"]:
            ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]", "warning preserved and not treated by the profile: a warning is never silently accepted", observed=w["code"])
    codes = {w.get("code") for w in view["warnings"] if _dict(w)}
    sel_use = [i for i in view["selected"] if view["rows"][i]["state"] == "USE"]
    if not sel_use and NO_AUDIO_WARNING not in codes:
        ctx.reject("INCONSISTENT_PLAN", f"{path}.warnings", "no selected USE row but the producer's NO_AUDIO_EVENTS_SELECTED warning is absent")
    policy = _dig(plan, "policy", "gap")
    if policy != profile["required_gap_policy"]:
        ctx.hold("POLICY_GAP_NOT_REQUIRED", f"{path}.policy.gap", "plan policy differs from the profile's required gap policy (zero-fill must not be silently allowed)", expected=profile["required_gap_policy"], observed=policy)
    _check_plan_geometry(ctx, view, codes, policy)
    # unresolved list must agree with the rows
    listed = {}
    for i, u in enumerate(view["unresolved"]):
        if (not _dict(u) or set(u) != {"event_id", "source_id", "state", "selected"} or not _is_str(u.get("event_id"), 1, 64) or not _is_str(u.get("source_id"), 1, 64)
                or u.get("state") not in UNRESOLVED_STATES or type(u.get("selected")) is not bool or u["event_id"] in listed):
            ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved[{i}]", "unresolved entry needs exactly event_id, source_id, an unresolved state and a boolean selected (unique event_id)", observed=u)
            return
        listed[u["event_id"]] = u
    view["unresolved_map"] = listed
    rows = view["rows"]
    actual = {i: e for i, e in rows.items() if e["state"] in UNRESOLVED_STATES}
    if set(listed) != set(actual) or any(listed[i]["selected"] != rows[i]["selected"] or listed[i]["state"] != rows[i]["state"] or listed[i]["source_id"] != rows[i]["source_id"]
                                         for i in listed if i in rows):
        ctx.reject("INCONSISTENT_PLAN", f"{path}.unresolved", "unresolved list and the rows disagree", expected=sorted(actual), observed=sorted(listed))
    if view["status"] == "READY" and any(rows[i]["selected"] for i in actual):
        ctx.reject("INCONSISTENT_PLAN", f"{path}.status", "status READY while a selected row is unresolved")


def _check_selection(ctx: _Ctx, profile, expected, view) -> None:
    """Selected-scope meaning. Whole-row structure is already validated; here only SELECTED rows carry meaning."""
    # every row the owner lists (selected or not) must exist in the plan with the same state; unselected rows are only compared, never made blockers
    for rid in sorted(expected["rows"]):
        er = expected["rows"][rid]
        orow = view["rows"].get(rid)
        pth = f"observed.stages.audio_plan.response.plan.events[{_k(rid)}]"
        if orow is None:
            ctx.reject("ROW_MISSING_IN_PLAN", pth, "the owner snapshot lists a row the plan does not carry", observed=rid)
        elif not er["selected"]:
            if orow["state"] != er["state"]:
                ctx.reject("ROW_STATE_MISMATCH", pth, "unselected row state differs from the owner snapshot", expected=er["state"], observed=orow["state"])
            elif "binding_ref" in er and "provenance_ref" in orow and orow["provenance_ref"] != er["binding_ref"]:
                ctx.reject("BINDING_MISMATCH", pth, "binding reference of an unselected row differs", expected=er["binding_ref"], observed=orow["provenance_ref"])
    exp_sel = {i: r for i, r in expected["rows"].items() if r["selected"]}
    obs_sel = view["selected"]
    if set(exp_sel) != obs_sel:
        ctx.reject("SELECTION_SET_MISMATCH", "observed.stages.audio_plan.response.plan.scope", "selected row set differs from the owner snapshot",
                   expected=sorted(exp_sel), observed=sorted(obs_sel))
        return
    for rid in sorted(exp_sel):
        e, o = exp_sel[rid], view["rows"][rid]
        p = f"observed.stages.audio_plan.response.plan.events[{rid}]"
        if e["state"] in UNRESOLVED_STATES:
            ctx.hold("SELECTED_UNRESOLVED", p, "owner snapshot itself selects an unresolved row", observed=e["state"])
        if o["state"] != e["state"]:
            ctx.reject("ROW_STATE_MISMATCH", p, "selected row state differs from the owner snapshot", expected=e["state"], observed=o["state"])
            continue
        if o["state"] in UNRESOLVED_STATES:
            ctx.hold("SELECTED_UNRESOLVED", p, "selected row is unresolved: it is not media and not silence", observed=o["state"])
            continue
        if o["state"] == "USE":
            missing = [k for k in ("asset", "source_in_sample", "source_out_sample", "gain", "channel_rule") if k not in o]
            if missing:
                ctx.hold("USE_ROW_INCOMPLETE", p, f"selected USE row lacks {', '.join(missing)}")
                continue
            g = o["gain"]
            if (not _is_str(o["asset"], 1, 64) or not _is_int(o["source_in_sample"]) or not _is_int(o["source_out_sample"]) or not 0 <= o["source_in_sample"] < o["source_out_sample"]
                    or not _dict(g) or set(g) != {"num", "den"} or not _is_int(g["num"]) or not _is_int(g["den"]) or g["num"] < 0 or g["den"] < 1
                    or o["channel_rule"] not in ("COPY", "MONO_TO_STEREO_DUPLICATE", "STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE")):
                ctx.reject("SHAPE_ROW", p, "selected USE row needs an asset id, 0 <= source_in < source_out integers, an integer gain num/den (den >= 1) and a known channel_rule", observed=o.get("asset", _ABSENT))
                continue
        want_disp = {"USE": "MIX", "INTENTIONAL_SILENCE": "SILENCE", "OMIT": "OMITTED"}[o["state"]]
        if o["disposition"] != want_disp:
            ctx.reject("ROW_DISPOSITION_MISMATCH", p, "disposition contradicts the state", expected=want_disp, observed=o["disposition"])
            continue
        if "binding_ref" in e:
            if "provenance_ref" not in o:
                ctx.hold("BINDING_UNPROVEN", p, "owner expects a binding reference; the observed row carries none (compared as opaque strings; mapping binding_ref<->provenance_ref is an ambiguity)")
            elif o["provenance_ref"] != e["binding_ref"]:
                ctx.reject("BINDING_MISMATCH", p, "binding reference differs", expected=e["binding_ref"], observed=o["provenance_ref"])
        elif o["state"] == "USE" and profile["require_binding_for_use"]:
            ctx.hold("BINDING_UNRESOLVED", p, "profile requires an owner binding reference for every selected USE row")
        if o["state"] == "INTENTIONAL_SILENCE":
            if "decision_ref" not in o:
                if profile["require_decision_ref_for_silence"]:
                    ctx.hold("SILENCE_UNQUALIFIED", p, "intentional silence without a decision_ref is unqualified")
            elif "decision_ref" in e and e["decision_ref"] != o["decision_ref"]:
                ctx.reject("DECISION_REF_MISMATCH", p, "silence decision reference differs", expected=e["decision_ref"], observed=o["decision_ref"])
            elif "decision_ref" not in e and profile["require_decision_ref_for_silence"]:
                ctx.hold("SILENCE_UNQUALIFIED", p, "owner snapshot carries no decision_ref for this silence")


def _effective_scope(ctx: _Ctx, profile, view) -> None:
    rows = view["rows"]
    use = sorted(i for i in view["selected"] if rows[i]["state"] == "USE")
    sil = sorted(i for i in view["selected"] if rows[i]["state"] == "INTENTIONAL_SILENCE"
                 and ("decision_ref" in rows[i] or not profile["require_decision_ref_for_silence"]))
    omit = sorted(i for i in view["selected"] if rows[i]["state"] == "OMIT")
    ctx.evidence["selected_scope"] = {"use": use, "qualified_silence": sil, "omit_no_contribution": omit,
                                      "selected_unresolved": sorted(i for i in view["selected"] if rows[i]["state"] in UNRESOLVED_STATES)}
    ctx.evidence["unselected_unresolved"] = [{"row_id": i, "state": rows[i]["state"]} for i in sorted(rows) if not rows[i]["selected"] and rows[i]["state"] in UNRESOLVED_STATES]
    ctx.evidence["warnings_preserved"] = sorted(w["code"] for w in view["warnings"] if _dict(w) and _is_str(w.get("code"), 1, 80))
    ctx.evidence["plan_hold_preserved"] = [h.get("code") for h in view["hold"] if _dict(h) and _is_str(h.get("code"), 1, 80)]
    if not use and not sil:
        ctx.hold("EMPTY_EFFECTIVE_SCOPE", "observed.stages.audio_plan.response.plan.scope",
                 "the selected scope contains neither a selected USE row nor qualified intentional silence (selected OMIT contributes nothing)")


# ----------------------------------------------------------------------------------------------------------------------------------- receipts
def _check_outputs(ctx: _Ctx, path: str, outs, roles, plan_sha) -> dict | None:
    """WHOLE outputs list: every entry is validated, so a malformed sibling cannot hide behind a valid object. Returns {role: entry} or None when the list is unusable."""
    if not _list(outs) or len(outs) > 32:
        ctx.reject("SHAPE_OUTPUTS", path, "outputs must be a list of at most 32 entries", observed=outs)
        return None
    by, bad = {}, False
    for i, o in enumerate(outs):
        p = f"{path}[{i}]"
        if (not _dict(o) or set(o) != {"name", "role", "bytes", "sha256", "partial"} or not _is_str(o.get("name"), 1, 128) or not _is_str(o.get("role"), 1, 40)
                or not _is_int(o.get("bytes")) or o["bytes"] < 0 or not _is_sha(o.get("sha256")) or type(o.get("partial")) is not bool):
            ctx.reject("SHAPE_OUTPUTS", p, "output entry needs exactly name, role, a non-negative integer bytes, a SHA-256 value and a boolean partial", observed=o)
            bad = True
            continue
        if o["role"] not in roles:
            ctx.hold("OUTPUT_ROLE_UNKNOWN", p, "output role is not one this profile knows", expected=list(roles), observed=o["role"])
            continue
        if (o["role"] in PARTIAL_ROLES) != o["partial"]:
            ctx.reject("OUTPUT_FLAG_CONTRADICTION", p, "the partial flag contradicts the role", observed={"role": o["role"], "partial": o["partial"]})
            bad = True
            continue
        if o["role"] in by:
            ctx.reject("SHAPE_OUTPUTS", p, "role appears twice", observed=o["role"])
            bad = True
            continue
        by[o["role"]] = o
    if bad:
        return None
    for r in PARTIAL_ROLES:
        if r in by:
            ctx.hold("OUTPUT_PARTIAL", path, "a partial output is listed: the run is not a clean completion", observed=r)
    final = "stem" if "stem" in roles else "video"
    if final not in by:
        ctx.hold("OUTPUT_MISSING", path, f"no non-partial {final} output")
    if "plan" not in by:
        ctx.hold("OUTPUT_MISSING", path, "no plan output")
    elif plan_sha is not None and by["plan"]["sha256"] != plan_sha:
        ctx.reject("OUTPUT_PLAN_SHA_MISMATCH", f"{path}[plan].sha256", "the plan output's SHA-256 differs from the receipt's own plan_sha256 (they hash the same canonical bytes)", expected=plan_sha, observed=by["plan"]["sha256"])
    return by


def _receipt_common(ctx: _Ctx, profile, name: str, packet, path: str) -> None:
    r = packet["response"]
    _schema_check(ctx, profile, name, r.get("schema"), f"{path}.schema")
    _status_and_exit(ctx, profile, name, packet, r.get("status"), profile["receipt_accepted_statuses"][name], profile["receipt_known_statuses"][name])
    v = r.get("written", _ABSENT)
    if type(v) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.written", "flag must be a boolean", observed=v)
    elif v is not True:
        ctx.hold("RECEIPT_NOT_WRITTEN", f"{path}.written", "receipt says nothing was written", observed=v)
    if r.get("stage") != "complete":
        ctx.hold("RECEIPT_INCOMPLETE", f"{path}.stage", "receipt stage is not 'complete' (partial / in-progress states never become READY)", expected="complete", observed=r.get("stage", _ABSENT))
    if r.get("quality_evaluated", _ABSENT) is not False:
        ctx.reject("QUALITY_CLAIM", f"{path}.quality_evaluated", "producer receipts never evaluate quality; anything but boolean false is a contradiction", observed=r.get("quality_evaluated", _ABSENT))
    unchanged = _dig(r, "input", "inputs_unchanged")
    if type(unchanged) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.input.inputs_unchanged", "flag must be a boolean", observed=unchanged)
    elif not unchanged:
        ctx.hold("INPUT_CHANGED", f"{path}.input.inputs_unchanged", "inputs changed (or became unreadable) during the run")


def _check_link(ctx: _Ctx, path: str, label: str, v) -> bool:
    if v is _ABSENT or v is None:
        ctx.hold("RECEIPT_LINK_MISSING", f"{path}.{label}", "the receipt carries no link back to its plan / manifest: it cannot be tied to this run")
        return False
    if not _is_sha(v):
        ctx.reject("SHAPE_PIN", f"{path}.{label}", "must be a 64-character lowercase hex SHA-256 value", observed=v)
        return False
    return True


def _decimal(v):
    """int for an int or a canonical decimal string (the producer's nb_read_frames shape); None for anything else (bool, float, padded or signed text)."""
    if _is_int(v):
        return v
    if type(v) is str and _DEC.match(v):
        return int(v)
    return None


def _check_readback_checks(ctx: _Ctx, profile, path: str, checks, required, all_ok, geometry=None) -> dict | None:
    """Producer readback checks: {name, expected, observed, ok}. Presence, uniqueness, ok=true, claimed-observed vs expected for the exact-equality checks, and all_ok == all(ok)."""
    if checks is _ABSENT or checks is None:
        ctx.hold("READBACK_EVIDENCE_MISSING", path, "required readback evidence is absent")
        return None
    if not _list(checks) or len(checks) > 64:
        ctx.reject("SHAPE_READBACK", path, "readback checks must be a list of at most 64 entries", observed=checks)
        return None
    by, bad = {}, False
    for i, c in enumerate(checks):
        p = f"{path}[{i}]"
        if not _dict(c) or set(c) != {"name", "expected", "observed", "ok"} or not _is_str(c.get("name"), 1, 64) or type(c.get("ok")) is not bool:
            ctx.reject("SHAPE_READBACK", p, "check needs exactly name, expected, observed and a boolean ok", observed=c)
            bad = True
            continue
        if c["name"] in by:
            ctx.reject("SHAPE_READBACK", p, "check name appears twice", observed=c["name"])
            bad = True
            continue
        by[c["name"]] = c
    if bad:
        return None
    for name in required:
        if name not in by:
            ctx.hold("READBACK_EVIDENCE_MISSING", path, f"required readback check {name!r} is absent")
    known = set(required) | {"container_frame_count"}
    for name in sorted(by):
        if name not in known:
            ctx.hold("READBACK_CHECK_UNKNOWN", f"{path}[{_k(name)}]", "readback check name is not one the v0 profile knows")
    for name, c in sorted(by.items()):
        p = f"{path}[{name}]"
        if c["ok"] is not True:
            ctx.hold("READBACK_NOT_OK", p, "readback check did not pass", observed=c["ok"])
        if name in VIDEO_EXACT_INT_CHECKS or name in AUDIO_READBACK_CHECKS:
            e, ob = c["expected"], c["observed"]
            o = _decimal(ob)
            if not _is_int(e) or e < 0 or (o is None and ob is not None):
                ctx.reject("CHECK_INCONSISTENT", p, "expected must be a non-negative integer and observed an integer, a canonical decimal string or null (no bool, float or padded text)", observed=ob)
            elif (o is not None and o == e) != c["ok"]:
                ctx.reject("CHECK_INCONSISTENT", p, "the claimed observed value and the ok flag contradict each other (ok must equal observed == expected)", expected=e, observed=ob)
        elif name in VIDEO_EXACT_STR_CHECKS:
            if not _is_str(c["expected"], 1, 64) or not (c["observed"] is None or _is_str(c["observed"], 1, 64)):
                ctx.reject("CHECK_INCONSISTENT", p, "expected and observed must be short strings (observed may be null)", observed=c["observed"])
            elif (c["observed"] == c["expected"]) != c["ok"]:
                ctx.reject("CHECK_INCONSISTENT", p, "the claimed observed value and the ok flag contradict each other", expected=c["expected"], observed=c["observed"])
        elif not all(x is None or (type(x) in (str, int) and (type(x) is not str or len(x) <= 128)) for x in (c["expected"], c["observed"])):
            ctx.reject("SHAPE_READBACK", p, "expected / observed must be short strings, integers or null", observed=c["observed"])
    if type(all_ok) is not bool:
        ctx.reject("SHAPE_FLAG", path.rsplit(".checks", 1)[0] + ".all_ok", "flag must be a boolean (1/0/'true' are not accepted)", observed=all_ok)
    else:
        every = all(c["ok"] is True for c in by.values())
        if all_ok != every:
            ctx.reject("CHECK_INCONSISTENT", path.rsplit(".checks", 1)[0] + ".all_ok", "all_ok contradicts the individual checks", expected=every, observed=all_ok)
        elif not all_ok:
            ctx.hold("READBACK_NOT_OK", path.rsplit(".checks", 1)[0] + ".all_ok", "readback checks did not all pass")
    return by


def _check_audio_receipt(ctx: _Ctx, profile, packet, plan_view) -> dict | None:
    name, path = "audio_receipt", "observed.stages.audio_receipt.response"
    r = packet["response"]
    _receipt_common(ctx, profile, name, packet, path)
    plan_sha = r.get("plan_sha256", _ABSENT)
    plan_ok = _check_link(ctx, path, "plan_sha256", plan_sha)
    man_ok = _check_link(ctx, path, "input.manifest_sha256", _dig(r, "input", "manifest_sha256"))
    outs = _check_outputs(ctx, f"{path}.outputs", r.get("outputs", _ABSENT), AUDIO_ROLES, plan_sha if plan_ok else None)
    stem = outs.get("stem") if outs else None
    out = plan_view["out"] if plan_view else None
    if stem is not None and out is not None:
        if stem["bytes"] != out["bytes"]:
            ctx.reject("COUNT_MISMATCH", f"{path}.outputs[stem].bytes", "stem size differs from the plan's output geometry", expected=out["bytes"], observed=stem["bytes"])
        if stem["name"] != out["file"]:
            ctx.reject("OUTPUT_GEOMETRY_INCONSISTENT", f"{path}.outputs[stem].name", "stem file name differs from the plan's output file", expected=out["file"], observed=stem["name"])
    # warnings / unresolved report
    warns = r.get("warnings", _ABSENT)
    if not _list(warns) or len(warns) > MAX_LIST:
        ctx.reject("SHAPE_WARNING", f"{path}.warnings", "warnings must be a list", observed=warns)
    else:
        for i, w in enumerate(warns):
            if not _dict(w) or not _is_str(w.get("code"), 1, 80):
                ctx.reject("SHAPE_WARNING", f"{path}.warnings[{i}]", "warning needs a string code", observed=w)
            elif w["code"] not in profile["treated_warning_codes"]:
                ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]", "receipt warning preserved and not treated by the profile", observed=w["code"])
        if plan_view is not None:
            plan_codes = sorted(w.get("code") for w in plan_view["warnings"] if _dict(w))
            rcpt_codes = sorted(w.get("code") for w in warns if _dict(w))
            if plan_codes != rcpt_codes:
                ctx.reject("RECEIPT_PLAN_MISMATCH", f"{path}.warnings", "receipt warnings differ from the plan's", expected=plan_codes, observed=rcpt_codes)
    ur = r.get("unresolved_report", _ABSENT)
    if not _list(ur) or len(ur) > MAX_LIST:
        ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved_report", "unresolved_report must be a list", observed=ur)
    else:
        listed, shape_ok = {}, True
        for i, u in enumerate(ur):
            if (not _dict(u) or set(u) != {"event_id", "source_id", "state", "selected"} or not _is_str(u.get("event_id"), 1, 64) or not _is_str(u.get("source_id"), 1, 64)
                    or u.get("state") not in UNRESOLVED_STATES or type(u.get("selected")) is not bool or u["event_id"] in listed):
                ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved_report[{i}]", "entry needs exactly event_id, source_id, an unresolved state and a boolean selected (unique event_id)", observed=u)
                shape_ok = False
                continue
            listed[u["event_id"]] = u
        if shape_ok:
            for u in listed.values():
                if u["selected"]:
                    ctx.hold("SELECTED_UNRESOLVED", f"{path}.unresolved_report", "receipt reports a selected unresolved row", observed=u["event_id"])
            pm = plan_view.get("unresolved_map") if plan_view else None
            if pm is not None and not _strict_equal(listed, pm):
                ctx.reject("RECEIPT_PLAN_MISMATCH", f"{path}.unresolved_report", "receipt unresolved report differs from the plan's (ids, states, selection flags and source ids must be equal)",
                           expected=sorted(pm), observed=sorted(listed))
            ctx.evidence["receipt_unresolved_preserved"] = sorted(listed)
    # readback
    wav = _dig(r, "readback", "wav")
    rb = _check_readback_checks(ctx, profile, f"{path}.readback.checks", _dig(r, "readback", "checks"), profile["required_audio_readback_checks"], _dig(r, "readback", "all_ok"))
    total = plan_view["total_samples"] if plan_view else None
    if rb is not None and out is not None:
        want = {"channels": out["channels"], "sample_rate": 48000, "bits": 16, "sample_count": out["samples"], "file_bytes": out["bytes"]}
        for k, v in want.items():
            if k in rb and _is_int(rb[k]["expected"]) and rb[k]["expected"] != v:
                ctx.reject("COUNT_MISMATCH", f"{path}.readback.checks[{k}]", "readback expectation differs from the plan's output geometry", expected=v, observed=rb[k]["expected"])
    if not _dict(wav) or not all(_is_int(wav.get(k)) for k in ("sample_count", "file_bytes", "channels", "sample_rate", "bits")):
        ctx.reject("SHAPE_COUNT", f"{path}.readback.wav", "wav facts need integer sample_count / file_bytes / channels / sample_rate / bits (bool and float are not accepted)", observed=wav)
    elif out is not None:
        for k, v in (("sample_count", out["samples"]), ("file_bytes", out["bytes"]), ("channels", out["channels"]), ("sample_rate", 48000), ("bits", 16)):
            if wav[k] != v:
                ctx.reject("COUNT_MISMATCH", f"{path}.readback.wav.{k}", "readback wav fact differs from the plan's output geometry", expected=v, observed=wav[k])
    sw, cl = _dig(r, "mix", "samples_written"), _dig(r, "mix", "clipped_samples")
    for label, v in (("mix.samples_written", sw), ("mix.clipped_samples", cl)):
        if not _is_int(v) or v < 0:
            ctx.reject("SHAPE_COUNT", f"{path}.{label}", "count must be a non-negative integer (bool and float are not accepted)", observed=v)
    if _is_int(sw) and _is_int(total) and sw != total:
        ctx.reject("COUNT_MISMATCH", f"{path}.mix.samples_written", "count differs from the plan's total_samples", expected=total, observed=sw)
    if _is_int(cl) and cl > 0:
        ctx.hold("CLAMPED", f"{path}.mix.clipped_samples", "samples were clipped", observed=cl)
    return {"stem": stem, "plan_out": (outs or {}).get("plan"), "plan_sha256": plan_sha if plan_ok else _ABSENT, "manifest_sha256": _dig(r, "input", "manifest_sha256") if man_ok else _ABSENT}


def _check_video_receipt(ctx: _Ctx, profile, packet, plan_view) -> dict | None:
    name, path = "video_receipt", "observed.stages.video_receipt.response"
    r = packet["response"]
    _receipt_common(ctx, profile, name, packet, path)
    plan_sha = r.get("plan_sha256", _ABSENT)
    plan_ok = _check_link(ctx, path, "plan_sha256", plan_sha)
    outs = _check_outputs(ctx, f"{path}.outputs", r.get("outputs", _ABSENT), VIDEO_ROLES, plan_sha if plan_ok else None)
    video = outs.get("video") if outs else None
    comp = _dig(r, "readback", "comparison")
    if comp is _ABSENT or comp is None:
        ctx.hold("READBACK_EVIDENCE_MISSING", f"{path}.readback.comparison", "required readback evidence is absent")
        by = None
    elif not _dict(comp):
        ctx.reject("SHAPE_READBACK", f"{path}.readback.comparison", "comparison must be an object", observed=comp)
        by = None
    else:
        by = _check_readback_checks(ctx, profile, f"{path}.readback.comparison.checks", comp.get("checks", _ABSENT), profile["required_video_readback_checks"], comp.get("all_ok", _ABSENT))
    if by is not None and "decoded_frame_count" in by and plan_view is not None and _is_int(plan_view["total_frames"]):
        c = by["decoded_frame_count"]
        if _is_int(c["expected"]) and c["expected"] != plan_view["total_frames"]:
            ctx.reject("COUNT_MISMATCH", f"{path}.readback.comparison.checks[decoded_frame_count]", "video frame count differs from the audio plan timeline", expected=plan_view["total_frames"], observed=c["expected"])
    return {"video": video}


def _check_manifest_view(ctx: _Ctx, profile, packet, expected) -> dict:
    path = "observed.stages.avmux_manifest_view"
    out = {}
    for k in ("selection_ref",):
        if not _is_str(packet.get(k)):
            ctx.reject("SHAPE_VIEW", f"{path}.{k}", "must be a 1..128 character string", observed=packet.get(k, _ABSENT))
        elif packet[k] != expected["identity"]["selection_snapshot_ref"]:
            ctx.reject("SELECTION_REF_MISMATCH", f"{path}.{k}", "the AV manifest's selection.ref differs from the owner's selection snapshot", expected=expected["identity"]["selection_snapshot_ref"], observed=packet[k])
    for k in ("video_sha256", "audio_sha256"):
        if not _is_sha(packet.get(k)):
            ctx.reject("SHAPE_VIEW", f"{path}.{k}", "must be a 64-character lowercase hex SHA-256 value", observed=packet.get(k, _ABSENT))
        else:
            out[k] = packet[k]
    return out


# ----------------------------------------------------------------------------------------------------------------------------------- pins
def _check_pins(ctx: _Ctx, profile, expected, audio_plan, audio_rcpt, video_rcpt, view_vals, packets) -> None:
    sources = {k: [] for k in PIN_KINDS}
    if audio_plan is not None and _is_sha(audio_plan["sha"]):
        sources["plan_sha256"].append(("observed.stages.audio_plan.response.plan_sha256", audio_plan["sha"]))
    if audio_rcpt is not None:
        if _is_sha(audio_rcpt["plan_sha256"]):
            sources["plan_sha256"].append(("observed.stages.audio_receipt.response.plan_sha256", audio_rcpt["plan_sha256"]))
        if _is_sha(audio_rcpt["manifest_sha256"]):
            sources["manifest_sha256"].append(("observed.stages.audio_receipt.response.input.manifest_sha256", audio_rcpt["manifest_sha256"]))
        if audio_rcpt["stem"] is not None:
            sources["stem_sha256"].append(("observed.stages.audio_receipt.response.outputs[stem].sha256", audio_rcpt["stem"]["sha256"]))
        if audio_rcpt["plan_out"] is not None:
            sources["plan_sha256"].append(("observed.stages.audio_receipt.response.outputs[plan].sha256", audio_rcpt["plan_out"]["sha256"]))
    ar_packet = packets.get("audio_receipt")
    if _dict(ar_packet) and _is_sha(ar_packet.get("receipt_sha256")):
        sources["receipt_sha256"].append(("observed.stages.audio_receipt.receipt_sha256", ar_packet["receipt_sha256"]))
    elif _dict(ar_packet) and "receipt_sha256" in ar_packet:
        ctx.reject("SHAPE_PIN", "observed.stages.audio_receipt.receipt_sha256", "must be a 64-character lowercase hex SHA-256 value", observed=ar_packet["receipt_sha256"])
    if video_rcpt is not None and video_rcpt["video"] is not None:
        sources["video_sha256"].append(("observed.stages.video_receipt.response.outputs[video].sha256", video_rcpt["video"]["sha256"]))
    if "video_sha256" in view_vals:
        sources["video_sha256"].append(("observed.stages.avmux_manifest_view.video_sha256", view_vals["video_sha256"]))
    if "audio_sha256" in view_vals:
        sources["stem_sha256"].append(("observed.stages.avmux_manifest_view.audio_sha256", view_vals["audio_sha256"]))
    reached = {}
    for kind in profile["required_pins"]:
        want = expected["pins"][kind]
        if not sources[kind]:
            if kind == "receipt_sha256" and "audio_receipt" not in packets:
                continue  # the absent stage is already a reason; do not double count
            ctx.hold("OBSERVED_PIN_MISSING", f"pins.{kind}", "no observed value to compare with the owner's pin (the producing stage is absent, partial or malformed)")
            continue
        for src, got in sources[kind]:
            if got != want:
                ctx.reject("PIN_MISMATCH", src, f"{kind} differs from the owner's pin", expected=want, observed=got)
        reached[kind] = [s for s, _ in sources[kind]]
    ctx.evidence["pins_compared"] = {k: v for k, v in sorted(reached.items())}


# ----------------------------------------------------------------------------------------------------------------------------------- entry point
def evaluate(expected, observed, profile) -> dict:
    """See the module docstring. Never raises for any argument values; never mutates them; the result is a fresh JSON-serializable dict."""
    ctx = _Ctx()
    try:
        _evaluate(ctx, expected, observed, profile)
    except Exception as exc:  # safety net only: the structured paths above are what the tests exercise
        ctx.reject("INTERNAL_EVALUATION_ERROR", "evaluate", f"unexpected {type(exc).__name__} while evaluating; treated as non-READY")
    return _finish(ctx)


_ALL_CHECKS = ("BOUNDS", "PROFILE", "EXPECTED", "OBSERVED", "IDENTITY", "AUDIO_PLAN", "SELECTION", "AUDIO_RECEIPT", "VIDEO_RECEIPT", "MANIFEST_VIEW", "PINS", "EFFECTIVE_SCOPE")


def _evaluate(ctx: _Ctx, expected, observed, profile) -> None:
    bounded = [False]
    ctx.run("BOUNDS", lambda: bounded.__setitem__(0, _check_bounds(ctx, expected=expected, observed=observed, profile=profile)))
    if not bounded[0]:
        for cid in _ALL_CHECKS[1:]:
            ctx.skip(cid, "input is not bounded JSON data")
        return
    if not ctx_run_profile(ctx, profile):
        for cid in _ALL_CHECKS[2:]:
            ctx.skip(cid, "profile unusable")
        return
    exp = [None]

    def do_expected():
        exp[0] = _check_expected(ctx, profile, expected)
    ctx.run("EXPECTED", do_expected)
    if exp[0] is None:
        for cid in ("OBSERVED", "IDENTITY", "AUDIO_PLAN", "SELECTION", "AUDIO_RECEIPT", "VIDEO_RECEIPT", "MANIFEST_VIEW", "PINS", "EFFECTIVE_SCOPE"):
            ctx.skip(cid, "owner-expected snapshot unusable")
        return
    exp = exp[0]
    stages = _dig(observed, "stages")
    packets = {}

    def do_observed():
        if observed is None:
            ctx.hold("OBSERVED_MISSING", "observed", "no observed stage packets were supplied")
            return
        if not _dict(observed) or not _dict(stages):
            ctx.reject("SHAPE_OBSERVED", "observed", "observed must be an object with a 'stages' object", observed=observed)
            return
        for k in sorted(stages):
            if k not in STAGES:
                ctx.hold("STAGE_UNKNOWN", f"observed.stages.{_k(k)}", "stage name is not one this profile knows")
        for name in profile["required_stages"]:
            if name not in stages:
                ctx.hold("REQUIRED_RECEIPT_ABSENT" if name.endswith("receipt") else "REQUIRED_STAGE_ABSENT", f"observed.stages.{name}", "a stage required by the profile was not supplied")
            else:
                packets[name] = stages[name]
    ctx.run("OBSERVED", do_observed)
    usable = {}

    def do_identity():
        for name, pk in packets.items():
            p = _stage_packet(ctx, name, pk, needs_exit=name != "avmux_manifest_view")
            if p is not None:
                usable[name] = p
                _compare_identity(ctx, name, p, exp["identity"])
    ctx.run("IDENTITY", do_identity)
    plan_view = [None]
    if "audio_plan" in usable:
        def do_plan():
            plan_view[0] = _check_audio_plan(ctx, profile, exp, usable["audio_plan"])
            if plan_view[0] is not None:
                _check_plan_hold_and_warnings(ctx, profile, plan_view[0])
        ctx.run("AUDIO_PLAN", do_plan)
    else:
        ctx.skip("AUDIO_PLAN", "stage absent or its envelope is unusable")
    if plan_view[0] is not None:
        ctx.run("SELECTION", lambda: _check_selection(ctx, profile, exp, plan_view[0]))
    else:
        ctx.skip("SELECTION", "audio plan not comparable")
    a_rcpt, v_rcpt, view_vals = [None], [None], [{}]
    if "audio_receipt" in usable:
        ctx.run("AUDIO_RECEIPT", lambda: a_rcpt.__setitem__(0, _check_audio_receipt(ctx, profile, usable["audio_receipt"], plan_view[0])))
    else:
        ctx.skip("AUDIO_RECEIPT", "stage absent or its envelope is unusable")
    if "video_receipt" in usable:
        ctx.run("VIDEO_RECEIPT", lambda: v_rcpt.__setitem__(0, _check_video_receipt(ctx, profile, usable["video_receipt"], plan_view[0])))
    else:
        ctx.skip("VIDEO_RECEIPT", "stage absent or its envelope is unusable")
    if "avmux_manifest_view" in usable:
        ctx.run("MANIFEST_VIEW", lambda: view_vals.__setitem__(0, _check_manifest_view(ctx, profile, usable["avmux_manifest_view"], exp)))
    else:
        ctx.skip("MANIFEST_VIEW", "stage absent or its envelope is unusable")
    ctx.run("PINS", lambda: _check_pins(ctx, profile, exp, plan_view[0], a_rcpt[0], v_rcpt[0], view_vals[0], packets))
    if plan_view[0] is not None:
        ctx.run("EFFECTIVE_SCOPE", lambda: _effective_scope(ctx, profile, plan_view[0]))
    else:
        ctx.skip("EFFECTIVE_SCOPE", "audio plan not comparable")


def ctx_run_profile(ctx: _Ctx, profile) -> bool:
    ok = [False]

    def do():
        ok[0] = _check_profile(ctx, profile)
    ctx.run("PROFILE", do)
    return ok[0]


_RANK = {"REJECT": 0, "HOLD": 1}


def _finish(ctx: _Ctx) -> dict:
    reasons = sorted(ctx.reasons, key=lambda r: (_RANK[r["severity"]], r["code"], r["path"], str(r.get("observed", ""))))
    status = "REJECTED" if any(r["severity"] == "REJECT" for r in reasons) else "HOLD" if reasons else "READY"
    result = {"schema": SCHEMA, "status": status, "reasons": reasons, "checks": list(ctx.checks), "evidence": ctx.evidence, "not_claimed": list(_NOT_CLAIMED),
              "checked": list(CHECKED_V0), "not_checked": [dict(x) for x in NOT_CHECKED_V0]}
    result["scope_of_status"] = READY_SCOPE if status == "READY" else "not READY: nothing here is admitted"
    return result
