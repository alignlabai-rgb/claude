# Claude Workspace Instructions

このフォルダは、Claude に外部から更新や整理を依頼するための作業対象です。

実験的に運用する前提はありますが、それは「壊してよい」という意味ではありません。
正しい前提は、`安全に更新し、必要なら戻せるようにする` です。

## Purpose

- 自分用の技術ハブとして機能させる
- ALIGN_Lab の理念や制作を支える技術・運用知識を蓄積する
- 外出中でも Claude に非同期で更新を依頼できる基盤にする
- 必要に応じて Codex でも同じ構造を理解できる状態を保つ

## 新しいセッションで最初に読むべきページ

1. このファイル（CLAUDE.md）
2. `foundations/align-lab-context.html` — ALIGN Lab のミッション・資産・AI協働方針
3. `foundations/agent-protocol.html` — エージェントとして作業する際のプロトコル・ルール
4. `logs/next-actions.html` — 直近の TODO と優先タスク

上記4点を読んでから作業を始めること。迷ったら `foundations/operating-principles.html` も参照。

## Priorities

1. リンク切れを作らない
2. ルートの `index.html` を常に総合ポータルとして維持する
3. 新規情報がどのカテゴリに属するか明確にする
4. 将来の拡張に耐える構造を保つ
5. 重要な変更は記録する

## Folder Rules

- `foundations/`
  - 目的、理念、運用原則、ALIGN_Lab との関係
- `strategy/`
  - 事業との接続、優先順位、能力マップ、ロードマップ
- `creator-system/`
  - 制作システム本体
- `claw-stack/`
  - Claude / PicoClaw / NanoClaw / OpenClaw の導入・比較・判断材料
- `research/`
  - 調査記事、FAQ、用語集、リファレンス
- `workbench/`
  - 実務知識、操作ガイド、学習コンテンツ
- `logs/`
  - 変更ログ、実験メモ、TODO

## Editing Policy

- 既存情報を消す前に、まず整理して入口を作る
- 文字化けやリンク切れは最優先で直す
- カテゴリをまたぐ変更は `logs/` に記録する
- 大きい構造変更をしたら `README.md` と必要な指示書を更新する
- 公開して問題ない内容かを意識する
- 「動く」だけでなく「次のエージェントが理解できる」状態を目指す

## When Adding New Content

1. まずカテゴリを決める
2. カテゴリトップから辿れるようにする
3. 必要なら `logs/` に更新メモを残す
4. 重要なら `strategy/` や `foundations/` にも反映する
5. 最後にリンク確認を行う
