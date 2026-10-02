#!/usr/bin/env python3
"""Read-only HTML/link checks. Pass paths, or inspect changed/untracked HTML."""
from html.parser import HTMLParser
from pathlib import Path
import json
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
STRICT = {
    "workbench/knowledge/ontology-engineering.html",
    "workbench/knowledge/context-engineering.html",
    "workbench/knowledge/graph-engineering.html",
    "research/grok-bot-deep-dive.html",
    "research/muse-agent-deep-dive.html",
    "research/chatgpt-dots-deep-dive.html",
    "research/always-on-agents-2026-10.html",
    "research/muse-recent-updates.html",
}


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs, self.ids, self.stack, self.errors = [], set(), [], []
        self.h1_count = 0
        self.title_count = 0
        self.duplicate_ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.h1_count += tag == "h1"
        self.title_count += tag == "title"
        if "id" in attrs:
            if attrs["id"] in self.ids:
                self.duplicate_ids.append(attrs["id"])
            self.ids.add(attrs["id"])
        if tag == "a" and "href" in attrs:
            self.refs.append(attrs["href"])
        if tag in {"link", "script", "img"}:
            ref = attrs.get("href") if tag == "link" else attrs.get("src")
            if ref:
                self.refs.append(ref)
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("unbalanced closing tag: " + tag)
        else:
            self.stack.pop()


def parse(path):
    parser = Document()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def git_paths(*args):
    output = subprocess.check_output(["git", *args], cwd=ROOT)
    return [value.decode("utf-8") for value in output.split(b"\0") if value]


def main():
    paths = sys.argv[1:] or sorted(set(
        git_paths("diff", "--name-only", "-z", "HEAD")
        + git_paths("ls-files", "--others", "--exclude-standard", "-z")))
    paths = [path for path in paths if path.endswith(".html")]
    errors, local_refs, external_refs, strict_docs = [], 0, set(), 0
    cache = {}
    for name in paths:
        path = (ROOT / name).resolve()
        doc = cache.setdefault(path, parse(path))
        if name in STRICT:
            strict_docs += 1
            if doc.h1_count != 1 or doc.title_count != 1:
                errors.append([name, "expected one h1 and title"])
            for issue in doc.errors + doc.duplicate_ids:
                errors.append([name, issue])
            if doc.stack:
                errors.append([name, "unclosed tags: " + repr(doc.stack)])
            if "最終更新: 2026-10-03" not in path.read_text(encoding="utf-8"):
                errors.append([name, "missing update date"])
        for ref in doc.refs:
            url = urlsplit(ref)
            if url.scheme or url.netloc:
                if url.scheme in {"http", "https"}:
                    external_refs.add(ref)
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target = target / "index.html"
            local_refs += 1
            if not target.exists():
                errors.append([name, "missing local target: " + ref])
            elif name in STRICT and url.fragment and target.suffix == ".html":
                if target not in cache:
                    cache[target] = parse(target)
                if unquote(url.fragment) not in cache[target].ids:
                    errors.append([name, "missing anchor: " + ref])
    print(json.dumps({"changed_html": len(paths), "strict_article_checks": strict_docs,
                      "local_refs_checked": local_refs,
                      "external_urls_in_changed_pages": len(external_refs),
                      "errors": errors}, ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
