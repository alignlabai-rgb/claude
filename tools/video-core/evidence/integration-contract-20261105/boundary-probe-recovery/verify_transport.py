#!/usr/bin/env python3
"""Verify and (optionally) extract the boundary-probe recovery transport. Python 3.11+ stdlib only; no git needed (optional extra check with --git-commit).

  python verify_transport.py --manifest MANIFEST.json (--zip FILE.zip | --parts-dir DIR_WITH_b64_PARTS) [--extract-to NEW_DIR] [--git-commit SHA --repo DIR]

Checks, in order: (parts) every chunk exists, is ASCII, <= chunk_max, and matches its manifest bytes+sha256, no extra part files, the concatenation matches the total sha256 and decodes (strict) to the zip;
the zip matches the manifest bytes+sha256; no encrypted/link/directory member; member names are safe relative '/' paths, unique, with no casefold collision; the member set equals the manifest exactly;
every member's size, sha256 and recomputed git blob id match (zero-byte members included); totals match; with --git-commit every member also equals the committed blob and the commit/tree ids match.
Extraction is create-only into a NEW directory (exclusive-create files). Exit 0 and VERIFIED only if everything matches."""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile


def safe(name):
    return (bool(name) and not name.startswith("/") and "\\" not in name and ":" not in name and "\x00" not in name
            and all(p not in ("", ".", "..") for p in name.split("/")) and ".git" not in name.split("/"))


def fail(problems):
    print("PROBLEMS:")
    for p in problems[:40]:
        print("  ", p)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--zip")
    ap.add_argument("--parts-dir")
    ap.add_argument("--extract-to")
    ap.add_argument("--git-commit")
    ap.add_argument("--repo", default=os.getcwd())
    ns = ap.parse_args(argv)
    with open(ns.manifest, encoding="utf-8") as fh:
        man = json.load(fh)
    problems = []
    if bool(ns.zip) == bool(ns.parts_dir):
        print("give exactly one of --zip / --parts-dir")
        return 2
    if ns.parts_dir:
        b = man["base64"]
        names = sorted(n for n in os.listdir(ns.parts_dir) if re.fullmatch(r".+\.b64\.part\d{3}", n))
        want_names = [c["name"] for c in b["chunks"]]
        if names != want_names:
            return fail([f"part files differ from the manifest: found {names}, expected {want_names}"])
        full = b""
        for c in b["chunks"]:
            with open(os.path.join(ns.parts_dir, c["name"]), "rb") as fh:
                t = fh.read()
            if any(x > 127 for x in t):
                problems.append(f"{c['name']}: not ASCII")
            if len(t) > b["chunk_max_bytes"]:
                problems.append(f"{c['name']}: larger than {b['chunk_max_bytes']} bytes")
            if len(t) != c["bytes"] or hashlib.sha256(t).hexdigest() != c["sha256"]:
                problems.append(f"{c['name']}: bytes/sha256 differ from the manifest")
            full += t
        if len(full) != b["total_bytes"] or hashlib.sha256(full).hexdigest() != b["total_sha256"]:
            problems.append("concatenated base64 differs from the manifest total")
        if problems:
            return fail(problems)
        try:
            zbytes = base64.b64decode(b"".join(full.split()), validate=True)
        except ValueError as exc:
            return fail([f"base64 does not decode: {exc}"])
    else:
        with open(ns.zip, "rb") as fh:
            zbytes = fh.read()
    if hashlib.sha256(zbytes).hexdigest() != man["zip"]["sha256"] or len(zbytes) != man["zip"]["bytes"]:
        return fail(["zip sha256/size differs from the manifest"])
    try:
        zf = zipfile.ZipFile(io.BytesIO(zbytes))
    except zipfile.BadZipFile as exc:
        return fail([f"not a zip: {exc}"])
    infos = zf.infolist()
    names = [i.filename for i in infos]
    if len(names) != len(set(names)):
        problems.append("duplicate member names")
    seen = {}
    for i in infos:
        n = i.filename
        if not safe(n):
            problems.append(f"unsafe member path: {n!r}")
        if n.endswith("/"):
            problems.append(f"directory member: {n!r}")
        if i.flag_bits & 0x1:
            problems.append(f"encrypted member: {n!r}")
        if (i.external_attr >> 16) & 0o170000 not in (0, 0o100000):
            problems.append(f"non-regular (link/special) member: {n!r}")
        k = n.casefold()
        if k in seen and seen[k] != n:
            problems.append(f"casefold collision: {n!r} / {seen[k]!r}")
        seen[k] = n
    want = {m["path"]: m for m in man["members"]}
    if len(want) != len(man["members"]):
        problems.append("manifest lists a path twice")
    if set(names) != set(want):
        problems.append(f"member set differs: extra={sorted(set(names) - set(want))[:5]} missing={sorted(set(want) - set(names))[:5]}")
    blobs = {}
    if ns.git_commit:
        cm = subprocess.run(["git", "-C", ns.repo, "rev-parse", "--verify", ns.git_commit + "^{commit}"], capture_output=True, check=True).stdout.decode().strip()
        tr = subprocess.run(["git", "-C", ns.repo, "rev-parse", cm + "^{tree}"], capture_output=True, check=True).stdout.decode().strip()
        if cm != man["source_commit"] or tr != man["source_tree"]:
            problems.append("commit/tree ids differ from the manifest")
        for rec in subprocess.run(["git", "-C", ns.repo, "ls-tree", "-r", "-z", cm], capture_output=True, check=True).stdout.split(b"\0"):
            if rec:
                meta, path = rec.split(b"\t", 1)
                blobs[path.decode("utf-8")] = meta.split()[2].decode()
    ok, datas = 0, {}
    for n in names:
        if n not in want:
            continue
        try:
            d = zf.read(n)
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError) as exc:
            problems.append(f"cannot read member {n}: {exc}")
            continue
        m = want[n]
        if len(d) != m["bytes"] or hashlib.sha256(d).hexdigest() != m["sha256"]:
            problems.append(f"size/sha256 mismatch: {n}")
            continue
        if hashlib.sha1(b"blob %d\0" % len(d) + d).hexdigest() != m["git_blob_id"]:
            problems.append(f"git blob id mismatch: {n}")
            continue
        if m["repo_path"] != man["member_path_base"] + n:
            problems.append(f"repo_path/member mismatch: {n}")
            continue
        if ns.git_commit and blobs.get(m["repo_path"]) != m["git_blob_id"]:
            problems.append(f"not the committed blob at {ns.git_commit}: {n}")
            continue
        datas[n] = d
        ok += 1
    total = sum(len(v) for v in datas.values())
    zero = sum(1 for v in datas.values() if not v)
    if ok == len(want) and (len(want) != man["member_count"] or total != man["member_bytes_total"] or zero != man["zero_byte_members"]):
        problems.append("count/bytes/zero-byte totals differ from the manifest")
    print(f"members verified: {ok}/{len(want)}  total bytes: {total}  zero-byte members: {zero}  git-checked: {bool(ns.git_commit)}")
    if problems:
        return fail(problems)
    if ns.extract_to:
        root = os.path.abspath(ns.extract_to)
        os.makedirs(os.path.dirname(root) or ".", exist_ok=True)
        try:
            os.mkdir(root)
        except FileExistsError:
            print(f"REFUSED: {root} already exists (extraction is create-only)")
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
