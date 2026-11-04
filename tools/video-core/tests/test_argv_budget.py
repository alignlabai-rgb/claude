"""Argument-list bounds. The Windows rule is evaluated with list2cmdline (the exact string Python hands to
CreateProcess on Windows); tests force `windows=True` / patch is_windows() so they run on any OS.
This does NOT execute anything on Windows."""

import os
import subprocess
import unittest
from unittest import mock

from tests.helpers import TmpCase, color_clips, episode_doc, no_process, sample_bytes, to_bytes
from video_core import Limits, prepare
from video_core import limits as L
from video_core.errors import ValidationFailed

BASELINE_PLAN_SHA256 = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"


def codes(fn):
    try:
        fn()
    except ValidationFailed as exc:
        return {i.code for i in exc.issues}
    return set()


class Budget(unittest.TestCase):
    def test_cmdline_chars_is_the_windows_command_line_length(self):
        argv = ["C:\\Program Files\\ffmpeg\\ffmpeg.exe", "-i", "a b.mp4", 'say "hi"', "back\\slash\\", "テスト"]
        self.assertEqual(L.cmdline_chars(argv), len(subprocess.list2cmdline(argv)))
        self.assertGreater(L.cmdline_chars(argv), sum(len(a) for a in argv) + len(argv) - 1, "quoting/escaping adds characters")

    def test_windows_bound_is_exact_at_the_boundary(self):
        lim = Limits(max_windows_cmdline_chars=100)
        for n, ok in ((100, True), (101, False)):
            argv = ["x" * (n - 5), "-n", "z"]  # (n-5) + 1 + 2 + 1 + 1 = n
            self.assertEqual(L.cmdline_chars(argv), n)
            self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim, windows=True)) == set(), ok)

    def test_windows_bound_only_enforced_on_windows_but_always_reported(self):
        argv = ["x" * 200]
        lim = Limits(max_windows_cmdline_chars=100)
        self.assertEqual(L.check_argv_budget(argv, lim, windows=False)["windows_cmdline_chars"], 200)
        self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim, windows=True)), {"limit_cmdline_windows"})
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim)), {"limit_cmdline_windows"})
        with mock.patch.object(L, "is_windows", return_value=False):
            self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim)), set())

    def test_byte_bound_applies_everywhere(self):
        self.assertEqual(codes(lambda: L.check_argv_budget(["x" * 50], Limits(max_argv_bytes=50), windows=False)), {"limit_argv_bytes"})
        self.assertEqual(codes(lambda: L.check_argv_budget(["x" * 49], Limits(max_argv_bytes=50), windows=False)), set())

    def test_non_ascii_counts_bytes_for_the_byte_bound_and_chars_for_windows(self):
        argv = ["テスト"]
        self.assertEqual(L.argv_budget(argv), {"argv_bytes": 10, "windows_cmdline_chars": 3})

    def test_limit_value_is_validated_against_the_win32_hard_limit(self):
        self.assertEqual(Limits(max_windows_cmdline_chars=L.WIN_CMDLINE_HARD_LIMIT).max_windows_cmdline_chars, 32_766)
        for bad in (32_767, 0, -1, True):
            with self.assertRaises(ValueError):
                Limits(max_windows_cmdline_chars=bad)


class PlanBudget(TmpCase):
    def test_baseline_plan_hash_is_unchanged_by_this_patch(self):
        """Pins the planned command (and thus the documented colour behaviour) to the first delivery."""
        self.assertEqual(prepare(sample_bytes()).sha256, BASELINE_PLAN_SHA256)

    def test_plan_reports_the_windows_budget_without_enforcing_it_on_posix(self):
        doc = episode_doc(total=256, clips=color_clips([1] * 256), captions=[])
        with no_process(), mock.patch.object(L, "is_windows", return_value=False):
            p = prepare(to_bytes(doc))
        b = p.observations["argv_budget"]
        self.assertFalse(b["fits_windows_limit"])
        self.assertGreater(b["estimated_windows_cmdline_chars"], 32_000)
        self.assertEqual(b["windows_hard_limit_chars"], 32_766)

    def test_plan_rejects_the_same_episode_when_the_platform_is_windows(self):
        doc = episode_doc(total=256, clips=color_clips([1] * 256), captions=[])
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(codes(lambda: prepare(to_bytes(doc))), {"limit_cmdline_windows"})
        small = episode_doc(total=50, clips=color_clips([1] * 50), captions=[])
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertTrue(prepare(to_bytes(small)).observations["argv_budget"]["fits_windows_limit"])

    def test_caller_can_tighten_the_windows_bound(self):
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(codes(lambda: prepare(sample_bytes(), limits=Limits(max_windows_cmdline_chars=500))), {"limit_cmdline_windows"})

    def test_many_file_sources_are_not_falsely_rejected_by_slack(self):
        """Regression: the first delivery added 1024 bytes per file input and rejected ~100 file clips by default."""
        assets = self.mkdir("assets")
        n = 100
        for i in range(n):
            with open(os.path.join(assets, f"clip{i:03d}.mp4"), "wb") as fh:
                fh.write(b"x")
        clips = [{"id": f"c{i}", "start_frame": i, "duration_frames": 1, "source": {"kind": "file", "path": f"clip{i:03d}.mp4"}} for i in range(n)]
        with no_process(), mock.patch.object(L, "is_windows", return_value=False):
            p = prepare(to_bytes(episode_doc(total=n, clips=clips, captions=[])), asset_root=assets)
        self.assertLess(p.observations["argv_budget"]["estimated_argv_bytes"], 40_000)

    def test_real_roots_make_the_estimate_longer_than_the_unknown_root_assumption_when_they_are_long(self):
        deep = self.mkdir("a" * 100, "b" * 100, "c" * 100)
        with open(os.path.join(deep, "c.mp4"), "wb") as fh:
            fh.write(b"x")
        doc = episode_doc(total=180, clips=[{"id": "v", "start_frame": 0, "duration_frames": 180, "source": {"kind": "file", "path": "c.mp4"}}], captions=[])
        short = self.mkdir("s")
        with open(os.path.join(short, "c.mp4"), "wb") as fh:
            fh.write(b"x")
        long_b = prepare(to_bytes(doc), asset_root=deep).observations["argv_budget"]["estimated_argv_bytes"]
        short_b = prepare(to_bytes(doc), asset_root=short).observations["argv_budget"]["estimated_argv_bytes"]
        self.assertGreater(long_b - short_b, 250)


if __name__ == "__main__":
    unittest.main()
