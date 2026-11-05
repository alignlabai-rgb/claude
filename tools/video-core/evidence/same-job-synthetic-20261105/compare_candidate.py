#!/usr/bin/env python3
"""Same-job pin table + caller-candidate (55ce8bc, UNREVIEWED v0, unchanged) comparison, computed ONLY from the recorded evidence in run/ (no media, no process, no network).

    python3 compare_candidate.py --out comparison.json        (--out is created exclusively)

Every producer artefact is read unmodified. The owner pins are file-byte SHA-256 values recorded by generate_chain.py with hashlib AND sha256sum (hashes.json) or hashes of the recorded
producer files themselves, NOT copies of a receipt's claim. The candidate's expected snapshot and identity envelope are SYNTHETIC TEST ASSERTIONS (no human adoption, voice qualification,
authenticity or execution permission); the exit codes are the REAL recorded exit codes of the steps. The candidate and its v0 profile are not changed or relaxed.
"""

import argparse
import copy
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(HERE, "run")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "caller-reference-20261105"))
import caller_admission as ca  # noqa: E402


def raw(rel):
    with open(os.path.join(RUN, rel), "rb") as fh:
        return fh.read()


def J(rel):
    return json.loads(raw(rel))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def step(name):
    for f in sorted(os.listdir(os.path.join(RUN, "commands"))):
        if f.endswith(f"-{name}.cmd.json"):
            return J("commands/" + f)
    raise KeyError(name)


H = J("hashes.json")
AM, MM = J("inputs/audio.manifest.json"), J("inputs/mux.manifest.json")
APLAN_STDOUT, AREC, VREC = J("receipts/audio.plan.stdout.json"), J("receipts/audio.receipt.json"), J("receipts/video.receipt.json")
MREC, MPLAN, VPLAN, APLAN = J("receipts/mux.receipt.json"), J("receipts/mux.plan.canonical.json"), J("receipts/video.plan.canonical.json"), J("receipts/audio.plan.canonical.json")
FF = {n: J(f"media-facts/{n}.ffprobe.json") for n in ("output.mp4", "stem.wav", "output.mov")}
ST = lambda d, k: next(s for s in d["streams"] if s["codec_type"] == k)  # noqa: E731


def same_job_table():
    rows = []

    def eq(name, values):
        rows.append({"name": name, "values": {k: v for k, v in values}, "all_equal": len({json.dumps(v) for _, v in values}) == 1})
    mp4_out = next(o for o in VREC["outputs"] if o["role"] == "video")
    stem_out = next(o for o in AREC["outputs"] if o["role"] == "stem")
    eq("audio plan hash", [("plan.canonical.json file bytes", sha(raw("receipts/audio.plan.canonical.json"))), ("plan CLI stdout plan_sha256", APLAN_STDOUT["plan_sha256"]), ("audio receipt plan_sha256", AREC["plan_sha256"]),
                           ("audio receipt outputs[plan].sha256", next(o for o in AREC["outputs"] if o["role"] == "plan")["sha256"])])
    eq("audio manifest hash", [("manifest file bytes", sha(raw("inputs/audio.manifest.json"))), ("audio receipt input.manifest_sha256", AREC["input"]["manifest_sha256"])])
    eq("stem bytes", [("stem.wav file bytes (hashlib == sha256sum)", H["stem.wav"]["sha256"]), ("audio receipt outputs[stem]", stem_out["sha256"]), ("mux manifest audio.sha256", MM["audio"]["sha256"]),
                      ("mux receipt input.audio.sha256_before", MREC["input"]["audio"]["sha256_before"]), ("mux receipt input.audio.sha256_after", MREC["input"]["audio"]["sha256_after"])])
    eq("audio receipt bytes", [("audio.receipt.json file bytes", sha(raw("receipts/audio.receipt.json"))), ("mux manifest audio.receipt.sha256", MM["audio"]["receipt"]["sha256"])])
    eq("video bytes", [("output.mp4 file bytes (hashlib == sha256sum)", H["output.mp4"]["sha256"]), ("video receipt outputs[video]", mp4_out["sha256"]), ("mux manifest video.sha256", MM["video"]["sha256"]),
                       ("mux receipt input.video.sha256_before", MREC["input"]["video"]["sha256_before"]), ("mux receipt input.video.sha256_after", MREC["input"]["video"]["sha256_after"])])
    eq("video plan hash", [("video plan file bytes", sha(raw("receipts/video.plan.canonical.json"))), ("video receipt plan_sha256", VREC["plan_sha256"]), ("video receipt outputs[plan]", next(o for o in VREC["outputs"] if o["role"] == "plan")["sha256"])])
    eq("frames", [("audio manifest timeline", AM["timeline"]["total_frames"]), ("audio plan timeline", APLAN["timeline"]["total_frames"]), ("video plan episode", VPLAN["expected_readback"]["frames"]),
                  ("video receipt decoded_frame_count expected", next(c for c in VREC["readback"]["comparison"]["checks"] if c["name"] == "decoded_frame_count")["expected"]),
                  ("mux manifest declared total_frames", MM["video"]["declared"]["total_frames"]), ("ffprobe output.mp4 nb_frames", int(ST(FF["output.mp4"], "video")["nb_frames"])),
                  ("ffprobe output.mov video nb_frames", int(ST(FF["output.mov"], "video")["nb_frames"]))])
    eq("samples", [("audio manifest source_out_sample", AM["events"][0]["source_out_sample"]), ("audio plan total_samples", APLAN["timeline"]["total_samples"]), ("audio receipt sample_count", AREC["readback"]["wav"]["sample_count"]),
                   ("mux manifest declared sample_count", MM["audio"]["declared"]["sample_count"]), ("ffprobe output.mov audio nb_frames", int(ST(FF["output.mov"], "audio")["nb_frames"]))])
    eq("geometry", [("episode canvas", [VPLAN["expected_readback"]["width"], VPLAN["expected_readback"]["height"]]), ("mux declared", [MM["video"]["declared"]["width"], MM["video"]["declared"]["height"]]),
                    ("ffprobe output.mp4", [ST(FF["output.mp4"], "video")["width"], ST(FF["output.mp4"], "video")["height"]]), ("ffprobe output.mov", [ST(FF["output.mov"], "video")["width"], ST(FF["output.mov"], "video")["height"]])])
    eq("duration (s)", [("ffprobe output.mp4", ST(FF["output.mp4"], "video")["duration"]), ("ffprobe stem.wav", ST(FF["stem.wav"], "audio")["duration"]), ("ffprobe output.mov video", ST(FF["output.mov"], "video")["duration"]),
                        ("ffprobe output.mov audio", ST(FF["output.mov"], "audio")["duration"])])
    eq("selection ref", [("mux manifest selection.ref", MM["selection"]["ref"]), ("mux receipt selection.ref", MREC["selection"]["ref"])])
    return rows


def packets():
    sel = MM["selection"]["ref"]
    ident = {"job_id": "synthetic-same-job-job", "work_id": "synthetic-same-job-work", "shot_id": "synthetic-same-job-shot", "attempt_id": "synthetic-same-job-attempt-1", "selection_snapshot_ref": sel}
    pins = {"manifest_sha256": sha(raw("inputs/audio.manifest.json")), "plan_sha256": sha(raw("receipts/audio.plan.canonical.json")), "receipt_sha256": sha(raw("receipts/audio.receipt.json")),
            "stem_sha256": H["stem.wav"]["sha256"], "video_sha256": H["output.mp4"]["sha256"]}
    ev = AM["events"][0]
    rows = [{"row_id": ev["id"], "selected": True, "state": ev["state"], "binding_ref": ev["provenance_ref"]}]
    expected = {"snapshot_ref": "synthetic-owner-snapshot", "identity": ident, "pins_source": "owner_record", "pins": pins, "rows": rows}
    code = lambda n: step(n)["exit_code"]  # noqa: E731
    observed = {"stages": {
        "audio_plan": {"identity": ident, "exit_code": code("audiomix-plan"), "response": copy.deepcopy(APLAN_STDOUT)},
        "audio_receipt": {"identity": ident, "exit_code": code("audiomix-mix"), "receipt_sha256": pins["receipt_sha256"], "response": copy.deepcopy(AREC)},
        "video_receipt": {"identity": ident, "exit_code": code("video-render"), "response": copy.deepcopy(VREC)},
        "avmux_manifest_view": {"identity": ident, "selection_ref": MM["selection"]["ref"], "video_sha256": MM["video"]["sha256"], "audio_sha256": MM["audio"]["sha256"]}}}
    return expected, observed


def summarize(res):
    return {"status": res["status"], "reasons": [{"severity": r["severity"], "code": r["code"], "path": r["path"]} for r in res["reasons"]], "checks": {c["id"]: c["outcome"] for c in res["checks"]},
            "pins_compared": {k: len(v) for k, v in res["evidence"].get("pins_compared", {}).items()}, "selected_scope": res["evidence"].get("selected_scope"), "warnings_preserved": res["evidence"].get("warnings_preserved"),
            "scope_of_status": res["scope_of_status"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ns = ap.parse_args()
    exp, obs = packets()
    before = copy.deepcopy((exp, obs))
    positive = ca.evaluate(exp, obs, ca.PROFILE_V0_UNREVIEWED)
    assert (exp, obs) == before
    # exactly ONE negative: the owner's stem pin names a different, real artefact of the same job (the video file) -> every observed stem source must disagree
    neg_exp = copy.deepcopy(exp)
    neg_exp["pins"]["stem_sha256"] = H["output.mp4"]["sha256"]
    negative = ca.evaluate(neg_exp, obs, ca.PROFILE_V0_UNREVIEWED)
    table = same_job_table()
    out = {"schema": "same_job_comparison/1", "candidate": "evidence/caller-reference-20261105/caller_admission.py @ 55ce8bc (UNREVIEWED v0, unchanged)", "python": sys.version.split()[0],
           "synthetic_assertions": ["identity envelope (job/work/shot/attempt; the selection ref is the avmux manifest's own synthetic ref)", "owner snapshot rows incl. binding_ref = the manifest's synthetic provenance_ref",
                                    "pins_source = owner_record (declaration only)", "owner pins = file-byte SHA-256 values (hashes.json) and hashes of the recorded producer files"],
           "real_values_used": ["exit codes of the recorded steps", "audio plan CLI stdout (unmodified)", "audio receipt, video receipt (unmodified)", "mux manifest-derived view (mechanical)"],
           "same_job_pin_table": table, "all_same_job_rows_equal": all(r["all_equal"] for r in table),
           "candidate_positive": summarize(positive), "candidate_negative_stem_pin_replaced": summarize(negative),
           "not_compared_by_the_candidate": ["the avmux receipt / output.mov (the candidate has no avmux-receipt stage; the mux tie is in same_job_pin_table)", "timing qualification (GAP-06)", "creative approval"]}
    text = json.dumps(out, indent=1, sort_keys=True)
    if ns.out:
        with open(ns.out, "x", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(json.dumps({"all_same_job_rows_equal": out["all_same_job_rows_equal"], "positive": out["candidate_positive"]["status"], "positive_reasons": [r["code"] for r in out["candidate_positive"]["reasons"]],
                      "negative": out["candidate_negative_stem_pin_replaced"]["status"], "negative_reasons": [r["code"] for r in out["candidate_negative_stem_pin_replaced"]["reasons"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
