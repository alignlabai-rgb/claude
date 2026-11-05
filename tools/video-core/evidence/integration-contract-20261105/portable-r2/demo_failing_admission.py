#!/usr/bin/env python3
"""Evidence script: the disposable, INTENTIONALLY FAILING deny control stops everything after admission (the same case as tests/test_admission.py).

It performs NO prohibited action: its only child READS a file the harness created in the owned world and then (wrongly, on purpose) claims the read was denied. Reads are never denied,
so the control fails - the shape of a deny control that does not hold - and the run must STOP with exit 5 before any contract case, other mechanism test or mutation control.
  python demo_failing_admission.py --library-root DIR --scratch-root DIR --out-dir NEWDIR [--normalize-stored-paths]
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import portable_harness  # noqa: E402
import portable_selftest  # noqa: E402


def failing_admission(ctx):
    w = ctx.run.new_world("adm-fail")
    marker = os.path.join(w.docs, "harmless.txt")
    with open(marker, "wb") as fh:
        fh.write(b"readable")
    child = portable_selftest.control_job(ctx.run, w, "read_is_allowed", marker)
    c = (child.log or {}).get("control") or {}
    ctx.control("INTENTIONAL FAILURE: claim that reading an owned file is denied", True, bool(c.get("denied_by_hook")))


failing_admission.case_id, failing_admission.title, failing_admission.refs = "MSX_DISPOSABLE_FAILING_DENY_CONTROL", "disposable intentionally failing deny control (test only)", []
sys.exit(portable_harness.main(sys.argv[1:] + ["--only", "PC00_fixtures_independent_check,PC01_validation_vs_execution"], admission_cases=[failing_admission]))
