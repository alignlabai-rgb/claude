"""Japanese line-start / line-end diagnostic with a DECLARED SMALL rule set.

This is NOT an implementation of Unicode UAX #14 and NOT JIS X 4051 (which also has a 'loose' and a 'strict' profile and
many more rules). It only inspects the line breaks the author wrote as '\\n' in a caption (the renderer never wraps text
itself), and reports characters from the small sets below that land where traditional kinsoku shori avoids them.
Each set member's UAX #14 line-break class (Unicode 15.0.0, taken from a LOCAL Perl 5 Unicode database, see
uax14_local_probe.json) is recorded as a consistency check on the *declaration*, not as proof of conformance.

Reading order note: manga pages are read right-to-left between panels; this does not reverse the character order of a
horizontal Japanese subtitle, and nothing here reorders text.
"""

from __future__ import annotations

# Line-START prohibited: closing brackets, stops, exclamation/question, small kana, prolonged sound mark, iteration marks.
CLOSE_BRACKETS = "）］｝〕〉》」』】〙〗｠)]}"
STOPS_COMMA_PERIOD = "、。，．"
EXCL_QUEST = "！？"
NONSTARTER_MISC = "・：；"
SMALL_KANA = "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ"
PROLONGED_AND_ITERATION = "ーヽヾゝゞ々〻"
DASH_WAVE = "〜"          # U+301C WAVE DASH
LINE_START_PROHIBITED = frozenset(CLOSE_BRACKETS + STOPS_COMMA_PERIOD + EXCL_QUEST + NONSTARTER_MISC + SMALL_KANA + PROLONGED_AND_ITERATION + DASH_WAVE)

# Line-END prohibited: opening brackets.
LINE_END_PROHIBITED = frozenset("（［｛〔〈《「『【〘〖｟([{")

# Do not split between two identical code points of these (two-dot / three-dot leaders, two-em dash) across a break.
INSEPARABLE_REPEATS = frozenset("…‥―")

RULESET_ID = "jpcap-kinsoku-small/1"
RULESET_DECLARATION = {
    "id": RULESET_ID,
    "line_start_prohibited": "".join(sorted(LINE_START_PROHIBITED)),
    "line_end_prohibited": "".join(sorted(LINE_END_PROHIBITED)),
    "inseparable_repeat_at_break": "".join(sorted(INSEPARABLE_REPEATS)),
    "not_claimed": [
        "full Unicode UAX #14 line-breaking conformance",
        "JIS X 4051 conformance (strict or loose)",
        "hanging punctuation, burasagari, or proportional-width adjustments",
        "automatic wrapping (the renderer does not wrap)",
        "vertical writing",
    ],
}


def check_lines(text: str) -> list:
    """Findings for the explicit '\\n' breaks of `text`. Each: {code, line (1-based, the line that is affected), char, codepoint}.
    Empty lines are skipped (the validator already rejects them)."""
    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        if not line:
            continue
        first, last = line[0], line[-1]
        if i > 0 and first in LINE_START_PROHIBITED:
            out.append({"code": "LINE_START_PROHIBITED", "line": i + 1, "char": first, "codepoint": f"U+{ord(first):04X}"})
        if i < len(lines) - 1 and last in LINE_END_PROHIBITED:
            out.append({"code": "LINE_END_PROHIBITED", "line": i + 1, "char": last, "codepoint": f"U+{ord(last):04X}"})
        if i < len(lines) - 1 and lines[i + 1] and last in INSEPARABLE_REPEATS and lines[i + 1][0] == last:
            out.append({"code": "SPLIT_INSEPARABLE", "line": i + 1, "char": last, "codepoint": f"U+{ord(last):04X}"})
    return out
