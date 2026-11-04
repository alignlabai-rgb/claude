import json
import os
import signal
import subprocess
import sys
import time
import unittest

from tests.audio_fixtures import asset, dumps, event_use, manifest, tone, wav_bytes, write
from tests.helpers import EXAMPLE_EPISODE, POSIX, ROOT, TmpCase, jl, rb, tree_snapshot


class AudioCli(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out = self.mkdir("assets"), self.mkdir("out")
        data = wav_bytes(tone("square", 480, 3200, 1000))
        write(os.path.join(self.assets, "a.wav"), data)
        self.a = asset("a", "a.wav", data)
        self.doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [self.a], total=2)
        self.mpath = write(os.path.join(self.tmp, "m.json"), dumps(self.doc))

    def cli(self, *args, cwd=None):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        return subprocess.run([sys.executable, "-m", "video_core.audiomix", *args], capture_output=True, cwd=cwd or self.tmp, env=env, timeout=120)

    def test_default_action_is_a_side_effect_free_plan(self):
        before = tree_snapshot(self.tmp)
        a = self.cli(self.mpath, "--asset-root", self.assets)
        b = self.cli("plan", self.mpath, "--asset-root", self.assets)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        out = json.loads(a.stdout)
        self.assertEqual((out["mode"], out["status"], out["plan"]["output"]["samples"]), ("plan", "READY", 3200))
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_validate_is_manifest_only_and_reports_hold_if_planned(self):
        r = self.cli("validate", self.mpath)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["ok"], out["total_samples"], out["events"]), (True, 3200, 1))
        bad = write(os.path.join(self.tmp, "bad.json"), dumps(dict(self.doc, manifest_version=2)))
        r = self.cli("validate", bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsupported_version", r.stdout.decode())
        self.assertEqual(self.cli("validate", os.path.join(self.tmp, "none.json")).returncode, 1)

    def test_validate_with_episode_cross_check(self):
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2)], [self.a], total=180)
        doc["events"][0].update(end_frame=2)
        m = write(os.path.join(self.tmp, "m180.json"), dumps(doc))
        self.assertEqual(self.cli("validate", m, "--episode", EXAMPLE_EPISODE).returncode, 0)
        wrong = write(os.path.join(self.tmp, "m24.json"), dumps(manifest([event_use("e", "a", 0, 2000, 0, 1)], [self.a], fps=(24, 1), total=180)))
        r = self.cli("validate", wrong, "--episode", EXAMPLE_EPISODE)
        self.assertEqual(r.returncode, 1)
        self.assertIn("episode_fps_mismatch", r.stdout.decode())

    def test_plan_hold_exit_code_and_structure(self):
        doc = manifest([event_use("e", "a", 0, 3200, 0, 2), {"id": "m", "source_id": "m", "state": "MISSING"}], [self.a], total=2)
        m = write(os.path.join(self.tmp, "hold.json"), dumps(doc))
        r = self.cli("plan", m, "--asset-root", self.assets)
        self.assertEqual(r.returncode, 5)
        self.assertEqual(json.loads(r.stdout)["plan"]["hold"][0]["code"], "SELECTED_UNRESOLVED")

    def test_mix_without_the_flag_is_a_dry_run(self):
        before = tree_snapshot(self.tmp)
        r = self.cli("mix", self.mpath, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "r1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["mode"], "dry_run")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_mix_requires_an_explicit_output_root(self):
        r = self.cli("mix", self.mpath, "--asset-root", self.assets, "--mix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("output_root_required", r.stdout.decode())

    def test_mix_verified_hold_refusal_and_exit_codes(self):
        r = self.cli("mix", self.mpath, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "ok", "--mix")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "MIXED_VERIFIED")
        self.assertEqual(os.path.getsize(os.path.join(self.out, "ok", "stem.wav")), 44 + 3200 * 2)
        again = self.cli("mix", self.mpath, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "ok", "--mix")
        self.assertEqual(again.returncode, 1)
        self.assertIn("output_exists", again.stdout.decode())
        hold = write(os.path.join(self.tmp, "hold.json"), dumps(manifest([event_use("e", "a", 0, 3200, 0, 2), {"id": "m", "source_id": "m", "state": "PROXY"}], [self.a], total=2)))
        r = self.cli("mix", hold, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "held", "--mix")
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (5, "HOLD"))
        self.assertFalse(os.path.exists(os.path.join(self.out, "held")))
        clip = manifest([event_use("e1", "a", 0, 3200, 0, 2), event_use("e2", "a", 0, 3200, 0, 2)], [self.a], total=2, overlap="ALLOW_SUM")
        clip["policy"]["clipping"] = "REJECT"
        data = wav_bytes([20000] * 3200)
        write(os.path.join(self.assets, "loud.wav"), data)
        loud = manifest([event_use("e1", "l", 0, 3200, 0, 2), event_use("e2", "l", 0, 3200, 0, 2)], [asset("l", "loud.wav", data)], total=2, overlap="ALLOW_SUM")
        r = self.cli("mix", write(os.path.join(self.tmp, "loud.json"), dumps(loud)), "--asset-root", self.assets, "--output-root", self.out, "--output-name", "loud", "--mix")
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (4, "CLIPPING_REJECTED"))

    def test_default_output_name_comes_from_the_plan_hash(self):
        r = self.cli("mix", self.mpath, "--asset-root", self.assets, "--output-root", self.out, "--mix")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.out), ["stem-" + json.loads(r.stdout)["plan_sha256"][:12]])

    def test_limit_flags_and_usage_errors(self):
        r = self.cli("plan", self.mpath, "--asset-root", self.assets, "--max-output-samples", "100")
        self.assertEqual(r.returncode, 1)
        self.assertIn("limit_output_samples", r.stdout.decode())
        self.assertEqual(self.cli("plan", self.mpath, "--chunk-samples", "1").returncode, 2)
        self.assertEqual(self.cli("plan", self.mpath, "--max-events", "5000").returncode, 2)
        self.assertEqual(self.cli().returncode, 2)

    def test_the_video_cli_is_unchanged_and_still_video_only(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        r = subprocess.run([sys.executable, "-m", "video_core", EXAMPLE_EPISODE], capture_output=True, cwd=self.tmp, env=env, timeout=120)
        plan = json.loads(r.stdout)
        self.assertEqual(plan["plan_sha256"], "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21")
        argv = [a for a in plan["plan"]["ffmpeg"]["argv"] if isinstance(a, str)]
        self.assertIn("-an", argv)
        self.assertNotIn("-i", argv, "the colour baseline has no inputs, hence still no audio input")

    @unittest.skipUnless(POSIX, "POSIX signals")
    def test_sigterm_cancels_a_running_mix_and_keeps_the_partial(self):
        n = 48000 * 60
        data = wav_bytes([1000] * n)
        write(os.path.join(self.assets, "long.wav"), data)
        doc = manifest([event_use("e", "long", 0, n, 0, 1800)], [asset("long", "long.wav", data)], total=1800)
        m = write(os.path.join(self.tmp, "long.json"), dumps(doc))
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        proc = subprocess.Popen([sys.executable, "-m", "video_core.audiomix", "mix", m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "r",
                                 "--mix", "--chunk-samples", "64"], cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            partial = os.path.join(self.out, "r", "stem.wav.partial")
            deadline = time.monotonic() + 60
            while not (os.path.exists(partial) and os.path.getsize(partial) > 44 + 20000):
                self.assertIsNone(proc.poll(), "the mix ended before it could be cancelled")
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertEqual(proc.returncode, 130, stderr)
        receipt = jl(os.path.join(self.out, "r", "receipt.json"))
        self.assertEqual(receipt["status"], "CANCELLED")
        self.assertLess(receipt["mix"]["samples_written"], n)
        self.assertGreater(receipt["mix"]["samples_written"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.out, "r", "stem.wav.partial")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "r", "stem.wav")))


if __name__ == "__main__":
    unittest.main()
