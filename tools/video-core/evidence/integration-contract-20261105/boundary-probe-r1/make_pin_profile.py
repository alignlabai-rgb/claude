#!/usr/bin/env python3
"""Write a SOURCE-PIN-ONLY profile (no reviewed expectations, no proposals) for one exported source tree.
  python make_pin_profile.py --library-root DIR --label b4ca4a3 --commit <full sha> --role original|candidate|input --out profiles/pin_b4ca4a3.json
The inventory comes from the unchanged r2 harness (portable_core.inventory). The export must come from `git archive <commit> tools/video-core` (no working-tree files)."""
import argparse, json, os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--library-root", required=True); ap.add_argument("--label", required=True); ap.add_argument("--commit", required=True)
ap.add_argument("--role", required=True, choices=("original", "candidate", "input")); ap.add_argument("--out", required=True)
ap.add_argument("--harness-dir", default=os.path.join(HERE, "..", "portable-r2"))
ns = ap.parse_args()
sys.path.insert(0, os.path.abspath(ns.harness_dir))
from portable_core import inventory  # noqa: E402
if os.path.lexists(ns.out):
    raise SystemExit("refused: output exists")
inv = inventory(ns.library_root)
pin = {k: inv[k] for k in ("package_digest_raw", "package_digest_eol", "all_digest_raw", "all_digest_eol", "file_count", "package_file_count", "digest_definition")}
pin.update(excluded=inv["excluded"], source_label=ns.label, source_commit=ns.commit, source_role=ns.role, computed_from="git archive %s tools/video-core (no working-tree files)" % ns.commit[:7])
doc = {"description": "SOURCE PIN ONLY for %s (%s). No reviewed expectations and no proposals: observations stay observations. Not a review, not production adoption." % (ns.label, ns.role),
       "profile_version": 1, "review_status": "NONE", "reviewed_expectations": {}, "source_pin": pin}
with open(ns.out, "x", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print(ns.label, pin["package_digest_raw"][:12], pin["all_digest_raw"][:12], pin["file_count"])
