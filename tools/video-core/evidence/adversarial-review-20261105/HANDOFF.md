# Handoff / 引き継ぎ — adversarial review of `tools/video-core` @ `b4ca4a3`

## English (concise)

* **What this is:** a separate automated adversarial review (4 lanes) of the recovered video/audio/mux foundation. Not production acceptance, not security certification, not quality approval. Synthetic fixtures only; no real FFmpeg run, no media, no network. Source, specs and all earlier evidence are byte-identical.
* **Result:** the arithmetic/format core matched an independent oracle in ≈21,800 comparisons with 0 disagreements; input hygiene held (no overwrite, escape, or side effect from rejected input). **10 defects** validated (2 MEDIUM, 8 LOW; 11 reproducer tests) plus one stub-only static concern, all in the failure/receipt/qualification layer — `FINDINGS.json`.
* **Adoption-relevant before use:** (1) **F-INT-1** – cancelling outside the mux step is reported as tool-unavailable / rejection / inspection-failed and more child processes start; (2) **F-REC-2** – an avmux `audio.receipt` qualifies a stem on three self-asserted fields only; treat it as an unauthenticated caller file until fixed. Others are robustness/inventory/classification slips with conservative failure modes.
* **Tests:** existing suite unchanged 454 / 451 pass / 3 skip / 0 fail on 3.11, 3.12, 3.13. Audit suite 89 tests: 78 pass, 0 skip, 0 fail, 0 error, **11 expected failures = reproduced defects** (same 11 are hard failures with `--strict`). Run: `python3 run_audit.py [--strict]` in this directory.
* **Not done / unverified:** Windows, macOS, real FFmpeg/AAC behaviour, ceiling-sized inputs, any quality or perceptual sync. The AAC zero-vs-absent summary and the CPU/memory cap belong to the other session and were not touched. The huge-integer limit issue (F-NUM-1) is listed only as already assigned.
* **Owner action:** apply the one-to-three-line patches in `REPORT.md` §3, then run `--strict`; every reproducer should turn into UNEXPECTED SUCCESS (then remove its `@known_defect`).

## 日本語（要約）

* **位置づけ:** 復元された動画／音声／mux 基盤に対する、独立した自動の敵対的レビュー（4 レーン）。本番受入、セキュリティ認証、品質承認ではありません。合成フィクスチャのみ。実際の FFmpeg 実行・メディア生成・ネットワークは一切なし。ソース・仕様・既存証跡はバイト単位で不変です。
* **結果:** 数値・フォーマット中核は、独立オラクル約 21,800 件の比較で不一致 0。入力衛生（上書き・脱出・拒否入力の副作用）も問題なし。一方で **10 件の欠陥**を確認（MEDIUM 2、LOW 8。再現テストは 11 本）、ほかにスタブのみの静的懸念 1 件。いずれも失敗時／receipt／適格性判定の層です（`FINDINGS.json`）。
* **採用前に重要:** (1) **F-INT-1** — mux 工程以外でキャンセルすると「ツール不在」「拒否」「検査失敗」と誤報告され、さらに子プロセスが起動される。(2) **F-REC-2** — avmux の `audio.receipt` は自己申告の 3 項目だけで stem を「適格」とみなす。修正されるまでは認証されていない呼び出し側ファイルとして扱ってください。他は堅牢性・一覧・分類の軽微な不備で、失敗側に倒れます。
* **テスト:** 既存スイートは無改変で 3.11／3.12／3.13 とも 454 件・成功 451・スキップ 3・失敗 0。監査スイートは 89 件：成功 78、スキップ 0、失敗 0、エラー 0、**期待失敗 11（＝再現した欠陥）**。`--strict` では同じ 11 件が失敗として表示されます。実行: このディレクトリで `python3 run_audit.py [--strict]`。
* **未実施・未確認:** Windows、macOS、実 FFmpeg／AAC の挙動、上限サイズ入力、品質・知覚的同期。AAC の 0 と欠落の区別、および CPU／メモリ上限は別セッション担当のため扱っていません。巨大整数の上限問題（F-NUM-1）は担当割当済みとして記載のみ。
* **担当者の作業:** `REPORT.md` §3 の数行パッチを適用後に `--strict` を実行。各再現テストが UNEXPECTED SUCCESS になれば修正確認です（その後 `@known_defect` を外す）。
