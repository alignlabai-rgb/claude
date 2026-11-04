"""Containment wired into render/mux/CLI, plus the static boundary of the native code. Orchestration uses FAKE ffmpeg scripts executed under the REAL worker
(so the fake genuinely runs with the kernel limits and reports them); the fakes are never render evidence."""

import ast
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.audio_fixtures import dumps
from tests.avmux_fakes import av_manifest, calls as av_calls, default_behavior as av_default, make_av_tools, mux_calls, seed_inputs
from tests.helpers import EXAMPLE_EPISODE, POSIX, ROOT, TmpCase, fake_calls, jl, make_fake_tools, sample_bytes, tree_snapshot
from video_core import receipt as rc
from video_core.avmux import mux as av_mux
from video_core.containment import ResourceEnvelope, contained
from video_core.errors import ValidationFailed
from video_core.render import render

MIB = 1024 * 1024
PKG = os.path.join(ROOT, "video_core", "containment")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class StaticBoundary(unittest.TestCase):
    """Native APIs live in exactly one file, only inside functions; nothing else in the package (or the rest of the repo) gains ctypes/subprocess/signals."""

    def trees(self):
        for name in sorted(os.listdir(PKG)):
            if name.endswith(".py"):
                yield name, ast.parse(read_text(os.path.join(PKG, name)), feature_version=(3, 11))

    @staticmethod
    def imports(node):
        if isinstance(node, ast.Import):
            return [a.name.split(".")[0] for a in node.names]
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            return [node.module.split(".")[0]]
        return []

    def test_ctypes_resource_and_subprocess_appear_only_inside_functions_of_the_worker(self):
        seen = {"_worker.py": set()}
        for name, tree in self.trees():
            for top in tree.body:  # module level
                for m in self.imports(top):
                    self.assertNotIn(m, {"ctypes", "resource", "subprocess", "signal", "multiprocessing"}, f"{name}: module-level import of {m}")
            for node in ast.walk(tree):
                for m in self.imports(node):
                    if m in {"ctypes", "resource", "subprocess"}:
                        self.assertEqual(name, "_worker.py", f"{name} imports {m}; native APIs belong only to the isolated worker")
                        seen["_worker.py"].add(m)
        self.assertEqual(seen["_worker.py"], {"ctypes", "resource", "subprocess"})

    def test_no_shell_no_name_based_or_broad_killing_no_process_enumeration(self):
        forbidden_calls = {("os", "system"), ("os", "kill"), ("os", "killpg"), ("os", "popen"), ("subprocess", "getoutput"), ("subprocess", "check_output")}
        for name, tree in self.trees():
            src = read_text(os.path.join(PKG, name))
            for bad in ("taskkill", "pkill", "killall", "psutil", "tasklist", "wmic", "OpenProcess", "DuplicateHandle", "SetPriorityClass", "CREATE_BREAKAWAY", "JOB_OBJECT_LIMIT_BREAKAWAY",
                        "SILENT_BREAKAWAY", "TerminateJobObject", "CreateJobObjectW(None, \"", "OpenJobObject"):
                self.assertNotIn(bad, src.replace("never ", "").replace("Never ", "") if bad in ("OpenProcess",) else src, f"{name} mentions {bad}")
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "shell":
                    self.fail(f"{name}: shell= must not appear")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    self.assertNotIn((node.func.value.id, node.func.attr), forbidden_calls, name)

    def test_the_worker_assigns_only_itself_and_never_opens_other_processes_or_jobs(self):
        tree = ast.parse(read_text(os.path.join(PKG, "_worker.py")))
        assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "AssignProcessToJobObject"]
        self.assertEqual(len(assigns), 1, "exactly one call site")
        job, proc = assigns[0].args
        self.assertEqual((job.id, proc.id), ("job", "me"))
        me = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "me" for t in n.targets)]
        self.assertEqual(len(me), 1)
        self.assertEqual(ast.unparse(me[0].value), "k32.GetCurrentProcess()", "the only process handle ever used is the pseudo-handle of the worker itself")
        called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for bad in ("OpenProcess", "OpenJobObject", "DuplicateHandle", "SetHandleInformation", "TerminateJobObject", "TerminateProcess"):
            self.assertNotIn(bad, called)
        creates = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "CreateJobObjectW"]
        self.assertEqual([[ast.unparse(a) for a in c.args] for c in creates], [["None", "None"]], "unnamed job with default (non-inheritable) security")

    def test_windows_job_flags_are_exactly_the_allowed_set_with_no_breakaway_ui_or_security_flag(self):
        """Flag set pinned. Four constants are confirmed by the coordinator-supplied Microsoft notes; PROCESS_TIME and PROCESS_MEMORY are NOT in those notes
        (author's recollection, to be checked by the reviewer) -- the test records that distinction instead of hiding it."""
        tree = ast.parse(read_text(os.path.join(PKG, "_worker.py")))
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith(("JOB_OBJECT_", "JobObject")):
                consts[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(set(consts), {"JOB_OBJECT_LIMIT_PROCESS_TIME", "JOB_OBJECT_LIMIT_JOB_TIME", "JOB_OBJECT_LIMIT_ACTIVE_PROCESS", "JOB_OBJECT_LIMIT_PROCESS_MEMORY",
                                       "JOB_OBJECT_LIMIT_JOB_MEMORY", "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", "JobObjectExtendedLimitInformation"})
        confirmed = {"JOB_OBJECT_LIMIT_JOB_TIME": 0x4, "JOB_OBJECT_LIMIT_ACTIVE_PROCESS": 0x8, "JOB_OBJECT_LIMIT_JOB_MEMORY": 0x200,
                     "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE": 0x2000, "JobObjectExtendedLimitInformation": 9}
        for name, value in confirmed.items():
            self.assertEqual(consts[name], value, name)
        unconfirmed = {"JOB_OBJECT_LIMIT_PROCESS_TIME": 0x2, "JOB_OBJECT_LIMIT_PROCESS_MEMORY": 0x100}  # not in the supplied notes: reviewer to verify
        for name, value in unconfirmed.items():
            self.assertEqual(consts[name], value, name)
        bits = [v for k, v in consts.items() if k.startswith("JOB_OBJECT_LIMIT_")]
        self.assertEqual(len(set(bits)), len(bits))
        self.assertTrue(all(b & (b - 1) == 0 for b in bits))
        used = 0
        for b in bits:
            used |= b
        for forbidden_bit in (0x800, 0x1000, 0x10, 0x20, 0x80):  # breakaway-ok, silent-breakaway-ok, affinity, priority class, scheduling class
            self.assertEqual(used & forbidden_bit, 0)
        self.assertFalse([k for k in consts if any(w in k for w in ("BREAKAWAY", "UI", "SECURITY", "DESKTOP", "HANDLES"))])

    def test_the_documentation_states_the_corrected_readback_guarantee_and_the_provenance_of_the_notes(self):
        doc = read_text(os.path.join(ROOT, "CONTAINMENT.md"))
        for needle in ("coordinator-supplied", "not proof that any Windows execution was tested", "does **not** detect a wrongly numbered flag constant", "user-mode time only",
                       "committed", "PROCESS_MEMORY", "3 GiB", "an FFmpeg run and are not presented as one", "jobapi2/nf-jobapi2-assignprocesstojobobject"):
            self.assertIn(needle, doc)
        self.assertNotIn("a wrong flag constant or layout makes the read-back disagree", doc, "the earlier overstated claim must be gone")

    def test_other_packages_still_obey_the_original_guards(self):
        # the pre-existing guard modules scan video_core/, audiomix/ and avmux/ for ctypes/subprocess/socket; the containment package is deliberately outside them
        for sub in ("", "audiomix", "avmux"):
            base = os.path.join(ROOT, "video_core", sub)
            for name in sorted(os.listdir(base)):
                if name.endswith(".py"):
                    tree = ast.parse(read_text(os.path.join(base, name)))
                    for node in ast.walk(tree):
                        for m in self.imports(node):
                            self.assertNotEqual(m, "ctypes", f"{sub}/{name}")
                            self.assertNotEqual(m, "resource", f"{sub}/{name}")


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class RenderWithEnvelope(TmpCase):
    def setUp(self):
        super().setUp()
        self.out, self.bin = self.mkdir("out"), self.mkdir("bin")
        self.ff, self.fp = make_fake_tools(self.bin, record_rlimits=True)

    def go(self, name="r1", **kw):
        return render(sample_bytes(), asset_root=None, output_root=self.out, output_name=name, ffmpeg=self.ff, ffprobe=self.fp, **kw)

    def test_the_main_ffmpeg_process_really_runs_under_the_requested_limits(self):
        env = ResourceEnvelope(cpu_seconds=60, memory_bytes=256 * MIB, max_file_bytes=64 * MIB)
        r = self.go(envelope=env)
        self.assertEqual(r["status"], rc.STATUS_MATCH)
        seen = jl(os.path.join(self.out, "r1", "fake_observed.json"))["rlimits"]
        self.assertEqual(seen, {"RLIMIT_CPU": [60, 60], "RLIMIT_AS": [256 * MIB, 256 * MIB], "RLIMIT_FSIZE": [64 * MIB, 64 * MIB]}, "reported by the process itself")
        c = r["containment"]
        self.assertEqual((c["requested"], c["applied"], c["refusal"], c["go_sent"]), (env.requested(), env.requested(), None, True))
        render_exec = [e for e in r["executions"] if e["phase"] == "render"][0]
        self.assertEqual(render_exec["containment"], c)
        self.assertEqual(render_exec["argv"][0], self.ff, "the receipt names the target command, not the worker")
        others = [e for e in r["executions"] if e["phase"] != "render"]
        self.assertTrue(others and all("containment" not in e for e in others), "probes and readback are NOT run inside the envelope")

    def test_without_an_envelope_nothing_changes(self):
        r = self.go()
        self.assertNotIn("containment", r)
        self.assertEqual(jl(os.path.join(self.out, "r1", "fake_observed.json"))["rlimits"], {"RLIMIT_CPU": [-1, -1], "RLIMIT_AS": [-1, -1], "RLIMIT_FSIZE": [-1, -1]})
        self.assertTrue(all("containment" not in e for e in r["executions"]))

    def test_the_canonical_plan_hash_is_identical_with_and_without_an_envelope(self):
        a = self.go("a")
        b = self.go("b", envelope=ResourceEnvelope(cpu_seconds=60))
        self.assertEqual(a["plan_sha256"], b["plan_sha256"])
        self.assertEqual(a["plan_sha256"], "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21")

    def test_a_limit_this_platform_cannot_apply_is_rejected_before_anything_is_created(self):
        before = tree_snapshot(self.out)
        with self.assertRaises(ValidationFailed) as cm:
            self.go(envelope=ResourceEnvelope(cpu_seconds=60, max_processes=4))
        self.assertEqual({i.code for i in cm.exception.issues}, {"envelope_unsupported"})
        self.assertIn("RLIMIT_NPROC", cm.exception.issues[0].message)
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual([c for c in fake_calls(self.bin) if c["argv"] and c["argv"][-1].endswith(".partial")], [], "the render was never launched")

    def test_a_runtime_refusal_leaves_a_receipt_and_never_starts_ffmpeg(self):
        stub = os.path.join(self.tmp, "stub.py")
        with open(stub, "w") as fh:
            fh.write("import sys, time\ntime.sleep(30)\n")
        with mock.patch.object(contained, "WORKER_PATH", stub):
            r = self.go(envelope=ResourceEnvelope(cpu_seconds=60, handshake_timeout_seconds=0.5))
        self.assertEqual((r["status"], r["stage"]), (rc.STATUS_CONTAINMENT_REFUSED, "render"))
        self.assertEqual(r["containment"]["refusal"]["reason"], "handshake_timeout")
        self.assertFalse(r["containment"]["target_may_have_started"])
        d = os.path.join(self.out, "r1")
        self.assertEqual([f for f in os.listdir(d) if f.startswith("output.mp4")], [], "no partial output: the target never ran")
        self.assertEqual([c for c in fake_calls(self.bin) if c["argv"] and c["argv"][-1].endswith(".partial")], [])
        self.assertEqual(len([e for e in r["executions"] if e["phase"] == "render"]), 1, "no retry")


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class MuxWithEnvelope(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out, self.bin = self.mkdir("assets"), self.mkdir("out"), self.mkdir("bin")
        seed_inputs(self.assets)
        self.ff, self.fp = make_av_tools(self.bin, **av_default("MOV_PCM_S16LE", record_rlimits=True))

    def go(self, name="r1", **kw):
        return av_mux.mux(dumps(av_manifest()), asset_root=self.assets, output_root=self.out, output_name=name, ffmpeg=self.ff, ffprobe=self.fp, **kw)

    def test_only_the_mux_process_runs_under_the_envelope(self):
        env = ResourceEnvelope(cpu_seconds=90, memory_bytes=256 * MIB)
        r = self.go(envelope=env)
        self.assertEqual(r["status"], av_mux.S_MOV_OK)
        seen = jl(os.path.join(self.out, "r1", "fake_observed.json"))["rlimits"]
        self.assertEqual((seen["RLIMIT_CPU"], seen["RLIMIT_AS"]), ([90, 90], [256 * MIB, 256 * MIB]))
        self.assertEqual(r["containment"]["applied"], env.requested())
        self.assertEqual([e["phase"] for e in r["executions"] if "containment" in e], ["mux"])

    def test_defaults_plan_hash_and_receipt_shape_are_unchanged_without_an_envelope(self):
        a, b = self.go("a"), self.go("b", envelope=ResourceEnvelope(cpu_seconds=90))
        self.assertNotIn("containment", a)
        self.assertEqual(a["plan_sha256"], b["plan_sha256"])
        self.assertEqual(a["status"], b["status"])

    def test_unsupported_limit_is_rejected_before_any_tool_runs(self):
        before = tree_snapshot(self.out)
        with self.assertRaises(ValidationFailed) as cm:
            self.go(envelope=ResourceEnvelope(tree_memory_bytes=128 * MIB))
        self.assertEqual(cm.exception.issues[0].code, "envelope_unsupported")
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(mux_calls(self.bin), [])

    def test_runtime_refusal_is_a_distinct_status_and_the_mux_never_starts(self):
        stub = os.path.join(self.tmp, "stub.py")
        with open(stub, "w") as fh:
            fh.write("import time\ntime.sleep(30)\n")
        with mock.patch.object(contained, "WORKER_PATH", stub):
            r = self.go(envelope=ResourceEnvelope(cpu_seconds=90, handshake_timeout_seconds=0.5))
        self.assertEqual(r["status"], rc.STATUS_CONTAINMENT_REFUSED)
        self.assertEqual(mux_calls(self.bin), [])
        self.assertFalse(r["technical_verdict"]["met"])


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class CliFlags(TmpCase):
    def setUp(self):
        super().setUp()
        self.out, self.bin, self.assets = self.mkdir("out"), self.mkdir("bin"), self.mkdir("assets")
        self.ff, self.fp = make_fake_tools(self.bin, record_rlimits=True)

    def cli(self, *a, module="video_core"):
        return subprocess.run([sys.executable, "-m", module, *a], capture_output=True, cwd=self.tmp, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}, timeout=120)

    def render_args(self, *extra, name="r1"):
        return ["render", EXAMPLE_EPISODE, "--output-root", self.out, "--output-name", name, "--ffmpeg", self.ff, "--ffprobe", self.fp, *extra]

    def test_envelope_flags_apply_only_with_an_explicit_render(self):
        r = self.cli(*self.render_args("--render", "--envelope-cpu-seconds", "60", "--envelope-memory-bytes", str(256 * MIB)))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["containment"]["applied"], {"cpu_seconds": 60, "memory_bytes": 256 * MIB})
        self.assertEqual(jl(os.path.join(self.out, "r1", "fake_observed.json"))["rlimits"]["RLIMIT_AS"], [256 * MIB, 256 * MIB])

    def test_dry_run_reports_support_and_applies_nothing(self):
        before = tree_snapshot(self.tmp)
        r = self.cli("render", EXAMPLE_EPISODE, "--output-root", self.out, "--output-name", "x", "--envelope-cpu-seconds", "60", "--envelope-max-processes", "4")
        out = json.loads(r.stdout)
        self.assertEqual((r.returncode, out["mode"]), (0, "dry_run"))
        self.assertEqual(out["envelope"]["requested"], {"cpu_seconds": 60, "max_processes": 4})
        self.assertEqual([u["limit"] for u in out["envelope"]["unsupported_here"]], ["max_processes"])
        self.assertIn("starts no process", out["envelope"]["applied"])
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_unsupported_flag_exits_1_with_nothing_created(self):
        before = tree_snapshot(self.out)
        r = self.cli(*self.render_args("--render", "--envelope-tree-cpu-seconds", "10"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("envelope_unsupported", r.stdout.decode())
        self.assertEqual((tree_snapshot(self.out), fake_calls(self.bin)), (before, []))

    def test_bad_envelope_values_are_usage_errors_before_anything_runs(self):
        before = tree_snapshot(self.out)
        for flag, bad in (("cpu-seconds", "nan"), ("cpu-seconds", "5.5"), ("cpu-seconds", "0"), ("memory-bytes", "inf"), ("memory-bytes", "1000"), ("memory-bytes", "-5"), ("max-file-bytes", "1e3"),
                          ("handshake-timeout-seconds", "nan")):
            r = self.cli(*self.render_args("--render", f"--envelope-{flag}={bad}", *(["--envelope-cpu-seconds", "5"] if flag == "handshake-timeout-seconds" else [])))
            self.assertEqual(r.returncode, 2, (flag, bad, r.stderr))
        r = self.cli(*self.render_args("--render", "--envelope-handshake-timeout-seconds", "3"))
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"without any envelope limit", r.stderr)
        self.assertEqual((tree_snapshot(self.out), fake_calls(self.bin)), (before, []))

    def test_no_flags_means_no_envelope(self):
        r = self.cli(*self.render_args("--render"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("containment", json.loads(r.stdout))

    def test_avmux_cli_has_the_same_opt_in_flags(self):
        seed_inputs(self.assets)
        m = os.path.join(self.tmp, "m.json")
        with open(m, "wb") as fh:
            fh.write(dumps(av_manifest()))
        ff, fp = make_av_tools(self.mkdir("bin2"), **av_default("MOV_PCM_S16LE", record_rlimits=True))
        def args(name, *extra):
            return ["mux", m, "--asset-root", self.assets, "--output-root", self.out, "--output-name", name, "--ffmpeg", ff, "--ffprobe", fp, "--mux", *extra]
        r = self.cli(*args("m1", "--envelope-cpu-seconds", "90"), module="video_core.avmux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["containment"]["applied"], {"cpu_seconds": 90})
        before = tree_snapshot(self.out)
        r = self.cli(*args("m2", "--envelope-max-processes", "3"), module="video_core.avmux")
        self.assertEqual(r.returncode, 1)
        self.assertIn("envelope_unsupported", r.stdout.decode())
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(self.cli(*args("m3", "--envelope-cpu-seconds=nan"), module="video_core.avmux").returncode, 2)


if __name__ == "__main__":
    unittest.main()
