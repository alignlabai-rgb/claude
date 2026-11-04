#!/usr/bin/env python3
"""Applies candidate.patch to FRESH TEMPORARY exports of the base commit and runs the regression tests there (never in the repo tree).

  A  base export, unmodified                       -> default suite (original tests)           [reference counts]
  B  base export + candidate.patch                 -> full suite (original tests + test_color_policy.py)
  C  base export + candidate.patch, new test file removed -> ORIGINAL suite against the patched code  [must equal A]
  D  B again on one Python with VIDEO_CORE_INTEGRATION=1, only tests.test_color_policy (7 single-frame ffmpeg runs, 16x16, <=10 s each)

Counts are parsed from unittest's own summary line.  Stdlib only.  Usage: run_patch_tests.py <workdir> [--no-integration]
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_SHA = "b4ca4a3b031351a631c0fbe1eb149935f4db19ba"
PLAN_SHA = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"
PYTHONS = ["python3.11", "python3.12", "python3.13"]
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "LC_ALL": "C"}


def git_root():
    return subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()


def export_base(dest):
    os.makedirs(dest)
    arc = subprocess.run(["git", "-C", git_root(), "archive", BASE_SHA, "tools/video-core"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", dest], input=arc, check=True)
    return os.path.join(dest, "tools", "video-core")


def apply_patch(dest):
    patch = os.path.join(HERE, "candidate.patch")
    subprocess.run(["git", "apply", "--check", "-p1", patch], cwd=dest, check=True)
    subprocess.run(["git", "apply", "-p1", patch], cwd=dest, check=True)


def run_suite(py, root, logfile, extra_env=None, args=("discover", "-s", "tests", "-t", ".")):
    env = dict(ENV, **(extra_env or {}))
    p = subprocess.run([py, "-m", "unittest", *args], cwd=root, env=env, capture_output=True, text=True, timeout=900)
    text = p.stdout + p.stderr
    with open(logfile, "w") as fh:
        fh.write(text)
    ran = int(re.search(r"Ran (\d+) tests?", text).group(1))
    m = re.search(r"^(OK|FAILED)(?: \((.*)\))?$", text, re.M)
    kv = dict((k, int(v)) for k, v in re.findall(r"(\w+)=(\d+)", m.group(2) or ""))
    skipped, failed, errored = kv.get("skipped", 0), kv.get("failures", 0), kv.get("errors", 0)
    return {"python": subprocess.run([py, "--version"], capture_output=True, text=True).stdout.strip(), "returncode": p.returncode,
            "total": ran, "passed": ran - skipped - failed - errored, "skipped": skipped, "failed": failed + errored, "status": m.group(1),
            "log": os.path.relpath(logfile, HERE) if logfile.startswith(HERE) else logfile}


def plan_hashes(py, root):
    code = ("import sys; sys.dont_write_bytecode=True; sys.path.insert(0, '.')\n"
            "from video_core import prepare\n"
            "d=open('examples/episode.synthetic.json','rb').read()\n"
            "import json\n"
            "out={'default': prepare(d).sha256}\n"
            "try:\n"
            "    out['opt_in']=prepare(d, color_policy='bt709-tv-gbrp-v1').sha256\n"
            "except TypeError as e:\n"
            "    out['opt_in']='n/a: '+str(e)\n"
            "print(json.dumps(out))")
    return json.loads(subprocess.run([py, "-c", code], cwd=root, env=ENV, capture_output=True, text=True, check=True).stdout)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    work = os.path.abspath(sys.argv[1])
    integration = "--no-integration" not in sys.argv
    if os.path.exists(work):
        sys.exit("workdir must not exist (fresh temporary export)")
    logs = os.path.join(HERE, "regression")
    os.makedirs(logs, exist_ok=True)
    res = {"base_sha": BASE_SHA, "started_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "patch_sha256": None,
           "copies": {}, "note": "all runs in fresh temporary exports of the base commit; the repository working tree is never the test subject"}
    import hashlib
    res["patch_sha256"] = hashlib.sha256(open(os.path.join(HERE, "candidate.patch"), "rb").read()).hexdigest()
    a = export_base(os.path.join(work, "A_base"))
    b = export_base(os.path.join(work, "B_patched"))
    apply_patch(os.path.join(work, "B_patched"))
    c = export_base(os.path.join(work, "C_patched_original_tests"))
    apply_patch(os.path.join(work, "C_patched_original_tests"))
    os.remove(os.path.join(c, "tests", "test_color_policy.py"))
    for name, root in (("A_base_default_suite", a), ("B_patched_full_suite", b), ("C_patched_original_tests_only", c)):
        res["copies"][name] = {"plan_hashes": plan_hashes("python3.13", root), "runs": []}
        for py in PYTHONS:
            if shutil.which(py) is None:
                res["copies"][name]["runs"].append({"python": py, "status": "NOT AVAILABLE"})
                continue
            r = run_suite(py, root, os.path.join(logs, f"{name}.{py}.log"))
            res["copies"][name]["runs"].append(r)
            print(name, py, {k: r[k] for k in ("total", "passed", "skipped", "failed", "status")}, flush=True)
    if integration:
        r = run_suite("python3.13", b, os.path.join(logs, "D_patched_optin_integration.python3.13.log"), {"VIDEO_CORE_INTEGRATION": "1"},
                      ("-v", "tests.test_color_policy"))
        res["copies"]["D_patched_optin_integration_color_policy_only"] = {"runs": [r], "note": "7 single-frame 16x16 ffmpeg runs (regression test, NOT study experiments)"}
        print("D", {k: r[k] for k in ("total", "passed", "skipped", "failed", "status")}, flush=True)
    res["finished_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(HERE, "patch-test-results.json"), "w") as fh:
        json.dump(res, fh, indent=1)
        fh.write("\n")
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
