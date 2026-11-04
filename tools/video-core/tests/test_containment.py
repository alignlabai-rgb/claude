"""Optional resource containment (video_core.containment).

Layers, kept distinct on purpose:
  * pure validation / support-table tests (any OS)
  * NATIVE POSIX tests: real owned Python fixtures run under the real worker and the real kernel limits; the fixtures report their own limits or hit them
  * parent-side handshake tests that substitute a STUB worker -- these test the parent's verification logic only and are NOT native evidence
  * Windows tests (skipped here; written to run on a Windows host later) and a ctypes layout test that runs anywhere
Every resource probe is small and has a hard outer deadline (<= 10 s, <= 256 MiB cap, bounded allocations, no unbounded loops except the CPU burner, which is
killed by its own 1-2 s CPU limit and by the runner's timeout)."""

import glob
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests.helpers import POSIX, ROOT, TmpCase, jl, no_process, pid_dead
from tests.lifecycle_support import sigint_default
from video_core import numeric, runner
from video_core.containment import ResourceEnvelope, contained, run_contained, unsupported_limits, validate_hello
from video_core.containment import envelope as E

PY = sys.executable
HAS_RESOURCE = importlib.util.find_spec("resource") is not None
MIB = 1024 * 1024
NAN, INF = float("nan"), float("inf")
BAD_INTS = [NAN, INF, -INF, 5.5, 100.0, True, False, "5", None, 0, -1, 2**63]
BAD_SECONDS = [NAN, INF, -INF, 0, -1, True, "5", None, 61, 1e308]


def code(text: str):
    return [PY, "-c", text]


def run(env_kwargs, target, timeout=10, **kw):
    return run_contained(target, ResourceEnvelope(**env_kwargs), phase="t", timeout_s=timeout, stdout_limits=kw.pop("stdout_limits", (4096, 4096)), **kw)


class EnvelopeValidation(unittest.TestCase):
    def test_requires_at_least_one_limit_and_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            ResourceEnvelope()
        with self.assertRaises(ValueError):
            ResourceEnvelope(handshake_timeout_seconds=3)
        with self.assertRaises(ValueError):
            ResourceEnvelope.from_mapping({"cpu_seconds": 5, "nice": 10})
        with self.assertRaises(ValueError):
            ResourceEnvelope.from_mapping([("cpu_seconds", 5)])
        self.assertEqual(ResourceEnvelope.from_mapping({"cpu_seconds": 5}).requested(), {"cpu_seconds": 5})

    def test_every_integer_limit_rejects_nan_inf_bool_float_zero_negative_and_overflow(self):
        for name in ("cpu_seconds", "tree_cpu_seconds", "memory_bytes", "tree_memory_bytes", "max_file_bytes", "max_processes"):
            for v in BAD_INTS:
                with self.assertRaises(ValueError, msg=(name, v)):
                    ResourceEnvelope(**{name: v})

    def test_documented_floors_and_ceilings(self):
        for kw in ({"memory_bytes": 64 * MIB - 1}, {"tree_memory_bytes": 1}, {"max_processes": 1}, {"cpu_seconds": E.MAX_CPU_SECONDS + 1}, {"memory_bytes": E.MAX_BYTES + 1},
                   {"max_processes": E.MAX_MAX_PROCESSES + 1}):
            with self.assertRaises(ValueError, msg=kw):
                ResourceEnvelope(**kw)
        for kw in ({"memory_bytes": 64 * MIB}, {"max_processes": 2}, {"cpu_seconds": 1}, {"cpu_seconds": E.MAX_CPU_SECONDS}, {"max_file_bytes": 1}):
            self.assertTrue(ResourceEnvelope(**kw).requested(), kw)

    def test_handshake_timeout_is_finite_positive_and_bounded(self):
        for v in BAD_SECONDS:
            with self.assertRaises(ValueError, msg=v):
                ResourceEnvelope(cpu_seconds=5, handshake_timeout_seconds=v)
        self.assertEqual(ResourceEnvelope(cpu_seconds=5, handshake_timeout_seconds=0.25).handshake_timeout_seconds, 0.25)
        self.assertEqual(ResourceEnvelope(cpu_seconds=5, handshake_timeout_seconds=60).handshake_timeout_seconds, 60)

    def test_support_table_is_explicit_per_platform(self):
        full = ResourceEnvelope(cpu_seconds=5, memory_bytes=128 * MIB, max_file_bytes=MIB, tree_cpu_seconds=5, tree_memory_bytes=128 * MIB, max_processes=4)
        posix = {u["limit"]: u["reason"] for u in unsupported_limits(full, "posix", True)}
        self.assertEqual(set(posix), {"tree_cpu_seconds", "tree_memory_bytes", "max_processes"})
        self.assertIn("RLIMIT_NPROC", posix["max_processes"])
        self.assertIn("aggregate", posix["tree_memory_bytes"])
        nt = {u["limit"] for u in unsupported_limits(full, "nt")}
        self.assertEqual(nt, {"max_file_bytes"})
        self.assertEqual({u["limit"] for u in unsupported_limits(full, "java")}, set(full.requested()), "unknown platforms fail explicitly, every limit")
        self.assertEqual({u["limit"] for u in unsupported_limits(ResourceEnvelope(cpu_seconds=5), "posix", False)}, {"cpu_seconds"}, "no resource module => nothing is supported")
        self.assertEqual(unsupported_limits(ResourceEnvelope(cpu_seconds=5, memory_bytes=128 * MIB), "posix", True), [])

    def test_the_numeric_helpers_are_the_shared_strict_ones(self):
        self.assertIs(E.positive_int, numeric.positive_int)
        self.assertIs(E.finite_seconds, numeric.finite_seconds)

    def test_hello_validation_is_strict(self):
        req = {"cpu_seconds": 5, "memory_bytes": 128 * MIB}
        good = {"schema": contained.HELLO_SCHEMA, "token": "t", "requested": req, "applied": {"cpu_seconds": 5, "memory_bytes": 128 * MIB}, "unsupported": [], "errors": []}
        self.assertEqual(validate_hello(good, "t", req)[0], True)
        stricter = dict(good, applied={"cpu_seconds": 3, "memory_bytes": 64 * MIB})
        self.assertEqual(validate_hello(stricter, "t", req)[0], True, "an already-stricter limit satisfies the request")
        bad = {
            "wrong schema": dict(good, schema="x"), "wrong token": dict(good, token="u"), "echo differs": dict(good, requested={"cpu_seconds": 6}),
            "unsupported": dict(good, unsupported=[{"limit": "*", "reason": "x"}]), "errors": dict(good, errors=["boom"]),
            "missing limit": dict(good, applied={"cpu_seconds": 5}), "extra limit": dict(good, applied={**good["applied"], "max_processes": 2}),
            "weaker": dict(good, applied={"cpu_seconds": 6, "memory_bytes": 128 * MIB}), "zero": dict(good, applied={"cpu_seconds": 0, "memory_bytes": 128 * MIB}),
            "bool": dict(good, applied={"cpu_seconds": True, "memory_bytes": 128 * MIB}), "float": dict(good, applied={"cpu_seconds": 5.0, "memory_bytes": 128 * MIB}),
            "none": dict(good, applied={"cpu_seconds": None, "memory_bytes": 128 * MIB}), "unlimited": dict(good, applied={"cpu_seconds": -1, "memory_bytes": 128 * MIB}),
            "applied not a dict": dict(good, applied=[]),
        }
        for label, hello in bad.items():
            self.assertFalse(validate_hello(hello, "t", req)[0], label)
        self.assertFalse(validate_hello([], "t", req)[0])
        self.assertFalse(validate_hello(None, "t", req)[0])


class Compatibility(unittest.TestCase):
    def test_run_bounded_default_signature_and_receipt_shape_are_unchanged(self):
        import inspect
        params = inspect.signature(runner.run_bounded).parameters
        self.assertEqual(params["after_start"].default, None)
        for name, default in (("stdout_limits", (0, 0)), ("stderr_limits", (8192, 8192)), ("grace_s", 2.0), ("drain_timeout_s", 2.0)):
            self.assertEqual(params[name].default, default)
        r = runner.run_bounded(code("pass"), phase="p", timeout_s=10)
        self.assertNotIn("containment", r.to_dict(), "without an envelope the receipt has no containment key")

    def test_importing_the_package_changes_no_limits_and_applies_nothing(self):
        if not HAS_RESOURCE:
            self.skipTest("no resource module")
        probe = ("import resource, sys; names=('RLIMIT_CPU','RLIMIT_AS','RLIMIT_FSIZE','RLIMIT_NPROC'); "
                 "before=[resource.getrlimit(getattr(resource,n)) for n in names]; import video_core.containment, video_core, video_core.avmux, video_core.render; "
                 "after=[resource.getrlimit(getattr(resource,n)) for n in names]; assert before==after, (before, after); print('ok')")
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "vc-envelope-*")))
        r = subprocess.run([PY, "-c", probe], capture_output=True, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, b"ok"), r.stderr)
        self.assertEqual(set(glob.glob(os.path.join(tempfile.gettempdir(), "vc-envelope-*"))), before, "no handshake directory is created at import")

    def test_the_worker_module_imports_without_side_effects_on_any_os(self):
        spec = importlib.util.spec_from_file_location("vc_worker_under_test", contained.WORKER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(mod.main))
        self.assertIsNone(mod._JOB_HANDLE)


class WindowsStructLayout(unittest.TestCase):
    """Layout of the ctypes structures the Windows branch uses. Runs anywhere; it pins the 64-bit sizes (this is a layout check, NOT Windows evidence)."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("vc_worker_layout", contained.WORKER_PATH)
        cls.w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.w)

    @unittest.skipUnless(sys.maxsize > 2**32, "64-bit layout only")
    def test_sizes_and_offsets_match_the_documented_64_bit_layout(self):
        Basic, Io, Ext, ctypes = self.w.windows_structs()
        self.assertEqual((ctypes.sizeof(Basic), ctypes.sizeof(Io), ctypes.sizeof(Ext)), (64, 48, 144))
        off = lambda cls, n: getattr(cls, n).offset
        self.assertEqual([off(Basic, n) for n in ("PerProcessUserTimeLimit", "PerJobUserTimeLimit", "LimitFlags", "MinimumWorkingSetSize", "ActiveProcessLimit", "Affinity", "PriorityClass")],
                         [0, 8, 16, 24, 40, 48, 56])
        self.assertEqual([off(Ext, n) for n in ("BasicLimitInformation", "IoInfo", "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed")], [0, 64, 112, 120, 128, 136])

    def test_flag_constants_are_distinct_single_bits(self):
        flags = [self.w.JOB_OBJECT_LIMIT_PROCESS_TIME, self.w.JOB_OBJECT_LIMIT_JOB_TIME, self.w.JOB_OBJECT_LIMIT_ACTIVE_PROCESS, self.w.JOB_OBJECT_LIMIT_PROCESS_MEMORY,
                 self.w.JOB_OBJECT_LIMIT_JOB_MEMORY, self.w.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE]
        self.assertEqual(len(set(flags)), 6)
        self.assertTrue(all(f & (f - 1) == 0 for f in flags))
        self.assertEqual(self.w.HUNDRED_NS_PER_SECOND, 10_000_000)


@unittest.skipUnless(POSIX and HAS_RESOURCE, "native POSIX containment")
class NativePosix(TmpCase):
    RLIMITS = ("import resource, json, os; print(json.dumps({'pid': os.getpid(), 'cpu': resource.getrlimit(resource.RLIMIT_CPU), "
               "'as': resource.getrlimit(resource.RLIMIT_AS), 'fsize': resource.getrlimit(resource.RLIMIT_FSIZE)}))")

    def setUp(self):
        super().setUp()
        import resource
        self._before = [resource.getrlimit(r) for r in (resource.RLIMIT_CPU, resource.RLIMIT_AS, resource.RLIMIT_FSIZE, resource.RLIMIT_NPROC)]
        self._tmp_before = set(glob.glob(os.path.join(tempfile.gettempdir(), "vc-envelope-*")))

    def tearDown(self):
        import resource
        self.assertEqual(self._before, [resource.getrlimit(r) for r in (resource.RLIMIT_CPU, resource.RLIMIT_AS, resource.RLIMIT_FSIZE, resource.RLIMIT_NPROC)],
                         "the calling process's own limits must never change")
        self.assertEqual(set(glob.glob(os.path.join(tempfile.gettempdir(), "vc-envelope-*"))), self._tmp_before, "the handshake directory is always removed")

    def sentinel(self):
        p = subprocess.Popen(code("import time; time.sleep(60)"), start_new_session=True)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    # ---- accepted target: the TARGET itself reports the limits (behaviour, not just a receipt) ----
    def test_accepted_target_runs_with_exactly_the_requested_limits_in_force(self):
        r = run({"cpu_seconds": 7, "memory_bytes": 256 * MIB, "max_file_bytes": MIB}, code(self.RLIMITS))
        self.assertEqual((r.outcome, r.exit_code), (runner.EXITED_ZERO, 0), r.stderr.text())
        seen = json.loads(r.stdout_bytes())
        self.assertEqual((seen["cpu"], seen["as"], seen["fsize"]), ([7, 7], [256 * MIB, 256 * MIB], [MIB, MIB]))
        c = r.containment
        self.assertEqual(c["requested"], {"cpu_seconds": 7, "memory_bytes": 256 * MIB, "max_file_bytes": MIB})
        self.assertEqual(c["applied"], c["requested"])
        self.assertEqual((c["unsupported"], c["refusal"], c["go_sent"], c["worker_started"], c["platform"]), ([], None, True, True, "posix"))
        self.assertEqual(c["worker_pid"], seen["pid"], "POSIX: the target runs as the worker process image (execv), nothing in between")
        self.assertLess(c["handshake_seconds"], 5)
        self.assertIn("never the calling process", c["applied_to"])
        self.assertTrue(c["not_contained"])
        self.assertEqual(c["retry"].split(":")[0], "none")

    def test_the_receipt_shows_the_target_command_and_the_result_dict_carries_containment(self):
        r = run({"cpu_seconds": 7}, code("print('hi')"))
        d = r.to_dict()
        self.assertEqual(d["argv"], code("print('hi')"))
        self.assertEqual(d["containment"]["requested"], {"cpu_seconds": 7})
        self.assertEqual(r.stdout_bytes().strip(), b"hi")

    def test_a_single_limit_does_not_apply_the_others(self):
        r = run({"cpu_seconds": 9}, code(self.RLIMITS))
        seen = json.loads(r.stdout_bytes())
        self.assertEqual((seen["cpu"], seen["as"], seen["fsize"]), ([9, 9], [-1, -1], [-1, -1]))

    def test_an_inherited_stricter_limit_is_never_raised(self):
        inner = ("import resource, json, sys; resource.setrlimit(resource.RLIMIT_CPU, (3, 3)); "
                 "from video_core.containment import ResourceEnvelope, run_contained; "
                 "r = run_contained([sys.executable, '-c', %r], ResourceEnvelope(cpu_seconds=50), phase='t', timeout_s=10, stdout_limits=(4096, 0)); "
                 "print(json.dumps({'outcome': r.outcome, 'seen': json.loads(r.stdout_bytes())['cpu'], 'applied': r.containment['applied'], 'notes': r.containment['notes']}))") % self.RLIMITS
        p = subprocess.run([PY, "-c", inner], capture_output=True, timeout=30, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT})
        out = json.loads(p.stdout)
        self.assertEqual((out["outcome"], out["seen"], out["applied"]), ("EXITED_ZERO", [3, 3], {"cpu_seconds": 3}), p.stderr)
        self.assertIn("already stricter", out["notes"][0])

    # ---- unsupported limits fail closed BEFORE anything starts ----
    def test_unsupported_limit_refuses_before_any_process_starts(self):
        marker = os.path.join(self.tmp, "target-ran")
        with no_process():  # Popen would raise AssertionError: proves neither a worker nor the target was launched
            for kw in ({"tree_memory_bytes": 128 * MIB}, {"tree_cpu_seconds": 5}, {"max_processes": 4}, {"cpu_seconds": 5, "max_processes": 4}):
                r = run(kw, code(f"open({marker!r}, 'w').write('x')"))
                self.assertEqual((r.outcome, r.exit_code, r.signal), (runner.CONTAINMENT_REFUSED, None, None), kw)
                c = r.containment
                self.assertEqual((c["worker_started"], c["go_sent"], c["target_may_have_started"], c["applied"]), (False, False, False, {}), kw)
                self.assertEqual(c["refusal"]["reason"], "unsupported_limit")
                self.assertTrue({u["limit"] for u in c["unsupported"]} <= {"tree_memory_bytes", "tree_cpu_seconds", "max_processes"})
        self.assertFalse(os.path.exists(marker))

    def test_a_mixed_request_applies_nothing_not_even_the_supported_part(self):
        r = run({"cpu_seconds": 5, "tree_memory_bytes": 128 * MIB}, code("print(1)"))
        self.assertEqual((r.outcome, r.containment["applied"], r.stdout_bytes()), (runner.CONTAINMENT_REFUSED, {}, b""))

    def test_no_weaker_fallback_is_attempted(self):
        calls = []
        real = runner.run_bounded
        with mock.patch.object(runner, "run_bounded", lambda *a, **k: (calls.append(1), real(*a, **k))[1]):
            run({"cpu_seconds": 5, "max_processes": 4}, code("pass"))
            run({"cpu_seconds": 5}, code("pass"))
        self.assertEqual(len(calls), 1, "the refused request started nothing and was not retried with fewer limits")

    # ---- exhaustion of owned fixtures ----
    def test_cpu_limit_terminates_a_busy_target_quickly(self):
        t0 = time.monotonic()
        r = run({"cpu_seconds": 1}, code("while True:\n    pass"), timeout=10)
        self.assertLess(time.monotonic() - t0, 8)
        self.assertEqual((r.outcome, r.timed_out, r.kill_reason), (runner.SIGNALED, False, None), "ended by the kernel's RLIMIT_CPU, not by our timeout")
        self.assertIn(r.signal, (signal.SIGXCPU, signal.SIGKILL))
        self.assertEqual(r.containment["signal_hint"]["signal"], r.signal)
        self.assertIn("hint, not a proof", r.containment["signal_hint"]["note"])

    ALLOC = ("import sys\nchunks = []\nfailed_at = None\nfor i in range(24):\n    try:\n        chunks.append(bytearray(8 * 1024 * 1024))\n"
             "    except MemoryError:\n        failed_at = i\n        break\nprint(failed_at if failed_at is not None else 'none')")

    def test_memory_limit_makes_allocation_fail_where_a_control_run_succeeds(self):
        capped = run({"memory_bytes": 128 * MIB}, code(self.ALLOC), timeout=10)
        self.assertEqual((capped.outcome, capped.exit_code), (runner.EXITED_ZERO, 0), capped.stderr.text())
        failed_at = capped.stdout_bytes().strip()
        self.assertNotEqual(failed_at, b"none", "the 192 MiB allocation loop must hit the 128 MiB cap")
        self.assertLess(int(failed_at) * 8, 128, "failed before 128 MiB had been allocated")
        control = runner.run_bounded(code(self.ALLOC), phase="control", timeout_s=10, stdout_limits=(64, 0))
        self.assertEqual(control.stdout_bytes().strip(), b"none", "without the envelope the same fixture allocates all 192 MiB: the cap is what stopped it")

    def test_file_size_limit_stops_a_writer(self):
        path = os.path.join(self.tmp, "big.bin")
        prog = (f"import sys\ntry:\n    with open({path!r}, 'wb') as fh:\n        for _ in range(8):\n            fh.write(b'x' * (256 * 1024))\n            fh.flush()\n"
                "    print('wrote-all')\nexcept OSError as e:\n    print('EFBIG' if e.errno == 27 else e.errno)")
        r = run({"max_file_bytes": MIB}, code(prog))
        self.assertEqual(r.stdout_bytes().strip(), b"EFBIG")
        self.assertLessEqual(os.path.getsize(path), MIB)

    # ---- timeout / cancellation cleanup of an owned process tree ----
    TREE_FIXTURE = (
        "import os, subprocess, sys, time\n"
        "prefix = sys.argv[1]\n"
        "grand = 'import time; time.sleep(60)'\n"
        "child = ('import os, subprocess, sys, time\\n'\n"
        "         'g = subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(60)\"])\\n'\n"
        "         'open(sys.argv[1] + \".g\", \"w\").write(str(g.pid))\\n'\n"
        "         'time.sleep(60)\\n')\n"
        "c = subprocess.Popen([sys.executable, '-c', child, prefix])\n"
        "open(prefix + '.c', 'w').write(str(c.pid))\n"
        "open(prefix + '.t', 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )

    def tree_target(self, pidfile):
        script = os.path.join(self.tmp, "tree_fixture.py")
        if not os.path.exists(script):
            with open(script, "w") as fh:
                fh.write(self.TREE_FIXTURE)
        return [PY, script, pidfile]

    def pids(self, pidfile):
        deadline = time.monotonic() + 8
        out = {}
        while time.monotonic() < deadline and len(out) < 3:
            for k in ("t", "c", "g"):
                try:
                    with open(f"{pidfile}.{k}") as fh:
                        out[k] = int(fh.read())
                except (OSError, ValueError):
                    pass
            time.sleep(0.05)
        return out

    def test_timeout_kills_the_owned_tree_and_leaves_an_unrelated_sentinel_alone(self):
        bystander = self.sentinel()
        pidfile = os.path.join(self.tmp, "pids")
        t0 = time.monotonic()
        r = run({"cpu_seconds": 30, "memory_bytes": 256 * MIB}, self.tree_target(pidfile), timeout=2.5)
        self.assertLess(time.monotonic() - t0, 12)
        self.assertEqual((r.outcome, r.timed_out, r.kill_reason), (runner.TIMEOUT, True, "timeout"))
        pids = self.pids(pidfile)
        self.assertEqual(set(pids), {"t", "c", "g"}, "target, child and grandchild all started")
        for k, pid in pids.items():
            self.assertTrue(pid_dead(pid), f"{k} ({pid}) must be dead after the timeout")
        self.assertIsNone(bystander.poll(), "an unrelated process in its own session must survive")

    def test_cancellation_cleans_up_the_tree_and_spares_the_sentinel(self):
        bystander = self.sentinel()
        pidfile = os.path.join(self.tmp, "pids2")
        timer = threading.Timer(1.5, lambda: signal.pthread_kill(threading.main_thread().ident, signal.SIGINT))
        timer.start()
        try:
            with sigint_default():  # a suite started with `cmd &` from a non-interactive shell inherits SIGINT=SIG_IGN; without this the signal would be dropped and the run would time out
                r = run({"cpu_seconds": 30}, self.tree_target(pidfile), timeout=10)
        finally:
            timer.cancel()
        self.assertEqual((r.outcome, r.kill_reason), (runner.INTERRUPTED, "interrupt"))
        for k, pid in self.pids(pidfile).items():
            self.assertTrue(pid_dead(pid), k)
        self.assertIsNone(bystander.poll())

    def test_output_is_drained_so_a_chatty_target_cannot_deadlock_the_envelope(self):
        r = run({"cpu_seconds": 30}, code("import sys; sys.stdout.write('o' * 3_000_000); sys.stderr.write('e' * 500_000)"), stdout_limits=(100, 100), stderr_limits=(50, 50))
        self.assertEqual((r.outcome, r.stdout.summary()["total_bytes"], r.stderr.summary()["total_bytes"]), (runner.EXITED_ZERO, 3_000_000, 500_000))

    def test_nonzero_exit_and_stderr_of_the_target_pass_through(self):
        r = run({"cpu_seconds": 30}, code("import sys; sys.stderr.write('oops'); sys.exit(5)"))
        self.assertEqual((r.outcome, r.exit_code, r.stderr.text()), (runner.NONZERO_EXIT, 5, "oops"))

    def test_a_missing_target_fails_after_go_with_the_exec_error_and_is_not_retried(self):
        r = run({"cpu_seconds": 30}, [os.path.join(self.tmp, "no-such-binary")])
        self.assertEqual((r.outcome, r.exit_code), (runner.NONZERO_EXIT, 88))
        self.assertIn("cannot start target", r.stderr.text())
        self.assertTrue(r.containment["go_sent"])

    def test_strict_numeric_arguments_are_rejected_before_anything_starts(self):
        env = ResourceEnvelope(cpu_seconds=5)
        with no_process():
            for bad in BAD_SECONDS[:7]:
                with self.assertRaises(ValueError, msg=bad):
                    run_contained(code("pass"), env, phase="t", timeout_s=bad)
            for kw in ({"grace_s": NAN}, {"drain_timeout_s": INF}, {"max_drain_bytes": 5.5}, {"stdout_limits": (1,)}):
                with self.assertRaises(ValueError, msg=kw):
                    run_contained(code("pass"), env, phase="t", timeout_s=5, **kw)
        for bad in ("ls", [], [1], ["a\x00"]):
            with self.assertRaises(TypeError):
                run_contained(bad, env, phase="t", timeout_s=5)
        with self.assertRaises(TypeError):
            run_contained(code("pass"), {"cpu_seconds": 5}, phase="t", timeout_s=5)

    def test_worker_refuses_a_malformed_invocation_without_touching_anything(self):
        for args in ([], ["not json", "--", "x"], [json.dumps({"dir": self.tmp}), "--", "x"], [json.dumps({"dir": self.tmp, "token": "t", "limits": {}, "wait_go_seconds": 1}), "no-dashes", "x"]):
            p = subprocess.run([PY, "-I", "-S", contained.WORKER_PATH, *args], capture_output=True, timeout=20)
            self.assertEqual(p.returncode, 89, args)
        self.assertEqual(os.listdir(self.tmp), [])


STUB = r'''
import json, os, sys, time
cfg = json.loads(sys.argv[1]); d = cfg["dir"]; token = cfg["token"]; mode = os.environ_mode if False else "%MODE%"
here = %HERE%
def hello(**over):
    h = {"schema": "video_core.containment_hello/1", "token": token, "worker_pid": os.getpid(), "platform": "posix", "requested": cfg["limits"],
         "applied": dict(cfg["limits"]), "unsupported": [], "errors": []}
    h.update(over)
    tmp = os.path.join(d, "hello.json.tmp"); open(tmp, "w").write(json.dumps(h)); os.replace(tmp, os.path.join(d, "hello.json"))
open(os.path.join(here, "mode_%s.dirmode" % oct(os.stat(d).st_mode & 0o777)[2:]), "w").write("x")
if mode == "silent": time.sleep(30)
elif mode == "exit": sys.exit(3)
elif mode == "garbage": open(os.path.join(d, "hello.json"), "w").write("not json{{")
elif mode == "huge": open(os.path.join(d, "hello.json"), "w").write("{" + " " * 200000 + "}")
elif mode == "token": hello(token="wrong")
elif mode == "echo": hello(requested={"cpu_seconds": 999})
elif mode == "weaker": hello(applied={k: v * 2 for k, v in cfg["limits"].items()})
elif mode == "missing": hello(applied={})
elif mode == "extra": hello(applied={**cfg["limits"], "max_processes": 3})
elif mode == "unsupported": hello(unsupported=[{"limit": "cpu_seconds", "reason": "stub says no"}])
elif mode == "errors": hello(errors=["stub error"])
elif mode == "ok": hello()
else: sys.exit(99)
for _ in range(60):
    if os.path.exists(os.path.join(d, "go")): open(os.path.join(here, "saw_go"), "w").write("go"); break
    if os.path.exists(os.path.join(d, "abort")): open(os.path.join(here, "saw_abort"), "w").write("abort"); break
    time.sleep(0.01)
sys.exit(0)
'''


@unittest.skipUnless(POSIX and HAS_RESOURCE, "stub-worker tests use the POSIX layout")
class ParentHandshakeWithStubWorker(TmpCase):
    """These substitute a STUB worker to test only the PARENT's bounded handshake and verification. They are not native-containment evidence."""

    def stub(self, mode):
        path = os.path.join(self.tmp, "stub_worker.py")
        with open(path, "w") as fh:
            fh.write(STUB.replace("%MODE%", mode).replace("%HERE%", repr(self.tmp)))
        return path

    def go(self, mode, **env_kw):
        env_kw.setdefault("cpu_seconds", 5)
        env_kw.setdefault("handshake_timeout_seconds", 1.0)
        with mock.patch.object(contained, "WORKER_PATH", self.stub(mode)):
            t0 = time.monotonic()
            r = run(env_kw, code("open(%r, 'w').write('target ran')" % os.path.join(self.tmp, "target-ran")), timeout=10)
            return r, time.monotonic() - t0

    def assert_refused(self, r, reason):
        self.assertEqual((r.outcome, r.containment["refusal"]["reason"], r.containment["go_sent"], r.containment["target_may_have_started"]), (runner.CONTAINMENT_REFUSED, reason, False, False))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "target-ran")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "saw_go")), "the worker never saw a go signal")

    def test_a_worker_that_never_answers_is_refused_within_the_handshake_bound_and_killed(self):
        r, took = self.go("silent", handshake_timeout_seconds=0.5)
        self.assert_refused(r, "handshake_timeout")
        self.assertLess(took, 8)
        self.assertEqual(r.containment["applied"], {})

    def test_a_worker_that_exits_early_is_detected_without_waiting_for_the_timeout(self):
        r, took = self.go("exit", handshake_timeout_seconds=30)
        self.assert_refused(r, "worker_exited_before_hello")
        self.assertLess(took, 10)

    def test_every_kind_of_bad_hello_is_refused_and_no_go_is_ever_sent(self):
        for mode, reason in (("garbage", "hello_unreadable"), ("huge", "hello_too_large"), ("token", "limits_not_applied"), ("echo", "limits_not_applied"), ("weaker", "limits_not_applied"),
                             ("missing", "limits_not_applied"), ("extra", "limits_not_applied"), ("unsupported", "limits_not_applied"), ("errors", "limits_not_applied")):
            for f in ("target-ran", "saw_go", "saw_abort"):
                try:
                    os.remove(os.path.join(self.tmp, f))
                except OSError:
                    pass
            r, _ = self.go(mode)
            self.assert_refused(r, reason)
            self.assertTrue(r.containment["refusal"]["detail"] or reason in ("hello_unreadable", "hello_too_large"), mode)

    def test_the_handshake_directory_is_private(self):
        self.go("ok", cpu_seconds=5)
        names = [n for n in os.listdir(self.tmp) if n.startswith("mode_")]
        self.assertEqual(names, ["mode_700.dirmode"], "the owned handshake directory is created 0700")

    def test_a_valid_hello_from_a_stub_releases_go(self):
        with mock.patch.object(contained, "WORKER_PATH", self.stub("ok")):
            r = run({"cpu_seconds": 5, "handshake_timeout_seconds": 2.0}, code("pass"), timeout=10)
        self.assertTrue(r.containment["go_sent"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "saw_go")))
        self.assertEqual(r.containment["refusal"], None)


@unittest.skipUnless(os.name == "nt", "Windows Job Object tests: written to run on a Windows host; NOT run on the Linux author host")
class NativeWindows(TmpCase):
    """Behavioural checks for the Windows branch (new unnamed Job Object assigned by the worker to itself). Unrun on the author's host."""

    def test_accepted_small_target_with_cpu_and_memory(self):
        r = run({"cpu_seconds": 10, "memory_bytes": 256 * MIB}, code("print('ok')"))
        self.assertEqual((r.outcome, r.exit_code, r.stdout_bytes().strip()), (runner.EXITED_ZERO, 0, b"ok"), r.stderr.text())
        self.assertEqual(r.containment["applied"], {"cpu_seconds": 10, "memory_bytes": 256 * MIB})
        self.assertEqual(r.containment["platform"], "nt")
        self.assertIn("kill_on_job_close", r.containment["also_in_force"])

    def test_unsupported_max_file_bytes_refuses_before_launch(self):
        with no_process():
            r = run({"max_file_bytes": MIB}, code("pass"))
        self.assertEqual((r.outcome, r.containment["worker_started"]), (runner.CONTAINMENT_REFUSED, False))

    def test_memory_limit_makes_allocation_fail(self):
        r = run({"memory_bytes": 128 * MIB}, code(NativePosix.ALLOC), timeout=10)
        self.assertEqual(r.exit_code, 0, r.stderr.text())
        self.assertNotEqual(r.stdout_bytes().strip(), b"none")

    def test_cpu_limit_terminates_a_busy_target(self):
        t0 = time.monotonic()
        r = run({"cpu_seconds": 1}, code("while True:\n    pass"), timeout=10)
        self.assertLess(time.monotonic() - t0, 9)
        self.assertNotEqual(r.outcome, runner.EXITED_ZERO)

    def test_timeout_kills_the_whole_job_and_spares_a_sentinel(self):
        sentinel = subprocess.Popen(code("import time; time.sleep(60)"))
        self.addCleanup(lambda: (sentinel.kill(), sentinel.wait()))
        pidfile = os.path.join(self.tmp, "pids")
        script = os.path.join(self.tmp, "win_tree.py")
        with open(script, "w") as fh:
            fh.write("import subprocess, sys, time\nc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\nopen(sys.argv[1], 'w').write(str(c.pid))\ntime.sleep(60)\n")
        target = [PY, script, pidfile]
        r = run({"cpu_seconds": 30, "max_processes": 8}, target, timeout=3)
        self.assertEqual(r.outcome, runner.TIMEOUT)
        deadline = time.monotonic() + 5
        while not os.path.exists(pidfile) and time.monotonic() < deadline:
            time.sleep(0.05)
        with open(pidfile) as fh:
            child = int(fh.read())
        self.assertTrue(pid_dead(child) if hasattr(sys, "getwindowsversion") is False else self._gone(child), "kill-on-job-close removed the grandchild")
        self.assertIsNone(sentinel.poll())

    @staticmethod
    def _gone(pid):
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return True
        code_ = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code_))
        ctypes.windll.kernel32.CloseHandle(h)
        return code_.value != 259  # STILL_ACTIVE

    def test_nested_job_restriction_is_reported_as_unsupported_not_hidden(self):
        r = run({"cpu_seconds": 10}, code("pass"))
        if r.outcome == runner.CONTAINMENT_REFUSED:
            self.assertIn("AssignProcessToJobObject", json.dumps(r.containment["unsupported"]) + json.dumps(r.containment["refusal"]))
        else:
            self.assertEqual(r.outcome, runner.EXITED_ZERO)


if __name__ == "__main__":
    unittest.main()
