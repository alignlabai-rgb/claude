import unittest
from fractions import Fraction
from math import floor

from tests.helpers import episode_doc, color_clips, to_bytes
from video_core import timebase as tb
from video_core.episode import load_episode
from video_core.srt import export_srt

NTSC = Fraction(30000, 1001)


class RationalFps(unittest.TestCase):
    def test_exact_duration_at_ntsc(self):
        self.assertEqual(tb.duration_seconds(180, NTSC), Fraction(3003, 500))  # 6.006 s exactly
        self.assertEqual(tb.ratio_str(tb.duration_seconds(180, NTSC)), "3003/500")
        self.assertEqual(tb.duration_seconds(30000, NTSC), 1001)  # 30000 frames is exactly 1001 s

    def test_frame_to_ms_matches_exact_rational_half_up_for_every_frame(self):
        for frame in range(0, 18001):
            exact = Fraction(frame) / NTSC * 1000
            self.assertEqual(tb.frame_to_ms(frame, NTSC), floor(exact + Fraction(1, 2)), frame)

    def test_accumulated_rounding_drift_is_detected(self):
        """A naive per-frame rounded duration drifts by seconds; our conversion must not."""
        naive_per_frame_ms = round(1000 * 1001 / 30000)  # 33
        naive = 18000 * naive_per_frame_ms  # 594000 ms
        exact = tb.frame_to_ms(18000, NTSC)  # 600600 ms
        self.assertEqual(exact, 600_600)
        self.assertGreater(exact - naive, 6000, "the naive method drifts > 6 s over 18000 frames; this test must see it")

    def test_float_arithmetic_would_be_off_for_some_frame(self):
        """Documents why Fraction is required: float 29.97 disagrees with exact 30000/1001."""
        diffs = [f for f in range(0, 18001) if int(f / 29.97 * 1000 + 0.5) != tb.frame_to_ms(f, NTSC)]
        self.assertTrue(diffs)

    def test_monotonic_and_no_collapse_up_to_1000fps(self):
        prev = -1
        for frame in range(0, 2000):
            ms = tb.frame_to_ms(frame, Fraction(1000, 1))
            self.assertGreater(ms, prev)
            prev = ms

    def test_srt_endpoint_is_exclusive_end_frame_not_off_by_one(self):
        doc = episode_doc(fps=(30000, 1001), total=90, clips=color_clips([90]),
                          captions=[{"id": "a", "start_frame": 30, "end_frame": 60, "text": "x"}])
        srt = export_srt(load_episode(to_bytes(doc))).decode()
        # frame 30 -> 1001 ms; frame 60 -> 2002 ms. Frames 29/31/59/61 would give 968/1034/1968/2035.
        self.assertIn("00:00:01,001 --> 00:00:02,002", srt)

    def test_srt_timestamps_at_30fps_and_ntsc_differ_where_expected(self):
        self.assertEqual(tb.frame_to_srt(15, Fraction(30)), "00:00:00,500")
        self.assertEqual(tb.frame_to_srt(15, NTSC), "00:00:00,501")  # 500.5 rounds half up
        self.assertEqual(tb.frame_to_srt(30000, NTSC), "00:16:41,000")

    def test_reduced_and_unreduced_rates_agree(self):
        self.assertEqual(tb.frame_to_ms(77, Fraction(60, 2)), tb.frame_to_ms(77, Fraction(30, 1)))

    def test_ms_formatting_and_negative(self):
        self.assertEqual(tb.ms_to_srt(3_723_004), "01:02:03,004")
        with self.assertRaises(ValueError):
            tb.ms_to_srt(-1)

    def test_parse_ratio(self):
        self.assertEqual(tb.parse_ratio("30000/1001"), NTSC)
        with self.assertRaises(ValueError):
            tb.parse_ratio("0/0")
        with self.assertRaises(ValueError):
            tb.parse_ratio("abc")


if __name__ == "__main__":
    unittest.main()
