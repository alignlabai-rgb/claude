"""OPTIONAL advisory diagnostic helper (a proposal; nothing in video_core imports or calls it).

`diagnose(text, ...)` returns findings about a caption string. It never changes, normalises, substitutes or rejects text:
the source characters stay the single source of truth, and the default renderer/validator behaviour is untouched.
Findings are heuristics from declared rules and font metrics; none of them is a legibility, correctness or
UAX #14 / JIS X 4051 conformance verdict.

Severity: "info" (worth knowing), "warn" (likely to look wrong or be mis-parsed by some consumer).
"""

from __future__ import annotations

import re
import unicodedata

try:  # the evidence directory is a flat script directory, not a package
    import kinsoku
except ImportError:  # pragma: no cover - only if imported as part of a package
    from . import kinsoku  # type: ignore[no-redef]

BIDI_CONTROLS = frozenset("‎‏؜‪‫‬‭‮⁦⁧⁨⁩")
LINE_BOUNDARY_NON_CC = frozenset("  ")
_MARKUP = re.compile(r"</?[A-Za-z][^<>\n]*>|\{\\[A-Za-z][^{}\n]*\}")
_TIMING_LINE = re.compile(r"^\s*\d{1,}:\d{2}:\d{2}[,.]\d{3}\s*-->")
_INDEX_LINE = re.compile(r"^\d+$")


def _is_variation_selector(ch: str) -> bool:
    cp = ord(ch)
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0x180B <= cp <= 0x180D


def _invisible_only(line: str) -> bool:
    return bool(line) and all(unicodedata.category(c) in ("Cf", "Zs", "Zl", "Zp") for c in line)


def line_width_units(line: str, font) -> tuple[int, int]:
    """(min_units, max_units) of advance widths in font units, summing hmtx advances without kerning/shaping.
    min treats combining marks (category Mn/Me) as zero-advance (what a shaper may do); max uses the raw hmtx advance.
    Unmapped characters count as .notdef (gid 0)."""
    lo = hi = 0
    for ch in line:
        adv = font.advance_units(font.gid(ord(ch)))
        hi += adv
        if unicodedata.category(ch) not in ("Mn", "Me"):
            lo += adv
    return lo, hi


def diagnose(text: str, *, font=None, font_size: int | None = None, canvas_width: int | None = None, box_border: int = 8) -> list:
    out: list = []

    def add(code, severity, detail, **extra):
        out.append({"code": code, "severity": severity, "detail": detail, **extra})

    if unicodedata.normalize("NFC", text) != text:
        add("NOT_NFC", "info", "text is not NFC (e.g. base + combining voiced mark); it is preserved as written, never normalised")
    if unicodedata.normalize("NFKC", text) != text:
        add("NFKC_WOULD_CHANGE", "info", "NFKC would change this text (halfwidth/fullwidth forms etc.); do not fold it to compare sources")
    lines = text.split("\n")
    for i, line in enumerate(lines, start=1):
        if _invisible_only(line):
            add("INVISIBLE_ONLY_LINE", "warn", f"line {i} contains only invisible/space/format characters", line=i)
        if _TIMING_LINE.match(line):
            add("TIMING_LINE_LOOKALIKE", "warn", f"line {i} looks like an SRT timing line", line=i)
        if i > 1 and _INDEX_LINE.match(line):
            add("INDEX_LINE_LOOKALIKE", "info", f"line {i} is only digits and may be read as a cue index by lenient parsers", line=i)
    if any(c in LINE_BOUNDARY_NON_CC for c in text):
        add("UNICODE_LINE_BOUNDARY", "warn", "U+2028/U+2029 are accepted (not Cc) but str.splitlines()-style consumers treat them as line breaks")
    if any(c in BIDI_CONTROLS for c in text):
        add("BIDI_CONTROL", "warn", "bidirectional control characters can reorder displayed text; horizontal Japanese is not reversed by reading order")
    if any(_is_variation_selector(c) for c in text):
        add("VARIATION_SELECTOR", "info", "variation selectors need cmap format 14 (or font mapping) support; production reads formats 4/12 only and will refuse them")
    if _MARKUP.search(text):
        add("MARKUP_LOOKALIKE", "info", "text contains <tag>/{\\override} shapes that some subtitle consumers interpret as styling")
    out.extend({"code": f["code"], "severity": "warn", "detail": f"{f['codepoint']} on line {f['line']} ({kinsoku.RULESET_ID})", "line": f["line"]}
               for f in kinsoku.check_lines(text))
    if font is not None:
        for ch in sorted({c for c in text if unicodedata.category(c) in ("Mn", "Me") and font.gid(ord(c))}):
            adv = font.advance_units(font.gid(ord(ch)))
            if adv:
                add("COMBINING_MARK_HAS_ADVANCE", "info",
                    f"U+{ord(ch):04X} has hmtx advance {adv}/{font.units_per_em} em. A shaper may zero it; in the one author render (drawtext + HarfBuzz, IPAGothic) the "
                    "mark was drawn attached to its base, so treat this as a width-estimate caveat, not a defect",
                    codepoint=f"U+{ord(ch):04X}")
        if font_size and canvas_width:
            for i, line in enumerate(lines, start=1):
                lo, hi = line_width_units(line, font)
                w_lo, w_hi = lo * font_size / font.units_per_em, hi * font_size / font.units_per_em
                if w_lo > canvas_width:
                    add("LINE_OVERFLOWS_CANVAS", "warn", f"line {i}: estimated width {w_lo:.0f}-{w_hi:.0f}px exceeds the {canvas_width}px canvas (drawtext does not wrap)", line=i,
                        width_px_min=round(w_lo, 1), width_px_max=round(w_hi, 1))
                elif w_hi > canvas_width - 2 * box_border:
                    add("LINE_NEARLY_FULL_WIDTH", "info", f"line {i}: estimated width up to {w_hi:.0f}px leaves less than the {box_border}px box border on each side", line=i,
                        width_px_min=round(w_lo, 1), width_px_max=round(w_hi, 1))
    return out
