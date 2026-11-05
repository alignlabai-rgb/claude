#!/usr/bin/env python3
"""Compare the per-check outcomes of a portable-r2 run with those of the closed harness's committed run on the SAME library (stdlib only; read-only).

  python compare_with_closed_harness.py --r2-run <r2 --out-dir> --closed-run <portable/results/py3.13> [--out FILE]
For every (case, check name) present in both: same layer and same outcome? Checks only in one side are listed (r2 adds mechanism checks; PC00 differs only in the cross-check it no longer skips).
Exit 0 only if no shared check differs.
"""
import argparse
import glob
import json
import os
import sys


def load(d):
    out = {}
    for f in glob.glob(os.path.join(d, "cases", "*.json")):
        with open(f, encoding="utf-8") as fh:
            c = json.load(fh)
        out[c["case"]] = c
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--r2-run", required=True)
    ap.add_argument("--closed-run", required=True)
    ap.add_argument("--out")
    ns = ap.parse_args()
    a, b = load(ns.closed_run), load(ns.r2_run)
    differ, only_closed, only_r2, shared = [], [], [], 0
    for case in sorted(set(a) | set(b)):
        ca = {c["name"]: c for c in a.get(case, {}).get("checks", [])}
        cb = {c["name"]: c for c in b.get(case, {}).get("checks", [])}
        for n in sorted(set(ca) | set(cb)):
            if n not in cb:
                only_closed.append([case, n])
            elif n not in ca:
                only_r2.append([case, n])
            else:
                shared += 1
                if (ca[n]["layer"], ca[n]["outcome"]) != (cb[n]["layer"], cb[n]["outcome"]):
                    differ.append({"case": case, "check": n, "closed": [ca[n]["layer"], ca[n]["outcome"]], "r2": [cb[n]["layer"], cb[n]["outcome"]]})
    doc = {"shared_checks": shared, "shared_checks_that_differ": differ, "only_in_closed_run": only_closed, "only_in_r2_run": only_r2,
           "cases_only_in_r2": sorted(set(b) - set(a)), "cases_only_in_closed": sorted(set(a) - set(b))}
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in doc.items()}, indent=1))
    if ns.out:
        with open(ns.out, "x", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
