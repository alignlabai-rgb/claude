#!/usr/bin/env python3
"""Run the 12 cases once and write the exact per-case results (outcome, reason, checks, residue, directory listing) as create-only JSON.  python report.py --out results/cases-py3.13.json"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tests"))
import test_stage_copy as t  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ns = ap.parse_args()
doc = {"python": sys.version.split()[0], "os_name": os.name, "platform": sys.platform, "cases": {}}
for fn in t.CASES:
    out = fn()
    doc["cases"][fn.__name__] = {k: ({"outcome": v[0]["outcome"], "reason": v[0]["reason"], "completed": v[0]["completed"], "destination_listing_after": v[0]["dest_listing"],
                                      "residue": v[1]["residue"], "checks": v[1]["checks"], "root_relationship": v[1]["root_relationship"], "observed": v[1]["observed"]} if isinstance(v, tuple) else v) for k, v in out.items()}
with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
print({c: {k: (v["outcome"] + "/" + v["reason"] if isinstance(v, dict) and "outcome" in v else v) for k, v in d.items()} for c, d in doc["cases"].items() if c in ("c01_normal_copy_and_zero_byte", "c04_copy_interrupted_midway", "c05_source_replaced_while_reading")})
