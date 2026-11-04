import struct
import unittest

from common import IPAG_PATH, IPAG_SHA256, UNIFONT_JP_PATH, UNIFONT_JP_SHA256, file_if_hash
import cmapx
import jpcap_eval as J
from tests.fontbuilder import make_font
from video_core.fontcheck import FontError, missing_codepoints


def sfnt(tables: dict, tag=b"\x00\x01\x00\x00") -> bytes:
    n = len(tables)
    head = tag + struct.pack(">HHHH", n, 16, 0, 0)
    off = 12 + 16 * n
    dirs, blobs = b"", b""
    for name, blob in tables.items():
        dirs += struct.pack(">4sIII", name.encode(), 0, off + len(blobs), len(blob))
        blobs += blob
    return head + dirs + blobs


def cmap_table(subs):
    """subs: [(platform, encoding, bytes)]"""
    hdr = struct.pack(">HH", 0, len(subs))
    off = 4 + 8 * len(subs)
    recs, blobs = b"", b""
    for p, e, blob in subs:
        recs += struct.pack(">HHI", p, e, off + len(blobs))
        blobs += blob
    return hdr + recs + blobs


def fmt6(first, gids):
    return struct.pack(">HHHHH", 6, 10 + 2 * len(gids), 0, first, len(gids)) + b"".join(struct.pack(">H", g) for g in gids)


def fmt13(groups):
    body = b"".join(struct.pack(">III", s, e, g) for s, e, g in groups)
    return struct.pack(">HHIII", 13, 0, 16 + len(body), 0, len(groups)) + body


def fmt14(vs, default_ranges, non_default):
    """one variation selector record"""
    rec_off = 10 + 11
    dflt = struct.pack(">I", len(default_ranges)) + b"".join(struct.pack(">I", s)[1:] + bytes([cnt]) for s, cnt in default_ranges)
    non = struct.pack(">I", len(non_default)) + b"".join(struct.pack(">I", u)[1:] + struct.pack(">H", g) for u, g in non_default)
    d_off, n_off = rec_off, rec_off + len(dflt)
    rec = struct.pack(">I", vs)[1:] + struct.pack(">II", d_off, n_off)
    body = struct.pack(">HII", 14, 0, 1) + rec + dflt + non
    return body[:2] + struct.pack(">I", len(body)) + body[6:]


def metrics_tables(upem=1000, advances=(500, 1000, 0)):
    head = bytearray(54)
    struct.pack_into(">H", head, 18, upem)
    hhea = bytearray(36)
    struct.pack_into(">H", hhea, 34, len(advances))
    hmtx = b"".join(struct.pack(">Hh", a, 0) for a in advances)
    maxp = struct.pack(">IH", 0x00005000, len(advances))
    return {"head": bytes(head), "hhea": bytes(hhea), "hmtx": hmtx, "maxp": maxp}


class IndependentReader(unittest.TestCase):
    def test_agrees_with_production_on_formats_4_and_12_including_gid0_and_union(self):
        for kw in (dict(fmt4=[(0x20, 0x7E), (0x3000, 0x30FF)]), dict(fmt12=[(0x1F600, 0x1F64F, 5), (0x41, 0x41, 0)]),
                   dict(fmt4=[(0x41, 0x5A)], fmt12=[(0x3042, 0x3042, 3)])):
            data = make_font(**kw)
            f = cmapx.read_font(data)
            probe = [0x41, 0x42, 0x3042, 0x3041, 0x30FF, 0x3100, 0x1F600, 0x1F64F, 0x1F650, 0x20, 0x7F]
            prod = set(missing_codepoints(data, ["".join(map(chr, probe))]))
            mine = {cp for cp in probe if not f.gid(cp)}
            self.assertEqual(prod, mine, kw)

    def test_formats_0_6_13_14_which_production_ignores(self):
        t = {"cmap": cmap_table([(3, 1, fmt6(0x3040, [0, 7, 8])), (3, 10, fmt13([(0x20000, 0x2FFFF, 9)])),
                                 (0, 5, fmt14(0xE0100, [(0x845B, 0)], [(0x8FBB, 11)]))]), **metrics_tables()}
        f = cmapx.read_font(sfnt(t))
        self.assertEqual([s.fmt for s in f.subtables], [6, 13, 14])
        self.assertEqual(f.gid(0x3041), 7)
        self.assertEqual(f.gid(0x3040), 0)
        self.assertEqual(f.gid(0x29E3D), 9)                       # format 13: many-to-one
        self.assertEqual(f.variation(0x845B, 0xE0100), "default")
        self.assertEqual(f.variation(0x8FBB, 0xE0100), "non-default")
        self.assertIsNone(f.variation(0x8FBB, 0xE0101))
        # production reads only formats 4/12 and rejects this font outright (no usable subtable besides unsupported formats)
        with self.assertRaises(FontError):
            missing_codepoints(sfnt(t), ["A"])

    def test_variation_sequences_are_invisible_to_the_production_check(self):
        """A font whose ONLY knowledge of U+845B U+E0100 is a format-14 record: production reports U+E0100 missing
        (conservative refusal), the independent reader can see the sequence."""
        base = make_font(fmt4=[(0x845B, 0x845B)], fmt12=[(0x845B, 0x845B, 4)])
        self.assertEqual(missing_codepoints(base, ["葛\U000e0100"]), [0xE0100])

    def test_hmtx_advances_and_tail_repeat(self):
        t = {"cmap": cmap_table([(3, 1, fmt6(0x41, [0, 1, 2, 2]))]), **metrics_tables(1000, (500, 1000, 250))}
        f = cmapx.read_font(sfnt(t))
        self.assertEqual(f.advance_units(f.gid(0x42)), 1000)
        self.assertEqual(f.advance_units(f.gid(0x43)), 250)
        self.assertEqual(f.advance_units(7), 250)                  # glyphs past numberOfHMetrics repeat the last advance

    def test_collection_and_garbage_are_refused_by_both(self):
        good = make_font(fmt4=[(0x41, 0x41)])
        for blob in (b"ttcf" + good[4:], b"", b"junk" * 10, good[:30]):
            with self.assertRaises(cmapx.SfntError):
                cmapx.read_font(blob)
        with self.assertRaises(FontError):
            missing_codepoints(b"ttcf" + good[4:], ["A"])


@unittest.skipIf(file_if_hash(IPAG_PATH, IPAG_SHA256) is None, "IPAGothic (exact build) not present")
class IpaGothicFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = file_if_hash(IPAG_PATH, IPAG_SHA256)
        cls.f = cmapx.read_font(cls.data, IPAG_PATH)

    def test_identity_and_cmap_formats(self):
        self.assertEqual(self.f.names["full"], "IPAGothic")
        self.assertEqual((self.f.units_per_em, self.f.num_glyphs), (2048, 12728))
        self.assertEqual(sorted((s.platform, s.encoding, s.fmt) for s in self.f.subtables), [(0, 3, 4), (3, 1, 4), (3, 10, 12)])
        self.assertFalse(any(s.fmt == 14 for s in self.f.subtables))

    def test_kana_kanji_punctuation_and_halfwidth_are_covered_astral_ext_b_partially(self):
        for ch in "ひらがなカタカナ日本語。、「」！？…ー〜ﾊﾟ゙゚゛゜ﾞﾟ　":
            self.assertTrue(self.f.gid(ord(ch)), f"U+{ord(ch):04X}")
        self.assertTrue(self.f.gid(0x29E3D))
        self.assertFalse(self.f.gid(0x20BB7))
        for cp in (0xE0100, 0xFE0F, 0x200B, 0x2028, 0x202E, 0xFEFF, 0x1F600):
            self.assertFalse(self.f.gid(cp), f"U+{cp:04X}")

    def test_production_and_independent_agree_on_every_accepted_corpus_text(self):
        import corpus_def as C
        for cid, _g, text, exp, _c, _n in C.CASES:
            if exp[0] != "accept":
                continue
            prod = missing_codepoints(self.data, [text])
            self.assertEqual(prod, J.independent_missing(self.f, [text]), cid)

    def test_combining_voiced_marks_have_a_full_em_advance(self):
        """Evidence for the COMBINING_MARK_HAS_ADVANCE diagnostic: hmtx says 1 em, i.e. potentially its own cell (shaping may change drawing)."""
        for cp in (0x3099, 0x309A):
            self.assertEqual(self.f.advance_units(self.f.gid(cp)), 2048)
        self.assertEqual(self.f.advance_units(self.f.gid(0xFF8A)), 1024)      # halfwidth kana = half em
        self.assertEqual(self.f.advance_units(self.f.gid(0x3042)), 2048)


@unittest.skipIf(file_if_hash(UNIFONT_JP_PATH, UNIFONT_JP_SHA256) is None, "Unifont-JP (exact build) not present")
class UnifontFacts(unittest.TestCase):
    def test_format_character_code_points_are_in_the_cmap(self):
        data = file_if_hash(UNIFONT_JP_PATH, UNIFONT_JP_SHA256)
        f = cmapx.read_font(data)
        for cp in (0x200B, 0x2028, 0x202E, 0x202C):
            self.assertTrue(f.gid(cp), f"U+{cp:04X}")
        self.assertEqual(missing_codepoints(data, ["​ ‮"]), [])        # cmap coverage says 'fine' ...


class PresentFontsAreReportedHonestly(unittest.TestCase):
    def test_ttc_is_unusable_for_both_readers_when_present(self):
        fonts = J.load_fonts()
        rec = fonts["wqy_zenhei_ttc"]
        if not rec["present"]:
            self.skipTest("wqy-zenhei.ttc not present")
        self.assertIsNone(rec["font"])
        self.assertIn("collection", J.production_missing(rec["data"], ["あ"])["error"])

    def test_committed_font_report_matches_installed_font_hashes_or_skips(self):
        import json
        with open(J.REPORTS + "/fonts.json", "rb") as fh:
            rep = json.loads(fh.read().decode("ascii"))
        fonts = J.load_fonts()
        checked = 0
        for row in rep["fonts"]:
            rec = fonts[row["id"]]
            if row.get("present") and rec["present"] and row["sha256"] == rec["sha256"]:
                checked += 1
        if not checked:
            self.skipTest("none of the report's fonts are installed with the same hash")
        self.assertGreaterEqual(checked, 1)


if __name__ == "__main__":
    unittest.main()


class RasterReportComparisonPolicy(unittest.TestCase):
    """glyph_raster.json depends on the Pillow/FreeType build; --check must neither fail spuriously nor pass silently."""

    def _committed(self):
        import json
        with open(J.REPORTS + "/glyph_raster.json", "rb") as fh:
            return json.loads(fh.read().decode("ascii"))

    def test_unusable_pillow_is_not_comparable_not_a_pass_by_omission(self):
        r = J.check_raster(J.REPORTS + "/glyph_raster.json", {"status": "SKIPPED", "reason": "Pillow not importable"})
        self.assertEqual((r["ok"], r["status"]), (True, "NOT_COMPARABLE"))

    def test_different_build_compares_the_stable_claims_and_detects_a_changed_claim(self):
        live = self._committed()
        live["pillow"], live["freetype"] = "0.0-other", "0.0-other"
        self.assertEqual(J.check_raster(J.REPORTS + "/glyph_raster.json", live)["status"], "CLAIMS_COMPARED_ONLY")
        self.assertTrue(J.check_raster(J.REPORTS + "/glyph_raster.json", live)["ok"])
        live["fonts"]["unifont_jp"]["covered_but_notdef_identical"] = ["U+3042"]
        self.assertFalse(J.check_raster(J.REPORTS + "/glyph_raster.json", live)["ok"])

    def test_same_build_is_byte_compared(self):
        live = self._committed()
        self.assertEqual(J.check_raster(J.REPORTS + "/glyph_raster.json", live)["status"], "BYTE_COMPARED")
        live["fonts"]["ipag"]["notdef_ink_pixels"] += 1
        self.assertFalse(J.check_raster(J.REPORTS + "/glyph_raster.json", live)["ok"])
