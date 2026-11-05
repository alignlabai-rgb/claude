#!/usr/bin/env python3
"""The portable-r2 harness, UNCHANGED, run with ONE fixture substitution made from outside (no harness file is edited):

    portable_cases.receipt_fabricated()  ->  a SELF_CONSISTENT_FORGERY for the same 4805-sample stem

Why: the closed harness uses its shape-faithful stub receipt as the 'valid control' for every avmux plan. A candidate that qualifies receipts (092b420, F-REC-2) correctly rejects that stub,
so several cases abort after the avmux step (r2 does not hide this: without this wrapper those ERRORs stay visible) and the checks after it are never evaluated. With a forgery that satisfies the candidate's mechanical checks those cases can run to the end.
Oracles and expected values are NOT touched. The substitute is a FORGERY, not a genuine receipt: its acceptance says nothing about provenance (see candidate_fixtures.py).
A marker file FIXTURE_OVERRIDE.json is written into the output directory so the run can never be mistaken for an unmodified run.

  python run_with_forgery_control.py <any portable_harness.py arguments>
"""
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                 # portable-r2 modules (self-contained)

import candidate_fixtures as cf  # noqa: E402
import portable_cases as pcs  # noqa: E402
import portable_harness  # noqa: E402

STUB = pcs.receipt_fabricated


def forged(stem_sha=None):
    return cf.forged_receipt(pcs.FB["stem.wav"], samples=4805)


pcs.receipt_fabricated = forged
argv = sys.argv[1:]
code = portable_harness.main(argv)
if "--out-dir" in argv:
    out = argv[argv.index("--out-dir") + 1]
    if os.path.isdir(out):
        with open(os.path.join(out, "FIXTURE_OVERRIDE.json"), "x", encoding="utf-8") as fh:
            json.dump({"override": "portable_cases.receipt_fabricated -> SELF_CONSISTENT_FORGERY (candidate_fixtures.forged_receipt) for the 4805-sample harness stem",
                       "replaces": "the shape-faithful FABRICATED_STUB receipt used as the valid control for every avmux plan",
                       "forgery_sha256": cf.sha256(cf.jbytes(forged())), "stub_sha256": cf.sha256(cf.jbytes(STUB())),
                       "not_genuine": True, "expected_values_changed": False, "harness_files_modified": False}, fh, indent=2, sort_keys=True)
            fh.write("\n")
sys.exit(code)
