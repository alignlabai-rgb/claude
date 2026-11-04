"""Lane 4 - execution / receipt failure semantics with bounded OWNED synthetic children and instrumentation.

* Real children are tiny `python -c` programs or the owned fake ffmpeg/ffprobe in adv_fake_tools.py (never real media tools, never real media).
* Each external child is bounded to <= 10 s (adv_support.child) and signalled only through its own process group / recorded pid.
* No resource envelope is implemented or tested here (assigned to another session).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import struct
import subprocess
import sys
import textwrap
import time
import unittest

import adv_fake_tools as FT
import adv_oracle as O
from adv_support import (AuditCase, POSIX, PY, TOOLS, audited, avmux_manifest, child, cli, jb, known_defect, manifest, pack_pcm, pcm_tone, pid_alive, record,
                         sha256, slurp, tree_listing, wav_from_frames)

from video_core import errors, runner  # noqa: E402
from video_core.audiomix import mix as amix  # noqa: E402
from video_core.audiomix.limits import AudioLimits  # noqa: E402
from video_core.avmux import checks as ck, mux as vmux  # noqa: E402
from video_core.limits import Limits  # noqa: E402


def py(code: str):
    return [PY, "-c", textwrap.dedent(code)]


# --------------------------------------------------------------------------------------------------------------------------- runner
@unittest.skipUnless(POSIX, "POSIX process groups / signals")
class RunnerSemantics(AuditCase):
    def test_outcome_matrix_keeps_exit_signal_timeout_and_drain_separate(self):
        rows = {}

        def run(label, code, **kw):
            kw.setdefault("timeout_s", 5)
            r = runner.run_bounded(py(code), phase=label, **kw)
            rows[label] = (r.outcome, r.exit_code, r.signal, r.timed_out, r.kill_reason, r.drain_complete, r.ok)
            return r
        run("zero", "pass")
        run("nonzero", "import sys; sys.exit(7)")
        run("self_kill", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        run("self_term", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
        t = run("timeout", "import time; print('pid', __import__('os').getpid(), flush=True); time.sleep(30)", timeout_s=0.6, grace_s=0.5, stdout_limits=(64, 0))
        pid = int(t.stdout_bytes().split()[1])
        self.assertFalse(pid_alive(pid), "timed-out child must be gone")
        stubborn = run("timeout_ignores_sigterm", "import signal, time, os; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('pid', os.getpid(), flush=True); time.sleep(30)",
                       timeout_s=0.6, grace_s=0.4, stdout_limits=(64, 0))
        self.assertFalse(pid_alive(int(stubborn.stdout_bytes().split()[1])))
        fl = run("flood", "import sys, time\nwhile True: sys.stdout.buffer.write(b'x'*65536); sys.stdout.flush()", max_drain_bytes=300_000, timeout_s=8)
        launch = runner.run_bounded(["/definitely/not/here/ffmpeg"], phase="launch", timeout_s=2)
        rows["launch_failed"] = (launch.outcome, launch.exit_code, launch.signal, launch.timed_out, launch.launch_error is not None)
        record("runner_matrix", rows=rows)
        self.assertEqual(rows["zero"], ("EXITED_ZERO", 0, None, False, None, True, True))
        self.assertEqual(rows["nonzero"], ("NONZERO_EXIT", 7, None, False, None, True, False))
        self.assertEqual(rows["self_kill"][:3], ("SIGNALED", None, 9))
        self.assertEqual(rows["self_term"][:3], ("SIGNALED", None, 15))
        self.assertEqual(rows["timeout"][:5], ("TIMEOUT", None, 15, True, "timeout"))
        self.assertEqual(rows["timeout_ignores_sigterm"][:5], ("TIMEOUT", None, 9, True, "timeout"))
        self.assertEqual(rows["flood"][0], "FLOOD_KILLED")
        self.assertGreater(fl.stdout.total, 300_000)
        self.assertEqual(rows["launch_failed"], ("LAUNCH_FAILED", None, None, False, True))

    def test_wall_clock_bound_is_respected_for_a_silent_child(self):
        t0 = time.monotonic()
        r = runner.run_bounded(py("import time; time.sleep(30)"), phase="t", timeout_s=0.5, grace_s=0.3)
        wall = time.monotonic() - t0
        record("runner_timeout_wall", wall=round(wall, 3))
        self.assertEqual(r.outcome, "TIMEOUT")
        self.assertLess(wall, 3.0)

    def test_invalid_numeric_arguments_fail_before_any_launch(self):
        body = r'''
        import json
        from video_core import runner
        res = {}
        for label, kw in (("nan", {"timeout_s": float("nan")}), ("inf", {"timeout_s": float("inf")}), ("neg", {"timeout_s": -1}), ("zero", {"timeout_s": 0}), ("bool", {"timeout_s": True}),
                          ("big_drain", {"timeout_s": 1, "max_drain_bytes": 0}), ("grace_nan", {"timeout_s": 1, "grace_s": float("nan")}),
                          ("limits_pair", {"timeout_s": 1, "stderr_limits": (1,)}), ("limits_neg", {"timeout_s": 1, "stdout_limits": (-1, 0)})):
            try:
                runner.run_bounded(["/bin/true"], phase="t", **kw); res[label] = "RAN"
            except ValueError as exc:
                res[label] = "ValueError"
            except BaseException as exc:
                res[label] = "LEAK:" + type(exc).__name__
        for label, argv in (("nul", ["a\x00b"]), ("empty", []), ("str", "ls"), ("int", [1])):
            try:
                runner.run_bounded(argv, phase="t", timeout_s=1); res[label] = "RAN"
            except TypeError:
                res[label] = "TypeError"
            except BaseException as exc:
                res[label] = "LEAK:" + type(exc).__name__
        print(json.dumps(res))
        '''
        rc, out, err, events, to = audited(body)
        self.assertEqual((rc, to), (0, False), err[-300:])
        res = json.loads(out)
        self.assertTrue(all(v in ("ValueError", "TypeError") for v in res.values()), res)
        self.assertEqual([e for e in events if e[0].startswith(("subprocess", "os.exec", "os.posix_spawn"))], [], "no launch may precede validation")

    def test_incomplete_drain_is_recorded_and_the_owned_group_is_cleaned(self):
        """One grandchild (self-expiring after 5 s) inherits the pipes after its parent exited 0. This replicates an existing scenario
        independently and adds a survivor check by pid."""
        sb = self.sb
        marker = sb.p("gc.pid")
        code = f"""
        import subprocess, sys
        subprocess.Popen([sys.executable, "-c", "import os,time; open({marker!r},'w').write(str(os.getpid())); time.sleep(5)"])
        import time
        while not __import__('os').path.exists({marker!r}): time.sleep(0.01)
        """
        r = runner.run_bounded(py(code), phase="drain", timeout_s=6, drain_timeout_s=0.5, grace_s=0.5)
        pid = int(slurp(marker))
        record("drain_incomplete", outcome=r.outcome, drain_complete=r.drain_complete, orphan_group_killed=r.orphan_group_killed, ok=r.ok, survivor=pid_alive(pid))
        self.assertEqual((r.outcome, r.drain_complete, r.orphan_group_killed, r.ok), ("EXITED_ZERO", True, True, False))  # drain completes only AFTER the group kill; orphan_group_killed is the signal
        time.sleep(0.2)
        self.assertFalse(pid_alive(pid), "the pipe-holding grandchild was in our group and must have been killed")

    def test_diagnostic_truncation_keeps_exact_head_tail_totals_and_full_stream_hash(self):
        head, tail = 5, 3
        for size in (0, 1, 3, 5, 7, 8, 9, 10, 100, 70000):
            data = bytes((i * 7 + 3) % 251 for i in range(size))
            for chunked in (False, True):
                code = f"""
                import os
                d = bytes((i * 7 + 3) % 251 for i in range({size}))
                if {chunked}:
                    for i in range(0, len(d), 3): os.write(2, d[i:i+3])
                else:
                    off = 0
                    while off < len(d): off += os.write(2, d[off:])
                """
                r = runner.run_bounded(py(code), phase="d", timeout_s=6, stderr_limits=(head, tail))
                s = r.stderr.summary()
                kept = r.stderr.raw()
                self.assertEqual(s["total_bytes"], size, (size, chunked))
                self.assertEqual(s["sha256_full_stream"], hashlib.sha256(data).hexdigest(), (size, chunked))
                if size <= head + tail:
                    self.assertEqual(kept, data)
                    self.assertFalse(s["truncated"])
                else:
                    self.assertEqual(kept, data[:head] + data[-tail:], (size, chunked))
                    self.assertTrue(s["truncated"])
                    self.assertEqual(s["dropped_bytes"], size - head - tail)
                self.assertEqual(s["kept_bytes"] + s["dropped_bytes"], size)
        zero = runner.run_bounded(py("import sys; sys.stderr.write('abc')"), phase="d", timeout_s=4, stderr_limits=(0, 0))
        self.assertEqual((zero.stderr.raw(), zero.stderr.summary()["total_bytes"]), (b"", 3))


class RunnerDefects(AuditCase):
    @known_defect("F-RUN-1")
    @unittest.skipUnless(POSIX, "POSIX")
    def test_output_beyond_the_drain_limit_is_never_reported_as_a_clean_exit(self):
        """Racy classification: a child that writes more than max_drain_bytes and exits inside the 50 ms poll window is EXITED_ZERO/ok,
        while the same child lingering one second is FLOOD_KILLED. The bound is a documented limit; its breach must be visible either way."""
        quick = runner.run_bounded(py("import sys; sys.stdout.buffer.write(b'x'*2000000)"), phase="q", timeout_s=6, stdout_limits=(8, 8), max_drain_bytes=1000)
        slow = runner.run_bounded(py("import sys, time; sys.stdout.buffer.write(b'x'*2000000); sys.stdout.flush(); time.sleep(1)"), phase="s", timeout_s=6, stdout_limits=(8, 8), max_drain_bytes=1000)
        record("F-RUN-1", quick=[quick.outcome, quick.ok, quick.stdout.total, quick.kill_reason], slow=[slow.outcome, slow.ok, slow.stdout.total, slow.kill_reason])
        self.harness_check(slow.outcome == "FLOOD_KILLED", "control case (lingering child) must be FLOOD_KILLED")
        self.assertEqual((quick.outcome, quick.ok), ("FLOOD_KILLED", False))

    @known_defect("F-RUN-2")
    @unittest.skipUnless(POSIX, "POSIX")
    def test_interrupt_during_pipe_drain_does_not_leave_a_member_of_the_owned_group_running(self):
        sb = self.sb
        marker = sb.p("gc.pid")
        sb.write("child_prog.py", _DRAIN_CHILD.replace("@@MARKER@@", repr(marker)).encode())
        harness = sb.write("harness.py", _DRAIN_HARNESS.replace("@@TOOLS@@", repr(TOOLS)).replace("@@MARKER@@", repr(marker)).replace("@@CHILD@@", repr(sb.p("child_prog.py"))).encode())
        rc, out, err, to = child([PY, harness], timeout=9)
        record("F-RUN-2", rc=rc, out=out.decode().split(), timed_out=to, err=err.decode()[-200:])
        self.harness_check((rc, to) == (0, False) and "KeyboardInterrupt" in out.decode(), "scenario did not run: rc=%r err=%s" % (rc, err.decode()[-300:]))
        self.assertIn("alive=False", out.decode(), out.decode())


_DRAIN_CHILD = r'''
import subprocess, sys, time, os
subprocess.Popen([sys.executable, "-c", "import os,time; open(" + repr(@@MARKER@@) + ",'w').write(str(os.getpid())); time.sleep(5)"])
while not os.path.exists(@@MARKER@@):
    time.sleep(0.01)
'''

_DRAIN_HARNESS = r'''
import os, signal, sys, threading, time
sys.path.insert(0, @@TOOLS@@)
from video_core import runner
# A background job started with `&` by a non-interactive shell inherits SIGINT=SIG_IGN, and Python then does NOT install its KeyboardInterrupt
# handler. Make the scenario independent of how the suite was launched (this masked the scenario in the first full matrix run).
signal.signal(signal.SIGINT, signal.default_int_handler)

def interrupt_when_the_pipe_holder_exists_and_its_parent_has_exited():
    t0 = time.monotonic()
    while not os.path.exists(@@MARKER@@) and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    time.sleep(0.6)                      # the parent child_prog exits right after the marker exists; run_bounded is now in its drain wait
    os.kill(os.getpid(), signal.SIGINT)

threading.Thread(target=interrupt_when_the_pipe_holder_exists_and_its_parent_has_exited, daemon=True).start()
try:
    runner.run_bounded([sys.executable, @@CHILD@@], phase="t", timeout_s=8, drain_timeout_s=3.0, grace_s=1.0)
    print("returned")
except KeyboardInterrupt:
    print("KeyboardInterrupt")
pid = int(open(@@MARKER@@).read())
try:
    os.kill(pid, 0); alive = True
except ProcessLookupError:
    alive = False
print("alive=%s" % alive, flush=True)
if alive:
    os.kill(pid, signal.SIGKILL)       # our own grandchild: clean up after ourselves
'''


# --------------------------------------------------------------------------------------------------------------------------- audio mixer stops
class AudioMixerStops(AuditCase):
    def _env(self, *, n_frames=3, dc=None, doc_over=None):
        sb = self.sb
        samples = n_frames * 1600
        frames = [(dc if dc is not None else (i % 200) - 100,) for i in range(samples)]
        w = wav_from_frames(frames)
        sb.write("assets/a.wav", w)
        ev = lambda eid: {"id": eid, "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": samples, "start_frame": 0, "end_frame": n_frames}
        doc = manifest(total=n_frames, assets=[{"id": "a", "path": "a.wav", "sha256": sha256(w)}], events=[ev("u")])
        if doc_over:
            doc_over(doc, ev)
        self.out = sb.mkdir("out")
        return doc

    def _disk_receipt(self, name):
        with open(self.sb.p("out", name, "receipt.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_cancel_keeps_a_partial_that_is_labelled_and_never_looks_like_a_stem(self):
        doc = self._env()
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] >= 2
        r = amix.mix(jb(doc), limits=AudioLimits(chunk_samples=1600), asset_root=self.sb.p("assets"), output_root=self.out, output_name="c1", cancel=cancel)
        listing = sorted(os.listdir(self.sb.p("out", "c1")))
        part = self.sb.p("out", "c1", "stem.wav.partial")
        with open(part, "rb") as fh:
            raw = fh.read()
        record("mixer_cancel", status=r["status"], listing=listing, samples_written=r["mix"]["samples_written"], partial_bytes=len(raw))
        self.assertEqual(r["status"], "CANCELLED")
        self.assertEqual(listing, ["plan.canonical.json", "receipt.json", "stem.wav.partial"])
        self.assertEqual(r["mix"]["samples_written"], 1600)
        self.assertEqual(len(raw), 44 + 1600 * 2)
        self.assertEqual(struct.unpack_from("<I", raw, 40)[0], 4800 * 2, "partial header still declares the PLANNED length (documented): it is not a valid WAV")
        self.assertEqual(self._disk_receipt("c1")["status"], "CANCELLED")
        self.assertTrue([o for o in r["outputs"] if o["name"] == "stem.wav.partial"][0]["partial"])
        self.assertFalse(r["quality_evaluated"])

    def test_injected_clock_timeout_is_a_timeout_not_a_success(self):
        doc = self._env()
        ticks = {"t": 0.0}

        def clock():
            ticks["t"] += 0.6
            return ticks["t"]
        r = amix.mix(jb(doc), limits=AudioLimits(chunk_samples=800, max_runtime_seconds=1.0), asset_root=self.sb.p("assets"), output_root=self.out, output_name="t1", clock=clock)
        record("mixer_timeout", status=r["status"], samples_written=r["mix"]["samples_written"])
        self.assertEqual(r["status"], "TIMEOUT")
        self.assertLess(r["mix"]["samples_written"], 4800)
        self.assertNotIn("stem.wav", os.listdir(self.sb.p("out", "t1")))
        self.assertEqual(self._disk_receipt("t1")["status"], "TIMEOUT")

    def test_keyboard_interrupt_inside_the_run_is_recorded_as_interrupted(self):
        doc = self._env()
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt
            return False
        r = amix.mix(jb(doc), limits=AudioLimits(chunk_samples=1600), asset_root=self.sb.p("assets"), output_root=self.out, output_name="k1", cancel=cancel)
        self.assertEqual(r["status"], "INTERRUPTED")
        self.assertEqual(self._disk_receipt("k1")["status"], "INTERRUPTED")
        self.assertIn("stem.wav.partial", os.listdir(self.sb.p("out", "k1")))

    def test_clipping_reject_stops_at_the_first_clipped_chunk_and_reports_the_exact_sample(self):
        def two_layers(doc, ev):
            doc["policy"]["overlap"] = "ALLOW_SUM"
            e2 = ev("v")
            doc["events"].append(e2)
            doc["scope"]["event_ids"].append("v")
        doc = self._env(dc=20000, doc_over=two_layers)
        r = amix.mix(jb(doc), limits=AudioLimits(chunk_samples=1600), asset_root=self.sb.p("assets"), output_root=self.out, output_name="x1")
        record("mixer_clip_reject", status=r["status"], mix=r["mix"])
        self.assertEqual(r["status"], "CLIPPING_REJECTED")
        self.assertEqual(r["mix"]["first_clipped_sample"], 0)
        self.assertEqual(r["mix"]["samples_written"], 0)
        self.assertEqual(r["mix"]["peak_before_clip"], 40000)
        self.assertEqual(r["mix"]["clipped_samples"], 1600)
        self.assertNotIn("stem.wav", os.listdir(self.sb.p("out", "x1")))

    def test_statuses_never_claim_quality(self):
        doc = self._env()
        r = amix.mix(jb(doc), asset_root=self.sb.p("assets"), output_root=self.out, output_name="q1")
        self.assertEqual(r["status"], "MIXED_VERIFIED")
        self.assertIs(r["quality_evaluated"], False)
        self.assertIn("No audio, speech or creative quality was evaluated", r["note"])


# --------------------------------------------------------------------------------------------------------------------------- avmux with fake tools
class AvEnv:
    def __init__(self, sb, *, samples=4800, frames=3, behavior_over=None, route="MOV_PCM_S16LE"):
        self.sb, self.samples, self.frames = sb, samples, frames
        self.frames_pcm = [((i * 37) % 2000 - 1000,) for i in range(samples)]
        self.wav = wav_from_frames(self.frames_pcm)
        self.pcm = pack_pcm(self.frames_pcm)
        self.vbytes = b"synthetic-not-a-video"
        sb.write("assets/a.wav", self.wav)
        sb.write("assets/v.mp4", self.vbytes)
        self.manifest = jb(avmux_manifest(route=route, video_bytes=self.vbytes, wav_bytes_=self.wav, samples=samples, frames=frames))
        self.mpath = sb.write("m.json", self.manifest)
        self.behavior = FT.mov_behavior(self.pcm, frames=frames, samples=samples)
        if behavior_over:
            self.behavior.update(behavior_over)
        self.tools = FT.install(sb.p("tools"), self.behavior)
        self.out = sb.mkdir("out")
        self.assets = sb.p("assets")

    def set(self, **over):
        self.behavior.update(over)
        FT.set_behavior(self.sb.p("tools"), self.behavior)

    def mux(self, name, limits=None):
        return vmux.mux(self.manifest, limits=limits or Limits(), asset_root=self.assets, output_root=self.out, output_name=name, ffmpeg=self.tools["ffmpeg"], ffprobe=self.tools["ffprobe"])

    def cli_args(self, name, extra=()):
        return ["mux", self.mpath, "--asset-root", self.assets, "--output-root", self.out, "--output-name", name, "--mux", "--ffmpeg", self.tools["ffmpeg"], "--ffprobe", self.tools["ffprobe"], *extra]

    def disk_receipt(self, name):
        with open(os.path.join(self.out, name, "receipt.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def listing(self, name):
        return sorted(os.listdir(os.path.join(self.out, name)))


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class AvmuxOrchestration(AuditCase):
    def _truthful(self, r):
        self.assertIs(r["quality_evaluated"], False)
        self.assertEqual(r["creative_acceptance"], "NOT_EVALUATED")
        if r.get("technical_verdict"):
            self.assertIs(r["technical_verdict"]["perceptual_sync_evaluated"], False)
            self.assertIs(r["technical_verdict"]["quality_evaluated"], False)
        self.assertNotEqual(r["status"], "IN_PROGRESS")

    def test_happy_path_with_a_faithful_double_and_the_in_progress_marker_during_the_run(self):
        env = AvEnv(self.sb)
        r = env.mux("ok")
        record("avmux_double_happy", status=r["status"], listing=env.listing("ok"))
        self.assertEqual(r["status"], "MUXED_VERIFIED_SAMPLE_EXACT")
        self._truthful(r)
        self.assertEqual(env.disk_receipt("ok"), json.loads(json.dumps(r)), "on-disk receipt equals the returned one")
        self.assertEqual([n for n in env.listing("ok") if not n.startswith("fake_")], ["ffprobe.output.json", "output.mov", "plan.canonical.json", "receipt.json"])
        seen = slurp(os.path.join(env.out, "ok", "fake_observed_receipt_status.txt"))
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertEqual(r["tmp_cleanup"], "removed")
        # the double read receipt.json from inside the 'ffmpeg' step: it must have been IN_PROGRESS (documented marker for UNKNOWN)
        self.assertTrue(seen.startswith("IN_PROGRESS"), seen)

    def test_failure_modes_have_truthful_distinct_statuses_and_keep_partials(self):
        rows = {}
        env = AvEnv(self.sb)
        cases = [("nonzero", {"mux_mode": "nonzero"}, "PROCESS_FAILED", None), ("noout", {"mux_mode": "noout"}, "UNKNOWN", None),
                 ("timeout", {"mux_mode": "sleep"}, "TIMEOUT", Limits(max_runtime_seconds=1.0)),
                 ("flood", {"mux_mode": "flood_stderr"}, "FLOOD_KILLED", Limits(max_drain_bytes=500_000))]
        for name, over, want, limits in cases:
            env.set(**over)
            t0 = time.monotonic()
            r = env.mux(name, limits)
            wall = time.monotonic() - t0
            rows[name] = {"status": r["status"], "wall": round(wall, 2), "listing": env.listing(name), "unknown_reason": r.get("unknown_reason")}
            self.assertEqual(r["status"], want, name)
            self._truthful(r)
            self.assertLess(wall, 9.5, name)
            self.assertNotIn("output.mov", env.listing(name), "a failed run must not leave something named like a deliverable")
            self.assertEqual(env.disk_receipt(name)["status"], want)
            self.assertFalse(r["technical_verdict"]["met"])
            if name != "noout":
                self.assertIn("output.mov.partial", env.listing(name), name)
        mux_exec = {n: [e for e in json.loads(json.dumps(env.disk_receipt(n)["executions"])) if e["phase"] == "mux"][0] for n in rows}
        self.assertEqual(mux_exec["nonzero"]["exit_code"], 7)
        self.assertTrue(mux_exec["timeout"]["timed_out"])
        self.assertEqual(mux_exec["timeout"]["kill_reason"], "timeout")
        self.assertEqual(mux_exec["flood"]["kill_reason"], "flood")
        record("avmux_failure_modes", rows=rows)
        pid = int(slurp(os.path.join(env.out, "timeout", "fake_mux_started")))
        self.assertFalse(pid_alive(pid), "the timed-out fake must be gone")

    def test_readback_mismatches_are_mismatches_and_the_file_is_quarantined(self):
        env = AvEnv(self.sb)
        flipped = bytearray(env.pcm)
        flipped[100] ^= 1
        env.set(decoded_audio_hex=bytes(flipped).hex())
        r = env.mux("audio")
        bad = [c["name"] for c in r["checks"] if not c["ok"]]
        self.assertEqual(r["status"], "READBACK_MISMATCH")
        self.assertEqual(bad, ["decoded_pcm_sha256_equals_wav_pcm"])
        self.assertIn("output.mov.mismatch", env.listing("audio"))
        self.assertNotIn("output.mov", env.listing("audio"))
        env2 = AvEnv(self.sb)
        env2.out = self.sb.mkdir("out2")
        out_md5 = env2.behavior["framemd5_out"].replace("00000000000000000000000000000009", "ffffffffffffffffffffffffffffffff")
        env2.set(framemd5_out=out_md5)
        r2 = env2.mux("video")
        self.assertEqual(r2["status"], "READBACK_MISMATCH")
        self.assertIn("decoded_video_frames_equal", [c["name"] for c in r2["checks"] if not c["ok"]])
        env3 = AvEnv(self.sb)
        env3.out = self.sb.mkdir("out3")
        env3.set(decoded_extra=1)  # one extra decoded sample frame: PCM route must be sample-exact
        r3 = env3.mux("extra")
        self.assertEqual(r3["status"], "READBACK_MISMATCH")
        self.assertIn("decoded_pcm_bytes_exact", [c["name"] for c in r3["checks"] if not c["ok"]])
        for r_ in (r, r2, r3):
            self._truthful(r_)
            self.assertFalse(r_["technical_verdict"]["met"])

    def test_diagnostics_truncation_in_the_receipt_is_exact_and_bounded(self):
        env = AvEnv(self.sb, behavior_over={"noise_bytes": 100_000})
        r = env.mux("noise", Limits(max_diag_bytes=1024))
        mux_exec = [e for e in env.disk_receipt("noise")["executions"] if e["phase"] == "mux"][0]
        se = mux_exec["stderr"]
        want = ("0123456789abcdef" * (100_000 // 16 + 1))[:100_000].encode()
        record("avmux_diag_truncation", stderr=se)
        self.assertEqual(r["status"], "MUXED_VERIFIED_SAMPLE_EXACT")
        self.assertEqual((se["total_bytes"], se["kept_bytes"], se["truncated"]), (100_000, 1024, True))
        self.assertEqual(se["sha256_full_stream"], hashlib.sha256(want).hexdigest())
        self.assertIn("bytes dropped", mux_exec["stderr"]["text"])
        self.assertLess(len(json.dumps(mux_exec)), 6000)


    def test_hash_comparison_ignores_timestamp_columns_characterisation(self):
        """Observation O-2 (a coverage gap, not a contract violation): the decoded-frame and copied-packet checks compare (size, hash) rows only;
        the stream/dts/pts/duration columns of framemd5/framehash are parsed away. A copy whose per-frame timing differs from the source but whose
        payload hashes are equal is therefore reported as sample-exact if ffprobe's summary fields (start 0, fps, frame count) still match."""
        env = AvEnv(self.sb)
        shifted = env.behavior["framemd5_out"].replace("0, 1, 1, 1,", "0, 3, 7, 1,").replace("0, 2, 2, 1,", "0, 9, 19, 1,")
        shifted_pk = env.behavior["framehash_out"].replace("0, 1, 1, 1,", "0, 3, 7, 1,").replace("0, 2, 2, 1,", "0, 9, 19, 1,")
        self.harness_check(shifted != env.behavior["framemd5_out"] and shifted_pk != env.behavior["framehash_out"], "timestamps must actually differ")
        env.set(framemd5_out=shifted, framehash_out=shifted_pk)
        r = env.mux("retimed")
        record("O-2/timing_columns_ignored", status=r["status"])
        self.assertEqual(r["status"], "MUXED_VERIFIED_SAMPLE_EXACT")
        self.assertEqual(ck.parse_hash_lines(shifted), ck.parse_hash_lines(env.behavior["framemd5_src"]))

    def test_aac_tolerance_boundary_is_exactly_one_aac_frame(self):
        for delta, ok in ((0, True), (320, True), (1024, True), (-1024, True), (1025, False), (-1025, False), (100000, False)):
            self.assertEqual(ck.tolerance_checks(4800 + delta, 4800, 1024)[0]["ok"], ok, delta)

    def test_a_clean_exit_with_a_surviving_pipe_holder_is_unknown(self):
        """An existing test covers the same contract with its own double; this one runs it end to end with one self-expiring holder."""
        env = AvEnv(self.sb)
        holder = self.sb.p("holder.pid")
        env.set(mux_mode="pipe_holder", holder_pid_file=holder)
        r = env.mux("holder", Limits(max_runtime_seconds=6.0))
        pid = int(slurp(holder))
        record("avmux_pipe_holder", status=r["status"], reason=r.get("unknown_reason"))
        self.assertEqual(r["status"], "UNKNOWN")
        self.assertIn("did not drain", r["unknown_reason"])
        time.sleep(0.3)
        self.assertFalse(pid_alive(pid))

    @known_defect("F-RCPT-1")
    def test_an_input_vanishing_during_the_mux_leaves_a_truthful_inventory(self):
        """Input deleted while ffmpeg runs: status UNKNOWN is right, but the receipt lists outputs=[] although output.mov.partial exists,
        and tmp/ is left behind without a tmp_cleanup note."""
        env = AvEnv(self.sb)
        env.set(mux_mode="delete_input", delete_input=os.path.join(env.assets, "a.wav"))
        r = env.mux("gone")
        on_disk = env.listing("gone")
        listed = sorted(o["name"] for o in env.disk_receipt("gone")["outputs"])
        record("F-RCPT-1/avmux", status=r["status"], on_disk=on_disk, listed=listed, internal_error=r.get("internal_error"), tmp_cleanup=r.get("tmp_cleanup"))
        self.harness_check(r["status"] == "UNKNOWN" and "internal_error" in r, "scenario precondition: input vanished => UNKNOWN with internal_error")
        expect = sorted(n for n in on_disk if n not in ("receipt.json", "tmp") and not n.startswith("fake_"))
        self.assertEqual(listed, expect)
        self.assertNotIn("tmp", on_disk, "the owned temporary directory is removed (or its survival is stated)")


# --------------------------------------------------------------------------------------------------------------------------- non-UTF-8 paths
@unittest.skipUnless(sys.platform.startswith("linux"), "raw non-UTF-8 file names are a Linux/POSIX property")
class NonUtf8Roots(AuditCase):
    @known_defect("F-ENC-1")
    def test_caller_chosen_root_with_a_non_utf8_byte_never_strands_an_in_progress_receipt(self):
        """A directory name containing byte 0xE9 is legal on Linux (Python decodes it with surrogateescape). Receipts embed argv paths and are
        written with a strict UTF-8 encoder. Correct behaviour: refuse the root before anything is created, OR still finish with a final receipt.
        Actual: video render promotes output.mp4 and then crashes writing the final receipt (on-disk receipt stays IN_PROGRESS); avmux creates
        the run directory and crashes writing the first receipt."""
        from video_core.render import render
        sb = self.sb
        raw_out = os.fsencode(sb.root) + b"/out_caf\xe9"
        raw_assets = os.fsencode(sb.root) + b"/assets_caf\xe9"
        try:
            os.mkdir(raw_out)
            os.mkdir(raw_assets)
        except OSError as exc:
            self.skipTest("filesystem refuses raw bytes: %r" % exc)
        out_root, assets = os.fsdecode(raw_out), os.fsdecode(raw_assets)
        outcomes = {}

        def classify(label, fn, run_dir):
            try:
                r = fn()
                exc = None
                status = r["status"]
            except errors.VideoCoreError as e:
                exc, status = "VideoCoreError:" + e.issues[0].code, None
            except BaseException as e:
                exc, status = type(e).__name__, None
            exists = os.path.isdir(run_dir)
            disk = None
            rp = os.path.join(run_dir, "receipt.json")
            if os.path.exists(rp):
                disk = json.loads(slurp(rp)).get("status")
            outcomes[label] = {"exception": exc, "returned_status": status, "run_dir_exists": exists, "on_disk_receipt_status": disk,
                               "promoted_output": os.path.exists(os.path.join(run_dir, "output.mp4")) or os.path.exists(os.path.join(run_dir, "output.mov"))}
        # video render (color-only episode: the asset root is irrelevant, the OUTPUT root carries the odd byte)
        vid = {"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 640, "height": 360, "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
               "nb_read_frames": "180", "duration": "6.000000"}
        vtools = FT.install(sb.p("vtools"), {"probe_out": {"streams": [vid], "format": {"format_name": "mov,mp4", "nb_streams": 1, "duration": "6.000000", "size": "20"}}, "mux_mode": "ok"})
        with open(os.path.join(TOOLS, "examples", "episode.synthetic.json"), "rb") as fh:
            ep = fh.read()
        from video_core.render import render
        classify("video_render", lambda: render(ep, asset_root=sb.mkdir("va"), output_root=out_root, output_name="v1", ffmpeg=vtools["ffmpeg"], ffprobe=vtools["ffprobe"]), os.path.join(out_root, "v1"))
        # avmux (the ASSET root carries the odd byte; it is embedded in the preflight probe argv of the very first receipt)
        env = AvEnv(sb)
        for n in ("a.wav", "v.mp4"):
            with open(sb.p("assets", n), "rb") as src, open(os.path.join(assets, n), "wb") as dst:
                dst.write(src.read())
        env.assets, env.out = assets, sb.mkdir("avout")
        classify("avmux_mux", lambda: env.mux("m1"), os.path.join(env.out, "m1"))
        record("F-ENC-1", outcomes=outcomes)
        self.harness_check(all(o["exception"] is not None or o["returned_status"] for o in outcomes.values()), "each scenario must end in an exception or a status")
        bad = {k: o for k, o in outcomes.items() if o["exception"] and not o["exception"].startswith("VideoCoreError") and o["run_dir_exists"] and o["on_disk_receipt_status"] in (None, "IN_PROGRESS")}
        self.assertEqual(bad, {})


# --------------------------------------------------------------------------------------------------------------------------- interrupt semantics
@unittest.skipUnless(POSIX, "signals / fake tools")
class InterruptSemantics(AuditCase):
    def _run_with_sigterm(self, argv, marker, tools_dir, module="video_core.avmux"):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen([PY, "-m", module, *argv], cwd=TOOLS, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
        t0 = time.monotonic()
        while not os.path.exists(marker) and time.monotonic() - t0 < 6 and proc.poll() is None:
            time.sleep(0.03)
        reached = os.path.exists(marker)
        at_signal = len(FT.calls(tools_dir))
        if reached:
            proc.send_signal(signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=9)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        return {"reached": reached, "rc": proc.returncode, "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace"),
                "calls_at_signal": at_signal, "calls_final": len(FT.calls(tools_dir))}

    def _stage(self, label, sleep_on):
        sb = self.sb.__class__(self.id() + "-" + label)
        self.addCleanup(sb.finish)
        env = AvEnv(sb)
        marker = sb.p("marker")
        env.set(sleep_on=dict(sleep_on, marker=marker))
        res = self._run_with_sigterm(env.cli_args("o", ["--probe-timeout-seconds", "20", "--inspect-timeout-seconds", "20"]), marker, sb.p("tools"))
        try:
            payload = json.loads(res["stdout"])
        except ValueError:
            payload = {}
        status = payload.get("status") or ("REJECTED:" + payload["error"]["issues"][0]["code"] if payload.get("error") else None)
        disk = None
        rp = os.path.join(env.out, "o", "receipt.json")
        if os.path.exists(rp):
            disk = json.loads(slurp(rp)).get("status")
        return {"stage": label, "reached": res["reached"], "rc": res["rc"], "reported": status, "disk_receipt_status": disk,
                "children_launched_after_interrupt": res["calls_final"] - res["calls_at_signal"]}

    @known_defect("F-INT-1")
    def test_sigterm_in_any_stage_ends_the_run_as_interrupted_without_launching_more_children(self):
        rows = [self._stage("tool_version_probe", {"tool": "ffmpeg", "match": "-version"}),
                self._stage("preflight_input_probe", {"tool": "ffprobe", "match": "v.mp4"}),
                self._stage("readback_decode", {"tool": "ffmpeg", "match": "framemd5"})]
        record("F-INT-1", rows=rows)
        self.harness_check(all(r["reached"] for r in rows), "every stage must have reached its sleep marker before SIGTERM was sent")
        problems = [r for r in rows if not (r["rc"] == 130 and r["children_launched_after_interrupt"] == 0)]
        self.assertEqual(problems, [])

    def test_sigterm_during_the_mux_step_itself_is_interrupted_and_partial_is_kept(self):
        sb = self.sb
        env = AvEnv(sb)
        env.set(mux_mode="sleep")
        marker = os.path.join(env.out, "m", "fake_mux_started")
        res = self._run_with_sigterm(env.cli_args("m", ["--max-runtime-seconds", "20"]), marker, sb.p("tools"))
        receipt = env.disk_receipt("m")
        record("avmux_sigterm_in_mux", rc=res["rc"], status=receipt["status"], listing=env.listing("m"))
        self.assertEqual(res["rc"], 130)
        self.assertEqual(receipt["status"], "INTERRUPTED")
        self.assertIn("output.mov.partial", env.listing("m"))
        self.assertFalse(pid_alive(int(slurp(marker))))

    def test_sigkill_of_the_orchestrator_leaves_in_progress_documented_limitation(self):
        """LIMITATIONS.md: killing the orchestrator with SIGKILL leaves the child running and the receipt IN_PROGRESS (= UNKNOWN).
        Confirmed. Only the recorded pids of this test are signalled."""
        sb = self.sb
        env = AvEnv(sb)
        env.set(mux_mode="sleep")
        marker = os.path.join(env.out, "k", "fake_mux_started")
        proc = subprocess.Popen([PY, "-m", "video_core.avmux", *env.cli_args("k", ["--max-runtime-seconds", "20"])], cwd=TOOLS, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), start_new_session=True)
        try:
            t0 = time.monotonic()
            while not os.path.exists(marker) and time.monotonic() - t0 < 6:
                time.sleep(0.03)
            self.assertTrue(os.path.exists(marker))
            fake_pid = int(slurp(marker))
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            status = env.disk_receipt("k")["status"]
            alive = pid_alive(fake_pid)
            record("sigkill_orchestrator", receipt_status=status, child_alive_after=alive)
            self.assertEqual(status, "IN_PROGRESS")
            self.assertTrue(alive, "documented: the child outlives a SIGKILLed orchestrator")
        finally:
            try:
                os.kill(fake_pid, signal.SIGKILL)
            except (OSError, UnboundLocalError):
                pass
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
            proc.communicate()


# --------------------------------------------------------------------------------------------------------------------------- plan purity
class PlanPurity(AuditCase):
    def test_plan_validate_and_dry_run_launch_nothing_and_create_nothing(self):
        sb = self.sb
        mono = wav_from_frames(pcm_tone(4800, 1, 5))
        vb = b"synthetic-not-a-video"
        sb.write("assets/a.wav", mono)
        sb.write("assets/v.mp4", vb)
        out = sb.mkdir("out")
        am = sb.write("am.json", jb(manifest(total=3, assets=[{"id": "a", "path": "a.wav", "sha256": sha256(mono)}],
                                            events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])))
        av = sb.write("av.json", jb(avmux_manifest(video_bytes=vb, wav_bytes_=mono, samples=4800)))
        ep = os.path.join(TOOLS, "examples", "episode.synthetic.json")
        before = tree_listing(sb.root)
        body = f'''
        import io, json, sys, contextlib
        from video_core import cli as vc
        from video_core.audiomix import cli as ac
        from video_core.avmux import cli as xc
        a, o = {sb.p("assets")!r}, {out!r}
        runs = [
          (vc.main, ["validate", {ep!r}]), (vc.main, ["plan", {ep!r}]), (vc.main, ["srt", {ep!r}]),
          (vc.main, ["plan", {ep!r}, "--output-root", o, "--output-name", "p1"]), (vc.main, ["render", {ep!r}, "--output-root", o, "--output-name", "p2"]),
          (ac.main, ["validate", {am!r}]), (ac.main, ["plan", {am!r}, "--asset-root", a]), (ac.main, ["mix", {am!r}, "--asset-root", a, "--output-root", o, "--output-name", "p3"]),
          (xc.main, ["validate", {av!r}]), (xc.main, ["plan", {av!r}, "--asset-root", a]), (xc.main, ["mux", {av!r}, "--asset-root", a, "--output-root", o, "--output-name", "p4"]),
        ]
        codes = []
        for fn, args in runs:
            buf = io.BytesIO()
            class W:  # capture sys.stdout.buffer writes
                buffer = buf
                def flush(self): pass
                def write(self, s): pass
            old = sys.stdout; sys.stdout = W()
            try:
                codes.append(fn(args))
            finally:
                sys.stdout = old
        print(json.dumps(codes))
        '''
        rc, out_s, err, events, to = audited(body, timeout=9)
        self.assertEqual((rc, to), (0, False), err[-500:])
        codes = json.loads(out_s)
        record("plan_purity", codes=codes, events=events)
        self.assertEqual(codes, [0] * 11)
        self.assertEqual(events, [], "plan/validate/dry-run must not launch a process or create/modify any file")
        self.assertEqual(tree_listing(sb.root), before)


if __name__ == "__main__":
    unittest.main()
