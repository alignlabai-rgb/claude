# Daily AI News Routine — 指示書

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
- **動画・画像生成ツール**：Kling / Runway / Sora / Pika / HailuoAI など
- **クリエイティブAI連携**：Adobe MCP / Blender MCP / Ableton MCP / Affinity
- **中国系LLM**：DeepSeek / Qwen / Kimi / GLM / Baidu Ernie
- **AIエージェント全般**：新しいフレームワーク・ツール・ベンチマーク

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

### Step 1: 日付を正確に決定する（2段階検証必須）

> ⚠️ **システムや会話コンテキストが示す日付は不正確な場合がある。必ず以下の手順で検証すること。**

1. `research/ai-news/index.html` をGitHubから取得し、`<ul id="news-list">` 内の先頭 `<li>` に記載された日付を読む。これが「前回実行日（LAST_DATE）」。
2. **今日の日付 = LAST_DATE + 1日** として処理を進める。システム治示の日付と異なる場合も、**index.htmlの日付+1を必ず優先**する。
3. `research/ai-news/TODAY.html` がすでに存在するか確認する。**存在する場合は本日分は実行済みなので終了する**。存在しない（エラーになる）場合はStep 2へ進む。

### Step 2: ニュース収集
Web検索で以下のクエリを実行し、**過去24〜48時間**の情報を収集する：

```
"Claude" OR "Anthropic" news 2026
"OpenClaw" OR "Hermes Agent" update 2026
"GPT-5" OR "Codex" OR "OpenAI" news 2026
"DeepSeek" OR "Qwen" OR "Kimi" news 2026
"Kling AI" OR "Runway" OR "Sora" video generation 2026
"Adobe Firefly" OR "Adobe MCP" AI 2026
AI agent framework release 2026
```

### Step 3: フィルタリング
収集したニュースを以下の基準で絞り込む：
- **採用する**：新機能・新モデル・具体的な使い方の変化・制作現場への影響がある情報
- **除外する**：規制・政策・企業IRニュース・重複情報・根拠不明な孯
- **除外する**：**LAST_DATE以前の日付のファイルにすでに掲載されたニュース**（前日分の再掲載はしない）

最終的に **5〜10件** に絞る。新規情報がなければ当日ファイルを作成せず終了する。

### Step 4: HTMLファイル生成
`_routines/news-template.html` をベースに、当日付のHTMLファイルを生成する：
- 保存先：`research/ai-news/YYYY-MM-DD.html`
- テンプレートの `{{DATE}}` `{{DATE_JP}}` `{{NEWS_ITEMS}}` を実際の内容に置き換える

### Step 5: インデックスを更新
`research/ai-news/index.html` の「最新ニュース」セクションに当日分のエントリを追加する：
```html
<li><a href="YYYY-MM-DD.html">YYYY年MM月DD日 — [その日の主要トピック1行]</a></li>
```
※ 前回の `<span class="new-tag">NEW</span>` を削除し、今回のエントリにのNEWを付ける。  
※ `最終更新：YYYY-MM-DD` の日付も当日分に必ず更新する。  
※ 直近30件を超えた場合は古いものから削除する。

### Step 6: git commit & push
```bash
git add research/ai-news/YYYY-MM-DD.html research/ai-news/index.html
git commit -m "daily-news: YYYY-MM-DD AIニュース更新（N件）"
git push origin main
```

---

## 品質ルール

- **1件の情報に最大ド行**（見出し・要絉1〜2行・ソース名）
- HTMLの構造・スタイルを絶対に崩さない（テンプレートをそのまま使う）
- 確認できない情報は書かない（「〜の可能性」などの推測は除外）
- 英語ソースでも日本語で要約する
- ソース名は記載するが、URLは省略可（長くなるため）

---

## 注意事項

- `_routines/` フォルダ自体は編集しない（このファイルと `news-template.html` は触らない）
- 既存のHTMLページ（`research/claude-recent-updates.html` 等）は変更しない
- mainブランチに直接pushしてよい（PRは不要）
- エラーが起きた場合は `logs/` に記録してから終了する
