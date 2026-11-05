"""Unit and integration tests of the boundary probe tooling (Python 3.11+ stdlib only).  Run:  python -B -m unittest discover -s tests -v   (from boundary-probe-r1; no files are written there)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import attribution  # noqa: E402
import boundary_probe  # noqa: E402

LIB = os.environ.get("BP_TEST_LIBRARY_ROOT") or os.path.abspath(os.path.join(ROOT, "..", "..", ".."))      # tools/video-core of the checkout (== b4ca4a3 for the library files)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def quiet_main(argv, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = boundary_probe.main(argv, **kw)
    return code, buf.getvalue()


class Oracle(unittest.TestCase):
    def setUp(self):
        boundary_probe.load_harness(boundary_probe.find_harness_dir([]))

    def test_zero_sample_frames_exact_rational(self):
        z = boundary_probe.zero_sample_frames
        self.assertEqual(z(60000, 1), [2, 7, 12])           # 0.8 samples per frame: one zero-sample frame in five
        self.assertEqual(z(48000, 1), [])                   # exactly one sample per frame
        self.assertEqual(z(48001, 1), [])                   # none inside the 18000-frame cap
        self.assertEqual(z(50000, 1)[0], 12)
        self.assertEqual(z(30000, 1001), [])
        self.assertEqual(z(60000, 1001), [])

    def test_sample_policy_matches_documented_integer_formula(self):
        for n, d in ((60000, 1), (30000, 1001), (50000, 1)):
            for f in range(0, 40):
                self.assertEqual(boundary_probe.S(f, n, d), (2 * f * d * 48000 + n) // (2 * n))

    def test_asc_and_scrub_keep_text_and_expose_surrogates(self):
        self.assertEqual(boundary_probe.asc("a\ud800b"), "a\\ud800b")
        self.assertEqual(boundary_probe.asc("日本 語"), "日本 語")
        self.assertEqual(boundary_probe.scrub({"k\udce9": ["\ud800", 1]}), {"k\\udce9": ["\\ud800", 1]})

    def test_documents_distinguish_lone_surrogate_from_literal_escape(self):
        lone = boundary_probe.dumps_ascii({"\ud800": 1})
        lit = boundary_probe.dumps_ascii({"\\ud800": 1})
        self.assertNotEqual(lone, lit)
        self.assertEqual(json.loads(lone), {"\ud800": 1})
        self.assertEqual(list(json.loads(lit)), ["\\ud800"])
        self.assertTrue(lone.isascii() and lit.isascii())

    def test_zman_layout_is_oracle_consistent(self):
        m = boundary_probe.zman(60000, 1, 2, "UNKNOWN", False)
        ev = {e["id"]: e for e in m["events"]}
        self.assertNotIn("ev_c", ev)                         # a zero-sample frame needs no filler
        self.assertEqual(m["scope"]["event_ids"], ["ev_a", "ev_b"])
        m2 = boundary_probe.zman(30000, 1001, 2, "UNKNOWN", False)
        self.assertIn("ev_c", {e["id"]: e for e in m2["events"]})
        self.assertEqual(m2["scope"]["event_ids"], ["ev_a", "ev_b", "ev_c"])
        m3 = boundary_probe.zman(30000, 1001, 2, "UNKNOWN", True)
        self.assertEqual(m3["scope"]["event_ids"], ["ev_a", "ev_b", "ev_z"])


class FakeRun:
    """Just enough of portable_core.Run for the platform-branch tests: worlds only, no child is ever started."""

    def __init__(self, tmp):
        self.tmp, self.n, self.children = tmp, 0, 0

    def new_world(self, label, dirname=None):
        self.n += 1
        parent = os.path.join(self.tmp, "w%d" % self.n)
        os.makedirs(parent)
        return boundary_probe.HX.pc.World(parent, dirname or "w")


class FakeCtx:
    case_id = "FAKE"

    def __init__(self, run):
        self.run, self.checks, self.invocations = run, [], []

    def control(self, *a, **k):
        pass


class PlatformBranches(unittest.TestCase):
    """The Windows (NOT_APPLICABLE) and 'filesystem refuses raw bytes' (SKIPPED) branches, exercised on this machine WITHOUT claiming any Windows execution."""

    def setUp(self):
        boundary_probe.load_harness(boundary_probe.find_harness_dir([]))

    def test_windows_branch_reports_not_applicable_and_creates_nothing(self):
        import types
        real_os = boundary_probe.os
        stub = types.SimpleNamespace(name="nt")
        boundary_probe.os = stub
        try:
            ctx = FakeCtx(None)
            boundary_probe.e1(ctx)
            ctx3 = FakeCtx(None)
            boundary_probe.e3(ctx3)
        finally:
            boundary_probe.os = real_os
        self.assertEqual({c["outcome"] for c in ctx.checks}, {"NOT_APPLICABLE"})
        self.assertEqual(len(ctx.checks), 6)
        self.assertTrue(all("no OS evidence" in c["reason"] for c in ctx.checks))
        self.assertEqual([c["outcome"] for c in ctx3.checks], ["NOT_APPLICABLE"])

    @unittest.skipIf(os.name == "nt", "POSIX raw-byte names")
    def test_filesystem_that_refuses_raw_bytes_is_skipped_not_failed(self):
        import errno
        from unittest import mock
        real_mkdir = os.mkdir

        def refusing(path, *a, **k):
            if isinstance(path, bytes) and b"\xe9" in path:
                raise OSError(errno.EILSEQ, "Illegal byte sequence")
            return real_mkdir(path, *a, **k)
        with tempfile.TemporaryDirectory() as t, mock.patch.object(os, "mkdir", refusing):
            ctx = FakeCtx(FakeRun(t))
            boundary_probe.e1(ctx)
        self.assertEqual([c["outcome"] for c in ctx.checks], ["SKIPPED"])
        self.assertIn("refuses a non-UTF-8 name", ctx.checks[0]["reason"])


class Classify(unittest.TestCase):
    def test_attribution_rules(self):
        c = attribution.classify
        self.assertEqual(c([("a", "MET"), ("b", "MET"), ("c", "MET")]), ("UNCHANGED_MET", None))
        self.assertEqual(c([("a", "NOT_MET"), ("b", "NOT_MET")]), ("UNCHANGED_NOT_MET", None))
        self.assertEqual(c([("a", "NOT_MET"), ("b", "MET"), ("c", "MET")]), ("CHANGED_NOT_MET_TO_MET", "b"))
        self.assertEqual(c([("a", "MET"), ("b", "NOT_MET")]), ("REGRESSED_MET_TO_NOT_MET", "b"))
        self.assertEqual(c([("a", "NOT_MET"), ("b", "MET"), ("c", "NOT_MET")])[0], "MIXED")
        self.assertEqual(c([("a", "MET"), ("b", "ERROR")])[0], "NOT_COMPARABLE")
        self.assertEqual(c([("a", "NOT_MET")])[0], "SINGLE_SOURCE_NOT_MET")


class Driver(unittest.TestCase):
    def test_refusals(self):
        with tempfile.TemporaryDirectory() as t:
            scratch, out = os.path.join(t, "s"), os.path.join(t, "o")
            os.mkdir(scratch)
            os.mkdir(out)
            self.assertEqual(quiet_main(["--source", f"b4ca4a3={LIB}", "--scratch-root", scratch, "--out-dir", out])[0], 2)                 # output exists: no overwrite
            self.assertEqual(quiet_main(["--source", f"nope={LIB}", "--scratch-root", scratch, "--out-dir", os.path.join(t, "o2")])[0], 2)    # unknown label
            self.assertEqual(quiet_main(["--source", f"b4ca4a3={LIB}", "--scratch-root", ROOT, "--out-dir", os.path.join(t, "o3")])[0], 2)   # scratch inside the repository
            self.assertEqual(quiet_main(["--source", f"b4ca4a3={LIB}", "--scratch-root", scratch, "--out-dir", os.path.join(ROOT, "x")])[0], 2)  # output inside the repository
            self.assertEqual(quiet_main(["--scratch-root", scratch, "--out-dir", os.path.join(t, "o4")])[0], 2)                                 # no source
            self.assertEqual(quiet_main(["--source", f"b4ca4a3={LIB}", "--scratch-root", scratch, "--out-dir", os.path.join(t, "o5"), "--only", "NOPE"])[0], 2)

    def test_failing_deny_admission_stops_before_any_probe_case(self):
        sys.path.insert(0, ROOT)
        import demo_failing_admission
        with tempfile.TemporaryDirectory() as t:
            scratch, out = os.path.join(t, "s"), os.path.join(t, "o")
            os.mkdir(scratch)
            code, text = quiet_main(["--source", f"b4ca4a3={LIB}", "--scratch-root", scratch, "--out-dir", out], admission_cases=[demo_failing_admission.failing_admission])
            self.assertEqual(code, 5, text)
            run = read_json(os.path.join(out, "run.json"))
            src = run["sources"][0]
            self.assertEqual(src["admission"]["statuses"], ["FAIL"])
            self.assertIsNotNone(run["stopped"])
            self.assertEqual(src["children_started"], 1)                       # only the one admission child; no probe case started
            self.assertEqual(src["cases"], {})
            self.assertTrue(os.path.isfile(os.path.join(out, "STOP.txt")))
            self.assertFalse(os.path.exists(os.path.join(out, "ATTRIBUTION.json")))

    def test_single_case_single_source_end_to_end(self):
        with tempfile.TemporaryDirectory() as t:
            scratch, out = os.path.join(t, "s"), os.path.join(t, "o")
            os.mkdir(scratch)
            code, text = quiet_main(["--source", f"b4ca4a3={LIB}", "--scratch-root", scratch, "--out-dir", out, "--only", "K5_lone_vs_literal_contrast"])
            self.assertEqual(code, 0, text)
            run = read_json(os.path.join(out, "sources", "b4ca4a3", "run.json"))
            self.assertEqual(run["admission"]["statuses"], ["PASS"])
            self.assertEqual(run["cases"], {"K5_lone_vs_literal_contrast": "CONTRACT_NOT_MET"})        # the original cannot report a lone-surrogate key: observed, not hidden
            self.assertTrue(run["source_unchanged_during_run"])
            self.assertEqual(run["all_digest_raw_before"], run["all_digest_raw_after"])
            self.assertFalse(os.path.exists(os.path.join(ROOT, "__pycache__")))


if __name__ == "__main__":
    unittest.main()
