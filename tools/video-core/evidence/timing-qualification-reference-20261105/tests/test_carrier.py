"""Tests of the GAP-06 carrier reference proposal (Python 3.11+ stdlib only).  Synthetic values only.
Run from the evidence directory:  python -B -m unittest discover -s tests -v      (TCR_MODULE_DIR selects another copy of the module; the mutation runner uses it)."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
import sys
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
MOD_DIR = os.environ.get("TCR_MODULE_DIR") or os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("timing_carrier_reference", os.path.join(MOD_DIR, "timing_carrier_reference.py"))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
compare = T.compare_carrier

SHA_A, SHA_B, SHA_C = ("a" * 64, "b" * 64, "c" * 64)
KIND = "PHRASE_BOUNDARY_OWNER_CHECKED"


def fixture():
    """Owner snapshot + carrier that match exactly.  r1/r3 selected USE, r2 selected INTENTIONAL_SILENCE, r4 unselected UNKNOWN, r5 selected OMIT, r6 unselected USE."""
    exp = {"snapshot_ref": "snap-0001", "snapshot_version": 3, "sample_rate": 48000, "rows": [
        {"row_id": "r1", "event_id": "e1", "selected": True, "state": "USE", "binding_ref": "bind-1", "source_sha256": SHA_A, "source_in_sample": 1000, "source_out_sample": 49000,
         "timeline_start_sample": 0, "timeline_end_sample": 48000, "qualification_revision": 2},
        {"row_id": "r2", "event_id": "e2", "selected": True, "state": "INTENTIONAL_SILENCE", "decision_ref": "owner-silence-1", "timeline_start_sample": 48000, "timeline_end_sample": 72000},
        {"row_id": "r3", "event_id": "e3", "selected": True, "state": "USE", "binding_ref": "bind-3", "source_sha256": SHA_B, "source_in_sample": 5000, "source_out_sample": 53000,
         "timeline_start_sample": 72000, "timeline_end_sample": 120000, "qualification_revision": 1},
        {"row_id": "r4", "event_id": "e4", "selected": False, "state": "UNKNOWN"},
        {"row_id": "r5", "event_id": "e5", "selected": True, "state": "OMIT"},
        {"row_id": "r6", "event_id": "e6", "selected": False, "state": "USE", "binding_ref": "bind-6", "source_sha256": SHA_C, "source_in_sample": 0, "source_out_sample": 10,
         "timeline_start_sample": 0, "timeline_end_sample": 10, "qualification_revision": 1}]}

    def rec(rid, eid, bind, sha, si, so, ts, te, ref, rev):
        return {"row_id": rid, "event_id": eid, "binding_ref": bind, "source_sha256": sha, "sample_rate": 48000, "source_in_sample": si, "source_out_sample": so, "timeline_start_sample": ts,
                "timeline_end_sample": te, "status": "QUALIFIED", "kind": KIND, "qualification_ref": ref, "qualification_revision": rev, "invalidation": "NONE"}
    obs = {"carrier_version": 1, "profile_id": T.PROFILE["profile_id"], "profile_version": 1, "profile_content_sha256": T.PROFILE_SHA256, "snapshot_ref": "snap-0001", "snapshot_version": 3,
           "records": [rec("r1", "e1", "bind-1", SHA_A, 1000, 49000, 0, 48000, "tq:syn-1", 2), rec("r3", "e3", "bind-3", SHA_B, 5000, 53000, 72000, 120000, "tq:syn-3", 1)],
           "silence_decisions": [{"row_id": "r2", "decision_ref": "owner-silence-1"}]}
    return exp, obs


def rec_of(obs, rid):
    return next(r for r in obs["records"] if r["row_id"] == rid)


def row_of(exp, rid):
    return next(r for r in exp["rows"] if r["row_id"] == rid)


def comp(res, row, name):
    return [c for c in res["comparisons"] if c["row_id"] == row and c["comparison"] == name]


def codes(res):
    return sorted({r["code"] for r in res["reasons"]})


class Controls(unittest.TestCase):
    def test_exact_match_is_consistent_assertions_only(self):
        res = compare(*fixture())
        self.assertEqual(res["verdict"], "CONSISTENT_ASSERTIONS_ONLY", res["reasons"])
        self.assertEqual(res["reasons"], [])
        self.assertEqual(res["stages_reached"], ["PROFILE", "STRUCTURE", "CARRIER", "CROSS_RECORD", "ROW_COMPARISON"])
        self.assertEqual({r["row_id"]: r["outcome"] for r in res["row_outcomes"]},
                         {"r1": "CLAIMS_CONSISTENT", "r2": "CLAIMS_CONSISTENT", "r3": "CLAIMS_CONSISTENT", "r4": "CONTRIBUTES_NOTHING", "r5": "CONTRIBUTES_NOTHING", "r6": "CONTRIBUTES_NOTHING"})
        for f in ("event_id", "binding_ref", "source_sha256", "sample_rate", "source_in_sample", "source_out_sample", "timeline_start_sample", "timeline_end_sample", "status_qualified", "qualification_revision"):
            self.assertEqual([c["outcome"] for c in comp(res, "r1", f)], ["MATCH"], f)

    def test_unselected_unknown_and_selected_omit_do_not_block_and_add_nothing(self):
        exp, obs = fixture()
        res = compare(exp, obs)
        self.assertEqual(res["verdict"], "CONSISTENT_ASSERTIONS_ONLY")
        self.assertEqual(comp(res, "r4", "selection_meaning")[0]["outcome"], "NOT_APPLICABLE")
        self.assertEqual(comp(res, "r5", "selection_meaning")[0]["outcome"], "NOT_APPLICABLE")
        self.assertEqual(comp(res, None, "effective_scope_non_empty")[0]["observed"], 3)        # r1, r2, r3 only: no implied silence for r4/r5
        self.assertEqual([c for c in res["comparisons"] if c["row_id"] in ("r4", "r5", "r6") and c["outcome"] != "NOT_APPLICABLE"], [])

    def test_forged_self_consistent_claims_are_accepted_documented_limit(self):
        exp, _ = fixture()
        forged = fixture()[1]                                 # built from the owner's own pins; nothing was heard, measured or issued
        res = compare(exp, forged)
        self.assertEqual(res["verdict"], "CONSISTENT_ASSERTIONS_ONLY")
        self.assertTrue(any("forged" in s for s in res["limitations"]))
        self.assertIn("speech suitability", res["not_asserted"])

    def test_no_ready_or_execution_fields_anywhere(self):
        def walk(o, keys, vals):
            if isinstance(o, dict):
                for k, v in o.items():
                    keys.add(k)
                    walk(v, keys, vals)
            elif isinstance(o, list):
                for v in o:
                    walk(v, keys, vals)
            elif isinstance(o, str):
                vals.add(o)
        for mutate in (None, lambda e, o: o["records"].clear()):
            exp, obs = fixture()
            if mutate:
                mutate(exp, obs)
            res = compare(exp, obs)
            keys, vals = set(), set()
            walk({k: v for k, v in res.items() if k not in ("not_asserted", "limitations")}, keys, vals)      # those two lists NEGATE readiness/approval by design
            self.assertFalse({k for k in keys if any(b in k.lower() for b in ("ready", "execut", "approv", "permission", "granted"))}, keys)
            self.assertFalse({v for v in vals if v.upper() in ("READY", "APPROVED", "EXECUTE", "GRANTED")})
            self.assertEqual(set(res), {"result_schema", "verdict", "primary_reason", "profile", "stages_reached", "reasons", "comparisons", "row_outcomes", "counts", "limitations", "not_asserted"})
            self.assertIn(res["verdict"], ("CONSISTENT_ASSERTIONS_ONLY", "HOLD", "REJECTED"))


class Case:
    def __init__(self, name, mutate, verdict, code, row, comparison, outcome="MISMATCH", stages=None):
        self.name, self.mutate, self.verdict, self.code, self.row, self.comparison, self.outcome = name, mutate, verdict, code, row, comparison, outcome


def shift(rid, fields, by=1):
    def f(e, o):
        for k in fields:
            rec_of(o, rid)[k] += by
    return f


NEG = [
    # --- shifted boundary with the SAME duration (must not inherit a qualification just because the length matches)
    Case("timeline shifted by 1 sample, same duration", lambda e, o: shift("r1", ("timeline_start_sample", "timeline_end_sample"))(e, o), "REJECTED", "PIN_MISMATCH", "r1", "timeline_start_sample"),
    Case("timeline shifted by 1 (end side reached)", lambda e, o: shift("r1", ("timeline_start_sample", "timeline_end_sample"))(e, o), "REJECTED", "PIN_MISMATCH", "r1", "timeline_end_sample"),
    Case("source shifted by 1 sample, same duration", lambda e, o: shift("r3", ("source_in_sample", "source_out_sample"))(e, o), "REJECTED", "PIN_MISMATCH", "r3", "source_in_sample"),
    Case("source shifted by 1 (out side reached)", lambda e, o: shift("r3", ("source_in_sample", "source_out_sample"))(e, o), "REJECTED", "PIN_MISMATCH", "r3", "source_out_sample"),
    Case("owner snapshot moved by 1, record not (stale claim)", lambda e, o: shift_expected(e), "REJECTED", "PIN_MISMATCH", "r1", "timeline_start_sample"),
    # --- stale / different pins
    Case("sample rate 44100", lambda e, o: rec_of(o, "r1").__setitem__("sample_rate", 44100), "REJECTED", "PIN_MISMATCH", "r1", "sample_rate"),
    Case("source sha256 changed", lambda e, o: rec_of(o, "r1").__setitem__("source_sha256", SHA_C), "REJECTED", "PIN_MISMATCH", "r1", "source_sha256"),
    Case("binding_ref changed", lambda e, o: rec_of(o, "r3").__setitem__("binding_ref", "bind-other"), "REJECTED", "PIN_MISMATCH", "r3", "binding_ref"),
    Case("event_id changed", lambda e, o: rec_of(o, "r3").__setitem__("event_id", "e-other"), "REJECTED", "PIN_MISMATCH", "r3", "event_id"),
    Case("stale snapshot_ref", lambda e, o: o.__setitem__("snapshot_ref", "snap-0000"), "REJECTED", "STALE_SNAPSHOT", None, "snapshot_ref"),
    Case("stale snapshot_version", lambda e, o: o.__setitem__("snapshot_version", 2), "REJECTED", "STALE_SNAPSHOT", None, "snapshot_version"),
    Case("carrier_version 2", lambda e, o: o.__setitem__("carrier_version", 2), "REJECTED", "CARRIER_VERSION", None, "carrier_version"),
    Case("profile_version 2", lambda e, o: o.__setitem__("profile_version", 2), "REJECTED", "PROFILE_IDENTITY", None, "profile_version"),
    Case("profile_id other", lambda e, o: o.__setitem__("profile_id", "other-profile"), "REJECTED", "PROFILE_IDENTITY", None, "profile_id"),
    Case("profile content hash other", lambda e, o: o.__setitem__("profile_content_sha256", "0" * 64), "REJECTED", "PROFILE_CONTENT_MISMATCH", None, "profile_content_sha256"),
    # --- HOLD: missing / unknown / unqualified / stale / invalidated
    Case("record missing for a selected USE", lambda e, o: o["records"].remove(rec_of(o, "r3")), "HOLD", "MISSING_RECORD", "r3", "record_present", "MISSING"),
    Case("status UNKNOWN", lambda e, o: rec_of(o, "r1").update(status="UNKNOWN", kind="NONE", qualification_ref="NONE"), "HOLD", "NOT_QUALIFIED", "r1", "status_qualified"),
    Case("status UNQUALIFIED", lambda e, o: rec_of(o, "r1").update(status="UNQUALIFIED"), "HOLD", "NOT_QUALIFIED", "r1", "status_qualified"),
    Case("qualification_revision stale", lambda e, o: row_of(e, "r1").__setitem__("qualification_revision", 3), "HOLD", "REVISION_STALE", "r1", "qualification_revision"),
    Case("claim invalidated by the supplied owner flag", lambda e, o: rec_of(o, "r3").__setitem__("invalidation", "INVALIDATED"), "HOLD", "INVALIDATED", "r3", "not_invalidated"),
    Case("selected UNKNOWN row", lambda e, o: row_of(e, "r4").__setitem__("selected", True), "HOLD", "SELECTED_UNRESOLVED", "r4", "selected_state_resolved"),
    Case("selected MISSING row", lambda e, o: row_of(e, "r4").update(selected=True, state="MISSING"), "HOLD", "SELECTED_UNRESOLVED", "r4", "selected_state_resolved"),
    Case("selected PROXY row", lambda e, o: row_of(e, "r4").update(selected=True, state="PROXY"), "HOLD", "SELECTED_UNRESOLVED", "r4", "selected_state_resolved"),
    Case("selected HOLD row", lambda e, o: row_of(e, "r4").update(selected=True, state="HOLD"), "HOLD", "SELECTED_UNRESOLVED", "r4", "selected_state_resolved"),
    Case("silence decision absent (no silence from absence)", lambda e, o: o["silence_decisions"].clear(), "HOLD", "MISSING_SILENCE_DECISION", "r2", "silence_decision_present", "MISSING"),
    Case("empty effective scope: nothing selected", lambda e, o: ([row_of(e, x).__setitem__("selected", False) for x in ("r1", "r2", "r3", "r5")], o["records"].clear(), o["silence_decisions"].clear()), "HOLD", "EMPTY_EFFECTIVE_SCOPE", None, "effective_scope_non_empty"),
    Case("empty effective scope: only selected OMIT", lambda e, o: ([row_of(e, x).__setitem__("selected", False) for x in ("r1", "r2", "r3")], o["records"].clear(), o["silence_decisions"].clear()), "HOLD", "EMPTY_EFFECTIVE_SCOPE", None, "effective_scope_non_empty"),
    # --- contradictions
    Case("different silence decision_ref", lambda e, o: o["silence_decisions"][0].__setitem__("decision_ref", "owner-silence-2"), "REJECTED", "SILENCE_DECISION_MISMATCH", "r2", "silence_decision_ref"),
    Case("invented phrase qualification for a silence row", lambda e, o: o["records"].append(dict(rec_of(o, "r1"), row_id="r2", event_id="e2")), "REJECTED", "RECORD_FOR_NON_USE_ROW", "r2", "record_only_for_selected_use"),
    Case("record for a selected OMIT row", lambda e, o: o["records"].append(dict(rec_of(o, "r1"), row_id="r5", event_id="e5")), "REJECTED", "RECORD_FOR_NON_USE_ROW", "r5", "record_only_for_selected_use"),
    Case("record for an unselected USE row", lambda e, o: o["records"].append(dict(rec_of(o, "r1"), row_id="r6", event_id="e6")), "REJECTED", "RECORD_FOR_NON_USE_ROW", "r6", "record_only_for_selected_use"),
    Case("record for an unknown row", lambda e, o: o["records"].append(dict(rec_of(o, "r1"), row_id="r99", event_id="e99")), "REJECTED", "RECORD_UNKNOWN_ROW", None, "record_row_exists_in_snapshot"),
    Case("duplicate record for one row", lambda e, o: o["records"].append(copy.deepcopy(rec_of(o, "r1"))), "REJECTED", "DUPLICATE_RECORD", "r1", "single_record_per_row"),
    Case("duplicate silence decision", lambda e, o: o["silence_decisions"].append(copy.deepcopy(o["silence_decisions"][0])), "REJECTED", "DUPLICATE_DECISION", "r2", "single_decision_per_row"),
    Case("decision for a non-silence row", lambda e, o: o["silence_decisions"].append({"row_id": "r1", "decision_ref": "owner-silence-9"}), "REJECTED", "DECISION_FOR_NON_SILENCE_ROW", "r1", "decision_only_for_selected_silence"),
]


def shift_expected(e):
    r = row_of(e, "r1")
    r["timeline_start_sample"] += 1
    r["timeline_end_sample"] += 1


class Negatives(unittest.TestCase):
    def test_each_negative_reaches_its_intended_comparison_and_nothing_earlier(self):
        for c in NEG:
            with self.subTest(c.name):
                exp, obs = fixture()
                c.mutate(exp, obs)
                res = compare(exp, obs)
                self.assertEqual(res["verdict"], c.verdict, res["reasons"])
                # not an earlier invalid fixture: structure and profile were passed and the row-comparison stage ran
                self.assertTrue({"PROFILE", "STRUCTURE", "CARRIER", "CROSS_RECORD", "ROW_COMPARISON"} <= set(res["stages_reached"]), res["stages_reached"])
                self.assertFalse([r for r in res["reasons"] if r["stage"] in ("PROFILE", "STRUCTURE")])
                self.assertIn(c.code, codes(res))
                hit = comp(res, c.row, c.comparison)
                self.assertTrue(hit, f"comparison {c.comparison} for {c.row} was not reached")
                self.assertTrue(all(h["reached"] for h in hit))
                self.assertIn(c.outcome, [h["outcome"] for h in hit])

    def test_shift_by_one_sample_changes_only_the_shifted_pins(self):
        exp, obs = fixture()
        shift("r1", ("timeline_start_sample", "timeline_end_sample"))(exp, obs)
        res = compare(exp, obs)
        out = {c["comparison"]: c["outcome"] for c in res["comparisons"] if c["row_id"] == "r1"}
        self.assertEqual(out["timeline_start_sample"], "MISMATCH")
        self.assertEqual(out["timeline_end_sample"], "MISMATCH")
        for f in ("source_in_sample", "source_out_sample", "source_sha256", "binding_ref", "event_id", "sample_rate"):
            self.assertEqual(out[f], "MATCH", f)                      # the DURATION is unchanged and still not enough
        self.assertEqual(rec_of(obs, "r1")["timeline_end_sample"] - rec_of(obs, "r1")["timeline_start_sample"], 48000)
        self.assertEqual({r["row_id"]: r["outcome"] for r in res["row_outcomes"]}["r1"], "REJECTED")

    def test_a_requalified_source_needs_new_pins_not_the_old_ref(self):
        exp, obs = fixture()
        row_of(exp, "r1")["source_sha256"] = SHA_C                     # owner re-pinned the source; the carrier still claims the old sha under the old ref
        res = compare(exp, obs)
        self.assertEqual(res["verdict"], "REJECTED")
        self.assertEqual(comp(res, "r1", "source_sha256")[0]["outcome"], "MISMATCH")
        self.assertEqual(comp(res, "r1", "status_qualified")[0]["outcome"], "MATCH")     # the old QUALIFIED string alone is not credited

    def test_precedence_rejected_over_hold_and_deterministic(self):
        exp, obs = fixture()
        rec_of(obs, "r1")["source_sha256"] = SHA_C
        obs["records"].remove(rec_of(obs, "r3"))
        res = compare(exp, obs)
        self.assertEqual(res["verdict"], "REJECTED")
        self.assertEqual(res["primary_reason"], "PIN_MISMATCH")
        self.assertIn("MISSING_RECORD", codes(res))
        exp2, obs2 = fixture()
        rec_of(obs2, "r1")["source_sha256"] = SHA_C
        obs2["records"].remove(rec_of(obs2, "r3"))
        self.assertEqual(json.dumps(compare(exp2, obs2), sort_keys=False), json.dumps(res, sort_keys=False))


def set_path(root, path, value):
    cur = root
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


MALFORMED = [
    ("bool as sample", ("o", ["records", 0, "source_in_sample"], True)), ("float sample", ("o", ["records", 0, "source_in_sample"], 1000.0)),
    ("string sample", ("o", ["records", 0, "source_in_sample"], "1000")), ("negative sample", ("o", ["records", 0, "source_in_sample"], -1)),
    ("oversized int", ("o", ["records", 0, "source_in_sample"], 2 ** 41)), ("float revision", ("o", ["records", 0, "qualification_revision"], 2.0)),
    ("bool revision", ("o", ["records", 0, "qualification_revision"], True)), ("bool snapshot_version", ("o", ["snapshot_version"], True)),
    ("float carrier_version", ("o", ["carrier_version"], 1.0)), ("NaN", ("o", ["records", 0, "source_in_sample"], float("nan"))),
    ("status missing value", ("o", ["records", 0, "status"], None)), ("status lowercase", ("o", ["records", 0, "status"], "qualified")),
    ("status READY", ("o", ["records", 0, "status"], "READY")), ("kind outside vocabulary", ("o", ["records", 0, "kind"], "AUTO")),
    ("kind empty", ("o", ["records", 0, "kind"], "")), ("ref bad syntax", ("o", ["records", 0, "qualification_ref"], "free text with spaces")),
    ("ref empty", ("o", ["records", 0, "qualification_ref"], "")), ("QUALIFIED with kind NONE", ("o", ["records", 0, "kind"], "NONE")),
    ("QUALIFIED with ref NONE", ("o", ["records", 0, "qualification_ref"], "NONE")), ("invalidation unknown value", ("o", ["records", 0, "invalidation"], "MAYBE")),
    ("sha uppercase", ("o", ["records", 0, "source_sha256"], "A" * 64)), ("sha short", ("o", ["records", 0, "source_sha256"], "a" * 63)),
    ("record span mismatch", ("o", ["records", 0, "source_out_sample"], 49001)), ("record zero span", ("o", ["records", 0, "timeline_end_sample"], 0)),
    ("records not a list", ("o", ["records"], {})), ("oversized string", ("o", ["records", 0, "binding_ref"], "x" * 129)),
    ("lone surrogate string", ("o", ["records", 0, "binding_ref"], "bind\ud800")), ("control char in ref", ("o", ["records", 0, "binding_ref"], "bind\n1")),
    ("expected sample_rate 44100", ("e", ["sample_rate"], 44100)), ("expected sample_rate bool", ("e", ["sample_rate"], True)),
    ("expected state null", ("e", ["rows", 3, "state"], None)), ("expected state unknown word", ("e", ["rows", 3, "state"], "APPROVED")),
    ("expected selected as int", ("e", ["rows", 0, "selected"], 1)), ("expected float boundary", ("e", ["rows", 0, "timeline_end_sample"], 48000.0)),
    ("expected span mismatch", ("e", ["rows", 0, "source_out_sample"], 49001)), ("expected rows not a list", ("e", ["rows"], "rows")),
    ("expected duplicate row_id", ("e", ["rows", 5, "row_id"], "r1")), ("expected duplicate event_id", ("e", ["rows", 5, "event_id"], "e1")),
    ("top-level expected is a list", ("e", [], [])), ("observed is None", ("o", [], None)),
]


class Malformed(unittest.TestCase):
    def test_malformed_shapes_are_rejected_before_any_row_comparison(self):
        for name, (which, path, value) in MALFORMED:
            with self.subTest(name):
                exp, obs = fixture()
                if not path:
                    exp, obs = (value, obs) if which == "e" else (exp, value)
                else:
                    set_path(exp if which == "e" else obs, path, value)
                res = compare(exp, obs)
                self.assertEqual(res["verdict"], "REJECTED")
                self.assertNotIn("ROW_COMPARISON", res["stages_reached"], name)      # rejected at structure: no later comparison could have produced the verdict
                self.assertTrue(all(r["stage"] in ("STRUCTURE", "PROFILE") for r in res["reasons"]))
                self.assertEqual(res["comparisons"][-1]["comparison"], "profile_content_sha256_equals_pin")     # only the profile pin comparison ran before the rejection

    def test_missing_and_extra_keys(self):
        for which, mutate in (("o", lambda o: o["records"][0].pop("kind")), ("o", lambda o: o["records"][0].pop("qualification_ref")), ("o", lambda o: o["records"][0].pop("status")),
                              ("o", lambda o: o["records"][0].update(approved_by="x")), ("o", lambda o: o.update(execution_permission=True)), ("o", lambda o: o.pop("silence_decisions")),
                              ("e", lambda e: e.update(ready=True)), ("e", lambda e: e["rows"][0].update(creative_approval="ok")), ("e", lambda e: e["rows"][0].pop("binding_ref")),
                              ("e", lambda e: e["rows"][2].pop("source_sha256")), ("e", lambda e: e["rows"][1].pop("decision_ref"))):
            exp, obs = fixture()
            mutate(obs if which == "o" else exp)
            res = compare(exp, obs)
            self.assertEqual(res["verdict"], "REJECTED")
            self.assertNotIn("ROW_COMPARISON", res["stages_reached"])

    def test_subclasses_cycles_depth_and_size_limits(self):
        class D(dict):
            pass

        class L(list):
            pass
        exp, obs = fixture()
        cyc = []
        cyc.append(cyc)
        deep = cur = []
        for _ in range(200):
            nxt = []
            cur.append(nxt)
            cur = nxt
        big = fixture()[0]
        big["rows"] = [{"row_id": f"x{i}", "event_id": f"y{i}", "selected": False, "state": "UNKNOWN"} for i in range(T.LIM["max_rows"] + 1)]
        ok256 = fixture()[0]
        ok256["rows"] += [{"row_id": f"x{i}", "event_id": f"y{i}", "selected": False, "state": "UNKNOWN"} for i in range(250)]
        many = fixture()[1]
        many["records"] = [rec_of(fixture()[1], "r1")] * (T.LIM["max_records"] + 1)
        wide = fixture()[1]
        wide["records"] = [{"k": [0] * 30} for _ in range(900)]
        cases = [("dict subclass", D(exp), obs), ("list subclass rows", dict(exp, rows=L(exp["rows"])), obs), ("cyclic list", dict(exp, rows=cyc), obs), ("depth 200", dict(exp, rows=deep), obs),
                 ("257 rows", big, obs), ("257 records", exp, many), ("node bomb", exp, wide), ("tuple instead of list", dict(exp, rows=tuple(exp["rows"])), obs), ("set value", dict(exp, rows=[{"row_id": {1}}]), obs),
                 ("bytes value", dict(exp, snapshot_ref=b"x"), obs), ("object value", dict(exp, snapshot_ref=object()), obs)]
        for name, e, o in cases:
            with self.subTest(name):
                res = compare(e, o)
                self.assertEqual(res["verdict"], "REJECTED")
                self.assertNotIn("ROW_COMPARISON", res["stages_reached"])
                json.dumps(res, allow_nan=False)
        res = compare(ok256, obs)                                              # 256 rows is within the limit and must not be rejected for size
        self.assertEqual(res["verdict"], "CONSISTENT_ASSERTIONS_ONLY")

    def test_profile_relaxed_under_the_same_identity_is_rejected(self):
        for name, mutate in (("add a kind", lambda p: p["kinds_proposed"].append("AUTO")), ("drop QUALIFIED requirement", lambda p: p["statuses"].remove("UNKNOWN")),
                             ("raise limit", lambda p: p["limits"].__setitem__("max_rows", 10 ** 6)), ("add a sample rate", lambda p: p["sample_rates"].append(44100)),
                             ("edit a rule", lambda p: p["proposed_rules"].__setitem__(0, "P-3 relaxed"))):
            with self.subTest(name):
                prof = copy.deepcopy(T.PROFILE)
                mutate(prof)
                self.assertEqual((prof["profile_id"], prof["profile_version"]), (T.PROFILE["profile_id"], T.PROFILE["profile_version"]))
                res = compare(*fixture(), profile=prof)
                self.assertEqual(res["verdict"], "REJECTED")
                self.assertEqual(codes(res), ["PROFILE_CONTENT_MISMATCH"])
                self.assertEqual(res["stages_reached"], ["PROFILE"])
        self.assertEqual(compare(*fixture(), profile=copy.deepcopy(T.PROFILE))["verdict"], "CONSISTENT_ASSERTIONS_ONLY")
        self.assertEqual(compare(*fixture(), profile="x")["verdict"], "REJECTED")

    def test_profile_pin_is_a_literal_and_equals_the_canonical_content(self):
        canon = json.dumps(T.PROFILE, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        self.assertEqual(hashlib.sha256(canon.encode("ascii")).hexdigest(), T.PINNED_PROFILE_SHA256)
        self.assertEqual(T.PINNED_PROFILE_SHA256, "1748bbff0ac869b546a66bc9263065787ea45a3b1f3aabbb2a43a83446e506dc")
        self.assertEqual(T.PROFILE["review_status"], "UNREVIEWED_PROPOSAL")


class Purity(unittest.TestCase):
    def test_inputs_are_not_mutated_and_results_are_deterministic_and_json_safe(self):
        for mutate in (None, lambda e, o: shift("r1", ("timeline_start_sample", "timeline_end_sample"))(e, o), lambda e, o: o["records"].clear()):
            exp, obs = fixture()
            if mutate:
                mutate(exp, obs)
            e0, o0 = copy.deepcopy(exp), copy.deepcopy(obs)
            r1, r2 = compare(exp, obs), compare(exp, obs)
            self.assertEqual((exp, obs), (e0, o0))
            self.assertEqual(json.dumps(r1), json.dumps(r2))
            json.dumps(r1, allow_nan=False)
            self.assertIsNot(r1["limitations"], T.LIMITATIONS)
            r1["limitations"].append("x")
            self.assertNotIn("x", compare(exp, obs)["limitations"])

    def test_input_order_independence(self):
        exp, obs = fixture()
        exp2, obs2 = copy.deepcopy(exp), copy.deepcopy(obs)
        obs2["records"].reverse()
        obs2 = {k: obs2[k] for k in reversed(list(obs2))}
        obs2["records"] = [{k: r[k] for k in reversed(list(r))} for r in obs2["records"]]
        self.assertEqual(json.dumps(compare(exp, obs)), json.dumps(compare(exp2, obs2)))
        exp3, obs3 = fixture()
        obs3["records"].reverse()
        rec_of(obs3, "r1")["qualification_revision"] = 9
        a = compare(exp3, obs3)
        exp4, obs4 = fixture()
        rec_of(obs4, "r1")["qualification_revision"] = 9
        self.assertEqual(json.dumps(a), json.dumps(compare(exp4, obs4)))

    def test_no_forbidden_imports_and_no_forbidden_runtime_events(self):
        with open(os.path.join(MOD_DIR, "timing_carrier_reference.py"), encoding="utf-8") as fh:
            src = fh.read()
        imported = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else n.module.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))}
        self.assertEqual(imported, {"__future__", "hashlib", "json", "re"})
        names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
        self.assertFalse(names & {"open", "os", "time", "datetime", "random", "socket", "subprocess", "environ", "input", "print", "sys", "exec", "eval", "__import__", "getattr", "setattr", "globals"}, names)
        events = []

        def hook(event, args):
            if getattr(Purity, "_hooking", False) and (event.startswith(("subprocess", "socket", "os.system", "os.exec", "os.spawn", "os.putenv", "os.mkdir", "os.remove", "os.rename", "urllib", "shutil", "tempfile"))
                                                        or (event == "open" and any(c in str(args[1]) for c in "wax+"))):
                events.append(event)
        sys.addaudithook(hook)
        Purity._hooking = True
        try:
            for _ in range(3):
                compare(*fixture())
        finally:
            Purity._hooking = False
        self.assertEqual(events, [])

    def test_fuzzy_inputs_never_raise(self):
        weird = [None, 0, 1.5, "x", [], {}, [[]], {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}, True, b"x", (1,), {1: 2}, {"snapshot_ref": "s"}, float("inf")]
        for e in weird:
            for o in weird:
                res = compare(e, o)
                self.assertEqual(res["verdict"], "REJECTED")
                json.dumps(res, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
