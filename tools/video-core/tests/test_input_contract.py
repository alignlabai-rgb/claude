"""Input / diagnostic-boundary contracts (F-CLI-1, F-AUD-1, F-REC-1, F-REC-2, F-ENC-1 of the 2026-11-05 adversarial audit).

Every test states the CONTRACT (AUDIO_SPEC / AVMUX_SPEC / INPUT_CONTRACT.md) and uses independent expectations: serialisation is compared with the
plain stdlib encoder, sample positions with a Fraction oracle, receipts are mutated copies of one written by the real audio mixer. All inputs are
tiny synthetic documents in temporary directories; child processes are interpreter-only (`python -m video_core...`, <= 10 s each); no real
media is rendered, mixed or muxed, no network and no font install. Fake ffmpeg/ffprobe are POSIX scripts that only record that they were called.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

from tests.audio_fixtures import dumps, sha, tone, wav_bytes, write
from tests.avmux_fakes import av_manifest, default_behavior, make_av_tools, mux_calls, seed_inputs
from tests.helpers import POSIX, ROOT, TmpCase, fake_calls, make_fake_tools, no_process, sample_bytes, sample_doc, to_bytes, tree_snapshot
from tests.receipt_fixtures import genuine_receipt
from video_core import receipt as rc
from video_core.audiomix import mix as amix
from video_core.audiomix import plan as aplan
from video_core.audiomix.manifest import load_manifest as load_audio_manifest
from video_core.avmux import load_manifest as load_av_manifest, prepare_plan
from video_core.avmux.mux import mux
from video_core.episode import load_episode
from video_core.errors import UnsafePath, ValidationFailed, VideoCoreError
from video_core.limits import Limits
from video_core.paths import real_directory
from video_core.render import render
from video_core.srt import export_srt

BASELINE = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"
JP = "日本語の字幕：テスト"


def codes_of(fn):
    try:
        fn()
    except VideoCoreError as exc:
        return {i.code for i in exc.issues}
    return None


def audio_doc(*, fps=(30, 1), total=30, events=(), scope=None, assets=(), gap="ALLOW_ZERO_FILL"):
    return {"manifest_version": 1, "timeline": {"fps": {"n": fps[0], "d": fps[1]}, "total_frames": total}, "output": {"sample_rate": 48000, "channels": 1, "bits": 16},
            "scope": {"event_ids": list(scope) if scope is not None else [e["id"] for e in events]}, "policy": {"gap": gap}, "assets": list(assets), "events": list(events)}


def sample_at(frame: int, n: int, d: int) -> int:
    """Independent oracle for the documented policy: round-half-up of frame/(n/d) seconds * 48000 (Fraction arithmetic, no shared code)."""
    x = Fraction(frame * d * 48000, n)
    return int(x + Fraction(1, 2)) if x >= 0 else 0  # int() truncates toward zero == floor for the non-negative values used


# ============================================================================================================ F-CLI-1
class CliChild(TmpCase):
    """Runs the real CLIs as interpreter children with marker ffmpeg/ffprobe on PATH, in an empty cwd, and records launches and side effects."""

    def setUp(self):
        super().setUp()
        self.cwd, self.bin = self.mkdir("cwd"), self.mkdir("markerbin")
        self.marker = os.path.join(self.tmp, "LAUNCHED")
        if POSIX:
            for name in ("ffmpeg", "ffprobe"):
                p = os.path.join(self.bin, name)
                with open(p, "w") as fh:
                    fh.write(f"#!/bin/sh\necho {name} >> '{self.marker}'\nexit 9\n")
                os.chmod(p, 0o755)

    def run_cli(self, module, args):
        env = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", ""), "PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("SYSTEMROOT", "TEMP", "TMP", "HOME"):
            if k in os.environ:
                env[k] = os.environ[k]
        before = tree_snapshot(self.cwd)
        p = subprocess.run([sys.executable, "-m", module, *args], capture_output=True, cwd=self.cwd, env=env, timeout=10)
        self.assertEqual(tree_snapshot(self.cwd), before, "no file may be created by a validate/plan")
        self.assertFalse(os.path.exists(self.marker), "no media process may be launched")
        return p


class LoneSurrogateKeyAtTheCommandLine(CliChild):
    """A JSON key written as "\\ud800" decodes to a lone surrogate. The documented rejection (exit 1 + structured document on stdout) must survive it."""

    def docs(self):
        am = audio_doc(events=[{"id": "e", "source_id": "s", "state": "UNKNOWN"}])
        av = av_manifest(wav=b"x")
        return (("video_core", sample_doc()), ("video_core.audiomix", am), ("video_core.avmux", av))

    def with_key(self, doc, key_json, where=()):
        """Insert `key_json: 1` into the document (top level or at a nested object) while the raw file spells the key as a JSON escape."""
        d = copy.deepcopy(doc)
        tgt = d
        for step in where:
            tgt = tgt[step]
        tgt["__PLACEHOLDER__"] = 1
        return json.dumps(d, ensure_ascii=False).replace('"__PLACEHOLDER__"', key_json).encode("utf-8")

    def check_rejection(self, p, expect_path_suffix):
        self.assertEqual(p.returncode, 1, p.stderr[-300:])
        self.assertNotIn(b"Traceback", p.stderr)
        out = json.loads(p.stdout)  # valid JSON on stdout
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["kind"], "validation_failed")
        paths = [i["path"] for i in out["error"]["issues"] if i["code"] == "unknown_field"]
        self.assertTrue(any(x.endswith(expect_path_suffix) for x in paths), paths)  # lossless: a JSON decode returns the exact offending key
        return out

    def test_all_three_validate_clis_reject_cleanly_for_a_top_level_lone_surrogate_key(self):
        for module, doc in self.docs():
            with self.subTest(module):
                path = self.write(f"{module}.json", self.with_key(doc, '"\\ud800"'))
                self.check_rejection(self.run_cli(module, ["validate", path]), "/\ud800")

    def test_the_other_surrogate_kinds_and_a_nested_key_are_also_clean(self):
        for key_json, expect in (('"\\udfff"', "/\udfff"), ('"a\\ud800b"', "/a\ud800b"), ('"\\ude00\\ud83d"', "/\ude00\ud83d")):  # low, embedded, and low-then-high (not a pair)
            with self.subTest(key_json):
                path = self.write("e.json", self.with_key(sample_doc(), key_json))
                self.check_rejection(self.run_cli("video_core", ["validate", path]), expect)
        nested = self.write("n.json", self.with_key(sample_doc(), '"\\ud800"', where=("canvas",)))
        self.check_rejection(self.run_cli("video_core", ["validate", nested]), "/canvas/\ud800")

    def test_plan_subcommands_are_equally_clean(self):
        for module, doc in self.docs():
            with self.subTest(module):
                path = self.write(f"{module}.plan.json", self.with_key(doc, '"\\ud800"'))
                self.check_rejection(self.run_cli(module, ["plan", path]), "/\ud800")

    def test_valid_japanese_unknown_key_is_still_written_as_literal_utf8_not_escapes(self):
        """Normal serialisation is unchanged: encodable text stays literal. Only unencodable code points are escaped."""
        for module, doc in self.docs():
            with self.subTest(module):
                path = self.write(f"{module}.jp.json", self.with_key(doc, '"未知キー"'))
                p = self.run_cli(module, ["validate", path])
                self.assertEqual(p.returncode, 1)
                self.assertIn("未知キー".encode("utf-8"), p.stdout)
                self.assertNotIn(b"\\u672a", p.stdout)
                self.assertIn("/未知キー", [i["path"] for i in json.loads(p.stdout)["error"]["issues"]][0])

    def test_positive_japanese_controls_still_pass_and_srt_bytes_are_exact(self):
        ep = sample_doc()
        ep["captions"][0]["text"] = JP
        path = self.write("jp.json", to_bytes(ep))
        p = self.run_cli("video_core", ["validate", path])
        self.assertEqual((p.returncode, json.loads(p.stdout)["ok"]), (0, True))
        srt = self.run_cli("video_core", ["srt", path])
        self.assertEqual(srt.returncode, 0)
        self.assertEqual(srt.stdout, export_srt(load_episode(to_bytes(ep))))
        self.assertIn(JP.encode("utf-8"), srt.stdout)  # bytes, not a normalised or replaced rendering
        am = audio_doc(events=[{"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "決定-参照 日本語"}])
        self.assertEqual(self.run_cli("video_core.audiomix", ["validate", self.write("am.json", to_bytes(am))]).returncode, 0)
        av = av_manifest(wav=b"x")
        av["selection"]["ref"] = "選択-参照"
        self.assertEqual(self.run_cli("video_core.avmux", ["validate", self.write("av.json", to_bytes(av))]).returncode, 0)

    def test_the_baseline_plan_hash_is_exactly_unchanged(self):
        p = self.run_cli("video_core", [os.path.join(ROOT, "examples", "episode.synthetic.json")])
        self.assertEqual(json.loads(p.stdout)["plan_sha256"], BASELINE)


class DiagnosticSerialisationContract(unittest.TestCase):
    def test_encodable_documents_are_byte_identical_to_the_plain_stdlib_encoding(self):
        doc = {"ok": False, "error": {"issues": [{"path": "/キー/«»", "message": JP, "emoji": "\U0001F600", "ctl": " \n\t"}]}, "n": 3}
        expected = (json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")  # strict encoder: would raise if anything were unencodable
        self.assertEqual(rc.dumps(doc), expected)

    def test_unencodable_text_is_escaped_losslessly_and_the_document_round_trips(self):
        for s in ("\ud800", "\udfff", "a\udce9b", "\ude00\ud83d", "日本\udc80語"):
            with self.subTest(s=repr(s)):
                doc = {"path": "/x/" + s, s: [s]}
                with self.assertRaises(UnicodeEncodeError):  # the old expression: this is the crash being removed
                    (json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                data = rc.dumps(doc)
                data.decode("utf-8")  # the bytes are valid UTF-8 ...
                self.assertEqual(json.loads(data), doc)  # ... valid JSON ... and decode to the identical value (nothing replaced or normalised)

    def test_canonical_plan_serialisation_stays_strict_so_a_hash_never_covers_escaped_text(self):
        from video_core import plan as vplan
        from video_core.avmux import plan as avplan
        for fn in (aplan.canonical_bytes, avplan.canonical_bytes, vplan.canonical_bytes):
            with self.assertRaises(UnicodeEncodeError):
                fn({"k": "\ud800"})
            self.assertEqual(fn({"k": "日本"}), '{"k":"日本"}'.encode("utf-8"))

    def test_lone_surrogates_in_every_key_or_string_position_never_escape_as_untyped_errors(self):
        """Library-level sweep: each string value and each key of valid documents is replaced by a lone surrogate. Whatever the loader decides, it
        must be a typed VideoCoreError, and the CLI's own error document must then serialise."""
        def positions(node, path=()):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield ("key", path, k)
                    yield from positions(v, path + (k,))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from positions(v, path + (i,))
            elif isinstance(node, str):
                yield ("value", path[:-1], path[-1])

        def mutate(doc, kind, parent, key):
            d = copy.deepcopy(doc)
            node = d
            for step in parent:
                node = node[step]
            if kind == "value":
                node[key] = "\ud800"
            else:
                node["\ud800"] = node.pop(key)
            return d

        checked = 0
        loaders = ((sample_doc(), lambda d: load_episode(json.dumps(d).encode("ascii"))),
                   (audio_doc(events=[{"id": "e", "source_id": "s", "state": "OMIT", "decision_ref": "r", "role": "x", "provenance_ref": "p"}],
                              assets=[{"id": "a", "path": "a.wav", "sha256": "0" * 64}]),
                    lambda d: aplan.analyze(load_audio_manifest(json.dumps(d).encode("ascii"), aplan.AudioLimits()), aplan.AudioLimits())),
                   (av_manifest(wav=b"x", receipt={"path": "r.json", "sha256": "0" * 64}), lambda d: load_av_manifest(json.dumps(d).encode("ascii"), Limits())))
        for doc, load in loaders:
            for kind, parent, key in list(positions(doc)):
                d = mutate(doc, kind, parent, key)
                try:
                    load(d)
                except VideoCoreError as exc:
                    out = rc.dumps({"ok": False, "error": exc.to_dict()})
                    json.loads(out)
                checked += 1
        self.assertGreater(checked, 80)


# ============================================================================================================ F-AUD-1
class ZeroSampleRanges(TmpCase):
    """AUDIO_SPEC: unselected events are never verified or read and never block a valid selected scope; OMIT consumes nothing. At 60000/1 a frame is
    0.8 samples, so a one-frame range can span zero samples. Only events that are consumed (selected USE / INTENTIONAL_SILENCE) need a non-empty span."""

    SILENCE = {"id": "a", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "r", "start_frame": 0, "end_frame": 10}
    ASSET = {"id": "x", "path": "x.wav", "sha256": "0" * 64}

    def ev(self, i, state, s, e, **kw):
        return {"id": i, "source_id": "s", "state": state, "start_frame": s, "end_frame": e, **kw}

    def plan(self, events, scope, assets=(), gap="ALLOW_ZERO_FILL", total=60):
        return aplan.prepare_plan(dumps(audio_doc(fps=(60000, 1), total=total, events=events, scope=scope, assets=assets, gap=gap)))

    def test_the_oracle_confirms_the_fixture_ranges_really_span_zero_samples(self):
        self.assertEqual((sample_at(2, 60000, 1), sample_at(3, 60000, 1)), (2, 2))
        self.assertEqual((sample_at(12, 60000, 1), sample_at(13, 60000, 1)), (10, 10))
        self.assertEqual((aplan.frame_to_sample(2, 60000, 1), aplan.frame_to_sample(3, 60000, 1)), (2, 2))
        self.assertEqual(sample_at(10, 60000, 1), aplan.frame_to_sample(10, 60000, 1))

    def test_unselected_events_of_every_state_never_block_a_valid_selected_scope(self):
        b = lambda state, **kw: self.ev("b", state, 2, 3, **kw)  # noqa: E731
        cases = {"UNKNOWN": b("UNKNOWN"), "MISSING": b("MISSING"), "PROXY": b("PROXY"), "OMIT": b("OMIT", decision_ref="r2"),
                 "INTENTIONAL_SILENCE": b("INTENTIONAL_SILENCE", decision_ref="r2"),
                 "USE": b("USE", asset="x", source_in_sample=0, source_out_sample=1)}
        for state, event in cases.items():
            with self.subTest(state):
                p = self.plan([self.SILENCE, event], ["a"], assets=[self.ASSET])
                self.assertEqual((p.status, p.hold), ("READY", []))
                self.assertEqual(p.canonical["scope"], ["a"])
                dispo = {e["id"]: e["disposition"] for e in p.canonical["events"]}
                self.assertEqual(dispo["a"], "SILENCE")
                self.assertNotEqual(dispo["b"], "SILENCE")
                self.assertEqual(p.mix_events, [])

    def test_an_unselected_unresolved_event_stays_in_the_report_and_is_never_verified(self):
        p = self.plan([self.SILENCE, self.ev("b", "UNKNOWN", 2, 3)], ["a"])
        self.assertEqual(p.canonical["unresolved"], [{"event_id": "b", "source_id": "s", "state": "UNKNOWN", "selected": False}])
        self.assertEqual([e["samples"] for e in p.canonical["events"] if e["id"] == "b"], [0])  # reported honestly as zero samples

    def test_a_selected_omit_consumes_nothing_and_does_not_count_as_coverage(self):
        p = self.plan([self.SILENCE, self.ev("b", "OMIT", 2, 3, decision_ref="r2")], ["a", "b"])
        self.assertEqual(p.status, "READY")
        self.assertEqual({e["id"]: e["disposition"] for e in p.canonical["events"]}["b"], "OMITTED")
        self.assertEqual(p.canonical["coverage"]["covered_samples"], sample_at(10, 60000, 1) - sample_at(0, 60000, 1))  # only the silence covers
        self.assertEqual(p.mix_events, [])
        total = p.total_samples
        self.assertEqual(p.canonical["coverage"]["gaps"], [{"sample_start": sample_at(10, 60000, 1), "sample_end": total}])

    def test_require_full_coverage_still_holds_when_only_a_zero_sample_omit_would_have_filled_it(self):
        p = self.plan([self.ev("b", "OMIT", 2, 3, decision_ref="r2")], ["b"], gap="REQUIRE_FULL_COVERAGE")
        self.assertEqual((p.status, [h["code"] for h in p.hold]), ("HOLD", ["INCOMPLETE_SELECTED_INPUT"]))

    def test_selected_events_that_are_consumed_are_still_rejected_with_zero_samples(self):
        silent = self.ev("b", "INTENTIONAL_SILENCE", 12, 13, decision_ref="r2")
        use = self.ev("b", "USE", 12, 13, asset="x", source_in_sample=0, source_out_sample=1)
        for label, events, assets in (("silence", [self.SILENCE, silent], ()), ("use", [self.SILENCE, use], [self.ASSET]), ("silence alone", [dict(silent, id="a")], ())):
            with self.subTest(label):
                scope = [e["id"] for e in events]
                self.assertEqual(codes_of(lambda: self.plan(events, scope, assets)), {"event_zero_samples"})

    def test_a_selected_unresolved_event_with_zero_samples_is_still_rejected_unchanged(self):
        """Unspecified by AUDIO_SPEC (a selected unresolved event yields HOLD, but its range was always validated first). Kept exactly as it was;
        see INPUT_CONTRACT.md 'remaining limits'. A normal-width selected unresolved event still yields HOLD, not a rejection."""
        self.assertEqual(codes_of(lambda: self.plan([self.SILENCE, self.ev("b", "UNKNOWN", 12, 13)], ["a", "b"])), {"event_zero_samples"})
        p = aplan.prepare_plan(dumps(audio_doc(events=[self.ev("b", "UNKNOWN", 3, 6)], scope=["b"])))
        self.assertEqual(p.status, "HOLD")
        self.assertIn("SELECTED_UNRESOLVED", [h["code"] for h in p.hold])

    def test_ordinary_frame_rates_are_unaffected(self):
        p = aplan.prepare_plan(dumps(audio_doc(events=[self.ev("a", "INTENTIONAL_SILENCE", 0, 10, decision_ref="r"), self.ev("b", "UNKNOWN", 2, 3)], scope=["a"])))
        self.assertEqual(p.status, "READY")
        self.assertEqual([e["samples"] for e in p.canonical["events"]], [10 * 1600, 1600])

    def test_a_real_selected_use_still_mixes_and_the_zero_sample_unselected_neighbour_is_never_read(self):
        """End to end with a real (tiny) WAV: the selected USE is verified and mixed sample-exactly; the zero-sample unselected USE names a file that
        does not even exist, which proves it is never opened. Pure Python mix, no process."""
        assets, out = self.mkdir("assets"), self.mkdir("out")
        src = wav_bytes(tone("square", 480, 4800, 8000))
        write(os.path.join(assets, "s.wav"), src)
        use = self.ev("u", "USE", 0, 6, asset="s", source_in_sample=0, source_out_sample=sample_at(6, 60000, 1) - 0)
        ghost = self.ev("g", "USE", 12, 13, asset="ghost", source_in_sample=0, source_out_sample=1)
        doc = audio_doc(fps=(60000, 1), total=60, events=[use, ghost], scope=["u"],
                        assets=[{"id": "s", "path": "s.wav", "sha256": sha(src)}, {"id": "ghost", "path": "ghost.wav", "sha256": "0" * 64}])
        before = tree_snapshot(self.tmp)
        with no_process():
            p = aplan.prepare_plan(dumps(doc), asset_root=assets, output_root=out, output_name="r")
        self.assertEqual((p.status, len(p.mix_events)), ("READY", 1))
        self.assertEqual(tree_snapshot(self.tmp), before, "planning created nothing")
        rec = amix.mix(dumps(doc), asset_root=assets, output_root=out, output_name="r")
        self.assertEqual(rec["status"], "MIXED_VERIFIED")
        with open(os.path.join(out, "r", "stem.wav"), "rb") as fh:
            stem = fh.read()
        n = sample_at(6, 60000, 1)
        self.assertEqual(len(stem), 44 + 2 * sample_at(60, 60000, 1))
        self.assertEqual(stem[44:44 + 2 * n], src[44:44 + 2 * n])  # sample-exact copy of the selected source range
        self.assertEqual(stem[44 + 2 * n:], bytes(len(stem) - 44 - 2 * n))  # the rest is the explicit zero fill

    def test_the_cli_plans_the_audit_pair_and_creates_nothing(self):
        cwd = self.mkdir("cwd")
        for label, extra, scope in (("unselected", self.ev("b", "UNKNOWN", 2, 3), ["a"]), ("omit", self.ev("b", "OMIT", 2, 3, decision_ref="r2"), ["a", "b"])):
            with self.subTest(label):
                path = self.write(f"{label}.json", dumps(audio_doc(fps=(60000, 1), total=60, events=[self.SILENCE, extra], scope=scope)))
                before = tree_snapshot(cwd)
                p = subprocess.run([sys.executable, "-m", "video_core.audiomix", "plan", path], capture_output=True, cwd=cwd, timeout=10,
                                   env={"PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", "")})
                self.assertEqual((p.returncode, json.loads(p.stdout)["status"]), (0, "READY"), p.stderr[-300:])
                self.assertEqual(tree_snapshot(cwd), before)


# ============================================================================================================ F-REC-1 / F-REC-2
class AudioReceiptContract(TmpCase):
    """avmux `audio.receipt`: strict loading + a MECHANICAL contract against what the real producer writes. Authentication is explicitly out of scope."""

    def setUp(self):
        super().setUp()
        self.assets = self.mkdir("assets")
        self.stem, self.genuine = genuine_receipt(self.mkdir("work"))
        self.video = b"FAKE-VIDEO"
        write(os.path.join(self.assets, "clip.mp4"), self.video)
        write(os.path.join(self.assets, "stem.wav"), self.stem)

    def plan_raw(self, raw: bytes):
        write(os.path.join(self.assets, "r.json"), raw)
        doc = av_manifest(wav=self.stem, video_bytes=self.video)
        doc["audio"]["receipt"] = {"path": "r.json", "sha256": sha(raw)}
        return prepare_plan(dumps(doc), asset_root=self.assets)

    def outcome(self, raw: bytes):
        try:
            with no_process():
                self.plan_raw(raw)
            return "ACCEPTED"
        except VideoCoreError as exc:  # anything else (AttributeError, RecursionError ...) propagates and fails the test: that is the contract
            return exc.issues[0].code

    def mod(self, fn) -> bytes:
        d = copy.deepcopy(self.genuine)
        fn(d)
        return json.dumps(d).encode()

    def stem_out(self, d):
        return next(o for o in d["outputs"] if o["role"] == "stem")

    def test_control_the_genuine_mixer_receipt_qualifies_and_records_only_mechanical_facts(self):
        raw = json.dumps(self.genuine).encode()
        c = self.plan_raw(raw).canonical["audio"]["receipt"]
        self.assertEqual((c["status"], c["plan_sha256"], c["schema"], c["mechanical_checks"]), ("MIXED_VERIFIED", self.genuine["plan_sha256"], "video_core.audio_receipt/1", "PASSED"))
        self.assertIn("unauthenticated", c["authentication"])
        self.assertIn("not prove", c["authentication"])
        with open(os.path.join(self.assets, "r.json"), "rb") as fh:
            self.assertEqual(self.outcome(fh.read()), "ACCEPTED")

    def test_the_committed_historical_mixer_receipt_also_qualifies_against_its_own_stem_facts(self):
        """A second, independent producer output (evidence/stage-b-audio-stem, written by an earlier run): the contract must not be tuned to one fixture."""
        from video_core.avmux.receipt_check import qualify_audio_receipt  # imported here so the rest of this file can also run against the unfixed tree
        with open(os.path.join(ROOT, "evidence", "stage-b-audio-stem", "receipt.stem-demo.json"), "rb") as fh:
            hist = json.loads(fh.read())
        stem = next(o for o in hist["outputs"] if o["role"] == "stem")
        facts = qualify_audio_receipt(hist, stem_sha256=stem["sha256"], stem_bytes=stem["bytes"], samples=4800, channels=1)
        self.assertEqual(facts["status"], "MIXED_VERIFIED")
        self.assertEqual(facts["plan_sha256"], hist["plan_sha256"])

    def test_warnings_and_unresolved_events_of_the_receipt_are_retained_not_dropped(self):
        stem, rec = genuine_receipt(self.mkdir("work2"), zero_fill_last_frame=True)
        self.assertEqual([w["code"] for w in rec["warnings"]], ["ZERO_FILL_APPLIED"])
        write(os.path.join(self.assets, "stem.wav"), stem)
        raw = json.dumps(rec).encode()
        write(os.path.join(self.assets, "r.json"), raw)
        doc = av_manifest(wav=stem, video_bytes=self.video)
        doc["audio"]["receipt"] = {"path": "r.json", "sha256": sha(raw)}
        c = prepare_plan(dumps(doc), asset_root=self.assets).canonical["audio"]["receipt"]
        self.assertEqual(c["warnings"], [{"code": "ZERO_FILL_APPLIED", "detail": rec["warnings"][0]["detail"]}])
        self.assertEqual((c["warnings_total"], c["warnings_truncated"], c["unresolved_events"]), (1, False, len(rec["unresolved_report"])))

    def test_a_plan_without_a_receipt_is_byte_for_byte_what_it_was(self):
        a = prepare_plan(dumps(av_manifest(wav=self.stem, video_bytes=self.video)), asset_root=self.assets)
        self.assertIsNone(a.canonical["audio"]["receipt"])
        self.assertNotIn(b"mechanical_checks", a.canonical_json)

    def test_malformed_receipt_content_is_a_typed_rejection_never_an_untyped_exception(self):
        shapes = {"[]": b"[]", "null": b"null", '"x"': b'"x"', "1": b"1", "true": b"true", "{}": b"{}",
                  "outputs str": self.mod(lambda d: d.update(outputs="x")), "outputs [1]": self.mod(lambda d: d.update(outputs=[1])),
                  "outputs null": self.mod(lambda d: d.update(outputs=None)),
                  "readback.wav str": self.mod(lambda d: d["readback"].update(wav="x")), "readback null": self.mod(lambda d: d.update(readback=None)),
                  "readback.checks str": self.mod(lambda d: d["readback"].update(checks="x")), "readback.checks [1]": self.mod(lambda d: d["readback"].update(checks=[1])),
                  "mix list": self.mod(lambda d: d.update(mix=[])), "input str": self.mod(lambda d: d.update(input="x")),
                  "warnings 5": self.mod(lambda d: d.update(warnings=5)), "warnings [1]": self.mod(lambda d: d.update(warnings=[1])),
                  "status list": self.mod(lambda d: d.update(status=["MIXED_VERIFIED"])), "status dict": self.mod(lambda d: d.update(status={})),
                  "unresolved_report str": self.mod(lambda d: d.update(unresolved_report="x"))}
        got = {k: self.outcome(v) for k, v in shapes.items()}
        self.assertTrue(all(v.startswith("audio_receipt_") for v in got.values()), got)

    def test_unreadable_receipts_get_audio_receipt_unreadable(self):
        genuine = json.dumps(self.genuine).encode()
        raw = genuine.decode()
        cases = {"empty": b"", "BOM": b"\xef\xbb\xbf" + genuine, "invalid UTF-8": b"\xff\xfe{}", "not JSON": b"not json", "truncated": genuine[:-5],
                 "NaN": raw.replace('"clipped_samples": 0', '"clipped_samples": NaN').encode(), "Infinity": raw.replace('"clipped_samples": 0', '"clipped_samples": Infinity').encode(),
                 "duplicate status keys, last wins": ('{"status":"HOLD",' + raw[1:].replace('"status": "MIXED_VERIFIED"', '"status": "MIXED_VERIFIED"', 1)).encode(),
                 "duplicate key anywhere": raw.replace('"written": true', '"written": false, "written": true', 1).encode(),
                 "deeply nested": b"[" * 100000 + b"]" * 100000, "deeply nested objects": b'{"a":' * 50000 + b"1" + b"}" * 50000,
                 "huge integer literal": raw.replace('"clipped_samples": 0', '"clipped_samples": ' + "9" * 5000).encode()}
        # the duplicate-status case must really contain two status keys
        self.assertEqual(cases["duplicate status keys, last wins"].count(b'"status"'), 2)
        got = {k: self.outcome(v) for k, v in cases.items()}
        self.assertEqual(got, {k: "audio_receipt_unreadable" for k in cases})

    def test_each_inconsistency_is_refused_with_its_own_code(self):
        S = self.stem_out
        table = {
            "schema-less three-field receipt": (json.dumps({"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": sha(self.stem)}], "readback": {"wav": {"sample_count": 4800}}}).encode(), "audio_receipt_schema"),
            "wrong schema": (self.mod(lambda d: d.update(schema="video_core.receipt/1")), "audio_receipt_schema"),
            "no schema": (self.mod(lambda d: d.pop("schema")), "audio_receipt_schema"),
            "written false": (self.mod(lambda d: d.update(written=False)), "audio_receipt_inconsistent"),
            "written 1": (self.mod(lambda d: d.update(written=1)), "audio_receipt_inconsistent"),
            "stage mix": (self.mod(lambda d: d.update(stage="mix")), "audio_receipt_inconsistent"),
            "quality_evaluated true": (self.mod(lambda d: d.update(quality_evaluated=True)), "audio_receipt_inconsistent"),
            "quality_evaluated missing": (self.mod(lambda d: d.pop("quality_evaluated")), "audio_receipt_inconsistent"),
            "all_ok false": (self.mod(lambda d: d["readback"].update(all_ok=False)), "audio_receipt_inconsistent"),
            "all_ok 1": (self.mod(lambda d: d["readback"].update(all_ok=1)), "audio_receipt_inconsistent"),
            "one check ok false": (self.mod(lambda d: d["readback"]["checks"][2].update(ok=False)), "audio_receipt_inconsistent"),
            "check expected != observed": (self.mod(lambda d: d["readback"]["checks"][1].update(expected=44100)), "audio_receipt_inconsistent"),
            "check missing": (self.mod(lambda d: d["readback"]["checks"].pop()), "audio_receipt_shape"),
            "check duplicated": (self.mod(lambda d: d["readback"]["checks"].append(copy.deepcopy(d["readback"]["checks"][0]))), "audio_receipt_shape"),
            "stem partial true": (self.mod(lambda d: S(d).update(partial=True)), "audio_receipt_inconsistent"),
            "stem partial missing": (self.mod(lambda d: S(d).pop("partial")), "audio_receipt_inconsistent"),
            "extra stem_partial output": (self.mod(lambda d: d["outputs"].append({"role": "stem_partial", "name": "stem.wav.partial", "bytes": 1, "sha256": "0" * 64, "partial": True})), "audio_receipt_stem_mismatch"),
            "two stems": (self.mod(lambda d: d["outputs"].append(copy.deepcopy(S(d)))), "audio_receipt_stem_mismatch"),
            "no stem": (self.mod(lambda d: d.update(outputs=[o for o in d["outputs"] if o["role"] != "stem"])), "audio_receipt_stem_mismatch"),
            "stem sha of another file": (self.mod(lambda d: S(d).update(sha256="0" * 64)), "audio_receipt_stem_mismatch"),
            "stem byte count": (self.mod(lambda d: S(d).update(bytes=S(d)["bytes"] + 1)), "audio_receipt_stem_mismatch"),
            "stem byte count float": (self.mod(lambda d: S(d).update(bytes=float(S(d)["bytes"]))), "audio_receipt_stem_mismatch"),
            "stem name": (self.mod(lambda d: S(d).update(name="other.wav")), "audio_receipt_stem_mismatch"),
            "wav sha of another file": (self.mod(lambda d: d["readback"]["wav"].update(sha256="0" * 64)), "audio_receipt_stem_mismatch"),
            "plan_sha256 short": (self.mod(lambda d: d.update(plan_sha256="ab" * 31)), "audio_receipt_plan_hash"),
            "plan_sha256 upper-case": (self.mod(lambda d: d.update(plan_sha256=d["plan_sha256"].upper())), "audio_receipt_plan_hash"),
            "plan_sha256 null": (self.mod(lambda d: d.update(plan_sha256=None)), "audio_receipt_plan_hash"),
            "plan_sha256 differs from the plan output": (self.mod(lambda d: d.update(plan_sha256="ab" * 32)), "audio_receipt_plan_hash"),
            "no plan output": (self.mod(lambda d: d.update(outputs=[o for o in d["outputs"] if o["role"] != "plan"])), "audio_receipt_plan_hash"),
            "sample_count float": (self.mod(lambda d: d["readback"]["wav"].update(sample_count=4800.0)), "audio_receipt_sample_mismatch"),
            "sample_count true-ish": (self.mod(lambda d: d["readback"]["wav"].update(sample_count=True)), "audio_receipt_sample_mismatch"),
            "sample_count other": (self.mod(lambda d: d["readback"]["wav"].update(sample_count=4799)), "audio_receipt_sample_mismatch"),
            "sample_count string": (self.mod(lambda d: d["readback"]["wav"].update(sample_count="4800")), "audio_receipt_sample_mismatch"),
            "wav channels 2": (self.mod(lambda d: d["readback"]["wav"].update(channels=2)), "audio_receipt_format_mismatch"),
            "wav sample_rate 44100": (self.mod(lambda d: d["readback"]["wav"].update(sample_rate=44100)), "audio_receipt_format_mismatch"),
            "wav bits 24": (self.mod(lambda d: d["readback"]["wav"].update(bits=24)), "audio_receipt_format_mismatch"),
            "clipped samples": (self.mod(lambda d: d["mix"].update(clipped_samples=1)), "audio_receipt_inconsistent"),
            "samples_written short": (self.mod(lambda d: d["mix"].update(samples_written=4799)), "audio_receipt_inconsistent"),
            "inputs changed": (self.mod(lambda d: d["input"].update(inputs_unchanged=False)), "audio_receipt_inconsistent"),
        }
        got = {k: self.outcome(raw) for k, (raw, _code) in table.items()}
        self.assertEqual(got, {k: code for k, (_raw, code) in table.items()})

    def test_every_non_verified_status_is_refused_first(self):
        for status in ("MIXED_WITH_CLAMPING", "CLIPPING_REJECTED", "HOLD", "UNKNOWN", "TIMEOUT", "CANCELLED", "INTERRUPTED", "READBACK_MISMATCH", "IN_PROGRESS", "", None, 1):
            with self.subTest(status=status):
                self.assertEqual(self.outcome(self.mod(lambda d, s=status: d.update(status=s))), "audio_receipt_not_verified")

    def test_a_receipt_for_another_stem_is_refused_whatever_else_it_says(self):
        other, rec = genuine_receipt(self.mkdir("work3"), samples=3200)
        self.assertNotEqual(sha(other), sha(self.stem))
        self.assertEqual(self.outcome(json.dumps(rec).encode()), "audio_receipt_stem_mismatch")

    def test_the_rejection_happens_before_any_output_or_process_and_cli_reports_it_structured(self):
        raw = b"[" * 100000 + b"]" * 100000
        write(os.path.join(self.assets, "r.json"), raw)
        doc = av_manifest(wav=self.stem, video_bytes=self.video)
        doc["audio"]["receipt"] = {"path": "r.json", "sha256": sha(raw)}
        mpath = self.write("m.json", dumps(doc))
        before = tree_snapshot(self.tmp)
        p = subprocess.run([sys.executable, "-m", "video_core.avmux", "plan", mpath, "--asset-root", self.assets], capture_output=True, timeout=10,
                           env={"PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", "")})
        self.assertEqual(p.returncode, 1)
        self.assertNotIn(b"Traceback", p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([i["code"] for i in out["error"]["issues"]], ["audio_receipt_unreadable"])
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_a_pinned_receipt_is_documented_as_unauthenticated_in_the_plan(self):
        c = self.plan_raw(json.dumps(self.genuine).encode()).canonical["audio"]["receipt"]
        for forbidden in ("signature", "signed_by", "approved_by", "adoption"):
            self.assertNotIn(forbidden, json.dumps(c).lower().replace("creative adoption", ""))


# ============================================================================================================ F-ENC-1
def raw_name_supported(parent: str) -> bool:
    if os.name != "posix":
        return False
    try:
        os.mkdir(os.path.join(os.fsencode(parent), b"probe_caf\xe9"))
    except (OSError, ValueError, UnicodeError):
        return False
    os.rmdir(os.path.join(os.fsencode(parent), b"probe_caf\xe9"))
    return True


class EncodingRoots(TmpCase):
    """A raw non-UTF-8 byte in a POSIX directory name decodes (surrogateescape) to a lone surrogate. Plans and receipts are UTF-8 documents, so such a
    root must be refused BEFORE anything is created; valid Japanese roots are unaffected. Windows cannot create a raw 0xE9 file name, so there
    this class is skipped and the platform-independent parts (encoding guard on strings, receipt writer) run instead."""

    def setUp(self):
        super().setUp()
        if not raw_name_supported(self.tmp):
            self.raw = None
            return
        self.raw = os.fsdecode(os.path.join(os.fsencode(self.tmp), b"out_caf\xe9"))
        os.mkdir(os.fsencode(self.raw))

    def need_raw(self):
        if self.raw is None:
            self.skipTest("this platform cannot create a raw non-UTF-8 (0xE9) file name; POSIX-only evidence")

    def test_real_directory_rejects_unencodable_roots_with_a_typed_error_that_does_not_echo_them(self):
        for bad in ("out_caf\udce9", "\ud800", os.path.join(self.tmp, "x\udfff")):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(UnsafePath) as cm:
                    real_directory(bad, "output_root")
                self.assertEqual([i.code for i in cm.exception.issues], ["root_invalid"])
                text = json.dumps(cm.exception.to_dict(), ensure_ascii=True)
                self.assertNotIn("\udce9", cm.exception.message)
                self.assertIn("UTF-8", text)
                json.dumps(cm.exception.to_dict(), ensure_ascii=False).encode("utf-8")  # the diagnostic itself is encodable

    def test_a_real_raw_byte_directory_and_a_symlink_to_it_are_both_rejected(self):
        self.need_raw()
        with self.assertRaises(UnsafePath) as cm:
            real_directory(self.raw, "output_root")
        self.assertEqual([i.code for i in cm.exception.issues], ["root_invalid"])
        link = os.path.join(self.tmp, "ascii_link")
        os.symlink(os.fsencode(self.raw), link)
        with self.assertRaises(UnsafePath) as cm2:  # the unencodable name is only visible after resolution
            real_directory(link, "asset_root")
        self.assertEqual([i.code for i in cm2.exception.issues], ["root_invalid"])

    def test_valid_japanese_and_ascii_roots_are_unchanged(self):
        jp = self.mkdir("出力先", "さらに内側")
        self.assertEqual(real_directory(jp, "output_root"), os.path.realpath(jp))
        self.assertEqual(real_directory(self.tmp, "asset_root"), os.path.realpath(self.tmp))

    def test_audio_mix_into_a_japanese_root_completes_with_a_terminal_receipt(self):
        src, root = self.mkdir("src"), self.mkdir("出力先")
        data = wav_bytes(tone("square", 480, 4800, 8000))
        write(os.path.join(src, "s.wav"), data)
        doc = audio_doc(total=3, assets=[{"id": "a", "path": "s.wav", "sha256": sha(data)}],
                        events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])
        rec = amix.mix(dumps(doc), asset_root=src, output_root=root, output_name="r")
        self.assertEqual(rec["status"], "MIXED_VERIFIED")
        with open(os.path.join(root, "r", "receipt.json"), "rb") as fh:
            self.assertEqual(json.loads(fh.read())["status"], "MIXED_VERIFIED")

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_video_render_into_a_raw_byte_root_is_refused_before_anything_exists_and_no_tool_runs(self):
        self.need_raw()
        bindir = self.mkdir("bin")
        ffmpeg, ffprobe = make_fake_tools(bindir)
        before = tree_snapshot(self.raw)
        with self.assertRaises(UnsafePath) as cm:
            render(sample_bytes(), asset_root=None, output_root=self.raw, output_name="run1", ffmpeg=ffmpeg, ffprobe=ffprobe)
        self.assertEqual([i.code for i in cm.exception.issues], ["root_invalid"])
        self.assertEqual(tree_snapshot(self.raw), before)
        self.assertEqual(os.listdir(os.fsencode(self.raw)), [], "no run directory, no IN_PROGRESS receipt, no output")
        self.assertEqual(fake_calls(bindir), [], "no tool was launched, not even a version probe")

    @unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
    def test_avmux_with_a_raw_byte_output_or_asset_root_is_refused_before_anything_exists(self):
        self.need_raw()
        bindir = self.mkdir("avbin")
        make_av_tools(bindir, **default_behavior("MOV_PCM_S16LE"))
        assets = self.mkdir("assets")
        seed_inputs(assets)
        ff, fp = os.path.join(bindir, "ffmpeg"), os.path.join(bindir, "ffprobe")
        with self.assertRaises(UnsafePath) as cm:
            mux(dumps(av_manifest()), asset_root=assets, output_root=self.raw, output_name="r1", ffmpeg=ff, ffprobe=fp)
        self.assertEqual([i.code for i in cm.exception.issues], ["root_invalid"])
        self.assertEqual(os.listdir(os.fsencode(self.raw)), [])
        raw_assets = os.fsdecode(os.path.join(os.fsencode(self.tmp), b"assets_caf\xe9"))
        os.mkdir(os.fsencode(raw_assets))
        for n in ("clip.mp4", "stem.wav"):
            with open(os.path.join(assets, n), "rb") as src, open(os.path.join(raw_assets, n), "wb") as dst:
                dst.write(src.read())
        out = self.mkdir("out")
        with self.assertRaises(UnsafePath) as cm2:
            mux(dumps(av_manifest()), asset_root=raw_assets, output_root=out, output_name="r1", ffmpeg=ff, ffprobe=fp)
        self.assertEqual([i.code for i in cm2.exception.issues], ["root_invalid"])
        self.assertEqual(os.listdir(out), [])
        self.assertEqual(mux_calls(bindir), [])

    def test_the_audio_mixer_and_plan_cli_refuse_a_raw_byte_root_with_a_structured_error(self):
        self.need_raw()
        src = self.mkdir("src")
        data = wav_bytes(tone("square", 480, 4800, 8000))
        write(os.path.join(src, "s.wav"), data)
        doc = audio_doc(total=3, assets=[{"id": "a", "path": "s.wav", "sha256": sha(data)}],
                        events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])
        with self.assertRaises(UnsafePath):
            amix.mix(dumps(doc), asset_root=src, output_root=self.raw, output_name="r")
        self.assertEqual(os.listdir(os.fsencode(self.raw)), [])
        ep = self.write("ep.json", sample_bytes())
        p = subprocess.run([sys.executable, "-m", "video_core", "plan", ep, "--output-root", self.raw, "--output-name", "x"], capture_output=True, timeout=10,
                           env={"PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1", "PATH": os.environ.get("PATH", "")})
        self.assertEqual(p.returncode, 1)
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertEqual([i["code"] for i in json.loads(p.stdout)["error"]["issues"]], ["root_invalid"])

    def test_defence_in_depth_receipt_writers_never_raise_on_unencodable_diagnostic_text(self):
        """Even if an unencodable string reaches a receipt (for example a tool path from argv), writing it must not strand a run."""
        run = self.mkdir("run")
        receipt = {"status": rc.STATUS_IN_PROGRESS, "stage": "setup", "argv": ["ffmpeg", "-i", "dir_caf\udce9/x.mp4"], "note": JP}
        path = os.path.join(run, "receipt.json")
        rc.write_new(path, rc.dumps(receipt))
        receipt["status"] = "READBACK_MATCH"
        rc.replace_atomic(path, rc.dumps(receipt))
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertEqual(json.loads(data.decode("utf-8")), receipt)  # lossless, valid UTF-8 and JSON
        self.assertIn(JP.encode("utf-8"), data)


if __name__ == "__main__":
    unittest.main()
