# Design handoff — colour drift of the `color` source chain / 設計ハンドオフ

Scope: **FFmpeg/ffprobe 6.1.1-3ubuntu5, Linux x86_64, one machine, 8-bit RGB hex → BT.709 limited-range yuv420p → H.264.** Synthetic diagnostic frames only; this is **not** a perceptual or creative quality verdict.
The candidate is **opt-in and unapplied** to production. The default plan hash `4bc20b3f…9f21` and the historical 6 s evidence are unchanged (`scope-verification.json`).

---

## English

### Finding (what was measured)
Twelve single-frame experiments (E01–E12, each run once; ≤256×256, ≤10 s, ≤2 threads), results in `measurements.json` / `conclusions.json`, original bytes in `raw/`.

1. **The symptom reproduces exactly.** E09 (flat 256×144 `#30506a`, the committed chain) → mp4 → ffmpeg decode = **44,78,102** (Δ −4,−2,−4 vs nominal 48,80,106), the value reported for the 6 s case.
2. **The `color` source draws BT.601, not BT.709.** E12 (the chain with the `scale` step removed) equals the exact-rational **BT.601** limited-range codes on **all 32 patches (max deviation 0)**; it is far from BT.709 (up to 28 codes in Y). Whether the frame carries a matrix tag is not observable in these logs.
3. **`scale=out_color_matrix=bt709` is not a no-op in this build, but is imprecise.** The negotiated scaler line is `yuv420p → yuv420p`; E01 ≠ E12, so a 601→709 YUV→YUV re-matrixing *does* happen (pre-registered hypothesis H1 said it would not — **H1 is rejected as written**; presumably `in_color_matrix=auto` falls back to BT.601 for the source frame — inferred, not observed; in E11 a frame tagged bt709 through the same scale step came out byte-identical). Result: final codes are within 3 codes of the BT.709 ideal (Y mean bias −1.35, Cb −0.54, Cr −0.57), 11 of 32 patches fail the pre-registered ≤1-code criterion. An exact two-stage oracle (601 codes → exact → 709) would be within 1 code, so ≈2 codes are swscale's YUV→YUV precision and the rest is unavoidable double 8-bit quantisation. (Inferred from outputs; FFmpeg source and ffmpeg.org docs were not reachable — see below.)
4. **Inserting `format=gbrp` before the scaler (E02) fixes the YUV values**: all 32 patches within 1 code of the BT.709 ideal; interior RGB error vs nominal (arithmetic decode of the raw frame) max **5 → 2**, mean **2.01 → 0.344 = exactly the oracle floor** (the best an 8-bit limited-range 4:2:0 frame can do). Through x264 crf 18 + ffmpeg's decoder (E01/E02 mp4): max **6 → 3**, mean abs **2.93 → 0.88**. On the reported colour: baseline mp4 decode 44,78,102 → proposed 47,77,105 (Δ −1,−3,−1): better, **not exact** — the remaining ≤3 comes from x264/decoder, and the pre-registered mp4 tolerance (4) does not separate E09 from E10 for this one colour (both pass); the chart-wide criteria do (baseline fails 11/9/11 patches for K1/K2/K3, proposal 0/0/0).
5. **Equivalent routes:** `format=rgb24` is byte-identical to `gbrp` (E03). `accurate_rnd+full_chroma_int` changes 464 of 49 152 samples by ≤1 (E04, no max-error gain). `zscale` (libzimg, E05) reproduces the exact-rational BT.709 frame **byte for byte**; swscale/gbrp differs from it in 1 360 samples (2.8 %) by 1 code.
6. **Tag-only is not a correction.** E08 (identical pixels to E01, sha256 equal; mp4 tagged smpte170m) decodes the reported patch to 47,80,101 and is up to **25** levels off on other patches. Relabelling changes the interpretation, not the pixels, and contradicts the plan's bt709 contract.

### Counterexamples and limits (visible, not tuned away)
* **File/PNG branch is already matrix-correct** (E06 raw ≤1 code, interior ≤2) and `format=gbrp` there is a **byte-identical no-op** (E06 = E07, raw and mp4). The patch therefore touches colour sources only. E06/E07 **fail K3** (mp4 interior 5 > 4) on `reported_6a5030` and `lcg2` for **both** routes: not caused by the matrix; not seam leakage within 8 px (post-hoc check); attributed to x264/decoder but **not isolated** (no x264-only experiment).
* **Hard colour edges are not fixed by any matrix choice.** In the PNG routes boundary pixels reach 18 (raw) / 20 (mp4) levels of error from 4:2:0 resampling; E01–E05 boundary numbers are **artificially clean** (32 separately converted flat sources stacked with `xstack`) and must not be quoted for edges. Production colour clips are flat, so this matters for file sources/captions, not for `color` clips.
* **`format=gbrp` on an already-BT.709 yuv420p *file* source (E11)** cost ≤1 code in the flat interiors (H6 "harmful" **not supported**), **but at the hard seams chroma moved by up to 32 codes over 5 188 samples (10.6 %)** (4:2:0→4:4:4→4:2:0 round trip); out-of-RGB-gamut or super-white/black YUV, full-range input and 10-bit sources were **not measured** (clipping in RGB is expected analytically, unverified). The patch does not apply gbrp to file sources.
* Residual error is bounded below by 8-bit quantisation (oracle floor: interior max 2, mean 0.344). x264 adds ≈1–2 levels. "Correct" here means **matrix/rounding correct**, not lossless.
* **Not claimed:** other FFmpeg versions/builds (the 601 default of `color` and the scaler's YUV→YUV path are observed behaviour of this build), HDR, ICC, wide-gamut/primaries/transfer conversion (hex values are treated as numeric full-range RGB; the policy changes the matrix path only; tags stay bt709/tv), full-range output, 4:4:4/10-bit, alpha, macOS/Windows, players' own rendering. No Windows/macOS run.

### Method and honesty notes
* Pre-registered before any experiment (`preregistration.json`, commit `130aa55`): H1–H7, tolerances (≤1 code YUV, ≤3 raw RGB, ≤4 mp4 RGB — engineering choices justified by the 8-bit floor 2 and x264/decoder noise), regions (interior ≥4 px from patch edge, boundary ≤1 px), arithmetic predictions. Unexpected result: H1 predicted a decode of 45,78,107 for the reported colour; the report (and E09) say 44,78,102.
* **Amendment 1:** E12 was reserved for harness-bug corrections; after seeing E01's raw codes (before any analysis script) it was used as a diagnostic tap. No harness bug occurred; no experiment was repeated; tolerances/criteria were not changed.
* Post-hoc items are labelled `POSTHOC` in `measurements.json` (deep-interior ≥8 px; bias description). The first analysis output is kept (`*.run1-superseded-additive.json`); the later one only added comparisons.
* Oracle: `fractions.Fraction`, round-half-up, no tie cases among the 32 patches × 2 matrices; the decode oracle uses nearest chroma, the mp4 read-back uses ffmpeg's own swscale — both reported.
* **ffmpeg.org (filters, scaler docs) was not reachable** (proxy 403 on CONNECT; not worked around). Primary documentation used: the installed binary's own `-h filter=color|scale|zscale|xstack|setparams|format` text (`docs/`). No FFmpeg source was read; statements about internals are inferences from measured outputs.
* The opt-in regression test makes 7 single-frame 16×16 ffmpeg runs per invocation (counted separately from E01–E12; invoked once during development and once in the final fresh-export run).

### Candidate (`candidate.patch`, not applied)
```
prepare(episode, ..., color_policy=None | "bt709-tv-gbrp-v1")   # also render(...)
python -m video_core plan|render ... --color-policy {baseline,bt709-tv-gbrp-v1}   # default baseline
```
* Default (`None`/`baseline`): canonical plan, hash, argv **byte-for-byte unchanged** (test pins `4bc20b3f…`, equals `evidence/plan.canonical.json`).
* Opt-in: colour clips get `…setpts=PTS-STARTPTS,format=gbrp,scale=out_color_matrix=bt709:out_range=tv,setsar=1,format=yuv420p`; canonical plan gains `"color_policy"` (only then) → a **different** hash. Schema string `video_core.plan/1` is unchanged — the owner should decide whether the extra key warrants `plan/2`.
* Unknown policy → `ValidationFailed(color_policy_invalid)` before anything else; file sources untouched.
* Tests (patch adds `tests/test_color_policy.py`, 11 tests, 1 opt-in): on fresh temporary exports of base `b4ca4a3` (`patch-test-results.json`, `regression/`), Python 3.11.18 / 3.12.3 / 3.13.18, each identical: **A** unmodified base: 454 total / 451 pass / 3 skip / 0 fail; **B** patched, full suite: 465 / 461 / 4 / 0; **C** patched code, original tests only (new test file removed): 454 / 451 / 3 / 0 — i.e. the default suite counts are unchanged; default plan hash `4bc20b3f…9f21` in A, B and C; opt-in hash `74d3ce28…c377`. **D** opt-in real-ffmpeg test (3.13, `VIDEO_CORE_INTEGRATION=1`, `tests.test_color_policy`): 11 / 11 / 0 / 0.
* Owner decisions: adopt/not; version bump; a fresh render with the policy if it should become evidence; whether to also pin FFmpeg builds (behaviour is build-dependent); making it the default would change every plan hash and needs its own versioned decision.

### Unresolved
Other FFmpeg builds; x264-only attribution of the 1–3 level residual; out-of-gamut/full-range/10-bit sources; hard-edge chroma handling for file sources; whether `color` should be replaced by a tagged generator (e.g. `-f lavfi` RGB source + explicit conversion) instead of a filter-chain patch.

---

## 日本語

### 結論(測定したこと)
単一フレーム実験12件(E01–E12、各1回、≤256×256・≤10秒・≤2スレッド)。結果は `measurements.json` / `conclusions.json`、元バイトは `raw/`。

1. **症状は完全に再現**:E09(256×144の`#30506a`、現行チェーン)→mp4→ffmpegデコード = **44,78,102**(名目48,80,106に対し −4,−2,−4)。6秒ケースの報告値と一致。
2. **`color`ソースはBT.709ではなくBT.601で描画**:`scale`を外したE12は、**32パッチ全てで厳密なBT.601 limitedコードと一致(最大偏差0)**。BT.709からは最大28コード(Y)離れる。フレームに行列タグが付いているかはログから観測できない。
3. **`scale=out_color_matrix=bt709`はこのビルドでは no-op ではないが精度が粗い**:ネゴシエート結果は `yuv420p→yuv420p`、E01≠E12 なので601→709のYUV→YUV再変換は**実行される**(事前登録のH1「再変換されない」は**記載どおりには棄却**。`in_color_matrix=auto`がBT.601に倒れていると推定 — 観測ではない。E11ではbt709タグ付きフレームが同じscaleを通ってバイト一致)。最終コードはBT.709理想から最大3コード(Y平均バイアス −1.35、Cb −0.54、Cr −0.57)、32パッチ中11が事前登録基準(≤1コード)を満たさない。厳密2段オラクルなら1コード以内なので、約2コードはswscaleのYUV→YUV精度、残りは8bit二重量子化の不可避分。(出力からの推定。FFmpegソースとffmpeg.org文書は未取得。)
4. **スケーラ前に `format=gbrp` を挿入(E02)するとYUV値が修正**:32パッチ全て理想から1コード以内。生フレームの算術デコードで内部RGB誤差は最大 **5→2**、平均 **2.01→0.344(オラクル下限と一致)**。x264 crf18+ffmpegデコード(mp4)では最大 **6→3**、平均 **2.93→0.88**。報告色は 44,78,102 → 47,77,105(−1,−3,−1)で**改善するが厳密ではない**(残りはx264/デコーダ由来)。この1色では事前登録のmp4許容(4)がE09とE10を区別できず両方合格。チャート全体の基準では差が出る(ベースライン K1/K2/K3 不合格 11/9/11パッチ、提案 0/0/0)。
5. **等価な経路**:`format=rgb24` は `gbrp` とバイト一致(E03)。`accurate_rnd+full_chroma_int` は49,152サンプル中464を±1変えるのみ(E04)。`zscale`(libzimg、E05)は厳密有理数のBT.709フレームと**バイト単位で一致**、swscale/gbrpは1,360サンプル(2.8%)で1コード差。
6. **タグだけの変更は補正ではない**:E08(ピクセルはE01とsha256一致、mp4を smpte170m でタグ)は報告パッチを 47,80,101 にデコードし、他パッチで最大 **25** ずれる。解釈が変わるだけで画素は不変、planのbt709契約とも矛盾。

### 反例と限界(隠さず記録)
* **ファイル/PNG分岐は既に行列として正しい**(E06 生≤1コード、内部≤2)。そこでの `format=gbrp` は**バイト一致のno-op**(E06=E07)。よってパッチは色ソースのみ対象。E06/E07 は**K3不合格**(mp4内部5>4、`reported_6a5030`と`lcg2`)で**両経路とも**同じ。行列が原因ではなく、8px内のシーム漏れでもない(事後確認)。x264/デコーダと推定するが**切り分け未実施**。
* **硬い色境界はどの行列選択でも直らない**:PNG経路の境界画素は生18/mp4 20レベルの誤差(4:2:0リサンプリング)。E01–E05の境界値は32枚を`xstack`した**人工的にきれいな値**で、エッジの数値として引用不可。本番の色クリップは平坦なので、影響するのはファイルソース/字幕。
* **既にBT.709のyuv420p*ファイル*ソースへのgbrp(E11)**は平坦な内部では≤1コードの損失(H6「有害」は**支持されず**)だが、**硬いシームではクロマが最大32コード、5,188サンプル(10.6%)で変化**(4:2:0→4:4:4→4:2:0往復)。RGB色域外/スーパーホワイト・ブラック、フルレンジ入力、10bitは**未測定**(RGBクリップは理論上起こるが未検証)。パッチはファイルソースにgbrpを適用しない。
* 残差は8bit量子化で下限が決まる(オラクル下限:内部最大2、平均0.344)。x264が約1–2レベル追加。ここでの「正しい」は**行列・丸めが正しい**であり可逆ではない。
* **主張しない事項**:他のFFmpegバージョン/ビルド(`color`の601既定とscalerのYUV→YUV経路はこのビルドの観測挙動)、HDR、ICC、広色域・primaries/transfer変換(hex値は数値のフルレンジRGB扱い。方針は行列経路のみ変更、タグはbt709/tvのまま)、フルレンジ出力、4:4:4/10bit、アルファ、macOS/Windows、各プレーヤーの表示。

### 方法と誠実性メモ
* 実験前に事前登録(`preregistration.json`、コミット `130aa55`):H1–H7、許容値(YUV≤1コード、生RGB≤3、mp4 RGB≤4 — 8bit下限2とx264/デコーダ雑音に基づく工学的選択)、領域(内部=パッチ端から4px以上、境界≤1px)、算術予測。想定外:H1は報告色のデコードを 45,78,107 と予測したが、実測(E09)は 44,78,102。
* **修正1**:E12は不具合訂正用に予約していたが、E01の生コードを見た後(解析スクリプト実行前)に診断用タップとして使用。ハーネス不具合は発生せず、再実験なし、許容値・基準は不変。
* 事後項目は `measurements.json` で `POSTHOC` と明記。最初の解析出力は保存(`*.run1-superseded-additive.json`)、後版は比較の追加のみ。
* オラクルは `fractions.Fraction`・四捨五入(32パッチ×2行列でタイなし)。デコード用オラクルは最近傍クロマ、mp4読戻しはffmpeg自身のswscale。両方を記録。
* **ffmpeg.org(filters/scaler文書)は未到達**(プロキシCONNECT 403。迂回せず)。一次資料はインストール済みバイナリ自身の `-h filter=…` 出力(`docs/`)。FFmpegソースは未読で、内部動作の記述は測定出力からの推定。
* opt-in回帰テストは1回あたり16×16の単一フレームffmpegを7回実行(E01–E12とは別計上。開発中1回+最終の新規export上で1回)。

### 候補パッチ(`candidate.patch`、未適用)
```
prepare(episode, ..., color_policy=None | "bt709-tv-gbrp-v1")   # render(...) も同様
python -m video_core plan|render ... --color-policy {baseline,bt709-tv-gbrp-v1}   # 既定 baseline
```
* 既定:canonical plan・ハッシュ・argv は**バイト単位で不変**(テストで `4bc20b3f…` を固定、`evidence/plan.canonical.json` と一致)。
* opt-in:色クリップが `…setpts=PTS-STARTPTS,format=gbrp,scale=out_color_matrix=bt709:out_range=tv,setsar=1,format=yuv420p`。canonical plan に `"color_policy"` が(その時だけ)加わり**別のハッシュ**になる。スキーマ文字列 `video_core.plan/1` は不変 — 追加キーで `plan/2` にするかは所有者判断。
* 未知のポリシー → `ValidationFailed(color_policy_invalid)`。ファイルソースは不変。
* テスト(パッチは `tests/test_color_policy.py` 11件・うちopt-in 1件を追加):ベース `b4ca4a3` の新規一時exportで実行(`patch-test-results.json`、`regression/`)。Python 3.11.18 / 3.12.3 / 3.13.18 で全て同一結果:**A** 無変更ベース 454件/合格451/skip3/失敗0、**B** patch適用・全テスト 465/461/4/0、**C** patch適用コード+元テストのみ(新テストファイル除去)454/451/3/0 = 既定スイートの件数は不変。既定planハッシュ `4bc20b3f…9f21` はA・B・Cで一致、opt-inハッシュ `74d3ce28…c377`。**D** opt-in実ffmpegテスト(3.13、`VIDEO_CORE_INTEGRATION=1`、`tests.test_color_policy`)11/11/0/0。
* 所有者の判断事項:採否、バージョン、方針適用での再レンダ(証拠化する場合)、FFmpegビルドの固定(挙動がビルド依存)。既定化は全planハッシュが変わるため別途バージョン決定が必要。

### 未解決
他のFFmpegビルド、x264のみの残差(1–3レベル)切り分け、色域外/フルレンジ/10bitソース、ファイルソースの硬い境界のクロマ処理、フィルタ修正でなくタグ付き生成器(RGBソース+明示変換)への置換案。
