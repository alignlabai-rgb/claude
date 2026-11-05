"""`avmux reinspect --original-receipt`: strict, typed, side-effect-free handling of the optional original receipt.

Contract (see INPUT_CONTRACT.md "Follow-up 2" and the module docstring of video_core/avmux/original_receipt.py): a bad original receipt is a stable structured rejection
(library: ValidationFailed/UnsafePath with an `original_receipt*` code; CLI: exit 1 and {"ok": false, "error": ...} on stdout) raised BEFORE any ffprobe/ffmpeg start and
before any output exists. Valid producer receipts stay accepted; the original status is kept verbatim and never promoted; a caller-supplied hash is byte integrity, not
authenticity. All inputs are tiny synthetic files; the only tools are the repo's owned FAKE ffprobe/ffmpeg scripts (POSIX) or a no-Popen guard; no real media.
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
from video_core.avmux import cli as avcli
from video_core.avmux.mux import OK_STATUSES, mux
from video_core.avmux.original_receipt import AUTHENTICATION, MAX_SUMMARY_DEPTH, MAX_SUMMARY_JSON_BYTES, read_original_receipt
from video_core.avmux.reinspect import reinspect_audio_packets
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
    """A media file, owned fake tools, an output root; `attempt(bytes)` runs the real library function, `cli(bytes)` the real public CLI main(argv)."""

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
        """The actual public entry point `python -m video_core.avmux reinspect ... --reinspect`, called as main(argv) with stdout captured."""
        self.put(data)
        buf = io.BytesIO()
        argv = ["reinspect", self.media, "--expect-sha256", MEDIA_SHA, "--original-receipt", self.rpath, "--output-root", self.out, "--output-name", name,
                "--ffprobe", self.fp, "--reinspect", *extra]
        with mock.patch.object(sys, "stdout", mock.Mock(buffer=buf)):
            rc = avcli.main(argv)
        return rc, json.loads(buf.getvalue())


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
        before = tree_snapshot(self.tmp)
        for label, (data, code) in REJECTIONS.items():
            with self.subTest(label):
                self.put(data)
                before = tree_snapshot(self.tmp)
                with no_process():  # a Popen anywhere (ffprobe version probe included) fails the test
                    got = codes_of(lambda: self.attempt(data))
                self.assertEqual(got, [code])
                self.assertEqual(tree_snapshot(self.tmp), before, "nothing created, nothing modified")
        self.assertEqual(calls(self.bin), [], "the owned fake ffprobe was never started")

    def test_a_receipt_over_the_input_limit_is_refused_and_the_limit_itself_is_accepted(self):
        data = raw(good(note="y" * 3000))
        n = len(data)
        with no_process():
            self.assertEqual(codes_of(lambda: self.attempt(data, limits=Limits(max_input_bytes=n - 1))), ["limit_input_bytes"])
        self.assertEqual(calls(self.bin), [])
        self.assertEqual(self.attempt(data, name="at-limit", limits=Limits(max_input_bytes=n))["status"], "REINSPECTED")

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


@unittest.skipUnless(POSIX, "owned fake tools are POSIX scripts")
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
                self.assertEqual(doc["error"]["issues"][0]["path"], "original_receipt" if code != "limit_input_bytes" else "original_receipt")
                self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(calls(self.bin), [])

    def test_a_real_interpreter_child_prints_no_traceback_and_starts_no_ffprobe(self):
        marker = os.path.join(self.tmp, "LAUNCHED")
        probe = os.path.join(self.bin, "marker-ffprobe")
        write(probe, f"#!/bin/sh\necho started >> '{marker}'\nexit 9\n".encode())
        os.chmod(probe, 0o755)
        for label in ("[]", "outputs str", "audio_packets str", "duplicate top-level key", "NaN", "100000 nested arrays", "status list"):
            data, code = REJECTIONS[label]
            with self.subTest(label):
                self.put(data)
                p = subprocess.run([sys.executable, "-m", "video_core.avmux", "reinspect", self.media, "--expect-sha256", MEDIA_SHA, "--original-receipt", self.rpath,
                                    "--output-root", self.out, "--output-name", "fix1", "--ffprobe", probe, "--reinspect"], capture_output=True, timeout=10,
                                   env={"PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", "")})
                self.assertEqual(p.returncode, 1, p.stderr[-300:])
                self.assertNotIn(b"Traceback", p.stderr)
                self.assertEqual([i["code"] for i in json.loads(p.stdout)["error"]["issues"]], [code])
                self.assertFalse(os.path.exists(marker))
                self.assertEqual(os.listdir(self.out), [])


@unittest.skipUnless(POSIX, "owned fake tools are POSIX scripts")
class ValidReceiptsStayAccepted(World):
    def test_a_receipt_written_by_the_real_muxer_is_accepted_and_its_status_is_kept_verbatim(self):
        """The original is produced by video_core.avmux.mux (owned fake ffmpeg/ffprobe), not hand-written; reinspected through the public CLI."""
        assets, mux_out, mbin = self.mkdir("assets"), self.mkdir("mux-out"), self.mkdir("mux-bin")
        seed_inputs(assets)
        from tests.test_avmux_mux import aac_audio
        make_av_tools(mbin, **default_behavior("MP4_AAC", decoded_audio_hex=aac_audio(4800), packets=packets_json(skip=1024, padding=1000)))
        r = mux(dumps(av_manifest("MP4_AAC")), asset_root=assets, output_root=mux_out, output_name="m1", ffmpeg=os.path.join(mbin, "ffmpeg"), ffprobe=os.path.join(mbin, "ffprobe"))
        self.assertEqual(r["status"], "MUXED_TECHNICAL_WITHIN_TOLERANCE")
        media = os.path.join(mux_out, "m1", "output.mp4")
        receipt_file = os.path.join(mux_out, "m1", "receipt.json")
        self.assertIn("audio_packets", r["observations"], "the producer's own record-based summary (side_data_records ...) is what gets read back")
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
        self.assertEqual(o["authentication"], AUTHENTICATION)
        self.assertIn("not who produced or approved", o["authentication"])
        self.assertNotEqual(doc["status"], o["status"], "the correction receipt's own status is never the original's")

    def test_the_committed_producer_receipts_are_accepted(self):
        for route in ("mov", "mp4"):
            data = rb(os.path.join(ROOT, "evidence", "avmux-synthetic", f"{route}.receipt.json"))
            media_sha = next(o["sha256"] for o in json.loads(data)["outputs"] if o["role"] == "media")
            facts = read_original_receipt(data, expected_sha256=media_sha, limits=Limits())
            self.assertIn(facts["status"], OK_STATUSES)
            self.assertEqual(facts["schema"], "video_core.avmux_receipt/1")

    def test_legacy_hand_written_and_extra_field_receipts_remain_accepted_with_provenance_gaps_visible(self):
        legacy = {"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "route": "MP4_AAC", "plan_sha256": "ab" * 32, "outputs": [{"role": "media", "name": "output.mp4", "sha256": MEDIA_SHA}],
                  "observations": {"audio_packets": ORIGINAL_SUMMARY}}
        r = self.attempt(raw(legacy), name="legacy")
        self.assertEqual((r["status"], r["original_receipt"]["schema"]), ("REINSPECTED", None), "no schema is recorded as null, not invented")
        extra = json.dumps({**good(), "note": "日本語の注記", "future_field": {"a": [1, 2]}}, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.attempt(extra, name="extra")["status"], "REINSPECTED")
        bare = raw({"status": "MUXED_VERIFIED_SAMPLE_EXACT", "outputs": [{"role": "media", "sha256": MEDIA_SHA}]})
        r = self.attempt(bare, name="bare")
        self.assertEqual((r["original_receipt"]["claimed_audio_packets"], r["original_receipt"]["status"]), (None, "MUXED_VERIFIED_SAMPLE_EXACT"))
        self.assertEqual(r["differences_from_original_summary"], [{"note": "no original packet summary was supplied or found; nothing to compare"}])

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

    def test_a_lone_surrogate_inside_the_copied_summary_is_written_losslessly(self):
        """Links the F-CLI-1 boundary: a summary value copied from the original receipt may be unencodable; the correction receipt must still be written and decode to it."""
        data = b'{"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "outputs": [{"role": "media", "sha256": "%s"}], "observations": {"audio_packets": {"note": "a\\ud800b"}}}' % MEDIA_SHA.encode()
        r = self.attempt(data, name="surrogate")
        self.assertEqual(r["original_receipt"]["claimed_audio_packets"], {"note": "a\ud800b"})
        with open(os.path.join(self.out, "surrogate", "receipt.json"), "rb") as fh:
            self.assertEqual(json.loads(fh.read())["original_receipt"]["claimed_audio_packets"], {"note": "a\ud800b"})

    def test_the_hash_recorded_is_of_the_bytes_that_were_parsed(self):
        data = raw(good())
        r = self.attempt(data)
        self.assertEqual(r["original_receipt"]["sha256"], hashlib.sha256(data).hexdigest())


@unittest.skipUnless(POSIX, "owned fake tools are POSIX scripts")
class StatusDistinctionsAreNotPromoted(World):
    NOT_VERIFIED = ("HOLD", "UNVERIFIED", "IN_PROGRESS", "PROCESS_FAILED", "FAILED", "INTERRUPTED", "UNKNOWN", "READBACK_MISMATCH", "INSPECTION_FAILED", "TIMEOUT", "SIGNALED",
                    "FLOOD_KILLED", "LAUNCH_FAILED", "CONTAINMENT_REFUSED", "SKIPPED_TOOL_UNAVAILABLE", "CANCELLED")

    def test_an_incomplete_mux_cannot_present_a_final_media_output(self):
        """The muxer promotes `media` only for the two verified statuses, so any other status with a final media output is not a receipt it wrote."""
        for status in self.NOT_VERIFIED:
            with self.subTest(status), no_process():
                self.assertEqual(codes_of(lambda: self.attempt(raw(good(status=status)))), ["original_receipt_inconsistent"])
        self.assertEqual(calls(self.bin), [])

    def test_the_same_statuses_without_a_media_output_fail_the_media_check_not_a_success_path(self):
        for status in self.NOT_VERIFIED:
            with self.subTest(status), no_process():
                data = raw(good(status=status, outputs=[{"role": "media_unverified", "sha256": MEDIA_SHA, "partial": True}]))
                self.assertEqual(codes_of(lambda: self.attempt(data)), ["original_receipt_media_mismatch"])

    def test_both_verified_statuses_are_recorded_exactly_and_the_correction_status_is_its_own(self):
        for status in OK_STATUSES:
            with self.subTest(status):
                r = self.attempt(raw(good(status=status)), name="s-" + status.lower())
                self.assertEqual((r["original_receipt"]["status"], r["status"], r["quality_evaluated"], r["regenerated_or_remuxed"]), (status, "REINSPECTED", False, False))


if __name__ == "__main__":
    unittest.main()
