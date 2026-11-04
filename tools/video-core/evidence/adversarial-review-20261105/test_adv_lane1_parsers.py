"""Lane 1 - manifest / JSON / WAV / SRT parsers and numeric algebra (public API only).

Deterministic seeds are recorded in observations.jsonl. Nothing here edits production code; tests marked @known_defect assert the
CORRECT behaviour and currently fail because the defect is real (see FINDINGS.json).
"""

from __future__ import annotations

import io
import json
import os
import struct
import time
import unittest
from fractions import Fraction

import adv_oracle as O
from adv_support import (AuditCase, PY, TOOLS, audited, avmux_manifest, chunk, cli, fmt_chunk, jb, known_defect, manifest, pack_pcm, pcm_tone,
                         record, riff, sfnt_font, sha256, wav_from_frames)

from video_core import errors, fontcheck, numeric  # noqa: E402  (path set by adv_support)
from video_core.audiomix import manifest as amanifest, plan as aplan, wav as awav  # noqa: E402
from video_core.audiomix.limits import AudioLimits  # noqa: E402
from video_core.avmux import manifest as vmanifest, plan as vplan  # noqa: E402
from video_core.episode import load_episode  # noqa: E402
from video_core.limits import Limits  # noqa: E402

SEED_WAV, SEED_EP, SEED_NUM = 20261105, 20261106, 20261107


def example_episode() -> dict:
    with open(os.path.join(TOOLS, "examples", "episode.synthetic.json"), encoding="utf-8") as fh:
        return json.load(fh)


def parse(data: bytes):
    try:
        info = awav.parse_wav(io.BytesIO(data), len(data))
        return "ok", info.sample_count
    except errors.ValidationFailed as exc:
        return "reject", [i.code for i in exc.issues][0]


# --------------------------------------------------------------------------------------------------------------------------- WAV
class WavParser(AuditCase):
    def _corpus(self):
        base1 = wav_from_frames(pcm_tone(8, 1, 1), 1)
        base2 = wav_from_frames(pcm_tone(8, 2, 2), 2)
        corpus = {}
        for tag, base in (("m", base1), ("s", base2)):
            corpus[f"{tag}:valid"] = base
            for cut in range(len(base)):  # every truncation point
                corpus[f"{tag}:trunc@{cut}"] = base[:cut]
            for extra in (1, 2, 3, 4, 7, 8, 9, 100):
                corpus[f"{tag}:trailing{extra}"] = base + b"\x00" * extra
            r = O.Rng(SEED_WAV + len(base))
            for off in range(44):  # every header byte flipped to three seeded values
                for _ in range(3):
                    b = bytearray(base)
                    b[off] = (b[off] ^ (1 + r.below(255))) & 0xFF
                    corpus[f"{tag}:flip@{off}:{b[off]:02x}"] = bytes(b)
        ch = 1
        pcm = pack_pcm(pcm_tone(8, 1, 3))
        fmt = fmt_chunk(channels=1)
        corpus.update({
            "list-before-fmt-even": riff([chunk(b"LIST", b"abcd"), fmt, chunk(b"data", pcm)]),
            "list-between-odd-padded": riff([fmt, chunk(b"LIST", b"abc"), chunk(b"data", pcm)]),
            "list-between-odd-NOT-padded": riff([fmt, chunk(b"LIST", b"abc", pad=False), chunk(b"data", pcm)]),
            "list-after-data": riff([fmt, chunk(b"data", pcm), chunk(b"LIST", b"xy")]),
            "list-after-data-odd-padded": riff([fmt, chunk(b"data", pcm), chunk(b"LIST", b"x")]),
            "list-after-data-odd-unpadded": riff([fmt, chunk(b"data", pcm), chunk(b"LIST", b"x", pad=False)]),
            "fmt18-cb0": riff([fmt_chunk(cb=0), chunk(b"data", pcm)]),
            "fmt18-cb2": riff([fmt_chunk(cb=2), chunk(b"data", pcm)]),
            "fmt-ext40": riff([chunk(b"fmt ", b"\x01\x00" * 20), chunk(b"data", pcm)]),
            "dup-fmt": riff([fmt, fmt, chunk(b"data", pcm)]),
            "dup-data": riff([fmt, chunk(b"data", pcm), chunk(b"data", pcm)]),
            "fmt-after-data": riff([chunk(b"data", pcm), fmt]),
            "data-only": riff([chunk(b"data", pcm)]),
            "fmt-only": riff([fmt]),
            "empty-riff": riff([]),
            "data-size-0": riff([fmt, chunk(b"data", b"")]),
            "data-odd-bytes-mono": riff([fmt, chunk(b"data", pcm + b"\x01")]),
            "data-size-ffffffff": riff([fmt, chunk(b"data", pcm, size=0xFFFFFFFF)]),
            "data-size-past-end": riff([fmt, chunk(b"data", pcm, size=len(pcm) + 2)]),
            "data-size-short-then-garbage": riff([fmt, chunk(b"data", pcm, size=len(pcm) - 2, pad=False)], trailing=b""),
            "riff-size-0xffffffff": riff([fmt, chunk(b"data", pcm)], riff_size=0xFFFFFFFF),
            "riff-size-minus1": riff([fmt, chunk(b"data", pcm)], riff_size=4 + len(fmt) + 8 + len(pcm) - 1),
            "riff-size-plus1": riff([fmt, chunk(b"data", pcm)], riff_size=4 + len(fmt) + 8 + len(pcm) + 1),
            "riff-size-zero": riff([fmt, chunk(b"data", pcm)], riff_size=0),
            "tag-null-bytes": riff([fmt, chunk(b"\x00\x00\x00\x00", b"zz"), chunk(b"data", pcm)]),
            "chunks-64": riff([fmt] + [chunk(b"JUNK", b"") for _ in range(61)] + [chunk(b"data", pcm)]),   # 63 chunks total
            "chunks-64-exact": riff([fmt] + [chunk(b"JUNK", b"") for _ in range(62)] + [chunk(b"data", pcm)]),  # 64
            "chunks-65": riff([fmt] + [chunk(b"JUNK", b"") for _ in range(63)] + [chunk(b"data", pcm)]),
            "rf64": b"RF64" + riff([fmt, chunk(b"data", pcm)])[4:],
            "rifx": b"RIFX" + riff([fmt, chunk(b"data", pcm)])[4:],
            "wave-tag-wrong": riff([fmt, chunk(b"data", pcm)]).replace(b"WAVE", b"WAVX", 1),
            "float32-tag3": riff([fmt_chunk(tag=3, bits=32), chunk(b"data", pcm)]),
            "alaw-tag6": riff([fmt_chunk(tag=6, bits=8), chunk(b"data", pcm)]),
            "rate-44100": riff([fmt_chunk(rate=44100), chunk(b"data", pcm)]),
            "bits-24": riff([fmt_chunk(bits=24), chunk(b"data", pcm)]),
            "channels-0": riff([fmt_chunk(channels=0, align=0, byte_rate=0), chunk(b"data", pcm)]),
            "channels-3": riff([fmt_chunk(channels=3), chunk(b"data", pcm * 3)]),
            "align-lies": riff([fmt_chunk(align=4), chunk(b"data", pcm)]),
            "byterate-lies": riff([fmt_chunk(byte_rate=1), chunk(b"data", pcm)]),
            "stereo-data-3-bytes": riff([fmt_chunk(channels=2), chunk(b"data", b"\x00\x00\x00")]),
            "stereo-ok": riff([fmt_chunk(channels=2), chunk(b"data", pcm * 2)]),
        })
        return corpus

    def test_wav_differential_against_independent_oracle(self):
        corpus = self._corpus()
        disagreements, acc = [], 0
        for name, data in corpus.items():
            want = O.wav_oracle(data)
            got = parse(data)
            if want[0] == "ok":
                acc += 1
            if (want[0], got[0]) != (want[0], want[0]) or (want[0] == "ok" and got[1] != want[1]["samples"]):
                disagreements.append((name, want, got))
        record("wav_differential", seed=SEED_WAV, cases=len(corpus), accepted_by_oracle=acc, disagreements=len(disagreements))
        self.assertGreater(len(corpus), 400)
        self.assertEqual(disagreements, [], "implementation and independent oracle disagree on WAV acceptance")

    def test_wav_known_documented_strictness_points(self):
        """Spot checks of decisions that are easy to get wrong; these pin the *documented* behaviour."""
        pcm = pack_pcm(pcm_tone(4, 1, 5))
        fmt = fmt_chunk()
        self.assertEqual(parse(riff([fmt, chunk(b"data", pcm), chunk(b"LIST", b"x", pad=False)]))[0], "reject", "odd final chunk without pad byte is rejected (documented)")
        self.assertEqual(parse(riff([fmt, chunk(b"data", pcm), chunk(b"LIST", b"x")]))[0], "ok")
        self.assertEqual(parse(riff([fmt, chunk(b"data", b"")])), ("ok", 0), "zero-sample data chunk parses (consumers must range-check)")
        self.assertEqual(parse(riff([fmt_chunk(cb=0), chunk(b"data", pcm)]))[0], "ok")
        # 64 chunks exactly is accepted, 65 rejected (limits.max_wav_chunks)
        self.assertEqual(parse(riff([fmt] + [chunk(b"JUNK", b"") for _ in range(62)] + [chunk(b"data", pcm)]))[0], "ok")
        self.assertEqual(parse(riff([fmt] + [chunk(b"JUNK", b"") for _ in range(63)] + [chunk(b"data", pcm)])), ("reject", "wav_too_many_chunks"))

    def test_zero_sample_wav_cannot_be_bound_by_a_use_event(self):
        """data chunk of 0 samples is a valid WAV; a USE event must be rejected by the range check, never read."""
        wav0 = riff([fmt_chunk(), chunk(b"data", b"")])
        sb = self.sb
        sb.write("assets/z.wav", wav0)
        doc = manifest(total=1, assets=[{"id": "z", "path": "z.wav", "sha256": sha256(wav0)}],
                       events=[{"id": "e", "source_id": "s", "state": "USE", "asset": "z", "source_in_sample": 0, "source_out_sample": 1600, "start_frame": 0, "end_frame": 1}])
        with self.assertRaises(errors.ValidationFailed) as cm:
            aplan.prepare_plan(jb(doc), asset_root=sb.p("assets"))
        self.assertEqual([i.code for i in cm.exception.issues], ["source_range_out_of_bounds"])


# --------------------------------------------------------------------------------------------------------------------------- JSON strictness
def _valid_docs():
    ep = example_episode()
    ev = [{"id": "e1", "source_id": "s", "state": "UNKNOWN"}]
    am = manifest(events=ev)
    av = avmux_manifest(wav_bytes_=b"x", samples=4800)
    return {"episode": (ep, lambda b: load_episode(b)), "audio": (am, lambda b: amanifest.load_manifest(b)),
            "avmux": (av, lambda b: vmanifest.load_manifest(b, Limits()))}


class JsonStrictness(AuditCase):
    def _mutations(self, kind, doc):
        base = json.dumps(doc, ensure_ascii=False)
        first_key = next(iter(doc))
        muts = {
            "valid": base,
            "duplicate-key": base[:-1] + f', {json.dumps(first_key)}: 1' + "}",
            "duplicate-key-escaped": base[:-1] + ', "' + "".join("\\u%04x" % ord(c) for c in first_key) + '": 1}',
            "nan": base.replace("1", "NaN", 1),
            "infinity": base.replace("1", "Infinity", 1),
            "neg-infinity": base.replace("1", "-Infinity", 1),
            "exp-overflow-float": base.replace("1", "1e999", 1),
            "float-for-int": base.replace(": 1,", ": 1.0,", 1) if ": 1," in base else base.replace("1", "1.0", 1),
            "bool-for-int": base.replace(": 1,", ": true,", 1) if ": 1," in base else base.replace("1", "true", 1),
            "null-top": "null", "list-top": "[]", "number-top": "7", "string-top": '"x"', "empty": "", "whitespace": "  \n",
            "trailing-garbage": base + " x", "trailing-comma": base[:-1] + ",}",
            "bom": "﻿" + base,
            "lone-surrogate-key": base[:-1] + ', "\\ud800": 1}',
            "int-4301-digits": base.replace("1", "1" + "0" * 4300, 1),
            "int-2pow63": base.replace("1", str(2**63), 1),
        }
        out = {k: v.encode("utf-8") for k, v in muts.items()}
        out["invalid-utf8"] = base.encode("utf-8") + b"\xff"
        out["overlong-utf8-slash"] = base.encode("utf-8").replace(b'"', b'"\xc0\xaf', 1)
        out["bom-bytes"] = b"\xef\xbb\xbf" + base.encode("utf-8")
        out["nul-byte"] = base.encode("utf-8")[:-1] + b"\x00}"
        return out

    def test_every_loader_raises_only_typed_validation_errors(self):
        bad, total = [], 0
        for kind, (doc, loader) in _valid_docs().items():
            for name, raw in self._mutations(kind, doc).items():
                total += 1
                try:
                    loader(raw)
                    verdict = "accepted"
                except errors.ValidationFailed:
                    verdict = "rejected"
                except Exception as exc:  # any other exception class is a contract leak
                    verdict = f"LEAK {type(exc).__name__}"
                if name == "valid" and kind != "avmux":
                    if verdict != "accepted":
                        bad.append((kind, name, verdict))
                elif name == "valid":
                    pass  # avmux manifest needs files only for plan; load_manifest accepts it
                elif verdict != "rejected":
                    bad.append((kind, name, verdict))
        record("json_strictness", cases=total, bad=len(bad))
        self.assertEqual(bad, [])

    def test_valid_docs_are_accepted(self):
        for kind, (doc, loader) in _valid_docs().items():
            loader(json.dumps(doc).encode())

    def test_deep_nesting_is_rejected_cleanly_in_a_bounded_child(self):
        body = r'''
        import json
        from video_core import errors
        from video_core.episode import load_episode
        from video_core.audiomix.manifest import load_manifest as lam
        from video_core.avmux.manifest import load_manifest as lvm
        from video_core.limits import Limits
        res = {}
        for name, payload in (("arrays", b"[" * 150000 + b"]" * 150000), ("objects", b'{"a":' * 60000 + b"1" + b"}" * 60000),
                              ("array-in-valid-key", b'{"version":' + b"[" * 100000 + b"]" * 100000 + b"}")):
            for loader_name, loader in (("episode", load_episode), ("audio", lam), ("avmux", lambda b: lvm(b, Limits()))):
                try:
                    loader(payload); res[name + "/" + loader_name] = "accepted"
                except errors.ValidationFailed as exc:
                    res[name + "/" + loader_name] = "rejected:" + exc.issues[0].code
                except BaseException as exc:
                    res[name + "/" + loader_name] = "LEAK:" + type(exc).__name__
        print(json.dumps(res))
        '''
        rc, out, err, events, timed_out = audited(body, timeout=9)
        self.assertFalse(timed_out)
        self.assertEqual(rc, 0, err[-400:])
        res = json.loads(out)
        record("deep_nesting", results=res, side_effect_events=events)
        self.assertTrue(all(v.startswith("rejected:") for v in res.values()), res)
        self.assertEqual(events, [])

    def test_decoder_digit_limit_is_the_only_integer_bomb_defence(self):
        """Environment-conditional observation, not a defect. Default interpreters refuse integer literals above 4300 digits (asserted below).
        With PYTHONINTMAXSTRDIGITS=0 the manifest parser (cap: 4 MiB) pays integer-conversion cost before any validation: measured here at
        100k and 200k digits only (bounded child); larger sizes were measured once by hand (see REPORT, 3.11: 1M digits = 4.3 s) and extrapolated."""
        body = r'''
        import json, time
        from video_core import errors
        from video_core.audiomix.manifest import load_manifest
        out = []
        for digits in (100000, 200000):
            raw = b'{"manifest_version":' + b"7" * digits + b"}"
            t = time.perf_counter()
            try:
                load_manifest(raw)
            except errors.ValidationFailed as exc:
                code = exc.issues[0].code
            out.append([digits, round(time.perf_counter() - t, 4), code])
        print(json.dumps(out))
        '''
        results = {}
        for label, env in (("default", None), ("digit-limit-disabled", {"PYTHONINTMAXSTRDIGITS": "0"})):
            rc, out, err, events, to = audited(body, timeout=9, env=env)
            self.assertEqual((rc, to), (0, False), err[-300:])
            results[label] = json.loads(out)
        record("int_digit_bomb_scaling", results=results)
        self.assertEqual(results["default"][0][2], "json_invalid", "default interpreter refuses oversized integer literals")
        # observation only (no timing assertion that could flake): quadratic growth when the limit is off
        t1, t2 = results["digit-limit-disabled"][0][1], results["digit-limit-disabled"][1][1]
        record("int_digit_bomb_ratio", t_100k=t1, t_200k=t2, ratio=(t2 / t1 if t1 else None), interpreter=PY)


# --------------------------------------------------------------------------------------------------------------------------- numeric algebra
class NumericAlgebra(AuditCase):
    def test_gain_and_average_rounding_match_fraction_oracle(self):
        from video_core.audiomix.mix import _avg, scale
        r = O.Rng(SEED_NUM)
        cases = 0
        for v in list(range(-40, 41)) + [-32768, -32767, 32766, 32767]:
            for den in (1, 2, 3, 4, 5, 7, 8, 16, 255, 256, 65535, 65536):
                for num in sorted({0, 1, den // 2, den - 1, den, den + 1, 2 * den, 4 * den}):
                    if num > 4 * den or num > 262144:
                        continue
                    self.assertEqual(scale(v, num, den), O.gain(v, num, den), (v, num, den))
                    cases += 1
        for _ in range(3000):
            v, den = r.between(-32768, 32767), r.between(1, 65536)
            num = r.between(0, min(4 * den, 262144))
            self.assertEqual(scale(v, num, den), O.gain(v, num, den), (v, num, den))
            a, b = r.between(-32768, 32767), r.between(-32768, 32767)
            self.assertEqual(_avg(a, b), O.avg2(a, b), (a, b))
            cases += 2
        record("gain_avg_oracle", seed=SEED_NUM, cases=cases)

    def test_frame_to_sample_and_ms_match_fraction_oracle_including_exact_halves(self):
        from video_core.audiomix.plan import frame_to_sample
        from video_core.timebase import frame_to_ms
        r = O.Rng(SEED_NUM + 1)
        fps_set = [(24, 1), (25, 1), (30, 1), (50, 1), (60, 1), (24000, 1001), (30000, 1001), (60000, 1001), (48000, 1001), (48000, 1), (60000, 1), (7, 3), (12345, 1001),
                   (1, 1), (1, 1001), (59999, 1001), (60000, 7), (44100, 1), (37, 11), (16, 1), (16, 3), (256, 1), (512, 1001), (768, 1), (1280, 7)]
        cases, halves, ms_halves = 0, 0, 0
        for n, d in fps_set:
            fr = Fraction(n, d)
            frames = set(range(0, 40)) | {17999, 18000} | {r.between(0, 18000) for _ in range(150)}
            for f in sorted(frames):
                want_s, want_ms = O.sample_at(f, n, d), O.ms_at(f, n, d)
                self.assertEqual(frame_to_sample(f, n, d), want_s, (f, n, d))
                self.assertEqual(frame_to_ms(f, fr), want_ms, (f, n, d))
                cases += 1
                if Fraction(f * d * 48000, n).denominator == 2:
                    halves += 1
                if Fraction(f * d * 1000, n).denominator == 2:
                    ms_halves += 1
        record("frame_boundary_oracle", seed=SEED_NUM + 1, cases=cases, exact_half_sample_cases=halves, exact_half_ms_cases=ms_halves)
        self.assertGreater(halves, 0, "the fps catalogue must include exact .5 sample boundaries or the rounding rule is untested")
        self.assertGreater(ms_halves, 0, "the fps catalogue must include exact .5 millisecond boundaries")

    def test_exact_half_boundaries_round_up_not_to_even(self):
        """Search for fps/frames whose 48 kHz position is exactly k+1/2 and check both k even and k odd resolve upward."""
        from video_core.audiomix.plan import frame_to_sample
        found = {"even": 0, "odd": 0}
        for n in (256, 512, 768, 1280, 1792, 3840, 7680, 15360, 30720):  # 2^8 | n is required for an exact half at 48 kHz
            for d in (1, 3, 7, 1001):
                for f in range(1, 80):
                    x = Fraction(f * d * 48000, n)
                    if x.denominator == 2:
                        k = x.numerator // 2
                        self.assertEqual(frame_to_sample(f, n, d), k + 1, (f, n, d))
                        found["even" if k % 2 == 0 else "odd"] += 1
        record("half_rounding", found=found)
        self.assertGreater(found["even"], 0)
        self.assertGreater(found["odd"], 0)

    def test_30000_1001_boundaries_do_not_accumulate(self):
        from video_core.audiomix.plan import frame_to_sample
        want = [0, 1602, 3203, 4805, 6406, 8008]  # documented in AUDIO_SPEC; re-derived by the oracle below
        self.assertEqual([O.sample_at(k, 30000, 1001) for k in range(6)], want)
        self.assertEqual([frame_to_sample(k, 30000, 1001) for k in range(6)], want)
        self.assertEqual(frame_to_sample(18000, 30000, 1001), 28_828_800)
        self.assertEqual(O.sample_at(18000, 30000, 1001), 28_828_800)
        per_frame = sum(round(Fraction(1001 * 48000, 30000)) for _ in range(18000))
        self.assertNotEqual(per_frame, 28_828_800, "sanity: naive per-frame rounding WOULD drift (so the absolute-boundary rule matters)")

    @known_defect("F-NUM-1")
    def test_absurd_finite_integer_limits_raise_value_error_not_overflow(self):
        """numeric.py promises every violation is a ValueError. A huge Python int is formatted with `:g` and raises OverflowError."""
        leaks = {}
        for label, fn in (("finite_seconds", lambda: numeric.finite_seconds("t", 10**400)),
                          ("Limits", lambda: Limits(max_runtime_seconds=10**400)),
                          ("AudioLimits", lambda: AudioLimits(max_runtime_seconds=10**400)),
                          ("Limits.replace", lambda: Limits().replace(probe_timeout_seconds=10**400))):
            try:
                fn()
                leaks[label] = "accepted"
            except ValueError:
                pass
            except BaseException as exc:
                leaks[label] = type(exc).__name__
        record("F-NUM-1", leaks=leaks)
        self.assertEqual(leaks, {})

    def test_other_numeric_edge_values_are_rejected_as_value_error(self):
        for bad in (True, False, float("nan"), float("inf"), -float("inf"), 0, -1, 0.0, -0.0, "5", None, 86401, 86400.0000001, [1]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                numeric.finite_seconds("t", bad)
        self.assertEqual(numeric.finite_seconds("t", 86400), 86400)
        self.assertEqual(numeric.finite_seconds("t", 5e-324), 5e-324)  # smallest positive subnormal is accepted (deadline == now): noted, not a defect
        for bad in (True, 1.0, 0, -5, 2**63, "1", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                numeric.positive_int("n", bad)
        self.assertEqual(numeric.positive_int("n", 2**63 - 1), 2**63 - 1)


# --------------------------------------------------------------------------------------------------------------------------- manifest bounds
class ManifestBounds(AuditCase):
    def _audio(self, mut):
        doc = manifest(events=[{"id": "e", "source_id": "s", "state": "UNKNOWN"}])
        mut(doc)
        return doc

    def _accepts(self, doc) -> bool:
        try:
            amanifest.load_manifest(jb(doc))
            return True
        except errors.ValidationFailed:
            return False

    def test_audio_manifest_numeric_boundaries(self):
        table = []

        def add(label, mut, ok):
            table.append((label, mut, ok))
        for v, ok in ((0, False), (1, True), (60000, True), (60001, False), (-1, False), (2**63 - 1, False), (2**63, False)):
            add(f"fps.n={v}", lambda d, v=v: d["timeline"]["fps"].__setitem__("n", v), ok)
        for v, ok in ((0, False), (1, True), (1001, True), (1002, False)):
            add(f"fps.d={v}", lambda d, v=v: d["timeline"]["fps"].__setitem__("d", v), ok)
        for v, ok in ((0, False), (1, True), (18000, True), (18001, False)):
            add(f"total_frames={v}", lambda d, v=v: d["timeline"].__setitem__("total_frames", v), ok)
        for v, ok in ((1, True), (2, False), (0, False)):
            add(f"channels={v}" if v != 2 else "channels=2(valid)", lambda d, v=v: d["output"].__setitem__("channels", v), ok or v == 2)
        for v, ok in ((48000, True), (44100, False), (96000, False)):
            add(f"sample_rate={v}", lambda d, v=v: d["output"].__setitem__("sample_rate", v), ok)
        for label, mut, ok in table:
            doc = self._audio(mut)
            self.assertEqual(self._accepts(doc), ok, label)
        record("audio_manifest_bounds", cases=len(table))

    def test_event_and_gain_boundaries(self):
        def use(**over):
            e = {"id": "e", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 1600, "start_frame": 0, "end_frame": 1}
            e.update(over)
            return e
        asset = [{"id": "a", "path": "a.wav", "sha256": "0" * 64}]
        cases = [
            ("start=-1", use(start_frame=-1), False), ("start=0", use(), True), ("end=start", use(end_frame=0), False), ("end=total", use(end_frame=30), True),
            ("end=total+1", use(end_frame=31), False), ("source_in=-1", use(source_in_sample=-1), False), ("source_out=in", use(source_out_sample=0), False),
            ("source_out=int64max", use(source_out_sample=2**63 - 1), True), ("source_out=2^63", use(source_out_sample=2**63), False),
            ("gain 1/1", use(gain={"num": 1, "den": 1}), True), ("gain 0/1", use(gain={"num": 0, "den": 1}), True), ("gain 4/1", use(gain={"num": 4, "den": 1}), True),
            ("gain 5/1", use(gain={"num": 5, "den": 1}), False), ("gain 262144/65536", use(gain={"num": 262144, "den": 65536}), True),
            ("gain 262145/65536", use(gain={"num": 262145, "den": 65536}), False), ("gain den 65537", use(gain={"num": 1, "den": 65537}), False),
            ("gain den 0", use(gain={"num": 1, "den": 0}), False), ("gain num -1", use(gain={"num": -1, "den": 1}), False),
            ("gain float", use(gain={"num": 1.5, "den": 1}), False), ("gain extra key", use(gain={"num": 1, "den": 1, "db": 3}), False),
            ("range only start", {k: v for k, v in use().items() if k != "end_frame"}, False),
            ("channel_rule bogus", use(channel_rule="MONO_TO_STEREO"), False), ("gain on non-USE", {"id": "e", "source_id": "s", "state": "UNKNOWN", "gain": {"num": 1, "den": 1}}, False),
            ("asset on OMIT", {"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "r", "asset": "a"}, False),
            ("OMIT without decision_ref", {"id": "e", "source_id": "s", "state": "OMIT"}, False),
            ("SILENCE without range", {"id": "e", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "r"}, False),
            ("ref with newline", {"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "a\nb"}, False),
            ("ref leading space", {"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": " a"}, False),
            ("ref 129 chars", {"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "a" * 129}, False),
            ("ref 128 chars", {"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "a" * 128}, True),
            ("ref lone surrogate", {"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "a\ud800"}, False),
            ("id 65 chars", use(id="a" * 65), False), ("id 64 chars", use(id="a" * 64), True), ("id with dot", use(id="a.b"), False),
            ("id trailing newline", use(id="ab\n"), False), ("role 33 chars", {"id": "e", "source_id": "s", "state": "UNKNOWN", "role": "r" * 33}, False),
        ]
        for label, ev, ok in cases:
            doc = manifest(assets=asset, events=[ev], scope=[ev["id"]] if "id" in ev else ["e"])
            self.assertEqual(self._accepts(doc), ok, label)
        record("audio_event_bounds", cases=len(cases))

    def test_scope_semantics(self):
        ev = [{"id": "a", "source_id": "s", "state": "UNKNOWN"}, {"id": "b", "source_id": "s", "state": "UNKNOWN"}]
        for scope, ok in (([], False), (["a"], True), (["a", "b"], True), (["a", "a"], False), (["c"], False), ([1], False), ("a", False), ([["a"]], False)):
            doc = manifest(events=ev)
            doc["scope"]["event_ids"] = scope
            self.assertEqual(self._accepts(doc), ok, repr(scope))
        doc = manifest(events=ev)
        del doc["scope"]
        self.assertFalse(self._accepts(doc), "no implicit 'all': a missing scope is rejected")

    def test_avmux_manifest_numeric_and_state_boundaries(self):
        base = lambda **kw: avmux_manifest(wav_bytes_=b"x", samples=4800, **kw)
        L = Limits()

        def accepts(doc):
            try:
                vmanifest.load_manifest(jb(doc), L)
                return True
            except errors.ValidationFailed:
                return False
        self.assertTrue(accepts(base()))
        for label, mut, ok in (
            ("state HOLD", lambda d: d["video"].__setitem__("state", "HOLD"), False), ("state PROXY audio", lambda d: d["audio"].__setitem__("state", "PROXY"), False),
            ("route bogus", lambda d: d.__setitem__("route", "MKV_FLAC"), False), ("codec hevc", lambda d: d["video"]["declared"].__setitem__("codec", "hevc"), False),
            ("width 15", lambda d: d["video"]["declared"].__setitem__("width", 15), False), ("width 7681", lambda d: d["video"]["declared"].__setitem__("width", 7681), False),
            ("frames 18001", lambda d: d["video"]["declared"].__setitem__("total_frames", 18001), False),
            ("sample_count 0", lambda d: d["audio"]["declared"].__setitem__("sample_count", 0), False),
            ("channels 3", lambda d: d["audio"]["declared"].__setitem__("channels", 3), False),
            ("video ext .mkv", lambda d: d["video"].__setitem__("path", "v.mkv"), False), ("video ext .M4V", lambda d: d["video"].__setitem__("path", "v.M4V"), True),
            ("audio ext .mp3", lambda d: d["audio"].__setitem__("path", "a.mp3"), False),
            ("receipt path only", lambda d: d["audio"].__setitem__("receipt", {"path": "r.json"}), False),
            ("receipt ext .txt", lambda d: d["audio"].__setitem__("receipt", {"path": "r.txt", "sha256": "0" * 64}), False),
            ("sha uppercase", lambda d: d["video"].__setitem__("sha256", "A" * 64), True), ("sha 63 chars", lambda d: d["video"].__setitem__("sha256", "a" * 63), False),
            ("sha with newline", lambda d: d["video"].__setitem__("sha256", "a" * 64 + "\n"), False),
        ):
            doc = base()
            mut(doc)
            self.assertEqual(accepts(doc), ok, label)


# --------------------------------------------------------------------------------------------------------------------------- episode differential
class EpisodeDifferential(AuditCase):
    def _random_episode(self, r: "O.Rng"):
        fps = r.choice([(30, 1), (24, 1), (25, 1), (30000, 1001), (24000, 1001), (60, 1), (120, 1), (121, 1), (5, 1), (4, 1), (60000, 1001), (1, 1), (60000, 1), (17, 3)])
        total = r.choice([1, 2, 3, 29, 30, 31, 180, 17999, 18000, 18001, r.between(1, 400)])
        w = r.choice([16, 18, 640, 1920, 7680, 7682, 15, 17, 14])
        h = r.choice([16, 18, 360, 1080, 4320, 7680, 7682, 15])
        # clip tiling: mostly valid, sometimes broken at exactly one boundary
        cuts = sorted({r.between(1, max(1, min(total - 1, 400))) for _ in range(r.below(4))} if total > 1 else set())
        starts = [0] + cuts
        ends = cuts + [total]
        clips = []
        for i, (s, e) in enumerate(zip(starts, ends)):
            clips.append({"id": f"c{i}", "start_frame": s, "duration_frames": e - s, "source": {"kind": "color", "value": "#102030"}})
        m = r.below(12)
        if m == 0 and clips:
            clips[-1]["duration_frames"] += 1       # runs past the end
        elif m == 1 and clips:
            clips[-1]["duration_frames"] -= 1       # leaves a gap at the end (or zero duration)
        elif m == 2 and len(clips) > 1:
            clips[1]["start_frame"] += 1            # gap
        elif m == 3 and len(clips) > 1:
            clips[1]["start_frame"] -= 1            # overlap
        elif m == 4 and len(clips) > 1:
            clips[1]["id"] = clips[0]["id"]         # duplicate id
        elif m == 5 and len(clips) > 1:
            clips[0], clips[1] = clips[1], clips[0]  # out of order
        caps = []
        for i in range(r.below(4)):
            s = r.choice([0, 1, total - 1, total, total + 1, r.between(0, total)])
            e = r.choice([s, s + 1, s + 2, total, total + 1, r.between(0, total + 1)])
            caps.append({"id": f"k{i}", "start_frame": s, "end_frame": e, "text": r.choice(["a", "テスト", "line1\nline2", "x --> y", "1\n2"])})
        if len(caps) > 1 and r.chance(1, 8):
            caps[1]["id"] = caps[0]["id"]
        return {"version": 1, "fps": {"n": fps[0], "d": fps[1]}, "canvas": {"width": w, "height": h}, "total_frames": total, "clips": clips, "captions": caps}

    def test_acceptance_and_srt_agree_with_independent_oracle(self):
        r = O.Rng(SEED_EP)
        stats = {"cases": 0, "accepted": 0, "rejected": 0}
        mismatches = []
        for _ in range(900):
            doc = self._random_episode(r)
            want = O.episode_oracle(doc)
            try:
                ep = load_episode(jb(doc))
                got = None
            except errors.ValidationFailed as exc:
                got = exc.issues[0].code if exc.issues else "?"
            stats["cases"] += 1
            if (want is None) != (got is None):
                mismatches.append({"want": want, "got": got, "doc": doc})
                continue
            if want is None:
                stats["accepted"] += 1
                from video_core.srt import export_srt
                if export_srt(ep) != O.expected_srt(doc):
                    mismatches.append({"want": "srt equal", "got": "srt differs", "doc": doc})
            else:
                stats["rejected"] += 1
        record("episode_differential", seed=SEED_EP, **stats, mismatches=len(mismatches))
        self.assertGreater(stats["accepted"], 40)
        self.assertGreater(stats["rejected"], 100)
        self.assertEqual(mismatches[:3], [])


# --------------------------------------------------------------------------------------------------------------------------- binary font parser
class FontParser(AuditCase):
    """fontcheck.py parses caller-supplied binary data at plan time. Corrupt fonts must end as FontError / ValidationFailed, never as a
    stray IndexError / struct.error / RecursionError / MemoryError, and never loop unboundedly."""

    def test_corrupt_fonts_only_raise_font_error(self):
        r = O.Rng(SEED_WAV + 99)
        text = ["ABC テスト", "line1\nline2"]
        leaks, cases, outcomes = [], 0, {"ok": 0, "FontError": 0}
        for fmt in (4, 12):
            base = sfnt_font([ord(c) for c in "ABC テスト line12"], fmt=fmt)
            variants = [base[:cut] for cut in range(len(base))]
            for _ in range(500):
                b = bytearray(base)
                for _k in range(1 + r.below(3)):
                    b[r.below(len(b))] = r.below(256)
                variants.append(bytes(b))
            for v in variants:
                cases += 1
                try:
                    fontcheck.missing_codepoints(v, text)
                    outcomes["ok"] += 1
                except fontcheck.FontError:
                    outcomes["FontError"] += 1
                except BaseException as exc:
                    leaks.append((fmt, type(exc).__name__, str(exc)[:60]))
                try:
                    fontcheck.require_glyph_coverage(v, text)
                except errors.ValidationFailed:
                    pass
                except BaseException as exc:
                    leaks.append((fmt, "require:" + type(exc).__name__, str(exc)[:60]))
        record("font_corpus", seed=SEED_WAV + 99, cases=cases, outcomes=outcomes, leaks=len(leaks))
        self.assertEqual(leaks[:5], [])

    def test_collections_and_wrong_magic_are_rejected(self):
        base = sfnt_font([65])
        for magic, ok in ((b"\x00\x01\x00\x00", True), (b"OTTO", True), (b"true", True), (b"ttcf", False), (b"wOFF", False), (b"\x00\x00\x00\x00", False)):
            f = magic + base[4:]
            try:
                fontcheck.missing_codepoints(f, ["A"])
                got = True
            except fontcheck.FontError:
                got = False
            self.assertEqual(got, ok, magic)

    def test_scan_cost_is_linear_in_codepoints_times_groups_measured_small(self):
        """STATIC CONCERN, measured at small scale only (bounded). _format12_has / _format4_has scan every group/segment for every distinct
        code point, i.e. O(distinct_chars x groups). Worst case allowed by the limits (<= 512 captions x 512 chars; font <= 32 MiB,
        <= 1,000,000 groups) was NOT executed; an extrapolation is recorded for the report."""
        import time as _t
        G, K = 8000, 80
        groups = [(0x4E00 + 3 * i, 0x4E00 + 3 * i + 1, 5 + i) for i in range(G)]
        font = sfnt_font([], fmt=12, groups=groups)
        text = ["".join(chr(0x4E00 + 3 * i + 2) for i in range(K))]          # K distinct code points, none covered: full scan each
        t0 = _t.perf_counter()
        miss = fontcheck.missing_codepoints(font, text)
        dt = _t.perf_counter() - t0
        self.assertEqual(len(miss), K)
        unit = dt / (G * K)
        record("font_scan_cost", groups=G, codepoints=K, seconds=round(dt, 4), unit_seconds_per_group_codepoint=unit,
               extrapolated_seconds_worst_case_262144_cp_x_1e6_groups=round(unit * 262144 * 1_000_000), extrapolated_seconds_realistic_5000cp_x_20000groups=round(unit * 5000 * 20000, 1))
        self.assertLess(dt, 5.0)


# --------------------------------------------------------------------------------------------------------------------------- audio receipt as an INPUT of the avmux plan
class AudioReceiptInput(AuditCase):
    """avmux.plan._check_audio_receipt consumes a caller-supplied JSON file pinned by SHA-256. Malformed or inconsistent content must be
    refused with typed errors (F-REC-1, F-REC-2). A control proves that a genuine receipt written by the real audio mixer is accepted."""

    def _env(self):
        sb = self.sb
        sb.mkdir("assets")
        self.frames = pcm_tone(4800, 1, 77)
        self.wav = wav_from_frames(self.frames)
        self.vbytes = b"synthetic-not-a-video"
        sb.write("assets/a.wav", self.wav)
        sb.write("assets/v.mp4", self.vbytes)
        self.good = {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": sha256(self.wav)}], "readback": {"wav": {"sample_count": 4800}}}

    def _plan(self, raw: bytes):
        self.sb.write("assets/r.json", raw)
        doc = avmux_manifest(video_bytes=self.vbytes, wav_bytes_=self.wav, samples=4800, receipt={"path": "r.json", "sha256": sha256(raw)})
        try:
            vplan.prepare_plan(jb(doc), asset_root=self.sb.p("assets"))
            return "ACCEPTED"
        except errors.VideoCoreError as exc:
            return "REJECTED:" + exc.issues[0].code
        except BaseException as exc:
            return "LEAK:" + type(exc).__name__

    def test_control_a_receipt_written_by_the_real_audio_mixer_is_accepted(self):
        """The stem is a < 10 KB synthetic fixture written to this test's temp dir by the mixer API and deleted with it."""
        from video_core.audiomix import mix as amix
        self._env()
        doc = manifest(total=3, assets=[{"id": "a", "path": "a.wav", "sha256": sha256(self.wav)}],
                       events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])
        rec = amix.mix(jb(doc), asset_root=self.sb.p("assets"), output_root=self.sb.mkdir("mixout"), output_name="real")
        self.assertEqual(rec["status"], "MIXED_VERIFIED")
        with open(self.sb.p("mixout", "real", "stem.wav"), "rb") as fh:
            stem = fh.read()
        self.sb.write("assets/a.wav", stem)
        self.wav = stem
        with open(self.sb.p("mixout", "real", "receipt.json"), "rb") as fh:
            raw = fh.read()
        self.assertEqual(self._plan(raw), "ACCEPTED")

    @known_defect("F-REC-1")
    def test_malformed_receipt_content_is_a_typed_rejection(self):
        self._env()
        cases = {"list": b"[]", "null": b"null", "string": b'"x"', "number": b"1", "outputs-string": json.dumps({**self.good, "outputs": "x"}).encode(),
                 "outputs-int-element": json.dumps({**self.good, "outputs": [1]}).encode(), "readback-wav-string": json.dumps({**self.good, "readback": {"wav": "x"}}).encode(),
                 "deep-nesting": b"[" * 100000 + b"]" * 100000, "empty": b"", "bom": b"\xef\xbb\xbf" + json.dumps(self.good).encode()}
        got = {k: self._plan(v) for k, v in cases.items()}
        record("F-REC-1", got=got)
        self.harness_check(self._plan(json.dumps(self.good).encode()) == "ACCEPTED", "control: the minimal well-formed receipt must plan")
        leaks = {k: v for k, v in got.items() if not v.startswith("REJECTED:")}
        self.assertEqual(leaks, {})

    @known_defect("F-REC-2")
    def test_inconsistent_or_schema_less_receipts_do_not_qualify_a_stem(self):
        self._env()
        g = self.good
        cases = {
            "no-schema-three-field-receipt": json.dumps(g).encode(),
            "readback-all_ok-false": json.dumps({**g, "readback": {"all_ok": False, "wav": {"sample_count": 4800}}}).encode(),
            "stem-marked-partial": json.dumps({**g, "outputs": [{"role": "stem", "sha256": sha256(self.wav), "partial": True}]}).encode(),
            "quality-evaluated-true": json.dumps({**g, "quality_evaluated": True}).encode(),
            "duplicate-status-last-wins": ('{"status":"HOLD","status":"MIXED_VERIFIED","outputs":[{"role":"stem","sha256":"%s"}],"readback":{"wav":{"sample_count":4800}}}' % sha256(self.wav)).encode(),
            "float-sample-count": json.dumps({**g, "readback": {"wav": {"sample_count": 4800.0}}}).encode(),
        }
        got = {k: self._plan(v) for k, v in cases.items()}
        record("F-REC-2", got=got)
        self.harness_check(self._plan(json.dumps({**g, "status": "HOLD"}).encode()) == "REJECTED:audio_receipt_not_verified", "control: a HOLD receipt must be refused")
        accepted = sorted(k for k, v in got.items() if v == "ACCEPTED")
        self.assertEqual(accepted, [])


# --------------------------------------------------------------------------------------------------------------------------- defects
class ParserDefects(AuditCase):
    @known_defect("F-AUD-1")
    def test_zero_sample_range_of_unselected_or_omitted_event_must_not_reject_the_manifest(self):
        """AUDIO_SPEC: unselected events are never verified and never block a valid selected scope; OMIT consumes nothing.
        At fps > 48000 a one-frame range can legitimately span 0 samples (frame 2->3 at 60000/1); the planner rejects the whole manifest."""
        self.harness_check((O.sample_at(2, 60000, 1), O.sample_at(3, 60000, 1)) == (2, 2), "oracle: frame 2->3 spans zero samples at 60000 fps")
        silence = {"id": "a", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "r", "start_frame": 0, "end_frame": 10}
        outcomes = {}
        for label, extra, scope in (("unselected UNKNOWN", {"id": "b", "source_id": "s", "state": "UNKNOWN", "start_frame": 2, "end_frame": 3}, ["a"]),
                                     ("selected OMIT", {"id": "b", "source_id": "s", "state": "OMIT", "decision_ref": "r2", "start_frame": 2, "end_frame": 3}, ["a", "b"])):
            doc = manifest(fps=(60000, 1), total=60, events=[silence, extra], scope=scope)
            try:
                plan = aplan.prepare_plan(jb(doc))
                outcomes[label] = plan.status
            except errors.VideoCoreError as exc:
                outcomes[label] = "REJECTED:" + exc.issues[0].code
        record("F-AUD-1", outcomes=outcomes)
        self.assertEqual(outcomes, {"unselected UNKNOWN": "READY", "selected OMIT": "READY"})

    @known_defect("F-CLI-1")
    def test_cli_error_output_survives_a_lone_surrogate_in_a_rejected_input(self):
        """A JSON key written as "\\ud800" is a lone surrogate. The unknown-field issue embeds it in `path`; the CLI then fails to encode
        its own error report, so the caller gets a Python traceback and an empty stdout instead of the documented structured rejection."""
        sb = self.sb
        ep = example_episode()
        ep_raw = json.dumps(ep)[:-1] + ', "\\ud800": 1}'
        am = json.dumps(manifest(events=[{"id": "e", "source_id": "s", "state": "UNKNOWN"}]))[:-1] + ', "\\ud800": 1}'
        av = json.dumps(avmux_manifest(wav_bytes_=b"x", samples=4800))[:-1] + ', "\\ud800": 1}'
        failures = {}
        for label, module, sub, raw in (("video", "video_core", "validate", ep_raw), ("audiomix", "video_core.audiomix", "validate", am), ("avmux", "video_core.avmux", "validate", av)):
            path = sb.write(f"{label}.json", raw.encode("utf-8"))
            rc, out, err, to = cli(module, [sub, path])
            try:
                payload = json.loads(out)
                structured = payload.get("ok") is False
            except ValueError:
                structured = False
            if not (rc == 1 and structured):
                failures[label] = {"rc": rc, "stdout": out[:60], "stderr_tail": err.strip().splitlines()[-1][:120] if err.strip() else ""}
        record("F-CLI-1", failures=failures)
        self.assertEqual(failures, {})


if __name__ == "__main__":
    unittest.main()
