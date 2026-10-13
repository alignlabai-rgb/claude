# Public AI Knowledge Hub — 作業指示

最終更新: 2026-10-13

このrepoは公開可能なAI技術・運用・制作の知識を扱います。Claude専用でも、非公開の組織資料を投影する場所でもありません。依頼された内容を安全に更新し、読者が判断・試験できる本文を残します。

## 作業開始

1. README、現在の依頼、環境側の現行instructionsを読む。
2. exact編集root、Git branch、remote、worktreeの既存差分を確認する。旧文書のパス例を現行配置の権限とみなさない。
3. 対象と対象外、書込ownerを固定する。他人の差分は消さない。
4. [編集contract](research/refresh-2026-10-13/EDITORIAL_CONTRACT.md)、[onboarding](foundations/onboarding-checklist.html)、[並列作業](foundations/multi-instance-protocol.html)を読む。

非公開repoのclone、認証変更、NAS操作はこの指示だけでは行いません。公開情報の更新に内部資料を持ち込む必要はありません。

## 本文の原則

- 継ぎ足しではなく、現在の読者の用途から再構成する。
- 製品・model ID・API・partner hosted・weights・harnessを分ける。
- 公式資料の実取得を根拠にする。search snippetだけを確認済みにしない。
- 発表日、確認日、期限、予定、停止実証を区別する。
- 提供者benchmarkを独立測定へ読み替えない。
- 固有の仕組み、選択、合成例、失敗条件、評価方法を含める。
- 試験案・未実行・未確認を明示する。実績を作らない。
- 秘密、個人情報、未公開IP、credential、内部host情報を公開本文へ追加しない。

## 旧情報

刷新前commitへの固定linkを末尾に置き、[資料棚](research/archive/index.html)からも読めるようにします。旧本文を新版の中へ折り畳んで残しません。過去の日次ニュース、日付付きsnapshot、sessionsは当時の記録として保持します。既存reviewを独断で削除・非公開化しません。

古い指示やADRは当時の判断として参照し、現在の依頼や現行の保存配置instructionsへ無条件に移し替えません。組織の権限・canon・契約を本repoの編集で改定しません。

## 並列作業

- 子担当のexclusiveファイル群を決める。共有index、search、Gitの操作は統合担当が扱う。
- authorの確認と独立reviewを区別する。自分の記事の自己点検を独立監査と呼ばない。
- 完了時に対象、source map、検査、未確認、他担当に関係する差分を返す。
- 統合後に全体の名称、期限、提供面、リンクを再検査する。
- network権限拒否を別transportで迂回しない。未実施を残し、許可された仕事だけを続ける。

## 現行modelの配置

[LLM Tracker](research/llm-tracker.html)が現行coverageの読み口です。[model ledger](research/refresh-2026-10-13/model-ledger.json)には公式根拠とgap、[日付付き差分](research/official-model-delta-2026-10-13.html)には確認時点の変更と期限、個別guideには使い方を置きます。[モデル世代](workbench/knowledge/model-generations.html)は識別と評価の方法です。第二のcurrent表を大量に複製しません。

## 検査と反映

新規ページをcategory入口・全索引・検索へ接続します。提供された索引生成と構造検査を使い、source確認・秘密境界・必要な独立reviewを別に行います。

```sh
python3 scripts/build-refresh-catalog.py
python3 scripts/build-reading-navigation.py
python3 scripts/audit-refresh.py
python3 scripts/check-knowledge-links.py
node scripts/test-reader-controls.js
python3 workbench/knowledge/examples/evidence_pipeline.py -q
git diff --check
```

catalog/navigation/refresh auditは今回の2026-10-13と固定baselineを再現するscriptです。次の刷新では対象・日付・新規記事の取込を確認して更新し、機械再生成で後続の記事を落としません。scriptのPASSは外部の全リンクや表示品質を保証しません。実行した検査と未実行を別々に報告します。

公開作用は現在の依頼に含まれる範囲だけ。branch push、PR、main merge、deploymentを別に読み戻します。反映前にremoteの日次writer更新を取り込み、他の更新を保ちます。force push、無断reset、history rewriteは禁止です。

[刷新前CLAUDE.md](https://github.com/alignlabai-rgb/claude/blob/4535dd63a3b2acf62ec793c02c9f8dbffabeece5/CLAUDE.md)
