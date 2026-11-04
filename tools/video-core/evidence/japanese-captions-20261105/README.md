# Japanese subtitle conformance and display evidence (2026-11-04/05)

日本語字幕の適合性・表示証跡。Branch `codex/video-core-japanese-captions-20261105`, created at `b4ca4a3b031351a631c0fbe1eb149935f4db19ba`.
Only new files under this directory were added. Production modules, the six original specs, the baseline plan hash and every old evidence byte are unchanged (proof below).

**What this is:** a reusable, bounded evaluation of the *existing* renderer's Japanese caption path (UTF-8 SRT export, cmap glyph-coverage check, drawtext plan, frame-to-caption timing) plus an optional diagnostic *proposal* (`PROPOSAL.md`).
**What it is not:** a new manga method, a replacement renderer, a legibility or aesthetic verdict, a Unicode line-breaking conformance claim, or an independent review. Manga *page* order is right-to-left between panels; that never means reversing the characters of a horizontal subtitle, and nothing here does.

## Findings / 結論

Classification vocabulary — **SUPPORTED** works as specified · **DOCUMENTED_REJECTION** refused with a stable code · **INTEROP_CAVEAT** accepted and preserved, a downstream consumer may treat it differently · **GAP** accepted although it defeats a stated purpose · **real defect** = a measurement contradicts a stated contract.

| # | finding (EN) | 所見 (JA) | class |
|---|---|---|---|
| 1 | Validator outcome matched the declaration for 51/51 corpus cases (41 accepted, 10 rejected with `text_control_char`, `text_blank_line`, `text_length`, `text_invalid_unicode`). | 51件中51件で検証結果が宣言どおり(受理41・拒否10)。 | SUPPORTED / DOCUMENTED_REJECTION |
| 2 | SRT export kept every accepted string **byte for byte**: NFD (`か`+U+3099), halfwidth kana, fullwidth Latin, astral CJK, quotes, `%{n}`, backslashes, `-->`. An independent serializer reproduces the exact bytes; a normalising exporter would be caught because equality is checked on bytes, not after NFC/NFKC. | SRT出力は全文字列をバイト単位で保存。正規化後の一致は「同一」と見なさない。 | SUPPORTED |
| 3 | drawtext path/escaping: caption text goes only through `cap_NNNN.txt` (bytes equal the UTF-8 source, 41/41), the planned argv is ASCII-only and identical for any caption text of the same timing, `expansion=none` is always set. | テキストは必ずファイル経由。argvは字幕文に依存しない。 | SUPPORTED |
| 4 | cmap coverage: production reader and the independent reader (`cmapx.py`) agree on every corpus text for all 7 parseable installed fonts and on a ~1,000 code-point sample. IPAGothic covers 30/41 accepted cases. It lacks `U+20BB7`, all variation selectors, `U+200B/200C/200D`, `U+2028/2029`, `U+202C/202E`, `U+FEFF`, emoji. Inter / DejaVu / Noto Emoji / Unifont-Upper lack kana (0–4 of 41 covered). `.ttc` is refused by both readers. | cmap判定は本番実装と独立実装が一致。IPAGothicは41件中30件を被覆。 | DOCUMENTED_REJECTION |
| 5 | **cmap coverage ≠ correct drawing.** Unifont-JP maps U+200B/200C/200D/2028/2029/202C/202E/FEFF and Pillow/FreeType draws non-blank ink for each of them (production's cmap check accepts them; IPAGothic maps none of them). No covered code point drew as `.notdef` in IPAGothic or Unifont-JP. | cmapにあっても正しい字形とは限らない(Unifontは書式文字にも可視の字形を描く)。 | evidence |
| 6 | Variation selectors (IVS/VS15/VS16) are refused at burn-in because production reads cmap formats 4/12 only, even if a font has a format-14 record. Conservative; documented only by the fontcheck docstring. | 異体字セレクタは format 14 を読まないため焼き込みで拒否(保守的)。 | DOCUMENTED_REJECTION |
| 7 | Timing is frame-exact where it is exact by construction: `enable=between(n,s,e-1)` equals the rational oracle for 36 edge sets (clip start/end, 1-frame, adjacent, gap, overlap) at 6 frame rates; **in the real render all 54 frames match** (no bleed, no off-by-one; frame PTS exactly i·1001/30000). | 焼き込みタイミングはフレーム厳密(実レンダで54/54一致)。 | SUPPORTED |
| 8 | SRT times use documented round-half-up milliseconds: exact for consumers that round or ceil frame time (0 mismatching windows, 14 frame rates). A consumer that compares the *exact rational* frame time with integer-ms cues, or floors, may show one frame early/late whenever the frame period is not a whole number of ms (e.g. 24 fps). Proposal `floor` is exact for all four consumer models (`PROPOSAL.md` B). Models, not measured players. | SRT時刻は四捨五入ms(文書化済)。厳密PTS比較の消費側では最大1フレームずれうる。 | INTEROP_CAVEAT (not a contract violation) |
| 9 | **No wrapping, no overflow warning.** A 31-char line at 20 px on a 320 px canvas is predicted 640 px wide and was drawn cut on both sides (ink x=1..318); cmap check and 512-char limit both pass. Character count is not a width. | 自動改行なし。画面幅超過に警告なし。文字数は幅の指標ではない。 | GAP (documented limitation, now measured) |
| 10 | Kinsoku diagnostic (small declared set, *not* UAX #14): flagged 5/5 deliberate violations (line starting with `。`, `っ`, `ー`; line ending with `「`; `…`/`…` split) and 0 findings on the control. | 小さな宣言済み禁則集合で故意の違反5/5を検出、対照は0件。完全な行分割適合ではない。 | evidence |
| 11 | Narrow validator gaps: U+2028/U+2029 (not `Cc`) are accepted, and `str.splitlines()` consumers split cues at two in a row; a line of only U+200B passes the blank-line rule. Confirmed only on a model consumer / by construction; no effect on the six original specs. | U+2028/2029 とゼロ幅のみの行は受理される(ごく限定的な隙)。 | GAP (low severity) |
| 12 | **No contract-contradicting defect** was found in the exporter, validator, cmap check or timing path by these checks. | 仕様に反する欠陥は今回の検査では見つからず。 | — |

Machine checks vs. author observation vs. pending: every number above is a **machine check** unless marked otherwise. The author looked at three decoded frames
(`render/AUTHOR_VISUAL_OBSERVATIONS.md`: NFC `が` and NFD `か`+゛ looked identical; two lines left-aligned in a centred block with the `「` side-bearing looking like an indent; the overflow line cut at both ends).
These are **not independent**. **Human independent legibility and creative acceptance: PENDING.** Not judged: reading speed, contrast/size at viewing distance, vertical writing, ruby, colour.

## The one real render / 実レンダ1回

Distinct new case `render_case.episode.json` (320×180, 30000/1001, 54 frames = 1.8018 s, 4 captions: plain, NFC+NFD+halfwidth, two-line, overflow; black background, white text). Not the six-second example, not a rerender. One attempt, `RENDER_ATTEMPTED.json` O_EXCL marker, no retry.

| item | value |
|---|---|
| started / status | 2026-11-04T22:36:42Z · `READBACK_MATCH` · exit 0 · no signal · no timeout · stderr empty |
| orchestrator | `render_once.py` using `video_core` prepare/materialize/runner/readback **unmodified**; executed argv = production argv + `-filter_threads 1 -filter_complex_threads 1 -threads 1` (`argv_delta`), CPU affinity {0,1}, hard timeout 30 s, `-fs 2097152` |
| observed | wall 0.126 s; peak 2 threads in the ffmpeg process (includes non-worker threads; sampled 30×) |
| plan / episode / SRT sha256 | `933bb1097db324af9eb76dd2c29360ebc24d066c84bebe2f5e48488aaba1e4c4` / `61c69de9b8520536ea6c8282a36ef779148bb72fab617ffa9369510338c9037e` / `3f7455b003d9eb9bfc973aee210d6807a27656242af76fcd64b609ca2973c6ef` |
| font | IPAGothic `ipag.ttf` sha256 `503af4a8b84d1079b8e2e358dc7f7a7fb8cb7a1f212f35eaef6782dbfc75a55e` (pre-installed, byte-copied into the asset root) |
| `output.mp4` | 10,681 bytes, sha256 `ca49d18f6c8e644acb005ba7bd6b8000987ba2636d0141c5d40f73b0a36438bf` — **not committed**; delivered as an attachment |
| probe / decode | ffprobe: 1 video stream, h264 yuv420p 320×180, `r_frame_rate` 30000/1001, 54 frames read, duration 1.801800; **full decode** of the same file: exit 0, no stderr, 54 frames (raw yuv420p sha256 `7ec1b83abd8788991040c8f39132cc2ae93b83967efaf4aa72177c821ccb3a4d`) |
| contact sheet | decoded frames **13, 31, 53** extracted from the same MP4 (not a render); sheet sha256 `193f8fef93b217809c94163c070669a14a8c3a4b9f9c0c312d47ac0c52fea59b`; per-file hashes in `render/attachments.json` |

Recoverability: `render/receipt.render-once.json` (full argv, exit/signal/timeouts, readback), `render/plan.canonical.json`, `render/ffprobe.readback.json`, `render/ffprobe.frames.json`, `render/analysis.json` (33 machine checks, per-frame visibility table), `render/attachments.json`.
**Disclosure:** the first analysis pass (`render/analysis.run1-criteria-too-strict.json`, 31/33 ok) failed two of *my* criteria on the same media: I required ink at exactly x=0 and x=319 for the clipped line (it was x=1..318: side-bearing), and compared 0/1 masks made with different thresholds for the PNG extraction. I revised both criteria (±3 px; gray-level comparison plus "best match lies in the same caption window") and re-ran the *analysis only*; the render was not repeated and the media hash is identical in both files.

## Fonts used (all pre-installed; nothing downloaded or installed) / 使用フォント

| id | name | sha256 (prefix) | cmap subtables (platform,encoding,format) | corpus covered |
|---|---|---|---|---|
| ipag | IPAGothic | `503af4a8b84d1079` | (0,3,4) (3,1,4) (3,10,12) | 30/41 |
| ipagp | IPAPGothic | `a63f6153841e56ec` | same | 30/41 |
| unifont_jp | Unifont-JP | `218eb241082aa4f4` | (3,1,4) (3,10,12) | 38/41 |
| unifont_upper | Unifont Upper | `f9a49517cb5a06e0` | (3,1,4) (3,10,12) | 0/41 |
| inter_regular | Inter Regular | `be6d709dcb730dda` | (0,3,4) (0,4,12) (3,1,4) (3,10,12) | 3/41 |
| dejavu_sans | DejaVu Sans | `ae7b7855e115a596` | + (1,0,6) | 4/41 |
| noto_color_emoji | Noto Color Emoji | `93cdc4ee9aa40e2a` | (0,5,14) (3,10,12) | 0/41 |
| wqy_zenhei_ttc | WenQuanYi Zen Hei (.ttc) | `79c18ebe7b811951` | refused (collection) by both readers | — |

Full per-font range coverage, advances and per-case coverage: `reports/fonts.json` (formats 4/12 are what production reads; 0/6/13/14 are reported by `cmapx.py` only).

## References / 参照 (primary pages: NOT reachable)

Fetch attempts through the session's existing proxy (no setting changed, no other transport tried) all failed with `curl: (56) CONNECT tunnel failed, response 403`: `https://www.unicode.org/reports/tr14/`, `https://www.unicode.org/Public/UCD/latest/ucd/LineBreak.txt`, `https://ffmpeg.org/ffmpeg-filters.html`, `https://ffmpeg.org/ffmpeg-formats.html`, `https://learn.microsoft.com/en-us/typography/opentype/spec/cmap`.
UAX #14 / the FFmpeg and OpenType pages were therefore **not read**; nothing in this directory claims to quote them. Local substitutes, labelled as such: the installed FFmpeg 6.1.1 `-h filter=drawtext` option list (`textfile`, `expansion`, `text_align`, `fix_bounds` default false, `text_shaping` default true, no wrapping option) and Perl 5.38 `Unicode::UCD` Line_Break values for Unicode 15.0.0 (`reports/uax14_local_probe.json`), used only to check that the *declared* kinsoku set is consistent with those classes.

## Source / history integrity / 履歴・ソース保全

`python3 jpcap_eval.py integrity` (also `suite/test_jp_integrity.py`): base commit is an ancestor of HEAD; `git diff --name-status base..HEAD` contains only `A` entries under this directory; the `tools/video-core` tree minus this directory has the same git blob ids as at the base (131 files); `baseline_tracked_files.sha256` (sha256 of all 131 base files, read from git objects) matches the working tree; the unmodified planner reproduces the baseline plan hash `4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21` (= sha256 of `evidence/plan.canonical.json`); `git ls-files` shows no media (mp4/png/…) in this directory.

## Test counts / テスト件数

Measured with `run_suites.py` (Linux x86_64, FFmpeg 6.1.1; no Windows/macOS). Format: **total / pass / skip / fail**. pass = total − skip − fail.

| Python | default suite, **unchanged** (`tools/video-core/tests`) | new suite, git checkout | new suite, clean export (no `.git`) |
|---|---|---|---|
| 3.11.18 | 454 / 451 / 3 / 0 | 88 / 84 / 4 / 0 | 88 / 80 / 8 / 0 |
| 3.12.3 | 454 / 451 / 3 / 0 | 88 / 87 / 1 / 0 | 88 / 83 / 5 / 0 |
| 3.13.18 | 454 / 451 / 3 / 0 | 88 / 87 / 1 / 0 | 88 / 83 / 5 / 0 |

* The default suite has the same 454 / 3-skip / 0-fail result as measured on the untouched base commit before any change (its 3 skips are the pre-existing opt-in integration tests).
* New-suite skips are explicit, never silent: 1 = attachment-byte check needs `JPCAP_MEDIA=/path/to/output.mp4`; 4 (export only) = history proof needs a git checkout with the base commit; 3 (Python 3.11 only) = Pillow's compiled `_imaging` does not import on that interpreter (the three raster tests, plus `glyph_raster.json` is `NOT_COMPARABLE` there).
* `static-report --check`: `conformance`, `fonts`, `kinsoku`, `timing` reproduce byte for byte on 3.11, 3.12 and 3.13 (same fonts). `glyph_raster.json` depends on the Pillow/FreeType build: byte-compared on the build that wrote it (Pillow 12.3.0, FreeType 2.14.3), claims-only on 3.12 (Pillow 10.2.0), not comparable on 3.11. The clean export (3.13) byte-compared all five.
* Reports were produced by a script the corpus did not see before it ran, but the corpus and checks were written by the same author as the code under test: this is **author verification, not independent review**.

## Reproduce / 再現

```bash
cd tools/video-core
python3 evidence/japanese-captions-20261105/jpcap_eval.py build-corpus --check      # corpus + SRT bytes (MANIFEST.sha256)
python3 evidence/japanese-captions-20261105/jpcap_eval.py static-report --check     # reports/*.json byte-identical on the same fonts
python3 evidence/japanese-captions-20261105/jpcap_eval.py uax14-probe --check       # optional: needs local perl
python3 evidence/japanese-captions-20261105/jpcap_eval.py baseline-manifest --check
python3 evidence/japanese-captions-20261105/jpcap_eval.py integrity                 # needs a git checkout containing the base commit
python3 -m unittest discover -s evidence/japanese-captions-20261105/suite           # new suite (no real render)
python3 -m unittest discover -s tests -t .                                          # default suite, unchanged
python3 evidence/japanese-captions-20261105/run_suites.py                           # both suites on 3.11 / 3.12 / 3.13
# the one render is NOT meant to be repeated; it refuses a second attempt in the same work directory:
#   python3 evidence/japanese-captions-20261105/render_once.py --workdir <outside the repo> --i-accept-the-single-attempt
# re-measure an existing render without rendering:  render_once.py --workdir <dir> --analyze-only
```

## Files

`corpus_def.py` (cases) → `corpus.json`, `corpus.episode.json`, `corpus.srt`, `render_case.episode.json`, `render_case.srt`, `MANIFEST.sha256` · `cmapx.py` independent sfnt/cmap/hmtx reader · `oracle.py` rational timing oracle, strict SRT reader, consumer models ·
`kinsoku.py`, `diagnostics.py` (proposal helpers) · `analysis.py` pixel/glyph measurements · `jpcap_eval.py` driver · `render_once.py` one bounded render + analysis · `run_suites.py` · `suite/` new tests ·
`reports/` (`conformance`, `fonts`, `glyph_raster`, `timing`, `kinsoku`, `uax14_local_probe`) · `render/` receipts and analysis · `baseline_tracked_files.sha256` · `PROPOSAL.md`.
