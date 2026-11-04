"""The real render is run once, by hand. These tests exercise the orchestrator with FAKE ffmpeg/ffprobe only: no real render here."""
import json
import os
import shutil
import tempfile
import unittest

from common import EVID  # noqa: F401
import render_once as R
from tests.fontbuilder import make_font
from tests.helpers import good_probe, make_fake_tools, fake_calls


def font_file(td):
    p = os.path.join(td, "perm.ttf")
    with open(p, "wb") as fh:
        fh.write(make_font(fmt12=[(0, 0x10FFFF, 1)]))
    return p


def probe_for_case():
    return good_probe(width=320, height=180, fps="30000/1001", frames=54, duration="1.801800")


class Guard(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="jpcap_guard_")
        self.addCleanup(shutil.rmtree, self.td, True)
        self.tools = os.path.join(self.td, "bin")
        os.makedirs(self.tools)
        self.work = os.path.join(self.td, "work")

    def test_with_caps_inserts_only_additive_options_at_the_right_places(self):
        argv = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-nostats", "-n", "-filter_complex", "G", "-map", "[v]", "-c:v", "libx264", "-f", "mp4", "out"]
        out, delta = R.with_caps(argv)
        self.assertEqual(delta, ["-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1"])
        self.assertEqual(out[:8], ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-nostats", "-n", "-filter_threads"])
        self.assertLess(out.index("-threads"), out.index("-c:v"))
        rest = list(out)
        for tok in delta:
            rest.remove(tok)
        self.assertEqual(rest, argv)                                  # nothing else changed

    def test_exactly_one_real_launch_then_refusal_even_after_success(self):
        ffmpeg, ffprobe = make_fake_tools(self.tools, probe=probe_for_case())
        r1 = R.render(self.work, ffmpeg, ffprobe, font_file(self.td))
        self.assertEqual(r1["status"], "READBACK_MATCH", r1.get("prerequisites"))
        renders = [c for c in fake_calls(self.tools) if "-filter_complex" in c["argv"]]
        self.assertEqual(len(renders), 1)
        a = renders[0]["argv"]
        self.assertEqual(a[a.index("-filter_threads") + 1], "1")
        self.assertEqual(a[a.index("-threads") + 1], "1")
        self.assertIn("-fs", a)
        self.assertEqual(int(a[a.index("-fs") + 1]), 2 * 1024 * 1024)
        r2 = R.render(self.work, ffmpeg, ffprobe, font_file(self.td))
        self.assertEqual(r2["status"], "REFUSED_ALREADY_ATTEMPTED")
        self.assertEqual(len([c for c in fake_calls(self.tools) if "-filter_complex" in c["argv"]]), 1)

    def test_a_failed_attempt_is_kept_and_cannot_be_retried(self):
        ffmpeg, ffprobe = make_fake_tools(self.tools, probe=probe_for_case(), mode="nonzero")
        r1 = R.render(self.work, ffmpeg, ffprobe, font_file(self.td))
        self.assertEqual(r1["status"], "RENDER_NONZERO_EXIT")
        self.assertTrue(os.path.exists(os.path.join(self.work, R.MARKER)))
        self.assertTrue(os.path.exists(os.path.join(self.work, "out", R.OUTPUT_NAME, "receipt.render-once.json")))
        self.assertIn("output.mp4.partial", r1["files"])                       # partial output is retained and described
        r2 = R.render(self.work, ffmpeg, ffprobe, font_file(self.td))
        self.assertEqual(r2["status"], "REFUSED_ALREADY_ATTEMPTED")
        self.assertEqual(len([c for c in fake_calls(self.tools) if "-filter_complex" in c["argv"]]), 1)

    def test_missing_tool_is_a_skip_with_no_marker_and_no_files(self):
        r = R.render(self.work, os.path.join(self.td, "no-such-ffmpeg"), os.path.join(self.td, "no-such-ffprobe"), font_file(self.td))
        self.assertEqual(r["status"], "SKIPPED_PREREQUISITE")
        self.assertFalse(os.path.exists(os.path.join(self.work, R.MARKER)))
        self.assertFalse(os.path.exists(os.path.join(self.work, "out")))

    def test_missing_filter_or_glyph_is_a_skip_not_a_try(self):
        ffmpeg, ffprobe = make_fake_tools(self.tools, probe=probe_for_case(), filters=["color", "trim"])
        r = R.render(self.work, ffmpeg, ffprobe, font_file(self.td))
        self.assertEqual(r["status"], "SKIPPED_PREREQUISITE")
        self.assertEqual([c for c in fake_calls(self.tools) if "-filter_complex" in c["argv"]], [])
        ffmpeg, ffprobe = make_fake_tools(self.tools, probe=probe_for_case())
        small = os.path.join(self.td, "ascii.ttf")
        with open(small, "wb") as fh:
            fh.write(make_font(fmt4=[(0x20, 0x7E)]))
        r = R.render(os.path.join(self.td, "work2"), ffmpeg, ffprobe, small)
        self.assertEqual(r["status"], "SKIPPED_PREREQUISITE")
        self.assertIn("font lacks glyphs", " ".join(r["prerequisites"]["reasons"]))

    def test_cli_refuses_without_the_explicit_flag_and_inside_the_repository(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(R.main(["--workdir", self.work]), 2)
            self.assertEqual(R.main(["--workdir", os.path.join(R.ROOT, "scratch-should-not-exist"), "--i-accept-the-single-attempt"]), 2)
        self.assertFalse(os.path.exists(os.path.join(R.ROOT, "scratch-should-not-exist")))

    def test_the_render_corpus_respects_every_declared_bound(self):
        with open(os.path.join(R.HERE, "render_case.episode.json"), "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        fps = doc["fps"]["n"] / doc["fps"]["d"]
        self.assertLessEqual(doc["total_frames"], 60)
        self.assertLessEqual(doc["total_frames"] / fps, 2.0)
        self.assertLessEqual((doc["canvas"]["width"], doc["canvas"]["height"]), (320, 180))
        self.assertEqual(R.THREAD_CAP, 1)
        self.assertLessEqual(R.THREAD_CAP, 2)
        self.assertLessEqual(R.HARD_SECONDS, 30)
        self.assertLessEqual(R.OUT_BYTES, 2 * 1024 * 1024)
        self.assertEqual(len(R.SHEET_FRAMES), 3)
        self.assertLess(max(R.SHEET_FRAMES), doc["total_frames"])


if __name__ == "__main__":
    unittest.main()
