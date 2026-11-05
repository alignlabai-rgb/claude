"""GAP-06 timing-qualification CARRIER REFERENCE PROPOSAL - UNREVIEWED, test-only, pure (Python 3.11+ stdlib only; no I/O, env, clock, network, subprocess; never mutates its inputs).

WHAT IT DOES: compares SUPPLIED CLAIMS.  `compare_carrier(expected, observed)` takes an owner-expected selection/qualification snapshot and the observed carrier records (plain synthetic
dict/list/str/int values, kept OUTSIDE every production manifest: the old schemas reject timing_qualification/phrase_boundary as unknown_field) and returns a deterministic verdict
CONSISTENT_ASSERTIONS_ONLY | HOLD | REJECTED with structured reasons and per-comparison reach evidence.

WHAT IT DOES NOT DO: it does not listen to voice, find or measure phrase boundaries, issue/authenticate/expire/revoke a qualification, decide that speech is suitable, or grant execution or creative
acceptance.  There is no READY, execute or approval field.  Matching QUALIFIED strings, refs and hashes are NOT proof of suitability; a self-consistent FORGED claim set copying the owner's pins is accepted
(recorded in `limitations`).  The profile below (kind vocabulary, ref syntax, limits, precedence, every rule marked P-n) is a PROPOSAL, not product canon.
Public basis only: CONTRACT_JA.md / GAPS_AND_TASKS.md at a0acfdaf3409ff09e172865210e2878ad4962680 (GAP-06, SIT-02, LIB-06, the caller pseudocode step 1).
"""
from __future__ import annotations

import hashlib
import json
import re

RESULT_SCHEMA = "synthetic.timing_carrier.result/1"

PROFILE = {
    "profile_id": "synthetic-timing-carrier-reference", "profile_version": 1, "review_status": "UNREVIEWED_PROPOSAL",
    "carrier_version": 1, "sample_rates": [48000],
    "statuses": ["QUALIFIED", "UNQUALIFIED", "UNKNOWN"],                                  # SIT-02 (public): explicit status, kind, qualification_ref per USE row
    "kinds_proposed": ["SAMPLE_BOUNDARY_OWNER_CHECKED", "PHRASE_BOUNDARY_OWNER_CHECKED"],  # P-1 proposed vocabulary; "NONE" is the explicit absence sentinel
    "ref_format_proposed": "^tq:[A-Za-z0-9._-]{1,64}$",                                  # P-2 proposed qualification_ref syntax; opaque, never resolved here
    "opaque_ref_format": "^[A-Za-z0-9._:/-]{1,128}$",                                    # row/event/binding/snapshot/decision refs: opaque bounded strings
    "invalidation_values": ["NONE", "INVALIDATED"],                                        # supplied owner flag; no clock, issuer or lifecycle is inferred from it
    "limits": {"max_rows": 256, "max_records": 256, "max_decisions": 256, "max_string": 128, "max_depth": 5, "max_nodes": 20000, "max_int": 1099511627776, "max_revision": 2147483647},
    "row_states": ["USE", "INTENTIONAL_SILENCE", "OMIT", "MISSING", "UNKNOWN", "PROXY", "HOLD"],
    "precedence": "REJECTED > HOLD > CONSISTENT_ASSERTIONS_ONLY; reasons ordered by stage, then expected-row order, then comparison order",
    "proposed_rules": [
        "P-3 a selected row in a state other than USE, INTENTIONAL_SILENCE, OMIT is HOLD",
        "P-4 a selected USE row needs exactly one carrier record; missing, status UNQUALIFIED/UNKNOWN, stale qualification_revision or INVALIDATED is HOLD",
        "P-5 a record whose row/event/binding/source sha256/sample rate/source or timeline sample boundaries differ from the owner snapshot is REJECTED (a changed source or boundary never inherits a prior qualification)",
        "P-6 a record for a row that is not a selected USE row, duplicate records, unknown rows and span-inconsistent records are REJECTED",
        "P-7 a selected INTENTIONAL_SILENCE row needs a separately supplied decision with the owner's decision_ref: missing is HOLD, different is REJECTED, a timing record for it is REJECTED; silence is never inferred from absence",
        "P-8 selected OMIT and unselected rows contribute no audio and no implied silence and never block",
        "P-9 an empty effective scope (no selected USE and no selected INTENTIONAL_SILENCE) is HOLD",
        "P-10 snapshot_ref, snapshot_version, carrier_version, profile id/version/content hash must equal the pinned values; a profile whose content differs under the same identity is REJECTED",
    ],
}


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


PINNED_PROFILE_SHA256 = "1748bbff0ac869b546a66bc9263065787ea45a3b1f3aabbb2a43a83446e506dc"      # literal pin of the canonical PROFILE content; any content change needs a new id/version AND a new pin
PROFILE_SHA256 = hashlib.sha256(_canon(PROFILE).encode("ascii")).hexdigest()

LIM = PROFILE["limits"]
_REF = re.compile(PROFILE["opaque_ref_format"])
_QREF = re.compile(PROFILE["ref_format_proposed"])
_SHA = re.compile(r"[0-9a-f]{64}")
LIMITATIONS = [
    "claims are compared, not verified: no voice was heard and no phrase boundary was found",
    "a QUALIFIED status with matching kind/ref/hash is NOT proof that the speech is suitable",
    "a self-consistent forged claim set that copies the owner's pins is accepted: forgery cannot be detected here",
    "issuance authority, expiry, revocation, kind vocabulary, ref syntax and requalification rules are unsettled; this profile only proposes (see README)",
    "no coverage, overlap or zero-fill check: those stay with the library and the caller",
]
NOT_ASSERTED = ["ready", "execution permission", "creative acceptance", "approval", "authenticity", "speech suitability"]

ROW_KEYS = {"row_id", "event_id", "selected", "state", "binding_ref", "source_sha256", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample", "decision_ref", "qualification_revision"}
USE_REQ = ("binding_ref", "source_sha256", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample", "qualification_revision")
REC_KEYS = ("row_id", "event_id", "binding_ref", "source_sha256", "sample_rate", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample", "status", "kind", "qualification_ref",
            "qualification_revision", "invalidation")
PIN_FIELDS = ("event_id", "binding_ref", "source_sha256", "sample_rate", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample")


def _is_int(x, lo=0, hi=None):
    return type(x) is int and lo <= x <= (LIM["max_int"] if hi is None else hi)


def _is_ref(x, rx=_REF):
    return type(x) is str and rx.fullmatch(x) is not None


def _walk(obj, lim):
    """Iterative, bounded structural walk (depth, nodes, string length, exact JSON-plain types).  Returns None or a short reason."""
    stack, nodes = [(obj, 1)], 0
    while stack:
        x, d = stack.pop()
        nodes += 1
        if nodes > lim["max_nodes"]:
            return "node count limit"
        if d > lim["max_depth"]:
            return "depth limit"
        t = type(x)
        if t is dict:
            for k, v in x.items():
                if type(k) is not str or len(k) > 64:
                    return "key type or length"
                stack.append((v, d + 1))
        elif t is list:
            stack.extend((v, d + 1) for v in x)
        elif t is str:
            if len(x) > lim["max_string"]:
                return "string length limit"
            try:
                x.encode("utf-8")
            except UnicodeEncodeError:
                return "string is not UTF-8 encodable"
        elif t is int or t is bool or x is None:
            pass
        else:
            return "type " + t.__name__ + " is not a plain JSON value"
    return None


class _Acc:
    def __init__(self):
        self.reasons, self.comparisons, self.stages = [], [], []

    def stage(self, s):
        if s not in self.stages:
            self.stages.append(s)

    def reason(self, severity, code, stage, comparison, detail, row_id=None):
        self.reasons.append({"severity": severity, "code": code, "stage": stage, "row_id": row_id, "comparison": comparison, "detail": detail})

    def cmp(self, stage, row_id, comparison, outcome, expected=None, observed=None):
        self.comparisons.append({"stage": stage, "row_id": row_id, "comparison": comparison, "reached": True, "outcome": outcome, "expected": expected, "observed": observed})


def _scalar(v):
    return v if (type(v) in (int, bool) or v is None or (type(v) is str and len(v) <= 128)) else None


def _check_expected(exp, acc):
    """Strict shape of the owner snapshot.  Returns list of validated rows or None (REJECTED reasons added)."""
    st = "STRUCTURE"
    keys = {"snapshot_ref", "snapshot_version", "sample_rate", "rows"}
    if set(exp) != keys:
        acc.reason("REJECTED", "EXPECTED_KEYS", st, "expected.keys", "owner snapshot must have exactly: snapshot_ref, snapshot_version, sample_rate, rows")
        return None
    bad = False
    if not _is_ref(exp["snapshot_ref"]):
        acc.reason("REJECTED", "EXPECTED_TYPE", st, "expected.snapshot_ref", "must be a bounded opaque string"); bad = True
    if not _is_int(exp["snapshot_version"], 0, LIM["max_revision"]):
        acc.reason("REJECTED", "EXPECTED_TYPE", st, "expected.snapshot_version", "must be a strict integer"); bad = True
    if not _is_int(exp["sample_rate"], 1) or exp["sample_rate"] not in PROFILE["sample_rates"]:
        acc.reason("REJECTED", "EXPECTED_TYPE", st, "expected.sample_rate", "must be a strict integer in the profile's sample rates"); bad = True
    rows = exp["rows"]
    if type(rows) is not list:
        acc.reason("REJECTED", "EXPECTED_TYPE", st, "expected.rows", "must be a list")
        return None
    if len(rows) > LIM["max_rows"]:
        acc.reason("REJECTED", "LIMIT", st, "expected.rows", "more rows than the profile limit")
        return None
    ids, evs = set(), set()
    for i, r in enumerate(rows):
        p = f"expected.rows[{i}]"
        if type(r) is not dict or not set(r) <= ROW_KEYS or not {"row_id", "event_id", "selected", "state"} <= set(r):
            acc.reason("REJECTED", "EXPECTED_ROW_SHAPE", st, p, "row must be an object with row_id, event_id, selected, state and only known keys"); bad = True
            continue
        ok = True
        for f in ("row_id", "event_id"):
            if not _is_ref(r[f]):
                acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.{f}", "must be a bounded opaque string"); ok = False
        if type(r["selected"]) is not bool:
            acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.selected", "must be a boolean"); ok = False
        if type(r["state"]) is not str or r["state"] not in PROFILE["row_states"]:
            acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.state", "state outside the vocabulary (never null)"); ok = False
        for f in ("binding_ref", "decision_ref"):
            if f in r and not _is_ref(r[f]):
                acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.{f}", "must be a bounded opaque string"); ok = False
        if "source_sha256" in r and not (type(r["source_sha256"]) is str and _SHA.fullmatch(r["source_sha256"])):
            acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.source_sha256", "must be 64 lowercase hex"); ok = False
        for f in ("source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample"):
            if f in r and not _is_int(r[f]):
                acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.{f}", "must be a strict non-negative integer (no bool, no float)"); ok = False
        if "qualification_revision" in r and not _is_int(r["qualification_revision"], 0, LIM["max_revision"]):
            acc.reason("REJECTED", "EXPECTED_TYPE", st, f"{p}.qualification_revision", "must be a strict integer"); ok = False
        if not ok:
            bad = True
            continue
        if r["row_id"] in ids or r["event_id"] in evs:
            acc.reason("REJECTED", "EXPECTED_DUPLICATE", st, p, "duplicate row_id or event_id"); bad = True
        ids.add(r["row_id"]); evs.add(r["event_id"])
        if r["selected"] and r["state"] == "USE":
            miss = [f for f in USE_REQ if f not in r]
            if miss:
                acc.reason("REJECTED", "EXPECTED_ROW_SHAPE", st, p, "selected USE row lacks: " + ", ".join(miss)); bad = True
                continue
        if r["selected"] and r["state"] == "INTENTIONAL_SILENCE":
            miss = [f for f in ("decision_ref", "timeline_start_sample", "timeline_end_sample") if f not in r]
            if miss:
                acc.reason("REJECTED", "EXPECTED_ROW_SHAPE", st, p, "selected INTENTIONAL_SILENCE row lacks: " + ", ".join(miss)); bad = True
                continue
        if "timeline_start_sample" in r and "timeline_end_sample" in r and r["timeline_end_sample"] <= r["timeline_start_sample"]:
            acc.reason("REJECTED", "EXPECTED_SPAN", st, p, "timeline range must be positive"); bad = True
        if r["state"] == "USE" and r["selected"] and r["source_out_sample"] - r["source_in_sample"] != r["timeline_end_sample"] - r["timeline_start_sample"]:
            acc.reason("REJECTED", "EXPECTED_SPAN", st, p, "source span and timeline span differ"); bad = True
    return None if bad else rows


def _check_observed(obs, acc):
    st = "STRUCTURE"
    keys = {"carrier_version", "profile_id", "profile_version", "profile_content_sha256", "snapshot_ref", "snapshot_version", "records", "silence_decisions"}
    if set(obs) != keys:
        acc.reason("REJECTED", "OBSERVED_KEYS", st, "observed.keys", "carrier must have exactly: " + ", ".join(sorted(keys)))
        return None
    bad = False
    if not _is_int(obs["carrier_version"], 0, LIM["max_revision"]) or not _is_int(obs["profile_version"], 0, LIM["max_revision"]) or not _is_int(obs["snapshot_version"], 0, LIM["max_revision"]):
        acc.reason("REJECTED", "OBSERVED_TYPE", st, "observed.versions", "carrier_version, profile_version, snapshot_version must be strict integers"); bad = True
    if not _is_ref(obs["profile_id"]) or not _is_ref(obs["snapshot_ref"]):
        acc.reason("REJECTED", "OBSERVED_TYPE", st, "observed.refs", "profile_id and snapshot_ref must be bounded opaque strings"); bad = True
    if not (type(obs["profile_content_sha256"]) is str and _SHA.fullmatch(obs["profile_content_sha256"])):
        acc.reason("REJECTED", "OBSERVED_TYPE", st, "observed.profile_content_sha256", "must be 64 lowercase hex"); bad = True
    recs, decs = obs["records"], obs["silence_decisions"]
    if type(recs) is not list or type(decs) is not list:
        acc.reason("REJECTED", "OBSERVED_TYPE", st, "observed.records", "records and silence_decisions must be lists")
        return None
    if len(recs) > LIM["max_records"] or len(decs) > LIM["max_decisions"]:
        acc.reason("REJECTED", "LIMIT", st, "observed.records", "more records or decisions than the profile limit")
        return None
    for i, r in enumerate(recs):
        p = f"observed.records[{i}]"
        if type(r) is not dict or set(r) != set(REC_KEYS):
            acc.reason("REJECTED", "RECORD_SHAPE", st, p, "record must have exactly: " + ", ".join(REC_KEYS)); bad = True
            continue
        ok = True
        for f in ("row_id", "event_id", "binding_ref"):
            if not _is_ref(r[f]):
                acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.{f}", "must be a bounded opaque string"); ok = False
        if not (type(r["source_sha256"]) is str and _SHA.fullmatch(r["source_sha256"])):
            acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.source_sha256", "must be 64 lowercase hex"); ok = False
        for f in ("sample_rate", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample"):
            if not _is_int(r[f]):
                acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.{f}", "must be a strict non-negative integer (no bool, no float)"); ok = False
        if not _is_int(r["qualification_revision"], 0, LIM["max_revision"]):
            acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.qualification_revision", "must be a strict integer"); ok = False
        if r["status"] not in PROFILE["statuses"] or type(r["status"]) is not str:
            acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.status", "status must be explicit: QUALIFIED, UNQUALIFIED or UNKNOWN"); ok = False
        if type(r["kind"]) is not str or (r["kind"] != "NONE" and r["kind"] not in PROFILE["kinds_proposed"]):
            acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.kind", "kind must be explicit: a proposed kind or NONE"); ok = False
        if not (_is_ref(r["qualification_ref"], _QREF) or r["qualification_ref"] == "NONE"):
            acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.qualification_ref", "qualification_ref must match the proposed syntax or be NONE"); ok = False
        if type(r["invalidation"]) is not str or r["invalidation"] not in PROFILE["invalidation_values"]:
            acc.reason("REJECTED", "RECORD_TYPE", st, f"{p}.invalidation", "must be NONE or INVALIDATED"); ok = False
        if not ok:
            bad = True
            continue
        if r["timeline_end_sample"] <= r["timeline_start_sample"] or r["source_out_sample"] - r["source_in_sample"] != r["timeline_end_sample"] - r["timeline_start_sample"]:
            acc.reason("REJECTED", "RECORD_SPAN_MISMATCH", st, p, "record's source span and timeline span must be positive and equal"); bad = True
        if r["status"] == "QUALIFIED" and (r["kind"] == "NONE" or r["qualification_ref"] == "NONE"):
            acc.reason("REJECTED", "QUALIFIED_WITHOUT_KIND_OR_REF", st, p, "QUALIFIED needs an explicit kind and qualification_ref"); bad = True
    for i, d in enumerate(decs):
        p = f"observed.silence_decisions[{i}]"
        if type(d) is not dict or set(d) != {"row_id", "decision_ref"} or not _is_ref(d["row_id"]) or not _is_ref(d["decision_ref"]):
            acc.reason("REJECTED", "DECISION_SHAPE", st, p, "decision must be exactly {row_id, decision_ref} with bounded opaque strings"); bad = True
    return None if bad else (recs, decs)


def compare_carrier(expected, observed, profile=None) -> dict:
    """Pure comparison of supplied claims.  Never raises; never mutates its arguments; no I/O."""
    acc = _Acc()
    try:
        return _compare(expected, observed, profile, acc)
    except Exception:                                    # defensive: any unforeseen input becomes a REJECTED result with a code only (no echo of input)
        acc.reasons = [{"severity": "REJECTED", "code": "INTERNAL_ERROR", "stage": "STRUCTURE", "row_id": None, "comparison": "internal", "detail": "unexpected input shape"}]
        acc.comparisons = []
        return _finish(acc, {}, [])


def _compare(expected, observed, profile, acc):
    # ---- stage PROFILE + STRUCTURE (a rejection here stops before any row comparison)
    acc.stage("PROFILE")
    prof = PROFILE if profile is None else profile
    if type(prof) is not dict or _walk(prof, dict(LIM, max_string=2048)) is not None:
        acc.reason("REJECTED", "PROFILE_UNREADABLE", "PROFILE", "profile.content", "profile is not a bounded plain JSON object")
        return _finish(acc, {}, [])
    try:
        psha = hashlib.sha256(_canon(prof).encode("ascii")).hexdigest()
    except (TypeError, ValueError):
        psha = None
    same = psha == PINNED_PROFILE_SHA256                   # MUTATION-TARGET: profile-pin check
    acc.cmp("PROFILE", None, "profile_content_sha256_equals_pin", "MATCH" if same else "MISMATCH", PINNED_PROFILE_SHA256, psha)
    if not same:
        acc.reason("REJECTED", "PROFILE_CONTENT_MISMATCH", "PROFILE", "profile.content", "profile content differs from the pinned content (same id/version cannot be relaxed or changed silently)")
        return _finish(acc, {}, [])
    acc.stage("STRUCTURE")
    if type(expected) is not dict or type(observed) is not dict:
        acc.reason("REJECTED", "TOP_TYPE", "STRUCTURE", "inputs", "expected and observed must be plain objects")
        return _finish(acc, {}, [])
    for name, obj in (("expected", expected), ("observed", observed)):
        why = _walk(obj, LIM)
        if why:
            acc.reason("REJECTED", "STRUCTURE_LIMIT", "STRUCTURE", name, why)
    if acc.reasons:
        return _finish(acc, {}, [])
    rows = _check_expected(expected, acc)
    got = _check_observed(observed, acc)
    if rows is None or got is None:
        return _finish(acc, {}, [])
    recs, decs = got
    # ---- stage CARRIER (identity / version pins)
    acc.stage("CARRIER")
    pins = (("carrier_version", PROFILE["carrier_version"], observed["carrier_version"], "CARRIER_VERSION"),
            ("profile_id", PROFILE["profile_id"], observed["profile_id"], "PROFILE_IDENTITY"),
            ("profile_version", PROFILE["profile_version"], observed["profile_version"], "PROFILE_IDENTITY"),
            ("profile_content_sha256", PROFILE_SHA256, observed["profile_content_sha256"], "PROFILE_CONTENT_MISMATCH"),
            ("snapshot_ref", expected["snapshot_ref"], observed["snapshot_ref"], "STALE_SNAPSHOT"),
            ("snapshot_version", expected["snapshot_version"], observed["snapshot_version"], "STALE_SNAPSHOT"))
    for name, e, o, code in pins:
        ok = e == o
        acc.cmp("CARRIER", None, name, "MATCH" if ok else "MISMATCH", _scalar(e), _scalar(o))
        if not ok:
            acc.reason("REJECTED", code, "CARRIER", name, "carrier does not carry the pinned identity/version/snapshot")
    # ---- stage CROSS_RECORD (duplicates, contradictions, records for rows that must not have one)
    acc.stage("CROSS_RECORD")
    by_id = {r["row_id"]: r for r in rows}
    rec_by, dup = {}, set()
    for r in recs:
        if r["row_id"] in rec_by:
            dup.add(r["row_id"])
        rec_by[r["row_id"]] = r
    dec_by, ddup = {}, set()
    for d in decs:
        if d["row_id"] in dec_by:
            ddup.add(d["row_id"])
        dec_by[d["row_id"]] = d
    for rid in sorted(dup):
        acc.cmp("CROSS_RECORD", rid, "single_record_per_row", "MISMATCH", 1, sum(1 for r in recs if r["row_id"] == rid))
        acc.reason("REJECTED", "DUPLICATE_RECORD", "CROSS_RECORD", "single_record_per_row", "more than one carrier record for the row", rid)
    for rid in sorted(ddup):
        acc.cmp("CROSS_RECORD", rid, "single_decision_per_row", "MISMATCH", 1, sum(1 for d in decs if d["row_id"] == rid))
        acc.reason("REJECTED", "DUPLICATE_DECISION", "CROSS_RECORD", "single_decision_per_row", "more than one silence decision for the row", rid)
    for rid in sorted(rec_by):
        row = by_id.get(rid)
        if row is None:
            acc.cmp("CROSS_RECORD", None, "record_row_exists_in_snapshot", "MISMATCH", True, False)
            acc.reason("REJECTED", "RECORD_UNKNOWN_ROW", "CROSS_RECORD", "record_row_exists_in_snapshot", "a carrier record names a row that is not in the owner snapshot")
        elif not (row["selected"] and row["state"] == "USE"):
            acc.cmp("CROSS_RECORD", rid, "record_only_for_selected_use", "MISMATCH", False, True)
            acc.reason("REJECTED", "RECORD_FOR_NON_USE_ROW", "CROSS_RECORD", "record_only_for_selected_use", "a timing record exists for a row that is not a selected USE row (no qualification is implied or invented)", rid)
    for rid in sorted(dec_by):
        row = by_id.get(rid)
        if row is None or not (row["selected"] and row["state"] == "INTENTIONAL_SILENCE"):
            acc.cmp("CROSS_RECORD", rid if row else None, "decision_only_for_selected_silence", "MISMATCH", False, True)
            acc.reason("REJECTED", "DECISION_FOR_NON_SILENCE_ROW", "CROSS_RECORD", "decision_only_for_selected_silence", "a silence decision exists for a row that is not a selected INTENTIONAL_SILENCE row", rid if row else None)
    # ---- stage ROW_COMPARISON (every comparison below runs for every selected row; none short-circuits another)
    acc.stage("ROW_COMPARISON")
    outcomes, effective = {}, 0
    for row in rows:
        rid, st = row["row_id"], "ROW_COMPARISON"
        if not row["selected"]:
            acc.cmp(st, rid, "selection_meaning", "NOT_APPLICABLE", "unselected", row["state"])
            outcomes[rid] = "CONTRIBUTES_NOTHING"
            continue
        if row["state"] == "OMIT":
            acc.cmp(st, rid, "selection_meaning", "NOT_APPLICABLE", "selected OMIT consumes nothing", row["state"])
            outcomes[rid] = "CONTRIBUTES_NOTHING"
            continue
        if row["state"] not in ("USE", "INTENTIONAL_SILENCE"):
            acc.cmp(st, rid, "selected_state_resolved", "MISMATCH", "USE|INTENTIONAL_SILENCE|OMIT", row["state"])
            acc.reason("HOLD", "SELECTED_UNRESOLVED", st, "selected_state_resolved", "a selected row is not resolved: no audio is produced and no silence is implied", rid)
            outcomes[rid] = "HOLD"
            continue
        effective += 1
        if row["state"] == "INTENTIONAL_SILENCE":
            d = dec_by.get(rid)
            if d is None:
                acc.cmp(st, rid, "silence_decision_present", "MISSING", True, False)
                acc.reason("HOLD", "MISSING_SILENCE_DECISION", st, "silence_decision_present", "no separately supplied owner decision for the selected silence (silence is never inferred from absence)", rid)  # MUTATION-TARGET: silence
            else:
                acc.cmp(st, rid, "silence_decision_present", "MATCH", True, True)
                same = d["decision_ref"] == row["decision_ref"]
                acc.cmp(st, rid, "silence_decision_ref", "MATCH" if same else "MISMATCH", row["decision_ref"], d["decision_ref"])
                if not same:
                    acc.reason("REJECTED", "SILENCE_DECISION_MISMATCH", st, "silence_decision_ref", "supplied decision_ref differs from the owner's", rid)
            if rid in rec_by:
                acc.cmp(st, rid, "no_timing_record_for_silence", "MISMATCH", False, True)
            outcomes[rid] = _row_outcome(acc, rid)
            continue
        rec = rec_by.get(rid)
        if rec is None or rid in dup:
            if rec is None:
                acc.cmp(st, rid, "record_present", "MISSING", True, False)
                acc.reason("HOLD", "MISSING_RECORD", st, "record_present", "no carrier record for a selected USE row", rid)                                      # MUTATION-TARGET: missing record
            outcomes[rid] = _row_outcome(acc, rid)
            continue
        acc.cmp(st, rid, "record_present", "MATCH", True, True)
        for f in PIN_FIELDS:
            e = expected["sample_rate"] if f == "sample_rate" else row[f]
            ok = e == rec[f]                                     # MUTATION-TARGET: pins
            acc.cmp(st, rid, f, "MATCH" if ok else "MISMATCH", _scalar(e), _scalar(rec[f]))
            if not ok:
                acc.reason("REJECTED", "PIN_MISMATCH", st, f, "the record does not pin the owner's value (a changed source or boundary cannot inherit a prior qualification)", rid)
        q = rec["status"] == "QUALIFIED"
        acc.cmp(st, rid, "status_qualified", "MATCH" if q else "MISMATCH", "QUALIFIED", rec["status"])
        if not q:                                                                                                                                                   # MUTATION-TARGET: status
            acc.reason("HOLD", "NOT_QUALIFIED", st, "status_qualified", "explicit status is " + rec["status"] + ", not QUALIFIED", rid)
        fresh = rec["qualification_revision"] == row["qualification_revision"]
        acc.cmp(st, rid, "qualification_revision", "MATCH" if fresh else "MISMATCH", row["qualification_revision"], rec["qualification_revision"])
        if not fresh:                                                                                                                                               # MUTATION-TARGET: revision
            acc.reason("HOLD", "REVISION_STALE", st, "qualification_revision", "the claim belongs to another owner record revision", rid)
        valid = rec["invalidation"] == "NONE"
        acc.cmp(st, rid, "not_invalidated", "MATCH" if valid else "MISMATCH", "NONE", rec["invalidation"])
        if not valid:
            acc.reason("HOLD", "INVALIDATED", st, "not_invalidated", "the supplied owner record says the qualification is invalidated", rid)
        outcomes[rid] = _row_outcome(acc, rid)
    acc.cmp("ROW_COMPARISON", None, "effective_scope_non_empty", "MATCH" if effective else "MISMATCH", ">=1 selected USE or INTENTIONAL_SILENCE row", effective)
    if not effective:                                                                                                                                              # MUTATION-TARGET: empty scope
        acc.reason("HOLD", "EMPTY_EFFECTIVE_SCOPE", "ROW_COMPARISON", "effective_scope_non_empty", "no selected row contributes audio or explicit silence")
    return _finish(acc, outcomes, rows)


def _row_outcome(acc, rid):
    sev = {r["severity"] for r in acc.reasons if r["row_id"] == rid}              # includes CROSS_RECORD reasons about this row
    return "REJECTED" if "REJECTED" in sev else "HOLD" if "HOLD" in sev else "CLAIMS_CONSISTENT"


def _finish(acc, outcomes, rows):
    rej = [r for r in acc.reasons if r["severity"] == "REJECTED"]
    hold = [r for r in acc.reasons if r["severity"] == "HOLD"]
    verdict = "REJECTED" if rej else "HOLD" if hold else "CONSISTENT_ASSERTIONS_ONLY"
    return {"result_schema": RESULT_SCHEMA, "verdict": verdict, "primary_reason": (rej or hold)[0]["code"] if (rej or hold) else None,
            "profile": {"id": PROFILE["profile_id"], "version": PROFILE["profile_version"], "content_sha256": PROFILE_SHA256, "review_status": PROFILE["review_status"]},
            "stages_reached": list(acc.stages), "reasons": acc.reasons, "comparisons": acc.comparisons,
            "row_outcomes": [{"row_id": r["row_id"], "outcome": outcomes.get(r["row_id"], "NOT_COMPARED")} for r in rows],
            "counts": {"reasons": len(acc.reasons), "comparisons": len(acc.comparisons), "rejected": len(rej), "hold": len(hold)},
            "limitations": list(LIMITATIONS), "not_asserted": list(NOT_ASSERTED)}
