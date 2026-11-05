#!/usr/bin/env python3
"""Seven one-line mutants of a COPY of stage_copy_reference.py (never the original): the unchanged tests must FAIL on each.  python selfcheck.py --out results/selfcheck-py3.13.json
Stderr of the child suites is shown (-W always::ResourceWarning) and any 'Warning' line is counted."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
MUT = [("S1", "source re-hash after the copy skipped", "after = _digest(src)", "after = before"),
       ("S2", "partial opened with overwrite instead of exclusive create", 'open(partial, "xb")', 'open(partial, "wb")'),
       ("S3", "final-name freshness check dropped", '    if os.path.lexists(final):\n        r.check("destination_final_name_is_fresh", True, False)', '    if False:\n        r.check("destination_final_name_is_fresh", True, False)'),
       ("S4", "root relationship not enforced", 'if r.roots != "DISJOINT":', "if False:"),
       ("S5", "destination digest taken from the copy stream instead of the file", "dest = _digest(partial)", 'dest = {"bytes": n, "sha256": stream.hexdigest()}'),
       ("S6", "partial promoted although verification failed", "    if not ok_src or not ok_dst:\n        return", "    if False:\n        return"),
       ("S7", "promotion by plain os.rename again (replaces an existing final on POSIX)", "os.link(partial, final, follow_symlinks=False)", "os.rename(partial, final)")]
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ns = ap.parse_args()
with open(os.path.join(HERE, "stage_copy_reference.py"), encoding="utf-8") as fh:
    src = fh.read()
res = []
for mid, what, a, b in MUT:
    assert src.count(a) == 1, mid
    with tempfile.TemporaryDirectory() as t:
        with open(os.path.join(t, "stage_copy_reference.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src.replace(a, b))
        p = subprocess.run([sys.executable, "-B", "-W", "always::ResourceWarning", "-m", "unittest", "discover", "-s", os.path.join(HERE, "tests")], capture_output=True, text=True, env=dict(os.environ, SCR_MODULE_DIR=t), cwd=HERE)
    out = p.stdout + p.stderr
    res.append({"id": mid, "what": what, "killed": p.returncode != 0, "failing_tests": sorted(set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", out, re.M))), "warning_lines": sum("Warning" in ln for ln in out.splitlines())})
doc = {"python": sys.version.split()[0], "mutants": res, "all_killed": all(r["killed"] for r in res), "warning_lines_total": sum(r["warning_lines"] for r in res)}
with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(doc, indent=1) + "\n")
for r in res:
    print(r["id"], "KILLED" if r["killed"] else "SURVIVED", r["failing_tests"])
print("all killed:", doc["all_killed"], "| warning lines:", doc["warning_lines_total"])
sys.exit(0 if doc["all_killed"] and not doc["warning_lines_total"] else 1)
