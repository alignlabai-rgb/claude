#!/usr/bin/env python3
"""Count the cases the suite ACTUALLY executes (Python 3.11+ stdlib only): runs tests/ once with a result class that records every subTest and its outcome, so README numbers are derived, not typed.
  python count_cases.py --out results/case-counts-py3.13.json"""
import argparse
import json
import os
import sys
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))


class Rec(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sub = {}

    def addSubTest(self, test, subtest, err):
        key = test.id().split(".")[-1]
        d = self.sub.setdefault(key, {"executed": 0, "failed": 0})
        d["executed"] += 1
        d["failed"] += err is not None
        super().addSubTest(test, subtest, err)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ns = ap.parse_args()
    sys.path.insert(0, os.path.join(HERE, "tests"))
    suite = unittest.defaultTestLoader.discover(os.path.join(HERE, "tests"))
    import io
    stream = io.StringIO()
    res = unittest.TextTestRunner(stream=stream, resultclass=Rec, verbosity=0).run(suite)
    import test_carrier as t
    doc = {"python": sys.version.split()[0], "tests_run": res.testsRun, "failures": len(res.failures), "errors": len(res.errors), "subtests_by_test": res.sub,
           "negative_table": {"cases": len(t.NEG), "hold": sum(c.verdict == "HOLD" for c in t.NEG), "rejected": sum(c.verdict == "REJECTED" for c in t.NEG)},
           "malformed_table_cases": len(t.MALFORMED), "subtests_executed_total": sum(v["executed"] for v in res.sub.values())}
    with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "subtests_by_test"}), {k: v["executed"] for k, v in res.sub.items()})
    return 0 if not (res.failures or res.errors) else 1


if __name__ == "__main__":
    sys.exit(main())
