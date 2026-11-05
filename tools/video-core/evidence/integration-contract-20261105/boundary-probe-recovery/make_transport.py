#!/usr/bin/env python3
"""Recovery-only transport of an EXACT committed scope (producer side; needs `git`; Python 3.11+ stdlib).

Packs, from the committed git blobs of COMMIT (never from the working tree, never regenerating anything):
    tools/video-core/evidence/integration-contract-20261105/boundary-probe-r1/**
into a create-only deterministic ZIP, an ASCII base64 text of the whole ZIP split into ordered chunk files of at most 512 KiB, and a manifest with
per-file repo path, byte count, git blob id and SHA-256, the source commit and tree ids, totals, and the zip/base64/chunk hashes.
Member names are relative to tools/video-core/evidence/integration-contract-20261105/ (they all start with boundary-probe-r1/). The transport lives OUTSIDE the packed scope
(boundary-probe-recovery/), so nothing is packed recursively; portable-recovery/, portable-candidate-recovery/ and all older evidence are not packed.
Refusals (any one aborts, nothing is silently omitted): non-regular git modes (symlinks/submodules), unsafe or non-UTF-8 paths, casefold collisions, a count/byte total that differs from --expect-*.

  python make_transport.py --commit e0af65b... --out-dir <NEW dir> [--expect-count N --expect-bytes B]
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
SCOPE_PREFIX = EVID + "boundary-probe-r1/"
CHUNK_MAX = 512 * 1024
LINE = 76


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True, timeout=300).stdout


def safe_member(name: str) -> bool:
    return (bool(name) and not name.startswith("/") and "\\" not in name and ":" not in name and "\x00" not in name
            and all(p not in ("", ".", "..") for p in name.split("/")) and ".git" not in name.split("/"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--expect-count", type=int)
    ap.add_argument("--expect-bytes", type=int)
    ns = ap.parse_args()
    commit = git(ns.repo, "rev-parse", "--verify", ns.commit + "^{commit}").decode().strip()
    tree = git(ns.repo, "rev-parse", commit + "^{tree}").decode().strip()
    subtree = git(ns.repo, "rev-parse", commit + ":" + SCOPE_PREFIX.rstrip("/")).decode().strip()
    entries = []
    for rec in git(ns.repo, "ls-tree", "-r", "-z", "-l", commit).split(b"\0"):
        if not rec:
            continue
        meta, raw = rec.split(b"\t", 1)
        mode, typ, blob, size = meta.split()
        path = raw.decode("utf-8")                              # strict: a non-UTF-8 path aborts
        if path.startswith(SCOPE_PREFIX):
            if mode not in (b"100644", b"100755") or typ != b"blob":
                sys.exit(f"REFUSED: non-regular entry in scope: {path} mode {mode!r}")
            entries.append((path, blob.decode(), int(size)))
    entries.sort()
    members = []
    seen = {}
    for path, blob, size in entries:
        rel = path[len(EVID):]
        if not safe_member(rel):
            sys.exit(f"REFUSED: unsafe member path {rel!r}")
        k = rel.casefold()
        if k in seen:
            sys.exit(f"REFUSED: casefold collision {rel!r} / {seen[k]!r}")
        seen[k] = rel
        members.append((path, rel, blob, size))
    total = sum(m[3] for m in members)
    if (ns.expect_count is not None and len(members) != ns.expect_count) or (ns.expect_bytes is not None and total != ns.expect_bytes):
        sys.exit(f"REFUSED: scope has {len(members)} files / {total} bytes, expected {ns.expect_count} / {ns.expect_bytes}")
    out = os.path.abspath(ns.out_dir)
    os.mkdir(out)                                              # create-only: refuses an existing directory
    zip_name = f"boundary-probe-{commit[:7]}.zip"
    zip_path = os.path.join(out, zip_name)
    mm, zero = [], 0
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, rel, blob, size in members:
            data = git(ns.repo, "cat-file", "blob", blob)
            assert len(data) == size, (rel, len(data), size)
            assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == blob, rel
            zero += size == 0
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            zf.writestr(zi, data)
            mm.append({"repo_path": path, "path": rel, "bytes": size, "git_blob_id": blob, "sha256": hashlib.sha256(data).hexdigest()})
    with open(zip_path, "rb") as fh:
        zbytes = fh.read()
    b64 = base64.b64encode(zbytes).decode("ascii")
    lines = [b64[i:i + LINE] + "\n" for i in range(0, len(b64), LINE)]
    chunks, cur, curlen = [], [], 0
    for ln in lines:
        if curlen + len(ln) > CHUNK_MAX:
            chunks.append("".join(cur))
            cur, curlen = [], 0
        cur.append(ln)
        curlen += len(ln)
    if cur:
        chunks.append("".join(cur))
    full = "".join(chunks)
    cm = []
    for i, c in enumerate(chunks, 1):
        name = f"{zip_name}.b64.part{i:03d}"
        with open(os.path.join(out, name), "x", encoding="ascii", newline="\n") as fh:
            fh.write(c)
        cm.append({"index": i, "name": name, "bytes": len(c.encode("ascii")), "sha256": hashlib.sha256(c.encode("ascii")).hexdigest()})
    manifest = {
        "transport": "recovery-only; not a build artifact; contains no regenerated output", "source_commit": commit, "source_tree": tree, "scope_subtree": subtree,
        "scope": {"prefix": SCOPE_PREFIX}, "member_path_base": EVID, "member_count": len(mm), "member_bytes_total": total, "zero_byte_members": zero,
        "zip": {"name": zip_name, "bytes": len(zbytes), "sha256": hashlib.sha256(zbytes).hexdigest(), "compression": "deflate level 9", "timestamps": "1980-01-01 00:00:00 (fixed)", "member_order": "sorted by repo path"},
        "base64": {"alphabet": "RFC 4648 standard, 76-column LF-wrapped, ASCII only; the chunks concatenated in index order are the complete text", "total_bytes": len(full.encode("ascii")),
                   "total_sha256": hashlib.sha256(full.encode("ascii")).hexdigest(), "chunk_max_bytes": CHUNK_MAX, "chunk_count": len(cm), "chunks": cm},
        "exclusions": {"enforced": [".git entries", "absolute paths", "'..' components", "backslashes, drive colons or NUL in member names", "non-regular git modes (symlinks/submodules): abort", "casefold collisions: abort",
                                    "anything outside boundary-probe-r1/**", "this boundary-probe-recovery/ directory and every older transport (outside the packed scope by construction)",
                                    "any working-tree or newly generated file (blobs are read from the commit)"], "actually_excluded_in_scope": [], "actually_omitted_in_scope": 0},
        "members": mm}
    with open(os.path.join(out, "MANIFEST.json"), "x", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    with open(os.path.join(out, "MANIFEST.sha256.txt"), "x", encoding="utf-8", newline="\n") as fh:
        for m in mm:
            fh.write(f"{m['sha256']}  {m['path']}\n")
    print(json.dumps({k: manifest[k] for k in ("source_commit", "source_tree", "scope_subtree", "member_count", "member_bytes_total", "zero_byte_members", "zip")}, indent=1))
    print("base64 total", manifest["base64"]["total_bytes"], manifest["base64"]["total_sha256"], "chunks", [(c["name"][-7:], c["bytes"]) for c in cm])
    return 0


if __name__ == "__main__":
    sys.exit(main())
