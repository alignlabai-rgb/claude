#!/usr/bin/env python3
"""Builds small comparison PNG contact sheets from the stored experiment outputs (stdlib only; no process).
The original bytes (raw/*.yuv, raw/*.mp4, raw/*.mp4.rgb24) remain the evidence; these sheets are views of them.
Panels are labelled E01.. (experiment id); NOM = the nominal sharp chart.  2x nearest-neighbour zoom.  Diagnostic frames, not a quality verdict."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_lib as L  # noqa: E402

OUT = os.path.join(HERE, "contact_sheets")
ZOOM, LABEL_H, GAP = 2, 14, 6


def rd(p):
    with open(os.path.join(HERE, p), "rb") as fh:
        return fh.read()


def oracle_rgb_rows(raw, w, h):
    yp, up, vp = L.split_yuv420p(raw, w, h)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            c = (y // 2) * (w // 2) + x // 2
            row.append(L.inverse_rgb((yp[y * w + x], up[c], vp[c]), "bt709"))
        rows.append(row)
    return rows


def rgb24_rows(buf, w, h):
    return [[tuple(buf[(y * w + x) * 3:(y * w + x) * 3 + 3]) for x in range(w)] for y in range(h)]


def err_rows(rows, nominal, gain=40):
    out = []
    for r, n in zip(rows, nominal):
        out.append([(lambda e: (e, e, e))(min(255, gain * max(abs(a - b) for a, b in zip(p, q)))) for p, q in zip(r, n)])
    return out


def sheet(panels, cols, w, h, name):
    pw, ph = w * ZOOM, h * ZOOM + LABEL_H
    nrows = (len(panels) + cols - 1) // cols
    W = cols * pw + (cols + 1) * GAP
    H = nrows * ph + (nrows + 1) * GAP
    img = [[(40, 40, 40)] * W for _ in range(H)]
    for k, (label, rows) in enumerate(panels):
        ox, oy = GAP + (k % cols) * (pw + GAP), GAP + (k // cols) * (ph + GAP)
        L.draw_text(img, ox + 2, oy + 2, label, scale=2)
        for y in range(h):
            for dy in range(ZOOM):
                line = img[oy + LABEL_H + y * ZOOM + dy]
                for x in range(w):
                    px = rows[y][x]
                    for dx in range(ZOOM):
                        line[ox + x * ZOOM + dx] = px
    os.makedirs(OUT, exist_ok=True)
    data = L.png_bytes(img)
    with open(os.path.join(OUT, name), "wb") as fh:
        fh.write(data)
    return name, len(data)


def main():
    patches = json.load(open(os.path.join(HERE, "fixtures/patches.json")))["patches"]
    nom = L.nominal_rgb_frame(patches)
    w, h = L.W, L.H
    order = ["E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08", "E12"]
    raw = {e: oracle_rgb_rows(rd(f"raw/{e}.yuv"), w, h) for e in order}
    mp4 = {e: rgb24_rows(rd(f"raw/{e}.mp4.rgb24"), w, h) for e in order}
    made = [
        sheet([("NOM", nom)] + [(e, raw[e]) for e in order], 3, w, h, "A_raw_frames_arithmetic_decode.png"),
        sheet([(e, err_rows(raw[e], nom)) for e in order], 3, w, h, "B_raw_error_x40_vs_nominal.png"),
        sheet([("NOM", nom)] + [(e, mp4[e]) for e in order], 3, w, h, "C_mp4_frames_ffmpeg_decode.png"),
        sheet([(e, err_rows(mp4[e], nom)) for e in order], 3, w, h, "D_mp4_error_x40_vs_nominal.png"),
    ]
    fw, fh = 256, 144
    flat_nom = [[(0x30, 0x50, 0x6A)] * fw for _ in range(fh)]
    fp = [("NOM", flat_nom)]
    for e in ("E09", "E10"):
        fp.append((e + " RAW", oracle_rgb_rows(rd(f"raw/{e}.yuv"), fw, fh)))
        fp.append((e + " MP4", rgb24_rows(rd(f"raw/{e}.mp4.rgb24"), fw, fh)))
    made.append(sheet(fp, 3, fw, fh, "E_flat_30506a_E09_E10.png"))
    with open(os.path.join(OUT, "README.txt"), "w") as fh:
        fh.write("Panel order A/C: NOM, E01, E02, E03, E04, E05, E06, E07, E08, E12 (3 columns, row-major).  B/D: same order without NOM.\n"
                 "A,B decode the stored raw yuv420p with the exact-rational BT.709 limited oracle (nearest chroma); C,D are the stored ffmpeg rgb24 readbacks of the mp4.\n"
                 "B/D: grey level = 40 x max channel error (255 = error >= 7). E: flat 256x144 #30506a, baseline (E09) vs +format=gbrp (E10).\n"
                 "IMPORTANT: E01-E05, E08, E12 build the chart from 32 separately converted flat sources, so their patch seams are unrealistically clean;\n"
                 "only E06/E07 (PNG through swscale) show real chroma-subsampling smear at seams.  Not a perceptual quality verdict.\n")
    print(made)


if __name__ == "__main__":
    main()
