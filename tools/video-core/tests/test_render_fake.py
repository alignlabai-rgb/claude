"""Render orchestration against FAKE ffmpeg/ffprobe scripts (see helpers.py).

These tests prove process handling, receipts and file-system protection. They do not
prove that real FFmpeg produces a correct video; see test_integration_ffmpeg.py and
evidence/ for that.
"""

import hashlib
import json
import os
import unittest

from tests.helpers import (jl, rb, POSIX, EXAMPLE_SRT, TmpCase, color_clips, episode_doc, fake_calls, good_probe, make_fake_tools, mutated,
                           sample_bytes, to_bytes, tree_snapshot)
from tests.fontbuilder import make_font
from video_core import Limits, prepare
from video_core.render import render
from video_core import receipt as rc
from video_core.errors import UnsafePath, ValidationFailed


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class RenderFake(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets = self.mkdir("assets")
        self.out = self.mkdir("out")
        self.bin = self.mkdir("bin")
        self.sentinel = self.write("outside/sentinel.txt", b"do not touch")

    def tools(self, **behavior):
        return make_fake_tools(self.bin, **behavior)

    def go(self, name="run1", episode=None, limits=Limits(), **kw):
        ff, fp = kw.pop("tools", None) or (os.path.join(self.bin, "ffmpeg"), os.path.join(self.bin, "ffprobe"))
        return render(episode or sample_bytes(), limits=limits, asset_root=kw.pop("asset_root", None), output_root=self.out,
                      output_name=name, ffmpeg=ff, ffprobe=fp, **kw)

    def run_dir(self, name="run1"):
        return os.path.join(self.out, name)

    def render_calls(self):
        return [c for c in fake_calls(self.bin) if not any(f in c["argv"] for f in ("-version", "-encoders", "-filters", "-demuxers")) and c["argv"] and c["argv"][0] != "-v"]

    # ------------------------------------------------------------------ success + receipt
    def test_success_receipt_records_versions_hashes_exits_and_ffprobe(self):
        self.tools()
        r = self.go()
        d = self.run_dir()
        self.assertEqual(r["status"], rc.STATUS_MATCH)
        on_disk = jl(os.path.join(d, "receipt.json"))
        self.assertEqual(on_disk["status"], rc.STATUS_MATCH)
        self.assertEqual(on_disk, json.loads(json.dumps(r)))
        self.assertFalse(r["quality_evaluated"])
        self.assertIn("No audio, visual or creative quality", r["note"])
        self.assertEqual(r["tools"]["ffmpeg"]["version"], "0.0-fake")
        self.assertEqual(r["tools"]["ffprobe"]["version"], "0.0-fake")
        self.assertEqual(r["tools"]["ffmpeg"]["executable_sha256"], sha(os.path.join(self.bin, "ffmpeg")))
        # input / plan / output hashes
        self.assertEqual(r["input"]["episode_sha256"], hashlib.sha256(sample_bytes()).hexdigest())
        self.assertEqual(r["plan_sha256"], prepare(sample_bytes()).sha256)
        self.assertEqual(sha(os.path.join(d, "plan.canonical.json")), r["plan_sha256"])
        by_name = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(set(by_name), {"output.mp4", "captions.srt", "plan.canonical.json", "ffprobe.json"})
        for name, rec in by_name.items():
            self.assertEqual(rec["sha256"], sha(os.path.join(d, name)), name)
            self.assertEqual(rec["bytes"], os.path.getsize(os.path.join(d, name)))
            self.assertFalse(rec["partial"])
        self.assertEqual(rb(os.path.join(d, "captions.srt")), rb(EXAMPLE_SRT))
        # every execution: phase, exit, signal, timeout, drain recorded separately
        phases = [e["phase"] for e in r["executions"]]
        self.assertEqual(phases, ["probe_version:ffmpeg", "probe_version:ffprobe", "probe_capability:-encoders", "probe_capability:-filters", "render", "inspect"])
        for e in r["executions"]:
            self.assertEqual((e["outcome"], e["exit_code"], e["signal"], e["timed_out"], e["drain_complete"]), ("EXITED_ZERO", 0, None, False, True))
        self.assertTrue(r["readback"]["comparison"]["all_ok"])
        self.assertEqual(r["readback"]["ffprobe_json_sha256"], by_name["ffprobe.json"]["sha256"])
        self.assertEqual(sorted(os.listdir(d)), ["captions.srt", "fake_observed.json", "ffprobe.json", "output.mp4", "plan.canonical.json", "receipt.json"])
        self.assertEqual(r["tmp_cleanup"], "removed")
        self.assertTrue(r["input"]["inputs_unchanged"])

    def test_actual_command_is_structured_argv_in_an_owned_cwd_with_no_shell_text(self):
        self.tools()
        r = self.go()
        call = self.render_calls()[0]
        argv = call["argv"]
        self.assertEqual(argv[-1], os.path.join(self.run_dir(), "output.mp4.partial"))
        self.assertIn("-n", argv)
        self.assertEqual(argv[argv.index("-frames:v") + 1], "180")
        self.assertEqual(argv[argv.index("-r") + 1], "30/1")
        self.assertTrue(call["cwd"].endswith(os.path.join("run1", "tmp")))
        self.assertEqual(call["home"], call["cwd"], "HOME is redirected to the owned temp dir")
        self.assertNotIn("USER", call["env_keys"])
        self.assertEqual(r["executions"][4]["argv"][1:], argv)
        self.assertEqual(r["executions"][4]["argv"][0], os.path.join(self.bin, "ffmpeg"))

    def test_receipt_says_in_progress_while_ffmpeg_runs(self):
        self.tools()
        self.go()
        seen = jl(os.path.join(self.run_dir(), "fake_observed.json"))
        self.assertEqual(seen["receipt_status_during_render"], rc.STATUS_IN_PROGRESS)
        self.assertEqual(seen["stage"], "render")

    # ------------------------------------------------------------------ readback mismatch
    def test_process_success_alone_is_not_a_pass(self):
        self.tools(probe=good_probe(frames=179))
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_MISMATCH)
        bad = {c["name"] for c in r["readback"]["comparison"]["checks"] if not c["ok"]}
        self.assertIn("decoded_frame_count", bad)
        self.assertEqual(r["executions"][4]["outcome"], "EXITED_ZERO")
        self.assertTrue(os.path.exists(os.path.join(self.run_dir(), "output.mp4")))

    def test_ffprobe_failure_or_garbage_is_inspection_failed(self):
        for mode in ("fail", "garbage"):
            self.tools(probe_mode=mode)
            r = self.go(name=f"run-{mode}")
            self.assertEqual(r["status"], rc.STATUS_INSPECTION_FAILED, mode)
            self.assertIsNotNone(r["readback"]["error"])

    # ------------------------------------------------------------------ failures keep partial / UNKNOWN, never retry
    def test_nonzero_exit_keeps_partial_and_does_not_retry(self):
        self.tools(mode="nonzero")
        r = self.go()
        d = self.run_dir()
        self.assertEqual(r["status"], rc.STATUS_PROCESS_FAILED)
        self.assertEqual(r["stage"], "render")
        ex = r["executions"][-1]
        self.assertEqual((ex["phase"], ex["exit_code"], ex["signal"], ex["timed_out"]), ("render", 7, None, False))
        self.assertIn("simulated failure", ex["stderr"]["text"])
        self.assertTrue(os.path.exists(os.path.join(d, "output.mp4.partial")))
        self.assertFalse(os.path.exists(os.path.join(d, "output.mp4")))
        partial = [o for o in r["outputs"] if o["role"] == "video_partial"][0]
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["sha256"], sha(os.path.join(d, "output.mp4.partial")))
        self.assertEqual(len(self.render_calls()), 1, "exactly one ffmpeg render launch: no retry")
        self.assertNotIn("inspect", [e["phase"] for e in r["executions"]])
        self.assertIsNone(r["readback"])

    def test_timeout_is_bounded_and_preserves_the_partial(self):
        self.tools(mode="sleep")
        r = self.go(limits=Limits(max_runtime_seconds=0.7))
        ex = r["executions"][-1]
        self.assertEqual(r["status"], rc.STATUS_TIMEOUT)
        self.assertEqual((ex["phase"], ex["outcome"], ex["timed_out"], ex["kill_reason"]), ("render", "TIMEOUT", True, "timeout"))
        self.assertLess(ex["wall_seconds"], 20)
        self.assertTrue(os.path.exists(os.path.join(self.run_dir(), "output.mp4.partial")))
        self.assertEqual(len(self.render_calls()), 1)

    def test_signal_termination_is_recorded_as_a_signal(self):
        self.tools(mode="sigkill")
        r = self.go()
        ex = r["executions"][-1]
        self.assertEqual(r["status"], rc.STATUS_SIGNALED)
        self.assertEqual((ex["signal"], ex["exit_code"], ex["timed_out"]), (9, None, False))
        self.assertEqual(len(self.render_calls()), 1)

    def test_diagnostics_are_truncated_but_totals_and_hash_are_kept(self):
        self.tools(noise=300_000)
        r = self.go(limits=Limits(max_diag_bytes=1000))
        st = r["executions"][4]["stderr"]
        self.assertEqual(r["status"], rc.STATUS_MATCH, "noise on stderr does not change the verdict")
        self.assertTrue(st["truncated"])
        self.assertEqual(st["total_bytes"], 300_000 + len("TAIL-MARKER"))
        self.assertEqual(st["kept_bytes"], 1000)
        self.assertEqual(st["sha256_full_stream"], hashlib.sha256(b"E" * 300_000 + b"TAIL-MARKER").hexdigest())
        self.assertIn("TAIL-MARKER", st["text"])
        self.assertLess(len(json.dumps(r)), 200_000, "receipt size stays bounded")

    def test_exit_zero_without_output_is_unknown_not_success(self):
        self.tools(mode="noout")
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("no output", r["unknown_reason"])

    def test_surviving_child_process_makes_the_result_unknown(self):
        self.tools(mode="orphan")
        r = self.go()
        ex = r["executions"][4]
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertEqual(ex["outcome"], "EXITED_ZERO")
        self.assertTrue(ex["orphan_group_killed"])

    def test_launch_failure_is_recorded(self):
        ff, fp = self.tools()
        os.chmod(ff, 0o755)
        with open(ff, "w") as fh:
            fh.write("#!/nonexistent/interpreter\n")
        r = render(sample_bytes(), asset_root=None, output_root=self.out, output_name="x", ffmpeg=ff, ffprobe=fp)
        self.assertEqual(r["status"], rc.STATUS_SKIPPED, "a tool that cannot even report its version is unavailable")
        self.assertFalse(os.path.exists(self.run_dir("x")))

    # ------------------------------------------------------------------ unavailable tools
    def test_missing_tools_are_skipped_without_creating_anything(self):
        before = tree_snapshot(self.tmp)
        r = render(sample_bytes(), asset_root=None, output_root=self.out, output_name="run1",
                   ffmpeg=os.path.join(self.tmp, "no-ffmpeg"), ffprobe=os.path.join(self.tmp, "no-ffprobe"))
        self.assertEqual(r["status"], "SKIPPED_TOOL_UNAVAILABLE")
        self.assertFalse(r["written"])
        self.assertEqual({x["tool"] for x in r["reasons"]}, {"ffmpeg", "ffprobe"})
        self.assertEqual(r["outputs"], [])
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_missing_encoder_or_filter_is_a_skip_not_a_failure(self):
        self.tools(encoder="libx265")
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_SKIPPED)
        self.assertEqual(r["reasons"][0]["detail"]["missing"], {"encoders": ["libx264"]})
        self.assertFalse(os.path.exists(self.run_dir()))
        self.tools(filters=["color", "trim"])
        r = self.go(name="run2")
        self.assertEqual(r["status"], rc.STATUS_SKIPPED)
        self.assertIn("scale", r["reasons"][0]["detail"]["missing"]["filters"])

    # ------------------------------------------------------------------ overwrite / source / output protection
    def test_overwrite_is_refused_and_first_output_is_untouched(self):
        self.tools()
        self.go()
        before = tree_snapshot(self.run_dir())
        with self.assertRaises(UnsafePath) as cm:
            self.go()
        self.assertEqual(cm.exception.issues[0].code, "output_exists")
        self.assertEqual(tree_snapshot(self.run_dir()), before)
        self.assertEqual(len(self.render_calls()), 1)

    def test_preexisting_directory_with_a_user_file_is_never_written_into(self):
        self.tools()
        keep = self.write("out/run1/precious.txt", b"mine")
        before = tree_snapshot(self.out)
        with self.assertRaises(UnsafePath):
            self.go()
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(rb(keep), b"mine")
        self.assertEqual(self.render_calls(), [])

    def test_sources_and_outside_files_are_unchanged_and_only_the_run_dir_is_added(self):
        clip = self.write("assets/clips/a.mp4", b"A" * 1000)
        still = self.write("assets/still.png", b"P" * 500)
        doc = episode_doc(clips=[
            {"id": "v", "start_frame": 0, "duration_frames": 60, "source": {"kind": "file", "path": "clips/a.mp4"}},
            {"id": "i", "start_frame": 60, "duration_frames": 60, "source": {"kind": "file", "path": "still.png"}},
            {"id": "c", "start_frame": 120, "duration_frames": 60, "source": {"kind": "color", "value": "#102030"}}], captions=[])
        self.tools()
        before_assets, before_outside = tree_snapshot(self.assets), tree_snapshot(os.path.dirname(self.sentinel))
        before_out = tree_snapshot(self.out)
        r = self.go(episode=to_bytes(doc), asset_root=self.assets)
        self.assertEqual(r["status"], rc.STATUS_MATCH)
        self.assertEqual(tree_snapshot(self.assets), before_assets)
        self.assertEqual(tree_snapshot(os.path.dirname(self.sentinel)), before_outside)
        self.assertEqual(rb(self.sentinel), b"do not touch")
        self.assertEqual({k.split(os.sep)[0] for k in tree_snapshot(self.out)} - {k.split(os.sep)[0] for k in before_out}, {"run1"})
        self.assertEqual({a["path"]: a["sha256_before"] for a in r["input"]["assets"]}, {"clips/a.mp4": sha(clip), "still.png": sha(still)})
        self.assertEqual({a["sha256_before"] for a in r["input"]["assets"]}, {a["sha256_after"] for a in r["input"]["assets"]})
        argv = self.render_calls()[0]["argv"]
        self.assertIn(os.path.join(self.assets, "clips", "a.mp4"), argv)
        i = argv.index(os.path.join(self.assets, "clips", "a.mp4"))
        self.assertEqual(argv[i - 5:i], ["-protocol_whitelist", "file", "-f", "mov", "-i"])
        outputs = [a for a in argv if a.startswith(self.out)]
        self.assertEqual(outputs, [os.path.join(self.run_dir(), "output.mp4.partial")])

    def test_an_input_modified_during_the_run_is_detected_and_not_called_a_match(self):
        clip = self.write("assets/a.mp4", b"A" * 100)
        doc = episode_doc(clips=[{"id": "v", "start_frame": 0, "duration_frames": 180, "source": {"kind": "file", "path": "a.mp4"}}], captions=[])
        self.tools(touch_input=clip)  # the fake appends to the input while "rendering"
        r = self.go(episode=to_bytes(doc), asset_root=self.assets)
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("input file changed", r["unknown_reason"])

    def test_rejections_happen_before_any_process_or_file(self):
        self.tools()
        before = tree_snapshot(self.tmp)
        bad_docs = [mutated(lambda d: d["clips"][0].update(duration_frames=0)),
                    mutated(lambda d: d["clips"][0].update(source={"kind": "file", "path": "../x.mp4"})),
                    mutated(lambda d: d["captions"][0].update(end_frame=999))]
        for doc in bad_docs:
            with self.assertRaises((ValidationFailed, UnsafePath)):
                self.go(episode=to_bytes(doc), asset_root=self.assets)
        with self.assertRaises(ValidationFailed):
            self.go(limits=Limits(max_frames=10))
        with self.assertRaises(UnsafePath):
            self.go(name="../escape")
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(fake_calls(self.bin), [])

    def test_burn_in_files_are_exact_bytes_in_the_owned_tmp_dir(self):
        self.write("assets/f.ttf", make_font(fmt4=[(0x20, 0x7E), (0x3000, 0x30FF)]))
        text = "テスト A: it's \\ \"q\" %{n}"
        doc = episode_doc(clips=color_clips([90, 90]), captions=[{"id": "a", "start_frame": 5, "end_frame": 50, "text": text}])
        self.tools()
        r = self.go(episode=to_bytes(doc), asset_root=self.assets, burn_in_font="f.ttf")
        self.assertEqual(r["status"], rc.STATUS_MATCH)
        seen = jl(os.path.join(self.run_dir(), "fake_observed.json"))
        self.assertEqual(bytes.fromhex(seen["cap_files"]["cap_0001.txt"]).decode("utf-8"), text)
        argv = self.render_calls()[0]["argv"]
        self.assertFalse(any("テスト" in a or "%{n}" in a for a in argv))
        self.assertEqual(r["input"]["font"]["path"], "f.ttf")
        self.assertEqual(r["tmp_cleanup"], "removed")


if __name__ == "__main__":
    unittest.main()
