#!/usr/bin/env python3
"""Read-only structural/coverage audit for the zero-base refresh.

This is not a live website, external-link, or independent fact audit.
"""
import json
import re
import subprocess
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = "4535dd63a3b2acf62ec793c02c9f8dbffabeece5"
DATE = "2026-10-13"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set(); self.refs = []; self.headings = Counter()
        self.stack = []; self.errors = []; self.visible = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            if attrs["id"] in self.ids: self.errors.append("duplicate_id:" + attrs["id"])
            self.ids.add(attrs["id"])
        for attr in ("href", "src"):
            if attrs.get(attr): self.refs.append(attrs[attr])
        if re.fullmatch(r"h[1-6]", tag): self.headings[tag] += 1
        if tag in {"style", "script"}: self.skip += 1
        if tag not in VOID: self.stack.append(tag)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID: self.handle_endtag(tag)
    def handle_endtag(self, tag):
        if tag in VOID: self.errors.append("closing_void:" + tag); return
        if tag in {"style", "script"}: self.skip = max(0, self.skip - 1)
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("unbalanced_close:" + tag)
            if tag in self.stack:
                while self.stack and self.stack[-1] != tag: self.stack.pop()
        if self.stack and self.stack[-1] == tag: self.stack.pop()
    def handle_data(self, data):
        if not self.skip: self.visible.append(data)


def main():
    catalog = json.loads((ROOT / "research/refresh-2026-10-13/catalog.json").read_text())
    def git_paths(*args):
        return set(x.decode("utf-8") for x in subprocess.check_output(["git", *args, "-z"], cwd=ROOT).split(b"\0") if x)
    tracked = git_paths("ls-files")
    new = git_paths("ls-files", "--others", "--exclude-standard")
    allowed = tracked | new
    changed = git_paths("diff", "--name-only", BASE) | new
    cache = {}; issues = []; volumes = []; refs = 0
    def parse(path):
        if path not in cache:
            data = (ROOT / path).read_text(encoding="utf-8", errors="replace")
            page = Page(); page.feed(data)
            cache[path] = (page, data)
        return cache[path]
    for path in sorted(changed):
        if not path.endswith(".html"): continue
        page, data = parse(path)
        for error in page.errors: issues.append({"path": path, "issue": error})
        if page.stack: issues.append({"path": path, "issue": "unclosed_tags:" + ",".join(page.stack)})
        if page.headings["h1"] != 1: issues.append({"path": path, "issue": "h1_count:" + str(page.headings["h1"])})
        if DATE not in data: issues.append({"path": path, "issue": "missing_refresh_date"})
        if path in tracked and BASE not in data:
            issues.append({"path": path, "issue": "missing_pinned_old_version"})
        if not re.search(r"<title[ >]", data): issues.append({"path": path, "issue": "missing_title"})
        if not re.search(r"name=[\"']viewport[\"']", data): issues.append({"path": path, "issue": "missing_viewport"})
        jp = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", "".join(page.visible)))
        volumes.append({"path": path, "japanese_characters": jp, "bytes": len(data.encode())})
        for ref in page.refs:
            parsed = urlsplit(ref)
            if parsed.scheme or parsed.netloc: continue
            refs += 1
            # This source template is rendered at research/ai-news/YYYY-MM-DD.html.
            ref_base = ROOT / "research/ai-news" if path == "_routines/news-template.html" else (ROOT / path).parent
            target = ref_base / unquote(parsed.path) if parsed.path else ROOT / path
            try: rel = target.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                issues.append({"path": path, "issue": "outside_root_ref", "ref": ref}); continue
            if target.is_dir(): target = target / "index.html"; rel = target.resolve().relative_to(ROOT).as_posix()
            if not target.is_file() or rel not in allowed:
                issues.append({"path": path, "issue": "unpublished_or_missing_ref", "ref": ref}); continue
            if parsed.fragment and target.suffix == ".html" and unquote(parsed.fragment) not in parse(rel)[0].ids:
                issues.append({"path": path, "issue": "missing_anchor", "ref": ref})
    missing = [e["path"] for e in catalog["pages"] if e["kind"] == "current_guide" and e["path"] not in changed]
    legacy_inbound = []
    for source in sorted(tracked - changed):
        if not source.endswith(".html"): continue
        page, _ = parse(source)
        for ref in page.refs:
            parsed = urlsplit(ref)
            if parsed.scheme or parsed.netloc or not parsed.fragment: continue
            target = (ROOT / source).parent / unquote(parsed.path) if parsed.path else ROOT / source
            if target.is_dir(): target = target / "index.html"
            try: rel = target.resolve().relative_to(ROOT).as_posix()
            except ValueError: continue
            if rel in changed and target.suffix == ".html" and target.is_file():
                if unquote(parsed.fragment) not in parse(rel)[0].ids:
                    legacy_inbound.append({"source": source, "target": rel, "fragment": unquote(parsed.fragment)})
    protected_changes = [e["path"] for e in catalog["pages"] if e["kind"] in {"historical_record", "retained_public_review"} and e["path"] in changed]
    secrets = []
    patterns = [r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", r"gh[pousr]_[A-Za-z0-9]{25,}", r"sk-[A-Za-z0-9_-]{30,}", r"/Users/", r"/Volumes/NAS", r"192\.168\.\d+\.\d+"]
    for path in sorted(changed):
        if not path.endswith((".html", ".json", ".md")): continue
        data = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for i, pattern in enumerate(patterns):
            if re.search(pattern, data): secrets.append({"path": path, "pattern_index": i})
    print(json.dumps({"changed_html": len(volumes), "local_references_checked": refs,
                      "template_reference_base": {"_routines/news-template.html": "research/ai-news/YYYY-MM-DD.html"},
                      "issues": issues, "unrefreshed_current_guides": missing,
                      "legacy_inbound_anchor_issues": legacy_inbound,
                      "historical_or_review_changes": protected_changes,
                      "secret_or_private_location_flags": secrets, "volumes": volumes}, ensure_ascii=False, indent=2))
    return bool(issues or missing or legacy_inbound or protected_changes or secrets)


if __name__ == "__main__": sys.exit(main())
