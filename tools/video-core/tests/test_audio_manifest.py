import copy
import json
import unittest

from tests.audio_fixtures import asset, dumps, event_use, manifest, tone, wav_bytes
from tests.helpers import no_process
from video_core.audiomix import load_manifest
from video_core.audiomix.limits import AudioLimits
from video_core.errors import ValidationFailed

A = wav_bytes(tone("square", 480, 4800, 1000))
ASSETS = [asset("asset_a", "a.wav", A)]


def good():
    return manifest([event_use("ev1", "asset_a", 0, 3200, 0, 2)], ASSETS)


def codes(doc, limits=AudioLimits()):
    data = doc if isinstance(doc, bytes) else dumps(doc)
    with no_process():
        try:
            load_manifest(data, limits)
        except ValidationFailed as exc:
            return {i.code for i in exc.issues}
    return set()


def mut(fn, base=None):
    d = copy.deepcopy(base or good())
    fn(d)
    return d


class Accepts(unittest.TestCase):
    def test_minimal_valid_manifest(self):
        m = load_manifest(dumps(good()))
        self.assertEqual((m.fps_n, m.fps_d, m.total_frames, m.channels, m.scope), (30, 1, 3, 1, ("ev1",)))
        self.assertEqual((m.overlap_policy, m.clipping_policy), ("REJECT", "REJECT"), "defaults are the safe ones")

    def test_all_states_and_caller_assertions_are_preserved_verbatim(self):
        evs = [event_use("ev1", "asset_a", 0, 1600, 0, 1, decision_ref="dec:テスト/1", provenance_ref="prov 2", role="narration"),
               {"id": "ev2", "source_id": "s2", "state": "OMIT", "decision_ref": "d2"},
               {"id": "ev3", "source_id": "s3", "state": "INTENTIONAL_SILENCE", "decision_ref": "d3", "start_frame": 1, "end_frame": 2},
               {"id": "ev4", "source_id": "s4", "state": "MISSING"},
               {"id": "ev5", "source_id": "s5", "state": "UNKNOWN", "start_frame": 2, "end_frame": 3, "role": "x"},
               {"id": "ev6", "source_id": "s6", "state": "PROXY", "provenance_ref": "proxy-of-s1"}]
        m = load_manifest(dumps(manifest(evs, ASSETS, scope=["ev1"])))
        by = {e.id: e for e in m.events}
        self.assertEqual((by["ev1"].decision_ref, by["ev1"].provenance_ref, by["ev1"].role, by["ev1"].source_id), ("dec:テスト/1", "prov 2", "narration", "src_ev1"))
        self.assertEqual({e.state for e in m.events}, {"USE", "OMIT", "INTENTIONAL_SILENCE", "MISSING", "UNKNOWN", "PROXY"})

    def test_caption_only_and_diegetic_metadata_needs_no_speech_or_asset(self):
        evs = [{"id": "cap1", "source_id": "caption_only_1", "state": "OMIT", "decision_ref": "caption-only", "role": "caption_only"},
               {"id": "dg1", "source_id": "diegetic_1", "state": "MISSING", "role": "diegetic"}]
        m = load_manifest(dumps(manifest(evs, [], scope=["cap1"])))
        self.assertEqual(len(m.assets), 0)

    def test_sha256_case_is_normalized_and_stereo_output_accepted(self):
        d = mut(lambda d: (d["assets"][0].update(sha256=d["assets"][0]["sha256"].upper()), d["output"].update(channels=2)))
        m = load_manifest(dumps(d))
        self.assertEqual((m.assets[0].sha256, m.channels), (ASSETS[0]["sha256"], 2))


class Rejects(unittest.TestCase):
    def test_version_unknown_fields_missing_fields(self):
        self.assertIn("unsupported_version", codes(mut(lambda d: d.update(manifest_version=2))))
        self.assertIn("unsupported_version", codes(mut(lambda d: d.update(manifest_version=True))))
        self.assertIn("unknown_field", codes(mut(lambda d: d.update(extra=1))))
        self.assertIn("unknown_field", codes(mut(lambda d: d["events"][0].update(speech="x"))))
        for k in ("timeline", "output", "scope", "policy", "assets", "events"):
            self.assertIn("missing_field", codes(mut(lambda d, k=k: d.pop(k))), k)
        self.assertIn("missing_field", codes(mut(lambda d: d["policy"].pop("gap"))), "gap policy must be explicit")

    def test_strict_json_and_numbers(self):
        self.assertIn("json_duplicate_key", codes(b'{"manifest_version": 1, "manifest_version": 1}'))
        self.assertIn("bom_not_allowed", codes(b"\xef\xbb\xbf" + dumps(good())))
        self.assertIn("json_non_finite", codes(dumps(good()).replace(b'"n": 30', b'"n": NaN')))
        self.assertIn("invalid_utf8", codes(b"\xff\xfe{}"))
        for bad in (30.0, "30", True, None, [30]):
            self.assertIn("type_error", codes(mut(lambda d, b=bad: d["timeline"]["fps"].update(n=b))), repr(bad))
        for bad in (1.5, True, "0", None):
            self.assertIn("type_error", codes(mut(lambda d, b=bad: d["events"][0].update(source_in_sample=b))), repr(bad))
        self.assertIn("int_overflow", codes(mut(lambda d: d["events"][0].update(source_out_sample=2**63))))
        self.assertIn("limit_input_bytes", codes(dumps(good()), AudioLimits(max_input_bytes=100)))

    def test_ids_duplicates_and_scope(self):
        self.assertIn("duplicate_id", codes(mut(lambda d: d["events"].append(copy.deepcopy(d["events"][0])))))
        self.assertIn("duplicate_id", codes(mut(lambda d: d["assets"].append(copy.deepcopy(d["assets"][0])))))
        for bad in ("", "a b", "é", "../x", "x" * 65, "a\n"):
            self.assertIn("invalid_id", codes(mut(lambda d, b=bad: d["events"][0].update(id=b))), repr(bad))
            self.assertIn("invalid_id", codes(mut(lambda d, b=bad: d["events"][0].update(source_id=b))), repr(bad))
        self.assertIn("scope_empty", codes(mut(lambda d: d["scope"].update(event_ids=[]))))
        self.assertIn("scope_unknown_event", codes(mut(lambda d: d["scope"].update(event_ids=["nope"]))))
        self.assertIn("scope_duplicate", codes(mut(lambda d: d["scope"].update(event_ids=["ev1", "ev1"]))))
        self.assertIn("missing_field", codes(mut(lambda d: d["scope"].pop("event_ids"))), "there is no implicit 'all'")
        self.assertIn("type_error", codes(mut(lambda d: d.update(scope=["ev1"]))))

    def test_state_and_per_state_requirements(self):
        self.assertIn("invalid_enum", codes(mut(lambda d: d["events"][0].update(state="use"))))
        for k in ("asset", "source_in_sample", "source_out_sample", "start_frame", "end_frame"):
            self.assertIn("missing_field" if k not in ("start_frame", "end_frame") else "range_incomplete",
                          codes(mut(lambda d, k=k: d["events"][0].pop(k))), k)
        sil = {"id": "s", "source_id": "s", "state": "INTENTIONAL_SILENCE", "start_frame": 0, "end_frame": 1}
        self.assertIn("missing_field", codes(manifest([sil], [])), "silence needs a decision_ref")
        self.assertIn("missing_field", codes(manifest([{"id": "o", "source_id": "o", "state": "OMIT"}], [])), "omit needs a decision_ref")
        self.assertIn("range_incomplete", codes(manifest([{k: v for k, v in {**sil, "decision_ref": "d"}.items() if k != "start_frame"}], [])))
        self.assertIn("missing_field", codes(manifest([{k: v for k, v in {**sil, "decision_ref": "d"}.items() if k not in ("start_frame", "end_frame")}], [])), "silence needs an explicit range")
        for state in ("OMIT", "MISSING", "UNKNOWN", "PROXY", "INTENTIONAL_SILENCE"):
            ev = {"id": "x", "source_id": "x", "state": state, "decision_ref": "d", "start_frame": 0, "end_frame": 1, "asset": "asset_a"}
            self.assertIn("field_not_allowed", codes(manifest([ev], ASSETS)), state)

    def test_ranges(self):
        self.assertIn("out_of_range", codes(mut(lambda d: d["events"][0].update(end_frame=0))))
        self.assertIn("event_range_invalid", codes(mut(lambda d: d["events"][0].update(start_frame=2, end_frame=1))))
        self.assertIn("event_range_invalid", codes(mut(lambda d: d["events"][0].update(start_frame=2, end_frame=2))))
        self.assertIn("event_past_end", codes(mut(lambda d: d["events"][0].update(end_frame=4))), "clip past total_frames")
        self.assertIn("out_of_range", codes(mut(lambda d: d["events"][0].update(start_frame=-1))))
        self.assertIn("source_range_invalid", codes(mut(lambda d: d["events"][0].update(source_in_sample=5, source_out_sample=5))))
        self.assertIn("source_range_invalid", codes(mut(lambda d: d["events"][0].update(source_in_sample=9, source_out_sample=3))))
        self.assertIn("out_of_range", codes(mut(lambda d: d["events"][0].update(source_in_sample=-1))))
        self.assertIn("range_incomplete", codes(mut(lambda d: d["events"][0].pop("end_frame"))))
        self.assertIn("unknown_asset", codes(mut(lambda d: d["events"][0].update(asset="ghost"))))

    def test_gain_rules(self):
        for g in ({"num": -1, "den": 1}, {"num": 1, "den": 0}, {"num": 1, "den": -2}, {"num": 5, "den": 1}, {"num": 262145, "den": 65536},
                  {"num": 1, "den": 65537}, {"num": True, "den": 1}, {"num": 1.0, "den": 1}, {"num": "1", "den": 1}, {"num": 1}, {"num": 1, "den": 1, "db": 3}):
            self.assertTrue(codes(mut(lambda d, g=g: d["events"][0].update(gain=g))), g)
        for g in ({"num": 0, "den": 1}, {"num": 4, "den": 1}, {"num": 262144, "den": 65536}, {"num": 1, "den": 65536}):
            self.assertEqual(codes(mut(lambda d, g=g: d["events"][0].update(gain=g))), set(), g)
        self.assertIn("type_error", codes(mut(lambda d: d["events"][0].update(gain=1.0))), "dB floats are not accepted")

    def test_channel_rule_enum(self):
        self.assertIn("invalid_enum", codes(mut(lambda d: d["events"][0].update(channel_rule="DOWNMIX"))))
        self.assertEqual(codes(mut(lambda d: d["events"][0].update(channel_rule="COPY"))), set())

    def test_asset_entries(self):
        for p, c in (("/abs.wav", "path_absolute"), ("../a.wav", "path_traversal"), ("http://x/a.wav", "path_protocol"), ("-a.wav", "path_option_like"),
                     ("a\\b.wav", "path_backslash"), ("a.mp3", "source_extension_unsupported"), ("a", "source_extension_unsupported"), ("a:b.wav", "path_absolute"),
                     ("a%03d.wav", "path_forbidden_char"), (".hid.wav", "path_hidden"), ("", "path_empty")):
            self.assertIn(c, codes(mut(lambda d, p=p: d["assets"][0].update(path=p))), p)
        for bad in ("abc", "g" * 64, "a" * 63, "a" * 65, "", None, 5):
            self.assertIn("invalid_sha256", codes(mut(lambda d, b=bad: d["assets"][0].update(sha256=b))), repr(bad))
        self.assertIn("type_error", codes(mut(lambda d: d["assets"][0].update(path=5))))

    def test_output_and_policy(self):
        for k, v in (("sample_rate", 44100), ("sample_rate", 96000), ("bits", 24), ("bits", 8)):
            self.assertIn("unsupported_output", codes(mut(lambda d, k=k, v=v: d["output"].update({k: v}))), (k, v))
        self.assertIn("out_of_range", codes(mut(lambda d: d["output"].update(channels=3))))
        self.assertIn("out_of_range", codes(mut(lambda d: d["output"].update(channels=0))))
        for k, v in (("gap", "ZERO"), ("gap", None), ("overlap", "SUM"), ("clipping", "LIMIT"), ("clipping", True)):
            self.assertIn("invalid_enum", codes(mut(lambda d, k=k, v=v: d["policy"].update({k: v}))), (k, v))
        self.assertIn("unknown_field", codes(mut(lambda d: d["policy"].update(normalize=True))), "no automatic normalization option exists")

    def test_refs_and_roles(self):
        for bad in ("", " x", "x ", "a\nb", "a​b", "x" * 129, 5, "\ud800"):
            self.assertIn("invalid_ref", codes(mut(lambda d, b=bad: d["events"][0].update(decision_ref=b))), repr(bad))
        self.assertIn("invalid_id", codes(mut(lambda d: d["events"][0].update(role="has space"))))

    def test_timeline(self):
        for k, v in (("n", 0), ("n", 60001), ("d", 0), ("d", 1002)):
            self.assertIn("out_of_range", codes(mut(lambda d, k=k, v=v: d["timeline"]["fps"].update({k: v}))), (k, v))
        self.assertIn("limit_frames", codes(mut(lambda d: d["timeline"].update(total_frames=18001))))
        self.assertIn("out_of_range", codes(mut(lambda d: d["timeline"].update(total_frames=0))))
        self.assertIn("invalid_sha256", codes(mut(lambda d: d["timeline"].update(episode_sha256="zz"))))

    def test_limits(self):
        self.assertIn("limit_events", codes(manifest([event_use(f"e{i}", "asset_a", 0, 1, 0, 1) for i in range(3)], ASSETS), AudioLimits(max_events=2)))
        self.assertIn("limit_assets", codes(manifest([event_use("e", "a0", 0, 1, 0, 1)], [asset(f"a{i}", f"a{i}.wav", A) for i in range(3)]), AudioLimits(max_assets=2)))
        self.assertIn("limit_frames", codes(good(), AudioLimits(max_frames=2)))
        for bad in ({"max_events": 1025}, {"max_assets": 257}, {"max_frames": 18001}, {"chunk_samples": 10}, {"chunk_samples": 2_000_000}, {"max_concurrent": 65}, {"max_mix_work": 0}):
            with self.assertRaises(ValueError):
                AudioLimits(**bad)

    def test_all_issues_reported_together(self):
        d = mut(lambda d: (d["events"][0].update(end_frame=99, gain={"num": 9, "den": 1}), d["assets"][0].update(sha256="x"), d.update(manifest_version=3)))
        self.assertTrue({"event_past_end", "gain_too_high", "invalid_sha256", "unsupported_version"} <= codes(d))


if __name__ == "__main__":
    unittest.main()
