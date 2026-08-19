# Claude Knowledge Hub

`claude/` は、Claude専用フォルダではなく、公開可能なAI運用知識を安全に更新・再利用するためのKnowledge Hubです。

## Role

- ALIGN_Lab の理念や制作活動を支える技術基盤
- 自分が学ぶべき知識を継続的に蓄積するハブ
- Claude / Codex に更新や整理を依頼しやすい構造を持つ作業領域
- 公開しても問題のない技術・運用・調査情報の置き場

## Current entry points

- [`research/llm-tracker.html`](research/llm-tracker.html)
  - 21 provider、中国系12 providerの現行model、surface、公式確認、専用tracker、鮮度gapを一画面で確認するcoverage ledger
- [`research/official-model-delta-2026-08-19.html`](research/official-model-delta-2026-08-19.html)
  - 8/11以後に変わった主要providerのmodel、alias、deprecation、tool更新を公式一次情報で確認するcurrent layer
- [`research/official-model-delta-2026-08-11.html`](research/official-model-delta-2026-08-11.html)
  - 8/11時点のhistorical snapshot。現在判断は8/19版を優先
- [`research/ai-news/index.html`](research/ai-news/index.html)
  - 毎朝のClaude Newsを速報レーダーとして残すアーカイブ。確定情報の根拠にはしない
- [`research/signal-to-organizational-leverage.html`](research/signal-to-organizational-leverage.html)
  - Claude News / GrokPulse / Hermesなどのsignalを、重複除去・一次確認・候補owner・最小の次手・失効日へ変換する方法
- [`workbench/knowledge/model-generations.html`](workbench/knowledge/model-generations.html)
  - ハブ内の現行モデル世代SSOT

## Structure

- `index.html`
  - ハブ全体の総合トップ
- `foundations/`
  - このハブの目的、運用原則、ALIGN_Lab との関係
- `strategy/`
  - 事業との接続、優先順位、能力マップ、ロードマップ
- `creator-system/`
  - AI Creator Hub 本体
- `claw-stack/`
  - Claude / PicoClaw / NanoClaw / OpenClaw 系の導入・比較
- `research/`
  - 調査アーカイブ
- `workbench/`
  - 実務知識・学習ハブ
- `logs/`
  - 実験メモ、更新履歴、TODO

## Maintenance

- Claude に作業させる前提の指示書は `CLAUDE.md`
- 公開前はリンク検査を行う
- 速報・公式確認・ローカル観測を同じ確度で混ぜない
- local client、remote API、local inferenceを区別し、model ID・license・実行receiptを確認する
- 秘密、個人情報、未公開IP、credential、内部host情報を公開ハブへ入れない
- 新しいページは、どのカテゴリに属するかを先に決めてから追加する
- 壊れてよいのではなく、戻せる前提で安全に更新する
