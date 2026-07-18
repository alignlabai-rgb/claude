# ALIGN Lab Review Pocket

スマートフォンから公開可能な制作候補を確認し、結果をChatGPTへ戻すための静的レビュー窓口です。

## 境界

- このGitHubリポジトリは公開です。秘密、個人情報、認証情報、非公開契約、公開不適切な正典は置きません。
- `見た限りOK` は visual review のみです。`HUMAN_LOCK`、正典化、公開、課金、`SHIP`を意味しません。
- 回答はブラウザの`localStorage`だけに保存され、GitHubへ自動送信されません。
- 「レビュー結果をコピー」で生成したテキストを、ChatGPTの該当タスクへ貼り付けます。
- Gitから項目を削除しても履歴には残ります。公開してよい素材だけを輸出します。

## キュー更新

1. `queue.json`へ公開可能な項目を追加する。
2. メディアを`assets/`へコピーする。
3. source、canon boundary、production status、可能ならSHA-256を記録する。
4. 375px幅で表示、動画再生、コピー文を確認する。
5. ピンポイントcommit/pushする。ALIGN_Lab本体やAntigravity同期とは混ぜない。

## 将来拡張

- review export packetを貼られたStory/Visual Directorが、該当IDを処理済みにする。
- 次回キュー更新時に処理済み項目を`queue.json`から除く。
- PWAのcore shellはオフライン対応。動画・画像は一度閲覧したものからruntime cacheされます。
