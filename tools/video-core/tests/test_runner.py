"""Runner behaviour against OWNED test subprocesses (our own python -c children)."""

import hashlib
import os
import subprocess
import sys
import time
import unittest

from tests.helpers import POSIX, TmpCase
from video_core import runner


def py(code, *args):
    return [sys.executable, "-c", code, *args]


def pid_dead(pid, wait=5.0):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except OSError:
            return True
        time.sleep(0.05)
    return False


class Outcomes(TmpCase):
    def test_clean_exit_records_everything_separately(self):
        r = runner.run_bounded(py("import sys; print('out'); sys.stderr.write('err')"), phase="p", timeout_s=20, stdout_limits=(100, 0), stderr_limits=(100, 0))
        self.assertEqual((r.outcome, r.exit_code, r.signal, r.timed_out, r.drain_complete, r.orphan_group_killed), (runner.EXITED_ZERO, 0, None, False, True, False))
        self.assertTrue(r.ok)
        self.assertEqual(r.stdout_bytes().strip(), b"out")
        self.assertEqual(r.to_dict()["phase"], "p")
        self.assertEqual(r.stderr.text(), "err")

    def test_nonzero_exit_is_not_a_signal_and_not_a_timeout(self):
        r = runner.run_bounded(py("import sys; sys.exit(3)"), phase="x", timeout_s=20)
        self.assertEqual((r.outcome, r.exit_code, r.signal, r.timed_out), (runner.NONZERO_EXIT, 3, None, False))
        self.assertFalse(r.ok)

    def test_timeout_kills_only_the_owned_child_and_records_it(self):
        bystander = subprocess.Popen(py("import time; time.sleep(60)"), start_new_session=True)
        try:
            t0 = time.monotonic()
            r = runner.run_bounded(py("import time; time.sleep(60)"), phase="render", timeout_s=0.5)
            self.assertLess(time.monotonic() - t0, 15)
            self.assertEqual((r.outcome, r.timed_out, r.kill_reason), (runner.TIMEOUT, True, "timeout"))
            if POSIX:
                self.assertIn(r.signal, (15, 9))
            self.assertIsNone(r.exit_code)
            self.assertFalse(r.ok)
            self.assertIsNone(bystander.poll(), "an unrelated process must never be killed")
        finally:
            bystander.kill()
            bystander.wait()

    @unittest.skipUnless(POSIX, "POSIX signals")
    def test_external_signal_is_distinguished_from_timeout(self):
        r = runner.run_bounded(py("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"), phase="render", timeout_s=20)
        self.assertEqual((r.outcome, r.signal, r.exit_code, r.timed_out, r.kill_reason), (runner.SIGNALED, 9, None, False, None))

    @unittest.skipUnless(POSIX, "POSIX signals")
    def test_sigterm_from_the_child_itself(self):
        r = runner.run_bounded(py("import os, signal; os.kill(os.getpid(), signal.SIGTERM)"), phase="render", timeout_s=20)
        self.assertEqual((r.outcome, r.signal), (runner.SIGNALED, 15))

    def test_launch_failure_has_no_exit_code(self):
        r = runner.run_bounded([os.path.join(self.tmp, "does-not-exist")], phase="render", timeout_s=5)
        self.assertEqual(r.outcome, runner.LAUNCH_FAILED)
        self.assertIsNone(r.exit_code)
        self.assertIn("FileNotFoundError", r.launch_error)

    def test_argv_is_validated(self):
        for bad in ([], "ls", [1], ["a\x00b"]):
            with self.assertRaises(TypeError):
                runner.run_bounded(bad, phase="x", timeout_s=1)

    def test_no_retry(self):
        counter = os.path.join(self.tmp, "count")
        code = "import sys; open(sys.argv[1], 'a').write('x'); sys.exit(1)"
        r = runner.run_bounded(py(code, counter), phase="x", timeout_s=20)
        self.assertEqual(r.outcome, runner.NONZERO_EXIT)
        with open(counter) as fh:
            self.assertEqual(fh.read(), "x")


class NoShell(TmpCase):
    def test_metacharacters_are_literal_argv_data(self):
        nasty = "a; touch PWNED $(id) `id` | & > f ' \" \\ テスト"
        r = runner.run_bounded(py("import sys; sys.stdout.buffer.write(sys.argv[1].encode())", nasty), phase="x", timeout_s=20, stdout_limits=(1000, 0), cwd=self.tmp)
        self.assertEqual(r.stdout_bytes().decode(), nasty)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_shell_true_is_not_used(self):
        import inspect
        src = inspect.getsource(runner.run_bounded)
        self.assertIn("shell=False", src)
        self.assertNotIn("shell=True", src)

    def test_stdin_is_closed(self):
        r = runner.run_bounded(py("import sys; sys.stdout.write(repr(sys.stdin.read()))"), phase="x", timeout_s=20, stdout_limits=(100, 0))
        self.assertEqual(r.stdout_bytes(), b"''")


class Diagnostics(TmpCase):
    def test_truncated_diagnostics_keep_head_tail_totals_and_full_hash(self):
        code = "import sys; sys.stderr.write('H'*50 + 'x'*1000000 + 'T'*50)"
        r = runner.run_bounded(py(code), phase="render", timeout_s=30, stderr_limits=(100, 100))
        s = r.stderr.summary()
        self.assertEqual((s["total_bytes"], s["kept_bytes"], s["dropped_bytes"], s["truncated"]), (1_000_100, 200, 999_900, True))
        self.assertEqual(s["sha256_full_stream"], hashlib.sha256(("H" * 50 + "x" * 1_000_000 + "T" * 50).encode()).hexdigest())
        text = r.stderr.text()
        self.assertTrue(text.startswith("H" * 50) and text.endswith("T" * 50))
        self.assertIn("999900 bytes dropped", text)
        self.assertLess(len(text), 400)
        self.assertEqual(r.outcome, runner.EXITED_ZERO, "a chatty child must not deadlock on a full pipe")

    def test_untruncated_diagnostics_report_no_loss(self):
        r = runner.run_bounded(py("import sys; sys.stderr.write('short')"), phase="x", timeout_s=20, stderr_limits=(100, 100))
        s = r.stderr.summary()
        self.assertFalse(s["truncated"])
        self.assertEqual(s["dropped_bytes"], 0)

    def test_output_flood_is_cut_off_and_reported(self):
        code = "import sys\nwhile True:\n    sys.stdout.write('y' * 65536); sys.stdout.flush()"
        t0 = time.monotonic()
        r = runner.run_bounded(py(code), phase="x", timeout_s=60, max_drain_bytes=2 * 1024 * 1024, stdout_limits=(10, 10))
        self.assertLess(time.monotonic() - t0, 30)
        self.assertEqual((r.outcome, r.kill_reason), (runner.FLOOD_KILLED, "flood"))
        self.assertLessEqual(r.stdout.summary()["kept_bytes"], 20)

    @unittest.skipUnless(POSIX, "process groups")
    def test_surviving_grandchild_holding_the_pipe_is_reported_and_cleaned(self):
        pidfile = os.path.join(self.tmp, "gc.pid")
        code = (
            "import os, subprocess, sys, time\n"
            "pidfile = sys.argv[1]\n"
            "subprocess.Popen([sys.executable, '-c', 'import os, sys, time; open(sys.argv[1], \"w\").write(str(os.getpid())); time.sleep(60)', pidfile])\n"
            "while not (os.path.exists(pidfile) and os.path.getsize(pidfile) > 0):\n"
            "    time.sleep(0.05)\n"
        )
        r = runner.run_bounded(py(code, pidfile), phase="render", timeout_s=30, drain_timeout_s=0.5)
        self.assertEqual(r.outcome, runner.EXITED_ZERO)
        self.assertTrue(r.orphan_group_killed)
        self.assertFalse(r.ok, "exit 0 with a surviving child process is not a clean result")
        with open(pidfile) as fh:
            self.assertTrue(pid_dead(int(fh.read())))


if __name__ == "__main__":
    unittest.main()
