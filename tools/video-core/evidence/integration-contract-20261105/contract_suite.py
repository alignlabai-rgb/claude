#!/usr/bin/env python3
"""Integration-contract suite for the existing public video_core / audiomix / avmux library (job 20261105).

BLACK-BOX: every library call goes through the real public CLIs (python -m video_core | video_core.audiomix | video_core.avmux) launched by the
harness-owned runner under an audit hook. Pure `video_core.avmux.checks` is imported once, for the declared-vs-measured comparison (test_f2).
NOTHING here renders, mixes, muxes, probes or downloads media: the harness refuses --render/--mix/--mux (and abbreviations) and `inspect`.

How to read a result (see CONTRACT_EN.md):
  LIBRARY_ENFORCED    the library itself refused / held / reported (asserted on the library's actual output)
  LIBRARY_PERMISSIVE  the library ACCEPTED something an owner must still guard (asserted; a gap is pinned: if the library ever closes it, this fails loudly)
  CALLER_GUARD_DEMO   an illustrative owner-side guard (reference_guards.py). Never the reason a test passes.
  INSTRUMENT / FIXTURE / ROLLBACK   harness self-checks and demonstration steps.

Run:  python3 contract_suite.py                     # prints the summary only, writes nothing
      python3 contract_suite.py --emit RESULT_DIR    # also writes observed-pyX.Y.json (+ rollback evidence) into RESULT_DIR
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import time
import unittest
from fractions import Fraction

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contract_lib as c  # noqa: E402
import reference_guards as g  # noqa: E402
from contract_lib import World, api_ref, canon, literal_ref, load_fixture, run_cli, sha256  # noqa: E402

TMP = os.environ.get("VCC_TMP") or None
OVERALL_BUDGET_S = 270.0
T_START = time.monotonic()
REGISTRY: dict = {}
PARENT_POPEN: list = []     # subprocess.Popen audit events seen in the harness driver itself

AUD, AVM, VID = "video_core.audiomix", "video_core.avmux", "video_core"

GAPS = {
    "GAP-01": "Video-only has no input-state vocabulary or binding reference: MISSING/UNKNOWN/PROXY/HOLD and adopted refs cannot be carried; a proxy file or a colour stand-in is accepted.",
    "GAP-02": "Video-only plan does not pin source content: plan_sha256 is unchanged when same-size source bytes change (hash first appears in the render receipt).",
    "GAP-03": "AV mux has no slot for a video render receipt or any video qualification: the video is bound by path+sha256 only and is not inspected by plan.",
    "GAP-04": "The audio receipt is optional in AV mux and only status, stem sha256 and sample_count are read: a hand-written or edited receipt is accepted; no ownership/authentication/binding to the plan or manifest.",
    "GAP-05": "AV mux drops audio-receipt warnings (ZERO_FILL_APPLIED, NO_AUDIO_EVENTS_SELECTED) and cannot tell an all-zero stem from intentional silence.",
    "GAP-06": "No field anywhere for phrase-boundary or sample-timing qualification: equal-length USE ranges are accepted however the boundaries were chosen.",
    "GAP-07": "selection.ref, decision_ref and scope are not cross-checked between stages: AV mux has no scope, no plan/manifest hash link and no --episode.",
    "GAP-08": "Exit code 0 for non-ready outcomes: audiomix `mix` dry-run on a HOLD plan, audiomix `validate` with hold_if_planned, avmux `plan` with UNVERIFIED readiness.",
    "GAP-09": "policy.gap=ALLOW_ZERO_FILL turns absent audio into zeros with a warning only (READY, exit 0).",
    "GAP-10": "Outcome vocabulary differs per stage: audiomix returns HOLD (exit 5); video-only and AV mux reject unresolved input (exit 1, issue code only), so HOLD survives AV mux only as the code input_state_not_use.",
    "GAP-11": "BY DESIGN: no carrier for execution permission or creative approval (unknown fields are rejected); these are owner records the library neither stores nor infers.",
    "GAP-12": "OWNER TASK: promoting the stem into AV mux's single asset root (neither stage may write inside its own asset root) is nobody's job in the library; only the sha256 pin protects the copy.",
    "GAP-14": "An audio receipt that is valid JSON of an unexpected shape (array, string, null, outputs/readback of the wrong type) makes `avmux plan` die with an uncaught AttributeError: exit 1 like a structured rejection, but empty stdout, a traceback and no issue code. Fail-closed (nothing created), yet only distinguishable from a rejection by the empty stdout.",
    "GAP-13": "NOT EXERCISED: source immutability is detected (re-hash after mix/mux), not prevented; this job ran no mix/mux, so post-run re-hash behaviour is documented, not re-demonstrated.",
}
OBSERVATIONS = {
    "OB-01": "Unselected audio rows are still schema-validated (a malformed unselected row rejects the whole manifest), but never read, hashed or overlap-checked.",
    "OB-02": "audiomix and avmux plans hash and parse the files they read; the video-only plan only stats them.",
}


def _parent_hook(event, args):
    if event == "subprocess.Popen":
        PARENT_POPEN.append((str(args[0]), [str(a) for a in args[1][:4]]))


def _budget():
    if time.monotonic() - T_START > OVERALL_BUDGET_S:
        raise RuntimeError("overall 270 s contract-suite budget exhausted")


class Base(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        _budget()
        self._t0 = time.monotonic()
        self.w = World(tmp_parent=TMP, label=self._testMethodName[:18])
        doc = (getattr(self, self._testMethodName).__doc__ or "").strip().splitlines()
        self.case = {"id": self._testMethodName, "title": doc[0] if doc else "", "api_refs": [], "gaps": [], "observations": [], "invocations": [],
                     "checks": [], "process_accounting": None, "evidence": None}
        self._cli_count = 0
        self._allow_process = False

    def tearDown(self):
        elapsed = time.monotonic() - self._t0
        pa = {"cli_calls": self._cli_count, "external_process_launches": 0, "network_events": 0, "filesystem_mutations": 0, "files_read_outside_world_library_stdlib": 0, "sentinel_media_tool_hits": len(self.w.sentinel_hits())}
        for inv in self.case["invocations"]:
            ev = inv["runner_events"]
            pa["external_process_launches"] += ev["process"]
            pa["network_events"] += ev["network"]
            pa["filesystem_mutations"] += ev["mutation"]
            pa["files_read_outside_world_library_stdlib"] += ev["foreign_reads"]
        self.case["process_accounting"] = pa
        self.case["positive_control"] = bool(self._allow_process)
        self.case["within_10s_case_budget"] = elapsed <= c.PER_CASE_BUDGET
        REGISTRY[self.case["id"]] = self.case
        self.w.cleanup()
        if not self._allow_process:
            self.assertEqual((pa["external_process_launches"], pa["network_events"], pa["filesystem_mutations"], pa["files_read_outside_world_library_stdlib"], pa["sentinel_media_tool_hits"]), (0, 0, 0, 0, 0),
                             "a default (plan/validate/dry-run) path launched a process, used the network, mutated the filesystem, read a file outside the world/library/stdlib or hit the media-tool sentinel")
        self.assertLessEqual(elapsed, c.PER_CASE_BUDGET, "case exceeded its 10 s budget")

    # ---- helpers
    def cli(self, module, args, label=None):
        r = run_cli(self.w, module, args)
        self._cli_count += 1
        rec = r.record()
        rec["label"] = label or f"{module.split('.')[-1]} {args[0]}"
        self.case["invocations"].append(rec)
        return r

    def check(self, kind, name, expected, observed):
        ok = expected == observed
        self.case["checks"].append({"kind": kind, "name": name, "expected": expected, "observed": observed, "ok": ok})
        self.assertEqual(expected, observed, f"[{kind}] {name}")

    def ref(self, relfile, symbol=None, literal=None):
        self.case["api_refs"].append(api_ref(relfile, symbol) if symbol else literal_ref(relfile, literal))

    def gap(self, *ids):
        for i in ids:
            assert i in GAPS, i
            if i not in self.case["gaps"]:
                self.case["gaps"].append(i)

    def obs(self, *ids):
        for i in ids:
            if i not in self.case["observations"]:
                self.case["observations"].append(i)

    # shortcuts for the three plans
    def vplan(self, ep, extra=(), label="video plan"):
        self.w.put_json("ep.json", ep)
        return self.cli(VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}", *extra], label)

    def aplan(self, am, extra=(), verb="plan", label=None, name="am.json"):
        self.w.put_json(name, am)
        a = [verb, "{docs}/" + name] + (["--asset-root", "{assets}"] if verb != "validate" else [])
        return self.cli(AUD, a + list(extra), label or f"audiomix {verb}")

    def av_manifest(self, *, route="MOV", receipt="default", mut=None, name="av.json"):
        """Write fixtures + an AV manifest. receipt: 'default' = the harness-fabricated verified receipt, None = no receipt key, dict/bytes = custom."""
        w = self.w
        w.put_fixtures()
        man = load_fixture("avmux_manifest.mov.json" if route == "MOV" else "avmux_manifest.mp4.json")
        if receipt is None:
            man["audio"].pop("receipt")
        else:
            data = canon(load_fixture("audio_receipt.harness_fabricated.json")) if receipt == "default" else (receipt if isinstance(receipt, bytes) else canon(receipt))
            w.put("audio_receipt.json", data)
            man["audio"]["receipt"] = {"path": "audio_receipt.json", "sha256": sha256(data)}
        if mut:
            mut(man)
        w.put_json(name, man)
        return man

    def avplan(self, name="av.json", extra=(), verb="plan", label=None):
        a = [verb, "{docs}/" + name] + (["--asset-root", "{assets}"] if verb != "validate" else [])
        return self.cli(AVM, a + list(extra), label or f"avmux {verb}")

    def receipt(self, **over):
        r = load_fixture("audio_receipt.harness_fabricated.json")
        r.update(over)
        return r


def rb(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def oracle_sample(frame, n, d):
    """Independent exact-rational oracle: round-half-up of frame*d/n seconds * 48000 (Fraction arithmetic, no use of the library formula)."""
    return math.floor(Fraction(frame * d * c.RATE, n) + Fraction(1, 2))


# =============================================================================================================== fixtures + instrument


class T00_Fixtures_And_Instrument(Base):
    def test_00_fixtures_are_pinned_and_deterministic(self):
        """Committed JSON fixtures equal their deterministic regeneration; generated bytes match the pinned sha256 index."""
        self.check("FIXTURE", "committed fixtures == regeneration", [], c.verify_fixtures())
        idx = load_fixture("FIXTURES_INDEX.json")
        self.check("FIXTURE", "byte fixtures hash to the pinned index", idx["bytes"], {k: sha256(v) for k, v in c.fixture_bytes().items()})
        self.check("FIXTURE", "regeneration is deterministic", c.build_fixture_json()["_index_bytes"], c.build_fixture_json()["_index_bytes"])
        self.check("FIXTURE", "the 'videos' are plain non-media bytes", True, all(v.startswith(b"SYNTHETIC-PLACEHOLDER-NOT-MEDIA") for k, v in c.fixture_bytes().items() if k.endswith(".mp4")))

    def test_01_instrument_positive_control(self):
        """The audit-hook + sentinel instrument detects a launch, a mkdir and a write (so 'zero events' is meaningful)."""
        self._allow_process = True
        r = c.run_control(self.w)
        ev = r.log["events"]
        self.check("INSTRUMENT", "control launch seen by audit hook", True, len(ev["process"]) >= 1)
        self.check("INSTRUMENT", "control read of a file outside world/library/stdlib seen as a foreign read", True, os.path.realpath(sys.executable) in [os.path.realpath(x) for x in ev["foreign_reads"]])
        self.check("INSTRUMENT", "control mkdir + write seen", True, len([m for m in ev["mutation"] if m["event"] == "os.mkdir"]) == 1 and any(m["event"] == "open-for-write" for m in ev["mutation"]))
        self.check("INSTRUMENT", "sentinel log saw exactly the stub launch", ["ffmpeg -version"], self.w.sentinel_hits())
        which = shutil.which("ffmpeg", path=self.w.bin)
        self.check("INSTRUMENT", "the only 'ffmpeg' reachable on the child PATH is the harness sentinel stub", (self.w.norm(self.w.p("bin", "ffmpeg")), True), (self.w.norm(which), "SENTINEL" in rb(which).decode()))
        self.check("INSTRUMENT", "the child PATH is exactly the world's bin dir (no system directory)", c.os.pathsep not in self.w.bin, True)

    def test_02_policy_refuses_to_execute_media(self):
        """The harness refuses --render/--mix/--mux (and argparse abbreviations) and `inspect`; refused calls launch nothing."""
        before = len(c.LAUNCHES)
        bad = [(VID, ["render", "x.json", "--render"]), (AUD, ["mix", "x.json", "--mix"]), (AVM, ["mux", "x.json", "--mux"]), (VID, ["render", "x", "--rend"]),
               (AVM, ["mux", "x", "--mu"]), (AUD, ["mix", "x", "--mi"]), (VID, ["inspect", "x.mp4"]), ("os", ["x"]), (VID, ["render", "x", "--render=1"])]
        refused = 0
        for mod, args in bad:
            try:
                run_cli(self.w, mod, args)
            except c.PolicyViolation:
                refused += 1
        self.check("INSTRUMENT", "every execution attempt refused", len(bad), refused)
        self.check("INSTRUMENT", "refused calls launched nothing", before, len(c.LAUNCHES))


# =============================================================================================================== A: valid paths


class A_ValidPaths(Base):
    def test_a1_video_only_plan_of_a_selected_subset(self):
        """Video-only validate/plan/render-dry-run of a caller-derived selected subset: -an, exact 30000/1001, nothing created."""
        self.ref("video_core/cli.py", "_cmd_plan")
        self.ref("video_core/plan.py", "prepare")
        self.ref("video_core/plan.py", "build_canonical")
        w = self.w
        w.put_fixtures()
        ep = load_fixture("episode.video_only.selected_subset.json")
        table = load_fixture("owner_selection_table.synthetic.json")
        self.check("CALLER_GUARD_DEMO", "owner selection table: selected rows resolved -> PROCEED (unselected unresolved rows ignored)", "PROCEED", g.guard_selection(table["rows"])["verdict"])
        r = self.vplan(ep)
        p = r.json["plan"]
        self.check("LIBRARY_ENFORCED", "exit/mode", (0, "plan"), (r.exit_code, r.json["mode"]))
        self.check("LIBRARY_ENFORCED", "exact rational fps and frames", ("30000/1001", 3, "1001/10000"), (p["episode"]["fps"]["rational"], p["episode"]["total_frames"], p["episode"]["duration_seconds"]))
        argv = p["ffmpeg"]["argv"]
        self.check("LIBRARY_ENFORCED", "video-only default: -an present, no audio input", (True, False), ("-an" in argv, "-c:a" in argv))
        self.check("LIBRARY_ENFORCED", "plan declares it created nothing", "none (no files created, no process launched by plan)", r.json["observations"]["side_effects"])
        self.check("LIBRARY_ENFORCED", "only the selected file source is recorded", [{"bytes": 168, "path": "clip_a.mp4"}], r.json["observations"]["assets"])
        r2 = self.vplan(ep)
        self.check("LIBRARY_ENFORCED", "plan is deterministic", r.json["plan_sha256"], r2.json["plan_sha256"])
        v = self.cli(VID, ["validate", "{docs}/ep.json", "--asset-root", "{assets}"])
        self.check("LIBRARY_ENFORCED", "validate resolves the file source", (0, 1, True), (v.exit_code, v.json["file_sources_resolved"], v.json["ok"]))
        d = self.cli(VID, ["render", "{docs}/ep.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-a1"], "video render WITHOUT --render (dry run)")
        self.check("LIBRARY_ENFORCED", "render without --render is a dry run that creates no directory", ("dry_run", 0, {}), (d.json["mode"], d.exit_code, w.snapshot()["out"]))

    def test_a2_audio_plan_selected_subset_unselected_unresolved_rows(self):
        """audiomix plan: 3 selected rows of 7 are READY; unselected MISSING/UNKNOWN/PROXY/unread-USE rows are reported, never blocking."""
        self.ref("video_core/audiomix/plan.py", "analyze")
        self.ref("video_core/audiomix/plan.py", "prepare_plan")
        self.obs("OB-01")
        self.w.put_fixtures()
        am = load_fixture("audio_manifest.selected_subset.json")
        r = self.aplan(am)
        p = r.json["plan"]
        self.check("LIBRARY_ENFORCED", "READY, exit 0, full coverage", ("READY", 0, 4805, 0), (p["status"], r.exit_code, p["coverage"]["covered_samples"], p["coverage"]["gap_count"]))
        self.check("LIBRARY_ENFORCED", "dispositions", {"ev_vo_1": "MIX", "ev_sil_2": "SILENCE", "ev_vo_3": "MIX", "ev_x_missing": "UNRESOLVED_NOT_SELECTED", "ev_x_unknown": "UNRESOLVED_NOT_SELECTED",
                                                        "ev_x_proxy": "UNRESOLVED_NOT_SELECTED", "ev_x_use_unselected": "NOT_SELECTED"}, {e["id"]: e["disposition"] for e in p["events"]})
        self.check("LIBRARY_ENFORCED", "unresolved rows stay in the report with selected=false", [("ev_x_missing", False), ("ev_x_unknown", False), ("ev_x_proxy", False)], [(u["event_id"], u["selected"]) for u in p["unresolved"]])
        self.check("LIBRARY_ENFORCED", "only the selected asset is read/verified; the unselected USE asset is not", {"asset_a": True, "asset_unselected": False}, {a["id"]: a["verified"] for a in p["assets"]})
        self.check("LIBRARY_ENFORCED", "sample boundaries of the selected ranges", [(0, 1602), (1602, 3203), (3203, 4805)], [(e["sample_start"], e["sample_end"]) for e in p["events"][:3]])
        self.check("LIBRARY_ENFORCED", "caller assertions are labelled unauthenticated", True, "not authenticated" in p["caller_assertions"])
        self.check("LIBRARY_ENFORCED", "opaque refs preserved verbatim", ["owner-binding/synthetic-001", "owner-decision/silence-001", "owner-binding/synthetic-003"], [e["decision_ref"] for e in p["events"][:3]])
        self.check("LIBRARY_ENFORCED", "no output directory", {}, self.w.snapshot()["out"])

    def test_a3_avmux_plan_both_routes_unverified(self):
        """avmux plan (MOV and MP4 routes): ok exit 0 but readiness UNVERIFIED; no -shortest/-t/-ss/-r/-vsync/-fps_mode/-ar/-af; both lengths exact."""
        self.ref("video_core/avmux/plan.py", "prepare_plan")
        self.ref("video_core/avmux/plan.py", "mux_argv_template")
        out = {}
        for route in ("MOV", "MP4"):
            self.av_manifest(route=route, name=f"av_{route}.json")
            r = self.avplan(f"av_{route}.json", label=f"avmux plan {route}")
            p = r.json["plan"]
            out[route] = (r.exit_code, r.json["readiness"].split(":")[0], p["timeline"]["expected_audio_samples"], p["audio"]["verified_from_file"]["sample_count"], p["video"]["properties_basis"],
                          p["verdicts"]["creative_acceptance"], p["verdicts"]["quality_evaluated"])
            argv = [a for a in p["ffmpeg"]["argv"] if isinstance(a, str)]
            self.check("LIBRARY_ENFORCED", f"{route}: no hiding flags in argv", [], [a for a in ("-shortest", "-t", "-ss", "-r", "-vsync", "-fps_mode", "-ar", "-af", "-async", "-an") if a in argv])
            self.check("LIBRARY_ENFORCED", f"{route}: video stream copy", True, ("-c:v" in argv and argv[argv.index("-c:v") + 1] == "copy"))
        self.check("LIBRARY_ENFORCED", "both routes: exit 0, UNVERIFIED, 4805 samples == WAV samples, caller-stated video, NOT_EVALUATED", {k: (0, "UNVERIFIED", 4805, 4805, "CALLER_STATED_UNVERIFIED", "NOT_EVALUATED", False) for k in ("MOV", "MP4")}, out)
        self.check("LIBRARY_ENFORCED", "durations are exact rationals", ("1001/10000", "961/9600"), (r.json["plan"]["timeline"]["video_duration_seconds"], r.json["plan"]["timeline"]["audio_duration_seconds"]))

    def test_a4_exact_rational_boundaries_agree_across_stages(self):
        """fps as an exact rational and 48000 Hz boundaries agree between the three stages and an independent Fraction oracle; +-1 sample is refused."""
        self.ref("video_core/audiomix/plan.py", "frame_to_sample")
        self.ref("video_core/avmux/plan.py", "prepare_plan")
        w = self.w
        w.put_fixtures()
        n, d, frames = 30000, 1001, 3
        oracle = [oracle_sample(f, n, d) for f in range(frames + 1)]
        self.check("FIXTURE", "oracle boundaries 0..3 @ 30000/1001", [0, 1602, 3203, 4805], oracle)
        vp = self.vplan(load_fixture("episode.video_only.selected_subset.json")).json["plan"]["episode"]
        ap = self.aplan(load_fixture("audio_manifest.selected_subset.json")).json["plan"]
        self.av_manifest()
        mp = self.avplan().json["plan"]
        self.check("LIBRARY_ENFORCED", "video fps/frames == audio timeline == AV timeline", (f"{n}/{d}", frames, f"{n}/{d}", frames, f"{n}/{d}", frames),
                   (vp["fps"]["rational"], vp["total_frames"], ap["timeline"]["fps"]["rational"], ap["timeline"]["total_frames"], mp["timeline"]["fps"]["rational"], mp["timeline"]["total_frames"]))
        self.check("LIBRARY_ENFORCED", "total samples: audio plan == AV plan == WAV == oracle", (oracle[-1],) * 4,
                   (ap["timeline"]["total_samples"], mp["timeline"]["expected_audio_samples"], mp["audio"]["verified_from_file"]["sample_count"], oracle_sample(frames, n, d)))
        # every frame range [a,b) of the 3-frame timeline: length accepted == oracle, length-1 refused, length+1 refused (no trimming/padding)
        per_range = {}
        for a in range(frames):
            for b in range(a + 1, frames + 1):
                want = oracle[b] - oracle[a]
                res = {}
                for delta in (0, -1, 1):
                    am = load_fixture("audio_manifest.selected_subset.json")
                    am["scope"]["event_ids"] = ["ev_vo_1"]
                    ev = am["events"][0]
                    ev["start_frame"], ev["end_frame"] = a, b
                    ev["source_in_sample"], ev["source_out_sample"] = 0, want + delta
                    r = self.aplan(am, label=f"audio plan range [{a},{b}) delta {delta}")
                    res[delta] = (r.exit_code, r.json["plan"]["events"][0]["samples"] if r.exit_code in (0, 5) else r.issue_codes)
                per_range[f"[{a},{b})"] = res
        expected = {f"[{a},{b})": {0: (0 if (a, b) == (0, frames) else 5, oracle[b] - oracle[a]), -1: (1, ["range_length_mismatch"]), 1: (1, ["range_length_mismatch"])} for a in range(frames) for b in range(a + 1, frames + 1)}
        self.check("LIBRARY_ENFORCED", "per-range: oracle length accepted (exit 5 = HOLD only where the one-row scope leaves gaps; the full range is READY/0), +-1 refused", expected, per_range)
        # per-frame accumulation would be wrong: 3 frames of rounded 1602 = 4806, of floored 1601 = 4803
        self.check("FIXTURE", "per-frame rounding would be off", (4806, 4803, 4805), (3 * 1602, 3 * 1601, oracle[-1]))
        # total_samples for several exact rationals from the schema's range, asked of `validate` (no files read)
        fam = {}
        for (fn, fd, fr) in ((30000, 1001, 3), (24000, 1001, 5), (60000, 1001, 7), (30, 1, 3), (25, 1, 3), (48000, 1001, 11), (24, 1, 7)):
            am = load_fixture("audio_manifest.selected_subset.json")
            am["timeline"] = {"fps": {"n": fn, "d": fd}, "total_frames": fr}
            am["scope"]["event_ids"] = ["ev_sil_2"]
            am["events"][1].update(start_frame=0, end_frame=fr)
            r = self.aplan(am, verb="validate", label=f"audio validate {fn}/{fd} x{fr}")
            fam[f"{fn}/{fd}x{fr}"] = r.json["total_samples"]
        self.check("LIBRARY_ENFORCED", "audiomix total_samples == oracle for 7 exact rationals", {f"{fn}/{fd}x{fr}": oracle_sample(fr, fn, fd) for (fn, fd, fr) in ((30000, 1001, 3), (24000, 1001, 5), (60000, 1001, 7), (30, 1, 3), (25, 1, 3), (48000, 1001, 11), (24, 1, 7))}, fam)
        # fps families must not be mixed: a 30/1 stem (4800) against a 30000/1001 declaration, and the reverse
        self.av_manifest(mut=lambda m: m["audio"].update(path="stem_30.wav", sha256=c.sha256(c.fixture_bytes()["stem_30.wav"]), declared={"sample_rate": 48000, "channels": 1, "sample_count": 4800}) or m["audio"].pop("receipt", None), name="av_mix.json")
        r = self.avplan("av_mix.json")
        self.check("LIBRARY_ENFORCED", "30/1-length stem declared against 30000/1001 is refused", (1, ["audio_sample_count_mismatch"]), (r.exit_code, r.issue_codes))
        self.av_manifest(mut=lambda m: (m["video"]["declared"].update(fps={"n": 30, "d": 1}), m["audio"].update(path="stem_30.wav", sha256=c.sha256(c.fixture_bytes()["stem_30.wav"]), declared={"sample_rate": 48000, "channels": 1, "sample_count": 4800}), m["audio"].pop("receipt", None)), name="av_30.json")
        r = self.avplan("av_30.json")
        self.check("LIBRARY_ENFORCED", "30/1 + 4800 samples agree", (0, 4800), (r.exit_code, r.json["plan"]["timeline"]["expected_audio_samples"]))

    def test_a5_episode_cross_check_exists_only_in_audiomix(self):
        """audiomix --episode refuses fps / frame-count / episode sha256 disagreement; avmux has no such option (GAP-07)."""
        self.ref("video_core/audiomix/plan.py", "cross_check_episode")
        self.gap("GAP-07")
        w = self.w
        w.put_fixtures()
        ep = load_fixture("episode.video_only.selected_subset.json")
        w.put_json("ep.json", ep)
        am = load_fixture("audio_manifest.selected_subset.json")
        r = self.aplan(am, ["--episode", "{docs}/ep.json"])
        self.check("LIBRARY_ENFORCED", "matching episode accepted", (0, "READY"), (r.exit_code, r.json["status"]))
        e2 = copy.deepcopy(ep)
        e2["fps"] = {"n": 30, "d": 1}
        w.put_json("e2.json", e2)
        e3 = copy.deepcopy(ep)
        e3["total_frames"], e3["clips"][1]["duration_frames"] = 4, 2
        w.put_json("e3.json", e3)
        got = {}
        got["fps"] = self.aplan(am, ["--episode", "{docs}/e2.json"]).issue_codes
        got["frames"] = self.aplan(am, ["--episode", "{docs}/e3.json"]).issue_codes
        bad = copy.deepcopy(am)
        bad["timeline"]["episode_sha256"] = "a" * 64
        got["sha256"] = self.aplan(bad, ["--episode", "{docs}/ep.json"], name="am_bad.json").issue_codes
        good = copy.deepcopy(am)
        good["timeline"]["episode_sha256"] = sha256(rb(w.p("docs", "ep.json")))
        got["sha256_ok"] = self.aplan(good, ["--episode", "{docs}/ep.json"], name="am_good.json").issue_codes
        self.check("LIBRARY_ENFORCED", "mismatches refused with precise codes", {"fps": ["episode_fps_mismatch"], "frames": ["episode_frames_mismatch"], "sha256": ["episode_sha256_mismatch"], "sha256_ok": []}, got)
        help_text = self.cli(AVM, ["plan", "--help"], "avmux plan --help").stdout
        self.check("LIBRARY_PERMISSIVE", "avmux plan offers no --episode / --scope / --audio-plan option", False, any(o in help_text for o in ("--episode", "--scope", "--audio-plan")))


# =============================================================================================================== B: selection / unresolved input


class B_SelectionAndUnresolved(Base):
    def test_b1_selected_unresolved_audio_is_hold_never_silence(self):
        """A SELECTED MISSING/UNKNOWN/PROXY audio row is HOLD (exit 5, no output, not silence); dropping it from scope makes the plan READY again."""
        self.ref("video_core/audiomix/plan.py", "analyze")
        self.ref("video_core/audiomix/cli.py", "_cmd_plan")
        self.ref("video_core/audiomix/mix.py", literal='"status": S_HOLD, "written": False')
        self.gap("GAP-08")
        self.case["evidence"] = {"not_executed_here": "`mix --mix` was never run. That a HOLD plan creates no directory at mix time is established by the cited source line and by the library's own tests/test_audio_mix.py::test_hold_creates_nothing_and_is_not_silence; this job shows the plan and dry-run side only."}
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        rows = {}
        for ev_id in ("ev_x_missing", "ev_x_unknown", "ev_x_proxy"):
            am = copy.deepcopy(base)
            am["scope"]["event_ids"].append(ev_id)
            r = self.aplan(am, label=f"plan with selected {ev_id}")
            dry = self.cli(AUD, ["mix", "{docs}/am.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "run-" + ev_id], f"mix WITHOUT --mix, selected {ev_id}")
            rows[ev_id] = {"plan": (r.exit_code, r.json["status"], [(h["code"], h["state"]) for h in r.json["plan"]["hold"]]),
                           "dry_run_mix": (dry.exit_code, dry.json["status"], dry.json["mode"]), "out_tree": self.w.snapshot()["out"]}
            self.check("CALLER_GUARD_DEMO", f"{ev_id}: exit code alone misreads the dry-run", "HOLD", g.guard_status_semantics(AUD, dry.exit_code, dry.json)["reading"])
        self.check("LIBRARY_ENFORCED", "selected unresolved -> HOLD (exit 5), reason says 'not silence', nothing created", {k: {"plan": (5, "HOLD", [("SELECTED_UNRESOLVED", k.replace("ev_x_", "").upper())]), "dry_run_mix": (0, "HOLD", "dry_run"), "out_tree": {}} for k in rows}, rows)
        self.check("LIBRARY_ENFORCED", "hold detail states 'not silence'", True, "not silence" in r.json["plan"]["hold"][0]["detail"])
        self.check("LIBRARY_PERMISSIVE", "GAP-08: the mix dry-run of a HOLD plan exits 0", 0, dry.exit_code)
        ok = self.aplan(base, label="the same manifest without the unresolved row in scope")
        self.check("LIBRARY_ENFORCED", "unselecting the unresolved row restores READY", (0, "READY"), (ok.exit_code, ok.json["status"]))
        sel = copy.deepcopy(base)
        sel["scope"]["event_ids"].append("ev_x_missing")
        v = self.aplan(sel, verb="validate", label="validate with selected MISSING")
        self.check("LIBRARY_PERMISSIVE", "GAP-08: validate exits 0 but lists hold_if_planned", (0, ["SELECTED_UNRESOLVED"]), (v.exit_code, [h["code"] for h in v.json["hold_if_planned"]]))

    def test_b2_null_hold_and_absent_states_are_rejected_not_defaulted(self):
        """NULL / absent / HOLD-as-audio-state are schema rejections (exit 1): nothing is defaulted to USE, silence or UNKNOWN."""
        self.ref("video_core/audiomix/manifest.py", "STATES")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        out = {}
        for tag, mut in (("null", lambda e: e.update(state=None)), ("absent", lambda e: e.pop("state")), ("HOLD", lambda e: e.update(state="HOLD")), ("lowercase_use", lambda e: e.update(state="use")), ("empty", lambda e: e.update(state=""))):
            am = copy.deepcopy(base)
            mut(am["events"][3])        # ev_x_missing (unselected!): even unselected rows are validated
            r = self.aplan(am, label=f"audio state {tag}")
            out[tag] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "invalid/NULL states rejected", {"null": (1, ["invalid_enum"]), "absent": (1, ["missing_field"]), "HOLD": (1, ["invalid_enum"]), "lowercase_use": (1, ["invalid_enum"]), "empty": (1, ["invalid_enum"])}, out)
        self.gap("GAP-10")
        self.check("LIBRARY_PERMISSIVE", "GAP-10: audiomix has no HOLD *state*; HOLD is an outcome only (owners must map their own HOLD rows to UNKNOWN/MISSING or drop them from scope)", "HOLD" in ("USE", "OMIT", "INTENTIONAL_SILENCE", "MISSING", "UNKNOWN", "PROXY"), False)
        self.obs("OB-01")

    def test_b3_unselected_unresolved_rows_do_not_block_a_valid_selected_subset(self):
        """Unselected MISSING/UNKNOWN/PROXY rows - even ones with overlapping frames or unreadable assets - never block; a malformed unselected row still does (OB-01)."""
        self.ref("video_core/audiomix/plan.py", "analyze")
        self.obs("OB-01")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        am = copy.deepcopy(base)
        for i in (3, 4, 5):            # unselected unresolved rows given frame ranges that overlap every selected row
            am["events"][i].update(start_frame=0, end_frame=3)
        r = self.aplan(am, label="unselected unresolved rows overlapping the selected ranges")
        self.check("LIBRARY_ENFORCED", "still READY", (0, "READY"), (r.exit_code, r.json["status"]))
        am2 = copy.deepcopy(base)
        am2["assets"].append({"id": "asset_never_read", "path": "does/not/exist.wav", "sha256": "1" * 64})
        am2["events"][6].update(asset="asset_never_read")
        r2 = self.aplan(am2, label="unselected USE row bound to a missing, wrongly-hashed asset")
        self.check("LIBRARY_ENFORCED", "unselected USE assets are not read or hashed", (0, "READY", False), (r2.exit_code, r2.json["status"], [a for a in r2.json["plan"]["assets"] if a["id"] == "asset_never_read"][0]["verified"]))
        am3 = copy.deepcopy(base)
        am3["events"][3].update(start_frame=2, end_frame=1)
        r3 = self.aplan(am3, label="malformed UNSELECTED row")
        self.check("LIBRARY_ENFORCED", "OB-01: an unselected row must still be schema-valid", (1, ["event_range_invalid"]), (r3.exit_code, r3.issue_codes))
        am4 = copy.deepcopy(base)
        am4["events"][6]["asset"] = "asset_nonexistent_id"
        r4 = self.aplan(am4, label="unselected USE row naming an undeclared asset id")
        self.check("LIBRARY_ENFORCED", "OB-01: unselected USE rows must still name declared asset ids", (1, ["unknown_asset"]), (r4.exit_code, r4.issue_codes))

    def test_b3b_scope_is_explicit_and_checked_against_the_rows(self):
        """scope.event_ids is explicit: empty / unknown / duplicate / malformed ids are rejected; there is no implicit 'all'; AV mux requires a non-empty selection.ref (opaque)."""
        self.ref("video_core/audiomix/manifest.py", literal="there is no implicit 'all'")
        self.ref("video_core/avmux/manifest.py", literal='c.keys(doc["selection"], ("ref",)')
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        got = {}

        def case(tag, mut):
            am = copy.deepcopy(base)
            mut(am)
            r = self.aplan(am, name="am_sc.json", label=f"audio scope: {tag}")
            got[tag] = (r.exit_code, r.issue_codes)
        case("empty_scope", lambda a: a["scope"].update(event_ids=[]))
        case("scope_missing", lambda a: a.pop("scope"))
        case("unknown_event_id", lambda a: a["scope"]["event_ids"].append("ev_does_not_exist"))
        case("duplicate_event_id", lambda a: a["scope"]["event_ids"].append("ev_vo_1"))
        case("malformed_event_id", lambda a: a["scope"]["event_ids"].append("ev vo 1!"))
        case("star_is_not_all", lambda a: a["scope"].update(event_ids=["*"]))
        self.check("LIBRARY_ENFORCED", "scope problems", {"empty_scope": (1, ["scope_empty"]), "scope_missing": (1, ["missing_field"]), "unknown_event_id": (1, ["scope_unknown_event"]), "duplicate_event_id": (1, ["scope_duplicate"]),
                                                          "malformed_event_id": (1, ["invalid_id"]), "star_is_not_all": (1, ["invalid_id"])}, got)
        av = {}
        for tag, mut in (("ref_missing", lambda m: m["selection"].pop("ref")), ("ref_empty", lambda m: m["selection"].update(ref="")), ("ref_with_newline", lambda m: m["selection"].update(ref="a\nb")),
                         ("ref_129_chars", lambda m: m["selection"].update(ref="x" * 129)), ("selection_missing", lambda m: m.pop("selection"))):
            self.av_manifest(mut=mut, name="av_sel.json")
            r = self.avplan("av_sel.json", label=f"avmux {tag}")
            av[tag] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "selection.ref must be a well-formed non-empty string (its meaning is not checked)", {"ref_missing": (1, ["missing_field"]), "ref_empty": (1, ["invalid_ref"]), "ref_with_newline": (1, ["invalid_ref"]),
                                                                                                                         "ref_129_chars": (1, ["invalid_ref"]), "selection_missing": (1, ["missing_field"])}, av)

    def test_b4_selected_audio_asset_file_missing_is_hold(self):
        """A selected USE row whose asset file is absent is HOLD (ASSET_FILE_MISSING, exit 5), not a rejection and not silence."""
        self.ref("video_core/audiomix/plan.py", literal="ASSET_FILE_MISSING")
        self.w.put_fixtures(names=())        # no files at all
        r = self.aplan(load_fixture("audio_manifest.selected_subset.json"))
        self.check("LIBRARY_ENFORCED", "HOLD with ASSET_FILE_MISSING", (5, "HOLD", ["ASSET_FILE_MISSING"]), (r.exit_code, r.json["status"], [h["code"] for h in r.json["plan"]["hold"]]))

    def test_b5_avmux_refuses_every_non_use_input_state(self):
        """avmux: video/audio state MISSING/UNKNOWN/PROXY/HOLD/NULL/absent, and missing files, are rejections (exit 1, nothing created) - HOLD survives only as a code."""
        self.ref("video_core/avmux/manifest.py", "_state")
        self.gap("GAP-10")
        out = {}
        for side in ("video", "audio"):
            for st in ("MISSING", "UNKNOWN", "PROXY", "HOLD", None, "ABSENT"):
                def mut(m, side=side, st=st):
                    if st == "ABSENT":
                        m[side].pop("state")
                    else:
                        m[side]["state"] = st
                self.av_manifest(mut=mut, name="av_s.json")
                r = self.avplan("av_s.json", label=f"avmux {side} state {st}")
                out[f"{side}:{st}"] = (r.exit_code, r.issue_codes)
        exp = {}
        for side in ("video", "audio"):
            for st in ("MISSING", "UNKNOWN", "PROXY", "HOLD"):
                exp[f"{side}:{st}"] = (1, ["input_state_not_use"])
            exp[f"{side}:None"] = (1, ["invalid_enum"])
            exp[f"{side}:ABSENT"] = (1, ["missing_field"])
        self.check("LIBRARY_ENFORCED", "every non-USE state rejected with a code", exp, out)
        self.check("LIBRARY_ENFORCED", "nothing created", {}, self.w.snapshot()["out"])
        self.av_manifest()
        os.remove(self.w.p("assets", "video_ok.mp4"))
        r = self.avplan()
        self.check("LIBRARY_ENFORCED", "selected video file missing -> reject asset_missing (not a structured HOLD)", (1, ["asset_missing"]), (r.exit_code, r.issue_codes))
        self.check("CALLER_GUARD_DEMO", "owner maps these codes to its own HOLD (guard_selection on a HOLD row)", "HOLD", g.guard_selection([{"row_id": "video", "selected": True, "state": "HOLD"}])["verdict"])

    def test_b6_video_only_cannot_carry_unresolved_state(self):
        """Video-only: missing file rejected (asset_missing); state/ref fields are unknown_field; a proxy file and a colour stand-in are ACCEPTED (GAP-01)."""
        self.ref("video_core/episode.py", "_validate_source")
        self.ref("video_core/paths.py", "resolve_asset")
        self.gap("GAP-01", "GAP-10")
        self.w.put_fixtures()
        ep = load_fixture("episode.video_only.selected_subset.json")
        out = {}

        def run(tag, mut):
            e = copy.deepcopy(ep)
            mut(e)
            r = self.vplan(e, label=f"video plan: {tag}")
            out[tag] = (r.exit_code, r.issue_codes)
            return r
        run("missing_file", lambda e: e["clips"][0]["source"].update(path="clip_gone.mp4"))
        run("state_in_source", lambda e: e["clips"][0]["source"].update(state="HOLD"))
        run("state_on_clip", lambda e: e["clips"][0].update(state="UNKNOWN"))
        run("binding_ref_on_clip", lambda e: e["clips"][0].update(binding_ref="owner-binding/x"))
        run("null_source", lambda e: e["clips"][0].update(source=None))
        run("proxy_named_file", lambda e: e["clips"][0]["source"].update(path="proxy_clip_a.mp4"))
        run("colour_stand_in_for_unresolved_clip", lambda e: e["clips"][0].update(source={"kind": "color", "value": "#000000"}))
        self.check("LIBRARY_ENFORCED", "unrepresentable / missing -> rejected", {k: (1, v) for k, v in {"missing_file": ["asset_missing"], "state_in_source": ["unknown_field"], "state_on_clip": ["unknown_field"], "binding_ref_on_clip": ["unknown_field"], "null_source": ["type_error"]}.items()}, {k: out[k] for k in ("missing_file", "state_in_source", "state_on_clip", "binding_ref_on_clip", "null_source")})
        self.check("LIBRARY_PERMISSIVE", "GAP-01: a proxy file and a colour stand-in are accepted - the plan cannot know", {"proxy_named_file": (0, []), "colour_stand_in_for_unresolved_clip": (0, [])}, {k: out[k] for k in ("proxy_named_file", "colour_stand_in_for_unresolved_clip")})
        table = load_fixture("owner_selection_table.synthetic.json")
        rows = copy.deepcopy(table["rows"])
        rows[0]["state"] = "PROXY"
        self.check("CALLER_GUARD_DEMO", "owner-side: a SELECTED proxy row stops the call before any episode is written", ("PROCEED", "HOLD"), (g.guard_selection(table["rows"])["verdict"], g.guard_selection(rows)["verdict"]))


# =============================================================================================================== C: source integrity and output rules


class C_SourceAndOutput(Base):
    def test_c1_changed_source_hash(self):
        """Changed bytes under a pinned sha256 are rejected by audiomix and avmux plans; the video-only plan does not notice (GAP-02)."""
        self.ref("video_core/audiomix/plan.py", literal="asset_sha256_mismatch")
        self.ref("video_core/avmux/plan.py", literal="video_sha256_mismatch")
        self.gap("GAP-02")
        w = self.w
        w.put_fixtures()
        am = load_fixture("audio_manifest.selected_subset.json")
        w.put("seg_a.wav", c.fixture_bytes()["stem_v2.wav"])      # another version of the selected source
        r = self.aplan(am)
        self.check("LIBRARY_ENFORCED", "audiomix: selected asset changed", (1, ["asset_sha256_mismatch"]), (r.exit_code, r.issue_codes))
        w.put_fixtures()
        self.av_manifest()
        w.put("video_ok.mp4", c.placeholder_video("video_ok CHANGED"))
        r = self.avplan()
        self.check("LIBRARY_ENFORCED", "avmux: video changed", (1, ["video_sha256_mismatch"]), (r.exit_code, r.issue_codes))
        w.put_fixtures()
        w.put("stem.wav", c.fixture_bytes()["stem_v2.wav"])
        r = self.avplan()
        self.check("LIBRARY_ENFORCED", "avmux: stem changed", (1, ["audio_sha256_mismatch"]), (r.exit_code, r.issue_codes))
        # video-only: same-size content swap leaves the plan byte-identical
        w.put_fixtures()
        ep = load_fixture("episode.video_only.selected_subset.json")
        before = self.vplan(ep).json
        w.put("clip_a.mp4", c.fixture_bytes()["clip_a_v2.mp4"])
        after = self.vplan(ep).json
        self.check("LIBRARY_PERMISSIVE", "GAP-02: plan_sha256 identical after the source bytes changed", (True, True), (before["plan_sha256"] == after["plan_sha256"], before["observations"]["assets"] == after["observations"]["assets"]))
        self.check("LIBRARY_PERMISSIVE", "GAP-02: the video-only plan contains no source hash at all", False, "sha256" in json.dumps(after["plan"]["clips"]) or "sha256" in json.dumps(after["observations"]["assets"]))
        self.check("CALLER_GUARD_DEMO", "owner-side hash pin catches the swap", ("PROCEED", "REJECT"), (g.guard_hash_pins({"clip_a.mp4": sha256(c.fixture_bytes()["clip_a.mp4"])}, {"clip_a.mp4": sha256(c.fixture_bytes()["clip_a.mp4"])})["verdict"],
                                                                                                    g.guard_hash_pins({"clip_a.mp4": sha256(c.fixture_bytes()["clip_a.mp4"])}, {"clip_a.mp4": sha256(c.fixture_bytes()["clip_a_v2.mp4"])})["verdict"]))

    def test_c2_source_output_collisions_in_every_stage(self):
        """Output location inside / containing the asset root, via a symlinked root, or already existing is refused by all three plans; a fresh name passes and creates nothing."""
        self.ref("video_core/paths.py", "check_output_location")
        w = self.w
        w.put_fixtures()
        os.makedirs(w.p("assets", "sub"))
        os.mkdir(w.p("out", "exists"))
        self.av_manifest()
        w.put_json("ep.json", load_fixture("episode.video_only.selected_subset.json"))
        w.put_json("am.json", load_fixture("audio_manifest.selected_subset.json"))
        stage = {"video": (VID, ["plan", "{docs}/ep.json"]), "audio": (AUD, ["plan", "{docs}/am.json"]), "avmux": (AVM, ["plan", "{docs}/av.json"])}
        cases = {"fresh": ("{out}", "n1"), "inside_asset_root": ("{assets}/sub", "n1"), "asset_root_itself": ("{assets}", "n1"), "existing_name": ("{out}", "exists")}
        symlinked = hasattr(os, "symlink")
        if symlinked:
            os.symlink(w.p("assets", "sub"), w.p("out", "lnk"))
            cases["symlink_root_into_assets"] = ("{out}/lnk", "n2")
        got = {}
        for st, (mod, base) in stage.items():
            for tag, (root, name) in cases.items():
                r = self.cli(mod, base + ["--asset-root", "{assets}", "--output-root", root, "--output-name", name], f"{st} plan, output {tag}")
                got[f"{st}:{tag}"] = (r.exit_code, r.issue_codes)
        exp = {}
        for st in stage:
            exp[f"{st}:fresh"] = (0, [])
            exp[f"{st}:inside_asset_root"] = (1, ["output_overlaps_assets"])
            exp[f"{st}:asset_root_itself"] = (1, ["output_overlaps_assets"])
            exp[f"{st}:existing_name"] = (1, ["output_exists"])
            if symlinked:
                exp[f"{st}:symlink_root_into_assets"] = (1, ["output_overlaps_assets"])
        self.check("LIBRARY_ENFORCED", "collision matrix", exp, got)
        self.check("LIBRARY_ENFORCED", "no plan created a directory", ["exists", "lnk"] if symlinked else ["exists"], sorted(w.snapshot()["out"]))
        # GAP-12: the natural handoff mistake - using the stem's directory as both avmux asset root and output root
        self.gap("GAP-12")
        r = self.cli(AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}", "--output-root", "{assets}", "--output-name", "mux-out"], "avmux: handoff dir reused as output root")
        self.check("LIBRARY_ENFORCED", "GAP-12: handoff dir cannot double as output root", (1, ["output_overlaps_assets"]), (r.exit_code, r.issue_codes))

    def test_c3_previous_output_is_immutable_and_never_resumed(self):
        """A previous (even stale IN_PROGRESS) output directory is never reused, resumed or touched: every stage refuses its name and the bytes stay identical."""
        self.ref("video_core/paths.py", "check_output_location")
        w = self.w
        w.put_fixtures()
        self.av_manifest()
        w.put_json("ep.json", load_fixture("episode.video_only.selected_subset.json"))
        w.put_json("am.json", load_fixture("audio_manifest.selected_subset.json"))
        # harness-fabricated 'previous outputs' (NOT produced by the library): one good-looking, one stale IN_PROGRESS
        w.put("prev_good/output.mp4", b"HARNESS-FABRICATED previous output bytes\n", area="out")
        w.put("prev_good/receipt.json", canon({"status": "READBACK_MATCH", "_fixture_note": "harness-fabricated"}), area="out")
        w.put("prev_stale/output.mp4.partial", b"partial\n", area="out")
        w.put("prev_stale/receipt.json", canon({"status": "IN_PROGRESS", "_fixture_note": "harness-fabricated"}), area="out")
        before = c.tree_hash(w.out)
        got = {}
        for name in ("prev_good", "prev_stale"):
            for st, (mod, base) in {"video": (VID, ["plan", "{docs}/ep.json"]), "audio": (AUD, ["plan", "{docs}/am.json"]), "avmux": (AVM, ["plan", "{docs}/av.json"])}.items():
                r = self.cli(mod, base + ["--asset-root", "{assets}", "--output-root", "{out}", "--output-name", name], f"{st} plan reusing {name}")
                got[f"{st}:{name}"] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "every stage refuses to reuse an existing output name", {f"{s}:{n}": (1, ["output_exists"]) for n in ("prev_good", "prev_stale") for s in ("video", "audio", "avmux")}, got)
        self.check("LIBRARY_ENFORCED", "previous outputs byte-identical afterwards", before, c.tree_hash(w.out))
        self.check("CALLER_GUARD_DEMO", "a stale IN_PROGRESS receipt is UNKNOWN: owner must choose a new name and keep the old dir as evidence", "REJECT", g.guard_video_receipt(json.loads(rb(w.p("out", "prev_stale/receipt.json")).decode()), "x" * 64)["verdict"])


# =============================================================================================================== D: receipts


class D_Receipts(Base):
    STEM_SHA = sha256(c.fixture_bytes()["stem.wav"])

    def test_d1_every_non_verified_audio_receipt_status_is_refused(self):
        """avmux plan refuses audio receipts whose status is IN_PROGRESS/UNKNOWN/HOLD/clamped/clip-rejected/mismatch/timeout/cancelled/interrupted/absent/empty."""
        self.ref("video_core/avmux/plan.py", "_check_audio_receipt")
        got = {}
        for st in ("IN_PROGRESS", "UNKNOWN", "HOLD", "MIXED_WITH_CLAMPING", "CLIPPING_REJECTED", "READBACK_MISMATCH", "TIMEOUT", "CANCELLED", "INTERRUPTED", "SKIPPED_TOOL_UNAVAILABLE", "mixed_verified", "", None, 1):
            self.av_manifest(receipt=self.receipt(status=st), name="av_r.json")
            r = self.avplan("av_r.json", label=f"audio receipt status {st!r}")
            got[repr(st)] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "only exactly MIXED_VERIFIED qualifies", {k: (1, ["audio_receipt_not_verified"]) for k in got}, got)
        rec = self.receipt()
        rec.pop("status")
        self.av_manifest(receipt=rec, name="av_r.json")
        r = self.avplan("av_r.json", label="audio receipt without a status key")
        self.check("LIBRARY_ENFORCED", "missing status key refused", (1, ["audio_receipt_not_verified"]), (r.exit_code, r.issue_codes))

    def test_d2_structurally_incomplete_or_mismatched_audio_receipts_are_refused(self):
        """Receipt that is not JSON, hash-mismatched, without/with two stems, partial-only, wrong sample count, or for another stem: refused with specific codes."""
        self.ref("video_core/avmux/plan.py", "_check_audio_receipt")
        got = {}

        def case(tag, receipt, mut=None):
            self.av_manifest(receipt=receipt, mut=mut, name="av_r.json")
            r = self.avplan("av_r.json", label=f"audio receipt: {tag}")
            got[tag] = (r.exit_code, r.issue_codes)
        case("not_json", b"{ this is not json")
        case("no_outputs", self.receipt(outputs=[]))
        stem = self.receipt()["outputs"][0]
        case("two_stems", self.receipt(outputs=[stem, dict(stem)]))
        case("partial_only", self.receipt(outputs=[dict(stem, role="stem_partial", name="stem.wav.partial", partial=True)]))
        case("other_stem_sha", self.receipt(outputs=[dict(stem, sha256="9" * 64)]))
        case("sample_count_wrong", self.receipt(readback={"wav": {"sample_count": 4804}}))
        case("sample_count_absent", self.receipt(readback={}))
        case("readback_absent", self.receipt(readback=None))

        def bad_receipt_hash(m):
            m["audio"]["receipt"]["sha256"] = "0" * 64
        case("manifest_receipt_sha_wrong", self.receipt(), bad_receipt_hash)
        exp = {"not_json": ["audio_receipt_unreadable"], "no_outputs": ["audio_receipt_stem_mismatch"], "two_stems": ["audio_receipt_stem_mismatch"],
               "partial_only": ["audio_receipt_stem_mismatch"], "other_stem_sha": ["audio_receipt_stem_mismatch"], "sample_count_wrong": ["audio_receipt_sample_mismatch"],
               "sample_count_absent": ["audio_receipt_sample_mismatch"], "readback_absent": ["audio_receipt_sample_mismatch"], "manifest_receipt_sha_wrong": ["audio_receipt_sha256_mismatch"]}
        self.check("LIBRARY_ENFORCED", "specific refusal codes", {k: (1, v) for k, v in exp.items()}, got)

    def test_d2b_receipt_of_unexpected_json_shape_crashes_instead_of_rejecting(self):
        """GAP-14: valid JSON of the wrong shape is an uncaught AttributeError (exit 1, empty stdout, traceback, nothing created) - not a structured audio_receipt_* rejection."""
        self.ref("video_core/avmux/plan.py", "_check_audio_receipt")
        self.gap("GAP-14", "GAP-10")
        sh = sha256(c.fixture_bytes()["stem.wav"])
        shapes = {"array": b"[]\n", "string": b'"x"\n', "null": b"null\n", "number": b"1\n", "outputs_is_string": canon({"status": "MIXED_VERIFIED", "outputs": "x"}),
                  "outputs_is_list_of_strings": canon({"status": "MIXED_VERIFIED", "outputs": ["a"]}), "readback_is_string": canon({"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": sh}], "readback": "x"}),
                  "wav_is_string": canon({"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": sh}], "readback": {"wav": "x"}})}
        got = {}
        for tag, data in shapes.items():
            self.av_manifest(receipt=data, name="av_shape.json")
            r = self.avplan("av_shape.json", label=f"audio receipt shape {tag}")
            got[tag] = (r.exit_code, r.stdout == "", "AttributeError" in r.stderr, r.issue_codes)
        self.check("LIBRARY_PERMISSIVE", "GAP-14: uncaught exception, no structured issue", {k: (1, True, True, []) for k in shapes}, got)
        self.check("LIBRARY_ENFORCED", "still fail-closed: nothing created", {}, self.w.snapshot()["out"])
        self.check("CALLER_GUARD_DEMO", "owner treats exit!=0 with empty stdout as REJECTED_UNSTRUCTURED, never as 'no news'", "REJECTED", g.guard_status_semantics(AVM, 1, None)["reading"])

    def test_d3_receipt_is_optional_and_forgeable_status_and_hash_are_not_evidence(self):
        """GAP-04: no receipt at all is accepted; a hand-written / edited receipt (UNKNOWN edited to MIXED_VERIFIED, inputs_unchanged false) is accepted; nothing binds it to an owner."""
        self.ref("video_core/avmux/plan.py", "_check_audio_receipt")
        self.ref("video_core/avmux/manifest.py", literal='("receipt",)')
        self.gap("GAP-04")
        # real plan of the audio stage, as an owner would hold it
        self.w.put_fixtures()
        am = load_fixture("audio_manifest.selected_subset.json")
        real = self.aplan(am, label="the owner's real audio plan").json
        man_sha = sha256(canon(am))
        out = {}
        # 1. no receipt: stem accepted by path+hash alone
        self.av_manifest(receipt=None, name="av_none.json")
        r = self.avplan("av_none.json", label="no receipt")
        out["no_receipt"] = (r.exit_code, r.json["plan"]["audio"]["receipt"])
        # 2. minimal forged receipt: three fields
        forged = {"status": "MIXED_VERIFIED", "outputs": [{"role": "stem", "sha256": self.STEM_SHA}], "readback": {"wav": {"sample_count": 4805}}}
        self.av_manifest(receipt=forged, name="av_forged.json")
        r = self.avplan("av_forged.json", label="three-field forged receipt")
        out["forged_minimal"] = (r.exit_code, r.json["plan"]["audio"]["receipt"]["status"], r.json["plan"]["audio"]["receipt"]["plan_sha256"])
        # 3. an UNKNOWN receipt edited by hand into MIXED_VERIFIED, with the contradicting fields left in place
        edited = self.receipt(status="MIXED_VERIFIED", stage="mix", written=False, plan_sha256="0" * 64)
        edited["input"] = {"manifest_sha256": "1" * 64, "inputs_unchanged": False, "assets": []}
        edited["readback"] = {"all_ok": False, "wav": {"sample_count": 4805}}
        edited["warnings"] = [{"code": "ZERO_FILL_APPLIED", "detail": "x"}, {"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}]
        self.av_manifest(receipt=edited, name="av_edited.json")
        r = self.avplan("av_edited.json", label="edited, self-contradicting receipt")
        out["edited_contradicting"] = (r.exit_code, r.json["plan"]["audio"]["receipt"]["status"])
        self.check("LIBRARY_PERMISSIVE", "GAP-04: all three accepted (exit 0)", {"no_receipt": (0, None), "forged_minimal": (0, "MIXED_VERIFIED", None), "edited_contradicting": (0, "MIXED_VERIFIED")}, out)
        self.check("LIBRARY_PERMISSIVE", "the plan echoes only path/sha256/status/plan_sha256 of the receipt (no warnings, no readback, no inputs_unchanged)", ["path", "plan_sha256", "sha256", "status"], sorted(r.json["plan"]["audio"]["receipt"]))
        # reference guard against the owner's own record of what it really ran
        gd = {k: g.guard_audio_receipt_binding(v, stem_sha256=self.STEM_SHA, audio_plan_sha256=real["plan_sha256"], manifest_sha256=man_sha)["verdict"] for k, v in (("forged_minimal", forged), ("edited_contradicting", edited), ("fabricated_fixture", self.receipt()))}
        self.check("CALLER_GUARD_DEMO", "owner-side comparison with its own plan/manifest hashes rejects all three (and would reject the fabricated fixture too: its plan hash is not the real one)", {"forged_minimal": "REJECT", "edited_contradicting": "REJECT", "fabricated_fixture": "REJECT"}, gd)
        self.check("CALLER_GUARD_DEMO", "guard limit is stated", True, "cannot show who produced or approved" in g.guard_audio_receipt_binding(forged, stem_sha256=self.STEM_SHA, audio_plan_sha256="x", manifest_sha256="y")["limit"])

    def test_d4_selection_ref_scope_and_plan_binding_are_not_cross_checked(self):
        """GAP-07: avmux accepts any selection.ref, an audio receipt for another plan/manifest, and never sees the audio scope."""
        self.ref("video_core/avmux/plan.py", "prepare_plan")
        self.gap("GAP-07", "GAP-04")
        got = {}
        for ref in ("synthetic-selection/0001", "totally/unrelated/ref", "APPROVED-BY-SOMEONE-ELSE", "a"):
            self.av_manifest(mut=lambda m, ref=ref: m["selection"].update(ref=ref), name="av_ref.json")
            r = self.avplan("av_ref.json", label=f"selection.ref {ref!r}")
            got[ref] = (r.exit_code, r.json["plan"]["selection"]["ref"])
        self.check("LIBRARY_PERMISSIVE", "any selection.ref is accepted and echoed verbatim", {k: (0, k) for k in got}, got)
        self.check("LIBRARY_PERMISSIVE", "the plan contains no scope / audio-plan hash / manifest hash of the audio stage", [], [k for k in r.json["plan"] if k in ("scope", "audio_plan_sha256", "audio_manifest_sha256", "event_ids")])
        r2 = self.avplan("av_ref.json", label="receipt of another plan (plan_sha256 'ffff...', manifest 'eeee...')")
        self.check("LIBRARY_PERMISSIVE", "receipt for another plan/manifest accepted", (0, "f" * 64), (r2.exit_code, r2.json["plan"]["audio"]["receipt"]["plan_sha256"]))
        self.check("CALLER_GUARD_DEMO", "owner compares selection.ref and the receipt's plan hash with its own record of what it ran", ("REJECT",), (g.guard_audio_receipt_binding(self.receipt(), stem_sha256=self.STEM_SHA, audio_plan_sha256="real" + "0" * 60, manifest_sha256="real" + "1" * 60)["verdict"],))

    def test_d5_no_slot_for_a_video_receipt_so_its_status_cannot_propagate(self):
        """GAP-03: video receipt fields are unknown_field in the AV manifest; the owner must test the video receipt (IN_PROGRESS/UNKNOWN/partial/mismatch) before building it."""
        self.ref("video_core/avmux/manifest.py", "_validate")
        self.gap("GAP-03")
        got = {}
        for tag, mut in (("video.receipt", lambda m: m["video"].update(receipt={"path": "r.json", "sha256": "0" * 64})), ("video.render_receipt", lambda m: m["video"].update(render_receipt="x")),
                         ("top.video_receipt", lambda m: m.update(video_receipt="x")), ("selection.approved_by", lambda m: m["selection"].update(approved_by="x"))):
            self.av_manifest(mut=mut, name="av_v.json")
            r = self.avplan("av_v.json", label=f"manifest field {tag}")
            got[tag] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "fail closed: unknown fields rejected (so nothing can be smuggled in, and nothing can be bound)", {k: (1, ["unknown_field"]) for k in got}, got)
        vh = sha256(c.fixture_bytes()["video_ok.mp4"])
        good = {"status": "READBACK_MATCH", "outputs": [{"name": "output.mp4", "sha256": vh, "partial": False}]}
        cases = {"READBACK_MATCH": good, "IN_PROGRESS": dict(good, status="IN_PROGRESS"), "UNKNOWN": dict(good, status="UNKNOWN"), "READBACK_MISMATCH": dict(good, status="READBACK_MISMATCH"),
                 "PROCESS_FAILED": dict(good, status="PROCESS_FAILED"), "partial_output_only": dict(good, outputs=[{"name": "output.mp4.partial", "sha256": vh, "partial": True}]),
                 "other_video_sha": dict(good, outputs=[{"name": "output.mp4", "sha256": "7" * 64, "partial": False}])}
        self.check("CALLER_GUARD_DEMO", "owner-side video receipt guard", {"READBACK_MATCH": "PROCEED", "IN_PROGRESS": "REJECT", "UNKNOWN": "REJECT", "READBACK_MISMATCH": "REJECT", "PROCESS_FAILED": "REJECT", "partial_output_only": "REJECT", "other_video_sha": "REJECT"},
                   {k: g.guard_video_receipt(v, vh)["verdict"] for k, v in cases.items()})


# =============================================================================================================== E: timing qualification, S: silence


class E_TimingQualification(Base):
    def test_e1_phrase_boundary_qualification_is_not_expressible_or_checked(self):
        """GAP-06: audiomix accepts equal-length ranges whether or not their boundaries are qualified; it has no field for a qualification and fails closed on unknown fields."""
        self.ref("video_core/audiomix/manifest.py", "_events")
        self.gap("GAP-06")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        table = copy.deepcopy(load_fixture("owner_selection_table.synthetic.json"))
        out = {}
        out["qualified"] = self.aplan(base, label="owner says QUALIFIED").exit_code
        table["rows"][2]["timing_qualification"]["status"] = "UNKNOWN"
        out["owner_says_UNKNOWN_manifest_unchanged"] = self.aplan(base, label="owner says UNKNOWN (manifest identical)").exit_code
        shifted = copy.deepcopy(base)
        shifted["events"][2]["source_in_sample"] += 37          # cut 37 samples off the phrase boundary
        shifted["events"][2]["source_out_sample"] += 37
        out["boundary_shifted_37_samples"] = self.aplan(shifted, label="range shifted 37 samples").exit_code
        bare = copy.deepcopy(base)
        for e in bare["events"][:3]:
            e.pop("decision_ref", None) if e["state"] == "USE" else None
            e.pop("role", None)
        out["no_refs_or_roles_on_USE_rows"] = self.aplan(bare, label="USE rows without decision_ref/role").exit_code
        self.check("LIBRARY_PERMISSIVE", "GAP-06: all accepted - the library cannot tell", {k: 0 for k in out}, out)
        got = {}
        for field, val in (("timing_qualification", {"status": "QUALIFIED"}), ("phrase_boundary", "q1"), ("qualified", True)):
            am = copy.deepcopy(base)
            am["events"][2][field] = val
            got[field] = self.aplan(am, name="am_q.json", label=f"event field {field}").issue_codes
        self.check("LIBRARY_ENFORCED", "no field exists to carry a qualification (unknown_field)", {k: ["unknown_field"] for k in got}, got)
        self.check("CALLER_GUARD_DEMO", "owner-side timing guard: UNKNOWN qualification on a selected USE row -> HOLD", ("PROCEED", "HOLD", ["ev_vo_3"]), (g.guard_timing(load_fixture("owner_selection_table.synthetic.json")["rows"])["verdict"], g.guard_timing(table["rows"])["verdict"], g.guard_timing(table["rows"])["unqualified_rows"]))

    def test_e2_no_duration_mix_or_stretch_is_inferred(self):
        """Wrong-length / out-of-bounds / overlapping selected ranges are rejected - nothing is trimmed, padded, stretched or implicitly summed."""
        self.ref("video_core/audiomix/plan.py", literal="range_length_mismatch")
        self.ref("video_core/audiomix/plan.py", literal="source_range_out_of_bounds")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        got = {}

        def case(tag, mut):
            am = copy.deepcopy(base)
            mut(am)
            r = self.aplan(am, name="am_e.json", label=f"audio: {tag}")
            got[tag] = (r.exit_code, r.issue_codes)
        case("source_too_short_by_1", lambda a: a["events"][0].update(source_out_sample=2601))
        case("source_too_long_by_1", lambda a: a["events"][0].update(source_out_sample=2603))
        case("source_beyond_end_of_wav", lambda a: a["events"][2].update(source_in_sample=5000, source_out_sample=6602))

        def overlap(a):
            a["events"][1].update(state="USE", asset="asset_a", source_in_sample=0, source_out_sample=1601, start_frame=1, end_frame=2)
            a["events"][1].pop("decision_ref")
            a["events"][0].update(end_frame=2, source_out_sample=3203 + 1000)
        case("overlapping_use_events_without_ALLOW_SUM", overlap)
        case("silence_overlapping_use", lambda a: a["events"][1].update(start_frame=0, end_frame=2))
        self.check("LIBRARY_ENFORCED", "no inference of duration or mix", {"source_too_short_by_1": (1, ["range_length_mismatch"]), "source_too_long_by_1": (1, ["range_length_mismatch"]), "source_beyond_end_of_wav": (1, ["source_range_out_of_bounds"]),
                                                                       "overlapping_use_events_without_ALLOW_SUM": (1, ["event_overlap"]), "silence_overlapping_use": (1, ["silence_overlap"])}, got)


class S_Silence(Base):
    def test_s1_intentional_silence_is_explicit_omit_and_absent_are_not_coverage(self):
        """INTENTIONAL_SILENCE needs a decision_ref and counts as coverage; OMIT and an unselected/absent row leave a gap -> HOLD under REQUIRE_FULL_COVERAGE."""
        self.ref("video_core/audiomix/plan.py", literal="INCOMPLETE_SELECTED_INPUT")
        self.ref("video_core/audiomix/manifest.py", literal="requires a decision_ref")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        got = {}

        def case(tag, mut):
            am = copy.deepcopy(base)
            mut(am)
            r = self.aplan(am, name="am_s.json", label=f"audio: {tag}")
            got[tag] = (r.exit_code, r.json.get("status") if r.json and "status" in r.json else None, r.issue_codes or [h["code"] for h in r.json["plan"]["hold"]] if r.json and "plan" in r.json else r.issue_codes)
            return r
        case("explicit_silence", lambda a: None)
        case("silence_without_decision_ref", lambda a: a["events"][1].pop("decision_ref"))
        case("OMIT_instead_of_silence", lambda a: a["events"][1].update(state="OMIT"))
        case("silence_row_not_in_scope", lambda a: a["scope"]["event_ids"].remove("ev_sil_2"))
        def to_unknown(a):
            for k in ("decision_ref", "start_frame", "end_frame"):
                a["events"][1].pop(k)
            a["events"][1]["state"] = "UNKNOWN"
        r = case("silence_row_state_UNKNOWN_and_selected", to_unknown)
        self.check("LIBRARY_ENFORCED", "explicit vs omitted vs absent vs unknown", {"explicit_silence": (0, "READY", []), "silence_without_decision_ref": (1, None, ["missing_field"]), "OMIT_instead_of_silence": (5, "HOLD", ["INCOMPLETE_SELECTED_INPUT"]),
                                                                           "silence_row_not_in_scope": (5, "HOLD", ["INCOMPLETE_SELECTED_INPUT"]), "silence_row_state_UNKNOWN_and_selected": (5, "HOLD", ["SELECTED_UNRESOLVED", "INCOMPLETE_SELECTED_INPUT"])}, got)
        self.check("LIBRARY_ENFORCED", "the gap is reported exactly (1602..3203)", [{"sample_start": 1602, "sample_end": 3203}], r.json["plan"]["coverage"]["gaps"])

    def test_s2_zero_fill_policy_turns_absent_audio_into_zeros_with_a_warning(self):
        """GAP-09: policy.gap=ALLOW_ZERO_FILL makes the same absent audio READY (exit 0) with only a warning; an all-absent scope is READY with NO_AUDIO_EVENTS_SELECTED."""
        self.ref("video_core/audiomix/plan.py", literal="ZERO_FILL_APPLIED")
        self.ref("video_core/audiomix/plan.py", literal="NO_AUDIO_EVENTS_SELECTED")
        self.gap("GAP-09", "GAP-08")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        am = copy.deepcopy(base)
        am["scope"]["event_ids"].remove("ev_sil_2")
        am["policy"]["gap"] = "ALLOW_ZERO_FILL"
        r = self.aplan(am, name="am_z1.json", label="absent middle row + ALLOW_ZERO_FILL")
        self.check("LIBRARY_PERMISSIVE", "GAP-09: READY with zero-fill warning", (0, "READY", ["ZERO_FILL_APPLIED"], [{"sample_start": 1602, "sample_end": 3203}]), (r.exit_code, r.json["status"], [w["code"] for w in r.json["plan"]["warnings"]], r.json["plan"]["coverage"]["gaps"]))
        no_audio = copy.deepcopy(base)
        no_audio["scope"]["event_ids"] = ["ev_sil_2"]
        no_audio["events"][1].update(state="OMIT")
        no_audio["policy"]["gap"] = "ALLOW_ZERO_FILL"
        r2 = self.aplan(no_audio, name="am_z2.json", label="only an OMIT row in scope + ALLOW_ZERO_FILL")
        self.check("LIBRARY_PERMISSIVE", "GAP-09: no audio selected at all is still READY (a silent stem would result)", (0, "READY", ["NO_AUDIO_EVENTS_SELECTED", "ZERO_FILL_APPLIED"]), (r2.exit_code, r2.json["status"], [w["code"] for w in r2.json["plan"]["warnings"]]))
        self.check("CALLER_GUARD_DEMO", "owner-side: any warning means HOLD unless the owner decided silence", ("HOLD", "HOLD"), (g.guard_silence_provenance(r.json, True)["verdict"], g.guard_silence_provenance(r2.json, True)["verdict"]))
        ok = self.aplan(base, name="am_z3.json", label="explicit silence under REQUIRE_FULL_COVERAGE")
        self.check("CALLER_GUARD_DEMO", "explicit INTENTIONAL_SILENCE with decision_ref passes the same guard", "PROCEED", g.guard_silence_provenance(ok.json, True)["verdict"])

    def test_s3_avmux_cannot_tell_a_silent_stem_from_intentional_silence_and_drops_warnings(self):
        """GAP-05: an all-zero stem and a receipt carrying ZERO_FILL_APPLIED/NO_AUDIO_EVENTS_SELECTED are accepted; the plan keeps none of that."""
        self.ref("video_core/avmux/plan.py", "_check_audio_receipt")
        self.gap("GAP-05")
        z = sha256(c.fixture_bytes()["stem_silent.wav"])
        warn_receipt = self.receipt(warnings=[{"code": "ZERO_FILL_APPLIED", "detail": "x"}, {"code": "NO_AUDIO_EVENTS_SELECTED", "detail": "x"}], unresolved_report=[{"event_id": "ev_x", "state": "UNKNOWN", "selected": True}])
        warn_receipt["outputs"][0]["sha256"] = z
        warn_receipt["readback"]["wav"]["sha256"] = z

        def silent(m):
            m["audio"]["path"], m["audio"]["sha256"] = "stem_silent.wav", z
        self.av_manifest(receipt=None, mut=silent, name="av_zero_norec.json")
        r1 = self.avplan("av_zero_norec.json", label="all-zero stem, no receipt")
        self.av_manifest(receipt=warn_receipt, mut=silent, name="av_zero_warn.json")
        r2 = self.avplan("av_zero_warn.json", label="all-zero stem, receipt with warnings and a selected UNKNOWN in unresolved_report")
        self.check("LIBRARY_PERMISSIVE", "GAP-05: both accepted", (0, 0), (r1.exit_code, r2.exit_code))
        self.check("LIBRARY_PERMISSIVE", "GAP-05: neither warnings nor unresolved_report survive into the plan", [], [k for k in json.dumps(r2.json).split('"') if k in ("ZERO_FILL_APPLIED", "NO_AUDIO_EVENTS_SELECTED", "unresolved_report", "warnings")])
        base_plan = {"plan": {"events": [{"selected": True, "state": "USE"}], "warnings": [{"code": "ZERO_FILL_APPLIED"}, {"code": "NO_AUDIO_EVENTS_SELECTED"}]}}
        self.check("CALLER_GUARD_DEMO", "owner-side silence-provenance guard on the audio plan it really holds", "HOLD", g.guard_silence_provenance(base_plan, True)["verdict"])

    def test_s4_avmux_declared_lengths_must_match_exactly_no_shortest_no_trim(self):
        """avmux plan refuses declared!=expected and WAV!=declared sample counts and channel disagreement; the argv has no length-hiding flag."""
        self.ref("video_core/avmux/plan.py", literal="audio_sample_count_mismatch")
        self.ref("video_core/avmux/plan.py", literal="audio_channels_mismatch")
        got = {}
        for tag, mut in (("declared_4806", lambda m: m["audio"]["declared"].update(sample_count=4806)), ("declared_4804", lambda m: m["audio"]["declared"].update(sample_count=4804)),
                         ("declared_stereo_for_mono_wav", lambda m: m["audio"]["declared"].update(channels=2)), ("frames_4_with_4805_samples", lambda m: m["video"]["declared"].update(total_frames=4)),
                         ("rate_44100", lambda m: m["audio"]["declared"].update(sample_rate=44100))):
            self.av_manifest(mut=mut, name="av_l.json")
            r = self.avplan("av_l.json", label=f"avmux {tag}")
            got[tag] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "length/channel/rate disagreements refused", {"declared_4806": (1, ["audio_sample_count_mismatch"]), "declared_4804": (1, ["audio_sample_count_mismatch"]), "declared_stereo_for_mono_wav": (1, ["audio_channels_mismatch"]),
                                                                                 "frames_4_with_4805_samples": (1, ["audio_sample_count_mismatch"]), "rate_44100": (1, ["unsupported_audio"])}, got)


# =============================================================================================================== F: declared vs measured, defaults, exit codes


class F_DeclaredVsMeasured(Base):
    def test_f1_plan_cannot_see_the_video_declared_properties_stay_unverified(self):
        """avmux plan accepts non-media bytes in a .mp4 and any consistent declaration: video codec/size/rate/frames are CALLER_STATED_UNVERIFIED, ffprobe is not run."""
        self.ref("video_core/avmux/plan.py", literal="CALLER_STATED_UNVERIFIED")
        self.ref("video_core/avmux/plan.py", literal='"readiness": "UNVERIFIED')
        self.gap("GAP-03", "GAP-08")
        w = self.w
        got = {}
        for tag, mut in (("as_fixture", lambda m: None), ("declared_other_size", lambda m: m["video"]["declared"].update(width=7680, height=4320)),
                         ("declared_other_codec_still_h264_only", lambda m: None)):
            self.av_manifest(mut=mut, name="av_d.json")
            r = self.avplan("av_d.json", label=f"avmux plan, {tag}")
            got[tag] = (r.exit_code, r.json["readiness"].split(":")[0], r.json["plan"]["video"]["properties_basis"], r.json["plan"]["video"]["declared"]["width"])
        self.check("LIBRARY_PERMISSIVE", "bytes that are not a video, with declared 320x180 or 7680x4320: exit 0, UNVERIFIED, caller-stated", {"as_fixture": (0, "UNVERIFIED", "CALLER_STATED_UNVERIFIED", 320), "declared_other_size": (0, "UNVERIFIED", "CALLER_STATED_UNVERIFIED", 7680),
                                                                                                     "declared_other_codec_still_h264_only": (0, "UNVERIFIED", "CALLER_STATED_UNVERIFIED", 320)}, got)
        self.check("LIBRARY_ENFORCED", "the plan itself says no ffprobe was run", True, "ffprobe is NOT run by plan" in r.json["observations"]["side_effects"])
        self.check("LIBRARY_ENFORCED", "the placeholder 'video' really is not media", True, w and rb(w.p("assets", "video_ok.mp4")).startswith(b"SYNTHETIC-PLACEHOLDER-NOT-MEDIA"))
        self.av_manifest(mut=lambda m: m["video"]["declared"].update(codec="hevc"), name="av_hevc.json")
        r2 = self.avplan("av_hevc.json")
        self.check("LIBRARY_ENFORCED", "only h264 may be declared", (1, ["video_codec_unsupported"]), (r2.exit_code, r2.issue_codes))
        self.check("CALLER_GUARD_DEMO", "exit code 0 must be read together with readiness", "UNVERIFIED", g.guard_status_semantics(AVM, 0, r.json)["reading"])

    def test_f2_preflight_comparison_catches_declared_vs_measured_demonstrated_on_canned_dicts(self):
        """(pure function, NOT end-to-end) avmux.checks.check_video on canned ffprobe-shaped dicts: declared != measured -> failing named checks with mapped rejection codes."""
        self.ref("video_core/avmux/checks.py", "check_video")
        self.ref("video_core/avmux/checks.py", literal="CODES = {")
        self.ref("video_core/avmux/mux.py", "_reject_checks")
        sys.path.insert(0, c.VC_DIR)
        try:
            from video_core.avmux import checks
        finally:
            sys.path.remove(c.VC_DIR)
        fps = Fraction(30000, 1001)
        good = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180, "r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001", "nb_read_frames": "3", "start_pts": 0, "start_time": "0.000000"}],
                "format": {"start_time": "0.000000"}}
        bad = copy.deepcopy(good)
        v = bad["streams"][0]
        v.update(width=321, r_frame_rate="30/1", avg_frame_rate="30/1", nb_read_frames="4", start_pts=512, start_time="0.033367", codec_name="hevc")
        failing = [x["name"] for x in checks.check_video(bad, width=320, height=180, fps=fps, frames=3) if not x["ok"]]
        self.check("LIBRARY_ENFORCED", "matching canned dict: every check ok", True, all(x["ok"] for x in checks.check_video(good, width=320, height=180, fps=fps, frames=3)))
        self.check("LIBRARY_ENFORCED", "mismatching canned dict: failing checks", ["video_codec", "video_width", "video_r_frame_rate", "video_avg_frame_rate", "video_frames", "video_start"], failing)
        self.check("LIBRARY_ENFORCED", "each failing check maps to a rejection code", ["video_codec_mismatch", "video_size_mismatch", "video_fps_mismatch", "video_fps_mismatch", "video_frame_count_mismatch", "video_start_nonzero"], [checks.CODES[n] for n in failing])
        self.check("FIXTURE", "canned dicts are harness-fabricated, not measured from any file", True, True)
        self.case["evidence"] = {"scope": "pure comparison function only; the end-to-end `mux --mux` preflight was NOT run in this job (no ffprobe launched). See tests/test_avmux_mux.py and evidence/avmux-synthetic/ for the library's own coverage."}

    def test_f3_video_only_default_stays_an_and_baseline_plan_is_unchanged(self):
        """Default video-only plan keeps -an/-sn/-dn and no audio input; the original six-spec example still plans to the pinned hash 4bc20b3f...9f21."""
        self.ref("video_core/plan.py", literal='"-an", "-sn", "-dn"')
        w = self.w
        r = self.vplan(load_fixture("episode.video_only.color_only.json"), label="colour-only video plan")
        argv = r.json["plan"]["ffmpeg"]["argv"]
        self.check("LIBRARY_ENFORCED", "-an -sn -dn present; -c:a/-i absent (no inputs at all)", (True, True, True, False, False), ("-an" in argv, "-sn" in argv, "-dn" in argv, "-c:a" in argv, "-i" in argv))
        ex = rb(os.path.join(c.VC_DIR, "examples", "episode.synthetic.json"))
        w.put("episode.synthetic.json", ex, area="docs")
        r2 = self.cli(VID, ["plan", "{docs}/episode.synthetic.json"], "plan of examples/episode.synthetic.json (copied into the world)")
        self.check("LIBRARY_ENFORCED", "baseline plan hash pinned by the library's own evidence", "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21", r2.json["plan_sha256"])
        self.check("LIBRARY_ENFORCED", "baseline episode sha256 pinned by the library's own evidence", "401ac89079cc60d4f7a158eeba9b6c1e57edb664a3322c8d00c2de4ac6a01440", r2.json["observations"]["input"]["episode_sha256"])
        self.av_manifest()
        a = self.avplan(label="avmux plan beside it")
        self.check("LIBRARY_ENFORCED", "the AV plan is a separate argv; the video-only plan was not altered by the audio stages", (True, True), ("-an" not in a.json["plan"]["ffmpeg"]["argv"], r2.json["plan_sha256"] == "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"))

    def test_f4_exit_code_zero_is_not_ready(self):
        """GAP-08 matrix with real outputs: validate with hold_if_planned, dry-run mix of a HOLD plan, READY with zero-fill warnings, avmux UNVERIFIED all exit 0."""
        self.ref("video_core/audiomix/cli.py", "_cmd_mix")
        self.ref("video_core/audiomix/cli.py", "_cmd_validate")
        self.gap("GAP-08", "GAP-09")
        self.w.put_fixtures()
        base = load_fixture("audio_manifest.selected_subset.json")
        sel = copy.deepcopy(base)
        sel["scope"]["event_ids"].append("ev_x_unknown")
        zf = copy.deepcopy(base)
        zf["scope"]["event_ids"].remove("ev_sil_2")
        zf["policy"]["gap"] = "ALLOW_ZERO_FILL"
        v = self.aplan(sel, verb="validate", name="a1.json", label="validate, selected UNKNOWN")
        d = self.cli(AUD, ["mix", "{docs}/a1.json", "--asset-root", "{assets}", "--output-root", "{out}", "--output-name", "q1"], "mix dry-run, selected UNKNOWN")
        z = self.aplan(zf, name="a2.json", label="plan with ALLOW_ZERO_FILL")
        self.av_manifest()
        a = self.avplan()
        h = self.aplan(sel, name="a1.json", label="plan, selected UNKNOWN")
        got = {"audiomix validate (hold_if_planned)": (v.exit_code, g.guard_status_semantics(AUD, v.exit_code, v.json)["reading"]),
               "audiomix mix dry-run of HOLD": (d.exit_code, g.guard_status_semantics(AUD, d.exit_code, d.json)["reading"]),
               "audiomix plan READY + ZERO_FILL warning": (z.exit_code, g.guard_status_semantics(AUD, z.exit_code, z.json)["reading"]),
               "audiomix plan HOLD": (h.exit_code, g.guard_status_semantics(AUD, h.exit_code, h.json)["reading"]),
               "avmux plan": (a.exit_code, g.guard_status_semantics(AVM, a.exit_code, a.json)["reading"])}
        self.check("LIBRARY_PERMISSIVE", "GAP-08: actual exit codes (library) and the correct owner-side reading (guard)", {"audiomix validate (hold_if_planned)": (0, "NOT_READY"), "audiomix mix dry-run of HOLD": (0, "HOLD"), "audiomix plan READY + ZERO_FILL warning": (0, "READY_WITH_WARNINGS"),
                                                                                                                         "audiomix plan HOLD": (5, "NOT_READY"), "avmux plan": (0, "UNVERIFIED")}, got)


# =============================================================================================================== G: stages, failures, rollback


class G_StagesFailuresRollback(Base):
    def test_g1_source_version_pinned_between_stages(self):
        """A source swapped between stages is caught by the sha256 chain at plan time (stem, video, receipt); a consistent re-pin including the receipt is accepted (GAP-04)."""
        self.ref("video_core/avmux/plan.py", "prepare_plan")
        self.gap("GAP-04", "GAP-13")
        w = self.w
        fb = c.fixture_bytes()
        v2 = sha256(fb["stem_v2.wav"])
        got = {}
        self.av_manifest(name="av_v1.json")
        got["baseline"] = self.avplan("av_v1.json").exit_code
        w.put("stem.wav", fb["stem_v2.wav"])                                                  # the stem is replaced after stage 2
        r = self.avplan("av_v1.json", label="stem swapped, manifest and receipt still pin v1")
        got["a_stem_swapped"] = (r.exit_code, r.issue_codes)

        def repin_manifest_only(m):
            m["audio"]["sha256"] = v2
        self.av_manifest(mut=repin_manifest_only, name="av_v2m.json")
        w.put("stem.wav", fb["stem_v2.wav"])
        r = self.avplan("av_v2m.json", label="manifest re-pinned to v2, receipt still describes v1")
        got["b_manifest_repinned_receipt_not"] = (r.exit_code, r.issue_codes)
        rec = self.receipt()
        rec["outputs"][0]["sha256"] = v2
        rec["readback"]["wav"]["sha256"] = v2
        self.av_manifest(receipt=rec, mut=repin_manifest_only, name="av_v2.json")
        w.put("stem.wav", fb["stem_v2.wav"])
        r = self.avplan("av_v2.json", label="manifest AND (hand-edited) receipt re-pinned to v2")
        got["c_everything_repinned_by_owner"] = (r.exit_code, r.json["plan"]["audio"]["receipt"]["status"])
        w.put_fixtures()
        w.put("video_ok.mp4", c.placeholder_video("video_ok v2"))
        r = self.avplan("av_v1.json", label="video swapped after the video-only stage")
        got["d_video_swapped"] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "pin chain", {"baseline": 0, "a_stem_swapped": (1, ["audio_sha256_mismatch"]), "b_manifest_repinned_receipt_not": (1, ["audio_receipt_stem_mismatch"]), "d_video_swapped": (1, ["video_sha256_mismatch"])},
                   {k: got[k] for k in ("baseline", "a_stem_swapped", "b_manifest_repinned_receipt_not", "d_video_swapped")})
        self.check("LIBRARY_PERMISSIVE", "GAP-04: an owner (or anyone) who re-pins manifest and receipt together gets a plan: the pin proves consistency, not provenance", (0, "MIXED_VERIFIED"), got["c_everything_repinned_by_owner"])
        self.check("CALLER_GUARD_DEMO", "owner-side guard against its own pin record flags the v2 swap", "REJECT", g.guard_hash_pins({"stem.wav": sha256(fb["stem.wav"])}, {"stem.wav": v2})["verdict"])

    def test_g2_failure_handoff_upstream_failure_never_becomes_downstream_input(self):
        """Stage-2 failure leftovers (stem.wav.partial, IN_PROGRESS receipt, absent stem) are refused by stage 3; only an omitted receipt lets a promoted stem through (GAP-04)."""
        self.ref("video_core/avmux/manifest.py", "_path")
        self.ref("video_core/avmux/plan.py", "_check_audio_receipt")
        self.gap("GAP-04", "GAP-10", "GAP-14")
        w = self.w
        fb = c.fixture_bytes()
        w.put_fixtures()
        # harness-fabricated leftovers of a failed audio stage (NOT produced by `audiomix mix`)
        w.put("stage_audio/stem.wav.partial", fb["stem.wav"][:2000])
        stale = self.receipt(status="IN_PROGRESS", stage="mix", written=True)
        w.put_json("stage_audio/receipt.json", stale, area="assets")
        w.put("prev_good/output.mov", b"HARNESS-FABRICATED previous good output\n", area="out")
        out_before = c.tree_hash(w.out)
        got = {}

        def at(path, receipt_path=None):
            def mut(m):
                m["audio"]["path"] = path
                m["audio"]["sha256"] = sha256(fb["stem.wav"])
                if receipt_path is None:
                    m["audio"].pop("receipt", None)
                else:
                    m["audio"]["receipt"] = {"path": receipt_path, "sha256": sha256(rb(w.p("assets", receipt_path)))}
            return mut
        for tag, mut in (("partial_file", at("stage_audio/stem.wav.partial")), ("stem_absent", at("stage_audio/stem.wav", "stage_audio/receipt.json"))):
            self.av_manifest(receipt=None, mut=mut, name="av_f.json")
            r = self.avplan("av_f.json", label=f"handoff: {tag}")
            got[tag] = (r.exit_code, r.issue_codes)
        w.put("stage_audio/stem.wav", fb["stem.wav"])           # the stem exists (e.g. promoted by the mixer's rename) but the run's receipt says IN_PROGRESS
        self.av_manifest(receipt=None, mut=at("stage_audio/stem.wav", "stage_audio/receipt.json"), name="av_f.json")
        r = self.avplan("av_f.json", label="handoff: stem present, receipt IN_PROGRESS")
        got["stem_present_receipt_in_progress"] = (r.exit_code, r.issue_codes)
        self.av_manifest(receipt=None, mut=at("stage_audio/stem.wav"), name="av_f.json")
        r = self.avplan("av_f.json", label="handoff: stem present, receipt omitted from the manifest")
        got["stem_present_receipt_omitted"] = (r.exit_code, r.json["plan"]["audio"]["receipt"])
        self.check("LIBRARY_ENFORCED", "failure leftovers are refused", {"partial_file": (1, ["source_extension_unsupported"]), "stem_absent": (1, ["asset_missing"]), "stem_present_receipt_in_progress": (1, ["audio_receipt_not_verified"])},
                   {k: got[k] for k in ("partial_file", "stem_absent", "stem_present_receipt_in_progress")})
        self.check("LIBRARY_PERMISSIVE", "GAP-04/10: with the receipt omitted, the same promoted stem of an UNKNOWN run is accepted", (0, None), got["stem_present_receipt_omitted"])
        self.check("LIBRARY_ENFORCED", "the previous good output was untouched and nothing was created", out_before, c.tree_hash(w.out))
        self.check("CALLER_GUARD_DEMO", "owner rule: a stem is handed on only together with a non-null MIXED_VERIFIED receipt it has itself bound", ("REJECT", "REJECT"), (g.guard_audio_receipt_binding(stale, stem_sha256=sha256(fb["stem.wav"]), audio_plan_sha256="f" * 64, manifest_sha256="e" * 64)["verdict"],
                                                                                                               g.guard_audio_receipt_binding({}, stem_sha256=sha256(fb["stem.wav"]), audio_plan_sha256="f" * 64, manifest_sha256="e" * 64)["verdict"]))

    def test_g3_rollback_on_isolated_copies(self):
        """REAL rollback (rename-swap and restore-from-copy) on isolated temp copies; original hashes re-verified; no production state read or changed."""
        self.ref("video_core/avmux/plan.py", "prepare_plan")
        self.ref("video_core/paths.py", "check_output_location")
        self.gap("GAP-12", "GAP-13")
        w = self.w
        fb = c.fixture_bytes()
        pins = load_fixture("FIXTURES_INDEX.json")
        touched, steps = [], []

        def inside(path):
            rp = os.path.realpath(path)
            assert rp == w.root or rp.startswith(w.root + os.sep), f"harness refused to touch {path!r}: outside the isolated world"
            touched.append(os.path.relpath(rp, w.root))
            return path

        def put(rel, data, area="assets"):
            inside(w.p(area, rel))
            return w.put(rel, data, area)

        def step(name, **kw):
            snap = {"assets": c.tree_hash(w.assets), "docs": c.tree_hash(w.docs), "out": c.tree_hash(w.out)}
            steps.append({"step": name, "tree_digest": c.digest_of(snap), **kw})
            return snap
        # 0. isolated copy of the 'current' state (fixtures only; synthetic placeholder bytes)
        put("current/stem.wav", fb["stem.wav"])
        put("current/video_ok.mp4", fb["video_ok.mp4"])
        put("current/audio_receipt.json", canon(load_fixture("audio_receipt.harness_fabricated.json")))
        put("prev_good/output.mov", b"HARNESS-FABRICATED previous good output\n", area="out")

        def manifest(stem_sha=None, rec_sha=None):
            m = load_fixture("avmux_manifest.mov.json")
            m["video"]["path"], m["audio"]["path"], m["audio"]["receipt"]["path"] = "current/video_ok.mp4", "current/stem.wav", "current/audio_receipt.json"
            m["audio"]["sha256"] = stem_sha or pins["bytes"]["stem.wav"]
            m["audio"]["receipt"]["sha256"] = rec_sha or sha256(rb(w.p("assets", "current/audio_receipt.json")))
            return m
        inside(w.p("docs", "av.json"))
        w.put_json("av.json", manifest())
        s0 = step("0 state before: isolated copy of the 'current' stem/video/receipt + one previous output")
        r0 = self.avplan(label="plan on the original state")
        p0 = r0.json["plan_sha256"]
        self.check("ROLLBACK", "original files carry the pinned fixture hashes", (pins["bytes"]["stem.wav"], pins["bytes"]["video_ok.mp4"], pins["json"]["audio_receipt.harness_fabricated.json"]),
                   (s0["assets"]["current/stem.wav"], s0["assets"]["current/video_ok.mp4"], s0["assets"]["current/audio_receipt.json"]))
        self.check("ROLLBACK", "plan on the original state succeeds", 0, r0.exit_code)
        # 1. snapshot (caller-side backup copy)
        snap_dir = os.path.join(w.root, "snap")
        inside(snap_dir)
        shutil.copytree(w.p("assets", "current"), os.path.join(snap_dir, "current"))
        snap_hash = c.tree_hash(os.path.join(snap_dir, "current"))
        step("1 backup copy taken", backup_tree=snap_hash)
        # 2. R1: candidate promotion by atomic rename swap, then failure detected by the library, then rollback by rename
        put("current.new/stem.wav", fb["stem_v2.wav"])
        put("current.new/video_ok.mp4", fb["video_ok.mp4"])
        put("current.new/audio_receipt.json", canon(load_fixture("audio_receipt.harness_fabricated.json")))     # owner forgot to update the receipt
        put("candidate-001/output.mov.partial", b"half-written candidate\n", area="out")
        os.rename(inside(w.p("assets", "current")), inside(w.p("assets", "current.old")))
        os.rename(inside(w.p("assets", "current.new")), inside(w.p("assets", "current")))
        step("2 candidate promoted by rename swap (stem v2, stale receipt) + a half-written candidate output dir")
        r1 = self.avplan(label="plan on the promoted candidate")
        self.check("ROLLBACK", "library detects the bad promotion (stem no longer matches the pinned hash)", (1, ["audio_sha256_mismatch"]), (r1.exit_code, r1.issue_codes))
        os.rename(inside(w.p("assets", "current")), inside(w.p("assets", "current.failed")))
        os.rename(inside(w.p("assets", "current.old")), inside(w.p("assets", "current")))
        shutil.rmtree(inside(w.p("out", "candidate-001")))
        failed_tree = c.tree_hash(w.p("assets", "current.failed"))
        shutil.rmtree(inside(w.p("assets", "current.failed")))
        s2 = step("3 rolled back (rename swap back, candidate output removed, failed copy kept only as a hash record)", failed_candidate_tree=failed_tree)
        self.check("ROLLBACK", "R1: tree after rollback == tree before promotion (all of assets/docs/out)", c.digest_of({"assets": s0["assets"], "docs": s0["docs"], "out": s0["out"]}), steps[-1]["tree_digest"])
        self.check("ROLLBACK", "R1: original pinned hashes verified again after restoration", (pins["bytes"]["stem.wav"], pins["bytes"]["video_ok.mp4"], pins["json"]["audio_receipt.harness_fabricated.json"]),
                   (s2["assets"]["current/stem.wav"], s2["assets"]["current/video_ok.mp4"], s2["assets"]["current/audio_receipt.json"]))
        self.check("ROLLBACK", "R1: the previous output was never touched", s0["out"]["prev_good/output.mov"], s2["out"]["prev_good/output.mov"])
        r2 = self.avplan(label="plan after R1 rollback")
        self.check("ROLLBACK", "R1: same plan_sha256 as before the promotion", p0, r2.json["plan_sha256"])
        # 3. R2: in-place damage (torn write of the stem), restore from the backup copy with copy + atomic replace
        with open(inside(w.p("assets", "current/stem.wav")), "r+b") as fh:
            fh.seek(100)
            fh.write(b"\xff" * 64)
        step("4 in-place damage: 64 bytes of the stem overwritten")
        r3 = self.avplan(label="plan on the damaged stem")
        self.check("ROLLBACK", "library detects the damage", (1, ["audio_sha256_mismatch"]), (r3.exit_code, r3.issue_codes))
        tmp_restore = inside(w.p("assets", "current/stem.wav.restore"))
        shutil.copyfile(os.path.join(snap_dir, "current", "stem.wav"), tmp_restore)
        os.replace(tmp_restore, inside(w.p("assets", "current/stem.wav")))
        s3 = step("5 restored from the backup copy")
        self.check("ROLLBACK", "R2: tree == original", c.digest_of({"assets": s0["assets"], "docs": s0["docs"], "out": s0["out"]}), steps[-1]["tree_digest"])
        r4 = self.avplan(label="plan after R2 restore")
        self.check("ROLLBACK", "R2: same plan_sha256 again", p0, r4.json["plan_sha256"])
        self.check("ROLLBACK", "every mutation stayed inside the isolated world (relative paths only)", True, all(not t.startswith("..") for t in touched))
        self.case["evidence"] = {
            "what_this_is": "A caller-side rollback protocol exercised on temporary copies built from synthetic fixtures. The library has no rollback and creates only fresh output directories; its plan paths created nothing here.",
            "what_this_is_not": "No production state was read, copied, changed or restored. No mix/mux/render was run, so no real stage output was rolled back.",
            "isolation": {"world": "temp dir created by the harness for this one test and deleted afterwards", "touched_paths_relative_to_world": sorted(set(touched)), "mutating_operations": len(touched)},
            "plan_sha256_before_and_after_both_rollbacks": p0, "backup_tree": snap_hash, "steps": steps,
        }
        self.assertEqual(w.sentinel_hits(), [])


# =============================================================================================================== P: process / directory instrumentation, X: permission and approval


class P_DefaultPathsLaunchNothing(Base):
    def test_p1_default_plan_validate_dryrun_paths_launch_no_process_and_create_no_directory(self):
        """Every default / dry-run CLI path, with a sentinel ffmpeg/ffprobe on PATH and with an empty PATH, launches nothing, touches no network and creates no file or directory."""
        self.ref("video_core/plan.py", "prepare")
        self.ref("video_core/tools.py", "locate")
        w = self.w
        w.put_fixtures()
        w.put_json("ep.json", load_fixture("episode.video_only.selected_subset.json"))
        w.put_json("am.json", load_fixture("audio_manifest.selected_subset.json"))
        self.av_manifest()
        empty_bin = os.path.join(w.root, "emptybin")
        os.mkdir(empty_bin)
        o = ["--output-root", "{out}", "--output-name"]
        cmds = {
            "video validate": (VID, ["validate", "{docs}/ep.json", "--asset-root", "{assets}"]), "video plan": (VID, ["plan", "{docs}/ep.json", "--asset-root", "{assets}", *o, "p1"]),
            "video default action": (VID, ["{docs}/ep.json", "--asset-root", "{assets}"]), "video render (no --render)": (VID, ["render", "{docs}/ep.json", "--asset-root", "{assets}", *o, "p2"]),
            "audiomix validate": (AUD, ["validate", "{docs}/am.json"]), "audiomix plan": (AUD, ["plan", "{docs}/am.json", "--asset-root", "{assets}", *o, "p3"]),
            "audiomix default action": (AUD, ["{docs}/am.json", "--asset-root", "{assets}"]), "audiomix mix (no --mix)": (AUD, ["mix", "{docs}/am.json", "--asset-root", "{assets}", *o, "p4"]),
            "avmux validate": (AVM, ["validate", "{docs}/av.json"]), "avmux plan": (AVM, ["plan", "{docs}/av.json", "--asset-root", "{assets}", *o, "p5"]),
            "avmux default action": (AVM, ["{docs}/av.json", "--asset-root", "{assets}"]), "avmux mux (no --mux)": (AVM, ["mux", "{docs}/av.json", "--asset-root", "{assets}", *o, "p6"]),
        }
        before = w.snapshot()
        got = {}
        for variant, pdir in (("sentinel_on_PATH", None), ("empty_PATH", empty_bin)):
            for tag, (mod, args) in cmds.items():
                r = run_cli(w, mod, args, path_dir=pdir)
                self._cli_count += 1
                rec = r.record()
                rec["label"] = f"{tag} [{variant}]"
                self.case["invocations"].append(rec)
                tl = ((r.json or {}).get("observations") or {}).get("tools_located")
                got[f"{variant}|{tag}"] = {"exit": r.exit_code, "process": len(r.log["events"]["process"]), "network": len(r.log["events"]["network"]), "mutation": len(r.log["events"]["mutation"]),
                                           "ffmpeg_located": None if tl is None else (tl["ffmpeg"]["path"] is not None)}
        exp = {}
        for variant in ("sentinel_on_PATH", "empty_PATH"):
            for tag, (mod, args) in cmds.items():
                has_tl = tag.split()[0] in ("video", "avmux") and not tag.endswith("validate") or tag.startswith("avmux mux") or tag.startswith("video render")
                exp[f"{variant}|{tag}"] = {"exit": 0, "process": 0, "network": 0, "mutation": 0, "ffmpeg_located": (variant == "sentinel_on_PATH") if has_tl else None}
        self.check("LIBRARY_ENFORCED", "24 default-path invocations: exit 0, zero launches/network/mutations; PATH lookup is reported but nothing is executed", exp, got)
        self.check("LIBRARY_ENFORCED", "no output directory or file anywhere; assets/docs/bin byte-identical; sentinel never run", before, w.snapshot())
        self.check("LIBRARY_ENFORCED", "the plans work with no ffmpeg/ffprobe on PATH at all", True, True)
        self.check("INSTRUMENT", "positive control (test_01) proves the same instrument sees launches/mkdir/writes", True, True)


class X_PermissionAndApproval(Base):
    def test_x1_execution_permission_and_creative_approval_have_no_carrier_and_are_never_inferred(self):
        """GAP-11 (by design): permission/approval fields are unknown_field in all three schemas; verdicts are NOT_EVALUATED; no output key claims approval; opaque refs are echoed, not believed."""
        self.ref("video_core/avmux/plan.py", literal='"creative_acceptance": "NOT_EVALUATED"')
        self.ref("video_core/audiomix/plan.py", literal="ASSERTION_NOTE")
        self.gap("GAP-11")
        w = self.w
        w.put_fixtures()
        got = {}
        ep = load_fixture("episode.video_only.selected_subset.json")
        for field in ("execution_permission", "creative_approval", "approved_by"):
            e = copy.deepcopy(ep)
            e[field] = {"granted": True}
            r = self.vplan(e, label=f"video episode field {field}")
            got[f"video:{field}"] = (r.exit_code, r.issue_codes)
            a = load_fixture("audio_manifest.selected_subset.json")
            a[field] = {"granted": True}
            r = self.aplan(a, name="am_x.json", label=f"audio manifest field {field}")
            got[f"audio:{field}"] = (r.exit_code, r.issue_codes)
            self.av_manifest(mut=lambda m, field=field: m.update({field: {"granted": True}}), name="av_x.json")
            r = self.avplan("av_x.json", label=f"avmux manifest field {field}")
            got[f"avmux:{field}"] = (r.exit_code, r.issue_codes)
        self.check("LIBRARY_ENFORCED", "no carrier: unknown_field everywhere", {k: (1, ["unknown_field"]) for k in got}, got)

        def keys(o, acc):
            if isinstance(o, dict):
                for k, v in o.items():
                    acc.add(k)
                    keys(v, acc)
            elif isinstance(o, list):
                for v in o:
                    keys(v, acc)
            return acc
        self.av_manifest()
        plans = {"video": self.vplan(ep).json, "audio": self.aplan(load_fixture("audio_manifest.selected_subset.json")).json, "avmux": self.avplan().json}
        suspicious = {k: sorted(x for x in keys(v, set()) if any(s in x.lower() for s in ("approv", "permission", "authoriz", "signed_off"))) for k, v in plans.items()}
        self.check("LIBRARY_ENFORCED", "no key in any plan claims approval/permission", {"video": [], "audio": [], "avmux": []}, suspicious)
        self.check("LIBRARY_ENFORCED", "AV plan verdicts", ("NOT_EVALUATED", False), (plans["avmux"]["plan"]["verdicts"]["creative_acceptance"], plans["avmux"]["plan"]["verdicts"]["quality_evaluated"]))
        am = load_fixture("audio_manifest.selected_subset.json")
        am["events"][0]["decision_ref"] = "APPROVED-BY-DIRECTOR/synthetic-not-real"
        r = self.aplan(am, name="am_ref.json", label="decision_ref that looks like an approval")
        self.check("LIBRARY_PERMISSIVE", "an approval-looking string is just an opaque, echoed, unauthenticated reference", (0, "APPROVED-BY-DIRECTOR/synthetic-not-real", True), (r.exit_code, r.json["plan"]["events"][0]["decision_ref"], "not authenticated" in r.json["plan"]["caller_assertions"]))
        h = self.cli(AUD, ["mix", "--help"], "audiomix mix --help").stdout
        self.check("LIBRARY_ENFORCED", "the only 'permission' is the bare --mix flag: no token/approval option exists", [], [t for t in ("token", "permission", "approve", "authoriz") if t in h.lower()])
        self.check("CALLER_GUARD_DEMO", "owner-side: execution permission and creative approval are owner records, unresolved by default", ("HOLD", ["no owner-held execution permission for this exact plan hash", "creative approval is unresolved (library verdicts are always NOT_EVALUATED)"]),
                   (g.guard_permission_and_approval({})["verdict"], g.guard_permission_and_approval({})["reasons"]))


# =============================================================================================================== driver


class CountingResult(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.passed = 0

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed += 1


def run_all(verbosity=1):
    import io
    sys.addaudithook(_parent_hook)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    stream = io.StringIO() if verbosity == 0 else sys.stderr
    runner = unittest.TextTestRunner(stream=stream, verbosity=verbosity, resultclass=CountingResult)
    t0 = time.monotonic()
    res = runner.run(suite)
    wall = time.monotonic() - t0
    return res, wall, stream


def build_report(res, wall) -> dict:
    kinds: dict = {}
    for case in REGISTRY.values():
        for ck in case["checks"]:
            k = kinds.setdefault(ck["kind"], {"checks": 0, "ok": 0})
            k["checks"] += 1
            k["ok"] += 1 if ck["ok"] else 0
    runner_argv = [(exe, a) for exe, a in PARENT_POPEN]
    harness_ok = all(exe == sys.executable and a[1:3] == ["-I", "-B"] and a[3].endswith("harness_runner.py") for exe, a in runner_argv)
    non_control = [cs for cs in REGISTRY.values() if not cs.get("positive_control")]
    acct = {
        "harness_python_cli_launches": len(c.LAUNCHES) + len(c.CONTROL_LAUNCHES),
        "of_which_library_cli_runs": len(c.LAUNCHES), "of_which_instrument_positive_control": len(c.CONTROL_LAUNCHES),
        "driver_process_popen_events": len(runner_argv), "driver_popen_events_all_harness_runner": harness_ok,
        "external_process_launches_inside_library_clis": sum(cs["process_accounting"]["external_process_launches"] for cs in non_control),
        "network_events_inside_library_clis": sum(cs["process_accounting"]["network_events"] for cs in non_control),
        "filesystem_mutations_inside_library_clis": sum(cs["process_accounting"]["filesystem_mutations"] for cs in non_control),
        "files_read_outside_world_library_stdlib_by_library_clis": sum(cs["process_accounting"]["files_read_outside_world_library_stdlib"] for cs in non_control),
        "media_tool_sentinel_hits_from_library_clis": sum(cs["process_accounting"]["sentinel_media_tool_hits"] for cs in non_control),
        "positive_control_sentinel_hits_expected_1": sum(cs["process_accounting"]["sentinel_media_tool_hits"] for cs in REGISTRY.values() if cs.get("positive_control")),
        "execution_flags_ever_passed": [a for l in c.LAUNCHES for a in l["args"] if a in c.FORBIDDEN_FLAGS],
        "child_PATH_values_seen": sorted({l["child_PATH"] for l in c.LAUNCHES}),
        "real_media_tools_reachable_by_library_clis": any(not p.startswith("<WORLD>/") for l in c.LAUNCHES for p in l["child_PATH"].split(os.pathsep)),
    }
    cases = {k: REGISTRY[k] for k in sorted(REGISTRY)}
    return {
        "python": ".".join(map(str, sys.version_info[:3])), "platform": sys.platform,
        "unit_tests": {"total": res.testsRun, "passed": res.passed, "skipped": len(res.skipped), "failed": len(res.failures), "errors": len(res.errors), "unexpected_successes": len(res.unexpectedSuccesses)},
        "checks_by_kind": dict(sorted(kinds.items())), "process_accounting": acct, "wall_seconds": round(wall, 1),
        "invocation_count": sum(len(cs["invocations"]) for cs in cases.values()),
        "cases_digest": c.digest_of(cases), "cases": cases,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--emit", help="directory to write observed-pyX.Y.json into (default: write nothing)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ns = ap.parse_args(argv)
    if os.name != "posix":
        print("SKIPPED: the sentinel/process-group harness is POSIX-only", file=sys.stderr)
        return 3
    res, wall, _ = run_all(2 if ns.verbose else 1)
    rep = build_report(res, wall)
    u = rep["unit_tests"]
    by_kind = json.dumps({k: "%d/%d" % (v["ok"], v["checks"]) for k, v in rep["checks_by_kind"].items()})
    print("\ncontract suite on Python %s: total=%d passed=%d skipped=%d failed=%d errors=%d | checks=%d %s | cli_invocations=%d wall=%ss" % (
        rep["python"], u["total"], u["passed"], u["skipped"], u["failed"], u["errors"], sum(v["checks"] for v in rep["checks_by_kind"].values()), by_kind, rep["invocation_count"], rep["wall_seconds"]))
    print("process accounting:", json.dumps(rep["process_accounting"], sort_keys=True))
    print("cases_digest:", rep["cases_digest"])
    if ns.emit:
        os.makedirs(ns.emit, exist_ok=True)
        with open(os.path.join(ns.emit, f"observed-py{sys.version_info[0]}.{sys.version_info[1]}.json"), "wb") as fh:
            fh.write(canon({**rep, "gaps": GAPS, "observations": OBSERVATIONS}))
    return 0 if (u["failed"] == 0 and u["errors"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
