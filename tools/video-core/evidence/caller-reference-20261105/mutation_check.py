#!/usr/bin/env python3
"""Mutation controls for the caller-admission reference candidate: each mutant is the candidate's source with ONE deliberate defect; the unit tests must fail for every mutant
(KILLED). A SURVIVED mutant means the tests do not protect that rule; NOT_APPLIED means the mutant text no longer matches the source. In-memory only: no file is written except the
optional --out JSON.   python3 mutation_check.py [--out F]

A high kill count is NOT evidence of completeness: it shows only that these specific single-point defects are caught. The independent checks that matter more are the exact leaf
allowlist test (every leaf of a conforming world must be decisive unless declared not_checked) and the reproduction of the externally reported cases."""

import argparse
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import caller_admission as ca  # noqa: E402
import test_caller_admission as T  # noqa: E402

with open(os.path.join(HERE, "caller_admission.py"), encoding="utf-8") as _fh:
    SRC = _fh.read()

NL = "\n"
# (id, rule protected, old text, new text, expected occurrences)
MUTANTS = [
    ("M01", "bool/float are not integer counts", "return type(v) is int  # bool and float are not integer counts", "return isinstance(v, (int, float))", 1),
    ("M02", "pins are compared", '            if got != want:' + NL + '                ctx.reject("PIN_MISMATCH"', '            if False:' + NL + '                ctx.reject("PIN_MISMATCH"', 1),
    ("M03", "identity is compared", 'if f in ident and _is_str(ident[f]) and ident[f] != expected_identity[f]:', 'if False:', 1),
    ("M04", "plan warnings are not silently accepted", '        if w["code"] not in profile["treated_warning_codes"]:' + NL + '            ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]"', '        if False:' + NL + '            ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]"', 1),
    ("M05", "receipt warnings are not silently accepted", '            elif w["code"] not in profile["treated_warning_codes"]:' + NL + '                ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]", "receipt warning', '            elif False:' + NL + '                ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]", "receipt warning', 1),
    ("M06", "selected unresolved rows hold", '        if o["state"] in UNRESOLVED_STATES:' + NL + '            ctx.hold("SELECTED_UNRESOLVED", p, "selected row is unresolved', '        if False:' + NL + '            ctx.hold("SELECTED_UNRESOLVED", p, "selected row is unresolved', 1),
    ("M07", "selected scope = rows flagged selected (unselected rows do not count)", 'selected_ids = {i for i, e in rows.items() if e["selected"]}', 'selected_ids = set(rows)', 1),
    ("M08", "ready status + bad exit code contradiction", 'if ready and not code_ok:', 'if False:', 1),
    ("M09", "readiness is the status, not exit 0", '    elif not ready:' + NL + '        known = status in known_list', '    elif False:' + NL + '        known = status in known_list', 1),
    ("M10", "empty effective scope is non-READY", 'if not use and not sil:', 'if False:', 1),
    ("M11", "silence requires a decision_ref", '                if profile["require_decision_ref_for_silence"]:' + NL + '                    ctx.hold("SILENCE_UNQUALIFIED", p, "intentional silence without', '                if False:' + NL + '                    ctx.hold("SILENCE_UNQUALIFIED", p, "intentional silence without', 1),
    ("M12", "audio readback all_ok", '        elif not all_ok:' + NL + '            ctx.hold("READBACK_NOT_OK"', '        elif False:' + NL + '            ctx.hold("READBACK_NOT_OK"', 1),
    ("M13", "partial role outputs hold", '    for r in PARTIAL_ROLES:' + NL + '        if r in by:', '    for r in ():' + NL + '        if r in by:', 1),
    ("M14", "required stage absent", '            if name not in stages:', '            if False:', 1),
    ("M15", "reasons are deterministically ordered", 'reasons = sorted(ctx.reasons, key=lambda r: (_RANK[r["severity"]], r["code"], r["path"], str(r.get("observed", ""))))', 'reasons = list(ctx.reasons)', 1),
    ("M16", "plan/receipt count equality", 'if _is_int(sw) and _is_int(total) and sw != total:', 'if False:', 1),
    ("M17", "scope vs flagged rows consistency", 'if set(scope) != selected_ids:', 'if False:', 1),
    ("M18", "any HOLD reason blocks READY", '"HOLD" if reasons else "READY"', '"READY"', 1),
    ("M19", "unknown/mismatched schema", '    if schema in known:' + NL + '        return', '    if True:' + NL + '        return', 1),
    ("M20", "empty/broken response is unstructured", 'if not _dict(packet.get("response")) or not packet.get("response"):', 'if False:', 1),
    ("M21", "pin source must be independent", 'elif src not in profile["allowed_pin_sources"]:', 'elif False:', 1),
    ("M22", "profile policy is never defaulted", '    missing = [k for k in _PROFILE_FIELDS if k not in profile]', '    missing = []', 1),
    ("M23", "selection set equality with the owner", 'if set(exp_sel) != obs_sel:', 'if False:', 1),
    ("M24", "receipt must link back to its plan", '    if v is _ABSENT or v is None:' + NL + '        ctx.hold("RECEIPT_LINK_MISSING"', '    if False:' + NL + '        ctx.hold("RECEIPT_LINK_MISSING"', 1),
    ("M25", "no-selected-USE warning consistency", 'if not sel_use and NO_AUDIO_WARNING not in codes:', 'if False:', 1),
    ("M26", "zero-fill policy", 'if policy != profile["required_gap_policy"]:', 'if False:', 1),
    ("M27", "decision_ref equality for silence", 'elif "decision_ref" in e and e["decision_ref"] != o["decision_ref"]:', 'elif False:', 1),
    ("M28", "manifest selection ref equals the owner snapshot", 'elif packet[k] != expected["identity"]["selection_snapshot_ref"]:', 'elif False:', 1),
    # ---- boundaries added after the independently reported false READYs
    ("M29", "profile policy is exactly v0 (cases 8/9)", '    if changed:' + NL + '        ctx.reject("PROFILE_POLICY_MODIFIED"', '    if False:' + NL + '        ctx.reject("PROFILE_POLICY_MODIFIED"', 1),
    ("M30", "profile equality is type strict", '    if type(a) is not type(b):' + NL + '        return False', '    if False:' + NL + '        return False', 1),
    ("M31", "required readback evidence null/absent (case 1)", '    if checks is _ABSENT or checks is None:' + NL + '        ctx.hold("READBACK_EVIDENCE_MISSING"', '    if False:' + NL + '        ctx.hold("READBACK_EVIDENCE_MISSING"', 1),
    ("M32", "required readback check names present (case 1)", '        if name not in by:' + NL + '            ctx.hold("READBACK_EVIDENCE_MISSING"', '        if False:' + NL + '            ctx.hold("READBACK_EVIDENCE_MISSING"', 1),
    ("M33", "claimed observed vs ok for int checks (case 2)", '            elif (o is not None and o == e) != c["ok"]:', '            elif False:', 1),
    ("M34", "claimed observed vs ok for string checks", '            elif (c["observed"] == c["expected"]) != c["ok"]:', '            elif False:', 1),
    ("M35", "decimal strings only (no padded / signed text)", '_DEC = re.compile(r"^(0|[1-9][0-9]{0,11})$")', '_DEC = re.compile(r"^\\s*[+-]?[0-9]+\\s*$")', 1),
    ("M36", "non-negative output sizes (case 3)", 'or not _is_int(o.get("bytes")) or o["bytes"] < 0 or', 'or not _is_int(o.get("bytes")) or', 1),
    ("M37", "stem size equals the plan geometry (case 3)", '        if stem["bytes"] != out["bytes"]:', '        if False:', 1),
    ("M38", "partial flag vs role (case 4)", '        if (o["role"] in PARTIAL_ROLES) != o["partial"]:', '        if False:', 1),
    ("M39", "plan output sha equals the receipt plan_sha256 (case 5)", '    elif plan_sha is not None and by["plan"]["sha256"] != plan_sha:', '    elif False:', 1),
    ("M42", "coverage: listed gaps add up", '    elif not trunc and total - listed != covered:', '    elif False:', 1),
    ("M43", "coverage evidence missing is hold", '    if cov is _ABSENT or cov is None:' + NL + '        ctx.hold("COVERAGE_EVIDENCE_MISSING"', '    if False:' + NL + '        ctx.hold("COVERAGE_EVIDENCE_MISSING"', 1),
    ("M44", "timeline sample formula", '    elif _is_int(total) and _is_int(frames) and total != (2 * frames * d * 48000 + n) // (2 * n):', '    elif False:', 1),
    ("M45", "output geometry bytes", '    elif _is_int(total) and (out["samples"] != total or out["bytes"] != 44 + total * out["channels"] * 2):', '    elif False:', 1),
    ("M46", "whole outputs list shape (case 7)", 'if (not _dict(o) or set(o) != {"name", "role", "bytes", "sha256", "partial"} or', 'if (False and not _dict(o) or set(o) != {"name", "role", "bytes", "sha256", "partial"} or', 1),
    ("M47", "duplicate output roles (case 7)", '        if o["role"] in by:' + NL + '            ctx.reject("SHAPE_OUTPUTS", p, "role appears twice"', '        if False:' + NL + '            ctx.reject("SHAPE_OUTPUTS", p, "role appears twice"', 1),
    ("M48", "receipt unresolved: exact boolean selected (case 10)",
     '                    or u.get("state") not in UNRESOLVED_STATES or type(u.get("selected")) is not bool or u["event_id"] in listed):' + NL + '                ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved_report[{i}]"',
     '                    or u.get("state") not in UNRESOLVED_STATES or u["event_id"] in listed):' + NL + '                ctx.reject("SHAPE_UNRESOLVED", f"{path}.unresolved_report[{i}]"', 1),
    ("M49", "receipt unresolved equals the plan's (case 10)", '            if pm is not None and not _strict_equal(listed, pm):', '            if False:', 1),
    ("M50", "bounds: depth (cycles)", '            if depth > MAX_DEPTH:', '            if False:', 1),
    ("M51", "bounds: node count", '            if nodes > MAX_NODES:', '            if False:', 1),
    ("M52", "bounds: NaN / inf", '                if not (v == v and v - v == 0.0):', '                if False:', 1),
    ("M53", "bounds: lone surrogates in strings",
     '                try:' + NL + '                    v.encode("utf-8")' + NL + '                except UnicodeEncodeError:' + NL + '                    ctx.reject("SHAPE_NOT_JSON", name, "string is not valid Unicode text (lone surrogate)")',
     '                try:' + NL + '                    pass' + NL + '                except UnicodeEncodeError:' + NL + '                    ctx.reject("SHAPE_NOT_JSON", name, "string is not valid Unicode text (lone surrogate)")', 1),
    ("M54", "bounds: only JSON types", '            else:' + NL + '                ctx.reject("SHAPE_NOT_JSON", name, f"{t.__name__} is not a JSON value")', '            else:' + NL + '                continue' + NL + '                ctx.reject("SHAPE_NOT_JSON", name, f"{t.__name__} is not a JSON value")', 1),
    ("M55", "unknown readback check names hold", '        if name not in known:' + NL + '            ctx.hold("READBACK_CHECK_UNKNOWN"', '        if False:' + NL + '            ctx.hold("READBACK_CHECK_UNKNOWN"', 1),
    ("M56", "owner rows must exist in the plan (selected or not)", '        if orow is None:' + NL + '            ctx.reject("ROW_MISSING_IN_PLAN"', '        if False:' + NL + '            ctx.reject("ROW_MISSING_IN_PLAN"', 1),
    ("M57", "unselected row state vs owner", '            if orow["state"] != er["state"]:' + NL + '                ctx.reject("ROW_STATE_MISMATCH", pth, "unselected row state', '            if False:' + NL + '                ctx.reject("ROW_STATE_MISMATCH", pth, "unselected row state', 1),
    ("M58", "unselected row disposition vocabulary", '        if want_disp is not None and e["disposition"] != want_disp:', '        if False:', 1),
    ("M59", "selected USE row fields", '            missing = [k for k in ("asset", "source_in_sample", "source_out_sample", "gain", "channel_rule") if k not in o]', '            missing = []', 1),
    ("M60", "stage envelope unknown keys", '    for extra in sorted(k for k in packet if k not in allowed):', '    for extra in []:', 1),
    ("M61", "receipt warnings equal the plan's", '            if plan_codes != rcpt_codes:', '            if False:', 1),
    ("M62", "video frame count equals the audio timeline", '        if _is_int(c["expected"]) and c["expected"] != plan_view["total_frames"]:', '        if False:', 1),
    ("M63", "all_ok equals every(ok)", '        if all_ok != every:', '        if False:', 1),
    ("M64", "video receipt links back to its plan", '    plan_ok = _check_link(ctx, path, "plan_sha256", plan_sha)' + NL + '    outs = _check_outputs(ctx, f"{path}.outputs", r.get("outputs", _ABSENT), VIDEO_ROLES', '    plan_ok = True' + NL + '    outs = _check_outputs(ctx, f"{path}.outputs", r.get("outputs", _ABSENT), VIDEO_ROLES', 1),
]

# Defence-in-depth rules that no input can exercise alone, so a test cannot isolate them (equivalent mutants given the rule named in `because`). They are reported, not counted as survivors.
REDUNDANT = [
    ("R40", "coverage: gap_count 0 means fully covered", '    if gap_count == 0 and covered != total:', '    if False:', 1,
     "with gap_count 0 the list must be empty and not truncated, so 'covered plus the listed gaps is not total_samples' (M42) fires on every input this rule would"),
    ("R41", "coverage: covered cannot exceed total", '    if covered > total:', '    if False:', 1,
     "covered > total either violates 'covered plus the listed gaps is total' (not truncated) or 'covered <= total - listed' (truncated), both always evaluated"),
]


def run_suite() -> tuple[int, int]:
    suite = unittest.defaultTestLoader.loadTestsFromModule(T)
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    return result.testsRun, len(result.failures) + len(result.errors)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ns = ap.parse_args()
    original = dict(ca.__dict__)
    base_run, base_bad = run_suite()
    rows = []
    for mid, rule, old, new, count in MUTANTS:
        found = SRC.count(old)
        if found != count:
            rows.append({"id": mid, "rule": rule, "status": "NOT_APPLIED", "occurrences_found": found})
            continue
        mutated = SRC.replace(old, new)
        ca.__dict__.clear()
        ca.__dict__.update({"__name__": "caller_admission", "__file__": original["__file__"]})
        try:
            exec(compile(mutated, "caller_admission(mutant " + mid + ")", "exec"), ca.__dict__)
            ran, bad = run_suite()
            rows.append({"id": mid, "rule": rule, "status": "KILLED" if bad else "SURVIVED", "tests_run": ran, "tests_failed": bad})
        finally:
            ca.__dict__.clear()
            ca.__dict__.update(original)
    redundant = []
    for mid, rule, old, new, count, because in REDUNDANT:
        if SRC.count(old) != count:
            redundant.append({"id": mid, "rule": rule, "status": "NOT_APPLIED"})
            continue
        ca.__dict__.clear()
        ca.__dict__.update({"__name__": "caller_admission", "__file__": original["__file__"]})
        try:
            exec(compile(SRC.replace(old, new), "caller_admission(mutant " + mid + ")", "exec"), ca.__dict__)
            ran, bad = run_suite()
            redundant.append({"id": mid, "rule": rule, "status": "KILLED" if bad else "EQUIVALENT_NOT_KILLED", "because": because})
        finally:
            ca.__dict__.clear()
            ca.__dict__.update(original)
    after_run, after_bad = run_suite()
    summary = {"baseline": {"tests_run": base_run, "tests_failed": base_bad}, "restored_baseline": {"tests_run": after_run, "tests_failed": after_bad},
               "python": sys.version.split()[0], "mutants": rows,
               "killed": sum(r["status"] == "KILLED" for r in rows), "survived": [r["id"] for r in rows if r["status"] == "SURVIVED"],
               "not_applied": [r["id"] for r in rows if r["status"] == "NOT_APPLIED"], "total": len(rows), "redundant_by_construction": redundant}
    text = json.dumps(summary, indent=1, sort_keys=True)
    print(text)
    if ns.out:
        with open(ns.out, "x", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if (base_bad == 0 and after_bad == 0 and not summary["survived"] and not summary["not_applied"]) else 1


if __name__ == "__main__":
    sys.exit(main())
