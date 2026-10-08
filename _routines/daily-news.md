# Daily AI News Routine — 指示書

> 改訂 2026-10-08: 1日1件に痩せる問題への対策として、Step 2（一次ソース巡回の追加）と Step 3（件数目標・掘り下げ手順）を強化。

このファイルはClaude Routinesが毎日実行するための指示書です。
新しいセッションでこのRoutineが起動したら、まずこのファイルを読み、
以下の手順をすべて実行してください。

---

## このRoutineの目的

ALIGN Labの制作・事業活動に関連するAIニュースを毎日収集し、
`research/ai-news/` に日次HTMLページを追加する。
リポジトリ（`alignlabai-rgb/claude`）のmainブランチに直接pushし、
GitHub Pages経由でいつでもブラウザから確認できる状態に保つ。

---

## 収集スコープ（優先度順）

### 🔴 最優先（必ず収集）
- **Claude / Anthropic**：新モデル・機能・API更新・価格変更・MCP新コネクター
- **OpenClaw / Hermes Agent**：バージョンアップ・新機能・コミュニティ動向
- **Claude Code / Claude Routines**：動作変更・新コマンド・SDK更新

### 🟠 高優先（積極的に収集）
- **GPT / OpenAI / Codex**：モデル更新・Codex新機能・エージェント関連
- **Grok / xAI**：モデル更新（Grok 4.x → 5）・Grok builds・Hermes Agent 連携・Grok Imagine（画像/動画）
- **動画・画像生成ツール**：Kling / Runway / Sora / Pika / HailuoAI など
- **クリエイティブAI連携**：Adobe MCP / Blender MCP / Ableton MCP / Affinity
- **中国系LLM**：DeepSeek / Qwen / Kimi / GLM / Baidu Ernie
- **AIエージェント全般**：新しいフレームワーク・ツール・ベンチマーク

> 📌 **LLM トラッカー6本との連動**: 本ハブは LLM 速報トラッカーを6本（Claude / ChatGPT / Gemini / Grok / Qwen / GLM）運用している。この6社の重要アップデートは特に拾い、日次ニュースに含める。蓄積した日次記事は、月次棚卸しで該当する `research/*-recent-updates.html` に反映する候補とする（トラッカーの深掘りは「日次の積み上げ」で行い、捏造で埋めない）。

### 🟡 中優先（重要なものだけ）
- **Gemini / Google AI**：大きなモデル更新や新サービスのみ
- **ビジネス・M&A × AI**：実際の導入事例・ROI事例・業界展開
- **収益化・マネタイズ**：クリエイターのAI活用収益化事例
- **音楽生成AI**：Udio / Suno / その他

### ⚪ 低優先（よほど重要でなければスキップ）
- AI規制・政策・政治的動向
- 学術論文（ベンチマーク記録更新は対象、理論研究はスキップ）
- 大企業の組織変更・採用情報

---

## 実行手順

### Step 1: 対象日を決める（欠損日の補完込み）

> ⚠️ **システムや会話コンテキストが示す日付は不正確な場合がある。** 基準は常に `index.html` の最新日付とする。

1. `research/ai-news/index.html` の `<ul id="news-list">` 先頭 `<li>` の日付を読む（**ファイル全体は読まず** `grep -m1 date-tag` 等で1行だけ取得）。これが「前回実行日（LAST_DATE）」。
2. **対象日リスト = LAST_DATE+1日 〜 システム日付**（JST）。通常は1日分、実行が飛んだ日があれば**複数日をまとめて1回の実行で作成**する（欠損日の補完）。
   - システム日付が LAST_DATE+1 より前（異常値）の場合は、LAST_DATE+1 の1日分のみを対象とする。
   - 対象日が**最大7日**を超える場合は直近7日だけ処理し、それ以前の欠損期間は `logs/` に「未補完: YYYY-MM-DD〜YYYY-MM-DD」と記録する。
3. 対象日リストの各日について `research/ai-news/YYYY-MM-DD.html` の存在を確認し、**既に存在する日はスキップ**。全日が存在すれば終了する。

### Step 2: ニュース収集（一次ソース巡回 ＋ 検索の二段構え）

対象日の**最古日〜最新日の全期間を1回の収集でカバー**する（日ごとに検索を繰り返さない）。

> ⚠️ 汎用のWeb検索は「比較ブログ」「SEO記事」ばかりを返し、日付つきの新着を取りこぼしやすい。**検索だけで終わらせず、必ず2a（一次ソース巡回）を先に行う。**

#### 2a. 一次ソース巡回（WebFetch・必須）
次のページを取得し、対象日の期間に該当する項目（見出し・日付）を拾う。取得不可のサイトは1回で諦めて次へ進む。

| 領域 | 巡回先 |
|---|---|
| Anthropic | https://www.anthropic.com/news ／ https://docs.claude.com/en/release-notes/overview ／ https://github.com/anthropics/claude-code/releases |
| OpenAI | https://openai.com/news ／ https://github.com/openai/codex/releases |
| xAI | https://x.ai/news |
| Google | https://blog.google/technology/ai/ |
| OpenClaw | https://github.com/openclaw/openclaw/releases |
| Hermes Agent | https://github.com/NousResearch/hermes-agent/releases |
| 動画・画像 | Runway / Kling / Luma / Pika / Adobe Firefly の各公式ブログ・ニュース |
| 中国系LLM | DeepSeek / Qwen / Moonshot(Kimi) / Z.ai(GLM) の公式ブログ・GitHub releases |
| 集約（補助） | https://llm-stats.com/ai-news ／ https://benchlm.ai/model-updates/releases（**日付の手掛かり専用。一次ソースで裏取りするまで採用しない**） |

#### 2b. Web検索（補完・WebSearch）
2aで拾えなかった領域を補う。クエリには**必ず「具体的な月日」または「this week」**を含める（例: `Anthropic announcement October 8 2026`、`Codex changelog this week`）。年だけの汎用クエリは比較ブログが混ざるので避ける。

```
Claude / Anthropic announcement {月日}
Claude Code release notes {月日}
OpenAI Codex GPT news {月日}
Grok xAI Grok Build Grok Imagine news {月日}
OpenClaw Hermes Agent release {月日}
DeepSeek Qwen Kimi GLM release {月日}
Kling Runway Veo Sora Pika Luma video model {月日}
Adobe Firefly MCP Blender MCP Ableton creative AI {月日}
AI agent framework MCP release {月日}
Suno Udio AI music news {月日}
Gemini Google AI new model {月日}
```

トークン節約ルール：
- 本文の全文取得は、日付・事実の確認が必須の項目に限る（目安: 1回の実行で最大8件まで）。
- 過去の日次HTMLを開かず、重複確認は `grep -o 'news-title.*' 直近3〜5日分` の見出しだけで行う。
- 検索結果が既出ばかりのクエリは打ち切ってよい。ただし**2aの巡回は省略しない**。

### Step 3: フィルタリング・件数目標・日付への割り当て
- **採用する**：新機能・新モデル・価格/制限の変更・具体的な使い方の変化・制作現場への影響がある情報。**リリースノート／changelog／GitHub releaseの具体的な変更点も採用対象**（大きな発表がない日でも、これで件数を確保する）。
- **除外する**：規制・政策・企業IRニュース・重複情報・根拠不明な情報・集約サイトのみが出典の情報
- **除外する**：LAST_DATE以前のファイルに掲載済みのニュース（再掲載しない）
- 各ニュースは**発表日（不明なら報道日）に該当する日**のファイルへ割り当てる。1件を複数日に重複掲載しない。

#### 件数目標（ボリューム基準）
- 1日あたり **目標 5〜10件**、下限の目安は **5件**。🔴最優先領域（Claude / OpenClaw・Hermes / Claude Code）から最低1件、🟠高優先領域から最低2件を含める。
- **5件に満たない場合、「該当なし」と結論づける前に次を必ず実行する**：
  1. 2aの未巡回の巡回先を残らず取得する（特にGitHub releases／changelog）。
  2. 領域別に絞った追加検索を3回以上行う（例: `Claude Code changelog`、`Hermes Agent v0.` 最新版、`Kling` 最新モデル）。
  3. 前日の発表が当日に報道されたものは、報道日の側に載せてよい（ただし重複掲載は不可）。
- 上記を尽くしても5件に届かなければ、**確認できた分だけ**を載せる（捏造・水増し・推測は禁止）。その場合、ページ末尾に「巡回した一次ソース」を1行で記すと、「本当に少ない日」だと後から判断できる。
- 日をまたぐ補完でも同じ基準を適用する（1日1件だけの連続は「調査不足」のサインとして扱う）。
- 該当ニュースがない日：
  - **過去日（補完対象）**は、テンプレートの `.no-news` ブロック（「この日は掲載できる新規ニュースが確認できませんでした」）だけのファイルを作成する（再度欠損日として検出されないようにするため）。indexには「（新規ニュースなし）」と記す。
  - **システム日付の当日**で新規が皆無なら、その日のファイルは作らず終了してよい。

### Step 4: HTMLファイル生成
`_routines/news-template.html` をベースに、**対象日ごとに**HTMLファイルを生成する：
- 保存先：`research/ai-news/YYYY-MM-DD.html`
- テンプレートの `{{DATE}}` `{{DATE_JP}}` `{{NEWS_ITEMS}}` を実際の内容に置き換える

### Step 5: インデックスを更新
`research/ai-news/index.html` の「最新ニュース」セクションに**対象日ぶんのエントリを新しい日付が上になる順で**追加する：
```html
<li><a href="YYYY-MM-DD.html">YYYY年MM月DD日 — [その日の主要トピック1行]</a></li>
```
※ 前回の `<span class="new-tag">NEW</span>` を削除し、**最新日のエントリのみ**にNEWを付ける。  
※ `最終更新：YYYY-MM-DD` の日付も当日分に必ず更新する。  
※ 直近30件を超えた場合は古いものから削除する。

### Step 6: commit → PR作成 → 即マージ（複数日でも1回にまとめる）

> トークン残量が乏しい場合は、**作成済みの日だけでも先にcommit/push/マージ**する（残りの日は次回実行で補完される）。

現在のブランチ名を確認し、そのブランチにcommit & pushしてからPRを作成して即マージする。

```bash
# 現在のブランチにcommit & push
git add research/ai-news/
git commit -m "daily-news: YYYY-MM-DD AIニュース更新（N件）"   # 複数日なら "YYYY-MM-DD〜YYYY-MM-DD 補完（N日・M件）"
git push origin HEAD

# PRを作成して即スカッシュマージ
gh pr create \
  --title "daily-news: YYYY-MM-DD AIニュース更新（N件）" \
  --body "Routinesによる日次AIニュース自動更新。" \
  --base main

gh pr merge --squash --auto
```

> `gh` コマンドが使えない場合は、GitHub MCP ツール（`mcp__github__create_pull_request` → `mcp__github__merge_pull_request`）を使って同等の操作を行う。
> リポジトリ: `alignlabai-rgb/claude` / base: `main` / merge_method: `squash`

---

## 品質ルール

- **1件の情報は「見出し ＋ 要約2〜3行 ＋ ソース名」**（何が変わったか／制作・運用への影響を1文入れる）
- HTMLの構造・スタイルを絶対に崩さない（テンプレートをそのまま使う）
- 確認できない情報は書かない（「〜の可能性」などの推測は除外）
- 英語ソースでも日本語で要約する
- ソース名は記載する。公式一次ソースの場合はソースにリンクを付けると望ましい
- 要約は日本語。モデル名・バージョン・日付などの固有名詞は原文どおりに書く
- 領域ごとに `section-label` で見出しを分け、🔴→🟠→🟡の順に並べる

---

## 注意事項

- `_routines/` フォルダ自体は、日次実行中は編集しない（このファイルと `news-template.html` は触らない）。指示変更はユーザーの依頼時のみ
- 既存のHTMLページ（`research/claude-recent-updates.html` 等）は変更しない
- mainブランチに直接pushしてよい（PRは不要）
- エラーが起きた場合は `logs/` に記録してから終了する
