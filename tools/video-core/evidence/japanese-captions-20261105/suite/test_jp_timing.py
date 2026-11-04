import os
import tempfile
import unittest
from fractions import Fraction

from common import EVID  # noqa: F401
import jpcap_eval as J
import oracle
from video_core import export_srt, load_episode, prepare
from video_core.timebase import frame_to_ms, ms_to_srt

FPS_SET = [(1, 1), (7, 1), (24, 1), (25, 1), (30, 1), (60, 1), (120, 1), (24000, 1001), (30000, 1001), (60000, 1001), (120000, 1001), (89, 3)]


class OracleItself(unittest.TestCase):
    def test_midpoint_form_equals_integer_form(self):
        for n, d in FPS_SET:
            fps = Fraction(n, d)
            for s in range(0, 14):
                for e in range(s + 1, 15):
                    self.assertEqual(oracle.visible_frames_midpoint(s, e, 18, fps), oracle.visible_frames_integer(s, e, 18), (n, d, s, e))

    def test_inclusive_start_exclusive_end_and_single_frame(self):
        fps = Fraction(30000, 1001)
        self.assertEqual(sorted(oracle.visible_frames_midpoint(5, 8, 20, fps)), [5, 6, 7])
        self.assertEqual(sorted(oracle.visible_frames_midpoint(0, 1, 20, fps)), [0])
        self.assertEqual(sorted(oracle.visible_frames_midpoint(19, 20, 20, fps)), [19])

    def test_adjacent_windows_partition_without_overlap_or_hole(self):
        fps = Fraction(24000, 1001)
        a, b = oracle.visible_frames_midpoint(0, 13, 40, fps), oracle.visible_frames_midpoint(13, 40, 40, fps)
        self.assertFalse(a & b)
        self.assertEqual(a | b, frozenset(range(40)))
        self.assertEqual(max(a) + 1, min(b))

    def test_enable_evaluator_is_inclusive_and_refuses_other_shapes(self):
        self.assertEqual(sorted(oracle.enable_frames("between(n,3,5)", 10)), [3, 4, 5])
        for bad in ("between(n,3,5)+1", "gte(n,3)", "between(t,3,5)", "between(n, 3,5)"):
            with self.assertRaises(ValueError):
                oracle.enable_frames(bad, 10)


class ProductionAgainstOracle(unittest.TestCase):
    def test_boundary_milliseconds_equal_independent_fraction_rounding(self):
        for n, d in FPS_SET:
            fps = Fraction(n, d)
            for f in range(0, 2500):
                self.assertEqual(frame_to_ms(f, fps), oracle.round_half_up_ms(f, fps), (n, d, f))

    def test_srt_timestamp_text_matches_independent_formatter(self):
        for ms in (0, 1, 999, 1000, 59999, 60000, 3599999, 3600000, 36000000 + 123):
            own = "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
            self.assertEqual(ms_to_srt(ms), own)

    def test_plan_enable_expression_and_srt_cues_for_edge_windows(self):
        rep = J.timing_report()
        self.assertEqual(rep["oracle_midpoint_vs_integer_disagreements"], 0)
        for b in rep["boundary_ms_vs_independent_rational"]:
            self.assertEqual((b["ms_disagreements"], b["srt_format_disagreements"]), (0, 0), b["fps"])
        self.assertEqual(len(rep["plan_and_srt_vs_oracle"]), 36)
        for p in rep["plan_and_srt_vs_oracle"]:
            self.assertTrue(p["all_ok"], (p["fps"], p["set"]))
            for pair in p["touching_pairs_share_boundary_ms"]:
                self.assertTrue(pair[2], (p["fps"], p["set"], pair))

    def test_start_and_end_of_clip_exact_frames_in_the_real_plan(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "assets"))
            with open(os.path.join(td, "assets", "font.ttf"), "wb") as fh:
                fh.write(J.fake_font_bytes())
            plan, _ = J._plan_enable([("a", 0, 1, "x"), ("b", 39, 40, "y")], (30000, 1001), 40, td)
            en = [c["enable"] for c in plan.canonical["burn_in"]["captions"]]
            self.assertEqual(en, ["between(n,0,0)", "between(n,39,39)"])


class InstrumentSensitivity(unittest.TestCase):
    """The comparisons above would be worthless if they could not fail. Seed off-by-one defects and see them caught."""

    def test_enable_end_plus_one_is_a_bleed_and_start_minus_one_is_early(self):
        fps = Fraction(24000, 1001)
        want = oracle.visible_frames_midpoint(10, 20, 40, fps)
        self.assertNotEqual(oracle.enable_frames("between(n,10,20)", 40), want)       # inclusive end used as exclusive: +1 frame
        self.assertNotEqual(oracle.enable_frames("between(n,9,19)", 40), want)        # one frame early
        self.assertEqual(oracle.enable_frames("between(n,10,19)", 40), want)

    def test_an_exporter_that_made_the_end_inclusive_is_caught_by_adjacency(self):
        fps = Fraction(24000, 1001)
        s, e = 13, 26
        end_incl_ms = oracle.round_half_up_ms(e + 1, fps)           # seeded defect: end = last frame's END
        got = oracle.consumer_visible(oracle.round_half_up_ms(s, fps), end_incl_ms, 40, fps, "round_half_up_ms")
        self.assertNotEqual(got, oracle.visible_frames_midpoint(s, e, 40, fps))

    def test_truncating_instead_of_rounding_start_shifts_a_frame_for_round_consumers(self):
        fps = Fraction(24000, 1001)
        diff = [f for f in range(1, 40) if oracle.floor_ms(f, fps) != oracle.round_half_up_ms(f, fps)]
        self.assertTrue(diff)                                                         # the two policies differ at some boundaries


class ConsumerModelFindings(unittest.TestCase):
    """Measured on consumer MODELS (not real players). Documents the exporter's behaviour; asserts no real defect against its own contract."""

    def test_half_up_is_exact_for_round_and_ceil_consumers_everywhere(self):
        for n, d in FPS_SET:
            r = oracle.compare_policy(Fraction(n, d), "half_up", 40)
            self.assertEqual(r["round_half_up_ms"]["mismatching_windows"], 0, (n, d))
            self.assertEqual(r["ceil_ms"]["mismatching_windows"], 0, (n, d))

    def test_half_up_differs_for_strict_exact_pts_and_floor_consumers_when_the_period_is_not_whole_ms(self):
        for n, d in FPS_SET:
            r = oracle.compare_policy(Fraction(n, d), "half_up", 40)
            whole_ms = (1000 * d) % n == 0
            if whole_ms:
                self.assertEqual(r["exact_pts_vs_ms"]["mismatching_windows"], 0, (n, d))
            else:
                self.assertGreater(r["exact_pts_vs_ms"]["mismatching_windows"], 0, (n, d))
                self.assertEqual(r["exact_pts_vs_ms"]["mismatching_windows"], r["floor_ms"]["mismatching_windows"], (n, d))
        first = oracle.compare_policy(Fraction(24, 1), "half_up", 10)["exact_pts_vs_ms"]["first"]
        self.assertEqual(first, [0, 1, [1], []])                                      # window [0,1): a strict consumer also shows frame 1

    def test_proposed_floor_policy_is_exact_for_all_four_consumer_models(self):
        for n, d in FPS_SET:
            r = oracle.compare_policy(Fraction(n, d), "floor", 40)
            for m, v in r.items():
                self.assertEqual(v["mismatching_windows"], 0, (n, d, m))

    def test_floor_policy_precondition_period_above_two_ms(self):
        # stated limit of the proposal: with a frame period of ~1 ms boundaries collide
        fps = Fraction(1000, 1)
        self.assertEqual(oracle.floor_ms(5, fps), 5)
        fps = Fraction(2000, 1)
        self.assertEqual(oracle.floor_ms(1, fps), 0)           # 0.5 ms period: two frames share a ms -> unrepresentable (production rejects >1000 fps)


class StrictReader(unittest.TestCase):
    def test_rejects_deviations(self):
        ok = b"1\n00:00:00,000 --> 00:00:01,000\nx\n"
        self.assertEqual(oracle.parse_srt_strict(ok)[0]["text"], "x")
        for bad in (b"\xef\xbb\xbf" + ok, ok.replace(b"\n", b"\r\n"), ok + b"\n", b"2\n00:00:00,000 --> 00:00:01,000\nx\n",
                    b"1\n00:00:01,000 --> 00:00:01,000\nx\n", b"1\n00:00:00,000 --> 00:00:01,000\n", b"1\n00:00:00.000 --> 00:00:01,000\nx\n", b"1\n00:00:00,000 --> 00:00:01,000\n\xff\n"):
            with self.assertRaises((oracle.SrtError, UnicodeDecodeError)):
                oracle.parse_srt_strict(bad)

    def test_timestamp_and_index_lookalikes_inside_text_do_not_confuse_the_strict_reader(self):
        cases = {c[0]: c[2] for c in __import__("corpus_def").CASES}
        for cid in ("timestamp_lookalike", "numeric_line", "arrow_inline"):
            ep = load_episode(J.single_case_episode(cases[cid]))
            cues = oracle.parse_srt_strict(export_srt(ep))
            self.assertEqual(len(cues), 1, cid)
            self.assertEqual(cues[0]["text"], cases[cid], cid)


if __name__ == "__main__":
    unittest.main()
