#!/usr/bin/env python3
"""Finite repeated-run matrix. NO retry-until-green: every iteration is recorded, failures keep their output.

    python3 repeat_matrix.py --tree TOOLS_VIDEO_CORE_DIR --python python3.12 --test tests.test_containment.X.y --n 60 --load none|busy4 --label LABEL --out OUT.json

Each iteration is a fresh `python -m unittest <test>` process (timeout 60 s). `--load busy4` runs 4 pure-Python busy loops (one per CPU of the 4-CPU sandbox) for the duration; they are
started and killed by this script and carry their own timeout. No network, no media."""
import argparse, json, os, signal, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--tree", required=True); ap.add_argument("--python", required=True); ap.add_argument("--test", required=True)
ap.add_argument("--n", type=int, required=True); ap.add_argument("--load", choices=("none", "busy4"), default="none"); ap.add_argument("--label", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
loaders = []
if a.load == "busy4":
    loaders = [subprocess.Popen([sys.executable, "-c", "import time\nend=time.time()+900\nwhile time.time()<end: pass"]) for _ in range(4)]
    time.sleep(0.5)
rows, fails = [], []
t0 = time.monotonic()
try:
    for i in range(a.n):
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([a.python, "-m", "unittest", a.test], capture_output=True, cwd=a.tree, env=env, timeout=60)
        ok = p.returncode == 0
        rows.append(ok)
        if not ok:
            txt = p.stderr.decode("utf-8", "replace")
            fails.append({"iteration": i, "returncode": p.returncode, "tail": txt[-1800:]})
finally:
    for l in loaders:
        l.kill()
        l.wait()
res = {"label": a.label, "python": a.python, "test": a.test, "load": a.load, "iterations": a.n, "pass": sum(rows), "fail": a.n - sum(rows), "seconds": round(time.monotonic() - t0, 1), "failures": fails}
json.dump(res, open(a.out, "w"), indent=1)
print(a.label, a.python, a.load, "pass", res["pass"], "fail", res["fail"], f'{res["seconds"]}s')
