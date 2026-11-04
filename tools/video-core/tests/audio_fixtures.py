"""Synthetic audio fixtures. Tones use integer arithmetic only (no floats, no math.sin) so every byte is reproducible on any platform.
The WAV writer here is deliberately independent of video_core.audiomix.wav so engine and fixtures cross-check each other."""

from __future__ import annotations

import hashlib
import json
import os
import struct

RATE = 48_000


def tone(kind: str, freq: int, samples: int, amp: int = 1000) -> list:
    """Mono tone. `freq` must divide 48000 so the period is an exact integer number of samples."""
    assert RATE % freq == 0
    p = RATE // freq
    out = []
    for i in range(samples):
        ph = i % p
        if kind == "square":
            out.append(amp if ph < p // 2 else -amp)
        elif kind == "triangle":
            half = p // 2
            out.append(-amp + (2 * amp * ph) // half if ph < half else amp - (2 * amp * (ph - half)) // half)
        elif kind == "saw":
            out.append(-amp + (2 * amp * ph) // p)
        elif kind == "dc":
            out.append(amp)
        else:
            raise ValueError(kind)
    return out


def interleave(*channels) -> list:
    return [v for frame in zip(*channels) for v in frame]


def wav_bytes(samples, channels=1, *, rate=RATE, bits=16, tag=1, block_align=None, byte_rate=None, extra_before=b"", extra_after=b"",
              riff_delta=0, data_size_delta=0, fmt_size=16, trunc=0) -> bytes:
    """Build a WAV; the keyword overrides exist to make *lying* or unsupported files on purpose."""
    pcm = b"".join(struct.pack("<h", v) for v in samples) if bits == 16 else bytes(len(samples) * bits // 8)
    ba = channels * bits // 8 if block_align is None else block_align
    br = rate * ba if byte_rate is None else byte_rate
    fmt = struct.pack("<HHIIHH", tag, channels, rate, br, ba, bits) + (b"\x00\x00" if fmt_size == 18 else b"")
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra_before
    body += b"data" + struct.pack("<I", len(pcm) + data_size_delta) + pcm + (b"\x00" if len(pcm) & 1 else b"") + extra_after
    data = b"RIFF" + struct.pack("<I", len(body) + riff_delta) + body
    return data[: len(data) - trunc] if trunc else data


def list_chunk(payload: bytes) -> bytes:
    return b"LIST" + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def event_use(eid, asset, s_in, s_out, start, end, *, source_id=None, gain=None, rule=None, **extra):
    e = {"id": eid, "source_id": source_id or f"src_{eid}", "state": "USE", "asset": asset, "source_in_sample": s_in, "source_out_sample": s_out,
         "start_frame": start, "end_frame": end}
    if gain:
        e["gain"] = {"num": gain[0], "den": gain[1]}
    if rule:
        e["channel_rule"] = rule
    e.update(extra)
    return e


def manifest(events, assets, scope=None, *, fps=(30, 1), total=3, channels=1, gap="ALLOW_ZERO_FILL", overlap=None, clipping=None, **top) -> dict:
    pol = {"gap": gap}
    if overlap:
        pol["overlap"] = overlap
    if clipping:
        pol["clipping"] = clipping
    doc = {"manifest_version": 1, "timeline": {"fps": {"n": fps[0], "d": fps[1]}, "total_frames": total},
           "output": {"sample_rate": RATE, "channels": channels, "bits": 16},
           "scope": {"event_ids": scope if scope is not None else [e["id"] for e in events]}, "policy": pol, "assets": assets, "events": events}
    doc.update(top)
    return doc


def asset(aid, path, data: bytes) -> dict:
    return {"id": aid, "path": path, "sha256": sha(data)}


def dumps(doc) -> bytes:
    try:
        return json.dumps(doc, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates can only travel as \\uXXXX escapes
        return json.dumps(doc, ensure_ascii=True).encode("ascii")
