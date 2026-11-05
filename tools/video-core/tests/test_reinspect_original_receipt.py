"""`avmux reinspect --original-receipt`: strict, typed, side-effect-free handling of the optional original receipt.

Contract (see INPUT_CONTRACT.md "Follow-up 2" and the module docstring of video_core/avmux/original_receipt.py): a bad original receipt is a stable structured rejection
(library: ValidationFailed/UnsafePath with an `original_receipt*` code; CLI: exit 1 and {"ok": false, "error": ...} on stdout) raised BEFORE any ffprobe/ffmpeg start and
before any output exists. Valid producer receipts stay accepted; the original status is kept verbatim and never promoted; a caller-supplied hash is byte integrity, not
authenticity. All inputs are tiny synthetic files; no real media, no real ffmpeg/ffprobe.

PORTABILITY (this module must be meaningful on Windows as well as POSIX):
* Rejection cases never need a tool: they call the real library function / the real public `main(argv)` under a no-Popen guard (`tests.helpers.no_process`), or a real interpreter
  child whose own subprocess.Popen is replaced by a raiser. They do not depend on POSIX executables, and they are not skipped on any platform.
* Completion cases (REINSPECTED, a receipt written by the real muxer) need a tool child. They use the test-only adapter in tests/portable_tools.py, which runs the repo's fake
  ffprobe/ffmpeg scripts as owned Python children through the REAL runner.run_bounded. That is evidence about orchestration and parsing only - NOT real ffprobe, NOT Windows
  executable-path handling, NOT signals. A tool problem there FAILS the test (status must be REINSPECTED); it is never accepted as a skip, and the adapter canary proves a child ran.
* The exact-byte input-limit contract is asserted at parser level (no tool needed); the reinspect-level over-limit rejection needs no tool either.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.audio_fixtures import dumps, sha, write
from tests.avmux_fakes import av_manifest, calls, default_behavior, make_av_tools, out_probe, packets_custom, packets_json, seed_inputs
from tests.helpers import POSIX, ROOT, TmpCase, no_process, rb, tree_snapshot
from tests.portable_tools import owned_python_tools
from video_core.avmux import cli as avcli
from video_core.avmux.mux import OK_STATUSES, mux
from video_core.avmux.original_receipt import AUTHENTICATION, MAX_SUMMARY_DEPTH, MAX_SUMMARY_JSON_BYTES, read_original_receipt
from video_core.avmux.reinspect import reinspect_audio_packets
from video_core import runner
from video_core.errors import UnsafePath, VideoCoreError
from video_core.limits import Limits

MEDIA = b"ORIGINAL-AAC-OUTPUT-BYTES"
MEDIA_SHA = hashlib.sha256(MEDIA).hexdigest()
ORIGINAL_SUMMARY = {"packet_count": 6, "skip_samples": 1024, "discard_padding": None, "skip_samples_side_data_seen": True}


def good(**over):
    d = {"schema": "video_core.avmux_receipt/1", "status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "route": "MP4_AAC", "written": True, "stage": "complete", "quality_evaluated": False,
         "plan_sha256": "ab" * 32,
         "outputs": [{"role": "media", "name": "output.mp4", "sha256": MEDIA_SHA, "bytes": len(MEDIA), "partial": False},
                     {"role": "plan", "name": "plan.canonical.json", "sha256": "ab" * 32, "bytes": 10, "partial": False}],
         "observations": {"audio_packets": copy.deepcopy(ORIGINAL_SUMMARY)}}
    d.update(over)
    return d


def raw(d) -> bytes:
    return json.dumps(d).encode()


def nested(n):
    d = "x"
    for _ in range(n):
        d = {"a": d}
    return d


def codes_of(fn):
    try:
        fn()
    except VideoCoreError as exc:
        return [i.code for i in exc.issues]
    return None



class World(TmpCase):
    """Portable fixture: a media file, an output root and (as plain FILES, never executed here) the fake tool scripts. `attempt(bytes)` runs the real library function,
    `cli(bytes)` the real public CLI `main(argv)`."""

    def setUp(self):
        super().setUp()
        self.bin, self.out, self.media_dir = self.mkdir("bin"), self.mkdir("out"), self.mkdir("media")
        self.media = write(os.path.join(self.media_dir, "output.mp4"), MEDIA)
        self.ff, self.fp = make_av_tools(self.bin, **default_behavior("MP4_AAC", packets=packets_json(skip=1024, padding=None), probe_out=out_probe("MP4_AAC")))
        self.rpath = os.path.join(self.media_dir, "original.receipt.json")

    def put(self, data: bytes) -> str:
        write(self.rpath, data)
        return self.rpath

    def attempt(self, data: bytes, name="fix1", limits=Limits()):
        self.put(data)
        return reinspect_audio_packets(self.media, expected_sha256=MEDIA_SHA, output_root=self.out, output_name=name, ffprobe=self.fp, original_receipt_path=self.rpath, limits=limits)

    def cli(self, data: bytes, name="fix1", *extra):
        self.put(data)
        buf = io.BytesIO()
        argv = ["reinspect", self.media, "--expect-sha256", MEDIA_SHA, "--original-receipt", self.rpath, "--output-root", self.out, "--output-name", name,
                "--ffprobe", self.fp, "--reinspect", *extra]
        with mock.patch.object(sys, "stdout", mock.Mock(buffer=buf)):
            rc = avcli.main(argv)
        return rc, json.loads(buf.getvalue())


class ToolWorld(World):
    """World plus the owned-Python-child adapter (tests/portable_tools.py) for completion cases. `self.seen` lists the mapped tool invocations."""

    def setUp(self):
        super().setUp()
        self.seen = self.enterContext(owned_python_tools(self.ff, self.fp))

REJECTIONS = {
    # not strict JSON
    "empty": (b"", "original_receipt_unreadable"), "BOM": (b"\xef\xbb\xbf" + raw(good()), "original_receipt_unreadable"), "invalid UTF-8": (b"\xff\xfe{}", "original_receipt_unreadable"),
    "truncated": (raw(good())[:-4], "original_receipt_unreadable"), "not JSON": (b"not json", "original_receipt_unreadable"),
    "NaN": (raw(good()).replace(b'"packet_count": 6', b'"packet_count": NaN'), "original_receipt_unreadable"),
    "Infinity": (raw(good()).replace(b'"packet_count": 6', b'"packet_count": Infinity'), "original_receipt_unreadable"),
    "-Infinity": (raw(good()).replace(b'"packet_count": 6', b'"packet_count": -Infinity'), "original_receipt_unreadable"),
    "duplicate top-level key": (raw(good()).replace(b'"route": "MP4_AAC"', b'"route": "MOV_PCM_S16LE", "route": "MP4_AAC"'), "original_receipt_unreadable"),
    "duplicate key in an output": (raw(good()).replace(b'"role": "media"', b'"role": "plan", "role": "media"'), "original_receipt_unreadable"),
    "duplicate status (last wins would be MUXED)": (raw(good()).replace(b'"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE"', b'"status": "HOLD", "status": "MUXED_TECHNICAL_WITHIN_TOLERANCE"'),
                                                    "original_receipt_unreadable"),
    "100000 nested arrays": (b"[" * 100000 + b"]" * 100000, "original_receipt_unreadable"), "50000 nested objects": (b'{"a":' * 50000 + b"1" + b"}" * 50000, "original_receipt_unreadable"),
    "5000-digit integer": (raw(good()).replace(b'"packet_count": 6', b'"packet_count": ' + b"9" * 5000), "original_receipt_unreadable"),
    # wrong JSON types
    "[]": (b"[]", "original_receipt_shape"), "null": (b"null", "original_receipt_shape"), '"x"': (b'"x"', "original_receipt_shape"), "1": (b"1", "original_receipt_shape"),
    "true": (b"true", "original_receipt_shape"), "{}": (b"{}", "original_receipt_shape"),
    "outputs str": (raw(good(outputs="x")), "original_receipt_shape"), "outputs [1]": (raw(good(outputs=[1])), "original_receipt_shape"), "outputs null": (raw(good(outputs=None)), "original_receipt_shape"),
    "outputs dict": (raw(good(outputs={"role": "media"})), "original_receipt_shape"),
    "observations str": (raw(good(observations="x")), "original_receipt_shape"), "observations list": (raw(good(observations=[1])), "original_receipt_shape"),
    "audio_packets str": (raw(good(observations={"audio_packets": "x"})), "original_receipt_shape"), "audio_packets list": (raw(good(observations={"audio_packets": [1]})), "original_receipt_shape"),
    "audio_packets number": (raw(good(observations={"audio_packets": 5})), "original_receipt_shape"),
    "status missing": (raw({k: v for k, v in good().items() if k != "status"}), "original_receipt_shape"), "status list": (raw(good(status=["MUXED"])), "original_receipt_shape"),
    "status number": (raw(good(status=5)), "original_receipt_shape"), "status null": (raw(good(status=None)), "original_receipt_shape"),
    "status lower-case": (raw(good(status="muxed")), "original_receipt_shape"), "status 65 chars": (raw(good(status="A" * 65)), "original_receipt_shape"),
    "status with control char": (raw(good(status="MUXED\nOK")), "original_receipt_shape"),
    "route number": (raw(good(route=5)), "original_receipt_shape"), "route unknown": (raw(good(route="MKV_FLAC")), "original_receipt_shape"),
    "plan_sha256 number": (raw(good(plan_sha256=5)), "original_receipt_shape"), "plan_sha256 short": (raw(good(plan_sha256="ab" * 31)), "original_receipt_shape"),
    "stage number": (raw(good(stage=5)), "original_receipt_shape"),
    "summary 17 levels deep": (raw(good(observations={"audio_packets": nested(MAX_SUMMARY_DEPTH + 1)})), "original_receipt_shape"),
    "summary larger than the bound": (raw(good(observations={"audio_packets": {"blob": "x" * (MAX_SUMMARY_JSON_BYTES + 1)}})), "original_receipt_shape"),
    # not an avmux receipt / contradicts itself
    "video render receipt schema": (raw(good(schema="video_core.receipt/1")), "original_receipt_schema"), "audio receipt schema": (raw(good(schema="video_core.audio_receipt/1")), "original_receipt_schema"),
    "written false": (raw(good(written=False)), "original_receipt_inconsistent"), "written 1": (raw(good(written=1)), "original_receipt_inconsistent"),
    "quality_evaluated true": (raw(good(quality_evaluated=True)), "original_receipt_inconsistent"), "stage mux": (raw(good(stage="mux")), "original_receipt_inconsistent"),
    "plan_sha256 differs from the plan output": (raw(good(plan_sha256="cd" * 32)), "original_receipt_inconsistent"),
    # does not describe THIS media
    "no media output": (raw(good(outputs=[])), "original_receipt_media_mismatch"),
    "media sha of another file": (raw(good(outputs=[{"role": "media", "sha256": "0" * 64}])), "original_receipt_media_mismatch"),
    "two media outputs": (raw(good(outputs=[{"role": "media", "sha256": MEDIA_SHA}, {"role": "media", "sha256": MEDIA_SHA}])), "original_receipt_media_mismatch"),
    "only a media_partial": (raw(good(outputs=[{"role": "media_partial", "sha256": MEDIA_SHA, "partial": True}])), "original_receipt_media_mismatch"),
}


class LibraryRejections(World):
    def test_every_bad_receipt_is_a_typed_rejection_before_any_process_or_output(self):
        for label, (data, code) in REJECTIONS.items():
            with self.subTest(label):
                self.put(data)
                before = tree_snapshot(self.tmp)
                with no_process():  # a Popen anywhere (the tool probe included) fails the test; no tool or POSIX executable is needed
                    got = codes_of(lambda: self.attempt(data))
                self.assertEqual(got, [code])
                self.assertEqual(tree_snapshot(self.tmp), before, "nothing created, nothing modified")

    def test_over_the_input_limit_is_refused_by_reinspect_before_any_tool_is_needed(self):
        data = raw(good(note="y" * 3000))
        n = len(data)
        self.put(data)
        before = tree_snapshot(self.tmp)
        with no_process():
            self.assertEqual(codes_of(lambda: self.attempt(data, limits=Limits(max_input_bytes=n - 1))), ["limit_input_bytes"])
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_reinspect_compares_a_present_media_bytes_claim_with_the_real_file_before_any_tool(self):
        for label, claim, code in (("another size", len(MEDIA) + 1, "original_receipt_media_mismatch"), ("float of the right size", float(len(MEDIA)), "original_receipt_shape"),
                                   ("negative", -1, "original_receipt_shape")):
            with self.subTest(label), no_process():
                data = raw(good(outputs=[{"role": "media", "name": "output.mp4", "sha256": MEDIA_SHA, "bytes": claim, "partial": False}]))
                self.assertEqual(codes_of(lambda: self.attempt(data)), [code])
        self.assertEqual(os.listdir(self.out), [])

    def test_not_a_regular_file_symlink_and_unreadable_are_typed_unsafe_path_errors(self):
        with no_process():
            for label, path in (("directory", self.mkdir("adir")), ("missing", os.path.join(self.tmp, "nope.json"))):
                with self.subTest(label), self.assertRaises(UnsafePath) as cm:
                    reinspect_audio_packets(self.media, expected_sha256=MEDIA_SHA, output_root=self.out, output_name="x", ffprobe=self.fp, original_receipt_path=path)
                self.assertEqual([i.code for i in cm.exception.issues], ["original_receipt_unreadable"])
            self.put(raw(good()))
            if POSIX:
                link = os.path.join(self.media_dir, "link.json")
                os.symlink(self.rpath, link)
                with self.assertRaises(UnsafePath) as cm:
                    reinspect_audio_packets(self.media, expected_sha256=MEDIA_SHA, output_root=self.out, output_name="x", ffprobe=self.fp, original_receipt_path=link)
                self.assertEqual([i.code for i in cm.exception.issues], ["original_receipt_unreadable"])
            real_open = open
            with mock.patch("builtins.open", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied")) if str(p) == self.rpath else real_open(p, *a, **k)):
                with self.assertRaises(UnsafePath) as cm:
                    reinspect_audio_packets(self.media, expected_sha256=MEDIA_SHA, output_root=self.out, output_name="x", ffprobe=self.fp, original_receipt_path=self.rpath)
            self.assertEqual([i.code for i in cm.exception.issues], ["original_receipt_unreadable"])
        self.assertEqual(os.listdir(self.out), [])


class ParserBoundary(unittest.TestCase):
    """read_original_receipt on its own: no file, no tool, nothing platform dependent."""

    def test_exact_byte_limit_is_accepted_and_one_byte_over_is_limit_input_bytes(self):
        data = raw(good(note="y" * 3000))
        n = len(data)
        self.assertEqual(read_original_receipt(data, expected_sha256=MEDIA_SHA, limits=Limits(max_input_bytes=n))["status"], "MUXED_TECHNICAL_WITHIN_TOLERANCE")
        self.assertEqual(codes_of(lambda: read_original_receipt(data, expected_sha256=MEDIA_SHA, limits=Limits(max_input_bytes=n - 1))), ["limit_input_bytes"])

    def test_committed_producer_receipts_are_accepted(self):
        for route in ("mov", "mp4"):
            data = rb(os.path.join(ROOT, "evidence", "avmux-synthetic", f"{route}.receipt.json"))
            media = next(o for o in json.loads(data)["outputs"] if o["role"] == "media")
            facts = read_original_receipt(data, expected_sha256=media["sha256"], limits=Limits(), media_bytes=media["bytes"])
            self.assertIn(facts["status"], OK_STATUSES)
            self.assertEqual(facts["schema"], "video_core.avmux_receipt/1")

    def test_the_worst_case_producer_summary_is_inside_the_bound(self):
        """packet_summary caps side_data_records at 1000; that largest producer-shaped summary must still be accepted (the bound may not exclude real receipts)."""
        from video_core.avmux import checks as ck
        pk = packets_custom({i: [{"side_data_type": "Skip Samples", "skip_samples": 1024 + i, "discard_padding": 3, "extra": "x"}] for i in range(1500)}, count=1500)
        summary = ck.packet_summary(pk)
        self.assertEqual(len(summary["side_data_records"]), ck.MAX_SIDE_DATA_RECORDS)
        self.assertLess(len(json.dumps(summary)), MAX_SUMMARY_JSON_BYTES)
        self.assertEqual(read_original_receipt(raw(good(observations={"audio_packets": summary})), expected_sha256=MEDIA_SHA, limits=Limits())["claimed_audio_packets"], summary)
        self.assertEqual(read_original_receipt(raw(good(observations={"audio_packets": nested(MAX_SUMMARY_DEPTH)})), expected_sha256=MEDIA_SHA, limits=Limits())["status"],
                         "MUXED_TECHNICAL_WITHIN_TOLERANCE")

    def test_the_packet_summary_is_labelled_a_claim_and_the_hash_is_not_authenticity(self):
        facts = read_original_receipt(raw(good()), expected_sha256=MEDIA_SHA, limits=Limits())
        self.assertTrue(facts["claimed_audio_packets_basis"].startswith("CLAIMED_UNVERIFIED"))
        self.assertIn("nothing here confirms", facts["claimed_audio_packets_basis"])
        self.assertEqual(facts["authentication"], AUTHENTICATION)
        self.assertIn("not who produced or approved", AUTHENTICATION)

    def test_a_self_consistent_forged_receipt_for_the_same_media_passes_by_design(self):
        """Documented limit, asserted so nobody reads acceptance as trust: anyone who has the media can write a matching receipt."""
        forged = good(status="MUXED_VERIFIED_SAMPLE_EXACT", route="MOV_PCM_S16LE", plan_sha256="12" * 32,
                      outputs=[{"role": "media", "name": "forged.mov", "sha256": MEDIA_SHA, "bytes": len(MEDIA), "partial": False},
                               {"role": "plan", "name": "plan.canonical.json", "sha256": "12" * 32, "bytes": 1, "partial": False}],
                      observations={"audio_packets": {"claim": "whatever the forger likes"}})
        facts = read_original_receipt(raw(forged), expected_sha256=MEDIA_SHA, limits=Limits(), media_bytes=len(MEDIA))
        self.assertEqual(facts["claimed_audio_packets"], {"claim": "whatever the forger likes"})

    def test_the_media_and_plan_output_claims_are_consistency_checked_when_present_and_optional_when_absent(self):
        """partial / bytes / name are the producer's per-file claims. Present-and-wrong contradicts the receipt itself (or the media on disk) and is rejected;
        absent is allowed (hand-written legacy receipts). Deliberately NOT 'required' and NOT 'ignored'."""
        def with_media(**claims):
            media = {"role": "media", "name": "output.mp4", "sha256": MEDIA_SHA, "bytes": len(MEDIA), "partial": False}
            media.update(claims)
            return raw(good(outputs=[media, {"role": "plan", "name": "plan.canonical.json", "sha256": "ab" * 32, "bytes": 10, "partial": False}]))

        def with_plan(**claims):
            plan = {"role": "plan", "name": "plan.canonical.json", "sha256": "ab" * 32, "bytes": 10, "partial": False}
            plan.update(claims)
            return raw(good(outputs=[{"role": "media", "sha256": MEDIA_SHA}, plan]))

        rejected = {
            "media partial true": (with_media(partial=True), "original_receipt_inconsistent"),
            "media partial 0": (with_media(partial=0), "original_receipt_inconsistent"),
            "media partial null": (with_media(partial=None), "original_receipt_inconsistent"),
            "media bytes float of the right size": (with_media(bytes=float(len(MEDIA))), "original_receipt_shape"),
            "media bytes -1": (with_media(bytes=-1), "original_receipt_shape"),
            "media bytes true": (with_media(bytes=True), "original_receipt_shape"),
            "media bytes string": (with_media(bytes=str(len(MEDIA))), "original_receipt_shape"),
            "media bytes another size": (with_media(bytes=len(MEDIA) + 1), "original_receipt_media_mismatch"),
            "media name number": (with_media(name=5), "original_receipt_shape"),
            "plan partial true": (with_plan(partial=True), "original_receipt_inconsistent"),
            "plan bytes float": (with_plan(bytes=10.0), "original_receipt_shape"),
            "plan bytes -1": (with_plan(bytes=-1), "original_receipt_shape"),
        }
        for label, (data, code) in rejected.items():
            with self.subTest(label):
                self.assertEqual(codes_of(lambda: read_original_receipt(data, expected_sha256=MEDIA_SHA, limits=Limits(), media_bytes=len(MEDIA))), [code])
        for label, data in (("exact claims", with_media()), ("no partial, bytes or name", raw(good(outputs=[{"role": "media", "sha256": MEDIA_SHA}]))),
                            ("bytes 0 on the plan output", with_plan(bytes=0))):
            with self.subTest(label):
                self.assertEqual(read_original_receipt(data, expected_sha256=MEDIA_SHA, limits=Limits(), media_bytes=len(MEDIA))["status"], "MUXED_TECHNICAL_WITHIN_TOLERANCE")
        # without the media size (parser used standalone) the size comparison is skipped but the type/sign checks still run
        self.assertEqual(read_original_receipt(with_media(bytes=len(MEDIA) + 1), expected_sha256=MEDIA_SHA, limits=Limits())["status"], "MUXED_TECHNICAL_WITHIN_TOLERANCE")
        self.assertEqual(codes_of(lambda: read_original_receipt(with_media(bytes=-1), expected_sha256=MEDIA_SHA, limits=Limits())), ["original_receipt_shape"])


class PublicCliRejections(World):
    def test_the_public_main_returns_exit_1_and_a_structured_error_with_no_popen_and_no_output(self):
        for label, (data, code) in REJECTIONS.items():
            with self.subTest(label):
                self.put(data)
                before = tree_snapshot(self.tmp)
                with no_process():
                    rc, doc = self.cli(data)
                self.assertEqual(rc, 1)
                self.assertIs(doc["ok"], False)
                self.assertEqual([i["code"] for i in doc["error"]["issues"]], [code])
                self.assertEqual(doc["error"]["issues"][0]["path"], "original_receipt")
                self.assertEqual(tree_snapshot(self.tmp), before)

    def test_a_real_interpreter_child_prints_no_traceback_and_launches_nothing(self):
        """Real interpreter, real `avmux.cli.main`; the CHILD replaces its own subprocess.Popen / os.system by a raiser, so any launch is a traceback. No POSIX executable involved."""
        child = ("import os, subprocess, sys\n"
                 "def boom(*a, **k): raise AssertionError('a subprocess was launched')\n"
                 "subprocess.Popen = boom; os.system = boom\n"
                 "from video_core.avmux import cli\n"
                 "sys.exit(cli.main(sys.argv[1:]))\n")
        env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1"}
        for label in ("[]", "outputs str", "audio_packets str", "duplicate top-level key", "NaN", "100000 nested arrays", "status list", "media sha of another file"):
            data, code = REJECTIONS[label]
            with self.subTest(label):
                self.put(data)
                p = subprocess.run([sys.executable, "-c", child, "reinspect", self.media, "--expect-sha256", MEDIA_SHA, "--original-receipt", self.rpath, "--output-root", self.out,
                                    "--output-name", "fix1", "--ffprobe", self.fp, "--reinspect"], capture_output=True, timeout=10, env=env)
                self.assertEqual(p.returncode, 1, p.stderr[-300:])
                self.assertNotIn(b"Traceback", p.stderr)
                self.assertEqual([i["code"] for i in json.loads(p.stdout)["error"]["issues"]], [code])
                self.assertEqual(os.listdir(self.out), [])


class AdapterCanary(ToolWorld):
    """Proves the completion tests below are not vacuous: the adapter really starts an owned Python child through the real run_bounded."""

    def test_the_adapter_runs_a_real_child_through_the_real_run_bounded(self):
        res = runner.run_bounded([self.fp, "-version"], phase="canary", timeout_s=10, stdout_limits=(65536, 0), stderr_limits=(1024, 1024))
        self.assertEqual((res.outcome, res.exit_code), (runner.EXITED_ZERO, 0))
        self.assertIn(b"ffprobe version", res.stdout_bytes())
        self.assertEqual([m["tool_argv"] for m in self.seen], [["-version"]])
        self.assertEqual([c["tool"] for c in calls(self.bin)], ["ffprobe"], "the fake script itself logged that it was executed")

    def test_rejections_never_start_even_the_owned_fake_tool(self):
        for label, (data, code) in REJECTIONS.items():
            with self.subTest(label):
                self.assertEqual(codes_of(lambda: self.attempt(data)), [code])
        self.assertEqual(self.seen, [])
        self.assertEqual(calls(self.bin), [])
        self.assertEqual(os.listdir(self.out), [])


class ValidReceiptsStayAccepted(ToolWorld):
    """Completion cases: each must end REINSPECTED. SKIPPED_TOOL_UNAVAILABLE here would be a failure of the fixture, not a pass."""

    def complete(self, data: bytes, name="fix1", limits=Limits()):
        r = self.attempt(data, name=name, limits=limits)
        self.assertEqual(r["status"], "REINSPECTED", "a tool skip is not coverage: " + json.dumps(r.get("reasons")))
        self.assertTrue(self.seen, "the owned tool child really ran")
        return r

    def test_the_exact_byte_input_limit_completes_and_the_next_byte_down_does_not(self):
        data = raw(good(note="y" * 3000))
        n = len(data)
        self.assertEqual(self.complete(data, "at-limit", Limits(max_input_bytes=n))["original_receipt"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(codes_of(lambda: self.attempt(data, "below", Limits(max_input_bytes=n - 1))), ["limit_input_bytes"])

    def test_a_receipt_written_by_the_real_muxer_is_accepted_and_its_status_is_kept_verbatim(self):
        """The original is produced by video_core.avmux.mux (owned Python-child fake tools), not hand-written; reinspected through the public CLI."""
        assets, mux_out = self.mkdir("assets"), self.mkdir("mux-out")
        seed_inputs(assets)
        from tests.test_avmux_mux import aac_audio
        mbin = self.mkdir("mux-bin")
        mff, mfp = make_av_tools(mbin, **default_behavior("MP4_AAC", decoded_audio_hex=aac_audio(4800), packets=packets_json(skip=1024, padding=1000)))
        with owned_python_tools(mff, mfp):
            r = mux(dumps(av_manifest("MP4_AAC")), asset_root=assets, output_root=mux_out, output_name="m1", ffmpeg=mff, ffprobe=mfp)
        self.assertEqual(r["status"], "MUXED_TECHNICAL_WITHIN_TOLERANCE", json.dumps(r.get("reasons")))
        media, receipt_file = os.path.join(mux_out, "m1", "output.mp4"), os.path.join(mux_out, "m1", "receipt.json")
        self.assertIn("audio_packets", r["observations"])
        buf = io.BytesIO()
        argv = ["reinspect", media, "--expect-sha256", hashlib.sha256(rb(media)).hexdigest(), "--original-receipt", receipt_file, "--output-root", self.out, "--output-name", "c1",
                "--ffprobe", self.fp, "--reinspect"]
        with mock.patch.object(sys, "stdout", mock.Mock(buffer=buf)):
            code = avcli.main(argv)
        doc = json.loads(buf.getvalue())
        self.assertEqual((code, doc["status"]), (0, "REINSPECTED"))
        o = doc["original_receipt"]
        self.assertEqual((o["status"], o["schema"], o["route"], o["plan_sha256"], o["sha256"]),
                         ("MUXED_TECHNICAL_WITHIN_TOLERANCE", "video_core.avmux_receipt/1", "MP4_AAC", r["plan_sha256"], hashlib.sha256(rb(receipt_file)).hexdigest()))
        self.assertEqual(o["claimed_audio_packets"], r["observations"]["audio_packets"])
        self.assertTrue(o["claimed_audio_packets_basis"].startswith("CLAIMED_UNVERIFIED"))
        self.assertEqual(o["authentication"], AUTHENTICATION)
        self.assertNotEqual(doc["status"], o["status"], "the correction receipt's own status is never the original's")

    def test_legacy_hand_written_and_extra_field_receipts_remain_accepted_with_provenance_gaps_visible(self):
        legacy = {"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "route": "MP4_AAC", "plan_sha256": "ab" * 32, "outputs": [{"role": "media", "name": "output.mp4", "sha256": MEDIA_SHA}],
                  "observations": {"audio_packets": ORIGINAL_SUMMARY}}
        r = self.complete(raw(legacy), "legacy")
        self.assertEqual((r["original_receipt"]["schema"], r["original_receipt"]["claimed_audio_packets"]), (None, ORIGINAL_SUMMARY), "no schema is recorded as null, not invented")
        extra = json.dumps({**good(), "note": "日本語の注記", "future_field": {"a": [1, 2]}}, ensure_ascii=False).encode("utf-8")
        self.complete(extra, "extra")
        r = self.complete(raw({"status": "MUXED_VERIFIED_SAMPLE_EXACT", "outputs": [{"role": "media", "sha256": MEDIA_SHA}]}), "bare")
        self.assertEqual((r["original_receipt"]["claimed_audio_packets"], r["original_receipt"]["status"]), (None, "MUXED_VERIFIED_SAMPLE_EXACT"))
        self.assertEqual(r["differences_from_original_summary"], [{"note": "no original packet summary was supplied or found; nothing to compare"}])

    def test_a_lone_surrogate_inside_the_copied_summary_is_written_losslessly(self):
        data = b'{"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "outputs": [{"role": "media", "sha256": "%s"}], "observations": {"audio_packets": {"note": "a\\ud800b"}}}' % MEDIA_SHA.encode()
        r = self.complete(data, "surrogate")
        self.assertEqual(r["original_receipt"]["claimed_audio_packets"], {"note": "a\ud800b"})
        with open(os.path.join(self.out, "surrogate", "receipt.json"), "rb") as fh:
            self.assertEqual(json.loads(fh.read())["original_receipt"]["claimed_audio_packets"], {"note": "a\ud800b"})

    def test_the_hash_recorded_is_of_the_bytes_that_were_parsed(self):
        data = raw(good())
        self.assertEqual(self.complete(data)["original_receipt"]["sha256"], hashlib.sha256(data).hexdigest())

    def test_both_verified_statuses_are_recorded_exactly_and_the_correction_status_is_its_own(self):
        for status in OK_STATUSES:
            with self.subTest(status):
                r = self.complete(raw(good(status=status)), "s-" + status.lower())
                self.assertEqual((r["original_receipt"]["status"], r["status"], r["quality_evaluated"], r["regenerated_or_remuxed"]), (status, "REINSPECTED", False, False))


class StatusDistinctionsAreNotPromoted(World):
    """Portable: pure rejections under the no-Popen guard."""
    NOT_VERIFIED = ("HOLD", "UNVERIFIED", "IN_PROGRESS", "PROCESS_FAILED", "FAILED", "INTERRUPTED", "UNKNOWN", "READBACK_MISMATCH", "INSPECTION_FAILED", "TIMEOUT", "SIGNALED",
                    "FLOOD_KILLED", "LAUNCH_FAILED", "CONTAINMENT_REFUSED", "SKIPPED_TOOL_UNAVAILABLE", "CANCELLED")

    def test_an_incomplete_mux_cannot_present_a_final_media_output(self):
        """The muxer promotes `media` only for the two verified statuses, so any other status with a final media output is not a receipt it wrote."""
        for status in self.NOT_VERIFIED:
            with self.subTest(status), no_process():
                self.assertEqual(codes_of(lambda: self.attempt(raw(good(status=status)))), ["original_receipt_inconsistent"])

    def test_the_same_statuses_without_a_media_output_fail_the_media_check_not_a_success_path(self):
        for status in self.NOT_VERIFIED:
            with self.subTest(status), no_process():
                data = raw(good(status=status, outputs=[{"role": "media_unverified", "sha256": MEDIA_SHA, "partial": True}]))
                self.assertEqual(codes_of(lambda: self.attempt(data)), ["original_receipt_media_mismatch"])


if __name__ == "__main__":
    unittest.main()
