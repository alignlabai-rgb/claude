"""A GENUINE audio-planner receipt, produced by the real `video_core.audiomix.mix` API (pure Python, no media process) on a bounded synthetic
tone. Tests mutate deep copies of this document instead of hand-writing receipts, so the fixture cannot drift from what the producer writes."""

from __future__ import annotations

import json
import os

from tests.audio_fixtures import dumps, sha, tone, wav_bytes, write
from video_core.audiomix import mix as amix


def genuine_receipt(workdir: str, samples: int = 4800, channels: int = 1, *, zero_fill_last_frame: bool = False) -> tuple[bytes, dict]:
    """Return (stem wav bytes, receipt dict) for an unclipped mono stem of `samples` samples at 30 fps (samples % 1600 == 0). With
    `zero_fill_last_frame` the last frame is deliberately left uncovered under policy ALLOW_ZERO_FILL, so the producer records a ZERO_FILL_APPLIED warning."""
    assert samples % 1600 == 0
    src_dir, out_dir = os.path.join(workdir, "mix-src"), os.path.join(workdir, "mix-out")
    os.makedirs(src_dir)
    os.makedirs(out_dir)
    frames = samples // 1600
    src = wav_bytes(tone("square", 480, samples, 8000))
    write(os.path.join(src_dir, "src.wav"), src)
    doc = {"manifest_version": 1, "timeline": {"fps": {"n": 30, "d": 1}, "total_frames": frames}, "output": {"sample_rate": 48000, "channels": channels, "bits": 16},
           "scope": {"event_ids": ["u"]}, "policy": {"gap": "ALLOW_ZERO_FILL" if zero_fill_last_frame else "REQUIRE_FULL_COVERAGE"}, "assets": [{"id": "a", "path": "src.wav", "sha256": sha(src)}],
           "events": [{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": samples - (1600 if zero_fill_last_frame else 0), "start_frame": 0, "end_frame": frames - (1 if zero_fill_last_frame else 0)}]}
    if channels == 2:
        raise NotImplementedError("mono only: the existing synthetic helpers build mono sources")
    rec = amix.mix(dumps(doc), asset_root=src_dir, output_root=out_dir, output_name="real")
    if rec["status"] != "MIXED_VERIFIED":  # a broken fixture must fail loudly, not weaken the tests
        raise AssertionError(f"fixture mix did not verify: {rec['status']}")
    with open(os.path.join(out_dir, "real", "stem.wav"), "rb") as fh:
        stem = fh.read()
    with open(os.path.join(out_dir, "real", "receipt.json"), "rb") as fh:
        on_disk = json.loads(fh.read())
    return stem, on_disk
