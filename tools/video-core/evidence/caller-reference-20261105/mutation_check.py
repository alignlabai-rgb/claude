#!/usr/bin/env python3
"""Mutation controls for the caller-admission reference candidate: each mutant is the candidate's source with ONE deliberate defect; the unit tests must fail for every mutant
(KILLED). A SURVIVED mutant means the tests do not protect that rule. In-memory only: no file is written except the optional --out JSON.   python3 mutation_check.py [--out F]"""

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

SRC = open(os.path.join(HERE, "caller_admission.py"), encoding="utf-8").read()

# (id, rule protected, old text, new text, expected occurrences)
MUTANTS = [
    ("M01", "bool/float are not integer counts", "return type(v) is int  # bool and float are not integer counts", "return isinstance(v, (int, float))", 1),
    ("M02", "pins are compared", '            if got != want:\n                ctx.reject("PIN_MISMATCH"', '            if False:\n                ctx.reject("PIN_MISMATCH"', 1),
    ("M03", "identity is compared", 'if f in ident and _is_str(ident[f]) and ident[f] != expected_identity[f]:', 'if False:', 1),
    ("M04", "plan warnings are not silently accepted", 'if w["code"] not in profile["treated_warning_codes"]:\n            ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]"', 'if False:\n            ctx.hold("WARNING_UNTREATED", f"{path}.warnings[{i}]"', 1),
    ("M05", "receipt warnings are not silently accepted", 'if code not in profile["treated_warning_codes"]:', 'if False:', 1),
    ("M06", "selected unresolved rows hold", '        if o["state"] in UNRESOLVED_STATES:\n            ctx.hold("SELECTED_UNRESOLVED", p, "selected row is unresolved', '        if False:\n            ctx.hold("SELECTED_UNRESOLVED", p, "selected row is unresolved', 1),
    ("M07", "selected scope = rows flagged selected (unselected rows do not count)", 'selected_ids = {i for i, e in rows.items() if e["selected"]}', 'selected_ids = set(rows)', 1),
    ("M08", "ready status + bad exit code contradiction", 'if ready and not code_ok:', 'if False:', 1),
    ("M09", "readiness is the status, not exit 0", '    elif not ready:\n        known = status in known_list', '    elif False:\n        known = status in known_list', 1),
    ("M10", "empty effective scope is non-READY", 'if not use and not sil:', 'if False:', 1),
    ("M11", "silence requires a decision_ref", '                if profile["require_decision_ref_for_silence"]:\n                    ctx.hold("SILENCE_UNQUALIFIED", p, "intentional silence without', '                if False:\n                    ctx.hold("SILENCE_UNQUALIFIED", p, "intentional silence without', 1),
    ("M12", "audio readback all_ok", '    elif not allok:\n        ctx.hold("READBACK_NOT_OK", f"{path}.readback.all_ok"', '    elif False:\n        ctx.hold("READBACK_NOT_OK", f"{path}.readback.all_ok"', 1),
    ("M13", "partial stem output", 'if stem.get("partial") is not False:', 'if False:', 1),
    ("M14", "required stage absent", '            if name not in stages:', '            if False:', 1),
    ("M15", "reasons are deterministically ordered", 'reasons = sorted(ctx.reasons, key=lambda r: (_RANK[r["severity"]], r["code"], r["path"], str(r.get("observed", ""))))', 'reasons = list(ctx.reasons)', 1),
    ("M16", "plan/receipt count equality", 'if _is_int(v) and v != plan_view["total_samples"]:', 'if False:', 1),
    ("M17", "scope vs flagged rows consistency", 'if set(scope) != selected_ids:', 'if False:', 1),
    ("M18", "any HOLD reason blocks READY", '"HOLD" if reasons else "READY"', '"READY"', 1),
    ("M19", "unknown/mismatched schema", '    if schema in known:\n        return', '    if True:\n        return', 1),
    ("M20", "empty/broken response is unstructured", 'if not _dict(packet.get("response")) or not packet.get("response"):', 'if False:', 1),
    ("M21", "pin source must be independent", 'elif src not in profile["allowed_pin_sources"]:', 'elif False:', 1),
    ("M22", "profile policy is never defaulted", '    missing = [k for k in _PROFILE_FIELDS if k not in profile]', '    missing = []', 1),
    ("M23", "selection set equality with the owner", 'if set(exp_sel) != obs_sel:', 'if False:', 1),
    ("M24", "receipt must link back to its plan", 'if v is _ABSENT:\n            ctx.hold("RECEIPT_LINK_MISSING"', 'if False:\n            ctx.hold("RECEIPT_LINK_MISSING"', 1),
    ("M25", "no-selected-USE warning consistency", 'if not sel_use and NO_AUDIO_WARNING not in codes:', 'if False:', 1),
    ("M26", "zero-fill policy", 'if policy != profile["required_gap_policy"]:', 'if False:', 1),
    ("M27", "decision_ref equality for silence", 'elif "decision_ref" in e and e["decision_ref"] != o["decision_ref"]:', 'elif False:', 1),
    ("M28", "manifest selection ref equals the owner snapshot", 'elif packet[k] != expected["identity"]["selection_snapshot_ref"]:', 'elif False:', 1),
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
    after_run, after_bad = run_suite()
    summary = {"baseline": {"tests_run": base_run, "tests_failed": base_bad}, "restored_baseline": {"tests_run": after_run, "tests_failed": after_bad},
               "python": sys.version.split()[0], "mutants": rows,
               "killed": sum(r["status"] == "KILLED" for r in rows), "survived": [r["id"] for r in rows if r["status"] == "SURVIVED"],
               "not_applied": [r["id"] for r in rows if r["status"] == "NOT_APPLIED"], "total": len(rows)}
    text = json.dumps(summary, indent=1, sort_keys=True)
    print(text)
    if ns.out:
        with open(ns.out, "x", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if (base_bad == 0 and after_bad == 0 and not summary["survived"] and not summary["not_applied"]) else 1


if __name__ == "__main__":
    sys.exit(main())
