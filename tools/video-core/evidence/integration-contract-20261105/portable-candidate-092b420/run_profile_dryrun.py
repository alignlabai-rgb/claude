#!/usr/bin/env python3
"""ADDITIVE dry-run wrapper (the closed `portable/` harness is imported unchanged and not edited).

Finding H-1 of the closed harness: a reviewed expectation loaded from a JSON profile is compared with `==` against the LIVE observation. JSON turns tuples into lists, so any
observation that contains a tuple (most multi-field checks) can never match a profile read from a file, although the same comparison works in memory (which is all MS05/MS07 test).
This wrapper does NOT repair the harness. It only wraps `portable_core.classify_check` so that, when the normal classification says FAIL/REVIEW_REQUIRED, a reviewed entry is
also tried after a JSON round-trip of the live observation. Nothing else changes: no expected value, no oracle, no layer. Optionally (--forgery-control) the stub receipt is replaced exactly
as in run_with_forgery_control.py. A marker file PROFILE_DRYRUN_PATCH.json is written into the output directory.

  python run_profile_dryrun.py [--forgery-control] <portable_harness.py arguments>
"""
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "portable"))
sys.path.insert(0, HERE)

import candidate_fixtures as cf  # noqa: E402
import portable_cases as pcs  # noqa: E402
import portable_core as pc  # noqa: E402
import portable_harness  # noqa: E402

argv = sys.argv[1:]
forgery = "--forgery-control" in argv
if forgery:
    argv.remove("--forgery-control")
    pcs.receipt_fabricated = lambda stem_sha=None: cf.forged_receipt(pcs.FB["stem.wav"], samples=4805)

_orig = pc.classify_check


def _norm(x):
    return json.loads(json.dumps(x, default=lambda o: {"__bytes_hex__": o.hex()} if isinstance(o, (bytes, bytearray)) else repr(o)))


def patched(layer, key, expected, observed, profile):
    out = _orig(layer, key, expected, observed, profile)
    if out["outcome"] in ("FAIL", "REVIEW_REQUIRED") and profile is not None and layer in ("LIBRARY_ENFORCED", "LIBRARY_PERMISSIVE"):
        rev = profile.reviewed_for(key)
        if rev is not None and _norm(rev.get("observed")) == _norm(observed):
            return {"outcome": "REVIEWED_CHANGE", "reviewed_by_ref": rev.get("reviewed_by_ref"), "note": rev.get("note"), "via": "json-normalised comparison (wrapper, not the harness)"}
    return out


pc.classify_check = patched
code = portable_harness.main(argv)
if "--out-dir" in argv:
    out = argv[argv.index("--out-dir") + 1]
    if os.path.isdir(out):
        with open(os.path.join(out, "PROFILE_DRYRUN_PATCH.json"), "x", encoding="utf-8") as fh:
            json.dump({"patch": "portable_core.classify_check wrapped: reviewed expectations also compared after a JSON round-trip of the live observation (harness finding H-1)",
                       "forgery_control": forgery, "harness_files_modified": False, "expected_values_changed": False, "mechanism_only": True}, fh, indent=2, sort_keys=True)
            fh.write("\n")
sys.exit(code)
