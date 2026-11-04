# 統合コントラクト — 既存オーナー向け (JA)

位置づけ: **汎用・公開の抽象化に対する設計と、実行可能なコントラクトテスト**です。実際の採用ではなく、運用可能性の主張でも、創作品質の主張でもありません。
実際のストーリー・音声・タイミングは未解決のままで、ここで使う合成フィクスチャから推測できません。レンダ・ミックス・mux・ffprobe・ダウンロードは一切実行していません。
**[test X]** と付いた記述は `contract_suite.py` の対応ケースで固定され、`EXPECTED_VS_OBSERVED.json` に再現可能な形で記録されています。

基準コミット: `b4ca4a3b031351a631c0fbe1eb149935f4db19ba`(ブランチ `codex/video-core-integration-20261105`)。対象ライブラリ: `tools/video-core`(`video_core` 0.1.0)。**変更なし**。

## 1. 1つの欄にまとめてはいけない6つの事柄

| # | 事柄 | 保持者 | ライブラリは運べるか | 似た値を渡した場合の挙動 |
|---|---|---|---|---|
| 1 | **入力選択の権限** — どの行を対象にするか | 既存の選択/バインディング担当 | 「導出済みの明示サブセット」としてのみ: audiomix の `scope.event_ids`、avmux は動画1本+ステム1本、video-only は自分で書いたクリップ一覧 | 誰が選んだかは判別不能。暗黙の「全部」は無い **[test b3b]** |
| 2 | **採用済みバインディング参照** — どのソース/テイクに結び付いたか | 同担当の記録 | 不透明な文字列のみ(`decision_ref`, `provenance_ref`, `selection.ref`, `source_id`) | そのまま転記(1〜128文字)、**認証されない**。「APPROVED-BY…」も単なる文字列 **[test x1]** |
| 3 | **技術的な計測値** | ライブラリ(明記された範囲) | 読んだファイルの sha256、厳格な WAV 事実、フレーム→サンプルの厳密整数演算。ffprobe 由来の事実は `avmux mux --mux` の事前検査でのみ(今回は未実行) | 申告値は `CALLER_STATED_UNVERIFIED`。plan は `UNVERIFIED…` と言いつつ終了コード0 **[test f1, f4]** |
| 4 | **フレーズ境界/サンプルタイミングの適格性** — 「この切り位置は計測・承認済み」 | 既存オーナー | **運べない**(GAP-06): `timing_qualification` 等は `unknown_field` | 境界の決め方に関係なく、長さが等しければ受理 **[test e1]** |
| 5 | **実行許可** — 「この plan を今走らせてよい」 | CLI を起動する人/システム | **運べない**(GAP-11、設計どおり): ゲートは素のフラグ `--render/--mix/--mux` だけ | なし |
| 6 | **創作上の承認** | 既存オーナー | **運べない**: 判定は `quality_evaluated:false`, `creative_acceptance:"NOT_EVALUATED"` | なし。出力に承認を示すキーは無い **[test x1]** |

原則: 認証されない JSON レシート内の status 文字列やハッシュは、**誰が作成・承認したかの証拠ではありません**。AV mux は手書きの3項目レシートも、
自己矛盾する改変レシートも受理します **[test d3]**。レシートは「整合性」の証拠としてのみ扱い、自分たちの記録に結び付けてください(§5)。

## 2. 各段階が実際に検証すること

`validate` / `plan` / 既定動作は、プロセス起動・ネットワーク・ファイルやディレクトリの作成を行いません **[test p1: 12コマンド × PATH 2通り(スタブあり/空) = 24回]**。
✔ はライブラリ自身が強制している検査。「plan のみ」は、今回実行していない本実行ではなく plan 時点の検査という意味です。

| 検査 | video-only (`video_core`) | 音声 (`audiomix`) | AV mux (`avmux`) |
|---|---|---|---|
| スキーマ・厳格JSON・整数・未知フィールド拒否 | ✔ | ✔ 選択/非選択を問わず全行 | ✔ |
| 厳密な有理数 fps、整数からの境界計算(累積なし) | ✔ フレーム/尺 | ✔ 絶対境界ごとに `floor((2·f·d·48000+n)/(2·n))` **[a4]** | ✔ 同じ規則。申告サンプル数は規則値**かつ**WAV実測と一致 |
| 選択行が**未解決** | 表現不可(GAP-01)。ファイル欠落は `asset_missing` で拒否 | ✔ **HOLD**、終了コード5、無音にしない **[b1]** | `input_state_not_use` で拒否(終了1)— HOLD はコードとしてのみ残る(GAP-10) **[b5]** |
| 非選択の未解決行 | 対象外(オーナーが除外) | ✔ ブロックせず報告 **[a2, b3]** | 対象外 |
| NULL / 状態欠落 | `type_error` | `invalid_enum` / `missing_field`(既定値に倒さない) **[b2]** | `invalid_enum` / `missing_field` **[b5]** |
| ソース内容を sha256 で固定 | ✘ **plan では不可**(GAP-02)。内容を差し替えても plan ハッシュ不変 **[c1]** | ✔ 選択アセットのみ読み・ハッシュ・WAV解析 **[c1]** | ✔ 両ファイルをハッシュ **[c1]**。動画はハッシュのみ(解析しない) |
| ソース/出力の衝突、既存出力、シンボリックリンクのルート | ✔ **[c2, c3]** | ✔ | ✔ |
| フレーズ/サンプルタイミングの*適格性* | ✘ | ✘(GAP-06)。厳密な*長さ*一致のみ(±1サンプルは拒否) **[a4, e2]** | ✘ |
| 明示的な無音 | 対象外 | ✔ `INTENTIONAL_SILENCE`+`decision_ref` がカバレッジに数えられる。`OMIT`/欠落は数えない **[s1]** | ✘ 無音ステムを区別できない(GAP-05) **[s3]** |
| 尺・ミックス・伸縮の推測なし | ✔ パディングなし | ✔ トリム/パディング/伸縮/暗黙加算なし **[e2]** | ✔ argv に `-shortest/-t/-ss/-r/-vsync/-fps_mode/-ar/-af` なし **[a3, s4]** |
| 動画プロパティ | 計画のみ | 対象外 | plan では**申告のみ**。計測は `--mux` 事前検査のみ **[f1]**。比較ロジックは固定辞書でのみ確認 **[f2]** |
| 前段のレシート | 受け取らない(GAP-03) | 対象外 | 任意。与えた場合: status `MIXED_VERIFIED`、stem の sha256 を持つ非 partial 出力1件、`readback.wav.sample_count` — **これだけ** **[d1–d4]** |
| 既定の `-an` | ✔ `-an -sn -dn`、音声入力なし。サンプル plan ハッシュ `4bc20b3f…9f21` 不変 **[f3]** | mux しない | 別 argv。動画は `copy` |
| エピソード照合 | – | ✔ 任意の `--episode`(fps・フレーム数・episode sha256) **[a5]** | ✘ なし(GAP-07) |
| plan 経路でのプロセス/ディレクトリ作成 | なし | なし | なし **[p1]** |

## 3. 呼び出し側の責任表

「ライブラリ」= LIBRARY_ENFORCED で証明済み。「オーナー」= 既存オーナー/呼び出し側が行うべきこと。テストは、ガード無しだとライブラリが受理すること(LIBRARY_PERMISSIVE)と、例示ガード(CALLER_GUARD_DEMO)を併記します。

| 責任 | ライブラリ | **オーナー/呼び出し側がやること** | ギャップ |
|---|---|---|---|
| 選択スナップショットを固定し、**選択行のみ**からマニフェストを導出 | 明示 scope、暗黙の全件なし | スナップショット参照を出す。非選択行を video-only/avmux に渡さない | – |
| **選択行**が MISSING/UNKNOWN/PROXY/HOLD/NULL なら呼ばない | audiomix は HOLD、avmux は非USEを拒否 | episode/マニフェストを作る*前*に止める(video-only は表現できない)。コードを自分の HOLD に写像。色クリップ・プロキシ・ゼロ埋めWAVで**代替しない** | 01, 10 |
| NULL/UNKNOWN を未解決のまま保つ | NULL拒否、UNKNOWN→HOLD | NULL→UNKNOWN に写すか scope から外す。既定値を入れない | – |
| バインディング参照の解決と保存 | 不透明な文字列を転記 | 呼び出し時点で自分の記録に照らして解決し、段階間で参照を比較 | 07 |
| フレーズ/サンプルタイミングの適格化 | 厳密演算のみ | 選択された USE 行ごとに QUALIFIED 記録を保持。無ければ HOLD。未適格な範囲も全段階が*受理*する | 06 |
| ソース版の固定 | audiomix/avmux は sha256 | 選択時に sha256 を記録(**video-only も**。plan は固定しない)。各段階の前に再確認 | 02, 13 |
| `REQUIRE_FULL_COVERAGE` を使い、plan の警告は未準備扱い | ギャップで HOLD、ゼロ埋めは警告 | `ZERO_FILL_APPLIED` / `NO_AUDIO_EVENTS_SELECTED` を READY として出荷しない。音声が無いなら video-only(`-an`)を選び、無音WAVを捏造しない | 09, 05 |
| 意図的な無音を明示 | `INTENTIONAL_SILENCE`+`decision_ref` | 判断はオーナーが持つ。avmux は再検証できない | 05 |
| 終了コードでなく**status**を読む | JSON の `status` / `readiness` | 終了0≠準備完了: HOLD の dry-run、`hold_if_planned`、ゼロ埋め READY、UNVERIFIED はすべて0。終了1+stdout空 = REJECTED_UNSTRUCTURED | 08, 14 |
| 音声レシートを実行に結び付ける | 浅い3項目 | 非nullのレシートを必須にし、`plan_sha256`・`input.manifest_sha256`・`inputs_unchanged`・`readback.all_ok`・`written/stage`・`warnings` を**自分の実行記録**と照合。それでも出所は証明されない | 04, 05, 07 |
| 動画レンダのレシートを結び付ける | 受け口なし | AV マニフェスト作成前に `READBACK_MATCH` と、非 partial 出力の sha256 == 動画 sha256 を確認 | 03 |
| IN_PROGRESS / UNKNOWN / `.partial` / HOLD は UNKNOWN 扱い | レシートを与えれば avmux が拒否 | 引き継がない。レシートを省くと UNKNOWN の実行のステムも受理される | 04 |
| ステムの受け渡し | 出力は asset root 内に置けない | AV 段の単一 asset root へコピーし、再ハッシュしてからマニフェスト作成 | 12 |
| 毎回新しい出力名、過去出力の保持 | 既存/重複出力を拒否、再開しない **[c3]** | 試行ごとに新名。失敗ディレクトリは証跡として残す | – |
| 実行許可と創作承認の保持 | 受け口なし | 正確な `plan_sha256` に結んだオーナー記録として保持し、両方が揃ってからフラグを渡す | 11 |
| ロールバック | なし(新規ディレクトリのみ) | ハッシュ付きバックアップからポインタ/コピーを復元しハッシュを確認 **[g3]** | – |

## 4. 伝播規則(失敗・適格性欠落・ロールバック)

1. **下流で補修しない。** 上流の HOLD/UNKNOWN/partial/clamp/不一致は拒否される(またはオーナーが拒否する)。無音・プロキシ・既定値には変換されない。**[b1, d1, g2]**
2. **HOLD は HOLD のまま。** audiomix は `plan` で終了5、`mix` の dry-run では終了**0**かつ `status:"HOLD"`。avmux は同じ状態を終了1 / `input_state_not_use` で報告。オーナーは3つを1つの HOLD に写して止める。**[b1, b5]**
3. **適格性の欠落には表現が無い。** どのマニフェストも作る前に確認し、欠けていれば呼ばない。**[e1]**
4. **構造化されない失敗がある。** JSON の形が違うレシートで `avmux plan` が異常終了(終了1、stdout空、トレースバック)。何も作られない。**[d2b]**
5. **ソース版の固定**は plan 時点の sha256 連鎖。マニフェストと*レシート*を揃えて再固定すれば受理される。**[g1]**
6. **過去の出力は不変**: 全段階が既存の出力名(古い `IN_PROGRESS` ディレクトリも)を拒否し、バイトに触れない。**[c3]**
7. **ロールバック**はオーナー作業: ハッシュ付きバックアップから前のポインタ/コピーを復元し、失敗候補だけを削除、元のハッシュを再検証し `plan` を再実行 — 以前と同じ `plan_sha256`。合成フィクスチャの一時コピー上で2方式(リネーム入替、コピー復元)を実演。本番状態の読取・変更は一切なし。**[g3, `rollback/ROLLBACK_EVIDENCE.json`]**

## 5. オーナー向けインターフェース擬似コード(設計のみ — 未実装、モジュールではない)

```text
# 名前は例示です。video_core にもこのディレクトリにも存在しません。
OwnerRequest {
  request_ref
  selection_snapshot: { ref, rows: [ {row_id, selected, state, binding_ref,
                                      source: {path, sha256},
                                      timing: {status: QUALIFIED|UNQUALIFIED|UNKNOWN, qualification_ref},
                                      silence: {decision_ref}} ] }
  execution_permission: { granted, plan_sha256, ref }     # オーナー記録。video_core には渡さない
  creative_approval:    { status, ref }                   # オーナー記録。video_core には渡さない
}
呼び出し側の手順:
  1 選択行はすべて {USE, INTENTIONAL_SILENCE, OMIT}、USE 行は固定済み+QUALIFIED+binding_ref 解決可能   さもなくば HOLD して停止
  2 選択ファイルを再ハッシュ == 固定値                                                                  さもなくば REJECT して停止
  3 選択行のみから episode / audio manifest / avmux manifest を作成(純粋なデータ変換、policy.gap = REQUIRE_FULL_COVERAGE)
  4 video_core validate,plan · audiomix validate,plan(+ --episode) → JSON status を読み READY / HOLD / UNVERIFIED / REJECTED / REJECTED_UNSTRUCTURED に写す
  5 plan の警告・HOLD・「検証済みが必要なのに UNVERIFIED」があれば停止。なければ permission.plan_sha256 == plan_sha256 か?  さもなくば HOLD
  6 新しい出力名で実行。各レシートの status を読み、非検証なら停止し、ディレクトリは証跡として保持
  7 受け渡し: ステムを AV の asset root へコピーし再ハッシュ。レシートを自分の記録(plan_sha256、manifest ハッシュ、inputs_unchanged、readback.all_ok、warnings)に結ぶ
  8 avmux --mux の事前検査が動画を計測。READBACK は技術的確認のみ
  9 creative_approval がオーナー記録になってから昇格。ロールバック = ハッシュ付きバックアップから前のポインタを復元
```

## 6. 真のコントラクトギャップ(API が要件を表現できない)

証拠と推奨タスクは `GAPS_AND_TASKS.md`。要約: **GAP-01** video-only に入力状態の語彙なし · **02** video-only の plan がソースハッシュを固定しない · **03** 動画レシートの受け口なし ·
**04** 音声レシートが任意・浅い・偽造可能 · **05** レシートの警告が落ちる/無音ステムを区別不能 · **06** タイミング適格性のフィールドなし · **07** 参照/scope を段階間で照合しない ·
**08** 非準備でも終了0 · **09** `ALLOW_ZERO_FILL` · **10** 段階ごとに HOLD の語彙が違う · **11** 許可/承認の受け口なし(設計どおり) · **12** ステム受け渡しの配置(オーナー作業) ·
**13** TOCTOU は未実行 · **14** 形の違うレシートで `AttributeError`。
互換性は捏造していません。API が区別を表現できない箇所では、テストが*実際の*挙動を固定し、ギャップ名を付けています。

## 7. 主張していないこと

ffmpeg/ffprobe/レンダ/ミックス/mux/ダウンロードは実行していません。「動画」は非メディアのバイト列、音声レシートはハーネスが作成した偽物です(ライブラリ自身のレシートは使っていません)。
`mux --mux` の事前検査は、固定辞書に対する純粋関数としてのみ確認しました **[test f2]**。
Linux x86_64 と Python 3.11/3.12/3.13 のみ。作成者自身の確認であり、独立レビューではありません。
