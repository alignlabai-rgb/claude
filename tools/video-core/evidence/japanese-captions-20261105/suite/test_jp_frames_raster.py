import unittest
from fractions import Fraction

from common import IPAG_PATH, IPAG_SHA256, UNIFONT_JP_PATH, UNIFONT_JP_SHA256, file_if_hash, need_pil
import analysis as A
import cmapx
import oracle

W, H = 64, 32


def frame_with(box=None):
    y = bytearray([16]) * (W * H)
    if box:
        x0, y0, x1, y1 = box
        for r in range(y0, y1 + 1):
            for c in range(x0, x1 + 1):
                y[r * W + c] = 235
    return bytes(y)


class FrameMeasurements(unittest.TestCase):
    def test_ink_stats(self):
        st = A.ink_stats(frame_with((10, 5, 20, 7)), W, H)
        self.assertEqual((st["ink"], st["bbox"], st["row_bands"]), (33, [10, 5, 20, 7], 1))
        self.assertEqual(A.ink_stats(frame_with(), W, H)["ink"], 0)
        y = bytearray(frame_with((10, 5, 20, 6)))
        for c in range(10, 21):
            y[10 * W + c] = 235
        self.assertEqual(A.ink_stats(bytes(y), W, H)["row_bands"], 2)

    def test_wrong_size_plane_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            A.ink_stats(b"\x10" * 5, W, H)

    def _sequence(self, shift_end=0, shift_start=0, swap_at=None):
        boxes = {"a": (5, 5, 25, 12), "b": (30, 15, 55, 22)}
        windows = {"a": frozenset(range(0, 5)), "b": frozenset(range(5, 9))}        # adjacent
        seq = []
        for i in range(12):
            who = None
            if shift_start <= i < 5 + shift_end:
                who = "a"
            elif 5 + shift_end <= i < 9:
                who = "b"
            if swap_at is not None and i == swap_at:
                who = "b" if who == "a" else "a"
            seq.append(frame_with(boxes[who]) if who else frame_with())
        return seq, windows

    def test_exact_sequence_has_no_violations(self):
        seq, windows = self._sequence()
        self.assertTrue(A.classify_sequence(seq, W, H, windows)["ok"])

    def test_seeded_bleed_early_start_late_end_and_wrong_caption_are_all_detected(self):
        seq, windows = self._sequence(shift_end=1)                    # caption a shown one frame too long (bleeds into b's first frame)
        kinds = {v["kind"] for v in A.classify_sequence(seq, W, H, windows)["violations"]}
        self.assertIn("wrong_caption", kinds)
        seq, windows = self._sequence()
        seq[9] = frame_with((30, 15, 55, 22))                         # b bleeds past its exclusive end
        self.assertIn("blank_expected_but_ink", {v["kind"] for v in A.classify_sequence(seq, W, H, windows)["violations"]})
        seq, windows = self._sequence()
        seq[0] = frame_with()                                         # a starts one frame late
        self.assertIn("ink_expected_but_blank", {v["kind"] for v in A.classify_sequence(seq, W, H, windows)["violations"]})
        seq, windows = self._sequence(swap_at=2)
        self.assertIn("wrong_caption", {v["kind"] for v in A.classify_sequence(seq, W, H, windows)["violations"]})

    def test_oracle_windows_for_the_real_render_corpus_are_disjoint(self):
        import corpus_def as C
        fps = Fraction(*C.RENDER_FPS)
        seen = set()
        for _cid, s, e, _t in C.RENDER_CAPTIONS:
            w = oracle.visible_frames_midpoint(s, e, C.RENDER_TOTAL, fps)
            self.assertFalse(seen & w)
            seen |= w
        self.assertEqual(set(range(26, 31)) & seen, set())           # the declared gap
        self.assertEqual(max(seen), C.RENDER_TOTAL - 1)              # last caption ends AT the clip end


@need_pil
@unittest.skipIf(file_if_hash(IPAG_PATH, IPAG_SHA256) is None, "IPAGothic exact build not present")
class RasterAndDirection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = cmapx.read_font(file_if_hash(IPAG_PATH, IPAG_SHA256))

    def test_glyph_report_flags_space_blank_and_notdef_probe(self):
        rep = A.glyph_raster_report(IPAG_PATH, [0x20, 0x3042, 0x3000, 0x65E5], 32, self.f.gid)
        per = rep["per_codepoint"]
        self.assertTrue(per["U+0020"]["blank"] and per["U+3000"]["blank"])
        self.assertFalse(per["U+3042"]["blank"] or per["U+3042"]["same_as_notdef"])
        self.assertGreater(per["U+65E5"]["ink_pixels"], 50)
        self.assertGreater(rep["notdef_ink_pixels"], 50)

    def test_forward_text_beats_reversed_text_in_a_pillow_drawn_line(self):
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(IPAG_PATH, 20, layout_engine=ImageFont.Layout.BASIC)
        text = "日本語の字幕、検証。"
        img = Image.new("L", (W * 4, 40), 16)
        ImageDraw.Draw(img).text((10, 6), text, fill=235, font=font)
        w, h = img.size
        y = img.tobytes()
        st = A.ink_stats(y, w, h)
        res = A.reversed_vs_forward(y, w, h, IPAG_PATH, 20, text, st["bbox"])
        self.assertTrue(res["forward_wins"], res)
        self.assertGreater(res["forward_iou"], 0.8)
        rev = Image.new("L", (W * 4, 40), 16)
        ImageDraw.Draw(rev).text((10, 6), text[::-1], fill=235, font=font)
        y2 = rev.tobytes()
        res2 = A.reversed_vs_forward(y2, w, h, IPAG_PATH, 20, text, A.ink_stats(y2, w, h)["bbox"])
        self.assertFalse(res2["forward_wins"], res2)           # the detector is not blind: a truly reversed drawing is recognised as reversed


@need_pil
@unittest.skipIf(file_if_hash(UNIFONT_JP_PATH, UNIFONT_JP_SHA256) is None, "Unifont-JP exact build not present")
class CmapCoverageIsNotCorrectDrawing(unittest.TestCase):
    def test_unifont_maps_format_characters_and_draws_visible_boxes_for_them(self):
        f = cmapx.read_font(file_if_hash(UNIFONT_JP_PATH, UNIFONT_JP_SHA256))
        rep = A.glyph_raster_report(UNIFONT_JP_PATH, [0x200B, 0x202E, 0x3042], 32, f.gid)
        per = rep["per_codepoint"]
        self.assertGreater(per["U+200B"]["ink_pixels"], 0)         # cmap says covered; it draws a visible hex box, not "nothing"
        self.assertGreater(per["U+202E"]["ink_pixels"], 0)
        self.assertFalse(per["U+3042"]["blank"])


if __name__ == "__main__":
    unittest.main()
