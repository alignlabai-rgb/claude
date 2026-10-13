#!/usr/bin/env python3
"""Build the reading/old-version catalog; no network or Git mutations.

Generated files are a mechanical index, not a freshness or fact audit.
"""
import html
import json
import re
import subprocess
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
BASE = "4535dd63a3b2acf62ec793c02c9f8dbffabeece5"
DATE = "2026-10-13"
OUT = ROOT / "research" / "refresh-2026-10-13"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.text = []
        self.in_title = False
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag == "title": self.in_title = True
        if tag in {"script", "style"}: self.skip += 1
    def handle_endtag(self, tag):
        if tag == "title": self.in_title = False
        if tag in {"script", "style"}: self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if not self.skip: self.text.append(data)


def kind(path):
    if path.startswith("review/"): return "retained_public_review"
    if path.startswith(("research/ai-news/", "logs/sessions/")):
        return "historical_record"
    name = Path(path).name
    if re.search(r"2026-\d\d-\d\d|2026-q\d", name) or name == "claude-timeline.html":
        return "historical_record"
    if path.startswith("_routines/"): return "routine_template"
    if path.startswith("Operations_Manuals/") and path != "Operations_Manuals/index.html":
        return "current_guide"
    if name in {"index.html", "search.html", "template.html", "page-template.html", "site-map.html"}:
        return "navigation_or_template"
    return "current_guide"


def main():
    tree = subprocess.check_output(["git", "ls-tree", "-r", "-l", "-z", BASE], cwd=ROOT).decode("utf-8")
    entries = []
    for row in tree.split("\0"):
        if not row: continue
        meta, path = row.split("\t", 1)
        if not path.endswith(".html"): continue
        mode, typ, blob, size = meta.split()
        raw = subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
        parser = Page(); parser.feed(raw.decode("utf-8", errors="replace"))
        current = (ROOT / path).read_bytes()
        changed = current != raw
        entries.append({"path": path, "title": "".join(parser.title).strip() or path,
                        "kind": kind(path), "baseline_blob": blob, "baseline_bytes": int(size),
                        "old_version": f"https://github.com/alignlabai-rgb/claude/blob/{BASE}/{quote(path, safe='/')}",
                        "working_text_changed": changed})
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "prepared_on": DATE, "baseline_commit": BASE,
               "note": "working_text_changed is not an independent content or freshness audit",
               "counts": dict(Counter(e["kind"] for e in entries)), "pages": entries}
    (OUT / "catalog.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sections = []
    labels = {"current_guide": "解説・選択ガイドの旧版", "navigation_or_template": "旧版の入口・テンプレート",
              "historical_record": "日次ニュース・日付付き調査・履歴", "retained_public_review": "既存公開レビュー（保持）",
              "routine_template": "運用テンプレートの旧版"}
    for group, label in labels.items():
        rows = []
        for e in entries:
            if e["kind"] != group: continue
            rows.append(f'<li><a href="{html.escape(e["old_version"], quote=True)}">{html.escape(e["title"])}</a>'
                        f' <span class="small">{html.escape(e["path"])}</span></li>')
        sections.append(f'<section class="panel"><h2>{label}</h2><ul>{"".join(rows)}</ul></section>')
    archive = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>旧版の資料棚 | Research</title><link rel="stylesheet" href="../../workbench/knowledge/deep-dive.css"></head>
<body><a class="skip" href="#main">本文へ</a><main class="shell" id="main">
<header class="panel"><nav><a href="../../index.html">Hub Top</a> / <a href="../index.html">Research</a></nav>
<h1>旧版の資料棚</h1><p class="lead">刷新前の本文は、新版に追記・折り畳みで混ぜず、この固定版から読むことができます。</p>
<p>保存版はコミット <code>{BASE}</code>。各リンクはその時点のGitHubファイルを開きます。後からmainが更新されてもリンク先の本文は変わりません。旧版にあるモデル名・料金・期限・利用条件は現在の判断にそのまま使わないでください。</p>
<p>日次ニュースや過去の調査は「当日の観測」を残す資料です。出来事を新しい日付に書き換えません。既存公開レビューも削除や非公開化をしていません。この索引は内容の公開適合性を再認定するものではありません。</p>
<p class="small">棚の作成: {DATE} / <a href="../refresh-2026-10-13/catalog.json">機械可読一覧</a> / <a href="../../foundations/archive-policy.html">履歴の読み方</a></p></header>
{"".join(sections)}<footer class="panel"><h2>関連</h2><p><a href="../index.html">Research</a> / <a href="../../foundations/site-map.html">現行の全ページ索引</a> / <a href="../../index.html">Hub Top</a></p></footer>
</main></body></html>'''
    dest = ROOT / "research" / "archive" / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(archive, encoding="utf-8")
    print(json.dumps({"pages": len(entries), "counts": payload["counts"]}, ensure_ascii=False))


if __name__ == "__main__": main()
