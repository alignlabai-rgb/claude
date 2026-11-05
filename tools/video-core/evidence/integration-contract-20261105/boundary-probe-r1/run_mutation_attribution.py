#!/usr/bin/env python3
"""Mechanism attribution by DISPOSABLE MUTANTS of the candidate (Python 3.11+ stdlib only).

For each of the three documented mechanisms of 092b420 this reverts exactly that one line in a COPY of the candidate export (never in the export itself, never in the repository),
runs the SAME probe cases against the copy, and compares every check with the unmutated candidate run.  If the probe discriminates by mechanism, reverting
  receipt.dumps backslashreplace  -> only F-CLI-1 checks flip   (M-CLI)
  paths._utf8_encodable guard     -> only F-ENC-1 checks flip   (M-ENC)
  audiomix/plan.py zero-sample    -> only F-AUD-1 checks flip   (M-AUD)
A mutant is a measurement of detection power, NOT a candidate: nothing here is a review, an adoption or a change proposal.

  python run_mutation_attribution.py --candidate-root <092b420 export> --baseline-sources-dir <probe out>/sources --scratch-root DIR --out-dir NEWDIR
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import boundary_probe  # noqa: E402

MUTANTS = {
    "M-CLI": {"finding": "F-CLI-1", "file": "video_core/receipt.py", "old": '.encode("utf-8", "backslashreplace")', "new": '.encode("utf-8")', "means": "receipt.dumps strict UTF-8 again (the pre-092b420 behaviour)"},
    "M-ENC": {"finding": "F-ENC-1", "file": "video_core/paths.py", "old": 'def _utf8_encodable(text: str) -> bool:\n    try:', "new": 'def _utf8_encodable(text: str) -> bool:\n    return True\n    try:',
              "means": "paths.real_directory accepts every root again"},
    "M-AUD": {"finding": "F-AUD-1", "file": "video_core/audiomix/plan.py", "old": 'if info["s1"] <= info["s0"] and sel and e.state != "OMIT":', "new": 'if info["s1"] <= info["s0"]:',
              "means": "event_zero_samples applies to every event again"},
}


def load_checks(cases_dir):
    out = {}
    for f in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            c = json.load(fh)
        for chk in c["checks"]:
            if chk["layer"] == "INPUT_CONTRACT_EXPECTATION":
                out[chk["key"]] = (chk["outcome"], chk.get("finding"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate-root", required=True)
    ap.add_argument("--baseline-sources-dir", required=True, help="the sources/ directory of a probe output that includes the unmutated 092b420")
    ap.add_argument("--scratch-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--harness-dir")
    ns = ap.parse_args(argv)
    if sys.version_info < (3, 11):
        return 3
    base = load_checks(os.path.join(ns.baseline_sources_dir, "092b420", "cases"))
    if not base:
        print("refused: no baseline checks for 092b420")
        return 2
    if os.path.lexists(ns.out_dir):
        print("refused: output directory exists")
        return 2
    os.mkdir(ns.out_dir)
    scratch = os.path.realpath(ns.scratch_root)
    report = {"mutation_attribution_version": 1, "baseline_checks": len(base), "mutants": {}}
    for mid, m in MUTANTS.items():
        work = os.path.join(scratch, "mut-" + mid)
        if os.path.lexists(work):
            shutil.rmtree(work)
        shutil.copytree(ns.candidate_root, work, ignore=shutil.ignore_patterns("__pycache__"))
        target = os.path.join(work, *m["file"].split("/"))
        with open(target, "rb") as fh:
            data = fh.read().decode("utf-8")
        if data.count(m["old"]) != 1:
            print(f"ERROR: mutation text for {mid} occurs {data.count(m['old'])} times in {m['file']}")
            return 1
        with open(target, "wb") as fh:
            fh.write(data.replace(m["old"], m["new"]).encode("utf-8"))
        mscratch = os.path.join(scratch, "scr-" + mid)
        os.mkdir(mscratch)
        mout = os.path.join(ns.out_dir, "mutants", mid)
        os.makedirs(os.path.dirname(mout), exist_ok=True)
        argv2 = ["--source", f"092b420={work}", "--scratch-root", mscratch, "--out-dir", mout, "--normalize-stored-paths"]
        if ns.harness_dir:
            argv2 += ["--harness-dir", ns.harness_dir]
        code = boundary_probe.main(argv2)
        # exit 4 is EXPECTED: the mutated tree no longer matches the 092b420 pin (it must be labelled UNPINNED, never PINNED_EXACT)
        mut = load_checks(os.path.join(mout, "sources", "092b420", "cases"))
        flipped, by_finding, unaffected = [], {}, 0
        for k, (oc, fd) in base.items():
            if mut.get(k, (None,))[0] != oc:
                flipped.append({"key": k, "finding": fd, "baseline": oc, "mutant": mut.get(k, (None,))[0]})
                by_finding[fd] = by_finding.get(fd, 0) + 1
            else:
                unaffected += 1
        run = json.load(open(os.path.join(mout, "run.json"), encoding="utf-8"))
        report["mutants"][mid] = {"mutation": m["means"], "file": m["file"], "probe_exit_code": code, "pin_status": run["sources"][0]["pin"]["status"], "flipped_checks": len(flipped),
                                  "flipped_by_finding": by_finding, "unaffected_checks": unaffected, "only_expected_finding_flipped": bool(flipped) and set(by_finding) == {m["finding"]},
                                  "flipped": flipped}
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(mscratch, ignore_errors=True)
    with open(os.path.join(ns.out_dir, "MUTATION_ATTRIBUTION.json"), "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    for mid, r in report["mutants"].items():
        print(mid, "pin", r["pin_status"], "exit", r["probe_exit_code"], "flipped", r["flipped_checks"], r["flipped_by_finding"], "only-expected-finding:", r["only_expected_finding_flipped"])
    return 0 if all(r["only_expected_finding_flipped"] and r["pin_status"] == "UNPINNED" for r in report["mutants"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
