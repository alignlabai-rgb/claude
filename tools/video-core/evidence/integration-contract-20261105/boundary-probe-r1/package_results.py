#!/usr/bin/env python3
"""Copy probe output directories into results/ with a DISCLOSED size reduction (Python 3.11+ stdlib only, create-only).

  python package_results.py --full py3.13=<out dir> --index py3.11=<out dir> --index py3.12=<out dir> --dest results
--full : the whole directory is copied, including every invocation's exact stdout.bin / stderr.bin / status.json.
--index: everything except invocations/ is copied, plus invocations_index.json that lists, for every invocation, its status.json fields including the raw retained-stdout SHA-256 and the
         normalised stdout SHA-256 - so equality of the exact bytes across interpreters remains checkable even though the byte files are stored once (in the --full copy).
Nothing is rewritten: copied files are byte-identical to the originals."""
import argparse
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="append", default=[], metavar="TAG=DIR")
    ap.add_argument("--index", action="append", default=[], metavar="TAG=DIR")
    ap.add_argument("--dest", required=True)
    ns = ap.parse_args(argv)
    os.mkdir(ns.dest)
    for mode, items in (("full", ns.full), ("index", ns.index)):
        for it in items:
            tag, _, d = it.partition("=")
            target = os.path.join(ns.dest, tag)
            if mode == "full":
                shutil.copytree(d, target)
                continue
            shutil.copytree(d, target, ignore=shutil.ignore_patterns("invocations"))      # the only directories named 'invocations' are sources/<label>/invocations
            index = {}
            for src in sorted(os.listdir(os.path.join(d, "sources"))):
                inv_dir = os.path.join(d, "sources", src, "invocations")
                rows = []
                for name in sorted(os.listdir(inv_dir)):
                    with open(os.path.join(inv_dir, name, "status.json"), encoding="utf-8") as fh:
                        st = json.load(fh)
                    rows.append({"dir": name, **{k: st.get(k) for k in ("no", "group", "case", "label", "module", "status", "exit_code", "stdout_bytes_total", "stderr_bytes_total", "stdout_retained", "stderr_retained", "truncated",
                                                                          "denied_attempts", "stdout_sha256_raw_retained", "stdout_sha256_normalized", "stdout_sha256_stored", "stderr_sha256_stored")}})
                index[src] = rows
            with open(os.path.join(target, "invocations_index.json"), "x", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
