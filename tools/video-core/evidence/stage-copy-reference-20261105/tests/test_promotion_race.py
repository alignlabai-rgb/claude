"""Regression for the promotion boundary: a writer that creates the FINAL name between the helper's last freshness check and its promotion step must not be overwritten.
The wrapper creates the sentinel with exclusive create ('xb') immediately BEFORE calling the original os.rename / os.link, i.e. inside the exact window.  Owned temp directories only; POSIX/regular files.
Run:  python -B -W error::ResourceWarning -m unittest discover -s tests -v"""
import contextlib
import errno
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_stage_copy as base  # noqa: E402

M, PIN, DUMMY, put, get = base.M, base.PIN, base.DUMMY, base.put, base.get
SENTINEL = b"OTHER-WRITER-FINAL-SENTINEL"


def racing(orig, log):
    def wrapper(src, dst, *a, **k):
        with open(dst, "xb") as fh:                         # another writer wins the final name inside the window
            fh.write(SENTINEL)
        log.append(os.path.basename(dst))
        return orig(src, dst, *a, **k)
    return wrapper


@contextlib.contextmanager
def race_patched():
    log = []
    with mock.patch.object(os, "rename", racing(os.rename, log)), mock.patch.object(os, "link", racing(os.link, log)):
        yield log


class PromotionRace(unittest.TestCase):
    def test_other_writers_final_is_kept_and_the_run_is_not_complete(self):
        with base.world() as (_, src, dst), race_patched() as log:
            res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
            self.assertEqual(log, ["stem_copy.wav"], "the wrapper must have run inside the window")
            self.assertEqual(get(os.path.join(dst, "stem_copy.wav")), SENTINEL, "the other writer's final was overwritten")
            self.assertEqual((res["outcome"], res["reason"], res["completed"]), ("FAILED_VERIFICATION", "PROMOTION_BLOCKED", False))
            self.assertEqual(sorted(os.listdir(dst)), ["stem_copy.wav", "stem_copy.wav.partial"])
            self.assertEqual(get(os.path.join(dst, "stem_copy.wav.partial")), DUMMY)                  # our own verified partial is kept, never promoted
            self.assertEqual((res["residue"]["final_exists"], res["residue"]["partial_exists"], res["residue"]["partial_bytes"]), (True, True, 1080))
            self.assertEqual(res["promotion"]["completed"], False)

    def test_normal_promotion_removes_only_our_partial_and_leaves_one_name(self):
        with base.world() as (_, src, dst):
            res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
            self.assertEqual((res["outcome"], res["completed"]), ("INTEGRITY_MATCH", True))
            self.assertEqual(sorted(os.listdir(dst)), ["stem_copy.wav"])
            self.assertEqual(get(os.path.join(dst, "stem_copy.wav")), DUMMY)
            self.assertEqual(os.stat(os.path.join(dst, "stem_copy.wav")).st_nlink, 1)               # no second hard link left behind
            self.assertEqual(res["promotion"]["partial_removed"], True)
            self.assertEqual(res["promotion"]["method"], "hardlink_create_only" if os.name == "posix" else "rename_no_overwrite")

    @unittest.skipUnless(os.name == "posix", "hard-link promotion is POSIX-only; no link is attempted elsewhere")
    def test_hardlink_unavailable_holds_and_keeps_the_partial(self):
        def no_link(*a, **k):
            raise OSError(errno.EPERM, "hard links not permitted on this filesystem (simulated)")
        with base.world() as (_, src, dst), mock.patch.object(os, "link", no_link):
            res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
            self.assertEqual((res["outcome"], res["reason"], res["completed"]), ("HOLD", "PROMOTION_UNAVAILABLE", False))
            self.assertEqual(sorted(os.listdir(dst)), ["stem_copy.wav.partial"])
            self.assertEqual(get(os.path.join(dst, "stem_copy.wav.partial")), DUMMY)

    def test_platform_without_a_known_safe_promotion_holds(self):
        with base.world() as (_, src, dst), mock.patch.object(M, "_promotion_method", lambda: None):
            res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
            self.assertEqual((res["outcome"], res["reason"], res["completed"]), ("HOLD", "PROMOTION_UNAVAILABLE", False))
            self.assertEqual(sorted(os.listdir(dst)), ["stem_copy.wav.partial"])


def tamper_at_final_stat(action):
    """Wrap os.stat: the first time it is asked about THIS call's final path (the helper's last size check) run `action(path)` and then the original stat."""
    orig, done = os.stat, []

    def wrapper(path, *a, **k):
        if not done and os.path.basename(os.fspath(path)) == "stem_copy.wav":
            done.append(1)
            action(os.fspath(path))
        return orig(path, *a, **k)
    return wrapper, done


class PostPromotionCheck(unittest.TestCase):
    """The final size comparison already recorded MISMATCH but the outcome still said INTEGRITY_MATCH / completed=True.  A mismatch or unreadable final must never be reported as complete.
    (Only the size boundary is covered; same-size rewrites after verification and authenticity are NOT claimed.)"""

    def run_with(self, action):
        wrapper, done = tamper_at_final_stat(action)
        with base.world() as (_, src, dst), mock.patch.object(os, "stat", wrapper):
            res = M.stage_copy(src, "stem.wav", PIN, dst, "stem_copy.wav", chunk_size=64)
            self.assertEqual(done, [1], "the tamper wrapper must have run at the final size check")
            return res, sorted(os.listdir(dst))

    def test_final_truncated_after_promotion_is_not_success(self):
        def truncate(path):
            with open(path, "r+b") as fh:
                fh.truncate(10)
        res, listing = self.run_with(truncate)
        last = res["checks"][-1]
        self.assertEqual((last["name"], last["outcome"], last["expected"], last["observed"]), ("promoted_file_bytes_equal_pin", "MISMATCH", 1080, 10))
        self.assertEqual((res["outcome"], res["reason"], res["completed"]), ("FAILED_VERIFICATION", "PROMOTED_FILE_MISMATCH", False))
        self.assertIn("stem_copy.wav", listing)                       # this call's own final is left in place and recorded, not deleted or "repaired"
        self.assertEqual(res["residue"]["final_exists"], True)

    def test_final_removed_after_promotion_is_not_success(self):
        res, listing = self.run_with(os.unlink)                       # the test removes the file this call created, to simulate its disappearance
        last = res["checks"][-1]
        self.assertEqual((last["name"], last["outcome"], last["observed"]), ("promoted_file_bytes_equal_pin", "MISMATCH", None))
        self.assertEqual((res["outcome"], res["reason"], res["completed"]), ("FAILED_VERIFICATION", "PROMOTED_FILE_UNREADABLE", False))
        self.assertNotIn("stem_copy.wav", listing)
        self.assertEqual(res["residue"]["final_exists"], False)


if __name__ == "__main__":
    unittest.main()
