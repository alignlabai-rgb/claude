#!/usr/bin/env python3
"""Verify and (optionally) extract the recovery transport. Python 3.11+ stdlib only; no git needed (optional extra check with --git-commit).

  python verify_transport.py --manifest MANIFEST.json (--zip portable-xxxxxxx.zip | --b64 portable-xxxxxxx.zip.b64) [--extract-to NEW_DIR] [--git-commit SHA --repo DIR]

Checks: base64 decodes to a zip with the manifest's sha256; every member path is relative, '/'-separated, without '..', drive colon, backslash or '.git';
the member set equals the manifest exactly (no extra, no missing, no duplicate); every member's size and sha256 match; with --git-commit every member also equals the committed
git blob (blob id recomputed from the bytes). Extraction is create-only into a NEW directory (refused if it exists); members are written with exclusive-create.
Exit 0 only if everything matches.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile


def safe(name):
    return bool(name) and not name.startswith("/") and "\\" not in name and ":" not in name and all(p not in ("", ".", "..") for p in name.split("/")) and ".git" not in name.split("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--zip")
    ap.add_argument("--b64")
    ap.add_argument("--extract-to")
    ap.add_argument("--git-commit")
    ap.add_argument("--repo", default=os.getcwd())
    ns = ap.parse_args()
    man = json.load(open(ns.manifest, encoding="utf-8"))
    problems = []
    if bool(ns.zip) == bool(ns.b64):
        print("give exactly one of --zip / --b64")
        return 2
    if ns.b64:
        text = open(ns.b64, "rb").read()
        if any(b > 127 for b in text):
            problems.append("base64 file is not ASCII")
        if hashlib.sha256(text).hexdigest() != man["base64"]["sha256"]:
            problems.append("base64 file sha256 differs from the manifest")
        try:
            zbytes = base64.b64decode(b"".join(text.split()), validate=True)
        except ValueError as exc:
            print("PROBLEMS:\n   base64 does not decode:", exc)
            return 1
    else:
        zbytes = open(ns.zip, "rb").read()
    if hashlib.sha256(zbytes).hexdigest() != man["zip"]["sha256"] or len(zbytes) != man["zip"]["bytes"]:
        problems.append("zip sha256/size differs from the manifest")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  ", p)
        return 1                      # the container itself disagrees with the manifest: do not even open it
    try:
        zf = zipfile.ZipFile(io.BytesIO(zbytes))
    except zipfile.BadZipFile as exc:
        print("PROBLEMS:\n   not a zip:", exc)
        return 1
    names = [i.filename for i in zf.infolist()]
    if len(names) != len(set(names)):
        problems.append("duplicate member names")
    for n in names:
        if not safe(n):
            problems.append(f"unsafe member path: {n!r}")
    want = {m["path"]: m for m in man["members"]}
    if set(names) != set(want):
        problems.append(f"member set differs: extra={sorted(set(names) - set(want))[:5]} missing={sorted(set(want) - set(names))[:5]}")
    blobs = {}
    if ns.git_commit:
        for rec in subprocess.run(["git", "-C", ns.repo, "ls-tree", "-r", "-z", ns.git_commit], capture_output=True, check=True).stdout.split(b"\0"):
            if rec:
                meta, path = rec.split(b"\t", 1)
                blobs[path.decode("utf-8")] = meta.split()[2].decode()
    ok = 0
    datas = {}
    for n in names:
        if n not in want:
            continue
        try:
            d = zf.read(n)
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError) as exc:
            problems.append(f"cannot read member {n}: {exc}")
            continue
        m = want[n]
        if len(d) != m["bytes"] or hashlib.sha256(d).hexdigest() != m["sha256"]:
            problems.append(f"size/sha256 mismatch: {n}")
            continue
        if hashlib.sha1(b"blob %d\0" % len(d) + d).hexdigest() != m["git_blob_id"]:
            problems.append(f"git blob id mismatch: {n}")
            continue
        if ns.git_commit and blobs.get(man["member_path_base"] + n) != m["git_blob_id"]:
            problems.append(f"not the committed blob at {ns.git_commit}: {n}")
            continue
        datas[n] = d
        ok += 1
    print(f"members verified: {ok}/{len(want)}  total bytes: {sum(len(v) for v in datas.values())}  git-checked: {bool(ns.git_commit)}")
    if problems:
        print("PROBLEMS:")
        for p in problems[:30]:
            print("  ", p)
        return 1
    if ns.extract_to:
        root = os.path.abspath(ns.extract_to)
        try:
            os.makedirs(os.path.dirname(root) or ".", exist_ok=True)      # missing PARENT directories are created; the target itself must be new
            os.mkdir(root)                               # create-only
        except FileExistsError:
            print(f"REFUSED: {root} already exists (extraction is create-only; choose a new directory)")
            return 2
        for n, d in datas.items():
            full = os.path.join(root, *n.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "xb") as fh:
                fh.write(d)
        print("extracted", len(datas), "files to", root)
    print("VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
