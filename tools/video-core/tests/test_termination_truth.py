"""Truthful termination reporting (Windows provenance finding on 59db31e: `_signal_group` returned True after `Popen.terminate()` had silently become a no-op for an already-exited
child, so `kill_delivered`/FLOOD_KILLED were reported for a child that had exited 0 on its own).

What CPython's Windows `Popen.terminate()` does (3.11-3.13): `if self.returncode is not None: return` (a silent no-op, no result); otherwise
`_winapi.TerminateProcess(self._handle, 1)`, and an `ERROR_ACCESS_DENIED` (PermissionError) is swallowed when `GetExitCodeProcess` shows the process is no longer active. So its return
value (None) can never say whether anything was sent. `runner._request` therefore (a) never sends to a child it already knows exited, (b) uses the owned process handle with the same
call and reports the result - success only for a live process, ACCESS_DENIED + exited = nothing sent - and (c) falls back to `Popen.terminate()` reporting `issued_unconfirmed`.

Real owned children are used wherever the platform allows (any OS: the exited-child probe, the barrier race, the live-kill control). The Windows branch of `_request` is exercised on
Linux with fakes standing in for `_winapi` and the Popen handle: that checks the LOGIC only and is NOT Windows execution evidence. Windows-only real scenarios are skipped here with an
explicit reason and are meant to be run by the requester on Windows.
"""

import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from tests.helpers import POSIX
from video_core import runner

PY = sys.executable


class FakeHandleApi:
    STILL_ACTIVE = 259

    def __init__(self, terminate_error=None, exit_code=None):
        self.terminate_error, self.exit_code, self.calls = terminate_error, exit_code, []

    def TerminateProcess(self, handle, code):
        self.calls.append(("TerminateProcess", handle, code))
        if self.terminate_error:
            raise self.terminate_error

    def GetExitCodeProcess(self, handle):
        self.calls.append(("GetExitCodeProcess", handle))
        return self.STILL_ACTIVE if self.exit_code is None else self.exit_code


class FakeProc:
    def __init__(self, returncode=None, handle=object(), pid=4242, exits_after_poll=False):
        self.returncode, self._handle, self.pid, self.terminated = returncode, handle, pid, 0
        self._exits_after_poll = exits_after_poll

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1


class WindowsBranchLogic(unittest.TestCase):
    """SIMULATED Windows branch (POSIX flag forced off, fakes for _winapi and the handle). Not Windows evidence."""

    def request(self, proc, api=None):
        with mock.patch.object(runner, "POSIX", False), mock.patch.object(runner, "_winapi", api):
            return runner._request(proc, signal.SIGTERM)

    def test_known_exited_child_is_never_sent_anything(self):
        api = FakeHandleApi()
        proc = FakeProc(returncode=0)
        self.assertEqual(self.request(proc, api), (runner.ST_NOT_SENT, True))
        self.assertEqual((api.calls, proc.terminated), ([], 0))

    def test_live_child_terminate_accepted(self):
        api = FakeHandleApi()
        h = object()
        proc = FakeProc(handle=h)
        self.assertEqual(self.request(proc, api), (runner.ST_ACCEPTED, False))
        self.assertEqual(api.calls, [("TerminateProcess", h, 1)])
        self.assertEqual(proc.terminated, 0, "the owned handle was used; Popen.terminate() (which hides the result) was not")

    def test_child_exits_between_the_poll_and_the_call_is_nothing_sent_not_a_kill(self):
        """The window a poll-only guard cannot close: poll() says running, the process dies, TerminateProcess fails with ACCESS_DENIED, the exit code is not STILL_ACTIVE."""
        api = FakeHandleApi(terminate_error=PermissionError(13, "Access is denied"), exit_code=0)
        proc = FakeProc()
        self.assertEqual(self.request(proc, api), (runner.ST_NOT_SENT, True))
        self.assertEqual(proc.returncode, 0, "same bookkeeping as Popen.terminate()")
        self.assertEqual(proc.terminated, 0)

    def test_access_denied_while_still_active_is_a_refusal_not_a_delivery(self):
        api = FakeHandleApi(terminate_error=PermissionError(13, "Access is denied"), exit_code=None)
        self.assertEqual(self.request(FakeProc(), api), ("refused:PermissionError", False))

    def test_other_os_errors_are_refusals(self):
        api = FakeHandleApi(terminate_error=OSError(6, "invalid handle"))
        self.assertEqual(self.request(FakeProc(), api), ("refused:OSError", False))

    def test_fallback_without_the_owned_handle_preserves_uncertainty(self):
        proc = FakeProc(handle=None)
        self.assertEqual(self.request(proc, None), (runner.ST_UNCONFIRMED, False))
        self.assertEqual(proc.terminated, 1)
        gone = FakeProc(returncode=3, handle=None)
        self.assertEqual(self.request(gone, None), (runner.ST_NOT_SENT, True))
        self.assertEqual(gone.terminated, 0, "even the fallback never calls terminate() on a known-exited child")

    def test_signal_group_is_true_only_for_an_accepted_request_and_logs_it(self):
        log = []
        runner._tls.log = log
        self.addCleanup(setattr, runner._tls, "log", None)
        with mock.patch.object(runner, "POSIX", False):
            with mock.patch.object(runner, "_winapi", FakeHandleApi()):
                self.assertTrue(runner._signal_group(FakeProc(), signal.SIGTERM))
                self.assertFalse(runner._signal_group(FakeProc(returncode=0), signal.SIGTERM))
            self.assertFalse(runner._signal_group(FakeProc(handle=None), signal.SIGTERM), "unconfirmed is not 'accepted'")
        self.assertEqual([(e["signal"], e["state"], e["leader_exited_before"]) for e in log],
                         [("TerminateProcess", "accepted", False), ("TerminateProcess", "not_sent_already_exited", True), ("TerminateProcess", "issued_unconfirmed", False)])


class SettledFacts(unittest.TestCase):
    def settle(self, reqs, rc, kill_reason="flood", posix=True):
        res = runner.RunResult(phase="p", argv=["x"], outcome=runner.LAUNCH_FAILED, kill_reason=kill_reason, termination_requests=reqs)
        with mock.patch.object(runner, "POSIX", posix):
            runner._settle_termination_facts(res, rc)
        return res

    @staticmethod
    def req(state, exited=False, sig="SIGTERM"):
        return {"signal": sig, "state": state, "leader_exited_before": exited}

    def test_flood_with_nothing_sent_is_not_a_kill(self):
        for posix in (True, False):
            r = self.settle([self.req(runner.ST_NOT_SENT, True)], 0, posix=posix)
            self.assertEqual((r.kill_reason, r.kill_attempted, r.kill_delivered, r.kill_confirmed), (None, False, False, None))

    def test_flood_where_only_survivors_were_reached_is_not_a_kill_of_the_leader(self):
        r = self.settle([self.req(runner.ST_ACCEPTED, True)], 0)
        self.assertEqual((r.kill_reason, r.kill_attempted, r.kill_delivered), (None, True, True))
        self.assertIs(r.kill_confirmed, False, "requests were issued but the leader's status does not show our signal")

    def test_posix_live_kill_is_confirmed_by_the_signal_in_the_exit_status(self):
        r = self.settle([self.req(runner.ST_ACCEPTED)], -signal.SIGTERM if POSIX else -15)
        self.assertEqual((r.kill_reason, r.kill_attempted, r.kill_delivered, r.kill_confirmed), ("flood", True, True, True))

    def test_windows_live_kill_is_accepted_but_never_claimed_confirmed(self):
        r = self.settle([self.req(runner.ST_ACCEPTED)], 1, posix=False)
        self.assertEqual((r.kill_reason, r.kill_delivered, r.kill_confirmed), ("flood", True, None))

    def test_windows_unconfirmed_request_needs_the_forced_exit_code_to_keep_the_kill_label(self):
        forced = self.settle([self.req(runner.ST_UNCONFIRMED)], 1, posix=False)
        natural = self.settle([self.req(runner.ST_UNCONFIRMED)], 0, posix=False)
        self.assertEqual((forced.kill_reason, forced.kill_attempted, forced.kill_delivered), ("flood", True, False))
        self.assertEqual((natural.kill_reason, natural.kill_attempted, natural.kill_delivered), (None, True, False), "exit 0 cannot be the forced code: it exited on its own")

    def test_refused_requests_count_as_attempted_not_delivered(self):
        r = self.settle([self.req("refused:PermissionError")], 0, kill_reason="timeout")
        self.assertEqual((r.kill_reason, r.kill_attempted, r.kill_delivered), ("timeout", True, False))

    def test_timeout_and_interrupt_labels_are_never_rewritten(self):
        for reason in ("timeout", "interrupt"):
            r = self.settle([self.req(runner.ST_NOT_SENT, True)], 0, kill_reason=reason)
            self.assertEqual(r.kill_reason, reason)


class RealOwnedChildren(unittest.TestCase):
    """Real owned children, on whatever OS runs the suite (Linux evidence here)."""

    def test_signal_group_on_an_already_exited_child_is_false(self):
        """The independently demonstrated probe: the child is KNOWN exited, nothing may be reported delivered."""
        p = subprocess.Popen([PY, "-c", "pass"])
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertIs(runner._signal_group(p, signal.SIGTERM), False)
        state, exited = runner._request(p, signal.SIGTERM)
        self.assertEqual((state, exited), (runner.ST_NOT_SENT, True))

    @unittest.skipUnless(POSIX, "needs waitid(WNOWAIT) to observe a zombie without reaping it")
    def test_exited_but_not_yet_reaped_child_is_also_not_sent_to(self):
        import os
        p = subprocess.Popen([PY, "-c", "pass"], start_new_session=True)
        info = os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOWAIT)  # blocks until it has exited, WITHOUT reaping: a zombie leader
        self.assertEqual(info.si_pid, p.pid)
        self.assertIsNone(p.returncode, "Popen has not reaped it yet")
        state, exited = runner._request(p, signal.SIGTERM)
        self.assertEqual((state, exited), (runner.ST_NOT_SENT, True), "killpg on a zombie would 'succeed'; the poll() first prevents reporting that as a delivery")
        self.assertEqual(p.returncode, 0)

    def test_live_child_request_is_accepted_and_the_child_dies(self):
        p = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"], start_new_session=POSIX)
        try:
            state, exited = runner._request(p, signal.SIGTERM)
            self.assertEqual((state, exited), (runner.ST_ACCEPTED, False))
            p.wait(timeout=5)
            self.assertIsNotNone(p.returncode)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()

    def test_public_barrier_race_for_every_phase_reports_not_sent(self):
        original = runner._terminate

        def barrier(proc, grace):
            self.assertEqual(proc.wait(timeout=3), 0)
            return original(proc, grace)
        with mock.patch.object(runner, "_terminate", barrier):
            r = runner.run_bounded([PY, "-c", "import sys,time;sys.stdout.buffer.write(b'x'*2000000);sys.stdout.flush();time.sleep(0.2)"], phase="actual_exit_during_termination_race",
                                   timeout_s=5, stdout_limits=(8, 8), max_drain_bytes=1000)
        d = r.to_dict()
        self.assertEqual((d["outcome"], d["exit_code"], d["kill_attempted"], d["kill_delivered"], d["drain_limit_exceeded"]), ("EXITED_ZERO", 0, False, False, True))
        self.assertEqual(d["termination_requests"][0]["state"], "not_sent_already_exited")

    def test_the_wrapper_form_that_forgets_to_return_the_original_result_is_still_truthful(self):
        original = runner._terminate

        def barrier(proc, grace):
            proc.wait(timeout=3)
            original(proc, grace)  # no return
        with mock.patch.object(runner, "_terminate", barrier):
            r = runner.run_bounded([PY, "-c", "import sys,time;sys.stdout.buffer.write(b'x'*2000000);sys.stdout.flush();time.sleep(0.2)"], phase="t", timeout_s=5,
                                   stdout_limits=(8, 8), max_drain_bytes=1000)
        self.assertEqual((r.outcome, r.kill_delivered, r.ok), (runner.EXITED_ZERO, False, False))

    @unittest.skipUnless(not POSIX, "Windows only: TerminateProcess on a real owned child; skipped on this OS - to be run by the requester on Windows, not Windows evidence here")
    def test_windows_live_kill_forces_exit_code_1_and_reports_accepted_unconfirmed(self):
        r = runner.run_bounded([PY, "-c", "import time; time.sleep(30)"], phase="t", timeout_s=0.5)
        self.assertEqual((r.outcome, r.exit_code, r.signal, r.kill_delivered, r.kill_confirmed), (runner.TIMEOUT, 1, None, True, None))
        self.assertEqual(r.termination_requests[0]["state"], runner.ST_ACCEPTED)

    @unittest.skipUnless(not POSIX, "Windows only: real already-exited handle through the owned-handle path; skipped on this OS - not Windows evidence here")
    def test_windows_exited_child_handle_path_reports_not_sent(self):
        p = subprocess.Popen([PY, "-c", "pass"])
        end = time.monotonic() + 5
        while p.returncode is None and time.monotonic() < end:
            time.sleep(0.01)
            import _winapi
            if _winapi.GetExitCodeProcess(p._handle) != _winapi.STILL_ACTIVE:
                break
        self.assertEqual(runner._request(p, signal.SIGTERM)[0], runner.ST_NOT_SENT)


if __name__ == "__main__":
    unittest.main()
