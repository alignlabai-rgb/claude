"""Pixel- and glyph-level measurement helpers (pure Python on bytes; Pillow only where stated and optional).

Everything here is a MACHINE CHECK of a narrow property. None of it establishes legibility, typographic correctness or
aesthetic quality. Frames are the decoded luma (Y) plane of limited-range yuv420p: black ~16, white ~235.
"""

from __future__ import annotations

INK_THRESHOLD = 64   # Y above this counts as caption ink (black bg=16, white text=235)


def have_pil() -> bool:
    try:
        import PIL.ImageFont  # noqa: F401
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ frame statistics ------------------------------

def ink_stats(y: bytes, w: int, h: int, thr: int = INK_THRESHOLD) -> dict:
    if len(y) != w * h:
        raise ValueError("luma plane has the wrong size")
    count = 0
    x0 = y0 = None
    x1 = y1 = -1
    rows = [0] * h
    for r in range(h):
        base = r * w
        row = y[base:base + w]
        c = 0
        first = last = None
        for x, v in enumerate(row):
            if v > thr:
                c += 1
                if first is None:
                    first = x
                last = x
        if c:
            rows[r] = c
            count += c
            if y0 is None:
                y0 = r
            y1 = r
            x0 = first if x0 is None else min(x0, first)
            x1 = max(x1, last)
    if count == 0:
        return {"ink": 0, "bbox": None, "row_bands": 0, "max_luma": max(y) if y else 0}
    bands, in_band = 0, False
    for c in rows:
        if c and not in_band:
            bands += 1
        in_band = bool(c)
    return {"ink": count, "bbox": [x0, y0, x1, y1], "row_bands": bands, "max_luma": max(y)}


def binarize(y: bytes, thr: int = INK_THRESHOLD) -> bytes:
    return bytes(1 if v > thr else 0 for v in y)


def hamming_fraction(a: bytes, b: bytes) -> float:
    """Differing ink pixels / union of ink pixels of two binary planes (0 = identical, 1 = disjoint)."""
    if len(a) != len(b):
        raise ValueError("size mismatch")
    union = diff = 0
    for p, q in zip(a, b):
        if p or q:
            union += 1
            if p != q:
                diff += 1
    return diff / union if union else 0.0


def bbox_close(a, b, tol: int = 1) -> bool:
    return a is not None and b is not None and all(abs(p - q) <= tol for p, q in zip(a, b))


def classify_sequence(frames: list, w: int, h: int, windows: dict, thr: int = INK_THRESHOLD) -> dict:
    """Compare per-frame visibility/identity with the oracle.

    frames: list of Y planes. windows: {caption_id: frozenset(frame indices it should be visible on)} (disjoint windows).
    Template for a caption = binary image of the *middle* frame of its oracle window (not a boundary frame).
    Returns per-frame records and the list of violations:
      - blank_expected_but_ink   : frame outside every window but showing ink  (bleed / early start)
      - ink_expected_but_blank   : frame inside a window but blank              (late start / early end)
      - wrong_caption            : frame inside window A closer to the template of another caption B
    """
    templates = {}
    for cid, fs in windows.items():
        mid = sorted(fs)[len(fs) // 2]
        templates[cid] = binarize(frames[mid], thr)
    owner = {}
    for cid, fs in windows.items():
        for i in fs:
            if i in owner:
                raise ValueError("windows overlap; this classifier needs disjoint windows")
            owner[i] = cid
    recs, violations = [], []
    for i, y in enumerate(frames):
        st = ink_stats(y, w, h, thr)
        best, best_d = None, None
        if st["ink"]:
            b = binarize(y, thr)
            for cid, t in templates.items():
                d = hamming_fraction(b, t)
                if best_d is None or d < best_d:
                    best, best_d = cid, d
        rec = {"frame": i, "expected": owner.get(i), "ink": st["ink"], "nearest": best, "nearest_distance": None if best_d is None else round(best_d, 4)}
        recs.append(rec)
        if owner.get(i) is None and st["ink"]:
            violations.append({"frame": i, "kind": "blank_expected_but_ink", "nearest": best})
        elif owner.get(i) is not None and not st["ink"]:
            violations.append({"frame": i, "kind": "ink_expected_but_blank", "expected": owner[i]})
        elif owner.get(i) is not None and best != owner[i]:
            violations.append({"frame": i, "kind": "wrong_caption", "expected": owner[i], "nearest": best})
    return {"frames": recs, "violations": violations, "ok": not violations}


# ------------------------------------------------------------------ glyph raster (Pillow, optional) ---------------

def glyph_raster_report(font_path: str, codepoints: list, size: int, gid_of) -> dict:
    """Rasterise each code point with FreeType via Pillow's BASIC layout and compare with the font's own .notdef drawing.

    A code point with cmap coverage can still be drawn blank (space-like), identical to .notdef (tofu), or as a visible
    glyph that is not the character (e.g. Unifont draws hex boxes for format characters). Only the first two are
    machine-detectable here; 'ink' means 'something drawn', NOT 'the right glyph'.
    """
    from PIL import ImageFont
    font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)

    def bm(cp):
        m = font.getmask(chr(cp))
        return tuple(m.size), bytes(m)

    unmapped = next(cp for cp in range(0xE000, 0xF8FF) if not gid_of(cp))   # PUA probe -> .notdef drawing
    notdef = bm(unmapped)
    rows = {}
    for cp in codepoints:
        if not gid_of(cp):
            continue
        sz, data = bm(cp)
        ink = sum(1 for b in data if b)
        rows[f"U+{cp:04X}"] = {"ink_pixels": ink, "same_as_notdef": (sz, data) == notdef, "blank": ink == 0, "bitmap_size": list(sz)}
    return {"size_px": size, "notdef_probe": f"U+{unmapped:04X}", "notdef_ink_pixels": sum(1 for b in notdef[1] if b),
            "engine": "PIL BASIC (FreeType), no shaping", "per_codepoint": rows}


def reversed_vs_forward(y: bytes, w: int, h: int, font_path: str, size: int, text: str, bbox, search: int = 3, thr: int = INK_THRESHOLD) -> dict:
    """Does the drawn line look like `text` left-to-right rather than reversed?  Heuristic: IoU of the binarised crop at `bbox`
    against a Pillow rendering of the string and of the string reversed (best horizontal shift within +-`search` px, same top-left).
    Different rasteriser/hinting than drawtext, so absolute IoU is modest; the *comparison* is what is reported."""
    from PIL import Image, ImageDraw, ImageFont
    x0, y0, x1, y1 = bbox
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    crop = [[1 if y[(y0 + r) * w + x0 + c] > thr else 0 for c in range(cw)] for r in range(ch)]
    font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)

    def render(s):
        img = Image.new("L", (cw + 2 * search + 8, ch + 16), 0)
        ImageDraw.Draw(img).text((search + 2, 4), s, fill=255, font=font)
        px = img.load()
        pts = [(x, yy) for yy in range(img.size[1]) for x in range(img.size[0]) if px[x, yy] > 128]
        if not pts:
            return None
        minx, miny = min(p[0] for p in pts), min(p[1] for p in pts)
        return {(x - minx, yy - miny) for x, yy in pts}

    ref_ink = {(c, r) for r in range(ch) for c in range(cw) if crop[r][c]}

    def best_iou(s):
        cand = render(s)
        if not cand:
            return 0.0
        best = 0.0
        for dx in range(-search, search + 1):
            for dy in range(-search, search + 1):
                moved = {(x + dx, yy + dy) for x, yy in cand}
                inter = len(moved & ref_ink)
                union = len(moved | ref_ink)
                if union:
                    best = max(best, inter / union)
        return best

    fwd, rev = best_iou(text), best_iou(text[::-1])
    return {"forward_iou": round(fwd, 4), "reversed_iou": round(rev, 4), "forward_wins": fwd > rev}
