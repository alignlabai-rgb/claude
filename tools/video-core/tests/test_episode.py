import copy
import json
import unittest

from tests.helpers import color_clips, episode_doc, mutated, no_process, sample_bytes, sample_doc, to_bytes
from video_core import Limits, load_episode, prepare
from video_core.errors import ValidationFailed


def codes(doc_or_bytes, limits=Limits()):
    data = doc_or_bytes if isinstance(doc_or_bytes, bytes) else to_bytes(doc_or_bytes)
    with no_process():  # every rejection must happen before any process could launch
        try:
            load_episode(data, limits)
        except ValidationFailed as exc:
            return {i.code for i in exc.issues}
    return set()


class Accepts(unittest.TestCase):
    def test_supplied_episode(self):
        ep = load_episode(sample_bytes())
        self.assertEqual((ep.total_frames, ep.fps_n, ep.fps_d, ep.width, ep.height), (180, 30, 1, 640, 360))
        self.assertEqual([(c.start_frame, c.end_frame) for c in ep.captions], [(15, 75), (105, 165)])
        self.assertEqual([c.end_frame for c in ep.clips], [90, 180])

    def test_color_case_insensitive_and_normalized(self):
        doc = mutated(lambda d: d["clips"][0]["source"].update(value="#AABBCC"))
        self.assertEqual(load_episode(to_bytes(doc)).clips[0].source.value, "#aabbcc")

    def test_caption_may_end_exactly_at_total_frames(self):
        doc = mutated(lambda d: d["captions"][1].update(end_frame=180))
        self.assertEqual(codes(doc), set())


class RejectsTimeline(unittest.TestCase):
    def test_zero_and_negative_duration(self):
        self.assertIn("duration_not_positive", codes(mutated(lambda d: d["clips"][0].update(duration_frames=0))))
        self.assertIn("duration_not_positive", codes(mutated(lambda d: d["clips"][0].update(duration_frames=-5))))

    def test_negative_start(self):
        self.assertIn("out_of_range", codes(mutated(lambda d: d["clips"][0].update(start_frame=-1))))

    def test_overlap(self):
        doc = episode_doc(clips=[
            {"id": "a", "start_frame": 0, "duration_frames": 100, "source": {"kind": "color", "value": "#000000"}},
            {"id": "b", "start_frame": 90, "duration_frames": 90, "source": {"kind": "color", "value": "#ffffff"}}])
        self.assertIn("clip_overlap", codes(doc))

    def test_gap_in_the_middle(self):
        doc = episode_doc(clips=[
            {"id": "a", "start_frame": 0, "duration_frames": 80, "source": {"kind": "color", "value": "#000000"}},
            {"id": "b", "start_frame": 90, "duration_frames": 90, "source": {"kind": "color", "value": "#ffffff"}}])
        self.assertIn("coverage_gap", codes(doc))

    def test_first_clip_not_at_zero(self):
        doc = episode_doc(clips=[{"id": "a", "start_frame": 10, "duration_frames": 170, "source": {"kind": "color", "value": "#000000"}}])
        self.assertIn("coverage_gap", codes(doc))

    def test_missing_coverage_at_end(self):
        self.assertIn("coverage_incomplete", codes(episode_doc(clips=color_clips([90, 80]))))

    def test_clip_past_total(self):
        self.assertIn("clip_past_end", codes(episode_doc(clips=color_clips([90, 100]))))

    def test_out_of_order_listing(self):
        clips = color_clips([90, 90])
        clips.reverse()
        self.assertIn("clip_order", codes(episode_doc(clips=clips)))

    def test_no_clips(self):
        self.assertIn("clips_empty", codes(episode_doc(clips=[])))

    def test_duplicate_ids(self):
        clips = color_clips([90, 90])
        clips[1]["id"] = clips[0]["id"]
        self.assertIn("duplicate_id", codes(episode_doc(clips=clips)))
        caps = [{"id": "x", "start_frame": 1, "end_frame": 5, "text": "a"}, {"id": "x", "start_frame": 6, "end_frame": 9, "text": "b"}]
        self.assertIn("duplicate_id", codes(episode_doc(captions=caps)))

    def test_invalid_ids(self):
        for bad in ("", "a b", "é", "../x", "x" * 65, "a\n"):
            self.assertIn("invalid_id", codes(mutated(lambda d, b=bad: d["clips"][0].update(id=b))), repr(bad))

    def test_invalid_colors(self):
        for bad in ("#12345", "#1234567", "30506a", "#gggggg", "#30506a\n", "red", "#30 506a", "", None, 7):
            self.assertIn(
                "invalid_color" if isinstance(bad, str) else "invalid_color",
                codes(mutated(lambda d, b=bad: d["clips"][0]["source"].update(value=b))), repr(bad))

    def test_caption_after_total_frames(self):
        self.assertIn("caption_past_end", codes(mutated(lambda d: d["captions"][1].update(end_frame=181))))
        self.assertIn("caption_past_end", codes(mutated(lambda d: d["captions"][1].update(start_frame=170, end_frame=400))))

    def test_caption_zero_or_negative_length(self):
        self.assertIn("caption_not_positive", codes(mutated(lambda d: d["captions"][0].update(end_frame=15))))
        self.assertIn("caption_not_positive", codes(mutated(lambda d: d["captions"][0].update(end_frame=10))))

    def test_caption_text_rules(self):
        for bad, code in (("", "text_length"), ("a\r\nb", "text_control_char"), ("a\tb", "text_control_char"), ("a\x00b", "text_control_char"),
                          ("a\n\nb", "text_blank_line"), ("a\n", "text_blank_line"), ("  ", "text_blank_line"), ("\ud800", "text_invalid_unicode"),
                          ("x" * 513, "text_length")):
            self.assertIn(code, codes(mutated(lambda d, b=bad: d["captions"][0].update(text=b))), repr(bad))

    def test_unrepresentable_srt_resolution(self):
        doc = episode_doc(fps=(60000, 1), total=100, clips=color_clips([100]),
                          captions=[{"id": "a", "start_frame": 1, "end_frame": 2, "text": "x"}])
        self.assertIn("caption_srt_resolution", codes(doc, Limits(max_fps=60000)))


class RejectsStructure(unittest.TestCase):
    def test_types_unknown_fields_version(self):
        self.assertIn("unsupported_version", codes(mutated(lambda d: d.update(version=2))))
        self.assertIn("unsupported_version", codes(mutated(lambda d: d.update(version=True))))
        self.assertIn("unknown_field", codes(mutated(lambda d: d.update(extra=1))))
        self.assertIn("unknown_field", codes(mutated(lambda d: d["clips"][0].update(extra=1))))
        self.assertIn("missing_field", codes(mutated(lambda d: d.pop("captions"))))
        self.assertIn("type_error", codes(mutated(lambda d: d.update(total_frames=180.0))))
        self.assertIn("type_error", codes(mutated(lambda d: d.update(total_frames=True))))
        self.assertIn("type_error", codes(mutated(lambda d: d["fps"].update(n="30"))))
        self.assertIn("type_error", codes(b"[]"))
        self.assertIn("unknown_source_kind", codes(mutated(lambda d: d["clips"][0].update(source={"kind": "url", "value": "x"}))))

    def test_fps_and_canvas_ranges(self):
        self.assertIn("out_of_range", codes(mutated(lambda d: d["fps"].update(n=0))))
        self.assertIn("out_of_range", codes(mutated(lambda d: d["fps"].update(d=1002))))
        self.assertIn("out_of_range", codes(mutated(lambda d: d["fps"].update(n=60001))))
        self.assertIn("dimension_odd", codes(mutated(lambda d: d["canvas"].update(width=641))))
        self.assertIn("out_of_range", codes(mutated(lambda d: d["canvas"].update(height=8))))

    def test_integer_overflow(self):
        self.assertIn("int_overflow", codes(mutated(lambda d: d["clips"][0].update(duration_frames=2**63))))
        self.assertIn("int_overflow", codes(mutated(lambda d: d["captions"][0].update(end_frame=10**30))))
        self.assertIn("json_invalid", codes(b'{"version": ' + b"9" * 5000 + b"}"))

    def test_json_level(self):
        self.assertIn("json_duplicate_key", codes(b'{"version": 1, "version": 1}'))
        self.assertIn("json_non_finite", codes(sample_bytes().replace(b'"n": 30', b'"n": NaN')))
        self.assertIn("bom_not_allowed", codes(b"\xef\xbb\xbf" + sample_bytes()))
        self.assertIn("invalid_utf8", codes(b"\xff\xfe{}"))
        self.assertIn("json_invalid", codes(b"{not json"))
        self.assertIn("json_too_deep", codes(b"[" * 100000))
        self.assertIn("limit_input_bytes", codes(sample_bytes(), Limits(max_input_bytes=100)))

    def test_all_issues_are_reported_together(self):
        doc = mutated(lambda d: (d["clips"][0].update(duration_frames=0), d["captions"][0].update(end_frame=999), d.update(version=3)))
        self.assertTrue({"duration_not_positive", "caption_past_end", "unsupported_version"} <= codes(doc))


class RejectsResourceExcess(unittest.TestCase):
    """Every limit must trip in validation, i.e. before any process could be launched."""

    def test_schema_ceilings(self):
        self.assertIn("limit_frames", codes(episode_doc(total=18001, clips=color_clips([18001]))))
        self.assertIn("limit_dimension", codes(episode_doc(width=7682)))
        self.assertIn("limit_clips", codes(episode_doc(total=257, clips=color_clips([1] * 257))))
        caps = [{"id": f"c{i}", "start_frame": 0, "end_frame": 1, "text": "x"} for i in range(513)]
        self.assertIn("limit_captions", codes(episode_doc(captions=caps)))

    def test_caller_limits_are_enforced(self):
        self.assertIn("limit_frames", codes(sample_bytes(), Limits(max_frames=100)))
        self.assertIn("limit_dimension", codes(sample_bytes(), Limits(max_dimension=512)))
        self.assertIn("limit_pixel_volume", codes(sample_bytes(), Limits(max_pixel_volume=640 * 360 * 179)))
        self.assertIn("limit_duration", codes(sample_bytes(), Limits(max_duration_seconds=5)))
        self.assertIn("limit_fps", codes(episode_doc(fps=(240, 1)), Limits(max_fps=120)))
        self.assertIn("limit_clips", codes(sample_bytes(), Limits(max_clips=1)))
        self.assertIn("limit_captions", codes(sample_bytes(), Limits(max_captions=1)))
        self.assertIn("text_length", codes(sample_bytes(), Limits(max_caption_chars=3)))
        self.assertEqual(codes(sample_bytes(), Limits(max_frames=180, max_pixel_volume=640 * 360 * 180)), set())

    def test_default_pixel_volume_rejects_extreme_but_schema_legal_episode(self):
        doc = episode_doc(width=7680, height=7680, total=18000, clips=color_clips([18000]), captions=[])
        self.assertIn("limit_pixel_volume", codes(doc))

    def test_argv_size_limit_rejected_in_plan(self):
        with no_process():
            with self.assertRaises(ValidationFailed) as cm:
                prepare(sample_bytes(), limits=Limits(max_argv_bytes=200))
        self.assertIn("limit_argv_bytes", {i.code for i in cm.exception.issues})

    def test_limits_cannot_exceed_schema_ceiling_or_be_nonpositive(self):
        for kw in ({"max_frames": 18001}, {"max_dimension": 8000}, {"max_clips": 300}, {"max_frames": 0}, {"max_fps": 60001}, {"max_runtime_seconds": -1}):
            with self.assertRaises(ValueError):
                Limits(**kw)


class FileSourceSyntax(unittest.TestCase):
    def _doc(self, path):
        return mutated(lambda d: d["clips"][0].update(source={"kind": "file", "path": path}))

    def test_syntactic_rejections_without_filesystem(self):
        cases = {
            "/etc/passwd": "path_absolute", "\\\\server\\share\\a.mp4": "path_absolute", "C:\\x\\a.mp4": "path_absolute", "C:a.mp4": "path_absolute",
            "../a.mp4": "path_traversal", "a/../../b.mp4": "path_traversal",
            "http://example.com/a.mp4": "path_protocol", "file:a.mp4": "path_protocol", "concat:a.mp4|b.mp4": "path_protocol", "pipe:0": "path_protocol",
            "-i.mp4": "path_option_like", "dir/-x.mp4": "path_option_like", "--help": "path_option_like",
            "a\\b.mp4": "path_backslash", "a//b.mp4": "path_not_normalized", "./a.mp4": "path_not_normalized", "a/": "path_not_normalized",
            "a%03d.png": "path_forbidden_char", "a*.mp4": "path_forbidden_char", "a|b.mp4": "path_forbidden_char", "a;b.mp4": "path_forbidden_char",
            "a'b.mp4": "path_forbidden_char", "a,b.mp4": "path_forbidden_char", "a=b.mp4": "path_forbidden_char", "ab:c/d.mp4": "path_protocol", "a:b/c.mp4": "path_absolute",
            "dir/x:y.mp4": "path_forbidden_char", ".hidden.mp4": "path_hidden", "a\x00.mp4": "path_control_char", "a\n.mp4": "path_control_char",
            "a\u202e.mp4": "path_control_char", "NUL.mp4": "path_reserved_name", "a.mp4.": "path_windows_unsafe", "clip.exe": "source_extension_unsupported",
            "clip.m3u8": "source_extension_unsupported", "clip": "source_extension_unsupported", "": "path_empty",
        }
        for path, code in cases.items():
            self.assertIn(code, codes(self._doc(path)), repr(path))

    def test_ordinary_and_japanese_names_pass_syntax(self):
        for ok in ("a.mp4", "dir/sub dir/clip-1_v2.mov", "素材/テスト（１）.png", "x+y@z~.jpg"):
            self.assertEqual(codes(self._doc(ok)), set(), ok)


if __name__ == "__main__":
    unittest.main()
