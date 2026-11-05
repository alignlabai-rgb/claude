#!/usr/bin/env python3
"""Reproduction of the independently reported false-READY cases against a given caller-reference candidate directory (read-only; stdlib; synthetic values only).

    python3 reproduce_false_ready.py --candidate <dir containing caller_admission.py + caller_fixtures.py> --out <result.json>

Method (as reported): take the candidate's own `world()` as a seed, correct audio_receipt.response.outputs[role=plan].sha256 to the owner's plan pin IN MEMORY (the 508d263 fixture used a different
value there), then change ONE field per case. Controls: corrected positive READY, valid unselected UNKNOWN READY, cross-job REJECTED, ordinary ZERO_FILL warning HOLD.
For each case the result records the status, the reason codes and the outcome of every comparison stage, so a false READY can be seen to have reached all stages.
No file other than --out is written; nothing is executed besides the candidate's pure function."""
import argparse
import copy
import importlib
import json
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--candidate", required=True)
ap.add_argument("--out", required=True)
ns = ap.parse_args()
sys.path.insert(0, ns.candidate)
ca = importlib.import_module("caller_admission")
cf = importlib.import_module("caller_fixtures")


def seed(rows=None, **kw):
    e, o = cf.world(rows, **kw) if rows is not None else cf.world(**kw)
    for out in o["stages"]["audio_receipt"]["response"]["outputs"]:
        if out.get("role") == "plan":
            out["sha256"] = e["pins"]["plan_sha256"]  # the in-memory correction
    return e, o


def get(o, *path):
    node = o
    for k in path:
        node = node[k]
    return node


def setp(o, path, value):
    node = o
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value


def run(e, o):
    r = ca.evaluate(e, o, ca.PROFILE_V0_UNREVIEWED)
    return {"status": r["status"], "reasons": sorted({x["code"] for x in r["reasons"]}), "stages": {c["id"]: c["outcome"] for c in r["checks"]}}


AR = ("stages", "audio_receipt", "response")
VR = ("stages", "video_receipt", "response")
cases = []


def case(n, title, mutate, rows=None, prof=None, **kw):
    e, o = seed(rows, **kw)
    mutate(e, o)
    cases.append((n, title, e, o, prof))


case(1, "video readback.comparison.checks = null", lambda e, o: setp(o, VR + ("readback", "comparison", "checks"), None))
case(2, "decoded_frame_count observed '99999' with ok=true", lambda e, o: setp(o, VR + ("readback", "comparison", "checks", 0, "observed"), "99999"))
case(3, "audio stem output bytes = -1", lambda e, o: setp(o, AR + ("outputs", 0, "bytes"), -1))
case(4, "audio plan output partial = true", lambda e, o: setp(o, AR + ("outputs", 1, "partial"), True))
case(5, "audio plan output sha256 differs from the receipt's plan_sha256", lambda e, o: setp(o, AR + ("outputs", 1, "sha256"), cf.H("another plan file")))
case(6, "plan.coverage.covered_samples = 0 (total 9600, gap_count 0)", lambda e, o: setp(o, ("stages", "audio_plan", "response", "plan", "coverage", "covered_samples"), 0))
case(7, "video outputs: a valid video object PLUS a non-object entry", lambda e, o: get(o, *VR, "outputs").append("garbage"))
case(8, "profile: same id/version, ONLY treated_warning_codes=[ZERO_FILL_APPLIED] changed (world carries an ordinary ZERO_FILL_APPLIED warning)", lambda e, o: None,
     warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "1 gap zero-filled"}], prof="treat_zero_fill")


def drop_receipt(e, o):
    del o["stages"]["audio_receipt"]


case(9, "profile: same id/version, required_stages without audio_receipt, required_pins plan/stem/video; audio_receipt packet removed", drop_receipt, prof="no_receipt")
U1 = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "u1", "state": "UNKNOWN", "selected": False}]
case(10, "unselected UNKNOWN u1: receipt unresolved_report[u1].selected = 1 (int)", lambda e, o: setp(o, AR + ("unresolved_report", 0, "selected"), 1), rows=U1)

out = {"candidate": ns.candidate, "python": sys.version.split()[0], "controls": {}, "cases": []}
e, o = seed()
out["controls"]["corrected_positive"] = run(e, o)
e, o = seed(U1)
out["controls"]["valid_unselected_unknown_with_use"] = run(e, o)
e, o = seed()
o["stages"]["audio_plan"]["identity"]["job_id"] = "other-job"
out["controls"]["cross_job_negative"] = run(e, o)
e, o = seed(warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}])
out["controls"]["ordinary_zero_fill_warning"] = run(e, o)
for n, title, e, o, prof in cases:
    p = copy.deepcopy(ca.PROFILE_V0_UNREVIEWED)
    if prof == "treat_zero_fill":
        p["treated_warning_codes"] = ["ZERO_FILL_APPLIED"]
    elif prof == "no_receipt":
        p["required_stages"] = [s for s in p["required_stages"] if s != "audio_receipt"]
        p["required_pins"] = ["plan_sha256", "stem_sha256", "video_sha256"]
    r = ca.evaluate(e, o, p)
    out["cases"].append({"case": n, "title": title, "status": r["status"], "false_ready": r["status"] == "READY", "reasons": sorted({x["code"] for x in r["reasons"]}),
                         "stages": {c["id"]: c["outcome"] for c in r["checks"]}})
out["false_ready_count"] = sum(c["false_ready"] for c in out["cases"])
with open(ns.out, "x", encoding="utf-8") as fh:
    json.dump(out, fh, indent=1, sort_keys=True)
    fh.write("\n")
print(json.dumps({"false_ready_count": out["false_ready_count"], "controls": {k: v["status"] for k, v in out["controls"].items()},
                  "cases": [(c["case"], c["status"]) for c in out["cases"]]}))
