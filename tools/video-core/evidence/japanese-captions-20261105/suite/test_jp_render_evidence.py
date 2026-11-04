"""Consistency of the COMMITTED evidence of the one real render. The media itself is not in Git; if it is available (env JPCAP_MEDIA)
its hash is verified too, otherwise those checks are skipped and said so."""
import hashlib
import json
import os
import unittest

from common import IPAG_PATH, IPAG_SHA256, file_if_hash
import corpus_def as C
import jpcap_eval as J

RD = os.path.join(J.HERE, "render")


def jl(name):
    with open(os.path.join(RD, name), "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@unittest.skipUnless(os.path.exists(os.path.join(RD, "receipt.render-once.json")), "no committed render receipt")
class CommittedRenderReceipt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc = jl("receipt.render-once.json")
        cls.an = jl("analysis.json")
        cls.run1 = jl("analysis.run1-criteria-too-strict.json")
        cls.att = jl("attachments.json")

    def test_one_attempt_succeeded_and_marker_is_recorded(self):
        self.assertEqual(self.rc["status"], "READBACK_MATCH")
        self.assertEqual(self.rc["render_execution"]["exit_code"], 0)
        self.assertIsNone(self.rc["render_execution"]["signal"])
        self.assertFalse(self.rc["render_execution"]["timed_out"])
        self.assertEqual(jl("RENDER_ATTEMPTED.marker.json")["meaning"], "the one render has been started; a second call must refuse")
        self.assertEqual(self.rc["contract"]["retry"], "none")

    def test_declared_bounds_were_respected(self):
        ct = self.rc["contract"]
        self.assertLessEqual(ct["max_frames"], 60)
        self.assertLessEqual(ct["max_seconds_of_video"], 2)
        self.assertLessEqual(self.rc["wall_seconds"], 30)
        out = self.rc["files"]["output.mp4"]
        self.assertLessEqual(out["bytes"], 2 * 1024 * 1024)
        self.assertEqual(self.rc["argv_delta"], ["-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1"])
        self.assertLessEqual(self.rc["observed_peak_threads_of_ffmpeg_process"]["peak"], 2)
        self.assertLessEqual(len(self.rc["cpu_affinity"]["applied"]), 2)
        a = self.rc["argv_executed"]
        for tok, val in (("-threads", "1"), ("-filter_threads", "1"), ("-filter_complex_threads", "1")):
            self.assertEqual(a[a.index(tok) + 1], val)
        self.assertEqual(int(a[a.index("-fs") + 1]), 2 * 1024 * 1024)
        self.assertEqual(a.count("-filter_complex"), 1)
        self.assertEqual(sum(1 for x in a if x.endswith("output.mp4.partial")), 1)
        prod = [x for x in self.rc["argv_production"]]
        rest = list(a)
        for tok in self.rc["argv_delta"]:
            rest.remove(tok)
        self.assertEqual(rest, prod)                                 # executed argv = production argv + only the additive caps

    def test_it_is_the_new_corpus_not_the_supplied_example(self):
        self.assertEqual(self.rc["episode_sha256"], sha(os.path.join(J.HERE, "render_case.episode.json")))
        self.assertEqual(self.rc["srt_sha256"], sha(os.path.join(J.HERE, "render_case.srt")))
        self.assertNotEqual(self.rc["plan_sha256"], J.BASELINE_PLAN_SHA256)
        self.assertEqual(self.rc["plan_sha256"], sha(os.path.join(RD, "plan.canonical.json")))

    def test_plan_hash_is_reproducible_with_the_same_font_and_code(self):
        data = file_if_hash(IPAG_PATH, IPAG_SHA256)
        if data is None:
            self.skipTest("IPAGothic exact build not present")
        import tempfile
        from video_core import prepare
        from video_core.limits import Limits
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "assets"))
            with open(os.path.join(td, "assets", "font.ttf"), "wb") as fh:
                fh.write(data)
            lim = Limits(max_frames=60, max_dimension=320, max_runtime_seconds=30.0, max_output_bytes=2 * 1024 * 1024, max_captions=8, max_pixel_volume=320 * 180 * 60)
            with open(os.path.join(J.HERE, "render_case.episode.json"), "rb") as fh:
                plan = prepare(fh.read(), limits=lim, asset_root=os.path.join(td, "assets"), burn_in_font="font.ttf", font_size=C.RENDER_FONT_SIZE)
        self.assertEqual(plan.sha256, self.rc["plan_sha256"])

    def test_final_analysis_passed_every_machine_check_and_run1_is_kept(self):
        self.assertTrue(self.an["all_machine_checks_ok"])
        self.assertTrue(all(c["ok"] for c in self.an["checks"]))
        self.assertFalse(self.run1["all_machine_checks_ok"])
        failed = sorted(c["name"] for c in self.run1["checks"] if not c["ok"])
        self.assertEqual(failed, ["extracted_png_frames_are_the_indexed_decoded_frames", "r4_overflow_predicted_and_ink_touches_both_frame_edges"])
        self.assertEqual(self.run1["media_sha256"], self.an["media_sha256"])      # same media; only the criteria changed

    def test_every_frame_matches_the_rational_oracle_in_the_committed_analysis(self):
        names = {c["name"]: c for c in self.an["checks"]}
        self.assertTrue(names["every_frame_matches_rational_oracle_no_bleed_no_off_by_one"]["ok"])
        self.assertEqual(names["full_decode_frame_count"]["expected"], 54)
        self.assertTrue(names["frame_pts_equal_exact_rational_i_over_fps"]["ok"])
        import oracle
        from fractions import Fraction
        fps = Fraction(*C.RENDER_FPS)
        want = {}
        for cid, s, e, _t in C.RENDER_CAPTIONS:
            for i in oracle.visible_frames_midpoint(s, e, C.RENDER_TOTAL, fps):
                want[i] = cid
        rows = self.an["per_frame_visibility"]
        self.assertEqual(len(rows), 54)
        for r in rows:
            self.assertEqual(r["expected"], want.get(r["frame"]), r)
            self.assertEqual(r["ink"] > 0, r["frame"] in want, r)
            if r["frame"] in want:
                self.assertEqual(r["nearest"], r["expected"], r)

    def test_attachment_manifest_matches_media_when_available(self):
        listed = {f["name"]: f for f in self.att["files"]}
        self.assertEqual(listed["output.mp4"]["sha256"], self.an["media_sha256"])
        self.assertEqual(listed["output.mp4"]["sha256"], self.rc["files"]["output.mp4"]["sha256"])
        media = os.environ.get("JPCAP_MEDIA")
        if not media or not os.path.isfile(media):
            self.skipTest("set JPCAP_MEDIA=/path/to/output.mp4 to verify the attachment bytes")
        self.assertEqual(sha(media), listed["output.mp4"]["sha256"])

    def test_no_media_or_png_is_committed_here(self):
        for name in os.listdir(RD):
            self.assertFalse(name.lower().endswith((".mp4", ".png", ".mov", ".partial")), name)

    def test_author_observations_are_labelled_pending_and_not_independent(self):
        with open(os.path.join(RD, "AUTHOR_VISUAL_OBSERVATIONS.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("pending independent human legibility", text)
        self.assertIn("NOT independent review", text)


if __name__ == "__main__":
    unittest.main()
