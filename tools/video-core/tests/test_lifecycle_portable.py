"""Portable (Windows + Linux) lifecycle scenarios for render / avmux, additive to tests/test_lifecycle_orchestrators.py (whose POSIX-only variants are unchanged).

See tests/portable_lifecycle.py for exactly what is real (owned Python children under the real runner, unmodified orchestrators), mapped (fake tool scripts launched through
sys.executable), text-mode emulated (CRLF) and injected (`_thread.interrupt_main`, `RunInterrupted` at a named boundary). Nothing here is FFmpeg, media, a production executable-path
acceptance, or real signal / Ctrl-C delivery. Every child is serial, tiny and bounded (<= 8 s, always terminated by the runner or exiting at once).
"""

import json
import os
import sys
import unittest
from unittest import mock

from tests.audio_fixtures import dumps
from tests.avmux_fakes import av_manifest, default_behavior, make_av_tools, seed_inputs
from tests.helpers import EXAMPLE_EPISODE, TmpCase, episode_doc, jl, make_fake_tools, sample_bytes, to_bytes, tree_snapshot
from tests.lifecycle_support import launches, sigint_default, wrap_tools
from tests.portable_lifecycle import interrupt_main_when, owned_python_tools
from video_core import Limits, finalize, receipt as rc, runner
from video_core import render as render_mod
from video_core.avmux import mux as M
from video_core.avmux.mux import mux
from video_core.render import render

MARKER = "stage.pid"


def killed_for_real(res_dict_or_result):
    """The interrupted child was really terminated by the runner: a request was accepted for a live process and the child has an exit status (POSIX signal / Windows forced code)."""
    r = res_dict_or_result
    get = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: getattr(r, k))
    return bool(get("kill_delivered")) and (get("signal") is not None or get("exit_code") is not None)


class AvFixture(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out, self.bin = self.mkdir("assets"), self.mkdir("out"), self.mkdir("bin")
        self.video, self.wav = seed_inputs(self.assets)
        self.ff, self.fp = make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE"))
        self.marker = os.path.join(self.tmp, MARKER)
        self.manifest = dumps(av_manifest())
        self.mapped = self.enterContext(owned_python_tools(self.ff, self.fp))

    def go(self, name="r1", **kw):
        return mux(self.manifest, asset_root=self.assets, output_root=self.out, output_name=name, ffmpeg=self.ff, ffprobe=self.fp, **kw)

    def cancel_at(self, tool, needle, **kw):
        wrap_tools(self.bin, tool=tool, needle=needle, marker=self.marker, sleep=8, **kw)

    def run_cancelled(self):
        with sigint_default():
            interrupt_main_when(self.marker)
            return self.go()


class AvmuxCancelBeforeRunDir(AvFixture):
    def check(self, tool, needle, phase):
        self.cancel_at(tool, needle)
        before = tree_snapshot(self.out)
        with sigint_default():
            interrupt_main_when(self.marker)
            with self.assertRaises(KeyboardInterrupt) as cm:
                self.go()
        res = cm.exception.result
        self.assertIsInstance(cm.exception, runner.RunInterrupted)
        self.assertEqual((res.outcome, res.phase), (runner.INTERRUPTED, phase))
        self.assertTrue(killed_for_real(res), "the owned child was really terminated by the runner")
        self.assertEqual(tree_snapshot(self.out), before, "no output directory and no receipt were pretended")
        last = launches(self.bin)[-1]
        self.assertEqual(last["tool"], tool, "the cancelled call is the last child ever launched")
        self.assertIn(needle[0], " ".join(last["argv"]))
        return launches(self.bin)

    def test_cancel_during_the_ffmpeg_version_probe(self):
        self.assertEqual(len(self.check("ffmpeg", ["-version"], "probe_version:ffmpeg")), 1)

    def test_cancel_during_the_ffprobe_version_probe_does_not_go_on_to_capabilities(self):
        self.assertEqual([c["tool"] for c in self.check("ffprobe", ["-version"], "probe_version:ffprobe")], ["ffmpeg", "ffprobe"])

    def test_cancel_during_a_capability_listing(self):
        self.assertEqual(self.check("ffmpeg", ["-encoders"], "probe_capability:-encoders")[-1]["argv"][-1], "-encoders")

    def test_cancel_during_the_preflight_probe_of_the_video_does_not_probe_the_audio(self):
        all_ = self.check("ffprobe", ["clip.mp4"], "preflight_probe_video")
        self.assertFalse([c for c in all_ if c["tool"] == "ffprobe" and "stem.wav" in " ".join(c["argv"])])

    def test_cancel_during_the_preflight_probe_of_the_audio(self):
        self.check("ffprobe", ["stem.wav"], "preflight_probe_audio")


class AvmuxCancelAfterRunDir(AvFixture):
    def cancelled_receipt(self, tool, needle):
        self.cancel_at(tool, needle)
        r = self.run_cancelled()
        d = os.path.join(self.out, "r1")
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)), "returned and on-disk receipts agree")
        self.assertEqual((r["status"], r["quality_evaluated"]), ("INTERRUPTED", False))
        self.assertTrue(r["technical_verdict"] is None or r["technical_verdict"]["met"] is False)
        last = r["executions"][-1]
        self.assertEqual((last["outcome"], last["kill_reason"]), ("INTERRUPTED", "interrupt"))
        self.assertTrue(killed_for_real(last))
        self.assertIn(needle[0], " ".join(launches(self.bin)[-1]["argv"]), "no child was launched after the cancelled one")
        self.assertFalse(os.path.exists(os.path.join(d, "output.mov")), "nothing was promoted")
        return r, d

    def test_cancel_during_a_readback_decode_is_interrupted_not_inspection_failed(self):
        r, d = self.cancelled_receipt("ffmpeg", ["framemd5"])
        self.assertEqual(r["executions"][-1]["phase"], "readback_decoded_source")
        by = {o["name"]: o for o in r["outputs"]}
        self.assertTrue(by["output.mov.partial"]["partial"])
        self.assertEqual(by["output.mov.partial"]["bytes"], os.path.getsize(os.path.join(d, "output.mov.partial")))
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertFalse(os.path.exists(os.path.join(d, "tmp")))
        self.assertTrue(r["input"]["inputs_unchanged"])
        for k in ("video", "audio"):
            self.assertEqual(r["input"][k]["sha256_after"], r["input"][k]["sha256_before"])

    def test_cancel_during_the_readback_probe_of_the_output_records_that_probe(self):
        r, _ = self.cancelled_receipt("ffprobe", ["output.mov.partial"])
        self.assertEqual((r["executions"][-1]["phase"], r["stage"]), ("readback_probe_output", "readback"))

    def test_cancel_during_the_mux_step_itself_is_interrupted_and_the_partial_is_kept(self):
        self.cancel_at("ffmpeg", ["output.mov.partial"])
        r = self.run_cancelled()
        self.assertEqual((r["status"], r["executions"][-1]["phase"], r["stage"]), ("INTERRUPTED", "mux", "mux"))
        self.assertEqual(len([c for c in launches(self.bin) if c["tool"] == "ffmpeg" and c["argv"] and c["argv"][-1].endswith(".partial")]), 1, "the mux was not retried")


class AvmuxInjectedBoundary(AvFixture):
    """INJECTED: RunInterrupted raised at a named boundary (no thread, no timing). The mux itself is a REAL owned child that ran to completion first."""

    def test_interrupt_at_the_readback_probe_boundary_after_a_real_mux(self):
        res = runner.RunResult(phase="readback_probe_output", argv=["injected"], outcome=runner.INTERRUPTED, kill_reason="interrupt")
        real = M.readback.run_probe

        def boundary(ffprobe, path, demuxer, limits, cwd, phase="inspect"):
            if phase == "readback_probe_output":  # only this named boundary is injected; the preflight probes are real owned children
                raise runner.RunInterrupted(res)
            return real(ffprobe, path, demuxer, limits, cwd, phase=phase)
        with mock.patch.object(M.readback, "run_probe", boundary):
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual(r["status"], "INTERRUPTED")
        self.assertEqual(r["executions"][-1]["argv"], ["injected"], "the injected result is on record, labelled by its own argv")
        self.assertIn("mux", [e["phase"] for e in r["executions"]])
        mux_exec = [e for e in r["executions"] if e["phase"] == "mux"][0]
        self.assertEqual((mux_exec["outcome"], mux_exec["exit_code"]), ("EXITED_ZERO", 0), "the real mux child exited on its own")
        self.assertEqual({o["name"] for o in r["outputs"]} >= {"output.mov.partial", "plan.canonical.json"}, True)
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertEqual(jl(os.path.join(d, "receipt.json"))["status"], "INTERRUPTED")

    def test_plain_keyboard_interrupt_in_the_readback_is_interrupted_too(self):
        with mock.patch.object(M, "_readback", side_effect=KeyboardInterrupt()):
            r = self.go()
        self.assertEqual(r["status"], "INTERRUPTED")
        self.assertNotIn("internal_error", r)


class RenderFixture(TmpCase):
    def setUp(self):
        super().setUp()
        self.out, self.bin = self.mkdir("out"), self.mkdir("bin")
        self.ff, self.fp = make_fake_tools(self.bin)
        self.marker = os.path.join(self.tmp, MARKER)
        self.mapped = self.enterContext(owned_python_tools(self.ff, self.fp))

    def go(self, name="r1", episode=None, asset_root=None, **kw):
        return render(episode or sample_bytes(), asset_root=asset_root, output_root=self.out, output_name=name, ffmpeg=self.ff, ffprobe=self.fp, **kw)


class RenderCancel(RenderFixture):
    def test_cancel_during_version_and_capability_probes_creates_nothing_and_launches_nothing_more(self):
        for i, (tool, needle) in enumerate((("ffmpeg", ["-version"]), ("ffprobe", ["-version"]), ("ffmpeg", ["-encoders"]), ("ffmpeg", ["-filters"]))):
            with self.subTest(tool=tool, needle=needle):
                bin_ = self.mkdir(f"bin{i}")
                ff, fp = make_fake_tools(bin_)
                marker = os.path.join(self.tmp, f"stage{i}.pid")
                wrap_tools(bin_, tool=tool, needle=needle, marker=marker, sleep=8)
                before = tree_snapshot(self.out)
                with owned_python_tools(ff, fp), sigint_default():
                    interrupt_main_when(marker)
                    with self.assertRaises(KeyboardInterrupt) as cm:
                        render(sample_bytes(), asset_root=None, output_root=self.out, output_name="r1", ffmpeg=ff, ffprobe=fp)
                self.assertEqual(cm.exception.result.outcome, runner.INTERRUPTED)
                self.assertTrue(killed_for_real(cm.exception.result))
                self.assertEqual(tree_snapshot(self.out), before)
                self.assertIn(needle[0], " ".join(launches(bin_)[-1]["argv"]), "the cancelled call is the last child ever launched")

    def test_cancel_during_the_readback_probe_is_interrupted_and_inventories_what_exists(self):
        wrap_tools(self.bin, tool="ffprobe", needle=["output.mp4"], marker=self.marker, sleep=8)
        with sigint_default():
            interrupt_main_when(self.marker)
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))
        self.assertEqual((r["status"], r["stage"]), (rc.STATUS_INTERRUPTED, "inspect"))
        last = r["executions"][-1]
        self.assertEqual((last["phase"], last["outcome"]), ("inspect", "INTERRUPTED"))
        self.assertTrue(killed_for_real(last))
        self.assertIn("output.mp4", {o["name"] for o in r["outputs"]})
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertEqual(len([c for c in launches(self.bin) if c["tool"] == "ffprobe" and "output.mp4" in " ".join(c["argv"])]), 1, "no retry")

    def test_injected_interrupt_at_the_readback_boundary(self):
        res = runner.RunResult(phase="inspect", argv=["injected"], outcome=runner.INTERRUPTED, kill_reason="interrupt")
        with mock.patch.object(render_mod.readback, "run_probe", side_effect=runner.RunInterrupted(res)):
            r = self.go()
        self.assertEqual((r["status"], r["executions"][-1]["argv"]), (rc.STATUS_INTERRUPTED, ["injected"]))
        render_exec = [e for e in r["executions"] if e["phase"] == "render"][0]
        self.assertEqual((render_exec["outcome"], render_exec["exit_code"]), ("EXITED_ZERO", 0), "the real render child exited on its own")
        self.assertEqual(r["tmp_cleanup"], "removed")


class AvmuxReceiptFinalization(AvFixture):
    def test_input_vanishing_during_the_mux_is_unknown_with_a_full_inventory(self):
        wav_path = os.path.join(self.assets, "stem.wav")
        wrap_tools(self.bin, tool="ffmpeg", needle=["output.mov.partial"], remove=wav_path)
        r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("became unreadable", r["unknown_reason"])
        aud = r["input"]["audio"]
        self.assertIsNone(aud["sha256_after"])
        self.assertIn(aud["sha256_after_error"], ("FileNotFoundError", "PermissionError", "OSError"))
        self.assertEqual(r["input"]["video"]["sha256_after"], r["input"]["video"]["sha256_before"])
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("plan.canonical.json", {o["name"] for o in r["outputs"]})
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertFalse(os.path.exists(os.path.join(d, "tmp")))
        self.assertFalse(os.path.exists(wav_path), "the original asset is not recreated")
        self.assertEqual(len([c for c in launches(self.bin) if c["tool"] == "ffmpeg" and c["argv"] and c["argv"][-1].endswith(".partial")]), 1, "no retry")

    def test_input_changed_during_the_mux_is_unknown_and_both_hashes_are_on_record(self):
        vpath = os.path.join(self.assets, "clip.mp4")
        make_av_tools(self.bin, **default_behavior("MOV_PCM_S16LE", touch_input=vpath))
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        v = r["input"]["video"]
        self.assertNotEqual(v["sha256_after"], v["sha256_before"])
        self.assertIsNotNone(v["sha256_after"])
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("input file changed", r["unknown_reason"])
        self.assertNotIn(r["status"], M.OK_STATUSES)

    def test_unexpected_exception_in_the_readback_lists_everything_on_disk(self):
        with mock.patch.object(M, "_readback", side_effect=RuntimeError("original failure")):
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual((r["status"], r["internal_error"]["type"], r["internal_error"]["message"], r["internal_error"]["stage"]), (rc.STATUS_UNKNOWN, "RuntimeError", "original failure", "readback"))
        by = {o["name"]: o for o in r["outputs"]}
        self.assertTrue({"output.mov.partial", "plan.canonical.json"} <= set(by))
        self.assertEqual(by["fake_observed.json"]["role"], "unrecognised")
        self.assertTrue(by["output.mov.partial"]["partial"])
        self.assertEqual(by["output.mov.partial"]["bytes"], os.path.getsize(os.path.join(d, "output.mov.partial")))
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))

    def test_a_failing_cleanup_and_a_failing_hasher_never_replace_the_original_failure(self):
        real_hash, armed = M.tools.sha256_file, []

        def readback(*a, **k):
            armed.append(1)
            raise RuntimeError("original failure")

        def hasher(path, max_bytes=None):
            if armed:
                raise PermissionError("denied")
            return real_hash(path, max_bytes)
        with mock.patch.object(M, "_readback", readback), mock.patch.object(finalize.shutil, "rmtree", side_effect=OSError("busy")), \
                mock.patch.object(M.tools, "sha256_file", hasher), mock.patch.object(finalize, "sha256_file", hasher):
            r = self.go()
        self.assertEqual((r["status"], r["internal_error"]["type"], r["internal_error"]["message"]), (rc.STATUS_UNKNOWN, "RuntimeError", "original failure"))
        self.assertEqual(r["tmp_cleanup"], "left in place: OSError")
        self.assertTrue(os.path.isdir(os.path.join(self.out, "r1", "tmp")))
        self.assertEqual((r["input"]["video"]["sha256_after"], r["input"]["video"]["sha256_after_error"]), (None, "PermissionError"))
        by = {o["name"]: o for o in r["outputs"]}
        self.assertEqual((by["output.mov.partial"]["sha256"], by["output.mov.partial"]["error"]), (None, "PermissionError"))
        self.assertEqual(jl(os.path.join(self.out, "r1", "receipt.json"))["status"], rc.STATUS_UNKNOWN)

    def test_previous_run_directories_are_never_touched_by_a_failed_run(self):
        ok = self.go("first")
        self.assertIn(ok["status"], M.OK_STATUSES)
        snap = tree_snapshot(os.path.join(self.out, "first"))
        with mock.patch.object(M, "_readback", side_effect=RuntimeError("x")):
            self.go("second")
        self.assertEqual(tree_snapshot(os.path.join(self.out, "first")), snap)


class RenderReceiptFinalization(RenderFixture):
    def test_unexpected_exception_after_the_probe_lists_the_deliverable_the_probe_json_and_cleans_tmp(self):
        with mock.patch.object(render_mod.readback, "summarize", side_effect=RuntimeError("original failure")):
            r = self.go()
        d = os.path.join(self.out, "r1")
        self.assertEqual((r["status"], r["internal_error"]["message"]), (rc.STATUS_UNKNOWN, "original failure"))
        by = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(set(by), {"output.mp4", "captions.srt", "plan.canonical.json", "ffprobe.json", "fake_observed.json"})
        self.assertEqual(by["output.mp4"]["bytes"], os.path.getsize(os.path.join(d, "output.mp4")))
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))

    def test_unlisted_files_are_reported_only_on_the_error_path(self):
        with mock.patch.object(render_mod.readback, "summarize", side_effect=RuntimeError("original failure")):
            r = self.go()
        self.assertIn(("fake_observed.json", "unrecognised"), {(o["name"], o["role"]) for o in r["outputs"]})
        ok = self.go("r2")
        self.assertEqual(ok["status"], rc.STATUS_MATCH)
        self.assertNotIn("unrecognised", {o["role"] for o in ok["outputs"]}, "the normal-path receipt shape is unchanged")

    def test_an_asset_that_vanishes_during_the_render_is_unknown_with_null_after_hash_and_a_reason(self):
        clip = self.write("assets/clip.mp4", b"A" * 1000)
        doc = episode_doc(clips=[{"id": "v", "start_frame": 0, "duration_frames": 180, "source": {"kind": "file", "path": "clip.mp4"}}], captions=[])
        wrap_tools(self.bin, tool="ffmpeg", needle=["output.mp4"], remove=clip)
        r = self.go("gone", episode=to_bytes(doc), asset_root=os.path.join(self.tmp, "assets"))
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("became unreadable", r["unknown_reason"])
        a = r["input"]["assets"][0]
        self.assertEqual((a["path"], a["sha256_after"]), ("clip.mp4", None))
        self.assertIn(a["sha256_after_error"], ("FileNotFoundError", "PermissionError", "OSError"))
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("output.mp4", {o["name"] for o in r["outputs"]})
        self.assertFalse(os.path.exists(clip), "never recreated")

    def test_an_asset_changed_during_the_render_is_unknown_not_a_match(self):
        clip = self.write("assets/clip.mp4", b"A" * 1000)
        make_fake_tools(self.bin, touch_input=clip)
        doc = episode_doc(clips=[{"id": "v", "start_frame": 0, "duration_frames": 180, "source": {"kind": "file", "path": "clip.mp4"}}], captions=[])
        r = self.go("changed", episode=to_bytes(doc), asset_root=os.path.join(self.tmp, "assets"))
        a = r["input"]["assets"][0]
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertNotEqual(a["sha256_after"], a["sha256_before"])
        self.assertFalse(r["input"]["inputs_unchanged"])


class DrainBreachIsNotVerified(RenderFixture):
    def test_render_whose_ffmpeg_flooded_stderr_is_never_a_match_whichever_way_the_race_goes(self):
        make_fake_tools(self.bin, noise=200_000)
        r = self.go(limits=Limits(max_drain_bytes=1000))
        ex = [e for e in r["executions"] if e["phase"] == "render"][0]
        self.assertTrue(ex["drain_limit_exceeded"])
        self.assertNotIn(r["status"], (rc.STATUS_MATCH, rc.STATUS_MISMATCH))
        self.assertIsNone(r["readback"], "no readback was run on a breached execution")
        if r["status"] == rc.STATUS_FLOOD:
            self.assertEqual((ex["kill_reason"], ex["kill_attempted"]), ("flood", True))
        else:
            self.assertEqual((r["status"], ex["outcome"], ex["exit_code"], ex["kill_attempted"], ex["kill_reason"]), (rc.STATUS_UNKNOWN, "EXITED_ZERO", 0, False, None))
            self.assertIn("the bound was breached", r["unknown_reason"])

    def test_state_boundary_a_child_that_exited_zero_with_a_breached_bound_is_unknown_and_not_called_killed(self):
        """The RunResult of a REAL owned render child is marked as breached (the only injected fact); the orchestrator must refuse to verify it."""
        real = runner.run_bounded

        def crafted(argv, **kw):
            res = real(argv, **kw)
            if kw.get("phase") == "render":
                res.drain_limit_exceeded, res.drain_limit_bytes = True, 1000
            return res
        with mock.patch.object(render_mod.runner, "run_bounded", crafted):
            r = self.go()
        ex = [e for e in r["executions"] if e["phase"] == "render"][0]
        self.assertEqual((r["status"], ex["outcome"], ex["exit_code"], ex["kill_attempted"]), (rc.STATUS_UNKNOWN, "EXITED_ZERO", 0, False))
        self.assertIn("exited on its own", r["unknown_reason"])
        self.assertIsNone(r["readback"])
        self.assertFalse(os.path.exists(os.path.join(self.out, "r1", "output.mp4")), "not promoted to a deliverable name")

    def test_a_probe_that_breaches_the_bound_is_not_trusted(self):
        from video_core import tools
        make_fake_tools(self.bin, version_line_ffmpeg="ffmpeg version 1 " + "x" * 5000)
        rec, runs = tools.probe_version("ffmpeg", self.ff, "explicit", Limits(max_drain_bytes=1000), self.tmp)
        self.assertTrue(runs[0].drain_limit_exceeded)
        self.assertIn("drain bound", rec.problem)
        self.assertIsNone(rec.version)


class HarnessHonesty(TmpCase):
    """The adapter changes only WHICH program is executed for registered fake-tool paths; it must not change how it is executed."""

    def test_it_delegates_to_the_real_run_bounded_with_unchanged_keywords_and_maps_only_registered_paths(self):
        bin_ = self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        spy = mock.Mock(wraps=runner.run_bounded)
        with mock.patch.object(runner, "run_bounded", spy), owned_python_tools(ff) as seen:
            a = runner.run_bounded([ff, "-hide_banner", "-version"], phase="p1", timeout_s=7, stderr_limits=(64, 64), stdout_limits=(4096, 0), cwd=self.tmp)
            b = runner.run_bounded([sys.executable, "-c", "print('hi')"], phase="p2", timeout_s=7, stdout_limits=(64, 0))
        self.assertEqual([x["tool_path"] for x in seen], [os.path.abspath(ff)], "only the registered path was mapped; the interpreter call was passed through")
        self.assertEqual((a.argv[0], a.outcome, a.timeout_seconds), (sys.executable, runner.EXITED_ZERO, 7), "a real owned child with the caller's timeout")
        self.assertIn(b"ffmpeg version", a.stdout_bytes())
        self.assertEqual((b.argv[0], b.outcome), (sys.executable, runner.EXITED_ZERO))
        inner = [c for c in spy.call_args_list if c.kwargs.get("phase") == "p1"]
        self.assertEqual((inner[0].kwargs["stderr_limits"], inner[0].kwargs["cwd"]), ((64, 64), self.tmp))

    def test_it_refuses_a_path_that_is_not_a_regular_file(self):
        with self.assertRaises(AssertionError):
            with owned_python_tools(os.path.join(self.tmp, "does-not-exist")):
                pass

    def test_crlf_emulation_reaches_the_parsers(self):
        bin_ = self.mkdir("bin")
        ff, fp = make_fake_tools(bin_)
        with owned_python_tools(ff, crlf=True):
            res = runner.run_bounded([ff, "-version"], phase="v", timeout_s=7, stdout_limits=(4096, 0))
        self.assertTrue(res.stdout_bytes().endswith(b"\r\n"))
        with owned_python_tools(ff, crlf=False):
            res = runner.run_bounded([ff, "-version"], phase="v", timeout_s=7, stdout_limits=(4096, 0))
        if os.name == "posix":  # on Windows plain text mode is CRLF natively
            self.assertNotIn(b"\r\n", res.stdout_bytes())

    @unittest.skip("UNVERIFIED on Windows here: real signal / console delivery (SIGTERM translated by the CLIs, Ctrl-C) needs a console or process-group privilege that this harness does "
                   "not use; the CLI exit-130 scenarios stay POSIX-only in tests/test_lifecycle_orchestrators.py and are for the coordinator to judge separately")
    def test_real_signal_delivery_to_a_cli_process(self):
        pass


if __name__ == "__main__":
    unittest.main()
