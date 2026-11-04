#!/usr/bin/env python3
"""Generate the synthetic tone assets and the manifest for the tiny evidence stem. Standalone: imports nothing from video_core.

usage: make_synthetic_assets.py OUT_DIR      (OUT_DIR must not exist yet; creates OUT_DIR/assets/*.wav and OUT_DIR/manifest.json)

Tones are integer-only (no floats), so the bytes are identical on every platform. The WAV files are generated media and are NOT committed;
the manifest (text) is committed and pins their SHA-256.
"""
import hashlib
import json
import os
import struct
import sys

RATE = 48000


def square(freq, n, amp):
    p = RATE // freq
    return [amp if (i % p) < p // 2 else -amp for i in range(n)]


def triangle(freq, n, amp):
    p = RATE // freq
    half = p // 2
    out = []
    for i in range(n):
        ph = i % p
        out.append(-amp + (2 * amp * ph) // half if ph < half else amp - (2 * amp * (ph - half)) // half)
    return out


def wav(samples):
    pcm = b"".join(struct.pack("<h", v) for v in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def main(out):
    os.makedirs(out)
    os.makedirs(os.path.join(out, "assets"))
    files = {"tone_square_480.wav": wav(square(480, 3200, 8000)), "tone_triangle_600.wav": wav(triangle(600, 3200, 6000))}
    for name, data in files.items():
        with open(os.path.join(out, "assets", name), "xb") as fh:
            fh.write(data)
    sha = {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    manifest = {
        "manifest_version": 1,
        "timeline": {"fps": {"n": 30, "d": 1}, "total_frames": 3},
        "output": {"sample_rate": 48000, "channels": 1, "bits": 16},
        "scope": {"event_ids": ["ev_square", "ev_triangle"]},
        "policy": {"gap": "REQUIRE_FULL_COVERAGE", "overlap": "ALLOW_SUM", "clipping": "REJECT"},
        "assets": [{"id": "asset_square", "path": "tone_square_480.wav", "sha256": sha["tone_square_480.wav"]},
                   {"id": "asset_triangle", "path": "tone_triangle_600.wav", "sha256": sha["tone_triangle_600.wav"]}],
        "events": [
            {"id": "ev_square", "source_id": "synthetic_square_480hz", "state": "USE", "asset": "asset_square", "source_in_sample": 0, "source_out_sample": 3200,
             "start_frame": 0, "end_frame": 2, "decision_ref": "synthetic-demo/decision-001", "role": "tone"},
            {"id": "ev_triangle", "source_id": "synthetic_triangle_600hz", "state": "USE", "asset": "asset_triangle", "source_in_sample": 0, "source_out_sample": 3200,
             "start_frame": 1, "end_frame": 3, "gain": {"num": 1, "den": 2}, "decision_ref": "synthetic-demo/decision-002", "role": "tone"},
            {"id": "ev_unresolved_not_selected", "source_id": "synthetic_placeholder", "state": "MISSING", "role": "diegetic"},
        ],
    }
    with open(os.path.join(out, "manifest.json"), "x", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(json.dumps({"assets": sha}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
