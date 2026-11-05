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
_OPAQUE_MAX = 128

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
}

_PROFILE_FIELDS = tuple(PROFILE_V0_UNREVIEWED)
_NOT_CLAIMED = (
    "production execution permission (no execute flag exists; permission is an owner record outside this function)",
    "creative or quality acceptance (quality_evaluated stays false in every producer receipt)",
    "authenticity or provenance of any input: a self-consistent forged set of values is indistinguishable from a genuine one by value comparison",
    "verification of any real file: hashes are compared as supplied values and never recomputed; no file was read",
    "binding adoption, selection authority, or timing/phrase-boundary qualification (GAP-06 has no carrier)",
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
def _check_profile(ctx: _Ctx, profile) -> bool:
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
    bad = []
    if not _is_str(profile["review_status"]):
        bad.append("review_status")
    for k in ("required_stages", "required_pins", "allowed_pin_sources", "plan_ready_statuses", "treated_warning_codes"):
        if not _list(profile[k]) or not all(type(x) is str for x in profile[k]):
            bad.append(k)
    for k in ("known_schemas", "receipt_accepted_statuses", "receipt_known_statuses", "exit_codes_ok"):
        if not _dict(profile[k]):
            bad.append(k)
    if not _dict(profile["exit_codes_ok"]) or not all(_list(v) and all(_is_int(x) for x in v) for v in profile["exit_codes_ok"].values()):
        bad.append("exit_codes_ok")
    for k in ("require_decision_ref_for_silence", "require_binding_for_use"):
        if type(profile[k]) is not bool:
            bad.append(k)
    if profile["required_gap_policy"] not in ("REQUIRE_FULL_COVERAGE", "ALLOW_ZERO_FILL"):
        bad.append("required_gap_policy")
    if profile["empty_effective_scope"] != "HOLD":
        bad.append("empty_effective_scope")  # the only supported treatment of an empty effective scope is non-READY
    if _list(profile.get("required_stages")) and not set(profile["required_stages"]) <= set(STAGES):
        bad.append("required_stages")
    if _list(profile.get("required_pins")) and not set(profile["required_pins"]) <= set(PIN_KINDS):
        bad.append("required_pins")
    if bad:
        ctx.hold("PROFILE_INCOMPLETE", "profile", f"profile fields malformed: {', '.join(sorted(set(bad)))}")
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
        ctx.reject("SHAPE_IDENTITY", f"{path}.{extra}", "unknown identity field", observed=extra)
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
    if "identity" not in expected:
        ctx.hold("EXPECTED_IDENTITY_MISSING", "expected.identity", "owner snapshot carries no identity")
        ok = False
    else:
        ok = _identity_ok(ctx, expected["identity"], "expected.identity")
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
                ctx.reject("SHAPE_PINS", f"expected.pins.{k}", "unknown pin kind", observed=k)
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
            if not _dict(row) or not _is_str(row.get("row_id")) or type(row.get("selected")) is not bool or row.get("state") not in STATES:
                ctx.reject("SHAPE_ROWS", p, "row needs a string row_id, a boolean selected and a known state", observed=row)
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
        if (not _dict(e) or not _is_str(e.get("id"), 1, 64) or e.get("state") not in STATES or type(e.get("selected")) is not bool or not _is_str(e.get("disposition"), 1, 40)):
            ctx.reject("SHAPE_ROW", p, "row needs an id, a known state, a boolean selected and a disposition", observed=e)
            return None
        if e["id"] in seen:
            ctx.reject("SHAPE_ROW", p, "duplicate row id", observed=e["id"])
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


def _check_plan_hold_and_warnings(ctx: _Ctx, profile, view) -> None:
    path = "observed.stages.audio_plan.response.plan"
    plan = view["plan"]
    if view["hold"]:
        if view["status"] == "READY":
            ctx.reject("INCONSISTENT_PLAN", f"{path}.hold", "status READY while the plan lists hold entries", observed=len(view["hold"]))
        for h in view["hold"]:
            ctx.hold("PLAN_HOLD_PRESERVED", f"{path}.hold", "producer hold entry preserved", observed=h.get("code", _ABSENT) if _dict(h) else h)
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
    gaps = _dig(plan, "coverage", "gap_count")
    if not _is_int(gaps) or gaps < 0:
        ctx.reject("SHAPE_COUNT", f"{path}.coverage.gap_count", "gap_count must be a non-negative integer", observed=gaps)
    elif gaps > 0:
        ctx.hold("INCOMPLETE_COVERAGE", f"{path}.coverage.gap_count", "selected rows do not cover the timeline", observed=gaps)
        if policy == "ALLOW_ZERO_FILL" and ZERO_FILL_WARNING not in codes:
            ctx.reject("INCONSISTENT_PLAN", f"{path}.warnings", "gaps under ALLOW_ZERO_FILL without the producer's ZERO_FILL_APPLIED warning")
    # unresolved list must agree with the rows
    listed = {}
    for i, u in enumerate(view["unresolved"]):
        if not _dict(u) or not _is_str(u.get("event_id"), 1, 64) or u.get("state") not in UNRESOLVED_STATES or type(u.get("selected")) is not bool:
            ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved[{i}]", "unresolved entry needs event_id, an unresolved state and a boolean selected", observed=u)
            return
        listed[u["event_id"]] = u
    rows = view["rows"]
    actual = {i: e for i, e in rows.items() if e["state"] in UNRESOLVED_STATES}
    if set(listed) != set(actual) or any(listed[i]["selected"] != rows[i]["selected"] or listed[i]["state"] != rows[i]["state"] for i in listed if i in rows):
        ctx.reject("INCONSISTENT_PLAN", f"{path}.unresolved", "unresolved list and the rows disagree", expected=sorted(actual), observed=sorted(listed))
    if view["status"] == "READY" and any(rows[i]["selected"] for i in actual):
        ctx.reject("INCONSISTENT_PLAN", f"{path}.status", "status READY while a selected row is unresolved")


def _check_selection(ctx: _Ctx, profile, expected, view) -> None:
    """Selected-scope meaning. Whole-row structure is already validated; here only SELECTED rows carry meaning."""
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
def _check_audio_receipt(ctx: _Ctx, profile, packet, plan_view) -> dict | None:
    name, path = "audio_receipt", "observed.stages.audio_receipt.response"
    r = packet["response"]
    _schema_check(ctx, profile, name, r.get("schema"), f"{path}.schema")
    _status_and_exit(ctx, profile, name, packet, r.get("status"), profile["receipt_accepted_statuses"][name], profile["receipt_known_statuses"][name])
    for key, want in (("written", True),):
        v = r.get(key, _ABSENT)
        if type(v) is not bool:
            ctx.reject("SHAPE_FLAG", f"{path}.{key}", "flag must be a boolean", observed=v)
        elif v is not want:
            ctx.hold("RECEIPT_NOT_WRITTEN", f"{path}.{key}", "receipt says nothing was written", observed=v)
    if r.get("stage") != "complete":
        ctx.hold("RECEIPT_INCOMPLETE", f"{path}.stage", "receipt stage is not 'complete' (partial / in-progress states never become READY)", expected="complete", observed=r.get("stage", _ABSENT))
    if r.get("quality_evaluated", _ABSENT) is not False:
        ctx.reject("QUALITY_CLAIM", f"{path}.quality_evaluated", "producer receipts never evaluate quality; anything but boolean false is a contradiction", observed=r.get("quality_evaluated", _ABSENT))
    for label, v in (("plan_sha256", r.get("plan_sha256", _ABSENT)), ("input.manifest_sha256", _dig(r, "input", "manifest_sha256"))):
        if v is _ABSENT:
            ctx.hold("RECEIPT_LINK_MISSING", f"{path}.{label}", "the receipt carries no link back to its plan / manifest: it cannot be tied to this run")
        elif not _is_sha(v):
            ctx.reject("SHAPE_PIN", f"{path}.{label}", "must be a 64-character lowercase hex SHA-256 value", observed=v)
    unchanged = _dig(r, "input", "inputs_unchanged")
    if type(unchanged) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.input.inputs_unchanged", "flag must be a boolean", observed=unchanged)
    elif not unchanged:
        ctx.hold("INPUT_CHANGED", f"{path}.input.inputs_unchanged", "inputs changed (or became unreadable) during the run")
    allok = _dig(r, "readback", "all_ok")
    if type(allok) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.readback.all_ok", "flag must be a boolean (1/0/'true' are not accepted)", observed=allok)
    elif not allok:
        ctx.hold("READBACK_NOT_OK", f"{path}.readback.all_ok", "readback checks did not all pass")
    for i, w in enumerate(r.get("warnings", []) if _list(r.get("warnings", [])) else []):
        code = w.get("code") if _dict(w) else None
        if code not in profile["treated_warning_codes"]:
            ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]", "receipt warning preserved and not treated by the profile", observed=code)
    if "warnings" in r and not _list(r["warnings"]):
        ctx.reject("SHAPE_WARNING", f"{path}.warnings", "warnings must be a list", observed=r["warnings"])
    ur = r.get("unresolved_report", _ABSENT)
    if not _list(ur):
        ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved_report", "unresolved_report must be a list", observed=ur)
    else:
        sel = [u for u in ur if _dict(u) and u.get("selected") is True]
        if any(not _dict(u) for u in ur):
            ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved_report", "entries must be objects")
        for u in sel:
            ctx.hold("SELECTED_UNRESOLVED", f"{path}.unresolved_report", "receipt reports a selected unresolved row", observed=u.get("event_id", _ABSENT))
        plan_ids = {u.get("event_id") for u in plan_view["unresolved"] if _dict(u)} if plan_view else None
        if plan_ids is not None and {u.get("event_id") for u in ur if _dict(u)} != plan_ids:
            ctx.reject("RECEIPT_PLAN_MISMATCH", f"{path}.unresolved_report", "receipt unresolved report differs from the plan's", expected=sorted(map(str, plan_ids)))
        ctx.evidence["receipt_unresolved_preserved"] = sorted(str(u.get("event_id")) for u in ur if _dict(u))
    # outputs
    outs = r.get("outputs", _ABSENT)
    stem = None
    if not _list(outs):
        ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "outputs must be a list", observed=outs)
    else:
        good = [o for o in outs if _dict(o) and o.get("role") == "stem"]
        part = [o for o in outs if _dict(o) and o.get("role") == "stem_partial"]
        if any(not _dict(o) for o in outs):
            ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "entries must be objects")
        if len(good) > 1:
            ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "more than one stem output", observed=len(good))
        elif good:
            stem = good[0]
            if stem.get("partial") is not False:
                ctx.hold("OUTPUT_PARTIAL", f"{path}.outputs", "stem output is flagged partial", observed=stem.get("partial", _ABSENT))
                stem = None
            elif not _is_sha(stem.get("sha256")) or not _is_int(stem.get("bytes")):
                ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "stem needs a SHA-256 value and an integer byte count", observed=stem.get("sha256", _ABSENT))
                stem = None
        else:
            ctx.hold("OUTPUT_PARTIAL" if part else "OUTPUT_MISSING", f"{path}.outputs", "no non-partial stem output" + (" (a partial one exists)" if part else ""))
    # counts
    sw, sc, cl = _dig(r, "mix", "samples_written"), _dig(r, "readback", "wav", "sample_count"), _dig(r, "mix", "clipped_samples")
    for label, v in (("mix.samples_written", sw), ("readback.wav.sample_count", sc), ("mix.clipped_samples", cl)):
        if not _is_int(v) or v < 0:
            ctx.reject("SHAPE_COUNT", f"{path}.{label}", "count must be a non-negative integer (bool and float are not accepted)", observed=v)
    if plan_view is not None and _is_int(plan_view["total_samples"]):
        for label, v in (("mix.samples_written", sw), ("readback.wav.sample_count", sc)):
            if _is_int(v) and v != plan_view["total_samples"]:
                ctx.reject("COUNT_MISMATCH", f"{path}.{label}", "count differs from the plan's total_samples", expected=plan_view["total_samples"], observed=v)
    if _is_int(cl) and cl > 0:
        ctx.hold("CLAMPED", f"{path}.mix.clipped_samples", "samples were clipped", observed=cl)
    return {"stem": stem, "plan_sha256": r.get("plan_sha256", _ABSENT), "manifest_sha256": _dig(r, "input", "manifest_sha256"), "response": r}


def _check_video_receipt(ctx: _Ctx, profile, packet, plan_view) -> dict | None:
    name, path = "video_receipt", "observed.stages.video_receipt.response"
    r = packet["response"]
    _schema_check(ctx, profile, name, r.get("schema"), f"{path}.schema")
    _status_and_exit(ctx, profile, name, packet, r.get("status"), profile["receipt_accepted_statuses"][name], profile["receipt_known_statuses"][name])
    if type(r.get("written", _ABSENT)) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.written", "flag must be a boolean", observed=r.get("written", _ABSENT))
    elif r["written"] is not True:
        ctx.hold("RECEIPT_NOT_WRITTEN", f"{path}.written", "receipt says nothing was written")
    if r.get("stage") != "complete":
        ctx.hold("RECEIPT_INCOMPLETE", f"{path}.stage", "receipt stage is not 'complete'", expected="complete", observed=r.get("stage", _ABSENT))
    if r.get("quality_evaluated", _ABSENT) is not False:
        ctx.reject("QUALITY_CLAIM", f"{path}.quality_evaluated", "quality_evaluated must be boolean false", observed=r.get("quality_evaluated", _ABSENT))
    unchanged = _dig(r, "input", "inputs_unchanged")
    if type(unchanged) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.input.inputs_unchanged", "flag must be a boolean", observed=unchanged)
    elif not unchanged:
        ctx.hold("INPUT_CHANGED", f"{path}.input.inputs_unchanged", "inputs changed during the run")
    allok = _dig(r, "readback", "comparison", "all_ok")
    if type(allok) is not bool:
        ctx.reject("SHAPE_FLAG", f"{path}.readback.comparison.all_ok", "flag must be a boolean", observed=allok)
    elif not allok:
        ctx.hold("READBACK_NOT_OK", f"{path}.readback.comparison.all_ok", "readback comparison did not all pass")
    outs = r.get("outputs", _ABSENT)
    video = None
    if not _list(outs):
        ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "outputs must be a list", observed=outs)
    else:
        good = [o for o in outs if _dict(o) and o.get("role") == "video"]
        part = [o for o in outs if _dict(o) and o.get("role") == "video_partial"]
        if len(good) > 1:
            ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "more than one video output", observed=len(good))
        elif good:
            video = good[0]
            if video.get("partial") is not False:
                ctx.hold("OUTPUT_PARTIAL", f"{path}.outputs", "video output is flagged partial", observed=video.get("partial", _ABSENT))
                video = None
            elif not _is_sha(video.get("sha256")):
                ctx.reject("SHAPE_OUTPUTS", f"{path}.outputs", "video output needs a SHA-256 value", observed=video.get("sha256", _ABSENT))
                video = None
        else:
            ctx.hold("OUTPUT_PARTIAL" if part else "OUTPUT_MISSING", f"{path}.outputs", "no non-partial video output")
    checks = _dig(r, "readback", "comparison", "checks")
    frames = None
    if _list(checks):
        hit = [c for c in checks if _dict(c) and c.get("name") == "decoded_frame_count"]
        if hit and _is_int(hit[0].get("expected")) and hit[0].get("ok") is True:
            frames = hit[0]["expected"]
        elif hit:
            ctx.reject("SHAPE_COUNT", f"{path}.readback.comparison.checks", "decoded_frame_count check is malformed or not ok (bool/float are not counts)", observed=hit[0].get("expected", _ABSENT))
        else:
            ctx.hold("FRAME_COUNT_UNPROVEN", f"{path}.readback.comparison.checks", "no decoded_frame_count check in the readback")
    if frames is not None and plan_view is not None and _is_int(plan_view["total_frames"]) and frames != plan_view["total_frames"]:
        ctx.reject("COUNT_MISMATCH", f"{path}.readback.comparison.checks", "video frame count differs from the audio plan timeline", expected=plan_view["total_frames"], observed=frames)
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


def _evaluate(ctx: _Ctx, expected, observed, profile) -> None:
    if not ctx_run_profile(ctx, profile):
        for cid in ("EXPECTED", "OBSERVED", "IDENTITY", "AUDIO_PLAN", "SELECTION", "AUDIO_RECEIPT", "VIDEO_RECEIPT", "MANIFEST_VIEW", "PINS", "EFFECTIVE_SCOPE"):
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
                ctx.hold("STAGE_UNKNOWN", f"observed.stages.{k}", "stage name is not one this profile knows")
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
    result = {"schema": SCHEMA, "status": status, "reasons": reasons, "checks": list(ctx.checks), "evidence": ctx.evidence, "not_claimed": list(_NOT_CLAIMED)}
    result["scope_of_status"] = READY_SCOPE if status == "READY" else "not READY: nothing here is admitted"
    return result
