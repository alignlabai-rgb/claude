"""Argument-list bounds.

The Windows rule is measured in UTF-16 code units of the list2cmdline string (what CreateProcessW consumes). It is
evaluated here with `windows=True` / a patched is_windows(), so these tests run on any OS. They do NOT execute anything on
Windows; the requester's own Windows launch of an owned Python with 17000 non-BMP characters (python_len 17106,
UTF-16 units 34106, UTF-8 bytes 68106, WinError 206) is the real-platform evidence that motivated the unit.
"""

import os
import subprocess
import unittest
from unittest import mock

from tests.helpers import TmpCase, color_clips, episode_doc, no_process, sample_bytes, to_bytes
from video_core import Limits, prepare
from video_core import limits as L
from video_core.errors import ValidationFailed
from video_core.plan import estimate_argv, materialize

BASELINE_PLAN_SHA256 = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"
EMOJI = "\U0001F600"  # non-BMP: 1 code point, 2 UTF-16 units, 4 UTF-8 bytes


def codes(fn):
    try:
        fn()
    except ValidationFailed as exc:
        return {i.code for i in exc.issues}
    return set()


class Units(unittest.TestCase):
    def test_utf16_units_bmp_nonbmp_and_lone_surrogate(self):
        self.assertEqual(L.utf16_units("abc"), 3)
        self.assertEqual(L.utf16_units("テスト"), 3, "BMP CJK is one unit each")
        self.assertEqual(L.utf16_units(EMOJI), 2)
        self.assertEqual(L.utf16_units("a" + EMOJI * 3), 7)
        self.assertEqual(L.utf16_units("\ud800"), 1, "a lone surrogate is the single unit CreateProcessW would receive")
        self.assertEqual(L.utf16_units(""), 0)

    def test_utf16_units_equals_real_utf16_encoding(self):
        for text in ("", "plain", "テスト", EMOJI * 5, "mixed \U0001F600 テ \u00e9 \U00010348", "q\"b\\"):
            self.assertEqual(L.utf16_units(text), len(text.encode("utf-16-le")) // 2, repr(text))

    def test_ill_formed_detection(self):
        self.assertFalse(L.has_ill_formed_utf16("fine " + EMOJI))
        for bad in ("\ud800", "x\udfff", "\udc80"):  # high, low, and a POSIX surrogateescape byte
            self.assertTrue(L.has_ill_formed_utf16(bad))

    def test_measurement_never_raises_on_surrogates(self):
        b = L.argv_budget(["exe", "a\ud800b", "\udc80"])
        self.assertTrue(b["ill_formed_utf16"])
        self.assertEqual(b["argv_bytes"], (3 + 1) + (1 + 3 + 1 + 1) + (3 + 1), "surrogatepass: 3 bytes per lone surrogate")
        self.assertEqual(L.cmdline_utf16_units(["exe", "a\ud800b"]), len("exe a\ud800b"))

    def test_cmdline_units_follow_list2cmdline_quoting(self):
        argv = ["C:\\Program Files\\ffmpeg\\ffmpeg.exe", "-i", "a b.mp4", 'say "hi"', "back\\slash\\", "テスト", EMOJI]
        text = subprocess.list2cmdline(argv)
        self.assertEqual(L.cmdline_utf16_units(argv), len(text) + text.count(EMOJI))
        self.assertGreater(len(text), sum(len(a) for a in argv) + len(argv) - 1, "quoting/escaping adds characters")
        self.assertIs(L.cmdline_chars, L.cmdline_utf16_units)


class Thresholds(unittest.TestCase):
    """Exact acceptance at the limit and rejection at limit+1, for each kind of content."""

    LIMIT = 200
    BASES = {
        "ascii": ["ffmpeg", "-n", "-i"],
        "bmp_cjk": ["ffmpeg", "テスト" * 5],
        "non_bmp": ["ffmpeg", EMOJI * 7],
        "quotes": ["ffmpeg", 'say "hi" and "bye"', 'a"b'],
        "backslashes": ["ffmpeg", "C:\\dir with space\\", "trail\\\\", 'q\\"x'],
        "mixed": ["ffmpeg", "a b\\", EMOJI + "テ" + '"', "\u00e9"],
    }

    def padded(self, base, units):
        """base plus one plain pad argument so the list2cmdline length is exactly `units`."""
        need = units - L.cmdline_utf16_units(base) - 1
        self.assertGreaterEqual(need, 1)
        return base + ["x" * need]

    def test_exact_acceptance_and_rejection_per_content_kind(self):
        lim = Limits(max_windows_cmdline_chars=self.LIMIT)
        for name, base in self.BASES.items():
            at = self.padded(base, self.LIMIT)
            over = self.padded(base, self.LIMIT + 1)
            self.assertEqual(L.cmdline_utf16_units(at), self.LIMIT, name)
            self.assertEqual(L.cmdline_utf16_units(over), self.LIMIT + 1, name)
            self.assertEqual(codes(lambda: L.check_argv_budget(at, lim, windows=True)), set(), name)
            self.assertEqual(codes(lambda: L.check_argv_budget(over, lim, windows=True)), {"limit_cmdline_windows"}, name)
            self.assertEqual(codes(lambda: L.check_argv_budget(over, lim, windows=False)), set(), f"{name}: reported, not enforced off Windows")

    def test_code_point_counting_would_undercount_non_bmp(self):
        """The old measure (len of the str) accepts what Windows rejects; UTF-16 units must not."""
        lim = Limits(max_windows_cmdline_chars=self.LIMIT)
        argv = ["ffmpeg", EMOJI * 120]  # 120 code points + 7 = 127 <= 200, but 240 + 7 = 247 UTF-16 units
        self.assertLessEqual(len(subprocess.list2cmdline(argv)), self.LIMIT)
        self.assertEqual(L.cmdline_utf16_units(argv), 247)
        self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim, windows=True)), {"limit_cmdline_windows"})

    def test_the_reported_windows_failure_shape(self):
        """One argument of 17000 emoji: python_len 17106, UTF-16 34106, UTF-8 68106, WinError 206 on the real Windows host.
        The same shape here (shorter executable) must be rejected under the default 32000-unit bound although
        both the code-point count and the UTF-8 byte count pass their own thresholds."""
        argv = ["p" * 100, EMOJI * 17000]
        self.assertEqual(len(subprocess.list2cmdline(argv)), 17101)
        self.assertEqual(L.cmdline_utf16_units(argv), 34101)
        self.assertEqual(L.argv_budget(argv)["argv_bytes"], (100 + 1) + (68000 + 1))  # UTF-8 bytes incl. one separator per argument
        self.assertLess(L.argv_budget(argv)["argv_bytes"], Limits().max_argv_bytes, "the byte bound alone does not catch it")
        self.assertEqual(codes(lambda: L.check_argv_budget(argv, Limits(), windows=True)), {"limit_cmdline_windows"})
        self.assertEqual(L.check_argv_budget(argv, Limits(), windows=False)["windows_cmdline_utf16_units"], 34101)

    def test_ill_formed_text_is_rejected_on_windows_and_reported_elsewhere(self):
        argv = ["ffmpeg", "bad\ud800name"]
        self.assertEqual(codes(lambda: L.check_argv_budget(argv, Limits(), windows=True)), {"argv_ill_formed_utf16"})
        self.assertTrue(L.check_argv_budget(argv, Limits(), windows=False)["ill_formed_utf16"])

    def test_windows_flag_follows_the_platform_by_default(self):
        argv = ["x" * 300]
        lim = Limits(max_windows_cmdline_chars=100)
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim)), {"limit_cmdline_windows"})
        with mock.patch.object(L, "is_windows", return_value=False):
            self.assertEqual(codes(lambda: L.check_argv_budget(argv, lim)), set())

    def test_byte_bound_applies_everywhere_and_counts_utf8(self):
        self.assertEqual(codes(lambda: L.check_argv_budget(["x" * 50], Limits(max_argv_bytes=50), windows=False)), {"limit_argv_bytes"})
        self.assertEqual(codes(lambda: L.check_argv_budget(["x" * 49], Limits(max_argv_bytes=50), windows=False)), set())
        self.assertEqual(L.argv_budget(["テスト"]), {"argv_bytes": 10, "windows_cmdline_utf16_units": 3, "ill_formed_utf16": False})
        self.assertEqual(L.argv_budget([EMOJI])["argv_bytes"], 5)

    def test_limit_value_is_validated_against_the_win32_hard_limit(self):
        self.assertEqual(Limits(max_windows_cmdline_chars=L.WIN_CMDLINE_HARD_LIMIT).max_windows_cmdline_chars, 32_766)
        for bad in (32_767, 0, -1, True):
            with self.assertRaises(ValueError):
                Limits(max_windows_cmdline_chars=bad)


class PlanBudget(TmpCase):
    def test_baseline_plan_hash_is_unchanged(self):
        """Pins the planned command (and thus the documented colour behaviour) to the first delivery."""
        self.assertEqual(prepare(sample_bytes()).sha256, BASELINE_PLAN_SHA256)

    def test_plan_reports_the_windows_budget_without_enforcing_it_off_windows(self):
        doc = episode_doc(total=256, clips=color_clips([1] * 256), captions=[])
        with no_process(), mock.patch.object(L, "is_windows", return_value=False):
            p = prepare(to_bytes(doc))
        b = p.observations["argv_budget"]
        self.assertFalse(b["fits_windows_limit"])
        self.assertGreater(b["estimated_windows_cmdline_utf16_units"], 32_000)
        self.assertEqual(b["windows_hard_limit_utf16_units"], 32_766)
        self.assertFalse(b["ill_formed_utf16"])

    def test_plan_rejects_the_same_episode_on_windows(self):
        doc = episode_doc(total=256, clips=color_clips([1] * 256), captions=[])
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(codes(lambda: prepare(to_bytes(doc))), {"limit_cmdline_windows"})
        small = episode_doc(total=50, clips=color_clips([1] * 50), captions=[])
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertTrue(prepare(to_bytes(small)).observations["argv_budget"]["fits_windows_limit"])

    def test_caller_can_tighten_the_windows_bound(self):
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(codes(lambda: prepare(sample_bytes(), limits=Limits(max_windows_cmdline_chars=500))), {"limit_cmdline_windows"})

    def _many_file_plan(self, n):
        assets = self.mkdir("assets")
        for i in range(n):
            with open(os.path.join(assets, f"clip{i:03d}.mp4"), "wb") as fh:
                fh.write(b"x")
        clips = [{"id": f"c{i}", "start_frame": i, "duration_frames": 1, "source": {"kind": "file", "path": f"clip{i:03d}.mp4"}} for i in range(n)]
        return assets, to_bytes(episode_doc(total=n, clips=clips, captions=[]))

    def test_estimate_equals_the_real_argv_so_there_is_no_per_file_slack(self):
        """Regression for the first delivery's 1024-byte-per-file slack, independent of how long the host TEMP path is:
        the estimate must equal the argv materialize() really builds for the same roots, byte for byte."""
        n = 100
        assets, ep = self._many_file_plan(n)
        with no_process(), mock.patch.object(L, "is_windows", return_value=False):
            p = prepare(ep, asset_root=assets)
        obs = p.observations
        exe = obs["tools_located"]["ffmpeg"]["path"] or "x" * 260
        real = materialize(p, ffmpeg_path=exe, asset_root=os.path.realpath(assets), run_dir="x" * 260)
        self.assertEqual(obs["argv_budget"]["estimated_argv_bytes"], L.argv_budget(real)["argv_bytes"])
        self.assertEqual(obs["argv_budget"]["estimated_windows_cmdline_utf16_units"], L.cmdline_utf16_units(real))
        # a limit exactly at the real size passes; one byte less rejects -> the estimate is tight, not padded by ~1 KiB per input
        tight = L.argv_budget(real)["argv_bytes"]
        with no_process(), mock.patch.object(L, "is_windows", return_value=False):
            prepare(ep, asset_root=assets, limits=Limits(max_argv_bytes=tight))
            self.assertEqual(codes(lambda: prepare(ep, asset_root=assets, limits=Limits(max_argv_bytes=tight - 1))), {"limit_argv_bytes"})

    def test_estimate_scales_exactly_with_root_length_without_creating_long_paths(self):
        """Long-root arithmetic is pure string math: no directory of that length is ever created (Windows long paths stay off)."""
        assets, ep = self._many_file_plan(5)
        template = prepare(ep, asset_root=assets).canonical["ffmpeg"]["argv"]
        inputs = sum(1 for el in template if isinstance(el, dict) and "asset" in el)
        self.assertEqual(inputs, 5)
        short = L.argv_budget(estimate_argv(template, "r" * 10, "o" * 10, "e"))
        long_ = L.argv_budget(estimate_argv(template, "r" * 310, "o" * 10, "e"))
        self.assertEqual(long_["argv_bytes"] - short["argv_bytes"], 300 * inputs)
        self.assertEqual(long_["windows_cmdline_utf16_units"] - short["windows_cmdline_utf16_units"], 300 * inputs)
        longer_run = L.argv_budget(estimate_argv(template, "r" * 10, "o" * 110, "e"))
        self.assertEqual(longer_run["argv_bytes"] - short["argv_bytes"], 100, "the run directory appears once, in the output argument")
        unknown = L.argv_budget(estimate_argv(template, None, None, None))
        assumed = L.argv_budget(estimate_argv(template, "x" * 260, "x" * 260, "x" * 260))
        self.assertEqual(unknown, assumed, "unknown roots and tool path are assumed to be 260 characters each")


if __name__ == "__main__":
    unittest.main()
