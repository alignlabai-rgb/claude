#!/usr/bin/env python3
"""A few targeted mutation controls (Python 3.11+ stdlib only).  Each mutant is a one-line textual change to a COPY of timing_carrier_reference.py in a new temp directory (never the original);
the unchanged test suite then runs against the copy (TCR_MODULE_DIR) and must FAIL.  The unmutated copy must pass.  Failures are retained in the JSON, not hidden.
  python mutation_controls.py --out MUTATION_RESULTS.json"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
MUTANTS = [
    ("MC1", "bool accepted as an integer", "return type(x) is int and lo <=", "return isinstance(x, int) and lo <="),
    ("MC2", "timeline boundaries no longer pinned (duration-only comparison)", 'PIN_FIELDS = ("event_id", "binding_ref", "source_sha256", "sample_rate", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample")',
     'PIN_FIELDS = ("event_id", "binding_ref", "source_sha256", "sample_rate", "source_in_sample", "source_out_sample")'),
    ("MC3", "UNQUALIFIED / UNKNOWN status no longer holds", "if not q:  ", "if False:  "),
    ("MC4", "profile content pin not enforced", "same = psha == PINNED_PROFILE_SHA256 ", "same = True "),
    ("MC5", "a missing selected-USE record no longer holds", 'acc.reason("HOLD", "MISSING_RECORD"', 'acc.reason("NOTE", "MISSING_RECORD"'),
    ("MC6", "stale qualification_revision ignored", "if not fresh:  ", "if False:  "),
    ("MC7", "silence inferred from the absence of a decision", 'acc.reason("HOLD", "MISSING_SILENCE_DECISION"', 'acc.reason("NOTE", "MISSING_SILENCE_DECISION"'),
    ("MC8", "empty effective scope no longer holds", "if not effective:  ", "if False:  "),
    ("MC9", "a record for a non-USE row tolerated", 'elif not (row["selected"] and row["state"] == "USE"):', "elif False:"),
]


def run_tests(moddir):
    env = dict(os.environ, TCR_MODULE_DIR=moddir, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-B", "-W", "always::ResourceWarning", "-m", "unittest", "discover", "-s", os.path.join(HERE, "tests")], capture_output=True, text=True, env=env, cwd=HERE, timeout=300)
    failing = sorted(set(m[:160] for m in re.findall(r"^(?:FAIL|ERROR): (.+)$", p.stdout + p.stderr, re.M)))
    ran = re.search(r"Ran (\d+) tests?", p.stdout + p.stderr)
    warn = [ln[:200] for ln in (p.stdout + p.stderr).splitlines() if "Warning" in ln]        # ResourceWarning is shown (-W always) so an unclosed handle in the suite cannot hide
    return p.returncode, failing, int(ran.group(1)) if ran else None, warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ns = ap.parse_args()
    with open(os.path.join(HERE, "timing_carrier_reference.py"), encoding="utf-8") as fh:
        src = fh.read()
    res = {"python": sys.version.split()[0], "mutants": []}
    with tempfile.TemporaryDirectory() as t0:
        with open(os.path.join(t0, "timing_carrier_reference.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        code, failing, ran, warn = run_tests(t0)
        res["unmutated_copy"] = {"exit": code, "tests_run": ran, "failing": failing, "warning_lines": warn}
    for mid, what, old, new in MUTANTS:
        if src.count(old) != 1:
            res["mutants"].append({"id": mid, "what": what, "error": f"mutation text occurs {src.count(old)} times"})
            continue
        with tempfile.TemporaryDirectory() as t:
            with open(os.path.join(t, "timing_carrier_reference.py"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src.replace(old, new))
            code, failing, ran, warn = run_tests(t)
        res["mutants"].append({"id": mid, "what": what, "old": old, "new": new, "killed": code != 0, "tests_run": ran, "failing_tests": failing, "warning_lines": warn})
    res["all_killed"] = all(m.get("killed") for m in res["mutants"]) and res["unmutated_copy"]["exit"] == 0
    res["warning_lines_total"] = len(res["unmutated_copy"]["warning_lines"]) + sum(len(m.get("warning_lines", [])) for m in res["mutants"])
    with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    for m in res["mutants"]:
        print(m["id"], "KILLED" if m.get("killed") else "SURVIVED/ERROR", (len(m["failing_tests"]), "failing test case(s), e.g.", m["failing_tests"][0][:110]) if m.get("failing_tests") else m.get("error"))
    print("unmutated copy: exit", res["unmutated_copy"]["exit"], "| all killed:", res["all_killed"], "| warning lines in all child runs:", res["warning_lines_total"])
    return 0 if res["all_killed"] and res["warning_lines_total"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
