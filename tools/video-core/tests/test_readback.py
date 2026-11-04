import unittest
from fractions import Fraction

from tests.helpers import good_probe
from video_core import readback

KW = dict(width=640, height=360, fps=Fraction(30), total_frames=180, codec="h264", pix_fmt="yuv420p")


def run(probe, **over):
    kw = dict(KW)
    kw.update(over)
    return readback.compare(readback.summarize(probe), **kw)


def failed(result):
    return {c["name"] for c in result["checks"] if not c["ok"]}


class Compare(unittest.TestCase):
    def test_matching_probe(self):
        r = run(good_probe())
        self.assertTrue(r["all_ok"], failed(r))

    def test_off_by_one_frame_count_is_a_mismatch(self):
        self.assertEqual(failed(run(good_probe(frames=179))), {"decoded_frame_count", "container_frame_count"})
        self.assertIn("decoded_frame_count", failed(run(good_probe(frames=181))))

    def test_wrong_dimensions_and_rate(self):
        self.assertEqual(failed(run(good_probe(width=638))), {"width"})
        self.assertEqual(failed(run(good_probe(fps="2997/100"))), {"r_frame_rate", "avg_frame_rate"})
        self.assertEqual(failed(run(good_probe(fps="0/0"))), {"r_frame_rate", "avg_frame_rate"})

    def test_ntsc_rate_and_duration_use_exact_rationals(self):
        ok = good_probe(fps="30000/1001", duration="6.006000")
        self.assertTrue(run(ok, fps=Fraction(30000, 1001))["all_ok"])
        self.assertTrue(failed(run(good_probe(fps="30/1", duration="6.006000"), fps=Fraction(30000, 1001))))

    def test_duration_drift_beyond_one_frame_fails(self):
        # tolerance is exactly one frame (1/30 s = 0.03333...)
        self.assertTrue(run(good_probe(duration="6.033000"))["all_ok"])
        self.assertIn("format_duration", failed(run(good_probe(duration="6.034000"))))
        self.assertIn("video_duration", failed(run(good_probe(duration="5.960000"))))
        self.assertIn("format_duration", failed(run(good_probe(duration="6.100000"))))
        self.assertIn("format_duration", failed(run(good_probe(duration="N/A"))))

    def test_extra_or_missing_streams(self):
        audio = {"index": 1, "codec_type": "audio", "codec_name": "aac"}
        self.assertEqual(failed(run(good_probe(extra_streams=[audio]))), {"non_video_stream_count"})
        self.assertIn("video_stream_count", failed(run({"streams": [], "format": {}})))

    def test_codec_and_pix_fmt(self):
        self.assertEqual(failed(run(good_probe(codec="mpeg4"))), {"codec_name"})
        self.assertEqual(failed(run(good_probe(pix_fmt="yuv444p"))), {"pix_fmt"})

    def test_every_check_is_recorded_with_expected_and_observed(self):
        for c in run(good_probe())["checks"]:
            self.assertEqual(set(c), {"name", "expected", "observed", "ok"})


if __name__ == "__main__":
    unittest.main()
