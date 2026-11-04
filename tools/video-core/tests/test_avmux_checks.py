import unittest
from fractions import Fraction

from tests.avmux_fakes import hash_text, out_probe, packets_json, video_probe, wav_probe
from video_core.avmux import checks as ck

FPS = Fraction(30)


def bad(checks):
    return {c["name"] for c in checks if not c["ok"]}


class SourceVideo(unittest.TestCase):
    def check(self, probe, **kw):
        args = dict(width=160, height=90, fps=FPS, frames=3)
        args.update(kw)
        return ck.check_video(probe, **args)

    def test_match(self):
        self.assertEqual(bad(self.check(video_probe())), set())

    def test_each_property_is_checked(self):
        self.assertEqual(bad(self.check(video_probe(codec="hevc"))), {"video_codec"})
        self.assertEqual(bad(self.check(video_probe(w=320))), {"video_width"})
        self.assertEqual(bad(self.check(video_probe(h=91))), {"video_height"})
        self.assertEqual(bad(self.check(video_probe(fps="2997/100"))), {"video_r_frame_rate", "video_avg_frame_rate"})
        self.assertEqual(bad(self.check(video_probe(fps="0/0"))), {"video_r_frame_rate", "video_avg_frame_rate"})
        self.assertEqual(bad(self.check(video_probe(frames=2))), {"video_frames"})
        self.assertEqual(bad(self.check(video_probe(frames=4))), {"video_frames"})
        self.assertEqual(bad(self.check(video_probe(start_pts=512, start_time="0.033333"))), {"video_start"})
        self.assertEqual(bad(self.check(video_probe(start_pts=None, start_time="N/A"))), {"video_start"}, "an unknown start is not 'starts at 0'")

    def test_rational_ntsc_rate(self):
        ntsc = Fraction(30000, 1001)
        self.assertEqual(bad(ck.check_video(video_probe(fps="30000/1001"), width=160, height=90, fps=ntsc, frames=3)), set())
        self.assertEqual(bad(ck.check_video(video_probe(fps="30/1"), width=160, height=90, fps=ntsc, frames=3)), {"video_r_frame_rate", "video_avg_frame_rate"})
        self.assertEqual(bad(ck.check_video(video_probe(fps="2997/100"), width=160, height=90, fps=ntsc, frames=3)), {"video_r_frame_rate", "video_avg_frame_rate"})

    def test_single_stream_layout(self):
        extra = {"index": 1, "codec_type": "audio", "codec_name": "aac"}
        self.assertEqual(bad(ck.check_layout_single_video(video_probe(extra=[extra]))), {"source_total_streams"})
        self.assertEqual(bad(ck.check_layout_single_video({"streams": [], "format": {}})), {"video_stream_count", "source_total_streams"})
        two_video = video_probe(extra=[video_probe()["streams"][0]])
        self.assertIn("video_stream_count", bad(ck.check_layout_single_video(two_video)))

    def test_every_check_has_a_rejection_code(self):
        names = {c["name"] for c in self.check(video_probe(codec="x"))} | {c["name"] for c in ck.check_audio_source(wav_probe(), channels=1, samples=4800)} | {c["name"] for c in ck.check_layout_single_video(video_probe())}
        self.assertTrue({n for n in names if n not in ck.CODES} <= {"source_total_streams"}, {n for n in names if n not in ck.CODES})


class SourceAudio(unittest.TestCase):
    def test_match_and_plain_wav_without_start_fields(self):
        self.assertEqual(bad(ck.check_audio_source(wav_probe(), channels=1, samples=4800)), set())

    def test_each_property_is_checked(self):
        def f(p, **kw):
            args = {"channels": 1, "samples": 4800}
            args.update(kw)
            return bad(ck.check_audio_source(p, **args))
        self.assertEqual(f(wav_probe(codec="pcm_f32le")), {"audio_codec"})
        self.assertEqual(f(wav_probe(rate="44100")), {"audio_sample_rate"})
        self.assertEqual(f(wav_probe(channels=2)), {"audio_channels"})
        self.assertEqual(f(wav_probe(samples=4799)), {"audio_duration_samples"})
        self.assertEqual(f(wav_probe(duration_ts=None, samples=4800), samples=4801), {"audio_duration_samples"})
        self.assertEqual(f(wav_probe(start_pts=5)), {"audio_start"})
        self.assertEqual(f({"streams": [], "format": {}}), {"audio_stream_count", "audio_codec", "audio_sample_rate", "audio_channels", "audio_duration_samples"})
        stereo_extra = wav_probe()
        stereo_extra["streams"].append({"codec_type": "video"})
        self.assertIn("audio_stream_count", f(stereo_extra))


class OutputChecks(unittest.TestCase):
    def audio(self, probe, route="MOV_PCM_S16LE", **kw):
        return ck.check_output_audio(probe, route=route, **dict(channels=1, samples=4800, **kw))

    def test_mov_pcm_requires_exact_start_and_duration(self):
        checks, obs = self.audio(out_probe("MOV_PCM_S16LE"))
        self.assertEqual(bad(checks), set())
        self.assertEqual(obs["duration_ts"], 4800)
        self.assertEqual(bad(self.audio(out_probe("MOV_PCM_S16LE", samples=4799))[0]), {"output_audio_duration_samples_exact"})
        self.assertEqual(bad(self.audio(out_probe("MOV_PCM_S16LE", a_start_pts=1, a_start_time="0.000021"))[0]), {"output_audio_start_pts_zero"})
        self.assertEqual(bad(self.audio(out_probe("MOV_PCM_S16LE", a_codec="aac"))[0]), {"output_audio_codec"})
        self.assertEqual(bad(self.audio(out_probe("MOV_PCM_S16LE", channels=2))[0]), {"output_audio_channels"})

    def test_mp4_aac_start_is_bounded_by_tolerance_and_duration_is_only_observed(self):
        tol = 1024 / 48000
        ok = out_probe("MP4_AAC", samples=4800 + 1024, a_start_pts=1024, a_start_time=f"{tol:.9f}")
        checks, obs = self.audio(ok, "MP4_AAC")
        self.assertEqual(bad(checks), set(), "within the documented one-frame tolerance")
        self.assertEqual(obs["duration_samples_from_container"], 5824.0, "observed and reported, never asserted exact")
        far = out_probe("MP4_AAC", a_start_pts=1025, a_start_time=f"{1025 / 48000:.9f}")
        self.assertEqual(bad(self.audio(far, "MP4_AAC")[0]), {"output_audio_start_within_tolerance"})
        self.assertEqual(bad(self.audio(out_probe("MP4_AAC", a_codec="pcm_s16le"), "MP4_AAC")[0]), {"output_audio_codec"})

    def test_layout(self):
        self.assertEqual(bad(ck.check_output_layout(out_probe())), set())
        self.assertEqual(bad(ck.check_output_layout({"streams": [video_probe()["streams"][0]]})), {"output_streams"})
        three = out_probe(extra=[{"codec_type": "subtitle"}])
        self.assertEqual(bad(ck.check_output_layout(three)), {"output_streams"})

    def test_tolerance_boundaries(self):
        for delta, ok in ((0, True), (1024, True), (-1024, True), (1025, False), (-1025, False)):
            self.assertEqual(bad(ck.tolerance_checks(4800 + delta, 4800, 1024)) == set(), ok, delta)
        c = ck.tolerance_checks(5000, 4800, 1024)[0]
        self.assertEqual(c["observed"], {"decoded": 5000, "delta": 200})


class HashAndPackets(unittest.TestCase):
    def test_parse_hash_lines(self):
        self.assertEqual(ck.parse_hash_lines(hash_text(["aa", "bb"])), [(28800, "aa"), (28800, "bb")])
        self.assertEqual(ck.parse_hash_lines("#only header\n\n"), [])
        for bad_line in ("0, 1, 2", "0, 0, 0, 1, notnum, aa", "0, 0, 0, 1, 5, ", "garbage"):
            with self.assertRaises(ValueError):
                ck.parse_hash_lines(bad_line)

    def test_real_ffmpeg_formatting_with_padding_and_extradata_header(self):
        text = "#format: frame checksums\n#extradata 0,                              51, c17c8a76\n#tb 0: 1/15360\n#stream#, dts,        pts, duration,     size, hash\n0,          0,          0,        1,   345600, ff9940e9748415c6c3247cdc3716a55b\n"
        self.assertEqual(ck.parse_hash_lines(text), [(345600, "ff9940e9748415c6c3247cdc3716a55b")])

    def test_packet_summary_reports_side_data_only_when_present(self):
        s = ck.packet_summary(packets_json(skip=1024, padding=1000))
        self.assertEqual((s["packet_count"], s["skip_samples_side_data_seen"], s["skip_samples"], s["discard_padding"]), (6, True, 1024, 1000))
        self.assertEqual(s["first_packet"]["pts"], 0)
        none = ck.packet_summary(packets_json(skip=None, padding=None))
        self.assertEqual((none["skip_samples_side_data_seen"], none["skip_samples"], none["discard_padding"]), (False, None, None))
        self.assertIn("absence is not evidence of zero", none["note"])
        self.assertEqual(ck.packet_summary({})["packet_count"], 0)


if __name__ == "__main__":
    unittest.main()
