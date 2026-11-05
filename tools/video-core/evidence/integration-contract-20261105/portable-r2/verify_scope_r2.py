#!/usr/bin/env python3
"""Prove that, relative to a base commit, ONLY paths under tools/video-core/evidence/integration-contract-20261105/portable-r2/ were added and NOTHING else was
modified or removed (so the closed portable/ harness, its pinned-original profile and results, portable-recovery/, portable-candidate-092b420/, portable-candidate-recovery/ and every library file are byte-identical).
Compares git blob ids (exact bytes). Python 3.11+ stdlib + the `git` executable; read-only.

  python verify_scope_r2.py --base 5b36a723cd4e3966c3dc70760599d9edcf6bf9e1 [--ref <commit>] [--repo <dir>] [--out FILE]
Without --ref the working tree (tracked + untracked, not ignored) is compared.
"""
import argparse
import json
import os
import subprocess
import sys

DEFAULT_BASE = "5b36a723cd4e3966c3dc70760599d9edcf6bf9e1"
ALLOWED_PREFIX = "tools/video-core/evidence/integration-contract-20261105/portable-r2/"
ALLOWED_FILES = set()


def git(repo, *a, inp=None):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, input=inp, check=True, timeout=120).stdout


def tree(repo, ref):
    out = {}
    for rec in git(repo, "ls-tree", "-r", "-z", ref).split(b"\0"):
        if rec:
            meta, path = rec.split(b"\t", 1)
            out[path.decode("utf-8", "surrogateescape")] = meta.split()[2].decode()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--ref")
    ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 5)))
    ap.add_argument("--out")
    ns = ap.parse_args()
    base = tree(ns.repo, ns.base)
    if ns.ref:
        now = tree(ns.repo, ns.ref)
    else:
        listed = set(base) | {p for p in git(ns.repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode("utf-8", "surrogateescape").split("\0") if p}
        existing = [p for p in sorted(listed) if os.path.isfile(os.path.join(ns.repo, p))]
        res = git(ns.repo, "hash-object", "--stdin-paths", inp=("\n".join(existing) + "\n").encode("utf-8", "surrogateescape")).decode().split()
        now = dict(zip(existing, res))
    modified = sorted(p for p in base if p in now and now[p] != base[p])
    removed = sorted(p for p in base if p not in now)
    added = sorted(p for p in now if p not in base)
    bad_added = [p for p in added if not (p.startswith(ALLOWED_PREFIX) or p in ALLOWED_FILES)]
    doc = {"base": ns.base, "compared": ns.ref or "working tree", "base_paths": len(base), "identical": len(base) - len(modified) - len(removed), "modified": modified, "removed": removed,
           "added_total": len(added), "added_in_scope": len(added) - len(bad_added), "added_out_of_scope": bad_added,
           "allowed": {"prefix": ALLOWED_PREFIX, "files": sorted(ALLOWED_FILES)},
           "verdict": "IN SCOPE: every base path is byte-identical; all additions are under portable-r2/" if not (modified or removed or bad_added) else "OUT OF SCOPE"}
    print(json.dumps(doc, indent=1))
    if ns.out:
        with open(ns.out, "x", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return 0 if doc["verdict"].startswith("IN SCOPE") else 1


if __name__ == "__main__":
    sys.exit(main())
