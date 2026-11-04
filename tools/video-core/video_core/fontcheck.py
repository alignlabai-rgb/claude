"""Minimal stdlib TrueType/OpenType cmap reader.

Purpose: before burn-in, verify that every code point of every caption has a
non-zero glyph id in a *provided local font*, so a missing glyph is rejected
instead of being silently drawn as a box. This proves cmap coverage only; it does
not prove shaping, hinting or that the renderer displays the text correctly.
Supported: single-font sfnt files, cmap formats 4 and 12. TTC is rejected.
"""

from __future__ import annotations

import struct

from .errors import Issue, ValidationFailed


class FontError(ValueError):
    pass


def _u16(data: bytes, off: int) -> int:
    if off < 0 or off + 2 > len(data):
        raise FontError("truncated font data")
    return struct.unpack_from(">H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    if off < 0 or off + 4 > len(data):
        raise FontError("truncated font data")
    return struct.unpack_from(">I", data, off)[0]


def _cmap_table(data: bytes) -> tuple[int, int]:
    if len(data) < 12:
        raise FontError("font too small")
    tag = data[:4]
    if tag == b"ttcf":
        raise FontError("font collections (.ttc) are not supported")
    if tag not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise FontError("not a TrueType/OpenType font")
    num_tables = _u16(data, 4)
    if num_tables == 0 or num_tables > 512:
        raise FontError("implausible table count")
    for i in range(num_tables):
        rec = 12 + 16 * i
        if rec + 16 > len(data):
            raise FontError("truncated table directory")
        if data[rec : rec + 4] == b"cmap":
            off, length = _u32(data, rec + 8), _u32(data, rec + 12)
            if off + length > len(data) or length < 4:
                raise FontError("cmap table out of bounds")
            return off, length
    raise FontError("font has no cmap table")


def _format4_has(data: bytes, base: int, cp: int) -> bool:
    if cp > 0xFFFF:
        return False
    seg_x2 = _u16(data, base + 6)
    seg = seg_x2 // 2
    end_off = base + 14
    start_off = end_off + seg_x2 + 2
    delta_off = start_off + seg_x2
    range_off = delta_off + seg_x2
    for i in range(seg):
        end = _u16(data, end_off + 2 * i)
        if cp > end:
            continue
        start = _u16(data, start_off + 2 * i)
        if cp < start:
            return False
        delta = _u16(data, delta_off + 2 * i)
        ro = _u16(data, range_off + 2 * i)
        if ro == 0:
            gid = (cp + delta) & 0xFFFF
        else:
            addr = range_off + 2 * i + ro + 2 * (cp - start)
            gid = _u16(data, addr)
            if gid:
                gid = (gid + delta) & 0xFFFF
        return gid != 0
    return False


def _format12_has(data: bytes, base: int, cp: int) -> bool:
    n = _u32(data, base + 12)
    if n > 1_000_000:
        raise FontError("implausible group count")
    for i in range(n):
        g = base + 16 + 12 * i
        start, end, gid = _u32(data, g), _u32(data, g + 4), _u32(data, g + 8)
        if start <= cp <= end:
            return gid + (cp - start) != 0
    return False


def missing_codepoints(font_bytes: bytes, text_values: list[str]) -> list[int]:
    """Code points (excluding '\\n') used by `text_values` that the font cannot map."""
    off, _length = _cmap_table(font_bytes)
    n_sub = _u16(font_bytes, off + 2)
    subtables = []
    for i in range(n_sub):
        rec = off + 4 + 8 * i
        platform, encoding, sub_off = _u16(font_bytes, rec), _u16(font_bytes, rec + 2), _u32(font_bytes, rec + 4)
        if platform == 0 or (platform == 3 and encoding in (1, 10)):
            base = off + sub_off
            fmt = _u16(font_bytes, base)
            if fmt in (4, 12):
                subtables.append((fmt, base))
    if not subtables:
        raise FontError("no supported Unicode cmap subtable (formats 4 or 12)")
    needed = sorted({ord(ch) for text in text_values for ch in text if ch != "\n"})
    missing = []
    for cp in needed:
        covered = False
        for fmt, base in subtables:
            has = _format4_has(font_bytes, base, cp) if fmt == 4 else _format12_has(font_bytes, base, cp)
            if has:
                covered = True
                break
        if not covered:
            missing.append(cp)
    return missing


def require_glyph_coverage(font_bytes: bytes, text_values: list[str], ptr: str = "font") -> None:
    try:
        missing = missing_codepoints(font_bytes, text_values)
    except FontError as exc:
        raise ValidationFailed("font cannot be used", [Issue("font_unusable", ptr, str(exc))]) from None
    if missing:
        shown = ", ".join(f"U+{cp:04X}" for cp in missing[:10])
        more = "" if len(missing) <= 10 else f" (+{len(missing) - 10} more)"
        raise ValidationFailed(
            "font lacks glyphs for caption text",
            [Issue("font_missing_glyphs", ptr, f"font has no glyph for {shown}{more}; burn-in refused")],
        )
