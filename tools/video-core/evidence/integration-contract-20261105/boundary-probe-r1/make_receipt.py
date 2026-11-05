#!/usr/bin/env python3
"""Compact, portable RECEIPT of one probe output directory (Python 3.11+ stdlib only; reads the directory, writes RECEIPT.json + RECEIPT.txt next to it only if --write).

The receipt is what a Windows (or any) coordinator sends back or diffs against the Linux receipt: interpreter/platform, harness+probe file hashes, deny-admission result,
per source: pin status and digests, case statuses, counts of MET / NOT_MET / NOT_APPLICABLE / SKIPPED, and the attribution summary.  It makes no claim beyond what ran on that machine.
  python make_receipt.py --out-dir <probe output> [--write]"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.dont_write_bytecode = True


def read(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def build(out_dir):
    top = read(os.path.join(out_dir, "run.json"))
    attr_path = os.path.join(out_dir, "ATTRIBUTION.json")
    attr = read(attr_path) if os.path.isfile(attr_path) else None
    srcs = []
    for s in top["sources"]:
        cases_dir = os.path.join(out_dir, "sources", s["source_label"], "cases")
        na, sk = [], []
        for fn in sorted(os.listdir(cases_dir)):
            c = read(os.path.join(cases_dir, fn))
            for chk in c["checks"]:
                if chk["outcome"] == "NOT_APPLICABLE":
                    na.append(chk["key"])
                elif chk["outcome"] == "SKIPPED":
                    sk.append({"key": chk["key"], "reason": chk.get("reason")})
        srcs.append({"label": s["source_label"], "role": s["source_role"], "commit": s["source_commit"], "pin_status": s["pin"]["status"], "profile_applicable": s["profile_applicable"],
                     "package_digest_raw": s["package_digest_raw"], "all_digest_raw_before": s["all_digest_raw_before"], "all_digest_raw_after": s["all_digest_raw_after"],
                     "unchanged_during_run": s["source_unchanged_during_run"], "admission": s["admission"]["statuses"], "children_started": s["children_started"], "case_status_counts": s["case_status_counts"],
                     "cases": s["cases"], "check_outcomes": s["check_outcomes"], "not_applicable_checks": na, "skipped_checks": sk})
    rec = {"receipt_version": 1, "probe": top["probe"], "python": top["python"], "exit_code": top["exit_code"], "wall_seconds": top["wall_seconds"], "stopped": top["stopped"], "hard_failures": top["hard_failures"],
           "pin_problems": top["pin_problems"], "harness_files_sha256": top["harness_files_sha256"], "probe_files_sha256": top["probe_files_sha256"], "sources": srcs,
           "attribution_summary": None if attr is None else attr["summary"], "claims": top["claims"]}
    return rec


def text(rec):
    py = rec["python"]
    lines = [f"RECEIPT boundary-probe: Python {py['version']} {py['implementation']} on {py['platform']} (os.name={py['os_name']}, fs={py['filesystem_encoding']}) exit={rec['exit_code']} wall={rec['wall_seconds']}s"]
    for s in rec["sources"]:
        lines.append(f"  {s['label']} ({s['role']}, {s['commit'][:7]}): pin={s['pin_status']} unchanged={s['unchanged_during_run']} admission={'/'.join(s['admission'])} children={s['children_started']}")
        lines.append(f"      cases {json.dumps(s['case_status_counts'], sort_keys=True)}  checks {json.dumps(s['check_outcomes'], sort_keys=True)}")
        if s["not_applicable_checks"]:
            lines.append(f"      NOT_APPLICABLE: {len(s['not_applicable_checks'])} check(s)")
        for k in s["skipped_checks"]:
            lines.append(f"      SKIPPED: {k['key']} - {k['reason']}")
    if rec["attribution_summary"]:
        lines.append("  attribution by finding: " + json.dumps(rec["attribution_summary"]["by_finding"], sort_keys=True))
    if rec["stopped"]:
        lines.append("  STOPPED: " + rec["stopped"]["message"])
    for k in ("hard_failures", "pin_problems"):
        if rec[k]:
            lines.append(f"  {k.upper()}: " + "; ".join(rec[k]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--write", action="store_true")
    ns = ap.parse_args(argv)
    rec = build(ns.out_dir)
    t = text(rec)
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(t.encode(enc, "backslashreplace").decode(enc, "replace"))
    if ns.write:
        for name, data in (("RECEIPT.json", (json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")), ("RECEIPT.txt", t.encode("utf-8"))):
            with open(os.path.join(ns.out_dir, name), "xb") as fh:
                fh.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
