"""Shared, stdlib-only helpers for the 2026-11-05 colour-conversion study.

Nothing here imports video_core or launches a process.  Contents:
  * the synthetic 32-patch chart definition (deterministic, no external asset),
  * an exact-rational RGB<->YCbCr oracle (fractions.Fraction, round-half-up) for BT.709 and BT.601,
  * region masks (interior / transition / boundary) for a grid of flat patches,
  * a minimal deterministic PNG writer and a 3x5 bitmap font for contact-sheet labels.
"""
from __future__ import annotations

import struct
import zlib
from fractions import Fraction as F
from functools import lru_cache

PATCH = 32                    # patch edge in pixels (even, so every patch edge is also a 4:2:0 chroma-block edge)
COLS, ROWS = 8, 4
W, H = PATCH * COLS, PATCH * ROWS   # 256 x 128  (<= 256x256)
INTERIOR_MIN_DIST = 4         # interior: >= 4 px from every patch edge
BOUNDARY_MAX_DIST = 1         # boundary: <= 1 px from a patch edge (2 and 3 px = excluded transition band)

MATRICES = {                  # (Kr, Kb) luma coefficients, exact decimals from the standards
    "bt709": (F(2126, 10000), F(722, 10000)),
    "bt601": (F(299, 1000), F(114, 1000)),
}


def lcg_colors(seed: int, n: int):
    """Documented generator for the pseudo-random patches: x' = (1103515245*x + 12345) mod 2^31; byte = (x >> 16) & 255."""
    x, out = seed, []
    for _ in range(n):
        rgb = []
        for _c in range(3):
            x = (1103515245 * x + 12345) % (2 ** 31)
            rgb.append((x >> 16) & 255)
        out.append(tuple(rgb))
    return out


def patch_table():
    named = [
        # row 0: neutral ramp incl. both extremes
        ("black", 0x000000), ("gray10", 0x101010), ("gray20", 0x202020), ("gray40", 0x404040),
        ("gray80", 0x808080), ("grayC0", 0xC0C0C0), ("grayE0", 0xE0E0E0), ("white", 0xFFFFFF),
        # row 1: the two colours of the reported 6 s case + dark chromatic patches
        ("reported_30506a", 0x30506A), ("reported_6a5030", 0x6A5030), ("dark_102030", 0x102030), ("dark_301020", 0x301020),
        ("dark_203010", 0x203010), ("dark_0a0a30", 0x0A0A30), ("dark_300a0a", 0x300A0A), ("dark_0a300a", 0x0A300A),
        # row 2: saturated primaries / secondaries / mixes
        ("red", 0xFF0000), ("green", 0x00FF00), ("blue", 0x0000FF), ("cyan", 0x00FFFF),
        ("magenta", 0xFF00FF), ("yellow", 0xFFFF00), ("orange_ff8000", 0xFF8000), ("violet_8000ff", 0x8000FF),
        # row 3: mid tones, pastels and 3 seeded pseudo-random colours (not chosen by hand)
        ("mid_804040", 0x804040), ("mid_408040", 0x408040), ("mid_404080", 0x404080), ("skin_e0b090", 0xE0B090),
        ("sky_90c0e0", 0x90C0E0),
    ]
    rnd = lcg_colors(20261105, 3)
    items = [(n, ((v >> 16) & 255, (v >> 8) & 255, v & 255)) for n, v in named]
    items += [(f"lcg{i + 1}", c) for i, c in enumerate(rnd)]
    assert len(items) == COLS * ROWS
    out = []
    for i, (name, rgb) in enumerate(items):
        out.append({"index": i, "name": name, "rgb": list(rgb), "hex": "#%02x%02x%02x" % rgb,
                    "x": (i % COLS) * PATCH, "y": (i // COLS) * PATCH, "w": PATCH, "h": PATCH})
    return out


# ------------------------------------------------------------------ exact oracle
def _rnd(x: F) -> int:
    """round half up"""
    return (x + F(1, 2)).__floor__()


@lru_cache(maxsize=None)
def forward_exact(rgb, matrix: str):
    """8-bit full-range RGB -> exact (unrounded) limited-range (Y, Cb, Cr) as Fractions."""
    kr, kb = MATRICES[matrix]
    kg = 1 - kr - kb
    r, g, b = (F(v, 255) for v in rgb)
    y = kr * r + kg * g + kb * b
    cb = (b - y) / (2 * (1 - kb))
    cr = (r - y) / (2 * (1 - kr))
    return 16 + 219 * y, 128 + 224 * cb, 128 + 224 * cr


@lru_cache(maxsize=None)
def forward_codes(rgb, matrix: str):
    return tuple(_rnd(v) for v in forward_exact(rgb, matrix))


@lru_cache(maxsize=None)
def inverse_rgb(ycc, matrix: str):
    """limited-range 8-bit (Y, Cb, Cr) codes -> clipped 8-bit RGB (exact arithmetic, round half up)."""
    kr, kb = MATRICES[matrix]
    kg = 1 - kr - kb
    y = F(ycc[0] - 16, 219)
    cb = F(ycc[1] - 128, 224)
    cr = F(ycc[2] - 128, 224)
    r = y + 2 * (1 - kr) * cr
    b = y + 2 * (1 - kb) * cb
    g = (y - kr * r - kb * b) / kg
    return tuple(max(0, min(255, _rnd(255 * c))) for c in (r, g, b))


def quantisation_floor(patches, matrix="bt709"):
    """Error of the best possible 8-bit limited-range YCbCr (flat patch, exact rounding): per patch max channel error."""
    res = []
    for p in patches:
        rgb = tuple(p["rgb"])
        back = inverse_rgb(forward_codes(rgb, matrix), matrix)
        res.append(max(abs(a - b) for a, b in zip(back, rgb)))
    return res


def worst_case_bound(matrix="bt709"):
    """Analytic per-channel RGB error bound (code units, before output rounding) when Y/Cb/Cr are each within 0.5 code."""
    kr, kb = MATRICES[matrix]
    ky = F(255, 219)
    kcr, kcb = F(255, 224) * 2 * (1 - kr), F(255, 224) * 2 * (1 - kb)
    kg = 1 - kr - kb
    kcg = (kr * kcr + kb * kcb) / kg
    half = F(1, 2)
    return {"R": float(half * (ky + kcr)), "G": float(half * (ky + kcg)), "B": float(half * (ky + kcb))}


# ------------------------------------------------------------------ regions
def dist_to_patch_edge(x, y, pw=PATCH, ph=PATCH):
    xm, ym = x % pw, y % ph
    return min(xm, pw - 1 - xm, ym, ph - 1 - ym)


def region_of(x, y, pw=PATCH, ph=PATCH):
    d = dist_to_patch_edge(x, y, pw, ph)
    if d >= INTERIOR_MIN_DIST:
        return "interior"
    if d <= BOUNDARY_MAX_DIST:
        return "boundary"
    return "transition"


# ------------------------------------------------------------------ frames
def nominal_rgb_frame(patches):
    """rows of RGB tuples, the sharp reference chart"""
    rows = []
    for y in range(H):
        row = []
        for x in range(W):
            p = patches[(y // PATCH) * COLS + x // PATCH]
            row.append(tuple(p["rgb"]))
        rows.append(row)
    return rows


def ideal_yuv420p_bytes(patches, matrix="bt709"):
    """Exact-rational conversion of the chart to planar limited-range yuv420p (patch edges align with chroma blocks, so box = point)."""
    yp, up, vp = bytearray(W * H), bytearray(W * H // 4), bytearray(W * H // 4)
    for y in range(H):
        for x in range(W):
            p = patches[(y // PATCH) * COLS + x // PATCH]
            c = forward_codes(tuple(p["rgb"]), matrix)
            yp[y * W + x] = c[0]
            if x % 2 == 0 and y % 2 == 0:
                up[(y // 2) * (W // 2) + x // 2] = c[1]
                vp[(y // 2) * (W // 2) + x // 2] = c[2]
    return bytes(yp + up + vp)


def split_yuv420p(buf, w=W, h=H):
    n = w * h
    return buf[:n], buf[n:n + n // 4], buf[n + n // 4:n + n // 2]


# ------------------------------------------------------------------ PNG + tiny font
def png_bytes(rows, w=None, h=None) -> bytes:
    """8-bit RGB PNG, filter 0, zlib level 9, no ancillary chunks -> byte-deterministic for a given zlib build."""
    h = len(rows)
    w = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for px in row:
            raw.extend(px)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def png_read_rgb(data: bytes):
    """Minimal reader for the PNGs written above (filter 0 only); used by the fixture self-check."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = 1 + 3 * w
    rows = []
    for y in range(h):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0
        rows.append([tuple(line[1 + 3 * x:4 + 3 * x]) for x in range(w)])
    return rows


FONT = {  # 3x5 glyphs
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"], "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"], "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"], "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"], "E": ["111", "100", "110", "100", "111"], "N": ["110", "101", "101", "101", "101"],
    "-": ["000", "000", "111", "000", "000"], " ": ["000"] * 5,
    "A": ["010", "101", "111", "101", "101"], "B": ["110", "101", "110", "101", "110"], "R": ["110", "101", "110", "101", "101"],
    "O": ["111", "101", "101", "101", "111"], "M": ["101", "111", "111", "101", "101"], "F": ["111", "100", "110", "100", "100"],
    "P": ["111", "101", "111", "100", "100"], "G": ["111", "100", "101", "101", "111"], "D": ["110", "101", "101", "101", "110"],
    "X": ["101", "101", "010", "101", "101"], "Y": ["101", "101", "010", "010", "010"], "U": ["101", "101", "101", "101", "111"],
}


def draw_text(img, x0, y0, text, scale=2, color=(255, 255, 255)):
    for ci, ch in enumerate(text):
        g = FONT.get(ch.upper(), FONT[" "])
        for gy, line in enumerate(g):
            for gx, bit in enumerate(line):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            yy, xx = y0 + gy * scale + dy, x0 + (ci * 4 + gx) * scale + dx
                            if 0 <= yy < len(img) and 0 <= xx < len(img[0]):
                                img[yy][xx] = color
