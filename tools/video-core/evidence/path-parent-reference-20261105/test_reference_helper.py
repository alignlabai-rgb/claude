"""12 focused cases for evidence/path-parent-reference-20261105/reference_helper.py. Run from tools/video-core:

    python3 evidence/path-parent-reference-20261105/test_reference_helper.py

Everything happens inside one OWNED temporary directory created by the test (root, outside and alias directories all below it); nothing outside it is read, no privilege is requested, no
Windows symlink/junction is created. Real symlinks are created only on POSIX where an ordinary user may; Windows reparse behaviour appears ONLY as STAT-STUB cases (a patched lstat result
carrying FILE_ATTRIBUTE_REPARSE_POINT) and says nothing about real junctions. A bare `ok` does not mean atomic: see test 9.
"""

import json
import os
import stat
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(os.path.dirname(HERE))
for p in (TOOLS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from reference_helper import media_under_trusted_root, resolve_under_trusted_root  # noqa: E402
from tests.helpers import POSIX, TmpCase, no_process, tree_snapshot  # noqa: E402
from video_core import cli as vcli, paths, tools  # noqa: E402
from video_core.avmux import cli as avcli  # noqa: E402
from video_core.avmux.reinspect import reinspect_audio_packets  # noqa: E402
from video_core.errors import UnsafePath, VideoCoreError  # noqa: E402
from video_core.limits import Limits  # noqa: E402

MOV = paths.PROBE_EXTENSIONS


def codes(fn):
    try:
        fn()
    except VideoCoreError as exc:
        return [i.code for i in exc.issues]
    return None


def stat_like(real, *, mode=None, attributes=None):
    kw = {"st_mode": real.st_mode if mode is None else mode, "st_size": real.st_size}
    if attributes is not None:
        kw["st_file_attributes"] = attributes
    return mock.Mock(spec=list(kw), **kw)


class World(TmpCase):
    def setUp(self):
        super().setUp()
        self.root = self.mkdir("world", "root")
        self.outside = self.mkdir("world", "outside")  # inside the owned world, but outside the trusted root
        self.clip = self.write("world/root/real_dir/clip.mp4", b"CLIP")
        self.real_dir = os.path.dirname(self.clip)

    def patched_lstat(self, target, replacement):
        real = os.lstat
        return mock.patch.object(paths.os, "lstat", lambda p, *a, **k: replacement if os.path.abspath(str(p)) == os.path.abspath(target) else real(p, *a, **k))


class ReferenceHelper(World):
    def test_01_japanese_and_spaced_regular_files_resolve_with_their_demuxer(self):
        for rel, demuxer in (("動画 テスト/クリップ 1.mp4", "mov"), ("my videos/a (1).MOV", "mov"), ("日本語.webm", "matroska")):
            with self.subTest(rel):
                p = self.write("world/root/" + rel, b"x" * 7)
                rf = resolve_under_trusted_root(self.root, rel, allowed_ext=MOV, max_bytes=100)
                self.assertEqual((rf.rel, rf.size, os.path.realpath(rf.abspath)), (rel, 7, os.path.realpath(p)))
                self.assertEqual(media_under_trusted_root(self.root, rel)[1], demuxer)

    @unittest.skipUnless(POSIX, "unprivileged symlink creation")
    def test_02_a_root_alias_is_the_callers_choice_but_a_link_below_the_root_is_not(self):
        """Two DIFFERENT policies: real_directory resolves the caller-chosen root (alias accepted, even one pointing elsewhere in the owned world); resolve_asset rejects link-like descendants."""
        alias = os.path.join(self.tmp, "world", "root_alias")
        os.symlink(self.root, alias)
        rf = resolve_under_trusted_root(alias, "real_dir/clip.mp4", allowed_ext=MOV)
        self.assertTrue(rf.abspath.startswith(os.path.realpath(self.root)), "the alias was resolved to the real root; descendants are then lstat-ed")
        other_alias = os.path.join(self.tmp, "world", "outside_alias")
        os.symlink(self.outside, other_alias)  # a trusted root that is an alias of another directory: accepted, because the ROOT choice is the caller's
        self.write("world/outside/o.mp4", b"O")
        self.assertEqual(resolve_under_trusted_root(other_alias, "o.mp4", allowed_ext=MOV).size, 1)
        os.symlink(self.real_dir, os.path.join(self.root, "linkdir"))
        self.assertEqual(codes(lambda: resolve_under_trusted_root(alias, "linkdir/clip.mp4", allowed_ext=MOV)), ["symlink_rejected"])

    @unittest.skipUnless(POSIX, "unprivileged symlink creation")
    def test_03_a_leaf_symlink_is_rejected_even_when_it_points_inside_the_root(self):
        os.symlink(self.clip, os.path.join(self.root, "leaf.mp4"))
        os.symlink(os.path.join(self.outside, "nope.mp4"), os.path.join(self.root, "dangling.mp4"))
        for rel in ("leaf.mp4", "dangling.mp4"):
            with self.subTest(rel), no_process():
                self.assertEqual(codes(lambda: resolve_under_trusted_root(self.root, rel, allowed_ext=MOV)), ["symlink_rejected"])

    @unittest.skipUnless(POSIX, "unprivileged symlink creation")
    def test_04_a_parent_symlink_is_rejected_by_the_helper_but_accepted_by_the_current_inspect_check(self):
        """CURRENT BEHAVIOUR, not a verdict: check_media_for_inspect lstat-s only the leaf, so a path through a symlinked parent passes. Whether that should change is an owner decision."""
        os.symlink(self.real_dir, os.path.join(self.root, "parent_link"))
        self.assertEqual(codes(lambda: resolve_under_trusted_root(self.root, "parent_link/clip.mp4", allowed_ext=MOV)), ["symlink_rejected"])
        through_link = os.path.join(self.root, "parent_link", "clip.mp4")
        self.assertEqual(paths.check_media_for_inspect(through_link), (through_link, "mov"))

    def test_05_windows_reparse_attributes_stat_stub_only_leaf_and_parent_components(self):
        """STAT-STUB EVIDENCE ONLY. A patched lstat result with FILE_ATTRIBUTE_REPARSE_POINT; NO real junction or Windows run. The helper checks every component; inspect only the leaf."""
        sub_dir = os.path.join(self.root, "real_dir")
        parent_stub = stat_like(os.lstat(sub_dir), mode=stat.S_IFDIR | 0o755, attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with self.patched_lstat(sub_dir, parent_stub):
            self.assertEqual(codes(lambda: resolve_under_trusted_root(self.root, "real_dir/clip.mp4", allowed_ext=MOV)), ["symlink_rejected"])
            self.assertEqual(paths.check_media_for_inspect(self.clip)[1], "mov", "current inspect never lstat-s the parent, so a stubbed parent is not seen")
        leaf_stub = stat_like(os.lstat(self.clip), attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        with self.patched_lstat(self.clip, leaf_stub):
            self.assertEqual(codes(lambda: resolve_under_trusted_root(self.root, "real_dir/clip.mp4", allowed_ext=MOV)), ["symlink_rejected"])
            self.assertEqual(codes(lambda: paths.check_media_for_inspect(self.clip)), ["symlink_rejected"])
        plain = stat_like(os.lstat(self.clip), attributes=stat.FILE_ATTRIBUTE_ARCHIVE)
        with self.patched_lstat(self.clip, plain):
            self.assertEqual(resolve_under_trusted_root(self.root, "real_dir/clip.mp4", allowed_ext=MOV).size, 4)

    def test_06_syntax_escape_and_protocol_refusals_need_no_process_and_create_nothing(self):
        table = {"..": "path_traversal", "real_dir/../x.mp4": "path_traversal", "/abs/clip.mp4": "path_absolute", "C:/clip.mp4": "path_absolute", "real_dir\\clip.mp4": "path_backslash",
                 "http://x/a.mp4": "path_protocol", "concat:a.mp4|b.mp4": "path_protocol", "file:clip.mp4": "path_protocol", "-x.mp4": "path_option_like", ".hidden.mp4": "path_hidden",
                 "real_dir//clip.mp4": "path_not_normalized", "./clip.mp4": "path_not_normalized", "a\x00b.mp4": "path_control_char", "a\nb.mp4": "path_control_char",
                 "bad*name.mp4": "path_forbidden_char", "pipe|name.mp4": "path_forbidden_char", "": "path_empty", "x" * 300 + ".mp4": "path_component_too_long"}
        before = tree_snapshot(self.tmp)
        with no_process():
            for rel, code in table.items():
                with self.subTest(repr(rel)[:40]):
                    self.assertIn(code, codes(lambda: resolve_under_trusted_root(self.root, rel, allowed_ext=MOV)) or [])
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_07_filesystem_refusals_extension_missing_directory_oversize_and_windows_unsafe_names(self):
        self.write("world/root/note.txt", b"x")
        self.write("world/root/big.mp4", b"x" * 50)
        os.makedirs(os.path.join(self.root, "isdir.mp4"))
        table = {"note.txt": "source_extension_unsupported", "real_dir/missing.mp4": "asset_missing", "isdir.mp4": "not_regular_file", "big.mp4": "limit_source_bytes",
                 "trailing dot.mp4.": "path_windows_unsafe", "CON.mp4": "path_reserved_name", "lpt1.mov": "path_reserved_name", " lead.mp4": "path_windows_unsafe"}
        before = tree_snapshot(self.tmp)
        with no_process():
            for rel, code in table.items():
                with self.subTest(rel):
                    self.assertIn(code, codes(lambda: resolve_under_trusted_root(self.root, rel, allowed_ext=MOV, max_bytes=10)) or [])
        self.assertEqual(tree_snapshot(self.tmp), before)

    def test_08_the_root_policy_is_unchanged_missing_not_a_directory_nul_and_non_utf8_roots(self):
        f = self.write("world/afile.mp4", b"x")
        for label, root, code in (("missing", os.path.join(self.tmp, "nope"), "root_missing"), ("a file", f, "root_not_directory"), ("NUL", self.root + "\x00", "root_invalid"),
                                  ("lone surrogate", os.path.join(self.root, "caf\udce9"), "root_invalid"), ("empty", "", "root_invalid")):
            with self.subTest(label), no_process():
                self.assertEqual(codes(lambda: resolve_under_trusted_root(root, "real_dir/clip.mp4", allowed_ext=MOV)), [code])

    @unittest.skipUnless(POSIX, "unprivileged symlink creation")
    def test_09_detection_is_not_atomic_a_file_swapped_after_resolution_is_followed_by_open(self):
        """LIMITATION DEMONSTRATED, deterministic and single-threaded: after the helper accepted a regular file, replacing it with a symlink makes a plain open() of the returned path read the
        symlink target (here, a file in the owned `outside` directory). The helper reduces what is accepted; it does not prevent this race. A fix would need open-by-descriptor with O_NOFOLLOW / openat
        or equivalent per-platform handling, an owner decision this reference does not make."""
        secret = self.write("world/outside/secret-looking.mp4", b"OUTSIDE-OWNED-FIXTURE")
        rf = resolve_under_trusted_root(self.root, "real_dir/clip.mp4", allowed_ext=MOV)
        tmp_link = os.path.join(self.root, "real_dir", "swap")
        os.symlink(secret, tmp_link)
        os.replace(tmp_link, rf.abspath)
        with open(rf.abspath, "rb") as fh:
            self.assertEqual(fh.read(), b"OUTSIDE-OWNED-FIXTURE")
        self.assertEqual(codes(lambda: resolve_under_trusted_root(self.root, "real_dir/clip.mp4", allowed_ext=MOV)), ["symlink_rejected"], "a second resolution does see the swap")

    @unittest.skipUnless(POSIX, "unprivileged symlink creation")
    def test_10_what_each_current_read_route_does_with_a_leaf_symlink_and_a_parent_symlink(self):
        """Pins the CURRENT behaviour behind the README table (source commit 019d059). 'accepted' means the route's own checks did not refuse it; it is not a defect claim."""
        link_dir = os.path.join(self.tmp, "linkdir")
        os.symlink(self.real_dir, link_dir)
        data = json.dumps({"x": 1}).encode()
        self.write("world/root/real_dir/doc.json", data)
        os.symlink(os.path.join(self.real_dir, "doc.json"), os.path.join(self.real_dir, "leaf_doc.json"))
        for label, fn in (("video cli _read_episode", lambda p: vcli._read_episode(p, Limits())), ("avmux cli _read (manifest)", lambda p: avcli._read(p, 4096))):
            with self.subTest(label):
                self.assertEqual(fn(os.path.join(self.real_dir, "leaf_doc.json")), data, "a leaf symlink is followed (os.stat + open)")
                self.assertEqual(fn(os.path.join(link_dir, "doc.json")), data, "a parent symlink is followed")
        media = os.path.join(self.root, "m.mp4")
        with open(media, "wb") as fh:
            fh.write(b"MEDIA")
        import hashlib
        sha = hashlib.sha256(b"MEDIA").hexdigest()
        receipt = json.dumps({"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "outputs": [{"role": "media", "sha256": sha}]}).encode()
        self.write("world/root/real_dir/r.json", receipt)
        os.symlink(os.path.join(self.real_dir, "r.json"), os.path.join(self.real_dir, "leaf_r.json"))

        names = iter(("o1", "o2"))

        def reinspect(receipt_path):
            with no_process(), mock.patch.object(tools, "locate", lambda name, explicit=None: (None, "patched: stop after the receipt stage")):
                return reinspect_audio_packets(media, expected_sha256=sha, output_root=self.mkdir("world", "out"), output_name=next(names), original_receipt_path=receipt_path)

        with self.assertRaises(UnsafePath) as cm:
            reinspect(os.path.join(self.real_dir, "leaf_r.json"))
        self.assertEqual([i.code for i in cm.exception.issues], ["original_receipt_unreadable"], "reinspect: a LEAF symlink receipt is refused (os.path.islink)")
        self.assertEqual(reinspect(os.path.join(link_dir, "r.json"))["status"], "SKIPPED_TOOL_UNAVAILABLE", "reinspect: a PARENT symlink receipt passes the receipt stage (tool lookup patched; no coverage claim)")
        self.assertEqual(codes(lambda: paths.check_media_for_inspect(os.path.join(self.real_dir, "leaf_m.mp4"))), ["asset_missing"])
        os.symlink(media, os.path.join(self.real_dir, "leaf_m.mp4"))
        self.assertEqual(codes(lambda: paths.check_media_for_inspect(os.path.join(self.real_dir, "leaf_m.mp4"))), ["symlink_rejected"], "inspect: a LEAF symlink media is refused")
        self.assertEqual(paths.check_media_for_inspect(os.path.join(link_dir, "clip.mp4"))[1], "mov", "inspect: a PARENT symlink media passes")

    def test_11_the_helper_is_a_pure_delegation_identical_results_and_rejections_to_resolve_asset(self):
        corpus = ["real_dir/clip.mp4", "real_dir/missing.mp4", "../x.mp4", "-x.mp4", "note.txt", "real_dir", "http://x/a.mp4", "real_dir/clip.MP4"]
        for rel in corpus:
            with self.subTest(rel):
                a = b = None
                try:
                    a = paths.resolve_asset(self.root, rel, ptr="/path", allowed_ext=MOV, max_bytes=100)
                except VideoCoreError as exc:
                    a = [(i.code, i.path) for i in exc.issues]
                try:
                    b = resolve_under_trusted_root(self.root, rel, allowed_ext=MOV, max_bytes=100)
                except VideoCoreError as exc:
                    b = [(i.code, i.path) for i in exc.issues]
                self.assertEqual(a, b)

    def test_12_this_reference_adds_no_policy_to_the_package_and_its_decisions_stay_open(self):
        import video_core
        pkg = os.path.dirname(video_core.__file__)
        for dirpath, _dirs, files in os.walk(pkg):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                        self.assertNotIn("reference_helper", fh.read(), f"{name} must not import the reference helper")
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as fh:
            text = fh.read()
        for marker in ("hold", "owner decision", "not atomic"):  # case-insensitive: the README writes "Not atomic" and "HOLD"
            self.assertIn(marker, text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
