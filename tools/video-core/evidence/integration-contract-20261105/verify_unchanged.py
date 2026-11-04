#!/usr/bin/env python3
"""Prove that nothing outside tools/video-core/evidence/integration-contract-20261105/ differs from the base commit.

Compares git blob ids (exact bytes) of EVERY path in the base commit's tree against either the working tree (default) or a ref (--ref HEAD),
and lists any added / removed / modified path outside the new directory. Groups: source modules, tests, schemas, examples, original specs
(root *.md), old evidence. Exit 0 only if the set of differences is a subset of 'added under the new directory'.

  python3 verify_unchanged.py [--base SHA] [--ref HEAD] [--repo DIR] [--out FILE]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

BASE = "b4ca4a3b031351a631c0fbe1eb149935f4db19ba"
NEW = "tools/video-core/evidence/integration-contract-20261105/"


def git(repo, *a, inp=None):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, input=inp, check=True, timeout=60).stdout


def tree(repo, ref):
    out = {}
    for rec in git(repo, "ls-tree", "-r", "-z", ref).split(b"\0"):
        if rec:
            meta, path = rec.split(b"\t", 1)
            out[path.decode("utf-8", "surrogateescape")] = meta.split()[2].decode()
    return out


def working(repo, paths):
    out = {}
    existing = [p for p in paths if os.path.isfile(os.path.join(repo, p))]
    res = git(repo, "hash-object", "--stdin-paths", inp=("\n".join(existing) + "\n").encode()).decode().split()
    out.update(zip(existing, res))
    return out


def group(p):
    if p.startswith("tools/video-core/video_core/"):
        return "source_modules"
    if p.startswith("tools/video-core/tests/"):
        return "tests"
    if p.startswith("tools/video-core/schemas/"):
        return "schemas"
    if p.startswith("tools/video-core/examples/"):
        return "examples"
    if p.startswith("tools/video-core/evidence/"):
        return "old_evidence"
    if p.startswith("tools/video-core/") and p.count("/") == 2 and p.endswith(".md"):
        return "original_specs_and_docs"
    if p.startswith("tools/video-core/"):
        return "other_video_core"
    return "rest_of_repo"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--ref")
    ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")))
    ap.add_argument("--out")
    ns = ap.parse_args()
    base = tree(ns.repo, ns.base)
    if ns.ref:
        now = tree(ns.repo, ns.ref)
        mode = f"ref {ns.ref}"
    else:
        listed = set(base)
        extra = git(ns.repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode("utf-8", "surrogateescape").split("\0")
        listed.update(p for p in extra if p)
        now = working(ns.repo, sorted(listed))
        mode = "working tree"
    modified = sorted(p for p in base if p in now and now[p] != base[p])
    removed = sorted(p for p in base if p not in now)
    added = sorted(p for p in now if p not in base)
    bad_added = [p for p in added if not p.startswith(NEW)]
    groups = {}
    for p in base:
        gr = groups.setdefault(group(p), {"files": 0, "identical": 0})
        gr["files"] += 1
        gr["identical"] += 1 if (p in now and now[p] == base[p]) else 0
    manifest_hash = hashlib.sha256("".join(f"{p}\t{base[p]}\n" for p in sorted(base)).encode("utf-8", "surrogateescape")).hexdigest()
    doc = {"compared": mode, "base": ns.base, "ref": ns.ref or "(working tree)", "head": git(ns.repo, "rev-parse", "HEAD").decode().strip(),
           "base_paths": len(base), "identical_to_base": len(base) - len(modified) - len(removed), "modified_vs_base": modified, "removed_vs_base": removed,
           "added_outside_new_directory": bad_added, "added_inside_new_directory": len(added) - len(bad_added), "new_directory": NEW,
           "groups_identical": groups, "base_tree_manifest_sha256": manifest_hash,
           "verdict": "UNCHANGED: every base path is byte-identical and all additions are under the new directory" if not (modified or removed or bad_added) else "CHANGED"}
    print(json.dumps({k: v for k, v in doc.items() if k != "groups_identical"}, indent=1))
    print(json.dumps(groups, sort_keys=True))
    if ns.out:
        with open(ns.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return 0 if doc["verdict"].startswith("UNCHANGED") else 1


if __name__ == "__main__":
    sys.exit(main())
