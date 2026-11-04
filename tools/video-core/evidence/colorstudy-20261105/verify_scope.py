#!/usr/bin/env python3
"""Proves (read-only, no ffmpeg) that this study touched nothing outside evidence/colorstudy-20261105/ and that its records are intact.
Usage: verify_scope.py [--ref HEAD]   -> prints JSON and writes scope-verification.json
Checks: changed paths vs the base commit; the base tree of everything else is identical at the ref; evidence/plan.canonical.json hash;
the study's baseline filter text equals the production plan's text (colour branch and file branch) via a temporary base export;
every record's output hashes match the files; exactly the numbered experiments exist, each started once; <=12; dimensions <=256."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
BASE = "b4ca4a3b031351a631c0fbe1eb149935f4db19ba"
PREFIX = "tools/video-core/evidence/colorstudy-20261105/"
PLAN_SHA = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"


def git(*a, **kw):
    return subprocess.run(["git", "-C", HERE, *a], capture_output=True, text=True, check=True, **kw).stdout


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    ref = sys.argv[sys.argv.index("--ref") + 1] if "--ref" in sys.argv else "HEAD"
    out = {"base": BASE, "ref": git("rev-parse", ref).strip(), "checks": {}}
    ok = lambda name, cond, detail=None: out["checks"].__setitem__(name, {"ok": bool(cond), **({"detail": detail} if detail is not None else {})})
    ok("ref_descends_from_base", subprocess.run(["git", "-C", HERE, "merge-base", "--is-ancestor", BASE, ref]).returncode == 0)
    names = git("diff", "--name-status", BASE, ref).splitlines()
    outside = [n for n in names if not n.split("\t", 1)[1].startswith(PREFIX)]
    statuses = {n.split("\t")[0] for n in names}
    ok("all_changed_paths_inside_colorstudy_dir", not outside, outside)
    ok("only_additions", statuses <= {"A"}, sorted(statuses))
    out["changed_path_count"] = len(names)
    root = git("rev-parse", "--show-toplevel").strip()
    base_tree = git("ls-tree", "-r", BASE, "--", "tools/video-core")
    ref_tree = "".join(l + "\n" for l in git("ls-tree", "-r", ref, "--", "tools/video-core").splitlines() if "\t" + PREFIX not in l)
    ok("everything_else_under_tools_video_core_identical_to_base", base_tree == ref_tree)
    whole_base = git("ls-tree", "-r", BASE)
    whole_ref = "".join(l + "\n" for l in git("ls-tree", "-r", ref).splitlines() if "\t" + PREFIX not in l)
    ok("whole_repo_outside_colorstudy_dir_identical_to_base", whole_base == whole_ref)
    blob = subprocess.run(["git", "-C", HERE, "show", f"{ref}:tools/video-core/evidence/plan.canonical.json"], capture_output=True, check=True).stdout
    ok("baseline_plan_canonical_sha256", hashlib.sha256(blob).hexdigest() == PLAN_SHA, hashlib.sha256(blob).hexdigest())
    ok("no_pycache_tracked", not [l for l in git("ls-tree", "-r", "--name-only", ref).splitlines() if "__pycache__" in l or l.endswith(".pyc")])
    # filter text equality against production code from a temporary base export
    import run_study as R
    with tempfile.TemporaryDirectory(prefix="vc-scope-") as td:
        arc = subprocess.run(["git", "-C", root, "archive", BASE, "tools/video-core"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", td], input=arc, check=True)
        vc = os.path.join(td, "tools", "video-core")
        code = ("import sys, json, os; sys.dont_write_bytecode=True; sys.path.insert(0, '.')\n"
                "from video_core import prepare\n"
                "d=open('examples/episode.synthetic.json','rb').read()\n"
                "g=prepare(d).canonical['ffmpeg']['argv']; print(json.dumps({'colour': g[g.index('-filter_complex')+1]}))\n"
                "import tempfile\n"
                "from tests.helpers import episode_doc, to_bytes\n"
                "a=tempfile.mkdtemp(); open(os.path.join(a,'s.png'),'wb').write(b'x')\n"
                "doc=episode_doc(total=1,width=256,height=128,fps=(30,1),captions=[],clips=[{'id':'f','start_frame':0,'duration_frames':1,'source':{'kind':'file','path':'s.png'}}])\n"
                "g=prepare(to_bytes(doc),asset_root=a).canonical['ffmpeg']['argv']; print(json.dumps({'file': g[g.index('-filter_complex')+1]}))")
        res = subprocess.run([sys.executable, "-c", code], cwd=vc, capture_output=True, text=True, env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"})
        lines = [json.loads(l) for l in res.stdout.splitlines() if l.startswith("{")]
        colour_graph = lines[0]["colour"]
        file_graph = lines[1]["file"] if len(lines) > 1 else ""
    mine_c = R.color_clip_chain("30506a", 640, 360, "baseline", n_frames=90) + "[v0]"
    ok("study_baseline_colour_chain_is_byte_identical_to_production_plan", mine_c in colour_graph)
    mine_f = R.file_clip_chain("baseline", 256, 128, n_frames=1) + "[v0]"
    ok("study_baseline_file_chain_is_byte_identical_to_production_plan", mine_f in file_graph, {"production": file_graph, "study": mine_f} if mine_f not in file_graph else None)
    # records
    rec_dir, issues, ids = os.path.join(HERE, "records"), [], set()
    for f in sorted(os.listdir(rec_dir)):
        if f.endswith(".started.json"):
            ids.add(f[:3])
    ok("at_most_12_experiments", len(ids) <= 12, sorted(ids))
    done = {f[:3] for f in os.listdir(rec_dir) if re.match(r"E\d\d\.json$", f)}
    ok("every_started_experiment_has_a_record", ids == done, {"started_only": sorted(ids - done)})
    for eid in sorted(done):
        r = json.load(open(os.path.join(rec_dir, f"{eid}.json")))
        g = r["geometry"]
        if g["w"] > 256 or g["h"] > 256:
            issues.append((eid, "dimension"))
        if r["main"]["wall_seconds"] > 10 or r["main"]["timed_out"]:
            issues.append((eid, "time"))
        for rb in r["readbacks"]:
            if rb["wall_seconds"] > 10 or rb["timed_out"]:
                issues.append((eid, "readback time"))
        argv = r["main"]["argv"]
        if "-threads" in argv and any(int(argv[i + 1]) > 2 for i, a in enumerate(argv) if a == "-threads"):
            issues.append((eid, "threads"))
        for k, o in r["outputs"].items():
            if o is None or sha(os.path.join(HERE, o["path"])) != o["sha256"]:
                issues.append((eid, f"output {k} hash"))
        for rb in r["readbacks"]:
            o = rb.get("output")
            if o and sha(os.path.join(HERE, o["path"])) != o["sha256"]:
                issues.append((eid, "readback hash"))
        if r["main"]["stderr_sha256"] != sha(os.path.join(HERE, r["main"]["stderr_file"])):
            issues.append((eid, "stderr hash"))
    ok("records_match_files_limits_respected", not issues, issues)
    out["all_ok"] = all(c["ok"] for c in out["checks"].values())
    with open(os.path.join(HERE, "scope-verification.json"), "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    print(json.dumps({k: v["ok"] for k, v in out["checks"].items()}, indent=1), "\nALL OK:", out["all_ok"])
    return 0 if out["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
