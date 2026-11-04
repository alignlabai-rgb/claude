import os
import unittest

from tests.helpers import POSIX, TmpCase, mutated, no_process, sample_bytes, to_bytes, tree_snapshot
from video_core import Limits, prepare
from video_core.errors import UnsafePath, ValidationFailed
from video_core.paths import SOURCE_EXTENSIONS, check_media_for_inspect, check_output_location, resolve_asset


def file_episode(path):
    return to_bytes(mutated(lambda d: d["clips"][0].update(source={"kind": "file", "path": path})))


def rejected_codes(fn):
    with no_process():
        try:
            fn()
        except (UnsafePath, ValidationFailed) as exc:
            return {i.code for i in exc.issues}
    return set()


class AssetResolution(TmpCase):
    def setUp(self):
        super().setUp()
        self.assets = self.mkdir("assets")
        self.write("assets/ok.mp4", b"video")
        self.write("assets/sub/deeper/日本語.png", b"png")
        self.outside = self.write("outside/secret.mp4", b"secret")

    def resolve(self, rel):
        return lambda: resolve_asset(self.assets, rel, ptr="t", allowed_ext=SOURCE_EXTENSIONS)

    def test_existing_regular_files_resolve(self):
        r = resolve_asset(self.assets, "sub/deeper/日本語.png", ptr="t", allowed_ext=SOURCE_EXTENSIONS)
        self.assertEqual(r.size, 3)
        self.assertTrue(r.abspath.startswith(self.assets))

    def test_missing_directory_and_nonfile(self):
        self.assertIn("asset_missing", rejected_codes(self.resolve("nope.mp4")))
        os.mkdir(os.path.join(self.assets, "dir.mp4"))
        self.assertIn("not_regular_file", rejected_codes(self.resolve("dir.mp4")))
        self.assertIn("asset_missing", rejected_codes(self.resolve("ok.mp4/child.mp4")))

    def test_absolute_traversal_protocol_and_option_like_never_touch_the_filesystem(self):
        for rel, code in ((self.outside, "path_absolute"), ("../outside/secret.mp4", "path_traversal"), ("sub/../../outside/secret.mp4", "path_traversal"),
                          ("http://x/a.mp4", "path_protocol"), ("-i", "path_option_like"), ("-ok.mp4", "path_option_like")):
            self.assertIn(code, rejected_codes(self.resolve(rel)), rel)

    @unittest.skipUnless(POSIX, "symlinks")
    def test_symlink_escape_file_and_directory_and_internal_symlink(self):
        os.symlink(self.outside, os.path.join(self.assets, "escape.mp4"))
        os.symlink(os.path.dirname(self.outside), os.path.join(self.assets, "escdir"))
        os.symlink(os.path.join(self.assets, "ok.mp4"), os.path.join(self.assets, "inner.mp4"))
        os.symlink(os.path.join(self.tmp, "does-not-exist"), os.path.join(self.assets, "dangling.mp4"))
        for rel in ("escape.mp4", "escdir/secret.mp4", "inner.mp4", "dangling.mp4"):
            self.assertIn("symlink_rejected", rejected_codes(self.resolve(rel)), rel)

    @unittest.skipUnless(POSIX, "symlinks")
    def test_asset_root_itself_may_be_a_caller_chosen_symlink(self):
        link = os.path.join(self.tmp, "assets-link")
        os.symlink(self.assets, link)
        self.assertEqual(resolve_asset(link, "ok.mp4", ptr="t", allowed_ext=SOURCE_EXTENSIONS).size, 5)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "mkfifo")
    def test_fifo_is_rejected_without_blocking(self):
        os.mkfifo(os.path.join(self.assets, "pipe.mp4"))
        self.assertIn("not_regular_file", rejected_codes(self.resolve("pipe.mp4")))

    def test_size_limit(self):
        self.assertIn("limit_source_bytes", rejected_codes(lambda: resolve_asset(self.assets, "ok.mp4", ptr="t", max_bytes=2)))

    def test_missing_or_bad_root(self):
        self.assertIn("root_missing", rejected_codes(lambda: resolve_asset(os.path.join(self.tmp, "none"), "ok.mp4", ptr="t")))
        self.assertIn("root_not_directory", rejected_codes(lambda: resolve_asset(self.outside, "ok.mp4", ptr="t")))

    def test_plan_requires_an_asset_root_for_file_sources(self):
        self.assertIn("asset_root_required", rejected_codes(lambda: prepare(file_episode("ok.mp4"))))

    def test_plan_with_file_source_does_not_modify_anything(self):
        before = tree_snapshot(self.tmp)
        with no_process():
            p = prepare(file_episode("ok.mp4"), asset_root=self.assets)
        self.assertEqual(tree_snapshot(self.tmp), before)
        argv = p.canonical["ffmpeg"]["argv"]
        i = argv.index({"asset": "ok.mp4"})
        self.assertEqual(argv[i - 5:i], ["-protocol_whitelist", "file", "-f", "mov", "-i"])

    def test_image_input_options_disable_pattern_expansion(self):
        p = prepare(file_episode("sub/deeper/日本語.png"), asset_root=self.assets)
        argv = p.canonical["ffmpeg"]["argv"]
        i = argv.index({"asset": "sub/deeper/日本語.png"})
        self.assertEqual(argv[i - 11:i], ["-protocol_whitelist", "file", "-f", "image2", "-pattern_type", "none", "-loop", "1", "-framerate", "30/1", "-i"])


class OutputLocation(TmpCase):
    def setUp(self):
        super().setUp()
        self.out = self.mkdir("out")
        self.assets = self.mkdir("assets")

    def check(self, name, **kw):
        return lambda: check_output_location(self.out, name, kw.get("assets", self.assets))

    def test_fresh_name_ok_and_nothing_created(self):
        before = tree_snapshot(self.tmp)
        loc = check_output_location(self.out, "run-1", self.assets)
        self.assertEqual(loc.run_dir, os.path.join(self.out, "run-1"))
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_existing_dir_file_and_dangling_symlink_are_refused(self):
        os.mkdir(os.path.join(self.out, "taken"))
        self.write("out/taken-file", b"keep")
        self.assertIn("output_exists", rejected_codes(self.check("taken")))
        self.assertIn("output_exists", rejected_codes(self.check("taken-file")))
        if POSIX:
            os.symlink(os.path.join(self.tmp, "nowhere"), os.path.join(self.out, "dangling"))
            self.assertIn("output_exists", rejected_codes(self.check("dangling")))

    def test_unsafe_names(self):
        for bad in ("", ".", "..", "../x", "a/b", "-rf", ".hidden", "a b", "a\\b", "x" * 65, "CON", "nul.txt", "trailing.", "é", "a:b"):
            self.assertIn("output_name_invalid", rejected_codes(self.check(bad)), repr(bad))

    def test_missing_or_file_root(self):
        self.assertIn("root_missing", rejected_codes(lambda: check_output_location(os.path.join(self.tmp, "none"), "x")))
        self.write("afile", b"x")
        self.assertIn("root_not_directory", rejected_codes(lambda: check_output_location(os.path.join(self.tmp, "afile"), "x")))

    def test_output_may_not_overlap_the_asset_root(self):
        self.assertIn("output_overlaps_assets", rejected_codes(lambda: check_output_location(self.assets, "run", self.assets)))
        nested = self.mkdir("assets", "inner")
        self.assertIn("output_overlaps_assets", rejected_codes(lambda: check_output_location(nested, "run", self.assets)))

    def test_plan_validates_output_without_creating_it(self):
        before = tree_snapshot(self.tmp)
        with no_process():
            p = prepare(sample_bytes(), output_root=self.out, output_name="planned")
        self.assertEqual(tree_snapshot(self.tmp), before)
        self.assertEqual(p.observations["output"]["name"], "planned")
        self.assertIn("output_incomplete", rejected_codes(lambda: prepare(sample_bytes(), output_root=self.out)))


class InspectPath(TmpCase):
    def test_rules(self):
        ok = self.write("m.mp4", b"x")
        self.assertEqual(check_media_for_inspect(ok)[1], "mov")
        self.assertIn("path_protocol", rejected_codes(lambda: check_media_for_inspect("http://x/a.mp4")))
        self.assertIn("path_protocol", rejected_codes(lambda: check_media_for_inspect("concat:a.mp4|b.mp4")))
        self.assertIn("source_extension_unsupported", rejected_codes(lambda: check_media_for_inspect(self.write("p.m3u8", b"x"))))
        self.assertIn("asset_missing", rejected_codes(lambda: check_media_for_inspect(os.path.join(self.tmp, "no.mp4"))))
        if POSIX:
            os.symlink(ok, os.path.join(self.tmp, "l.mp4"))
            self.assertIn("symlink_rejected", rejected_codes(lambda: check_media_for_inspect(os.path.join(self.tmp, "l.mp4"))))
            cwd = os.getcwd()
            os.chdir(self.tmp)
            try:
                self.write("-x.mp4", b"x")
                self.assertIn("path_option_like", rejected_codes(lambda: check_media_for_inspect("-x.mp4")))
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
