"""Explicit synthetic Japanese caption corpus (definition). Every string is written with \\u escapes where a
character would otherwise be invisible or ambiguous. No real story, dialogue, asset or personal data.

`expect` is the *declared* production-validator expectation (what `video_core.load_episode` should do):
  ("accept",)                       -> validator accepts and the SRT carries the text byte-for-byte
  ("reject", "<issue code>")        -> validator documents a rejection with that code (episode.py)
Font-dependent outcomes (cmap coverage) are NOT declared here; they are measured per installed font.

`cls` is the author's classification vocabulary for what the *current default behaviour* is:
  SUPPORTED            accepted and preserved exactly; nothing to flag
  DOCUMENTED_REJECTION rejected with a stable, documented code
  INTEROP_CAVEAT       accepted and preserved, but a downstream consumer may treat it differently
  GAP                  accepted although it defeats a stated purpose (not an SRT-contract violation)
A "real defect" is only asserted when a measurement contradicts a stated contract (see FINDINGS in README.md).
"""

from __future__ import annotations

# (id, group, text, expect, cls, note)
CASES = [
    ("kana_hira_kata", "kana_kanji", "ひらがなカタカナ", ("accept",), "SUPPORTED", "hiragana + katakana, BMP"),
    ("kanji_plain", "kana_kanji", "日本語の字幕を検証する", ("accept",), "SUPPORTED", "kanji + okurigana"),
    ("long_single_line", "layout", "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。", ("accept",), "GAP",
     "31 characters on one line: no wrapping in drawtext; width vs canvas is not checked"),
    ("dakuten_precomposed", "combining", "がぎぐげご ばびぶべぼ ぱぴぷぺぽ", ("accept",), "SUPPORTED", "precomposed voiced/semi-voiced kana (NFC)"),
    ("dakuten_combining_nfd", "combining", "か\u3099き\u3099 は\u309a", ("accept",), "INTEROP_CAVEAT",
     "NFD: base + U+3099/U+309A; must stay NFD in the SRT (not silently composed)"),
    ("dakuten_spacing_marks", "combining", "か\u309b ふ\u309c", ("accept",), "SUPPORTED", "U+309B/U+309C spacing voiced marks (distinct from combining U+3099/U+309A)"),
    ("halfwidth_kana", "combining", "ﾊﾟｿｺﾝ ｶﾞｷﾞｸﾞ ｱｲｳ", ("accept",), "SUPPORTED", "halfwidth katakana + U+FF9E/U+FF9F; must NOT be NFKC-folded"),
    ("fullwidth_ascii", "combining", "ＡＢＣ１２３", ("accept",), "SUPPORTED", "fullwidth Latin/digits (NFKC would change them)"),
    ("astral_cjk_ext_b", "astral", "\U00020bb7野家 \U00029e3d", ("accept",), "SUPPORTED", "CJK Ext. B code points (U+20BB7, U+29E3D): cmap format 12 needed"),
    ("ivs_ideographic", "astral", "葛\U000e0100城 辻\U000e0101", ("accept",), "INTEROP_CAVEAT",
     "ideographic variation selectors U+E0100/U+E0101; sequence registration (IVD) not asserted"),
    ("vs_text_emoji", "astral", "\u2764\ufe0e \u2764\ufe0f", ("accept",), "INTEROP_CAVEAT", "VS15/VS16 after U+2764"),
    ("emoji_astral", "astral", "笑😀", ("accept",), "SUPPORTED", "U+1F600 (exercises a colour-emoji font absence)"),
    ("brackets_jp", "punctuation", "「こんにちは」『書名』（注）【見出し】〈引用〉《章》〔補〕［配列］｛集合｝", ("accept",), "SUPPORTED", "opening/closing brackets"),
    ("punct_jp_stops", "punctuation", "終わり。次へ、進む！本当？…‥・ー〜～", ("accept",), "SUPPORTED", "Japanese stops, ellipsis, middle dot, long-vowel mark, wave dashes"),
    ("punct_fullwidth_misc", "punctuation", "注：重要；確認！？", ("accept",), "SUPPORTED", "fullwidth colon/semicolon/exclamation/question"),
    ("punct_ascii_in_jp", "punctuation", "Hello, world! (OK) [1] {2} <3> 100% 3:30 \"q\" 'q' a/b", ("accept",), "SUPPORTED", "ASCII punctuation mixed in"),
    ("percent_directives", "escaping", "進捗100%{n}と%{localtime}と%%", ("accept",), "SUPPORTED", "drawtext expansion directives must stay literal (expansion=none)"),
    ("colon_quote_backslash", "escaping", "時刻 12:34:56 の「it's」と \"say\" と C:\\dir\\ファイル と \\n と \\\\", ("accept",), "SUPPORTED",
     "colon, both quotes, single/double backslash, literal backslash-n"),
    ("shell_meta", "escaping", "`$(id)` $HOME ; | & > < ' \"", ("accept",), "SUPPORTED", "shell/filtergraph metacharacters stay data"),
    ("two_lines", "layout", "一行目の字幕\n二行目の字幕", ("accept",), "SUPPORTED", "explicit LF line break"),
    ("kinsoku_start_stop", "kinsoku", "本日は晴天なり\n。続きは明日", ("accept",), "GAP", "second line starts with U+3002 (line-start prohibited)"),
    ("kinsoku_start_small_kana", "kinsoku", "これは\nっぽい例", ("accept",), "GAP", "second line starts with small kana U+3063"),
    ("kinsoku_start_long_vowel", "kinsoku", "コーヒ\nーを飲む", ("accept",), "GAP", "second line starts with U+30FC"),
    ("kinsoku_end_open", "kinsoku", "彼は「\n行く」と言った", ("accept",), "GAP", "first line ends with U+300C (line-end prohibited)"),
    ("kinsoku_split_ellipsis", "kinsoku", "待って…\n…そうか", ("accept",), "GAP", "two-em ellipsis U+2026 U+2026 split across the break"),
    ("kinsoku_clean_two_lines", "kinsoku", "「本日は晴天なり」\nと彼は言った。", ("accept",), "SUPPORTED", "control: no line-start/line-end violation"),
    ("markup_lookalike_html", "interop", "<i>斜体</i> と <b>太字</b>", ("accept",), "INTEROP_CAVEAT", "SRT consumers may interpret <i>/<b> as styling"),
    ("markup_lookalike_ass", "interop", "{\\an8}上寄せ", ("accept",), "INTEROP_CAVEAT", "ASS override look-alike {\\an8}"),
    ("timestamp_lookalike", "interop", "字幕\n00:00:01,000 --> 00:00:02,000", ("accept",), "INTEROP_CAVEAT", "a text line shaped like a cue timing line"),
    ("numeric_line", "interop", "字幕\n42", ("accept",), "INTEROP_CAVEAT", "a text line shaped like a cue index"),
    ("arrow_inline", "interop", "A --> B", ("accept",), "SUPPORTED", "'-->' inside text (also pinned by the default suite)"),
    ("ideographic_space", "whitespace", "日本語\u3000字幕\u3000テスト", ("accept",), "SUPPORTED", "U+3000 between words"),
    ("nbsp", "whitespace", "日本語\u00a0字幕", ("accept",), "SUPPORTED", "U+00A0"),
    ("trailing_space", "whitespace", "末尾に空白 ", ("accept",), "INTEROP_CAVEAT", "trailing ASCII space is preserved; consumers may trim"),
    ("zwsp_only_second_line", "whitespace", "字幕\n\u200b", ("accept",), "GAP", "second line is only U+200B: visually blank but not rejected as blank"),
    ("zwj_zwnj", "whitespace", "日\u200d本\u200c語", ("accept",), "INTEROP_CAVEAT", "zero-width joiner/non-joiner (category Cf)"),
    ("line_separator_u2028", "whitespace", "字幕\u2028二行目", ("accept",), "INTEROP_CAVEAT", "U+2028 is not Cc: accepted; str.splitlines() treats it as a line boundary"),
    ("paragraph_separator_u2029", "whitespace", "字幕\u2029二行目", ("accept",), "INTEROP_CAVEAT", "U+2029 likewise"),
    ("double_u2028", "whitespace", "字幕\u2028\u2028二行目", ("accept",), "INTEROP_CAVEAT",
     "two U+2028: a str.splitlines() consumer sees an EMPTY line (= end of cue); the validator's blank-line rule only looks at LF"),
    ("bom_inside", "whitespace", "字幕\ufeff末尾", ("accept",), "INTEROP_CAVEAT", "U+FEFF inside text (Cf), not at file start"),
    ("bidi_override", "bidi", "\u202e字幕\u202c", ("accept",), "INTEROP_CAVEAT", "RLO/PDF reorder display in bidi-aware renderers; NOT how Japanese horizontal text is read"),
    ("reject_tab", "rejected", "a\tb", ("reject", "text_control_char"), "DOCUMENTED_REJECTION", "TAB is Cc"),
    ("reject_crlf", "rejected", "a\r\nb", ("reject", "text_control_char"), "DOCUMENTED_REJECTION", "CR is Cc"),
    ("reject_nel", "rejected", "a\u0085b", ("reject", "text_control_char"), "DOCUMENTED_REJECTION", "U+0085 is Cc"),
    ("reject_esc", "rejected", "\u001b[31m赤", ("reject", "text_control_char"), "DOCUMENTED_REJECTION", "ESC is Cc"),
    ("reject_nul", "rejected", "a\u0000b", ("reject", "text_control_char"), "DOCUMENTED_REJECTION", "NUL is Cc"),
    ("reject_blank_line", "rejected", "a\n\nb", ("reject", "text_blank_line"), "DOCUMENTED_REJECTION", "empty line would end an SRT cue"),
    ("reject_ideographic_blank_line", "rejected", "字幕\n\u3000", ("reject", "text_blank_line"), "DOCUMENTED_REJECTION", "whitespace-only line (U+3000 is stripped by str.strip)"),
    ("reject_empty", "rejected", "", ("reject", "text_length"), "DOCUMENTED_REJECTION", "empty text"),
    ("reject_too_long", "rejected", "あ" * 513, ("reject", "text_length"), "DOCUMENTED_REJECTION", "513 characters > 512"),
    ("reject_lone_surrogate", "rejected", "a\ud800b", ("reject", "text_invalid_unicode"), "DOCUMENTED_REJECTION",
     "lone surrogate; arrives from JSON as the escape \\ud800 and must be rejected, never replaced"),
]

CORPUS_FPS = (24000, 1001)       # fractional on purpose
CORPUS_CANVAS = (640, 360)
CAPTION_WINDOW = 12              # frames per accepted case
GAP_EVERY = 5                    # a 3-frame gap after every 5th accepted case (adjacent otherwise)
GAP_FRAMES = 3

# Distinct, tiny, render-only corpus (never the six-second example). Every text must be covered by the chosen font.
RENDER_FPS = (30000, 1001)
RENDER_CANVAS = (320, 180)
RENDER_TOTAL = 54                # 54 / (30000/1001) = 1.8018 s
RENDER_FONT_SIZE = 20
RENDER_CAPTIONS = [
    # id, start, end (exclusive), text
    ("r1", 0, 13, "日本語の字幕、検証。"),
    ("r2", 13, 26, "「ぱ」ﾊﾟｿｺﾝ が か\u3099"),   # NFC が vs NFD か+U+3099, halfwidth kana
    ("r3", 31, 42, "彼は言った。\n「行こう」"),
    ("r4", 42, 54, "これは長い一行の字幕であり横幅を超えることを確認する検査文です。"),
]
