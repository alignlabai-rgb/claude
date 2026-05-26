# Claude Workspace Instructions

このフォルダは、Claude / Codex / その他エージェントに外部から更新や整理を依頼するための作業対象です。

実験的に運用する前提はありますが、それは「壊してよい」という意味ではありません。
正しい前提は `安全に更新し、必要なら戻せるようにする` です。

**最終更新:** 2026-05-26

---

## Purpose

- 自分用の技術ハブとして機能させる
- ALIGN_Lab の理念・制作を支える技術・運用知識を蓄積する
- 外出中でも Claude に非同期で更新を依頼できる基盤にする
- 必要に応じて Codex でも同じ構造を理解できる状態を保つ
- 複数のセッション（Claude / Codex の並走）が同時稼働しても破綻しないこと

このハブは公開リポジトリです（外部から見られても問題ない設計）。一方で参照する `ALIGN_Lab` 本体は非公開・ローカル運用なので、このハブ内に取り込んだ抜粋（`foundations/align-lab-context.html`）でコンテキストを担保しています。

---

## 新しいセッションで最初に読むべきページ

1. このファイル（**CLAUDE.md**）
2. **[foundations/onboarding-checklist.html](foundations/onboarding-checklist.html)** — 最初の10分の具体ステップ（Phase A: git 確認 / Phase B: 必読チェック / Phase C: 過去判断確認）
3. **[foundations/multi-instance-protocol.html](foundations/multi-instance-protocol.html)** — 並走セッションの作法（並走は常態）
4. [foundations/align-lab-context.html](foundations/align-lab-context.html) — ALIGN Lab のミッション・資産・AI協働方針
5. [foundations/agent-protocol.html](foundations/agent-protocol.html) — エージェントとして作業する際のプロトコル・ルール
6. [logs/next-actions.html](logs/next-actions.html) — 直近の TODO と優先タスク
7. [logs/hub-health.html](logs/hub-health.html) — ハブ全体の鮮度・ALERT 確認

迷ったら `foundations/operating-principles.html` も参照。

---

## トップ index.html の構造（2026-05-25 から）

トップは以下の3カテゴリ構造で運用しています:

### 制作・技術コア（主軸）

- **Creator System**（spotlight: 力点領域）— 動画・漫画パイプライン、評価、マネタイズ
- **Claw Stack** — Claude / PicoClaw / NanoClaw / OpenClaw
- **Tech Workbench** — 開発実務（git・docker・mac・VSCode・Vibe Coding 等）
- **LLM 個別深掘り** — ベンダー別の特性・実装メモ（workbench/knowledge 配下）

### ナレッジ・資料

- **Industry Trends** — ベンダー横断の長文分析（research/trend-*）
- **Briefings & 配布資料** — 過去のデモ・配布資料アーカイブ（demos/）

### ハブ運用

- **Foundations** — 前提・理念・運用プロトコル・参照ドキュメント
- **Strategy** — 事業接続・能力マップ・優先順位・ロードマップ
- **Logs** — ダッシュボード・四半期レビュー・TODO・セッションログ

トップにはこのほか **Daily News Hub**（最上段）と **LLM Updates Hub**（カード群の直前）が常設されています。

---

## Priorities

1. **リンク切れを作らない** — 移動より上書き、削除より別場所への退避を優先
2. **ルートの index.html を常に総合ポータルとして維持する**
3. **新規情報がどのカテゴリに属するか明確にする** — 迷ったら ADR で「暫定配置」を記録
4. **将来の拡張に耐える構造を保つ** — `物理パスとカテゴリラベルは分離可能` という思想
5. **重要な変更は記録する** — 構造変更は ADR（`foundations/decisions.html`）に必ず追記
6. **並走を前提に動く** — push 前に必ず fetch、別セッションのブランチ・コミットを消さない

---

## Folder Rules

物理フォルダの命名規約と用途:

| フォルダ | 用途 |
|---|---|
| `foundations/` | 目的・理念・運用原則・運用プロトコル・参照ドキュメント（用語集 / 決定ログ / アンチパターン / Onboarding / 並走プロトコル / Site Map / Page Template / Style Guide） |
| `strategy/` | 事業接続・能力マップ・能力進化・優先順位・ロードマップ・リスクレジスター |
| `creator-system/` | ★ 主軸: 制作システム本体（動画・漫画パイプライン、評価、マネタイズ、OpenClaw 連携） |
| `claw-stack/` | Claude / PicoClaw / NanoClaw / OpenClaw の比較・導入・運用判断 |
| `workbench/` | 実務知識・操作ガイド・LLM 個別深掘り（60+ ページ） |
| `research/` | 調査記事・LLM トラッカー・Industry Trends 長文・AI News 日次・OpenClaw News |
| `demos/` | ブリーフィング・配布資料アーカイブ（過去のデモ含む参照資料） |
| `logs/` | 変更ログ・実験メモ・TODO・ダッシュボード・四半期レビュー |
| `_routines/` | 日次運用テンプレート（AI News 等の生成用） |

新カテゴリ追加は ADR-002（3カテゴリ構造）の方針に沿って慎重に判断する。

---

## Editing Policy

### 基本原則

- 既存情報を消す前に、まず整理して入口を作る
- 文字化けやリンク切れは最優先で直す
- カテゴリをまたぐ変更は `logs/` に記録する
- 大きい構造変更をしたら `README.md` と必要な指示書を更新する
- 公開リポなので、機密情報・取引先固有情報は混入させない
- 「動く」だけでなく「次のエージェントが理解できる」状態を目指す

### 並走前提のルール

- セッション開始時に必ず `git fetch && git status && git branch -r` で状態確認
- 自分のフィーチャーブランチは `claude/*` / `codex/*` / `feat/*` / `fix/*` 等に切る
- 他セッションのブランチ・コミットを消さない（force push 厳禁）
- 詳細は [`foundations/multi-instance-protocol.html`](foundations/multi-instance-protocol.html)

### 構造変更時のルール

- 後から戻すコストが大きい変更（物理リネーム・フォルダ削除・カテゴリ統合）は<strong>必ず ADR を書く</strong>
- 大きな変更前にユーザーに選択肢を A/B/C で提示する
- 物理パスは触らず<strong>表示ラベルだけ変える</strong>選択肢を必ず検討（ADR-005 / ADR-004 のパターン）

---

## When Adding New Content

1. **カテゴリを決める**（上の Folder Rules 参照）
2. **テンプレートを選ぶ**（`foundations/page-template.html` の Light / Rich）
3. **スタイルガイドに従う**（`foundations/style-guide.html`）
4. **本文を書く**
5. **必須末尾要件をクリア**：最終更新日・関連リンク・出典実 URL
6. **親カテゴリ index.html からリンク**を張る
7. **必要なら top index.html のカードにも surface**
8. **`foundations/site-map.html` に追記**
9. **大きな変更なら ADR を書く**（`foundations/decisions.html`）
10. **失敗事例があれば anti-patterns に記録**
11. **新用語が出たら glossary に 1-2 行追加**

詳細チェックリストは `foundations/definition-of-done.html` を参照。

---

## 重要な参照ドキュメント

| 用途 | パス |
|---|---|
| 新セッションの作業開始手順 | `foundations/onboarding-checklist.html` |
| 並走時の作法 | `foundations/multi-instance-protocol.html` |
| 全ページ索引 | `foundations/site-map.html` |
| 用語集 | `foundations/glossary.html` |
| 決定ログ（ADR） | `foundations/decisions.html` |
| アンチパターン集 | `foundations/anti-patterns.html` |
| ページテンプレート | `foundations/page-template.html` |
| スタイルガイド | `foundations/style-guide.html` |
| Done 定義 | `foundations/definition-of-done.html` |
| Hub 健全性 | `logs/hub-health.html` |
| 直近 TODO | `logs/next-actions.html` |
| 四半期レビュー | `logs/2026-q2-review.html` |
| リスクレジスター | `strategy/risk-register.html` |

---

## ALIGN_Lab との関係

ALIGN_Lab 本体レポジトリは<strong>非公開・ローカル運用</strong>で、このマシンには pull されていません。本ハブ内では `foundations/align-lab-context.html`（154行）で ALIGN_Lab のミッション・データ資産・役割分担を抜粋しており、それで本ハブの作業に必要なコンテキストは担保されます。

ALIGN_Lab 本体の更新が必要な作業（戦略再整合・ALIGN_Lab 側の機能と紐付くタスク）は、別マシン（ALIGN_Lab がローカルにあるマシン）で実行することを前提とします。

---

## このファイル自体のメンテナンス

このファイルは「ハブの憲法」的役割を持ちます。以下のタイミングで更新:

- 新カテゴリを追加した時（Folder Rules を更新）
- 構造変更時（Editing Policy を更新）
- 新しい必読ドキュメントを追加した時（読書リスト更新）
- 四半期に1回（鮮度確認）

過去の版は git 履歴に残るので、削除より追記・置換が望ましい。
