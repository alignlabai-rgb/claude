# Optional diagnostic proposal (NOT applied; default behaviour unchanged)

提案(任意)です。本番コードには**適用していません**。既定動作・baseline plan hash・既存evidenceは不変です。
Nothing in `video_core/` imports or calls anything here; `diagnostics.py`, `kinsoku.py`, `cmapx.py`, `oracle.py` live only in this evidence directory
(`suite/test_jp_kinsoku_diag.py::HelperDoesNotChangeDefaults` pins that, and the baseline plan hash `4bc20b3f…9f21` is recomputed by the unmodified planner).

## A. Advisory caption diagnostics (`diagnostics.diagnose`)

What it would do if adopted: return findings for a caption string, never change or reject it.
Findings are *information for the author*, kept **outside** the canonical plan (so the plan hash does not move), e.g. in `plan.observations["caption_diagnostics"]`.

| code | severity | basis (measured here) |
|---|---|---|
| `LINE_OVERFLOWS_CANVAS` / `LINE_NEARLY_FULL_WIDTH` | warn / info | drawtext does not wrap. Render r4: 31-char line, predicted 640 px (hmtx sum, 20 px) on a 320 px canvas, drawn ink x=1..318: both ends cut, no warning from the pipeline. Estimate only (no kerning/shaping). |
| `LINE_START_PROHIBITED`, `LINE_END_PROHIBITED`, `SPLIT_INSEPARABLE` | warn | small declared set in `kinsoku.py`; inspects only the author's explicit `\n` breaks |
| `UNICODE_LINE_BOUNDARY` | warn | U+2028/U+2029 are not `Cc`, so the validator accepts them; `str.splitlines()` consumers split on them, two in a row make an empty line (= cue end) — demonstrated on a model consumer in `test_jp_corpus.py` |
| `INVISIBLE_ONLY_LINE` | warn | a line of only U+200B etc. passes `text_blank_line` but is visually blank |
| `BIDI_CONTROL` | warn | U+202E etc. are accepted; unrelated to the right-to-left *panel* order of manga pages, which never reverses horizontal character order |
| `VARIATION_SELECTOR` | info | production's cmap check reads formats 4/12 only; sequences are refused at burn-in even if a font has a format-14 record |
| `NOT_NFC`, `NFKC_WOULD_CHANGE` | info | the text is preserved byte for byte; never compare sources after normalising |
| `MARKUP_LOOKALIKE`, `TIMING_LINE_LOOKALIKE`, `INDEX_LINE_LOOKALIKE` | info/warn | shapes some SRT consumers interpret |
| `COMBINING_MARK_HAS_ADVANCE` | info | hmtx advance of U+3099/U+309A = 1 em in IPAGothic; in the one author render the mark was drawn attached anyway (see `render/AUTHOR_VISUAL_OBSERVATIONS.md`) — so only a width-estimate caveat |

Limitations: heuristics; the kinsoku set is *declared and small* (not UAX #14, not JIS X 4051); widths are estimates; the helper needs an hmtx reader
(`cmapx.py`) that production does not have; "no finding" is not "legible".

## B. Optional SRT boundary policy `floor` (exporter option, never the default)

`timing.json` / `test_jp_timing.py::ConsumerModelFindings`: production rounds frame boundaries to the nearest millisecond (documented). Over every window 0 ≤ s < e ≤ 40
at 14 frame rates (1, 7, 15, 24, 25, 30, 60, 120, 24000/1001, 30000/1001, 48000/1001, 60000/1001, 120000/1001, 89/3):

| consumer MODEL (not a real player) | production `half_up` | proposal `floor` |
|---|---|---|
| rounds frame time to ms, half up | 0 mismatching windows | 0 |
| ceils frame time to ms | 0 | 0 |
| compares exact rational frame time with integer-ms cue bounds | mismatches whenever the frame period is not a whole number of ms (e.g. 24 fps: caption `[0,1)` also shown on frame 1) | 0 |
| floors frame time to ms | same mismatches as above | 0 |

`floor` (start_ms = ⌊t_s⌋, end_ms = ⌊t_e⌋) was exact for all four models in this exhaustive-to-40-frames grid; the reasoning needs a frame period above ~2 ms
(≤ ~500 fps); it is a grid result plus an argument, not a proof for every episode. It changes SRT bytes at non-whole-ms boundaries, therefore it would have to be an explicit opt-in flag
so existing plan hashes and the six original specs stay valid. **Burn-in is unaffected**: drawtext `enable=between(n,s,e-1)` was frame-exact by construction and in the render (54/54 frames match the oracle).
The default behaviour is *not* a contract violation: the documented rule holds for round/ceil consumers; only strict-exact consumers see a ≤1-frame difference.

## C. Not proposed here
Automatic wrapping, `fix_bounds`, vertical writing, ruby, hanging punctuation, colour/contrast (another session), changing the validator's rejection set, speeding up the
O(groups) format-12 lookup (≈6 ms per code point on large tables in the profile; harmless at the 512-character limit).

## How a maintainer could try it without touching production
```bash
cd tools/video-core
python3 - <<'PY'
import sys; sys.path.insert(0, "evidence/japanese-captions-20261105")
import diagnostics, cmapx
f = cmapx.read_font(open("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "rb").read())
for d in diagnostics.diagnose("これは\nっぽい例です。", font=f, font_size=20, canvas_width=320): print(d["severity"], d["code"], d["detail"])
PY
```
