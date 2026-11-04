#!/usr/bin/env python3
"""Run the DEFAULT video_core suite (unchanged, tools/video-core/tests) and the NEW Japanese-caption suite on every available
Python (3.11 / 3.12 / 3.13 by default) and print {python: {suite: total/pass/skip/fail}} as JSON.

pass = total - skipped - failures - errors - expected_failures + 0 (unexpected successes count as failures).
Nothing here installs software; a missing interpreter is reported as "unavailable", not skipped silently.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PYTHONS = sys.argv[1:] or ["python3.11", "python3.12", "python3.13"]
SUITES = {
    "default_unchanged": (["-m", "unittest", "discover", "-s", "tests", "-t", "."], ROOT),
    "new_japanese_captions": (["-m", "unittest", "discover", "-s", os.path.join(HERE, "suite")], ROOT),
}


def parse(out: str) -> dict:
    m = re.search(r"^Ran (\d+) tests? in", out, re.M)
    if not m:
        return {"error": "no summary line", "tail": out[-300:]}
    total = int(m.group(1))
    tail = out.strip().splitlines()[-1]
    kv = dict((k, int(v)) for k, v in re.findall(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d+)", tail))
    fail = kv.get("failures", 0) + kv.get("errors", 0) + kv.get("unexpected successes", 0)
    skip = kv.get("skipped", 0)
    xfail = kv.get("expected failures", 0)
    return {"total": total, "pass": total - skip - fail - xfail, "skip": skip, "fail": fail, "result_line": tail}


def main() -> int:
    procs = {}
    for py in PYTHONS:
        exe = shutil.which(py)
        if not exe:
            procs[py] = None
            continue
        for name, (args, cwd) in SUITES.items():
            procs[(py, name)] = subprocess.Popen([exe, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    result = {}
    for py in PYTHONS:
        if procs.get(py, 1) is None:
            result[py] = "unavailable"
            continue
        ver = subprocess.run([shutil.which(py), "-c", "import sys;print(sys.version.split()[0])"], capture_output=True, text=True).stdout.strip()
        result[py] = {"version": ver}
        for name in SUITES:
            out, _ = procs[(py, name)].communicate()
            result[py][name] = parse(out)
    print(json.dumps(result, indent=1, sort_keys=True))
    bad = [(py, n) for py, v in result.items() if isinstance(v, dict) for n in SUITES if v[n].get("fail", 1) != 0]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
