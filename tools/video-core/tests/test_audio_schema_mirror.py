"""The audio validator is a stdlib mirror of schemas/audio-manifest.schema.json. These tests fail if enums, bounds or patterns drift apart."""

import json
import os
import unittest

from tests.audio_fixtures import asset, dumps, event_use, manifest, tone, wav_bytes
from tests.helpers import ROOT
from video_core.audiomix import manifest as M
from video_core.audiomix import limits as L
from video_core.audiomix.manifest import load_manifest
from video_core.errors import ValidationFailed

with open(os.path.join(ROOT, "schemas", "audio-manifest.schema.json"), encoding="utf-8") as fh:
    S = json.load(fh)
P = S["properties"]
EV = P["events"]["items"]["properties"]


class Mirror(unittest.TestCase):
    def test_enums_match(self):
        self.assertEqual(tuple(EV["state"]["enum"]), M.STATES)
        self.assertEqual(tuple(EV["channel_rule"]["enum"]), M.CHANNEL_RULES)
        self.assertEqual(tuple(P["policy"]["properties"]["gap"]["enum"]), M.GAP_POLICIES)
        self.assertEqual(tuple(P["policy"]["properties"]["overlap"]["enum"]), M.OVERLAP_POLICIES)
        self.assertEqual(tuple(P["policy"]["properties"]["clipping"]["enum"]), M.CLIPPING_POLICIES)
        self.assertEqual(P["policy"]["properties"]["overlap"]["default"], "REJECT")
        self.assertEqual(P["policy"]["properties"]["clipping"]["default"], "REJECT")

    def test_numeric_bounds_match(self):
        self.assertEqual(P["timeline"]["properties"]["total_frames"]["maximum"], L.SCHEMA_MAX_FRAMES)
        self.assertEqual(P["events"]["maxItems"], L.SCHEMA_MAX_EVENTS)
        self.assertEqual(P["assets"]["maxItems"], L.SCHEMA_MAX_ASSETS)
        self.assertEqual(P["output"]["properties"]["sample_rate"]["const"], L.SAMPLE_RATE)
        gain = EV["gain"]["properties"]
        self.assertEqual((gain["den"]["maximum"], gain["num"]["maximum"]), (M.GAIN_MAX_DEN, M.GAIN_MAX_RATIO * M.GAIN_MAX_DEN))
        self.assertEqual((P["timeline"]["properties"]["fps"]["properties"]["n"]["maximum"], P["timeline"]["properties"]["fps"]["properties"]["d"]["maximum"]), (60000, 1001))

    def test_patterns_match(self):
        self.assertEqual(S["$defs"]["id"]["pattern"], "^" + M._ID.pattern + "$")
        self.assertEqual(EV["role"]["pattern"], "^" + M._ROLE.pattern + "$")
        self.assertEqual(P["assets"]["items"]["properties"]["sha256"]["pattern"], "^" + M._SHA.pattern + "$")
        self.assertEqual(S["$defs"]["opaque"]["maxLength"], 128)

    def test_required_fields_match(self):
        self.assertEqual(set(S["required"]), {"manifest_version", "timeline", "output", "scope", "policy", "assets", "events"})
        self.assertEqual(P["policy"]["required"], ["gap"], "the gap policy is the only mandatory policy")
        self.assertFalse(S["additionalProperties"])

    def test_per_state_requirements_in_the_schema_are_enforced_by_the_validator(self):
        a = asset("a", "a.wav", wav_bytes(tone("square", 480, 100)))
        rules = {c["if"]["properties"]["state"].get("const") or tuple(c["if"]["properties"]["state"]["enum"]): c["then"] for c in S["properties"]["events"]["items"]["allOf"]}
        self.assertEqual(set(rules["USE"]["required"]), {"asset", "source_in_sample", "source_out_sample", "start_frame", "end_frame"})
        for field in rules["USE"]["required"]:
            ev = event_use("e", "a", 0, 1600, 0, 1)
            del ev[field]
            with self.assertRaises(ValidationFailed, msg=field):
                load_manifest(dumps(manifest([ev], [a], total=1)))
        for state in ("INTENTIONAL_SILENCE", "OMIT"):
            for field in rules[state]["required"]:
                ev = {"id": "x", "source_id": "x", "state": state, "decision_ref": "d", "start_frame": 0, "end_frame": 1}
                del ev[field]
                with self.assertRaises(ValidationFailed, msg=(state, field)):
                    load_manifest(dumps(manifest([ev], [], total=1)))
        banned = rules[("OMIT", "INTENTIONAL_SILENCE", "MISSING", "UNKNOWN", "PROXY")]["not"]["anyOf"]
        self.assertEqual({list(b["required"])[0] for b in banned}, {"asset", "source_in_sample", "source_out_sample", "gain", "channel_rule"})

    def test_there_is_no_speech_normalization_resampling_or_stretching_vocabulary(self):
        text = json.dumps(S).lower()
        keys = [k.lower() for k in list(P["policy"]["properties"]) + list(EV)]
        for word in ("tts", "speech", "voice", "normaliz", "resampl", "stretch", "loudness", "lufs"):
            self.assertNotIn(word, text)
            self.assertFalse(any(word in k for k in keys), word)


if __name__ == "__main__":
    unittest.main()
