"""The validator is a stdlib mirror of schemas/episode.schema.json (no jsonschema dependency).
These tests read the supplied schema and fail if the bounds enforced in code drift from it."""

import json
import os
import re
import unittest

from tests.helpers import ROOT, color_clips, episode_doc, mutated, sample_doc, to_bytes
from video_core import Limits, load_episode
from video_core.errors import ValidationFailed
from video_core import limits as limits_mod
from video_core.episode import _COLOR, _ID

with open(os.path.join(ROOT, "schemas", "episode.schema.json"), encoding="utf-8") as fh:
    SCHEMA = json.load(fh)
P = SCHEMA["properties"]


def rejected(doc, limits=Limits()):
    try:
        load_episode(to_bytes(doc), limits)
    except ValidationFailed:
        return True
    return False


class Mirror(unittest.TestCase):
    def test_numeric_ceilings_match_the_schema(self):
        self.assertEqual(P["total_frames"]["maximum"], limits_mod.SCHEMA_MAX_FRAMES)
        self.assertEqual(P["canvas"]["properties"]["width"]["maximum"], limits_mod.SCHEMA_MAX_DIMENSION)
        self.assertEqual(P["clips"]["maxItems"], limits_mod.SCHEMA_MAX_CLIPS)
        self.assertEqual(P["captions"]["maxItems"], limits_mod.SCHEMA_MAX_CAPTIONS)
        self.assertEqual(P["captions"]["items"]["properties"]["text"]["maxLength"], limits_mod.SCHEMA_MAX_CAPTION_CHARS)

    def test_patterns_match_the_schema(self):
        self.assertEqual(P["clips"]["items"]["properties"]["id"]["pattern"], "^[a-zA-Z0-9_-]{1,64}$")
        self.assertEqual(_ID.pattern, "[a-zA-Z0-9_-]{1,64}")
        self.assertEqual(P["clips"]["items"]["properties"]["source"]["oneOf"][0]["properties"]["value"]["pattern"], "^#[0-9a-fA-F]{6}$")
        self.assertEqual(_COLOR.pattern, "#[0-9a-fA-F]{6}")

    def test_schema_minimums_and_maximums_are_enforced_at_the_boundary(self):
        fps = P["fps"]["properties"]
        for key in ("n", "d"):
            lo, hi = fps[key]["minimum"], fps[key]["maximum"]
            ok = lambda v, k=key: not rejected(mutated(lambda d: d["fps"].update({k: v})), Limits(max_fps=60_000, max_duration_seconds=10**9, max_pixel_volume=10**13))
            self.assertTrue(ok(lo), f"fps.{key}={lo} must be accepted")
            self.assertTrue(ok(hi), f"fps.{key}={hi} must be accepted")
            self.assertFalse(ok(lo - 1))
            self.assertFalse(ok(hi + 1))
        cv = P["canvas"]["properties"]["width"]
        self.assertEqual((cv["minimum"], cv["multipleOf"]), (16, 2))
        self.assertFalse(rejected(episode_doc(width=16, height=16)))
        self.assertTrue(rejected(episode_doc(width=14, height=16)))
        self.assertTrue(rejected(episode_doc(width=18, height=17)))
        self.assertFalse(rejected(episode_doc(total=18000, clips=color_clips([18000]), captions=[], width=16, height=16)))

    def test_required_and_additional_properties(self):
        self.assertEqual(set(SCHEMA["required"]), {"version", "fps", "canvas", "total_frames", "clips", "captions"})
        self.assertFalse(SCHEMA["additionalProperties"])
        for key in SCHEMA["required"]:
            self.assertTrue(rejected(mutated(lambda d, k=key: d.pop(k))), key)
        self.assertTrue(rejected(mutated(lambda d: d.update(surprise=1))))

    def test_caption_end_minimum_and_clip_minimums(self):
        self.assertEqual(P["captions"]["items"]["properties"]["end_frame"]["minimum"], 1)
        self.assertEqual(P["clips"]["items"]["properties"]["duration_frames"]["minimum"], 1)
        self.assertEqual(P["clips"]["items"]["properties"]["start_frame"]["minimum"], 0)
        self.assertEqual(P["clips"]["minItems"], 1)
        self.assertTrue(rejected(mutated(lambda d: d["captions"][0].update(end_frame=0, start_frame=0))))

    def test_the_supplied_example_validates_against_the_code(self):
        self.assertFalse(rejected(sample_doc()))


if __name__ == "__main__":
    unittest.main()
