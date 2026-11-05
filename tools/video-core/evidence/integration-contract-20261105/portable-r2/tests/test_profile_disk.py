"""Tests of reviewed profiles that are REALLY written to and read from disk (finding H-1), classification, source-pin handling and what a profile can never rescue. No children, no library run."""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portable_core as pc  # noqa: E402
from portable_core import CaseCtx, OutDir, Profile, ProfileError, classify_check, load_profile_file, parse_profile_bytes  # noqa: E402

H = lambda c: c * 64
INV = {"package_digest_raw": H("a"), "package_digest_eol": H("b"), "all_digest_raw": H("c"), "all_digest_eol": H("d")}
PIN = dict(INV)
WRONG_PIN = {"package_digest_raw": H("0"), "package_digest_eol": H("1"), "all_digest_raw": H("2"), "all_digest_eol": H("3")}
LIVE = ((0, [1, (2, 3)]), {5: ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")])        # what a case observes: tuples, nested arrays, int keys, bool, float, null


class DiskCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="vcc-r2-test-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def write_profile(self, reviewed, pin=PIN, name="p.json", **extra):
        path = os.path.join(self.d, name)
        doc = dict({"profile_version": 1, "source_pin": pin, "reviewed_expectations": reviewed}, **extra)
        with open(path, "x", encoding="utf-8", newline="\n") as fh:                 # a REAL file, written like any profile
            json.dump(doc, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        return path

    def entry(self, observed, ref="reviewer-ref-1"):
        return {"observed": json.loads(json.dumps(observed)), "reviewed_by_ref": ref, "note": "n"}      # exactly what a profile file holds: JSON form of the live value

    def profile(self, path, inv=INV):
        doc, raw = load_profile_file(path)
        return Profile(doc, inv)

    # ---- the H-1 scenario itself
    def test_h1_live_tuples_vs_a_profile_read_from_disk(self):
        path = self.write_profile({"c::x": self.entry(LIVE)})
        self.assertNotEqual(json.loads(json.dumps(LIVE)), LIVE, "the old comparison (==) cannot match: this is finding H-1")
        prof = self.profile(path)
        for layer in ("LIBRARY_ENFORCED", "LIBRARY_PERMISSIVE"):
            out = classify_check(layer, "c::x", "something else", LIVE, prof)
            self.assertEqual(out["outcome"], "REVIEWED_CHANGE", layer)
            self.assertEqual(out["reviewed_by_ref"], "reviewer-ref-1")

    def test_correct_and_wrong_source_pins(self):
        ok = self.profile(self.write_profile({"c::x": self.entry(LIVE)}, name="ok.json"))
        bad = self.profile(self.write_profile({"c::x": self.entry(LIVE)}, pin=WRONG_PIN, name="bad.json"))
        self.assertTrue(ok.applicable)
        self.assertFalse(bad.applicable)
        self.assertEqual(classify_check("LIBRARY_ENFORCED", "c::x", 0, LIVE, ok)["outcome"], "REVIEWED_CHANGE")
        self.assertEqual(classify_check("LIBRARY_ENFORCED", "c::x", 0, LIVE, bad)["outcome"], "FAIL")
        self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::x", 0, LIVE, bad)["outcome"], "REVIEW_REQUIRED")
        self.assertEqual(sorted(bad.reviewed_ignored), ["c::x"])
        # the three labelled pin states that count as applicable, and the one that does not
        for inv, status, applicable in ((INV, "PINNED_EXACT", True), (dict(INV, all_digest_raw=H("9")), "PACKAGE_PINNED_OTHER_FILES_DIFFER", True),
                                        (dict(INV, package_digest_raw=H("9")), "PINNED_MODULO_LINE_ENDINGS", True), (dict(INV, package_digest_raw=H("9"), package_digest_eol=H("8")), "UNPINNED", False)):
            p = Profile({"source_pin": PIN, "reviewed_expectations": {}}, inv)
            self.assertEqual((p.pin["status"], p.applicable), (status, applicable))

    def test_nested_arrays_and_every_counterexample_stays_unresolved(self):
        path = self.write_profile({"c::x": self.entry(LIVE)})
        prof = self.profile(path)
        counter = {
            "bool_for_int": ((0, [1, (2, 3)]), {5: ("x", None), 6: (1, 1.5)}, [(1, "a"), (2, "b")]),
            "int_for_bool_swapped": ((0, [1, (2, 3)]), {5: ("x", None), 6: (True, 1.5)}, [(True, "a"), (2, "b")]),
            "float_for_int": ((0, [1, (2.0, 3)]), {5: ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
            "int_for_float": ((0, [1, (2, 3)]), {5: ("x", None), 6: (True, 2)}, [(1, "a"), (2, "b")]),
            "none_for_string": ((0, [1, (2, 3)]), {5: (None, None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
            "order_swapped": ((0, [1, (2, 3)]), {5: ("x", None), 6: (True, 1.5)}, [(2, "b"), (1, "a")]),
            "inner_order_swapped": ((0, [1, (3, 2)]), {5: ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
            "extra_element": ((0, [1, (2, 3, 4)]), {5: ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
            "missing_key": ((0, [1, (2, 3)]), {5: ("x", None)}, [(1, "a"), (2, "b")]),
            "extra_key": ((0, [1, (2, 3)]), {5: ("x", None), 6: (True, 1.5), 7: 0}, [(1, "a"), (2, "b")]),
            "null_for_missing": ((0, [1, (2, 3)]), {5: ("x", None), 6: (True, 1.5), 7: None}, [(1, "a"), (2, "b")]),
            "string_key_type_changed": ((0, [1, (2, 3)]), {"5": ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),  # equal after JSON key canonicalisation: this one IS equivalent (see below)
            "string_for_int": ((0, [1, ("2", 3)]), {5: ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
            "empty_for_value": ((0, []), {5: ("x", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
            "unicode_changed": ((0, [1, (2, 3)]), {5: ("x́", None), 6: (True, 1.5)}, [(1, "a"), (2, "b")]),
        }
        for name, live in counter.items():
            with self.subTest(name):
                out = classify_check("LIBRARY_ENFORCED", "c::x", "no", live, prof)["outcome"]
                if name == "string_key_type_changed":
                    self.assertEqual(out, "REVIEWED_CHANGE")        # int key 5 and string key "5" are the same JSON object key: documented single canonicalisation
                else:
                    self.assertEqual(out, "FAIL")
                    self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::x", "no", live, prof)["outcome"], "REVIEW_REQUIRED")

    def test_unknown_changed_gaps_remain_review_required_and_reviews_are_per_key(self):
        prof = self.profile(self.write_profile({"c::reviewed": self.entry(1)}))
        self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::reviewed", 0, 1, prof)["outcome"], "REVIEWED_CHANGE")
        self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::unknown_gap", 0, 1, prof)["outcome"], "REVIEW_REQUIRED")
        self.assertEqual(classify_check("LIBRARY_ENFORCED", "c::unknown", 0, 1, prof)["outcome"], "FAIL")
        self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::reviewed", 0, 2, prof)["outcome"], "REVIEW_REQUIRED", "same key, different observation")
        self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::reviewed", 0, 0, prof)["outcome"], "OBSERVED_OPEN_GAP", "unchanged behaviour is not turned into a review")
        self.assertEqual(classify_check("LIBRARY_ENFORCED", "c::reviewed", 1, 1, prof)["outcome"], "PASS", "a reviewed entry never overrides a plain match")

    def test_error_guard_mismatch_and_control_failure_cannot_be_rescued(self):
        key_g, key_c, key_e = "c::guard", "c::control", "c::unsupported"
        prof = self.profile(self.write_profile({key_g: self.entry(2), key_c: self.entry(2), key_e: self.entry(2)}))
        self.assertEqual(classify_check("CALLER_GUARD_DEMO", key_g, 1, 2, prof)["outcome"], "GUARD_MISMATCH")
        self.assertEqual(classify_check("HARNESS_CONTROL", key_c, 1, 2, prof)["outcome"], "CONTROL_FAILED")
        for layer in ("LIBRARY_ENFORCED", "LIBRARY_PERMISSIVE", "CALLER_GUARD_DEMO", "HARNESS_CONTROL"):
            for bad in (b"bytes", float("nan"), {1, 2}):
                with self.subTest(layer=layer, bad=type(bad).__name__):
                    self.assertEqual(classify_check(layer, key_e, bad, bad, prof)["outcome"], "ERROR")
                    self.assertEqual(classify_check(layer, key_e, 2, bad, prof)["outcome"], "ERROR")

    def test_case_result_status_with_a_disk_profile(self):
        prof = self.profile(self.write_profile({"C1::gap": self.entry(LIVE)}))
        ctx = CaseCtx(types.SimpleNamespace(profile=prof), "C1", "t")
        ctx.add("LIBRARY_PERMISSIVE", "gap", ("pinned",), LIVE)
        self.assertEqual(ctx.result()["status"], "PASS_WITH_REVIEWED_CHANGES")
        ctx.add("LIBRARY_PERMISSIVE", "other gap", ("pinned",), LIVE)
        self.assertEqual(ctx.result()["status"], "REVIEW_REQUIRED")
        ctx.add("CALLER_GUARD_DEMO", "g", 1, 2)
        self.assertEqual(ctx.result()["status"], "FAIL")

    def test_unsupported_value_in_a_check_is_an_error_entry_without_the_payload_and_is_never_written_stringified(self):
        ctx = CaseCtx(types.SimpleNamespace(profile=None), "C2", "t")
        ctx.add("LIBRARY_ENFORCED", "bytes", b"abc", b"abc")
        ctx.add("LIBRARY_ENFORCED", "nan", float("nan"), 1.0)
        e1, e2 = ctx.checks
        for e in (e1, e2):
            self.assertEqual(e["outcome"], "ERROR")
            self.assertNotIn("expected", e)
            self.assertNotIn("observed", e)
            self.assertIn("unsupported", e["error"])
        out = OutDir.create_new(os.path.join(self.d, "o"), [])
        out.write_json("ok.json", ctx.result())                                      # the ERROR entries are plain JSON
        with self.assertRaises(TypeError):
            out.write_json("bad.json", {"v": b"abc"})                                 # no default=: nothing is stringified or hex-encoded
        with self.assertRaises(ValueError):
            out.write_json("nan.json", {"v": float("nan")})

    def test_not_a_review_entries_are_flagged(self):
        prof = self.profile(self.write_profile({"c::x": self.entry(1, ref="NOT A REVIEW: automatic promotion"), "c::y": self.entry(1, ref="SELF-TEST ONLY: x"), "c::z": self.entry(1, ref="real-reviewer-ref")}))
        a, b, c = (classify_check("LIBRARY_ENFORCED", k, 0, 1, prof) for k in ("c::x", "c::y", "c::z"))
        self.assertEqual([a.get("review_marker"), b.get("review_marker"), c.get("review_marker")], ["NOT_A_REVIEW", "NOT_A_REVIEW", None])
        self.assertEqual(sorted(prof.applied_not_a_review), ["c::x", "c::y"])

    def test_proposed_expectations_are_inert(self):
        prof = self.profile(self.write_profile({}, proposed_expectations={"c::x": {"observed": 1, "review_status": "UNREVIEWED"}}))
        self.assertEqual(classify_check("LIBRARY_PERMISSIVE", "c::x", 0, 1, prof)["outcome"], "REVIEW_REQUIRED")
        self.assertEqual(classify_check("LIBRARY_ENFORCED", "c::x", 0, 1, prof)["outcome"], "FAIL")

    # ---- the loader refuses unsafe files (whole file, never partially applied)
    def test_loader_rejections(self):
        good = {"profile_version": 1, "source_pin": PIN, "reviewed_expectations": {"c::x": {"observed": 1, "reviewed_by_ref": "r"}}}
        raw = lambda o: json.dumps(o).encode("utf-8")
        bad = {
            "bom": b"\xef\xbb\xbf" + raw(good), "not_utf8": b"\xff\xfe", "not_json": b"{", "array": b"[]", "duplicate_key": b'{"profile_version": 1, "profile_version": 1}',
            "nan": raw(good).replace(b'"observed": 1', b'"observed": NaN'), "inf": raw(good).replace(b'"observed": 1', b'"observed": -Infinity'),
            "version_str": raw(dict(good, profile_version="1")), "version_true": raw(dict(good, profile_version=True)), "pin_missing": raw(dict(good, source_pin={})),
            "pin_short": raw(dict(good, source_pin=dict(PIN, all_digest_raw="abc"))), "no_ref": raw(dict(good, reviewed_expectations={"c::x": {"observed": 1}})),
            "blank_ref": raw(dict(good, reviewed_expectations={"c::x": {"observed": 1, "reviewed_by_ref": " \t"}})), "extra_field": raw(dict(good, reviewed_expectations={"c::x": {"observed": 1, "reviewed_by_ref": "r", "approved": True}})),
            "bad_key": raw(dict(good, reviewed_expectations={"nocolon": {"observed": 1, "reviewed_by_ref": "r"}})), "too_big": b'{"x": "' + b"a" * 1_000_100 + b'"}',
        }
        self.assertEqual(parse_profile_bytes(raw(good))["reviewed_expectations"]["c::x"]["observed"], 1)
        for name, data in bad.items():
            with self.subTest(name), self.assertRaises(ProfileError):
                parse_profile_bytes(data)

    def test_the_two_shipped_profiles_load_and_are_unreviewed(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in sorted(os.listdir(os.path.join(here, "profiles"))):
            doc, _ = load_profile_file(os.path.join(here, "profiles", name))
            if "proposal" in name.lower() or "pin" in name.lower():
                self.assertEqual(doc["reviewed_expectations"], {}, name)


if __name__ == "__main__":
    unittest.main()
