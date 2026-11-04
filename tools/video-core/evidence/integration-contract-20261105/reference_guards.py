"""REFERENCE GUARDS — illustrations of what the *existing owners* must check before / between library calls.

These are NOT part of video_core, NOT a production adapter and NOT a stricter replacement validator. They are a few lines each, they run only on
data the harness already holds, and they exist so the report can show, next to the library's *actual* behaviour, what the missing check looks like.
A contract test never passes because a guard rejected something: every test first asserts what the library itself did; guard results are recorded
separately as kind=CALLER_GUARD_DEMO.
"""
from __future__ import annotations

RESOLVED_STATES = ("USE", "INTENTIONAL_SILENCE", "OMIT")


def guard_selection(rows: list) -> dict:
    """Owner-side (selection authority). Selected rows must be resolved; unselected rows never block; unresolved SELECTED rows stop the call."""
    reasons = [f"{r['row_id']}: selected row is {r['state']}" for r in rows if r.get("selected") and r["state"] not in RESOLVED_STATES]
    return {"verdict": "HOLD" if reasons else "PROCEED", "reasons": reasons}


def guard_timing(rows: list) -> dict:
    """Owner-side. A selected USE row needs an owner-held timing qualification; the library has no field for one (GAP-06)."""
    bad = [r["row_id"] for r in rows if r.get("selected") and r["state"] == "USE" and (r.get("timing_qualification") or {}).get("status") != "QUALIFIED"]
    return {"verdict": "HOLD" if bad else "PROCEED", "unqualified_rows": bad}


def guard_hash_pins(pinned: dict, actual: dict) -> dict:
    """Owner-side. Pin every selected source by sha256 at selection time; compare before each stage (the video-only plan does not, GAP-02)."""
    changed = sorted(k for k in pinned if actual.get(k) != pinned[k])
    return {"verdict": "REJECT" if changed else "PROCEED", "changed": changed}


def guard_video_receipt(receipt: dict, expected_video_sha256: str) -> dict:
    """Owner-side. avmux has no slot for a video render receipt (GAP-03): the owner must check it before building the AV manifest."""
    reasons = []
    if receipt.get("status") != "READBACK_MATCH":
        reasons.append(f"video receipt status is {receipt.get('status')!r}, not READBACK_MATCH (IN_PROGRESS/UNKNOWN/partial must be treated as UNKNOWN)")
    outs = [o for o in receipt.get("outputs", []) if not o.get("partial")]
    if not any(o.get("sha256") == expected_video_sha256 for o in outs):
        reasons.append("no non-partial output with the video sha256 the AV manifest names")
    return {"verdict": "REJECT" if reasons else "PROCEED", "reasons": reasons}


def guard_audio_receipt_binding(receipt: dict, *, stem_sha256: str, audio_plan_sha256: str, manifest_sha256: str) -> dict:
    """Owner-side. avmux reads only status, outputs[role=stem].sha256 and readback.wav.sample_count (GAP-04). Everything below it ignores."""
    r = []
    if receipt.get("plan_sha256") != audio_plan_sha256:
        r.append("receipt.plan_sha256 is not the plan the owner ran")
    if (receipt.get("input") or {}).get("manifest_sha256") != manifest_sha256:
        r.append("receipt.input.manifest_sha256 is not the manifest the owner submitted")
    if (receipt.get("input") or {}).get("inputs_unchanged") is not True:
        r.append("receipt.input.inputs_unchanged is not true")
    if not (receipt.get("readback") or {}).get("all_ok"):
        r.append("receipt.readback.all_ok is not true")
    if receipt.get("written") is not True or receipt.get("stage") != "complete":
        r.append("receipt.written/stage do not show a completed write")
    if receipt.get("warnings"):
        r.append(f"receipt carries warnings the AV stage drops: {[w.get('code') if isinstance(w, dict) else w for w in receipt['warnings']]}")
    if not any(o.get("role") == "stem" and o.get("sha256") == stem_sha256 and not o.get("partial") for o in receipt.get("outputs", [])):
        r.append("no non-partial stem output with the stem sha256")
    return {"verdict": "REJECT" if r else "PROCEED", "reasons": r,
            "limit": "even a PROCEED proves only internal consistency; an unauthenticated JSON file cannot show who produced or approved it"}


def guard_silence_provenance(audio_plan: dict, stem_all_zero: bool) -> dict:
    """Owner-side. An all-zero stem is acceptable only when selected INTENTIONAL_SILENCE rows with a decision_ref cover it; absent/unmeasured audio is not silence."""
    p = audio_plan["plan"]
    sil = [e for e in p["events"] if e["selected"] and e["state"] == "INTENTIONAL_SILENCE" and e.get("decision_ref")]
    zero_fill = any(w["code"] == "ZERO_FILL_APPLIED" for w in p["warnings"])
    reasons = []
    if zero_fill:
        reasons.append("ZERO_FILL_APPLIED: part of the stem is zeros because input was ABSENT, not because silence was decided")
    if any(w["code"] == "NO_AUDIO_EVENTS_SELECTED" for w in p["warnings"]):
        reasons.append("NO_AUDIO_EVENTS_SELECTED: a video-only (-an) path is the honest outcome, not a silent WAV")
    if stem_all_zero and not sil:
        reasons.append("all-zero stem without any selected INTENTIONAL_SILENCE row carrying a decision_ref")
    return {"verdict": "HOLD" if reasons else "PROCEED", "reasons": reasons}


def guard_status_semantics(module: str, exit_code: int, doc: dict | None) -> dict:
    """Caller-side reading of 'ready'. Exit code 0 alone is not READY for audiomix dry-run (HOLD exits 0) or avmux plan (UNVERIFIED exits 0)."""
    doc = doc or {}
    if module == "video_core.audiomix":
        status = doc.get("status") or (doc.get("plan") or {}).get("status")
        if exit_code == 0 and status == "HOLD":
            return {"reading": "HOLD", "note": "exit code 0 with status HOLD (dry-run); the exit code alone would be misread as success"}
        if exit_code == 0 and (doc.get("hold_if_planned") or doc.get("warnings")):
            return {"reading": "NOT_READY", "note": "validate exits 0 while hold_if_planned/warnings are non-empty"}
        if exit_code == 0 and status == "READY" and (doc.get("plan") or {}).get("warnings"):
            return {"reading": "READY_WITH_WARNINGS", "note": "exit 0 and READY while warnings (e.g. ZERO_FILL_APPLIED) are present"}
        return {"reading": "READY" if exit_code == 0 and status in (None, "READY") else "NOT_READY"}
    if module == "video_core.avmux" and exit_code == 0:
        ready = str(doc.get("readiness", ""))
        return {"reading": "UNVERIFIED" if ready.startswith("UNVERIFIED") else "NO_READINESS_FIELD",
                "note": "an avmux plan never says verified; only the explicit mux preflight measures the video"}
    return {"reading": "REJECTED" if exit_code else "OK"}


def guard_permission_and_approval(request: dict) -> dict:
    """Owner-side. Execution permission and creative approval are owner records; neither can be carried by, nor inferred from, any library field."""
    reasons = []
    if not (request.get("execution_permission") or {}).get("granted"):
        reasons.append("no owner-held execution permission for this exact plan hash")
    if (request.get("creative_approval") or {}).get("status") not in ("APPROVED_BY_OWNER_RECORD",):
        reasons.append("creative approval is unresolved (library verdicts are always NOT_EVALUATED)")
    return {"verdict": "HOLD" if reasons else "PROCEED", "reasons": reasons}
