#!/usr/bin/env python3
"""Reproducible, bounded Japanese-caption conformance + display-evidence driver.

It does NOT modify video_core and never changes default behaviour. It reads production code (validator, SRT export, cmap
check, drawtext plan) and compares it with independent code in this directory (cmapx.py, oracle.py, kinsoku.py).

Subcommands (all offline; the only process launches are `git`, `perl` and, for `render-once`, ffmpeg/ffprobe):
  build-corpus [--check]     write/verify corpus.json, corpus.episode.json, corpus.srt, render_case.*, MANIFEST.sha256
  static-report [--check]    write/verify reports/*.json (conformance, fonts, glyph raster, timing, kinsoku)
  uax14-probe [--check]      Line_Break classes of the declared kinsoku set from the LOCAL Perl 5 Unicode DB (optional)
  baseline-manifest [--check] sha256 of every pre-existing tools/video-core file at the base commit
  integrity                  history/source-integrity proof against the base commit (JSON to stdout)
  render-once ...            THE one bounded real render (see render_once.py); refuses to run twice
  analyze-render ...         re-measure an existing render without rendering
Default is read-only unless --out/--write is stated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))           # tools/video-core
REPO = os.path.abspath(os.path.join(ROOT, "..", ".."))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cmapx  # noqa: E402
import corpus_def as C  # noqa: E402
import diagnostics  # noqa: E402
import kinsoku  # noqa: E402
import oracle  # noqa: E402
from video_core import Limits, export_srt, load_episode, prepare  # noqa: E402
from video_core.errors import ValidationFailed  # noqa: E402
from video_core.fontcheck import FontError, missing_codepoints  # noqa: E402
from video_core.timebase import frame_to_ms, ms_to_srt  # noqa: E402

BASE_COMMIT = "b4ca4a3b031351a631c0fbe1eb149935f4db19ba"
BASELINE_PLAN_SHA256 = "4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21"
EVIDENCE_REL = "tools/video-core/evidence/japanese-captions-20261105"
REPORTS = os.path.join(HERE, "reports")

FONTS = [  # id, label, path (all pre-installed; nothing is downloaded or installed)
    ("ipag", "IPAGothic", "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"),
    ("ipagp", "IPAPGothic", "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf"),
    ("unifont_jp", "Unifont-JP", "/usr/share/fonts/opentype/unifont/unifont_jp.otf"),
    ("unifont_upper", "Unifont Upper", "/usr/share/fonts/opentype/unifont/unifont_upper.otf"),
    ("inter_regular", "Inter Regular (no kana expected)", "/usr/share/fonts/opentype/inter/Inter-Regular.otf"),
    ("dejavu_sans", "DejaVu Sans (no kana expected)", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("noto_color_emoji", "Noto Color Emoji", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
    ("wqy_zenhei_ttc", "WenQuanYi Zen Hei (.ttc)", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
]
PRIMARY_FONT = "ipag"
IPAG_SHA256 = "503af4a8b84d1079b8e2e358dc7f7a7fb8cb7a1f212f35eaef6782dbfc75a55e"

RANGES = [("ascii_printable", 0x20, 0x7E), ("hiragana", 0x3040, 0x309F), ("katakana", 0x30A0, 0x30FF), ("cjk_symbols_punct", 0x3000, 0x303F),
          ("halfwidth_fullwidth_forms", 0xFF00, 0xFFEF), ("general_punct", 0x2000, 0x206F), ("variation_selectors", 0xFE00, 0xFE0F),
          ("cjk_unified_ideographs", 0x4E00, 0x9FFF), ("cjk_ext_b", 0x20000, 0x2A6DF), ("ivs_supplement", 0xE0100, 0xE01EF), ("emoticons", 0x1F600, 0x1F64F)]


# ----------------------------------------------------------------------------- small utilities ---------------------

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dumps(obj) -> bytes:
    """Deterministic, ASCII-only JSON (every non-ASCII character is a \\u escape so code points are visible in diffs)."""
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def cps(text: str) -> list:
    return [f"U+{ord(c):04X}" for c in text]


def read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def write_or_check(path: str, data: bytes, check: bool) -> dict:
    if check:
        have = read(path) if os.path.exists(path) else None
        return {"path": os.path.relpath(path, HERE), "ok": have == data, "sha256": sha256(data), "committed_sha256": None if have is None else sha256(have)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return {"path": os.path.relpath(path, HERE), "ok": True, "sha256": sha256(data), "bytes": len(data)}


def episode_doc(fps, canvas, total, captions):
    return {"version": 1, "fps": {"n": fps[0], "d": fps[1]}, "canvas": {"width": canvas[0], "height": canvas[1]}, "total_frames": total,
            "clips": [{"id": "black", "start_frame": 0, "duration_frames": total, "source": {"kind": "color", "value": "#000000"}}],
            "captions": [{"id": cid, "start_frame": s, "end_frame": e, "text": t} for cid, s, e, t in captions]}


def episode_bytes(doc) -> bytes:
    return dumps(doc)


def single_case_episode(text: str) -> bytes:
    return episode_bytes(episode_doc((30, 1), (640, 360), 30, [("a", 0, 10, text)]))


def validator_outcome(text: str) -> str:
    try:
        load_episode(single_case_episode(text))
        return "accept"
    except ValidationFailed as exc:
        return "reject:" + exc.issues[0].code


# ----------------------------------------------------------------------------- corpus build ------------------------

def accepted_cases():
    return [c for c in C.CASES if c[3][0] == "accept"]


def corpus_layout():
    """[(case_id, start, end)], total_frames — adjacent windows, a 3-frame gap after every 5th, last caption ends AT total."""
    pos, out = 0, []
    for k, case in enumerate(accepted_cases()):
        out.append((case[0], pos, pos + C.CAPTION_WINDOW))
        pos += C.CAPTION_WINDOW
        if (k + 1) % C.GAP_EVERY == 0 and k + 1 < len(accepted_cases()):
            pos += C.GAP_FRAMES
    return out, pos


def build_corpus_files() -> dict:
    layout, total = corpus_layout()
    text_of = {c[0]: c[2] for c in C.CASES}
    caps = [(cid.replace("_", "-") if False else cid, s, e, text_of[cid]) for cid, s, e in layout]
    corpus_ep = episode_bytes(episode_doc(C.CORPUS_FPS, C.CORPUS_CANVAS, total, caps))
    ep = load_episode(corpus_ep)
    srt = export_srt(ep)
    rend_caps = [(cid, s, e, t) for cid, s, e, t in C.RENDER_CAPTIONS]
    render_ep = episode_bytes(episode_doc(C.RENDER_FPS, C.RENDER_CANVAS, C.RENDER_TOTAL, rend_caps))
    render_srt = export_srt(load_episode(render_ep))
    corpus_json = dumps({
        "schema": "jpcap.corpus/1",
        "fps": {"n": C.CORPUS_FPS[0], "d": C.CORPUS_FPS[1]}, "canvas": list(C.CORPUS_CANVAS), "caption_window_frames": C.CAPTION_WINDOW,
        "total_frames": total, "layout": [{"id": cid, "start_frame": s, "end_frame": e} for cid, s, e in layout],
        "note": "synthetic strings only; text is stored as JSON \\u escapes so every code point is visible; rejected cases are not in the episode/SRT",
        "cases": [{"id": cid, "group": g, "text": t, "codepoints": cps(t) if len(t) <= 80 else cps(t[:8]) + ["..."], "chars": len(t),
                   "utf8_bytes": len(t.encode("utf-8", "surrogatepass")), "text_sha256": sha256(t.encode("utf-8", "surrogatepass")),
                   "declared_validator": ":".join(exp) if exp[0] == "reject" else "accept", "classification": cls, "note": note}
                  for cid, g, t, exp, cls, note in C.CASES],
        "render_case": {"fps": {"n": C.RENDER_FPS[0], "d": C.RENDER_FPS[1]}, "canvas": list(C.RENDER_CANVAS), "total_frames": C.RENDER_TOTAL,
                        "font_size": C.RENDER_FONT_SIZE,
                        "captions": [{"id": cid, "start_frame": s, "end_frame": e, "text": t, "codepoints": cps(t)} for cid, s, e, t in C.RENDER_CAPTIONS]},
    })
    files = {"corpus.json": corpus_json, "corpus.episode.json": corpus_ep, "corpus.srt": srt,
             "render_case.episode.json": render_ep, "render_case.srt": render_srt}
    manifest = "".join(f"{sha256(b)}  {n}  ({len(b)} bytes)\n" for n, b in sorted(files.items())).encode("ascii")
    files["MANIFEST.sha256"] = manifest
    return files


# ----------------------------------------------------------------------------- font handling ---------------------

def load_fonts() -> dict:
    out = {}
    for fid, label, path in FONTS:
        if not os.path.isfile(path):
            out[fid] = {"id": fid, "label": label, "path": path, "present": False}
            continue
        data = read(path)
        rec = {"id": fid, "label": label, "path": path, "present": True, "bytes": len(data), "sha256": sha256(data), "data": data}
        try:
            rec["font"] = cmapx.read_font(data, path)
        except cmapx.SfntError as exc:
            rec["font"] = None
            rec["independent_reader_error"] = str(exc)
        out[fid] = rec
    return out


def production_missing(data: bytes, text_values: list):
    try:
        return {"missing": missing_codepoints(data, text_values), "error": None}
    except FontError as exc:
        return {"missing": None, "error": str(exc)}


def independent_missing(font, text_values: list):
    needed = sorted({ord(ch) for t in text_values for ch in t if ch != "\n"})
    return [cp for cp in needed if not font.gid(cp)]


def sample_codepoints() -> list:
    out = list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + list(range(0x2000, 0x2070)) + list(range(0x3000, 0x3100)) + list(range(0xFE00, 0xFE10))
    out += list(range(0xFF00, 0xFFF0)) + list(range(0x4E00, 0xA000, 97)) + [0x1F600, 0x1F64F, 0x20BB7, 0x29E3D, 0xE0100, 0xE0101, 0x20000, 0x2A6DF]
    return sorted(set(out) - set(range(0xD800, 0xE000)))


def font_report(fonts: dict) -> dict:
    rows = []
    sample = sample_codepoints()
    sample_text = "".join(map(chr, sample))
    for fid, label, path in FONTS:
        rec = fonts[fid]
        row = {"id": fid, "label": label, "path": path, "present": rec["present"]}
        if not rec["present"]:
            rows.append(row)
            continue
        row.update({"bytes": rec["bytes"], "sha256": rec["sha256"]})
        f = rec.get("font")
        prod_sample = production_missing(rec["data"], [sample_text])
        row["production_reader"] = {"usable": prod_sample["error"] is None, "error": prod_sample["error"]}
        if f is None:
            row["independent_reader"] = {"usable": False, "error": rec.get("independent_reader_error")}
            rows.append(row)
            continue
        row["names"] = f.names
        row["units_per_em"] = f.units_per_em
        row["glyphs"] = f.num_glyphs
        row["outline_tag"] = "OTTO(CFF)" if f.tag == b"OTTO" else "TrueType"
        row["cmap_subtables"] = cmapx.describe_subtables(f)
        row["has_format14_variation_sequences"] = any(s.fmt == 14 for s in f.subtables)
        cov = {}
        for name, a, b in RANGES:
            cov[name] = {"range": f"U+{a:04X}-U+{b:04X}", "mapped": sum(1 for cp in range(a, b + 1) if f.gid(cp)), "of": b - a + 1}
        row["range_coverage"] = cov
        indep_sample = [cp for cp in sample if not f.gid(cp)]
        if prod_sample["error"] is None:
            pm = set(prod_sample["missing"])
            row["production_vs_independent_sample"] = {"code_points_compared": len(sample), "production_missing": len(pm), "independent_missing": len(indep_sample),
                                                       "disagreements": sorted(f"U+{c:04X}" for c in pm ^ set(indep_sample))}
        adv = {}
        for cp in (0x3042, 0x3099, 0x309A, 0xFF8A, 0xFF9E, 0x0041, 0x3000):
            g = f.gid(cp)
            adv[f"U+{cp:04X}"] = None if not g else {"gid": g, "advance_units": f.advance_units(g), "advance_em": round(f.advance_units(g) / f.units_per_em, 4)}
        row["advances"] = adv
        per_case = {}
        union = "".join(sorted({ch for c in accepted_cases() for ch in c[2] if ch != "\n"}))
        pm_union = production_missing(rec["data"], [union])        # per-code-point predicate: one call answers every case
        pm_set = set(pm_union["missing"] or [])
        for cid, _g, text, _exp, _cls, _n in accepted_cases():
            needed = sorted({ord(ch) for ch in text if ch != "\n"})
            prod = None if pm_union["error"] else [cp for cp in needed if cp in pm_set]
            im = independent_missing(f, [text])
            per_case[cid] = {"production_missing": None if prod is None else [f"U+{c:04X}" for c in prod],
                             "independent_missing": [f"U+{c:04X}" for c in im],
                             "agree": prod is not None and prod == im, "production_error": pm_union["error"]}
        row["corpus_coverage"] = per_case
        row["corpus_fully_covered_cases"] = sorted(k for k, v in per_case.items() if v["production_missing"] == [])
        row["corpus_not_covered_cases"] = sorted(k for k, v in per_case.items() if v["production_missing"] != [])
        rows.append(row)
    return {"schema": "jpcap.font_report/1",
            "note": "cmap coverage only. A mapped code point is not proof that the drawn glyph is correct; see glyph_raster.json and the author's visual observations.",
            "fonts": rows}


def ttc_font_row(fonts):
    rec = fonts["wqy_zenhei_ttc"]
    if not rec["present"]:
        return {"present": False}
    r = production_missing(rec["data"], ["あ"])
    return {"present": True, "production_error": r["error"], "independent_error": rec.get("independent_reader_error")}


# ----------------------------------------------------------------------------- conformance ------------------------

def fake_font_bytes() -> bytes:
    """A cmap-only sfnt covering every code point U+0000..U+10FFFF (format 12). NOT a usable font: lets `prepare` run the full
    burn-in planning path for any accepted text without depending on an installed font."""
    from tests.fontbuilder import make_font
    return make_font(fmt12=[(0, 0x10FFFF, 1)])


def conformance_report(fonts: dict) -> dict:
    layout, total = corpus_layout()
    window = {cid: (s, e) for cid, s, e in layout}
    corpus_ep_bytes = read(os.path.join(HERE, "corpus.episode.json")) if os.path.exists(os.path.join(HERE, "corpus.episode.json")) else build_corpus_files()["corpus.episode.json"]
    ep = load_episode(corpus_ep_bytes)
    srt = export_srt(ep)
    cues = oracle.parse_srt_strict(srt)
    by_id = {cap.id: cap for cap in ep.captions}
    order = sorted(range(len(ep.captions)), key=lambda i: (ep.captions[i].start_frame, ep.captions[i].end_frame, i))
    cue_text = {ep.captions[idx].id: cues[n]["text"] for n, idx in enumerate(order)}
    ffont = fonts["ipag"]
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "assets"))
        with open(os.path.join(td, "assets", "font.ttf"), "wb") as fh:
            fh.write(fake_font_bytes())
        rows = []
        for cid, group, text, exp, cls, note in C.CASES:
            declared = "accept" if exp[0] == "accept" else ":".join(exp)
            observed = validator_outcome(text)
            row = {"id": cid, "group": group, "classification": cls, "declared_validator": declared, "observed_validator": observed,
                   "validator_as_declared": observed == declared, "codepoints": cps(text) if len(text) <= 80 else "(long)", "text_sha256": sha256(text.encode("utf-8", "surrogatepass")),
                   "nfc_stable": unicodedata.is_normalized("NFC", text) if "\ud800" not in text else None,
                   "nfkc_stable": unicodedata.is_normalized("NFKC", text) if "\ud800" not in text else None, "note": note}
            if observed == "accept":
                src = text.encode("utf-8")
                got = cue_text[cid]
                row["srt"] = {"text_equal_as_str": got == text, "text_bytes_equal": got.encode("utf-8") == src,
                              "bytes_in_srt_stream": src in srt, "cue_start_frame": window[cid][0], "cue_end_frame": window[cid][1]}
                # full burn-in planning with the permissive fake font: text -> file bytes, never into argv/filtergraph
                eb = single_case_episode(text)
                plan = prepare(eb, asset_root=os.path.join(td, "assets"), burn_in_font="font.ttf", font_size=20)
                files = plan.caption_files
                # text-independence of the planned command: same timing + a different caption text => identical argv template
                ref = prepare(single_case_episode("x"), asset_root=os.path.join(td, "assets"), burn_in_font="font.ttf", font_size=20)
                argv_json = json.dumps(plan.canonical["ffmpeg"]["argv"], ensure_ascii=False)
                same_argv = plan.canonical["ffmpeg"]["argv"] == ref.canonical["ffmpeg"]["argv"]
                row["burn_in_plan_with_permissive_fake_font"] = {
                    "text_file_bytes_equal": list(files.values()) == [src], "text_file_names": sorted(files),
                    "argv_independent_of_caption_text": same_argv, "argv_is_ascii_only": all(ord(ch) < 128 for ch in argv_json),
                    "filtergraph_charset_guard": "passed (prepare did not raise)",
                    "expansion_none": "expansion=none" in " ".join(a for a in plan.canonical["ffmpeg"]["argv"] if isinstance(a, str))}
                f = ffont.get("font") if ffont["present"] else None
                if f is not None:
                    pm = production_missing(ffont["data"], [text])
                    row["ipag_burn_in"] = "covered" if pm["missing"] == [] else "missing:" + ",".join(f"U+{c:04X}" for c in pm["missing"])
                row["kinsoku"] = kinsoku.check_lines(text)
                row["diagnostics"] = [d["code"] for d in diagnostics.diagnose(text, font=f, font_size=20, canvas_width=C.CORPUS_CANVAS[0])]
            rows.append(row)
    ok_decl = sum(1 for r in rows if r["validator_as_declared"])
    return {"schema": "jpcap.conformance/1", "cases": len(rows), "validator_as_declared": ok_decl,
            "srt_exact_for_all_accepted": all(r["srt"]["text_bytes_equal"] and r["srt"]["text_equal_as_str"] for r in rows if "srt" in r),
            "corpus_srt_sha256": sha256(srt), "corpus_episode_sha256": sha256(corpus_ep_bytes), "total_frames": total,
            "unchanged_defaults": "no production file was imported with modification; counts come from the unmodified video_core", "rows": rows}


# ----------------------------------------------------------------------------- glyph raster -----------------------

def glyph_raster_report(fonts: dict) -> dict:
    import analysis
    if not analysis.have_pil():
        return {"schema": "jpcap.glyph_raster/1", "status": "SKIPPED", "reason": "Pillow not importable"}
    import PIL
    needed = sorted({ord(ch) for c in accepted_cases() for ch in c[2] if ch != "\n"})
    from PIL import features
    out = {"schema": "jpcap.glyph_raster/1", "status": "RAN", "pillow": PIL.__version__, "freetype": features.version("freetype2"),
           "claim": "machine detection of blank / .notdef-identical drawings only; 'ink' does not mean the correct glyph", "fonts": {}}
    for fid in ("ipag", "unifont_jp"):
        rec = fonts[fid]
        if not rec["present"] or rec.get("font") is None:
            out["fonts"][fid] = {"status": "SKIPPED", "reason": "font absent"}
            continue
        f = rec["font"]
        rep = analysis.glyph_raster_report(rec["path"], needed, 32, f.gid)
        per = rep["per_codepoint"]
        rep["covered_but_notdef_identical"] = sorted(k for k, v in per.items() if v["same_as_notdef"])
        rep["covered_but_blank_not_space_like"] = sorted(k for k, v in per.items()
                                                         if v["blank"] and unicodedata.category(chr(int(k[2:], 16))) not in ("Zs", "Cf", "Cc", "Zl", "Zp", "Mn"))
        rep["format_or_space_chars_drawn_with_ink"] = sorted(k for k, v in per.items() if not v["blank"] and unicodedata.category(chr(int(k[2:], 16))) in ("Cf", "Zl", "Zp"))
        rep["sha256_font"] = rec["sha256"]
        out["fonts"][fid] = rep
    return out


# ----------------------------------------------------------------------------- timing ----------------------------

TIMING_FPS = [(1, 1), (7, 1), (15, 1), (24, 1), (25, 1), (30, 1), (60, 1), (120, 1), (24000, 1001), (30000, 1001), (48000, 1001), (60000, 1001), (120000, 1001), (89, 3)]


def _plan_enable(captions, fps, total, td):
    eb = episode_bytes(episode_doc(fps, (64, 36), total, captions))
    plan = prepare(eb, asset_root=os.path.join(td, "assets"), burn_in_font="font.ttf", font_size=12)
    return plan, eb


def timing_report() -> dict:
    fr = {}
    # 1) every frame boundary: production integer ms vs independent Fraction round-half-up (and own SRT formatter)
    boundary = []
    for n, d in TIMING_FPS:
        fps = Fraction(n, d)
        limit = 3000
        bad = [f for f in range(limit) if frame_to_ms(f, fps) != oracle.round_half_up_ms(f, fps)]
        fmt_bad = 0
        for f in range(0, limit, 7):
            ms = oracle.round_half_up_ms(f, fps)
            own = "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)
            fmt_bad += own != ms_to_srt(ms)
        boundary.append({"fps": f"{n}/{d}", "frames_compared": limit, "ms_disagreements": len(bad), "srt_format_disagreements": fmt_bad})
    fr["boundary_ms_vs_independent_rational"] = boundary
    # 2) oracle self-consistency: midpoint form == integer form
    mism = 0
    for n, d in TIMING_FPS[:10]:
        fps = Fraction(n, d)
        for s in range(0, 30):
            for e in range(s + 1, 31):
                mism += oracle.visible_frames_midpoint(s, e, 35, fps) != oracle.visible_frames_integer(s, e, 35)
    fr["oracle_midpoint_vs_integer_disagreements"] = mism
    # 3) drawtext enable expressions and SRT cues from the real plan/export path, edge windows, vs oracle
    plans = []
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "assets"))
        with open(os.path.join(td, "assets", "font.ttf"), "wb") as fh:
            fh.write(fake_font_bytes())
        for n, d in [(24000, 1001), (30000, 1001), (30, 1), (25, 1), (60000, 1001), (7, 1)]:
            total = 40
            sets = [
                ("clip_start_one_frame", [("a", 0, 1, "x")]),
                ("clip_end_one_frame", [("a", total - 1, total, "x")]),
                ("whole_clip", [("a", 0, total, "x")]),
                ("adjacent", [("a", 0, 13, "x"), ("b", 13, 26, "y"), ("c", 26, total, "z")]),
                ("gap_and_one_frame", [("a", 5, 6, "x"), ("b", 6, 7, "y"), ("c", 20, 22, "z")]),
                ("overlap_allowed", [("a", 3, 15, "x"), ("b", 10, 20, "y")]),
            ]
            for label, caps in sets:
                plan, _eb = _plan_enable(caps, (n, d), total, td)
                fps = Fraction(n, d)
                burn = plan.canonical["burn_in"]["captions"]
                srt_cues = oracle.parse_srt_strict(plan.srt)
                order = sorted(range(len(caps)), key=lambda i: (caps[i][1], caps[i][2], i))
                rec = {"fps": f"{n}/{d}", "set": label, "captions": [], "all_ok": True}
                for k, idx in enumerate(order):
                    cid, s, e, _t = caps[idx]
                    want = oracle.visible_frames_midpoint(s, e, total, fps)
                    got_enable = oracle.enable_frames(burn[k]["enable"], total)
                    cue = srt_cues[k]
                    sm, em = oracle.round_half_up_ms(s, fps), oracle.round_half_up_ms(e, fps)
                    models = {m: oracle.consumer_visible(cue["start_ms"], cue["end_ms"], total, fps, m) == want for m in oracle.CONSUMER_MODELS}
                    c = {"id": cid, "window": [s, e], "enable": burn[k]["enable"], "enable_equals_oracle": got_enable == want,
                         "srt_ms_equals_independent": [cue["start_ms"], cue["end_ms"]] == [sm, em], "srt_consumer_models_match_oracle": models}
                    rec["captions"].append(c)
                    rec["all_ok"] = rec["all_ok"] and c["enable_equals_oracle"] and c["srt_ms_equals_independent"]
                # adjacency: touching windows share exactly one boundary millisecond in the SRT
                touching = []
                for a_i, a in enumerate(order):
                    for b_i, b in enumerate(order):
                        if a != b and caps[a][2] == caps[b][1]:
                            touching.append([caps[a][0], caps[b][0], srt_cues[a_i]["end_ms"] == srt_cues[b_i]["start_ms"]])
                rec["touching_pairs_share_boundary_ms"] = touching
                plans.append(rec)
    fr["plan_and_srt_vs_oracle"] = plans
    # 4) consumer-model comparison of the two boundary policies (exhaustive over windows 0<=s<e<=40)
    pol = []
    for n, d in TIMING_FPS:
        fps = Fraction(n, d)
        entry = {"fps": f"{n}/{d}", "frame_period_ms": round(1000 * d / n, 3)}
        for policy in ("half_up", "floor"):
            res = oracle.compare_policy(fps, policy, 40)
            entry[policy] = {m: {"windows": v["windows"], "mismatching": v["mismatching_windows"], "first": v["first"]} for m, v in res.items()}
        pol.append(entry)
    fr["consumer_model_policy_table"] = pol
    return {"schema": "jpcap.timing/1",
            "rule": "frame i is shown by caption [s,e) iff s <= i < e (start inclusive, end exclusive); oracle uses Fraction midpoints (i+1/2)/fps",
            "consumer_models_note": "MODELS of how a player might compare integer-ms cue times with frame times; not measurements of any real player",
            "policy_note": "half_up = production's documented SRT rule; floor = the proposal. 'mismatching' counts windows whose shown frames differ from the oracle.",
            **fr}


# ----------------------------------------------------------------------------- kinsoku / UAX14 -------------------

def kinsoku_report() -> dict:
    rows = []
    for cid, _g, text, _exp, _cls, _n in accepted_cases():
        found = kinsoku.check_lines(text)
        rows.append({"id": cid, "findings": found})
    return {"schema": "jpcap.kinsoku/1", "ruleset": kinsoku.RULESET_DECLARATION, "cases": rows,
            "cases_with_findings": sorted(r["id"] for r in rows if r["findings"])}


PERL_PROBE = ("use Unicode::UCD qw(charprop); print \"UNICODE_VERSION\\t\", Unicode::UCD::UnicodeVersion(), \"\\n\"; "
              "for my $h (@ARGV) { my $c = hex($h); printf \"U+%04X\\t%s\\n\", $c, charprop($c, \"Line_Break\"); }")


def uax14_probe() -> dict:
    chars = sorted(set(kinsoku.LINE_START_PROHIBITED) | set(kinsoku.LINE_END_PROHIBITED) | set(kinsoku.INSEPARABLE_REPEATS) | set("\u2028\u2029\u200b\u3099\u30fc"))
    args = [f"{ord(c):X}" for c in chars]
    try:
        cp = subprocess.run(["perl", "-e", PERL_PROBE, *args], capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"schema": "jpcap.uax14_local/1", "status": "SKIPPED", "reason": f"perl unavailable: {type(exc).__name__}"}
    classes, version = {}, None
    for line in cp.stdout.splitlines():
        k, v = line.split("\t")
        if k == "UNICODE_VERSION":
            version = v
        else:
            classes[k] = v
    return {"schema": "jpcap.uax14_local/1", "status": "RAN", "source": "local Perl 5 Unicode::UCD charprop(Line_Break); Unicode " + str(version),
            "perl": "perl -e <PERL_PROBE> <hex code points>", "not_fetched_from_unicode_org": True,
            "note": "This is the local UCD value used as a consistency check of the DECLARED set. It is not the UAX #14 text (unreachable here) and not a conformance run.",
            "classes": classes}


def declared_set_consistency(probe: dict) -> dict:
    if probe.get("status") != "RAN":
        return {"status": "SKIPPED"}
    start_ok = {"Close_Punctuation", "Close_Parenthesis", "Exclamation", "Infix_Numeric", "Nonstarter", "Conditional_Japanese_Starter", "Break_After", "Inseparable"}
    odd_start = {k: v for k, v in probe["classes"].items() if chr(int(k[2:], 16)) in kinsoku.LINE_START_PROHIBITED and v not in start_ok}
    odd_end = {k: v for k, v in probe["classes"].items() if chr(int(k[2:], 16)) in kinsoku.LINE_END_PROHIBITED and v != "Open_Punctuation"}
    return {"status": "RAN", "line_start_members_with_unexpected_class": odd_start, "line_end_members_with_unexpected_class": odd_end}


# ----------------------------------------------------------------------------- integrity -------------------------

def git(*args, check=True) -> str:
    cp = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, check=check)
    return cp.stdout


def integrity() -> dict:
    out = {"base_commit": BASE_COMMIT}
    out["head"] = git("rev-parse", "HEAD").strip()
    out["branch"] = git("branch", "--show-current").strip()
    out["base_is_ancestor_of_head"] = subprocess.run(["git", "-C", REPO, "merge-base", "--is-ancestor", BASE_COMMIT, "HEAD"]).returncode == 0
    ns = git("diff", "--name-status", f"{BASE_COMMIT}..HEAD").splitlines()
    non_new = [ln for ln in ns if not (ln.startswith("A\t" + EVIDENCE_REL + "/"))]
    out["changed_since_base"] = {"total": len(ns), "outside_new_evidence_dir_or_not_added": non_new}
    base_tree = git("ls-tree", "-r", BASE_COMMIT, "--", "tools/video-core")
    head_tree = git("ls-tree", "-r", "HEAD", "--", "tools/video-core")
    head_old = "\n".join(ln for ln in head_tree.splitlines() if "\t" + EVIDENCE_REL + "/" not in ln) + "\n"
    out["base_tree_listing_sha256"] = sha256(base_tree.encode())
    out["head_tree_listing_without_new_dir_sha256"] = sha256(head_old.encode())
    out["old_tree_identical_blob_ids"] = base_tree == head_old
    out["base_file_count"] = len(base_tree.splitlines())
    man = os.path.join(HERE, "baseline_tracked_files.sha256")
    if os.path.exists(man):
        bad, n = [], 0
        for line in read(man).decode().splitlines():
            h, rel = line.split("  ", 1)
            n += 1
            blob = subprocess.run(["git", "-C", REPO, "show", f"{BASE_COMMIT}:{rel}"], capture_output=True, check=True).stdout
            if sha256(blob) != h:
                bad.append([rel, "manifest differs from base blob"])
            p = os.path.join(REPO, rel)
            if not os.path.isfile(p) or sha256(read(p)) != h:
                bad.append([rel, "working-tree file differs from base"])
        out["baseline_manifest"] = {"files": n, "mismatches": bad}
    status = git("status", "--porcelain", "--", "tools/video-core").splitlines()
    out["worktree_status_porcelain"] = status
    from video_core.plan import canonical_bytes  # noqa: F401
    ex = read(os.path.join(ROOT, "examples", "episode.synthetic.json"))
    plan = prepare(ex)
    out["baseline_plan_sha256"] = {"recomputed": plan.sha256, "expected": BASELINE_PLAN_SHA256, "ok": plan.sha256 == BASELINE_PLAN_SHA256,
                                   "old_plan_canonical_file_sha256": sha256(read(os.path.join(ROOT, "evidence", "plan.canonical.json")))}
    out["production_source_files_identical_to_base"] = all(
        subprocess.run(["git", "-C", REPO, "diff", "--quiet", BASE_COMMIT, "--", "tools/video-core/video_core", "tools/video-core/tests", "tools/video-core/schemas", "tools/video-core/examples"]).returncode == 0
        for _ in (0,))
    out["ok"] = (out["base_is_ancestor_of_head"] and not non_new and out["old_tree_identical_blob_ids"] and out["baseline_plan_sha256"]["ok"]
                 and out["production_source_files_identical_to_base"] and not out.get("baseline_manifest", {}).get("mismatches"))
    return out


# ----------------------------------------------------------------------------- CLI --------------------------------

def cmd_build_corpus(ns) -> int:
    files = build_corpus_files()
    res = [write_or_check(os.path.join(HERE, name), data, ns.check) for name, data in sorted(files.items())]
    print(json.dumps(res, indent=1))
    return 0 if all(r["ok"] for r in res) else 1


def static_reports() -> dict:
    fonts = load_fonts()
    fr = font_report(fonts)
    fr["ttc_note"] = ttc_font_row(fonts)
    return {"conformance.json": conformance_report(fonts), "fonts.json": fr, "glyph_raster.json": glyph_raster_report(fonts),
            "timing.json": timing_report(), "kinsoku.json": kinsoku_report()}


RASTER_CLAIMS = ("covered_but_notdef_identical", "covered_but_blank_not_space_like", "format_or_space_chars_drawn_with_ink")


def check_raster(path: str, live: dict) -> dict:
    """glyph_raster.json depends on the Pillow/FreeType build. Byte-compare only when both match the committed file; otherwise compare
    the stable claims, or say NOT_COMPARABLE (Pillow unusable). Never reports a silent pass."""
    have = json.loads(read(path).decode("ascii")) if os.path.exists(path) else None
    rec = {"path": os.path.relpath(path, HERE)}
    if live.get("status") != "RAN":
        return {**rec, "ok": True, "status": "NOT_COMPARABLE", "reason": live.get("reason")}
    if have is None:
        return {**rec, "ok": False, "status": "MISSING"}
    if (have.get("pillow"), have.get("freetype")) == (live["pillow"], live["freetype"]):
        data = dumps(live)
        return {**rec, "ok": read(path) == data, "status": "BYTE_COMPARED", "sha256": sha256(data)}
    same = all(have["fonts"][f].get(k) == live["fonts"][f].get(k) for f in have["fonts"] for k in RASTER_CLAIMS)
    return {**rec, "ok": same, "status": "CLAIMS_COMPARED_ONLY", "committed_build": [have.get("pillow"), have.get("freetype")], "live_build": [live["pillow"], live["freetype"]]}


def cmd_static(ns) -> int:
    reps = static_reports()
    res = []
    for name, obj in sorted(reps.items()):
        if ns.check and name == "glyph_raster.json":
            res.append(check_raster(os.path.join(REPORTS, name), obj))
        else:
            res.append(write_or_check(os.path.join(REPORTS, name), dumps(obj), ns.check))
    print(json.dumps(res, indent=1))
    return 0 if all(r["ok"] for r in res) else 1


def cmd_uax14(ns) -> int:
    probe = uax14_probe()
    probe["declared_set_consistency"] = declared_set_consistency(probe)
    path = os.path.join(REPORTS, "uax14_local_probe.json")
    if ns.check:
        if probe.get("status") != "RAN":
            print(json.dumps({"status": "SKIPPED", "reason": probe.get("reason")}))
            return 0
        r = write_or_check(path, dumps(probe), True)
        print(json.dumps(r))
        return 0 if r["ok"] else 1
    print(json.dumps(write_or_check(path, dumps(probe), False)))
    return 0


def baseline_manifest_bytes() -> bytes:
    """sha256 of every file under tools/video-core AT THE BASE COMMIT (read from git objects, not the working tree)."""
    names = git("ls-tree", "-r", "--name-only", BASE_COMMIT, "--", "tools/video-core").splitlines()
    lines = []
    for rel in sorted(names):
        blob = subprocess.run(["git", "-C", REPO, "show", f"{BASE_COMMIT}:{rel}"], capture_output=True, check=True).stdout
        lines.append(f"{sha256(blob)}  {rel}\n")
    return "".join(lines).encode("utf-8")


def cmd_baseline(ns) -> int:
    res = write_or_check(os.path.join(HERE, "baseline_tracked_files.sha256"), baseline_manifest_bytes(), ns.check)
    print(json.dumps(res))
    return 0 if res["ok"] else 1


def cmd_integrity(ns) -> int:
    rep = integrity()
    sys.stdout.write(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    return 0 if rep["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("build-corpus", cmd_build_corpus), ("static-report", cmd_static), ("uax14-probe", cmd_uax14), ("baseline-manifest", cmd_baseline)):
        p = sub.add_parser(name)
        p.add_argument("--check", action="store_true", help="compare with the committed file(s); write nothing")
        p.set_defaults(fn=fn)
    p = sub.add_parser("integrity")
    p.set_defaults(fn=cmd_integrity)
    p = sub.add_parser("render-once", help="see render_once.py")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda ns: __import__("render_once").main(ns.rest))
    p = sub.add_parser("analyze-render", help="see render_once.py")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda ns: __import__("render_once").main(["--analyze-only", *ns.rest]))
    ns = ap.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    sys.exit(main())
