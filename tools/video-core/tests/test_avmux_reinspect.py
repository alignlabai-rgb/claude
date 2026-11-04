"""Re-inspection of an existing muxed file (read-only on the media; a NEW correction receipt in a fresh directory). Fake ffprobe only."""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.audio_fixtures import dumps, sha, write
from tests.avmux_fakes import calls, default_behavior, make_av_tools, packets_custom, packets_json, seed_inputs
from tests.helpers import POSIX, ROOT, TmpCase, jl, rb, tree_snapshot
from video_core import tools
from video_core.avmux.reinspect import SCHEMA, reinspect_audio_packets
from video_core.errors import UnsafePath, ValidationFailed

ORIGINAL_SUMMARY = {"packet_count": 6, "skip_samples": 1024, "discard_padding": None, "skip_samples_side_data_seen": True, "first_packet": {"pts": -1024}, "last_packet": {"pts": 4096},
                    "note": "skip/padding values are reported only if ffprobe exposes them as packet side data; absence is not evidence of zero"}


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class Reinspect(TmpCase):
    def setUp(self):
        super().setUp()
        self.bin, self.out, self.media_dir = self.mkdir("bin"), self.mkdir("out"), self.mkdir("media")
        self.media = write(os.path.join(self.media_dir, "output.mp4"), b"ORIGINAL-AAC-OUTPUT-BYTES")
        self.sha = sha(rb(self.media))
        self.ff, self.fp = make_av_tools(self.bin, **default_behavior("MP4_AAC", packets=packets_json(skip=1024, padding=None), probe_out=self.probe()))
        self.receipt_path = write(os.path.join(self.media_dir, "original.receipt.json"), dumps(
            {"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "route": "MP4_AAC", "plan_sha256": "ab" * 32, "outputs": [{"role": "media", "name": "output.mp4", "sha256": self.sha}],
             "observations": {"audio_packets": ORIGINAL_SUMMARY}}))

    def probe(self):
        from tests.avmux_fakes import out_probe
        return out_probe("MP4_AAC")

    def run_it(self, name="fix1", **kw):
        args = dict(expected_sha256=self.sha, output_root=self.out, output_name=name, ffprobe=self.fp, original_receipt_path=self.receipt_path)
        args.update(kw)
        return reinspect_audio_packets(self.media, **args)

    def test_correction_receipt_is_reinspection_only_and_references_the_original_hashes(self):
        r = self.run_it()
        self.assertEqual((r["schema"], r["status"], r["reinspection_only"], r["regenerated_or_remuxed"], r["quality_evaluated"]), (SCHEMA, "REINSPECTED", True, False, False))
        self.assertIn("Re-inspection only", r["note"])
        self.assertIn("not modified", r["note"])
        m = r["media"]
        self.assertEqual((m["name"], m["sha256_expected"], m["sha256_before"], m["sha256_after"], m["unchanged"]), ("output.mp4", self.sha, self.sha, self.sha, True))
        o = r["original_receipt"]
        self.assertEqual((o["name"], o["sha256"], o["status"], o["route"], o["plan_sha256"]), ("original.receipt.json", sha(rb(self.receipt_path)), "MUXED_TECHNICAL_WITHIN_TOLERANCE", "MP4_AAC", "ab" * 32))
        self.assertEqual(r["audio_packets"]["schema"], "video_core.avmux_audio_packets/2")
        self.assertEqual(jl(os.path.join(self.out, "fix1", "receipt.json")), json.loads(json.dumps(r)))
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "fix1"))), ["ffprobe.packets.json", "receipt.json"])
        self.assertEqual(r["ffprobe_packets_json_sha256"], sha(rb(os.path.join(self.out, "fix1", "ffprobe.packets.json"))))
        self.assertNotIn(self.tmp, json.dumps(r["media"]) + json.dumps(r["original_receipt"]), "only base names are recorded for the media and the original receipt")

    def test_the_media_and_the_original_receipt_are_never_modified(self):
        before = tree_snapshot(self.media_dir)
        self.run_it()
        self.assertEqual(tree_snapshot(self.media_dir), before)
        self.assertEqual(rb(self.media), b"ORIGINAL-AAC-OUTPUT-BYTES")

    def test_it_never_muxes_or_calls_ffmpeg(self):
        self.run_it()
        self.assertEqual({c["tool"] for c in calls(self.bin)}, {"ffprobe"})
        for c in calls(self.bin):
            self.assertNotIn("-c:v", c["argv"])
            self.assertNotIn("copy", c["argv"])
        argvs = [c["argv"] for c in calls(self.bin) if "-show_packets" in c["argv"]]
        self.assertEqual(len(argvs), 1)
        self.assertEqual(argvs[0][argvs[0].index("-select_streams") + 1], "a:0")
        self.assertEqual(argvs[0][argvs[0].index("-f") + 1], "mov")
        self.assertIn("-protocol_whitelist", argvs[0])

    def test_difference_report_explains_the_original_null_when_zero_was_actually_observed(self):
        r = self.run_it()
        d = {x["field"]: x for x in r["differences_from_original_summary"]}
        self.assertIsNone(d["discard_padding"]["original_summary_value"])
        self.assertEqual(d["discard_padding"]["corrected"]["observed_zero_in_records"], 1)
        self.assertIn("conflated", d["discard_padding"]["explanation"])
        self.assertEqual(d["skip_samples"]["original_summary_value"], 1024)
        self.assertEqual(d["skip_samples"]["corrected"]["nonzero"], [{"packet_index": 0, "value": 1024}])
        self.assertIn("one of the corrected non-zero records", d["skip_samples"]["explanation"])

    def test_difference_report_when_ffprobe_really_printed_nothing(self):
        make_av_tools(self.bin, **default_behavior("MP4_AAC", packets=packets_json(skip=None, padding=None), probe_out=self.probe()))
        r = self.run_it()
        d = {x["field"]: x for x in r["differences_from_original_summary"]}
        self.assertIn("printed no discard_padding", d["discard_padding"]["explanation"])
        self.assertEqual(r["audio_packets"]["side_data_record_count"], 0)

    def test_without_an_original_receipt_there_is_nothing_to_compare(self):
        r = self.run_it(original_receipt_path=None)
        self.assertIsNone(r["original_receipt"])
        self.assertIn("nothing to compare", r["differences_from_original_summary"][0]["note"])

    def test_a_wrong_media_hash_inspects_nothing_and_writes_nothing(self):
        before = tree_snapshot(self.out)
        with self.assertRaises(ValidationFailed) as cm:
            self.run_it(expected_sha256="0" * 64)
        self.assertEqual(cm.exception.issues[0].code, "media_sha256_mismatch")
        self.assertIn(self.sha, cm.exception.issues[0].message)
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(calls(self.bin), [])
        for bad in ("zz", "", "a" * 63):
            with self.assertRaises(ValidationFailed):
                self.run_it(expected_sha256=bad)

    def test_original_receipt_must_describe_this_exact_media(self):
        other = write(os.path.join(self.media_dir, "other.json"), dumps({"status": "X", "outputs": [{"role": "media", "sha256": "1" * 64}]}))
        with self.assertRaises(ValidationFailed) as cm:
            self.run_it(original_receipt_path=other)
        self.assertEqual(cm.exception.issues[0].code, "original_receipt_media_mismatch")
        bad = write(os.path.join(self.media_dir, "bad.json"), b"not json")
        with self.assertRaises(ValidationFailed):
            self.run_it(original_receipt_path=bad)
        with self.assertRaises(UnsafePath):
            self.run_it(original_receipt_path=os.path.join(self.media_dir, "missing.json"))
        os.symlink(self.receipt_path, os.path.join(self.media_dir, "link.json"))
        with self.assertRaises(UnsafePath):
            self.run_it(original_receipt_path=os.path.join(self.media_dir, "link.json"))
        self.assertEqual(os.listdir(self.out), [])

    def test_unsafe_media_and_output_locations_are_refused(self):
        for p, code in (("http://x/a.mp4", "path_protocol"), (os.path.join(self.media_dir, "x.avi"), "source_extension_unsupported"), (os.path.join(self.media_dir, "none.mp4"), "asset_missing")):
            with self.assertRaises(UnsafePath) as cm:
                reinspect_audio_packets(p, expected_sha256=self.sha, output_root=self.out, output_name="n", ffprobe=self.fp)
            self.assertEqual(cm.exception.issues[0].code, code)
        os.symlink(self.media, os.path.join(self.media_dir, "link.mp4"))
        with self.assertRaises(UnsafePath):
            reinspect_audio_packets(os.path.join(self.media_dir, "link.mp4"), expected_sha256=self.sha, output_root=self.out, output_name="n", ffprobe=self.fp)
        self.run_it(name="taken")
        with self.assertRaises(UnsafePath) as cm:
            self.run_it(name="taken")
        self.assertEqual(cm.exception.issues[0].code, "output_exists")
        for bad in ("../x", "a b", ""):
            with self.assertRaises(UnsafePath):
                self.run_it(name=bad)
        with self.assertRaises(UnsafePath):
            self.run_it(name="..")

    def test_missing_ffprobe_is_a_skip_and_ffprobe_failure_writes_nothing(self):
        r = self.run_it(ffprobe=os.path.join(self.tmp, "none"))
        self.assertEqual((r["status"], r["written"]), ("SKIPPED_TOOL_UNAVAILABLE", False))
        make_av_tools(self.bin, **default_behavior("MP4_AAC", probe_exit=1))
        before = tree_snapshot(self.out)
        r = self.run_it(name="fail")
        self.assertEqual((r["status"], r["written"]), ("INSPECTION_FAILED", False))
        self.assertEqual(tree_snapshot(self.out), before)

    def test_a_media_file_that_changes_during_inspection_is_unknown_but_preserved(self):
        real = tools.sha256_file
        seen = []

        def flaky(path, max_bytes=None):
            h = real(path, max_bytes)
            if path == self.media:
                seen.append(1)
                return h if len(seen) == 1 else "f" * 64
            return h
        with mock.patch("video_core.avmux.reinspect.tools.sha256_file", flaky):
            r = self.run_it()
        self.assertEqual(r["status"], "UNKNOWN")
        self.assertFalse(r["media"]["unchanged"])
        self.assertIn("changed", r["unknown_reason"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "fix1", "receipt.json")))

    def test_cli_requires_the_explicit_flag_and_exit_codes(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}

        def cli(*a):
            return subprocess.run([sys.executable, "-m", "video_core.avmux", "reinspect", *a], capture_output=True, cwd=self.tmp, env=env, timeout=120)
        before = tree_snapshot(self.out)
        r = cli(self.media, "--expect-sha256", self.sha, "--output-root", self.out)
        self.assertEqual((r.returncode, json.loads(r.stdout)["mode"]), (0, "dry_run"))
        self.assertEqual((tree_snapshot(self.out), calls(self.bin)), (before, []))
        r = cli(self.media, "--expect-sha256", self.sha, "--output-root", self.out, "--ffprobe", self.fp, "--original-receipt", self.receipt_path, "--reinspect")
        self.assertEqual((r.returncode, json.loads(r.stdout)["status"]), (0, "REINSPECTED"), r.stderr)
        self.assertEqual(os.listdir(self.out), ["reinspect-" + self.sha[:12]])
        r = cli(self.media, "--expect-sha256", "0" * 64, "--output-root", self.out, "--ffprobe", self.fp, "--reinspect")
        self.assertEqual(r.returncode, 1)
        self.assertIn("media_sha256_mismatch", r.stdout.decode())
        for bad in ("nan", "inf", "0"):
            self.assertEqual(cli(self.media, "--expect-sha256", self.sha, "--output-root", self.out, "--reinspect", f"--inspect-timeout-seconds={bad}").returncode, 2)


if __name__ == "__main__":
    unittest.main()
