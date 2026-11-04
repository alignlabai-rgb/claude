"""Lane 2 - local path / source integrity / output protection (owned temporary paths only, no existing user file is touched).

Known, documented TOCTOU residual risk (LIMITATIONS.md, AUDIO_SPEC.md 'Race windows') is tested as a *characterisation* and labelled
as such; it is never reported as a new defect. Anything beyond the documented claim is reported separately.
"""

from __future__ import annotations

import json
import os
import stat
import struct
import sys
import unittest
from unittest import mock

import adv_oracle as O
from adv_support import (AuditCase, POSIX, TOOLS, audited, avmux_manifest, chunk, fmt_chunk, jb, known_defect, manifest, pack_pcm, pcm_tone, record, riff,
                         sha256, sha256_path, tree_listing, wav_from_frames)

from video_core import errors, paths  # noqa: E402
from video_core.audiomix import mix as amix, plan as aplan  # noqa: E402
from video_core.audiomix.limits import AudioLimits  # noqa: E402
from video_core.avmux import plan as vplan  # noqa: E402


def example_episode() -> dict:
    with open(os.path.join(TOOLS, "examples", "episode.synthetic.json"), encoding="utf-8") as fh:
        return json.load(fh)


def codes(exc):
    return [i.code for i in exc.issues]


# --------------------------------------------------------------------------------------------------------------------------- syntax
class PathSyntax(AuditCase):
    MUST_REJECT = [
        "../a.wav", "a/../b.wav", "a/..", "..", "/abs.wav", "\\\\srv\\share\\a.wav", "C:/x.wav", "c:x.wav", "a\\b.wav", "http://h/a.wav", "file:a.wav", "concat:a|b.wav",
        "pipe:0", "subfile,,start,0,end,1,,:a.wav", "-a.wav", "a/-b.wav", ".hidden.wav", "a/.b/c.wav", "a//b.wav", "a/./b.wav", "./a.wav", "a/", "", "a.wav ", " a.wav",
        "a.wav.", "CON.wav", "nul.wav", "Com1.wav", "LPT9.wav", "dir/aux.txt.wav", "a\x00.wav", "a\n.wav", "a\t.wav", "a\u202e.wav", "a\u200b.wav", "a\ufeff.wav", "a%20.wav",
        "a%d.wav", "a*.wav", "a?.wav", "a[1].wav", "a{b}.wav", "a,b.wav", "a|b.wav", "a;b.wav", "a'b.wav", 'a"b.wav', "a$b.wav", "a`b.wav", "a=b.wav", "a:b.wav", "a<b.wav",
        "a>b.wav", "a&b.wav", "a#b.wav", "a!b.wav", "a^b.wav", "a" * 513 + ".wav", "x" * 256 + ".wav", "\ud800.wav", "a\U000e0001.wav", "a/\u2028/b.wav",
    ]
    MUST_ACCEPT = ["a.wav", "dir/a b.wav", "日本語/テスト.wav", "a+b(1)@x~y.wav", "A.WAV", "d1/d2/d3/x_y-z.wav", "e\u0301.wav", "x" * 251 + ".wav"]
    OBSERVED_RISKY = ["COM\u00b9.wav", "a\uff0fb.wav", "a\u2024b.wav", "\uff2e\uff35\uff2c.wav", "a\u00a0b.wav", "a\u3000b.wav", "e\u0301.wav", "\u00e9.wav"]

    def test_hostile_names_are_rejected_and_ordinary_names_accepted(self):
        wrong = []
        for p in self.MUST_REJECT:
            if not paths.check_relative_path(p, allowed_ext=(".wav",)):
                wrong.append(("accepted", p))
        for p in self.MUST_ACCEPT:
            if paths.check_relative_path(p, allowed_ext=(".wav",)):
                wrong.append(("rejected", p))
        record("path_syntax", must_reject=len(self.MUST_REJECT), must_accept=len(self.MUST_ACCEPT), wrong=wrong)
        self.assertEqual(wrong, [])

    def test_non_string_paths_are_not_crashes(self):
        for bad in (None, 5, b"a.wav", ["a.wav"], {"a": 1}, 1.5, True):
            probs = paths.check_relative_path(bad, allowed_ext=(".wav",))
            self.assertTrue(probs, repr(bad))

    def test_observed_accepted_names_that_deserve_platform_review(self):
        """Characterisation only (no pass/fail claim about Windows/macOS, which were not run): these names are accepted today."""
        seen = {p: not paths.check_relative_path(p, allowed_ext=(".wav",)) for p in self.OBSERVED_RISKY}
        record("path_syntax_accepted_but_platform_sensitive", accepted=seen,
               note="COM\u00b9 is a reserved device name on some Windows builds; fullwidth/NFD names may alias on macOS/NTFS normalisation; untested here")
        self.assertIsInstance(seen, dict)

    def test_unicode_normalisation_aliases_are_two_distinct_names_here(self):
        """NFC vs NFD spelling of one visible name are both accepted and distinct on Linux. If a caller binds both as separate assets on a
        normalising filesystem (macOS) they collide. Declared platform-specific, untested."""
        a, b = "\u00e9.wav", "e\u0301.wav"
        self.assertNotEqual(a, b)
        self.assertFalse(paths.check_relative_path(a, allowed_ext=(".wav",)))
        self.assertFalse(paths.check_relative_path(b, allowed_ext=(".wav",)))


# --------------------------------------------------------------------------------------------------------------------------- filesystem resolution
@unittest.skipUnless(POSIX, "symlink/FIFO fixtures are POSIX-only")
class AssetResolution(AuditCase):
    def setUp(self):
        super().setUp()
        sb = self.sb
        self.root = sb.mkdir("assets")
        self.outside = sb.mkdir("outside")
        self.good = wav_from_frames(pcm_tone(16, 1, 1))
        sb.write("assets/good.wav", self.good)
        sb.write("outside/secret.wav", self.good)
        os.symlink(sb.p("outside", "secret.wav"), sb.p("assets", "link_out.wav"))
        os.symlink(sb.p("assets", "good.wav"), sb.p("assets", "link_in.wav"))
        os.symlink(sb.p("assets", "nope.wav"), sb.p("assets", "dangling.wav"))
        os.symlink(sb.p("outside"), sb.p("assets", "dirlink"))
        os.mkdir(sb.p("assets", "sub"))
        os.symlink(sb.p("assets", "good.wav"), sb.p("assets", "sub", "deep_link.wav"))
        os.mkfifo(sb.p("assets", "fifo.wav"))
        os.mkdir(sb.p("assets", "dir.wav"))

    def _resolve(self, rel):
        return paths.resolve_asset(self.root, rel, ptr="/t", allowed_ext=(".wav",))

    def test_links_fifos_and_directories_are_refused(self):
        expected = {"link_out.wav": "symlink_rejected", "link_in.wav": "symlink_rejected", "dangling.wav": "symlink_rejected", "dirlink/secret.wav": "symlink_rejected",
                    "sub/deep_link.wav": "symlink_rejected", "fifo.wav": "not_regular_file", "dir.wav": "not_regular_file", "missing.wav": "asset_missing", "sub": None}
        got = {}
        for rel, want in expected.items():
            if want is None:
                continue
            with self.assertRaises(errors.UnsafePath, msg=rel) as cm:
                self._resolve(rel)
            got[rel] = codes(cm.exception)[0]
        record("asset_resolution", got=got)
        self.assertEqual(got, {k: v for k, v in expected.items() if v})
        self.assertEqual(self._resolve("good.wav").size, len(self.good))

    def test_symlinked_asset_root_is_accepted_by_design_but_links_below_it_are_not(self):
        os.symlink(self.root, self.sb.p("rootlink"))
        self.assertEqual(paths.resolve_asset(self.sb.p("rootlink"), "good.wav", ptr="/t", allowed_ext=(".wav",)).rel, "good.wav")
        with self.assertRaises(errors.UnsafePath):
            paths.resolve_asset(self.sb.p("rootlink"), "link_in.wav", ptr="/t", allowed_ext=(".wav",))

    def test_hard_link_to_outside_file_is_accepted_documented_limitation(self):
        """LIMITATIONS.md: 'Hard links to outside files cannot be detected.' Confirmed (characterisation, not a new defect)."""
        os.link(self.sb.p("outside", "secret.wav"), self.sb.p("assets", "hard.wav"))
        rf = self._resolve("hard.wav")
        self.assertEqual(os.stat(rf.abspath).st_nlink, 2)
        record("hardlink_accepted_documented", nlink=2)

    def test_size_limit_is_enforced_before_reading(self):
        with self.assertRaises(errors.UnsafePath) as cm:
            paths.resolve_asset(self.root, "good.wav", ptr="/t", allowed_ext=(".wav",), max_bytes=len(self.good) - 1)
        self.assertEqual(codes(cm.exception), ["limit_source_bytes"])

    def test_audio_plan_never_opens_unselected_or_unbound_assets(self):
        """Only assets bound by a SELECTED USE event are touched: a hostile unselected binding (symlink, FIFO) must not be opened."""
        sb = self.sb
        n = 4800
        w = wav_from_frames(pcm_tone(n, 1, 9))
        sb.write("assets/real.wav", w)
        doc = manifest(total=3, assets=[{"id": "real", "path": "real.wav", "sha256": sha256(w)}, {"id": "evil", "path": "fifo.wav", "sha256": "0" * 64},
                                        {"id": "evil2", "path": "dirlink/secret.wav", "sha256": "0" * 64}],
                       events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "real", "source_in_sample": 0, "source_out_sample": n, "start_frame": 0, "end_frame": 3},
                               {"id": "x", "source_id": "s", "state": "USE", "asset": "evil", "source_in_sample": 0, "source_out_sample": n, "start_frame": 0, "end_frame": 3}],
                       scope=["u"])
        mpath = sb.write("m.json", jb(doc))
        body = f'''
        import json
        from video_core.audiomix.plan import prepare_plan
        p = prepare_plan(open({mpath!r}, "rb").read(), asset_root={self.root!r})
        print(json.dumps({{"status": p.status, "verified": [a["id"] for a in p.canonical["assets"] if a["verified"]]}}))
        '''
        # the unselected FIFO event overlaps the selected one in time; overlap rejection only concerns selected events, so this is READY
        rc, out, err, events, to = audited(body)
        self.assertEqual(rc, 0, err[-300:])
        self.assertEqual(json.loads(out), {"status": "READY", "verified": ["real"]})
        self.assertEqual(events, [])


# --------------------------------------------------------------------------------------------------------------------------- output protection
class OutputProtection(AuditCase):
    def setUp(self):
        super().setUp()
        self.assets = self.sb.mkdir("assets")
        self.out = self.sb.mkdir("out")

    def test_bad_output_names_are_refused_and_nothing_is_created(self):
        before = tree_listing(self.out)
        bad = ["", ".", "..", "../x", "a/b", "a\\b", ".hid", "-x", "a" * 65, "con", "NUL.txt", "com1.x", "name.", "na me", "na\x00me", "naïve", " x", "x\n", None, 5]
        for name in bad:
            with self.assertRaises(errors.UnsafePath, msg=repr(name)) as cm:
                paths.check_output_location(self.out, name, self.assets)
            self.assertEqual(codes(cm.exception), ["output_name_invalid"], repr(name))
        self.assertEqual(tree_listing(self.out), before)
        for ok in ("a", "A0._-", "x" * 64, "stem-0123456789ab"):
            self.assertEqual(paths.check_output_location(self.out, ok, self.assets).name, ok)
        self.assertEqual(tree_listing(self.out), before, "validation alone must create nothing")

    @unittest.skipUnless(POSIX, "symlinks")
    def test_existing_locations_are_never_overwritten(self):
        sb = self.sb
        sb.write("out/file_in_the_way", b"keep me")
        os.mkdir(sb.p("out", "dir_in_the_way"))
        sb.write("out/dir_in_the_way/sentinel.txt", b"sentinel")
        os.symlink(sb.p("nowhere"), sb.p("out", "dangling"))
        os.symlink(sb.p("assets"), sb.p("out", "to_assets"))
        h0 = {p: sha256_path(sb.p("out", p)) for p in ("file_in_the_way", "dir_in_the_way/sentinel.txt")}
        for name in ("file_in_the_way", "dir_in_the_way", "dangling", "to_assets"):
            with self.assertRaises(errors.UnsafePath, msg=name) as cm:
                paths.check_output_location(self.out, name, self.assets)
            self.assertEqual(codes(cm.exception), ["output_exists"], name)
        w = wav_from_frames(pcm_tone(4800, 1, 2))
        sb.write("assets/a.wav", w)
        doc = manifest(total=3, assets=[{"id": "a", "path": "a.wav", "sha256": sha256(w)}],
                       events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])
        for name in ("file_in_the_way", "dir_in_the_way", "dangling", "to_assets"):
            with self.assertRaises(errors.UnsafePath):
                amix.mix(jb(doc), asset_root=self.assets, output_root=self.out, output_name=name)
        self.assertEqual({p: sha256_path(sb.p("out", p)) for p in h0}, h0)
        self.assertEqual(sorted(os.listdir(sb.p("out", "dir_in_the_way"))), ["sentinel.txt"])
        self.assertEqual(sorted(os.listdir(self.assets)), ["a.wav"])

    @unittest.skipUnless(POSIX, "symlinks")
    def test_output_may_not_alias_or_contain_the_asset_root(self):
        sb = self.sb
        sb.mkdir("assets", "nested")
        os.symlink(self.assets, sb.p("out", "via_link"))
        cases = [("inside assets", self.assets, "x", self.assets), ("root == assets", self.assets, "y", self.assets), ("subdir of assets", sb.p("assets", "nested"), "z", self.assets),
                 ("symlinked root into assets", sb.p("out", "via_link"), "q", self.assets)]
        got = {}
        for label, root, name, assets in cases:
            with self.assertRaises(errors.UnsafePath, msg=label) as cm:
                paths.check_output_location(root, name, assets)
            got[label] = codes(cm.exception)[0]
        record("output_alias", got=got)
        self.assertTrue(all(c == "output_overlaps_assets" for c in got.values()), got)
        # an asset root that CONTAINS the output root is also refused (run_dir inside assets)
        with self.assertRaises(errors.UnsafePath):
            paths.check_output_location(self.out, "n", self.sb.root)
        self.assertEqual(os.listdir(sb.p("assets", "nested")), [])

    def test_unusable_roots(self):
        for bad in ("", "\x00", self.sb.p("does-not-exist"), self.sb.write("plain_file", b"x")):
            with self.assertRaises(errors.UnsafePath, msg=repr(bad)):
                paths.check_output_location(bad, "ok", self.assets)


# --------------------------------------------------------------------------------------------------------------------------- rejected inputs are inert
class RejectedInputsAreInert(AuditCase):
    """Under an audit hook (not mock.patch): every rejected request must launch no process, execute nothing, write/rename/create nothing."""

    def test_rejected_requests_launch_nothing_and_create_nothing(self):
        sb = self.sb
        sb.mkdir("assets")
        sb.mkdir("out")
        sb.mkdir("out", "taken")
        mono = wav_from_frames(pcm_tone(4800, 1, 3))
        stereo = wav_from_frames(pcm_tone(4800, 2, 4), 2)
        trunc = mono[:-10]
        lie = riff([fmt_chunk(), chunk(b"data", pack_pcm(pcm_tone(4800, 1, 3)))], riff_size=10)
        sb.write("assets/mono.wav", mono)
        sb.write("assets/stereo.wav", stereo)
        sb.write("assets/trunc.wav", trunc)
        sb.write("assets/lie.wav", lie)
        sb.write("assets/v.mp4", b"synthetic-not-a-video")
        if POSIX:
            os.symlink(sb.p("assets", "mono.wav"), sb.p("assets", "link.wav"))
        a = lambda i, p, d: {"id": i, "path": p, "sha256": sha256(d)}

        def use(asset, s_out=4800, s_in=0, f0=0, f1=3, eid="u", **kw):
            e = {"id": eid, "source_id": "s", "state": "USE", "asset": asset, "source_in_sample": s_in, "source_out_sample": s_out, "start_frame": f0, "end_frame": f1}
            e.update(kw)
            return e
        audio_cases = {
            "sha_wrong": (manifest(total=3, assets=[{"id": "m", "path": "mono.wav", "sha256": "0" * 64}], events=[use("m")]), "asset_sha256_mismatch", {}),
            "wav_truncated": (manifest(total=3, assets=[a("m", "trunc.wav", trunc)], events=[use("m")]), "wav_truncated", {}),
            "wav_riff_lie": (manifest(total=3, assets=[a("m", "lie.wav", lie)], events=[use("m")]), "wav_riff_size_mismatch", {}),
            "path_traversal": (manifest(total=3, assets=[a("m", "../mono.wav", mono)], events=[use("m")]), "path_traversal", {}),
            "length_mismatch": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m", s_out=4799)]), "range_length_mismatch", {}),
            "source_oob": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m", s_in=1, s_out=4801)]), "source_range_out_of_bounds", {}),
            "channel_rule_required": (manifest(total=3, assets=[a("s", "stereo.wav", stereo)], events=[use("s")]), "channel_rule_required", {}),
            "channel_rule_incompatible": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m", channel_rule="STEREO_TO_MONO_LEFT")]), "channel_rule_incompatible", {}),
            "overlap": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m"), use("m", eid="v")]), "event_overlap", {}),
            "output_exists": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m")]), "output_exists", {"name": "taken"}),
            "bad_output_name": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m")]), "output_name_invalid", {"name": "../escape"}),
            "asset_root_required": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m")]), "asset_root_required", {"no_assets": True}),
            "asset_inside_output": (manifest(total=3, assets=[a("m", "mono.wav", mono)], events=[use("m")]), "output_overlaps_assets", {"out_is_assets": True}),
        }
        if POSIX:
            audio_cases["symlink_asset"] = (manifest(total=3, assets=[a("m", "link.wav", mono)], events=[use("m")]), "symlink_rejected", {})
        cases = {f"audio:{k}": {"kind": "audio", "manifest": v[0], "expect": v[1], **v[2]} for k, v in audio_cases.items()}
        cases["audio:invalid_json"] = {"kind": "audio", "raw": "{not json", "expect": "json_invalid"}
        cases["audio:lone_surrogate_key"] = {"kind": "audio", "raw": json.dumps(manifest(total=3, events=[{"id": "e", "source_id": "s", "state": "UNKNOWN"}]))[:-1] + ', "\\ud800": 1}', "expect": "unknown_field"}
        vm = lambda **kw: avmux_manifest(video_bytes=b"synthetic-not-a-video", wav_bytes_=mono, wav_path="mono.wav", samples=4800, **kw)
        bad_sha = vm(); bad_sha["video"]["sha256"] = "1" * 64
        bad_asha = vm(); bad_asha["audio"]["sha256"] = "2" * 64
        bad_rec = vm(receipt={"path": "r.json", "sha256": "3" * 64})
        sb.write("assets/r.json", b'{"status":"HOLD"}')
        not_verified = vm(receipt={"path": "r.json", "sha256": sha256(b'{"status":"HOLD"}')})
        cases.update({
            "avmux:video_sha": {"kind": "avmux", "manifest": bad_sha, "expect": "video_sha256_mismatch"},
            "avmux:audio_sha": {"kind": "avmux", "manifest": bad_asha, "expect": "audio_sha256_mismatch"},
            "avmux:samples": {"kind": "avmux", "manifest": avmux_manifest(video_bytes=b"synthetic-not-a-video", wav_bytes_=mono, wav_path="mono.wav", samples=4801), "expect": "audio_sample_count_mismatch"},
            "avmux:channels": {"kind": "avmux", "manifest": avmux_manifest(video_bytes=b"synthetic-not-a-video", wav_bytes_=mono, wav_path="mono.wav", samples=4800, channels=2), "expect": "audio_channels_mismatch"},
            "avmux:missing_video": {"kind": "avmux", "manifest": vm(video_path="gone.mp4"), "expect": "asset_missing"},
            "avmux:receipt_hash": {"kind": "avmux", "manifest": bad_rec, "expect": "audio_receipt_sha256_mismatch"},
            "avmux:receipt_not_verified": {"kind": "avmux", "manifest": not_verified, "expect": "audio_receipt_not_verified"},
            "avmux:output_exists": {"kind": "avmux", "manifest": vm(), "expect": "output_exists", "name": "taken"},
        })
        ep_ok = example_episode()
        ep_file = json.loads(json.dumps(ep_ok))
        ep_file["clips"][0]["source"] = {"kind": "file", "path": "absent.mp4"}
        cases.update({
            "video:missing_asset": {"kind": "video", "manifest": ep_file, "expect": "asset_missing"},
            "video:output_exists": {"kind": "video", "manifest": ep_ok, "expect": "output_exists", "name": "taken"},
            "video:bad_json": {"kind": "video", "raw": "[1,2", "expect": "json_invalid"},
        })
        for k, c in cases.items():
            raw = c.get("raw")
            data = raw.encode("utf-8") if raw is not None else jb(c["manifest"])
            c["file"] = sb.write("cases/" + k.replace(":", "__") + ".json", data)
        sb.write("cases/index.json", json.dumps(cases).encode())
        h_assets = {p["path"]: p["sha256"] for p in tree_listing(sb.p("assets"))}
        h_out_before = tree_listing(sb.p("out"))
        body = f'''
        import json, os
        from video_core import errors
        from video_core.audiomix import mix as amix
        from video_core.avmux import mux as vmux
        from video_core.render import render
        cases = json.load(open({sb.p("cases", "index.json")!r}))
        assets, out = {sb.p("assets")!r}, {sb.p("out")!r}
        res = {{}}
        for name, c in cases.items():
            data = open(c["file"], "rb").read()
            oname = c.get("name", "fresh-" + name.replace(":", "-"))
            aroot = None if c.get("no_assets") else assets
            oroot = assets if c.get("out_is_assets") else out
            try:
                if c["kind"] == "audio":
                    r = amix.mix(data, asset_root=aroot, output_root=oroot, output_name=oname)
                elif c["kind"] == "avmux":
                    r = vmux.mux(data, asset_root=aroot, output_root=oroot, output_name=oname)
                else:
                    r = render(data, asset_root=aroot, output_root=oroot, output_name=oname)
                res[name] = "RETURNED:" + str(r.get("status"))
            except errors.VideoCoreError as exc:
                res[name] = "|".join(i.code for i in exc.issues)
            except BaseException as exc:
                res[name] = "LEAK:" + type(exc).__name__ + ":" + str(exc)[:80]
        print(json.dumps(res))
        '''
        rc, out_s, err, events, to = audited(body, timeout=9)
        self.assertEqual((rc, to), (0, False), err[-500:])
        res = json.loads(out_s)
        record("rejected_inert", cases=len(cases), results=res, events=events)
        mismatches = {k: (res.get(k), c["expect"]) for k, c in cases.items() if c["expect"] not in (res.get(k) or "").split("|")}
        self.assertEqual(mismatches, {})
        self.assertEqual(events, [], "a rejected request launched a process or touched the filesystem")
        self.assertEqual(tree_listing(sb.p("out")), h_out_before)
        self.assertEqual({p["path"]: p["sha256"] for p in tree_listing(sb.p("assets"))}, h_assets, "inputs unchanged")


# --------------------------------------------------------------------------------------------------------------------------- stage hash changes
class HashAcrossStages(AuditCase):
    """Documented residual: inputs are hashed at plan time and again after the run. These tests characterise exactly what that does and
    does not catch for the audio mixer, and say how wide the window is. They are not reported as new defects."""

    def _setup(self, n_frames=3, chunk=1600):
        sb = self.sb
        samples = n_frames * 1600
        self.orig_frames = pcm_tone(samples, 1, 11)
        self.alt_frames = pcm_tone(samples, 1, 12)
        self.orig = wav_from_frames(self.orig_frames)
        self.alt = wav_from_frames(self.alt_frames)
        self.assert_same_size = len(self.orig) == len(self.alt)
        sb.write("assets/a.wav", self.orig)
        self.doc = manifest(total=n_frames, assets=[{"id": "a", "path": "a.wav", "sha256": sha256(self.orig)}],
                            events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": samples, "start_frame": 0, "end_frame": n_frames}])
        self.limits = AudioLimits(chunk_samples=chunk)
        self.apath = sb.p("assets", "a.wav")
        self.out = sb.mkdir("out")

    def _stem_pcm(self, receipt, name):
        p = self.sb.p("out", name, "stem.wav")
        with open(p, "rb") as fh:
            return fh.read()[44:]

    def test_persistent_swap_is_reported_unknown(self):
        self._setup()
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            if calls["n"] == 2:  # before the second chunk: replace the file with different, equally valid content (same size)
                with open(self.apath, "wb") as fh:
                    fh.write(self.alt)
            return False
        r = amix.mix(jb(self.doc), limits=self.limits, asset_root=self.sb.p("assets"), output_root=self.out, output_name="r1", cancel=cancel)
        record("swap_persistent", status=r["status"], inputs_unchanged=r["input"]["inputs_unchanged"], reason=r.get("unknown_reason"))
        self.assertEqual(r["status"], "UNKNOWN")
        self.assertFalse(r["input"]["inputs_unchanged"])
        self.assertIn("changed", r["unknown_reason"])
        with open(self.sb.p("out", "r1", "receipt.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "UNKNOWN", "on-disk receipt agrees with the returned one")

    def test_transient_swap_and_restore_is_not_detected_documented_residual(self):
        """The mixer re-opens the asset for every chunk, so the unprotected window is the WHOLE mix, not the 'tiny gap' LIMITATIONS.md
        describes for FFmpeg's open(). A swap-and-restore yields MIXED_VERIFIED with inputs_unchanged=True although the stem contains
        bytes that never matched the pinned SHA-256."""
        self._setup()
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            if calls["n"] == 2:
                with open(self.apath, "wb") as fh:
                    fh.write(self.alt)
            elif calls["n"] == 3:
                with open(self.apath, "wb") as fh:
                    fh.write(self.orig)
            return False
        r = amix.mix(jb(self.doc), limits=self.limits, asset_root=self.sb.p("assets"), output_root=self.out, output_name="r2", cancel=cancel)
        pcm = self._stem_pcm(r, "r2")
        want = b"".join(struct.pack("<h", fr[0]) for fr in self.orig_frames)
        record("swap_transient", status=r["status"], inputs_unchanged=r["input"]["inputs_unchanged"], stem_equals_pinned_input=(pcm == want),
               chunks_differing=[i for i in range(3) if pcm[i * 3200:(i + 1) * 3200] != want[i * 3200:(i + 1) * 3200]])
        self.assertEqual(r["status"], "MIXED_VERIFIED")
        self.assertTrue(r["input"]["inputs_unchanged"])
        self.assertNotEqual(pcm, want, "characterisation: the stem silently contains the transient bytes")
        self.assertEqual(pcm[:3200], want[:3200])
        self.assertEqual(pcm[3200:6400], b"".join(struct.pack("<h", fr[0]) for fr in self.alt_frames)[3200:6400])

    def _delete_mid_mix(self, name):
        self._setup()
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            if calls["n"] == 2:
                os.remove(self.apath)
            return False
        return amix.mix(jb(self.doc), limits=self.limits, asset_root=self.sb.p("assets"), output_root=self.out, output_name=name, cancel=cancel)

    def test_deleted_input_during_mix_is_reported_unknown_not_a_crash(self):
        r = self._delete_mid_mix("r3")
        listing = sorted(os.listdir(self.sb.p("out", "r3")))
        record("input_deleted_mid_mix", status=r["status"], listing=listing, internal_error=r.get("internal_error"), outputs=[o["name"] for o in r["outputs"]])
        self.assertEqual(r["status"], "UNKNOWN")
        self.assertIn("stem.wav.partial", listing)
        with open(self.sb.p("out", "r3", "receipt.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "UNKNOWN")

    @known_defect("F-RCPT-1")
    def test_receipt_inventory_after_an_internal_error_lists_what_is_on_disk(self):
        """After an unexpected exception the run keeps its partial files (documented) but the receipt says outputs=[] and has no
        sha256_after for the inputs, although stem.wav.partial and plan.canonical.json exist."""
        r = self._delete_mid_mix("r4")
        on_disk = sorted(n for n in os.listdir(self.sb.p("out", "r4")) if n != "receipt.json")
        listed = sorted(o["name"] for o in r["outputs"])
        record("F-RCPT-1/audiomix", on_disk=on_disk, listed=listed, input_keys=sorted(r["input"]["assets"][0].keys()))
        self.assertEqual(listed, on_disk)
        self.assertIn("sha256_after", r["input"]["assets"][0])

    def test_decoy_with_matching_extension_but_other_content_is_rejected(self):
        sb = self.sb
        for name, data in (("fake.wav", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64), ("empty.wav", b""), ("riffonly.wav", b"RIFF\x04\x00\x00\x00WAVE")):
            sb.write("assets/" + name, data)
            doc = manifest(total=3, assets=[{"id": "a", "path": name, "sha256": sha256(data)}],
                           events=[{"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": 4800, "start_frame": 0, "end_frame": 3}])
            with self.assertRaises(errors.ValidationFailed, msg=name):
                aplan.prepare_plan(jb(doc), asset_root=sb.p("assets"))

    def test_avmux_plan_labels_caller_stated_video_properties_as_unverified(self):
        sb = self.sb
        mono = wav_from_frames(pcm_tone(4800, 1, 5))
        vbytes = b"this is not an mp4 at all"
        sb.write("assets/a.wav", mono)
        sb.write("assets/v.mp4", vbytes)
        doc = avmux_manifest(video_bytes=vbytes, wav_bytes_=mono, samples=4800)
        plan = vplan.prepare_plan(jb(doc), asset_root=sb.p("assets"))
        self.assertTrue(plan.canonical["readiness"].startswith("UNVERIFIED"))
        self.assertEqual(plan.canonical["video"]["properties_basis"], "CALLER_STATED_UNVERIFIED")
        argv = plan.canonical["ffmpeg"]["argv"]
        for i, el in enumerate(argv):  # every input is preceded by a forced demuxer and the file-only protocol whitelist
            if el == "-i":
                self.assertEqual(argv[i - 2], "-f")
                self.assertEqual(argv[i - 4:i - 2], ["-protocol_whitelist", "file"])
        for bad in ("-shortest", "-t", "-ss", "-r", "-vsync", "-fps_mode", "-af", "-ar"):
            self.assertNotIn(bad, argv)


# --------------------------------------------------------------------------------------------------------------------------- static / stub
class WindowsReparseStub(AuditCase):
    def test_inspect_path_check_ignores_reparse_points_unlike_resolve_asset(self):
        """STUB ONLY (no Windows run, no real junction): resolve_asset() uses is_link_like() (symlink OR FILE_ATTRIBUTE_REPARSE_POINT) but
        check_media_for_inspect() tests only S_ISLNK. A reparse-point *file* given to `inspect` would pass that check. Platform-specific,
        unverified on a real Windows junction; reported as a static consistency observation (F-PATH-1)."""
        fake = type("St", (), {"st_mode": stat.S_IFREG | 0o644, "st_file_attributes": 0x400, "st_size": 1})()
        self.assertTrue(paths.is_link_like(fake))
        real = self.sb.write("m.mp4", b"x")
        with mock.patch.object(paths.os, "lstat", return_value=fake):
            got = paths.check_media_for_inspect(real)
        record("F-PATH-1", accepted_reparse_stub=bool(got))
        self.assertEqual(os.path.basename(got[0]), "m.mp4")


if __name__ == "__main__":
    unittest.main()
