#!/usr/bin/env python3
"""SUPPLEMENTARY probe (not part of the closed `portable/` harness, which is imported unchanged): how avmux `plan` treats the three receipt classes.

Plan-only: every observation is one `python -m video_core.avmux plan` run by the closed harness's OWN bounded driver (10 s/child, 256 KiB diagnostics, serial, wall budget)
inside its OWN denying audit hook. Before anything else the harness's deny positive controls (MS01) are re-run; if any fails the probe STOPS (exit 5) and reports.
No mix, no render, no FFmpeg, no media; "videos" are non-media stubs.

  python probe_receipt_classes.py --library-root DIR --scratch-root DIR --out-dir NEWDIR --role original|candidate [--normalize-stored-paths]

--role selects which pinned expectation table is evaluated:
  original   observations of b4ca4a3 (derived by reading its _check_audio_receipt: three shallow fields)
  candidate  the documented contract of 092b420 (INPUT_CONTRACT.md "F-REC-1 and F-REC-2" + the check order of receipt_check.py)
Layers as in the harness: LIBRARY_ENFORCED (must hold) / LIBRARY_PERMISSIVE (accepted although an owner must still guard it). A mismatch is FAIL (never normalised).
Exit: 0 all as expected | 1 mismatch | 2 usage | 5 deny control failed (STOP).
"""
import argparse
import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "portable"))
sys.path.insert(0, HERE)

import candidate_fixtures as cf  # noqa: E402
import portable_cases as pcs  # noqa: E402
import portable_selftest as pst  # noqa: E402
from portable_core import (OutDir, Profile, Run, World, cli, classify_check, find_repo_root, inside, inventory, run_case, sha256_bytes)  # noqa: E402

AVM = "video_core.avmux"
MANIFEST_BASE = {"avmux_version": 1, "route": "MOV_PCM_S16LE", "selection": {"ref": "synthetic-selection/0001"}}


def avmux_world(run, label, *, stem, stem_sha, samples, fps, frames, receipt_bytes):
    w = run.new_world(label)
    w.put("stem.wav", stem)
    w.put("video_ok.mp4", pcs.FB["video_ok.mp4"])
    man = dict(MANIFEST_BASE, video={"state": "USE", "path": "video_ok.mp4", "sha256": pcs.H["video_ok.mp4"],
                                     "declared": {"codec": "h264", "width": 320, "height": 180, "fps": {"n": fps[0], "d": fps[1]}, "total_frames": frames}},
               audio={"state": "USE", "path": "stem.wav", "sha256": stem_sha, "declared": {"sample_rate": 48000, "channels": 1, "sample_count": samples}})
    if receipt_bytes is not None:
        w.put("audio_receipt.json", receipt_bytes)
        man["audio"]["receipt"] = {"path": "audio_receipt.json", "sha256": sha256_bytes(receipt_bytes)}
    w.put_json("av.json", man)
    return w


def observe(run, w, sid):
    r = cli(run, w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], label=sid, case_id="receipt-probe")
    j = r.json if isinstance(r.json, dict) else {}
    plan_receipt = ((j.get("plan") or {}).get("audio") or {}).get("receipt", "<no plan>") if "plan" in j else "<no plan>"
    obs = {"exit": r.exit_code, "accepted": r.exit_code == 0 and "plan" in j, "codes": r.issue_codes,
           "unstructured_crash": r.exit_code == 1 and r.stdout_bytes.strip() == b"" and ("Error" in r.stderr_text), "denied_attempts": len(r.denials), "child_status": r.status}
    if obs["accepted"]:
        pr = plan_receipt if isinstance(plan_receipt, dict) else None
        obs["plan_receipt_keys"] = sorted(pr) if pr else None
        if pr:
            obs["mechanical_checks"] = pr.get("mechanical_checks")
            obs["authentication_note_present"] = "authentication" in pr
            obs["warnings_in_plan"] = [x["code"] for x in pr.get("warnings", [])] if "warnings" in pr else None
            obs["unresolved_events_in_plan"] = pr.get("unresolved_events")
    return obs


def build_scenarios(genuine_bytes):
    """[(id, class, description, builder(run)->World, {role: (layer, expected-subset-dict)})]. Expected dicts are compared on the keys they name."""
    S = []
    gstem = cf.genuine_stem_bytes()
    gdoc = json.loads(genuine_bytes.decode("utf-8")) if genuine_bytes else None
    fab_stem = pcs.FB["stem.wav"]

    def gworld(label, rb):
        return lambda run: avmux_world(run, label, stem=gstem, stem_sha=cf.GENUINE_STEM_SHA256, samples=4800, fps=(30, 1), frames=3, receipt_bytes=rb)

    def fworld(label, rb):
        return lambda run: avmux_world(run, label, stem=fab_stem, stem_sha=pcs.H["stem.wav"], samples=4805, fps=(30000, 1001), frames=3, receipt_bytes=rb)
    ACC = {"accepted": True}
    rej = lambda *codes: {"accepted": False, "codes": list(codes), "unstructured_crash": False}
    if gdoc is not None:
        S.append(("G1", "GENUINE", "committed genuine receipt + stem proven hash-identical to the genuine stem", gworld("g1", genuine_bytes),
                  {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", {"accepted": True, "mechanical_checks": "PASSED", "authentication_note_present": True, "warnings_in_plan": []})}))
        other_stem = pcs.FB["stem.wav"]
        S.append(("G2", "GENUINE", "the genuine receipt presented for a DIFFERENT stem (hash binding)", lambda run: avmux_world(run, "g2", stem=other_stem, stem_sha=pcs.H["stem.wav"], samples=4805, fps=(30000, 1001), frames=3, receipt_bytes=genuine_bytes),
                  {"original": ("LIBRARY_ENFORCED", rej("audio_receipt_stem_mismatch")), "candidate": ("LIBRARY_ENFORCED", rej("audio_receipt_stem_mismatch"))}))
        for sid, how, cand_code in (("A1", "all_ok_false", "audio_receipt_inconsistent"), ("A2", "stem_partial_true", "audio_receipt_inconsistent"), ("A3", "sample_count_float", "audio_receipt_sample_mismatch"),
                                    ("A4", "inputs_unchanged_false", "audio_receipt_inconsistent"), ("A5", "quality_evaluated_true", "audio_receipt_inconsistent")):
            S.append((sid, "TAMPERED_GENUINE", f"genuine receipt with one field edited ({how}), manifest re-pinned to the edited bytes", gworld(sid.lower(), cf.jbytes(cf.tamper(gdoc, how))),
                      {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", rej(cand_code))}))
        txt = genuine_bytes.decode("utf-8")
        dup = ('{"status": "UNKNOWN",' + txt.lstrip()[1:]).encode("utf-8")
        nan = ('{"harness_note": NaN,' + txt.lstrip()[1:]).encode("utf-8")
        S.append(("D1", "TAMPERED_GENUINE", "duplicate key: first status UNKNOWN, last status MIXED_VERIFIED ('last wins' in a lenient parser)", gworld("d1", dup),
                  {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", rej("audio_receipt_unreadable"))}))
        S.append(("N1", "TAMPERED_GENUINE", "a NaN literal inside the receipt", gworld("n1", nan), {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", rej("audio_receipt_unreadable"))}))
        S.append(("O1", "NO_RECEIPT", "receipt omitted from the manifest (genuine stem)", gworld("o1", None),
                  {"original": ("LIBRARY_PERMISSIVE", dict(ACC, plan_receipt_keys=None)), "candidate": ("LIBRARY_PERMISSIVE", dict(ACC, plan_receipt_keys=None))}))
    three = {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": pcs.H["stem.wav"]}], "readback": {"wav": {"sample_count": 4805}}}
    S.append(("S1", "FABRICATED_STUB", "the closed harness's shape-faithful stub (no plan output, empty checks, no mix block)", fworld("s1", pcs.jbytes(pcs.receipt_fabricated())),
              {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", rej("audio_receipt_plan_hash"))}))
    S.append(("S2", "FABRICATED_STUB", "three-field hand-written receipt (status, stem sha256, sample count)", fworld("s2", pcs.jbytes(three)),
              {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", rej("audio_receipt_schema"))}))
    contra = dict(pcs.receipt_fabricated(), stage="mix", written=False, plan_sha256="0" * 64, input={"manifest_sha256": "1" * 64, "inputs_unchanged": False, "assets": []}, readback={"all_ok": False, "wav": {"sample_count": 4805}})
    S.append(("S3", "FABRICATED_STUB", "stub contradicting its own status (written false, inputs_unchanged false, all_ok false)", fworld("s3", pcs.jbytes(contra)),
              {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_ENFORCED", rej("audio_receipt_inconsistent"))}))
    forged = cf.forged_receipt(fab_stem, samples=4805)
    S.append(("F1", "SELF_CONSISTENT_FORGERY", "hand-made receipt satisfying every mechanical check for the 4805-sample stem", fworld("f1", cf.jbytes(forged)),
              {"original": ("LIBRARY_PERMISSIVE", ACC), "candidate": ("LIBRARY_PERMISSIVE", {"accepted": True, "mechanical_checks": "PASSED", "authentication_note_present": True})}))
    forged_w = cf.forged_receipt(fab_stem, samples=4805, warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}, {"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}], unresolved=[{"event_id": "e", "state": "UNKNOWN", "selected": True}])
    S.append(("F2", "SELF_CONSISTENT_FORGERY", "forgery carrying two warnings and one unresolved event: does the plan keep them?", fworld("f2", cf.jbytes(forged_w)),
              {"original": ("LIBRARY_PERMISSIVE", dict(ACC, plan_receipt_keys=["path", "plan_sha256", "sha256", "status"])),
               "candidate": ("LIBRARY_ENFORCED", {"accepted": True, "warnings_in_plan": ["ZERO_FILL_APPLIED", "NO_AUDIO_EVENTS_SELECTED"], "unresolved_events_in_plan": 1})}))
    shapes = {"M1_array": b"[]\n", "M2_string": b'"x"\n', "M3_null": b"null\n", "M4_number": b"1\n", "M5_outputs_is_string": cf.jbytes({"status": "MIXED_VERIFIED", "outputs": "x"}),
              "M6_outputs_list_of_strings": cf.jbytes({"status": "MIXED_VERIFIED", "outputs": ["a"]}), "M7_readback_is_string": cf.jbytes({"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": pcs.H["stem.wav"]}], "readback": "x"}),
              "M8_wav_is_string": cf.jbytes({"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": pcs.H["stem.wav"]}], "readback": {"wav": "x"}})}
    for sid, rb in shapes.items():
        code = "audio_receipt_shape" if sid in ("M1_array", "M2_string", "M3_null", "M4_number") else "audio_receipt_schema"
        S.append((sid, "MALFORMED", "valid JSON of an unexpected shape", fworld(sid.lower(), rb),
                  {"original": ("LIBRARY_PERMISSIVE", {"accepted": False, "unstructured_crash": True, "codes": []}), "candidate": ("LIBRARY_ENFORCED", rej(code))}))
    return S


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for a in ("library-root", "scratch-root", "out-dir"):
        ap.add_argument("--" + a, required=True)
    ap.add_argument("--role", required=True, choices=("original", "candidate"))
    ap.add_argument("--normalize-stored-paths", action="store_true")
    ns = ap.parse_args(argv)
    lib, scratch_root = os.path.realpath(ns.library_root), os.path.realpath(ns.scratch_root)
    repo = find_repo_root(HERE)
    for p in (scratch_root,):
        for f in (lib, repo, HERE):
            if f and inside(p, f):
                print("refused: scratch root inside", f)
                return 2
    try:
        out = OutDir.create_new(ns.out_dir, [x for x in (lib, repo, HERE) if x])
    except SystemExit as exc:
        print(exc)
        return 2
    inv = inventory(lib)
    tmp = tempfile.TemporaryDirectory(dir=scratch_root, prefix="vcc-probe-", ignore_cleanup_errors=True)
    scratch = os.path.realpath(tmp.name)
    run = Run(library_root=lib, scratch=scratch, out=out, budget_s=300.0, profile=Profile(None, inv), strict=False, normalize_stored=ns.normalize_stored_paths)
    try:
        # STOP rule: the deny controls must be active before any library observation
        run.group = "mechanism"
        ctl = run_case(run, next(f for f in pst.MS_CASES if f.case_id.startswith("MS01")))
        out.write_json("deny_controls.json", ctl)
        if ctl["status"] != "PASS":
            msg = "STOP: the harness deny controls (MS01) did NOT pass; no library observation was made. See deny_controls.json."
            out.write("STOP.txt", (msg + "\n").encode())
            print(msg)
            return 5
        run.group = "probe"
        gp = os.path.join(lib, *cf.GENUINE_RECEIPT_RELPATH)
        genuine = open(gp, "rb").read() if os.path.isfile(gp) else None
        genuine_ok = genuine is not None and cf.sha256(cf.lf(genuine)) == cf.GENUINE_RECEIPT_SHA256_LF
        stem_ok = cf.sha256(cf.genuine_stem_bytes()) == cf.GENUINE_STEM_SHA256
        rows, skipped = [], []
        if genuine is not None and not genuine_ok:
            skipped.append("genuine receipt present but its LF-normalised sha256 differs from the pin: genuine-class scenarios skipped")
            genuine = None
        if genuine is None and not any("skipped" in s for s in skipped):
            skipped.append("committed genuine receipt not found under the library root: genuine-class scenarios skipped (qualification limit)")
        for sid, klass, desc, build, exp in build_scenarios(genuine):
            w = build(run)
            obs = observe(run, w, sid)
            layer, want = exp[ns.role]
            got = {k: obs.get(k) for k in want}
            res = classify_check(layer, f"probe::{sid}", want, got, None)
            rows.append({"id": sid, "receipt_class": klass, "description": desc, "layer": layer, "expected": want, "observed": got, "full_observation": obs, "outcome": res["outcome"]})
    finally:
        tmp.cleanup()
    outcomes = {}
    for r in rows:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    by_class = {}
    for r in rows:
        by_class.setdefault(r["receipt_class"], {"accepted": 0, "rejected": 0, "crashed": 0})
        o = r["full_observation"]
        by_class[r["receipt_class"]]["accepted" if o["accepted"] else ("crashed" if o["unstructured_crash"] else "rejected")] += 1
    doc = {"probe": "receipt classes (supplementary, plan-only)", "role": ns.role, "library_pin": {"package_digest_raw": inv["package_digest_raw"], "all_digest_raw": inv["all_digest_raw"]},
           "genuine_stem_reproduced_hash_identical": stem_ok, "genuine_receipt_pinned_sha256_lf": cf.GENUINE_RECEIPT_SHA256_LF, "genuine_receipt_found_and_pinned": genuine_ok, "skipped": skipped,
           "children_started": run.children, "outcomes": outcomes, "acceptance_by_class": by_class, "deny_controls": ctl["status"],
           "trust_statement": "Acceptance of ANY class is a mechanical consistency result only. Nothing here authenticates who produced or approved a receipt; GENUINE and SELF_CONSISTENT_FORGERY are indistinguishable to the library by design.",
           "rows": rows}
    out.write_json("receipt_classes.json", doc)
    lines = [f"receipt-class probe, role={ns.role}, children={run.children}, deny controls={ctl['status']}", "outcomes: " + json.dumps(outcomes, sort_keys=True), "acceptance by class: " + json.dumps(by_class, sort_keys=True)]
    for r in rows:
        mark = "ok " if r["outcome"] in ("PASS", "OBSERVED_OPEN_GAP") else "!! "
        lines.append(f"{mark}{r['id']:<28} {r['receipt_class']:<24} {r['outcome']:<18} observed={json.dumps(r['observed'], ensure_ascii=False)[:150]}")
    lines += [f"SKIPPED: {s}" for s in skipped]
    text = "\n".join(lines) + "\n"
    out.write("SUMMARY.txt", text.encode("utf-8"))
    sys.stdout.write(text.encode(sys.stdout.encoding or "utf-8", "backslashreplace").decode(sys.stdout.encoding or "utf-8", "replace"))
    return 0 if all(r["outcome"] in ("PASS", "OBSERVED_OPEN_GAP") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
