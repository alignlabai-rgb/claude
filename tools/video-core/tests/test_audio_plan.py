import os
import unittest
from fractions import Fraction
from math import floor
from unittest import mock

from tests.audio_fixtures import RATE, asset, dumps, event_use, interleave, list_chunk, manifest, sha, tone, wav_bytes, write
from tests.helpers import POSIX, TmpCase, episode_doc, color_clips, no_process, tree_snapshot, to_bytes
from video_core import paths as paths_mod
from video_core.audiomix import frame_to_sample, prepare_plan
from video_core.audiomix.limits import AudioLimits
from video_core.errors import UnsafePath, ValidationFailed

NTSC = (30000, 1001)


def rejected(fn):
    with no_process():
        try:
            fn()
        except (ValidationFailed, UnsafePath) as exc:
            return {i.code for i in exc.issues}
    return set()


class SampleMath(unittest.TestCase):
    def test_30fps_is_exact_1600_per_frame(self):
        for f in (0, 1, 2, 3, 17999, 18000):
            self.assertEqual(frame_to_sample(f, 30, 1), 1600 * f)

    def test_ntsc_known_boundaries(self):
        # 48000 * 1001 / 30000 = 1601.6 samples per frame
        self.assertEqual([frame_to_sample(f, *NTSC) for f in range(0, 6)], [0, 1602, 3203, 4805, 6406, 8008])
        self.assertEqual(frame_to_sample(30000, *NTSC), 48_048_000)  # 30000 frames = exactly 1001 s

    def test_every_boundary_equals_the_exact_rational_round_half_up(self):
        for n, d in (NTSC, (30, 1), (24000, 1001), (25, 1), (60000, 1001)):
            fps = Fraction(n, d)
            for f in range(0, 18001, 7):
                self.assertEqual(frame_to_sample(f, n, d), floor(Fraction(f) / fps * RATE + Fraction(1, 2)), (n, d, f))

    def test_no_cumulative_drift_unlike_a_per_frame_rounded_length(self):
        naive = 18000 * round(48000 * 1001 / 30000)  # 1602 per frame
        exact = frame_to_sample(18000, *NTSC)
        self.assertEqual(exact, 28_828_800)
        self.assertEqual(naive - exact, 7_200, "per-frame rounding would be 0.15 s late after 18000 frames")

    def test_adjacent_lengths_alternate_but_boundaries_never_move(self):
        lens = [frame_to_sample(f + 1, *NTSC) - frame_to_sample(f, *NTSC) for f in range(10)]
        self.assertEqual(set(lens), {1601, 1602})
        self.assertEqual(frame_to_sample(10, *NTSC), sum(lens))

    def test_ntsc_and_30fps_boundaries_differ_by_the_exact_amount(self):
        self.assertEqual(frame_to_sample(2, *NTSC) - frame_to_sample(2, 30, 1), 3203 - 3200)
        self.assertEqual(frame_to_sample(1, *NTSC), 1602, "1601.6 rounds half up to 1602")
        self.assertEqual(frame_to_sample(3, *NTSC), 4805, "4804.8 -> 4805, not 3 * 1602 = 4806")


class World(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out = self.mkdir("assets"), self.mkdir("out")
        self.files = {}

    def put(self, aid, samples, channels=1, name=None, **kw):
        data = wav_bytes(samples, channels, **kw)
        name = name or f"{aid}.wav"
        write(os.path.join(self.assets, name), data)
        self.files[aid] = data
        return asset(aid, name, data)

    def plan(self, doc, **kw):
        return prepare_plan(dumps(doc), asset_root=self.assets, **kw)


class Timeline(World):
    def test_source_range_must_equal_the_timeline_length_at_30fps(self):
        a = self.put("a", tone("square", 480, 6400))
        for end, ok in ((3200, True), (3199, False), (3201, False), (1600, False)):
            doc = manifest([event_use("e", "a", 0, end, 0, 2)], [a])
            self.assertEqual(rejected(lambda: self.plan(doc)) == set(), ok, end)
        self.assertIn("range_length_mismatch", rejected(lambda: self.plan(manifest([event_use("e", "a", 0, 3199, 0, 2)], [a]))))

    def test_ntsc_alternating_lengths_are_enforced_exactly(self):
        a = self.put("a", tone("square", 480, 9600, 100))
        # frames [0,1) = 1602 samples, [1,2) = 1601 samples
        ok = manifest([event_use("e1", "a", 0, 1602, 0, 1), event_use("e2", "a", 2000, 3601, 1, 2)], [a], fps=NTSC, total=2)
        p = self.plan(ok)
        self.assertEqual([(e.sample_start, e.sample_end) for e in p.mix_events], [(0, 1602), (1602, 3203)])
        self.assertEqual(p.total_samples, 3203)
        bad = manifest([event_use("e1", "a", 0, 1602, 0, 1), event_use("e2", "a", 2000, 3602, 1, 2)], [a], fps=NTSC, total=2)
        self.assertEqual(rejected(lambda: self.plan(bad)), {"range_length_mismatch"}, "1602 where the boundary arithmetic gives 1601")
        also_bad = manifest([event_use("e1", "a", 0, 1601, 0, 1)], [a], fps=NTSC, total=2)
        self.assertEqual(rejected(lambda: self.plan(also_bad)), {"range_length_mismatch"})

    def test_source_trims_start_anywhere_inside_the_asset(self):
        a = self.put("a", tone("triangle", 600, 6400, 500))
        p = self.plan(manifest([event_use("e", "a", 1000, 4200, 0, 2)], [a]))
        self.assertEqual((p.mix_events[0].source_in, p.canonical["events"][0]["source_out_sample"]), (1000, 4200))
        self.assertEqual(rejected(lambda: self.plan(manifest([event_use("e", "a", 3300, 6500, 0, 2)], [a]))), {"source_range_out_of_bounds"})
        self.assertEqual(rejected(lambda: self.plan(manifest([event_use("e", "a", 3200, 6400, 0, 2)], [a]))), set(), "ending exactly at the last sample is fine")
        self.assertEqual(rejected(lambda: self.plan(manifest([event_use("e", "a", 3201, 6401, 0, 2)], [a]))), {"source_range_out_of_bounds"}, "one sample past")

    def test_event_past_total_frames_is_rejected(self):
        a = self.put("a", tone("square", 480, 6400))
        self.assertEqual(rejected(lambda: self.plan(manifest([event_use("e", "a", 0, 3200, 2, 4)], [a], total=3))), {"event_past_end"})

    def test_total_samples_and_output_description(self):
        a = self.put("a", tone("square", 480, 6400))
        p = self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a], total=3))
        self.assertEqual((p.total_samples, p.canonical["output"]["bytes"], p.canonical["output"]["samples"]), (4800, 44 + 4800 * 2, 4800))
        self.assertEqual(p.canonical["mux_interface"]["duration_seconds"], "1/10")
        self.assertEqual(p.canonical["mux_interface"]["start_offset_samples"], 0)


class Overlap(World):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 9600, 100))

    def two(self, s2, e2, overlap=None, gain=None):
        n2 = 1600 * (e2 - s2)
        return manifest([event_use("e1", "a", 0, 3200, 0, 2), event_use("e2", "a", 0, n2, s2, e2)], [self.a], total=4, overlap=overlap)

    def test_overlap_rejected_by_default_and_by_policy(self):
        self.assertEqual(rejected(lambda: self.plan(self.two(1, 3))), {"event_overlap"})
        self.assertEqual(rejected(lambda: self.plan(self.two(1, 3, overlap="REJECT"))), {"event_overlap"})

    def test_touching_events_are_not_overlapping(self):
        self.assertEqual(rejected(lambda: self.plan(self.two(2, 4))), set())

    def test_allow_sum_permits_overlap_up_to_the_concurrency_limit(self):
        p = self.plan(self.two(1, 3, overlap="ALLOW_SUM"))
        self.assertEqual(p.canonical["mix"]["peak_concurrent"], 2)
        evs = [event_use(f"e{i}", "a", 0, 1600, 0, 1) for i in range(3)]
        doc = manifest(evs, [self.a], total=1, overlap="ALLOW_SUM")
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_concurrent=2))), {"limit_concurrent"})
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_concurrent=3))), set())

    def test_intentional_silence_never_overlaps_consumed_events(self):
        sil = {"id": "sil", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "d", "start_frame": 1, "end_frame": 3}
        doc = manifest([event_use("e1", "a", 0, 3200, 0, 2), sil], [self.a], total=4, overlap="ALLOW_SUM")
        self.assertEqual(rejected(lambda: self.plan(doc)), {"silence_overlap"})
        doc = manifest([event_use("e1", "a", 0, 3200, 0, 2), {**sil, "start_frame": 2, "end_frame": 4}], [self.a], total=4)
        self.assertEqual(rejected(lambda: self.plan(doc)), set())

    def test_overlap_of_unselected_events_does_not_block(self):
        doc = self.two(1, 3)
        doc["scope"]["event_ids"] = ["e1"]
        self.assertEqual(rejected(lambda: self.plan(doc)), set())


class ScopeAndStates(World):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 9600, 100))
        self.use = event_use("use1", "a", 0, 1600, 0, 1)

    def doc(self, extra, scope, **kw):
        return manifest([self.use] + extra, [self.a], scope=scope, total=4, **kw)

    def test_selected_missing_unknown_proxy_each_yield_hold_not_silence(self):
        for state in ("MISSING", "UNKNOWN", "PROXY"):
            ev = {"id": "x", "source_id": "sx", "state": state, "start_frame": 1, "end_frame": 2}
            p = self.plan(self.doc([ev], ["use1", "x"]))
            self.assertEqual(p.status, "HOLD", state)
            self.assertEqual([(h["code"], h["event_id"], h["state"]) for h in p.hold if h["code"] == "SELECTED_UNRESOLVED"], [("SELECTED_UNRESOLVED", "x", state)])
            self.assertEqual(p.mix_events[0].event_id, "use1", "the resolved part is still described, but nothing may be mixed")
            self.assertEqual(p.canonical["status"], "HOLD")

    def test_unselected_unresolved_events_stay_in_the_report_and_do_not_block(self):
        evs = [{"id": "m", "source_id": "sm", "state": "MISSING"}, {"id": "u", "source_id": "su", "state": "UNKNOWN", "role": "diegetic"},
               {"id": "p", "source_id": "sp", "state": "PROXY", "provenance_ref": "r"}]
        p = self.plan(manifest([self.use] + evs, [self.a], scope=["use1"], total=1))
        self.assertEqual(p.status, "READY")
        self.assertEqual([(u["event_id"], u["state"], u["selected"]) for u in p.canonical["unresolved"]], [("m", "MISSING", False), ("u", "UNKNOWN", False), ("p", "PROXY", False)])
        self.assertEqual({e["id"]: e["disposition"] for e in p.canonical["events"]}, {"use1": "MIX", "m": "UNRESOLVED_NOT_SELECTED", "u": "UNRESOLVED_NOT_SELECTED", "p": "UNRESOLVED_NOT_SELECTED"})

    def test_unselected_use_event_is_reported_but_its_asset_is_never_read(self):
        b = {"id": "b", "path": "broken.wav", "sha256": "0" * 64}  # no file at all, wrong hash
        write(os.path.join(self.assets, "junk.wav"), b"not a wav")
        c = {"id": "c", "path": "junk.wav", "sha256": "1" * 64}
        evs = [event_use("other", "b", 0, 1600, 1, 2), event_use("other2", "c", 0, 1600, 2, 3)]
        doc = manifest([self.use] + evs, [self.a, b, c], scope=["use1"], total=4)
        p = self.plan(doc)
        self.assertEqual(p.status, "READY")
        self.assertEqual([a["verified"] for a in p.canonical["assets"]], [True, False, False])
        self.assertEqual({e["id"]: e["disposition"] for e in p.canonical["events"]}["other"], "NOT_SELECTED")
        self.assertIn("not verified", p.canonical["assets"][1]["reason"])

    def test_explicit_omit_and_silence_are_resolved_states_with_references(self):
        om = {"id": "om", "source_id": "s_om", "state": "OMIT", "decision_ref": "decision/omit-1"}
        sil = {"id": "sil", "source_id": "s_sil", "state": "INTENTIONAL_SILENCE", "decision_ref": "decision/silence-1", "provenance_ref": "prov/9", "start_frame": 1, "end_frame": 2}
        p = self.plan(self.doc([om, sil], ["use1", "om", "sil"]))
        self.assertEqual(p.status, "READY")
        ev = {e["id"]: e for e in p.canonical["events"]}
        self.assertEqual((ev["om"]["disposition"], ev["om"]["decision_ref"]), ("OMITTED", "decision/omit-1"))
        self.assertEqual((ev["sil"]["disposition"], ev["sil"]["decision_ref"], ev["sil"]["provenance_ref"], ev["sil"]["sample_start"], ev["sil"]["sample_end"]),
                         ("SILENCE", "decision/silence-1", "prov/9", 1600, 3200))
        self.assertIn("opaque caller assertions", p.canonical["caller_assertions"])
        self.assertIn("not authenticated", p.canonical["caller_assertions"])

    def test_event_and_source_ids_and_roles_are_preserved(self):
        ev = event_use("ev_x", "a", 0, 1600, 1, 2, source_id="SRC-42", role="narration", decision_ref="d/1")
        p = self.plan(manifest([self.use, ev], [self.a], total=2))
        got = {e["id"]: e for e in p.canonical["events"]}["ev_x"]
        self.assertEqual((got["source_id"], got["role"], got["decision_ref"]), ("SRC-42", "narration", "d/1"))

    def test_caption_only_selected_event_needs_no_audio_and_adds_no_hold(self):
        cap = {"id": "cap", "source_id": "caption_only", "state": "OMIT", "decision_ref": "caption-only", "role": "caption_only"}
        p = self.plan(manifest([cap, self.use], [self.a], scope=["cap", "use1"], total=1))
        self.assertEqual((p.status, p.hold), ("READY", []))

    def test_selecting_nothing_but_silence_is_explicit_not_an_error(self):
        sil = {"id": "sil", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "d", "start_frame": 0, "end_frame": 4}
        p = self.plan(manifest([sil], [], total=4))
        self.assertEqual(p.status, "READY")
        self.assertEqual([w["code"] for w in p.canonical["warnings"]], ["NO_AUDIO_EVENTS_SELECTED"])


class GapPolicy(World):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 9600, 100))

    def test_zero_fill_allowed_reports_gaps_exactly(self):
        doc = manifest([event_use("e", "a", 0, 1600, 1, 2)], [self.a], total=4, gap="ALLOW_ZERO_FILL")
        p = self.plan(doc)
        self.assertEqual(p.status, "READY")
        self.assertEqual(p.canonical["coverage"]["gaps"], [{"sample_start": 0, "sample_end": 1600}, {"sample_start": 3200, "sample_end": 6400}])
        self.assertEqual((p.canonical["coverage"]["covered_samples"], p.canonical["coverage"]["gap_count"]), (1600, 2))
        self.assertEqual([w["code"] for w in p.canonical["warnings"]], ["ZERO_FILL_APPLIED"])

    def test_require_full_coverage_holds_on_gaps(self):
        doc = manifest([event_use("e", "a", 0, 1600, 1, 2)], [self.a], total=4, gap="REQUIRE_FULL_COVERAGE")
        p = self.plan(doc)
        self.assertEqual(p.status, "HOLD")
        self.assertEqual([h["code"] for h in p.hold], ["INCOMPLETE_SELECTED_INPUT"])

    def test_full_coverage_by_use_and_explicit_silence_is_ready_but_omit_does_not_cover(self):
        sil = {"id": "sil", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "d", "start_frame": 0, "end_frame": 1}
        om = {"id": "om", "source_id": "o", "state": "OMIT", "decision_ref": "d", "start_frame": 2, "end_frame": 3}
        evs = [sil, event_use("e", "a", 0, 1600, 1, 2), om]
        self.assertEqual(self.plan(manifest(evs[:2], [self.a], total=2, gap="REQUIRE_FULL_COVERAGE")).status, "READY")
        p = self.plan(manifest(evs, [self.a], total=3, gap="REQUIRE_FULL_COVERAGE"))
        self.assertEqual((p.status, p.canonical["coverage"]["gaps"]), ("HOLD", [{"sample_start": 3200, "sample_end": 4800}]))

    def test_hold_reasons_combine(self):
        evs = [event_use("e", "a", 0, 1600, 1, 2), {"id": "m", "source_id": "m", "state": "MISSING"}]
        p = self.plan(manifest(evs, [self.a], total=4, gap="REQUIRE_FULL_COVERAGE"))
        self.assertEqual(sorted(h["code"] for h in p.hold), ["INCOMPLETE_SELECTED_INPUT", "SELECTED_UNRESOLVED"])


class AssetVerification(World):
    def test_bad_sha256_is_rejected_with_both_values(self):
        a = self.put("a", tone("square", 480, 6400))
        a["sha256"] = "0" * 64
        with self.assertRaises(ValidationFailed) as cm:
            self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a]))
        issue = cm.exception.issues[0]
        self.assertEqual(issue.code, "asset_sha256_mismatch")
        self.assertIn("0" * 64, issue.message)
        self.assertIn(sha(self.files["a"]), issue.message)

    def test_hash_is_of_the_whole_file_not_just_the_samples(self):
        a = self.put("a", tone("square", 480, 6400), extra_before=list_chunk(b"tag"))
        self.assertEqual(self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])).status, "READY")
        a["sha256"] = sha(wav_bytes(tone("square", 480, 6400)))
        self.assertEqual(rejected(lambda: self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a]))), {"asset_sha256_mismatch"})

    def test_bad_headers_are_rejected_before_use(self):
        good = tone("square", 480, 6400)
        cases = {"wav_unsupported_format": dict(rate=44100), "wav_header_mismatch": dict(byte_rate=1), "wav_truncated": dict(trunc=20), "wav_riff_size_mismatch": dict(riff_delta=-4)}
        for expected, kw in cases.items():
            a = self.put("a", good, **kw)
            self.assertEqual(rejected(lambda: self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a]))), {expected}, kw)

    def test_missing_file_for_a_selected_event_is_a_structured_hold(self):
        a = asset("a", "ghost.wav", b"x")
        p = self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a]))
        self.assertEqual(p.status, "HOLD")
        self.assertEqual([(h["code"], h["asset"]) for h in p.hold], [("ASSET_FILE_MISSING", "a")])
        self.assertEqual(p.mix_events, [])

    def test_asset_root_is_required_for_selected_use_events_only(self):
        a = self.put("a", tone("square", 480, 6400))
        self.assertEqual(rejected(lambda: prepare_plan(dumps(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])))), {"asset_root_required"})
        om = {"id": "o", "source_id": "o", "state": "OMIT", "decision_ref": "d"}
        self.assertEqual(prepare_plan(dumps(manifest([om], []))).status, "READY")

    def test_source_size_limits(self):
        a = self.put("a", tone("square", 480, 6400))
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_source_bytes=100))), {"limit_source_bytes"})
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_total_source_bytes=100))), {"limit_total_source_bytes"})

    def test_resource_limits_before_any_io(self):
        a = self.put("a", tone("square", 480, 6400))
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_output_samples=4799))), {"limit_output_samples"})
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_mix_work=3199))), {"limit_mix_work"})
        self.assertEqual(rejected(lambda: prepare_plan(dumps(doc), asset_root=self.assets, limits=AudioLimits(max_mix_work=3200))), set())


class ChannelRules(World):
    def test_matrix_of_source_and_output_channels(self):
        mono = self.put("m", tone("square", 480, 3200, 100))
        st = self.put("s", interleave(tone("square", 480, 3200, 100), tone("saw", 600, 3200, 50)), channels=2)
        cases = [(mono, 1, None, "COPY"), (st, 2, None, "COPY"), (mono, 2, "MONO_TO_STEREO_DUPLICATE", "MONO_TO_STEREO_DUPLICATE"),
                 (st, 1, "STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_LEFT"), (st, 1, "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_RIGHT"),
                 (st, 1, "STEREO_TO_MONO_AVERAGE", "STEREO_TO_MONO_AVERAGE")]
        for a, out_ch, rule, want in cases:
            doc = manifest([event_use("e", a["id"], 0, 3200, 0, 2, rule=rule)], [a], channels=out_ch, total=2)
            self.assertEqual(self.plan(doc).canonical["events"][0]["channel_rule"], want, (a["id"], out_ch, rule))

    def test_mismatches_need_an_explicit_compatible_rule(self):
        mono = self.put("m", tone("square", 480, 3200, 100))
        st = self.put("s", interleave(tone("square", 480, 3200, 100), tone("saw", 600, 3200, 50)), channels=2)
        for a, out_ch, rule, code in ((mono, 2, None, "channel_rule_required"), (st, 1, None, "channel_rule_required"), (mono, 1, "STEREO_TO_MONO_LEFT", "channel_rule_incompatible"),
                                      (st, 2, "STEREO_TO_MONO_LEFT", "channel_rule_incompatible"), (mono, 2, "COPY", "channel_rule_incompatible"),
                                      (st, 1, "COPY", "channel_rule_incompatible"), (st, 1, "MONO_TO_STEREO_DUPLICATE", "channel_rule_incompatible")):
            doc = manifest([event_use("e", a["id"], 0, 3200, 0, 2, rule=rule)], [a], channels=out_ch, total=2)
            self.assertEqual(rejected(lambda: self.plan(doc)), {code}, (a["id"], out_ch, rule))


class PathSafety(World):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 6400))

    def doc_with_path(self, path):
        a = dict(self.a, path=path)
        return manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])

    def test_unsafe_paths_never_reach_the_filesystem(self):
        for path, code in (("/etc/passwd.wav", "path_absolute"), ("../outside.wav", "path_traversal"), ("sub/../../x.wav", "path_traversal"), ("file:a.wav", "path_protocol"),
                           ("-a.wav", "path_option_like"), ("a\\b.wav", "path_backslash")):
            self.assertIn(code, rejected(lambda: self.plan(self.doc_with_path(path))), path)

    @unittest.skipUnless(POSIX, "symlinks")
    def test_symlink_and_directory_symlink_escape_are_rejected(self):
        outside = write(os.path.join(self.tmp, "outside", "secret.wav"), self.files["a"])
        os.symlink(outside, os.path.join(self.assets, "link.wav"))
        os.symlink(os.path.dirname(outside), os.path.join(self.assets, "dirlink"))
        os.symlink(os.path.join(self.assets, "a.wav"), os.path.join(self.assets, "inner.wav"))
        for rel in ("link.wav", "dirlink/secret.wav", "inner.wav"):
            self.assertEqual(rejected(lambda: self.plan(self.doc_with_path(rel))), {"symlink_rejected"}, rel)

    def test_windows_reparse_points_are_treated_like_symlinks(self):
        """Junctions are not S_ISLNK; the FILE_ATTRIBUTE_REPARSE_POINT bit must be honoured (exercised with a stat-like stub, no Windows needed)."""
        import stat as st
        plain = os.lstat(os.path.join(self.assets, "a.wav"))
        self.assertFalse(paths_mod.is_link_like(plain))
        stub = mock.Mock(st_mode=plain.st_mode, st_file_attributes=st.FILE_ATTRIBUTE_REPARSE_POINT)
        self.assertTrue(paths_mod.is_link_like(stub))
        stub2 = mock.Mock(st_mode=plain.st_mode, st_file_attributes=st.FILE_ATTRIBUTE_ARCHIVE)
        self.assertFalse(paths_mod.is_link_like(stub2))
        real_lstat = os.lstat
        with mock.patch.object(paths_mod.os, "lstat", lambda p: stub if str(p).endswith("a.wav") else real_lstat(p)):
            self.assertEqual(rejected(lambda: self.plan(self.doc_with_path("a.wav"))), {"symlink_rejected"})

    def test_missing_directory_component_is_a_hold_not_a_crash(self):
        p = self.plan(self.doc_with_path("nodir/a.wav"))
        self.assertEqual([h["code"] for h in p.hold], ["ASSET_FILE_MISSING"])

    def test_output_location_rules(self):
        doc = self.doc_with_path("a.wav")
        inside = self.mkdir("assets", "outdir")
        self.assertEqual(rejected(lambda: self.plan(doc, output_root=inside, output_name="r")), {"output_overlaps_assets"})
        os.mkdir(os.path.join(self.out, "taken"))
        self.assertEqual(rejected(lambda: self.plan(doc, output_root=self.out, output_name="taken")), {"output_exists"})
        for bad in ("../x", "a/b", "", "-r", "a b"):
            self.assertEqual(rejected(lambda: self.plan(doc, output_root=self.out, output_name=bad)), {"output_name_invalid"}, bad)
        self.assertEqual(rejected(lambda: self.plan(doc, output_root=self.out)), {"output_incomplete"})


class Purity(World):
    def test_plan_creates_nothing_launches_nothing_and_is_deterministic(self):
        a = self.put("a", tone("square", 480, 6400))
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])
        before = tree_snapshot(self.tmp)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with no_process():
                p1 = self.plan(doc, output_root=self.out, output_name="planned")
                p2 = self.plan(doc, output_root=self.out, output_name="other")
        finally:
            os.chdir(cwd)
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual((p1.canonical_json, p1.sha256), (p2.canonical_json, p2.sha256), "canonical plan does not depend on output names or machine paths")
        self.assertNotIn(self.tmp.encode(), p1.canonical_json)

    def test_plan_hash_changes_when_anything_selected_changes(self):
        a = self.put("a", tone("square", 480, 6400))
        base = self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])).sha256
        self.assertNotEqual(base, self.plan(manifest([event_use("e", "a", 100, 3300, 0, 2)], [a])).sha256)
        self.assertNotEqual(base, self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2, gain=(1, 2))], [a])).sha256)
        self.assertNotEqual(base, self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2, decision_ref="d")], [a])).sha256)
        self.assertNotEqual(base, self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a], gap="REQUIRE_FULL_COVERAGE")).sha256)
        self.assertEqual(base, self.plan(manifest([event_use("e", "a", 0, 3200, 0, 2)], [a])).sha256)

    def test_episode_cross_check(self):
        a = self.put("a", tone("square", 480, 6400))
        ep = to_bytes(episode_doc(fps=(30, 1), total=3, clips=color_clips([3]), captions=[]))
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [a], total=3)
        self.assertEqual(self.plan(doc, episode_bytes=ep).status, "READY")
        for bad_ep, code in ((to_bytes(episode_doc(fps=(24, 1), total=3, clips=color_clips([3]), captions=[])), "episode_fps_mismatch"),
                             (to_bytes(episode_doc(fps=(30, 1), total=4, clips=color_clips([4]), captions=[])), "episode_frames_mismatch")):
            self.assertEqual(rejected(lambda: self.plan(doc, episode_bytes=bad_ep)), {code})
        doc2 = manifest([event_use("e", "a", 0, 3200, 0, 2)], [a], total=3, )
        doc2["timeline"]["episode_sha256"] = "0" * 64
        self.assertEqual(rejected(lambda: self.plan(doc2, episode_bytes=ep)), {"episode_sha256_mismatch"})
        doc2["timeline"]["episode_sha256"] = sha(ep)
        self.assertEqual(self.plan(doc2, episode_bytes=ep).status, "READY")


if __name__ == "__main__":
    unittest.main()
