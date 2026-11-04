"""Lifecycle regressions for the orchestrators (audit findings F-INT-1 and F-RCPT-1; the F-RUN-1 consequence for verification).

F-INT-1: a cancel (Ctrl-C / SIGTERM) during ANY owned child run (version / capability probes, preflight probe, readback) ends the whole run: exit 130, terminal status
INTERRUPTED wherever a run receipt exists, no further child is launched, and no receipt is claimed where none was written.
F-RCPT-1: an input that vanishes or an unexpected exception during render / mix / mux still leaves a receipt that lists the files that exist (hashes, partial flags), the
inputs' after-hash (null + a reason when unreadable) and the tmp cleanup result - without ever hiding the original failure behind a finalization problem.

All tools are FAKE ffmpeg/ffprobe scripts (wrapped to sleep <= 8 s at a chosen call); no real tool, no media. Cancellation in-process is a SIGINT this process sends to itself.
"""

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from tests import test_audio_mix as TAM
from tests.audio_fixtures import dumps, event_use, manifest, tone
from tests.avmux_fakes import av_manifest, default_behavior, make_av_tools, seed_inputs
from tests.helpers import POSIX, ROOT, EXAMPLE_EPISODE, TmpCase, good_probe, jl, make_fake_tools, rb, sample_bytes, tree_snapshot
from tests.lifecycle_support import interrupt_when, launches, read_pid, sigint_default, wrap_tools
from video_core import Limits, finalize, receipt as rc, runner, tools
from video_core import render as render_mod
from video_core.audiomix import mix as mixmod
from video_core.avmux import mux as M
from video_core.avmux.mux import mux
from video_core.render import render

PIDFILE = "stage.pid"


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class AvmuxFixture(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out, self.bin = self.mkdir("assets"), self.mkdir("out"), self.mkdir("bin")
        self.video, self.wav = seed_inputs(self.assets)
        self.ff, self.fp = make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE"))
        self.marker = os.path.join(self.tmp, PIDFILE)
        self.manifest = dumps(av_manifest())

    def go(self, name="r1", **kw):
        return mux(self.manifest, asset_root=self.assets, output_root=self.out, output_name=name, ffmpeg=self.ff, ffprobe=self.fp, **kw)

    def cancel_at(self, tool, needle):
        wrap_tools(self.bin, tool=tool, needle=needle, marker=self.marker, sleep=8)

    def after_marker(self):
        """launches strictly after the one that wrote the marker (the cancelled call)."""
        pid = read_pid(self.marker)
        all_ = launches(self.bin)
        return all_, pid


class AvmuxCancelBeforeAnyRunDir(AvmuxFixture):
    """Stages that happen before the fresh output directory exists: the cancel escapes as KeyboardInterrupt (the CLIs map it to 130), nothing is created, nothing more is launched."""

    def check(self, tool, needle, phase):
        self.cancel_at(tool, needle)
        before = tree_snapshot(self.out)
        with sigint_default():
            interrupt_when(self.marker, delay=0.15)
            with self.assertRaises(KeyboardInterrupt) as cm:
                self.go()
        exc = cm.exception
        self.assertIsInstance(exc, runner.RunInterrupted)
        self.assertEqual((exc.result.outcome, exc.result.phase), (runner.INTERRUPTED, phase))
        self.assertEqual(tree_snapshot(self.out), before, "no output directory and no receipt were pretended")
        all_ = launches(self.bin)
        self.assertEqual(all_[-1]["tool"], tool, "the cancelled call is the last child that was ever launched")
        self.assertTrue(all(needle[0] in " ".join(all_[-1]["argv"]) for _ in [0]))
        return all_

    def test_cancel_during_the_ffmpeg_version_probe(self):
        all_ = self.check("ffmpeg", ["-version"], "probe_version:ffmpeg")
        self.assertEqual(len(all_), 1)

    def test_cancel_during_the_ffprobe_version_probe_does_not_go_on_to_capabilities(self):
        all_ = self.check("ffprobe", ["-version"], "probe_version:ffprobe")
        self.assertEqual([c["tool"] for c in all_], ["ffmpeg", "ffprobe"])

    def test_cancel_during_a_capability_listing(self):
        all_ = self.check("ffmpeg", ["-encoders"], "probe_capability:-encoders")
        self.assertEqual(all_[-1]["argv"][-1], "-encoders")

    def test_cancel_during_the_preflight_probe_of_the_video_does_not_probe_the_audio(self):
        all_ = self.check("ffprobe", ["clip.mp4"], "preflight_probe_video")
        self.assertFalse([c for c in all_ if c["tool"] == "ffprobe" and "stem.wav" in " ".join(c["argv"])], "the audio probe was never launched")

    def test_cancel_during_the_preflight_probe_of_the_audio(self):
        self.check("ffprobe", ["stem.wav"], "preflight_probe_audio")


class AvmuxCancelAfterRunDir(AvmuxFixture):
    """Stages after the run directory exists: terminal status INTERRUPTED in the returned AND the on-disk receipt, the interrupted run is recorded, nothing more is launched."""

    def run_cancelled(self, tool, needle):
        self.cancel_at(tool, needle)
        with sigint_default():
            interrupt_when(self.marker, delay=0.15)
            r = self.go()
        d = os.path.join(self.out, "r1")
        on_disk = jl(os.path.join(d, "receipt.json"))
        self.assertEqual(on_disk, json.loads(json.dumps(r)))
        self.assertEqual((r["status"], r["quality_evaluated"]), ("INTERRUPTED", False))
        self.assertTrue(r["technical_verdict"] is None or r["technical_verdict"]["met"] is False, "a cancelled run never meets the contract")
        self.assertEqual(r["executions"][-1]["outcome"], "INTERRUPTED")
        self.assertEqual(r["executions"][-1]["kill_reason"], "interrupt")
        all_ = launches(self.bin)
        self.assertIn(needle[0], " ".join(all_[-1]["argv"]), "no child was launched after the cancelled one")
        self.assertFalse(os.path.exists(os.path.join(d, "output.mov")), "nothing was promoted")
        return r, d

    def test_cancel_during_a_readback_decode_is_interrupted_not_inspection_failed(self):
        r, d = self.run_cancelled("ffmpeg", ["framemd5"])
        self.assertEqual(r["executions"][-1]["phase"], "readback_decoded_source")
        roles = {o["name"]: o for o in r["outputs"]}
        self.assertTrue(roles["output.mov.partial"]["partial"])
        self.assertEqual(roles["output.mov.partial"]["sha256"], __import__("hashlib").sha256(rb(os.path.join(d, "output.mov.partial"))).hexdigest())
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(r["input"]["video"]["sha256_after"], r["input"]["video"]["sha256_before"])

    def test_cancel_during_the_readback_probe_of_the_output_records_that_probe(self):
        r, d = self.run_cancelled("ffprobe", ["output.mov.partial"])
        self.assertEqual(r["executions"][-1]["phase"], "readback_probe_output")
        self.assertEqual(r["stage"], "readback")

    def test_cancel_during_the_mux_step_itself_is_unchanged(self):
        self.cancel_at("ffmpeg", ["output.mov.partial"])
        with sigint_default():
            interrupt_when(self.marker, delay=0.15)
            r = self.go()
        self.assertEqual((r["status"], r["executions"][-1]["phase"], r["stage"]), ("INTERRUPTED", "mux", "mux"))
        self.assertEqual(len([c for c in launches(self.bin) if c["tool"] == "ffmpeg" and c["argv"] and c["argv"][-1].endswith(".partial")]), 1, "the mux was not retried")


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class RenderCancel(TmpCase):
    def setUp(self):
        super().setUp()
        self.out, self.bin = self.mkdir("out"), self.mkdir("bin")
        self.ff, self.fp = make_fake_tools(self.bin)
        self.marker = os.path.join(self.tmp, PIDFILE)

    def go(self, **kw):
        return render(sample_bytes(), asset_root=None, output_root=self.out, output_name="r1", ffmpeg=self.ff, ffprobe=self.fp, **kw)

    def test_cancel_during_version_and_capability_probes_creates_nothing_and_launches_nothing_more(self):
        for i, (tool, needle) in enumerate((("ffmpeg", ["-version"]), ("ffprobe", ["-version"]), ("ffmpeg", ["-encoders"]), ("ffmpeg", ["-filters"]))):
            with self.subTest(tool=tool, needle=needle):
                bin_ = self.mkdir(f"bin{i}")
                ff, fp = make_fake_tools(bin_)
                marker = os.path.join(self.tmp, f"stage{i}.pid")
                wrap_tools(bin_, tool=tool, needle=needle, marker=marker, sleep=8)
                before = tree_snapshot(self.out)
                with sigint_default():
                    interrupt_when(marker, delay=0.15)
                    with self.assertRaises(KeyboardInterrupt) as cm:
                        render(sample_bytes(), asset_root=None, output_root=self.out, output_name="r1", ffmpeg=ff, ffprobe=fp)
                self.assertEqual(cm.exception.result.outcome, runner.INTERRUPTED)
                self.assertEqual(tree_snapshot(self.out), before)
                self.assertIn(needle[0], " ".join(launches(bin_)[-1]["argv"]), "the cancelled call is the last child ever launched")

    def test_cancel_during_the_readback_probe_is_interrupted_and_inventories_what_exists(self):
        wrap_tools(self.bin, tool="ffprobe", needle=["output.mp4"], marker=self.marker, sleep=8)
        with sigint_default():
            interrupt_when(self.marker, delay=0.15)
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))
        self.assertEqual((r["status"], r["stage"]), (rc.STATUS_INTERRUPTED, "inspect"))
        self.assertEqual((r["executions"][-1]["phase"], r["executions"][-1]["outcome"]), ("inspect", "INTERRUPTED"))
        self.assertIn("output.mp4", {o["name"] for o in r["outputs"]})
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertIn("output.mp4", " ".join(launches(self.bin)[-1]["argv"]), "nothing was launched after the cancelled probe")
        self.assertEqual(len([c for c in launches(self.bin) if c["tool"] == "ffprobe" and "output.mp4" in " ".join(c["argv"])]), 1, "no retry")


@unittest.skipUnless(POSIX, "fake tools and signals are POSIX")
class CliExitCode130(AvmuxFixture):
    """The unmodified CLIs: SIGTERM at any stage -> exit 130; a receipt exists (and says INTERRUPTED) only where a run directory was created."""

    def cli(self, module, argv):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        proc = subprocess.Popen([sys.executable, "-m", module, *argv], cwd=self.tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 30
            while not os.path.exists(self.marker) and proc.poll() is None and time.monotonic() < end:
                time.sleep(0.02)
            reached = os.path.exists(self.marker)
            at_signal = len(launches(self.bin))
            time.sleep(0.15)
            if reached:
                proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        return reached, proc.returncode, out, err, at_signal

    def mux_argv(self):
        mpath = os.path.join(self.tmp, "m.json")
        with open(mpath, "wb") as fh:
            fh.write(self.manifest)
        return ["mux", mpath, "--asset-root", self.assets, "--output-root", self.out, "--output-name", "r1", "--mux", "--ffmpeg", self.ff, "--ffprobe", self.fp]

    def test_avmux_cli_sigterm_before_the_run_directory(self):
        for tool, needle in (("ffmpeg", ["-version"]), ("ffprobe", ["clip.mp4"]), ("ffmpeg", ["-encoders"])):
            with self.subTest(tool=tool, needle=needle):
                if os.path.exists(self.marker):
                    os.remove(self.marker)
                if os.path.exists(os.path.join(self.bin, "launches.log")):
                    os.remove(os.path.join(self.bin, "launches.log"))
                make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE"))
                wrap_tools(self.bin, tool=tool, needle=needle, marker=self.marker, sleep=8)
                reached, code, out, err, at_signal = self.cli("video_core.avmux", self.mux_argv())
                self.assertTrue(reached)
                self.assertEqual(code, 130, err)
                self.assertEqual(out, b"", "no receipt is printed for a run that wrote none")
                self.assertFalse(os.path.exists(os.path.join(self.out, "r1")))
                self.assertEqual(len(launches(self.bin)), at_signal, "no child was launched after the signal")

    def test_avmux_cli_sigterm_during_the_readback(self):
        wrap_tools(self.bin, tool="ffmpeg", needle=["framemd5"], marker=self.marker, sleep=8)
        reached, code, out, err, at_signal = self.cli("video_core.avmux", self.mux_argv())
        self.assertTrue(reached)
        self.assertEqual(code, 130, err)
        printed = json.loads(out)
        self.assertEqual(printed["status"], "INTERRUPTED")
        self.assertEqual(jl(os.path.join(self.out, "r1", "receipt.json"))["status"], "INTERRUPTED")
        self.assertEqual(len(launches(self.bin)), at_signal)

    def test_video_cli_sigterm_during_the_version_probe_and_during_the_readback(self):
        for tool, needle, expect_receipt in (("ffmpeg", ["-version"], False), ("ffprobe", ["output.mp4"], True)):
            with self.subTest(needle=needle):
                shutil_bin = self.mkdir("vbin_" + needle[0].strip("-").replace(".", "_"))
                ff, fp = make_fake_tools(shutil_bin)
                self.bin = shutil_bin
                for stale in (self.marker,):
                    if os.path.exists(stale):
                        os.remove(stale)
                wrap_tools(shutil_bin, tool=tool, needle=needle, marker=self.marker, sleep=8)
                out_root = self.mkdir("vout_" + needle[0].strip("-").replace(".", "_"))
                reached, code, out, err, at_signal = self.cli("video_core", ["render", EXAMPLE_EPISODE, "--output-root", out_root, "--output-name", "r1", "--render", "--ffmpeg", ff, "--ffprobe", fp])
                self.assertTrue(reached)
                self.assertEqual(code, 130, err)
                self.assertEqual(len(launches(shutil_bin)), at_signal)
                if expect_receipt:
                    self.assertEqual(json.loads(out)["status"], "INTERRUPTED")
                    self.assertEqual(jl(os.path.join(out_root, "r1", "receipt.json"))["status"], "INTERRUPTED")
                else:
                    self.assertEqual(out, b"")
                    self.assertEqual(os.listdir(out_root), [])


# ------------------------------------------------------------------------------------------------------------------------------------- F-RCPT-1
class AudioReceiptFinalization(TAM.MixCase):
    def setUp(self):
        super().setUp()
        self.a = self.put("a", tone("square", 480, 4800, 1000))
        self.doc = manifest([event_use("e", "a", 0, 4800, 0, 3)], [self.a], total=3)
        self.limits = TAM.AudioLimits(chunk_samples=1000)

    def test_an_input_vanishing_during_the_mix_leaves_an_honest_inventory(self):
        calls = []

        def cancel():  # the asset disappears before the second chunk
            if calls:
                os.remove(self.paths["a"])
            calls.append(1)
            return False
        r = self.mix(self.doc, cancel=cancel, limits=self.limits)
        d = os.path.join(self.out, "r1")
        self.assertEqual(r["status"], mixmod.S_UNKNOWN)
        self.assertEqual(r["internal_error"]["type"], "FileNotFoundError")
        by = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(set(by), {"stem.wav.partial", "plan.canonical.json"})
        self.assertTrue(by["stem.wav.partial"]["partial"])
        self.assertEqual(by["stem.wav.partial"]["bytes"], os.path.getsize(os.path.join(d, "stem.wav.partial")))
        import hashlib
        self.assertEqual(by["stem.wav.partial"]["sha256"], hashlib.sha256(rb(os.path.join(d, "stem.wav.partial"))).hexdigest())
        asset = r["input"]["assets"][0]
        self.assertIsNone(asset["sha256_after"])
        self.assertEqual(asset["sha256_after_error"], "FileNotFoundError")
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))
        self.assertFalse(os.path.exists(self.paths["a"]), "an original asset is never recreated or touched")

    def test_a_broken_finalizer_never_replaces_the_original_failure(self):
        with mock.patch.object(mixmod.os, "rename", side_effect=RuntimeError("original failure")), mock.patch.object(finalize, "inventory", side_effect=PermissionError("cannot list")):
            r = self.mix(self.doc, limits=self.limits)
        self.assertEqual((r["status"], r["internal_error"]["type"], r["internal_error"]["message"]), (mixmod.S_UNKNOWN, "RuntimeError", "original failure"))
        self.assertEqual(r["finalize_errors"], [{"step": "inventory", "error": "PermissionError"}])
        self.assertIsNotNone(r["input"]["assets"][0]["sha256_after"], "the rest of the finalization still ran")
        self.assertEqual(jl(os.path.join(self.out, "r1", "receipt.json"))["status"], mixmod.S_UNKNOWN)

    def test_interrupt_inventories_too(self):
        def cancel():
            raise KeyboardInterrupt
        r = self.mix(self.doc, cancel=cancel, limits=self.limits)
        self.assertEqual(r["status"], mixmod.S_INTERRUPTED)
        self.assertIn("plan.canonical.json", {o["name"] for o in r["outputs"]})
        self.assertIn("stem.wav.partial", {o["name"] for o in r["outputs"]})
        self.assertIsNotNone(r["input"]["assets"][0]["sha256_after"])

    def test_previous_run_directories_are_never_overwritten_by_a_failed_run(self):
        first = self.mix(self.doc, name="first", limits=self.limits)
        self.assertEqual(first["status"], mixmod.S_VERIFIED)
        snap = tree_snapshot(os.path.join(self.out, "first"))
        self.mix(self.doc, name="second", cancel=lambda: (_ for _ in ()).throw(RuntimeError("x")), limits=self.limits)
        self.assertEqual(tree_snapshot(os.path.join(self.out, "first")), snap)


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class AvmuxReceiptFinalization(AvmuxFixture):
    def test_input_vanishing_during_the_mux_is_unknown_with_a_full_inventory(self):
        wav_path = os.path.join(self.assets, "stem.wav")
        wrap_tools(self.bin, tool="ffmpeg", needle=["output.mov.partial"], remove=wav_path)  # remove the WAV when the mux is launched, then run the normal fake
        r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("became unreadable", r["unknown_reason"])
        aud = r["input"]["audio"]
        self.assertEqual((aud["sha256_after"], aud["sha256_after_error"]), (None, "FileNotFoundError"))
        self.assertEqual(r["input"]["video"]["sha256_after"], r["input"]["video"]["sha256_before"])
        self.assertFalse(r["input"]["inputs_unchanged"])
        names = {o["name"]: o for o in r["outputs"]}
        self.assertIn("plan.canonical.json", names)
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertFalse(os.path.exists(os.path.join(d, "tmp")))
        self.assertFalse(os.path.exists(wav_path), "the original asset is not recreated")
        self.assertEqual(len([c for c in launches(self.bin) if c["tool"] == "ffmpeg" and c["argv"] and c["argv"][-1].endswith(".partial")]), 1, "the mux was not retried")

    def test_unexpected_exception_in_the_readback_lists_everything_on_disk(self):
        with mock.patch.object(M, "_readback", side_effect=RuntimeError("original failure")):
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual((r["status"], r["internal_error"]["type"], r["internal_error"]["message"], r["internal_error"]["stage"]), (rc.STATUS_UNKNOWN, "RuntimeError", "original failure", "readback"))
        by = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(set(by), {"output.mov.partial", "plan.canonical.json", "fake_observed.json"})
        self.assertEqual(by["fake_observed.json"]["role"], "unrecognised", "a file nobody vouched for is still listed on the error path")
        self.assertTrue(by["output.mov.partial"]["partial"])
        self.assertEqual(by["output.mov.partial"]["bytes"], os.path.getsize(os.path.join(d, "output.mov.partial")))
        self.assertEqual(r["input"]["video"]["sha256_after"], r["input"]["video"]["sha256_before"])
        self.assertEqual(r["input"]["audio"]["sha256_after"], r["input"]["audio"]["sha256_before"])
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))

    def test_a_probe_file_written_before_the_exception_is_listed_and_partial_checks_survive(self):
        real = M.ck.check_output_layout

        def boom(parsed):
            raise RuntimeError("original failure")
        with mock.patch.object(M.ck, "check_output_layout", boom):
            r = self.go()
        names = {o["name"] for o in r["outputs"]}
        self.assertEqual(names, {"output.mov.partial", "plan.canonical.json", "ffprobe.output.json", "fake_observed.json"})
        self.assertEqual(r["internal_error"]["message"], "original failure")
        self.assertIs(real, M.ck.check_output_layout)

    def test_a_failing_cleanup_and_a_failing_hasher_never_replace_the_original_failure(self):
        real_hash, armed = M.tools.sha256_file, []

        def readback(*a, **k):
            armed.append(1)  # from here on (the run directory exists, the mux ran) every hash attempt is denied
            raise RuntimeError("original failure")

        def hasher(path, max_bytes=None):
            if armed:
                raise PermissionError("denied")
            return real_hash(path, max_bytes)
        with mock.patch.object(M, "_readback", readback), mock.patch.object(finalize.shutil, "rmtree", side_effect=OSError("busy")), mock.patch.object(M.tools, "sha256_file", hasher), \
                mock.patch.object(finalize, "sha256_file", hasher):
            r = self.go()
        self.assertEqual((r["status"], r["internal_error"]["type"]), (rc.STATUS_UNKNOWN, "RuntimeError"))
        self.assertEqual(r["tmp_cleanup"], "left in place: OSError")
        self.assertTrue(os.path.isdir(os.path.join(self.out, "r1", "tmp")), "left in place, and said so")
        self.assertEqual(r["input"]["video"]["sha256_after"], None)
        self.assertEqual(r["input"]["video"]["sha256_after_error"], "PermissionError")
        by = {o["name"]: o for o in r["outputs"]}
        self.assertIsNone(by["output.mov.partial"]["sha256"])
        self.assertEqual(by["output.mov.partial"]["error"], "PermissionError")
        self.assertEqual(jl(os.path.join(self.out, "r1", "receipt.json"))["status"], rc.STATUS_UNKNOWN)


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class RenderReceiptFinalization(TmpCase):
    def setUp(self):
        super().setUp()
        self.out, self.bin = self.mkdir("out"), self.mkdir("bin")
        self.ff, self.fp = make_fake_tools(self.bin)

    def go(self, name="r1"):
        return render(sample_bytes(), asset_root=None, output_root=self.out, output_name=name, ffmpeg=self.ff, ffprobe=self.fp)

    def test_unexpected_exception_after_the_probe_lists_the_deliverable_the_probe_json_and_cleans_tmp(self):
        with mock.patch.object(render_mod.readback, "summarize", side_effect=RuntimeError("original failure")):
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual((r["status"], r["internal_error"]["message"]), (rc.STATUS_UNKNOWN, "original failure"))
        by = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(set(by), {"output.mp4", "captions.srt", "plan.canonical.json", "ffprobe.json", "fake_observed.json"})
        self.assertEqual(by["output.mp4"]["bytes"], os.path.getsize(os.path.join(d, "output.mp4")))
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertFalse(os.path.exists(os.path.join(d, "tmp")))
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))

    def test_unlisted_files_in_the_run_directory_are_reported_only_on_the_error_path(self):
        with mock.patch.object(render_mod.readback, "summarize", side_effect=RuntimeError("original failure")):
            r = self.go()
        self.assertIn(("fake_observed.json", "unrecognised"), {(o["name"], o["role"]) for o in r["outputs"]})
        ok = self.go("r2")
        self.assertEqual(ok["status"], rc.STATUS_MATCH)
        self.assertNotIn("unrecognised", {o["role"] for o in ok["outputs"]}, "the normal-path receipt shape is unchanged")

    def test_an_asset_that_vanishes_during_the_render_is_unknown_with_null_after_hash_and_a_reason(self):
        from tests.helpers import episode_doc, to_bytes
        clip = self.write("assets/clip.mp4", b"A" * 1000)
        doc = episode_doc(clips=[{"id": "v", "start_frame": 0, "duration_frames": 180, "source": {"kind": "file", "path": "clip.mp4"}}], captions=[])
        wrap_tools(self.bin, tool="ffmpeg", needle=["output.mp4"], remove=clip)
        r = render(to_bytes(doc), asset_root=os.path.join(self.tmp, "assets"), output_root=self.out, output_name="gone", ffmpeg=self.ff, ffprobe=self.fp)
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("became unreadable", r["unknown_reason"])
        a = r["input"]["assets"][0]
        self.assertEqual((a["path"], a["sha256_after"], a["sha256_after_error"]), ("clip.mp4", None, "FileNotFoundError"))
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("output.mp4", {o["name"] for o in r["outputs"]})
        self.assertFalse(os.path.exists(clip), "never recreated")
        self.assertEqual(jl(os.path.join(self.out, "gone", "receipt.json"))["status"], rc.STATUS_UNKNOWN)


class RenderDrainBreachIsNotVerified(TmpCase):
    """F-RUN-1 downstream: a child that exited 0 but breached the drain bound must not pass verification."""

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_render_whose_ffmpeg_flooded_stderr_is_never_a_match_whichever_way_the_race_goes(self):
        """A real flooding fake: depending on scheduling the runner sees the flood while the child is alive (FLOOD_KILLED, a kill) or after it exited (UNKNOWN, no kill).
        Both are acceptable; a MATCH is not, and each outcome must be internally truthful."""
        out, bin_ = self.mkdir("out"), self.mkdir("bin")
        ff, fp = make_fake_tools(bin_, noise=200_000)
        r = render(sample_bytes(), limits=Limits(max_drain_bytes=1000), asset_root=None, output_root=out, output_name="r1", ffmpeg=ff, ffprobe=fp)
        ex = [e for e in r["executions"] if e["phase"] == "render"][0]
        self.assertTrue(ex["drain_limit_exceeded"])
        self.assertNotIn(r["status"], (rc.STATUS_MATCH, rc.STATUS_MISMATCH))
        self.assertIsNone(r["readback"], "no readback was run on a breached execution")
        if r["status"] == rc.STATUS_FLOOD:
            self.assertEqual((ex["kill_reason"], ex["kill_attempted"]), ("flood", True))
        else:
            self.assertEqual((r["status"], ex["outcome"], ex["exit_code"], ex["kill_attempted"], ex["kill_reason"]), (rc.STATUS_UNKNOWN, "EXITED_ZERO", 0, False, None))
            self.assertIn("the bound was breached", r["unknown_reason"])

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_state_boundary_a_child_that_exited_zero_with_a_breached_bound_is_unknown_and_not_called_killed(self):
        """Deterministic: the exact RunResult the runner returns for 'exited 0 on its own after exceeding max_drain_bytes' is fed to the orchestrator."""
        out, bin_ = self.mkdir("out"), self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        real = runner.run_bounded

        def crafted(argv, **kw):
            res = real(argv, **kw)
            if kw.get("phase") == "render":
                res.drain_limit_exceeded, res.drain_limit_bytes = True, 1000
            return res
        with mock.patch.object(render_mod.runner, "run_bounded", crafted):
            r = render(sample_bytes(), asset_root=None, output_root=out, output_name="r1", ffmpeg=ff, ffprobe=fp)
        ex = [e for e in r["executions"] if e["phase"] == "render"][0]
        self.assertEqual((r["status"], ex["outcome"], ex["exit_code"], ex["kill_attempted"]), (rc.STATUS_UNKNOWN, "EXITED_ZERO", 0, False))
        self.assertIn("exited on its own", r["unknown_reason"])
        self.assertNotIn("FLOOD", r["status"])
        self.assertIsNone(r["readback"])
        self.assertFalse(os.path.exists(os.path.join(out, "r1", "output.mp4")), "the file was not promoted to a deliverable name")

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_a_probe_that_breaches_the_bound_is_not_trusted(self):
        bin_ = self.mkdir("bin")
        ff, fp = make_fake_tools(bin_, version_line_ffmpeg="ffmpeg version 1 " + "x" * 5000)
        rec, runs = tools.probe_version("ffmpeg", ff, "explicit", Limits(max_drain_bytes=1000), self.tmp)
        self.assertTrue(runs[0].drain_limit_exceeded)
        self.assertIsNotNone(rec.problem)
        self.assertIn("drain bound", rec.problem)
        self.assertIsNone(rec.version)


if __name__ == "__main__":
    unittest.main()
