import json
import os
import struct
import unittest
import wave
from fractions import Fraction
from math import floor

from tests.audio_fixtures import RATE, asset, dumps, event_use, interleave, manifest, sha, tone, wav_bytes, write
from tests.helpers import TmpCase, jl, rb, tree_snapshot
from video_core import receipt as rc
from video_core.audiomix import frame_to_sample
from video_core.audiomix import mix as mixmod
from video_core.audiomix.limits import AudioLimits
from video_core.errors import UnsafePath, ValidationFailed


def ref_scale(v, num, den):
    x = Fraction(v * num, den)
    r = floor(abs(x) + Fraction(1, 2))
    return r if x >= 0 else -r


def ref_avg(a, b):
    return ref_scale(a + b, 1, 2)


def read_stem(path):
    """Independent readback through the stdlib wave module."""
    with wave.open(path, "rb") as w:
        n, ch = w.getnframes(), w.getnchannels()
        assert (w.getframerate(), w.getsampwidth()) == (RATE, 2)
        raw = w.readframes(n)
    return ch, n, list(struct.unpack(f"<{n * ch}h", raw))


class MixCase(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out = self.mkdir("assets"), self.mkdir("out")
        self.paths = {}

    def put(self, aid, samples, channels=1, **kw):
        data = wav_bytes(samples, channels, **kw)
        self.paths[aid] = write(os.path.join(self.assets, f"{aid}.wav"), data)
        return asset(aid, f"{aid}.wav", data)

    def mix(self, doc, name="r1", **kw):
        kw.setdefault("asset_root", self.assets)
        return mixmod.mix(dumps(doc), output_root=self.out, output_name=name, **kw)

    def stem(self, name="r1"):
        return read_stem(os.path.join(self.out, name, "stem.wav"))


class Arithmetic(unittest.TestCase):
    def test_scale_is_round_half_away_from_zero_and_matches_the_rational_reference(self):
        for v in range(-40, 41):
            for num, den in ((1, 2), (1, 3), (2, 3), (3, 2), (5, 4), (1, 1), (0, 7), (7, 65536), (262144, 65536), (1, 8)):
                self.assertEqual(mixmod.scale(v, num, den), ref_scale(v, num, den), (v, num, den))
        self.assertEqual([mixmod.scale(v, 1, 2) for v in (3, -3, 1, -1, 2, -2)], [2, -2, 1, -1, 1, -1], "exact halves go away from zero")
        self.assertEqual(mixmod.scale(32767, 4, 1), 131068)

    def test_average_rounds_half_away_from_zero(self):
        for a in range(-5, 6):
            for b in range(-5, 6):
                self.assertEqual(mixmod._avg(a, b), ref_avg(a, b), (a, b))
        self.assertEqual((mixmod._avg(1, 2), mixmod._avg(-1, -2), mixmod._avg(1, 0), mixmod._avg(-1, 0), mixmod._avg(32767, 32767)), (2, -2, 1, -1, 32767))

    def test_channel_maps(self):
        stereo = [10, -10, 20, -20]
        self.assertEqual(mixmod.mono_frames(stereo, 2, "STEREO_TO_MONO_LEFT"), [10, 20])
        self.assertEqual(mixmod.mono_frames(stereo, 2, "STEREO_TO_MONO_RIGHT"), [-10, -20])
        self.assertEqual(mixmod.mono_frames(stereo, 2, "STEREO_TO_MONO_AVERAGE"), [0, 0])
        self.assertEqual(mixmod.mono_frames([5, 6], 1, "MONO_TO_STEREO_DUPLICATE"), [(5, 5), (6, 6)])
        self.assertEqual(mixmod.mono_frames(stereo, 2, "COPY"), [(10, -10), (20, -20)])
        self.assertEqual(mixmod.mono_frames([5, 6], 1, "COPY"), [5, 6])


class Mixing(MixCase):
    def test_overlapping_tones_equal_an_independent_reference_sample_for_sample(self):
        ta, tb = tone("square", 480, 3200, 8000), tone("triangle", 600, 3200, 6001)
        a, b = self.put("a", ta), self.put("b", tb)
        doc = manifest([event_use("ea", "a", 0, 3200, 0, 2), event_use("eb", "b", 0, 3200, 1, 3, gain=(1, 2))], [a, b], overlap="ALLOW_SUM")
        r = self.mix(doc)
        self.assertEqual(r["status"], mixmod.S_VERIFIED, r.get("readback"))
        expected = [0] * 4800
        for i, v in enumerate(ta):
            expected[i] += v
        for i, v in enumerate(tb):
            expected[1600 + i] += ref_scale(v, 1, 2)
        ch, n, got = self.stem()
        self.assertEqual((ch, n), (1, 4800))
        self.assertEqual(got, expected)
        self.assertEqual(r["mix"]["peak_before_clip"], max(abs(v) for v in expected))
        self.assertEqual((r["mix"]["clipped_samples"], r["mix"]["first_clipped_sample"]), (0, None))

    def test_ntsc_event_placement_uses_absolute_boundaries(self):
        tone_a = tone("saw", 480, 3203, 4000)
        a = self.put("a", tone_a)
        doc = manifest([event_use("e1", "a", 0, 1602, 0, 1), event_use("e2", "a", 1602, 3203, 1, 2)], [a], fps=(30000, 1001), total=3)
        r = self.mix(doc)
        self.assertEqual(r["status"], mixmod.S_VERIFIED)
        ch, n, got = self.stem()
        self.assertEqual(n, frame_to_sample(3, 30000, 1001))  # 4805
        self.assertEqual(got[:3203], tone_a[:3203])
        self.assertEqual(got[3203:], [0] * (n - 3203), "frame 2..3 has no event: zero-filled by the explicit gap policy")

    def test_stereo_and_channel_rules_end_to_end(self):
        left, right = tone("square", 480, 1600, 3000), tone("saw", 600, 1600, 2000)
        st = self.put("st", interleave(left, right), channels=2)
        mono = self.put("mo", tone("triangle", 400, 1600, 5000))
        evs = [event_use("e1", "st", 0, 1600, 0, 1), event_use("e2", "mo", 0, 1600, 1, 2, rule="MONO_TO_STEREO_DUPLICATE", gain=(3, 4))]
        r = self.mix(manifest(evs, [st, mono], channels=2, total=2), name="stereo")
        self.assertEqual(r["status"], mixmod.S_VERIFIED)
        ch, n, got = self.stem("stereo")
        self.assertEqual((ch, n), (2, 3200))
        self.assertEqual(got[:3200], interleave(left, right))
        tm = tone("triangle", 400, 1600, 5000)
        self.assertEqual(got[3200:], interleave([ref_scale(v, 3, 4) for v in tm], [ref_scale(v, 3, 4) for v in tm]))
        self.assertEqual(len(r["mix"]["peak_output_per_channel"]), 2)

    def test_stereo_to_mono_average_and_gain_order(self):
        left, right = tone("dc", 1000, 1600, 101), tone("dc", 1000, 1600, -50)
        st = self.put("st", interleave(left, right), channels=2)
        doc = manifest([event_use("e", "st", 0, 1600, 0, 1, rule="STEREO_TO_MONO_AVERAGE", gain=(1, 3))], [st], total=1)
        self.assertEqual(self.mix(doc)["status"], mixmod.S_VERIFIED)
        self.assertEqual(set(self.stem()[2]), {ref_scale(ref_avg(101, -50), 1, 3)})

    def test_explicit_silence_and_zero_fill_write_zeros_and_unselected_events_write_nothing(self):
        a = self.put("a", tone("dc", 1000, 1600, 7777))
        b = self.put("b", tone("dc", 1000, 1600, 5555))
        sil = {"id": "sil", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "d", "start_frame": 1, "end_frame": 2}
        evs = [event_use("ea", "a", 0, 1600, 0, 1), sil, event_use("eb", "b", 0, 1600, 3, 4), {"id": "m", "source_id": "m", "state": "MISSING"}]
        r = self.mix(manifest(evs, [a, b], scope=["ea", "sil"], total=4))
        self.assertEqual(r["status"], mixmod.S_VERIFIED)
        got = self.stem()[2]
        self.assertEqual(got, [7777] * 1600 + [0] * 4800, "eb is not in scope: not mixed; silence + zero-fill are zeros")
        self.assertEqual([u["event_id"] for u in r["unresolved_report"]], ["m"])
        self.assertEqual([w["code"] for w in r["warnings"]], ["ZERO_FILL_APPLIED"])

    def test_chunk_size_does_not_change_a_single_byte_and_runs_are_deterministic(self):
        a, b = self.put("a", tone("square", 480, 3200, 9000)), self.put("b", tone("triangle", 600, 3200, 9000))
        doc = manifest([event_use("ea", "a", 0, 3200, 0, 2), event_use("eb", "b", 0, 3200, 1, 3, gain=(1, 3))], [a, b], overlap="ALLOW_SUM", total=4)
        hashes = []
        for i, chunk in enumerate((64, 100, 1600, 48000, 4799)):
            r = self.mix(doc, name=f"c{i}", limits=AudioLimits(chunk_samples=chunk))
            self.assertEqual(r["status"], mixmod.S_VERIFIED, chunk)
            hashes.append(next(o["sha256"] for o in r["outputs"] if o["name"] == "stem.wav"))
            self.assertEqual(r["mix"]["chunks"], -(-6400 // chunk))
        self.assertEqual(len(set(hashes)), 1)
        self.assertEqual(hashes[0], sha(rb(os.path.join(self.out, "c0", "stem.wav"))))


class Clipping(MixCase):
    def doc(self, a, clipping=None):
        return manifest([event_use("e1", "a", 0, 1600, 0, 1), event_use("e2", "a", 0, 1600, 0, 1)], [a], overlap="ALLOW_SUM", total=1, clipping=clipping)

    def test_boundary_values_are_not_clipping(self):
        a = self.put("a", tone("dc", 1000, 1600, 16383) )
        b = self.put("b", tone("dc", 1000, 1600, -16384))
        doc = manifest([event_use("e1", "a", 0, 1600, 0, 1), event_use("e2", "a", 0, 1600, 0, 1), event_use("e3", "b", 0, 1600, 0, 1), event_use("e4", "b", 0, 1600, 0, 1)],
                       [a, b], overlap="ALLOW_SUM", total=1)
        self.assertEqual(self.mix(doc)["status"], mixmod.S_VERIFIED, "16383*2 = 32766 and -16384*2 = -32768 are inside int16")

    def test_default_rejects_clipping_keeps_the_partial_and_reports_where(self):
        a = self.put("a", tone("dc", 1000, 1600, 16384))
        r = self.mix(self.doc(a), limits=AudioLimits(chunk_samples=64))
        self.assertEqual(r["status"], mixmod.S_CLIP_REJECTED)
        self.assertEqual((r["mix"]["first_clipped_sample"], r["mix"]["clipped_samples"], r["mix"]["peak_before_clip"]), (0, 64, 32768))
        d = os.path.join(self.out, "r1")
        self.assertTrue(os.path.exists(os.path.join(d, "stem.wav.partial")))
        self.assertFalse(os.path.exists(os.path.join(d, "stem.wav")))
        partial = [o for o in r["outputs"] if o["role"] == "stem_partial"][0]
        self.assertTrue(partial["partial"])
        self.assertEqual(r["stage"], "mix")
        self.assertEqual(r["mix"]["samples_written"], 0, "the first chunk already clips: nothing was written as audio")
        self.assertEqual(r["input"]["inputs_unchanged"], True)

    def test_late_clipping_reports_the_exact_first_sample_and_partial_progress(self):
        samples = [1000] * 1000 + [20000] * 600
        a = self.put("a", samples)
        r = self.mix(self.doc(a), limits=AudioLimits(chunk_samples=100))
        self.assertEqual(r["status"], mixmod.S_CLIP_REJECTED)
        self.assertEqual((r["mix"]["first_clipped_sample"], r["mix"]["samples_written"], r["mix"]["chunks"]), (1000, 1000, 10))

    def test_clamp_policy_saturates_and_is_reported_not_called_verified(self):
        samples = [1000] * 800 + [20000] * 400 + [-20000] * 400
        a = self.put("a", samples)
        r = self.mix(self.doc(a, clipping="CLAMP"))
        self.assertEqual(r["status"], mixmod.S_CLAMPED)
        self.assertEqual((r["mix"]["clipped_samples"], r["mix"]["first_clipped_sample"], r["mix"]["peak_before_clip"], r["mix"]["peak_output"]), (800, 800, 40000, 32768))
        self.assertEqual(self.stem()[2], [2000] * 800 + [32767] * 400 + [-32768] * 400)
        self.assertTrue(r["readback"]["all_ok"])


class Protection(MixCase):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 3200, 1000))
        self.doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [self.a], total=2)

    def test_hold_creates_nothing_and_is_not_silence(self):
        before = tree_snapshot(self.tmp)
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2), {"id": "m", "source_id": "m", "state": "UNKNOWN"}], [self.a], total=2)
        r = self.mix(doc)
        self.assertEqual((r["status"], r["written"]), (mixmod.S_HOLD, False))
        self.assertEqual(r["hold"][0]["code"], "SELECTED_UNRESOLVED")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_overwrite_is_refused_and_the_first_result_is_untouched(self):
        self.assertEqual(self.mix(self.doc)["status"], mixmod.S_VERIFIED)
        before = tree_snapshot(os.path.join(self.out, "r1"))
        with self.assertRaises(UnsafePath) as cm:
            self.mix(self.doc)
        self.assertEqual(cm.exception.issues[0].code, "output_exists")
        self.assertEqual(tree_snapshot(os.path.join(self.out, "r1")), before)

    def test_existing_directory_with_user_files_is_never_written_into(self):
        keep = write(os.path.join(self.out, "r1", "precious.txt"), b"mine")
        before = tree_snapshot(self.out)
        with self.assertRaises(UnsafePath):
            self.mix(self.doc)
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(rb(keep), b"mine")

    def test_inputs_and_outside_files_are_unchanged_and_only_the_run_dir_is_added(self):
        sentinel = write(os.path.join(self.tmp, "outside", "sentinel.txt"), b"do not touch")
        before_assets, before_out = tree_snapshot(self.assets), tree_snapshot(self.out)
        r = self.mix(self.doc)
        self.assertEqual(r["status"], mixmod.S_VERIFIED)
        self.assertEqual(tree_snapshot(self.assets), before_assets)
        self.assertEqual(rb(sentinel), b"do not touch")
        self.assertEqual({k.split(os.sep)[0] for k in tree_snapshot(self.out)} - set(before_out), {"r1"})
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "r1"))), ["plan.canonical.json", "receipt.json", "stem.wav"])

    def test_rejections_create_nothing(self):
        before = tree_snapshot(self.tmp)
        bad = [dict(self.doc, manifest_version=9), manifest([event_use("e", "a", 0, 3199, 0, 2)], [self.a], total=2)]
        for d in bad:
            with self.assertRaises(ValidationFailed):
                self.mix(d)
        with self.assertRaises(UnsafePath):
            self.mix(self.doc, name="../escape")
        with self.assertRaises(UnsafePath):
            mixmod.mix(dumps(self.doc), asset_root=self.assets, output_root=self.assets, output_name="in-assets")
        self.assertEqual(tree_snapshot(self.tmp), before)


class Receipt(MixCase):
    def test_receipt_pins_manifest_assets_plan_output_properties_and_rehash(self):
        a = self.put("a", tone("square", 480, 3200, 1000))
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2, decision_ref="dec-1", source_id="SRC9")], [a], total=2)
        data = dumps(doc)
        r = mixmod.mix(data, asset_root=self.assets, output_root=self.out, output_name="r1")
        d = os.path.join(self.out, "r1")
        on_disk = jl(os.path.join(d, "receipt.json"))
        self.assertEqual(on_disk, json.loads(json.dumps(r)))
        self.assertEqual((r["status"], r["quality_evaluated"]), (mixmod.S_VERIFIED, False))
        self.assertIn("No audio, speech or creative quality", r["note"])
        self.assertEqual(r["input"]["manifest_sha256"], sha(data))
        self.assertEqual(r["input"]["assets"][0]["sha256_before"], sha(rb(self.paths["a"])))
        self.assertEqual(r["input"]["assets"][0]["sha256_after"], r["input"]["assets"][0]["sha256_before"])
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(sha(rb(os.path.join(d, "plan.canonical.json"))), r["plan_sha256"])
        outs = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(outs["stem.wav"]["sha256"], sha(rb(os.path.join(d, "stem.wav"))))
        self.assertEqual(outs["stem.wav"]["bytes"], 44 + 3200 * 2)
        rb_ = r["readback"]
        self.assertTrue(rb_["all_ok"])
        self.assertEqual((rb_["wav"]["channels"], rb_["wav"]["sample_rate"], rb_["wav"]["bits"], rb_["wav"]["sample_count"]), (1, 48000, 16, 3200))
        self.assertEqual({c["name"] for c in rb_["checks"]}, {"channels", "sample_rate", "bits", "sample_count", "file_bytes"})
        self.assertIn("not authenticated", r["caller_assertions"])
        self.assertEqual(r["mix"]["samples_written"], 3200)

    def test_receipt_says_in_progress_while_mixing(self):
        a = self.put("a", tone("square", 480, 3200, 1000))
        seen = []

        def cancel():
            seen.append(jl(os.path.join(self.out, "r1", "receipt.json"))["status"])
            return False
        self.mix(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a], total=2), cancel=cancel)
        self.assertEqual(set(seen), {rc.STATUS_IN_PROGRESS})


class CancellationAndRuntime(MixCase):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 32000, 1000))
        self.doc = manifest([event_use("e", "a", 0, 32000, 0, 20)], [self.a], total=20)  # 32000 samples
        self.limits = AudioLimits(chunk_samples=1000)

    def test_cancel_between_chunks_preserves_the_partial_without_retry(self):
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) > 5
        r = self.mix(self.doc, cancel=cancel, limits=self.limits)
        self.assertEqual(r["status"], mixmod.S_CANCELLED)
        self.assertEqual((r["mix"]["chunks"], r["mix"]["samples_written"]), (5, 5000))
        self.assertEqual(len(calls), 6, "checked once per chunk, stopped at the first True, never resumed")
        d = os.path.join(self.out, "r1")
        self.assertEqual(sorted(os.listdir(d)), ["plan.canonical.json", "receipt.json", "stem.wav.partial"])
        self.assertTrue([o for o in r["outputs"] if o["role"] == "stem_partial"][0]["partial"])
        self.assertEqual(r["stage"], "mix")

    def test_cancel_before_the_first_chunk(self):
        r = self.mix(self.doc, cancel=lambda: True, limits=self.limits)
        self.assertEqual((r["status"], r["mix"]["samples_written"]), (mixmod.S_CANCELLED, 0))

    def test_runtime_bound_uses_the_injected_clock(self):
        ticks = iter(range(0, 1000))
        clock = lambda: float(next(ticks))  # one tick per call: 1 for deadline, 1 per chunk check, 1 for stats
        r = self.mix(self.doc, clock=clock, limits=self.limits.replace(max_runtime_seconds=4))
        self.assertEqual(r["status"], mixmod.S_TIMEOUT)
        self.assertLess(r["mix"]["samples_written"], 32000)
        self.assertGreater(r["mix"]["samples_written"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.out, "r1", "stem.wav.partial")))

    def test_keyboard_interrupt_is_recorded_not_lost(self):
        def cancel():
            raise KeyboardInterrupt
        r = self.mix(self.doc, cancel=cancel, limits=self.limits)
        self.assertEqual(r["status"], mixmod.S_INTERRUPTED)
        self.assertEqual(jl(os.path.join(self.out, "r1", "receipt.json"))["status"], mixmod.S_INTERRUPTED)

    def test_an_input_changed_during_the_run_is_unknown_not_verified(self):
        calls = []

        def cancel():
            if not calls:  # flip one sample in place (same size, same header) after the plan verified the hash
                with open(self.paths["a"], "r+b") as fh:
                    fh.seek(44 + 2)
                    fh.write(b"\x55\x55")
            calls.append(1)
            return False
        r = self.mix(self.doc, cancel=cancel, limits=self.limits)
        self.assertEqual(r["status"], mixmod.S_UNKNOWN)
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("input file changed", r["unknown_reason"])

    def test_an_input_that_stops_being_a_valid_wav_mid_run_is_unknown_with_the_error(self):
        def cancel():
            with open(self.paths["a"], "ab") as fh:
                fh.write(b"JUNK")
            return False
        r = self.mix(self.doc, cancel=cancel, limits=self.limits)
        self.assertEqual(r["status"], mixmod.S_UNKNOWN)
        self.assertEqual(r["internal_error"]["type"], "ValidationFailed")


if __name__ == "__main__":
    unittest.main()
