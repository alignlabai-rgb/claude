#!/usr/bin/env python3
"""Offline checks for the two stable daily-news entrances, not news accuracy."""
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("navigation", ROOT / "scripts/build-reading-navigation.py")
nav = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nav)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href"))


pattern = re.escape(nav.NEWS_START) + r".*?" + re.escape(nav.NEWS_END)
for path in nav.NEWS_PAGES:
    page = (ROOT / path).read_text(encoding="utf-8")
    matches = list(re.finditer(pattern, page, re.S))
    assert len(matches) == 1, (path, "exactly one news block required")
    block = matches[0].group()
    assert block == nav.news_entry(), (path, "generator and published markup differ")
    assert page.index("</header>") < matches[0].start() < page.index("<h2>"), path
    links = Links()
    links.feed(block)
    assert links.hrefs == [
        "https://alignlabai-rgb.github.io/claude/research/ai-news/",
        "https://alignlabai-rgb.github.io/GrokPulse/",
        "https://alignlabai-rgb.github.io/ai-news-from-x/",
    ], (path, links.hrefs)
    for url in links.hrefs:
        assert not re.search(r"/20\d\d-\d\d-\d\d", url), (path, "date-pinned destination")
    # Everything outside the inserted block must remain exactly unchanged.
    baseline = subprocess.check_output(["git", "show", f"ac6d74a:{path}"], cwd=ROOT).decode()
    assert re.sub(pattern, "", page, flags=re.S) == baseline, (path, "unrelated page changes")

# Full regeneration must keep the same entrances, too.
nav.INFO = {p: {"title": p} for p in ("index.html", "search.html", "foundations/site-map.html")}
rendered = nav.render("index.html", "test", "test", "<section>existing</section>")
assert rendered.count(nav.NEWS_START) == 1
assert rendered.index(nav.NEWS_START) < rendered.index("<section>existing</section>")
readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert "https://alignlabai-rgb.github.io/GrokPulse/" in readme
assert "https://alignlabai-rgb.github.io/claude/research/ai-news/" in readme
print("PASS: both news entrances, stable destinations, generator retention, unchanged surrounding content")
