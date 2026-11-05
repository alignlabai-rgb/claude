#!/usr/bin/env python3
"""Recovery-only transport of an EXACT committed scope (producer side; needs `git`; Python 3.11+ stdlib).

Packs, from the committed git blobs of COMMIT (never from the working tree, never regenerating anything):
    <evidence dir>/portable/**   and   <evidence dir>/PORTABLE_HANDOFF.md
into a create-only ZIP + an ASCII base64 copy + a per-member manifest (path, size, sha256, git blob id).
Member paths are relative to <evidence dir> (= tools/video-core/evidence/integration-contract-20261105/). The transport lives OUTSIDE the packed scope
(portable-recovery/), so nothing is packed recursively. Exclusions are enforced, listed and counted; nothing is omitted silently.

  python make_transport.py --commit 8cab6e44b7626924c9ad3a46814c7e57b575b5bc --out-dir <NEW dir>
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile

EVID = "tools/video-core/evidence/integration-contract-20261105/"
SCOPE_PREFIX = EVID + "portable/"
SCOPE_FILES = {EVID + "PORTABLE_HANDOFF.md"}


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True, timeout=300).stdout


def safe_member(name: str) -> bool:
    return bool(name) and not name.startswith("/") and "\\" not in name and ":" not in name and all(p not in ("", ".", "..") for p in name.split("/")) and ".git" not in name.split("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repo", default=os.getcwd())
    ns = ap.parse_args()
    commit = git(ns.repo, "rev-parse", "--verify", ns.commit + "^{commit}").decode().strip()
    entries = []
    for rec in git(ns.repo, "ls-tree", "-r", "-z", "-l", commit).split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, typ, blob, size = meta.split()
        path = path.decode("utf-8")
        if path.startswith(SCOPE_PREFIX) or path in SCOPE_FILES:
            entries.append((path, blob.decode(), int(size), mode.decode()))
    entries.sort()
    excluded = []
    members = []
    for path, blob, size, mode in entries:
        rel = path[len(EVID):]
        if not safe_member(rel):
            excluded.append({"path": path, "reason": "unsafe member path"})
        elif mode not in (b"100644".decode(), b"100755".decode()):
            excluded.append({"path": path, "reason": f"git mode {mode} is not a regular file"})
        else:
            members.append((rel, blob, size))
    out = os.path.abspath(ns.out_dir)
    os.mkdir(out)                        # create-only: refuses an existing directory
    zip_path = os.path.join(out, f"portable-{commit[:7]}.zip")
    manifest_members = []
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel, blob, size in members:
            data = git(ns.repo, "cat-file", "blob", blob)
            assert len(data) == size, (rel, len(data), size)
            assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == blob, rel          # bytes are exactly the committed blob
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            zf.writestr(zi, data)
            manifest_members.append({"path": rel, "bytes": size, "sha256": hashlib.sha256(data).hexdigest(), "git_blob_id": blob})
    with open(zip_path, "rb") as fh:
        zbytes = fh.read()
    b64 = base64.b64encode(zbytes).decode("ascii")
    b64_text = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76)) + "\n"
    b64_path = zip_path + ".b64"
    with open(b64_path, "x", encoding="ascii", newline="\n") as fh:
        fh.write(b64_text)
    total = sum(m["bytes"] for m in manifest_members)
    manifest = {
        "transport": "recovery-only; not a build artifact; contains no regenerated output", "source_commit": commit, "scope": {"prefix": SCOPE_PREFIX, "files": sorted(SCOPE_FILES)},
        "member_path_base": EVID, "member_count": len(manifest_members), "member_bytes_total": total,
        "zip": {"name": os.path.basename(zip_path), "bytes": len(zbytes), "sha256": hashlib.sha256(zbytes).hexdigest(), "compression": "deflate level 9", "timestamps": "1980-01-01 00:00:00 (fixed)"},
        "base64": {"name": os.path.basename(b64_path), "bytes": len(b64_text.encode("ascii")), "sha256": hashlib.sha256(b64_text.encode("ascii")).hexdigest(), "alphabet": "RFC 4648 standard, 76-column LF-wrapped, ASCII only"},
        "exclusions": {"enforced": [".git entries", "absolute paths", "'..' components", "backslashes or drive colons in member names", "non-regular git modes (symlinks/submodules)", "anything outside portable/** and PORTABLE_HANDOFF.md",
                                    "this portable-recovery/ directory (outside the packed scope by construction)", "any working-tree or newly generated file (blobs are read from the commit)"],
                       "actually_excluded_in_scope": excluded, "actually_omitted_in_scope": 0 if not excluded else len(excluded)},
        "members": manifest_members}
    with open(os.path.join(out, "MANIFEST.json"), "x", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    with open(os.path.join(out, "MANIFEST.sha256.txt"), "x", encoding="utf-8", newline="\n") as fh:
        for m in manifest_members:
            fh.write(f"{m['sha256']}  {m['path']}\n")
    print(json.dumps({k: manifest[k] for k in ("source_commit", "member_count", "member_bytes_total", "zip", "base64")}, indent=1))
    print("excluded in scope:", excluded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
