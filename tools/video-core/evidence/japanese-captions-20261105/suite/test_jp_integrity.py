import os
import subprocess
import unittest

from common import EVID  # noqa: F401
import jpcap_eval as J


@unittest.skipUnless(os.path.isdir(os.path.join(J.REPO, ".git")), "not a git checkout (clean export): history proof not applicable")
class SourceAndHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        have = subprocess.run(["git", "-C", J.REPO, "cat-file", "-e", J.BASE_COMMIT + "^{commit}"], capture_output=True).returncode == 0
        if not have:
            raise unittest.SkipTest("base commit not present in this checkout")
        cls.rep = J.integrity()

    def test_base_commit_is_an_ancestor_and_nothing_old_changed(self):
        self.assertTrue(self.rep["base_is_ancestor_of_head"])
        self.assertEqual(self.rep["changed_since_base"]["outside_new_evidence_dir_or_not_added"], [])
        self.assertTrue(self.rep["old_tree_identical_blob_ids"])
        self.assertTrue(self.rep["production_source_files_identical_to_base"])

    def test_baseline_plan_hash_is_reproduced_by_the_unmodified_planner(self):
        self.assertTrue(self.rep["baseline_plan_sha256"]["ok"])
        self.assertEqual(self.rep["baseline_plan_sha256"]["recomputed"], J.BASELINE_PLAN_SHA256)
        self.assertEqual(self.rep["baseline_plan_sha256"]["old_plan_canonical_file_sha256"], J.BASELINE_PLAN_SHA256)

    def test_every_old_file_still_has_the_base_bytes(self):
        man = self.rep.get("baseline_manifest")
        self.assertIsNotNone(man, "baseline_tracked_files.sha256 missing")
        self.assertEqual(man["mismatches"], [])
        self.assertEqual(man["files"], self.rep["base_file_count"])

    def test_no_media_file_is_tracked_in_the_evidence_directory(self):
        out = subprocess.run(["git", "-C", J.REPO, "ls-files", "--", J.EVIDENCE_REL], capture_output=True, text=True, check=True).stdout.split()
        bad = [p for p in out if p.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".partial", ".wav"))]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
