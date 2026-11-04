#!/usr/bin/env python3
"""Runs the adversarial-review suite once and writes a machine-readable summary.

  python3.X run_audit.py [--strict] [--label LABEL] [--wall-limit SECONDS] [-v]

* Discovers test_adv_*.py in this directory (the existing production suite is NOT touched and not discovered here).
* default mode : tests marked @known_defect are expected failures (the defect is reproduced and counted separately).
  --strict      : ADV_STRICT=1, the same tests are hard FAILURES (what an adopter should gate on until the defects are fixed).
* Hard overall wall limit (default 300 s, documented in REPORT.md): the interpreter is terminated by faulthandler if exceeded.
* Output: runs/<label>/results.json + observations.jsonl (+ cases/ evidence), human log on stdout.
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
import shutil
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--label")
    ap.add_argument("--wall-limit", type=float, default=300.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--pattern", default="test_adv_*.py")
    ns = ap.parse_args()
    mode = "strict" if ns.strict else "default"
    label = ns.label or "py%d.%d-%s" % (sys.version_info[0], sys.version_info[1], mode)
    os.environ["ADV_RUN_LABEL"] = label
    os.environ["ADV_STRICT"] = "1" if ns.strict else "0"
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.dont_write_bytecode = True
    sys.path.insert(0, HERE)
    run_dir = os.path.join(HERE, "runs", label)
    shutil.rmtree(run_dir, ignore_errors=True)
    os.makedirs(run_dir, exist_ok=True)
    faulthandler.dump_traceback_later(ns.wall_limit, exit=True)

    class Result(unittest.TextTestResult):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.passed, self.reproduced, self.detail = [], [], []

        def addSuccess(self, test):
            super().addSuccess(test)
            self.passed.append(test.id())

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            fn = getattr(test, test._testMethodName, None)
            self.reproduced.append({"test": test.id(), "finding": getattr(fn, "__finding__", None)})

    suite = unittest.defaultTestLoader.discover(HERE, pattern=ns.pattern, top_level_dir=HERE)
    t0 = time.monotonic()
    runner = unittest.TextTestRunner(verbosity=2 if ns.verbose else 1, resultclass=Result, stream=sys.stdout)
    res = runner.run(suite)
    wall = time.monotonic() - t0
    faulthandler.cancel_dump_traceback_later()

    obs = []
    op = os.path.join(run_dir, "observations.jsonl")
    if os.path.exists(op):
        with open(op, encoding="utf-8") as fh:
            obs = [json.loads(line) for line in fh if line.strip()]
    seeds = {o["case"]: {"seed": o["seed"], "cases": o.get("cases")} for o in obs if "seed" in o}
    summary = {
        "python": sys.version.split()[0], "executable": sys.executable, "platform": sys.platform, "mode": mode, "label": label,
        "total": res.testsRun, "passed": len(res.passed), "skipped": len(res.skipped), "failures": len(res.failures), "errors": len(res.errors),
        "expected_failures_reproduced_defects": len(res.expectedFailures), "unexpected_successes": len(res.unexpectedSuccesses),
        "reproduced": sorted(res.reproduced, key=lambda r: r["test"]),
        "failure_ids": [t.id() for t, _ in res.failures], "error_ids": [t.id() for t, _ in res.errors],
        "skips": [{"test": t.id(), "reason": r} for t, r in res.skipped],
        "wall_seconds": round(wall, 2), "wall_limit_seconds": ns.wall_limit, "seeds_and_case_counts": seeds,
        "baseline_plan_sha256": next((o["sha256"] for o in obs if o.get("case") == "baseline_plan_hash"), None),
        "observation_records": len(obs),
    }
    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
    print("\nSUMMARY " + json.dumps({k: summary[k] for k in ("python", "mode", "total", "passed", "skipped", "failures", "errors",
                                                              "expected_failures_reproduced_defects", "unexpected_successes", "wall_seconds")}))
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
