"""Integration tests of the deny-first admission, --only selection and the disposable, intentionally failing deny control (real bounded children; ~1 minute).
Library under test: $VCC_LIBRARY_ROOT or tools/video-core of this checkout (skipped with the reason if absent). Scratch: $VCC_TMP or the system temp."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import portable_harness  # noqa: E402
import portable_selftest  # noqa: E402

def jl(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


LIB = os.environ.get("VCC_LIBRARY_ROOT") or os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def failing_admission(ctx):
    """DISPOSABLE and INTENTIONALLY FAILING deny control. It performs NO prohibited action: its only child READS a file the harness created in the owned world, then the control asserts
    (wrongly, on purpose) that this read was denied. Reads are never denied, so the control fails - exactly the shape of a deny control that does not hold."""
    w = ctx.run.new_world("adm-fail")
    marker = os.path.join(w.docs, "harmless.txt")
    with open(marker, "wb") as fh:
        fh.write(b"readable")
    child = portable_selftest.control_job(ctx.run, w, "read_is_allowed", marker)
    c = (child.log or {}).get("control") or {}
    ctx.control("INTENTIONAL FAILURE: claim that reading an owned file is denied", True, bool(c.get("denied_by_hook")))


failing_admission.case_id, failing_admission.title, failing_admission.refs = "MSX_DISPOSABLE_FAILING_DENY_CONTROL", "disposable intentionally failing deny control (test only)", []


@unittest.skipUnless(os.path.isfile(os.path.join(LIB, "video_core", "__init__.py")), "library root with video_core/ not found (set VCC_LIBRARY_ROOT)")
class Admission(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="vcc-r2-adm-", dir=os.environ.get("VCC_TMP") or None)
        self.addCleanup(shutil.rmtree, base, True)
        self.scr = os.path.join(base, "scratch")
        os.mkdir(self.scr)
        self.base, self.n = base, 0

    def run_main(self, *extra, admission_cases=None):
        self.n += 1
        out = os.path.join(self.base, f"out{self.n}")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = portable_harness.main(["--library-root", LIB, "--scratch-root", self.scr, "--out-dir", out, "--strict", *extra], admission_cases=admission_cases)
        return code, out, buf.getvalue()

    @staticmethod
    def records(out):
        d = os.path.join(out, "invocations")
        return [jl(os.path.join(d, x, "status.json")) for x in sorted(os.listdir(d))] if os.path.isdir(d) else []

    def test_mechanism_test_can_be_selected_explicitly_and_admission_runs_first(self):
        code, out, text = self.run_main("--only", "MS01_deny_controls_prove_the_hook_is_active")
        run = jl(os.path.join(out, "run.json"))
        self.assertEqual(code, 0, text)
        self.assertEqual(run["admission"]["statuses"], ["PASS"])
        self.assertEqual(run["admission"]["children"], run["bounds"]["children_started"], "selecting MS01 runs it once, as the admission, and nothing else")
        self.assertEqual({r["group"] for r in self.records(out)}, {"admission"})
        self.assertEqual([c["case"] for c in [jl(os.path.join(out, "cases", f)) for f in os.listdir(os.path.join(out, "cases"))]], ["MS01_deny_controls_prove_the_hook_is_active"])

    def test_admission_precedes_every_contract_case_and_other_mechanism_tests_can_be_selected(self):
        code, out, text = self.run_main("--only", "PC00_fixtures_independent_check,MS02_execution_flags_and_inspect_refused_before_any_child")
        self.assertEqual(code, 0, text)
        recs = self.records(out)
        groups = [r["group"] for r in recs]
        self.assertEqual(groups[0], "admission")
        first_contract = groups.index("contract") if "contract" in groups else len(groups)
        self.assertTrue(all(g == "admission" for g in groups[:first_contract]) and first_contract >= 1)
        self.assertTrue(all(g != "admission" for g in groups[first_contract:]))
        cases = sorted(f[:-5] for f in os.listdir(os.path.join(out, "cases")))
        self.assertEqual(cases, ["MS01_deny_controls_prove_the_hook_is_active", "MS02_execution_flags_and_inspect_refused_before_any_child", "PC00_fixtures_independent_check"])

    def test_unknown_ids_are_refused_before_any_child(self):
        code, out, _ = self.run_main("--only", "PC99_nope")
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(out))

    def test_a_failing_deny_control_stops_everything_that_follows(self):
        code, out, text = self.run_main("--only", "PC00_fixtures_independent_check,PC01_validation_vs_execution", admission_cases=[failing_admission])
        run = jl(os.path.join(out, "run.json"))
        self.assertEqual(code, 5, text)
        self.assertEqual(run["exit_code"], 5)
        self.assertEqual(run["admission"]["stopped"]["stage"], "admission")
        self.assertEqual(run["admission"]["stopped"]["failed"], ["MSX_DISPOSABLE_FAILING_DENY_CONTROL"])
        self.assertEqual(run["bounds"]["children_started"], 1, "exactly the failing control's one child ran; no contract case, no mechanism test, no mutation control")
        self.assertEqual(run["driver_accounting"]["launches_by_group"], {"admission": 1})
        self.assertEqual([r["group"] for r in self.records(out)], ["admission"])
        self.assertEqual(sorted(os.listdir(os.path.join(out, "cases"))), ["MSX_DISPOSABLE_FAILING_DENY_CONTROL.json"])
        self.assertTrue(os.path.isfile(os.path.join(out, "STOP.txt")))
        self.assertEqual(run["mutation_controls"], [])
        self.assertEqual(run["summary"]["cases_by_status"], {"FAIL": 1})

    def test_the_failing_control_never_permitted_a_prohibited_action(self):
        code, out, _ = self.run_main(admission_cases=[failing_admission])
        run = jl(os.path.join(out, "run.json"))
        case = jl(os.path.join(out, "cases", "MSX_DISPOSABLE_FAILING_DENY_CONTROL.json"))
        self.assertEqual(code, 5)
        self.assertEqual(run["bounds"]["children_started"], 1)
        self.assertEqual([c["outcome"] for c in case["checks"]], ["CONTROL_FAILED"])
        # the control did not request, attempt or allow anything forbidden: its single child is a READ control (denied attempts 0, no process/network/write events)
        rec = self.records(out)[0]
        self.assertEqual((rec["denied_attempts"], rec["status"], rec["exit_code"]), (0, "OK", 0))
        self.assertTrue(run["source"]["unchanged_during_run"])
        self.assertTrue(run["scratch_cleaned"])
        self.assertEqual(os.listdir(self.scr), [], "the owned scratch directory was removed; nothing was left behind")

    def test_a_passing_custom_admission_lets_later_work_run(self):
        def passing(ctx):
            ctx.control("harmless control that holds", 1, 1)
        passing.case_id, passing.title, passing.refs = "MSX_DISPOSABLE_PASSING", "t", []
        code, out, text = self.run_main("--only", "PC00_fixtures_independent_check", admission_cases=[passing])
        run = jl(os.path.join(out, "run.json"))
        self.assertEqual(code, 0, text)
        self.assertEqual(run["admission"]["statuses"], ["PASS"])
        self.assertIn("PC00_fixtures_independent_check.json", os.listdir(os.path.join(out, "cases")))

    def test_a_not_a_review_profile_can_never_give_a_green_run(self):
        """On a disposable COPY of the library whose HOLD logic is broken: no profile -> exit 1; a profile (pin matches the copy) with a real reviewer reference -> exit 0; the SAME profile
        with a NOT-A-REVIEW reference -> exit 4 (the dry run is flagged, never green); a wrong pin -> exit 1 again."""
        import portable_core as pc
        mu = next(m for m in portable_selftest.MUTATIONS if m["id"] == "MU1_hold_to_ready")
        copy = os.path.join(self.base, "libcopy")
        shutil.copytree(os.path.join(LIB, "video_core"), os.path.join(copy, "video_core"), ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(copy, *mu["file"].split("/"))
        src = open(path, "rb").read().decode("utf-8")
        if src.count(mu["old"]) != 1:
            self.skipTest("MU1 pattern not applicable to this library")
        open(path, "wb").write(src.replace(mu["old"], mu["new"]).encode("utf-8"))
        inv = pc.inventory(copy)
        pin = {k: inv[k] for k in ("package_digest_raw", "package_digest_eol", "all_digest_raw", "all_digest_eol")}

        def go(extra_args):
            self.n += 1
            out = os.path.join(self.base, f"out{self.n}")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = portable_harness.main(["--library-root", copy, "--scratch-root", self.scr, "--out-dir", out, "--only", "PC03_unresolved_hold_and_selection", *extra_args])
            return code, out
        code0, out0 = go([])
        self.assertEqual(code0, 1)
        case = jl(os.path.join(out0, "cases", "PC03_unresolved_hold_and_selection.json"))
        changed = {c["key"]: c["observed"] for c in case["checks"] if c["outcome"] == "FAIL"}
        self.assertEqual(len(changed), 1)

        def profile(name, ref, p=pin):
            f = os.path.join(self.base, name)
            with open(f, "x", encoding="utf-8") as fh:
                json.dump({"profile_version": 1, "source_pin": p, "reviewed_expectations": {k: {"observed": v, "reviewed_by_ref": ref} for k, v in changed.items()}}, fh)
            return f
        code1, out1 = go(["--profile", profile("real.json", "reviewer-ref-2026-11-05")])
        self.assertEqual(code1, 0, "a matching-pin profile with a real reviewer reference resolves the reviewed change")
        code2, out2 = go(["--profile", profile("dry.json", "NOT A REVIEW: dry run")])
        run2 = jl(os.path.join(out2, "run.json"))
        self.assertEqual(code2, 4)
        self.assertEqual(len(run2["inputs"]["reviewed_entries_applied_not_a_review"]), 1)
        code3, _ = go(["--profile", profile("wrongpin.json", "reviewer-ref-2026-11-05", dict(pin, package_digest_raw="0" * 64, package_digest_eol="0" * 64))])
        self.assertEqual(code3, 1, "a wrong source pin rescues nothing")

if __name__ == "__main__":
    unittest.main()
