#!/usr/bin/env python3
"""Mutation self-check: proves the contract suite is not vacuous.

It copies tools/video-core to a scratch directory (never touching the original), applies ONE small behavioural change to the COPY, runs the contract
suite against the copy (VCC_VIDEO_CORE_DIR), and reports which tests failed. Each mutation either breaks something the library enforces (the
matching LIBRARY_ENFORCED test must fail) or closes a gap the suite pins as LIBRARY_PERMISSIVE (that test must fail loudly, telling us to update
the contract). A mutation that no test notices would be a hole in the suite.

  python3 mutation_self_check.py SCRATCH_DIR [--out results/MUTATION_SELF_CHECK.json]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VC = os.path.abspath(os.path.join(HERE, "..", ".."))
MUTATIONS = [
    {"id": "M1_unresolved_selected_no_longer_holds", "file": "video_core/audiomix/plan.py",
     "old": '''            if sel:
                hold.append({"code": "SELECTED_UNRESOLVED"''', "new": '''            if False:
                hold.append({"code": "SELECTED_UNRESOLVED"''',
     "expect_fail": ["test_b1_selected_unresolved_audio_is_hold_never_silence", "test_f4_exit_code_zero_is_not_ready"], "meaning": "a library regression: selected MISSING/UNKNOWN/PROXY would stop being HOLD"},
    {"id": "M2_output_may_overlap_asset_root", "file": "video_core/paths.py",
     "old": "        if _inside(assets, run_dir) or _inside(run_dir, assets):", "new": "        if False:",
     "expect_fail": ["test_c2_source_output_collisions_in_every_stage"], "meaning": "a library regression: source/output collision no longer refused"},
    {"id": "M3_gap04_closed_receipt_becomes_mandatory", "file": "video_core/avmux/plan.py",
     "old": "    receipt = _check_audio_receipt(m, asset_root, limits) if m.receipt_path else None",
     "new": '    if not m.receipt_path:\n        _reject("audio_receipt_required", "/audio/receipt", "an audio receipt is required")\n    receipt = _check_audio_receipt(m, asset_root, limits)',
     "expect_fail": ["test_d3_receipt_is_optional_and_forgeable_status_and_hash_are_not_evidence", "test_g2_failure_handoff_upstream_failure_never_becomes_downstream_input", "test_s3_avmux_cannot_tell_a_silent_stem_from_intentional_silence_and_drops_warnings"],
     "meaning": "closing GAP-04 (optional receipt) must make the pinned LIBRARY_PERMISSIVE checks fail so the contract gets updated, not silently go stale"},
    {"id": "M4_gap02_closed_video_plan_records_source_hash", "file": "video_core/plan.py",
     "old": '''        "assets": [{"path": a.rel, "bytes": a.size} for a in plan.assets],''',
     "new": '''        "assets": [{"path": a.rel, "bytes": a.size, "sha256": sha256_bytes(open(a.abspath, "rb").read())} for a in plan.assets],''',
     "expect_fail": ["test_a1_video_only_plan_of_a_selected_subset", "test_c1_changed_source_hash"], "meaning": "closing GAP-02 (video plan pins no source hash) must break the pinned permissive checks"},
    {"id": "M5_plan_launches_a_process", "file": "video_core/tools.py",
     "old": '''    if not path:
        return None, how''', "new": '''    if not path:
        return None, how
    __import__("subprocess").run([path, "-version"], capture_output=True)''',
     "expect_fail": ["test_p1_default_plan_validate_dryrun_paths_launch_no_process_and_create_no_directory", "test_a1_video_only_plan_of_a_selected_subset"],
     "meaning": "a plan path that starts executing the located tool (the sentinel stub) must be caught by the audit hook AND the sentinel log"},
    {"id": "M6_plan_creates_the_output_directory", "file": "video_core/avmux/plan.py",
     "old": "        location = check_output_location(output_root, output_name, asset_root)", "new": "        location = check_output_location(output_root, output_name, asset_root)\n        os.makedirs(location.run_dir)",
     "expect_fail": ["test_p1_default_plan_validate_dryrun_paths_launch_no_process_and_create_no_directory", "test_c2_source_output_collisions_in_every_stage"], "meaning": "a plan that creates the output directory must be caught"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scratch")
    ap.add_argument("--out")
    ns = ap.parse_args()
    results = []
    for m in MUTATIONS:
        dst = os.path.join(ns.scratch, m["id"])
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(VC, dst, ignore=shutil.ignore_patterns("evidence", "__pycache__", "tests"))
        path = os.path.join(dst, m["file"])
        src = open(path, encoding="utf-8").read()
        assert src.count(m["old"]) == 1, (m["id"], "pattern must occur exactly once in the copy")
        if "os.makedirs" in m["new"] and "import os" not in src:
            src = "import os\n" + src
        open(path, "w", encoding="utf-8").write(src.replace(m["old"], m["new"]))
        env = dict(os.environ, VCC_VIDEO_CORE_DIR=dst, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "contract_suite.py")], capture_output=True, text=True, env=env, timeout=240)
        text = p.stderr + p.stdout
        failed = sorted(set(re.findall(r"^(?:FAIL|ERROR): (test_\w+)", text, re.M)))
        results.append({"id": m["id"], "file": m["file"], "meaning": m["meaning"], "exit_code": p.returncode, "failed_tests": failed,
                        "expected_to_fail": m["expect_fail"], "all_expected_failed": all(t in failed for t in m["expect_fail"]), "detected": bool(failed)})
        shutil.rmtree(dst, ignore_errors=True)
        print(m["id"], "detected" if failed else "NOT DETECTED", failed, flush=True)
    doc = {"note": "Each mutation was applied to a scratch COPY of tools/video-core; the original tree was never modified.", "results": results,
           "all_detected": all(r["detected"] for r in results), "all_expected_failed": all(r["all_expected_failed"] for r in results)}
    if ns.out:
        with open(ns.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return 0 if doc["all_detected"] and doc["all_expected_failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
