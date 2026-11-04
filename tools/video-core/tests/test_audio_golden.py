"""Golden cross-check of the evidence stem: the committed manifest, regenerated assets and the engine must reproduce the pinned hashes.
The tones here come from tests/audio_fixtures.py; the evidence script (evidence/stage-b-audio-stem/make_synthetic_assets.py) has its own copy.
Agreement of the asset SHA-256 values proves the two independent tone implementations are byte-identical."""

import json
import os
import unittest

from tests.audio_fixtures import tone, wav_bytes, write, sha
from tests.helpers import ROOT, TmpCase, rb
from video_core.audiomix import mix as mixmod
from video_core.audiomix import prepare_plan

EVIDENCE = os.path.join(ROOT, "evidence", "stage-b-audio-stem")
PLAN_SHA256 = "a897549b8c56c5b474952420059db3adfda8aaf458093ee49373fd82eb90228e"
STEM_SHA256 = "68f8180e0630509d8743c11c38de674481e3f711c54c12bffa996c0af1c8ba88"


class Golden(TmpCase):
    def setUp(self):
        super().setUp()
        self.manifest = rb(os.path.join(EVIDENCE, "manifest.synthetic.json"))
        self.assets = self.mkdir("assets")
        write(os.path.join(self.assets, "tone_square_480.wav"), wav_bytes(tone("square", 480, 3200, 8000)))
        write(os.path.join(self.assets, "tone_triangle_600.wav"), wav_bytes(tone("triangle", 600, 3200, 6000)))

    def test_regenerated_assets_match_the_committed_manifest_hashes(self):
        for a in json.loads(self.manifest)["assets"]:
            self.assertEqual(sha(rb(os.path.join(self.assets, a["path"]))), a["sha256"], a["id"])

    def test_plan_hash_is_pinned(self):
        plan = prepare_plan(self.manifest, asset_root=self.assets)
        self.assertEqual((plan.status, plan.sha256), ("READY", PLAN_SHA256))
        self.assertEqual(plan.total_samples, 4800)
        self.assertEqual(rb(os.path.join(EVIDENCE, "plan.canonical.json")), plan.canonical_json, "the committed canonical plan is exactly what the planner produces")

    def test_mixed_stem_hash_is_pinned_and_matches_the_committed_receipt(self):
        out = self.mkdir("out")
        r = mixmod.mix(self.manifest, asset_root=self.assets, output_root=out, output_name="golden")
        self.assertEqual(r["status"], mixmod.S_VERIFIED)
        got = sha(rb(os.path.join(out, "golden", "stem.wav")))
        self.assertEqual(got, STEM_SHA256)
        committed = json.loads(rb(os.path.join(EVIDENCE, "receipt.stem-demo.json")))
        self.assertEqual(next(o["sha256"] for o in committed["outputs"] if o["name"] == "stem.wav"), STEM_SHA256)
        self.assertEqual(committed["plan_sha256"], PLAN_SHA256)
        self.assertEqual(json.loads(rb(os.path.join(EVIDENCE, "independent_verification.json")))["sha256"], STEM_SHA256)


if __name__ == "__main__":
    unittest.main()
