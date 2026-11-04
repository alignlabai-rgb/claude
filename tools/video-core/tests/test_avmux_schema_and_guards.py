import json
import os
import unittest

from tests.audio_fixtures import dumps
from tests.avmux_fakes import av_manifest
from tests.helpers import ROOT
from video_core import Limits
from video_core.avmux import manifest as M
from video_core.avmux import plan as P
from video_core.avmux.manifest import load_manifest
from video_core.errors import ValidationFailed

with open(os.path.join(ROOT, "schemas", "avmux-manifest.schema.json"), encoding="utf-8") as fh:
    S = json.load(fh)
PR = S["properties"]


class SchemaMirror(unittest.TestCase):
    def test_enums_bounds_and_requireds_match_the_validator(self):
        self.assertEqual(tuple(PR["route"]["enum"]), M.ROUTES)
        self.assertEqual(tuple(PR["video"]["properties"]["state"]["enum"]), M.INPUT_STATES)
        self.assertEqual(tuple(PR["audio"]["properties"]["state"]["enum"]), M.INPUT_STATES)
        self.assertEqual(PR["video"]["properties"]["declared"]["properties"]["codec"]["const"], M.SUPPORTED_VIDEO_CODEC)
        d = PR["video"]["properties"]["declared"]["properties"]
        self.assertEqual((d["total_frames"]["maximum"], d["fps"]["properties"]["n"]["maximum"], d["fps"]["properties"]["d"]["maximum"], d["width"]["maximum"]), (18000, 60000, 1001, 7680))
        self.assertEqual(PR["audio"]["properties"]["declared"]["properties"]["sample_rate"]["const"], 48000)
        self.assertEqual(set(S["required"]), {"avmux_version", "route", "selection", "video", "audio"})
        self.assertEqual(set(PR["video"]["required"]), {"state", "path", "sha256", "declared"})
        self.assertFalse(S["additionalProperties"])
        for key in S["required"]:
            doc = av_manifest()
            doc.pop(key)
            with self.assertRaises(ValidationFailed):
                load_manifest(dumps(doc), Limits())

    def test_no_option_exists_for_hiding_mismatches(self):
        """The manifest vocabulary has no way to ask for -shortest, resampling, stretching, trimming or padding."""
        keys = {k.lower() for k in list(PR) + list(PR["video"]["properties"]) + list(PR["audio"]["properties"]) + list(PR["video"]["properties"]["declared"]["properties"])}
        for word in ("shortest", "resample", "stretch", "trim", "pad", "normalize", "tts", "voice", "speech", "offset", "delay"):
            self.assertFalse(any(word in k for k in keys), word)
        self.assertEqual(set(PR), {"avmux_version", "route", "selection", "video", "audio"})

    def test_tolerance_is_one_aac_frame(self):
        self.assertEqual((P.AAC_FRAME_SAMPLES, P.AAC_TOLERANCE_SAMPLES), (1024, 1024))


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Guards(unittest.TestCase):
    def test_avmux_package_is_stdlib_only_and_never_launches_processes_itself(self):
        import ast
        base = os.path.join(ROOT, "video_core", "avmux")
        std = set(__import__("sys").stdlib_module_names)
        seen = 0
        for name in sorted(os.listdir(base)):
            if not name.endswith(".py"):
                continue
            seen += 1
            tree = ast.parse(read_text(os.path.join(base, name)))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    self.assertIn(m, std, f"{name}: {m}")
                    self.assertNotIn(m, {"subprocess", "socket", "ssl", "urllib", "http", "multiprocessing", "ctypes"}, f"{name} imports {m}")
                if isinstance(node, ast.keyword) and node.arg == "shell":
                    self.fail("shell= must never appear")
        self.assertGreaterEqual(seen, 6)

    def test_python_311_syntax(self):
        import ast
        base = os.path.join(ROOT, "video_core", "avmux")
        for name in sorted(os.listdir(base)):
            if name.endswith(".py"):
                ast.parse(read_text(os.path.join(base, name)), feature_version=(3, 11))


if __name__ == "__main__":
    unittest.main()
