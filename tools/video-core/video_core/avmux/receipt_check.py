"""Mechanical qualification of a pinned audio-planner receipt (a pure function over decoded JSON; no file, no process).

`audio.receipt` in an avmux manifest names a `receipt.json` that `video_core.audiomix` wrote for the stem. The avmux plan reads it with the
strict loader (no duplicate keys, NaN/Infinity, BOM, bad UTF-8 or runaway nesting) and `qualify_audio_receipt` then checks, against what the
producer (`audiomix.mix`) really writes for a `MIXED_VERIFIED` run, that the document is complete, internally consistent and describes
exactly THIS stem. Every check is exact: `is True` / `is False` for flags, `type(x) is int` for counters (no 4800.0, no True), no coercion.

What this is NOT: authentication. The receipt is a caller-supplied file pinned only by its SHA-256 in the same manifest, so its structure
and hashes prove consistency with the stem, not who produced or approved it, and they are not evidence of creative adoption or quality.
There is no signature, credential or approval authority here, and none is invented.

Rejection codes (all `ValidationFailed`, pointer `/audio/receipt` plus a `#/json/pointer` into the receipt):
  audio_receipt_shape           a required container is missing or has the wrong JSON type
  audio_receipt_not_verified    status is not MIXED_VERIFIED (HOLD, clamped, rejected, timed-out, unknown, in-progress ...)
  audio_receipt_schema          `schema` is not video_core.audio_receipt/1
  audio_receipt_inconsistent    a flag or counter contradicts a verified, complete, non-partial, unclipped, unchanged-input run
  audio_receipt_plan_hash       plan_sha256 is not a 64-digit lowercase hex digest, or does not equal the receipt's own plan output
  audio_receipt_stem_mismatch   no single final stem output, or its SHA-256 / byte count / name is not this stem
  audio_receipt_sample_mismatch the receipt's WAV sample count is not the manifest's declared count
  audio_receipt_format_mismatch the receipt's WAV channels / rate / bit depth differ from the declared ones
"""

from __future__ import annotations

import re

from ..audiomix.mix import RECEIPT_SCHEMA, S_VERIFIED
from ..audiomix.plan import PLAN_NAME, STEM_NAME
from ..errors import Issue, ValidationFailed

SAMPLE_RATE = 48_000
BITS = 16
MAX_COPIED_WARNINGS = 32
MAX_COPIED_TEXT = 300
_HEX64 = re.compile(r"[0-9a-f]{64}")
# readback checks the producer always records on a verified run, and the wav field each one mirrors
READBACK_CHECKS = {"channels": "channels", "sample_rate": "sample_rate", "bits": "bits", "sample_count": "sample_count", "file_bytes": "file_bytes"}

AUTHENTICATION_NOTE = ("MECHANICAL structure and hash consistency only: the receipt is a caller-pinned, unauthenticated file; this does not prove "
                       "who produced or approved it, creative adoption or audio quality")


def _fail(code: str, pointer: str, message: str):
    raise ValidationFailed(message, [Issue(code, "/audio/receipt" + (f"#{pointer}" if pointer else ""), message)])


def _int(v) -> bool:
    return type(v) is int  # not bool, not float, not a subclass


def _show(v) -> str:
    """Bounded, ASCII-safe description of a decoded value for a diagnostic (never echoes more than 40 characters)."""
    if isinstance(v, str):
        return repr(v[:40])
    if v is None or isinstance(v, (bool, int, float)):
        return repr(v)[:40]
    return f"a JSON {'array' if isinstance(v, list) else 'object' if isinstance(v, dict) else type(v).__name__}"


def _obj(rec, key, pointer):
    v = rec.get(key)
    if not isinstance(v, dict):
        _fail("audio_receipt_shape", f"{pointer}/{key}", f"audio receipt field {pointer}/{key} must be a JSON object, not {_show(v)}")
    return v


def _list(rec, key, pointer):
    v = rec.get(key)
    if not isinstance(v, list):
        _fail("audio_receipt_shape", f"{pointer}/{key}", f"audio receipt field {pointer}/{key} must be a JSON array, not {_show(v)}")
    return v


def qualify_audio_receipt(rec, *, stem_sha256: str, stem_bytes: int, samples: int, channels: int) -> dict:
    """Return the facts the avmux plan records about a qualifying receipt, or raise ValidationFailed (typed, never AttributeError)."""
    if not isinstance(rec, dict):
        _fail("audio_receipt_shape", "", f"audio receipt must be a JSON object, not {_show(rec)}")
    status = rec.get("status")
    if status != S_VERIFIED:  # a non-string / missing status can never equal it
        _fail("audio_receipt_not_verified", "/status",
              f"audio receipt status is {_show(status)}; only {S_VERIFIED} qualifies a stem (HOLD, clamped, rejected or unknown stems are refused)")
    if rec.get("schema") != RECEIPT_SCHEMA:
        _fail("audio_receipt_schema", "/schema", f"audio receipt schema is {_show(rec.get('schema'))}; it must be {RECEIPT_SCHEMA!r} (a receipt written by video_core.audiomix)")
    if rec.get("written") is not True:
        _fail("audio_receipt_inconsistent", "/written", "a verified audio receipt must have written == true (outputs exist)")
    if rec.get("stage") != "complete":
        _fail("audio_receipt_inconsistent", "/stage", f"a verified audio receipt must have stage == 'complete', not {_show(rec.get('stage'))}")
    if rec.get("quality_evaluated") is not False:
        _fail("audio_receipt_inconsistent", "/quality_evaluated", "audio receipts never evaluate quality: quality_evaluated must be false")
    plan_sha = rec.get("plan_sha256")
    if not isinstance(plan_sha, str) or not _HEX64.fullmatch(plan_sha):
        _fail("audio_receipt_plan_hash", "/plan_sha256", "plan_sha256 must be a 64-digit lowercase hex SHA-256 of the audio plan")

    outputs = _list(rec, "outputs", "")
    if not all(isinstance(o, dict) for o in outputs):
        _fail("audio_receipt_shape", "/outputs", "every audio receipt output must be a JSON object")
    stems = [o for o in outputs if o.get("role") == "stem"]
    if len(stems) != 1 or any(o.get("role") == "stem_partial" for o in outputs):
        _fail("audio_receipt_stem_mismatch", "/outputs", "the receipt must describe exactly one final stem and no partial stem output")
    stem = stems[0]
    if stem.get("partial") is not False:
        _fail("audio_receipt_inconsistent", "/outputs/stem/partial", "the stem output must be final: partial must be false")
    if stem.get("sha256") != stem_sha256:
        _fail("audio_receipt_stem_mismatch", "/outputs/stem/sha256", "the receipt does not describe a stem with this audio sha256")
    if stem.get("name") != STEM_NAME or not _int(stem.get("bytes")) or stem["bytes"] != stem_bytes:
        _fail("audio_receipt_stem_mismatch", "/outputs/stem", f"the receipt's stem must be named {STEM_NAME!r} with exactly {stem_bytes} bytes")
    plans = [o for o in outputs if o.get("role") == "plan"]
    if len(plans) != 1 or plans[0].get("name") != PLAN_NAME or plans[0].get("sha256") != plan_sha:
        _fail("audio_receipt_plan_hash", "/outputs/plan", "plan_sha256 must equal the sha256 of the receipt's single plan output (plan.canonical.json)")

    rb = _obj(rec, "readback", "")
    if rb.get("all_ok") is not True:
        _fail("audio_receipt_inconsistent", "/readback/all_ok", "a verified audio receipt must have readback.all_ok == true")
    wav = _obj(rb, "wav", "/readback")
    if not _int(wav.get("sample_count")) or wav["sample_count"] != samples:
        _fail("audio_receipt_sample_mismatch", "/readback/wav/sample_count", f"receipt says {_show(wav.get('sample_count'))} samples, manifest declares {samples}")
    for key, want in (("channels", channels), ("sample_rate", SAMPLE_RATE), ("bits", BITS)):
        if not _int(wav.get(key)) or wav[key] != want:
            _fail("audio_receipt_format_mismatch", f"/readback/wav/{key}", f"receipt's WAV {key} is {_show(wav.get(key))}; the declared stem has {want}")
    if not _int(wav.get("file_bytes")) or wav["file_bytes"] != stem_bytes or wav.get("sha256") != stem_sha256:
        _fail("audio_receipt_stem_mismatch", "/readback/wav", "the receipt's WAV readback (file_bytes / sha256) is not this stem")

    checks = _list(rb, "checks", "/readback")
    by_name = {}
    for c in checks:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or c["name"] in by_name:
            _fail("audio_receipt_shape", "/readback/checks", "readback.checks must be objects with distinct string names")
        by_name[c["name"]] = c
    if set(by_name) != set(READBACK_CHECKS):
        _fail("audio_receipt_shape", "/readback/checks", f"readback.checks must be exactly {sorted(READBACK_CHECKS)}")
    for name, field in READBACK_CHECKS.items():
        c = by_name[name]
        if c.get("ok") is not True or not _int(c.get("expected")) or c["expected"] != c.get("observed") or not _int(c.get("observed")) or c["observed"] != wav.get(field):
            _fail("audio_receipt_inconsistent", f"/readback/checks/{name}", f"readback check {name!r} must be ok, with equal integer expected/observed values that match readback.wav.{field}")


    mix = _obj(rec, "mix", "")
    if not _int(mix.get("clipped_samples")) or mix["clipped_samples"] != 0:
        _fail("audio_receipt_inconsistent", "/mix/clipped_samples", "a MIXED_VERIFIED receipt cannot report clipped samples (that is MIXED_WITH_CLAMPING)")
    if not _int(mix.get("samples_written")) or mix["samples_written"] != samples:
        _fail("audio_receipt_inconsistent", "/mix/samples_written", "mix.samples_written must equal the declared sample count")
    if _obj(rec, "input", "").get("inputs_unchanged") is not True:
        _fail("audio_receipt_inconsistent", "/input/inputs_unchanged", "a verified audio receipt requires input.inputs_unchanged == true")

    warnings, total = [], 0
    for w in _list(rec, "warnings", ""):
        if not isinstance(w, dict) or not isinstance(w.get("code"), str):
            _fail("audio_receipt_shape", "/warnings", "every audio receipt warning must be an object with a string code")
        total += 1
        if len(warnings) < MAX_COPIED_WARNINGS:
            detail = w.get("detail")
            warnings.append({"code": w["code"][:MAX_COPIED_TEXT], "detail": detail[:MAX_COPIED_TEXT] if isinstance(detail, str) else None})
    unresolved = _list(rec, "unresolved_report", "")
    return {"schema": RECEIPT_SCHEMA, "status": S_VERIFIED, "plan_sha256": plan_sha, "mechanical_checks": "PASSED", "authentication": AUTHENTICATION_NOTE,
            "warnings": warnings, "warnings_total": total, "warnings_truncated": total > len(warnings), "unresolved_events": len(unresolved)}
