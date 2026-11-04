import hashlib
import json
import os
import unicodedata
import unittest

from common import EVID  # noqa: F401  (sets sys.path)
import corpus_def as C
import jpcap_eval as J
import oracle
from video_core import export_srt, load_episode


def rb(path):
    with open(path, "rb") as fh:
        return fh.read()


class CorpusFiles(unittest.TestCase):
    def test_committed_files_equal_a_fresh_deterministic_build(self):
        for name, data in J.build_corpus_files().items():
            self.assertEqual(rb(os.path.join(J.HERE, name)), data, name)

    def test_manifest_lists_the_real_hashes(self):
        lines = rb(os.path.join(J.HERE, "MANIFEST.sha256")).decode("ascii").splitlines()
        self.assertEqual(len(lines), 5)
        for line in lines:
            h, name = line.split("  ")[0], line.split("  ")[1]
            self.assertEqual(hashlib.sha256(rb(os.path.join(J.HERE, name))).hexdigest(), h, name)

    def test_case_ids_unique_and_corpus_json_matches_definition(self):
        ids = [c[0] for c in C.CASES]
        self.assertEqual(len(ids), len(set(ids)))
        doc = json.loads(rb(os.path.join(J.HERE, "corpus.json")).decode("utf-8"))
        self.assertEqual([c["id"] for c in doc["cases"]], ids)
        for c, d in zip(C.CASES, doc["cases"]):
            self.assertEqual(d["text"], c[2], c[0])
            self.assertEqual(d["text_sha256"], hashlib.sha256(c[2].encode("utf-8", "surrogatepass")).hexdigest())

    def test_required_corpus_features_are_present(self):
        text = "".join(c[2] for c in C.CASES)
        cps = {ord(ch) for ch in text}
        self.assertTrue(0x3072 in cps and 0x30AB in cps and 0x65E5 in cps)             # kana, kanji
        self.assertTrue(0x3099 in cps and 0x309A in cps)                                # combining dakuten / handakuten
        self.assertTrue(0xFF8A in cps and 0xFF9E in cps and 0xFF9F in cps)             # halfwidth kana + marks
        self.assertTrue(any(cp >= 0x20000 for cp in cps))                               # astral CJK
        self.assertTrue(0xE0100 in cps and 0xFE0F in cps)                               # variation selectors
        for ch in "「」（）。、！？:%'\"\\":
            self.assertIn(ord(ch), cps, ch)
        self.assertTrue(any("\n" in c[2] for c in C.CASES))                             # two lines
        self.assertTrue(any(c[3][0] == "reject" for c in C.CASES))                      # unsupported controls

    def test_render_corpus_is_distinct_from_the_supplied_example_and_within_bounds(self):
        ex = json.loads(rb(os.path.join(J.ROOT, "examples", "episode.synthetic.json")).decode("utf-8"))
        self.assertNotEqual(rb(os.path.join(J.HERE, "render_case.episode.json")), rb(os.path.join(J.ROOT, "examples", "episode.synthetic.json")))
        self.assertNotEqual((ex["fps"]["n"], ex["fps"]["d"]), C.RENDER_FPS)
        ep = load_episode(rb(os.path.join(J.HERE, "render_case.episode.json")))
        self.assertLessEqual(ep.total_frames, 60)
        self.assertLessEqual(ep.width, 320)
        self.assertLessEqual(ep.height, 180)
        self.assertLessEqual(float(ep.duration), 2.0)
        texts = {c.text for c in ep.captions}
        supplied = {c["text"] for c in ex["captions"]}
        self.assertFalse(texts & supplied)


class ValidatorAsDeclared(unittest.TestCase):
    def test_every_case_outcome_matches_the_declaration(self):
        for cid, _g, text, exp, _cls, _n in C.CASES:
            declared = "accept" if exp[0] == "accept" else ":".join(exp)
            self.assertEqual(J.validator_outcome(text), declared, cid)

    def test_documented_rejections_never_substitute_or_strip(self):
        # a rejected string must raise; it must not be cleaned and accepted
        for cid, _g, text, exp, _cls, _n in C.CASES:
            if exp[0] == "reject":
                self.assertTrue(J.validator_outcome(text).startswith("reject:"), cid)


class SrtBytesAreSourceBytes(unittest.TestCase):
    def setUp(self):
        self.ep = load_episode(rb(os.path.join(J.HERE, "corpus.episode.json")))
        self.srt = export_srt(self.ep)
        self.cues = oracle.parse_srt_strict(self.srt)

    def test_srt_is_strict_utf8_lf_no_bom(self):
        self.assertFalse(self.srt.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", self.srt)
        self.srt.decode("utf-8")
        self.assertEqual(self.srt, rb(os.path.join(J.HERE, "corpus.srt")))

    def test_each_accepted_text_is_byte_identical_in_the_stream_and_in_its_cue(self):
        order = sorted(range(len(self.ep.captions)), key=lambda i: (self.ep.captions[i].start_frame, self.ep.captions[i].end_frame, i))
        by_cue = {self.ep.captions[idx].id: self.cues[n]["text"] for n, idx in enumerate(order)}
        for cid, _g, text, exp, _cls, _n in C.CASES:
            if exp[0] != "accept":
                continue
            self.assertEqual(by_cue[cid].encode("utf-8"), text.encode("utf-8"), cid)
            self.assertIn(text.encode("utf-8"), self.srt, cid)

    def test_no_silent_normalisation_nfd_halfwidth_fullwidth_keep_their_form(self):
        by_id = {c.id: c.text for c in self.ep.captions}
        self.assertFalse(unicodedata.is_normalized("NFC", by_id["dakuten_combining_nfd"]))
        self.assertIn("が".encode("utf-8"), self.srt)
        self.assertIn("ﾊﾟｿｺﾝ".encode("utf-8"), self.srt)
        self.assertIn("ＡＢＣ１２３".encode("utf-8"), self.srt)

    def test_a_normalising_exporter_would_be_caught_by_byte_equality_but_not_by_nfc_equality(self):
        """Instrument sensitivity: if a (hypothetical) exporter NFC/NFKC-folded text, 'equal after normalising' would still say OK;
        the byte comparison used here would say NOT equal. This is why normalisation never counts as source equality."""
        for cid in ("dakuten_combining_nfd", "halfwidth_kana", "fullwidth_ascii"):
            src = next(c[2] for c in C.CASES if c[0] == cid)
            folded = unicodedata.normalize("NFKC", src)
            self.assertNotEqual(folded.encode(), src.encode(), cid)                       # byte check detects it
            self.assertEqual(unicodedata.normalize("NFKC", folded), unicodedata.normalize("NFKC", src), cid)   # normalised comparison would hide it

    def test_independent_serializer_reproduces_the_exporter_bytes(self):
        fps = self.ep.fps
        order = sorted(range(len(self.ep.captions)), key=lambda i: (self.ep.captions[i].start_frame, self.ep.captions[i].end_frame, i))

        def ts(ms):
            return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
        blocks = []
        for n, idx in enumerate(order, 1):
            c = self.ep.captions[idx]
            blocks.append(f"{n}\n{ts(oracle.round_half_up_ms(c.start_frame, fps))} --> {ts(oracle.round_half_up_ms(c.end_frame, fps))}\n{c.text}")
        self.assertEqual(("\n\n".join(blocks) + "\n").encode("utf-8"), self.srt)

    def test_lone_surrogate_json_escape_is_rejected_not_replaced(self):
        self.assertEqual(J.validator_outcome("a\ud800b"), "reject:text_invalid_unicode")

    def test_two_u2028_is_accepted_but_a_splitlines_consumer_sees_an_empty_line(self):
        """Interop caveat, measured: the strict LF reader keeps one cue; a str.splitlines() reader splits the cue at the empty line."""
        text = next(c[2] for c in C.CASES if c[0] == "double_u2028")
        ep = load_episode(J.single_case_episode(text))
        srt = export_srt(ep)
        self.assertEqual(len(oracle.parse_srt_strict(srt)), 1)
        self.assertGreater(len(oracle.parse_srt_splitlines(srt)), 1)


if __name__ == "__main__":
    unittest.main()
