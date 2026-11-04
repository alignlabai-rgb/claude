# Author visual observations (NOT machine checks, NOT independent review)

Status: **pending independent human legibility / creative acceptance.** Nothing below is a pass/fail verdict.
Author = the same session that wrote the corpus and the checks. Observed by looking at `contact_sheet_frames_13_31_53.png`
(decoded frames 13, 31, 53 of the one render; 2x nearest-neighbour display scale; the native frames are 320x180).
Machine checks are in `analysis.json` (labelled `kind: machine`).

作者の目視所見です(機械検査ではなく、独立レビューでもありません)。**独立した人間による可読性・制作上の受入は未実施**です。

| frame | caption | what the author saw | 作者の所見 |
|---|---|---|---|
| 13 | r2 `「ぱ」ﾊﾟｿｺﾝ が か+U+3099` | The NFC `が` and the NFD `か`+combining dakuten looked the same: the mark sat on its base, not in a separate cell. Halfwidth `ﾊﾟｿｺﾝ` was drawn small and thin; readable at 2x, the author did not judge it at native size. Both quote brackets and the semi-voiced `ぱ` were distinguishable. | NFC の「が」と NFD の「か+゛」は同じように見え、濁点は基底字に付いていた。半角カナは小さく細いが、2倍表示では読めた(等倍は未判断)。 |
| 31 | r3 two lines `彼は言った。` / `「行こう」` | Two lines, lines left-aligned inside a horizontally centred block. Line 2 looks indented by about half an em because the `「` glyph carries its ink in the right half of its cell. No hanging-punctuation or bracket compression is applied. Readable at 2x. | 2行、ブロックは中央・行は左揃え。`「` の字形が右寄りのため2行目が半角ほどインデントして見える。ぶら下げ・括弧詰めなし。 |
| 53 | r4 31-character single line | The line is wider than the 320 px canvas and is cut on both sides: only the middle characters (`字幕であり横幅を超えることを確認`) are visible; the first and last characters are lost. The pipeline gave no warning (cmap coverage and the 512-character limit both passed). | 1行が画面幅を超え、両端が欠ける。cmap検査も512文字制限も通り、警告は出ない。 |

Not observed / not judged: contrast and size choices at the intended viewing distance, comfortable reading speed, whether the
50 % black box (invisible on a black background) helps on real footage, vertical writing, ruby, any colour. Frames 0-12, 14-30,
32-52 were checked by machine only; the author did not look at them individually.
