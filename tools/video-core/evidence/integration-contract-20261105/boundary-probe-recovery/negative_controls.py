#!/usr/bin/env python3
"""Negative controls for verify_transport.py (Python 3.11+ stdlib; read-only on the transport; writes only into a NEW scratch directory).
Every tampered variant below must be REJECTED by verify_transport.py; the untouched transport must VERIFY.
  python negative_controls.py --transport-dir <dir with MANIFEST.json, *.zip, *.b64.partNNN> --scratch NEWDIR"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*a):
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, "verify_transport.py"), *a], capture_output=True, text=True).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport-dir", required=True)
    ap.add_argument("--scratch", required=True)
    ns = ap.parse_args()
    T, N = ns.transport_dir, ns.scratch
    os.mkdir(N)
    man = json.load(open(os.path.join(T, "MANIFEST.json"), encoding="utf-8"))
    zname = man["zip"]["name"]
    zpath = os.path.join(T, zname)
    results = []

    def expect(name, want_ok, code):
        good = (code == 0) if want_ok else (code != 0)
        results.append(good)
        print(("ok   " if good else "FAIL ") + f"{name}: verify_transport exit {code} ({'must verify' if want_ok else 'must be rejected'})")
    expect("untouched zip", True, run("--manifest", os.path.join(T, "MANIFEST.json"), "--zip", zpath))
    expect("untouched parts", True, run("--manifest", os.path.join(T, "MANIFEST.json"), "--parts-dir", T))
    z = bytearray(open(zpath, "rb").read())
    z[len(z) // 2] ^= 1
    open(os.path.join(N, "flip.zip"), "wb").write(z)
    expect("one bit flipped in the zip", False, run("--manifest", os.path.join(T, "MANIFEST.json"), "--zip", os.path.join(N, "flip.zip")))
    parts = [c["name"] for c in man["base64"]["chunks"]]
    for tag in ("chg", "miss", "extra", "nonascii", "swap"):
        d = os.path.join(N, "p_" + tag)
        os.mkdir(d)
        for p in parts:
            shutil.copy(os.path.join(T, p), d)
    t = bytearray(open(os.path.join(N, "p_chg", parts[1]), "rb").read())
    t[100] = ord("A") if t[100] != ord("A") else ord("B")
    open(os.path.join(N, "p_chg", parts[1]), "wb").write(t)
    os.remove(os.path.join(N, "p_miss", parts[2]))
    open(os.path.join(N, "p_extra", parts[0][:-3] + "099"), "w").write("QQ==\n")
    open(os.path.join(N, "p_nonascii", parts[-1]), "ab").write("é".encode())
    a, b = os.path.join(N, "p_swap", parts[0]), os.path.join(N, "p_swap", parts[1])
    da, db = open(a, "rb").read(), open(b, "rb").read()
    open(a, "wb").write(db)
    open(b, "wb").write(da)
    for tag, what in (("chg", "one character changed in a chunk"), ("miss", "a chunk missing"), ("extra", "an extra chunk file"), ("nonascii", "a non-ASCII byte appended to a chunk"), ("swap", "chunks 1 and 2 swapped")):
        expect(what, False, run("--manifest", os.path.join(T, "MANIFEST.json"), "--parts-dir", os.path.join(N, "p_" + tag)))
    zf = zipfile.ZipFile(zpath)
    items = [(i.filename, zf.read(i.filename)) for i in zf.infolist()]
    first_bin = next(k for k, (n, d) in enumerate(items) if n.endswith("stdout.bin") and d)
    first_zero = next(k for k, (n, d) in enumerate(items) if not d)

    def variant(name, mutate, descr):
        out = os.path.join(N, name + ".zip")
        its = mutate(list(items))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
            for n, d in its:
                zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
                zi.external_attr = 0o644 << 16
                w.writestr(zi, d)
        zb = open(out, "rb").read()
        m = dict(man)
        m["zip"] = dict(man["zip"], bytes=len(zb), sha256=hashlib.sha256(zb).hexdigest())      # container pin updated on purpose: the MEMBER checks must catch it
        mp = os.path.join(N, name + ".json")
        json.dump(m, open(mp, "w"))
        expect(descr, False, run("--manifest", mp, "--zip", out))
    variant("traversal", lambda it: it + [("../escape.txt", b"x")], "extra member ../escape.txt")
    variant("absolute", lambda it: it + [("/abs/x.txt", b"x")], "extra member /abs/x.txt")
    variant("backslash", lambda it: it + [("a\\b.txt", b"x")], "extra member with a backslash")
    variant("dotgit", lambda it: it + [(".git/config", b"x")], "extra member .git/config")
    variant("other", lambda it: it + [("portable/extra.txt", b"x")], "extra member outside the scope")
    variant("missing", lambda it: it[1:], "a member missing")
    variant("dup", lambda it: it + [it[0]], "a duplicate member name")
    variant("casefold", lambda it: it + [(it[0][0].upper(), b"x")], "casefold-colliding member")
    variant("alt_bin", lambda it: [(n, d + b"x") if k == first_bin else (n, d) for k, (n, d) in enumerate(it)], "one stdout.bin altered (1 byte appended)")
    variant("zero_filled", lambda it: [(n, b"x") if k == first_zero else (n, d) for k, (n, d) in enumerate(it)], "one zero-byte file given content")
    variant("bin_dropped", lambda it: [(n, b"") if k == first_bin else (n, d) for k, (n, d) in enumerate(it)], "one stdout.bin emptied")
    expect("extraction target exists", False, run("--manifest", os.path.join(T, "MANIFEST.json"), "--zip", zpath, "--extract-to", N))
    print(f"{sum(results)}/{len(results)} controls behaved as required")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
