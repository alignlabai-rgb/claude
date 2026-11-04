#!/usr/bin/env python3
"""Offline analysis of the recorded single-frame experiments (stdlib only; launches no process).

Reads records/Exx.json + raw/*, computes region metrics against the exact-rational oracle and evaluates the pre-registered
criteria.  Writes measurements.json and conclusions.json.  It can be re-run any number of times: it never touches ffmpeg.
If a run turns out to be wrong, keep its output as measurements.run<N>-<reason>.json (see README) before correcting this script.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_lib as L  # noqa: E402

PRE = json.load(open(os.path.join(HERE, "preregistration.json")))
TOL = PRE["tolerances"]
T_YUV, T_RAW, T_MP4, T_SYM = TOL["T_YUV_codes"], TOL["T_RGB_raw_interior"], TOL["T_RGB_mp4_interior"], TOL["T_symptom_min_error_baseline"]


def rd(path):
    with open(os.path.join(HERE, path), "rb") as fh:
        return fh.read()


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Geo:
    def __init__(self, g, patches):
        self.w, self.h, self.pw, self.ph, self.cols = g["w"], g["h"], g["pw"], g["ph"], g["cols"]
        self.patches = patches
        self.pix = {p["index"]: {"interior": [], "boundary": [], "deep_posthoc": []} for p in patches}
        self.cpix = {p["index"]: {"interior": [], "boundary": []} for p in patches}
        for y in range(self.h):
            for x in range(self.w):
                r = L.region_of(x, y, self.pw, self.ph)
                if r != "transition":
                    self.pix[self.pidx(x, y)][r].append((x, y))
                if L.dist_to_patch_edge(x, y, self.pw, self.ph) >= 8:   # POST-HOC region (not pre-registered), only for diagnosis
                    self.pix[self.pidx(x, y)]["deep_posthoc"].append((x, y))
        for cy in range(self.h // 2):
            for cx in range(self.w // 2):
                lum = [(2 * cx + dx, 2 * cy + dy) for dx in (0, 1) for dy in (0, 1)]
                regs = {L.region_of(x, y, self.pw, self.ph) for x, y in lum}
                p = self.pidx(2 * cx, 2 * cy)
                if regs == {"interior"}:
                    self.cpix[p]["interior"].append((cx, cy))
                elif "boundary" in regs:
                    self.cpix[p]["boundary"].append((cx, cy))

    def pidx(self, x, y):
        return (y // self.ph) * self.cols + x // self.pw


def yuv_at(planes, w, x, y):
    yp, up, vp = planes
    c = (y // 2) * (w // 2) + x // 2
    return yp[y * w + x], up[c], vp[c]


def stat_dev(vals, target):
    d = [v - target for v in vals]
    return {"n": len(vals), "min": min(vals), "max": max(vals), "max_abs_dev": max(abs(x) for x in d)}


def rgb_stats(pixels, getter, nominal):
    """pixels: [(x,y)], getter(x,y)->(r,g,b)"""
    if not pixels:
        return None
    err = [0, 0, 0]
    sum_abs, sum_signed = [0, 0, 0], [0, 0, 0]
    cnt = Counter()
    for x, y in pixels:
        px = getter(x, y)
        cnt[px] += 1
        for c in range(3):
            e = px[c] - nominal[c]
            sum_signed[c] += e
            sum_abs[c] += abs(e)
            err[c] = max(err[c], abs(e))
    n = len(pixels)
    mode, mode_n = cnt.most_common(1)[0]
    return {"n": n, "max_abs_err": max(err), "max_abs_err_rgb": err, "mean_abs_err": round(sum(sum_abs) / (3 * n), 4),
            "mean_signed_err_rgb": [round(s / n, 4) for s in sum_signed], "mode_rgb": list(mode), "mode_fraction": round(mode_n / n, 4),
            "mode_signed_delta_rgb": [mode[c] - nominal[c] for c in range(3)], "unique_rgb": len(cnt)}


def analyse_frame(geo, raw, rgb_mp4):
    w, h = geo.w, geo.h
    planes = L.split_yuv420p(raw, w, h)
    yp, up, vp = planes
    out = {}
    for p in geo.patches:
        rgb = tuple(p["rgb"])
        i = p["index"]
        c709, c601 = L.forward_codes(rgb, "bt709"), L.forward_codes(rgb, "bt601")
        ex = [float(v) for v in L.forward_exact(rgb, "bt709")]
        entry = {"name": p["name"], "nominal_rgb": list(rgb), "ideal_codes_bt709": list(c709), "ideal_codes_bt601": list(c601),
                 "exact_unrounded_bt709": [round(v, 4) for v in ex]}
        for region in ("interior", "boundary", "deep_posthoc"):
            if region == "deep_posthoc":
                getter = lambda x, y: L.inverse_rgb(yuv_at(planes, w, x, y), "bt709")
                entry["rgb_oracle_decode_deep_posthoc"] = rgb_stats(geo.pix[i][region], getter, rgb)
                if rgb_mp4 is not None:
                    g2 = lambda x, y: tuple(rgb_mp4[(y * w + x) * 3:(y * w + x) * 3 + 3])
                    entry["rgb_ffmpeg_mp4_decode_deep_posthoc"] = rgb_stats(geo.pix[i][region], g2, rgb)
                continue
            lum = [yp[y * w + x] for x, y in geo.pix[i][region]]
            cb = [up[cy * (w // 2) + cx] for cx, cy in geo.cpix[i][region]]
            cr = [vp[cy * (w // 2) + cx] for cx, cy in geo.cpix[i][region]]
            yuv = {}
            for name, vals, k in (("Y", lum, 0), ("Cb", cb, 1), ("Cr", cr, 2)):
                if not vals:
                    continue
                yuv[name] = dict(stat_dev(vals, c709[k]), mode=Counter(vals).most_common(1)[0][0],
                                 max_abs_dev_bt601=max(abs(v - c601[k]) for v in vals),
                                 mean_signed_dev_vs_exact_bt709=round(sum(vals) / len(vals) - ex[k], 4))
            entry[f"yuv_{region}"] = yuv
            getter = lambda x, y: L.inverse_rgb(yuv_at(planes, w, x, y), "bt709")
            entry[f"rgb_oracle_decode_{region}"] = rgb_stats(geo.pix[i][region], getter, rgb)
            if rgb_mp4 is not None:
                g2 = lambda x, y: tuple(rgb_mp4[(y * w + x) * 3:(y * w + x) * 3 + 3])
                entry[f"rgb_ffmpeg_mp4_decode_{region}"] = rgb_stats(geo.pix[i][region], g2, rgb)
        if rgb_mp4 is not None:
            d = 0
            for x, y in geo.pix[i]["interior"]:
                o = L.inverse_rgb(yuv_at(planes, w, x, y), "bt709")
                m = tuple(rgb_mp4[(y * w + x) * 3:(y * w + x) * 3 + 3])
                d = max(d, max(abs(a - b) for a, b in zip(o, m)))
            entry["mp4_decode_vs_raw_oracle_decode_interior_max_abs"] = d
        out[i] = entry
    return out


def summarise(per_patch, has_mp4):
    s = {}
    items = list(per_patch.values())
    k1 = {e["name"]: max(v["max_abs_dev"] for v in e["yuv_interior"].values()) for e in items}
    s["K1_yuv_interior_max_dev_bt709_by_patch"] = k1
    s["K1_pass"] = all(v <= T_YUV for v in k1.values())
    s["K1_failing_patches"] = [n for n, v in k1.items() if v > T_YUV]
    s["yuv_interior_max_dev_bt709_overall"] = max(k1.values())
    s["yuv_interior_max_dev_bt601_by_patch"] = {e["name"]: max(v["max_abs_dev_bt601"] for v in e["yuv_interior"].values()) for e in items}
    k2 = {e["name"]: e["rgb_oracle_decode_interior"]["max_abs_err"] for e in items}
    s["K2_rgb_oracle_interior_max_err_by_patch"] = k2
    s["K2_pass"] = all(v <= T_RAW for v in k2.values())
    s["K2_failing_patches"] = [n for n, v in k2.items() if v > T_RAW]
    s["rgb_oracle_interior_max_err_overall"] = max(k2.values())
    s["rgb_oracle_interior_mean_abs_err_mean_over_patches"] = round(sum(e["rgb_oracle_decode_interior"]["mean_abs_err"] for e in items) / len(items), 4)
    s["rgb_oracle_boundary_max_err_overall"] = max(e["rgb_oracle_decode_boundary"]["max_abs_err"] for e in items if e["rgb_oracle_decode_boundary"])
    s["rgb_oracle_boundary_mean_abs_err_mean_over_patches"] = round(sum(e["rgb_oracle_decode_boundary"]["mean_abs_err"] for e in items if e["rgb_oracle_decode_boundary"]) / len(items), 4)
    neutral = [e for e in items if len(set(e["nominal_rgb"])) == 1]
    chroma = [e for e in items if len(set(e["nominal_rgb"])) > 1]
    s["neutral_patches"] = {"n": len(neutral), "rgb_oracle_interior_max_err": max((e["rgb_oracle_decode_interior"]["max_abs_err"] for e in neutral), default=None)}
    s["chromatic_patches"] = {"n": len(chroma), "rgb_oracle_interior_max_err": max((e["rgb_oracle_decode_interior"]["max_abs_err"] for e in chroma), default=None)}
    s["bias_vs_exact_bt709_mean_over_patches_interior"] = {
        k: round(sum(e["yuv_interior"][k]["mean_signed_dev_vs_exact_bt709"] for e in items) / len(items), 4) for k in ("Y", "Cb", "Cr")}
    s["worst_patch_rgb_oracle_interior"] = max(items, key=lambda e: e["rgb_oracle_decode_interior"]["max_abs_err"])["name"]
    if has_mp4:
        s["POSTHOC_rgb_mp4_deep_interior_max_err_overall"] = max((e["rgb_ffmpeg_mp4_decode_deep_posthoc"]["max_abs_err"] for e in items if e["rgb_ffmpeg_mp4_decode_deep_posthoc"]), default=None)
        k3 = {e["name"]: e["rgb_ffmpeg_mp4_decode_interior"]["max_abs_err"] for e in items}
        s["K3_rgb_ffmpeg_mp4_interior_max_err_by_patch"] = k3
        s["K3_pass"] = all(v <= T_MP4 for v in k3.values())
        s["K3_failing_patches"] = [n for n, v in k3.items() if v > T_MP4]
        s["rgb_mp4_interior_max_err_overall"] = max(k3.values())
        s["rgb_mp4_interior_mean_abs_err_mean_over_patches"] = round(sum(e["rgb_ffmpeg_mp4_decode_interior"]["mean_abs_err"] for e in items) / len(items), 4)
        s["rgb_mp4_boundary_max_err_overall"] = max(e["rgb_ffmpeg_mp4_decode_boundary"]["max_abs_err"] for e in items if e["rgb_ffmpeg_mp4_decode_boundary"])
        s["rgb_mp4_boundary_mean_abs_err_mean_over_patches"] = round(sum(e["rgb_ffmpeg_mp4_decode_boundary"]["mean_abs_err"] for e in items if e["rgb_ffmpeg_mp4_decode_boundary"]) / len(items), 4)
        s["mp4_decode_vs_raw_oracle_interior_max_abs_overall"] = max(e["mp4_decode_vs_raw_oracle_decode_interior_max_abs"] for e in items)
    return s


def plane_diff(a, b, w, h, geo=None, region=None):
    pa, pb = L.split_yuv420p(a, w, h), L.split_yuv420p(b, w, h)
    res = {}
    for k, name in enumerate(("Y", "Cb", "Cr")):
        res[name] = max(abs(x - y) for x, y in zip(pa[k], pb[k]))
    res["max"] = max(res.values())
    res["differing_samples"] = sum(1 for k in range(3) for x, y in zip(pa[k], pb[k]) if x != y)
    return res


def probe_summary(eid):
    p = os.path.join("raw", f"{eid}.ffprobe.json")
    if not os.path.exists(os.path.join(HERE, p)):
        return None
    j = json.loads(rd(p))
    s = j["streams"][0]
    keep = ("codec_name", "profile", "pix_fmt", "width", "height", "color_range", "color_space", "color_transfer", "color_primaries",
            "chroma_location", "field_order", "r_frame_rate", "nb_frames", "has_b_frames", "level")
    return {k: s.get(k) for k in keep}


def main():
    patches = json.load(open(os.path.join(HERE, "fixtures/patches.json")))["patches"]
    meas = {"schema": "colorstudy.measurements/1", "oracle": PRE["oracle"], "tolerances": TOL, "experiments": {}, "cross": {}}
    recs = {}
    for f in sorted(os.listdir(os.path.join(HERE, "records"))):
        if f.endswith(".json") and not f.endswith(".started.json"):
            r = json.load(open(os.path.join(HERE, "records", f)))
            recs[r["id"]] = r
    raws = {}
    for eid, r in recs.items():
        g = r["geometry"]
        gp = patches if g["pw"] == L.PATCH else [{"index": 0, "name": "reported_30506a", "rgb": [0x30, 0x50, 0x6A]}]
        geo = Geo(g, gp)
        exp = {"title": r["title"], "spec": r["spec"], "geometry": g,
               "tool": {"ffmpeg": r["tools"]["ffmpeg_version"], "ffmpeg_realpath": r["tools"]["ffmpeg_realpath"], "ffprobe": r["tools"]["ffprobe_version"]},
               "argv": r["main"]["argv"], "returncode": r["main"]["returncode"], "signal": r["main"]["signal"], "timed_out": r["main"]["timed_out"],
               "wall_seconds": r["main"]["wall_seconds"], "negotiated_scaler_lines": r["negotiated_scaler_lines"],
               "outputs": r["outputs"], "readbacks": [{k: v for k, v in rb.items() if k in ("name", "argv", "returncode", "output", "negotiated_scaler_lines", "wall_seconds")} for rb in r["readbacks"]],
               "ffprobe_stream": probe_summary(eid)}
        if r["spec"]["kind"] == "yuv_counterexample":
            src = rd("fixtures/chart_bt709_ideal.yuv420p")
            exp["counterexample"] = {}
            for name in ("raw_baseline", "raw_gbrp"):
                buf = rd(r["outputs"][name]["path"])
                raws[(eid, name)] = buf
                per = {}
                psrc = L.split_yuv420p(src, g["w"], g["h"])
                pout = L.split_yuv420p(buf, g["w"], g["h"])
                for p in patches:
                    i = p["index"]
                    dev = {}
                    for k, nm in enumerate(("Y", "Cb", "Cr")):
                        if k == 0:
                            pts = geo.pix[i]["interior"]
                            idx = [y * g["w"] + x for x, y in pts]
                        else:
                            idx = [cy * (g["w"] // 2) + cx for cx, cy in geo.cpix[i]["interior"]]
                        dev[nm] = max(abs(pout[k][j] - psrc[k][j]) for j in idx)
                    per[p["name"]] = dev
                exp["counterexample"][name] = {
                    "all_samples_diff_vs_source": plane_diff(src, buf, g["w"], g["h"]),
                    "interior_max_dev_by_patch": per,
                    "interior_max_dev_overall": max(max(v.values()) for v in per.values()),
                    "patches_exceeding_T_YUV": [n for n, v in per.items() if max(v.values()) > T_YUV],
                    "sha256_equals_source": sha(buf) == sha(src)}
            meas["experiments"][eid] = exp
            continue
        raw = rd(r["outputs"]["raw"]["path"])
        raws[(eid, "raw")] = raw
        rgb_mp4 = None
        if r["readbacks"] and r["readbacks"][-1]["returncode"] == 0:
            rgb_mp4 = rd(r["readbacks"][-1]["output"]["path"])
        per = analyse_frame(geo, raw, rgb_mp4)
        exp["per_patch"] = per
        exp["summary"] = summarise(per, rgb_mp4 is not None)
        meas["experiments"][eid] = exp
    # ---- oracle floor: the exact-rational ideal frame pushed through the same decode (no ffmpeg anywhere)
    ideal = rd("fixtures/chart_bt709_ideal.yuv420p")
    gch = {"w": L.W, "h": L.H, "pw": L.PATCH, "ph": L.PATCH, "cols": L.COLS}
    fl = analyse_frame(Geo(gch, patches), ideal, None)
    meas["oracle_floor"] = {"description": "exact BT.709 limited 4:2:0 of the chart (patch edges align with chroma blocks) decoded with nearest chroma: the best an 8-bit 4:2:0 chart can do with this decoder",
                            "summary": summarise(fl, False), "sha256_of_ideal_frame": sha(ideal)}
    # ---- cross-experiment comparisons
    sha_of = {f"{k[0]}:{k[1]}": sha(v) for k, v in raws.items()}
    meas["cross"]["raw_sha256"] = sha_of
    w, h = L.W, L.H
    pairs = [("E02", "E03"), ("E02", "E04"), ("E02", "E05"), ("E01", "E12"), ("E01", "E08"), ("E06", "E07"), ("E01", "E06"), ("E02", "E06")]
    meas["cross"]["raw_yuv_max_abs_diff_all_samples"] = {f"{a}_vs_{b}": plane_diff(raws[(a, "raw")], raws[(b, "raw")], w, h) for a, b in pairs}
    meas["cross"]["raw_yuv_vs_ideal_bt709_all_samples"] = {e: plane_diff(raws[(e, "raw")], ideal, w, h) for e in ("E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E12")}
    meas["cross"]["raw_sha256_equal"] = {f"{a}=={b}": sha_of[f"{a}:raw"] == sha_of[f"{b}:raw"] for a, b in pairs}
    meas["cross"]["raw_sha256_equal"]["E05==ideal_bt709_exact_rational"] = sha_of["E05:raw"] == sha(ideal)
    # ---- post-hoc (NOT pre-registered) look at what the baseline bias looks like
    e1 = meas["experiments"]["E01"]["per_patch"]
    trunc = rnd = 0
    for e in e1.values():
        ex = [F(str(v)) for v in []]
    ph = {"label": "POST-HOC, not pre-registered: exploratory description of the E01/E12 bias", "per_patch": {}}
    cnt_floor = cnt_round = cnt_minus1 = total = 0
    planes = L.split_yuv420p(raws[("E12", "raw")], w, h)
    geo = Geo(gch, patches)
    for p in patches:
        rgb = tuple(p["rgb"])
        ex = L.forward_exact(rgb, "bt709")
        x, y = geo.pix[p["index"]]["interior"][0]
        got = yuv_at(planes, w, x, y)
        fl_ = tuple(v.__floor__() for v in ex)
        rd_ = L.forward_codes(rgb, "bt709")
        for k in range(3):
            total += 1
            cnt_floor += got[k] == fl_[k]
            cnt_round += got[k] == rd_[k]
            cnt_minus1 += got[k] == rd_[k] - 1
        ph["per_patch"][p["name"]] = {"E12_code": list(got), "floor_exact": list(fl_), "round_exact": list(rd_)}
    ph["samples"] = total
    ph["E12_code_equals_floor_of_exact_bt709"] = cnt_floor
    ph["E12_code_equals_round_of_exact_bt709"] = cnt_round
    ph["E12_code_equals_round_minus_1"] = cnt_minus1
    meas["cross"]["posthoc_bias_description"] = ph
    # ---- (amendment-1 follow-up) what does the colour source draw, and how much of E01 is intrinsic two-stage 8-bit quantisation?
    geo = Geo(gch, patches)
    p12 = L.split_yuv420p(raws[("E12", "raw")], w, h)
    p01 = L.split_yuv420p(raws[("E01", "raw")], w, h)
    dev601 = {}
    two_stage_vs_e01, two_stage_vs_709, e01_vs_709 = {}, {}, {}
    for p in patches:
        rgb = tuple(p["rgb"])
        x, y = geo.pix[p["index"]]["interior"][0]
        got12, got01 = yuv_at(p12, w, x, y), yuv_at(p01, w, x, y)
        i601 = L.forward_codes(rgb, "bt601")
        dev601[p["name"]] = max(abs(a - b) for a, b in zip(got12, i601))
        # exact 601 limited decode WITHOUT rounding/clipping, then exact 709 forward with rounding = the best any 601->709 yuv->yuv step could do
        kr, kb = L.MATRICES["bt601"]; kg = 1 - kr - kb
        yy, cb, cr = F(got12[0] - 16, 219), F(got12[1] - 128, 224), F(got12[2] - 128, 224)
        r = yy + 2 * (1 - kr) * cr; b = yy + 2 * (1 - kb) * cb; g = (yy - kr * r - kb * b) / kg
        kr9, kb9 = L.MATRICES["bt709"]; kg9 = 1 - kr9 - kb9
        y9 = kr9 * r + kg9 * g + kb9 * b
        two = tuple(L._rnd(v) for v in (16 + 219 * y9, 128 + 224 * (b - y9) / (2 * (1 - kb9)), 128 + 224 * (r - y9) / (2 * (1 - kr9))))
        two_stage_vs_e01[p["name"]] = max(abs(a - b_) for a, b_ in zip(got01, two))
        two_stage_vs_709[p["name"]] = max(abs(a - b_) for a, b_ in zip(two, L.forward_codes(rgb, "bt709")))
        e01_vs_709[p["name"]] = max(abs(a - b_) for a, b_ in zip(got01, L.forward_codes(rgb, "bt709")))
    meas["cross"]["E12_colour_source_output_vs_exact_bt601_limited"] = {"max_dev_codes_by_patch": dev601, "max_over_patches": max(dev601.values()),
        "exact_match_all_patches": max(dev601.values()) == 0}
    meas["cross"]["two_stage_oracle"] = {
        "description": "E12 codes (what the colour source drew, BT.601) -> exact rational RGB (no rounding/clipping) -> exact BT.709 limited rounding. "
                       "This is the best any 8-bit-source yuv->yuv re-matrixing could give; compare with what scale produced (E01).",
        "max_dev_oracle_vs_bt709_ideal_by_patch": two_stage_vs_709, "max_over_patches_vs_bt709_ideal": max(two_stage_vs_709.values()),
        "max_dev_E01_vs_two_stage_oracle_by_patch": two_stage_vs_e01, "max_over_patches_E01_vs_oracle": max(two_stage_vs_e01.values()),
        "max_dev_E01_vs_bt709_ideal": max(e01_vs_709.values())}
    return meas, raws, recs


def conclusions(meas, raws, recs):
    E = meas["experiments"]
    S = lambda e: E[e]["summary"]
    c = {"schema": "colorstudy.conclusions/1", "criteria": {}, "hypotheses": {}, "counterexamples": [], "key_numbers": {}}
    for e in ("E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E12"):
        c["criteria"][f"K1_{e}"] = {"pass": S(e)["K1_pass"], "overall_max_dev_codes": S(e)["yuv_interior_max_dev_bt709_overall"], "failing_patches": S(e)["K1_failing_patches"]}
        c["criteria"][f"K2_{e}"] = {"pass": S(e)["K2_pass"], "overall_max_err": S(e)["rgb_oracle_interior_max_err_overall"], "failing_patches": S(e)["K2_failing_patches"]}
        c["criteria"][f"K3_{e}"] = {"pass": S(e)["K3_pass"], "overall_max_err": S(e)["rgb_mp4_interior_max_err_overall"], "failing_patches": S(e)["K3_failing_patches"]}
    for e in ("E09", "E10"):
        c["criteria"][f"K1_{e}"] = {"pass": S(e)["K1_pass"], "overall_max_dev_codes": S(e)["yuv_interior_max_dev_bt709_overall"]}
        c["criteria"][f"K2_{e}"] = {"pass": S(e)["K2_pass"], "overall_max_err": S(e)["rgb_oracle_interior_max_err_overall"]}
        c["criteria"][f"K3_{e}"] = {"pass": S(e)["K3_pass"], "overall_max_err": S(e)["rgb_mp4_interior_max_err_overall"]}
    k4 = S("E09")["rgb_mp4_interior_max_err_overall"]
    c["criteria"]["K4_symptom_reproduced_E09"] = {"pass": k4 >= T_SYM, "ffmpeg_decoded_interior_mode_rgb": E["E09"]["per_patch"][0]["rgb_ffmpeg_mp4_decode_interior"]["mode_rgb"],
                                                  "signed_delta_vs_nominal": E["E09"]["per_patch"][0]["rgb_ffmpeg_mp4_decode_interior"]["mode_signed_delta_rgb"], "max_err": k4}
    c["criteria"]["K5_tag_only_pixels_identical_E08_E01"] = {"pass": meas["cross"]["raw_sha256_equal"]["E01==E08"]}
    d67 = meas["cross"]["raw_yuv_max_abs_diff_all_samples"]["E06_vs_E07"]["max"]
    c["criteria"]["K6_png_file_branch"] = {"pass": S("E06")["K1_pass"] and S("E06")["K2_pass"] and d67 <= T_YUV, "E06_vs_E07_max_code_diff": d67}
    ce = E["E11"]["counterexample"]
    c["criteria"]["K8_gbrp_misapplied_to_yuv_source"] = {"baseline_identical_to_source": ce["raw_baseline"]["sha256_equals_source"],
                                                         "gbrp_interior_max_dev_codes": ce["raw_gbrp"]["interior_max_dev_overall"],
                                                         "patches_exceeding": ce["raw_gbrp"]["patches_exceeding_T_YUV"]}
    H = c["hypotheses"]
    # H1 (601-drawn): supported only if E01 matches 601 within 1 for >=30 patches AND is not within 1 of 709 for the patches where they differ by >1
    b601 = S("E01")["yuv_interior_max_dev_bt601_by_patch"]
    differ = [p["name"] for p in json.load(open(os.path.join(HERE, "fixtures/patches.json")))["patches"]
              if max(abs(a - b) for a, b in zip(L.forward_codes(tuple(p["rgb"]), "bt601"), L.forward_codes(tuple(p["rgb"]), "bt709"))) > 2]
    h1_match601 = sum(1 for n in differ if b601[n] <= 1)
    h1_match709 = sum(1 for n in differ if S("E01")["K1_yuv_interior_max_dev_bt709_by_patch"][n] <= 1)
    H["H1_as_pre_registered"] = {"verdict": "REJECTED as written (its 'scale is a no-op' clause is false: E01 != E12); its 'colour source draws BT.601' clause is SUPPORTED by E12",
                                 "patches_where_601_and_709_differ_by_more_than_2_codes": len(differ),
                                 "of_which_E01_final_codes_within_1_code_of_601": h1_match601, "of_which_E01_final_codes_within_1_code_of_709": h1_match709,
                                 "E12_equals_exact_bt601_limited_on_all_32_patches": meas["cross"]["E12_colour_source_output_vs_exact_bt601_limited"]["exact_match_all_patches"],
                                 "scale_step_changed_pixels_E01_vs_E12": not meas["cross"]["raw_sha256_equal"]["E01==E12"],
                                 "two_stage_oracle": {k: v for k, v in meas["cross"]["two_stage_oracle"].items() if k.startswith("max_over") or k == "max_dev_E01_vs_bt709_ideal"}}
    H["H2_gbrp_fix"] = {"verdict": "SUPPORTED" if (S("E02")["K1_pass"] and S("E02")["K2_pass"]) else "NOT SUPPORTED (see failing patches)",
                        "E02_K1": S("E02")["K1_pass"], "E02_K2": S("E02")["K2_pass"], "E02_K3": S("E02")["K3_pass"],
                        "E01_K1": S("E01")["K1_pass"], "E01_K2": S("E01")["K2_pass"], "E01_K3": S("E01")["K3_pass"]}
    neutral_same = all(E["E01"]["per_patch"][i]["yuv_interior"] == E["E01"]["per_patch"][i]["yuv_interior"] for i in E["E01"]["per_patch"])
    gray_err_base = S("E01")["neutral_patches"]["rgb_oracle_interior_max_err"]
    gray_err_fix = S("E02")["neutral_patches"]["rgb_oracle_interior_max_err"]
    H["H3_neutral_unaffected_by_matrix"] = {"verdict": "REJECTED as stated" if gray_err_base > gray_err_fix else "SUPPORTED",
                                           "neutral_max_rgb_err_baseline": gray_err_base, "neutral_max_rgb_err_fix": gray_err_fix,
                                           "note": "the matrix does not matter for grays, but the baseline's code bias (see post-hoc) still shifts them"}
    H["H4_png_branch_already_correct"] = {"verdict": "SUPPORTED" if c["criteria"]["K6_png_file_branch"]["pass"] else "NOT SUPPORTED"}
    H["H5_tag_only_pixels_identical"] = {"verdict": "SUPPORTED" if c["criteria"]["K5_tag_only_pixels_identical_E08_E01"]["pass"] else "NOT SUPPORTED",
                                         "E01_decode_mode_rgb_reported_patch": E["E01"]["per_patch"][8]["rgb_ffmpeg_mp4_decode_interior"]["mode_rgb"],
                                         "E08_decode_mode_rgb_reported_patch": E["E08"]["per_patch"][8]["rgb_ffmpeg_mp4_decode_interior"]["mode_rgb"]}
    H["H6_gbrp_on_yuv_source_harmful"] = {"verdict": "SUPPORTED" if ce["raw_gbrp"]["interior_max_dev_overall"] > T_YUV else "NOT SUPPORTED", "max_dev_codes": ce["raw_gbrp"]["interior_max_dev_overall"]}
    H["H7_alternatives_equivalent"] = {"verdict": "see cross.raw_yuv_max_abs_diff_all_samples", "E02_vs_E03": meas["cross"]["raw_yuv_max_abs_diff_all_samples"]["E02_vs_E03"],
                                       "E02_vs_E04": meas["cross"]["raw_yuv_max_abs_diff_all_samples"]["E02_vs_E04"], "E02_vs_E05": meas["cross"]["raw_yuv_max_abs_diff_all_samples"]["E02_vs_E05"]}
    H["E12_tap"] = {"scale_step_changes_pixels_E01_vs_E12": not meas["cross"]["raw_sha256_equal"]["E01==E12"]}
    # counterexamples = every failed criterion for a proposed route, plus any patch over tolerance
    for e in ("E02", "E03", "E04", "E05", "E07", "E10"):
        for k in ("K1", "K2", "K3"):
            cr = c["criteria"][f"{k}_{e}"]
            if not cr["pass"]:
                c["counterexamples"].append({"experiment": e, "criterion": k, **cr})
    c["key_numbers"] = {e: {"K2_interior_max_err_raw": S(e)["rgb_oracle_interior_max_err_overall"], "K3_interior_max_err_mp4": S(e)["rgb_mp4_interior_max_err_overall"],
                            "interior_mean_abs_err_raw": S(e)["rgb_oracle_interior_mean_abs_err_mean_over_patches"],
                            "boundary_max_err_raw": S(e)["rgb_oracle_boundary_max_err_overall"], "boundary_mean_abs_err_raw": S(e)["rgb_oracle_boundary_mean_abs_err_mean_over_patches"],
                            "boundary_max_err_mp4": S(e)["rgb_mp4_boundary_max_err_overall"],
                            "yuv_interior_max_dev_codes_bt709": S(e)["yuv_interior_max_dev_bt709_overall"]} for e in ("E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E09", "E10", "E12")}
    c["key_numbers"]["oracle_floor"] = {"interior_max_err": meas["oracle_floor"]["summary"]["rgb_oracle_interior_max_err_overall"],
                                       "boundary_max_err": meas["oracle_floor"]["summary"]["rgb_oracle_boundary_max_err_overall"]}
    return c


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "measurements.json"
    meas, raws, recs = main()
    with open(os.path.join(HERE, out), "w") as fh:
        json.dump(meas, fh, indent=1)
        fh.write("\n")
    con = conclusions(meas, raws, recs)
    with open(os.path.join(HERE, out.replace("measurements", "conclusions")), "w") as fh:
        json.dump(con, fh, indent=1)
        fh.write("\n")
    print(json.dumps({"criteria": {k: v.get("pass") for k, v in con["criteria"].items()}, "hypotheses": {k: v["verdict"] for k, v in con["hypotheses"].items() if "verdict" in v}}, indent=1))
