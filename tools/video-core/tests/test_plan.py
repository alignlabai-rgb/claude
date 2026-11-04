import json
import os
import unittest

from tests.helpers import TmpCase, color_clips, episode_doc, no_process, sample_bytes, to_bytes, tree_snapshot
from video_core import prepare


class AcceptanceCase1(TmpCase):
    def test_supplied_episode_plans_exactly_as_specified(self):
        with no_process():
            p = prepare(sample_bytes())
        c = p.canonical
        self.assertEqual(c["episode"]["total_frames"], 180)
        self.assertEqual(c["episode"]["fps"], {"n": 30, "d": 1, "rational": "30/1"})
        self.assertEqual(c["episode"]["canvas"], {"width": 640, "height": 360})
        self.assertEqual(c["episode"]["duration_seconds"], "6/1")
        self.assertEqual([(x["start_frame"], x["end_frame"]) for x in c["captions"]], [(15, 75), (105, 165)])
        self.assertEqual([x["text"] for x in c["captions"]], ["テスト A", "テスト B"])
        self.assertEqual([(x["srt_start"], x["srt_end"]) for x in c["captions"]], [("00:00:00,500", "00:00:02,500"), ("00:00:03,500", "00:00:05,500")])
        self.assertEqual([(x["start_frame"], x["end_frame"], x["source"]["value"]) for x in c["clips"]], [(0, 90, "#30506a"), (90, 180, "#6a5030")])
        self.assertEqual(sum(x["duration_frames"] for x in c["clips"]), 180)
        self.assertEqual(c["expected_readback"]["frames"], 180)

    def test_plan_is_deterministic_byte_for_byte(self):
        a, b = prepare(sample_bytes()), prepare(sample_bytes())
        self.assertEqual(a.canonical_json, b.canonical_json)
        self.assertEqual(a.sha256, b.sha256)
        self.assertEqual(json.dumps(a.canonical, sort_keys=True), json.dumps(b.canonical, sort_keys=True))

    def test_plan_hash_does_not_depend_on_machine_paths(self):
        out1, out2 = self.mkdir("one", "out"), self.mkdir("two", "deeper", "out")
        a = prepare(sample_bytes(), output_root=out1, output_name="r")
        b = prepare(sample_bytes(), output_root=out2, output_name="other")
        self.assertEqual(a.sha256, b.sha256)
        self.assertNotIn(self.tmp.encode(), a.canonical_json)

    def test_plan_creates_no_files_and_launches_no_process(self):
        out, assets = self.mkdir("out"), self.mkdir("assets")
        before = tree_snapshot(self.tmp)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with no_process():
                prepare(sample_bytes(), asset_root=assets, output_root=out, output_name="planned")
        finally:
            os.chdir(cwd)
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_plan_changes_when_the_episode_changes(self):
        base = prepare(sample_bytes()).sha256
        doc = json.loads(sample_bytes())
        doc["captions"][0]["text"] = "テスト A!"
        self.assertNotEqual(prepare(to_bytes(doc)).sha256, base)
        doc = json.loads(sample_bytes())
        doc["clips"][0]["source"]["value"] = "#30506b"
        self.assertNotEqual(prepare(to_bytes(doc)).sha256, base)

    def test_argv_is_a_structured_list_with_no_user_text_and_stable_flags(self):
        argv = prepare(sample_bytes()).canonical["ffmpeg"]["argv"]
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(a, (str, dict)) for a in argv))
        flat = [a for a in argv if isinstance(a, str)]
        for flag in ("-nostdin", "-n", "-an", "-sn", "-dn", "-frames:v", "-fps_mode", "-fs", "-f"):
            self.assertIn(flag, flat)
        self.assertEqual(flat[flat.index("-frames:v") + 1], "180")
        self.assertEqual(argv[-1], {"run_file": "output.mp4.partial"})
        self.assertNotIn("テスト", " ".join(flat))
        graph = flat[flat.index("-filter_complex") + 1]
        self.assertIn("color=c=0x30506a:s=640x360:r=30/1,trim=end_frame=90", graph)
        self.assertIn("concat=n=2:v=1:a=0[vout]", graph)
        self.assertNotIn("drawtext", graph, "no burn-in unless a local font is provided")

    def test_srt_hash_is_part_of_the_plan(self):
        p = prepare(sample_bytes())
        self.assertEqual(p.canonical["srt"]["sha256"], __import__("hashlib").sha256(p.srt).hexdigest())


class RationalPlan(unittest.TestCase):
    def test_ntsc_plan_uses_exact_rationals_everywhere(self):
        doc = episode_doc(fps=(30000, 1001), total=180, clips=color_clips([90, 90]),
                          captions=[{"id": "a", "start_frame": 15, "end_frame": 75, "text": "x"}])
        p = prepare(to_bytes(doc))
        c = p.canonical
        self.assertEqual(c["episode"]["duration_seconds"], "3003/500")
        self.assertEqual(c["episode"]["fps"]["rational"], "30000/1001")
        self.assertEqual(c["clips"][1]["start_seconds"], "3003/1000")
        self.assertEqual(c["captions"][0]["start_seconds"], "1001/2000")
        self.assertEqual(c["captions"][0]["srt_start"], "00:00:00,501")
        self.assertEqual(c["captions"][0]["srt_end"], "00:00:02,503")  # 75*1001/30 = 2502.5 -> 2503
        flat = [a for a in c["ffmpeg"]["argv"] if isinstance(a, str)]
        self.assertEqual(flat[flat.index("-r") + 1], "30000/1001")
        self.assertIn(":r=30000/1001,", flat[flat.index("-filter_complex") + 1])
        self.assertEqual(flat[flat.index("-frames:v") + 1], "180", "frame count stays an integer; no duration-based truncation")

    def test_single_clip_episode(self):
        doc = episode_doc(total=10, clips=color_clips([10]), captions=[])
        flat = [a for a in prepare(to_bytes(doc)).canonical["ffmpeg"]["argv"] if isinstance(a, str)]
        self.assertIn("concat=n=1:v=1:a=0[vout]", flat[flat.index("-filter_complex") + 1])

    def test_unreduced_rate_is_planned_as_given_but_reported_reduced(self):
        doc = episode_doc(fps=(60, 2), total=60, clips=color_clips([60]), captions=[])
        c = prepare(to_bytes(doc)).canonical
        self.assertEqual(c["episode"]["fps"], {"n": 60, "d": 2, "rational": "30/1"})
        self.assertEqual(c["episode"]["duration_seconds"], "2/1")


if __name__ == "__main__":
    unittest.main()
