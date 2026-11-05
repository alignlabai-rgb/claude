#!/usr/bin/env python3
"""Create-only ASCII (base64) transport of the ORIGINAL final media of the recorded run, so the exact bytes can be recovered; nothing is regenerated or re-encoded.
    python3 make_transport.py --work /tmp/vc-same-job --out transport      (--out must not exist)
mp4 is base64 of the raw bytes; the larger PCM files are gzip -9 -n (deterministic header) then base64. The manifest records original bytes + SHA-256 and encoded SHA-256.
Decode:  base64 -d X.b64.txt | gunzip > X   (mp4: base64 -d only)   then  sha256sum X"""
import argparse, base64, gzip, hashlib, json, os, sys

ap = argparse.ArgumentParser()
ap.add_argument("--work", required=True)
ap.add_argument("--out", required=True)
ns = ap.parse_args()
FILES = {"output.mp4": ("out_video/video-job/output.mp4", False), "stem.wav": ("out_audio/stem-job/stem.wav", True), "output.mov": ("out_mux/mux-job/output.mov", True)}
os.makedirs(ns.out)
man = []
for name, (rel, gz) in FILES.items():
    raw = open(os.path.join(ns.work, rel), "rb").read()
    enc = gzip.compress(raw, 9, mtime=0) if gz else raw
    text = base64.encodebytes(enc)  # 76-column ASCII lines
    fname = name + (".gz" if gz else "") + ".b64.txt"
    with open(os.path.join(ns.out, fname), "xb") as fh:
        fh.write(text)
    back = base64.b64decode(text)
    back = gzip.decompress(back) if gz else back
    assert hashlib.sha256(back).hexdigest() == hashlib.sha256(raw).hexdigest()
    man.append({"name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "encoding": "gzip-9-n+base64" if gz else "base64", "file": fname,
                "encoded_file_bytes": len(text), "encoded_file_sha256": hashlib.sha256(text).hexdigest()})
with open(os.path.join(ns.out, "manifest.json"), "x") as fh:
    json.dump({"schema": "same_job_transport/1", "note": "originals of the recorded run; synthetic test media only", "files": man}, fh, indent=1, sort_keys=True)
    fh.write("\n")
print(json.dumps([(m["name"], m["bytes"], m["encoded_file_bytes"]) for m in man]))
