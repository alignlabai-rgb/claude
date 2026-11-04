"""avmux orchestration against FAKE ffmpeg/ffprobe (tests/avmux_fakes.py). They prove process handling, receipts, rejection ordering and
file protection; they are never render or mux evidence."""

import hashlib
import json
import os
import unittest

from tests.audio_fixtures import dumps, sha, tone, wav_bytes, write
from tests.avmux_fakes import (FRAMES, PACKETS, av_manifest, calls, default_behavior, hash_text, make_av_tools, mux_calls, out_probe, packets_json, seed_inputs,
                               video_probe, wav_probe)
from tests.helpers import POSIX, TmpCase, jl, rb, tree_snapshot
from video_core import Limits, receipt as rc
from video_core.avmux import mux as M
from video_core.avmux.mux import mux
from video_core.errors import UnsafePath, ValidationFailed

PCM = wav_bytes(tone("square", 480, 4800, 8000))[44:]


def aac_audio(samples):
    return ((b"\x05\x00" * samples)).hex()


@unittest.skipUnless(POSIX, "fake tools are POSIX scripts")
class Base(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets, self.out, self.bin = self.mkdir("assets"), self.mkdir("out"), self.mkdir("bin")
        self.video, self.wav = seed_inputs(self.assets)

    def tools(self, route="MOV_PCM_S16LE", **over):
        return make_av_tools(self.bin, **default_behavior(route, **over))

    def go(self, route="MOV_PCM_S16LE", doc=None, name="r1", limits=Limits(), **kw):
        return mux(dumps(doc or av_manifest(route)), limits=limits, asset_root=self.assets, output_root=self.out, output_name=name,
                   ffmpeg=kw.pop("ffmpeg", os.path.join(self.bin, "ffmpeg")), ffprobe=kw.pop("ffprobe", os.path.join(self.bin, "ffprobe")), **kw)

    def rdir(self, name="r1"):
        return os.path.join(self.out, name)

    def failed_checks(self, r):
        return {c["name"] for c in r["checks"] if not c["ok"]}

    def assert_nothing_created_nothing_muxed(self, before, fn):
        with self.assertRaises((ValidationFailed, UnsafePath)) as cm:
            fn()
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(mux_calls(self.bin), [], "the mux was never launched")
        return {i.code for i in cm.exception.issues}


class MovRoute(Base):
    def test_verified_sample_exact_receipt(self):
        self.tools()
        r = self.go()
        d = self.rdir()
        self.assertEqual(r["status"], M.S_MOV_OK)
        self.assertEqual(jl(os.path.join(d, "receipt.json")), json.loads(json.dumps(r)))
        tv = r["technical_verdict"]
        self.assertEqual((tv["contract"], tv["met"], tv["sample_exact"], tv["tolerance"]), ("MOV_PCM_S16LE:sample_exact_pcm", True, True, None))
        self.assertEqual((r["quality_evaluated"], r["creative_acceptance"], tv["quality_evaluated"], tv["perceptual_sync_evaluated"]), (False, "NOT_EVALUATED", False, False))
        self.assertIn("Technical verdict only", r["note"])
        self.assertTrue(all(c["ok"] for c in r["checks"]))
        phases = [e["phase"] for e in r["executions"]]
        self.assertEqual(phases, ["probe_version:ffmpeg", "probe_version:ffprobe", "probe_capability:-encoders", "probe_capability:-demuxers", "probe_capability:-muxers", "probe_capability:-decoders",
                                  "preflight_probe_video", "preflight_probe_audio", "mux", "readback_probe_output", "readback_decoded_source", "readback_decoded_output",
                                  "readback_packets_source", "readback_packets_output", "readback_decode_audio_output"])
        for e in r["executions"]:
            self.assertEqual((e["outcome"], e["exit_code"], e["signal"], e["timed_out"], e["drain_complete"], e["orphan_group_killed"]), ("EXITED_ZERO", 0, None, False, True, False))
        self.assertEqual(sorted(os.listdir(d)), ["fake_observed.json", "ffprobe.output.json", "output.mov", "plan.canonical.json", "receipt.json"])
        self.assertEqual(r["tmp_cleanup"], "removed")
        outs = {o["name"]: o for o in r["outputs"]}
        self.assertEqual(outs["output.mov"]["sha256"], sha(rb(os.path.join(d, "output.mov"))))
        self.assertEqual(r["plan_sha256"], sha(rb(os.path.join(d, "plan.canonical.json"))))
        self.assertEqual(r["observations"]["decoded_audio"], {"bytes": 9600, "samples": 4800, "sha256": sha(PCM)})
        self.assertEqual(r["tools"]["ffmpeg"]["version"], "0.0-fake")

    def test_receipt_pins_inputs_before_and_after_and_the_measured_preflight(self):
        self.tools()
        r = self.go()
        i = r["input"]
        self.assertEqual((i["video"]["sha256_before"], i["video"]["sha256_after"], i["audio"]["sha256_before"], i["audio"]["sha256_after"]), (sha(self.video), sha(self.video), sha(self.wav), sha(self.wav)))
        self.assertEqual((i["inputs_unchanged"], i["audio"]["pcm_sha256"]), (True, sha(self.wav[44:])))
        self.assertEqual(r["preflight"]["video_properties_basis"], "MEASURED_BY_FFPROBE_AT_PREFLIGHT")
        self.assertTrue(all(c["ok"] for c in r["preflight"]["source_checks"]))
        self.assertEqual(r["selection"]["ref"], "caller-selection/synthetic-1")

    def test_actual_mux_command_preserves_video_and_adds_no_hiding_flags(self):
        self.tools()
        r = self.go()
        call = mux_calls(self.bin)[0]
        argv = call["argv"]
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        self.assertEqual(argv[argv.index("-c:a") + 1], "pcm_s16le")
        for banned in ("-shortest", "-t", "-ss", "-r", "-ar", "-af", "-vsync", "-fps_mode", "-vf", "-ac"):
            self.assertNotIn(banned, argv)
        self.assertEqual(argv[-1], os.path.join(self.rdir(), "output.mov.partial"))
        self.assertTrue(call["cwd"].endswith(os.path.join("r1", "tmp")))
        self.assertEqual(call["home"], call["cwd"])
        self.assertEqual(r["executions"][8]["argv"][1:], argv)
        inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
        self.assertEqual(inputs, [os.path.join(self.assets, "clip.mp4"), os.path.join(self.assets, "stem.wav")])
        self.assertEqual([a for a in argv if a.startswith(self.out)], [os.path.join(self.rdir(), "output.mov.partial")], "only the fresh run dir is written")

    def test_receipt_is_in_progress_while_muxing(self):
        self.tools()
        self.go()
        self.assertEqual(jl(os.path.join(self.rdir(), "fake_observed.json"))["receipt_status_during_mux"], rc.STATUS_IN_PROGRESS)

    def test_stereo_source_is_checked_with_its_own_channel_count(self):
        wav = wav_bytes([1000, -1000] * 4800, 2)
        write(os.path.join(self.assets, "stem.wav"), wav)
        self.tools(channels=2, pcm=wav[44:])
        r = self.go(doc=av_manifest(wav=wav, channels=2))
        self.assertEqual(r["status"], M.S_MOV_OK)
        self.assertEqual(r["observations"]["decoded_audio"]["bytes"], 19200)

    def test_decoded_pcm_must_be_byte_identical(self):
        flipped = bytearray(PCM)
        flipped[100] ^= 1
        self.tools(decoded_audio_hex=bytes(flipped).hex())
        r = self.go()
        self.assertEqual(r["status"], M.S_MISMATCH)
        self.assertEqual(self.failed_checks(r), {"decoded_pcm_sha256_equals_wav_pcm"})
        self.assertEqual((r["technical_verdict"]["met"], r["technical_verdict"]["sample_exact"]), (False, False))
        d = self.rdir()
        self.assertTrue(os.path.exists(os.path.join(d, "output.mov.mismatch")))
        self.assertFalse(os.path.exists(os.path.join(d, "output.mov")), "a contract failure must not look like a deliverable")
        self.assertEqual([o["role"] for o in r["outputs"] if o["name"].endswith(".mismatch")], ["media_unverified"])

    def test_one_missing_sample_is_a_mismatch_with_both_counts(self):
        self.tools(decoded_audio_hex=PCM[:-2].hex())
        r = self.go()
        self.assertEqual(r["status"], M.S_MISMATCH)
        self.assertIn("decoded_pcm_bytes_exact", self.failed_checks(r))
        c = next(c for c in r["checks"] if c["name"] == "decoded_pcm_bytes_exact")
        self.assertEqual((c["expected"], c["observed"]), (9600, 9598))

    def test_container_level_deviations_fail_the_mov_contract(self):
        self.tools(probe_out=out_probe("MOV_PCM_S16LE", a_start_pts=1, a_start_time="0.000021"))
        self.assertIn("output_audio_start_pts_zero", self.failed_checks(self.go()))
        self.tools(probe_out=out_probe("MOV_PCM_S16LE", samples=4799))
        self.assertIn("output_audio_duration_samples_exact", self.failed_checks(self.go(name="r2")))
        self.tools(probe_out=out_probe("MOV_PCM_S16LE", a_codec="aac"))
        self.assertIn("output_audio_codec", self.failed_checks(self.go(name="r3")))
        self.tools(probe_out=out_probe("MOV_PCM_S16LE", extra=[{"codec_type": "data"}]))
        self.assertIn("output_streams", self.failed_checks(self.go(name="r4")))

    def test_video_must_be_preserved_decoded_frames_and_copied_packets(self):
        self.tools(framemd5_out=hash_text(FRAMES[:2] + ["f" * 64]))
        r = self.go()
        self.assertEqual((r["status"], self.failed_checks(r)), (M.S_MISMATCH, {"decoded_video_frames_equal"}))
        self.tools(framehash_out=hash_text(PACKETS[:2] + ["e" * 64]))
        r = self.go(name="r2")
        self.assertEqual((r["status"], self.failed_checks(r)), (M.S_MISMATCH, {"copied_video_packets_equal"}))
        self.tools(framemd5_out=hash_text(FRAMES[:2]), framemd5_src=hash_text(FRAMES[:2]))
        r = self.go(name="r3")
        self.assertIn("decoded_video_frames_equal", self.failed_checks(r), "equal hashes are not enough: the frame count must equal the declared total")
        self.tools(probe_out=out_probe("MOV_PCM_S16LE", video=video_probe(frames=2)))
        self.assertIn("output_video_frames", self.failed_checks(self.go(name="r4")))

    def test_decode_errors_on_stderr_fail_the_verification(self):
        self.tools(decode_stderr="[h264] error while decoding MB 3 4")
        r = self.go()
        self.assertEqual(r["status"], M.S_MISMATCH)
        self.assertTrue({"readback_decoded_output_no_decode_errors"} <= self.failed_checks(r))

    def test_decode_that_cannot_run_is_inspection_failed_not_a_pass(self):
        self.tools(decode_exit=1)
        r = self.go()
        self.assertEqual(r["status"], M.S_INSPECT_FAILED)
        self.assertIn("error", " ".join(r["observations"]))
        self.assertTrue(os.path.exists(os.path.join(self.rdir(), "output.mov.mismatch")))

    def test_unreadable_output_is_inspection_failed_and_quarantined(self):
        self.tools(out_probe_garbage=True)
        r = self.go()
        self.assertEqual(r["status"], M.S_INSPECT_FAILED)
        self.assertTrue(os.path.exists(os.path.join(self.rdir(), "output.mov.mismatch")))
        self.assertFalse(os.path.exists(os.path.join(self.rdir(), "output.mov")))


class Mp4AacRoute(Base):
    def test_within_tolerance_is_reported_as_technical_not_sample_exact(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), packets=packets_json(skip=1024, padding=1000))
        r = self.go("MP4_AAC")
        self.assertEqual(r["status"], M.S_MP4_OK)
        tv = r["technical_verdict"]
        self.assertEqual((tv["contract"], tv["met"], tv["sample_exact"], tv["tolerance"]), ("MP4_AAC:lossy_within_tolerance", True, False, {"samples": 1024}))
        o = r["observations"]["aac_observations"]
        self.assertEqual((o["decoded_samples"], o["expected_samples"], o["delta_samples"], o["exact_sample_count"]), (4800, 4800, 0, True))
        self.assertEqual((o["sample_exact_claimed"], o["perceptual_sync_evaluated"]), (False, False))
        self.assertIn("not a sample-exact claim", o["note"])
        pk = r["observations"]["audio_packets"]
        self.assertEqual(pk["packet_count"], 6)
        self.assertEqual(pk["skip_samples"]["nonzero"], [{"packet_index": 0, "value": 1024}])
        self.assertEqual(pk["discard_padding"]["nonzero"], [{"packet_index": 5, "value": 1000}])
        self.assertEqual(pk["discard_padding"]["observed_zero_in_records"], 1, "the first record's observed discard_padding=0 is preserved as a zero")
        self.assertEqual([r["observed"] for r in pk["side_data_records"]], [{"skip_samples": 1024, "discard_padding": 0}, {"skip_samples": 0, "discard_padding": 1000}])
        self.assertEqual(r["observations"]["output_audio_stream"]["time_base"], "1/48000")
        self.assertTrue(os.path.exists(os.path.join(self.rdir(), "output.mp4")))
        self.assertEqual([e["phase"] for e in r["executions"]][-2:], ["readback_decode_audio_output", "readback_audio_packets"])
        argv = mux_calls(self.bin)[0]["argv"]
        self.assertEqual((argv[argv.index("-c:a") + 1], argv[argv.index("-b:a") + 1], argv[argv.index("-f", argv.index("-fs")) + 1]), ("aac", "128k", "mp4"))

    def test_decoded_count_off_by_up_to_one_aac_frame_is_reported_with_its_delta(self):
        for delta in (1024, -1024, 37, 1):
            self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800 + delta))
            r = self.go("MP4_AAC", name=f"d{delta}".replace("-", "m"))
            self.assertEqual(r["status"], M.S_MP4_OK, delta)
            o = r["observations"]["aac_observations"]
            self.assertEqual((o["delta_samples"], o["exact_sample_count"]), (delta, False))
            self.assertFalse(r["technical_verdict"]["sample_exact"])

    def test_decoded_count_beyond_one_frame_cannot_meet_the_declared_contract(self):
        for delta in (1025, -1025, 4800):
            self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800 + delta) if delta > -4800 else "")
            r = self.go("MP4_AAC", name=f"x{abs(delta)}{'p' if delta > 0 else 'm'}")
            self.assertEqual(r["status"], M.S_MISMATCH, delta)
            self.assertEqual(self.failed_checks(r), {"aac_decoded_samples_within_tolerance"})
            self.assertFalse(r["technical_verdict"]["met"])
            self.assertTrue(os.path.exists(os.path.join(self.rdir(r["input"] and f"x{abs(delta)}{'p' if delta > 0 else 'm'}"), "output.mp4.mismatch")))

    def test_start_offset_beyond_tolerance_is_a_mismatch_not_hidden(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), probe_out=out_probe("MP4_AAC", a_start_pts=2048, a_start_time=f"{2048 / 48000:.9f}"))
        r = self.go("MP4_AAC")
        self.assertEqual((r["status"], self.failed_checks(r)), (M.S_MISMATCH, {"output_audio_start_within_tolerance"}))
        self.assertEqual(r["observations"]["output_audio_stream"]["start_pts"], 2048)

    def test_start_offset_inside_tolerance_is_reported_exactly(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), probe_out=out_probe("MP4_AAC", a_start_pts=1024, a_start_time=f"{1024 / 48000:.9f}"))
        r = self.go("MP4_AAC")
        self.assertEqual(r["status"], M.S_MP4_OK)
        self.assertEqual(r["observations"]["output_audio_stream"]["start_time"], "0.021333333")

    def test_missing_packet_side_data_is_reported_as_absent_never_as_zero(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), packets=packets_json(skip=None, padding=None))
        r = self.go("MP4_AAC")
        pk = r["observations"]["audio_packets"]
        self.assertEqual((r["status"], pk["side_data_record_count"], pk["side_data_records"]), (M.S_MP4_OK, 0, []))
        self.assertEqual((pk["skip_samples"]["reported_in_records"], pk["skip_samples"]["observed_zero_in_records"], pk["discard_padding"]["observed_zero_in_records"]), (0, 0, 0))
        self.assertIn("NOT reported", pk["note"])

    def test_unusable_packet_listing_is_reported_but_does_not_fake_observations(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), packets={"not": "packets"})
        r = self.go("MP4_AAC")
        self.assertEqual(r["observations"]["audio_packets"]["packet_count"], 0)

    def test_video_preservation_is_still_required_on_the_mp4_route(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), framehash_out=hash_text(PACKETS[:2] + ["c" * 64]))
        r = self.go("MP4_AAC")
        self.assertEqual((r["status"], self.failed_checks(r)), (M.S_MISMATCH, {"copied_video_packets_equal"}))

    def test_a_pcm_audio_stream_in_the_mp4_output_violates_the_route(self):
        self.tools("MP4_AAC", decoded_audio_hex=aac_audio(4800), probe_out=out_probe("MP4_AAC", a_codec="pcm_s16le"))
        self.assertIn("output_audio_codec", self.failed_checks(self.go("MP4_AAC")))


class PreflightRejections(Base):
    """Measured input properties that disagree with the declaration are rejected before a directory exists or the mux is launched."""

    def reject(self, **behavior):
        self.tools(**behavior)
        before = tree_snapshot(self.out)
        return self.assert_nothing_created_nothing_muxed(before, self.go)

    def test_video_property_mismatches(self):
        self.assertEqual(self.reject(probe_video=video_probe(codec="hevc")), {"video_codec_mismatch"})
        self.assertEqual(self.reject(probe_video=video_probe(w=320)), {"video_size_mismatch"})
        self.assertEqual(self.reject(probe_video=video_probe(fps="2997/100")), {"video_fps_mismatch"})
        self.assertEqual(self.reject(probe_video=video_probe(frames=4)), {"video_frame_count_mismatch"})
        self.assertEqual(self.reject(probe_video=video_probe(start_pts=512, start_time="0.033333")), {"video_start_nonzero"})

    def test_video_stream_layout(self):
        extra = {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2}
        codes = self.reject(probe_video=video_probe(extra=[extra]))
        self.assertEqual(codes, {"input_property_mismatch"} | codes)
        self.assertTrue(codes)

    def test_rational_fps_is_matched_exactly_at_ntsc(self):
        d = av_manifest(fps=(30000, 1001), total=3, samples=4805)
        wav = wav_bytes(tone("square", 480, 4805, 8000))
        write(os.path.join(self.assets, "stem.wav"), wav)
        d = av_manifest(fps=(30000, 1001), total=3, samples=4805, wav=wav)
        ntsc = video_probe(fps="30000/1001")
        self.tools(probe_video=ntsc, probe_wav=wav_probe(1, 4805), probe_out=out_probe(samples=4805, video=ntsc), pcm=wav[44:], samples=4805)
        self.assertEqual(self.go(doc=d)["status"], M.S_MOV_OK)
        self.tools(probe_video=video_probe(fps="30/1"), probe_wav=wav_probe(1, 4805), pcm=wav[44:])
        os.remove(os.path.join(self.bin, "calls.log"))  # the second half must show that no mux is launched for a 30/1 video declared as 30000/1001
        before = tree_snapshot(self.out)
        self.assertEqual(self.assert_nothing_created_nothing_muxed(before, lambda: self.go(doc=d, name="n2")), {"video_fps_mismatch"})

    def test_audio_as_measured_by_ffprobe_must_match_the_strict_wav_parse(self):
        self.assertEqual(self.reject(probe_wav=wav_probe(channels=2)), {"audio_probe_mismatch"})
        self.assertEqual(self.reject(probe_wav=wav_probe(samples=4799)), {"audio_probe_mismatch"})
        self.assertEqual(self.reject(probe_wav=wav_probe(rate="44100")), {"audio_probe_mismatch"})
        self.assertEqual(self.reject(probe_wav=wav_probe(codec="pcm_f32le")), {"audio_probe_mismatch"})
        self.assertEqual(self.reject(probe_wav=wav_probe(start_pts=7)), {"audio_probe_mismatch"})

    def test_ffprobe_failure_is_reported_precisely(self):
        self.assertEqual(self.reject(probe_exit=1), {"preflight_probe_failed"})

    def test_all_mismatches_are_listed_together_with_expected_and_measured(self):
        self.tools(probe_video=video_probe(codec="hevc", w=320, frames=4))
        with self.assertRaises(ValidationFailed) as cm:
            self.go()
        msgs = " ".join(i.message for i in cm.exception.issues)
        self.assertEqual({i.code for i in cm.exception.issues}, {"video_codec_mismatch", "video_size_mismatch", "video_frame_count_mismatch"})
        self.assertIn("expected 'h264', measured 'hevc'", msgs)

    def test_manifest_and_plan_level_rejections_happen_before_any_tool_is_called(self):
        self.tools()
        before = tree_snapshot(self.out)
        for doc in (av_manifest(samples=4799), mut_state("video", "PROXY"), mut_state("audio", "HOLD"), mut_state("audio", "MISSING"), mut_sha("video"), mut_sha("audio")):
            with self.assertRaises((ValidationFailed, UnsafePath)):
                self.go(doc=doc)
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(calls(self.bin), [], "not even a version probe ran")


def mut_state(which, state):
    d = av_manifest()
    d[which]["state"] = state
    return d


def mut_sha(which):
    d = av_manifest()
    d[which]["sha256"] = "0" * 64
    return d


class ToolAvailability(Base):
    def test_missing_tools_are_skipped_without_creating_anything(self):
        before = tree_snapshot(self.tmp)
        r = self.go(ffmpeg=os.path.join(self.tmp, "no-ffmpeg"), ffprobe=os.path.join(self.tmp, "no-ffprobe"))
        self.assertEqual((r["status"], r["written"]), (rc.STATUS_SKIPPED, False))
        self.assertEqual({x["tool"] for x in r["reasons"]}, {"ffmpeg", "ffprobe"})
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_each_route_needs_its_own_encoder_muxer_and_decoder(self):
        cases = (("MP4_AAC", {"encoders": ["pcm_s16le"]}, "encoders", ["aac"]),
                 ("MOV_PCM_S16LE", {"encoders": ["aac"]}, "encoders", ["pcm_s16le"]),
                 ("MP4_AAC", {"muxers": ["mov", "framemd5", "framehash", "s16le"]}, "muxers", ["mp4"]),
                 ("MOV_PCM_S16LE", {"muxers": ["mp4", "framemd5", "framehash", "s16le"]}, "muxers", ["mov"]),
                 ("MOV_PCM_S16LE", {"muxers": ["mov", "framehash", "s16le"]}, "muxers", ["framemd5"]),
                 ("MOV_PCM_S16LE", {"decoders": ["pcm_s16le"]}, "decoders", ["h264"]),
                 ("MP4_AAC", {"decoders": ["h264", "pcm_s16le"]}, "decoders", ["aac"]),
                 ("MOV_PCM_S16LE", {"demuxers": ["mov"]}, "demuxers", ["wav"]))
        for i, (route, over, kind, missing) in enumerate(cases):
            self.tools(route, **over)
            r = self.go(route, name=f"s{i}")
            self.assertEqual(r["status"], rc.STATUS_SKIPPED, (route, over))
            self.assertEqual(r["reasons"][0]["detail"]["missing"], {kind: missing}, (route, over))
            self.assertFalse(os.path.exists(self.rdir(f"s{i}")))
        self.assertEqual(mux_calls(self.bin), [])

    def test_the_mov_route_does_not_need_aac_and_the_mp4_route_does_not_need_pcm_encoder(self):
        self.tools("MOV_PCM_S16LE", encoders=["pcm_s16le"], decoders=["h264", "pcm_s16le"], muxers=["mov", "framemd5", "framehash", "s16le"])
        self.assertEqual(self.go("MOV_PCM_S16LE")["status"], M.S_MOV_OK)
        self.tools("MP4_AAC", encoders=["aac"], decoders=["h264", "aac"], muxers=["mp4", "framemd5", "framehash", "s16le"], decoded_audio_hex=aac_audio(4800))
        self.assertEqual(self.go("MP4_AAC", name="r2")["status"], M.S_MP4_OK)

    def test_unrecognised_listing_layout_is_probe_failed_not_missing_and_not_success(self):
        self.tools(listing_layout="unknown", unknown_flag="-muxers")
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_SKIPPED)
        d = r["reasons"][0]["detail"]
        self.assertEqual((d["probe_failed"], d["missing"]), (["-muxers"], {}))
        self.assertIn("not recognised", d["details"]["-muxers"]["reason"])
        self.assertFalse(os.path.exists(self.rdir()))

    def test_tool_that_cannot_report_its_version_is_unavailable(self):
        self.tools()
        with open(os.path.join(self.bin, "ffmpeg"), "w") as fh:
            fh.write("#!/nonexistent/interpreter\n")
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_SKIPPED)
        self.assertFalse(os.path.exists(self.rdir()))


class FailuresAndPartials(Base):
    def test_nonzero_exit_keeps_the_partial_and_never_retries(self):
        self.tools(mode="nonzero")
        r = self.go()
        d = self.rdir()
        self.assertEqual((r["status"], r["stage"]), (rc.STATUS_PROCESS_FAILED, "mux"))
        ex = r["executions"][-1]
        self.assertEqual((ex["phase"], ex["exit_code"], ex["signal"], ex["timed_out"]), ("mux", 7, None, False))
        self.assertIn("simulated mux failure", ex["stderr"]["text"])
        self.assertTrue(os.path.exists(os.path.join(d, "output.mov.partial")))
        self.assertEqual([f for f in os.listdir(d) if f.startswith("output.mov")], ["output.mov.partial"])
        self.assertEqual(len(mux_calls(self.bin)), 1)
        self.assertEqual(r["checks"], [])
        self.assertEqual(r["technical_verdict"]["met"], False)
        self.assertTrue([o for o in r["outputs"] if o["role"] == "media_partial"][0]["partial"])

    def test_timeout_is_bounded_and_preserves_the_partial(self):
        self.tools(mode="sleep")
        r = self.go(limits=Limits(max_runtime_seconds=0.7))
        ex = r["executions"][-1]
        self.assertEqual(r["status"], rc.STATUS_TIMEOUT)
        self.assertEqual((ex["phase"], ex["outcome"], ex["timed_out"], ex["kill_reason"]), ("mux", "TIMEOUT", True, "timeout"))
        self.assertLess(ex["wall_seconds"], 20)
        self.assertTrue(os.path.exists(os.path.join(self.rdir(), "output.mov.partial")))
        self.assertEqual(len(mux_calls(self.bin)), 1)

    def test_signal_termination_is_a_signal_not_a_timeout(self):
        self.tools(mode="sigkill")
        r = self.go()
        ex = r["executions"][-1]
        self.assertEqual(r["status"], rc.STATUS_SIGNALED)
        self.assertEqual((ex["signal"], ex["exit_code"], ex["timed_out"]), (9, None, False))

    def test_exit_zero_without_output_is_unknown(self):
        self.tools(mode="noout")
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertIn("no output", r["unknown_reason"])

    def test_surviving_child_makes_the_result_unknown_and_keeps_the_partial(self):
        self.tools(mode="orphan")
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertTrue(r["executions"][-1]["orphan_group_killed"])
        self.assertTrue(os.path.exists(os.path.join(self.rdir(), "output.mov.partial")))
        self.assertEqual(r["checks"], [], "no readback is trusted after an unclean process tree")

    def test_diagnostics_are_truncated_but_totals_and_hash_are_kept(self):
        self.tools(noise=300_000)
        r = self.go(limits=Limits(max_diag_bytes=1000))
        st = r["executions"][8]["stderr"]
        self.assertEqual(r["status"], M.S_MOV_OK)
        self.assertEqual((st["truncated"], st["total_bytes"], st["kept_bytes"]), (True, 300_000 + len("TAIL-MARKER"), 1000))
        self.assertEqual(st["sha256_full_stream"], hashlib.sha256(b"E" * 300_000 + b"TAIL-MARKER").hexdigest())
        self.assertIn("TAIL-MARKER", st["text"])
        self.assertLess(len(json.dumps(r)), 300_000)

    def test_an_input_changed_during_the_mux_is_unknown_not_verified(self):
        self.tools(touch_input=os.path.join(self.assets, "stem.wav"))
        r = self.go()
        self.assertEqual(r["status"], rc.STATUS_UNKNOWN)
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("input file changed", r["unknown_reason"])
        self.assertFalse(r["technical_verdict"]["met"])
        self.assertTrue(os.path.exists(os.path.join(self.rdir(), "output.mov.mismatch")))
        self.assertFalse(os.path.exists(os.path.join(self.rdir(), "output.mov")))

    def test_each_failure_is_one_mux_launch_only(self):
        for i, mode in enumerate(("nonzero", "sigkill", "noout", "orphan")):
            self.tools(mode=mode)
            self.go(name=f"m{i}")
        self.assertEqual(len(mux_calls(self.bin)), 4)


class Protection(Base):
    def test_overwrite_is_refused_and_the_first_result_is_untouched(self):
        self.tools()
        self.go()
        before = tree_snapshot(self.rdir())
        with self.assertRaises(UnsafePath) as cm:
            self.go()
        self.assertEqual(cm.exception.issues[0].code, "output_exists")
        self.assertEqual(tree_snapshot(self.rdir()), before)
        self.assertEqual(len(mux_calls(self.bin)), 1)

    def test_existing_directory_with_a_user_file_is_never_written_into(self):
        self.tools()
        keep = write(os.path.join(self.out, "r1", "precious.txt"), b"mine")
        before = tree_snapshot(self.out)
        with self.assertRaises(UnsafePath):
            self.go()
        self.assertEqual(tree_snapshot(self.out), before)
        self.assertEqual(rb(keep), b"mine")

    def test_inputs_and_outside_files_are_unchanged_and_only_the_run_dir_is_added(self):
        self.tools()
        sentinel = write(os.path.join(self.tmp, "outside", "sentinel.txt"), b"do not touch")
        before_assets, before_out = tree_snapshot(self.assets), tree_snapshot(self.out)
        r = self.go()
        self.assertEqual(r["status"], M.S_MOV_OK)
        self.assertEqual(tree_snapshot(self.assets), before_assets)
        self.assertEqual(rb(sentinel), b"do not touch")
        self.assertEqual({k.split(os.sep)[0] for k in tree_snapshot(self.out)} - set(before_out), {"r1"})

    def test_unsafe_output_locations_are_rejected_before_anything_runs(self):
        self.tools()
        for kw in ({"name": "../escape"}, {"name": "a b"}, {"name": ""}):
            with self.assertRaises(UnsafePath):
                self.go(**kw)
        with self.assertRaises(UnsafePath):
            mux(dumps(av_manifest()), asset_root=self.assets, output_root=self.assets, output_name="in-assets", ffmpeg=os.path.join(self.bin, "ffmpeg"), ffprobe=os.path.join(self.bin, "ffprobe"))
        self.assertEqual(calls(self.bin), [])

    def test_windows_command_line_bound_is_enforced_before_any_tool_runs(self):
        from unittest import mock
        from video_core import limits as L
        self.tools()
        with mock.patch.object(L, "is_windows", return_value=True):
            with self.assertRaises(ValidationFailed) as cm:
                self.go(limits=Limits(max_windows_cmdline_chars=300))
        self.assertEqual(cm.exception.issues[0].code, "limit_cmdline_windows")
        self.assertEqual(calls(self.bin), [])
        self.assertFalse(os.path.exists(self.rdir()))

    def test_materialized_argv_is_rechecked_with_the_real_roots(self):
        """Plan estimates with 260-character placeholders for unknown roots; the real argv is checked again right before creating the directory."""
        from unittest import mock
        from video_core import limits as L
        self.tools()
        real_check = M.check_argv_budget
        seen = []

        def spy(argv, limits, **kw):
            seen.append(list(argv))
            return real_check(argv, limits, **kw)
        with mock.patch.object(M, "check_argv_budget", spy):
            self.go()
        self.assertEqual(len(seen), 1)
        self.assertIn(os.path.join(self.assets, "clip.mp4"), seen[0])


if __name__ == "__main__":
    unittest.main()
