"""Lane 3 - timeline and sample/frame invariants across modules, checked against an arithmetic oracle (adv_oracle.py) that imports
nothing from video_core. Includes a seeded property test of the audio planner/mixer and the video-only / canonical-hash invariants."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import textwrap
import unittest
from fractions import Fraction

import adv_oracle as O
from adv_support import (AuditCase, PY, TOOLS, audited, avmux_manifest, cli, child, jb, known_defect, manifest, pack_pcm, pcm_tone, record, sfnt_font, sha256,
                         wav_from_frames)

from video_core import errors  # noqa: E402
from video_core.audiomix import mix as amix, plan as aplan  # noqa: E402
from video_core.audiomix.limits import AudioLimits  # noqa: E402
from video_core.audiomix.manifest import load_manifest  # noqa: E402
from video_core.avmux import plan as vplan  # noqa: E402
from video_core.limits import Limits  # noqa: E402

SEED_PLAN, SEED_SPAN = 20261110, 20261111
FPS_CATALOG = [(24, 1), (25, 1), (30, 1), (50, 1), (60, 1), (24000, 1001), (30000, 1001), (60000, 1001), (48000, 1001), (7, 3), (12345, 1001), (16, 1), (256, 1)]


def example_episode_bytes() -> bytes:
    with open(os.path.join(TOOLS, "examples", "episode.synthetic.json"), "rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------------------------------------------------------- spans
class SampleSpans(AuditCase):
    def test_planner_event_spans_and_totals_match_oracle_for_odd_frame_rates(self):
        r = O.Rng(SEED_SPAN)
        cases = 0
        for n, d in FPS_CATALOG + [(60000, 1), (48001, 1), (59999, 1)]:
            for _ in range(40):
                total = r.between(2, 600)
                s = r.below(total)
                e = r.between(s + 1, total)
                ev = {"id": "e", "source_id": "s", "state": "UNKNOWN", "start_frame": s, "end_frame": e}
                doc = manifest(fps=(n, d), total=total, events=[ev])
                s0, s1 = O.sample_at(s, n, d), O.sample_at(e, n, d)
                try:
                    plan = aplan.prepare_plan(jb(doc))
                except errors.ValidationFailed as exc:
                    self.assertEqual([i.code for i in exc.issues], ["event_zero_samples"])
                    self.assertLessEqual(s1, s0, (n, d, s, e))
                    cases += 1
                    continue
                ce = plan.canonical["events"][0]
                self.assertEqual((ce["sample_start"], ce["sample_end"], ce["samples"]), (s0, s1, s1 - s0), (n, d, s, e))
                self.assertEqual(plan.canonical["timeline"]["total_samples"], O.sample_at(total, n, d))
                self.assertEqual(plan.total_samples, O.sample_at(total, n, d))
                cases += 1
        record("span_oracle", seed=SEED_SPAN, cases=cases)

    def test_adjacent_frame_ranges_tile_the_sample_axis_without_gap_or_overlap(self):
        for n, d in FPS_CATALOG:
            if Fraction(n, d) > 48000:
                continue
            total = 61
            evs = [{"id": f"e{k}", "source_id": "s", "state": "UNKNOWN", "start_frame": k, "end_frame": k + 1} for k in range(total)]
            plan = aplan.prepare_plan(jb(manifest(fps=(n, d), total=total, events=evs)))
            spans = [(e["sample_start"], e["sample_end"]) for e in plan.canonical["events"]]
            self.assertEqual(spans[0][0], 0)
            self.assertEqual(spans[-1][1], plan.total_samples)
            for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
                self.assertEqual(a1, b0, (n, d))
                self.assertGreaterEqual(a1, a0)

    def test_avmux_declared_sample_count_accepts_exactly_the_oracle_count(self):
        sb = self.sb
        sb.mkdir("assets")
        vbytes = b"synthetic-not-a-video"
        sb.write("assets/v.mp4", vbytes)
        cases = 0
        limits = Limits(max_fps=60000)
        for n, d in [(24, 1), (30, 1), (30000, 1001), (24000, 1001), (60000, 1001), (7, 3), (16, 1), (256, 1), (48000, 1001)]:
            for frames in (1, 2, 5, 30):
                want = O.sample_at(frames, n, d)
                if want > 60000:
                    continue
                good = wav_from_frames(pcm_tone(want, 1, want))
                sb.write("assets/a.wav", good)
                plan = vplan.prepare_plan(jb(avmux_manifest(video_bytes=vbytes, wav_bytes_=good, fps=(n, d), frames=frames, samples=want)), limits=limits, asset_root=sb.p("assets"))
                self.assertEqual(plan.canonical["timeline"]["expected_audio_samples"], want, (n, d, frames))
                for off in (-1, 1):
                    other = wav_from_frames(pcm_tone(want + off, 1, 5))
                    sb.write("assets/a.wav", other)
                    with self.assertRaises(errors.ValidationFailed, msg=(n, d, frames, off)) as cm:
                        vplan.prepare_plan(jb(avmux_manifest(video_bytes=vbytes, wav_bytes_=other, fps=(n, d), frames=frames, samples=want + off)), limits=limits, asset_root=sb.p("assets"))
                    self.assertEqual([i.code for i in cm.exception.issues], ["audio_sample_count_mismatch"])
                    cases += 1
                cases += 1
        record("avmux_sample_rule", cases=cases)
        self.assertGreater(cases, 60)

    def test_use_event_source_length_must_equal_timeline_length_at_alternating_boundaries(self):
        """30000/1001: one frame is 1601 or 1602 samples depending on position. The planner must accept the exact oracle length and
        reject +-1 for every single-frame event of the first 30 frames."""
        sb = self.sb
        sb.mkdir("assets")
        w = wav_from_frames(pcm_tone(60000, 1, 7))
        sb.write("assets/a.wav", w)
        assets = [{"id": "a", "path": "a.wav", "sha256": sha256(w)}]
        lengths = set()
        for k in range(30):
            ln = O.sample_at(k + 1, 30000, 1001) - O.sample_at(k, 30000, 1001)
            lengths.add(ln)
            for off, ok in ((0, True), (-1, False), (1, False)):
                ev = {"id": "u", "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 10, "source_out_sample": 10 + ln + off, "start_frame": k, "end_frame": k + 1}
                doc = manifest(fps=(30000, 1001), total=30, assets=assets, events=[ev])
                if ok:
                    self.assertEqual(aplan.prepare_plan(jb(doc), asset_root=sb.p("assets")).status, "READY")
                else:
                    with self.assertRaises(errors.ValidationFailed) as cm:
                        aplan.prepare_plan(jb(doc), asset_root=sb.p("assets"))
                    self.assertEqual([i.code for i in cm.exception.issues], ["range_length_mismatch"])
        self.assertEqual(lengths, {1601, 1602})


# --------------------------------------------------------------------------------------------------------------------------- semantics
class SelectionAndHold(AuditCase):
    def _assets(self, n=30000):
        sb = self.sb
        sb.mkdir("assets")
        w = wav_from_frames(pcm_tone(n, 1, 21))
        sb.write("assets/a.wav", w)
        return [{"id": "a", "path": "a.wav", "sha256": sha256(w)}]

    def _use(self, eid, f0, f1, fps=(30, 1), **kw):
        ln = O.sample_at(f1, *fps) - O.sample_at(f0, *fps)
        e = {"id": eid, "source_id": "s", "state": "USE", "asset": "a", "source_in_sample": 0, "source_out_sample": ln, "start_frame": f0, "end_frame": f1}
        e.update(kw)
        return e

    def test_selected_unresolved_is_hold_never_silence_even_when_zero_fill_is_allowed(self):
        assets = self._assets()
        for st in ("MISSING", "UNKNOWN", "PROXY"):
            doc = manifest(total=6, assets=assets, gap="ALLOW_ZERO_FILL",
                           events=[self._use("u", 0, 3), {"id": "x", "source_id": "s", "state": st, "start_frame": 3, "end_frame": 6}], scope=["u", "x"])
            plan = aplan.prepare_plan(jb(doc), asset_root=self.sb.p("assets"))
            self.assertEqual(plan.status, "HOLD", st)
            self.assertEqual([h["code"] for h in plan.hold], ["SELECTED_UNRESOLVED"])
            r = amix.mix(jb(doc), asset_root=self.sb.p("assets"), output_root=self.sb.mkdir("out"), output_name=f"h-{st}")
            self.assertEqual((r["status"], r["written"]), ("HOLD", False))
            self.assertFalse(os.path.exists(self.sb.p("out", f"h-{st}")), "HOLD must not create a directory")

    def test_unselected_unresolved_is_reported_but_never_blocks(self):
        assets = self._assets()
        doc = manifest(total=6, assets=assets, gap="ALLOW_ZERO_FILL",
                       events=[self._use("u", 0, 6), {"id": "x", "source_id": "s", "state": "UNKNOWN", "start_frame": 0, "end_frame": 6}], scope=["u"])
        plan = aplan.prepare_plan(jb(doc), asset_root=self.sb.p("assets"))
        self.assertEqual(plan.status, "READY")
        self.assertEqual(plan.canonical["unresolved"], [{"event_id": "x", "source_id": "s", "state": "UNKNOWN", "selected": False}])

    def test_omit_is_not_coverage_but_intentional_silence_is(self):
        assets = self._assets()
        use = self._use("u", 0, 3)
        omit = {"id": "o", "source_id": "s", "state": "OMIT", "decision_ref": "r1", "start_frame": 3, "end_frame": 6}
        sil = {"id": "z", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "r2", "start_frame": 3, "end_frame": 6}
        for ev, want, gaps in ((omit, "HOLD", [(4800, 9600)]), (sil, "READY", [])):
            doc = manifest(total=6, assets=assets, gap="REQUIRE_FULL_COVERAGE", events=[use, ev])
            plan = aplan.prepare_plan(jb(doc), asset_root=self.sb.p("assets"))
            self.assertEqual(plan.status, want, ev["state"])
            self.assertEqual([(g["sample_start"], g["sample_end"]) for g in plan.canonical["coverage"]["gaps"]], gaps)

    def test_zero_fill_is_labelled_and_exact(self):
        assets = self._assets()
        doc = manifest(total=6, assets=assets, gap="ALLOW_ZERO_FILL", events=[self._use("u", 1, 2)])
        plan = aplan.prepare_plan(jb(doc), asset_root=self.sb.p("assets"))
        self.assertEqual(plan.status, "READY")
        self.assertEqual([w["code"] for w in plan.canonical["warnings"]], ["ZERO_FILL_APPLIED"])
        self.assertEqual([(g["sample_start"], g["sample_end"]) for g in plan.canonical["coverage"]["gaps"]], [(0, 1600), (3200, 9600)])
        self.assertEqual(plan.canonical["coverage"]["covered_samples"], 1600)

    def test_silence_may_not_overlap_consumed_audio_but_touching_is_fine(self):
        assets = self._assets()
        sil = lambda f0, f1: {"id": "z", "source_id": "s", "state": "INTENTIONAL_SILENCE", "decision_ref": "r", "start_frame": f0, "end_frame": f1}
        ok = manifest(total=6, assets=assets, events=[self._use("u", 0, 3), sil(3, 6)])
        self.assertEqual(aplan.prepare_plan(jb(ok), asset_root=self.sb.p("assets")).status, "READY")
        for pol in ("REJECT", "ALLOW_SUM"):
            bad = manifest(total=6, assets=assets, overlap=pol, events=[self._use("u", 0, 4), sil(3, 6)])
            with self.assertRaises(errors.ValidationFailed) as cm:
                aplan.prepare_plan(jb(bad), asset_root=self.sb.p("assets"))
            self.assertEqual([i.code for i in cm.exception.issues], ["silence_overlap"])

    def test_concurrency_limit_is_exact(self):
        assets = self._assets()
        mk = lambda k: manifest(total=3, assets=assets, overlap="ALLOW_SUM", clipping="CLAMP", events=[self._use(f"u{i}", 0, 3) for i in range(k)])
        self.assertEqual(aplan.prepare_plan(jb(mk(8)), asset_root=self.sb.p("assets")).status, "READY")
        with self.assertRaises(errors.ValidationFailed) as cm:
            aplan.prepare_plan(jb(mk(9)), asset_root=self.sb.p("assets"))
        self.assertEqual([i.code for i in cm.exception.issues], ["limit_concurrent"])
        # touching events do not count as concurrent
        seq = manifest(total=9, assets=assets, overlap="ALLOW_SUM", events=[self._use(f"u{i}", i, i + 1) for i in range(9)])
        self.assertEqual(aplan.prepare_plan(jb(seq), asset_root=self.sb.p("assets"), limits=AudioLimits(max_concurrent=1)).status, "READY")

    def test_all_omit_manifest_yields_a_verified_all_zero_stem_characterisation(self):
        """Design observation O-1 (not a defect): with ALLOW_ZERO_FILL a manifest that selects only OMIT events mixes a stem of pure
        zeros and the receipt says MIXED_VERIFIED (warnings carry NO_AUDIO_EVENTS_SELECTED / ZERO_FILL_APPLIED). A later qualifier that
        reads only `status` cannot tell zero-fill from audio."""
        doc = manifest(total=3, gap="ALLOW_ZERO_FILL", events=[{"id": "o", "source_id": "s", "state": "OMIT", "decision_ref": "r"}])
        r = amix.mix(jb(doc), asset_root=None, output_root=self.sb.mkdir("out"), output_name="allzero")
        with open(self.sb.p("out", "allzero", "stem.wav"), "rb") as fh:
            body = fh.read()[44:]
        record("O-1/all_zero_stem", status=r["status"], warnings=[w["code"] for w in r["warnings"]], all_zero=(set(body) == {0}), bytes=len(body))
        self.assertEqual(r["status"], "MIXED_VERIFIED")
        self.assertEqual(set(body), {0})
        self.assertEqual(sorted(w["code"] for w in r["warnings"]), ["NO_AUDIO_EVENTS_SELECTED", "ZERO_FILL_APPLIED"])


# --------------------------------------------------------------------------------------------------------------------------- property test
class PlannerMixerProperty(AuditCase):
    FPS = [(30, 1), (30000, 1001), (24, 1), (25, 1), (60, 1), (24000, 1001)]

    def _scenario(self, r: "O.Rng"):
        n, d = r.choice(self.FPS)
        total = r.between(3, 10)
        oc = r.choice([1, 2])
        events, order = [], []
        for i in range(r.between(1, 5)):
            f0 = r.between(0, total - 1)
            f1 = r.between(f0 + 1, total)
            st = r.choice(["USE", "USE", "USE", "INTENTIONAL_SILENCE", "OMIT", "UNKNOWN", "MISSING", "PROXY"])
            e = {"id": f"e{i}", "source_id": "src", "state": st}
            if st != "OMIT" or r.chance(1, 2):
                e["start_frame"], e["end_frame"] = f0, f1
            if st in ("INTENTIONAL_SILENCE", "OMIT"):
                e["decision_ref"] = f"ref-{i}"
            if st == "USE":
                a = r.choice(["m", "s"])
                span = O.sample_at(f1, n, d) - O.sample_at(f0, n, d)
                ln = span + (0 if r.chance(9, 10) else r.choice([-1, 1]))
                ln = max(ln, 1)
                sin = r.between(0, 30000 - ln)
                e.update(asset=a, source_in_sample=sin, source_out_sample=sin + ln)
                src_ch = 1 if a == "m" else 2
                if src_ch == oc:
                    rule = r.choice([None, "COPY", "COPY", "MONO_TO_STEREO_DUPLICATE"])
                elif src_ch == 1:
                    rule = r.choice([None, "MONO_TO_STEREO_DUPLICATE", "MONO_TO_STEREO_DUPLICATE", "STEREO_TO_MONO_LEFT"])
                else:
                    rule = r.choice([None, "STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE", "COPY"])
                if rule:
                    e["channel_rule"] = rule
                if r.chance(1, 2):
                    den = r.choice([1, 2, 3, 7, 255, 65536])
                    e["gain"] = {"num": r.between(0, 4 * den), "den": den}
            events.append(e)
        scope = [e["id"] for e in events if r.chance(3, 4)] or [events[0]["id"]]
        doc = manifest(fps=(n, d), total=total, channels=oc, gap=r.choice(["ALLOW_ZERO_FILL", "REQUIRE_FULL_COVERAGE"]), overlap=r.choice([None, "REJECT", "ALLOW_SUM"]),
                       clipping=r.choice([None, "REJECT", "CLAMP"]), events=events, scope=scope)
        return doc

    def _channel_problem(self, doc):
        oc = doc["output"]["channels"]
        sel = set(doc["scope"]["event_ids"])
        for e in doc["events"]:
            if e["id"] in sel and e["state"] == "USE":
                src = 1 if e["asset"] == "m" else 2
                rule = e.get("channel_rule")
                allowed = ("COPY",) if src == oc else (("MONO_TO_STEREO_DUPLICATE",) if src == 1 else ("STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE"))
                if (rule is None and src != oc) or (rule is not None and rule not in allowed):
                    return True
        return False

    def test_seeded_scenarios_agree_with_the_independent_planner_and_mixer_oracle(self):
        sb = self.sb
        sb.mkdir("assets")
        out = sb.mkdir("out")
        m_frames = pcm_tone(30000, 1, 31, amp=20000)
        s_frames = pcm_tone(30000, 2, 32, amp=20000)
        wm, ws = wav_from_frames(m_frames, 1), wav_from_frames(s_frames, 2)
        sb.write("assets/m.wav", wm)
        sb.write("assets/s.wav", ws)
        assets = [{"id": "m", "path": "m.wav", "sha256": sha256(wm)}, {"id": "s", "path": "s.wav", "sha256": sha256(ws)}]
        pcm_of, ch_of = {"m": m_frames, "s": s_frames}, {"m": 1, "s": 2}
        r = O.Rng(SEED_PLAN)
        tally = {"cases": 0, "REJECT": 0, "HOLD": 0, "READY": 0, "mixed": 0, "statuses": {}}
        bad = []
        limits = AudioLimits(chunk_samples=997)  # prime chunk size: event and chunk boundaries never align
        for k in range(700):
            doc = self._scenario(r)
            doc["assets"] = assets
            want = O.audio_plan_oracle(doc, {"m": 30000, "s": 30000}, ch_of)
            if want["verdict"] != "REJECT" and self._channel_problem(doc):
                want = {"verdict": "REJECT", "reason": "channel", "total": want["total"]}
            try:
                plan = aplan.prepare_plan(jb(doc), limits=limits, asset_root=sb.p("assets"))
                got = plan.status
            except errors.VideoCoreError:
                got = "REJECT"
            tally["cases"] += 1
            tally[want["verdict"]] += 1
            if got != want["verdict"]:
                bad.append({"k": k, "want": want["verdict"], "reason": want.get("reason"), "got": got})
                continue
            if got == "READY":
                self.assertEqual([(g["sample_start"], g["sample_end"]) for g in plan.canonical["coverage"]["gaps"]], want["gaps"], k)
                self.assertEqual(plan.canonical["coverage"]["covered_samples"], want["covered"], k)
                expect_bytes, st = O.mix_oracle(doc, pcm_of, ch_of)
                rec = amix.mix(jb(doc), limits=limits, asset_root=sb.p("assets"), output_root=out, output_name=f"c{k}")
                tally["mixed"] += 1
                tally["statuses"][rec["status"]] = tally["statuses"].get(rec["status"], 0) + 1
                pol = doc["policy"].get("clipping", "REJECT")
                if st["clipped"] and pol == "REJECT":
                    if rec["status"] != "CLIPPING_REJECTED" or rec["mix"]["first_clipped_sample"] != st["first_clipped_sample"]:
                        bad.append({"k": k, "want": "CLIPPING_REJECTED@%s" % st["first_clipped_sample"], "got": (rec["status"], rec["mix"]["first_clipped_sample"])})
                    continue
                want_status = "MIXED_WITH_CLAMPING" if st["clipped"] else "MIXED_VERIFIED"
                with open(sb.p("out", f"c{k}", "stem.wav"), "rb") as fh:
                    got_bytes = fh.read()[44:]
                if rec["status"] != want_status or got_bytes != expect_bytes or rec["mix"]["clipped_samples"] != st["clipped"] or rec["mix"]["peak_before_clip"] != st["peak_before_clip"]:
                    bad.append({"k": k, "want": want_status, "got": rec["status"], "bytes_equal": got_bytes == expect_bytes, "clipped": (rec["mix"]["clipped_samples"], st["clipped"])})
                os.remove(sb.p("out", f"c{k}", "stem.wav"))  # keep the sandbox small
            elif got == "HOLD":
                r2 = amix.mix(jb(doc), limits=limits, asset_root=sb.p("assets"), output_root=out, output_name=f"h{k}")
                if r2["status"] != "HOLD" or os.path.exists(sb.p("out", f"h{k}")):
                    bad.append({"k": k, "want": "HOLD without directory", "got": r2["status"]})
        record("planner_mixer_property", seed=SEED_PLAN, **tally, disagreements=len(bad))
        self.assertGreater(tally["REJECT"], 20)
        self.assertGreater(tally["HOLD"], 10)
        self.assertGreater(tally["mixed"], 40)
        self.assertGreater(tally["statuses"].get("MIXED_WITH_CLAMPING", 0) + tally["statuses"].get("CLIPPING_REJECTED", 0), 3, "clipping paths must be exercised")
        self.assertEqual(bad[:3], [])

    def test_channel_rule_matrix_against_oracle(self):
        sb = self.sb
        sb.mkdir("assets")
        out = sb.mkdir("out")
        m_frames, s_frames = pcm_tone(3200, 1, 41), pcm_tone(3200, 2, 42)
        wm, ws = wav_from_frames(m_frames, 1), wav_from_frames(s_frames, 2)
        sb.write("assets/m.wav", wm)
        sb.write("assets/s.wav", ws)
        assets = [{"id": "m", "path": "m.wav", "sha256": sha256(wm)}, {"id": "s", "path": "s.wav", "sha256": sha256(ws)}]
        pcm_of, ch_of = {"m": m_frames, "s": s_frames}, {"m": 1, "s": 2}
        rules = [None, "COPY", "MONO_TO_STEREO_DUPLICATE", "STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE"]
        accepted = 0
        for a in ("m", "s"):
            for oc in (1, 2):
                for rule in rules:
                    src = ch_of[a]
                    allowed = ("COPY",) if src == oc else (("MONO_TO_STEREO_DUPLICATE",) if src == 1 else ("STEREO_TO_MONO_LEFT", "STEREO_TO_MONO_RIGHT", "STEREO_TO_MONO_AVERAGE"))
                    ok = (rule in allowed) or (rule is None and src == oc)
                    ev = {"id": "u", "source_id": "s", "state": "USE", "asset": a, "source_in_sample": 5, "source_out_sample": 5 + 1600, "start_frame": 0, "end_frame": 1}
                    if rule:
                        ev["channel_rule"] = rule
                    doc = manifest(total=1, channels=oc, assets=assets, events=[ev])
                    try:
                        aplan.prepare_plan(jb(doc), asset_root=sb.p("assets"))
                        got = True
                    except errors.ValidationFailed:
                        got = False
                    self.assertEqual(got, ok, (a, oc, rule))
                    if ok:
                        name = f"{a}{oc}{rule}"
                        rec = amix.mix(jb(doc), asset_root=sb.p("assets"), output_root=out, output_name=name)
                        want, _ = O.mix_oracle(doc, pcm_of, ch_of)
                        with open(sb.p("out", name, "stem.wav"), "rb") as fh:
                            self.assertEqual(fh.read()[44:], want, (a, oc, rule))
                        self.assertEqual(rec["status"], "MIXED_VERIFIED")
                        accepted += 1
        record("channel_rule_matrix", accepted=accepted)
        self.assertEqual(accepted, 2 + 1 + 3 + 2)  # (mono,out1): None+COPY; (mono,out2): DUPLICATE; (stereo,out1): LEFT+RIGHT+AVERAGE; (stereo,out2): None+COPY


# --------------------------------------------------------------------------------------------------------------------------- video-only default / hash compatibility
class VideoOnlyDefault(AuditCase):
    def test_baseline_plan_hash_and_bytes_are_stable_and_video_only(self):
        rc, out, err, to = cli("video_core", ["plan", os.path.join(TOOLS, "examples", "episode.synthetic.json")])
        self.assertEqual((rc, to), (0, False), err[-300:])
        doc = json.loads(out)
        canon = json.dumps(doc["plan"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        h = hashlib.sha256(canon).hexdigest()
        self.assertEqual(doc["plan_sha256"], h)
        self.assertTrue(h.startswith("4bc20b3f") and h.endswith("9f21"), h)  # the value pinned in LIMITATIONS.md / ACCEPTANCE evidence
        with open(os.path.join(TOOLS, "evidence", "plan.canonical.json"), "rb") as fh:
            archived = fh.read()
        self.assertEqual(hashlib.sha256(archived).hexdigest(), h, "archived evidence plan equals today's plan byte for byte")
        argv = doc["plan"]["ffmpeg"]["argv"]
        for flag in ("-an", "-sn", "-dn"):
            self.assertIn(flag, argv)
        maps = [argv[i + 1] for i, a in enumerate(argv) if a == "-map"]
        self.assertEqual(maps, ["[vout]"], "the only mapped stream is the filtergraph's video output")
        self.assertNotIn("-c:a", argv)
        self.assertNotIn("-b:a", argv)
        record("baseline_plan_hash", sha256=h, interpreter=PY)

    def test_importing_the_audio_modules_does_not_change_the_video_plan(self):
        ep = os.path.join(TOOLS, "examples", "episode.synthetic.json")
        outs = []
        for pre in ("", "import video_core.audiomix, video_core.avmux, video_core.audiomix.cli, video_core.avmux.cli\n"):
            body = pre + textwrap.dedent(f'''
            import sys
            from video_core.plan import prepare
            p = prepare(open({ep!r}, "rb").read())
            sys.stdout.write(p.sha256 + " " + ("audiomix" in " ".join(sys.modules) and "loaded" or "absent"))
            ''')
            rc, out, err, events, to = audited(body)
            self.assertEqual(rc, 0, err[-300:])
            outs.append(out)
            self.assertEqual(events, [])
        self.assertEqual(outs[0].split()[0], outs[1].split()[0])
        self.assertEqual((outs[0].split()[1], outs[1].split()[1]), ("absent", "loaded"))

    def test_srt_matches_example_file_and_oracle(self):
        rc, out, err, to = cli("video_core", ["srt", os.path.join(TOOLS, "examples", "episode.synthetic.json")])
        self.assertEqual(rc, 0, err)
        with open(os.path.join(TOOLS, "examples", "captions.synthetic.srt"), "rb") as fh:
            self.assertEqual(out.encode("utf-8"), fh.read())
        self.assertEqual(out.encode("utf-8"), O.expected_srt(json.loads(example_episode_bytes())))

    def test_burn_in_enable_windows_agree_with_srt_cue_ends_and_oracle(self):
        sb = self.sb
        sb.mkdir("assets")
        doc = json.loads(example_episode_bytes())
        doc["fps"] = {"n": 30000, "d": 1001}
        doc["captions"] = [{"id": "b", "start_frame": 40, "end_frame": 90, "text": "テスト B"}, {"id": "a", "start_frame": 0, "end_frame": 40, "text": "テスト A"},
                           {"id": "c", "start_frame": 40, "end_frame": 41, "text": "ABC"}]
        font = sfnt_font([ord(c) for c in "テストABC "], fmt=12)
        sb.write("assets/f.ttf", font)
        from video_core.plan import prepare
        plan = prepare(jb(doc), asset_root=sb.p("assets"), burn_in_font="f.ttf")
        caps = plan.canonical["burn_in"]["captions"]
        order = sorted(range(3), key=lambda i: (doc["captions"][i]["start_frame"], doc["captions"][i]["end_frame"], i))
        self.assertEqual([c["caption_id"] for c in caps], [doc["captions"][i]["id"] for i in order])
        for c, i in zip(caps, order):
            src = doc["captions"][i]
            self.assertEqual(c["enable"], f"between(n,{src['start_frame']},{src['end_frame'] - 1})")
        # SRT cue end for caption 'a' is the START of the next cue boundary (exclusive end) at the same absolute timestamp
        srt = plan.srt.decode("utf-8").split("\n\n")
        self.assertIn(O.srt_stamp(O.ms_at(40, 30000, 1001)) + " --> ", srt[1])
        self.assertIn(" --> " + O.srt_stamp(O.ms_at(40, 30000, 1001)), srt[0])
        self.assertEqual(plan.srt, O.expected_srt(doc))
        self.assertEqual(plan.canonical["captions"][0]["srt_start"], O.srt_stamp(O.ms_at(40, 30000, 1001)))


if __name__ == "__main__":
    unittest.main()
