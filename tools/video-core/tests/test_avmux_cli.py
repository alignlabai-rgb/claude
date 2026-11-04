import json
import os
import signal
import subprocess
import sys
import time
import unittest

from tests.audio_fixtures import dumps, write
from tests.avmux_fakes import av_manifest, calls, default_behavior, make_av_tools, mux_calls, out_probe, seed_inputs
from tests.helpers import POSIX, ROOT, TmpCase, jl, pid_dead, tree_snapshot


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class AvmuxCli(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out, self.bin = self.mkdir("assets"), self.mkdir("out"), self.mkdir("bin")
        seed_inputs(self.assets)
        self.manifests = {r: write(os.path.join(self.tmp, f"{r}.json"), dumps(av_manifest(r))) for r in ("MOV_PCM_S16LE", "MP4_AAC")}
        self.m = self.manifests["MOV_PCM_S16LE"]
        self.ff, self.fp = make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE"))

    def cli(self, *args):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        return subprocess.run([sys.executable, "-m", "video_core.avmux", *args], capture_output=True, cwd=self.tmp, env=env, timeout=120)

    def tools_args(self):
        return ["--ffmpeg", self.ff, "--ffprobe", self.fp]

    def test_default_action_is_plan_and_creates_nothing_and_runs_no_tool(self):
        before = tree_snapshot(self.tmp)
        a = self.cli(self.m, "--asset-root", self.assets, *self.tools_args())
        b = self.cli("plan", self.m, "--asset-root", self.assets, *self.tools_args())
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        out = json.loads(a.stdout)
        self.assertEqual((out["mode"], out["plan"]["video"]["properties_basis"]), ("plan", "CALLER_STATED_UNVERIFIED"))
        self.assertTrue(out["readiness"].startswith("UNVERIFIED"))
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(calls(self.bin), [], "plan never runs ffprobe or ffmpeg")

    def test_validate_exit_codes(self):
        self.assertEqual(self.cli("validate", self.m).returncode, 0)
        bad = write(os.path.join(self.tmp, "bad.json"), dumps(av_manifest(route="MKV")))
        r = self.cli("validate", bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid_enum", r.stdout.decode())
        self.assertEqual(self.cli("validate", os.path.join(self.tmp, "none.json")).returncode, 1)
        proxy = av_manifest()
        proxy["audio"]["state"] = "PROXY"
        r = self.cli("validate", write(os.path.join(self.tmp, "proxy.json"), dumps(proxy)))
        self.assertEqual(r.returncode, 1)
        self.assertIn("input_state_not_use", r.stdout.decode())

    def test_mux_without_the_flag_is_a_dry_run_that_runs_nothing(self):
        before = tree_snapshot(self.tmp)
        r = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "r1", *self.tools_args())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["mode"], "dry_run")
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(calls(self.bin), [])

    def test_mux_requires_an_explicit_output_root(self):
        r = self.cli("mux", self.m, "--asset-root", self.assets, "--mux")
        self.assertEqual(r.returncode, 1)
        self.assertIn("output_root_required", r.stdout.decode())

    def test_exit_codes_follow_the_status_for_both_routes(self):
        r = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "mov", "--mux", *self.tools_args())
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (0, "MUXED_VERIFIED_SAMPLE_EXACT"), r.stderr)
        make_av_tools(self.bin, **default_behavior("MP4_AAC", decoded_audio_hex=(b"\x05\x00" * 4800).hex()))
        r = self.cli("mux", self.manifests["MP4_AAC"], "--asset-root", self.assets, "--output-root", self.out, "--output-name", "mp4", "--mux", *self.tools_args())
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (0, "MUXED_TECHNICAL_WITHIN_TOLERANCE"))
        make_av_tools(self.bin, **default_behavior("MP4_AAC", decoded_audio_hex=(b"\x05\x00" * 9000).hex()))
        r = self.cli("mux", self.manifests["MP4_AAC"], "--asset-root", self.assets, "--output-root", self.out, "--output-name", "bad", "--mux", *self.tools_args())
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (4, "READBACK_MISMATCH"))
        r = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "skip", "--mux", "--ffmpeg", os.path.join(self.tmp, "none"), "--ffprobe", self.fp)
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (3, "SKIPPED_TOOL_UNAVAILABLE"))
        self.assertFalse(os.path.exists(os.path.join(self.out, "skip")))
        again = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "mov", "--mux", *self.tools_args())
        self.assertEqual(again.returncode, 1)
        self.assertIn("output_exists", again.stdout.decode())

    def test_preflight_rejection_exits_1_with_nothing_created_and_no_mux(self):
        make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE", probe_video={"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc"}], "format": {}}))
        before = tree_snapshot(self.out)
        r = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "rej", "--mux", *self.tools_args())
        self.assertEqual(r.returncode, 1)
        self.assertIn("video_codec_mismatch", r.stdout.decode())
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(mux_calls(self.bin), [])

    def test_default_output_name_comes_from_the_plan_hash(self):
        r = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--mux", *self.tools_args())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.out), ["mux-" + json.loads(r.stdout)["plan_sha256"][:12]])

    def test_non_finite_and_mistyped_limits_exit_2_before_anything_runs(self):
        before = tree_snapshot(self.tmp)
        for flag in ("--max-runtime-seconds", "--probe-timeout-seconds", "--inspect-timeout-seconds"):
            for bad in ("nan", "inf", "-inf", "0", "-1", "1e999", "86400.5"):
                r = self.cli("mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "r", "--mux", *self.tools_args(), f"{flag}={bad}")
                self.assertEqual(r.returncode, 2, (flag, bad, r.stderr))
                self.assertEqual(r.stdout, b"")
                self.assertIn(b"usage error", r.stderr)
        for flag in ("--max-output-bytes", "--max-diag-bytes", "--max-drain-bytes", "--max-frames"):
            for bad in ("nan", "5.5", "0", "-3"):
                self.assertEqual(self.cli("plan", self.m, "--asset-root", self.assets, flag, bad).returncode, 2, (flag, bad))
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(calls(self.bin), [])
        ok = self.cli("plan", self.m, "--asset-root", self.assets, "--max-runtime-seconds", "0.5")
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_sigterm_interrupts_the_mux_stops_only_the_owned_child_and_keeps_the_partial(self):
        make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE", mode="sleep"))
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        proc = subprocess.Popen([sys.executable, "-m", "video_core.avmux", "mux", self.m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "r", "--mux", *self.tools_args()],
                                cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        try:
            observed = os.path.join(self.out, "r", "fake_observed.json")
            deadline = time.monotonic() + 60
            while not os.path.exists(observed) or os.path.getsize(observed) == 0:
                self.assertLess(time.monotonic(), deadline, "the fake mux never started")
                time.sleep(0.05)
            time.sleep(0.2)
            child = jl(observed)["pid"]
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=60)
            self.assertIsNone(bystander.poll(), "an unrelated process must survive")
        finally:
            bystander.kill()
            bystander.wait()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertEqual(proc.returncode, 130, stderr)
        receipt = jl(os.path.join(self.out, "r", "receipt.json"))
        self.assertEqual((receipt["status"], receipt["executions"][-1]["kill_reason"]), ("INTERRUPTED", "interrupt"))
        self.assertTrue(pid_dead(child))
        self.assertTrue(os.path.exists(os.path.join(self.out, "r", "output.mov.partial")))


if __name__ == "__main__":
    unittest.main()
