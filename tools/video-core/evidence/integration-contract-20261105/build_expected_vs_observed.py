#!/usr/bin/env python3
"""Assemble EXPECTED_VS_OBSERVED.json, per-version summaries and the rollback evidence from observed-pyX.Y.json files written by
`contract_suite.py --emit DIR`.  It refuses to write anything unless every run passed and all versions produced the same cases_digest.

  python3 build_expected_vs_observed.py EMIT_DIR --out-dir .   [--default-logs logs]
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VC = os.path.abspath(os.path.join(HERE, "..", ".."))
BASE_SHA = "b4ca4a3b031351a631c0fbe1eb149935f4db19ba"
NEWDIR = os.path.basename(HERE)

LAYERS = {
    "LIBRARY_ENFORCED": "Asserted on the library's own output: it refused, held, reported or computed this. This is what the library checks.",
    "LIBRARY_PERMISSIVE": "Asserted on the library's own output: it ACCEPTED or omitted this. The owner must guard it. The assertion pins the gap - if the library ever closes it, the test fails loudly so the contract is updated.",
    "CALLER_GUARD_DEMO": "An illustrative owner-side guard (reference_guards.py), shown next to the library's behaviour. Never the reason a test passes; not part of the library; not a replacement validator.",
    "INSTRUMENT": "Harness self-checks: positive control of the audit hook/sentinel, refusal of execution flags.",
    "FIXTURE": "Fixture determinism and oracle values.",
    "ROLLBACK": "Steps of the isolated-copy rollback demonstration.",
}
NOT_CLAIMED = [
    "No adoption of this library by any existing application, exporter or workflow; this is a generic public abstraction of requirements.",
    "No operational readiness, no production state read or changed, no production source or asset used.",
    "No creative, visual, audio, speech, timing or story-quality claim; real story/voice/timing is unresolved and is not inferable from synthetic fixtures.",
    "No ffmpeg/ffprobe, render, mix, mux or download was executed; the 'videos' are plain non-media bytes; the audio receipt used for AV-mux plans is harness-fabricated.",
    "Linux x86_64 only; Python 3.11/3.12/3.13 only; macOS and Windows untested (the harness is POSIX-only).",
    "End-to-end `mux --mux` preflight behaviour was not run; only its pure comparison function was exercised on canned dicts.",
]


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def library_tree_hash():
    items = []
    for dp, dn, fn in os.walk(VC):
        dn[:] = sorted(d for d in dn if d != "__pycache__" and not (dp == os.path.join(VC, "evidence") and d == NEWDIR))
        for f in sorted(fn):
            p = os.path.join(dp, f)
            items.append((os.path.relpath(p, VC), sha(p)))
    items.sort()
    return hashlib.sha256("".join(f"{a}\t{b}\n" for a, b in items).encode()).hexdigest(), len(items)


def parse_default_log(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"Ran (\d+) tests? in ([\d.]+)s", text)
    ok = re.search(r"^(OK|FAILED)(?: \((.*)\))?$", text, re.M)
    skipped = re.search(r"skipped=(\d+)", ok.group(2) or "") if ok else None
    failures = re.search(r"failures=(\d+)", (ok.group(2) or "") if ok else "")
    errors = re.search(r"errors=(\d+)", (ok.group(2) or "") if ok else "")
    total = int(m.group(1)) if m else None
    sk, fl, er = (int(skipped.group(1)) if skipped else 0), (int(failures.group(1)) if failures else 0), (int(errors.group(1)) if errors else 0)
    return {"total": total, "passed": None if total is None else total - sk - fl - er, "skipped": sk, "failed": fl, "errors": er, "verdict": ok.group(1) if ok else None, "wall_seconds": float(m.group(2)) if m else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("emit_dir")
    ap.add_argument("--out-dir", default=HERE)
    ap.add_argument("--default-logs", default=os.path.join(HERE, "logs"))
    ns = ap.parse_args()
    runs = {}
    for f in sorted(os.listdir(ns.emit_dir)):
        m = re.fullmatch(r"observed-py(3\.\d+)\.json", f)
        if m:
            runs[m.group(1)] = json.load(open(os.path.join(ns.emit_dir, f), encoding="utf-8"))
    if sorted(runs) != ["3.11", "3.12", "3.13"]:
        print("need runs for 3.11, 3.12 and 3.13; have", sorted(runs))
        return 2
    digests = {v: r["cases_digest"] for v, r in runs.items()}
    bad = {v: r["unit_tests"] for v, r in runs.items() if r["unit_tests"]["failed"] or r["unit_tests"]["errors"]}
    if len(set(digests.values())) != 1 or bad:
        print("REFUSING: digests differ or a run failed", digests, bad)
        return 1
    canon_run = runs["3.13"]
    refs = {}
    for case in canon_run["cases"].values():
        for r in case["api_refs"]:
            refs.setdefault(r["file"], {"file_sha256": r["file_sha256"], "referenced": []})["referenced"].append({k: v for k, v in r.items() if k not in ("file", "file_sha256")})
    for v in refs.values():
        seen, uniq = set(), []
        for r in v["referenced"]:
            key = json.dumps(r, sort_keys=True)
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        v["referenced"] = sorted(uniq, key=lambda r: (r["line"], r.get("symbol", r.get("literal", ""))))
    tree_hash, nfiles = library_tree_hash()
    defaults = {}
    if os.path.isdir(ns.default_logs):
        for f in sorted(os.listdir(ns.default_logs)):
            m = re.fullmatch(r"default-suite-py(3\.\d+)\.log", f)
            if m:
                defaults[m.group(1)] = parse_default_log(os.path.join(ns.default_logs, f))
    cases = []
    for cid, c in canon_run["cases"].items():
        ok = all(ck["ok"] for ck in c["checks"])
        cases.append({"id": cid, "title": c["title"], "result": "PASS" if ok else "FAIL", "gaps": c["gaps"], "observations": c["observations"], "source_api_references": c["api_refs"],
                      "checks": c["checks"], "invocations": c["invocations"], "process_accounting": c["process_accounting"], "within_10s_case_budget": c["within_10s_case_budget"],
                      **({"evidence": c["evidence"]} if c.get("evidence") else {})})
    doc = {
        "schema": "video_core.integration_contract.expected_vs_observed/1", "job": NEWDIR, "base_sha": BASE_SHA,
        "scope": "Cross-stage integration contract for the existing public video-only / audiomix / avmux library: what each stage verifies, what existing owners must supply, how missing qualification, failure and rollback propagate.",
        "not_claimed": NOT_CLAIMED, "layers": LAYERS,
        "library_under_test": {"package": "tools/video-core (video_core 0.1.0)", "tree_sha256_excluding_this_directory": tree_hash, "files": nfiles,
                               "referenced_files": refs, "note": "every source reference is resolved against the file at run time; a symbol or literal that no longer exists fails the run"},
        "gaps": canon_run["gaps"], "observations": canon_run["observations"],
        "runs": {v: {"python": r["python"], "unit_tests": r["unit_tests"], "checks_by_kind": r["checks_by_kind"], "cli_invocations": r["invocation_count"], "cases_digest": r["cases_digest"], "wall_seconds": r["wall_seconds"]} for v, r in sorted(runs.items())},
        "cross_version_cases_digest_identical": True,
        "process_accounting": canon_run["process_accounting"],
        "untouched_default_suite": defaults,
        "summary": {"cases": len(cases), "cases_pass": sum(1 for c in cases if c["result"] == "PASS"), "checks_by_kind": canon_run["checks_by_kind"],
                    "gaps_pinned_by_a_test": sorted({g for c in cases for g in c["gaps"]}), "gaps_defined": sorted(canon_run["gaps"])},
        "cases": cases,
    }
    out = ns.out_dir
    os.makedirs(os.path.join(out, "results"), exist_ok=True)
    os.makedirs(os.path.join(out, "rollback"), exist_ok=True)
    enc = lambda o: (json.dumps(o, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    open(os.path.join(out, "EXPECTED_VS_OBSERVED.json"), "wb").write(enc(doc))
    for v, r in runs.items():
        slim = {k: r[k] for k in ("python", "platform", "unit_tests", "checks_by_kind", "process_accounting", "invocation_count", "cases_digest")}
        slim["per_case"] = {cid: {"result": "PASS" if all(ck["ok"] for ck in c["checks"]) else "FAIL", "checks": len(c["checks"])} for cid, c in r["cases"].items()}
        open(os.path.join(out, "results", f"summary-py{v}.json"), "wb").write(enc(slim))
    ev = canon_run["cases"]["test_g3_rollback_on_isolated_copies"]
    open(os.path.join(out, "rollback", "ROLLBACK_EVIDENCE.json"), "wb").write(enc({
        "case": ev["id"], "title": ev["title"], "evidence": ev["evidence"], "checks": ev["checks"], "invocations": ev["invocations"],
        "reproduce": "python3 contract_suite.py   # test_g3_rollback_on_isolated_copies; the world is a temp dir created and deleted by the test"}))
    print("wrote EXPECTED_VS_OBSERVED.json (%d cases, %d checks), %d summaries, rollback evidence" % (len(cases), sum(len(c["checks"]) for c in cases), len(runs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
