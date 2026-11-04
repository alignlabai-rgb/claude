"""Opt-in tests that run REAL ffmpeg/ffprobe. Skipped unless VIDEO_CORE_INTEGRATION=1 and both tools exist.

    VIDEO_CORE_INTEGRATION=1 python3 -m unittest tests.test_integration_ffmpeg -v
    VIDEO_CORE_TEST_FONT=/path/to/font.ttf  (optional) also exercises drawtext burn-in with that local font.

Source media is generated at test time by ffmpeg's synthetic lavfi sources into a temp
directory; nothing is downloaded and nothing is committed. The supplied 6 s case is
rendered by the CLI as documented in USAGE.md (see evidence/), not duplicated here.
"""

import json
import os
import shutil
import subprocess
import unittest

from tests.helpers import TmpCase, tree_snapshot, to_bytes
from video_core import Limits
from video_core import receipt as rc
from video_core.render import render

FF, FP = shutil.which("ffmpeg"), shutil.which("ffprobe")
ENABLED = os.environ.get("VIDEO_CORE_INTEGRATION") == "1" and FF and FP
FONT = os.environ.get("VIDEO_CORE_TEST_FONT")


def gen(args, out):
    subprocess.run([FF, "-v", "error", "-nostdin", "-y", *args, out], check=True, timeout=120)


@unittest.skipUnless(ENABLED, "set VIDEO_CORE_INTEGRATION=1 with ffmpeg+ffprobe on PATH")
class RealFfmpeg(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets = self.mkdir("assets")
        self.out = self.mkdir("out")

    def _render(self, doc, name, **kw):
        return render(to_bytes(doc), limits=Limits(max_runtime_seconds=240), asset_root=self.assets, output_root=self.out, output_name=name, **kw)

    def test_file_sources_at_ntsc_rate(self):
        gen(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p"], os.path.join(self.assets, "clip.mp4"))
        gen(["-f", "lavfi", "-i", "color=c=0xcc3322:s=100x60", "-frames:v", "1"], os.path.join(self.assets, "still.png"))
        doc = {"version": 1, "fps": {"n": 30000, "d": 1001}, "canvas": {"width": 640, "height": 360}, "total_frames": 90,
               "clips": [{"id": "v", "start_frame": 0, "duration_frames": 30, "source": {"kind": "file", "path": "clip.mp4"}},
                         {"id": "i", "start_frame": 30, "duration_frames": 30, "source": {"kind": "file", "path": "still.png"}},
                         {"id": "c", "start_frame": 60, "duration_frames": 30, "source": {"kind": "color", "value": "#102030"}}],
               "captions": [{"id": "a", "start_frame": 10, "end_frame": 80, "text": "テスト"}]}
        before = tree_snapshot(self.assets)
        r = self._render(doc, "ntsc")
        self.assertEqual(r["status"], rc.STATUS_MATCH, json.dumps(r["readback"], indent=1)[:2000])
        self.assertTrue(all(c["ok"] for c in r["readback"]["comparison"]["checks"]))
        self.assertEqual(tree_snapshot(self.assets), before)
        v = r["readback"]["summary"]["streams"][0]
        self.assertEqual((v["r_frame_rate"], v["nb_read_frames"]), ("30000/1001", "90"))

    @unittest.skipUnless(FONT and os.path.isfile(FONT), "set VIDEO_CORE_TEST_FONT to a local .ttf/.otf with the needed glyphs")
    def test_burn_in_with_a_provided_local_font(self):
        shutil.copyfile(os.path.realpath(FONT), os.path.join(self.assets, "font" + os.path.splitext(FONT)[1].lower()))
        doc = {"version": 1, "fps": {"n": 30, "d": 1}, "canvas": {"width": 640, "height": 360}, "total_frames": 60,
               "clips": [{"id": "c", "start_frame": 0, "duration_frames": 60, "source": {"kind": "color", "value": "#304050"}}],
               "captions": [{"id": "a", "start_frame": 10, "end_frame": 50, "text": "a:b 'q' \\ %{n}\nテスト"}]}
        r = self._render(doc, "burn", burn_in_font="font" + os.path.splitext(FONT)[1].lower())
        self.assertEqual(r["status"], rc.STATUS_MATCH, r["executions"][-2]["stderr"]["text"][:1500] if len(r["executions"]) > 1 else r)


if __name__ == "__main__":
    unittest.main()
