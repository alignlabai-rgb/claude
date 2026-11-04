import os
import unittest

from tests.fontbuilder import make_font
from tests.helpers import TmpCase, color_clips, episode_doc, no_process, to_bytes, tree_snapshot
from video_core import prepare
from video_core.errors import UnsafePath, ValidationFailed
from video_core.fontcheck import missing_codepoints, require_glyph_coverage

JP = [(0x20, 0x7E), (0x3000, 0x30FF)]


class Cmap(unittest.TestCase):
    def test_format4_coverage(self):
        font = make_font(fmt4=JP)
        self.assertEqual(missing_codepoints(font, ["テスト A"]), [])
        self.assertEqual(missing_codepoints(font, ["漢字"]), sorted([0x6F22, 0x5B57]))

    def test_newline_is_not_a_glyph(self):
        self.assertEqual(missing_codepoints(make_font(fmt4=JP), ["A\nB"]), [])

    def test_format12_astral_and_glyph_zero_counts_as_missing(self):
        font = make_font(fmt12=[(0x1F600, 0x1F64F, 5), (0x41, 0x41, 0)])
        self.assertEqual(missing_codepoints(font, ["\U0001F600"]), [])
        self.assertEqual(missing_codepoints(font, ["A"]), [0x41], "glyph id 0 is .notdef, not a glyph")

    def test_union_of_subtables(self):
        font = make_font(fmt4=[(0x41, 0x5A)], fmt12=[(0x3042, 0x3042, 3)])
        self.assertEqual(missing_codepoints(font, ["Aあ"]), [])

    def test_unusable_fonts_are_rejected_not_guessed(self):
        good = make_font(fmt4=JP)
        for blob in (b"", b"\x00" * 11, b"OTTO", b"ttcf" + good[4:], b"WOFF" + good[4:], good[:40], make_font(fmt4=JP, tag=b"junk"), make_font()):
            with self.assertRaises(ValidationFailed) as cm:
                require_glyph_coverage(blob, ["A"])
            self.assertEqual(cm.exception.issues[0].code, "font_unusable", blob[:8])

    def test_missing_glyph_message_lists_code_points_only(self):
        with self.assertRaises(ValidationFailed) as cm:
            require_glyph_coverage(make_font(fmt4=[(0x41, 0x41)]), ["AB漢"])
        issue = cm.exception.issues[0]
        self.assertEqual(issue.code, "font_missing_glyphs")
        self.assertIn("U+0042", issue.message)
        self.assertIn("U+6F22", issue.message)


class BurnInPlanning(TmpCase):
    TEXTS = ["テスト A", "a:b'c\"d\\e,f;[x]=%{n}\n二行目"]

    def setUp(self):
        super().setUp()
        self.assets = self.mkdir("assets")
        self.write("assets/fonts/f.ttf", make_font(fmt4=JP + [(0x5F, 0x5F)]))
        self.write("assets/fonts/bad.woff", b"x")
        caps = [{"id": f"c{i}", "start_frame": 10 + 40 * i, "end_frame": 40 + 40 * i, "text": t} for i, t in enumerate(self.TEXTS)]
        self.ep = to_bytes(episode_doc(clips=color_clips([90, 90]), captions=caps))

    def _plan(self, font="fonts/f.ttf", ep=None):
        with no_process():
            return prepare(ep or self.ep, asset_root=self.assets, burn_in_font=font)

    def test_text_travels_only_in_files_never_in_argv_or_filtergraph(self):
        # texts contain only glyphs present in the synthetic font except ones we added below
        font_all = make_font(fmt4=[(0x20, 0x7E), (0x3000, 0x30FF), (0x4E00, 0x9FFF)])
        self.write("assets/fonts/all.ttf", font_all)
        p = self._plan("fonts/all.ttf")
        argv = [a for a in p.canonical["ffmpeg"]["argv"] if isinstance(a, str)]
        joined = "\n".join(argv)
        for t in self.TEXTS:
            for frag in ("a:b", "%{n}", "テスト", "二行目", "'c\"d"):
                self.assertNotIn(frag, joined)
        self.assertEqual(p.caption_files["cap_0001.txt"], self.TEXTS[0].encode("utf-8"))
        self.assertEqual(p.caption_files["cap_0002.txt"], self.TEXTS[1].encode("utf-8"))
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertEqual(graph.count("expansion=none"), 2, "drawtext %{...} expansion must be off for literal text")
        self.assertIn("enable='between(n,10,39)'", graph)  # end_frame 40 is exclusive
        self.assertIn("enable='between(n,50,79)'", graph)
        self.assertIn("fontfile=font.ttf", graph)
        self.assertEqual(p.canonical["requirements"]["filters"].count("drawtext"), 1)

    def test_missing_glyph_rejected_before_anything_exists(self):
        before = tree_snapshot(self.tmp)
        with self.assertRaises(ValidationFailed) as cm:
            self._plan()  # the synthetic font has no CJK ideograph for 二行目
        self.assertEqual(cm.exception.issues[0].code, "font_missing_glyphs")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_font_must_be_a_validated_local_asset(self):
        for rel, code in (("fonts/bad.woff", "source_extension_unsupported"), ("../x.ttf", "path_traversal"), ("/abs/f.ttf", "path_absolute"),
                          ("http://x/f.ttf", "path_protocol"), ("fonts/none.ttf", "asset_missing")):
            with self.assertRaises(UnsafePath) as cm:
                self._plan(rel)
            self.assertIn(code, {i.code for i in cm.exception.issues}, rel)
        if os.name == "posix":
            os.symlink(os.path.join(self.assets, "fonts/f.ttf"), os.path.join(self.assets, "fonts/link.ttf"))
            with self.assertRaises(UnsafePath):
                self._plan("fonts/link.ttf")

    def test_font_requires_asset_root_and_valid_size(self):
        with self.assertRaises(ValidationFailed):
            prepare(self.ep, burn_in_font="fonts/f.ttf")
        for size in (0, 7, 9999, True, "20"):
            with self.assertRaises(ValidationFailed):
                prepare(to_bytes(episode_doc(captions=[{"id": "a", "start_frame": 1, "end_frame": 2, "text": "A"}])), asset_root=self.assets, burn_in_font="fonts/f.ttf", font_size=size)


if __name__ == "__main__":
    unittest.main()
