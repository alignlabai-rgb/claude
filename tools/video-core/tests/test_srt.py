import hashlib
import unittest

from tests.helpers import EXAMPLE_SRT, color_clips, episode_doc, sample_bytes, to_bytes
from video_core import export_srt, load_episode


def srt_for(texts, fps=(30, 1)):
    caps = [{"id": f"c{i}", "start_frame": 10 * i, "end_frame": 10 * i + 5, "text": t} for i, t in enumerate(texts)]
    ep = load_episode(to_bytes(episode_doc(fps=fps, total=180, clips=color_clips([90, 90]), captions=caps)))
    return export_srt(ep)


class Srt(unittest.TestCase):
    def test_supplied_case_is_byte_identical_to_supplied_srt(self):
        got = export_srt(load_episode(sample_bytes()))
        with open(EXAMPLE_SRT, "rb") as fh:
            self.assertEqual(got, fh.read())

    def test_deterministic_and_utf8_lf_without_bom(self):
        a, b = export_srt(load_episode(sample_bytes())), export_srt(load_episode(sample_bytes()))
        self.assertEqual(hashlib.sha256(a).digest(), hashlib.sha256(b).digest())
        self.assertFalse(a.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", a)
        self.assertTrue(a.endswith(b"\n") and not a.endswith(b"\n\n"))
        a.decode("utf-8")

    def test_japanese_ascii_linebreaks_punctuation_preserved(self):
        texts = ["テスト A", "日本語、句読点。「括弧」！？", "two\nlines\nそして三行", "ASCII only, 123.", "ｆｕｌｌｗｉｄｔｈ ｶﾀｶﾅ 😀 e\u0301"]
        out = srt_for(texts).decode("utf-8")
        for t in texts:
            self.assertIn("\n" + t + "\n", out + "\n")
        self.assertEqual(out.count(" --> "), len(texts))

    def test_colon_quote_backslash_and_friends_are_verbatim_not_escaped(self):
        nasty = ['a:b', "it's", 'say "hi"', r"back\slash", r"C:\path\file", "100% %{n} $HOME `id` $(id)", "a --> b", "x;y|z&w", "<i>tag</i>", "[brackets]{braces}"]
        out = srt_for(nasty).decode("utf-8")
        for t in nasty:
            self.assertIn(t, out)
        self.assertNotIn("\\'", out)
        self.assertNotIn('\\"', out)
        self.assertNotIn("\\:", out)
        self.assertNotIn("\\\\", out)  # no doubling of the single backslash in r"back\slash"

    def test_ordering_is_by_start_end_then_input_order(self):
        caps = [{"id": "late", "start_frame": 50, "end_frame": 60, "text": "L"},
                {"id": "b", "start_frame": 10, "end_frame": 40, "text": "B"},
                {"id": "a", "start_frame": 10, "end_frame": 20, "text": "A"},
                {"id": "a2", "start_frame": 10, "end_frame": 20, "text": "A2"}]
        ep = load_episode(to_bytes(episode_doc(clips=color_clips([90, 90]), captions=caps)))
        lines = export_srt(ep).decode().split("\n\n")
        self.assertEqual([blk.split("\n")[2] for blk in lines], ["A", "A2", "B", "L"])
        self.assertEqual([blk.split("\n")[0] for blk in lines], ["1", "2", "3", "4"])

    def test_no_captions_gives_empty_file(self):
        ep = load_episode(to_bytes(episode_doc(clips=color_clips([90, 90]), captions=[])))
        self.assertEqual(export_srt(ep), b"")

    def test_ntsc_timestamps(self):
        caps = [{"id": "a", "start_frame": 0, "end_frame": 18000, "text": "x"}]
        ep = load_episode(to_bytes(episode_doc(fps=(30000, 1001), total=18000, clips=color_clips([18000]), captions=caps)))
        # 18000 frames * 1001/30000 s = 600.6 s exactly; a 33 ms/frame approximation would say 594.000
        self.assertIn("00:00:00,000 --> 00:10:00,600", export_srt(ep).decode())


if __name__ == "__main__":
    unittest.main()
