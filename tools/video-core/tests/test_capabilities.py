"""Capability-listing parsers: observed FFmpeg 6.1.1 layouts, the reconstructed two-flag layout,
noise rejection, exact-name matching, CRLF, and the fail-closed behaviour of missing_capabilities."""

import os
import unittest

from tests.helpers import ROOT, POSIX, TmpCase, make_fake_tools
from video_core import Limits, capabilities as cap, tools

FIX = os.path.join(ROOT, "tests", "fixtures", "ffmpeg_listings")
REQUIRED_FILTERS = {"color", "concat", "format", "scale", "setpts", "setsar", "trim"}


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8", newline="") as fh:
        return fh.read()


class Filters(unittest.TestCase):
    def test_observed_ffmpeg6_three_flag_layout(self):
        got = cap.parse_filters(fx("filters.ffmpeg-6.1.1.observed.txt"))
        self.assertEqual(got.recognised, 17)
        self.assertEqual(got.ignored, 8, "exactly the 8 header/legend lines")
        self.assertTrue(REQUIRED_FILTERS | {"drawtext", "fps", "pad", "null"} <= got.names)
        self.assertEqual(got.names & {"scale", "scale2ref", "colorkey", "color"}, {"scale", "scale2ref", "colorkey", "color"})

    def test_reconstructed_two_flag_layout_including_the_quoted_lines(self):
        got = cap.parse_filters(fx("filters.two-flag-layout.reconstructed.txt"))
        self.assertTrue(REQUIRED_FILTERS | {"drawtext", "fps", "pad", "null"} <= got.names)
        self.assertEqual(got.recognised, 13)
        self.assertEqual(got.ignored, 7, "the 7 header/legend lines")

    def test_the_three_quoted_lines_verbatim_and_with_padding(self):
        quoted = [" .. color |->V Provide an uniformly colored input.", " .. scale V->V Scale the input video size and/or convert the image format.",
                  " T. drawtext V->V Draw text ..."]
        self.assertEqual(cap.parse_filters("\n".join(quoted)).names, {"color", "scale", "drawtext"})
        padded = [" .. color            |->V       Provide ...", " .. scale            V->V       Scale ...", " T. drawtext         V->V       Draw ..."]
        self.assertEqual(cap.parse_filters("\n".join(padded)).names, {"color", "scale", "drawtext"})

    def test_both_layouts_in_one_listing_union(self):
        text = fx("filters.ffmpeg-6.1.1.observed.txt") + fx("filters.two-flag-layout.reconstructed.txt")
        self.assertEqual(cap.parse_filters(text).recognised, 17 + 13)

    def test_crlf_and_trailing_whitespace_and_tabs(self):
        for variant in (lambda t: t.replace("\n", "\r\n"), lambda t: t.replace("\n", "  \n"), lambda t: t.replace("  ", "\t").replace(" V", "\tV")):
            for name in ("filters.ffmpeg-6.1.1.observed.txt", "filters.two-flag-layout.reconstructed.txt"):
                self.assertGreaterEqual(cap.parse_filters(variant(fx(name))).recognised, 13, name)

    def test_malformed_and_header_lines_are_never_entries(self):
        got = cap.parse_filters(fx("filters.malformed.txt"))
        self.assertEqual((got.recognised, got.names), (0, set()))
        self.assertEqual(got.ignored, 13)
        self.assertEqual(len(got.sample_ignored), 5, "diagnostic sample is bounded")
        self.assertTrue(all(len(s) <= 80 for s in got.sample_ignored))

    def test_legend_lines_of_both_layouts_do_not_register_equals_sign_names(self):
        legend = "Filters:\n  T.. = Timeline support\n  .S. = Slice threading\n  ..C = Command support\n  T. = Timeline support\n  .C = Command support\n  V = Video input/output\n  | = Source or sink filter\n"
        self.assertEqual(cap.parse_filters(legend).recognised, 0)

    def test_empty_and_binary_noise(self):
        for text in ("", "\n\n", "\x00\x01\x02 garbage\n\xff\xfe", "ffmpeg: error while loading shared libraries\n"):
            self.assertEqual(cap.parse_filters(text).recognised, 0)


class Encoders(unittest.TestCase):
    def test_observed_layout_and_exact_names(self):
        got = cap.parse_encoders(fx("encoders.ffmpeg-6.1.1.observed.txt"))
        self.assertEqual((got.recognised, got.ignored), (5, 10))
        self.assertEqual(got.names, {"a64multi", "aac", "libx264", "libx264rgb", "mpeg4"})
        self.assertIn("libx264", got.names)

    def test_only_the_six_character_flag_layout_is_accepted(self):
        self.assertEqual(cap.parse_encoders(" V....D libx264   libx264 H.264").names, {"libx264"})
        self.assertEqual(cap.parse_encoders(" A....D aac   AAC").names, {"aac"})
        for bad in (" V...D libx264 x", " V.....D libx264 x", " X....D libx264 x", " V....Q libx264 x", " V..... = Video", " ------"):
            self.assertEqual(cap.parse_encoders(bad).recognised, 0, bad)

    def test_crlf(self):
        self.assertEqual(cap.parse_encoders(fx("encoders.ffmpeg-6.1.1.observed.txt").replace("\n", "\r\n")).recognised, 5)


class Demuxers(unittest.TestCase):
    def test_observed_layout_aliases_and_exactness(self):
        got = cap.parse_demuxers(fx("demuxers.ffmpeg-6.1.1.observed.txt"))
        self.assertEqual((got.recognised, got.ignored), (6, 4))
        self.assertTrue({"image2", "mov", "mp4", "matroska", "webm", "aac", "concat"} <= got.names)
        self.assertIn("image2pipe", got.names)
        self.assertNotIn("image", got.names)

    def test_mux_flag_variant_and_headers_rejected(self):
        self.assertEqual(cap.parse_demuxers(" DE mov,mp4,m4a Quick").names, {"mov", "mp4", "m4a"})
        self.assertEqual(cap.parse_demuxers(" D  image2 sequence").names, {"image2"})
        for bad in (" D. = Demuxing supported", " .E = Muxing supported", "File formats:", " --", " E  mp4 muxer only"):
            self.assertEqual(cap.parse_demuxers(bad).recognised, 0, bad)

    def test_crlf(self):
        self.assertEqual(cap.parse_demuxers(fx("demuxers.ffmpeg-6.1.1.observed.txt").replace("\n", "\r\n")).recognised, 6)


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class MissingCapabilitiesFailClosed(TmpCase):
    REQ = {"encoders": ["libx264"], "filters": sorted(REQUIRED_FILTERS | {"drawtext"}), "demuxers": ["mov", "image2"]}

    def check(self, **behavior):
        ff, _ = make_fake_tools(self.mkdir("bin"), **behavior)
        return tools.missing_capabilities(ff, self.REQ, Limits(), self.tmp)[0]

    def test_everything_present_in_the_three_flag_layout(self):
        got = self.check()
        self.assertEqual((got["missing"], got["probe_failed"]), ({}, []))
        self.assertGreater(got["details"]["-filters"]["recognised"], 5)

    def test_everything_present_in_the_two_flag_layout(self):
        got = self.check(filters_layout="two")
        self.assertEqual((got["missing"], got["probe_failed"]), ({}, []))

    def test_everything_present_with_crlf_output(self):
        got = self.check(filters_layout="two", crlf=True)
        self.assertEqual((got["missing"], got["probe_failed"]), ({}, []))

    def test_truly_missing_is_reported_by_name_in_either_layout(self):
        for layout in ("three", "two"):
            got = self.check(filters_layout=layout, filters=["color", "trim", "scale2ref"])
            self.assertEqual(got["probe_failed"], [])
            self.assertEqual(got["missing"]["filters"], sorted({"concat", "format", "scale", "setpts", "setsar", "drawtext"}), layout)

    def test_unrecognised_layout_is_probe_failed_not_everything_missing_and_not_success(self):
        got = self.check(filters_layout="unknown")
        self.assertEqual(got["probe_failed"], ["-filters"])
        self.assertNotIn("filters", got["missing"])
        self.assertIn("not recognised", got["details"]["-filters"]["reason"])
        self.assertTrue(got["details"]["-filters"]["sample_ignored"])

    def test_missing_encoder_and_demuxer(self):
        got = self.check(encoder="libx265")
        self.assertEqual(got["missing"], {"encoders": ["libx264"]})

    def test_failed_or_truncated_listing_is_probe_failed(self):
        got = self.check(filters_exit=2)
        self.assertEqual(got["probe_failed"], ["-filters"])
        self.assertIn("listing unavailable", got["details"]["-filters"]["reason"])


@unittest.skipUnless(os.environ.get("VIDEO_CORE_INTEGRATION") == "1", "opt-in: parses the REAL local ffmpeg listings (no render)")
class RealListings(unittest.TestCase):
    def test_real_ffmpeg_listings_are_understood(self):
        import shutil
        ff = shutil.which("ffmpeg")
        if not ff:
            self.skipTest("ffmpeg not on PATH")
        req = {"encoders": ["libx264"], "filters": sorted(REQUIRED_FILTERS | {"fps", "pad"}), "demuxers": ["mov", "image2"]}
        got, runs = tools.missing_capabilities(ff, req, Limits(), os.getcwd())
        self.assertEqual((got["missing"], got["probe_failed"]), ({}, []), got)
        for d in got["details"].values():
            self.assertGreater(d["recognised"], 50)


if __name__ == "__main__":
    unittest.main()
