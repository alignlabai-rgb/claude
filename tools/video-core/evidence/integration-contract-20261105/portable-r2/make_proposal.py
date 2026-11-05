#!/usr/bin/env python3
"""Build an UNREVIEWED PROPOSAL profile from a finished harness run (Python 3.11+ stdlib only; create-only output).

  python make_proposal.py --run-dir <harness --out-dir of a run on the candidate> --pin-profile profiles/source_pin_092b420.json --out NEW.json --mode asis|forgery_control
  python make_proposal.py --run-dir ... --pin-profile ... --out NEW.json --mode ... --promote-for-dryrun      # MECHANICAL DRY-RUN COPY ONLY - see below

The proposal records, for every LIBRARY check that is REVIEW_REQUIRED or FAIL on the candidate, the OBSERVED value together with the finding of INPUT_CONTRACT.md that explains it.
It goes under the key `proposed_expectations`, which the harness IGNORES; `reviewed_expectations` stays empty. Nothing becomes REVIEWED_CHANGE until a human copies an entry into
`reviewed_expectations` with a real `reviewed_by_ref`. Checks the profile mechanism cannot resolve (ERROR, GUARD_MISMATCH, SKIPPED) are listed separately - never papered over.
--promote-for-dryrun writes a COPY whose entries are promoted with reviewed_by_ref 'NOT A REVIEW ...' so the harness can show which items a profile could and could not resolve.
"""
import argparse
import glob
import json
import os
import sys

RULES = [  # (substring of the check name, finding, text of INPUT_CONTRACT.md that explains it, kind)
    ("avmux plan exits 0 and says UNVERIFIED", "F-REC-2", "The harness's FABRICATED_STUB receipt no longer qualifies (\"The old tests' three-field receipt no longer qualifies (that is F-REC-2)\"), so the stub-based avmux plan is rejected instead of UNVERIFIED. Fixture artefact of the stub, not a change of the UNVERIFIED rule.", "fixture_artefact"),
    ("mis-shaped receipt: exit 1, empty stdout", "F-REC-1", "Receipt read with the strict loader, then `qualify_audio_receipt`: \"typed, never AttributeError\" - structured rejection, JSON on stdout.", "intended_change"),
    ("eight mis-shaped", "F-REC-1", "`[]`, `null`, `\"x\"`, `1` -> audio_receipt_shape; objects lacking the producer's `schema` -> audio_receipt_schema (schema is checked right after status). No AttributeError any more.", "intended_change"),
    ("output namespace matrix", "F-REC-2", "avmux rows are rejected by the stub receipt (first failing check: no single `plan` output -> audio_receipt_plan_hash) BEFORE the output-location rules run. Fixture artefact; the namespace rules themselves are unchanged (the audio and video rows still pass).", "fixture_artefact"),
    ("structurally wrong receipts", "F-REC-2", "More checks and a fixed order (status, schema, flags, plan hash, outputs, readback, counters): the same malformed input now fails at an earlier check than the old 'sample_count' rule.", "intended_change"),
    ("hand-written three-field receipt", "F-REC-2", "\"the old tests' three-field receipt no longer qualifies (that IS F-REC-2)\": rejected with audio_receipt_schema (see probe S2). The harness observation records only that no plan exists.", "intended_change"),
    ("contradicting its own status", "F-REC-2", "written/stage/flags are checked: rejected with audio_receipt_inconsistent (see probe S3). The harness observation records only that no plan exists.", "intended_change"),
    ("the plan keeps only", "F-REC-1/2", "\"Warnings are retained\": a qualifying receipt's plan entry now carries warnings, warnings_total, unresolved_events, mechanical_checks, authentication. This harness check feeds a CONTRADICTING receipt, which is rejected, so it cannot observe the retention; probe F2 does.", "intended_change"),
    ("any selection.ref", "F-REC-2", "Only when the stub is the valid control: the stub is rejected, so no plan exists. With a qualifying control the observation is UNCHANGED (selection.ref is still arbitrary).", "fixture_artefact"),
]


def finding_for(name):
    for sub, fid, text, kind in RULES:
        if sub in name:
            return {"finding": fid, "explanation": text, "kind": kind}
    return {"finding": "UNMAPPED", "explanation": "no explanation recorded: a reviewer must look at this item", "kind": "unmapped"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--pin-profile", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", required=True, choices=("asis", "forgery_control"))
    ap.add_argument("--promote-for-dryrun", action="store_true")
    ns = ap.parse_args()
    pin = json.load(open(ns.pin_profile, encoding="utf-8"))
    proposed, unresolved = {}, []
    for f in sorted(glob.glob(os.path.join(ns.run_dir, "cases", "*.json"))):
        case = json.load(open(f, encoding="utf-8"))
        for ch in case["checks"]:
            o = ch["outcome"]
            if o in ("REVIEW_REQUIRED", "FAIL") and ch["layer"] in ("LIBRARY_PERMISSIVE", "LIBRARY_ENFORCED"):
                ent = {"layer": ch["layer"], "pinned_original_expectation": ch["expected"], "observed_on_candidate": ch["observed"], "outcome_today": o, "review_status": "UNREVIEWED", **finding_for(ch["name"])}
                if ch.get("gap"):
                    ent["gap"] = ch["gap"]
                proposed[ch["key"]] = ent
            elif o in ("ERROR", "GUARD_MISMATCH", "SKIPPED", "CONTROL_FAILED", "BUDGET_EXHAUSTED", "POLICY_VIOLATION"):
                unresolved.append({"key": ch["key"], "outcome": o, "layer": ch["layer"], "why_a_profile_cannot_resolve_it": {
                    "ERROR": "the case aborted at this step; later checks of the same case were NOT evaluated (not covered)",
                    "GUARD_MISMATCH": "caller-guard demos are never rescued or failed by reviewed expectations; the guard's expected reading belongs to the harness and needs a human-reviewed harness revision",
                    "SKIPPED": "not applicable to this source (reason recorded in the case file)"}.get(o, o)})
    doc = {"profile_version": 1, "mode": ns.mode, "review_status": "UNREVIEWED_PROPOSAL - NOT A REVIEW, NOT PRODUCTION ADOPTION",
           "description": "Proposed changed expectations for input-contract candidate 092b420, derived from one harness run. The harness reads only `reviewed_expectations`, which is EMPTY here.",
           "source_pin": pin["source_pin"], "reviewed_expectations": {}, "proposed_expectations": proposed, "not_resolvable_by_profile": unresolved,
           "how_to_review": "A human reads CANDIDATE_REVIEW.md and the cited invocation output, then moves chosen entries into `reviewed_expectations` as {\"observed\": <value>, \"reviewed_by_ref\": \"<who/when>\", \"note\": \"...\"} in a NEW file and re-runs with --profile."}
    if ns.promote_for_dryrun:
        doc["review_status"] = "NOT A REVIEW - MECHANICAL DRY-RUN COPY (promoted automatically to show what a profile could resolve)"
        doc["reviewed_expectations"] = {k: {"observed": v["observed_on_candidate"], "reviewed_by_ref": "NOT A REVIEW: automatic promotion for a dry run", "note": v["finding"]} for k, v in proposed.items()}
    with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"{len(proposed)} proposed entries, {len(unresolved)} not resolvable by a profile -> {os.path.basename(ns.out)} ({doc['review_status'][:40]})")


if __name__ == "__main__":
    sys.exit(main())
