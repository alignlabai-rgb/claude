"""Short checks for the producer-shape compatibility audit: the committed source files are the exact ones recorded, nothing is READY, and each case reaches the stage it claims to show.
Run: python3 -m unittest discover -s evidence/producer-shape-compatibility-20261105 -p "test_*.py"  (from tools/video-core)"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_producer_shape as A  # noqa: E402

# SHA-256 of the committed producer files exactly as they were when this audit was written (they must never change).
PINNED = {
    "video_receipt_6s": "tools/video-core/evidence/receipt.synthetic-6s.json",
    "video_plan_6s": "tools/video-core/evidence/plan.canonical.json",
    "audio_receipt_stem": "tools/video-core/evidence/stage-b-audio-stem/receipt.stem-demo.json",
    "audio_plan_stem": "tools/video-core/evidence/stage-b-audio-stem/plan.canonical.json",
    "audio_manifest_stem": "tools/video-core/evidence/stage-b-audio-stem/manifest.synthetic.json",
    "audio_stem_independent": "tools/video-core/evidence/stage-b-audio-stem/independent_verification.json",
    "avmux_manifest_mov": "tools/video-core/evidence/avmux-synthetic/mov.manifest.json",
    "avmux_receipt_mov": "tools/video-core/evidence/avmux-synthetic/mov.receipt.json",
}
CASES = {c["id"]: c for c in A.build_cases()}


def codes(c):
    return {r["code"] for r in c["reasons"]}


class Audit(unittest.TestCase):
    def test_sources_are_the_recorded_files_and_the_blob_ids_are_git_ids(self):
        table = {s["name"]: s for s in A.source_table()}
        self.assertEqual(set(table), set(PINNED))
        for name, path in PINNED.items():
            self.assertEqual(table[name]["path"], path)
            self.assertEqual(table[name]["sha256"], hashlib.sha256(A.RAW[name]).hexdigest())
        self.assertEqual(A.git_blob(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")  # git's id of the empty blob
        self.assertEqual(A.PLAN_FILE_SHA_VIDEO, "4bc20b3fd703f466" + A.PLAN_FILE_SHA_VIDEO[16:])  # the baseline plan hash
        self.assertEqual(A.PLAN_FILE_SHA_AUDIO[:16], "a897549b8c56c5b4")

    def test_no_case_is_ready_and_nothing_was_rewritten_to_make_one_ready(self):
        self.assertEqual({c["status"] for c in CASES.values()} & {"READY"}, set())
        self.assertEqual(sorted(CASES), ["A1", "A2", "M1", "P1", "S1", "S2", "V1", "X1", "X2", "X3"])
        # the audio packets carry the committed values byte for byte (status, counts, hashes)
        plan = A.audio_plan_packet(A.ident("A"))["response"]["plan"]
        self.assertEqual(plan, A.DOC["audio_plan_stem"])
        self.assertEqual(A.audio_receipt_packet(A.ident("A"))["response"], A.DOC["audio_receipt_stem"])
        self.assertEqual(A.video_receipt_packet(A.ident("A"))["response"], A.DOC["video_receipt_6s"])

    def test_real_shapes_reach_and_pass_their_own_stages(self):
        for cid in ("A1", "A2", "M1", "X3"):
            self.assertEqual((CASES[cid]["checks"]["AUDIO_PLAN"], CASES[cid]["checks"]["AUDIO_RECEIPT"]), ("PASS", "PASS"), cid)
        self.assertEqual(CASES["V1"]["checks"]["VIDEO_RECEIPT"], "PASS")
        self.assertEqual(CASES["M1"]["checks"]["MANIFEST_VIEW"], "PASS")
        self.assertEqual(CASES["M1"]["pins_compared"]["stem_sha256"], 2)  # receipt stem output + manifest audio sha, each against the owner pin

    def test_the_committed_plan_has_no_binding_so_v0_cannot_be_satisfied_and_is_not_relaxed(self):
        self.assertIn("BINDING_UNRESOLVED", codes(CASES["A1"]))
        self.assertIn("BINDING_UNPROVEN", codes(CASES["A2"]))
        self.assertTrue(all("provenance_ref" not in e for e in A.DOC["audio_plan_stem"]["events"]))

    def test_missing_stages_stay_hold(self):
        for cid in ("A1", "A2", "V1", "M1", "X3"):
            self.assertEqual(CASES[cid]["status"], "HOLD", cid)
        self.assertIn("REQUIRED_RECEIPT_ABSENT", codes(CASES["X3"]))
        self.assertNotIn("VIDEO_RECEIPT", [k for k, v in CASES["X3"]["checks"].items() if v == "PASS"])

    def test_different_demonstrations_are_never_one_job(self):
        self.assertEqual(CASES["X1"]["status"], "REJECTED")
        self.assertIn("IDENTITY_MISMATCH", codes(CASES["X1"]))
        self.assertTrue({"COUNT_MISMATCH", "PIN_MISMATCH"} <= codes(CASES["X2"]))
        self.assertEqual(A.DOC["video_receipt_6s"]["readback"]["comparison"]["checks"][6]["expected"], 180)
        self.assertEqual(A.DOC["audio_plan_stem"]["timeline"]["total_frames"], 3)

    def test_wrong_artefact_in_a_slot_and_wrong_owner_pin_are_rejected(self):
        self.assertEqual(CASES["S1"]["status"], "REJECTED")
        self.assertIn("SCHEMA_UNKNOWN", codes(CASES["S1"]))
        self.assertEqual(CASES["S2"]["checks"]["AUDIO_PLAN"], "FAIL")
        self.assertIn("SCHEMA_UNKNOWN", codes(CASES["S2"]))
        self.assertEqual(CASES["P1"]["status"], "REJECTED")
        self.assertEqual(sum(r["code"] == "PIN_MISMATCH" for r in CASES["P1"]["reasons"]), 3)

    def test_deterministic(self):
        self.assertEqual([c["reasons"] for c in A.build_cases()], [c["reasons"] for c in A.build_cases()])


if __name__ == "__main__":
    unittest.main()
