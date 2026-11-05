#!/usr/bin/env python3
"""Export the three pinned source trees from a clone into a NEW directory (Python 3.11+ stdlib + the `git` executable; works on Windows and Linux; read-only on the repository).

  python export_sources.py --repo <clone containing the three commits> --dest <NEW dir>
creates <dest>/b4ca4a3/tools/video-core, <dest>/092b420/tools/video-core, <dest>/91ec2d4/tools/video-core from `git archive <full sha> tools/video-core` (committed bytes only, no working-tree files),
checks that each full commit id resolves exactly, refuses unsafe archive member names, and never overwrites.  Then pass each <dest>/<label>/tools/video-core as --source <label>=... to boundary_probe.py."""
import argparse
import os
import subprocess
import sys
import tempfile
import zipfile

SOURCES = {"b4ca4a3": "b4ca4a3b031351a631c0fbe1eb149935f4db19ba", "092b420": "092b4200b11346753686579e37af2ff2bc9bbca8", "91ec2d4": "91ec2d40f360e8019f357132a43c370ab66bb673"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dest", required=True)
    ns = ap.parse_args(argv)
    if os.path.lexists(ns.dest):
        print("refused: destination exists")
        return 2
    for label, sha in SOURCES.items():
        r = subprocess.run(["git", "-C", ns.repo, "rev-parse", "--verify", "--quiet", sha + "^{commit}"], capture_output=True, text=True)
        if r.returncode != 0 or r.stdout.strip() != sha:
            print(f"refused: commit {sha} ({label}) is not available in {ns.repo}; fetch the branch first")
            return 2
    os.mkdir(ns.dest)
    for label, sha in SOURCES.items():
        target = os.path.join(ns.dest, label)
        os.mkdir(target)
        with tempfile.TemporaryDirectory() as t:
            z = os.path.join(t, label + ".zip")
            subprocess.run(["git", "-C", ns.repo, "archive", "--format=zip", "-o", z, sha, "tools/video-core"], check=True, timeout=120)
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    parts = name.split("/")
                    if name.startswith("/") or ".." in parts or ":" in parts[0]:
                        print("refused: unsafe archive member", name)
                        return 2
                zf.extractall(target)
        print(label, sha[:7], "->", os.path.join(target, "tools", "video-core"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
