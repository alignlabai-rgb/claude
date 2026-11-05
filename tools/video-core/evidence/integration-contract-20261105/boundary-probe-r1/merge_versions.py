#!/usr/bin/env python3
"""Cross-Python-version consistency of probe results (Python 3.11+ stdlib only).

Inputs: several probe output directories produced by boundary_probe.py on DIFFERENT interpreters of the same sources.  Output: CROSS_VERSION.json (create-only) with, per source and check,
whether outcome and observed value are identical on every interpreter, and per invocation whether exit code and normalised stdout hash are identical.
  python merge_versions.py --run 3.11=<out dir> --run 3.12=<out dir> --run 3.13=<out dir> --out CROSS_VERSION.json
Disagreement is reported, never averaged or hidden."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.dont_write_bytecode = True


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, metavar="TAG=DIR")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    runs = {}
    for r in ns.run:
        tag, _, d = r.partition("=")
        runs[tag] = d
    py, exit_codes, cases = {}, {}, {}
    sources = None
    for tag, d in runs.items():
        top = read(os.path.join(d, "run.json"))
        py[tag] = top["python"]
        exit_codes[tag] = top["exit_code"]
        labels = [s["source_label"] for s in top["sources"]]
        sources = labels if sources is None else sources
        if labels != sources:
            print("ERROR: different source sets", labels, sources)
            return 1
        for lab in labels:
            for f in sorted(glob.glob(os.path.join(d, "sources", lab, "cases", "*.json"))):
                c = read(f)
                for chk in c["checks"]:
                    if chk["layer"] in ("INPUT_CONTRACT_EXPECTATION", "OBSERVATION", "NOT_APPLICABLE", "SKIPPED"):
                        cases.setdefault((lab, chk["key"]), {})[tag] = {"outcome": chk["outcome"], "observed": json.dumps(chk.get("observed"), sort_keys=True), "layer": chk["layer"]}
                for inv in c.get("invocations", []):
                    cases.setdefault((lab, "INV::" + c["case"] + "::" + inv["label"]), {})[tag] = {"outcome": "INV", "observed": json.dumps([inv["exit_code"], inv.get("stdout_sha256_normalized")]), "layer": "INVOCATION"}
    rows, bad = [], 0
    for (lab, key), per in sorted(cases.items()):
        same_o = len({v["outcome"] for v in per.values()}) == 1
        same_v = len({v["observed"] for v in per.values()}) == 1
        complete = set(per) == set(runs)
        if not (same_o and same_v and complete):
            bad += 1
            rows.append({"source": lab, "key": key, "complete": complete, "same_outcome": same_o, "same_observed": same_v, "per_python": per})
    doc = {"cross_version_version": 1, "pythons": py, "probe_exit_codes": exit_codes, "sources": sources, "compared_items": len(cases), "disagreements": bad, "disagreement_rows": rows,
           "note": "'observed' includes the normalised stdout hash of every invocation (world paths placeholdered); a disagreement is a finding about interpreter-dependent behaviour or a harness gap, not noise."}
    with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print("compared", len(cases), "items over", sorted(runs), "-> disagreements:", bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
