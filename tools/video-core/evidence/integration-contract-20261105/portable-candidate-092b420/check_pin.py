#!/usr/bin/env python3
"""Print the complete source hash inventory summary of a library root and compare it with a source-pin profile. Read-only; Python 3.11+ stdlib; uses the closed harness's inventory().

  python check_pin.py --library-root <dir containing video_core/> --profile profiles/source_pin_092b420.json [--list-files]
Exit 0 only for PINNED_EXACT (every inventoried file byte-identical to the pin). A CRLF-only difference is LABELLED PINNED_MODULO_LINE_ENDINGS (exit 3), never equated silently.
"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portable"))
from portable_core import inventory, pin_status  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library-root", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--list-files", action="store_true")
    ns = ap.parse_args()
    inv = inventory(ns.library_root)
    pin = json.load(open(ns.profile, encoding="utf-8"))["source_pin"]
    st = pin_status(inv, pin)
    print(f"files inventoried: {inv['file_count']} (video_core/: {inv['package_file_count']}); excluded: {inv['excluded']}")
    for k in ("package_digest_raw", "package_digest_eol", "all_digest_raw", "all_digest_eol"):
        print(f"{k:20} {inv[k]}  pin {'==' if inv[k] == pin.get(k) else '!='} {pin.get(k)}")
    print("status:", st["status"], "-", st["detail"])
    if ns.list_files:
        for f in inv["files"]:
            print(f"{f['sha256']}  {f['bytes']:>8}  {f['path']}")
    return {"PINNED_EXACT": 0, "PINNED_MODULO_LINE_ENDINGS": 3, "PACKAGE_PINNED_OTHER_FILES_DIFFER": 4}.get(st["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
