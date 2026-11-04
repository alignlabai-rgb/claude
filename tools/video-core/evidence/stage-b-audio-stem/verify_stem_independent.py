#!/usr/bin/env python3
"""Independent verification of the evidence stem. Imports NOTHING from video_core.

Parses the RIFF/WAVE bytes with `struct`, cross-checks with the stdlib `wave` module, and re-derives every expected sample from the
documented construction (own integer tone code and own half-away-from-zero rounding with fractions): 480 Hz square amp 8000 on
samples [0,3200) plus 600 Hz triangle amp 6000 at gain 1/2 on [1600,4800). Prints JSON; exit 0 only if every check holds.
This is a structural/numeric check of a synthetic stem; it is not an audio-quality judgement.

usage: verify_stem_independent.py STEM.wav
"""
import hashlib
import io
import json
import struct
import sys
import wave
from fractions import Fraction
from math import floor

RATE = 48000


def square(freq, n, amp):
    p = RATE // freq
    return [amp if (i % p) < p // 2 else -amp for i in range(n)]


def triangle(freq, n, amp):
    p, out = RATE // freq, []
    half = p // 2
    for i in range(n):
        ph = i % p
        out.append(-amp + (2 * amp * ph) // half if ph < half else amp - (2 * amp * (ph - half)) // half)
    return out


def half_away(num, den):
    x = Fraction(num, den)
    r = floor(abs(x) + Fraction(1, 2))
    return r if x >= 0 else -r


def main(path):
    raw = open(path, "rb").read()
    checks = []

    def check(name, expected, observed):
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": expected == observed})

    n = 4800
    check("file_bytes", 44 + n * 2, len(raw))
    check("riff_magic", b"RIFF".hex(), raw[0:4].hex())
    check("riff_size_field", 36 + n * 2, struct.unpack("<I", raw[4:8])[0])
    check("wave_magic", b"WAVE".hex(), raw[8:12].hex())
    check("fmt_tag", b"fmt ".hex(), raw[12:16].hex())
    check("fmt_size", 16, struct.unpack("<I", raw[16:20])[0])
    audio_format, channels, rate, byte_rate, block_align, bits = struct.unpack("<HHIIHH", raw[20:36])
    check("audio_format_pcm", 1, audio_format)
    check("channels", 1, channels)
    check("sample_rate", 48000, rate)
    check("byte_rate", 96000, byte_rate)
    check("block_align", 2, block_align)
    check("bits_per_sample", 16, bits)
    check("data_tag", b"data".hex(), raw[36:40].hex())
    check("data_size_field", n * 2, struct.unpack("<I", raw[40:44])[0])
    samples = list(struct.unpack(f"<{(len(raw) - 44) // 2}h", raw[44:]))
    check("sample_count", n, len(samples))

    expected = [0] * n
    for i, v in enumerate(square(480, 3200, 8000)):
        expected[i] += v
    for i, v in enumerate(triangle(600, 3200, 6000)):
        expected[1600 + i] += half_away(v, 2)
    mismatches = [i for i in range(min(n, len(samples))) if samples[i] != expected[i]]
    check("all_samples_equal_expected", 0, len(mismatches))
    check("peak_abs", max(abs(v) for v in expected), max(abs(v) for v in samples))
    check("first_region_only_square", True, samples[:1600] == expected[:1600] == square(480, 1600, 8000))
    check("overlap_region_is_the_sum", True, samples[1600:3200] == [a + b for a, b in zip(square(480, 3200, 8000)[1600:3200], [half_away(v, 2) for v in triangle(600, 1600, 6000)])])
    with wave.open(io.BytesIO(raw)) as w:
        check("stdlib_wave_params", [1, 2, 48000, n], [w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()])
    out = {"file": path.replace("\\", "/").rsplit("/", 1)[-1], "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "checks": checks,
           "first_mismatch_indices": mismatches[:10], "all_ok": all(c["ok"] for c in checks),
           "note": "Structural and numeric verification of a synthetic stem against its documented construction; no audio-quality judgement."}
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0 if out["all_ok"] else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
