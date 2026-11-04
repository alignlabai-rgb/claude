import json
import os
import signal
import subprocess
import sys
import time
import unittest

from tests.helpers import (EXAMPLE_EPISODE, EXAMPLE_SRT, POSIX, ROOT, TmpCase, good_probe, jl, make_fake_tools, mutated, pid_dead, rb, to_bytes, tree_snapshot)


class Cli(TmpCase):
    def run_cli(self, *args, env_extra=None, cwd=None):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT, "PYTHONHASHSEED": "0"}
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-m", "video_core", *args], capture_output=True, cwd=cwd or self.tmp, env=env, timeout=120)

    def test_default_action_is_a_side_effect_free_plan(self):
        before = tree_snapshot(self.tmp)
        a = self.run_cli(EXAMPLE_EPISODE)
        b = self.run_cli("plan", EXAMPLE_EPISODE)
        self.assertEqual(a.returncode, 0, a.stderr)
        out = json.loads(a.stdout)
        self.assertEqual(out["mode"], "plan")
        self.assertEqual(out["plan"]["episode"]["total_frames"], 180)
        self.assertEqual(a.stdout, b.stdout, "default action == plan, deterministic output")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_validate_exit_codes_and_issue_codes(self):
        ok = self.run_cli("validate", EXAMPLE_EPISODE)
        self.assertEqual(ok.returncode, 0)
        self.assertTrue(json.loads(ok.stdout)["ok"])
        bad = self.write("bad.json", to_bytes(mutated(lambda d: d["clips"][1].update(start_frame=95))))
        r = self.run_cli("validate", bad)
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout)
        self.assertFalse(out["ok"])
        self.assertIn("coverage_gap", {i["code"] for i in out["error"]["issues"]})
        self.assertEqual(self.run_cli("validate", os.path.join(self.tmp, "missing.json")).returncode, 1)

    def test_srt_stdout_is_exact_utf8_even_in_a_hostile_locale(self):
        r = self.run_cli("srt", EXAMPLE_EPISODE, env_extra={"LC_ALL": "C", "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, rb(EXAMPLE_SRT))

    def test_limit_flags_reject_before_launch_and_bad_flags_are_usage_errors(self):
        r = self.run_cli("plan", EXAMPLE_EPISODE, "--max-frames", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("limit_frames", r.stdout.decode())
        self.assertEqual(self.run_cli("plan", EXAMPLE_EPISODE, "--max-frames", "0").returncode, 2)
        self.assertEqual(self.run_cli("plan", EXAMPLE_EPISODE, "--max-frames", "99999").returncode, 2)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_render_without_the_render_flag_is_a_dry_run(self):
        out = self.mkdir("out")
        before = tree_snapshot(self.tmp)
        r = self.run_cli("render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "run1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["mode"], "dry_run")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_render_requires_an_explicit_output_root(self):
        r = self.run_cli("render", EXAMPLE_EPISODE, "--render")
        self.assertEqual(r.returncode, 1)
        self.assertIn("output_root_required", r.stdout.decode())

    def test_render_refuses_existing_output(self):
        out = self.mkdir("out")
        self.mkdir("out", "run1")
        r = self.run_cli("render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "run1", "--render")
        self.assertEqual(r.returncode, 1)
        self.assertIn("output_exists", r.stdout.decode())

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_render_exit_codes_follow_the_status(self):
        out, bin_ = self.mkdir("out"), self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        r = self.run_cli("render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "ok", "--render", "--ffmpeg", ff, "--ffprobe", fp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "READBACK_MATCH")
        make_fake_tools(bin_, probe=good_probe(frames=179))
        r = self.run_cli("render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "bad", "--render", "--ffmpeg", ff, "--ffprobe", fp)
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (4, "READBACK_MISMATCH"))
        r = self.run_cli("render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "skip", "--render", "--ffmpeg", os.path.join(self.tmp, "nope"), "--ffprobe", fp)
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (3, "SKIPPED_TOOL_UNAVAILABLE"))
        self.assertFalse(os.path.exists(os.path.join(out, "skip")))

    @unittest.skipUnless(POSIX, "signals and fake tools are POSIX")
    def test_sigterm_to_the_orchestrator_stops_its_owned_child_and_leaves_an_honest_receipt(self):
        out, bin_ = self.mkdir("out"), self.mkdir("bin")
        ff, fp = make_fake_tools(bin_, mode="sleep")
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        proc = subprocess.Popen([sys.executable, "-m", "video_core", "render", EXAMPLE_EPISODE, "--output-root", out, "--output-name", "r",
                                 "--render", "--ffmpeg", ff, "--ffprobe", fp], cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            observed = os.path.join(out, "r", "fake_observed.json")
            deadline = time.monotonic() + 60
            while not os.path.exists(observed) or os.path.getsize(observed) == 0:
                self.assertLess(time.monotonic(), deadline, "fake ffmpeg never started")
                time.sleep(0.05)
            time.sleep(0.2)
            child_pid = jl(observed)["pid"]
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertEqual(proc.returncode, 130, stderr)
        receipt = jl(os.path.join(out, "r", "receipt.json"))
        self.assertEqual(receipt["status"], "INTERRUPTED")
        self.assertEqual(receipt["executions"][-1]["kill_reason"], "interrupt")
        self.assertTrue(pid_dead(child_pid), "the owned ffmpeg child must not outlive an interrupted run")
        self.assertTrue(os.path.exists(os.path.join(out, "r", "output.mp4.partial")), "partial output is preserved")

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_default_output_name_is_derived_from_the_plan_hash(self):
        out, bin_ = self.mkdir("out"), self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        r = self.run_cli("render", EXAMPLE_EPISODE, "--output-root", out, "--render", "--ffmpeg", ff, "--ffprobe", fp)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan_sha = json.loads(r.stdout)["plan_sha256"]
        self.assertEqual(os.listdir(out), ["render-" + plan_sha[:12]])

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_inspect_reads_back_and_compares_without_modifying_the_file(self):
        bin_ = self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        media = self.write("m.mp4", b"not really a video")
        before = rb(media)
        r = self.run_cli("inspect", media, "--ffprobe", fp, "--episode", EXAMPLE_EPISODE)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["status"], out["quality_evaluated"]), ("READBACK_MATCH", False))
        self.assertEqual(rb(media), before)
        make_fake_tools(bin_, probe=good_probe(width=320, height=180))
        self.assertEqual(self.run_cli("inspect", media, "--ffprobe", fp, "--episode", EXAMPLE_EPISODE).returncode, 4)
        self.assertEqual(self.run_cli("inspect", "http://example.com/a.mp4", "--ffprobe", fp).returncode, 1)
        self.assertEqual(self.run_cli("inspect", media, "--ffprobe", os.path.join(self.tmp, "none")).returncode, 3)


if __name__ == "__main__":
    unittest.main()
