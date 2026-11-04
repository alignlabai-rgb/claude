"""Caller resource limits and runner numeric arguments must be finite and correctly typed.

Background: Limits(max_runtime_seconds=nan) was accepted; `time.monotonic() >= nan` is always False, so the wall-clock bound could never fire.
Every rejection below happens before anything is written or any process is launched; where a process could have been launched,
subprocess.Popen is patched to raise, so no child (bounded or not) is ever started by these tests."""

import math
import os
import subprocess
import sys
import unittest

from tests.audio_fixtures import asset, dumps, event_use, manifest, tone, wav_bytes, write
from tests.helpers import EXAMPLE_EPISODE, POSIX, ROOT, TmpCase, fake_calls, make_fake_tools, no_process, tree_snapshot
from video_core import Limits, numeric, runner
from video_core.containment import ResourceEnvelope, run_contained
from video_core.audiomix.limits import AudioLimits

NAN, INF = float("nan"), float("inf")
BAD_SECONDS = [NAN, INF, -INF, 0, 0.0, -1, -0.001, True, False, "5", None, [1], 1e308, 86_400.001, 1e300]
GOOD_SECONDS = [0.001, 0.25, 0.5, 1, 10, 600.0, 86_400, 86_400.0]
BAD_INTS = [NAN, INF, -INF, 5.5, 100.0, True, False, "5", None, [1], 0, -1, 2**63]
VIDEO_FLOATS = ("max_runtime_seconds", "probe_timeout_seconds", "inspect_timeout_seconds")


def rejects(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


class Helpers(unittest.TestCase):
    def test_finite_seconds(self):
        for v in GOOD_SECONDS:
            self.assertEqual(numeric.finite_seconds("x", v), v)
        for v in BAD_SECONDS:
            self.assertTrue(rejects(lambda v=v: numeric.finite_seconds("x", v)), repr(v))
        self.assertTrue(rejects(lambda: numeric.finite_seconds("g", 3601, numeric.GRACE_CEILING_SECONDS)))
        self.assertEqual(numeric.finite_seconds("g", 3600, numeric.GRACE_CEILING_SECONDS), 3600)

    def test_positive_and_non_negative_int(self):
        self.assertEqual(numeric.positive_int("x", 1), 1)
        self.assertEqual(numeric.non_negative_int("x", 0), 0)
        for v in BAD_INTS:
            self.assertTrue(rejects(lambda v=v: numeric.positive_int("x", v)), repr(v))
        for v in (NAN, INF, 1.0, True, "0", None, -1, 2**63):
            self.assertTrue(rejects(lambda v=v: numeric.non_negative_int("x", v)), repr(v))

    def test_message_names_the_field_and_the_reason(self):
        with self.assertRaises(ValueError) as cm:
            numeric.finite_seconds("limit max_runtime_seconds", NAN)
        self.assertIn("max_runtime_seconds", str(cm.exception))
        self.assertIn("finite", str(cm.exception))


class VideoLimits(unittest.TestCase):
    def test_reported_constructions_are_rejected(self):
        for kw in ({"max_runtime_seconds": NAN}, {"max_runtime_seconds": INF}, {"probe_timeout_seconds": NAN}, {"max_drain_bytes": NAN}):
            with self.assertRaises(ValueError, msg=kw):
                Limits(**kw)

    def test_every_time_limit_rejects_every_bad_value_and_keeps_legitimate_fractions(self):
        for name in VIDEO_FLOATS:
            for v in BAD_SECONDS:
                self.assertTrue(rejects(lambda: Limits(**{name: v})), (name, v))
            for v in GOOD_SECONDS:
                self.assertEqual(getattr(Limits(**{name: v}), name), v, (name, v))

    def test_every_integer_limit_rejects_every_bad_value(self):
        for name in Limits.__dataclass_fields__:
            if name in VIDEO_FLOATS:
                continue
            for v in BAD_INTS:
                self.assertTrue(rejects(lambda: Limits(**{name: v})), (name, v))

    def test_replace_goes_through_the_same_validation(self):
        base = Limits()
        for name, v in (("max_runtime_seconds", NAN), ("max_runtime_seconds", INF), ("max_frames", 10.5), ("max_drain_bytes", NAN), ("probe_timeout_seconds", -INF)):
            self.assertTrue(rejects(lambda: base.replace(**{name: v})), (name, v))
        self.assertEqual(base.replace(max_runtime_seconds=0.75).max_runtime_seconds, 0.75)
        self.assertEqual(base.replace(max_runtime_seconds=None), base, "None means 'keep the default' and is not an error")

    def test_schema_ceilings_and_defaults_are_preserved(self):
        d = Limits()
        self.assertEqual((d.max_frames, d.max_dimension, d.max_clips, d.max_captions, d.max_runtime_seconds), (18_000, 7_680, 256, 512, 600.0))
        for kw in ({"max_frames": 18_001}, {"max_dimension": 7_681}, {"max_clips": 257}, {"max_captions": 513}, {"max_fps": 60_001}, {"max_windows_cmdline_chars": 32_767}):
            with self.assertRaises(ValueError):
                Limits(**kw)
        self.assertEqual(Limits(max_frames=18_000).max_frames, 18_000)

    def test_no_accepted_value_can_make_the_deadline_comparison_permanently_false(self):
        """Property: every accepted time limit is finite, so `now + limit` is a real number that the clock eventually passes."""
        for name in VIDEO_FLOATS:
            for v in GOOD_SECONDS:
                self.assertTrue(math.isfinite(getattr(Limits(**{name: v}), name)))
                self.assertLessEqual(getattr(Limits(**{name: v}), name), numeric.TIMEOUT_CEILING_SECONDS)


class AudioLimitsNumeric(unittest.TestCase):
    def test_runtime_and_integer_limits(self):
        for v in BAD_SECONDS:
            self.assertTrue(rejects(lambda v=v: AudioLimits(max_runtime_seconds=v)), repr(v))
        for v in GOOD_SECONDS:
            self.assertEqual(AudioLimits(max_runtime_seconds=v).max_runtime_seconds, v)
        for name in AudioLimits.__dataclass_fields__:
            if name == "max_runtime_seconds":
                continue
            for v in BAD_INTS:
                self.assertTrue(rejects(lambda: AudioLimits(**{name: v})), (name, v))

    def test_replace_and_existing_ceilings(self):
        self.assertTrue(rejects(lambda: AudioLimits().replace(max_runtime_seconds=NAN)))
        self.assertTrue(rejects(lambda: AudioLimits().replace(chunk_samples=100.0)))
        for kw in ({"max_events": 1025}, {"max_frames": 18001}, {"chunk_samples": 63}, {"max_concurrent": 65}):
            with self.assertRaises(ValueError):
                AudioLimits(**kw)


class RunnerBoundary(unittest.TestCase):
    """run_bounded accepts raw numbers, so it validates them itself, before Popen."""

    ARGV = [sys.executable, "-c", "pass"]

    def call(self, **kw):
        args = dict(phase="t", timeout_s=5)
        args.update(kw)
        return runner.run_bounded(self.ARGV, **args)

    def test_bad_numbers_are_rejected_before_any_process_is_launched(self):
        cases = {"timeout_s": BAD_SECONDS, "grace_s": BAD_SECONDS + [3601], "drain_timeout_s": BAD_SECONDS + [3601], "max_drain_bytes": BAD_INTS}
        with no_process():  # Popen would raise AssertionError, which is not a ValueError: the test fails if anything launches
            for name, values in cases.items():
                for v in values:
                    with self.assertRaises(ValueError, msg=(name, v)):
                        self.call(**{name: v})

    def test_bad_capture_limits_are_rejected_before_launch(self):
        bad = [(1,), (1, 2, 3), "ab", None, 5, (True, 1), (1, False), (-1, 0), (0, -1), (1.5, 0), (0, NAN), (INF, 0), ("1", 1), (2**63, 0)]
        with no_process():
            for label in ("stdout_limits", "stderr_limits"):
                for v in bad:
                    with self.assertRaises(ValueError, msg=(label, v)):
                        self.call(**{label: v})

    def test_nan_timeout_is_rejected_not_silently_unbounded(self):
        with no_process():
            with self.assertRaises(ValueError) as cm:
                self.call(timeout_s=NAN)
        self.assertIn("finite", str(cm.exception))

    def test_argv_validation_is_unchanged(self):
        for bad in ([], "ls", [1], ["a\x00b"]):
            with self.assertRaises(TypeError):
                runner.run_bounded(bad, phase="x", timeout_s=1)

    def test_legitimate_values_still_work_including_fractions_and_ints(self):
        for kw in ({"timeout_s": 0.25}, {"timeout_s": 20}, {"timeout_s": 5, "grace_s": 0.5, "drain_timeout_s": 0.5}, {"timeout_s": 5, "stdout_limits": (0, 0), "stderr_limits": [10, 10]},
                   {"timeout_s": 5, "max_drain_bytes": 1024}):
            r = self.call(**kw)
            self.assertTrue(r.ok, kw)

    def test_a_short_fractional_timeout_still_bounds_a_sleeping_owned_child(self):
        """The bound itself still works: a 0.3 s timeout kills (only) our own sleeping child. This child is bounded by that timeout."""
        t0 = __import__("time").monotonic()
        r = runner.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], phase="t", timeout_s=0.3)
        self.assertEqual((r.outcome, r.timed_out), (runner.TIMEOUT, True))
        self.assertLess(__import__("time").monotonic() - t0, 15)


class Cli(TmpCase):
    def run_cli(self, module, *args):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        return subprocess.run([sys.executable, "-m", module, *args], capture_output=True, cwd=self.tmp, env=env, timeout=120)

    def test_video_cli_rejects_non_finite_and_out_of_range_times_with_usage_exit(self):
        before = tree_snapshot(self.tmp)
        for flag in ("--max-runtime-seconds",):
            for bad in ("nan", "NaN", "inf", "-inf", "infinity", "1e999", "0", "-1", "86400.5", "1e308"):
                r = self.run_cli("video_core", "plan", EXAMPLE_EPISODE, f"{flag}={bad}")  # '=' form reaches our own validation
                self.assertEqual(r.returncode, 2, (flag, bad, r.stderr))
                self.assertEqual(r.stdout, b"", "nothing is printed or planned")
                self.assertIn(b"usage error", r.stderr)
                self.assertEqual(self.run_cli("video_core", "plan", EXAMPLE_EPISODE, flag, bad).returncode, 2, "separated form (argparse may reject first)")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_video_cli_integer_flags_reject_nan_and_floats(self):
        for flag in ("--max-diag-bytes", "--max-frames", "--max-source-bytes", "--max-argv-bytes"):
            for bad in ("nan", "inf", "5.5", "1e3", "0", "-5"):
                self.assertEqual(self.run_cli("video_core", "plan", EXAMPLE_EPISODE, flag, bad).returncode, 2, (flag, bad))

    def test_video_cli_accepts_a_legitimate_fractional_timeout(self):
        r = self.run_cli("video_core", "plan", EXAMPLE_EPISODE, "--max-runtime-seconds", "0.5")
        self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_render_with_a_nan_timeout_launches_nothing_and_creates_nothing(self):
        out, bin_ = self.mkdir("out"), self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        before = tree_snapshot(out)
        r = self.run_cli("video_core", "render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "r", "--render", "--ffmpeg", ff, "--ffprobe", fp,
                         "--max-runtime-seconds", "nan")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(tree_snapshot(out), before)
        self.assertEqual(fake_calls(bin_), [], "not even a version probe ran")

    def test_audio_cli_rejects_the_same_values(self):
        data = wav_bytes(tone("square", 480, 3200))
        assets, out = self.mkdir("assets"), self.mkdir("out")
        write(os.path.join(assets, "a.wav"), data)
        m = write(os.path.join(self.tmp, "m.json"), dumps(manifest([event_use("e", "a", 0, 3200, 0, 2)], [asset("a", "a.wav", data)], total=2)))
        before = tree_snapshot(self.tmp)
        for sub in ("plan", "validate"):
            for bad in ("nan", "inf", "-inf", "0", "86400.5", "1e999"):
                r = self.run_cli("video_core.audiomix", sub, m, "--max-runtime-seconds", bad)
                self.assertEqual(r.returncode, 2, (sub, bad, r.stderr))
        r = self.run_cli("video_core.audiomix", "mix", m, "--asset-root", assets, "--output-root", out, "--output-name", "r", "--mix", "--max-runtime-seconds", "nan")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(tree_snapshot(self.tmp), before, "no output directory was created")
        self.assertEqual(self.run_cli("video_core.audiomix", "plan", m, "--asset-root", assets, "--chunk-samples", "100.5").returncode, 2)
        ok = self.run_cli("video_core.audiomix", "plan", m, "--asset-root", assets, "--max-runtime-seconds", "0.5")
        self.assertEqual(ok.returncode, 0, ok.stderr)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
# Huge integers (found by independent Windows testing): finite_seconds('probe', 10**400) raised OverflowError because the ERROR MESSAGE formatted the raw int with
# `{value:g}` (int -> float conversion); positive_int/non_negative_int raised a misleading digit-limit ValueError for ints with more than 4300 digits.
# Contract: every rejection is a plain ValueError with a short, bounded message, raised before anything is created or launched.
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------

HUGE = [10**400, -(10**400), 10**4300, 10**4301, 10**5000, -(10**5000), 2**64, -(2**64), 2**63, 2**1024, 2**1030]


def raised(fn):
    try:
        fn()
    except BaseException as exc:  # noqa: B902 - the point is to see exactly what escapes
        return exc
    return None


class HugeIntegerBoundary(unittest.TestCase):
    def assert_plain_value_error(self, exc, label):
        self.assertIsNotNone(exc, f"{label}: accepted")
        self.assertIs(type(exc), ValueError, f"{label}: {type(exc).__name__}: {str(exc)[:80]}")
        self.assertNotIsInstance(exc, ArithmeticError)
        self.assertLess(len(str(exc)), 220, label)
        import re
        self.assertIsNone(re.search(r"\d{25}", str(exc)), f"{label}: the raw giant number must not be echoed")

    def test_the_reported_reproduction(self):
        exc = raised(lambda: numeric.finite_seconds("probe", 10**400))
        self.assert_plain_value_error(exc, "finite_seconds('probe', 10**400)")
        self.assertIn("1329 bits", str(exc))
        self.assertIn("probe", str(exc))

    def test_every_numeric_helper_rejects_every_huge_int_with_a_plain_value_error(self):
        for v in HUGE:
            for label, fn in (("finite_seconds", lambda v=v: numeric.finite_seconds("x", v)),
                              ("finite_seconds/grace", lambda v=v: numeric.finite_seconds("x", v, numeric.GRACE_CEILING_SECONDS)),
                              ("positive_int", lambda v=v: numeric.positive_int("x", v)),
                              ("positive_int/ceiling", lambda v=v: numeric.positive_int("x", v, 10)),
                              ("non_negative_int", lambda v=v: numeric.non_negative_int("x", v)),
                              ("non_negative_int/ceiling", lambda v=v: numeric.non_negative_int("x", v, 10))):
                self.assert_plain_value_error(raised(fn), f"{label}({v.bit_length()}-bit {'neg' if v < 0 else 'pos'})")

    def test_messages_describe_size_not_value_and_negative_wording_is_sane(self):
        self.assertIn("a negative integer of", str(raised(lambda: numeric.finite_seconds("x", -(10**400)))))
        self.assertIn("an integer of", str(raised(lambda: numeric.positive_int("x", 10**5000))))
        self.assertIn("got 5", str(raised(lambda: numeric.positive_int("x", 5, 4))), "small ints are still shown exactly")
        self.assertIn("got -3", str(raised(lambda: numeric.finite_seconds("x", -3))))
        self.assertEqual(numeric._show(2**64 - 1), str(2**64 - 1), "an integer that fits 64 bits is shown exactly")
        self.assertEqual(numeric._show(2**64), "an integer of 65 bits")

    def test_a_hostile_repr_cannot_turn_a_rejection_into_a_crash(self):
        class Bad:
            def __repr__(self):
                raise RuntimeError("boom")
        for fn in (numeric.finite_seconds, numeric.positive_int, numeric.non_negative_int):
            exc = raised(lambda fn=fn: fn("x", Bad()))
            self.assertIs(type(exc), ValueError)
            self.assertIn("Bad", str(exc))

    def test_valid_values_are_untouched_fractions_ints_and_the_exact_ceiling(self):
        for v in (0.25, 0.001, 5e-324, 1e-9, 1, 10, 3599.999, 86_399.99999, 86_400, 86_400.0):
            self.assertEqual(numeric.finite_seconds("x", v), v)
            self.assertIs(type(numeric.finite_seconds("x", v)), type(v), "the value is returned as given, not converted")
        self.assertEqual(numeric.finite_seconds("x", 3600, numeric.GRACE_CEILING_SECONDS), 3600)
        for bad in (86_400.0000001, 86_401, 1e308, NAN, INF, -INF, 0, -0.0, -1, True, False, "5", None, [1], 10**400):
            self.assertTrue(rejects(lambda b=bad: numeric.finite_seconds("x", b)), repr(bad)[:30])
        self.assertEqual((numeric.positive_int("x", 7), numeric.non_negative_int("x", 0), numeric.positive_int("x", numeric.INT_MAX)), (7, 0, numeric.INT_MAX))
        for bad in (numeric.INT_MAX + 1, True, 5.0, "5", None, 0, -1):
            self.assertTrue(rejects(lambda b=bad: numeric.positive_int("x", b)))

    def test_int_vs_float_comparison_is_exact_so_the_ceiling_is_decided_correctly_for_ints(self):
        self.assertEqual(numeric.finite_seconds("x", 86_400), 86_400)
        self.assertTrue(rejects(lambda: numeric.finite_seconds("x", 86_401)))
        self.assertTrue(rejects(lambda: numeric.finite_seconds("x", 2**53 + 1, 2.0**53)), "just above a float-representable ceiling is still rejected exactly")

    def test_limits_audio_limits_and_envelope_reject_huge_ints_in_every_field(self):
        from video_core import Limits
        from video_core.audiomix.limits import AudioLimits
        for v in HUGE:
            for cls in (Limits, AudioLimits):
                for name in cls.__dataclass_fields__:
                    self.assert_plain_value_error(raised(lambda cls=cls, name=name, v=v: cls(**{name: v})), f"{cls.__name__}.{name}")
            for name in ("cpu_seconds", "tree_cpu_seconds", "memory_bytes", "tree_memory_bytes", "max_file_bytes", "max_processes"):
                self.assert_plain_value_error(raised(lambda name=name, v=v: ResourceEnvelope(**{name: v})), f"ResourceEnvelope.{name}")
            self.assert_plain_value_error(raised(lambda v=v: ResourceEnvelope(cpu_seconds=1, handshake_timeout_seconds=v)), "ResourceEnvelope.handshake_timeout_seconds")
        self.assert_plain_value_error(raised(lambda: Limits().replace(max_runtime_seconds=10**400)), "Limits.replace")
        self.assert_plain_value_error(raised(lambda: AudioLimits().replace(max_runtime_seconds=10**400)), "AudioLimits.replace")

    def test_the_reported_envelope_construction(self):
        exc = raised(lambda: ResourceEnvelope(cpu_seconds=1, handshake_timeout_seconds=10**400))
        self.assert_plain_value_error(exc, "ResourceEnvelope(cpu_seconds=1, handshake_timeout_seconds=10**400)")
        self.assertIn("handshake_timeout_seconds", str(exc))
        self.assertEqual(ResourceEnvelope(cpu_seconds=1, handshake_timeout_seconds=0.25).handshake_timeout_seconds, 0.25)

    def test_run_bounded_and_run_contained_reject_huge_ints_before_any_process_is_launched(self):
        argv = [sys.executable, "-c", "pass"]
        env = ResourceEnvelope(cpu_seconds=5)
        with no_process():  # a launch would raise AssertionError, which is neither ValueError nor OverflowError
            for v in HUGE:
                for kw in ({"timeout_s": v}, {"timeout_s": 5, "grace_s": v}, {"timeout_s": 5, "drain_timeout_s": v}, {"timeout_s": 5, "max_drain_bytes": v},
                           {"timeout_s": 5, "stdout_limits": (v, 0)}, {"timeout_s": 5, "stdout_limits": (0, v)}, {"timeout_s": 5, "stderr_limits": (v, 0)}, {"timeout_s": 5, "stderr_limits": (0, v)}):
                    self.assert_plain_value_error(raised(lambda kw=kw: runner.run_bounded(argv, phase="t", **kw)), f"run_bounded {sorted(kw)[0]}")
                self.assert_plain_value_error(raised(lambda v=v: run_contained(argv, env, phase="t", timeout_s=v)), "run_contained timeout_s")
                for kw in ({"grace_s": v}, {"drain_timeout_s": v}, {"max_drain_bytes": v}):
                    self.assert_plain_value_error(raised(lambda kw=kw: run_contained(argv, env, phase="t", timeout_s=5, **kw)), f"run_contained {sorted(kw)[0]}")

    def test_the_exact_reported_run_bounded_call(self):
        with no_process():
            exc = raised(lambda: runner.run_bounded([sys.executable, "-c", "pass"], phase="t", timeout_s=10**400))
        self.assert_plain_value_error(exc, "run_bounded(timeout_s=10**400)")

    def test_a_valid_call_right_after_a_rejected_huge_one_still_runs(self):
        self.assertTrue(rejects(lambda: runner.run_bounded([sys.executable, "-c", "pass"], phase="t", timeout_s=10**400)))
        self.assertTrue(runner.run_bounded([sys.executable, "-c", "pass"], phase="t", timeout_s=0.5 + 9.5).ok)

    def test_defaults_and_the_baseline_plan_hash_are_unchanged(self):
        from tests.helpers import sample_bytes
        from video_core import Limits, prepare
        d = Limits()
        self.assertEqual((d.max_runtime_seconds, d.probe_timeout_seconds, d.inspect_timeout_seconds, d.max_drain_bytes, d.max_frames), (600.0, 30.0, 120.0, 64 * 1024 * 1024, 18_000))
        self.assertEqual(prepare(sample_bytes()).sha256, "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21")


class HugeIntegersAtTheCommandLine(TmpCase):
    """Command-line spellings of the same bug; each must be a usage error (exit 2) with nothing written and no tool launched."""

    def cli(self, module, *a):
        return subprocess.run([sys.executable, "-m", module, *a], capture_output=True, cwd=self.tmp, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}, timeout=60)

    def test_huge_integer_flags_exit_2_without_a_traceback(self):
        before = set(os.listdir(self.tmp))
        for digits in (400, 5000):
            big = "9" * digits
            for flag in ("--max-frames", "--max-diag-bytes", "--max-source-bytes", "--max-drain-bytes", "--max-windows-cmdline-chars"):
                r = self.cli("video_core", "plan", EXAMPLE_EPISODE, f"{flag}={big}")
                self.assertEqual(r.returncode, 2, (flag, digits, r.stderr[-200:]))
                self.assertNotIn(b"Traceback", r.stderr)
                self.assertEqual(r.stdout, b"")
        self.assertEqual(set(os.listdir(self.tmp)), before)

    def test_huge_time_values_exit_2(self):
        for spelling in ("1" + "0" * 400, "1e400", "9" * 5000):
            r = self.cli("video_core", "plan", EXAMPLE_EPISODE, f"--max-runtime-seconds={spelling}")
            self.assertEqual(r.returncode, 2, (spelling[:12], r.stderr[-200:]))
            self.assertNotIn(b"Traceback", r.stderr)

    def test_envelope_and_the_other_clis_reject_huge_values_the_same_way(self):
        big = "9" * 400
        r = self.cli("video_core", "render", EXAMPLE_EPISODE, "--output-root", self.tmp, "--output-name", "x", f"--envelope-cpu-seconds={big}")
        self.assertEqual(r.returncode, 2, r.stderr[-200:])
        r = self.cli("video_core", "render", EXAMPLE_EPISODE, "--output-root", self.tmp, "--output-name", "x", "--envelope-cpu-seconds=5", f"--envelope-handshake-timeout-seconds=1{'0' * 400}")
        self.assertEqual(r.returncode, 2, r.stderr[-200:])
        for module, sub in (("video_core.audiomix", "plan"), ("video_core.avmux", "plan")):
            r = self.cli(module, sub, EXAMPLE_EPISODE, f"--max-runtime-seconds=1{'0' * 400}")
            self.assertEqual(r.returncode, 2, (module, r.stderr[-200:]))
            self.assertNotIn(b"Traceback", r.stderr)
