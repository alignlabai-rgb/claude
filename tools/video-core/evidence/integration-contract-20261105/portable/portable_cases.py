"""Portable contract cases (Python 3.11+ stdlib only). Read-only: real validate / plan / srt / dry-run CLI behaviour; nothing is rendered, mixed or muxed.

Expected values are INDEPENDENT of the library code: exact-rational oracles (fractions), independently assembled SRT bytes, Python's own `wave` module,
the original spec's published example hashes, filesystem probes, and the documented rules in SPEC/USAGE/AUDIO_SPEC/AVMUX_SPEC.
Layers: LIBRARY_ENFORCED (library must do it) / LIBRARY_PERMISSIVE (OBSERVED OPEN GAP of the pinned source; never a pass) / CALLER_GUARD_DEMO (illustrative owner guard).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import struct
import wave
from fractions import Fraction

from portable_core import World, sha256_bytes

VID, AUD, AVM = "video_core", "video_core.audiomix", "video_core.avmux"
RATE = 48_000
N, D, FRAMES = 30000, 1001, 3

CASES: list = []


def case(case_id, title, refs=None):
    def deco(fn):
        fn.case_id, fn.title, fn.refs = case_id, title, refs or []
        CASES.append(fn)
        return fn
    return deco


# ------------------------------------------------------------------------------------------------------------------------------- oracles

def o_sample(frame, n, d):
    """Exact rational boundary in 48 kHz samples, round half up (AUDIO_SPEC 'one documented rounding policy')."""
    return math.floor(Fraction(frame * d * RATE, n) + Fraction(1, 2))


def o_ms(frame, n, d):
    return math.floor(Fraction(frame * 1000 * d, n) + Fraction(1, 2))


def o_srt_ts(ms):
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, milli = divmod(r, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, milli)


def o_srt(captions, n, d):
    """captions: [(index, start_frame, end_frame, text)] -> expected SRT bytes (UTF-8, LF, no BOM, ordered by (start,end,index), numbered from 1)."""
    blocks = []
    for k, (idx, s, e, text) in enumerate(sorted(captions, key=lambda c: (c[1], c[2], c[0])), start=1):
        blocks.append("%d\n%s --> %s\n%s" % (k, o_srt_ts(o_ms(s, n, d)), o_srt_ts(o_ms(e, n, d)), text))
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def o_ratio(num, den):
    f = Fraction(num, den)
    return "%d/%d" % (f.numerator, f.denominator)


# ------------------------------------------------------------------------------------------------------------------------------- fixtures

def tone(kind, freq, n, amp=1000):
    p = RATE // freq
    out = []
    for i in range(n):
        ph = i % p
        if kind == "square":
            out.append(amp if ph < p // 2 else -amp)
        elif kind == "triangle":
            h = p // 2
            out.append(-amp + (2 * amp * ph) // h if ph < h else amp - (2 * amp * (ph - h)) // h)
        elif kind == "saw":
            out.append(-amp + (2 * amp * ph) // p)
        else:
            out.append(0)
    return out


def wav_bytes(samples):
    pcm = b"".join(struct.pack("<h", v) for v in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def placeholder(tag):
    """NOT media: plain bytes with a media-looking extension. Caller-declared video metadata stays UNVERIFIED."""
    return (f"SYNTHETIC-PLACEHOLDER-NOT-MEDIA {tag}\n" * 4).encode("ascii")


def fixture_bytes():
    return {"seg_a.wav": wav_bytes(tone("square", 480, 6000)), "stem.wav": wav_bytes(tone("triangle", 600, 4805)), "stem_silent.wav": wav_bytes(tone("zero", 600, 4805)),
            "clip_a.mp4": placeholder("clip_a v1"), "clip_a_v2.mp4": placeholder("clip_a v2"), "proxy_clip_a.mp4": placeholder("proxy-of-clip_a"), "video_ok.mp4": placeholder("video_ok")}


FB = fixture_bytes()
H = {k: sha256_bytes(v) for k, v in FB.items()}


def put_fixtures(w: World, names=None):
    for k, v in FB.items():
        if names is None or k in names:
            w.put(k, v)


def episode(file_clip="clip_a.mp4"):
    return {"version": 1, "fps": {"n": N, "d": D}, "canvas": {"width": 320, "height": 180}, "total_frames": FRAMES,
            "clips": [{"id": "clip_a", "start_frame": 0, "duration_frames": 2, "source": {"kind": "file", "path": file_clip}},
                      {"id": "clip_b", "start_frame": 2, "duration_frames": 1, "source": {"kind": "color", "value": "#30506a"}}], "captions": []}


def audio_manifest(asset_path="seg_a.wav", asset_sha=None):
    return {"manifest_version": 1, "timeline": {"fps": {"n": N, "d": D}, "total_frames": FRAMES}, "output": {"sample_rate": 48000, "channels": 1, "bits": 16},
            "scope": {"event_ids": ["ev_vo_1", "ev_sil_2", "ev_vo_3"]}, "policy": {"gap": "REQUIRE_FULL_COVERAGE", "overlap": "REJECT", "clipping": "REJECT"},
            "assets": [{"id": "asset_a", "path": asset_path, "sha256": asset_sha or H["seg_a.wav"]}, {"id": "asset_unselected", "path": "seg_unselected.wav", "sha256": "0" * 64}],
            "events": [
                {"id": "ev_vo_1", "source_id": "src_syn_1", "state": "USE", "role": "vo", "decision_ref": "owner-binding/synthetic-001", "start_frame": 0, "end_frame": 1, "asset": "asset_a", "source_in_sample": 1000, "source_out_sample": 2602},
                {"id": "ev_sil_2", "source_id": "src_syn_2", "state": "INTENTIONAL_SILENCE", "decision_ref": "owner-decision/silence-001", "start_frame": 1, "end_frame": 2},
                {"id": "ev_vo_3", "source_id": "src_syn_3", "state": "USE", "role": "vo", "decision_ref": "owner-binding/synthetic-003", "start_frame": 2, "end_frame": 3, "asset": "asset_a", "source_in_sample": 4000, "source_out_sample": 5602},
                {"id": "ev_x_missing", "source_id": "src_syn_x1", "state": "MISSING"}, {"id": "ev_x_unknown", "source_id": "src_syn_x2", "state": "UNKNOWN"}, {"id": "ev_x_proxy", "source_id": "src_syn_x3", "state": "PROXY"},
                {"id": "ev_x_use_unselected", "source_id": "src_syn_x4", "state": "USE", "start_frame": 0, "end_frame": 1, "asset": "asset_unselected", "source_in_sample": 0, "source_out_sample": 1602}]}


def receipt_fabricated(stem_sha=None):
    """HARNESS-FABRICATED (no mix was run). Shape mirrors a genuine recorded receipt (checked against it in PC08 when available)."""
    s = stem_sha or H["stem.wav"]
    return {"_fixture_note": "HARNESS-FABRICATED, not produced by audiomix mix", "schema": "video_core.audio_receipt/1", "status": "MIXED_VERIFIED", "written": True, "stage": "complete",
            "quality_evaluated": False, "plan_sha256": "f" * 64, "input": {"manifest_sha256": "e" * 64, "manifest_bytes": 1, "inputs_unchanged": True, "assets": []},
            "warnings": [], "unresolved_report": [], "outputs": [{"name": "stem.wav", "role": "stem", "bytes": len(FB["stem.wav"]), "sha256": s, "partial": False}],
            "readback": {"all_ok": True, "checks": [], "wav": {"sample_rate": 48000, "channels": 1, "bits": 16, "sample_count": 4805, "sha256": s}}}


def jbytes(obj):
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def avmux_manifest(w: World, route="MOV_PCM_S16LE", receipt="default", video="video_ok.mp4", audio="stem.wav", receipt_name="audio_receipt.json", mut=None, name="av.json"):
    """Write an AV manifest (+ receipt) into the world; returns the manifest dict. receipt: 'default' | None | dict | bytes."""
    man = {"avmux_version": 1, "route": route, "selection": {"ref": "synthetic-selection/0001"},
           "video": {"state": "USE", "path": video, "sha256": H["video_ok.mp4"], "declared": {"codec": "h264", "width": 320, "height": 180, "fps": {"n": N, "d": D}, "total_frames": FRAMES}},
           "audio": {"state": "USE", "path": audio, "sha256": H["stem.wav"], "declared": {"sample_rate": 48000, "channels": 1, "sample_count": o_sample(FRAMES, N, D)}}}
    if receipt is not None:
        data = jbytes(receipt_fabricated()) if receipt == "default" else (receipt if isinstance(receipt, bytes) else jbytes(receipt))
        w.put(receipt_name, data)
        man["audio"]["receipt"] = {"path": receipt_name, "sha256": sha256_bytes(data)}
    if mut:
        mut(man)
    w.put_json(name, man)
    return man


# ------------------------------------------------------------------------------------------------------------------------------- caller-side illustrations

def classify_outcome(module, verb, exit_code, doc, stdout_empty):
    """CALLER_GUARD_DEMO: how an owner must read a result. Exit code alone cannot separate these."""
    if exit_code != 0:
        if stdout_empty:
            return "REJECTED_UNSTRUCTURED"
        return "HOLD" if (doc or {}).get("status") == "HOLD" else "REJECTED"
    doc = doc or {}
    if module == AUD:
        status = doc.get("status") or (doc.get("plan") or {}).get("status")
        if status == "HOLD":
            return "HOLD"
        if doc.get("hold_if_planned"):
            return "HOLD_IF_PLANNED"
        if status == "READY" and (doc.get("plan") or {}).get("warnings"):
            return "READY_WITH_WARNINGS"
        return "READY"
    if module == AVM and str(doc.get("readiness", "")).startswith("UNVERIFIED"):
        return "UNVERIFIED"
    return "PLANNED"


def guard_receipt_binding(receipt, *, stem_sha256, audio_plan_sha256, manifest_sha256):
    r = []
    if receipt.get("plan_sha256") != audio_plan_sha256:
        r.append("plan_sha256 is not the plan the owner ran")
    if (receipt.get("input") or {}).get("manifest_sha256") != manifest_sha256:
        r.append("manifest_sha256 is not the manifest the owner submitted")
    return {"verdict": "REJECT" if r else "PROCEED", "reasons": r}


def guard_silence(plan_doc):
    warnings = [w["code"] for w in plan_doc["plan"]["warnings"]]
    return "HOLD" if warnings else "PROCEED"


# ------------------------------------------------------------------------------------------------------------------------------- helpers

def hold_codes(r):
    return [h["code"] for h in r.json["plan"]["hold"]]


def finish_world_checks(ctx, w: World, before: dict, name="plan/validate/dry-run paths created, changed and removed nothing"):
    ctx.enforced(name, before, w.snapshot())


# ------------------------------------------------------------------------------------------------------------------------------- cases

@case("PC00_fixtures_independent_check", "Generated fixtures are well-formed (checked with Python's own `wave`) and equal the original harness's pinned bytes when available",
      ["tools/video-core/evidence/integration-contract-20261105/fixtures/FIXTURES_INDEX.json"])
def pc00(ctx):
    import io
    for name, frames in (("seg_a.wav", 6000), ("stem.wav", 4805), ("stem_silent.wav", 4805)):
        with wave.open(io.BytesIO(FB[name]), "rb") as wf:
            ctx.control(f"{name}: wave module sees mono / 16-bit / 48000 Hz / {frames} frames", (1, 2, 48000, frames), (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()))
    ctx.control("stem_silent.wav PCM is all zeros", True, FB["stem_silent.wav"][44:] == b"\x00" * (4805 * 2))
    ctx.control("the 'videos' are plain non-media bytes", True, all(v.startswith(b"SYNTHETIC-PLACEHOLDER-NOT-MEDIA") for k, v in FB.items() if k.endswith(".mp4")))
    idx = os.path.join(ctx.run.library_root, "evidence", "integration-contract-20261105", "fixtures", "FIXTURES_INDEX.json")
    if not os.path.isfile(idx):
        idx = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "FIXTURES_INDEX.json")
    if os.path.isfile(idx):
        with open(idx, "rb") as fh:
            pinned = json.loads(fh.read().decode("utf-8"))["bytes"]
        ctx.control("bytes equal the original (POSIX) harness's pinned fixture hashes", {k: pinned[k] for k in H if k in pinned}, {k: H[k] for k in H if k in pinned})
    else:
        ctx.skip("cross-check with the original harness's FIXTURES_INDEX.json", "file not found next to the harness")
    ctx.control("oracle: boundaries 0..3 @ 30000/1001 are 0,1602,3203,4805 and 10000 frames are 16,016,000 (no per-frame accumulation)", ([0, 1602, 3203, 4805], 16_016_000),
                ([o_sample(f, N, D) for f in range(4)], o_sample(10000, N, D)))


@case("PC01_validation_vs_execution", "validate reads no media; plan checks what it can read; dry-runs (no execution flag) create nothing",
      ["video_core/cli.py:_cmd_validate", "video_core/audiomix/cli.py:_cmd_validate", "video_core/avmux/cli.py:_cmd_validate"])
def pc01(ctx):
    run = ctx.run
    # ---- video-only
    w = run.new_world("pc01v")
    w.put_json("ep.json", episode())
    before = w.snapshot()
    r = ctx.call(w, VID, ["validate", "{docs}/ep.json"], "video validate, no asset root")
    ctx.enforced("video validate without --asset-root resolves no file: ok, 0 resolved, 1 unresolved", (0, True, 0, 1), (r.exit_code, r.json["ok"], r.json["file_sources_resolved"], r.json["file_sources_unresolved"]))
    r = ctx.call(w, VID, ["validate", "{docs}/ep.json", "--asset-root", "{assets}"], "video validate, asset root, file absent")
    ctx.enforced("video validate with an asset root and the file absent is rejected", (1, ["asset_missing"]), (r.exit_code, r.issue_codes))
    w.put("clip_a.mp4", FB["clip_a.mp4"])
    before = w.snapshot()
    r = ctx.call(w, VID, ["validate", "{docs}/ep.json", "--asset-root", "{assets}"], "video validate, file present")
    ctx.enforced("video validate with the file present resolves it", (0, 1), (r.exit_code, r.json["file_sources_resolved"]))
    r = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan")
    ctx.enforced("video plan reports itself side-effect free", (0, "plan", True), (r.exit_code, r.json["mode"], r.json["observations"]["side_effects"].startswith("none")))
    r = ctx.call(w, VID, ["render", "{docs}/ep.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-v"], "video render WITHOUT --render")
    ctx.enforced("render without the explicit flag is a dry run", (0, "dry_run"), (r.exit_code, r.json["mode"]))
    finish_world_checks(ctx, w, before)
    # ---- audio
    w = run.new_world("pc01a")
    w.put_json("am.json", audio_manifest())
    r = ctx.call(w, AUD, ["validate", "{docs}/am.json"], "audio validate, no asset files at all")
    ctx.enforced("audio validate reads no asset: ok with every asset file absent", (0, True), (r.exit_code, r.json["ok"]))
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, asset files absent")
    ctx.enforced("audio plan with the selected asset absent is HOLD ASSET_FILE_MISSING (not a rejection, not silence)", (5, "HOLD", ["ASSET_FILE_MISSING"]), (r.exit_code, r.json["status"], hold_codes(r)))
    w.put("seg_a.wav", FB["stem.wav"])           # a valid WAV, but not the pinned bytes
    r = ctx.call(w, AUD, ["validate", "{docs}/am.json"], "audio validate, wrong bytes present")
    v_ok = (r.exit_code, r.json["ok"])
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, wrong bytes present")
    ctx.enforced("same manifest: validate ok but plan rejects the changed source (sha256 pin)", ((0, True), (1, ["asset_sha256_mismatch"])), (v_ok, (r.exit_code, r.issue_codes)))
    w.put("seg_a.wav", FB["seg_a.wav"])
    before = w.snapshot()
    r = ctx.call(w, AUD, ["mix", "{docs}/am.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-a"], "audio mix WITHOUT --mix")
    ctx.enforced("mix without the explicit flag is a dry run on a READY plan", (0, "dry_run", "READY"), (r.exit_code, r.json["mode"], r.json["status"]))
    finish_world_checks(ctx, w, before)
    # ---- avmux
    w = run.new_world("pc01m")
    avmux_manifest(w)
    r = ctx.call(w, AVM, ["validate", "{docs}/av.json"], "avmux validate, no files at all")
    ctx.enforced("avmux validate reads nothing but the manifest", (0, True), (r.exit_code, r.json["ok"]))
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "avmux plan, files absent")
    ctx.enforced("avmux plan with files absent is a rejection", (1, ["asset_missing"]), (r.exit_code, r.issue_codes))
    put_fixtures(w)
    w.put("audio_receipt.json", jbytes(receipt_fabricated()))
    before = w.snapshot()
    r = ctx.call(w, AVM, ["mux", "{docs}/av.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-m"], "avmux mux WITHOUT --mux")
    ctx.enforced("mux without the explicit flag is a dry run", (0, "dry_run"), (r.exit_code, r.json["mode"]))
    finish_world_checks(ctx, w, before)


@case("PC02_status_distinctions_despite_exit0", "READY / HOLD / UNVERIFIED / REJECTED / REJECTED_UNSTRUCTURED are separable only from the payload; exit 0 covers five of them",
      ["video_core/audiomix/cli.py:_cmd_mix", "video_core/avmux/plan.py:prepare_plan"])
def pc02(ctx):
    run = ctx.run
    w = run.new_world("pc02")
    put_fixtures(w)
    base = audio_manifest()
    held = copy.deepcopy(base)
    held["scope"]["event_ids"].append("ev_x_unknown")
    zf = copy.deepcopy(base)
    zf["scope"]["event_ids"].remove("ev_sil_2")
    zf["policy"]["gap"] = "ALLOW_ZERO_FILL"
    bad = copy.deepcopy(base)
    bad["execution_permission"] = {"granted": True}
    w.put_json("ready.json", base)
    w.put_json("held.json", held)
    w.put_json("zf.json", zf)
    w.put_json("bad.json", bad)
    avmux_manifest(w)
    obs = {}
    obs["ready"] = ctx.call(w, AUD, ["plan", "{docs}/ready.json", "--asset-root", "{assets}"], "audio plan READY")
    obs["hold"] = ctx.call(w, AUD, ["plan", "{docs}/held.json", "--asset-root", "{assets}"], "audio plan HOLD")
    obs["dry_hold"] = ctx.call(w, AUD, ["mix", "{docs}/held.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "dry-h"], "audio mix dry-run of the HOLD manifest")
    obs["val_hold"] = ctx.call(w, AUD, ["validate", "{docs}/held.json"], "audio validate of the HOLD manifest")
    obs["zero_fill"] = ctx.call(w, AUD, ["plan", "{docs}/zf.json", "--asset-root", "{assets}"], "audio plan READY with zero-fill warning")
    obs["reject"] = ctx.call(w, AUD, ["plan", "{docs}/bad.json", "--asset-root", "{assets}"], "audio plan REJECTED (unknown field)")
    obs["av_plan"] = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "avmux plan")
    avmux_manifest(w, receipt=b"[]\n", name="av_bad.json")
    obs["av_unstructured"] = ctx.call(w, AVM, ["plan", "{docs}/av_bad.json", "--asset-root", "{assets}"], "avmux plan with a mis-shaped receipt")
    ctx.enforced("audio plan READY is exit 0 / READY / no warnings", (0, "READY", []), (obs["ready"].exit_code, obs["ready"].json["status"], obs["ready"].json["plan"]["warnings"]))
    ctx.enforced("audio plan with a selected UNKNOWN is exit 5 / HOLD", (5, "HOLD"), (obs["hold"].exit_code, obs["hold"].json["status"]))
    ctx.enforced("audio plan with an unknown manifest field is exit 1 and a structured rejection", (1, ["unknown_field"], False), (obs["reject"].exit_code, obs["reject"].issue_codes, obs["reject"].json["ok"]))
    ctx.open_gap("GAP-08", "mix dry-run of a HOLD plan exits 0 while the payload says HOLD", (0, "HOLD", "dry_run"), ctx.observe(lambda: (obs["dry_hold"].exit_code, obs["dry_hold"].json["status"], obs["dry_hold"].json["mode"])),
                 "a non-ready dry-run should be distinguishable without parsing the payload")
    ctx.open_gap("GAP-08", "audio validate with hold_if_planned exits 0", (0, ["SELECTED_UNRESOLVED"]), ctx.observe(lambda: (obs["val_hold"].exit_code, [h["code"] for h in obs["val_hold"].json["hold_if_planned"]])),
                 "validate should surface that the plan would be HOLD in its status/exit")
    ctx.open_gap("GAP-09/08", "READY with a zero-fill warning exits 0", (0, "READY", ["ZERO_FILL_APPLIED"]), ctx.observe(lambda: (obs["zero_fill"].exit_code, obs["zero_fill"].json["status"], [x["code"] for x in obs["zero_fill"].json["plan"]["warnings"]])),
                 "absent audio converted to zeros should not be READY without an explicit decision")
    ctx.open_gap("GAP-08", "avmux plan exits 0 and says UNVERIFIED", (0, True), ctx.observe(lambda: (obs["av_plan"].exit_code, obs["av_plan"].json["readiness"].startswith("UNVERIFIED"))),
                 "an unverified plan should not look like a verified one by exit code")
    ctx.open_gap("GAP-14", "avmux plan with a mis-shaped receipt: exit 1, empty stdout, uncaught AttributeError", (1, True, True),
                 ctx.observe(lambda: (obs["av_unstructured"].exit_code, obs["av_unstructured"].stdout_bytes.strip() == b"", "AttributeError" in obs["av_unstructured"].stderr_text)),
                 "a structured audio_receipt_* rejection instead of a crash")
    labels = {k: classify_outcome(AVM if k.startswith("av") else AUD, "x", v.exit_code, v.json, v.stdout_bytes.strip() == b"") for k, v in obs.items()}
    ctx.guard("owner-side reading of each outcome", {"ready": "READY", "hold": "HOLD", "dry_hold": "HOLD", "val_hold": "HOLD_IF_PLANNED", "zero_fill": "READY_WITH_WARNINGS", "reject": "REJECTED",
                                                    "av_plan": "UNVERIFIED", "av_unstructured": "REJECTED_UNSTRUCTURED"}, labels)
    ctx.guard("exit code 0 alone covers five distinct readings (so it cannot be the signal)", sorted(["READY", "HOLD", "HOLD_IF_PLANNED", "READY_WITH_WARNINGS", "UNVERIFIED"]),
              sorted({labels[k] for k, v in obs.items() if v.exit_code == 0}))


@case("PC03_unresolved_hold_and_selection", "Selected UNKNOWN/MISSING/PROXY stay HOLD; unselected unresolved rows are excluded; NULL/absent states are never defaulted",
      ["video_core/audiomix/plan.py:analyze", "video_core/audiomix/manifest.py:STATES", "video_core/avmux/manifest.py:_state"])
def pc03(ctx):
    run = ctx.run
    w = run.new_world("pc03")
    put_fixtures(w)
    base = audio_manifest()
    rows = {}
    for ev in ("ev_x_missing", "ev_x_unknown", "ev_x_proxy"):
        am = copy.deepcopy(base)
        am["scope"]["event_ids"].append(ev)
        w.put_json("am.json", am)
        r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], f"audio plan, selected {ev}")
        rows[ev] = (r.exit_code, r.json["status"], [(h["code"], h["state"]) for h in r.json["plan"]["hold"]])
    ctx.enforced("selected MISSING/UNKNOWN/PROXY -> exit 5, HOLD, SELECTED_UNRESOLVED with the state", {"ev_x_missing": (5, "HOLD", [("SELECTED_UNRESOLVED", "MISSING")]), "ev_x_unknown": (5, "HOLD", [("SELECTED_UNRESOLVED", "UNKNOWN")]),
                                                                                                   "ev_x_proxy": (5, "HOLD", [("SELECTED_UNRESOLVED", "PROXY")])}, rows)
    w.put_json("am.json", base)
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, subset only")
    ctx.enforced("unselected MISSING/UNKNOWN/PROXY/unread-USE rows are excluded and reported", (0, "READY", [("ev_x_missing", False), ("ev_x_unknown", False), ("ev_x_proxy", False)], {"asset_a": True, "asset_unselected": False}),
                 (r.exit_code, r.json["status"], [(u["event_id"], u["selected"]) for u in r.json["plan"]["unresolved"]], {a["id"]: a["verified"] for a in r.json["plan"]["assets"]}))
    am = copy.deepcopy(base)
    for i in (3, 4, 5):
        am["events"][i].update(start_frame=0, end_frame=3)
    w.put_json("am.json", am)
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, unselected rows overlapping the selected frames")
    ctx.enforced("unselected rows never take part in overlap analysis", (0, "READY"), (r.exit_code, r.json["status"]))
    am = copy.deepcopy(base)
    am["events"][3].update(start_frame=2, end_frame=1)
    w.put_json("am.json", am)
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, malformed UNSELECTED row")
    ctx.enforced("OB-01: an unselected row must still be schema-valid", (1, ["event_range_invalid"]), (r.exit_code, r.issue_codes))
    got = {}
    for tag, mut in (("null", lambda e: e.update(state=None)), ("absent", lambda e: e.pop("state")), ("HOLD", lambda e: e.update(state="HOLD")), ("lowercase", lambda e: e.update(state="unknown"))):
        am = copy.deepcopy(base)
        mut(am["events"][3])
        w.put_json("am.json", am)
        r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], f"audio state {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    ctx.enforced("NULL / absent / HOLD / lowercase states are rejected, never defaulted", {"null": (1, ["invalid_enum"]), "absent": (1, ["missing_field"]), "HOLD": (1, ["invalid_enum"]), "lowercase": (1, ["invalid_enum"])}, got)
    got = {}
    for tag, mut in (("empty", lambda a: a["scope"].update(event_ids=[])), ("unknown_id", lambda a: a["scope"]["event_ids"].append("nope")), ("duplicate", lambda a: a["scope"]["event_ids"].append("ev_vo_1")), ("star", lambda a: a["scope"].update(event_ids=["*"]))):
        am = copy.deepcopy(base)
        mut(am)
        w.put_json("am.json", am)
        r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], f"audio scope {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    ctx.enforced("scope is explicit: empty / unknown / duplicate / '*' rejected (no implicit all)", {"empty": (1, ["scope_empty"]), "unknown_id": (1, ["scope_unknown_event"]), "duplicate": (1, ["scope_duplicate"]), "star": (1, ["invalid_id"])}, got)
    got = {}
    for side in ("video", "audio"):
        for st in ("MISSING", "UNKNOWN", "PROXY", "HOLD", None):
            avmux_manifest(w, mut=lambda m, side=side, st=st: m[side].update(state=st))
            r = ctx.call(w, AVM, ["validate", "{docs}/av.json"], f"avmux {side} state {st}")
            got[f"{side}:{st}"] = (r.exit_code, r.issue_codes)
    exp = {f"{s}:{st}": (1, ["input_state_not_use"]) for s in ("video", "audio") for st in ("MISSING", "UNKNOWN", "PROXY", "HOLD")}
    exp.update({f"{s}:None": (1, ["invalid_enum"]) for s in ("video", "audio")})
    ctx.enforced("avmux accepts only USE inputs; every other state is a rejection code (HOLD survives only as input_state_not_use)", exp, got)
    # video-only cannot express state
    w2 = run.new_world("pc03v")
    put_fixtures(w2)
    got = {}
    for tag, mut in (("state_on_clip", lambda e: e["clips"][0].update(state="HOLD")), ("binding_ref_on_clip", lambda e: e["clips"][0].update(binding_ref="x")), ("null_source", lambda e: e["clips"][0].update(source=None))):
        e = episode()
        mut(e)
        w2.put_json("ep.json", e)
        r = ctx.call(w2, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], f"video {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    ctx.enforced("video-only cannot carry a state or binding ref (unknown_field) and refuses a null source", {"state_on_clip": (1, ["unknown_field"]), "binding_ref_on_clip": (1, ["unknown_field"]), "null_source": (1, ["type_error"])}, got)
    e = episode(file_clip="proxy_clip_a.mp4")
    w2.put_json("ep.json", e)
    r = ctx.call(w2, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan naming a proxy file")
    ctx.open_gap("GAP-01", "video-only accepts a proxy file (it cannot know)", (0, []), ctx.observe(lambda: (r.exit_code, r.issue_codes)), "an unresolved/proxy selected input should stop the call before an episode exists")
    e = episode()
    e["clips"][0]["source"] = {"kind": "color", "value": "#000000"}
    w2.put_json("ep.json", e)
    r = ctx.call(w2, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan with a colour stand-in for an unresolved clip")
    ctx.open_gap("GAP-01", "video-only accepts a colour stand-in for an unresolved clip", (0, []), ctx.observe(lambda: (r.exit_code, r.issue_codes)), "substituting unresolved input must not be silent")


@case("PC04_silence_omit_zero_fill_warnings", "Explicit silence vs OMIT vs absent vs ALLOW_ZERO_FILL; warnings are reported exactly",
      ["video_core/audiomix/plan.py:analyze", "video_core/audiomix/manifest.py:_events"])
def pc04(ctx):
    run = ctx.run
    w = run.new_world("pc04")
    put_fixtures(w)
    base = audio_manifest()
    gap_expected = [{"sample_start": o_sample(1, N, D), "sample_end": o_sample(2, N, D)}]
    got = {}

    def go(tag, mut, cmdargs=("plan",)):
        am = copy.deepcopy(base)
        mut(am)
        w.put_json("am.json", am)
        return ctx.call(w, AUD, [*cmdargs, "{docs}/am.json", "--asset-root", "{assets}"], f"audio {tag}")
    r = go("explicit silence", lambda a: None)
    got["explicit_silence"] = (r.exit_code, r.json["status"], r.json["plan"]["warnings"], r.json["plan"]["coverage"]["gap_count"])
    r = go("silence without decision_ref", lambda a: a["events"][1].pop("decision_ref"))
    got["silence_without_decision_ref"] = (r.exit_code, r.issue_codes)
    r = go("OMIT instead of silence", lambda a: a["events"][1].update(state="OMIT"))
    got["omit"] = (r.exit_code, r.json["status"], hold_codes(r), r.json["plan"]["coverage"]["gaps"])
    r = go("silence row left out of scope", lambda a: a["scope"]["event_ids"].remove("ev_sil_2"))
    got["absent"] = (r.exit_code, r.json["status"], hold_codes(r), r.json["plan"]["coverage"]["gaps"])
    ctx.enforced("explicit silence counts as coverage; missing decision_ref is rejected; OMIT and absent leave the exact gap [1602,3203) and HOLD",
                 {"explicit_silence": (0, "READY", [], 0), "silence_without_decision_ref": (1, ["missing_field"]), "omit": (5, "HOLD", ["INCOMPLETE_SELECTED_INPUT"], gap_expected),
                  "absent": (5, "HOLD", ["INCOMPLETE_SELECTED_INPUT"], gap_expected)}, got)

    def zf(a):
        a["scope"]["event_ids"].remove("ev_sil_2")
        a["policy"]["gap"] = "ALLOW_ZERO_FILL"
    r = go("absent row + ALLOW_ZERO_FILL", zf)
    ctx.enforced("zero-fill is reported: warning code and the exact filled gap", (["ZERO_FILL_APPLIED"], gap_expected), ([x["code"] for x in r.json["plan"]["warnings"]], r.json["plan"]["coverage"]["gaps"]))
    ctx.open_gap("GAP-09", "ALLOW_ZERO_FILL makes absent audio READY (exit 0)", (0, "READY"), ctx.observe(lambda: (r.exit_code, r.json["status"])), "absent audio must not become zeros without an explicit decision_ref")
    ctx.guard("owner reading: any warning means HOLD", "HOLD", guard_silence(r.json))
    zero_fill_plan = r.json

    def only_omit(a):
        a["scope"]["event_ids"] = ["ev_sil_2"]
        a["events"][1].update(state="OMIT")
        a["policy"]["gap"] = "ALLOW_ZERO_FILL"
    r = go("only an OMIT row in scope + ALLOW_ZERO_FILL", only_omit)
    ctx.enforced("no audio selected at all is warned about", ["NO_AUDIO_EVENTS_SELECTED", "ZERO_FILL_APPLIED"], sorted(x["code"] for x in r.json["plan"]["warnings"]))
    ctx.open_gap("GAP-09", "an all-absent scope is READY under ALLOW_ZERO_FILL (a silent stem would result)", (0, "READY"), ctx.observe(lambda: (r.exit_code, r.json["status"])), "no audio selected should select the video-only (-an) path, not a silent WAV")
    r = go("explicit silence under REQUIRE_FULL_COVERAGE again", lambda a: None)
    ctx.guard("explicit INTENTIONAL_SILENCE with decision_ref passes the same owner reading", "PROCEED", guard_silence(r.json))
    ctx.guard("zero-fill plan fixture is the one that was held", True, zero_fill_plan["plan"]["policy"]["gap"] == "ALLOW_ZERO_FILL")


@case("PC05_half_open_rational_boundaries", "Half-open [start,end) frame/sample boundaries from exact rationals; touching ranges are not overlap; +-1 refused",
      ["video_core/audiomix/plan.py:frame_to_sample", "video_core/timebase.py:frame_to_ms"])
def pc05(ctx):
    run = ctx.run
    w = run.new_world("pc05")
    put_fixtures(w)
    base = audio_manifest()
    per = {}
    for a in range(FRAMES):
        for b in range(a + 1, FRAMES + 1):
            res = {}
            for delta in (0, -1, 1):
                am = copy.deepcopy(base)
                am["scope"]["event_ids"] = ["ev_vo_1"]
                ev = am["events"][0]
                ev.update(start_frame=a, end_frame=b, source_in_sample=0, source_out_sample=o_sample(b, N, D) - o_sample(a, N, D) + delta)
                w.put_json("am.json", am)
                r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], f"audio range [{a},{b}) delta {delta}")
                res[delta] = (r.exit_code, r.json["plan"]["events"][0]["sample_start"], r.json["plan"]["events"][0]["sample_end"]) if r.exit_code in (0, 5) else (r.exit_code, r.issue_codes)
            per[f"[{a},{b})"] = res
    exp = {}
    for a in range(FRAMES):
        for b in range(a + 1, FRAMES + 1):
            exp[f"[{a},{b})"] = {0: (0 if (a, b) == (0, FRAMES) else 5, o_sample(a, N, D), o_sample(b, N, D)), -1: (1, ["range_length_mismatch"]), 1: (1, ["range_length_mismatch"])}
    ctx.enforced("every range: oracle samples accepted at exactly start..end, +-1 sample refused", exp, per)
    fam, expf = {}, {}
    for (fn, fd, fr) in ((30000, 1001, 3), (24000, 1001, 5), (60000, 1001, 7), (30, 1, 3), (25, 1, 3), (48000, 1001, 11), (24, 1, 7), (30000, 1001, 10000)):
        am = copy.deepcopy(base)
        am["timeline"] = {"fps": {"n": fn, "d": fd}, "total_frames": fr}
        am["scope"]["event_ids"] = ["ev_sil_2"]
        am["events"][1].update(start_frame=0, end_frame=fr)
        w.put_json("am.json", am)
        r = ctx.call(w, AUD, ["validate", "{docs}/am.json"], f"audio validate {fn}/{fd} x{fr}")
        fam[f"{fn}/{fd}x{fr}"] = r.json["total_samples"]
        expf[f"{fn}/{fd}x{fr}"] = o_sample(fr, fn, fd)
    ctx.enforced("total samples equal the exact-rational oracle for 8 rates incl. 10000 frames @ 30000/1001 = 16,016,000 (per-frame rounding would be 16,020,000)", expf, fam)
    # half-open: touching is not overlap, one shared frame is
    def two(a1, a2):
        am = copy.deepcopy(base)
        am["scope"]["event_ids"] = ["ev_vo_1", "ev_vo_3"]
        e1, e3 = am["events"][0], am["events"][2]
        e1.update(start_frame=a1[0], end_frame=a1[1], source_in_sample=0, source_out_sample=o_sample(a1[1], N, D) - o_sample(a1[0], N, D))
        e3.update(start_frame=a2[0], end_frame=a2[1], source_in_sample=100, source_out_sample=100 + o_sample(a2[1], N, D) - o_sample(a2[0], N, D))
        w.put_json("am.json", am)
        return ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], f"audio events {a1} and {a2}")
    r = two((0, 2), (2, 3))
    touching = (r.exit_code, r.json["status"], r.json["plan"]["coverage"]["gap_count"])
    r = two((0, 2), (1, 3))
    overlap = (r.exit_code, r.issue_codes)
    ctx.enforced("[0,2)+[2,3) touch (READY, no gap); [0,2)+[1,3) share frame 1 and are refused", ((0, "READY", 0), (1, ["event_overlap"])), (touching, overlap))
    # avmux agreement with the oracle
    avmux_manifest(w)
    put_fixtures(w)
    w.put("audio_receipt.json", jbytes(receipt_fabricated()))
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "avmux plan")
    ctx.enforced("avmux expected samples == oracle == audio plan total; durations are exact rationals", (o_sample(3, N, D), o_ratio(3 * D, N), o_ratio(o_sample(3, N, D), RATE)),
                 (r.json["plan"]["timeline"]["expected_audio_samples"], r.json["plan"]["timeline"]["video_duration_seconds"], r.json["plan"]["timeline"]["audio_duration_seconds"]))
    got = {}
    for tag, v in (("minus1", o_sample(3, N, D) - 1), ("plus1", o_sample(3, N, D) + 1), ("per_frame_rounded_x3", 3 * 1602)):
        avmux_manifest(w, mut=lambda m, v=v: m["audio"]["declared"].update(sample_count=v))
        r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], f"avmux declared {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    ctx.enforced("declared sample count must equal the exact boundary (+-1 and the accumulated per-frame value are refused)", {k: (1, ["audio_sample_count_mismatch"]) for k in got}, got)
    # video-only half-open captions / coverage
    w2 = run.new_world("pc05v")
    got = {}

    def vep(mut):
        e = {"version": 1, "fps": {"n": N, "d": D}, "canvas": {"width": 320, "height": 180}, "total_frames": 60,
             "clips": [{"id": "c1", "start_frame": 0, "duration_frames": 60, "source": {"kind": "color", "value": "#30506a"}}], "captions": [{"id": "k1", "start_frame": 30, "end_frame": 60, "text": "ok"}]}
        mut(e)
        w2.put_json("ep.json", e)
        return ctx.call(w2, VID, ["plan", "{docs}/ep.json"], "video plan variant")
    r = vep(lambda e: None)
    got["caption_end_equals_total"] = (r.exit_code, r.json["plan"]["captions"][0]["srt_end"], r.json["plan"]["episode"]["duration_seconds"])
    got["caption_end_total_plus_1"] = (lambda x: (x.exit_code, x.issue_codes))(vep(lambda e: e["captions"][0].update(end_frame=61)))
    got["caption_zero_length"] = (lambda x: (x.exit_code, x.issue_codes))(vep(lambda e: e["captions"][0].update(end_frame=30)))
    got["clip_gap"] = (lambda x: (x.exit_code, x.issue_codes))(vep(lambda e: e.update(clips=[{"id": "c1", "start_frame": 0, "duration_frames": 29, "source": {"kind": "color", "value": "#000000"}}, {"id": "c2", "start_frame": 30, "duration_frames": 30, "source": {"kind": "color", "value": "#000000"}}])))
    got["clip_overlap"] = (lambda x: (x.exit_code, x.issue_codes))(vep(lambda e: e.update(clips=[{"id": "c1", "start_frame": 0, "duration_frames": 31, "source": {"kind": "color", "value": "#000000"}}, {"id": "c2", "start_frame": 30, "duration_frames": 30, "source": {"kind": "color", "value": "#000000"}}])))
    got["coverage_short_by_one"] = (lambda x: (x.exit_code, x.issue_codes))(vep(lambda e: e["clips"][0].update(duration_frames=59)))
    ctx.enforced("video: caption end may equal total_frames (exclusive) but not exceed it; zero-length, gap, overlap and 1-frame-short coverage are refused",
                 {"caption_end_equals_total": (0, o_srt_ts(o_ms(60, N, D)), o_ratio(60 * D, N)), "caption_end_total_plus_1": (1, ["caption_past_end"]), "caption_zero_length": (1, ["caption_not_positive"]),
                  "clip_gap": (1, ["coverage_gap"]), "clip_overlap": (1, ["clip_overlap"]), "coverage_short_by_one": (1, ["coverage_incomplete"])}, got)


@case("PC06_srt_and_utf8_bytes", "SRT cue times from exact rationals, ordering, LF-only bytes and valid UTF-8 text (incl. non-BMP, combining marks, '-->') preserved byte for byte",
      ["video_core/srt.py:export_srt", "video_core/timebase.py:frame_to_srt"])
def pc06(ctx):
    run = ctx.run
    w = run.new_world("pc06")
    texts = {"c1": "後 Japanese テスト", "c2": 'ASCII: "quote" \\ back % --> arrow', "c3": "emoji \U0001F3AC é (NFD) é (NFC)\n二行目 line two", "c4": "tie second 同時"}
    caps = [("c1", 30, 45), ("c2", 0, 30), ("c3", 45, 60), ("c4", 0, 30)]
    e = {"version": 1, "fps": {"n": N, "d": D}, "canvas": {"width": 320, "height": 180}, "total_frames": 60,
         "clips": [{"id": "clip", "start_frame": 0, "duration_frames": 60, "source": {"kind": "color", "value": "#30506a"}}],
         "captions": [{"id": cid, "start_frame": s, "end_frame": t, "text": texts[cid]} for cid, s, t in caps]}
    w.put_json("ep.json", e)
    expected = o_srt([(i, s, t, texts[cid]) for i, (cid, s, t) in enumerate(caps)], N, D)
    r = ctx.call(w, VID, ["srt", "{docs}/ep.json"], "video srt")
    ctx.enforced("SRT bytes equal the independently assembled oracle (sha256 + length)", (0, sha256_bytes(expected), len(expected)), (r.exit_code, sha256_bytes(r.stdout_bytes), len(r.stdout_bytes)))
    ctx.enforced("no BOM, no CR byte, ends with one LF, valid UTF-8", (False, False, True, True), (r.stdout_bytes.startswith(b"\xef\xbb\xbf"), b"\r" in r.stdout_bytes, r.stdout_bytes.endswith(b"\n") and not r.stdout_bytes.endswith(b"\n\n"), _utf8_ok(r.stdout_bytes)))
    ctx.enforced("cue times: [0,30) -> 00:00:00,000 --> 00:00:01,001 ; end of [45,60) -> 00:00:02,002 (round-half-up of the exact rational)", ("00:00:01,001", "00:00:02,002"), (o_srt_ts(o_ms(30, N, D)), o_srt_ts(o_ms(60, N, D))))
    r2 = ctx.call(w, VID, ["plan", "{docs}/ep.json"], "video plan (caption text)")
    ctx.enforced("plan keeps caption text exactly as given (no Unicode normalisation, no escaping)", [texts[c] for c, _s, _t in caps], [c["text"] for c in r2.json["plan"]["captions"]])
    ctx.enforced("plan SRT section hashes the same bytes", (sha256_bytes(expected), len(expected)), (r2.json["plan"]["srt"]["sha256"], r2.json["plan"]["srt"]["bytes"]))
    ctx.enforced("plan cue seconds are exact rationals (no float)", [o_ratio(s * D, N) for _c, s, _t in caps], [c["start_seconds"] for c in r2.json["plan"]["captions"]])
    bad = copy.deepcopy(e)
    bad["captions"][0]["text"] = "line one\n\nline after blank"
    w.put_json("bad.json", bad)
    r3 = ctx.call(w, VID, ["srt", "{docs}/bad.json"], "video srt with a blank line in a cue")
    ctx.enforced("a blank line (which would end an SRT cue) is rejected", (1, ["text_blank_line"]), (r3.exit_code, r3.issue_codes))
    # the original spec's published example
    ex = os.path.join(run.library_root, "examples", "episode.synthetic.json")
    sx = os.path.join(run.library_root, "examples", "captions.synthetic.srt")
    if os.path.isfile(ex) and os.path.isfile(sx):
        with open(ex, "rb") as fh:
            exb = fh.read()
        w.put("episode.synthetic.json", exb, area="docs")
        r4 = ctx.call(w, VID, ["srt", "{docs}/episode.synthetic.json"], "video srt of the original spec example")
        ctx.enforced("the original spec's example episode (pinned sha256) exports the spec's published SRT (pinned sha256)", ("401ac89079cc60d4f7a158eeba9b6c1e57edb664a3322c8d00c2de4ac6a01440", "e16670605dc78395cc66a4f53ff31372fbe03f47cbf5c81fc6f07f680fcc21a1"),
                     (sha256_bytes(exb), sha256_bytes(r4.stdout_bytes)))
    else:
        ctx.skip("original spec example SRT", "examples/ not present under the library root")


def _utf8_ok(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


@case("PC07_plan_hash_pins_output_namespace", "Plan hash and source-pin semantics; output namespace refusals (existing, inside assets, reserved/unsafe names, case-insensitive twins)",
      ["video_core/paths.py:check_output_location", "video_core/plan.py:prepare"])
def pc07(ctx):
    run = ctx.run
    w = run.new_world("pc07")
    put_fixtures(w)
    w.put_json("ep.json", episode())
    p1 = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan #1")
    p2 = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan #2 (same inputs)")
    ctx.enforced("video plan is deterministic and records the sha256 of the episode bytes", (True, sha256_bytes(open(os.path.join(w.docs, "ep.json"), "rb").read())), (p1.json["plan_sha256"] == p2.json["plan_sha256"], p1.json["observations"]["input"]["episode_sha256"]))
    e2 = episode()
    e2["clips"][1]["source"]["value"] = "#6a5030"
    w.put_json("ep2.json", e2)
    p3 = ctx.call(w, VID, ["plan", "{docs}/ep2.json", "--asset-root", "{assets}"], "video plan with a changed colour")
    ctx.enforced("a changed episode changes plan_sha256", True, p3.json["plan_sha256"] != p1.json["plan_sha256"])
    w.put("clip_a.mp4", FB["clip_a_v2.mp4"])
    p4 = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan after a same-size source swap")
    ctx.open_gap("GAP-02", "video-only plan_sha256 is unchanged when the source bytes change (no source hash in the plan)", (True, False), ctx.observe(lambda: (p4.json["plan_sha256"] == p1.json["plan_sha256"], "sha256" in json.dumps(p4.json["observations"]["assets"]))),
                 "the plan (or the episode) should pin source content")
    w.put("clip_a.mp4", FB["clip_a.mp4"])
    # audio / avmux pins are enforced
    am = audio_manifest(asset_sha="1" * 64)
    w.put_json("am.json", am)
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, wrong pin")
    ctx.enforced("audiomix enforces the pin of a selected asset", (1, ["asset_sha256_mismatch"]), (r.exit_code, r.issue_codes))
    avmux_manifest(w, mut=lambda m: m["video"].update(sha256="2" * 64))
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "avmux plan, wrong video pin")
    ctx.enforced("avmux enforces the video pin", (1, ["video_sha256_mismatch"]), (r.exit_code, r.issue_codes))
    # output namespace
    os.makedirs(os.path.join(w.assets, "sub"))
    os.mkdir(os.path.join(w.out, "exists"))
    os.mkdir(os.path.join(w.out, "Run-Twin"))
    twin_insensitive = os.path.lexists(os.path.join(w.out, "run-twin"))      # filesystem probe: independent oracle for case-insensitivity
    w.put_json("am.json", audio_manifest())
    avmux_manifest(w)
    w.put("audio_receipt.json", jbytes(receipt_fabricated()))
    stages = {"video": (VID, ["plan", "{docs}/ep.json"]), "audio": (AUD, ["plan", "{docs}/am.json"]), "avmux": (AVM, ["plan", "{docs}/av.json"])}
    before = w.snapshot()
    got = {}
    cases = {"fresh": ("{out}", "n1"), "inside_assets": ("{assets}/sub", "n1"), "assets_root_itself": ("{assets}", "n1"), "existing": ("{out}", "exists"), "case_twin": ("{out}", "run-twin")}
    for st, (mod, base) in stages.items():
        for tag, (root, name) in cases.items():
            r = ctx.call(w, mod, base + ["--asset-root", "{assets}", "--output-root", root, "--output-name", name], f"{st} plan, output {tag}")
            got[f"{st}:{tag}"] = (r.exit_code, r.issue_codes)
    exp = {}
    for st in stages:
        exp.update({f"{st}:fresh": (0, []), f"{st}:inside_assets": (1, ["output_overlaps_assets"]), f"{st}:assets_root_itself": (1, ["output_overlaps_assets"]), f"{st}:existing": (1, ["output_exists"]),
                    f"{st}:case_twin": (1, ["output_exists"]) if twin_insensitive else (0, [])})
    ctx.enforced(f"output namespace matrix for all three stages (case-twin expectation from a filesystem probe: case-insensitive={twin_insensitive})", exp, got)
    finish_world_checks(ctx, w, {**before}, "no plan created any directory or file")
    bad_names = {"reserved_CON": "CON", "reserved_nul_txt": "nul.txt", "reserved_com1": "com1", "trailing_dot": "name.", "backslash": "a\\b", "colon": "a:b", "space": "a b", "dotdot": "..", "slash": "a/b", "leading_dash": "-x", "leading_dot": ".hidden"}
    got = {}
    for tag, name in bad_names.items():
        name_args = ["--output-name=" + name] if name.startswith("-") else ["--output-name", name]      # '=' form: argparse must not mistake '-x' for an option
        r = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}", "--output-root", "{out}", *name_args], f"video plan, output name {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    ctx.enforced("unsafe / Windows-reserved output names are refused on every platform", {k: (1, ["output_name_invalid"]) for k in bad_names}, got)
    r = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}", "--output-root", "{out}"], "video plan, root without name")
    ctx.enforced("output root and name must be given together", (1, ["output_incomplete"]), (r.exit_code, r.issue_codes))


@case("PC08_receipts_malformed_and_shallow", "Receipt statuses are enforced; malformed shapes, forged/omitted receipts, dropped warnings and arbitrary refs are OBSERVED OPEN GAPS on the pinned source",
      ["video_core/avmux/plan.py:_check_audio_receipt", "video_core/avmux/manifest.py:_validate"])
def pc08(ctx):
    run = ctx.run
    w = run.new_world("pc08")
    put_fixtures(w)
    got = {}
    for st in ("IN_PROGRESS", "UNKNOWN", "HOLD", "MIXED_WITH_CLAMPING", "CLIPPING_REJECTED", "READBACK_MISMATCH", "TIMEOUT", "CANCELLED", "INTERRUPTED", "mixed_verified", "", None):
        rec = receipt_fabricated()
        rec["status"] = st
        avmux_manifest(w, receipt=rec)
        r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], f"receipt status {st!r}")
        got[repr(st)] = (r.exit_code, r.issue_codes)
    ctx.enforced("only exactly MIXED_VERIFIED qualifies", {k: (1, ["audio_receipt_not_verified"]) for k in got}, got)
    got = {}
    stem = receipt_fabricated()["outputs"][0]
    structured = {"not_json": b"{ nope", "no_outputs": dict(receipt_fabricated(), outputs=[]), "two_stems": dict(receipt_fabricated(), outputs=[stem, dict(stem)]),
                  "partial_only": dict(receipt_fabricated(), outputs=[dict(stem, role="stem_partial", partial=True)]), "other_stem_sha": dict(receipt_fabricated(), outputs=[dict(stem, sha256="9" * 64)]),
                  "sample_count_wrong": dict(receipt_fabricated(), readback={"wav": {"sample_count": 4804}}), "readback_absent": dict(receipt_fabricated(), readback=None)}
    for tag, rec in structured.items():
        avmux_manifest(w, receipt=rec)
        r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], f"receipt {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    exp_codes = {"not_json": "audio_receipt_unreadable", "no_outputs": "audio_receipt_stem_mismatch", "two_stems": "audio_receipt_stem_mismatch", "partial_only": "audio_receipt_stem_mismatch",
                 "other_stem_sha": "audio_receipt_stem_mismatch", "sample_count_wrong": "audio_receipt_sample_mismatch", "readback_absent": "audio_receipt_sample_mismatch"}
    ctx.enforced("structurally wrong receipts that the library does read are refused with specific codes", {k: (1, [v]) for k, v in exp_codes.items()}, got)
    shapes = {"array": b"[]\n", "string": b'"x"\n', "null": b"null\n", "number": b"1\n", "outputs_is_string": {"status": "MIXED_VERIFIED", "outputs": "x"}, "outputs_list_of_strings": {"status": "MIXED_VERIFIED", "outputs": ["a"]},
              "readback_is_string": {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": H["stem.wav"]}], "readback": "x"}, "wav_is_string": {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": H["stem.wav"]}], "readback": {"wav": "x"}}}
    got = {}
    for tag, rec in shapes.items():
        avmux_manifest(w, receipt=rec)
        r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], f"receipt shape {tag}")
        got[tag] = (r.exit_code, r.stdout_bytes.strip() == b"", "AttributeError" in r.stderr_text, r.issue_codes)
    ctx.open_gap("GAP-14", "eight mis-shaped (valid JSON) receipts: exit 1, empty stdout, uncaught AttributeError, no issue code", {k: (1, True, True, []) for k in shapes}, got,
                 "a structured audio_receipt_unreadable rejection for every shape")
    avmux_manifest(w, receipt=None)
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "no receipt at all")
    ctx.open_gap("GAP-04", "no receipt: the stem is accepted by path+sha256 alone", (0, None), ctx.observe(lambda: (r.exit_code, r.json["plan"]["audio"]["receipt"])), "a stem should not be handed on without a qualifying receipt")
    forged = {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": H["stem.wav"]}], "readback": {"wav": {"sample_count": 4805}}}
    avmux_manifest(w, receipt=forged)
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "three-field forged receipt")
    ctx.open_gap("GAP-04", "a hand-written three-field receipt is accepted", (0, "MIXED_VERIFIED", None), ctx.observe(lambda: (r.exit_code, r.json["plan"]["audio"]["receipt"]["status"], r.json["plan"]["audio"]["receipt"]["plan_sha256"])), "receipt provenance/ownership cannot be shown by status+hash")
    edited = dict(receipt_fabricated(), stage="mix", written=False, plan_sha256="0" * 64, input={"manifest_sha256": "1" * 64, "inputs_unchanged": False, "assets": []},
                  readback={"all_ok": False, "wav": {"sample_count": 4805}}, warnings=[{"code": "ZERO_FILL_APPLIED"}, {"code": "NO_AUDIO_EVENTS_SELECTED"}], unresolved_report=[{"event_id": "x", "state": "UNKNOWN", "selected": True}])
    avmux_manifest(w, receipt=edited)
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], "self-contradicting edited receipt")
    ctx.open_gap("GAP-04", "a receipt contradicting its own status (inputs_unchanged false, all_ok false, written false) is accepted", (0, "MIXED_VERIFIED"), ctx.observe(lambda: (r.exit_code, r.json["plan"]["audio"]["receipt"]["status"])), "shallow qualification")
    ctx.open_gap("GAP-05", "the plan keeps only path/plan_sha256/sha256/status of the receipt - warnings and the unresolved report are dropped", ["path", "plan_sha256", "sha256", "status"], ctx.observe(lambda: sorted(r.json["plan"]["audio"]["receipt"])),
                 "warnings (ZERO_FILL_APPLIED, NO_AUDIO_EVENTS_SELECTED) and unresolved items should be surfaced")
    refs = {}
    for ref in ("synthetic-selection/0001", "totally/unrelated", "APPROVED-BY-SOMEONE-ELSE"):
        avmux_manifest(w, mut=lambda m, ref=ref: m["selection"].update(ref=ref))
        r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}"], f"selection.ref {ref!r}")
        refs[ref] = ctx.observe(lambda: (r.exit_code, r.json["plan"]["selection"]["ref"]))
    ctx.open_gap("GAP-07", "any selection.ref is accepted and echoed (not cross-checked with the audio stage)", {k: (0, k) for k in refs}, refs, "refs/scope/plan hashes should be comparable across stages")
    ctx.guard("an owner comparing the receipt with its own audio plan/manifest rejects the forged receipt", "REJECT",
              guard_receipt_binding(forged, stem_sha256=H["stem.wav"], audio_plan_sha256="a" * 64, manifest_sha256="b" * 64)["verdict"])
    got = {}
    for tag, mut in (("video.receipt", lambda m: m["video"].update(receipt={"path": "r.json", "sha256": "0" * 64})), ("selection.approved_by", lambda m: m["selection"].update(approved_by="x")), ("top.execution_permission", lambda m: m.update(execution_permission=True))):
        avmux_manifest(w, mut=mut)
        r = ctx.call(w, AVM, ["validate", "{docs}/av.json"], f"manifest field {tag}")
        got[tag] = (r.exit_code, r.issue_codes)
    ctx.enforced("fail closed: there is no slot for a video receipt, approval or permission (unknown_field)", {k: (1, ["unknown_field"]) for k in got}, got)
    # shape oracle: a genuine, hash-pinned, already-recorded receipt (stage-b evidence). It cannot be an avmux plan input: its stem is not in the repository.
    gp = os.path.join(run.library_root, "evidence", "stage-b-audio-stem", "receipt.stem-demo.json")
    if os.path.isfile(gp):
        raw = open(gp, "rb").read()
        g = json.loads(raw.decode("utf-8"))
        fab = receipt_fabricated()
        ctx.control("genuine recorded receipt is the pinned file (sha256)", "f87030ea130a55805a400454b48c00fa7d88ab7ceb3313ff36a10df1a37c1711", sha256_bytes(raw.replace(b"\r\n", b"\n")))
        ctx.control("SHAPE ORACLE ONLY: the fabricated receipt's keys are a subset of the genuine one's (top, input, output item, readback.wav); no provenance is implied",
                    (True, True, True, True), (set(fab) - {"_fixture_note"} <= set(g), set(fab["input"]) <= set(g["input"]), set(fab["outputs"][0]) <= set(g["outputs"][0]), set(fab["readback"]["wav"]) <= set(g["readback"]["wav"])))
    else:
        ctx.skip("genuine receipt shape oracle", "evidence/stage-b-audio-stem/receipt.stem-demo.json not present under the library root; qualification limit: without it the fabricated receipt shape is unchecked")


@case("PC09_windows_paths_japanese_spaces", "Ordinary Japanese + space roots and file names work in all three planners; Windows path traps are refused by rule",
      ["video_core/paths.py:check_relative_path", "video_core/paths.py:resolve_asset"])
def pc09(ctx):
    run = ctx.run
    jp_root = "日本語 作業 フォルダ"
    w = run.new_world("pc09jp", dirname=jp_root)
    names = {"video": "素材 テスト.mp4", "wav": "音声 サンプル.wav", "av_video": "映像 サンプル.mp4", "av_stem": "音声 ステム.wav", "receipt": "領収 receipt.json"}
    w.put(names["video"], FB["clip_a.mp4"])
    w.put(names["wav"], FB["seg_a.wav"])
    w.put(names["av_video"], FB["video_ok.mp4"])
    w.put(names["av_stem"], FB["stem.wav"])
    ctx.control("the world root really contains Japanese and a space", (True, True), (jp_root in w.root, " " in w.root))
    w.put_json("ep.json", episode(file_clip=names["video"]))
    r = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-jp"], "video plan, Japanese+space root and file")
    ctx.enforced("video-only plan: exit 0, the file name is preserved, output location fresh", (0, [names["video"]], True), (r.exit_code, [a["path"] for a in r.json["observations"]["assets"]], (r.json["observations"]["output"] or {}).get("fresh")))
    w.put_json("am.json", audio_manifest(asset_path=names["wav"]))
    r = ctx.call(w, AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}"], "audio plan, Japanese+space asset")
    ctx.enforced("audiomix plan: READY with the Japanese+space asset verified", (0, "READY", names["wav"]), (r.exit_code, r.json["status"], r.json["plan"]["assets"][0]["path"]))
    avmux_manifest(w, video=names["av_video"], audio=names["av_stem"], receipt_name=names["receipt"])
    r = ctx.call(w, AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-jp-m"], "avmux plan, Japanese+space files")
    ctx.enforced("avmux plan: exit 0, names preserved, readiness UNVERIFIED", (0, names["av_video"], names["av_stem"], True), (r.exit_code, r.json["plan"]["video"]["path"], r.json["plan"]["audio"]["path"], r.json["readiness"].startswith("UNVERIFIED")))
    ctx.enforced("nothing was created by the three plans", {}, w.tree("out"))
    # traps, by rule (validate only: no files needed)
    wt = run.new_world("pc09trap")
    traps = {"path_backslash": "sub\\a.wav", "path_absolute": "C:evil.wav", "path_absolute_unc_backslash": "\\\\host\\share\\x.wav", "path_absolute_unc_slash": "//host/share/x.wav", "path_traversal": "../x.wav",
             "path_not_normalized": "a/./x.wav", "path_windows_unsafe_dot": "a.wav.", "path_windows_unsafe_space": "a.wav ", "path_reserved_name": "CON.wav", "path_option_like": "-x.wav", "path_hidden": ".h.wav",
             "path_protocol": "ab:c.wav", "path_forbidden_char": "a*b.wav", "path_control_char": "a\u0000b.wav"}
    got = {}
    for code, rel in traps.items():
        am = audio_manifest(asset_path=rel)
        wt.put_json("am.json", am)
        r = ctx.call(wt, AUD, ["validate", "{docs}/am.json"], f"audio validate, asset path {code}")
        got[code] = (r.exit_code, code.replace("_unc_backslash", "").replace("_unc_slash", "").replace("_dot", "").replace("_space", "") in r.issue_codes)
    ctx.enforced("Windows-flavoured path traps are refused by rule (code present, exit 1)", {k: (1, True) for k in traps}, got)


@case("PC10_symlink_rejection_if_creatable", "A symlinked asset is rejected; creating a symlink may need privileges the harness never enables, so it SKIPS with the reason",
      ["video_core/paths.py:resolve_asset"])
def pc10(ctx):
    run = ctx.run
    w = run.new_world("pc10")
    put_fixtures(w)
    target = os.path.join(w.assets, "clip_a.mp4")
    link = os.path.join(w.assets, "link_a.mp4")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError, AttributeError) as exc:
        ctx.skip("symlinked asset rejected", f"cannot create a symlink here without privileges ({type(exc).__name__}: {str(exc)[:120]}); privileges are never enabled by this harness")
        return
    w.put_json("ep.json", episode(file_clip="link_a.mp4"))
    r = ctx.call(w, VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}"], "video plan via a symlinked asset")
    ctx.enforced("a symlinked asset is rejected", (1, ["symlink_rejected"]), (r.exit_code, r.issue_codes))
