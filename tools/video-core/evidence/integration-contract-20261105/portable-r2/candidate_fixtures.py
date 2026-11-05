"""Fixtures shared by the supplementary candidate tools (Python 3.11+ stdlib only). Nothing here is part of the closed `portable/` harness, which stays untouched.

THREE receipt classes are kept strictly apart (the names are used in every result):
  GENUINE                 the committed receipt that the real mixer wrote (evidence/stage-b-audio-stem/receipt.stem-demo.json, sha256-pinned) together with a stem whose
                          bytes are PROVEN identical (sha256 68f8180e... == the recorded stem hash) to the genuine one. The stem is reproduced by independent integer
                          arithmetic from the documented construction - `audiomix mix` is never run. Qualification limit: the bytes are the genuine bytes, but this process did
                          not produce them via the producer, so only hash identity - not provenance - links them.
  FABRICATED_STUB         shallow hand-made receipts (three fields; the closed harness's shape-faithful stub; a self-contradicting edit). Never produced by a mixer.
  SELF_CONSISTENT_FORGERY a hand-made receipt built so that EVERY mechanical check of the candidate passes for a given stem. Acceptance of this class is NOT evidence of
                          provenance: nothing in the library authenticates a receipt (INPUT_CONTRACT.md says so itself).
"""
from __future__ import annotations

import copy
import hashlib
import json
import struct

RATE = 48000
GENUINE_STEM_SHA256 = "68f8180e0630509d8743c11c38de674481e3f711c54c12bffa996c0af1c8ba88"
GENUINE_STEM_BYTES = 9644
GENUINE_RECEIPT_SHA256_LF = "f87030ea130a55805a400454b48c00fa7d88ab7ceb3313ff36a10df1a37c1711"       # sha256 of the committed file with CRLF normalised to LF
GENUINE_RECEIPT_RELPATH = ("evidence", "stage-b-audio-stem", "receipt.stem-demo.json")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def genuine_stem_bytes() -> bytes:
    """480 Hz square (amp 8000) on samples [0,3200) + 600 Hz triangle (amp 6000, gain 1/2, round-half-away) on [1600,4800), mono 48 kHz s16le: the documented construction."""
    def square(f, n, a):
        p = RATE // f
        return [a if (i % p) < p // 2 else -a for i in range(n)]

    def tri(f, n, a):
        p = RATE // f
        h = p // 2
        return [(-a + (2 * a * (i % p)) // h) if (i % p) < h else (a - (2 * a * ((i % p) - h)) // h) for i in range(n)]

    def half(v):
        q, r = divmod(abs(v), 2)
        q += 1 if r else 0
        return q if v >= 0 else -q
    out = [0] * 4800
    for i, v in enumerate(square(480, 3200, 8000)):
        out[i] += v
    for i, v in enumerate(tri(600, 3200, 6000)):
        out[1600 + i] += half(v)
    pcm = b"".join(struct.pack("<h", v) for v in out)
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def forged_receipt(stem: bytes, *, samples: int, channels: int = 1, warnings=(), unresolved=(), note="SELF_CONSISTENT_FORGERY - hand-made, never produced by a mixer") -> dict:
    """A receipt that satisfies every mechanical check of video_core.avmux.receipt_check for `stem`. It proves nothing about who made it."""
    s = sha256(stem)
    plan_bytes = b"HARNESS-FORGERY plan.canonical.json placeholder"
    plan_sha = sha256(plan_bytes)
    wav = {"sample_rate": RATE, "channels": channels, "bits": 16, "sample_count": samples, "file_bytes": len(stem), "sha256": s, "data_bytes": len(stem) - 44, "data_offset": 44}
    checks = [{"name": n, "expected": wav[n], "observed": wav[n], "ok": True} for n in ("channels", "sample_rate", "bits", "sample_count", "file_bytes")]
    return {"_fixture_note": note, "schema": "video_core.audio_receipt/1", "status": "MIXED_VERIFIED", "written": True, "stage": "complete", "quality_evaluated": False, "plan_sha256": plan_sha,
            "input": {"manifest_sha256": "e" * 64, "manifest_bytes": 1, "inputs_unchanged": True, "assets": []},
            "mix": {"clipped_samples": 0, "samples_written": samples, "chunks": 1},
            "outputs": [{"name": "stem.wav", "role": "stem", "bytes": len(stem), "sha256": s, "partial": False},
                        {"name": "plan.canonical.json", "role": "plan", "bytes": len(plan_bytes), "sha256": plan_sha, "partial": False}],
            "readback": {"all_ok": True, "checks": checks, "wav": wav}, "warnings": [dict(w) for w in warnings], "unresolved_report": [dict(u) for u in unresolved]}


def jbytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def tamper(genuine_doc: dict, how: str) -> dict:
    d = copy.deepcopy(genuine_doc)
    if how == "all_ok_false":
        d["readback"]["all_ok"] = False
    elif how == "stem_partial_true":
        [o for o in d["outputs"] if o["role"] == "stem"][0]["partial"] = True
    elif how == "sample_count_float":
        d["readback"]["wav"]["sample_count"] = 4800.0
    elif how == "inputs_unchanged_false":
        d["input"]["inputs_unchanged"] = False
    elif how == "quality_evaluated_true":
        d["quality_evaluated"] = True
    else:
        raise ValueError(how)
    return d
