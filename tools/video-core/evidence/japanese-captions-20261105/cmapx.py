"""Independent sfnt reader for the Japanese-caption evidence (does NOT import video_core).

Reads: table directory, name (family/full/PostScript), head.unitsPerEm, maxp.numGlyphs, hhea/hmtx advance
widths, and every cmap subtable of formats 0, 4, 6, 10, 12, 13 and 14 (variation sequences). It exists to give a
second opinion on `video_core.fontcheck` (formats 4/12 only) and to report formats the production reader ignores.
Scope: single-font sfnt (TrueType 0x00010000 / 'true' / 'OTTO'); a '.ttc' is detected and reported, not parsed.
Cmap coverage is NOT proof that the drawn glyph is right; see jpcap_eval.py for the raster checks.
"""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, field

TTC_TAG = b"ttcf"
SFNT_TAGS = (b"\x00\x01\x00\x00", b"OTTO", b"true")


class SfntError(ValueError):
    pass


def _u16(d: bytes, o: int) -> int:
    if o < 0 or o + 2 > len(d):
        raise SfntError("truncated u16")
    return struct.unpack_from(">H", d, o)[0]


def _i16(d: bytes, o: int) -> int:
    if o < 0 or o + 2 > len(d):
        raise SfntError("truncated i16")
    return struct.unpack_from(">h", d, o)[0]


def _u32(d: bytes, o: int) -> int:
    if o < 0 or o + 4 > len(d):
        raise SfntError("truncated u32")
    return struct.unpack_from(">I", d, o)[0]


@dataclass
class Subtable:
    platform: int
    encoding: int
    offset: int
    fmt: int
    unicode_capable: bool
    ranges: list = field(default_factory=list)     # [(start, end, gid_at_start, kind)] kind: 'lin' (gid+offset) | 'const' (format 13)
    gids: dict = field(default_factory=dict)       # explicit cp -> gid (formats 0/4/6/10 are expanded; sparse ones only)
    uvs: dict = field(default_factory=dict)        # format 14: vs -> {"default": [(start,end)], "non": {base: gid}}

    def lookup(self, cp: int) -> int:
        if self.fmt == 14:
            return 0
        if self.gids:
            return self.gids.get(cp, 0)
        starts = [r[0] for r in self.ranges]
        i = bisect.bisect_right(starts, cp) - 1
        if i >= 0:
            s, e, g, kind = self.ranges[i]
            if s <= cp <= e:
                return g if kind == "const" else g + (cp - s)
        return 0


@dataclass
class Font:
    path_hint: str
    size: int
    tag: bytes
    tables: dict
    names: dict
    units_per_em: int
    num_glyphs: int
    subtables: list
    advances: list          # per glyph id (len numberOfHMetrics; tail repeats the last advance)
    num_hmetrics: int

    def unicode_subtables(self):
        return [s for s in self.subtables if s.unicode_capable and s.fmt != 14]

    def gid(self, cp: int) -> int:
        """Best Unicode mapping across all Unicode-capable (non-14) subtables; 0 = unmapped."""
        for s in self.unicode_subtables():
            g = s.lookup(cp)
            if g:
                return g
        return 0

    def advance_units(self, gid: int) -> int:
        if not self.advances:
            raise SfntError("no hmtx")
        return self.advances[min(gid, len(self.advances) - 1)]

    def variation(self, base: int, vs: int):
        """Format-14 answer: 'default' (use cmap glyph), 'non-default' (own glyph) or None (sequence not in font)."""
        for s in self.subtables:
            if s.fmt != 14:
                continue
            ent = s.uvs.get(vs)
            if not ent:
                continue
            for a, b in ent["default"]:
                if a <= base <= b:
                    return "default"
            if base in ent["non"]:
                return "non-default"
        return None


def _parse_subtable(d: bytes, cmap_off: int, platform: int, encoding: int, sub_off: int) -> Subtable:
    base = cmap_off + sub_off
    fmt = _u16(d, base)
    uni = platform == 0 or (platform == 3 and encoding in (1, 10))
    st = Subtable(platform, encoding, sub_off, fmt, uni)
    if fmt == 0:
        for cp in range(256):
            if d[base + 6 + cp]:
                st.gids[cp] = d[base + 6 + cp]
    elif fmt == 4:
        segx2 = _u16(d, base + 6)
        seg = segx2 // 2
        end_o = base + 14
        start_o = end_o + segx2 + 2
        delta_o = start_o + segx2
        range_o = delta_o + segx2
        for i in range(seg):
            e, s = _u16(d, end_o + 2 * i), _u16(d, start_o + 2 * i)
            dl, ro = _u16(d, delta_o + 2 * i), _u16(d, range_o + 2 * i)
            if s > e:
                raise SfntError("format 4 segment start > end")
            for cp in range(s, e + 1):
                if cp == 0xFFFF:
                    continue
                if ro == 0:
                    g = (cp + dl) & 0xFFFF
                else:
                    g = _u16(d, range_o + 2 * i + ro + 2 * (cp - s))
                    if g:
                        g = (g + dl) & 0xFFFF
                if g:
                    st.gids[cp] = g
    elif fmt == 6:
        first, count = _u16(d, base + 6), _u16(d, base + 8)
        for k in range(count):
            g = _u16(d, base + 10 + 2 * k)
            if g:
                st.gids[first + k] = g
    elif fmt == 10:
        first, count = _u32(d, base + 12), _u32(d, base + 16)
        if count > 1_000_000:
            raise SfntError("implausible format 10 count")
        for k in range(count):
            g = _u16(d, base + 20 + 2 * k)
            if g:
                st.gids[first + k] = g
    elif fmt in (12, 13):
        n = _u32(d, base + 12)
        if n > 1_000_000:
            raise SfntError("implausible group count")
        for k in range(n):
            o = base + 16 + 12 * k
            s, e, g = _u32(d, o), _u32(d, o + 4), _u32(d, o + 8)
            if e < s:
                raise SfntError("group end < start")
            st.ranges.append((s, e, g, "lin" if fmt == 12 else "const"))
        st.ranges.sort()
    elif fmt == 14:
        n = _u32(d, base + 6)
        for k in range(n):
            rec = base + 10 + 11 * k
            vs = (d[rec] << 16) | (d[rec + 1] << 8) | d[rec + 2]
            dflt, non = _u32(d, rec + 3), _u32(d, rec + 7)
            ent = {"default": [], "non": {}}
            if dflt:
                o = base + dflt
                for j in range(_u32(d, o)):
                    r = o + 4 + 4 * j
                    s = (d[r] << 16) | (d[r + 1] << 8) | d[r + 2]
                    ent["default"].append((s, s + d[r + 3]))
            if non:
                o = base + non
                for j in range(_u32(d, o)):
                    r = o + 4 + 5 * j
                    u = (d[r] << 16) | (d[r + 1] << 8) | d[r + 2]
                    ent["non"][u] = _u16(d, r + 3)
            st.uvs[vs] = ent
    return st


def _read_names(d: bytes, tables: dict) -> dict:
    out = {}
    if "name" not in tables:
        return out
    off, _ln = tables["name"]
    count, str_off = _u16(d, off + 2), _u16(d, off + 4)
    for i in range(count):
        r = off + 6 + 12 * i
        plat, enc, lang, nid, ln, so = (_u16(d, r + 2 * k) for k in range(6))
        if nid not in (1, 2, 4, 5, 6, 16, 17):
            continue
        raw = d[off + str_off + so: off + str_off + so + ln]
        try:
            text = raw.decode("utf-16-be") if plat in (0, 3) else raw.decode("mac_roman")
        except UnicodeDecodeError:
            continue
        key = {1: "family", 2: "subfamily", 4: "full", 5: "version", 6: "postscript", 16: "typographic_family", 17: "typographic_subfamily"}[nid]
        # prefer Windows English (3/1/0x409), else first seen
        if key not in out or (plat == 3 and lang == 0x409):
            out[key] = text
    return out


def read_font(data: bytes, path_hint: str = "") -> Font:
    if len(data) < 12:
        raise SfntError("too small")
    tag = data[:4]
    if tag == TTC_TAG:
        raise SfntError("font collection (.ttc): not parsed by this reader (production also rejects it)")
    if tag not in SFNT_TAGS:
        raise SfntError(f"not sfnt: {tag!r}")
    n = _u16(data, 4)
    tables = {}
    for i in range(n):
        rec = 12 + 16 * i
        t = data[rec:rec + 4].decode("latin-1")
        off, ln = _u32(data, rec + 8), _u32(data, rec + 12)
        if off + ln > len(data):
            raise SfntError(f"table {t} out of bounds")
        tables[t] = (off, ln)
    if "cmap" not in tables:
        raise SfntError("no cmap")
    upem = _u16(data, tables["head"][0] + 18) if "head" in tables else 0
    ng = _u16(data, tables["maxp"][0] + 4) if "maxp" in tables else 0
    nh, adv = 0, []
    if "hhea" in tables and "hmtx" in tables:
        nh = _u16(data, tables["hhea"][0] + 34)
        o = tables["hmtx"][0]
        adv = [_u16(data, o + 4 * i) for i in range(nh)]
    co, _cl = tables["cmap"]
    subs = []
    for i in range(_u16(data, co + 2)):
        rec = co + 4 + 8 * i
        subs.append(_parse_subtable(data, co, _u16(data, rec), _u16(data, rec + 2), _u32(data, rec + 4)))
    return Font(path_hint, len(data), tag, tables, _read_names(data, tables), upem, ng, subs, adv, nh)


def describe_subtables(f: Font) -> list:
    out = []
    for s in f.subtables:
        mapped = len(s.gids) if s.gids else sum(e - b + 1 for b, e, _g, _k in s.ranges)
        out.append({"platform": s.platform, "encoding": s.encoding, "format": s.fmt, "unicode": s.unicode_capable,
                    "production_reads": bool(s.unicode_capable and s.fmt in (4, 12)),
                    "mapped_code_points": mapped if s.fmt != 14 else None,
                    "variation_selectors": sorted(f"U+{v:04X}" for v in s.uvs) if s.fmt == 14 else None})
    return out
