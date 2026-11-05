"""Short checks over the RECORDED same-job chain (reads evidence only; no media tool, no process).  python3 -m unittest discover -s evidence/same-job-synthetic-20261105 -p "test_*.py"  (from tools/video-core)"""
import base64
import gzip
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_candidate as C  # noqa: E402


def J(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
        return json.load(fh)


class Chain(unittest.TestCase):
    def test_every_recorded_step_exited_zero_with_empty_stderr_and_the_chain_completed(self):
        res = J("run/chain_result.json")
        self.assertEqual(res["outcome"], "COMPLETED")
        self.assertEqual(len(res["steps"]), 17)
        for s in res["steps"]:
            self.assertEqual((s["exit_code"], s["stderr_bytes"]), (0, 0), s["step"])

    def test_real_producer_statuses_are_untouched(self):
        self.assertEqual(J("run/receipts/audio.receipt.json")["status"], "MIXED_VERIFIED")
        self.assertEqual(J("run/receipts/video.receipt.json")["status"], "READBACK_MATCH")
        self.assertEqual(J("run/receipts/mux.receipt.json")["status"], "MUXED_VERIFIED_SAMPLE_EXACT")
        for n in ("audio", "video", "mux"):
            self.assertFalse(J(f"run/receipts/{n}.receipt.json")["quality_evaluated"])

    def test_one_geometry_everywhere(self):
        self.assertTrue(C.same_job_table()[0]["all_equal"])
        rows = {r["name"]: r for r in C.same_job_table()}
        self.assertTrue(all(r["all_equal"] for r in rows.values()), [n for n, r in rows.items() if not r["all_equal"]])
        self.assertEqual(set(rows["frames"]["values"].values()), {30})
        self.assertEqual(set(rows["samples"]["values"].values()), {48000})

    def test_file_byte_hashes_agree_between_hashlib_and_sha256sum_and_media_facts(self):
        h = J("run/hashes.json")
        self.assertTrue(all(v["agree"] for v in h.values()))
        self.assertEqual(h["stem.wav"]["sha256"], h["tone_input.wav"]["sha256"], "a 48 000-sample mono tone mixed 1:1 at gain 1 is byte-identical to its source")
        for n in ("output.mp4", "stem.wav", "output.mov"):
            with open(os.path.join(HERE, f"run/media-facts/{n}.decode.txt")) as fh:
                self.assertEqual(fh.read(), "exit=0\nstderr_bytes=0\n", n)

    def test_candidate_positive_and_single_pin_negative(self):
        exp, obs = C.packets()
        pos = C.summarize(C.ca.evaluate(exp, obs, C.ca.PROFILE_V0_UNREVIEWED))
        self.assertEqual((pos["status"], pos["reasons"]), ("READY", []))
        self.assertTrue(all(v == "PASS" for v in pos["checks"].values()))
        self.assertIn("synthetic snapshot", pos["scope_of_status"])
        neg_exp = json.loads(json.dumps(exp))
        neg_exp["pins"]["stem_sha256"] = C.H["output.mp4"]["sha256"]
        neg = C.summarize(C.ca.evaluate(neg_exp, obs, C.ca.PROFILE_V0_UNREVIEWED))
        self.assertEqual((neg["status"], sorted(r["code"] for r in neg["reasons"])), ("REJECTED", ["PIN_MISMATCH", "PIN_MISMATCH"]))
        self.assertEqual(C.ca.PROFILE_V0_UNREVIEWED["treated_warning_codes"], [])  # the profile was not relaxed

    def test_recorded_comparison_is_what_the_script_computes(self):
        rec = J("comparison.json")
        exp, obs = C.packets()
        self.assertEqual(rec["candidate_positive"], C.summarize(C.ca.evaluate(exp, obs, C.ca.PROFILE_V0_UNREVIEWED)))
        self.assertEqual(rec["same_job_pin_table"], C.same_job_table())

    def test_transport_decodes_to_the_recorded_originals(self):
        man = J("transport/manifest.json")["files"]
        h = J("run/hashes.json")
        self.assertEqual({m["name"] for m in man}, {"output.mp4", "stem.wav", "output.mov"})
        for m in man:
            with open(os.path.join(HERE, "transport", m["file"]), "rb") as fh:
                text = fh.read()
            self.assertEqual(hashlib.sha256(text).hexdigest(), m["encoded_file_sha256"])
            data = base64.b64decode(text)
            data = gzip.decompress(data) if m["encoding"].startswith("gzip") else data
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (m["bytes"], m["sha256"]))
            self.assertEqual(m["sha256"], h[m["name"]]["sha256"])


if __name__ == "__main__":
    unittest.main()
