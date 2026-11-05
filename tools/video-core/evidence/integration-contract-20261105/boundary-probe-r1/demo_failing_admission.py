#!/usr/bin/env python3
"""Evidence script: a disposable, INTENTIONALLY FAILING deny control stops the probe at admission (exit 5) before any probe case. Same device as portable-r2/demo_failing_admission.py.

It performs NO prohibited action: its only child READS a file the harness created in the owned world and then (wrongly, on purpose) claims the read was denied. Reads are never denied, so
the control fails - the shape of a deny control that does not hold - and the probe must STOP with exit 5 without starting any probe case.
  python demo_failing_admission.py --source b4ca4a3=<root> --scratch-root DIR --out-dir NEWDIR
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import boundary_probe  # noqa: E402


def failing_admission(ctx):
    hx = boundary_probe.HX
    w = ctx.run.new_world("adm-fail")
    marker = os.path.join(w.docs, "harmless.txt")
    with open(marker, "wb") as fh:
        fh.write(b"readable")
    child = hx.ps.control_job(ctx.run, w, "read_is_allowed", marker)
    c = (child.log or {}).get("control") or {}
    ctx.control("INTENTIONAL FAILURE: claim that reading an owned file is denied", True, bool(c.get("denied_by_hook")))


failing_admission.case_id, failing_admission.title, failing_admission.refs = "MSX_DISPOSABLE_FAILING_DENY_CONTROL", "disposable intentionally failing deny control (test only)", []

if __name__ == "__main__":
    sys.exit(boundary_probe.main(sys.argv[1:], admission_cases=[failing_admission]))
