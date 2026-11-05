"""Unit / property tests for the caller-admission reference candidate (UNREVIEWED v0). Stdlib only; synthetic public values only; no file, process, clock or network is touched by the
function under test. Run:  python3 -m unittest discover -s evidence/caller-reference-20261105 -p "test_*.py"   (from tools/video-core)

Every test goes through `ev()`, which also asserts the cross-cutting properties: the inputs are not mutated, the result is JSON-serializable and deterministic (two calls are equal),
reasons are in the documented sorted order, and no INTERNAL_EVALUATION_ERROR safety-net result is ever the reason a test passes.

`expect()` makes each test prove it reached its INTENDED comparison: the named check must be FAIL with exactly the named reason codes, and every other check must be PASS (or listed as
SKIPPED because a prerequisite was deliberately removed) - so a malformed fixture that fails early at a different check cannot satisfy the test by accident.
"""

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import caller_admission as ca  # noqa: E402
from caller_fixtures import H, IDENT, PINS, STEM_BYTES, profile, world  # noqa: E402,F401

ORDER = ["PROFILE", "EXPECTED", "OBSERVED", "IDENTITY", "AUDIO_PLAN", "SELECTION", "AUDIO_RECEIPT", "VIDEO_RECEIPT", "MANIFEST_VIEW", "PINS", "EFFECTIVE_SCOPE"]
PROF = ca.PROFILE_V0_UNREVIEWED


def ev(expected, observed, prof=PROF):
    before = copy.deepcopy((expected, observed, prof))
    a = ca.evaluate(expected, observed, prof)
    b = ca.evaluate(expected, observed, prof)
    assert (expected, observed, prof) == before, "the inputs were mutated"
    assert a == b, "not deterministic"
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    keys = [(ca._RANK[r["severity"]], r["code"], r["path"]) for r in a["reasons"]]
    assert keys == sorted(keys), "reasons are not in the documented order"
    assert "INTERNAL_EVALUATION_ERROR" not in {r["code"] for r in a["reasons"]}, a["reasons"]
    assert [c["id"] for c in a["checks"]] == ORDER, [c["id"] for c in a["checks"]]
    a["__expected_unchanged"] = True
    del a["__expected_unchanged"]
    return a


def codes(res):
    return {r["code"] for r in res["reasons"]}


def outcome(res, check_id):
    return next(c["outcome"] for c in res["checks"] if c["id"] == check_id)


class Base(unittest.TestCase):
    def expect(self, res, status, reason_codes, fail, skipped=()):
        """`fail`: the check ids that must FAIL (the intended comparison). Every other check must PASS, except the listed SKIPPED ones."""
        self.assertEqual(res["status"], status, res["reasons"])
        self.assertEqual(codes(res), set(reason_codes), res["reasons"])
        for cid in ORDER:
            want = "FAIL" if cid in fail else "SKIPPED" if cid in skipped else "PASS"
            self.assertEqual(outcome(res, cid), want, f"check {cid}: {res['checks']}")

    def ready(self, res):
        self.expect(res, "READY", [], [])


class PositiveControl(Base):
    def test_conforming_producer_shaped_world_is_ready_and_says_exactly_what_that_means(self):
        res = ev(*world())
        self.ready(res)
        self.assertEqual(res["scope_of_status"], ca.READY_SCOPE)
        self.assertIn("synthetic snapshot", res["scope_of_status"])
        self.assertEqual(res["evidence"]["selected_scope"], {"use": ["e1"], "qualified_silence": ["e2"], "omit_no_contribution": [], "selected_unresolved": []})
        self.assertEqual(sorted(res["evidence"]["pins_compared"]), sorted(ca.PIN_KINDS))
        self.assertGreaterEqual(len(res["evidence"]["pins_compared"]["plan_sha256"]), 2, "the plan pin was checked against more than one observed source")
        self.assertGreaterEqual(len(res["evidence"]["pins_compared"]["stem_sha256"]), 2)
        self.assertGreaterEqual(len(res["evidence"]["pins_compared"]["video_sha256"]), 2)
        joined = " ".join(res["not_claimed"])
        for word in ("production execution permission", "creative", "authenticity", "no file was read"):
            self.assertIn(word, joined)
        self.assertEqual(res["evidence"]["profile"]["review_status"], "UNREVIEWED")

    def test_there_is_no_action_or_execute_surface(self):
        import inspect
        self.assertEqual(list(inspect.signature(ca.evaluate).parameters), ["expected", "observed", "profile"])
        res = ev(*world())
        for k in res:
            self.assertNotIn("execute", k.lower())
            self.assertNotIn("approv", k.lower())

    def test_determinism_and_no_aliasing_with_the_inputs(self):
        e, o = world()
        res = ca.evaluate(e, o, PROF)
        snap = copy.deepcopy((e, o))
        res["evidence"]["selected_scope"]["use"].append("tampered")
        res["not_claimed"].append("x")
        self.assertEqual((e, o), snap, "mutating the result must not reach the inputs")
        self.assertEqual(ca.evaluate(e, o, PROF)["evidence"]["selected_scope"]["use"], ["e1"])


class Silence(Base):
    def test_warning_free_qualified_explicit_silence_is_ready(self):
        e, o = world()
        self.assertEqual(o["stages"]["audio_plan"]["response"]["plan"]["warnings"], [])
        res = ev(e, o)
        self.ready(res)
        self.assertEqual(res["evidence"]["selected_scope"]["qualified_silence"], ["e2"])

    def test_warned_silence_only_scope_is_hold_there_is_no_silence_only_exception(self):
        rows = [{"id": "e2", "state": "INTENTIONAL_SILENCE", "selected": True, "decision": "dec-silence-e2"}]
        e, o = world(rows, warnings=[{"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "no selected USE event"}])
        res = ev(e, o)
        self.expect(res, "HOLD", ["WARNING_UNTREATED"], ["AUDIO_PLAN", "AUDIO_RECEIPT"])
        self.assertEqual(res["evidence"]["warnings_preserved"], ["NO_AUDIO_EVENTS_SELECTED"])
        self.assertEqual(res["evidence"]["selected_scope"]["qualified_silence"], ["e2"], "the silence itself was qualified; only the warning blocks")

    def test_silence_only_without_the_producers_warning_is_a_contradiction_not_a_pass(self):
        rows = [{"id": "e2", "state": "INTENTIONAL_SILENCE", "selected": True, "decision": "dec-silence-e2"}]
        res = ev(*world(rows))
        self.expect(res, "REJECTED", ["INCONSISTENT_PLAN"], ["AUDIO_PLAN"])

    def test_silence_without_a_decision_ref_is_unqualified(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "e2", "state": "INTENTIONAL_SILENCE", "selected": True}]
        res = ev(*world(rows))
        self.expect(res, "HOLD", ["SILENCE_UNQUALIFIED"], ["SELECTION"])
        self.assertEqual(res["evidence"]["selected_scope"]["qualified_silence"], [])

    def test_silence_decision_ref_must_match_the_owner(self):
        e, o = world()
        o["stages"]["audio_plan"]["response"]["plan"]["events"][1]["decision_ref"] = "dec-OTHER"
        self.expect(ev(e, o), "REJECTED", ["DECISION_REF_MISMATCH"], ["SELECTION"])

    def test_zero_fill_applied_is_preserved_and_hold(self):
        w = [{"code": "ZERO_FILL_APPLIED", "detail": "1 gap zero-filled"}]
        e, o = world(warnings=w, gap_count=1, gap_policy="ALLOW_ZERO_FILL")
        res = ev(e, o)
        self.expect(res, "HOLD", ["WARNING_UNTREATED", "POLICY_GAP_NOT_REQUIRED", "INCOMPLETE_COVERAGE"], ["AUDIO_PLAN", "AUDIO_RECEIPT"])
        self.assertIn("ZERO_FILL_APPLIED", res["evidence"]["warnings_preserved"])

    def test_zero_fill_gaps_without_the_producers_warning_is_a_contradiction(self):
        e, o = world(gap_count=1, gap_policy="ALLOW_ZERO_FILL")
        res = ev(e, o)
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("INCONSISTENT_PLAN", codes(res))

    def test_a_profile_may_explicitly_treat_a_warning_code_but_never_by_default(self):
        w = [{"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}]
        rows = [{"id": "e2", "state": "INTENTIONAL_SILENCE", "selected": True, "decision": "dec-silence-e2"}]
        e, o = world(rows, warnings=w)
        self.assertEqual(ev(e, o)["status"], "HOLD")
        self.ready(ev(e, o, profile(treated_warning_codes=["NO_AUDIO_EVENTS_SELECTED"])))


class SelectedScope(Base):
    def test_unselected_unknown_does_not_block_a_qualified_selected_use_scope_and_is_preserved(self):
        rows = list(world.__defaults__[0]) + [{"id": "e3", "state": "UNKNOWN", "selected": False}]
        res = ev(*world(rows))
        self.ready(res)
        self.assertEqual(res["evidence"]["unselected_unresolved"], [{"row_id": "e3", "state": "UNKNOWN"}])
        self.assertEqual(res["evidence"]["selected_scope"]["selected_unresolved"], [])
        self.assertNotIn("e3", res["evidence"]["selected_scope"]["use"] + res["evidence"]["selected_scope"]["qualified_silence"], "never media or silence")

    def test_unselected_unknown_known_only_to_the_producer_is_preserved_too(self):
        rows = list(world.__defaults__[0]) + [{"id": "e3", "state": "UNKNOWN", "selected": False}]
        e, o = world(rows)
        e["rows"] = [r for r in e["rows"] if r["row_id"] != "e3"]  # the owner snapshot does not list unselected rows
        res = ev(e, o)
        self.ready(res)
        self.assertEqual(res["evidence"]["unselected_unresolved"], [{"row_id": "e3", "state": "UNKNOWN"}])

    def test_selected_omit_does_not_block_a_qualified_use_and_contributes_nothing(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "e4", "state": "OMIT", "selected": True, "decision": "dec-omit-e4"}]
        res = ev(*world(rows))
        self.ready(res)
        self.assertEqual(res["evidence"]["selected_scope"], {"use": ["e1"], "qualified_silence": [], "omit_no_contribution": ["e4"], "selected_unresolved": []})

    def test_selected_unknown_is_hold(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "e3", "state": "UNKNOWN", "selected": True}]
        res = ev(*world(rows, hold=[{"code": "SELECTED_UNRESOLVED", "event_id": "e3", "state": "UNKNOWN", "detail": "x"}], gap_count=0))
        self.expect(res, "HOLD", ["SELECTED_UNRESOLVED", "STATUS_NOT_ACCEPTED", "EXIT_CODE_NOT_OK", "PLAN_HOLD_PRESERVED"], ["AUDIO_PLAN", "SELECTION", "AUDIO_RECEIPT"])
        self.assertEqual(res["evidence"]["selected_scope"]["selected_unresolved"], ["e3"])

    def test_selected_missing_and_proxy_are_hold_too(self):
        for st in ("MISSING", "PROXY"):
            rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "e3", "state": st, "selected": True}]
            res = ev(*world(rows, hold=[{"code": "SELECTED_UNRESOLVED"}]))
            self.assertEqual(res["status"], "HOLD", st)
            self.assertIn("SELECTED_UNRESOLVED", codes(res), st)

    def test_plan_claiming_ready_while_a_selected_row_is_unresolved_is_a_contradiction(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "e3", "state": "UNKNOWN", "selected": True}]
        res = ev(*world(rows, plan_status="READY", plan_exit=0))
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("INCONSISTENT_PLAN", codes(res))

    def test_empty_effective_scope_is_not_ready_even_if_the_profile_treats_the_warning(self):
        rows = [{"id": "e4", "state": "OMIT", "selected": True, "decision": "dec-omit-e4"}]
        w = [{"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}]
        e, o = world(rows, warnings=w, gap_count=1)
        res = ev(e, o, profile(treated_warning_codes=["NO_AUDIO_EVENTS_SELECTED"]))
        self.expect(res, "HOLD", ["EMPTY_EFFECTIVE_SCOPE", "INCOMPLETE_COVERAGE"], ["AUDIO_PLAN", "EFFECTIVE_SCOPE"])

    def test_row_with_a_disposition_that_contradicts_its_state_is_rejected(self):
        e, o = world()
        o["stages"]["audio_plan"]["response"]["plan"]["events"][0]["disposition"] = "SILENCE"
        self.expect(ev(e, o), "REJECTED", ["ROW_DISPOSITION_MISMATCH"], ["SELECTION"])

    def test_whole_row_structure_is_validated_even_for_unselected_rows(self):
        rows = list(world.__defaults__[0]) + [{"id": "e3", "state": "UNKNOWN", "selected": False}]
        e, o = world(rows)
        o["stages"]["audio_plan"]["response"]["plan"]["events"][2]["state"] = "MAYBE"
        res = ev(e, o)
        self.expect(res, "REJECTED", ["SHAPE_ROW"], ["AUDIO_PLAN"], skipped=["SELECTION", "EFFECTIVE_SCOPE"])

    def test_selection_set_differences_are_rejected(self):
        e, o = world()
        e["rows"].append({"row_id": "e9", "selected": True, "state": "USE", "binding_ref": "bind-e9"})  # the owner selected one more row than the plan carries
        self.expect(ev(e, o), "REJECTED", ["SELECTION_SET_MISMATCH"], ["SELECTION"])
        e, o = world()
        e["rows"][1]["selected"] = False  # the plan selected a row the owner did not
        self.expect(ev(e, o), "REJECTED", ["SELECTION_SET_MISMATCH"], ["SELECTION"])

    def test_row_state_and_binding_differences_are_rejected(self):
        e, o = world()
        e["rows"][0]["state"] = "OMIT"
        self.expect(ev(e, o), "REJECTED", ["ROW_STATE_MISMATCH"], ["SELECTION"])
        e, o = world()
        e["rows"][0]["binding_ref"] = "bind-OTHER"
        self.expect(ev(e, o), "REJECTED", ["BINDING_MISMATCH"], ["SELECTION"])

    def test_binding_unproven_and_unresolved_are_hold(self):
        e, o = world()
        del o["stages"]["audio_plan"]["response"]["plan"]["events"][0]["provenance_ref"]
        self.expect(ev(e, o), "HOLD", ["BINDING_UNPROVEN"], ["SELECTION"])
        e, o = world()
        del e["rows"][0]["binding_ref"]
        self.expect(ev(e, o), "HOLD", ["BINDING_UNRESOLVED"], ["SELECTION"])
        self.ready(ev(e, o, profile(require_binding_for_use=False)))


class CrossJobAndPins(Base):
    def test_a_single_identity_field_in_any_stage_is_rejected_although_every_hash_matches(self):
        stages = ("audio_plan", "audio_receipt", "video_receipt", "avmux_manifest_view")
        for stage in stages:
            for field in ca.IDENTITY_FIELDS:
                with self.subTest(stage=stage, field=field):
                    e, o = world()
                    o["stages"][stage]["identity"][field] = "other-" + field
                    res = ev(e, o)
                    self.expect(res, "REJECTED", ["IDENTITY_MISMATCH"], ["IDENTITY"])
                    self.assertEqual([r["path"] for r in res["reasons"]], [f"observed.stages.{stage}.identity.{field}"])
                    self.assertEqual(outcome(res, "PINS"), "PASS", "the pins matched: the only fault is the job/work/shot/attempt/selection identity")

    def test_the_owner_expecting_a_different_attempt_is_the_same_rejection(self):
        e, o = world()
        e["identity"]["attempt_id"] = "attempt-0002"
        res = ev(e, o)
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"IDENTITY_MISMATCH"}))
        self.assertEqual(len(res["reasons"]), 4, "all four stage packets are from the old attempt")

    def test_the_manifest_selection_ref_must_equal_the_owner_snapshot(self):
        e, o = world()
        o["stages"]["avmux_manifest_view"]["selection_ref"] = "selsnap-B"
        self.expect(ev(e, o), "REJECTED", ["SELECTION_REF_MISMATCH"], ["MANIFEST_VIEW"])

    def test_every_observed_hash_source_is_compared_to_the_owner_pin(self):
        paths = [
            ("audio_plan", ("response", "plan_sha256"), "plan_sha256"),
            ("audio_receipt", ("response", "plan_sha256"), "plan_sha256"),
            ("audio_receipt", ("response", "input", "manifest_sha256"), "manifest_sha256"),
            ("audio_receipt", ("receipt_sha256",), "receipt_sha256"),
            ("audio_receipt", ("response", "outputs", 0, "sha256"), "stem_sha256"),
            ("avmux_manifest_view", ("audio_sha256",), "stem_sha256"),
            ("video_receipt", ("response", "outputs", 0, "sha256"), "video_sha256"),
            ("avmux_manifest_view", ("video_sha256",), "video_sha256"),
        ]
        for stage, path, kind in paths:
            with self.subTest(stage=stage, path=path):
                e, o = world()
                node = o["stages"][stage]
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = H("a different but well-formed value")
                res = ev(e, o)
                self.expect(res, "REJECTED", ["PIN_MISMATCH"], ["PINS"])
                self.assertEqual(len(res["reasons"]), 1)

    def test_the_owner_pin_changing_rejects_every_observed_source_of_that_kind(self):
        e, o = world()
        e["pins"]["stem_sha256"] = H("owner expects another stem")
        res = ev(e, o)
        self.expect(res, "REJECTED", ["PIN_MISMATCH"], ["PINS"])
        self.assertEqual(len(res["reasons"]), 2)

    def test_matching_hashes_alone_prove_nothing_about_the_other_comparisons(self):
        e, o = world()
        o["stages"]["audio_plan"]["response"]["plan"]["scope"] = ["e1"]
        o["stages"]["audio_plan"]["response"]["plan"]["events"][1]["selected"] = False
        res = ev(e, o)
        self.assertEqual(outcome(res, "PINS"), "PASS")
        self.assertNotEqual(res["status"], "READY")

    def test_malformed_pins_are_rejected_missing_pins_are_hold(self):
        for bad in ("A" * 64, "a" * 63, 7, None, True):
            e, o = world()
            e["pins"]["plan_sha256"] = bad
            res = ev(e, o)
            self.assertEqual((res["status"], codes(res)), ("REJECTED", {"SHAPE_PINS"}), repr(bad))
            self.assertEqual(outcome(res, "EXPECTED"), "FAIL")
        for kind in ca.PIN_KINDS:
            e, o = world()
            del e["pins"][kind]
            res = ev(e, o)
            self.assertEqual((res["status"], codes(res)), ("HOLD", {"EXPECTED_PIN_MISSING"}), kind)

    def test_pins_not_declared_as_coming_from_an_independent_owner_record_are_hold(self):
        for src in ("observed_receipt", "same_receipt", ""):
            e, o = world()
            e["pins_source"] = src
            self.assertEqual((ev(e, o)["status"], codes(ev(e, o))), ("HOLD", {"PIN_SOURCE_NOT_INDEPENDENT"}), src)
        e, o = world()
        del e["pins_source"]
        self.assertEqual(codes(ev(e, o)), {"PIN_SOURCE_NOT_INDEPENDENT"})

    def test_a_counts_and_hash_mismatch_between_stages_is_reached(self):
        e, o = world()
        o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["checks"][0]["expected"] = 7
        self.expect(ev(e, o), "REJECTED", ["COUNT_MISMATCH"], ["VIDEO_RECEIPT"])


class MissingAndIncompleteReceipts(Base):
    def test_a_required_receipt_that_is_absent_is_hold_and_nothing_downstream_pretends(self):
        e, o = world()
        del o["stages"]["audio_receipt"]
        res = ev(e, o)
        self.expect(res, "HOLD", ["REQUIRED_RECEIPT_ABSENT", "OBSERVED_PIN_MISSING"], ["OBSERVED", "PINS"], skipped=["AUDIO_RECEIPT"])
        e, o = world()
        del o["stages"]["video_receipt"]
        res = ev(e, o)
        self.assertEqual(res["status"], "HOLD")
        self.assertIn("REQUIRED_RECEIPT_ABSENT", codes(res))
        self.assertEqual(outcome(res, "VIDEO_RECEIPT"), "SKIPPED")

    def test_a_required_non_receipt_stage_absent_is_hold(self):
        e, o = world()
        del o["stages"]["avmux_manifest_view"]
        res = ev(e, o)
        self.assertEqual(res["status"], "HOLD")
        self.assertIn("REQUIRED_STAGE_ABSENT", codes(res))

    def test_a_stage_present_but_null_is_a_shape_rejection_not_absence(self):
        e, o = world()
        o["stages"]["audio_receipt"] = None
        res = ev(e, o)
        self.expect(res, "REJECTED", ["SHAPE_STAGE", "OBSERVED_PIN_MISSING"], ["IDENTITY", "PINS"], skipped=["AUDIO_RECEIPT"])

    def test_unknown_stage_names_are_hold_and_do_not_replace_required_ones(self):
        e, o = world()
        o["stages"]["mystery_stage"] = {"identity": dict(IDENT)}
        self.expect(ev(e, o), "HOLD", ["STAGE_UNKNOWN"], ["OBSERVED"])

    def test_four_incomplete_receipt_patterns_never_become_ready(self):
        patterns = {}
        # 1. in progress: written/stage not final, no outputs, no readback
        e, o = world()
        r = o["stages"]["audio_receipt"]["response"]
        r.update(status="IN_PROGRESS", written=True, stage="mix", outputs=[], mix=None, readback=None)
        patterns["in_progress"] = (e, o)
        # 2. interrupted with only a partial stem
        e, o = world()
        r = o["stages"]["audio_receipt"]["response"]
        r.update(status="INTERRUPTED", stage="mix")
        r["outputs"] = [{"name": "stem.wav.partial", "role": "stem_partial", "bytes": 100, "sha256": H("p"), "partial": True}]
        patterns["interrupted_partial"] = (e, o)
        # 3. UNKNOWN with an internal error and changed inputs
        e, o = world()
        r = o["stages"]["audio_receipt"]["response"]
        r.update(status="UNKNOWN", internal_error={"type": "RuntimeError"})
        r["input"]["inputs_unchanged"] = False
        patterns["unknown_changed_inputs"] = (e, o)
        # 4. the bare 3-field receipt (status, stem sha, sample_count) of GAP-04
        e, o = world()
        o["stages"]["audio_receipt"]["response"] = {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": PINS["stem_sha256"], "partial": False}],
                                                    "readback": {"wav": {"sample_count": 9600}}}
        patterns["bare_three_fields"] = (e, o)
        expected_codes = {"in_progress": {"STATUS_NOT_ACCEPTED", "RECEIPT_INCOMPLETE", "OUTPUT_MISSING"}, "interrupted_partial": {"STATUS_NOT_ACCEPTED", "RECEIPT_INCOMPLETE", "OUTPUT_PARTIAL"},
                          "unknown_changed_inputs": {"STATUS_NOT_ACCEPTED", "INPUT_CHANGED"}, "bare_three_fields": {"SCHEMA_UNKNOWN", "SHAPE_FLAG"}}
        for name, (e, o) in patterns.items():
            with self.subTest(pattern=name):
                res = ev(e, o)
                self.assertNotEqual(res["status"], "READY")
                self.assertEqual(outcome(res, "AUDIO_RECEIPT"), "FAIL", "the receipt comparison itself was reached: " + str(res["checks"]))
                self.assertTrue(expected_codes[name] <= codes(res), (name, codes(res)))
                self.assertEqual(outcome(res, "IDENTITY"), "PASS")
                self.assertEqual(outcome(res, "EXPECTED"), "PASS")

    def test_receipt_flags_partial_and_all_ok(self):
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["readback"]["all_ok"] = False
        self.expect(ev(e, o), "HOLD", ["READBACK_NOT_OK"], ["AUDIO_RECEIPT"])
        for bad in (1, 0, "true", None, "False"):
            e, o = world()
            o["stages"]["audio_receipt"]["response"]["readback"]["all_ok"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_FLAG"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["outputs"][0]["partial"] = True
        self.expect(ev(e, o), "HOLD", ["OUTPUT_PARTIAL"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["written"] = False
        self.expect(ev(e, o), "HOLD", ["RECEIPT_NOT_WRITTEN"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["quality_evaluated"] = True
        self.expect(ev(e, o), "REJECTED", ["QUALITY_CLAIM"], ["AUDIO_RECEIPT"])

    def test_receipt_warnings_and_unresolved_report_are_preserved(self):
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["warnings"] = [{"code": "ZERO_FILL_APPLIED", "detail": "x"}]
        self.expect(ev(e, o), "HOLD", ["WARNING_UNTREATED"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["unresolved_report"] = [{"event_id": "e1", "source_id": "src-e1", "state": "UNKNOWN", "selected": True}]
        res = ev(e, o)
        self.assertEqual(res["status"], "REJECTED")  # differs from the plan's list ...
        self.assertIn("RECEIPT_PLAN_MISMATCH", codes(res))
        self.assertIn("SELECTED_UNRESOLVED", codes(res))  # ... and a selected unresolved row is also reported
        self.assertEqual(res["evidence"]["receipt_unresolved_preserved"], ["e1"])

    def test_video_receipt_flags(self):
        e, o = world()
        o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["all_ok"] = False
        self.expect(ev(e, o), "HOLD", ["READBACK_NOT_OK"], ["VIDEO_RECEIPT"])
        e, o = world()
        o["stages"]["video_receipt"]["response"]["outputs"][0]["partial"] = True
        self.expect(ev(e, o), "HOLD", ["OUTPUT_PARTIAL"], ["VIDEO_RECEIPT"])
        e, o = world()
        o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["checks"] = []
        self.expect(ev(e, o), "HOLD", ["FRAME_COUNT_UNPROVEN"], ["VIDEO_RECEIPT"])


class ConsistencyOfTheReportItself(Base):
    def test_plan_scope_list_must_equal_the_rows_flagged_selected(self):
        e, o = world()
        o["stages"]["audio_plan"]["response"]["plan"]["scope"] = ["e1"]  # e2 is still flagged selected on its row, and the owner selected it too
        self.expect(ev(e, o), "REJECTED", ["INCONSISTENT_PLAN"], ["AUDIO_PLAN"])
        e, o = world()
        o["stages"]["audio_plan"]["response"]["plan"]["scope"] = ["e1", "e2", "e2"]
        self.assertEqual(ev(e, o)["status"], "REJECTED")

    def test_receipt_must_link_back_to_its_plan_and_manifest(self):
        e, o = world()
        del o["stages"]["audio_receipt"]["response"]["plan_sha256"]
        self.expect(ev(e, o), "HOLD", ["RECEIPT_LINK_MISSING"], ["AUDIO_RECEIPT"])
        e, o = world()
        del o["stages"]["audio_receipt"]["response"]["input"]["manifest_sha256"]
        self.expect(ev(e, o), "HOLD", ["RECEIPT_LINK_MISSING", "OBSERVED_PIN_MISSING"], ["AUDIO_RECEIPT", "PINS"])
        for bad in (None, 7, "A" * 64, "a" * 10):
            e, o = world()
            o["stages"]["audio_receipt"]["response"]["plan_sha256"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_PIN"], ["AUDIO_RECEIPT"])


class StatusVersusExitCode(Base):
    NON_ACCEPTED_AUDIO = ("IN_PROGRESS", "UNKNOWN", "INTERRUPTED", "TIMEOUT", "CANCELLED", "HOLD", "MIXED_WITH_CLAMPING", "READBACK_MISMATCH", "CLIPPING_REJECTED")
    NON_ACCEPTED_VIDEO = ("IN_PROGRESS", "UNKNOWN", "INTERRUPTED", "TIMEOUT", "SIGNALED", "FLOOD_KILLED", "READBACK_MISMATCH", "INSPECTION_FAILED", "PROCESS_FAILED", "SKIPPED_TOOL_UNAVAILABLE")

    def test_parseable_non_accepted_receipt_statuses_with_exit_zero_are_hold(self):
        for st in self.NON_ACCEPTED_AUDIO:
            with self.subTest(stage="audio_receipt", status=st):
                e, o = world()
                o["stages"]["audio_receipt"]["response"]["status"] = st
                self.expect(ev(e, o), "HOLD", ["STATUS_NOT_ACCEPTED"], ["AUDIO_RECEIPT"])
        for st in self.NON_ACCEPTED_VIDEO:
            with self.subTest(stage="video_receipt", status=st):
                e, o = world()
                o["stages"]["video_receipt"]["response"]["status"] = st
                self.expect(ev(e, o), "HOLD", ["STATUS_NOT_ACCEPTED"], ["VIDEO_RECEIPT"])

    def test_unknown_status_strings_are_hold_not_ready(self):
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["status"] = "MIXED_VERIFIED_PLUS"
        self.expect(ev(e, o), "HOLD", ["STATUS_UNKNOWN"], ["AUDIO_RECEIPT"])

    def test_plan_exit_zero_with_hold_status_is_hold_readiness_is_not_exit_zero(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}]
        e, o = world(rows, plan_status="HOLD", plan_exit=0, hold=[{"code": "INCOMPLETE_SELECTED_INPUT"}], gap_count=1)
        res = ev(e, o)
        self.assertEqual(res["status"], "HOLD")
        self.assertTrue({"STATUS_NOT_ACCEPTED", "PLAN_HOLD_PRESERVED", "INCOMPLETE_COVERAGE"} <= codes(res))
        self.assertNotIn("EXIT_CODE_NOT_OK", codes(res), "exit 0 was accepted as an exit code; the status is what blocks")

    def test_ready_status_with_a_non_accepted_exit_code_is_a_contradiction(self):
        for stage in ("audio_plan", "audio_receipt", "video_receipt"):
            for code in (1, 2, 5, 130, -9):
                e, o = world()
                o["stages"][stage]["exit_code"] = code
                res = ev(e, o)
                self.assertEqual((res["status"], codes(res)), ("REJECTED", {"CODE_STATUS_CONTRADICTION"}), (stage, code))

    def test_non_ready_status_with_a_non_accepted_exit_code_is_hold_with_both_reasons(self):
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["status"] = "TIMEOUT"
        o["stages"]["audio_receipt"]["exit_code"] = 4
        self.expect(ev(e, o), "HOLD", ["STATUS_NOT_ACCEPTED", "EXIT_CODE_NOT_OK"], ["AUDIO_RECEIPT"])

    def test_exit_code_must_be_an_integer_not_bool_float_or_string(self):
        for bad in (True, False, 0.0, "0", None):
            e, o = world()
            o["stages"]["audio_receipt"]["exit_code"] = bad
            res = ev(e, o)
            self.expect(res, "REJECTED", ["SHAPE_EXIT_CODE", "OBSERVED_PIN_MISSING"], ["IDENTITY", "PINS"], skipped=["AUDIO_RECEIPT"])

    def test_empty_or_broken_responses_are_unstructured_for_any_exit_code(self):
        for resp in (None, {}, "", "not json", [], 0, [1]):
            for code in (0, 1):
                e, o = world()
                o["stages"]["audio_plan"]["response"] = resp
                o["stages"]["audio_plan"]["exit_code"] = code
                res = ev(e, o)
                self.assertEqual(res["status"], "REJECTED", (resp, code))
                self.assertIn("RESPONSE_UNSTRUCTURED", codes(res))
                self.assertEqual(outcome(res, "AUDIO_PLAN"), "SKIPPED")

    def test_response_key_absent_is_unstructured(self):
        e, o = world()
        del o["stages"]["video_receipt"]["response"]
        res = ev(e, o)
        self.assertIn("RESPONSE_UNSTRUCTURED", codes(res))
        self.assertEqual(res["status"], "REJECTED")


class CountsAndSchemas(Base):
    def test_counts_must_be_integers_not_bool_or_float_or_strings(self):
        targets = [("audio_receipt", ("response", "mix", "samples_written")), ("audio_receipt", ("response", "readback", "wav", "sample_count")),
                   ("audio_receipt", ("response", "mix", "clipped_samples"))]
        for stage, path in targets:
            for bad in (9600.0, True, "9600", None, 9600.5):
                with self.subTest(path=path, bad=bad):
                    e, o = world()
                    node = o["stages"][stage]
                    for k in path[:-1]:
                        node = node[k]
                    node[path[-1]] = bad if path[-1] != "clipped_samples" else (0.0 if bad == 9600.0 else bad)
                    res = ev(e, o)
                    self.expect(res, "REJECTED", ["SHAPE_COUNT"], ["AUDIO_RECEIPT"])
        for key in ("total_samples", "total_frames"):
            for bad in (9600.0, True, 0, -1, "6"):
                e, o = world()
                o["stages"]["audio_plan"]["response"]["plan"]["timeline"][key] = bad
                res = ev(e, o)
                self.assertEqual(res["status"], "REJECTED", (key, bad))
                self.assertIn("SHAPE_COUNT", codes(res))
                self.assertEqual(outcome(res, "AUDIO_PLAN"), "FAIL")
        e, o = world()
        o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["checks"][0]["expected"] = 6.0
        self.expect(ev(e, o), "REJECTED", ["SHAPE_COUNT"], ["VIDEO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["outputs"][0]["bytes"] = True
        self.expect(ev(e, o), "REJECTED", ["SHAPE_OUTPUTS"], ["AUDIO_RECEIPT"])

    def test_count_differences_between_plan_and_receipt_are_rejected(self):
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["mix"]["samples_written"] = 9599
        self.expect(ev(e, o), "REJECTED", ["COUNT_MISMATCH"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["readback"]["wav"]["sample_count"] = 9601
        self.expect(ev(e, o), "REJECTED", ["COUNT_MISMATCH"], ["AUDIO_RECEIPT"])

    def test_clipped_samples_are_hold(self):
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["mix"]["clipped_samples"] = 3
        self.expect(ev(e, o), "HOLD", ["CLAMPED"], ["AUDIO_RECEIPT"])

    def test_unknown_and_wrong_version_schemas(self):
        for stage, path in (("audio_plan", ("response", "plan", "schema")), ("audio_receipt", ("response", "schema")), ("video_receipt", ("response", "schema"))):
            for bad, want in (("video_core.audio_plan/2" if stage == "audio_plan" else ("video_core.audio_receipt/2" if stage == "audio_receipt" else "video_core.receipt/2"), "SCHEMA_VERSION_MISMATCH"),
                              ("something.else/1", "SCHEMA_UNKNOWN"), (None, "SCHEMA_UNKNOWN"), (7, "SCHEMA_UNKNOWN")):
                with self.subTest(stage=stage, schema=bad):
                    e, o = world()
                    node = o["stages"][stage]
                    for k in path[:-1]:
                        node = node[k]
                    node[path[-1]] = bad
                    res = ev(e, o)
                    self.assertEqual(codes(res), {want})
                    self.assertEqual(res["status"], "REJECTED" if want == "SCHEMA_VERSION_MISMATCH" else "HOLD")
                    self.assertEqual(outcome(res, {"audio_plan": "AUDIO_PLAN", "audio_receipt": "AUDIO_RECEIPT", "video_receipt": "VIDEO_RECEIPT"}[stage]), "FAIL")


class MalformedShapes(Base):
    def test_six_malformed_shapes_are_typed_non_ready_never_exceptions(self):
        cases = {}
        e, o = world(); cases["expected_is_a_list"] = ([], o)
        e, o = world(); cases["observed_is_a_string"] = (e, "x")
        e, o = world(); cases["stages_is_a_list"] = (e, {"stages": []})
        e, o = world(); o["stages"]["audio_plan"]["response"]["plan"]["events"] = {}; cases["plan_events_is_a_dict"] = (e, o)
        e, o = world(); o["stages"]["audio_receipt"]["response"]["outputs"] = "stem"; cases["receipt_outputs_is_a_string"] = (e, o)
        e, o = world(); o["stages"]["video_receipt"]["identity"] = "job-001"; cases["identity_is_a_string"] = (e, o)
        want = {"expected_is_a_list": ("REJECTED", "SHAPE_EXPECTED", "EXPECTED"), "observed_is_a_string": ("REJECTED", "SHAPE_OBSERVED", "OBSERVED"),
                "stages_is_a_list": ("REJECTED", "SHAPE_OBSERVED", "OBSERVED"), "plan_events_is_a_dict": ("REJECTED", "SHAPE_PLAN", "AUDIO_PLAN"),
                "receipt_outputs_is_a_string": ("REJECTED", "SHAPE_OUTPUTS", "AUDIO_RECEIPT"), "identity_is_a_string": ("REJECTED", "SHAPE_IDENTITY", "IDENTITY")}
        for name, (e, o) in cases.items():
            with self.subTest(shape=name):
                res = ev(e, o)
                status, code, check = want[name]
                self.assertEqual(res["status"], status)
                self.assertIn(code, codes(res))
                self.assertEqual(outcome(res, check), "FAIL", f"the intended check was reached: {res['checks']}")
                for cid in ORDER[:ORDER.index(check)]:
                    self.assertIn(outcome(res, cid), ("PASS", "SKIPPED"), (name, cid))

    def test_none_and_missing_inputs_are_hold(self):
        e, o = world()
        self.assertEqual(codes(ev(None, o)), {"EXPECTED_MISSING"})
        self.assertEqual((ev(e, None)["status"], codes(ev(e, None))), ("HOLD", {"OBSERVED_MISSING", "OBSERVED_PIN_MISSING"}))

    def test_bad_expected_parts(self):
        for mutate, code in ((lambda e: e.pop("rows"), "EXPECTED_ROWS_MISSING"), (lambda e: e.pop("identity"), "EXPECTED_IDENTITY_MISSING"), (lambda e: e.pop("pins"), "EXPECTED_PIN_MISSING"),
                             (lambda e: e.update(rows={}), "SHAPE_ROWS"), (lambda e: e["rows"].append("x"), "SHAPE_ROWS"), (lambda e: e["rows"].append(dict(e["rows"][0])), "SHAPE_ROWS"),
                             (lambda e: e["rows"][0].update(state="BOGUS"), "SHAPE_ROWS"), (lambda e: e["rows"][0].update(selected=1), "SHAPE_ROWS"),
                             (lambda e: e["identity"].update(job_id=""), "SHAPE_IDENTITY"), (lambda e: e["identity"].update(extra="x"), "SHAPE_IDENTITY"),
                             (lambda e: e["identity"].pop("shot_id"), "IDENTITY_FIELD_MISSING"), (lambda e: e["pins"].update(unknown="a" * 64), "SHAPE_PINS")):
            e, o = world()
            mutate(e)
            res = ev(e, o)
            self.assertNotEqual(res["status"], "READY")
            self.assertIn(code, codes(res))
            self.assertEqual(outcome(res, "EXPECTED"), "FAIL")
            self.assertEqual(outcome(res, "AUDIO_PLAN"), "SKIPPED")

    def test_every_leaf_replaced_by_hostile_values_never_crashes_and_matters_where_it_should(self):
        base_e, base_o = world()

        def leaves(node, path=()):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from leaves(v, path + (k,))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from leaves(v, path + (i,))
            else:
                yield path

        hostile = (None, True, False, 0, -1, 2 ** 70, 1.5, float("nan"), "", "x" * 300, [], {}, [1], {"a": 1}, ("t",))
        ignorable_prefixes = {("stages", "audio_plan", "response", "mode"), ("stages", "audio_plan", "response", "observations")}
        ignorable_substrings = ("caller_assertions", "detail", "source_id", "bytes", "gain", "channel_rule", "asset", "source_in_sample", "source_out_sample", "covered_samples", "gaps", "overlap", "clipping",
                                "assets", "checks", "observed", "name", "side_effects", "snapshot_ref", "overlap", "gaps_truncated", "plan.canonical", "sha256", "outputs.1.")
        mattered = ready_after = total = 0
        for which, root in (("expected", base_e), ("observed", base_o)):
            for path in leaves(root):
                for bad in hostile:
                    e, o = copy.deepcopy(base_e), copy.deepcopy(base_o)
                    node = e if which == "expected" else o
                    for k in path[:-1]:
                        node = node[k]
                    if type(node[path[-1]]) is type(bad) and node[path[-1]] == bad:
                        continue  # not a mutation
                    node[path[-1]] = bad
                    res = ev(e, o)  # ev() already asserts: no exception, no INTERNAL_EVALUATION_ERROR, deterministic, inputs untouched
                    total += 1
                    if res["status"] == "READY":
                        ready_after += 1
                        key = path if which == "observed" else ("expected",) + path
                        text = ".".join(map(str, key))
                        # a hostile value may stay READY only on leaves the candidate documents as ignored
                        self.assertTrue(any(s in text for s in ignorable_substrings) or any(tuple(path[:len(p)]) == p for p in ignorable_prefixes),
                                        f"READY survived a hostile value at {text} = {bad!r}")
                    else:
                        mattered += 1
        self.assertGreater(total, 1500)
        self.assertGreater(mattered, ready_after)

    def test_decisive_leaves_always_break_readiness(self):
        decisive = [("observed", ("stages", "audio_plan", "response", "status")), ("observed", ("stages", "audio_plan", "exit_code")), ("observed", ("stages", "audio_receipt", "response", "status")),
                    ("observed", ("stages", "audio_receipt", "response", "readback", "all_ok")), ("observed", ("stages", "audio_receipt", "response", "mix", "samples_written")),
                    ("observed", ("stages", "audio_receipt", "response", "written")), ("observed", ("stages", "audio_receipt", "response", "stage")),
                    ("observed", ("stages", "audio_receipt", "response", "input", "inputs_unchanged")), ("observed", ("stages", "audio_receipt", "response", "plan_sha256")),
                    ("observed", ("stages", "audio_receipt", "response", "input", "manifest_sha256")), ("observed", ("stages", "audio_receipt", "receipt_sha256")),
                    ("observed", ("stages", "video_receipt", "response", "status")), ("observed", ("stages", "video_receipt", "response", "outputs", 0, "sha256")),
                    ("observed", ("stages", "avmux_manifest_view", "audio_sha256")), ("observed", ("stages", "avmux_manifest_view", "selection_ref")),
                    ("observed", ("stages", "audio_plan", "identity", "job_id")), ("observed", ("stages", "audio_plan", "response", "plan", "warnings")),
                    ("expected", ("identity", "job_id")), ("expected", ("pins", "video_sha256")), ("expected", ("pins_source",)), ("expected", ("rows", 0, "state"))]
        for which, path in decisive:
            for bad in (None, True, 0, 1.5, "zzz", [], {}):
                e, o = world()
                node = e if which == "expected" else o
                for k in path[:-1]:
                    node = node[k]
                if type(node[path[-1]]) is type(bad) and node[path[-1]] == bad:
                    continue  # not a mutation
                node[path[-1]] = bad
                self.assertNotEqual(ev(e, o)["status"], "READY", (which, path, bad))


class ProfileHandling(Base):
    def test_no_profile_or_a_non_object_profile_is_hold_and_never_defaults(self):
        e, o = world()
        for bad in (None, [], "v0", 0):
            res = ev(e, o, bad)
            self.assertEqual((res["status"], codes(res)), ("HOLD", {"PROFILE_NOT_SUPPLIED"}))
            self.assertEqual(outcome(res, "PROFILE"), "FAIL")
            self.assertTrue(all(c["outcome"] == "SKIPPED" for c in res["checks"][1:]))

    def test_every_missing_policy_field_is_unresolved_hold(self):
        e, o = world()
        for k in list(PROF):
            if k in ("profile_id", "profile_version"):
                continue
            p = profile()
            del p[k]
            res = ev(e, o, p)
            self.assertEqual((res["status"], codes(res)), ("HOLD", {"PROFILE_INCOMPLETE"}), k)
            self.assertIn(k, res["reasons"][0]["detail"])

    def test_malformed_policy_fields_are_hold_not_defaulted(self):
        e, o = world()
        for k, bad in (("treated_warning_codes", "NONE"), ("exit_codes_ok", {"audio_plan": [True]}), ("require_binding_for_use", 1), ("required_gap_policy", "ZERO"),
                       ("empty_effective_scope", "READY"), ("required_pins", ["bogus"]), ("required_stages", ["audio_plan", "x"]), ("review_status", ""), ("known_schemas", [])):
            res = ev(e, o, profile(**{k: bad}))
            self.assertEqual((res["status"], codes(res)), ("HOLD", {"PROFILE_INCOMPLETE"}), k)

    def test_unknown_profile_id_and_version_mismatch_are_rejected(self):
        e, o = world()
        self.assertEqual((ev(e, o, profile(profile_id="other-profile"))["status"], codes(ev(e, o, profile(profile_id="other-profile")))), ("REJECTED", {"PROFILE_ID_UNKNOWN"}))
        for v in (1, -1, True, 0.0, "0"):
            res = ev(e, o, profile(profile_version=v))
            self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_VERSION_MISMATCH"}), v)

    def test_the_profile_changes_the_outcome_only_through_explicit_values(self):
        e, o = world()
        self.ready(ev(e, o, profile(review_status="UNREVIEWED-LOCAL-COPY")))
        self.assertEqual(ev(e, o, profile(required_gap_policy="ALLOW_ZERO_FILL"))["status"], "HOLD")
        self.assertIn("POLICY_GAP_NOT_REQUIRED", codes(ev(e, o, profile(required_gap_policy="ALLOW_ZERO_FILL"))))
        self.assertEqual(ev(e, o, profile(required_stages=["audio_plan"]))["status"], "HOLD", "dropping a stage alone leaves its pins unobserved")
        res = ev(e, o, profile(required_stages=["audio_plan"], required_pins=["plan_sha256"]))  # fewer stages AND pins: an explicit choice of the profile owner
        self.assertEqual((res["status"], res["reasons"]), ("READY", []))
        self.assertEqual([outcome(res, c) for c in ("AUDIO_RECEIPT", "VIDEO_RECEIPT", "MANIFEST_VIEW")], ["SKIPPED"] * 3, "stages the profile does not require are not evaluated (and say so)")
        res = ev(e, o, profile(exit_codes_ok={"audio_plan": [0, 5], "audio_receipt": [0], "video_receipt": [0]}))
        self.ready(res)


class PurityAndForgeryBoundary(Base):
    def test_module_is_pure_by_static_inspection(self):
        import ast
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "caller_admission.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        imported = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imported, {"re", "__future__"})
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id != "re"} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                                                                                           and not (isinstance(n.value, ast.Name) and n.value.id == "re")}
        for forbidden in ("open", "os", "sys", "subprocess", "time", "datetime", "socket", "environ", "getenv", "random", "exec", "eval", "compile", "input", "print", "__import__"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_a_self_consistent_forgery_is_ready_value_comparison_cannot_authenticate(self):
        fake = {k: "ab" * 32 for k in ca.PIN_KINDS}
        e, o = world(pins=fake)
        res = ev(e, o)
        self.ready(res)
        self.assertTrue(any("authenticity" in s for s in res["not_claimed"]), "the result says it cannot authenticate")
        # the forgery's only protection is the OWNER's independent pin: one wrong value and it is caught
        e2, o2 = world(pins=fake)
        e2["pins"]["stem_sha256"] = PINS["stem_sha256"]
        self.assertEqual(ev(e2, o2)["status"], "REJECTED")

    def test_pins_taken_from_the_receipt_itself_would_make_the_comparison_vacuous_and_are_declared_not_independent(self):
        e, o = world()
        e["pins"] = {k: o["stages"]["audio_receipt"]["response"]["plan_sha256"] for k in ca.PIN_KINDS}  # same value copied everywhere
        e["pins_source"] = "observed_receipt"
        res = ev(e, o)
        self.assertIn("PIN_SOURCE_NOT_INDEPENDENT", codes(res))
        self.assertNotEqual(res["status"], "READY")

    def test_result_never_claims_execution_or_approval(self):
        res = ev(*world())
        text = json.dumps(res).lower()
        for word in ("approved", "execute now", "granted", "permission granted"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
