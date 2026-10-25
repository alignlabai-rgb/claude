# AI運用知識ハブ

Claudeだけでなく、主要LLM、中国系モデル、常設agent、知識設計、開発、制作、企業利用を扱う公開の知識ハブです。速報を消すのではなく、一次根拠・用途・限定試験・導入判断へつなげます。

最終更新: 2026-10-13

## 読む入口

- 毎日のニュース（公開版）：[Claude · AI News](https://alignlabai-rgb.github.io/claude/research/ai-news/) / [Grok · GrokPulse](https://alignlabai-rgb.github.io/GrokPulse/)
- 旧版の補助入口：[X Tech News（カテゴリ一覧）](https://alignlabai-rgb.github.io/ai-news-from-x/)
- [総合トップ](index.html) / [検索](search.html) / [全ページ索引](foundations/site-map.html)
- [LLM Tracker](research/llm-tracker.html): 22の提供者・モデル系列。中国発13系列。全世界・全accountの網羅を保証しません。
- [10/13公式差分](research/official-model-delta-2026-10-13.html): 変更、移行期限、確認範囲、gap
- [モデル世代の読み方](workbench/knowledge/model-generations.html): model ID・surface・段階・licenseを分ける
- [Ontology](workbench/knowledge/ontology-engineering.html) / [Context](workbench/knowledge/context-engineering.html) / [Graph](workbench/knowledge/graph-engineering.html)
- [常設agentの比較](research/always-on-agents-2026-10.html): Grok Bot・Muse・dots
- [情報を組織で活かす方法](research/signal-to-organizational-leverage.html)
- [制作](creator-system/index.html) / [開発・道具](workbench/index.html) / [操作マニュアル](Operations_Manuals/index.html)
- [日次ニュース](research/ai-news/index.html): 当日の観測。現行事実の確定根拠とは別。

## 現行本文と保存版

各guideは用途・仕組み・選択・具体例・失敗条件・評価から再構成します。古い本文は新版の中に追記や折り畳みで混ぜず、[固定版の資料棚](research/archive/index.html)へ分離しています。

保存の基準はcommit `4535dd63a3b2acf62ec793c02c9f8dbffabeece5`。日次ニュース、日付付き調査、過去sessionsは当時の記録として保持します。既存公開reviewの保持は公開適合性の再認定ではありません。

## 根拠の扱い

- 発見源（ニュース・X）、公式事実、提供者評価、独立試験を分けます。
- 確認日と出来事の日付を別々に書きます。予定を実施済みにしません。
- app / API / partner hosted / weights / local inferenceを混ぜません。
- 取得できなかった資料やcurrent catalogのgapを明示します。
- 合成例と未実行の試験を成功実績として書きません。
- 秘密、個人情報、未公開IP、credential、内部host情報を追加しません。
- routingの提案、domain判断、作用の許可を区別します。

今回の[出典対応記録](research/refresh-2026-10-13/)はauthorの確認mapです。独立監査は別の記録で範囲を示します。

## 維持と検査

作業指示は[CLAUDE.md](CLAUDE.md)。ページはHTML/CSSで読むことができ、検索は外部CDNやfetchに依存しません。

```sh
python3 scripts/build-refresh-catalog.py
python3 scripts/build-reading-navigation.py
python3 scripts/audit-refresh.py
python3 scripts/check-knowledge-links.py
node scripts/test-reader-controls.js
python3 workbench/knowledge/examples/evidence_pipeline.py -q
git diff --check
```

上二つは今回の索引の機械再生成です。個別本文を自動で執筆・事実認定するものではありません。catalog/navigation/refresh auditは2026-10-13の対象・baselineに固定した再現用scriptです。将来の刷新では日付と対象を見直し、新規記事を失わないよう更新してから使います。検査scriptも実機試験、外部リンクの全面live検査、表示QA、独立出典監査の代わりではありません。

ニュース入口だけを復旧する場合は `python3 scripts/build-reading-navigation.py --news-only` を使います。トップとResearchのニュース欄だけが対象で、後続の日次記事・一覧・検索索引を再生成しません。導線の回帰検査は `python3 scripts/test-news-navigation.py` です（導線復旧: 2026-10-26。上記の本文確認日とは別）。

local編集、commit、branch push、PR、main merge、site deploymentは別状態です。他writerの日次更新を反映直前に確認し、force pushや無断resetで消しません。

[刷新前README](https://github.com/alignlabai-rgb/claude/blob/4535dd63a3b2acf62ec793c02c9f8dbffabeece5/README.md)
