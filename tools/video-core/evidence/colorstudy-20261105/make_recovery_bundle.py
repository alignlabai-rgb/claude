#!/usr/bin/env python3
"""Lossless transport workaround (NOT a measurement, no ffmpeg, no regeneration): bundles the EXISTING original bytes of this study into one zip,
then writes the same archive bytes as ASCII base64 for connectors that only carry UTF-8 text.
Outputs: recovery/originals.zip.b64, recovery/MANIFEST.json (archive bytes/sha256, per-member bytes/sha256 + sha256 of the source file on disk).
Members are copied byte for byte; relative POSIX paths only; fixed timestamps and sorted order so the zip itself is reproducible for this zlib.
Decode:  base64 -d originals.zip.b64 > originals.zip ; sha256sum originals.zip (compare MANIFEST.json) ; unzip originals.zip
"""
import base64
import hashlib
import io
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "recovery")
INCLUDE_DIRS = ["fixtures", "raw", "records", "contact_sheets"]
INCLUDE_FILES = ["preregistration.json", "preregistration.amendment-1.json", "docs/probes.json"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def collect():
    paths = []
    for d in INCLUDE_DIRS:
        for dp, _dn, fn in os.walk(os.path.join(HERE, d)):
            for f in fn:
                paths.append(os.path.relpath(os.path.join(dp, f), HERE).replace(os.sep, "/"))
    paths += INCLUDE_FILES
    paths = sorted(set(paths))
    for p in paths:
        assert not p.startswith("/") and ".." not in p.split("/") and "\\" not in p and ":" not in p, p
    return paths


def main():
    os.makedirs(OUT, exist_ok=True)
    members, buf = [], io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in collect():
            with open(os.path.join(HERE, p), "rb") as fh:
                data = fh.read()
            zi = zipfile.ZipInfo(p, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type, zi.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            z.writestr(zi, data, compresslevel=9)
            members.append({"path": p, "bytes": len(data), "sha256": sha(data)})
    archive = buf.getvalue()
    b64 = base64.b64encode(archive)
    assert base64.b64decode(b64) == archive
    with open(os.path.join(OUT, "originals.zip.b64"), "wb") as fh:
        fh.write(b64 + b"\n")  # 'base64 -d' ignores the trailing newline
    manifest = {"schema": "colorstudy.recovery-manifest/1",
                "purpose": "lossless transport of existing original outputs through a UTF-8-only connector; no new media, no measurement, no experiment",
                "archive": {"name": "originals.zip", "bytes": len(archive), "sha256": sha(archive), "format": "zip, deflate, fixed 1980-01-01 timestamps, sorted members"},
                "base64_file": {"name": "originals.zip.b64", "bytes": len(b64) + 1, "sha256": sha(b64 + b"\n"), "encoding": "RFC 4648 base64, ASCII, single trailing newline"},
                "decode": ["base64 -d originals.zip.b64 > originals.zip", "sha256sum originals.zip   # must equal archive.sha256", "unzip originals.zip"],
                "member_count": len(members), "members": members}
    with open(os.path.join(OUT, "MANIFEST.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")
    print(len(members), "members; archive", len(archive), "bytes", sha(archive))


if __name__ == "__main__":
    main()
