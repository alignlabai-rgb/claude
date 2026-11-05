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

ORDER = ["BOUNDS", "PROFILE", "EXPECTED", "OBSERVED", "IDENTITY", "AUDIO_PLAN", "SELECTION", "AUDIO_RECEIPT", "VIDEO_RECEIPT", "MANIFEST_VIEW", "PINS", "EFFECTIVE_SCOPE"]
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

    def test_a_caller_cannot_treat_a_warning_by_modifying_the_profile_under_the_v0_identity(self):
        w = [{"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}]
        rows = [{"id": "e2", "state": "INTENTIONAL_SILENCE", "selected": True, "decision": "dec-silence-e2"}]
        e, o = world(rows, warnings=w)
        self.assertEqual(ev(e, o)["status"], "HOLD")
        res = ev(e, o, profile(treated_warning_codes=["NO_AUDIO_EVENTS_SELECTED"]))
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))
        e, o = world(warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}])
        self.assertEqual(ev(e, o)["status"], "HOLD")
        self.assertEqual(ev(e, o, profile(treated_warning_codes=["ZERO_FILL_APPLIED"]))["status"], "REJECTED")


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

    def test_empty_effective_scope_is_not_ready(self):
        rows = [{"id": "e4", "state": "OMIT", "selected": True, "decision": "dec-omit-e4"}]
        e, o = world(rows, warnings=[{"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}], gap_count=1)
        res = ev(e, o)
        self.expect(res, "HOLD", ["WARNING_UNTREATED", "EMPTY_EFFECTIVE_SCOPE", "INCOMPLETE_COVERAGE"], ["AUDIO_PLAN", "AUDIO_RECEIPT", "EFFECTIVE_SCOPE"])
        self.assertEqual(res["evidence"]["selected_scope"], {"use": [], "qualified_silence": [], "omit_no_contribution": ["e4"], "selected_unresolved": []})

    def test_a_caller_cannot_treat_the_empty_scope_warning_to_get_there(self):
        rows = [{"id": "e4", "state": "OMIT", "selected": True, "decision": "dec-omit-e4"}]
        e, o = world(rows, warnings=[{"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}], gap_count=1)
        res = ev(e, o, profile(treated_warning_codes=["NO_AUDIO_EVENTS_SELECTED"]))
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))


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
        self.expect(ev(e, o), "REJECTED", ["SELECTION_SET_MISMATCH", "ROW_MISSING_IN_PLAN"], ["SELECTION"])
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
        res = ev(e, o, profile(require_binding_for_use=False))  # relaxing the policy under the same id/version is not honoured
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))


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
        other = H("a different but well-formed value")
        pin_only = (["PIN_MISMATCH"], ["PINS"])
        plan_pair = (["PIN_MISMATCH", "OUTPUT_PLAN_SHA_MISMATCH"], ["AUDIO_RECEIPT", "PINS"])  # the receipt's own plan output must equal its plan_sha256 too
        paths = [
            ("audio_plan", ("response", "plan_sha256"), pin_only),
            ("audio_receipt", ("response", "plan_sha256"), plan_pair),
            ("audio_receipt", ("response", "outputs", 1, "sha256"), plan_pair),
            ("audio_receipt", ("response", "input", "manifest_sha256"), pin_only),
            ("audio_receipt", ("receipt_sha256",), pin_only),
            ("audio_receipt", ("response", "outputs", 0, "sha256"), pin_only),
            ("avmux_manifest_view", ("audio_sha256",), pin_only),
            ("video_receipt", ("response", "outputs", 0, "sha256"), pin_only),
            ("avmux_manifest_view", ("video_sha256",), pin_only),
        ]
        for stage, path, (want_codes, want_fail) in paths:
            with self.subTest(stage=stage, path=path):
                e, o = world()
                node = o["stages"][stage]
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = other
                res = ev(e, o)
                self.expect(res, "REJECTED", want_codes, want_fail)

    def test_the_video_receipt_plan_output_must_equal_the_receipts_own_plan_hash(self):
        e, o = world()
        o["stages"]["video_receipt"]["response"]["outputs"][2]["sha256"] = H("not the video plan")
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_PLAN_SHA_MISMATCH"], ["VIDEO_RECEIPT"])
        e, o = world()
        del o["stages"]["video_receipt"]["response"]["plan_sha256"]
        self.expect(ev(e, o), "HOLD", ["RECEIPT_LINK_MISSING"], ["VIDEO_RECEIPT"])

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

    def test_a_frame_count_mismatch_between_video_and_audio_timeline_is_reached(self):
        e, o = world()
        c = next(c for c in o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["checks"] if c["name"] == "decoded_frame_count")
        c["expected"], c["observed"] = 7, "7"  # internally consistent, but not the audio timeline's 6 frames
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
        r = o["stages"]["audio_receipt"]["response"]
        r["readback"]["checks"][4].update(observed=r["readback"]["checks"][4]["expected"] - 2, ok=False)  # a genuinely failed check AND all_ok=false: consistent, not passing
        r["readback"]["all_ok"] = False
        self.expect(ev(e, o), "HOLD", ["READBACK_NOT_OK"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["readback"]["all_ok"] = False  # all checks pass but all_ok says otherwise: a contradiction, not a hold
        self.expect(ev(e, o), "REJECTED", ["CHECK_INCONSISTENT"], ["AUDIO_RECEIPT"])
        for bad in (1, 0, "true", None, "False"):
            e, o = world()
            o["stages"]["audio_receipt"]["response"]["readback"]["all_ok"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_FLAG"], ["AUDIO_RECEIPT"])
        e, o = world()  # a genuinely partial run: the stem is only listed as stem_partial
        r = o["stages"]["audio_receipt"]["response"]
        r["outputs"][0].update(name="stem.wav.partial", role="stem_partial", partial=True)
        self.expect(ev(e, o), "HOLD", ["OUTPUT_PARTIAL", "OUTPUT_MISSING"], ["AUDIO_RECEIPT"])
        e, o = world()  # partial flag on a non-partial role is a contradiction
        o["stages"]["audio_receipt"]["response"]["outputs"][0]["partial"] = True
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_FLAG_CONTRADICTION"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["written"] = False
        self.expect(ev(e, o), "HOLD", ["RECEIPT_NOT_WRITTEN"], ["AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["quality_evaluated"] = True
        self.expect(ev(e, o), "REJECTED", ["QUALITY_CLAIM"], ["AUDIO_RECEIPT"])

    def test_receipt_warnings_and_unresolved_report_are_preserved(self):
        w = [{"code": "ZERO_FILL_APPLIED", "detail": "x"}]
        e, o = world(warnings=w)  # plan and receipt agree on the warning: preserved, untreated, HOLD at both
        res = ev(e, o)
        self.expect(res, "HOLD", ["WARNING_UNTREATED"], ["AUDIO_PLAN", "AUDIO_RECEIPT"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["warnings"] = w  # the receipt alone claims a warning the plan did not have
        self.expect(ev(e, o), "REJECTED", ["RECEIPT_PLAN_MISMATCH", "WARNING_UNTREATED"], ["AUDIO_RECEIPT"])
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "u1", "state": "UNKNOWN", "selected": False}]
        e, o = world(rows)
        res = ev(e, o)
        self.ready(res)
        self.assertEqual(res["evidence"]["receipt_unresolved_preserved"], ["u1"])
        e, o = world()
        o["stages"]["audio_receipt"]["response"]["unresolved_report"] = [{"event_id": "e1", "source_id": "src-e1", "state": "UNKNOWN", "selected": True}]
        res = ev(e, o)
        self.assertEqual(res["status"], "REJECTED")
        self.assertTrue({"RECEIPT_PLAN_MISMATCH", "SELECTED_UNRESOLVED"} <= codes(res))

    def test_unselected_unresolved_report_rows_must_stay_exact_and_never_become_a_selected_blocker(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "u1", "state": "UNKNOWN", "selected": False}]
        for bad in (1, 0, "false", None, "False", 1.0):
            e, o = world(rows)
            o["stages"]["audio_receipt"]["response"]["unresolved_report"][0]["selected"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_UNRESOLVED"], ["AUDIO_RECEIPT"])
        for key, bad in (("state", "MISSING"), ("source_id", "src-other"), ("event_id", "u2")):
            e, o = world(rows)
            o["stages"]["audio_receipt"]["response"]["unresolved_report"][0][key] = bad
            res = ev(e, o)
            self.assertEqual((res["status"], "RECEIPT_PLAN_MISMATCH" in codes(res)), ("REJECTED", True), key)
        e, o = world(rows)
        o["stages"]["audio_receipt"]["response"]["unresolved_report"][0]["extra"] = 1
        self.expect(ev(e, o), "REJECTED", ["SHAPE_UNRESOLVED"], ["AUDIO_RECEIPT"])
        e, o = world(rows)
        o["stages"]["audio_receipt"]["response"]["unresolved_report"].append(dict(o["stages"]["audio_receipt"]["response"]["unresolved_report"][0]))
        self.expect(ev(e, o), "REJECTED", ["SHAPE_UNRESOLVED"], ["AUDIO_RECEIPT"])
        e, o = world(rows)
        o["stages"]["audio_receipt"]["response"]["unresolved_report"] = []  # the receipt dropped the unselected row
        self.expect(ev(e, o), "REJECTED", ["RECEIPT_PLAN_MISMATCH"], ["AUDIO_RECEIPT"])
        e, o = world(rows)
        res = ev(e, o)
        self.ready(res)
        self.assertEqual(res["evidence"]["unselected_unresolved"], [{"row_id": "u1", "state": "UNKNOWN"}])

    def test_video_receipt_flags(self):
        e, o = world()
        ch = o["stages"]["video_receipt"]["response"]["readback"]["comparison"]
        next(c for c in ch["checks"] if c["name"] == "pix_fmt").update(observed="yuv444p", ok=False)
        ch["all_ok"] = False
        self.expect(ev(e, o), "HOLD", ["READBACK_NOT_OK"], ["VIDEO_RECEIPT"])
        e, o = world()
        o["stages"]["video_receipt"]["response"]["outputs"][0].update(name="output.mp4.partial", role="video_partial", partial=True)
        self.expect(ev(e, o), "HOLD", ["OUTPUT_PARTIAL", "OUTPUT_MISSING"], ["VIDEO_RECEIPT"])
        e, o = world()
        o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["checks"] = []
        self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["VIDEO_RECEIPT"])


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
        o["stages"]["audio_receipt"]["response"]["plan_sha256"] = None  # null is treated as no link
        self.expect(ev(e, o), "HOLD", ["RECEIPT_LINK_MISSING"], ["AUDIO_RECEIPT"])
        e, o = world()
        del o["stages"]["audio_receipt"]["response"]["input"]["manifest_sha256"]
        self.expect(ev(e, o), "HOLD", ["RECEIPT_LINK_MISSING", "OBSERVED_PIN_MISSING"], ["AUDIO_RECEIPT", "PINS"])
        for bad in (7, "A" * 64, "a" * 10, True):
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
        next(c for c in o["stages"]["video_receipt"]["response"]["readback"]["comparison"]["checks"] if c["name"] == "decoded_frame_count")["expected"] = 6.0
        self.expect(ev(e, o), "REJECTED", ["CHECK_INCONSISTENT"], ["VIDEO_RECEIPT"])
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

    # Leaves of the conforming world that may stay READY under hostile / different-valid values, each tied to a DECLARED not_checked id (and nothing else may).
    IGNORED_LEAVES = [
        (("expected", "snapshot_ref"), "owner_snapshot_label"),
        (("observed", "stages", "audio_plan", "response", "mode"), "producer_diagnostics"),
        (("observed", "stages", "audio_plan", "response", "observations"), "producer_diagnostics"),
        (("observed", "stages", "audio_plan", "response", "plan", "caller_assertions"), "producer_diagnostics"),
        (("observed", "stages", "audio_plan", "response", "plan", "policy", "overlap"), "event_mix_detail"),
        (("observed", "stages", "audio_plan", "response", "plan", "policy", "clipping"), "event_mix_detail"),
        (("observed", "stages", "audio_plan", "response", "plan", "events", "*", "source_id"), "event_mix_detail"),  # opaque value; type/length is validated
        (("observed", "stages", "audio_plan", "response", "plan", "events", "*", "asset"), "event_mix_detail"),  # opaque asset id; shape is validated
        (("observed", "stages", "audio_plan", "response", "plan", "events", "*", "source_in_sample"), "event_mix_detail"),  # a different valid integer: lengths vs ranges are not recomputed
        (("observed", "stages", "audio_plan", "response", "plan", "events", "*", "source_out_sample"), "event_mix_detail"),
        (("observed", "stages", "audio_plan", "response", "plan", "events", "*", "gain", "num"), "event_mix_detail"),
        (("observed", "stages", "audio_plan", "response", "plan", "events", "*", "gain", "den"), "event_mix_detail"),
        (("observed", "stages", "audio_receipt", "response", "outputs", "*", "bytes"), "other_output_sizes_and_names"),  # the stem's size IS checked (see below)
        (("observed", "stages", "audio_receipt", "response", "outputs", "*", "name"), "other_output_sizes_and_names"),  # the stem's name IS checked
        (("observed", "stages", "video_receipt", "response", "outputs", "*", "bytes"), "other_output_sizes_and_names"),
        (("observed", "stages", "video_receipt", "response", "outputs", "*", "name"), "other_output_sizes_and_names"),
        (("observed", "stages", "video_receipt", "response", "outputs", "*", "sha256"), "video_side_outputs"),  # srt / ffprobe_json only; video and plan hashes ARE checked
        (("observed", "stages", "video_receipt", "response", "readback", "comparison", "checks", "*", "expected"), "video_tolerance_checks"),
        (("observed", "stages", "video_receipt", "response", "readback", "comparison", "checks", "*", "observed"), "video_tolerance_checks"),
    ]

    @staticmethod
    def _matches(pattern, key):
        return len(pattern) <= len(key) and all(p == "*" and type(k) is int or p == k for p, k in zip(pattern, key))  # a pattern covers the leaf at or below it

    def test_every_leaf_of_the_conforming_world_is_decisive_unless_declared_not_checked(self):
        """The class of bug that hid ten false READYs: a contract-bearing leaf that nothing looks at. For EVERY leaf of a conforming world, every hostile value AND a different-but-valid
        value of the same type must leave the result non-READY, except for leaves on the exact allowlist above, each mapped to an id the result itself lists under not_checked."""
        base_e, base_o = world(list(world.__defaults__[0]) + [{"id": "u1", "state": "UNKNOWN", "selected": False}])

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

        def other(v):
            if type(v) is bool:
                return not v
            if type(v) is int:
                return v + 1
            if type(v) is str:
                return H("other" + v) if len(v) == 64 and all(c in "0123456789abcdef" for c in v) else v + "-x"
            return None
        declared = {x["id"] for x in ca.NOT_CHECKED_V0}
        survivors, total, hit = {}, 0, set()
        for which, root in (("expected", base_e), ("observed", base_o)):
            for path in leaves(root):
                orig = root
                for k in path:
                    orig = orig[k]
                cands = list(hostile) + ([other(orig)] if other(orig) is not None else [])
                for bad in cands:
                    if type(orig) is type(bad) and orig == bad:
                        continue
                    e, o = copy.deepcopy(base_e), copy.deepcopy(base_o)
                    node = e if which == "expected" else o
                    for k in path[:-1]:
                        node = node[k]
                    node[path[-1]] = bad
                    res = ev(e, o)  # ev(): no exception, no safety-net code, deterministic, inputs untouched, JSON-safe
                    total += 1
                    if res["status"] == "READY":
                        survivors.setdefault((which,) + path, []).append(bad)
        self.assertGreater(total, 2500)
        for key, vals in survivors.items():
            matching = [(pat, nid) for pat, nid in self.IGNORED_LEAVES if self._matches(pat, key)]
            self.assertTrue(matching, f"READY survived at undeclared leaf {'.'.join(map(str, key))} with {vals[:3]!r}")
            self.assertIn(matching[0][1], declared, "the allowlist entry must name a declared not_checked id")
            hit.add(matching[0][0])
        stale = [pat for pat, _ in self.IGNORED_LEAVES if pat not in hit]
        self.assertEqual(stale, [], "allowlist entries that no longer correspond to an ignored leaf must be removed")

    def test_the_ignored_leaves_are_still_shape_checked_where_the_result_claims_so(self):
        """An ignored value is ignored only for its VALUE: the stem's size and name, and the video / plan output hashes, are decisive although siblings are not."""
        for path, bad in ((("response", "outputs", 0, "bytes"), 99), (("response", "outputs", 0, "name"), "other.wav")):
            e, o = world()
            node = o["stages"]["audio_receipt"]
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = bad
            self.assertNotEqual(ev(e, o)["status"], "READY", path)
        for idx in (0, 2):
            e, o = world()
            o["stages"]["video_receipt"]["response"]["outputs"][idx]["sha256"] = H("different")
            self.assertNotEqual(ev(e, o)["status"], "READY", idx)

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
            self.assertTrue(all(c["outcome"] == "SKIPPED" for c in res["checks"][2:]))

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

    def test_any_changed_extra_or_malformed_policy_value_under_the_v0_identity_is_rejected_not_honoured(self):
        e, o = world()
        for k in PROF:
            if k in ("profile_id", "profile_version"):
                continue
            for bad in (None, True, 0, 1.5, "x", [], {}, ["NO_AUDIO_EVENTS_SELECTED"], "UNREVIEWED-LOCAL-COPY"):
                if type(PROF[k]) is type(bad) and PROF[k] == bad:
                    continue
                res = ev(e, o, profile(**{k: bad}))
                self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}), (k, bad))
                self.assertIn(k, res["reasons"][0]["detail"])
                self.assertTrue(all(c["outcome"] == "SKIPPED" for c in res["checks"][2:]))
        res = ev(e, o, profile(extra_policy=True))
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))

    def test_the_two_reported_profile_holes(self):
        """Same profile_id / version: treating ZERO_FILL_APPLIED, and dropping the audio receipt from the required stages and pins, must not turn a HOLD / HOLD-worthy world into READY."""
        e, o = world(warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}])
        self.assertEqual(ev(e, o)["status"], "HOLD")
        self.assertEqual(ev(e, o, profile(treated_warning_codes=["ZERO_FILL_APPLIED"]))["status"], "REJECTED")
        e, o = world()
        del o["stages"]["audio_receipt"]
        p = profile(required_stages=["audio_plan", "video_receipt", "avmux_manifest_view"], required_pins=["plan_sha256", "stem_sha256", "video_sha256"])
        res = ev(e, o, p)
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))
        self.assertEqual(ev(e, o)["status"], "HOLD")

    def test_an_equal_profile_is_honoured_whatever_the_key_order_and_identity_of_the_object(self):
        import copy as _c
        e, o = world()
        p = _c.deepcopy(PROF)
        self.ready(ev(e, o, dict(reversed(list(p.items())))))
        self.ready(ev(e, o, p))

    def test_profile_equality_is_type_strict(self):
        e, o = world()
        for key, bad in (("require_binding_for_use", 1), ("profile_version", 0.0), ("treated_warning_codes", ()), ("exit_codes_ok", {"audio_plan": [False], "audio_receipt": [0], "video_receipt": [0]})):
            res = ev(e, o, profile(**{key: bad}))
            self.assertEqual(res["status"], "REJECTED", key)

    def test_unknown_profile_id_and_version_mismatch_are_rejected(self):
        e, o = world()
        res = ev(e, o, profile(profile_id="other-profile"))
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_ID_UNKNOWN"}))
        for v in (1, -1, True, 0.0, "0"):
            res = ev(e, o, profile(profile_version=v))
            self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_VERSION_MISMATCH"}), v)

class ReportedFalseReadyCases(Base):
    """The ten false READYs found by independent validation of 508d263 (one field changed per case from a contract-conforming world). Each must now be non-READY AND must reach the intended
    comparison: the named check FAILs, every other check passes (or is explicitly skipped)."""

    def vr(self, o):
        return o["stages"]["video_receipt"]["response"]

    def ar(self, o):
        return o["stages"]["audio_receipt"]["response"]

    def check(self, o, name):
        return next(c for c in self.vr(o)["readback"]["comparison"]["checks"] if c["name"] == name)

    def test_case_1_missing_required_readback_evidence(self):
        e, o = world()
        self.vr(o)["readback"]["comparison"]["checks"] = None
        self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["VIDEO_RECEIPT"])
        e, o = world()
        del self.vr(o)["readback"]["comparison"]["checks"]
        self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["VIDEO_RECEIPT"])
        e, o = world()
        self.vr(o)["readback"]["comparison"] = None
        self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["VIDEO_RECEIPT"])
        e, o = world()
        del self.vr(o)["readback"]
        self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["VIDEO_RECEIPT"])
        for name in ca.VIDEO_READBACK_CHECKS:  # every required check individually
            e, o = world()
            self.vr(o)["readback"]["comparison"]["checks"] = [c for c in self.vr(o)["readback"]["comparison"]["checks"] if c["name"] != name]
            self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["VIDEO_RECEIPT"])
        e, o = world()
        self.ar(o)["readback"]["checks"] = None
        self.expect(ev(e, o), "HOLD", ["READBACK_EVIDENCE_MISSING"], ["AUDIO_RECEIPT"])
        e, o = world()
        self.vr(o)["readback"]["comparison"]["checks"] = "all good"
        self.expect(ev(e, o), "REJECTED", ["SHAPE_READBACK"], ["VIDEO_RECEIPT"])

    def test_case_2_the_claimed_observed_value_must_agree_with_expected_and_ok(self):
        e, o = world()
        self.check(o, "decoded_frame_count")["observed"] = "99999"  # ok stays true: the receipt contradicts itself
        self.expect(ev(e, o), "REJECTED", ["CHECK_INCONSISTENT"], ["VIDEO_RECEIPT"])
        for bad in (6, 6.0, True, "06", " 6", "6 ", "+6", "6.0", "", "six", [6], {"n": 6}):
            e, o = world()
            self.check(o, "decoded_frame_count")["observed"] = bad
            res = ev(e, o)
            if type(bad) is int and bad == 6:  # a bare int equal to expected is accepted shape-wise (the producer emits the ffprobe string, but an equal canonical integer is not a contradiction)
                self.ready(res)
            else:
                self.expect(res, "REJECTED", ["CHECK_INCONSISTENT"], ["VIDEO_RECEIPT"])
        # a mismatch that is honestly reported as failed is a HOLD (not READY), not a contradiction
        e, o = world()
        c = self.check(o, "decoded_frame_count")
        c.update(observed="99999", ok=False)
        self.vr(o)["readback"]["comparison"]["all_ok"] = False
        self.expect(ev(e, o), "HOLD", ["READBACK_NOT_OK"], ["VIDEO_RECEIPT"])
        e, o = world()
        c = self.check(o, "decoded_frame_count")
        c.update(observed=None, ok=False)
        self.vr(o)["readback"]["comparison"]["all_ok"] = False
        self.expect(ev(e, o), "HOLD", ["READBACK_NOT_OK"], ["VIDEO_RECEIPT"])
        # equal values reported as not ok are also a contradiction
        e, o = world()
        self.check(o, "decoded_frame_count")["ok"] = False
        self.vr(o)["readback"]["comparison"]["all_ok"] = False
        self.assertIn("CHECK_INCONSISTENT", codes(ev(e, o)))
        # the same rule for every exact-equality check
        for name in ("video_stream_count", "non_video_stream_count", "width", "height", "container_frame_count", "codec_name", "pix_fmt"):
            e, o = world()
            c = self.check(o, name)
            c["observed"] = c["expected"] + 1 if type(c["expected"]) is int else "other"
            self.expect(ev(e, o), "REJECTED", ["CHECK_INCONSISTENT"], ["VIDEO_RECEIPT"])

    def test_case_3_output_sizes_are_non_negative_and_the_stem_matches_the_plan_geometry(self):
        for bad in (-1, True, 1.5, "1889", None):
            e, o = world()
            self.ar(o)["outputs"][0]["bytes"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_OUTPUTS"], ["AUDIO_RECEIPT"])
            e, o = world()
            self.vr(o)["outputs"][0]["bytes"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_OUTPUTS"], ["VIDEO_RECEIPT"])
        e, o = world()
        self.ar(o)["outputs"][0]["bytes"] = 99  # a valid integer that is not the stem size the plan promised
        self.expect(ev(e, o), "REJECTED", ["COUNT_MISMATCH"], ["AUDIO_RECEIPT"])
        e, o = world()
        self.ar(o)["outputs"][0]["name"] = "other.wav"
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_GEOMETRY_INCONSISTENT"], ["AUDIO_RECEIPT"])

    def test_case_4_partial_flag_on_a_non_partial_output(self):
        e, o = world()
        self.ar(o)["outputs"][1]["partial"] = True
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_FLAG_CONTRADICTION"], ["AUDIO_RECEIPT"])
        for idx in range(4):
            e, o = world()
            self.vr(o)["outputs"][idx]["partial"] = True
            self.expect(ev(e, o), "REJECTED", ["OUTPUT_FLAG_CONTRADICTION"], ["VIDEO_RECEIPT"])
        for bad in (1, 0, "false", None):
            e, o = world()
            self.ar(o)["outputs"][1]["partial"] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_OUTPUTS"], ["AUDIO_RECEIPT"])
        e, o = world()  # a partial-role entry flagged NOT partial
        self.ar(o)["outputs"].append({"name": "stem.wav.partial", "role": "stem_partial", "bytes": 5, "sha256": H("p"), "partial": False})
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_FLAG_CONTRADICTION"], ["AUDIO_RECEIPT"])

    def test_case_5_the_plan_output_hash_must_equal_the_receipts_own_plan_hash(self):
        e, o = world()
        self.ar(o)["outputs"][1]["sha256"] = H("another plan file")
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_PLAN_SHA_MISMATCH", "PIN_MISMATCH"], ["AUDIO_RECEIPT", "PINS"])
        e, o = world()
        self.assertEqual(self.ar(o)["outputs"][1]["sha256"], self.ar(o)["plan_sha256"], "the conforming fixture itself satisfies the contract (plan file hash == plan_sha256)")
        e, o = world()
        del self.ar(o)["outputs"][1]
        self.expect(ev(e, o), "HOLD", ["OUTPUT_MISSING"], ["AUDIO_RECEIPT"])

    def test_case_6_coverage_evidence_must_be_meaningful(self):
        plan = lambda o: o["stages"]["audio_plan"]["response"]["plan"]
        e, o = world()
        plan(o)["coverage"]["covered_samples"] = 0  # total 9600, gap_count 0
        self.expect(ev(e, o), "REJECTED", ["COVERAGE_INCONSISTENT"], ["AUDIO_PLAN"])
        for bad in (9601, -1, 9600.0, True, "9600", None):
            e, o = world()
            plan(o)["coverage"]["covered_samples"] = bad
            res = ev(e, o)
            self.assertEqual(res["status"], "REJECTED", bad)
            self.assertEqual(outcome(res, "AUDIO_PLAN"), "FAIL")
        e, o = world()
        del plan(o)["coverage"]
        self.expect(ev(e, o), "HOLD", ["COVERAGE_EVIDENCE_MISSING"], ["AUDIO_PLAN"])
        e, o = world()
        plan(o)["coverage"] = "full"
        self.expect(ev(e, o), "REJECTED", ["SHAPE_COVERAGE"], ["AUDIO_PLAN"])
        for key, bad in (("gap_count", -1), ("gap_count", 1.0), ("gaps", {}), ("gaps_truncated", 0), ("gaps_truncated", True)):
            e, o = world()
            plan(o)["coverage"][key] = bad
            res = ev(e, o)
            self.assertEqual(res["status"], "REJECTED", (key, bad))
        # gaps listed but gap_count says none, and the reverse
        e, o = world()
        plan(o)["coverage"].update(gaps=[{"sample_start": 0, "sample_end": 100}])
        self.expect(ev(e, o), "REJECTED", ["COVERAGE_INCONSISTENT"], ["AUDIO_PLAN"])
        e, o = world(gap_count=2)
        plan(o)["coverage"]["gap_count"] = 3
        self.assertEqual(ev(e, o)["status"], "REJECTED")
        e, o = world(gap_count=2)
        plan(o)["coverage"]["gaps"][0]["sample_end"] = plan(o)["coverage"]["gaps"][0]["sample_start"]  # empty gap range
        self.expect(ev(e, o), "REJECTED", ["SHAPE_COVERAGE"], ["AUDIO_PLAN"])
        # an honest gapped plan is HOLD (incomplete), never READY
        e, o = world(gap_count=1)
        res = ev(e, o)
        self.assertEqual((res["status"], "INCOMPLETE_COVERAGE" in codes(res)), ("HOLD", True))
        # timeline / output geometry arithmetic
        e, o = world()
        plan(o)["timeline"]["total_samples"] = 9601
        res = ev(e, o)
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("TIMELINE_INCONSISTENT", codes(res))
        e, o = world()
        plan(o)["output"]["bytes"] += 2
        self.expect(ev(e, o), "REJECTED", ["OUTPUT_GEOMETRY_INCONSISTENT"], ["AUDIO_PLAN"])
        e, o = world()
        plan(o)["timeline"]["fps"]["rational"] = "60/2"
        self.expect(ev(e, o), "REJECTED", ["TIMELINE_INCONSISTENT"], ["AUDIO_PLAN"])
        e, o = world()
        del plan(o)["output"]
        self.expect(ev(e, o), "HOLD", ["OUTPUT_GEOMETRY_MISSING"], ["AUDIO_PLAN"])

    def test_case_6b_truncated_gap_reports(self):
        e, o = world(gap_count=3)
        cov = o["stages"]["audio_plan"]["response"]["plan"]["coverage"]
        self.assertFalse(cov["gaps_truncated"])
        cov["gaps_truncated"] = True  # claims truncation although every gap is listed
        self.assertEqual(ev(e, o)["status"], "REJECTED")

    def test_case_7_a_malformed_sibling_in_outputs_is_never_ignored(self):
        for bad in ("garbage", None, 7, [], {}, {"name": "x"}, {"name": "a", "role": "video", "bytes": 1, "sha256": H("x"), "partial": False, "extra": 1}):
            for stage in ("audio_receipt", "video_receipt"):
                e, o = world()
                o["stages"][stage]["response"]["outputs"].append(bad)
                res = ev(e, o)
                self.expect(res, "REJECTED", ["SHAPE_OUTPUTS"], [stage.upper()])
        e, o = world()
        self.vr(o)["outputs"].append(dict(self.vr(o)["outputs"][0]))  # same role twice
        self.expect(ev(e, o), "REJECTED", ["SHAPE_OUTPUTS"], ["VIDEO_RECEIPT"])
        e, o = world()
        self.vr(o)["outputs"].append({"name": "x.bin", "role": "mystery", "bytes": 1, "sha256": H("m"), "partial": False})
        self.expect(ev(e, o), "HOLD", ["OUTPUT_ROLE_UNKNOWN"], ["VIDEO_RECEIPT"])
        e, o = world()
        self.vr(o)["outputs"] = self.vr(o)["outputs"] * 20  # 80 entries
        self.expect(ev(e, o), "REJECTED", ["SHAPE_OUTPUTS"], ["VIDEO_RECEIPT"])

    def test_cases_8_and_9_the_v0_profile_identity_cannot_carry_modified_policy(self):
        e, o = world(warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}])
        self.assertEqual(ev(e, o)["status"], "HOLD")
        res = ev(e, o, profile(treated_warning_codes=["ZERO_FILL_APPLIED"]))
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))
        e, o = world()
        del o["stages"]["audio_receipt"]
        res = ev(e, o, profile(required_stages=["audio_plan", "video_receipt", "avmux_manifest_view"], required_pins=["plan_sha256", "stem_sha256", "video_sha256"]))
        self.assertEqual((res["status"], codes(res)), ("REJECTED", {"PROFILE_POLICY_MODIFIED"}))
        res = ev(e, o)  # the real v0 profile: a missing required receipt
        self.assertEqual(res["status"], "HOLD")

    def test_case_10_unselected_unresolved_rows_stay_exact_and_non_blocking(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "u1", "state": "UNKNOWN", "selected": False}]
        e, o = world(rows)
        self.ready(ev(e, o))
        e, o = world(rows)
        self.ar(o)["unresolved_report"][0]["selected"] = 1
        self.expect(ev(e, o), "REJECTED", ["SHAPE_UNRESOLVED"], ["AUDIO_RECEIPT"])

    def test_the_corrected_controls_from_the_report(self):
        e, o = world()
        self.ready(ev(e, o))
        e, o = world()
        o["stages"]["audio_plan"]["identity"]["job_id"] = "other-job"
        self.expect(ev(e, o), "REJECTED", ["IDENTITY_MISMATCH"], ["IDENTITY"])
        e, o = world(warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}])
        self.expect(ev(e, o), "HOLD", ["WARNING_UNTREATED"], ["AUDIO_PLAN", "AUDIO_RECEIPT"])


class EnvelopeAndRowBoundaries(Base):
    """Boundaries found by mutation controls to be unprotected: stage / owner envelope strictness, unselected-row comparison, selected USE row fields, coverage arithmetic in isolation."""

    def test_unknown_fields_in_any_envelope_are_rejected(self):
        for stage in ("audio_plan", "audio_receipt", "video_receipt", "avmux_manifest_view"):
            e, o = world()
            o["stages"][stage]["smuggled"] = {"approved": True}
            res = ev(e, o)
            self.assertEqual((res["status"], "SHAPE_STAGE" in codes(res), outcome(res, "IDENTITY")), ("REJECTED", True, "FAIL"), stage)
        e, o = world()
        later = ORDER[ORDER.index("EXPECTED") + 1:]  # an unusable owner snapshot stops every later comparison, and the result says so
        e["approved_by"] = "someone"
        self.expect(ev(e, o), "REJECTED", ["SHAPE_EXPECTED"], ["EXPECTED"], skipped=later)
        e, o = world()
        e["rows"][0]["note"] = "x"
        self.expect(ev(e, o), "REJECTED", ["SHAPE_ROWS"], ["EXPECTED"], skipped=later)
        e, o = world()
        e["snapshot_ref"] = 5
        self.expect(ev(e, o), "REJECTED", ["SHAPE_EXPECTED"], ["EXPECTED"], skipped=later)

    def test_unselected_owner_rows_are_compared_but_never_become_blockers(self):
        rows = [{"id": "e1", "state": "USE", "selected": True, "binding": "bind-e1"}, {"id": "u1", "state": "UNKNOWN", "selected": False, "binding": "bind-u1"}]
        e, o = world(rows)
        self.ready(ev(e, o))
        e, o = world(rows)
        e["rows"][1]["state"] = "OMIT"  # the owner believes u1 is an unselected OMIT, the plan says UNKNOWN
        self.expect(ev(e, o), "REJECTED", ["ROW_STATE_MISMATCH"], ["SELECTION"])
        e, o = world(rows)
        e["rows"][1]["binding_ref"] = "bind-OTHER"
        self.expect(ev(e, o), "REJECTED", ["BINDING_MISMATCH"], ["SELECTION"])
        e, o = world(rows)
        e["rows"].append({"row_id": "ghost", "selected": False, "state": "UNKNOWN"})  # an owner row the plan never carried
        self.expect(ev(e, o), "REJECTED", ["ROW_MISSING_IN_PLAN"], ["SELECTION"])
        e, o = world(rows)
        del o["stages"]["audio_plan"]["response"]["plan"]["events"][1]["provenance_ref"]  # unselected: absence of a binding is not a blocker
        self.ready(ev(e, o))

    def test_selected_use_rows_must_carry_their_own_binding_fields(self):
        for field in ("asset", "source_in_sample", "source_out_sample", "gain", "channel_rule"):
            e, o = world()
            del o["stages"]["audio_plan"]["response"]["plan"]["events"][0][field]
            self.expect(ev(e, o), "HOLD", ["USE_ROW_INCOMPLETE"], ["SELECTION"])
        for field, bad in (("asset", 7), ("source_in_sample", True), ("source_in_sample", 4800), ("source_out_sample", 0), ("gain", {"num": 1}), ("gain", {"num": 1, "den": 0}),
                           ("gain", {"num": 1.0, "den": 1}), ("channel_rule", "MIX_DOWN")):
            e, o = world()
            o["stages"]["audio_plan"]["response"]["plan"]["events"][0][field] = bad
            self.expect(ev(e, o), "REJECTED", ["SHAPE_ROW"], ["SELECTION"])

    def test_coverage_listed_gaps_must_add_up_in_isolation(self):
        e, o = world(gap_count=2)  # two 100-sample gaps: covered must be 9400
        cov = o["stages"]["audio_plan"]["response"]["plan"]["coverage"]
        self.assertEqual(cov["covered_samples"], 9400)
        cov["covered_samples"] = 9450  # below total, gap_count > 0, but not what the listed gaps leave
        res = ev(e, o)
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("COVERAGE_INCONSISTENT", codes(res))
        self.assertIn("covered_samples plus the listed gaps", " ".join(r["detail"] for r in res["reasons"]))

    def test_receipt_all_ok_false_is_reported_at_the_flag_as_well_as_at_the_failed_check(self):
        e, o = world()
        r = o["stages"]["audio_receipt"]["response"]
        r["readback"]["checks"][4].update(observed=r["readback"]["checks"][4]["expected"] - 2, ok=False)
        r["readback"]["all_ok"] = False
        res = ev(e, o)
        self.assertEqual(codes(res), {"READBACK_NOT_OK"})
        self.assertTrue(any(x["path"].endswith("readback.all_ok") for x in res["reasons"]))
        self.assertTrue(any(x["path"].endswith("checks[file_bytes]") for x in res["reasons"]))


class BoundsAndJsonSafety(Base):
    def test_inputs_must_be_bounded_json_values(self):
        class Boom:
            def __eq__(self, other):
                raise RuntimeError("must never be called")
            __hash__ = None
        big = [[0] * 1000 for _ in range(150)]  # 150k values > the node bound
        deep = []
        for _ in range(40):
            deep = [deep]
        cyc = []
        cyc.append(cyc)
        cyc_d = {}
        cyc_d["x"] = cyc_d
        cases = {"depth": (deep, "INPUT_TOO_DEEP"), "cycle_list": (cyc, "INPUT_TOO_DEEP"), "cycle_dict": (cyc_d, "INPUT_TOO_DEEP"), "nodes": (big, "INPUT_TOO_LARGE"),
                 "long_list": ([0] * 5001, "INPUT_TOO_LARGE"), "long_string": ("x" * 4097, "INPUT_TOO_LARGE"), "lone_surrogate": ("\ud800", "SHAPE_NOT_JSON"),
                 "nan": (float("nan"), "SHAPE_NOT_JSON"), "inf": (float("inf"), "SHAPE_NOT_JSON"), "big_int": (2 ** 64, "SHAPE_NOT_JSON"), "tuple": ((1, 2), "SHAPE_NOT_JSON"),
                 "set": ({1}, "SHAPE_NOT_JSON"), "bytes": (b"x", "SHAPE_NOT_JSON"), "object": (Boom(), "SHAPE_NOT_JSON"), "int_key": ({1: "x"}, "SHAPE_NOT_JSON"),
                 "surrogate_key": ({"\ud800": 1}, "SHAPE_NOT_JSON")}
        for name, (value, code) in cases.items():
            for where in ("expected", "observed", "profile"):
                e, o = world()
                args = {"expected": e, "observed": o, "profile": copy.deepcopy(PROF)}
                (e if where == "expected" else o if where == "observed" else args["profile"])
                if where == "profile":
                    args["profile"]["review_status"] = value
                else:
                    args[where] = dict(args[where], injected=value)
                before = ca.evaluate(*world(), PROF)  # sanity: the fixture itself is fine
                self.assertEqual(before["status"], "READY")
                res = ca.evaluate(args["expected"], args["observed"], args["profile"])  # NOT through ev(): deepcopy of a cycle would itself recurse
                with self.subTest(case=name, where=where):
                    self.assertEqual(res["status"], "REJECTED")
                    self.assertIn(code, codes(res))
                    self.assertEqual(outcome(res, "BOUNDS"), "FAIL")
                    self.assertTrue(all(c["outcome"] == "SKIPPED" for c in res["checks"][1:]), "nothing is interpreted after a bounds failure")
                    json.dumps(res, allow_nan=False).encode("utf-8")  # JSON-safe and UTF-8 encodable

    def test_extreme_nesting_never_recurses(self):
        e, o = world()
        deep = []
        for _ in range(20000):
            deep = [deep]
        res = ca.evaluate(e, dict(o, injected=deep), PROF)
        self.assertEqual((res["status"], "INPUT_TOO_DEEP" in codes(res)), ("REJECTED", True))

    def test_output_is_always_json_safe_and_paths_are_bounded(self):
        e, o = world()
        o["stages"]["x" * 4000] = {"identity": dict(IDENT)}
        res = ev(e, o)
        self.assertEqual(res["status"], "HOLD")
        json.dumps(res, allow_nan=False).encode("utf-8")
        self.assertLess(max(len(r["path"]) for r in res["reasons"]), 200)
        e, o = world()
        o["stages"]["audio_plan"]["response"]["plan"]["events"][0]["state"] = "x" * 4000
        res = ev(e, o)
        json.dumps(res, allow_nan=False).encode("utf-8")
        self.assertLess(len(json.dumps(res)), 40000, "reasons never echo giant values")

    def test_result_does_not_depend_on_dict_insertion_order(self):
        def reorder(v):
            if isinstance(v, dict):
                return {k: reorder(v[k]) for k in reversed(list(v))}
            if isinstance(v, list):
                return [reorder(x) for x in v]
            return v
        rows = list(world.__defaults__[0]) + [{"id": "u1", "state": "UNKNOWN", "selected": False}]
        for mutate in (lambda e, o: None, lambda e, o: o["stages"]["audio_receipt"]["response"]["outputs"][0].update(bytes=5),
                       lambda e, o: o["stages"]["audio_plan"]["identity"].update(job_id="x")):
            e, o = world(rows)
            mutate(e, o)
            a = ca.evaluate(e, o, PROF)
            b = ca.evaluate(reorder(e), reorder(o), reorder(PROF))
            self.assertEqual(json.dumps(a, sort_keys=False), json.dumps(b, sort_keys=False))

    def test_a_ready_result_states_what_was_not_checked(self):
        res = ev(*world())
        self.ready(res)
        ids = {x["id"] for x in res["not_checked"]}
        self.assertTrue({"file_reality", "authenticity", "timing_qualification", "event_mix_detail", "video_tolerance_checks", "producer_diagnostics"} <= ids)
        for x in res["not_checked"]:
            self.assertTrue(x["what"] and x["why"])
        self.assertTrue(any("exact bounded v0 profile" in c for c in res["checked"]))
        self.assertEqual(ev(*world())["not_checked"], res["not_checked"])


class SeededCompositionFuzz(Base):
    def test_random_multi_leaf_mutations_never_crash_and_stay_deterministic_and_json_safe(self):
        """Seeded (reproducible) composition of 1-4 simultaneous leaf mutations across both inputs and the profile. Through ev(): no exception, no safety-net code, deterministic, sorted
        reasons, inputs untouched. Additionally every non-READY result carries at least one reason and a READY result carries none."""
        import random
        rng = random.Random(20261105)
        rows = list(world.__defaults__[0]) + [{"id": "u1", "state": "UNKNOWN", "selected": False}]
        base_e, base_o = world(rows)

        def leaves(node, path=()):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from leaves(v, path + (k,))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from leaves(v, path + (i,))
            else:
                yield path
        pool = [("expected", p) for p in leaves(base_e)] + [("observed", p) for p in leaves(base_o)]
        values = [None, True, False, 0, 1, -1, 2 ** 40, 1.5, "", "x", "a" * 64, H("fuzz"), "READY", "HOLD", "UNKNOWN", [], {}, [0], {"k": 1}]
        counts = {"READY": 0, "HOLD": 0, "REJECTED": 0}
        for _ in range(1200):
            e, o = copy.deepcopy(base_e), copy.deepcopy(base_o)
            for _m in range(rng.randint(1, 4)):
                which, path = rng.choice(pool)
                node = e if which == "expected" else o
                try:
                    for k in path[:-1]:
                        node = node[k]
                    node[path[-1]] = rng.choice(values)
                except (KeyError, IndexError, TypeError):
                    pass  # an earlier mutation replaced an ancestor
            res = ev(e, o)
            counts[res["status"]] += 1
            self.assertEqual(res["status"] == "READY", not res["reasons"])
            json.dumps(res, allow_nan=False).encode("utf-8")
        self.assertGreater(counts["REJECTED"], 100)
        self.assertGreater(counts["HOLD"], 20)


class PurityAndForgeryBoundary(Base):
    def test_module_is_pure_by_static_inspection(self):
        import ast
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "caller_admission.py"), encoding="utf-8") as fh:
            src = fh.read()
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
