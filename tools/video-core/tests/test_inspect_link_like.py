"""F-PATH-1: `check_media_for_inspect` must apply the same link-like rule as `resolve_asset` (symlink OR Windows reparse point) to the named media file.

EVIDENCE LEVEL: every reparse-point case below uses a STAT-LIKE STUB (a patched `os.lstat` result carrying `st_file_attributes`). That proves the code
path honours the attribute; it is NOT proof about a real NTFS junction, mount point, OneDrive placeholder or any other Windows reparse object, which
were never created or tested here (no privileged link creation, no developer-mode change). Real-platform behaviour stays UNVERIFIED.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import unittest
from unittest import mock

from tests.helpers import POSIX, ROOT, TmpCase, no_process, tree_snapshot
from video_core import cli, paths
from video_core.avmux.reinspect import reinspect_audio_packets
from video_core.errors import UnsafePath, VideoCoreError


def stat_like(real: os.stat_result, *, attributes: int | None = None, mode: int | None = None):
    """What os.lstat would return for a file with the given Windows attribute bits (a Mock; `st_file_attributes` only when given)."""
    kw = {"st_mode": real.st_mode if mode is None else mode, "st_size": real.st_size}
    if attributes is not None:
        kw["st_file_attributes"] = attributes
    return mock.Mock(spec=list(kw), **kw)


def codes(fn):
    try:
        fn()
    except VideoCoreError as exc:
        return {i.code for i in exc.issues}
    return None


class StubbedLstat(TmpCase):
    """Patch `paths.os.lstat` for ONE path only; every other lstat is the real one."""

    def patched(self, target: str, replacement):
        real = os.lstat
        return mock.patch.object(paths.os, "lstat", lambda p, *a, **k: replacement if os.path.abspath(str(p)) == os.path.abspath(target) else real(p, *a, **k))


class InspectLinkLikeRule(StubbedLstat):
    def setUp(self):
        super().setUp()
        self.media = self.write("m.mp4", b"x")
        self.real = os.lstat(self.media)

    def test_the_stub_is_link_like_to_the_asset_resolver_and_is_now_rejected_by_inspect_too(self):
        stub = stat_like(self.real, attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        self.assertTrue(paths.is_link_like(stub))  # the rule resolve_asset already applies
        self.assertTrue(stat.S_ISREG(stub.st_mode), "a regular-file mode, so S_ISLNK alone cannot see it: this is the F-PATH-1 trigger")
        with self.patched(self.media, stub):
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.media)), {"symlink_rejected"})
            # parity: the asset resolver rejects the identical stub with the identical code
            self.assertEqual(codes(lambda: paths.resolve_asset(self.tmp, "m.mp4", ptr="/x", allowed_ext=(".mp4",))), {"symlink_rejected"})

    def test_only_the_reparse_bit_matters_other_attributes_and_plain_files_are_accepted(self):
        for label, attrs in (("none", None), ("zero", 0), ("archive", stat.FILE_ATTRIBUTE_ARCHIVE), ("hidden+archive", stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_ARCHIVE),
                             ("readonly", stat.FILE_ATTRIBUTE_READONLY), ("normal", stat.FILE_ATTRIBUTE_NORMAL)):
            with self.subTest(label), self.patched(self.media, stat_like(self.real, attributes=attrs)):
                self.assertEqual(paths.check_media_for_inspect(self.media), (os.path.abspath(self.media), "mov"))
        for label, attrs in (("reparse only", stat.FILE_ATTRIBUTE_REPARSE_POINT), ("reparse+archive", stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_ARCHIVE)):
            with self.subTest(label), self.patched(self.media, stat_like(self.real, attributes=attrs)):
                self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.media)), {"symlink_rejected"})

    def test_a_symlink_mode_is_still_rejected_and_a_directory_or_other_non_regular_is_still_not_regular(self):
        with self.patched(self.media, stat_like(self.real, mode=stat.S_IFLNK | 0o777)):
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.media)), {"symlink_rejected"})
        with self.patched(self.media, stat_like(self.real, mode=stat.S_IFDIR | 0o755)):
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.media)), {"not_regular_file"})

    def test_ordinary_regular_files_including_japanese_and_spaced_names_are_accepted(self):
        for name in ("m.mp4", "動画 テスト.mov", "my video.m4v", "日本語のファイル.webm", "a (1).mkv", "ＡＢＣ.mp4"):
            with self.subTest(name):
                p = self.write(name, b"x")
                self.assertEqual(paths.check_media_for_inspect(p), (os.path.abspath(p), paths.PROBE_EXTENSIONS[os.path.splitext(name)[1].lower()]))

    def test_the_other_guards_are_untouched_and_fire_before_any_filesystem_look(self):
        """Protocol, extension and option-like rules are decided on the string; a reparse stub must not change their outcome or order."""
        boom = mock.Mock(side_effect=AssertionError("lstat must not be reached"))
        with mock.patch.object(paths.os, "lstat", boom):
            self.assertEqual(codes(lambda: paths.check_media_for_inspect("http://x/a.mp4")), {"path_protocol"})
            self.assertEqual(codes(lambda: paths.check_media_for_inspect("concat:a.mp4|b.mp4")), {"path_protocol"})
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.write("p.m3u8", b"x"))), {"source_extension_unsupported"})
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.write("n.txt", b"x"))), {"source_extension_unsupported"})
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(os.path.join(self.tmp, "-x.mp4"))), {"path_option_like"})
            self.assertEqual(codes(lambda: paths.check_media_for_inspect("")), {"path_empty"})
        self.assertEqual(codes(lambda: paths.check_media_for_inspect(os.path.join(self.tmp, "absent.mp4"))), {"asset_missing"})

    @unittest.skipUnless(POSIX, "real symlinks")
    def test_a_real_posix_symlink_still_gets_the_same_code(self):
        os.symlink(self.media, os.path.join(self.tmp, "l.mp4"))
        self.assertEqual(codes(lambda: paths.check_media_for_inspect(os.path.join(self.tmp, "l.mp4"))), {"symlink_rejected"})


class InspectEntryPointsRejectBeforeAnySideEffect(StubbedLstat):
    """Both callers of the check (`video_core inspect`, `avmux reinspect`) must refuse the stubbed reparse file before any process or output exists."""

    def test_the_inspect_cli_main_returns_the_structured_rejection_and_launches_nothing(self):
        media = self.write("m.mp4", b"x")
        stub = stat_like(os.lstat(media), attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        before = tree_snapshot(self.tmp)
        buf = io.BytesIO()
        fake_stdout = mock.Mock(buffer=buf)
        with no_process(), self.patched(media, stub), mock.patch.object(sys, "stdout", fake_stdout):
            rc = cli.main(["inspect", media])
        self.assertEqual(rc, 1)
        doc = json.loads(buf.getvalue())
        self.assertEqual((doc["ok"], doc["error"]["kind"], [i["code"] for i in doc["error"]["issues"]]), (False, "unsafe_path", ["symlink_rejected"]))
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_avmux_reinspect_rejects_before_creating_an_output_directory_or_running_ffprobe(self):
        media = self.write("m.mp4", b"x")
        out = self.mkdir("out")
        stub = stat_like(os.lstat(media), attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        before = tree_snapshot(self.tmp)
        with no_process(), self.patched(media, stub):
            with self.assertRaises(UnsafePath) as cm:
                reinspect_audio_packets(media, expected_sha256="0" * 64, output_root=out, output_name="r1")
        self.assertEqual([i.code for i in cm.exception.issues], ["symlink_rejected"])
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(os.listdir(out), [])


class RealPlatformEvidenceIsNotClaimed(unittest.TestCase):
    def test_this_module_documents_that_stub_evidence_is_not_real_windows_evidence(self):
        self.assertIn("NOT proof about a real NTFS junction", __doc__)
        self.assertIn("UNVERIFIED", __doc__)


if __name__ == "__main__":
    unittest.main()
