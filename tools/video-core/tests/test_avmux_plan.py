import copy
import json
import os
import unittest
from unittest import mock

from tests.audio_fixtures import dumps, sha, tone, wav_bytes, write
from tests.avmux_fakes import av_manifest, seed_inputs
from tests.helpers import POSIX, TmpCase, no_process, tree_snapshot
from video_core import Limits, limits as L, runner
from video_core.audiomix.plan import frame_to_sample
from video_core.avmux import load_manifest, prepare_plan
from video_core.avmux import plan as planmod
from video_core.errors import UnsafePath, ValidationFailed

ROUTES = ("MOV_PCM_S16LE", "MP4_AAC")


def rejected(fn):
    with no_process():
        try:
            fn()
        except (ValidationFailed, UnsafePath) as exc:
            return {i.code for i in exc.issues}
    return set()


def mut(fn, base=None):
    d = copy.deepcopy(base or av_manifest())
    fn(d)
    return d


class ManifestRules(unittest.TestCase):
    def codes(self, doc):
        return rejected(lambda: load_manifest(dumps(doc) if not isinstance(doc, bytes) else doc, Limits()))

    def test_valid_for_both_routes(self):
        for r in ROUTES:
            m = load_manifest(dumps(av_manifest(r)), Limits())
            self.assertEqual((m.route, m.selection_ref, m.total_frames, m.audio_samples, m.audio_channels), (r, "caller-selection/synthetic-1", 3, 4800, 1))

    def test_structure_version_route_and_selection(self):
        self.assertIn("unsupported_version", self.codes(mut(lambda d: d.update(avmux_version=2))))
        self.assertIn("unsupported_version", self.codes(mut(lambda d: d.update(avmux_version=True))))
        self.assertIn("invalid_enum", self.codes(mut(lambda d: d.update(route="MKV_FLAC"))))
        self.assertIn("invalid_enum", self.codes(mut(lambda d: d.update(route="mov_pcm_s16le"))))
        for k in ("route", "selection", "video", "audio"):
            self.assertIn("missing_field", self.codes(mut(lambda d, k=k: d.pop(k))), k)
        self.assertIn("unknown_field", self.codes(mut(lambda d: d.update(shortest=True))), "there is no -shortest style option")
        for bad in ("", " x", "a\nb", "x" * 129, 5):
            self.assertIn("invalid_ref", self.codes(mut(lambda d, b=bad: d["selection"].update(ref=b))), repr(bad))
        self.assertIn("missing_field", self.codes(mut(lambda d: d["selection"].pop("ref"))), "the caller's explicit selection is required")

    def test_missing_unknown_proxy_hold_inputs_are_rejected_not_substituted(self):
        for which in ("video", "audio"):
            for state in ("MISSING", "UNKNOWN", "PROXY", "HOLD"):
                self.assertIn("input_state_not_use", self.codes(mut(lambda d, w=which, s=state: d[w].update(state=s))), (which, state))
            self.assertIn("invalid_enum", self.codes(mut(lambda d, w=which: d[w].update(state="use"))))

    def test_declared_video_properties(self):
        self.assertIn("video_codec_unsupported", self.codes(mut(lambda d: d["video"]["declared"].update(codec="hevc"))))
        for k, v in (("width", 15), ("width", 7681), ("height", 8), ("total_frames", 0), ("total_frames", 18001)):
            self.assertTrue(self.codes(mut(lambda d, k=k, v=v: d["video"]["declared"].update({k: v}))), (k, v))
        for k, v in (("n", 0), ("n", 60001), ("d", 0), ("d", 1002)):
            self.assertIn("out_of_range", self.codes(mut(lambda d, k=k, v=v: d["video"]["declared"]["fps"].update({k: v}))), (k, v))
        for bad in (30.0, "30", True, None):
            self.assertIn("type_error", self.codes(mut(lambda d, b=bad: d["video"]["declared"]["fps"].update(n=b))), repr(bad))

    def test_declared_audio_properties(self):
        self.assertIn("unsupported_audio", self.codes(mut(lambda d: d["audio"]["declared"].update(sample_rate=44100))))
        self.assertIn("out_of_range", self.codes(mut(lambda d: d["audio"]["declared"].update(channels=3))))
        self.assertIn("out_of_range", self.codes(mut(lambda d: d["audio"]["declared"].update(sample_count=0))))
        self.assertIn("type_error", self.codes(mut(lambda d: d["audio"]["declared"].update(sample_count=4800.0))))

    def test_paths_hashes_and_receipt_pairing(self):
        for p, c in (("/abs.mp4", "path_absolute"), ("../c.mp4", "path_traversal"), ("http://x/c.mp4", "path_protocol"), ("-c.mp4", "path_option_like"),
                     ("c.avi", "source_extension_unsupported"), ("c.mkv", "source_extension_unsupported"), ("c%d.mp4", "path_forbidden_char"), ("a\\c.mp4", "path_backslash")):
            self.assertIn(c, self.codes(mut(lambda d, p=p: d["video"].update(path=p))), p)
        for p, c in (("/abs.wav", "path_absolute"), ("../s.wav", "path_traversal"), ("s.mp3", "source_extension_unsupported"), ("file:s.wav", "path_protocol")):
            self.assertIn(c, self.codes(mut(lambda d, p=p: d["audio"].update(path=p))), p)
        for bad in ("zz", "a" * 63, "a" * 65, None):
            self.assertIn("invalid_sha256", self.codes(mut(lambda d, b=bad: d["video"].update(sha256=b))), repr(bad))
            self.assertIn("invalid_sha256", self.codes(mut(lambda d, b=bad: d["audio"].update(sha256=b))), repr(bad))
        self.assertTrue(self.codes(mut(lambda d: d["audio"].update(receipt={"path": "r.json"}))))
        self.assertTrue(self.codes(mut(lambda d: d["audio"].update(receipt={"path": "r.txt", "sha256": "0" * 64}))))
        self.assertIn("json_duplicate_key", self.codes(b'{"avmux_version": 1, "avmux_version": 1}'))
        self.assertIn("json_non_finite", self.codes(dumps(av_manifest()).replace(b'"n": 30', b'"n": NaN')))


class World(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out = self.mkdir("assets"), self.mkdir("out")
        self.video, self.wav = seed_inputs(self.assets)

    def plan(self, doc=None, **kw):
        kw.setdefault("asset_root", self.assets)
        return prepare_plan(dumps(doc or av_manifest()), **kw)


class PlanBasics(World):
    def test_plan_for_both_routes_is_deterministic_and_machine_independent(self):
        for r in ROUTES:
            a = self.plan(av_manifest(r), output_root=self.out, output_name="one")
            b = self.plan(av_manifest(r), output_root=self.mkdir("elsewhere", "deep"), output_name="two")
            self.assertEqual((a.canonical_json, a.sha256), (b.canonical_json, b.sha256), r)
            self.assertNotIn(self.tmp.encode(), a.canonical_json)
        self.assertNotEqual(self.plan(av_manifest("MOV_PCM_S16LE")).sha256, self.plan(av_manifest("MP4_AAC")).sha256)

    def test_plan_never_claims_verified_readiness_for_caller_stated_video_metadata(self):
        c = self.plan().canonical
        self.assertEqual(c["video"]["properties_basis"], "CALLER_STATED_UNVERIFIED")
        self.assertTrue(c["readiness"].startswith("UNVERIFIED"))
        self.assertNotIn("READY", c["readiness"])
        self.assertEqual(c["verdicts"], {"technical": "decided only after a mux run (see receipt)", "quality_evaluated": False, "creative_acceptance": "NOT_EVALUATED"})
        self.assertEqual(c["audio"]["verified_from_file"], {"sample_rate": 48000, "channels": 1, "bits": 16, "sample_count": 4800})

    def test_plan_records_pcm_hash_of_the_data_chunk_only(self):
        p = self.plan()
        self.assertEqual(p.pcm_sha256, sha(self.wav[44:]))
        self.assertEqual(p.canonical["audio"]["pcm_sha256"], sha(self.wav[44:]))
        self.assertNotEqual(p.pcm_sha256, sha(self.wav), "the file hash and the PCM hash are different things")

    def test_caller_assertions_are_preserved_and_labelled(self):
        c = self.plan().canonical
        self.assertEqual(c["selection"]["ref"], "caller-selection/synthetic-1")
        self.assertIn("not authenticated", c["selection"]["note"])

    def test_route_contracts_are_kept_separate(self):
        mov, mp4 = self.plan(av_manifest("MOV_PCM_S16LE")).canonical, self.plan(av_manifest("MP4_AAC")).canonical
        self.assertTrue(mov["contract"]["sample_exact"])
        self.assertFalse(mp4["contract"]["sample_exact"])
        self.assertEqual(mp4["contract"]["tolerance_samples"], planmod.AAC_TOLERANCE_SAMPLES)
        self.assertIn("sample-exact or bit-exact audio", mp4["contract"]["never_claimed"])
        self.assertIn("perceptual sync or audio quality", mp4["contract"]["never_claimed"])
        self.assertEqual((mov["requirements"]["encoders"], mp4["requirements"]["encoders"]), (["pcm_s16le"], ["aac"]))
        self.assertIn("mp4", mp4["requirements"]["muxers"])
        self.assertNotIn("mp4", mov["requirements"]["muxers"])
        self.assertIn("aac", mp4["requirements"]["decoders"])
        self.assertNotIn("aac", mov["requirements"]["decoders"])

    def test_argv_template_rules_for_both_routes(self):
        banned = {"-shortest", "-t", "-to", "-ss", "-r", "-ar", "-af", "-vf", "-filter_complex", "-vsync", "-fps_mode", "-ac", "-async", "-copyts", "-an", "-vn"}
        for r in ROUTES:
            argv = self.plan(av_manifest(r)).canonical["ffmpeg"]["argv"]
            flat = [a for a in argv if isinstance(a, str)]
            self.assertFalse(banned & set(flat), (r, banned & set(flat)))
            self.assertNotIn("aresample", " ".join(flat))
            i = flat.index("-c:v")
            self.assertEqual(flat[i + 1], "copy", "video is stream-copied")
            self.assertEqual(flat[flat.index("-map") + 1], "0:v:0")
            self.assertEqual(flat.count("-protocol_whitelist"), 2)
            self.assertEqual([flat[j + 1] for j, a in enumerate(flat) if a == "-f"], ["mov", "wav", "mov" if r == "MOV_PCM_S16LE" else "mp4"])
            self.assertIn("-n", flat)
            self.assertEqual(argv[-1], {"run_file": f"output.{'mov' if r == 'MOV_PCM_S16LE' else 'mp4'}.partial"})
            self.assertEqual([a for a in argv if isinstance(a, dict) and "asset" in a], [{"asset": "clip.mp4"}, {"asset": "stem.wav"}])
        mov = [a for a in self.plan(av_manifest("MOV_PCM_S16LE")).canonical["ffmpeg"]["argv"] if isinstance(a, str)]
        mp4 = [a for a in self.plan(av_manifest("MP4_AAC")).canonical["ffmpeg"]["argv"] if isinstance(a, str)]
        self.assertEqual(mov[mov.index("-c:a") + 1], "pcm_s16le")
        self.assertEqual((mp4[mp4.index("-c:a") + 1], mp4[mp4.index("-b:a") + 1]), ("aac", "128k"))
        self.assertIn("+faststart", mp4)
        self.assertNotIn("+faststart", mov)


class LengthPolicy(World):
    def doc(self, fps, total, declared, actual=None, channels=1):
        n = actual if actual is not None else declared
        wav = wav_bytes(tone("square", 480, n, 8000)) if channels == 1 else wav_bytes([1000] * (2 * n), 2)
        write(os.path.join(self.assets, "stem.wav"), wav)
        return av_manifest(fps=fps, total=total, samples=declared, wav=wav, channels=channels)

    def test_30fps_requires_exactly_1600_samples_per_frame(self):
        for declared, ok in ((4800, True), (4799, False), (4801, False), (3200, False), (6400, False)):
            codes = rejected(lambda: self.plan(self.doc((30, 1), 3, declared)))
            self.assertEqual(codes == set(), ok, declared)
            if not ok:
                self.assertEqual(codes, {"audio_sample_count_mismatch"})

    def test_ntsc_expected_sample_counts_come_from_absolute_boundaries(self):
        self.assertEqual([frame_to_sample(f, 30000, 1001) for f in (1, 2, 3, 4, 5)], [1602, 3203, 4805, 6406, 8008])
        for total, expected in ((3, 4805), (5, 8008)):
            self.assertEqual(self.plan(self.doc((30000, 1001), total, expected)).expected_samples, expected)
            for wrong in (expected - 1, expected + 1, total * 1602):
                if wrong != expected:
                    self.assertEqual(rejected(lambda: self.plan(self.doc((30000, 1001), total, wrong))), {"audio_sample_count_mismatch"}, (total, wrong))

    def test_message_names_both_numbers_and_says_nothing_is_adjusted(self):
        with self.assertRaises(ValidationFailed) as cm:
            self.plan(self.doc((30, 1), 3, 4801))
        msg = cm.exception.issues[0].message
        self.assertIn("4801", msg)
        self.assertIn("4800", msg)
        self.assertIn("never padded, trimmed or stretched", msg)

    def test_the_real_wav_must_match_what_is_declared(self):
        self.assertEqual(rejected(lambda: self.plan(self.doc((30, 1), 3, 4800, actual=4799))), {"audio_sample_count_mismatch"})
        self.assertEqual(rejected(lambda: self.plan(self.doc((30, 1), 3, 4800, actual=4801))), {"audio_sample_count_mismatch"})
        d = self.doc((30, 1), 3, 4800)
        d["audio"]["declared"]["channels"] = 2
        self.assertEqual(rejected(lambda: self.plan(d)), {"audio_channels_mismatch"})
        stereo = self.doc((30, 1), 3, 4800, channels=2)
        self.assertEqual(self.plan(stereo).canonical["audio"]["verified_from_file"]["channels"], 2)

    def test_fps_limit_is_enforced_from_the_shared_limits(self):
        d = self.doc((240, 1), 3, 600)
        self.assertEqual(rejected(lambda: self.plan(d, limits=Limits(max_fps=120))), {"limit_fps"})

    def test_frame_limit_comes_from_the_shared_limits(self):
        self.assertIn("limit_frames", rejected(lambda: self.plan(self.doc((30, 1), 3, 4800), limits=Limits(max_frames=2))))


class SourceProtection(World):
    def test_bad_hashes_are_rejected_with_both_values(self):
        for which, code in (("video", "video_sha256_mismatch"), ("audio", "audio_sha256_mismatch")):
            d = mut(lambda d, w=which: d[w].update(sha256="0" * 64))
            with self.assertRaises(ValidationFailed) as cm:
                self.plan(d)
            self.assertEqual(cm.exception.issues[0].code, code)
            self.assertIn("0" * 64, cm.exception.issues[0].message)

    def test_wav_problems_are_rejected_precisely(self):
        for kw, code in (({"rate": 44100}, "wav_unsupported_format"), ({"bits": 24}, "wav_unsupported_format"), ({"byte_rate": 7}, "wav_header_mismatch"),
                         ({"trunc": 11}, "wav_truncated"), ({"riff_delta": -4}, "wav_riff_size_mismatch"), ({"tag": 3}, "wav_unsupported_format")):
            wav = wav_bytes(tone("square", 480, 4800, 8000), **kw)
            write(os.path.join(self.assets, "stem.wav"), wav)
            self.assertEqual(rejected(lambda: self.plan(av_manifest(wav=wav))), {code}, kw)

    def test_missing_input_is_rejected_not_substituted(self):
        os.remove(os.path.join(self.assets, "stem.wav"))
        self.assertEqual(rejected(lambda: self.plan()), {"asset_missing"})
        write(os.path.join(self.assets, "stem.wav"), self.wav)
        os.remove(os.path.join(self.assets, "clip.mp4"))
        self.assertEqual(rejected(lambda: self.plan()), {"asset_missing"})

    def test_asset_root_is_required(self):
        self.assertEqual(rejected(lambda: prepare_plan(dumps(av_manifest()))), {"asset_root_required"})

    @unittest.skipUnless(POSIX, "symlinks")
    def test_symlinks_are_rejected_for_both_inputs(self):
        outside = write(os.path.join(self.tmp, "outside", "stem.wav"), self.wav)
        os.remove(os.path.join(self.assets, "stem.wav"))
        os.symlink(outside, os.path.join(self.assets, "stem.wav"))
        self.assertEqual(rejected(lambda: self.plan()), {"symlink_rejected"})
        os.remove(os.path.join(self.assets, "stem.wav"))
        write(os.path.join(self.assets, "stem.wav"), self.wav)
        os.rename(os.path.join(self.assets, "clip.mp4"), os.path.join(self.tmp, "outside", "clip.mp4"))
        os.symlink(os.path.join(self.tmp, "outside", "clip.mp4"), os.path.join(self.assets, "clip.mp4"))
        self.assertEqual(rejected(lambda: self.plan()), {"symlink_rejected"})

    def test_reparse_points_are_treated_as_links(self):
        import stat as st
        from video_core import paths as P
        plain = os.lstat(os.path.join(self.assets, "stem.wav"))
        stub = mock.Mock(st_mode=plain.st_mode, st_file_attributes=st.FILE_ATTRIBUTE_REPARSE_POINT)
        real = os.lstat
        with mock.patch.object(P.os, "lstat", lambda p: stub if str(p).endswith("clip.mp4") else real(p)):
            self.assertEqual(rejected(lambda: self.plan()), {"symlink_rejected"})

    def test_output_location_rules(self):
        inside = self.mkdir("assets", "o")
        self.assertEqual(rejected(lambda: self.plan(output_root=inside, output_name="r")), {"output_overlaps_assets"})
        os.mkdir(os.path.join(self.out, "taken"))
        self.assertEqual(rejected(lambda: self.plan(output_root=self.out, output_name="taken")), {"output_exists"})
        for bad in ("../x", "a/b", "", "-r", "a b"):
            self.assertEqual(rejected(lambda: self.plan(output_root=self.out, output_name=bad)), {"output_name_invalid"}, bad)
        self.assertEqual(rejected(lambda: self.plan(output_root=self.out)), {"output_incomplete"})


class AudioReceiptQualification(World):
    def receipt(self, **over):
        rec = {"status": "MIXED_VERIFIED", "plan_sha256": "ab" * 32, "outputs": [{"role": "stem", "name": "stem.wav", "sha256": sha(self.wav)}],
               "readback": {"wav": {"sample_count": 4800}}}
        rec.update(over)
        data = json.dumps(rec).encode()
        write(os.path.join(self.assets, "audio-receipt.json"), data)
        return {"path": "audio-receipt.json", "sha256": sha(data)}

    def doc(self, ref):
        return mut(lambda d: d["audio"].update(receipt=ref))

    def test_a_verified_audio_receipt_qualifies_the_stem(self):
        c = self.plan(self.doc(self.receipt())).canonical["audio"]["receipt"]
        self.assertEqual((c["status"], c["plan_sha256"]), ("MIXED_VERIFIED", "ab" * 32))

    def test_unverified_clamped_rejected_or_mismatching_receipts_are_refused(self):
        for status in ("MIXED_WITH_CLAMPING", "CLIPPING_REJECTED", "HOLD", "UNKNOWN", "TIMEOUT", "CANCELLED", "READBACK_MISMATCH", "IN_PROGRESS"):
            self.assertEqual(rejected(lambda: self.plan(self.doc(self.receipt(status=status)))), {"audio_receipt_not_verified"}, status)
        self.assertEqual(rejected(lambda: self.plan(self.doc(self.receipt(outputs=[{"role": "stem", "sha256": "0" * 64}])))), {"audio_receipt_stem_mismatch"})
        self.assertEqual(rejected(lambda: self.plan(self.doc(self.receipt(outputs=[])))), {"audio_receipt_stem_mismatch"})
        self.assertEqual(rejected(lambda: self.plan(self.doc(self.receipt(readback={"wav": {"sample_count": 4799}})))), {"audio_receipt_sample_mismatch"})
        ref = self.receipt()
        ref["sha256"] = "1" * 64
        self.assertEqual(rejected(lambda: self.plan(self.doc(ref))), {"audio_receipt_sha256_mismatch"})
        write(os.path.join(self.assets, "audio-receipt.json"), b"not json")
        self.assertEqual(rejected(lambda: self.plan(self.doc({"path": "audio-receipt.json", "sha256": sha(b"not json")}))), {"audio_receipt_unreadable"})


class WindowsBounds(World):
    EMOJI = "\U0001F600"

    def test_non_bmp_roots_count_two_utf16_units_each(self):
        root = self.mkdir("assets-" + self.EMOJI * 6)
        seed_inputs(root)
        p = self.plan(asset_root=root)
        b = p.observations["argv_budget"]
        exe = p.observations["tools_located"]["ffmpeg"]["path"]  # the plan estimates with the real tool path when it is found
        argv = planmod.estimate_argv(p.canonical["ffmpeg"]["argv"], os.path.realpath(root), None, exe)
        self.assertEqual(b["estimated_windows_cmdline_utf16_units"], L.cmdline_utf16_units(argv))
        self.assertGreater(b["estimated_windows_cmdline_utf16_units"], len(__import__("subprocess").list2cmdline(argv)), "emoji cost 2 units, not 1")

    def test_windows_limit_is_enforced_before_anything_exists_when_the_platform_is_windows(self):
        root = self.mkdir("assets-" + self.EMOJI * 6)
        seed_inputs(root)
        p0 = self.plan(asset_root=root)
        base = p0.observations["argv_budget"]["estimated_windows_cmdline_utf16_units"]
        before = tree_snapshot(self.tmp)
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(rejected(lambda: self.plan(asset_root=root, limits=Limits(max_windows_cmdline_chars=base - 1))), {"limit_cmdline_windows"})
            self.assertEqual(self.plan(asset_root=root, limits=Limits(max_windows_cmdline_chars=base)).observations["argv_budget"]["fits_windows_limit"], True)
        with mock.patch.object(L, "is_windows", return_value=False):
            self.assertFalse(self.plan(asset_root=root, limits=Limits(max_windows_cmdline_chars=base - 1)).observations["argv_budget"]["fits_windows_limit"], "reported, not enforced off Windows")
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_byte_bound_and_ill_formed_text(self):
        self.assertEqual(rejected(lambda: self.plan(limits=Limits(max_argv_bytes=300))), {"limit_argv_bytes"})
        with mock.patch.object(L, "is_windows", return_value=True):
            self.assertEqual(rejected(lambda: planmod.check_argv_budget(["ffmpeg", "bad\ud800"], Limits())), {"argv_ill_formed_utf16"})


class PurePlanningGuard(World):
    """validate/plan must create NO files and launch NO subprocesses, even though they read and hash inputs."""

    def test_plan_creates_nothing_and_launches_nothing_for_both_routes(self):
        import video_core.runner as R
        before = tree_snapshot(self.tmp)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        boom = mock.Mock(side_effect=AssertionError("run_bounded must not be called by pure planning"))
        try:
            with no_process(), mock.patch.object(R, "run_bounded", boom), mock.patch("video_core.tools.runner.run_bounded", boom):
                for r in ROUTES:
                    self.plan(av_manifest(r), output_root=self.out, output_name=f"planned-{r}")
                    load_manifest(dumps(av_manifest(r)), Limits())
        finally:
            os.chdir(cwd)
        self.assertEqual(tree_snapshot(self.tmp), before)
        boom.assert_not_called()

    def test_plan_does_not_open_anything_for_writing(self):
        real_open = open
        writes = []

        def spy(file, mode="r", *a, **k):
            if any(c in str(mode) for c in "wax+"):
                writes.append((file, mode))
            return real_open(file, mode, *a, **k)
        with mock.patch("builtins.open", spy), no_process():
            self.plan(output_root=self.out, output_name="n")
        self.assertEqual(writes, [])

    def test_cli_plan_and_validate_launch_nothing(self):
        import subprocess, sys
        from tests.helpers import ROOT
        m = write(os.path.join(self.tmp, "m.json"), dumps(av_manifest()))
        before = tree_snapshot(self.tmp)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        for args in (("validate", m), ("plan", m, "--asset-root", self.assets), (m, "--asset-root", self.assets)):
            r = subprocess.run([sys.executable, "-m", "video_core.avmux", *args], capture_output=True, cwd=self.tmp, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_other_commands_and_baseline_hash_are_untouched(self):
        from tests.helpers import sample_bytes
        from video_core import prepare
        self.assertEqual(prepare(sample_bytes()).sha256, "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21")
        self.assertIn("-an", [a for a in prepare(sample_bytes()).canonical["ffmpeg"]["argv"] if isinstance(a, str)])


if __name__ == "__main__":
    unittest.main()
