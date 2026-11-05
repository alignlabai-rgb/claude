"""Runner resource-boundary and cancellation regressions (audit findings F-RUN-1 and F-RUN-2, plus the interrupt coverage they imply).

Every child is an owned tiny `python -c` program; each scenario is bounded (<= 10 s outer, no child outlives its scenario), writes at most a few MB to a pipe (discarded by the
runner) and allocates nothing large. No FFmpeg and no media. Signals go only to this process (a SIGINT it handles) or to process groups the runner itself owns; an unrelated
sentinel process in its own session is checked to survive.
"""

import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from tests.helpers import POSIX, TmpCase, pid_dead
from tests.lifecycle_support import PY, interrupt_when, pid_alive, read_pid, sigint_default
from video_core import runner

BIG = 2_000_000
LIMIT = 1000


def py(code):
    return [PY, "-c", code]


class Sentinel:
    """An unrelated process in its own session; the runner must never signal it."""

    def __init__(self):
        self.proc = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"], start_new_session=True)

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


class DrainBound(unittest.TestCase):
    """F-RUN-1: exceeding max_drain_bytes is visible whether or not the child was still running when it was noticed."""

    def run_writer(self, code, **kw):
        kw.setdefault("timeout_s", 8)
        kw.setdefault("stdout_limits", (8, 8))
        kw.setdefault("max_drain_bytes", LIMIT)
        return runner.run_bounded(py(code), phase="t", **kw)

    def assert_truthful_termination(self, r):
        """Invariants that hold for EVERY scheduling (the kill/exit race is real and platform dependent): a kill is only reported if a request was accepted for the live leader,
        an actual exit 0 is never labelled killed, and the breach itself is always visible."""
        self.assertTrue(r.drain_limit_exceeded)
        self.assertFalse(r.ok)
        accepted_live = [q for q in r.termination_requests if q["state"] == runner.ST_ACCEPTED and not q["leader_exited_before"]]
        if r.outcome == runner.FLOOD_KILLED:
            self.assertEqual(r.kill_reason, "flood")
            self.assertTrue(accepted_live, "FLOOD_KILLED requires an accepted request for the live leader")
            self.assertTrue(r.kill_attempted and r.kill_delivered)
            self.assertNotEqual(r.exit_code, 0)
        else:
            self.assertEqual(r.outcome, runner.EXITED_ZERO)
            self.assertEqual((r.exit_code, r.signal, r.kill_reason), (0, None, None))
            self.assertFalse(accepted_live, "nothing was accepted for a live leader, so nothing may be called a kill")
            self.assertIsNot(r.kill_confirmed, True)
        if r.exit_code == 0:
            self.assertNotEqual(r.outcome, runner.FLOOD_KILLED, "an actual exit 0 is never reported as killed")

    def test_child_that_writes_beyond_the_bound_and_exits_at_once_is_never_a_clean_success(self):
        """Provenance is the CAPTURED stream, not the intended payload: a child still writing when the runner notices the breach may be killed mid-write (Windows: 1,769,472 of 2,000,000 bytes
        observed), so only the bytes actually read are counted and hashed. The completed-write / real-exit case with the full payload is test_exit_first_is_deterministic... below."""
        outcomes = set()
        for attempt in range(8):  # the original race depended on a 50 ms poll and, on Windows, on termination timing; every scheduling must satisfy the invariants
            r = self.run_writer(f"import sys; sys.stdout.buffer.write(b'x'*{BIG})")
            self.assert_truthful_termination(r)
            outcomes.add(r.outcome)
            self.assertTrue(r.drain_complete)
            s = r.stdout.summary()
            self.assertGreater(s["total_bytes"], LIMIT, "the breach itself means more than the bound was really read")
            self.assertLessEqual(s["total_bytes"], BIG)
            self.assertEqual(s["sha256_full_stream"], hashlib.sha256(b"x" * s["total_bytes"]).hexdigest(), "the hash covers exactly the bytes that were captured (all 'x')")
            if r.outcome == runner.EXITED_ZERO:
                self.assertEqual(s["total_bytes"], BIG, "a child that finished and exited on its own delivered the whole payload")
            self.assertEqual(r.stderr.total, 0)
        self.assertTrue(outcomes <= {runner.EXITED_ZERO, runner.FLOOD_KILLED})

    def test_a_child_that_is_still_writing_is_killed_and_the_captured_bytes_and_hash_are_exactly_what_was_read(self):
        """Deterministic still-writing control: 20 chunks of 100,000 bytes, 0.25 s apart (finite, <= 5 s); the runner must cut it off long before the end. Provenance: the
        total/hash describe only what was actually captured - no claim about bytes the child never delivered."""
        r = self.run_writer("import sys,time\nfor i in range(20):\n sys.stdout.buffer.write(b'x'*100000); sys.stdout.flush(); time.sleep(0.25)", timeout_s=8)
        s = r.stdout.summary()
        self.assertEqual((r.outcome, r.kill_reason, r.drain_limit_exceeded, r.ok), (runner.FLOOD_KILLED, "flood", True, False))
        self.assertTrue(r.kill_attempted and r.kill_delivered)
        self.assertGreater(s["total_bytes"], LIMIT)
        self.assertLess(s["total_bytes"], 20 * 100000, "it was stopped before it finished writing")
        self.assertEqual(s["sha256_full_stream"], hashlib.sha256(b"x" * s["total_bytes"]).hexdigest())
        self.assertEqual((s["kept_bytes"] + s["dropped_bytes"], s["truncated"]), (s["total_bytes"], True))

    def test_exit_first_is_deterministic_when_the_child_has_provably_exited_before_any_termination_request(self):
        """The public-API race with only a scheduling barrier: the real child exits 0 BEFORE the original termination routine runs."""
        original = runner._terminate

        def barrier(proc, grace):
            self.assertEqual(proc.wait(timeout=3), 0, "the owned child really exited 0 first")
            return original(proc, grace)
        with mock.patch.object(runner, "_terminate", barrier):
            r = runner.run_bounded(py(f"import sys,time;sys.stdout.buffer.write(b'x'*{BIG});sys.stdout.flush();time.sleep(0.2)"), phase="actual_exit_during_termination_race",
                                   timeout_s=5, stdout_limits=(8, 8), max_drain_bytes=LIMIT)
        self.assertEqual((r.outcome, r.exit_code, r.signal, r.kill_reason), (runner.EXITED_ZERO, 0, None, None), "an exited child is never reported killed")
        self.assertTrue(r.drain_limit_exceeded)
        self.assertFalse(r.ok)
        self.assertEqual((r.kill_attempted, r.kill_delivered, r.kill_confirmed, r.orphan_group_killed), (False, False, None, False))
        self.assertTrue(r.termination_requests, "the attempt is on record")
        self.assertEqual({q["state"] for q in r.termination_requests}, {runner.ST_NOT_SENT})
        self.assertTrue(all(q["leader_exited_before"] for q in r.termination_requests))
        self.assertEqual(r.stdout.summary()["sha256_full_stream"], hashlib.sha256(b"x" * BIG).hexdigest())
        self.assertEqual(r.stdout.total, BIG)
        self.assertNotIn("killed", r.describe())

    def test_timeout_race_keeps_the_timeout_label_but_never_claims_delivery(self):
        original = runner._terminate

        def barrier(proc, grace):
            self.assertEqual(proc.wait(timeout=3), 0)
            return original(proc, grace)
        with mock.patch.object(runner, "_terminate", barrier):
            r = runner.run_bounded(py("import time; time.sleep(0.6)"), phase="timeout_race", timeout_s=0.3)
        self.assertEqual((r.outcome, r.timed_out, r.exit_code), (runner.TIMEOUT, True, 0), "the deadline did pass; the real exit status is kept")
        self.assertEqual((r.kill_attempted, r.kill_delivered, r.kill_confirmed), (False, False, None))
        self.assertEqual({q["state"] for q in r.termination_requests}, {runner.ST_NOT_SENT})

    def test_lingering_child_is_killed_and_both_facts_are_recorded(self):
        """Positive live-kill control. POSIX: killed by our SIGTERM (no exit code, signal SIGTERM, confirmed by the exit status). Windows: TerminateProcess forces exit code 1 and
        there are no signals, so the exit status cannot confirm it (None) - the request was accepted for a live process."""
        t0 = time.monotonic()
        r = self.run_writer(f"import sys, time; sys.stdout.buffer.write(b'x'*{BIG}); sys.stdout.flush(); time.sleep(5)")
        self.assertLess(time.monotonic() - t0, 6)
        self.assertEqual((r.outcome, r.kill_reason), (runner.FLOOD_KILLED, "flood"))
        self.assertTrue(r.drain_limit_exceeded and r.kill_attempted and r.kill_delivered)
        self.assertFalse(r.ok)
        if POSIX:
            self.assertEqual((r.exit_code, r.signal, r.kill_confirmed), (None, signal.SIGTERM, True))
        else:
            self.assertEqual((r.exit_code, r.signal, r.kill_confirmed), (1, None, None))
        first = r.termination_requests[0]
        self.assertEqual((first["state"], first["leader_exited_before"]), (runner.ST_ACCEPTED, False))

    def test_boundary_exactly_at_the_limit_is_fine_one_byte_over_is_not(self):
        at = self.run_writer(f"import sys; sys.stdout.buffer.write(b'y'*{LIMIT})")
        over = self.barrier_run(f"import sys,time; sys.stdout.buffer.write(b'y'*{LIMIT + 1}); sys.stdout.flush(); time.sleep(0.2)")  # barrier: exits on its own before any termination
        under = self.run_writer("import sys; sys.stdout.buffer.write(b'y'*10)")
        self.assertEqual([(r.ok, r.drain_limit_exceeded) for r in (under, at, over)], [(True, False), (True, False), (False, True)])
        self.assertEqual((over.outcome, over.exit_code), (runner.EXITED_ZERO, 0))

    def test_the_bound_is_shared_by_stdout_and_stderr(self):
        r = self.barrier_run(f"import sys,time; sys.stdout.write('a'*{LIMIT // 2 + 1}); sys.stdout.flush(); sys.stderr.write('b'*{LIMIT // 2 + 1}); sys.stderr.flush(); time.sleep(0.2)", stderr_limits=(8, 8))
        self.assertTrue(r.drain_limit_exceeded)
        self.assertFalse(r.ok)
        self.assertEqual((r.stdout.total, r.stderr.total), (LIMIT // 2 + 1, LIMIT // 2 + 1))

    def barrier_run(self, code, **kw):
        """Deterministic: the real child reaches its own end (exit status fixed by its code) BEFORE the runner's termination routine runs, so the natural status must survive."""
        original = runner._terminate

        def barrier(proc, grace):
            proc.wait(timeout=5)
            return original(proc, grace)
        with mock.patch.object(runner, "_terminate", barrier):
            return self.run_writer(code, **kw)

    def test_natural_nonzero_exit_survives_a_breach_deterministically(self):
        r = self.barrier_run(f"import sys,time; sys.stdout.buffer.write(b'x'*{BIG}); sys.stdout.flush(); time.sleep(0.2); sys.exit(3)")
        self.assertEqual((r.outcome, r.exit_code, r.signal, r.kill_reason, r.drain_limit_exceeded), (runner.NONZERO_EXIT, 3, None, None, True))
        self.assertEqual((r.kill_attempted, r.kill_delivered, r.ok), (False, False, False))
        self.assertEqual({q["state"] for q in r.termination_requests}, {runner.ST_NOT_SENT})

    @unittest.skipUnless(POSIX, "POSIX only: a child killing itself with SIGKILL; Windows has no signals (skipped on Windows, not Windows evidence)")
    def test_natural_signal_death_survives_a_breach_deterministically(self):
        r = self.barrier_run(f"import os, signal, sys, time; sys.stdout.buffer.write(b'x'*{BIG}); sys.stdout.flush(); time.sleep(0.2); os.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual((r.outcome, r.exit_code, r.signal, r.kill_reason, r.drain_limit_exceeded, r.kill_attempted), (runner.SIGNALED, None, signal.SIGKILL, None, True, False))

    def test_unbarriered_flooding_nonzero_child_is_either_its_own_exit_3_or_a_real_kill_never_a_mix(self):
        """Scheduling decides which happens first (a child still flooding can be killed before its intended exit 3; Windows then reports exit code 1, POSIX signal SIGTERM).
        The ACTUAL result is preserved and must be internally consistent."""
        for _ in range(4):
            r = self.run_writer(f"import sys; sys.stdout.buffer.write(b'x'*{BIG}); sys.exit(3)")
            self.assertTrue(r.drain_limit_exceeded)
            if r.outcome == runner.NONZERO_EXIT:
                self.assertEqual((r.exit_code, r.signal, r.kill_reason), (3, None, None))
            else:
                self.assertEqual((r.outcome, r.kill_reason), (runner.FLOOD_KILLED, "flood"))
                self.assertTrue(any(q["state"] == runner.ST_ACCEPTED and not q["leader_exited_before"] for q in r.termination_requests))
                self.assertEqual((r.exit_code, r.signal), (None, signal.SIGTERM) if POSIX else (1, None))

    def test_true_live_kill_and_timeout_controls_keep_their_distinct_semantics(self):
        live = self.run_writer(f"import sys,time; sys.stdout.buffer.write(b'x'*{BIG}); sys.stdout.flush(); time.sleep(10)")
        self.assertEqual((live.outcome, live.kill_reason, live.kill_delivered, live.drain_limit_exceeded), (runner.FLOOD_KILLED, "flood", True, True))
        to = self.run_writer("import time; time.sleep(30)", timeout_s=0.5)
        self.assertEqual((to.outcome, to.timed_out, to.drain_limit_exceeded, to.kill_attempted, to.kill_delivered), (runner.TIMEOUT, True, False, True, True))
        self.assertEqual((to.exit_code, to.signal), (None, signal.SIGTERM) if POSIX else (1, None))

    def test_unbreached_runs_keep_their_old_shape(self):
        r = self.run_writer("import sys; sys.stdout.write('hello'); sys.stderr.write('warn')", stdout_limits=(64, 0), stderr_limits=(64, 0), max_drain_bytes=1 << 20)
        self.assertTrue(r.ok)
        self.assertEqual((r.outcome, r.exit_code, r.stdout_bytes(), r.kill_attempted, r.drain_limit_exceeded, r.drain_limit_bytes), (runner.EXITED_ZERO, 0, b"hello", False, False, 1 << 20))
        d = r.to_dict()
        self.assertEqual((d["drain_limit_exceeded"], d["kill_attempted"], d["kill_delivered"], d["drain_limit_bytes"]), (False, False, False, 1 << 20))
        self.assertIn("EXITED_ZERO", r.describe())

    def test_describe_and_reason_text_do_not_claim_a_kill_that_did_not_happen(self):
        quick = self.barrier_run(f"import sys,time; sys.stdout.buffer.write(b'x'*{BIG}); sys.stdout.flush(); time.sleep(0.2)")
        self.assertIn("exited on its own", quick.exit0_not_ok_reason("tool"))
        self.assertNotIn("killed", quick.describe())
        self.assertIn("no kill was needed or performed", quick.exit0_not_ok_reason("tool"))
        slow = self.run_writer(f"import sys, time; sys.stdout.buffer.write(b'x'*{BIG}); sys.stdout.flush(); time.sleep(5)")
        self.assertIn("runner killed it", slow.describe())

    @unittest.skipUnless(POSIX, "process groups")
    def test_orphan_that_keeps_flooding_after_the_leader_exits_is_cut_off_promptly(self):
        """The leader exits 0 at once; a grandchild in the owned group keeps the pipe open and keeps writing (finite: <= ~6 s). Previously the runner only noticed after the
        whole drain timeout; now the breach itself ends it."""
        child = ("import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
                 "\"import sys, time\\nend = time.time() + 6\\nwhile time.time() < end:\\n sys.stdout.write('z' * 4096); sys.stdout.flush(); time.sleep(0.005)\"])")
        t0 = time.monotonic()
        r = self.run_writer(child, timeout_s=8, drain_timeout_s=5.0, grace_s=1.0)
        self.assertLess(time.monotonic() - t0, 3.5, "must not wait out the 5 s drain timeout")
        self.assertTrue(r.drain_limit_exceeded)
        self.assertTrue(r.orphan_group_killed and r.kill_attempted and r.kill_delivered)
        self.assertEqual((r.outcome, r.exit_code), (runner.EXITED_ZERO, 0), "the leader's own exit is still what it was")
        self.assertFalse(r.ok)
        self.assertTrue(r.drain_complete)


class Descriptions(unittest.TestCase):
    def test_raise_if_interrupted_contract(self):
        ok = runner.RunResult(phase="p", argv=["x"], outcome=runner.EXITED_ZERO)
        self.assertIs(runner.raise_if_interrupted(ok), ok)
        for outcome in (runner.TIMEOUT, runner.NONZERO_EXIT, runner.FLOOD_KILLED, runner.LAUNCH_FAILED, runner.CONTAINMENT_REFUSED, runner.SIGNALED):
            runner.raise_if_interrupted(runner.RunResult(phase="p", argv=["x"], outcome=outcome))
        res = runner.RunResult(phase="phase-1", argv=["x"], outcome=runner.INTERRUPTED)
        with self.assertRaises(KeyboardInterrupt) as cm:
            runner.raise_if_interrupted(res)
        self.assertIsInstance(cm.exception, runner.RunInterrupted)
        self.assertIs(cm.exception.result, res)
        self.assertIn("phase-1", str(cm.exception))


@unittest.skipUnless(POSIX, "POSIX process groups and signals; Windows interrupt behaviour is tested separately by the requester")
class InterruptCleanup(TmpCase):
    """F-RUN-2 and its neighbours: a KeyboardInterrupt in ANY phase after the child exists ends with the owned group cleaned up and the usual INTERRUPTED RunResult
    (existing API semantics), and nothing outside the group is touched."""

    def setUp(self):
        super().setUp()
        self.sentinel = Sentinel()
        self.addCleanup(self.sentinel.close)
        self.addCleanup(self.kill_leftovers)
        self.pids = []

    def kill_leftovers(self):
        for pid in self.pids:  # our own test grandchildren, if a test failed before they were reaped
            if pid_alive(pid):
                os.kill(pid, signal.SIGKILL)

    def child_program(self, tag, *, leader_sleep=0.0, grandchild_seconds=5, ignore_term=False):
        marker = os.path.join(self.tmp, tag + ".pid")
        gc = (f"import os, time\\nopen({marker!r}, 'w').write(str(os.getpid()))\\n" + ("import signal\\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\\n" if ignore_term else "")
              + f"time.sleep({grandchild_seconds})")
        code = f"import subprocess, sys, time, os\nsubprocess.Popen([sys.executable, '-c', \"{gc}\"])\n" \
               f"while not os.path.exists({marker!r}):\n time.sleep(0.01)\ntime.sleep({leader_sleep})\n"
        return marker, py(code)

    def assert_clean(self, marker, r, t0, limit):
        gc = read_pid(marker)
        self.pids.append(gc)
        self.assertTrue(pid_dead(gc, wait=4), "a member of the owned group survived the unwinding of run_bounded")
        self.assertLess(time.monotonic() - t0, limit)
        self.assertTrue(self.sentinel.alive(), "an unrelated process in its own session must survive")

    def test_interrupt_during_the_final_pipe_drain_wait_cleans_the_group(self):
        """The audit reproducer's trigger: the leader exits 0 leaving a pipe-holding grandchild; SIGINT arrives while run_bounded waits for EOF. The audit's own harness expected
        the KeyboardInterrupt to ESCAPE; the specified API behaviour (kept) is to convert an interrupt into outcome INTERRUPTED, so the assertion here is on that plus cleanup."""
        marker, argv = self.child_program("drain")
        t0 = time.monotonic()
        with sigint_default():
            interrupt_when(marker, delay=0.6)
            r = runner.run_bounded(argv, phase="t", timeout_s=9, drain_timeout_s=4.0, grace_s=1.0)
        self.assertEqual((r.outcome, r.kill_reason), (runner.INTERRUPTED, "interrupt"))
        self.assertEqual((r.exit_code, r.signal), (0, None), "the leader had really exited 0; that is not rewritten")
        self.assertTrue(r.kill_attempted and r.kill_delivered, "the surviving group member was signalled")
        self.assertTrue(r.drain_complete, "pipes reached EOF once the member was gone")
        self.assertFalse(r.ok)
        self.assert_clean(marker, r, t0, 3.5)

    def test_interrupt_during_the_drain_wait_when_the_member_ignores_sigterm_is_still_bounded(self):
        marker, argv = self.child_program("drainterm", ignore_term=True)
        t0 = time.monotonic()
        with sigint_default():
            interrupt_when(marker, delay=0.5)
            r = runner.run_bounded(argv, phase="t", timeout_s=9, drain_timeout_s=4.0, grace_s=0.7)
        self.assertEqual(r.outcome, runner.INTERRUPTED)
        self.assertTrue(r.kill_delivered)
        self.assert_clean(marker, r, t0, 4.0)

    def test_interrupt_while_the_leader_is_still_running_reaps_leader_and_member(self):
        marker, argv = self.child_program("wait", leader_sleep=30)
        t0 = time.monotonic()
        with sigint_default():
            interrupt_when(marker, delay=0.3)
            r = runner.run_bounded(argv, phase="t", timeout_s=9, grace_s=1.0)
        self.assertEqual((r.outcome, r.kill_reason, r.timed_out), (runner.INTERRUPTED, "interrupt", False))
        self.assertEqual(r.signal, signal.SIGTERM)
        self.assertTrue(r.kill_attempted and r.kill_delivered)
        self.assert_clean(marker, r, t0, 4.0)

    def test_a_second_interrupt_during_the_cleanup_escalates_to_sigkill_instead_of_abandoning_it(self):
        ready = os.path.join(self.tmp, "ready")
        prog = py(f"import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen({ready!r}, 'w').write('1')\ntime.sleep(30)")
        t0 = time.monotonic()
        with sigint_default():
            interrupt_when(ready, delay=0.3)  # first cancel: the runner sends SIGTERM, which this child ignores, and waits `grace` (5 s)
            timer = threading.Timer(1.2, lambda: os.kill(os.getpid(), signal.SIGINT))  # second cancel, inside that wait
            timer.start()
            try:
                r = runner.run_bounded(prog, phase="t", timeout_s=9, grace_s=5.0)
            finally:
                timer.cancel()
        self.assertEqual(r.outcome, runner.INTERRUPTED)
        self.assertEqual(r.signal, signal.SIGKILL, "the escalation killed it; the 5 s grace was not waited out")
        self.assertLess(time.monotonic() - t0, 3.5)
        self.assertTrue(self.sentinel.alive())

    def test_interrupt_while_starting_the_drain_threads_kills_the_child_and_does_not_hang(self):
        real_start = threading.Thread.start
        calls = []

        def start(self_thread):
            calls.append(1)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            return real_start(self_thread)

        t0 = time.monotonic()
        with mock.patch.object(threading.Thread, "start", start):
            r = runner.run_bounded(py("import time; time.sleep(30)"), phase="t", timeout_s=9, grace_s=1.0, drain_timeout_s=3.0)
        self.assertEqual((r.outcome, r.kill_reason), (runner.INTERRUPTED, "interrupt"))
        self.assertTrue(r.kill_attempted and r.kill_delivered)
        self.assertLess(time.monotonic() - t0, 3.0, "pipes whose reader never started are not waited for")
        self.assertTrue(self.sentinel.alive())

    def test_interrupt_inside_the_after_start_hook_is_unchanged(self):
        def hook(proc):
            raise KeyboardInterrupt()

        r = runner.run_bounded(py("import time; time.sleep(30)"), phase="t", timeout_s=9, grace_s=1.0, after_start=hook)
        self.assertEqual((r.outcome, r.kill_reason), (runner.INTERRUPTED, "interrupt"))
        self.assertTrue(r.kill_attempted and r.kill_delivered)

    def test_an_interrupt_after_a_timeout_kill_is_recorded_as_interrupted_but_keeps_timed_out(self):
        """timeout fires (leader killed, member keeps the pipe) and the user cancels while the runner waits for EOF."""
        t0 = time.monotonic()
        with sigint_default():
            # the timeout at 0.8 s TERMs the whole group; use a member that ignores TERM so the pipe stays open into the drain phase
            marker2, argv2 = self.child_program("afto2", leader_sleep=30, ignore_term=True)
            interrupt_when(marker2, delay=1.4)
            r = runner.run_bounded(argv2, phase="t", timeout_s=0.8, grace_s=0.3, drain_timeout_s=4.0)
        self.assertEqual(r.outcome, runner.INTERRUPTED)
        self.assertTrue(r.timed_out, "the timeout really happened and stays recorded")
        self.assert_clean(marker2, r, t0, 4.5)


if __name__ == "__main__":
    unittest.main()
