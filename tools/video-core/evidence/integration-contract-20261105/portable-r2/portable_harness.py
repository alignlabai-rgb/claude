#!/usr/bin/env python3
"""portable-r2 DRIVER. Differences from ../portable/portable_harness.py: deny-first ADMISSION (MS01 runs before anything else; a failure STOPS the run with exit 5 before any contract case, other mechanism test
or mutation control), --only accepts PC, MS and MU ids, strict validated profile loading, a profile with NOT-A-REVIEW entries can never give exit 0. See R2_CHANGES.md.

Portable, bounded CONTRACT ACCEPTANCE HARNESS for the public video_core / audiomix / avmux CLIs (Python 3.11+ stdlib only; Linux and Windows).

Read-only: real `validate`, `plan`, `srt` and DRY-RUN (`render`/`mix`/`mux` WITHOUT their execution flag) behaviour. It never renders, mixes, muxes, probes or
downloads, never uses ffmpeg/ffprobe, never passes --render/--mix/--mux (or abbreviations) or `inspect`, never uses shell=True, and changes no global state.

  python portable_harness.py --library-root <dir containing video_core/> --scratch-root <existing dir> --out-dir <NEW dir>  [--strict] [--profile FILE]
                             [--budget-seconds N] [--only ID[,ID]] [--skip-selftests] [--skip-mutation] [--normalize-stored-paths] [--list]

Exit codes (r2 adds 5): 0 all enforced checks pass, every OPEN GAP is exactly as pinned (and, with --strict, the source is pinned/reviewed) | 1 failure, error, budget exhausted
            2 usage / refusal (e.g. output directory exists) | 3 Python < 3.11 | 4 REVIEW_REQUIRED (an OPEN GAP changed, --strict and the source is not pinned/reviewed, or the profile applied a NOT-A-REVIEW entry)
            5 STOPPED: the deny-control admission (MS01) did not pass; nothing else was run
An OPEN GAP match is OBSERVED_OPEN_GAP: it is never counted as a pass and never as a fix.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

sys.dont_write_bytecode = True      # the harness itself leaves no __pycache__ behind
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if sys.version_info < (3, 11):
    emit_err("portable_harness requires Python 3.11 or newer\n")
    sys.exit(3)

import portable_cases  # noqa: E402
import portable_selftest  # noqa: E402
from portable_core import (DEFAULT_BUDGET_S, HARNESS_VERSION, MAX_BUDGET_S, MAX_CHILDREN, PER_CHILD_TIMEOUT, STREAM_CAP, FLOOD_LIMIT, OutDir, Profile, ProfileError, Run, find_repo_root, inside,  # noqa: E402
                           inventory, load_profile_file, run_case, sha256_bytes, summarize)

DEFAULT_PROFILE = os.path.join(HERE, "profiles", "pinned_original_b4ca4a3.json")
HARNESS_FILES = ("portable_harness.py", "portable_core.py", "portable_cases.py", "portable_selftest.py", "child_runner.py", "json_equiv.py")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="portable_harness.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library-root", help="directory containing the video_core/ package under test (e.g. tools/video-core, or a separately recovered candidate)")
    ap.add_argument("--scratch-root", help="existing directory under which an OWNED temporary directory is created and removed")
    ap.add_argument("--out-dir", help="NEW directory for results; refused if it exists or is inside the library root / repository")
    ap.add_argument("--profile", default=DEFAULT_PROFILE, help="expectation profile (source pin + reviewed expectations); default: the pinned original b4ca4a3")
    ap.add_argument("--strict", action="store_true", help="a library that is not pinned (or reviewed by a matching profile) is REVIEW_REQUIRED -> exit 4")
    ap.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_S, help=f"total wall budget (default {DEFAULT_BUDGET_S:.0f}, max {MAX_BUDGET_S:.0f}); each child is limited to {PER_CHILD_TIMEOUT:.0f} s regardless")
    ap.add_argument("--only", help="comma-separated ids of contract cases (PC*), mechanism tests (MS*) and mutation controls (MU*); the deny-control admission MS01 ALWAYS runs first")
    ap.add_argument("--skip-selftests", action="store_true", help="skip the mechanism self-tests after the mandatory admission (not recommended)")
    ap.add_argument("--skip-mutation", action="store_true", help="skip mutation controls on disposable copies")
    ap.add_argument("--normalize-stored-paths", action="store_true", help="replace scratch/world/out paths by placeholders in STORED stdout/stderr (raw hashes are kept in status.json)")
    ap.add_argument("--list", action="store_true", help="list case ids and exit")
    return ap.parse_args(argv)


def admission_fn():
    return next(f for f in portable_selftest.MS_CASES if f.case_id.startswith("MS01"))


def main(argv=None, admission_cases=None) -> int:
    """admission_cases: for TESTS only (Python API, not a CLI option): replaces the admission list, e.g. by a disposable, intentionally failing deny control."""
    ns = parse_args(argv)
    cases = list(portable_cases.CASES)
    if ns.list:
        print("[admission, always first]", admission_fn().case_id)
        for fn in cases + portable_selftest.MS_CASES:
            print(fn.case_id, "-", fn.title)
        for m in portable_selftest.MUTATIONS:
            print(m["id"], "-", m["means"])
        return 0
    for req in ("library_root", "scratch_root", "out_dir"):
        if not getattr(ns, req):
            emit_err(f"usage: --{req.replace('_', '-')} is required\n")
            return 2
    if not (0 < ns.budget_seconds <= MAX_BUDGET_S):
        emit_err(f"usage: --budget-seconds must be in (0, {MAX_BUDGET_S:.0f}]\n")
        return 2
    lib = os.path.realpath(ns.library_root)
    if not os.path.isfile(os.path.join(lib, "video_core", "__init__.py")):
        emit_err(f"refused: {lib} does not contain video_core/__init__.py\n")
        return 2
    scratch_root = os.path.realpath(ns.scratch_root)
    if not os.path.isdir(scratch_root):
        emit_err(f"refused: scratch root {scratch_root} is not an existing directory\n")
        return 2
    repo = find_repo_root(HERE)
    forbidden = [p for p in (lib, repo, HERE) if p]
    for label, p in (("scratch root", scratch_root),):
        for f in forbidden:
            if inside(p, f):
                emit_err(f"refused: {label} {p} is inside {f}; the harness never writes into the repository or the library under test\n")
                return 2
    if inside(os.path.abspath(ns.out_dir), scratch_root) or inside(scratch_root, os.path.abspath(ns.out_dir)):
        emit_err("refused: the output directory and the scratch root must be separate directories\n")
        return 2
    try:
        profile_doc, profile_bytes = load_profile_file(ns.profile)
    except (OSError, ProfileError) as exc:
        emit_err(f"refused: cannot use profile {ns.profile}: {exc}\n")
        return 2
    all_ids = {fn.case_id for fn in cases} | {fn.case_id for fn in portable_selftest.MS_CASES} | {m["id"] for m in portable_selftest.MUTATIONS}
    only = set(x for x in ns.only.split(",") if x) if ns.only else None
    if only is not None:
        unknown = only - all_ids
        if unknown or not only:
            emit_err(f"usage: unknown or empty ids {sorted(unknown)}\n")
            return 2
    try:
        out = OutDir.create_new(ns.out_dir, forbidden)
    except SystemExit as exc:
        emit_err(str(exc) + "\n")
        return 2
    inv_before = inventory(lib)
    profile = Profile(profile_doc, inv_before)
    t_start = time.monotonic()
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results, mutation, cleanup_ok, admission_results, stopped = [], [], None, [], None
    tmp = tempfile.TemporaryDirectory(dir=scratch_root, prefix="vcc-portable-", ignore_cleanup_errors=True)
    scratch = os.path.realpath(tmp.name)
    adm_list = list(admission_cases) if admission_cases is not None else [admission_fn()]
    try:
        run = Run(library_root=lib, scratch=scratch, out=out, budget_s=ns.budget_seconds, profile=profile, strict=ns.strict, normalize_stored=ns.normalize_stored_paths)
        # ---- ADMISSION: the deny controls must pass BEFORE anything else; a failure stops everything (no contract case, no other mechanism test, no mutation control)
        run.group = "admission"
        for fn in adm_list:
            admission_results.append(run_case(run, fn))
        bad_adm = [r["case"] for r in admission_results if r["status"] != "PASS"]
        children_after_admission = run.children
        if bad_adm:
            stopped = {"stage": "admission", "failed": bad_adm, "children_started_when_stopped": run.children,
                       "message": "STOP: a deny control did not pass; the prohibited-action protection is not demonstrated on this machine. No contract case, mechanism test or mutation control was run."}
        else:
            run.group = "contract"
            sel_ms = [fn for fn in portable_selftest.MS_CASES if not fn.case_id.startswith("MS01")]
            for fn in cases:
                if only is None or fn.case_id in only:
                    results.append(run_case(run, fn))
            if (only is None and not ns.skip_selftests) or (only is not None and any(fn.case_id in only for fn in sel_ms)):
                for fn in sel_ms:
                    if only is None or fn.case_id in only:
                        run.group = "mechanism"
                        results.append(run_case(run, fn))
                run.group = "contract"
            mu_ids = {m["id"] for m in portable_selftest.MUTATIONS}
            if (only is None and not ns.skip_selftests and not ns.skip_mutation) or (only is not None and (only & mu_ids)):
                mutation = portable_selftest.run_mutation_controls(run, cases, only=(only & mu_ids) if only is not None else None)
                run.group = "contract"
    finally:
        try:
            tmp.cleanup()
            cleanup_ok = not os.path.exists(scratch)
        except OSError:
            cleanup_ok = False
    all_results = admission_results + results
    inv_after = inventory(lib)
    unchanged = inv_before["all_digest_raw"] == inv_after["all_digest_raw"]
    summary = summarize(all_results)
    outcomes = {}
    for cr in all_results:
        for c in cr["checks"]:
            outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
    mut_bad = [m for m in mutation if m["status"] == "NOT_DETECTED"]
    findings = []
    if stopped:
        findings.append("STOPPED: " + stopped["message"])
    if summary["cases_by_status"].get("FAIL"):
        findings.append("FAIL: at least one case failed")
    if mut_bad:
        findings.append("FAIL: a mutation was NOT detected: " + ", ".join(m["id"] for m in mut_bad))
    if not unchanged:
        findings.append("FAIL: the library root changed during the run")
    if cleanup_ok is False:
        findings.append("NOTE: the owned scratch directory could not be fully removed")
    review = []
    if summary["cases_by_status"].get("REVIEW_REQUIRED"):
        review.append("an OPEN GAP changed relative to the pinned source: review the listed checks; nothing was adopted")
    if ns.strict and not profile.applicable:
        review.append(f"--strict: library source is {profile.pin['status']} and no matching reviewed profile was supplied")
    if profile.applied_not_a_review:
        review.append(f"the profile applied {len(set(profile.applied_not_a_review))} entr(y/ies) marked NOT-A-REVIEW: a dry run, never a green run")
    bad_set = ("GUARD_MISMATCH", "CONTROL_FAILED", "POLICY_VIOLATION", "ERROR", "BUDGET_EXHAUSTED")
    if stopped:
        code = 5
    elif any(f.startswith("FAIL") for f in findings) or any(o in outcomes for o in bad_set):
        code = 1
    else:
        code = 4 if review else 0
    wall = time.monotonic() - t_start
    doc = {
        "harness": HARNESS_VERSION, "started_utc": started_utc, "wall_seconds": round(wall, 2), "exit_code": code,
        "python": {"version": sys.version.split()[0], "implementation": sys.implementation.name, "platform": sys.platform, "os_name": os.name, "executable_basename": os.path.basename(sys.executable)},
        "claims": "Executed only on the machine that produced this file. Windows behaviour is claimed only if python.platform is 'win32' in this file.",
        "bounds": {"per_child_timeout_s": PER_CHILD_TIMEOUT, "stream_cap_bytes": STREAM_CAP, "retained_per_child_bytes_max": 2 * STREAM_CAP, "flood_kill_bytes": FLOOD_LIMIT, "max_children": MAX_CHILDREN,
                   "total_budget_s": ns.budget_seconds, "children_started": run.children, "serial": True},
        "admission": {"cases": [r["case"] for r in admission_results], "statuses": [r["status"] for r in admission_results], "children": children_after_admission, "stopped": stopped},
        "inputs": {"library_root_basename": os.path.basename(lib), "profile": os.path.basename(ns.profile), "profile_sha256": sha256_bytes(profile_bytes), "profile_validated": True,
                   "reviewed_entries_in_profile": len(profile_doc.get("reviewed_expectations", {})), "reviewed_entries_applied_not_a_review": sorted(set(profile.applied_not_a_review)),
                   "strict": ns.strict, "only": sorted(only) if only else None, "normalize_stored_paths": ns.normalize_stored_paths},
        "source": {"pin": profile.pin, "profile_applicable": profile.applicable, "reviewed_expectations_ignored_for_pin_mismatch": sorted(profile.reviewed_ignored), "unchanged_during_run": unchanged,
                   "package_digest_raw": inv_before["package_digest_raw"], "package_digest_eol": inv_before["package_digest_eol"], "all_digest_raw": inv_before["all_digest_raw"], "all_digest_eol": inv_before["all_digest_eol"],
                   "file_count": inv_before["file_count"], "package_file_count": inv_before["package_file_count"], "excluded": inv_before["excluded"], "skipped": inv_before["skipped"]},
        "harness_files_sha256": {f: sha256_bytes(open(os.path.join(HERE, f), "rb").read()) for f in HARNESS_FILES},
        "summary": summary, "check_outcomes": dict(sorted(outcomes.items())), "mutation_controls": mutation,
        "findings": findings, "review_required": review, "scratch_cleaned": cleanup_ok,
        "driver_accounting": {"children_started": run.children, "refused_before_launch": run.refused, "launches_by_group": _count(run.launch_log, "group")},
        "reading_guide": {"PASS": "LIBRARY_ENFORCED matched its independent expectation", "OBSERVED_OPEN_GAP": "LIBRARY_PERMISSIVE matched the pinned gap (the library accepted/omitted what an owner must guard): NOT a pass, NOT a fix",
                          "REVIEW_REQUIRED": "an open-gap observation changed: needs an explicit reviewed expectation in a profile with a matching source pin", "REVIEWED_CHANGE": "differs from the default expectation but matches a reviewed profile entry",
                          "GUARD_OK": "an illustrative owner-side guard (never a library result)", "CONTROL_OK": "a harness mechanism control", "SKIPPED": "not run, with the reason recorded",
                          "ERROR": "an unsupported value was compared, or a case raised: never rescued by a profile"},
    }
    out.write_json("inventory.json", {"root_basename": os.path.basename(lib), **{k: v for k, v in inv_before.items() if k != "root_basename"}})
    for cr in all_results:
        out.write_json("cases/%s.json" % cr["case"], cr)
    out.write_json("run.json", doc)
    lines = [f"portable-r2 contract harness {HARNESS_VERSION} on Python {doc['python']['version']} ({sys.platform})", f"library pin: {profile.pin['status']} ({profile.pin['detail']})",
             f"admission: {', '.join(r['case'][:4] + '=' + r['status'] for r in admission_results)}  children after admission={children_after_admission}",
             f"cases: {json.dumps(summary['cases_by_status'])}  children={run.children}  wall={wall:.1f}s  exit={code}",
             "check outcomes: " + json.dumps(dict(sorted(outcomes.items()))),
             "layers: " + json.dumps(summary["checks_by_layer_and_outcome"], sort_keys=True)]
    for m in mutation:
        lines.append(f"mutation {m['id']}: {m['status']}" + (f" ({m.get('failing_check_count')} failing checks)" if m["status"] != "NOT_APPLICABLE" else " - " + m["reason"]))
    for cr in all_results:
        if cr["status"] != "PASS":
            lines.append(f"CASE {cr['case']}: {cr['status']}")
            for c in cr["checks"]:
                if c["outcome"] in ("FAIL", "ERROR", "REVIEW_REQUIRED", "GUARD_MISMATCH", "CONTROL_FAILED", "BUDGET_EXHAUSTED", "POLICY_VIOLATION"):
                    lines.append(f"   {c['outcome']}: {c['name']}" + (f"\n      expected={json.dumps(c.get('expected'), ensure_ascii=False)[:300]}\n      observed={json.dumps(c.get('observed'), ensure_ascii=False)[:300]}" if "expected" in c else f" {c.get('error', '')[:300]}"))
    lines += ["FINDINGS: " + "; ".join(findings) if findings else "FINDINGS: none", "REVIEW: " + "; ".join(review) if review else "REVIEW: none"]
    text = "\n".join(lines) + "\n"
    out.write("SUMMARY.txt", text.encode("utf-8"))
    if stopped:
        out.write("STOP.txt", (stopped["message"] + "\n").encode("utf-8"))
    emit(text)
    return code


def emit_err(text: str) -> None:
    emit(text, sys.stderr)


def emit(text: str, stream=None) -> None:
    """Print without ever raising UnicodeEncodeError on a legacy console (cp932/cp1252): unencodable characters become \\uXXXX escapes. The stored SUMMARY.txt is UTF-8."""
    stream = stream or sys.stdout
    enc = getattr(stream, "encoding", None) or "utf-8"
    stream.write(text.encode(enc, errors="backslashreplace").decode(enc, errors="replace"))
    stream.flush()


def _count(log, key):
    d = {}
    for e in log:
        d[e[key]] = d.get(e[key], 0) + 1
    return d


if __name__ == "__main__":
    sys.exit(main())
