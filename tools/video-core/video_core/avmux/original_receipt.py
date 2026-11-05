"""Strict, bounded reading of the OPTIONAL original avmux receipt that `reinspect --original-receipt` compares against (pure; no file, no process).

The original receipt is PROVENANCE for a correction, not a qualification: it is read so the new correction receipt can say which earlier receipt it refers to and how the
re-measured packet records differ from what that receipt's summary claimed. Rules, in the order they are applied (every failure is a typed `ValidationFailed`
with path `original_receipt`, raised before any ffprobe/ffmpeg is started and before anything is written):

  original_receipt_unreadable   not strict JSON: empty, BOM, invalid UTF-8, truncated, duplicate keys, NaN/Infinity, runaway nesting, oversized integer literal
  limit_input_bytes             larger than the input limit (raised by the caller, which reads at most limit+1 bytes)
  original_receipt_shape        top level not an object; `outputs` not a list of objects; `observations` / `observations.audio_packets` not objects or null; `status`
                                missing or not an upper-snake string; a present output `bytes` that is not a plain non-negative integer (float, boolean, negative) or `name` that is not a string; `route` / `plan_sha256` present but of the wrong type; the copied packet summary is deeper or larger than
                                the bounds below
  original_receipt_schema       `schema` present but not `video_core.avmux_receipt/1` (a video-render or audio receipt is not an avmux receipt)
  original_receipt_inconsistent a field present in the receipt contradicts another: `written`/`stage`/`quality_evaluated`; a final `media` output together with a status that is
                                not a verified mux (the producer only promotes `media` for MUXED_VERIFIED_SAMPLE_EXACT / MUXED_TECHNICAL_WITHIN_TOLERANCE);
                                `plan_sha256` different from the `plan` output's sha256; a present `partial` that is not exactly false on the media/plan output
  original_receipt_media_mismatch  not exactly one `media` output, or its sha256 / (when present) `bytes` is not the media being re-inspected

Compatibility rule (deliberate): fields the real producer always writes (`schema`, `written`, `stage`, `quality_evaluated`, `route`, `plan_sha256`) are OPTIONAL here, so the
small hand-written receipts used by earlier tests and tooling still work, but a present-and-wrong value is rejected. Receipts NOT accepted any more, and why: ones whose top
level, `outputs`, `observations` or `status` have the wrong JSON type (they crashed with AttributeError before); ones with duplicate keys, NaN/Infinity or runaway nesting (they
were silently accepted or crashed with RecursionError, and a duplicate key makes "which value was meant" a guess); ones that claim a final `media` output while their own status says
the mux was not verified (incomplete evidence must not be re-labelled as a deliverable's receipt); ones with a foreign `schema`. No receipt written by `video_core.avmux.mux` is excluded.

Output claims (`outputs[media|plan]`): `partial`, `name` and `bytes` are the producer's own per-file claims. They are OPTIONAL (legacy hand-written receipts omit them) but, by the
rule above, a present value must be right: `partial` exactly false (a final output is never partial), `bytes` a plain non-negative integer (not a float, boolean or negative number) and,
for the media, equal to the real size of the file being re-inspected. They are consistency checks only; the SHA-256 match is what ties the receipt to the media. The copied
`observations.audio_packets` summary is a CLAIM: it is recorded as `claimed_audio_packets` with a `claimed_audio_packets_basis` that says CLAIMED_UNVERIFIED, and it is never used as a measurement.

Status is kept VERBATIM and never promoted or rewritten: the correction receipt records the original status string exactly as the receipt says it, and a receipt whose status is
not a verified mux (HOLD, UNVERIFIED, IN_PROGRESS, FAILED/PROCESS_FAILED, INTERRUPTED, UNKNOWN, READBACK_MISMATCH, ...) is refused when it also lists a final `media` output; without one it
fails the media check anyway. The correction receipt's own status stays REINSPECTED / UNKNOWN / SKIPPED_TOOL_UNAVAILABLE and never becomes a success of the original. A caller-supplied hash is byte integrity of the file the caller named, not authenticity: nothing here is signed, and `authentication` says so.
"""

from __future__ import annotations

import json
import re

from ..episode import parse_json_bytes
from ..errors import Issue, ValidationFailed
from .manifest import ROUTES
from .mux import OK_STATUSES, RECEIPT_SCHEMA

_STATUS = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SHA = re.compile(r"[0-9a-fA-F]{64}")
MAX_SUMMARY_DEPTH = 16
MAX_SUMMARY_JSON_BYTES = 1024 * 1024  # a producer summary is ~1 KB, at most ~250 KB with the 1000-record cap (checks.MAX_SIDE_DATA_RECORDS); bounds what is copied into the new receipt
CLAIM_BASIS = ("CLAIMED_UNVERIFIED: copied exactly as the original receipt wrote it (bounded); the re-measured values are in audio_packets, and "
               "nothing here confirms the original claim, who made it, or that it describes this file beyond the SHA-256 match")
AUTHENTICATION = ("NONE: the original receipt is a caller-named file; its SHA-256 proves which bytes were read, not who produced or approved them, "
                  "and it is not evidence of creative adoption or quality")


def _fail(code: str, message: str):
    raise ValidationFailed(message, [Issue(code, "original_receipt", message)])


def _depth(node, limit: int) -> int:
    """Iterative depth (no recursion), stopping early once `limit` is exceeded."""
    deepest, stack = 0, [(node, 1)]
    while stack:
        n, d = stack.pop()
        if isinstance(n, (dict, list)):
            deepest = max(deepest, d)
            if deepest > limit:
                return deepest
            stack.extend((c, d + 1) for c in (n.values() if isinstance(n, dict) else n))
    return deepest


def read_original_receipt(blob: bytes, *, expected_sha256: str, limits, media_bytes: int | None = None) -> dict:
    """Parse and check `blob` (the exact bytes of the file) and return the facts the correction receipt records. `expected_sha256` is the media's lower-case SHA-256;
    `media_bytes` (optional) is the media's real size, against which a present `outputs[media].bytes` claim is compared."""
    try:
        doc = parse_json_bytes(blob, limits)  # strict: UTF-8, no BOM, no duplicate keys, no NaN/Infinity, nesting and integer limits -> ValidationFailed
    except ValidationFailed as exc:
        if exc.issues and exc.issues[0].code == "limit_input_bytes":
            raise
        _fail("original_receipt_unreadable", f"the original receipt is not strict JSON ({exc.issues[0].code if exc.issues else 'json_invalid'})")
    if not isinstance(doc, dict):
        _fail("original_receipt_shape", "the original receipt must be a JSON object")
    status = doc.get("status")
    if not isinstance(status, str) or not _STATUS.fullmatch(status):
        _fail("original_receipt_shape", "the original receipt's status must be an upper-case string such as MUXED_VERIFIED_SAMPLE_EXACT (it is recorded verbatim, never promoted)")
    schema = doc.get("schema")
    if schema is not None and schema != RECEIPT_SCHEMA:
        _fail("original_receipt_schema", f"the original receipt's schema must be {RECEIPT_SCHEMA!r} when present")
    route = doc.get("route")
    if route is not None and (not isinstance(route, str) or route not in ROUTES):
        _fail("original_receipt_shape", f"the original receipt's route must be one of {list(ROUTES)} when present")
    plan_sha = doc.get("plan_sha256")
    if plan_sha is not None and (not isinstance(plan_sha, str) or not _SHA.fullmatch(plan_sha)):
        _fail("original_receipt_shape", "the original receipt's plan_sha256 must be a 64-digit hex string when present")
    outputs = doc.get("outputs", [])
    if not isinstance(outputs, list) or not all(isinstance(o, dict) for o in outputs):
        _fail("original_receipt_shape", "the original receipt's outputs must be a list of JSON objects")
    obs = doc.get("observations")
    if obs is not None and not isinstance(obs, dict):
        _fail("original_receipt_shape", "the original receipt's observations must be a JSON object when present")
    claimed = (obs or {}).get("audio_packets")
    if claimed is not None:
        if not isinstance(claimed, dict):
            _fail("original_receipt_shape", "observations.audio_packets must be a JSON object when present")
        if _depth(claimed, MAX_SUMMARY_DEPTH) > MAX_SUMMARY_DEPTH or len(json.dumps(claimed)) > MAX_SUMMARY_JSON_BYTES:
            _fail("original_receipt_shape", f"observations.audio_packets is deeper than {MAX_SUMMARY_DEPTH} levels or larger than {MAX_SUMMARY_JSON_BYTES} bytes")
    for key, want in (("written", True), ("quality_evaluated", False)):
        if key in doc and doc[key] is not want:
            _fail("original_receipt_inconsistent", f"the original receipt's {key} must be {str(want).lower()} when present")
    if "stage" in doc and not isinstance(doc["stage"], str):
        _fail("original_receipt_shape", "the original receipt's stage must be a string when present")

    media = [o for o in outputs if o.get("role") == "media"]
    if len(media) != 1 or media[0].get("sha256") != expected_sha256:
        _fail("original_receipt_media_mismatch", "the original receipt does not describe a media output with this sha256")
    if status not in OK_STATUSES:
        _fail("original_receipt_inconsistent", f"the original receipt lists a final media output but its status is {status!r}; only a verified mux "
              f"({', '.join(OK_STATUSES)}) has one, so this is not a receipt the muxer wrote")
    if "stage" in doc and doc["stage"] != "complete":
        _fail("original_receipt_inconsistent", "a verified mux receipt has stage == 'complete'")
    plan_outputs = [o for o in outputs if o.get("role") == "plan"]
    if plan_sha is not None and plan_outputs and (len(plan_outputs) != 1 or str(plan_outputs[0].get("sha256", "")).lower() != plan_sha.lower()):
        _fail("original_receipt_inconsistent", "plan_sha256 must equal the sha256 of the receipt's single plan output")
    for label, o in [("media", media[0])] + [("plan", p) for p in plan_outputs]:
        # present-and-wrong is rejected (same rule as the top-level optional fields); absent stays allowed so hand-written legacy receipts keep working
        if "partial" in o and o["partial"] is not False:
            _fail("original_receipt_inconsistent", f"outputs[{label}].partial must be false when present: a final {label} output is never partial")
        if "name" in o and not isinstance(o["name"], str):
            _fail("original_receipt_shape", f"outputs[{label}].name must be a string when present")
        if "bytes" in o:
            if type(o["bytes"]) is not int or o["bytes"] < 0:
                _fail("original_receipt_shape", f"outputs[{label}].bytes must be a non-negative integer when present (not a float, boolean or negative number)")
            if label == "media" and media_bytes is not None and o["bytes"] != media_bytes:
                _fail("original_receipt_media_mismatch", f"outputs[media].bytes says {o['bytes']} but the media being re-inspected is {media_bytes} bytes")
    return {"schema": schema, "status": status, "route": route, "plan_sha256": plan_sha, "claimed_audio_packets": claimed,
            "claimed_audio_packets_basis": CLAIM_BASIS, "authentication": AUTHENTICATION}
