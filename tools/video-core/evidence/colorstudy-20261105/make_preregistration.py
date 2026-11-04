#!/usr/bin/env python3
"""Writes preregistration.json: hypotheses, arithmetic predictions, tolerances and pass/fail criteria, sealed (with the sha256 of the
driver, library and fixtures) and committed BEFORE any numbered experiment is run.  analyze.py reads the criteria from this file."""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_lib as L  # noqa: E402
import run_study as R  # noqa: E402


def sha(path):
    with open(os.path.join(HERE, path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    P = L.patch_table()
    floor = L.quantisation_floor(P)
    ties = []
    for p in P:
        for m in ("bt709", "bt601"):
            for v in L.forward_exact(tuple(p["rgb"]), m):
                if (v - v.__floor__()) == L.F(1, 2):
                    ties.append([p["name"], m])
    pred = {}
    for p in P:
        rgb = tuple(p["rgb"])
        c601, c709 = L.forward_codes(rgb, "bt601"), L.forward_codes(rgb, "bt709")
        pred[p["name"]] = {"rgb": list(rgb), "ideal_codes_bt709": list(c709), "ideal_codes_bt601": list(c601),
                           "H1_bt601_codes_read_as_bt709_rgb": list(L.inverse_rgb(c601, "bt709")),
                           "bt709_codes_read_as_bt709_rgb": list(L.inverse_rgb(c709, "bt709"))}
    doc = {
        "schema": "colorstudy.preregistration/1",
        "written_before_any_numbered_experiment": True,
        "sealed_files_sha256": {f: sha(f) for f in ("study_lib.py", "run_study.py", "make_fixtures.py", "fixtures/fixtures.sha256", "fixtures/patches.json")},
        "experiments": {k: {kk: vv for kk, vv in v.items()} for k, v in R.EXPERIMENTS.items()},
        "limits": R.LIMITS,
        "regions": {"patch_px": L.PATCH, "interior": f"distance to nearest patch edge >= {L.INTERIOR_MIN_DIST} px",
                    "boundary": f"distance <= {L.BOUNDARY_MAX_DIST} px", "transition": "2..3 px, excluded from both",
                    "chroma_interior": "a 4:2:0 chroma sample whose four luma pixels are all interior"},
        "oracle": {"kind": "fractions.Fraction exact arithmetic, round half up, standard luma coefficients",
                   "bt709": "Kr=0.2126 Kb=0.0722", "bt601": "Kr=0.299 Kb=0.114", "ranges": "limited: Y 16..235, C 16..240; RGB full 0..255",
                   "decode_chroma_upsampling": "nearest (replicate) for the arithmetic oracle; the ffmpeg read-back uses swscale's own filter",
                   "exact_tie_cases_in_forward_conversion": ties},
        "numerical_uncertainty": {
            "quantisation_floor_max_channel_error_on_chart": max(floor),
            "analytic_half_code_bound_rgb": L.worst_case_bound("bt709"),
            "note": "8-bit limited-range YCbCr cannot represent every RGB triple: the best achievable interior error is the floor above (<= 2 on this chart). "
                    "swscale uses fixed-point coefficients and may differ from exact rounding by 1 code; x264 (crf 18) and the decoder add more. "
                    "These are the reasons for the tolerances below; they are engineering choices, not standard values."},
        "tolerances": {"T_YUV_codes": 1, "T_RGB_raw_interior": 3, "T_RGB_mp4_interior": 4, "T_symptom_min_error_baseline": 3,
                       "justification": {"T_YUV_codes": "swscale fixed point vs exact rounding: one code per plane",
                                         "T_RGB_raw_interior": "floor (2) + one residual level from <=1-code plane drift; also the level the author reported (<=3)",
                                         "T_RGB_mp4_interior": "T_RGB_raw_interior + 1 for x264 and the decoder's own conversion",
                                         "T_symptom_min_error_baseline": "the reported symptom was 44,78,102 vs 48,80,106 (largest channel 4)"}},
        "hypotheses": {
            "H1": "the baseline `color` source draws BT.601 limited-range yuv420p; the following scale (yuv420p->yuv420p, matrix change only) does not re-convert; "
                  "the stream is tagged bt709, so decoders read 601 data as 709.  Prediction (E01, patch reported_30506a): raw codes (79,144,112) -> RGB (45,78,107) when read as BT.709. "
                  "PRE-RUN DISCREPANCY: the reported decode was (44,78,102), blue 5 lower than H1 predicts, so H1 may be incomplete; E01 raw codes decide.",
            "H2": "inserting format=gbrp before the scaler makes the colour source draw RGB and the scaler perform a real RGB->BT.709 limited conversion: "
                  "raw codes within 1 of the BT.709 ideal for all 32 patches (criterion K1) and interior RGB error <= T_RGB_raw_interior (K2).",
            "H3": "neutral patches are unaffected by the matrix (Kr+Kg+Kb=1): baseline and fix agree on grays; errors concentrate in saturated patches.",
            "H4": "the PNG (rgb24) FILE branch is already correct in the baseline (E06 passes K1,K2) and format=gbrp changes nothing there (E07 vs E06 within 1 code).",
            "H5": "tag-only relabelling changes decoded RGB but not a single pixel: sha256(E08 raw) == sha256(E01 raw).",
            "H6": "gbrp applied to a source that is already BT.709 yuv420p is harmful: round-trip drift > 1 code for some patches (E11).",
            "H7": "rgb24 (E03), gbrp+flags (E04) and zscale (E05) are all within K1/K2; differences between them are quantisation-level."},
        "criteria": {
            "K1": "for each experiment and each patch: interior max |dY|,|dCb|,|dCr| vs BT.709 exact-rounded ideal <= T_YUV_codes",
            "K2": "interior max abs channel error of the arithmetic-oracle decode of the raw frame vs nominal RGB <= T_RGB_raw_interior",
            "K3": "interior max abs channel error of the ffmpeg-decoded mp4 vs nominal RGB <= T_RGB_mp4_interior",
            "K4": "E09 reproduces the symptom: ffmpeg-decoded interior max channel error >= T_symptom_min_error_baseline",
            "K5": "sha256(E08 raw) == sha256(E01 raw)",
            "K6": "E06 passes K1+K2; max |dYUV| between E06 and E07 over all pixels <= T_YUV_codes",
            "K8": "E11: drift of the gbrp-misapplied output versus the exact source is reported (no pass/fail); H6 is supported when max interior |dYUV| > T_YUV_codes",
            "boundary": "reported only (max/mean per route); compared with the exact-arithmetic aligned-edge floor; never a pass/fail"},
        "per_patch_predictions": pred,
        "stop_rules": ["a failing criterion stays failing; no experiment id is run twice", "a harness bug keeps its run and gets a new numbered experiment (E12 is the only reserved slot)",
                       "analysis bugs are fixed in analyze.py; the first analysis output is kept as measurements.run1-*.json if it was wrong"],
    }
    with open(os.path.join(HERE, "preregistration.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    print("ok", len(json.dumps(doc)), "bytes; ties:", ties, "floor max:", max(floor))


if __name__ == "__main__":
    main()
