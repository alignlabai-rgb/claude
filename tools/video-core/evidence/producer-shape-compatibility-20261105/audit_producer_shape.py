#!/usr/bin/env python3
"""Producer-shape compatibility audit of the caller-admission reference candidate (55ce8bc) against producer JSON that is ALREADY committed. READ-ONLY and PURE:
it opens the committed JSON files, never modifies them, calls `caller_admission.evaluate` (a pure function) and prints / writes a result. No process, no network, no media, no fake tool.

    python3 audit_producer_shape.py [--out result.json]        # --out is created exclusively; nothing else is written

What is NOT done: no value of any producer file is rewritten to reach READY (identity, hash, duration, status stay as committed); no stage of one demonstration is silently merged into
another job; the 6 s video demonstration and the 3-frame / 0.1 s stem demonstration are different jobs and are only ever paired in cases that say so and are expected to fail.

Everything the producers do not carry is a SYNTHETIC ASSERTION made by this script and listed per case under `synthetic_assertions`:
  * the caller identity envelope (job / work / shot / attempt ids; only the selection snapshot ref of the avmux world is a committed value),
  * the CLI exit code (the committed files record none: 0 is asserted),
  * the owner's expected snapshot (rows, pins). Pins are computed from the committed FILE BYTES (or an independent committed record) so they are not copied from the receipt's own claim,
    except where no independent record exists (marked `receipt_own_value`), where the comparison is a tautology and is labelled as such,
  * the `audio_plan` response envelope {mode, status, plan_sha256, plan}: the committed file is the canonical plan only; the envelope is built MECHANICALLY (status = the plan's own status,
    plan_sha256 = SHA-256 of the committed file bytes) and no field of the plan is touched.
"""

import argparse
import copy
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.dirname(HERE)  # tools/video-core/evidence
sys.path.insert(0, os.path.join(EVID, "caller-reference-20261105"))
import caller_admission as ca  # noqa: E402

SOURCES = {
    "video_receipt_6s": "receipt.synthetic-6s.json",
    "video_plan_6s": "plan.canonical.json",
    "audio_receipt_stem": "stage-b-audio-stem/receipt.stem-demo.json",
    "audio_plan_stem": "stage-b-audio-stem/plan.canonical.json",
    "audio_manifest_stem": "stage-b-audio-stem/manifest.synthetic.json",
    "audio_stem_independent": "stage-b-audio-stem/independent_verification.json",
    "avmux_manifest_mov": "avmux-synthetic/mov.manifest.json",
    "avmux_receipt_mov": "avmux-synthetic/mov.receipt.json",
}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_blob(b: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()  # what `git hash-object` prints; computed here without running git


RAW = {}
for _name, _rel in SOURCES.items():
    with open(os.path.join(EVID, _rel), "rb") as _fh:
        RAW[_name] = _fh.read()
DOC = {k: json.loads(v) for k, v in RAW.items()}


def source_table():
    return [{"name": k, "path": "tools/video-core/evidence/" + SOURCES[k], "bytes": len(RAW[k]), "sha256": sha256(RAW[k]), "git_blob": git_blob(RAW[k])} for k in SOURCES]


def ident(tag: str, selection_ref: str = None) -> dict:
    return {"job_id": f"synthetic-job-{tag}", "work_id": f"synthetic-work-{tag}", "shot_id": f"synthetic-shot-{tag}", "attempt_id": f"synthetic-attempt-{tag}",
            "selection_snapshot_ref": selection_ref or f"synthetic-selection-{tag}"}


AVMUX_SELECTION_REF = DOC["avmux_manifest_mov"]["selection"]["ref"]  # committed value
PLACEHOLDER = lambda label: sha256(("synthetic-placeholder-not-applicable:" + label).encode())  # noqa: E731

PLAN_FILE_SHA_AUDIO = sha256(RAW["audio_plan_stem"])
PLAN_FILE_SHA_VIDEO = sha256(RAW["video_plan_6s"])
PIN_AUDIO = {"manifest_sha256": sha256(RAW["audio_manifest_stem"]), "plan_sha256": PLAN_FILE_SHA_AUDIO, "receipt_sha256": sha256(RAW["audio_receipt_stem"]),
             "stem_sha256": DOC["audio_stem_independent"]["sha256"], "video_sha256": DOC["avmux_manifest_mov"]["video"]["sha256"]}
VIDEO_OUT = next(o for o in DOC["video_receipt_6s"]["outputs"] if o["role"] == "video")
PIN_VIDEO = {"manifest_sha256": PLACEHOLDER("manifest"), "plan_sha256": PLAN_FILE_SHA_VIDEO, "receipt_sha256": PLACEHOLDER("audio-receipt"), "stem_sha256": PLACEHOLDER("stem"),
             "video_sha256": VIDEO_OUT["sha256"]}


def audio_plan_packet(identity: dict, plan_key="audio_plan_stem") -> dict:
    plan = copy.deepcopy(DOC[plan_key])
    resp = {"mode": "plan", "status": plan["status"], "plan_sha256": sha256(RAW[plan_key]), "plan": plan}
    return {"identity": identity, "exit_code": 0, "response": resp}


def audio_receipt_packet(identity: dict) -> dict:
    return {"identity": identity, "exit_code": 0, "receipt_sha256": sha256(RAW["audio_receipt_stem"]), "response": copy.deepcopy(DOC["audio_receipt_stem"])}


def video_receipt_packet(identity: dict) -> dict:
    return {"identity": identity, "exit_code": 0, "response": copy.deepcopy(DOC["video_receipt_6s"])}


def manifest_view(identity: dict) -> dict:
    m = DOC["avmux_manifest_mov"]
    return {"identity": identity, "selection_ref": m["selection"]["ref"], "video_sha256": m["video"]["sha256"], "audio_sha256": m["audio"]["sha256"]}


def audio_rows(binding: bool) -> list:
    rows = []
    for e in DOC["audio_plan_stem"]["events"]:
        r = {"row_id": e["id"], "selected": e["selected"], "state": e["state"]}
        if binding and e["selected"] and e["state"] == "USE":
            r["binding_ref"] = "synthetic-binding/" + e["id"]  # an owner ASSERTION; the committed plan carries no provenance_ref
        rows.append(r)
    return rows


def expected(identity: dict, pins: dict, rows: list) -> dict:
    return {"snapshot_ref": "synthetic-owner-snapshot", "identity": identity, "pins_source": "owner_record", "pins": dict(pins), "rows": rows}


GENERIC_ASSERT = ["identity envelope per stage packet (synthetic)", "exit_code 0 for every stage (the committed files record no exit code)", "pins_source=owner_record (declaration only)"]
ENVELOPE_ASSERT = "audio_plan response envelope built mechanically around the unmodified committed canonical plan (status = plan.status, plan_sha256 = SHA-256 of the file bytes)"


def run(exp, obs):
    r = ca.evaluate(exp, obs, ca.PROFILE_V0_UNREVIEWED)
    return {"status": r["status"], "reasons": [{"severity": x["severity"], "code": x["code"], "path": x["path"]} for x in r["reasons"]],
            "checks": {c["id"]: c["outcome"] for c in r["checks"]}, "unselected_unresolved": r["evidence"].get("unselected_unresolved"),
            "pins_compared": {k: len(v) for k, v in r["evidence"].get("pins_compared", {}).items()}}


def build_cases():
    A, B = ident("A", AVMUX_SELECTION_REF), ident("B")
    cases = []

    def add(cid, title, uses, extra_assert, exp, obs, shows):
        cases.append({"id": cid, "title": title, "sources_used": uses, "synthetic_assertions": GENERIC_ASSERT + extra_assert, "shows": shows, **run(exp, obs)})

    # A1 - real audio plan + real audio receipt (Stage B). Only these two stages exist for this job; others are absent and stay HOLD.
    add("A1", "Stage-B audio plan + audio receipt (real shapes), owner binding not asserted",
        ["audio_plan_stem", "audio_manifest_stem", "audio_stem_independent", "audio_receipt_stem", "avmux_manifest_mov"],
        [ENVELOPE_ASSERT, "pins: manifest/plan/receipt from file bytes, stem from the independent verification record, video from the avmux manifest of the 3-frame clip"],
        expected(A, PIN_AUDIO, audio_rows(False)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A)}},
        "real plan and receipt shapes reach AUDIO_PLAN and AUDIO_RECEIPT and pass them; the plan carries no provenance_ref, so a v0 USE row has no binding (BINDING_UNRESOLVED); video receipt / manifest view are absent (HOLD)")
    add("A2", "same, with an owner binding_ref asserted for the two USE rows",
        ["audio_plan_stem", "audio_manifest_stem", "audio_stem_independent", "audio_receipt_stem", "avmux_manifest_mov"],
        [ENVELOPE_ASSERT, "binding_ref per selected USE row (owner assertion; the plan has no provenance_ref to match it)"],
        expected(A, PIN_AUDIO, audio_rows(True)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A)}},
        "an asserted binding cannot be proven from the committed plan shape (BINDING_UNPROVEN): the v0 binding requirement is not satisfiable from this producer output; the profile is not relaxed")
    # V1 - real 6 s video render receipt only
    add("V1", "6 s video render receipt (real shape) only", ["video_receipt_6s", "video_plan_6s"],
        ["owner pins manifest / receipt / stem are placeholders (no such artefact in this job); video pin = the receipt's own output sha256 (receipt_own_value: no independent record is committed, so that comparison is a tautology)"],
        expected(A, PIN_VIDEO, []), {"stages": {"video_receipt": video_receipt_packet(A)}},
        "the real video receipt reaches VIDEO_RECEIPT and passes it (checks, flags, outputs, plan output sha == plan_sha256); everything else is absent (HOLD)")
    # M1 - avmux manifest view of the 3-frame clip + stem (committed avmux manifest values)
    add("M1", "avmux manifest view (committed mov.manifest.json) + Stage-B stem receipt/plan",
        ["avmux_manifest_mov", "audio_plan_stem", "audio_receipt_stem", "audio_manifest_stem", "audio_stem_independent"],
        [ENVELOPE_ASSERT, "manifest view derived mechanically from mov.manifest.json: selection.ref, video.sha256, audio.sha256"],
        expected(A, PIN_AUDIO, audio_rows(False)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A), "avmux_manifest_view": manifest_view(A)}},
        "MANIFEST_VIEW passes and the owner stem pin is compared with two observed sources (the receipt stem output and the manifest audio sha256); there is NO video render receipt for the 3-frame clip, so the job stays HOLD")
    # X1/X2 - the two different demonstrations are never one job
    add("X1", "6 s video receipt + Stage-B audio plan/receipt as DIFFERENT jobs (identities differ)",
        ["video_receipt_6s", "audio_plan_stem", "audio_receipt_stem"], ["video packet identity = job B, audio packets identity = job A (they are different demonstrations)"],
        expected(A, PIN_AUDIO, audio_rows(False)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A), "video_receipt": video_receipt_packet(B)}},
        "the 6 s video packet cannot be attributed to the audio job: IDENTITY_MISMATCH")
    add("X2", "6 s video receipt + Stage-B audio forced under ONE asserted identity (negative control only)",
        ["video_receipt_6s", "audio_plan_stem", "audio_receipt_stem"], ["a single identity asserted for all packets ONLY to show that the geometry still refuses the pairing; this is not a claim that they are one job"],
        expected(A, PIN_AUDIO, audio_rows(False)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A), "video_receipt": video_receipt_packet(A)}},
        "even under a forged common identity the 180-frame video and the 3-frame audio timeline cannot be reconciled (COUNT_MISMATCH) and the video pin differs (PIN_MISMATCH)")
    # X3 - the avmux world as committed has no video render receipt
    add("X3", "avmux world as committed: Stage-B stem + 3-frame clip manifest, no video render receipt",
        ["avmux_manifest_mov", "avmux_receipt_mov", "audio_plan_stem", "audio_receipt_stem"], [ENVELOPE_ASSERT],
        expected(A, PIN_AUDIO, audio_rows(False)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A), "avmux_manifest_view": manifest_view(A)}},
        "the committed avmux evidence is an avmux receipt (a different schema); no `video_core.receipt/1` for the 3-frame clip exists, so REQUIRED_RECEIPT_ABSENT: HOLD stays HOLD")
    # S1/S2 - stage packet substitutions with the wrong committed artefact
    add("S1", "avmux receipt substituted into the audio_receipt slot", ["avmux_receipt_mov", "audio_plan_stem"], [ENVELOPE_ASSERT],
        expected(A, PIN_AUDIO, audio_rows(False)),
        {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": {"identity": A, "exit_code": 0, "receipt_sha256": sha256(RAW["avmux_receipt_mov"]), "response": copy.deepcopy(DOC["avmux_receipt_mov"])}}},
        "a `video_core.avmux_receipt/1` is not an audio receipt: unknown schema and missing audio-receipt fields; never READY")
    vp = copy.deepcopy(DOC["video_plan_6s"])
    add("S2", "6 s video canonical plan substituted into the audio_plan slot", ["video_plan_6s", "audio_receipt_stem"], ["audio_plan response = {plan_sha256 of the file, plan} (the video plan has no status field to copy)"],
        expected(A, PIN_AUDIO, audio_rows(False)),
        {"stages": {"audio_plan": {"identity": A, "exit_code": 0, "response": {"plan_sha256": PLAN_FILE_SHA_VIDEO, "plan": vp}}, "audio_receipt": audio_receipt_packet(A)}},
        "a `video_core.plan/1` is not an audio plan: the audio-plan comparison is reached and fails on schema / status / shape")
    # P1 - owner pin for the plan names the OTHER demonstration's plan
    wrong = dict(PIN_AUDIO, plan_sha256=PLAN_FILE_SHA_VIDEO)
    add("P1", "Stage-B audio packets against an owner plan pin that is the 6 s video plan's hash", ["audio_plan_stem", "audio_receipt_stem", "video_plan_6s"],
        [ENVELOPE_ASSERT, "owner plan pin = SHA-256 of the 6 s video plan file"],
        expected(A, wrong, audio_rows(False)), {"stages": {"audio_plan": audio_plan_packet(A), "audio_receipt": audio_receipt_packet(A)}},
        "the real audio plan hash, the receipt's plan_sha256 and its plan output all disagree with that pin: PIN_MISMATCH at each source")
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ns = ap.parse_args()
    cases = build_cases()
    result = {"schema": "producer_shape_compatibility/1", "candidate": "evidence/caller-reference-20261105/caller_admission.py (UNREVIEWED v0)",
              "python": sys.version.split()[0], "sources": source_table(), "cases": cases,
              "ready_cases": [c["id"] for c in cases if c["status"] == "READY"],
              "note": "no case is READY: the committed evidence contains no render receipt for the 3-frame clip, and the committed audio plan carries no binding reference; neither was invented"}
    text = json.dumps(result, indent=1, sort_keys=True)
    if ns.out:
        with open(ns.out, "x", encoding="utf-8") as fh:
            fh.write(text + "\n")
    for c in cases:
        print(f"{c['id']:3} {c['status']:9} " + ",".join(sorted({r['code'] for r in c['reasons']})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
