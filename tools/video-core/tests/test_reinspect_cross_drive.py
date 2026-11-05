"""avmux reinspect: output-directory containment must treat paths on DIFFERENT Windows drives as non-containing, not crash.

Reported by an independent native-Windows run: media on C:, output root on D: (or the reverse) raised an untyped `ValueError: Paths don't have the same drive` from
os.path.commonpath inside reinspect_audio_packets, before any process or output. Fix: the containment test uses paths._inside(run_dir, media_dir), which already answers False on that
ValueError; the existing output_overlaps_media rejection is unchanged for same-drive paths.

EVIDENCE LEVEL: the cross-drive cases run on every OS by giving the code ntpath arithmetic (`_inside(..., _path=ntpath)` and, for the reinspect path, a temporary
os.path.commonpath/dirname = ntpath's with the two path checkers returning Windows-style strings). That exercises the path arithmetic only; it is NOT a Windows run, creates no link and
touches no real drive. A real C:/D: run stays with the independent Windows verifier. Nothing here launches a process (no-Popen guard) or reads/writes outside the owned temp directory.
"""

from __future__ import annotations

import io
import json
import ntpath
import os
import posixpath
import sys
import unittest
from unittest import mock

from tests.helpers import TmpCase, no_process, tree_snapshot
from video_core import tools
from video_core.avmux import cli as avcli, reinspect
from video_core.avmux.reinspect import reinspect_audio_packets
from video_core.errors import UnsafePath, VideoCoreError
from video_core.paths import OutputLocation, _inside

SHA = "0" * 64


class Reached(Exception):
    """Raised by the patched getsize: the containment check was passed."""


class PureContainment(unittest.TestCase):
    def test_the_fixture_premise_the_stdlib_raises_for_different_drives(self):
        """If this stops raising, the cross-drive cases below would pass vacuously."""
        for a, b in ((r"C:\a", r"D:\b"), (r"D:\a", r"C:\b"), (r"\\srv\share1\x", r"\\srv\share2\y")):
            with self.assertRaises(ValueError):
                ntpath.commonpath([a, b])

    def test_different_drives_or_shares_cannot_contain_each_other_and_do_not_raise(self):
        for root, cand in ((r"D:\ws\out\run1", r"C:\Temp\media"), (r"C:\Temp\out\run1", r"D:\ws\media"), (r"D:\ws", r"C:\ws"), (r"\\srv\share1\out", r"\\srv\share2\out\x")):
            with self.subTest(root=root, cand=cand):
                self.assertIs(_inside(root, cand, _path=ntpath), False)
                self.assertIs(_inside(cand, root, _path=ntpath), False)

    def test_same_drive_containment_positive_and_negative_ntpath_and_posixpath(self):
        nt = [(r"D:\ws\out\run1", r"D:\ws\out\run1", True), (r"D:\ws\out\run1", r"D:\ws\out\run1\sub\deeper", True), (r"d:\WS\Out\RUN1", r"D:\ws\out\run1\sub", True),
              (r"D:\ws\out\run1", r"D:\ws\out", False), (r"D:\ws\out\run1", r"D:\ws\out\run1x", False), (r"D:\ws\out\run1", r"D:\ws\out\other", False), (r"D:\ws\out\run1", "D:\\", False)]
        for root, cand, want in nt:
            with self.subTest("nt", root=root, cand=cand):
                self.assertIs(_inside(root, cand, _path=ntpath), want)
        posix = [("/ws/out/run1", "/ws/out/run1", True), ("/ws/out/run1", "/ws/out/run1/sub/deeper", True), ("/ws/out/run1", "/ws/out", False), ("/ws/out/run1", "/ws/out/run1x", False),
                 ("/ws/out/run1", "/ws/out/other", False), ("/ws/out/run1", "/", False)]
        for root, cand, want in posix:
            with self.subTest("posix", root=root, cand=cand):
                self.assertIs(_inside(root, cand, _path=posixpath), want)

    def test_the_platform_default_is_unchanged_resolve_asset_style_use(self):
        self.assertIs(_inside(os.path.abspath("x"), os.path.join(os.path.abspath("x"), "y")), True)
        self.assertIs(_inside(os.path.join(os.path.abspath("x"), "y"), os.path.abspath("x")), False)


def _simulate(media_dir, run_dir):
    """Context manager: reinspect sees Windows-style paths from its two path checkers and ntpath arithmetic; stops (Reached) right after the containment check."""
    loc = OutputLocation(root=ntpath.dirname(run_dir), name=ntpath.basename(run_dir), run_dir=run_dir)
    stack = [mock.patch.object(reinspect, "check_media_for_inspect", lambda p: (ntpath.join(media_dir, "m.mp4"), "mov")),
             mock.patch.object(reinspect, "check_output_location", lambda *a, **k: loc),
             mock.patch.object(os.path, "commonpath", ntpath.commonpath), mock.patch.object(os.path, "dirname", ntpath.dirname),
             mock.patch.object(os.path, "getsize", lambda p: (_ for _ in ()).throw(Reached()))]
    import contextlib
    cm = contextlib.ExitStack()
    for s in stack:
        cm.enter_context(s)
    return cm


def outcome(media_dir, run_dir):
    with _simulate(media_dir, run_dir), no_process():
        try:
            reinspect_audio_packets("ignored", expected_sha256=SHA, output_root="ignored", output_name="ignored")
        except Reached:
            return "passed"
        except VideoCoreError as exc:
            return [i.code for i in exc.issues]


class ReinspectContainment(unittest.TestCase):
    def test_cross_drive_media_and_output_pass_the_containment_check_in_both_directions(self):
        self.assertEqual(outcome(r"C:\Temp\media", r"D:\ws\out\run1"), "passed")
        self.assertEqual(outcome(r"D:\ws\media", r"C:\Temp\out\run1"), "passed")

    def test_same_drive_overlap_is_still_a_typed_rejection_and_same_drive_separation_still_passes(self):
        self.assertEqual(outcome(r"D:\ws\out\run1\sub", r"D:\ws\out\run1"), ["output_overlaps_media"])
        self.assertEqual(outcome(r"D:\ws\out\run1", r"D:\ws\out\run1"), ["output_overlaps_media"])
        self.assertEqual(outcome(r"D:\media", r"D:\ws\out\run1"), "passed")
        self.assertEqual(outcome(r"D:\media", r"D:\media\out\run1"), "passed", "the output directory being INSIDE the media's directory was never rejected by this check")
        self.assertEqual(outcome(r"C:\a\run1x", r"C:\a\run1"), "passed")

    def test_public_cli_main_emits_structured_json_not_a_traceback_for_cross_drive_and_for_overlap(self):
        def run_cli(media_dir, run_dir, hash_ok=False):
            buf = io.BytesIO()
            with _simulate(media_dir, run_dir), no_process(), mock.patch.object(os.path, "getsize", lambda p: 1), mock.patch.object(tools, "sha256_file", lambda *a, **k: "f" * 64), \
                    mock.patch.object(sys, "stdout", mock.Mock(buffer=buf)):
                rc = avcli.main(["reinspect", "m.mp4", "--expect-sha256", SHA, "--output-root", "o", "--output-name", "run1", "--reinspect"])
            return rc, json.loads(buf.getvalue())

        rc, doc = run_cli(r"C:\Temp\media", r"D:\ws\out\run1")  # passes containment, then stops at the next typed check (hash mismatch of the patched file)
        self.assertEqual((rc, doc["ok"], [i["code"] for i in doc["error"]["issues"]]), (1, False, ["media_sha256_mismatch"]))
        rc, doc = run_cli(r"D:\ws\out\run1\sub", r"D:\ws\out\run1")
        self.assertEqual((rc, doc["ok"], doc["error"]["kind"], [i["code"] for i in doc["error"]["issues"]]), (1, False, "unsafe_path", ["output_overlaps_media"]))


class NativeFilesystem(TmpCase):
    """Real files on the platform this runs on (a same-drive case on Windows, a normal case on POSIX): the real path checkers, no simulation."""

    def test_separate_directories_get_past_the_containment_check_to_the_tool_stage_and_an_existing_output_is_still_refused(self):
        media_dir, out = self.mkdir("media"), self.mkdir("out")
        media = self.write("media/m.mp4", b"OWNED-SYNTHETIC-NONMEDIA")
        import hashlib
        sha = hashlib.sha256(b"OWNED-SYNTHETIC-NONMEDIA").hexdigest()
        before = tree_snapshot(self.tmp)
        with no_process(), mock.patch.object(tools, "locate", lambda name, explicit=None: (None, "test guard: no tool is located or started")):
            r = reinspect_audio_packets(media, expected_sha256=sha, output_root=out, output_name="fresh")
        self.assertEqual((r["status"], r["written"]), ("SKIPPED_TOOL_UNAVAILABLE", False), "passed validation and containment; stopped at the (guarded) tool lookup - not a coverage claim")
        self.assertEqual(tree_snapshot(self.tmp), before)
        os.mkdir(os.path.join(out, "taken"))
        with no_process(), self.assertRaises(UnsafePath) as cm:
            reinspect_audio_packets(media, expected_sha256=sha, output_root=out, output_name="taken")
        self.assertEqual([i.code for i in cm.exception.issues], ["output_exists"], "on a real filesystem an existing output directory (even one containing the media) is refused earlier, by output_exists")


if __name__ == "__main__":
    unittest.main()
