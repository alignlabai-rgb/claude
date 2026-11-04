import hashlib
import json
import os
import shutil
import subprocess
import unittest

from common import IPAG_PATH, IPAG_SHA256, file_if_hash
import cmapx
import corpus_def as C
import diagnostics as D
import jpcap_eval as J
import kinsoku as K
from video_core import prepare


def codes(text, **kw):
    return [d["code"] for d in D.diagnose(text, **kw)]


class KinsokuRules(unittest.TestCase):
    def test_declared_findings_per_corpus_case(self):
        want = {
            "kinsoku_start_stop": ["LINE_START_PROHIBITED"], "kinsoku_start_small_kana": ["LINE_START_PROHIBITED"],
            "kinsoku_start_long_vowel": ["LINE_START_PROHIBITED"], "kinsoku_end_open": ["LINE_END_PROHIBITED"],
            "kinsoku_split_ellipsis": ["SPLIT_INSEPARABLE"],
        }
        for cid, _g, text, exp, _c, _n in C.CASES:
            if exp[0] != "accept":
                continue
            got = [f["code"] for f in K.check_lines(text)]
            self.assertEqual(got, want.get(cid, []), cid)

    def test_only_explicit_breaks_are_inspected_first_line_start_and_last_line_end_are_not(self):
        self.assertEqual(K.check_lines("。一行だけ「"), [])                       # a single line has no break to violate
        self.assertEqual([f["line"] for f in K.check_lines("あ\n。い\nう「\nえ")], [2, 3])
        self.assertEqual(K.check_lines("あ\nい「"), [])                          # trailing opener on the LAST line is not a break

    def test_text_is_never_modified_or_reordered(self):
        for _cid, _g, text, exp, _c, _n in C.CASES:
            if exp[0] == "accept":
                before = text
                K.check_lines(text)
                D.diagnose(text)
                self.assertEqual(text, before)

    def test_horizontal_text_is_not_reversed_by_any_helper(self):
        from video_core import export_srt, load_episode
        text = "右から左ではない"
        srt = export_srt(load_episode(J.single_case_episode(text))).decode("utf-8")
        self.assertIn("\n" + text + "\n", srt)
        self.assertNotIn(text[::-1], srt)

    def test_declared_sets_are_single_codepoints_and_disjoint_start_end(self):
        self.assertFalse(K.LINE_START_PROHIBITED & K.LINE_END_PROHIBITED)
        for ch in K.LINE_START_PROHIBITED | K.LINE_END_PROHIBITED | K.INSEPARABLE_REPEATS:
            self.assertEqual(len(ch), 1)
        self.assertIn("not_claimed", K.RULESET_DECLARATION)
        self.assertTrue(any("UAX #14" in x for x in K.RULESET_DECLARATION["not_claimed"]))

    def test_declared_sets_are_consistent_with_the_committed_local_ucd_classes(self):
        path = os.path.join(J.REPORTS, "uax14_local_probe.json")
        if not os.path.exists(path):
            self.skipTest("no committed probe")
        with open(path, "rb") as fh:
            probe = json.loads(fh.read().decode("ascii"))
        if probe.get("status") != "RAN":
            self.skipTest("probe was skipped when committed")
        self.assertEqual(J.declared_set_consistency(probe), {"status": "RAN", "line_start_members_with_unexpected_class": {}, "line_end_members_with_unexpected_class": {}})
        self.assertEqual(probe["classes"]["U+3002"], "Close_Punctuation")
        self.assertEqual(probe["classes"]["U+300C"], "Open_Punctuation")
        self.assertEqual(probe["classes"]["U+3063"], "Conditional_Japanese_Starter")
        self.assertEqual(probe["classes"]["U+2015"], "Ambiguous")       # the 2-em-dash rule is a DECLARED rule, not a UAX14 inseparable class

    @unittest.skipUnless(shutil.which("perl"), "perl not available")
    def test_live_local_probe_matches_committed_probe(self):
        live = J.uax14_probe()
        if live.get("status") != "RAN":
            self.skipTest("local Unicode::UCD not usable")
        live["declared_set_consistency"] = J.declared_set_consistency(live)
        with open(os.path.join(J.REPORTS, "uax14_local_probe.json"), "rb") as fh:
            self.assertEqual(fh.read(), J.dumps(live))


class StubFont:
    """1 em = 1000 units, every character 1000 wide except combining marks (500)."""
    units_per_em = 1000

    def gid(self, cp):
        return 1

    def advance_units(self, gid):
        return 1000


class Diagnostics(unittest.TestCase):
    def test_expected_codes(self):
        self.assertIn("NOT_NFC", codes("が"))
        self.assertIn("NFKC_WOULD_CHANGE", codes("ﾊﾟ"))
        self.assertNotIn("NOT_NFC", codes("が"))
        self.assertIn("INVISIBLE_ONLY_LINE", codes("字幕\n​"))
        self.assertIn("UNICODE_LINE_BOUNDARY", codes("a b"))
        self.assertIn("BIDI_CONTROL", codes("‮字幕‬"))
        self.assertIn("VARIATION_SELECTOR", codes("葛\U000e0100"))
        self.assertIn("MARKUP_LOOKALIKE", codes("<i>x</i>"))
        self.assertIn("MARKUP_LOOKALIKE", codes("{\\an8}x"))
        self.assertIn("TIMING_LINE_LOOKALIKE", codes("字幕\n00:00:01,000 --> 00:00:02,000"))
        self.assertIn("INDEX_LINE_LOOKALIKE", codes("字幕\n42"))
        self.assertEqual(codes("日本語の字幕"), [])

    def test_overflow_logic_uses_font_metrics_and_the_declared_estimate(self):
        f = StubFont()
        self.assertIn("LINE_OVERFLOWS_CANVAS", codes("あ" * 17, font=f, font_size=20, canvas_width=320))   # 340 > 320
        self.assertIn("LINE_NEARLY_FULL_WIDTH", codes("あ" * 16, font=f, font_size=20, canvas_width=320))  # 320 > 320-16 but not > 320
        self.assertEqual(codes("あ" * 10, font=f, font_size=20, canvas_width=320), [])
        self.assertEqual(codes("あ" * 10 + "\n" + "い" * 17, font=f, font_size=20, canvas_width=320), ["LINE_OVERFLOWS_CANVAS"])

    def test_character_count_alone_is_not_the_criterion(self):
        # same number of characters, different widths: halfwidth kana are half an em in IPAGothic
        data = file_if_hash(IPAG_PATH, IPAG_SHA256)
        if data is None:
            self.skipTest("IPAGothic exact build not present")
        f = cmapx.read_font(data)
        wide, narrow = "あ" * 16, "ｱ" * 16
        self.assertEqual(len(wide), len(narrow))
        self.assertIn("LINE_OVERFLOWS_CANVAS", codes(wide + "あ", font=f, font_size=20, canvas_width=320))
        self.assertNotIn("LINE_OVERFLOWS_CANVAS", codes(narrow + "ｱ", font=f, font_size=20, canvas_width=320))

    def test_combining_mark_advance_diagnostic_on_ipagothic(self):
        data = file_if_hash(IPAG_PATH, IPAG_SHA256)
        if data is None:
            self.skipTest("IPAGothic exact build not present")
        f = cmapx.read_font(data)
        self.assertIn("COMBINING_MARK_HAS_ADVANCE", codes("が", font=f))
        self.assertNotIn("COMBINING_MARK_HAS_ADVANCE", codes("が", font=f))

    def test_width_range_for_combining_mark_is_min_max_not_a_single_number(self):
        data = file_if_hash(IPAG_PATH, IPAG_SHA256)
        if data is None:
            self.skipTest("IPAGothic exact build not present")
        f = cmapx.read_font(data)
        lo, hi = D.line_width_units("が", f)
        self.assertEqual((lo, hi), (2048, 4096))


class HelperDoesNotChangeDefaults(unittest.TestCase):
    def test_baseline_plan_hash_and_production_functions_are_unchanged_with_the_helper_loaded(self):
        import video_core.plan as plan_mod
        before = (plan_mod.prepare, plan_mod.build_canonical)
        import diagnostics  # noqa: F401  (already loaded)
        with open(os.path.join(J.ROOT, "examples", "episode.synthetic.json"), "rb") as fh:
            self.assertEqual(prepare(fh.read()).sha256, J.BASELINE_PLAN_SHA256)
        self.assertEqual(before, (plan_mod.prepare, plan_mod.build_canonical))

    def test_no_production_module_imports_the_helper(self):
        pkg = os.path.join(J.ROOT, "video_core")
        for dirpath, _d, files in os.walk(pkg):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        src = fh.read()
                    for mod in ("diagnostics", "kinsoku", "cmapx", "jpcap"):
                        self.assertNotIn("import " + mod, src, name)


if __name__ == "__main__":
    unittest.main()
