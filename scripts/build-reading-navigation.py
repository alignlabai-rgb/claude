#!/usr/bin/env python3
"""Mechanically rebuild reader navigation/search from tracked publication files.

Article prose is authored separately. No network or Git mutations.
"""
import html
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-10-13"
BASE = "4535dd63a3b2acf62ec793c02c9f8dbffabeece5"
CATALOG = ROOT / "research/refresh-2026-10-13/catalog.json"
OWNED_ELSEWHERE = {"claw-stack/index.html", "claw-stack/nanoclaw/index.html",
    "claw-stack/openclaw/index.html", "claw-stack/picoclaw/index.html",
    "creator-system/Claude_Design/index.html", "research/openclaw-news/index.html",
    "workbench/knowledge/claude-code/index.html", "workbench/knowledge/openclaw/index.html",
    "workbench/vibe-coding-guide/index.html"}
class Info(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []; self.lead = []; self.paragraphs = []; self.p = []
        self.intitle = False; self.inlead = False; self.inp = False; self.p_meta = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title": self.intitle = True
        if tag == "p":
            self.inp = True; self.p = []
            classes = set(a.get("class", "").split())
            self.inlead = "lead" in classes
            self.p_meta = bool(classes & {"eyebrow", "small", "date-tag", "meta", "notice"})
    def handle_endtag(self, tag):
        if tag == "title": self.intitle = False
        if tag == "p":
            value = "".join(self.p).strip()
            if not self.p_meta and not value.startswith(("最終更新", "公式情報確認日")):
                self.paragraphs.append(value)
            self.inp = False; self.inlead = False
    def handle_data(self, data):
        if self.intitle: self.title.append(data)
        if self.inp: self.p.append(data)
        if self.inlead: self.lead.append(data)

def rel(path, target):
    return quote(os.path.relpath(target, Path(path).parent), safe="/")
def e(s): return html.escape(str(s), quote=True)
def read_info(path):
    p = Info(); p.feed((ROOT / path).read_text(encoding="utf-8"))
    desc = "".join(p.lead).strip() or next((x for x in p.paragraphs if len(x) >= 30), "")
    return {"url": path, "title": "".join(p.title).strip(),
            "desc": desc[:220], "keywords": path.replace("/", " ").replace("-", " "),
            "category": path.split("/")[0] if "/" in path else "root"}
def link(path, target, label=None):
    return '<a href="' + rel(path, target) + '">' + e(label or INFO[target]["title"]) + "</a>"
def item(path, target):
    return "<li>" + link(path, target) + '<p class="small">' + e(INFO[target]["desc"]) + "</p></li>"
def section(path, title, description, paths):
    return '<section class="panel"><h2>' + e(title) + "</h2><p>" + e(description) + "</p><ul>" + "".join(item(path, x) for x in paths if x in INFO) + "</ul></section>"
def prefix(prefix, kinds=("current_guide",), deep=True):
    return [x for x, row in ENTRIES.items() if x.startswith(prefix) and row["kind"] in kinds and (deep or "/" not in x[len(prefix):])]
NEWS_PAGES = ("index.html", "research/index.html")
NEWS_START = "<!-- daily-news-entry:start -->"
NEWS_END = "<!-- daily-news-entry:end -->"

def news_entry():
    """Stable public indexes: never pin the reader to yesterday's issue."""
    return NEWS_START + '''<section class="panel" id="daily-news" aria-labelledby="daily-news-title">
<h2 id="daily-news-title">毎日のニュースを読む</h2>
<p>ClaudeのAI Newsと、GrokのGrokPulse。いつも読む2つの速報へ、ここから直接移動できます。</p>
<div class="grid">
<a class="card" href="https://alignlabai-rgb.github.io/claude/research/ai-news/" aria-label="ClaudeのAI News公開版を読む">
<h3>Claude · AI News</h3><p>Claude側の日次AIニュース。公開版の日付一覧から、新しい号を読めます。</p><strong>Claudeのニュースを読む →</strong></a>
<a class="card" href="https://alignlabai-rgb.github.io/GrokPulse/" aria-label="GrokPulseの最新ニュースを読む">
<h3>Grok · GrokPulse</h3><p>Xから集めた話題を日別・カテゴリ別に読む速報。噂や反応も含めて流れを追えます。</p><strong>GrokPulseのニュースを読む →</strong></a>
</div>
<p class="small">どちらも日付固定の記事ではなく、更新される公開入口へのリンクです。掲載日はリンク先で確認できます。</p>
<p class="small">旧版の補助入口：<a href="https://alignlabai-rgb.github.io/ai-news-from-x/">X Tech News（カテゴリ一覧）</a></p>
</section>''' + NEWS_END

def restore_news_entries():
    """Update only the two entry pages; do not replay the Oct 13 catalog."""
    for path in NEWS_PAGES:
        target = ROOT / path
        page = target.read_text(encoding="utf-8")
        if NEWS_START in page:
            pattern = re.escape(NEWS_START) + r".*?" + re.escape(NEWS_END)
            page, count = re.subn(pattern, lambda _: news_entry(), page, flags=re.S)
            if count != 1:
                raise ValueError(f"Expected one news entry in {path}, got {count}")
        else:
            if page.count("</header>") != 1:
                raise ValueError(f"Expected one header in {path}")
            page = page.replace("</header>", "</header>" + news_entry(), 1)
        target.write_text(page, encoding="utf-8")

def render(path, title, lead, body):
    if path in NEWS_PAGES:
        body = news_entry() + body
    css = rel(path, "workbench/knowledge/deep-dive.css")
    return '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + e(title) + '</title><link rel="stylesheet" href="' + css + '"></head><body><a class="skip" href="#main">本文へ</a><main class="shell" id="main"><header class="panel"><nav>' + link(path, "index.html", "Hub Top") + " / " + link(path, "search.html", "検索") + " / " + link(path, "foundations/site-map.html", "全ページ索引") + '</nav><h1>' + e(title) + '</h1><p class="lead">' + e(lead) + '</p><p class="small">最終更新: ' + DATE + " / 入口の編集日と、個別記事の公式確認日は別です。</p></header>" + body + '<footer class="panel"><h2>旧版と読み方</h2><p>' + link(path, "research/archive/index.html", "旧版の資料棚") + " / " + link(path, "foundations/archive-policy.html", "履歴を読む") + '</p><p><a href="https://github.com/alignlabai-rgb/claude/blob/' + BASE + "/" + quote(path, safe="/") + '">この入口の刷新前固定版</a>。過去の本文は新版に混在させていません。</p></footer></main></body></html>\n'
def write(path, title, lead, groups):
    body = "".join(section(path, *g) for g in groups)
    (ROOT / path).write_text(render(path, title, lead, body), encoding="utf-8")

def search_page(data):
    path = "search.html"
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    controls = '<section class="panel"><h2>検索</h2><label for="q">言葉・モデル名・用途</label><input id="q" type="search" placeholder="例: ontology preview 動画" style="width:100%;font:inherit;padding:12px"><label for="kind">資料の種類</label><select id="kind"><option value="current">現行ガイド・入口</option><option value="history">日次ニュース・履歴</option><option value="all">すべて</option></select><p id="count" aria-live="polite"></p><p class="small">複数語はすべて含むものを表示。曖昧検索ではありません。外部CDN・network・fetchなしで検索できます。</p></section><section class="panel"><h2>結果</h2><ul id="results"></ul><noscript><p>JavaScriptを使わない場合は<a href="foundations/site-map.html">全ページ索引</a>を使ってください。</p></noscript></section>'
    page = render(path, "知識ハブの検索", "現行の判断ガイドと当日の観測記録を分けて探せます。検索結果は根拠の正しさや鮮度を自動認定しません。", controls)
    js = """<script>
const pages=DATA,q=document.querySelector("#q"),kind=document.querySelector("#kind"),results=document.querySelector("#results");
const norm=s=>s.normalize("NFKC").toLowerCase();
function draw(){const words=norm(q.value).trim().split(/\\s+/).filter(Boolean);const mode=kind.value;const list=pages.filter(p=>{const historical=["historical_record","retained_public_review"].includes(p.kind);return (mode==="all"||(mode==="history"?historical:!historical))&&words.every(w=>norm([p.title,p.desc,p.keywords,p.url].join(" ")).includes(w));});results.replaceChildren();for(const p of list){const li=document.createElement("li"),a=document.createElement("a"),d=document.createElement("p");a.href=p.url;a.textContent=p.title;d.className="small";d.textContent=p.desc+" — "+(p.kind==="historical_record"?"日付付き観測・履歴":p.kind==="retained_public_review"?"既存公開レビュー・保持":"現行の読み口");li.append(a,d);results.append(li);}document.querySelector("#count").textContent=list.length+"件 / "+pages.length+"件";}
q.addEventListener("input",draw);kind.addEventListener("change",draw);draw();
</script>"""
    page = page.replace("</body>", js.replace("DATA", payload, 1) + "</body>")
    (ROOT / path).write_text(page, encoding="utf-8")

def main():
    global ENTRIES, INFO
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    ENTRIES = {x["path"]: x for x in raw["pages"]}
    evidence_dir = ROOT / "research/refresh-2026-10-13"
    evidence_links = "".join('<li><a href="' + quote(p.name) + '">' + e(p.name) + '</a></li>'
        for p in sorted(evidence_dir.iterdir()) if p.suffix in {".json", ".md"})
    evidence_html = '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>2026-10-13 刷新の根拠と検査資料</title><link rel="stylesheet" href="../../workbench/knowledge/deep-dive.css"></head><body><main class="shell" id="main"><header class="panel"><nav><a href="../../index.html">Hub Top</a> / <a href="../llm-tracker.html">モデル台帳</a></nav><h1>2026-10-13 刷新の根拠と検査資料</h1><p class="lead">記事の根拠、非著者の重点照合、構造検査を区別して読む資料棚です。JSONを置いたことだけでは正しさを認定しません。</p><p class="small">最終更新: 2026-10-13</p></header><section class="panel"><h2>記録の種類</h2><p>sourcesは著者が実際に読んだ一次資料とclaimの対応です。independent auditは担当記事の著者以外が重要な主張を照合した記録で、全文・全serviceの実機検証を意味しません。catalogは固定baselineの分類、model ledgerは提供面とgap、validationは実施した検査の範囲と結果です。実績でない試験案は合成教材として扱います。</p><p>公式資料の相違、source不足、account別条件は解消したふりをせず本文と記録に残します。<a href="../archive/index.html">旧版の資料棚</a>には刷新前の本文があり、保存したことは旧内容の再承認ではありません。</p></section><section class="panel"><h2>資料ファイル</h2><ul>' + evidence_links + '</ul></section></main></body></html>\n'
    evidence_note = '<section class="panel"><h2>独立照合で残る制約</h2><p>重点抽出した主張の照合であり、全段落・全リンク・実accountの認定ではありません。DeepSeek公式API資料とSenseTime公式記事は、一部の独立reviewerが再取得できませんでした。著者の実取得記録とは分け、未確認を残しています。取得失敗をサービス停止や内容の誤りの証明にはしません。</p><p>Omniの提供段階、AI Builderの契約移行、Ollamaの既定context、NVIDIAのcodeとweightsのlicenseには公式資料内・資料間の表記差があります。各guideの限定と元資料を確認してください。scopeを落としたモデルID、Claudeの定期実行と必要version、検索説明の抽出は、指摘後に修正・再確認しました。これも導入・公開・法務の承認を意味しません。</p><p>日次ニュースの実scheduleは今回変更していません。daily-news.mdにはdirect mainとPRの表記差が残り、実稼働の指示整理は記事刷新とは別の課題です。外部hostingの配備確認、全サービス実機試験、visual renderingも今回の検査範囲外です。</p></section>'
    evidence_html = evidence_html.replace('<section class="panel"><h2>資料ファイル</h2>', evidence_note + '<section class="panel"><h2>資料ファイル</h2>')
    (evidence_dir / "index.html").write_text(evidence_html, encoding="utf-8")
    for path in ("research/archive/index.html", "research/official-model-delta-2026-10-13.html", "research/refresh-2026-10-13/index.html"):
        ENTRIES[path] = {"kind": "historical_record" if "official-model-delta" in path else "navigation_or_template"}
    INFO = {p: read_info(p) for p in ENTRIES}
    write("index.html", "AI運用知識ハブ", "速報を入口に、一次根拠・仕組み・限定試験・導入判断まで読める公開の知識ハブです。Claudeだけでなく主要provider、agent、制作、開発、企業利用を扱います。", [
        ("いま何を変えるか", "現行モデルの提供面、移行期限、速報から組織への活用を先に見ます。", ["research/official-model-delta-2026-10-13.html", "research/llm-tracker.html", "research/ai-news/index.html", "research/signal-to-organizational-leverage.html"]),
        ("仕組みから学ぶ", "Ontology・Context・Graphは万能な階層ではありません。設計目的と評価条件を分けて理解します。", ["workbench/knowledge/ontology-engineering.html", "workbench/knowledge/context-engineering.html", "workbench/knowledge/graph-engineering.html", "workbench/knowledge/agent-memory.html", "research/always-on-agents-2026-10.html"]),
        ("制作と開発へ使う", "モデルの紹介だけでなく、工程、検査、手戻り、失敗の再利用を扱います。", ["creator-system/index.html", "workbench/knowledge/ai-creative.html", "workbench/knowledge/openai-codex.html", "workbench/knowledge/claude-code/index.html", "claw-stack/index.html", "Operations_Manuals/index.html"]),
        ("仕事・企業導入へ使う", "利用プラン、app内のagent、API、企業基盤を区別し、同じ名前の条件を混ぜません。", ["workbench/knowledge/microsoft-ecosystem.html", "workbench/knowledge/google-ecosystem.html", "workbench/knowledge/intelligence-matrix.html", "strategy/index.html"]),
        ("根拠と履歴を読む", "古い本文は固定版へ。日次ニュースは当日の観測として保持します。", ["research/index.html", "workbench/knowledge/index.html", "foundations/index.html", "foundations/site-map.html", "research/archive/index.html", "logs/hub-health.html"])])
    write("research/index.html", "Research：変化を、判断できる知識へ", "新しい名前・噂・期限・公式情報を分離し、使い方と次の試験までつなげます。日付付きsnapshotは当時の記録、各trackerは現行の読み口です。", [
        ("現行と速報", "今日の判断と過去の観測を区別します。", ["research/llm-tracker.html","research/official-model-delta-2026-10-13.html","research/ai-news/index.html","research/llm-radar-candidates.html"]),
        ("providerの使い分け", "app・API・coding harness・weightsの条件を追います。", [p for p in prefix("research/", deep=False) if p.endswith("-recent-updates.html")]),
        ("常設agentと知識の転用", "流行を受け手が評価できるpacketへ変換する方法です。", ["research/grok-bot-deep-dive.html","research/muse-agent-deep-dive.html","research/chatgpt-dots-deep-dive.html","research/always-on-agents-2026-10.html","research/signal-to-organizational-leverage.html","research/grok-gemini-services.html"]),
        ("産業の読み方", "未来予言や相場ではなく、一次根拠から制約・機会を読むガイド。", [p for p in prefix("research/", deep=False) if "trend-" in p or p.endswith(("industry-trends.html","highlights.html"))]),
        ("OpenClawの専門資料と履歴", "専門章も現行指南として再構成。日付付き調査は資料棚へ。", ["research/openclaw-news/index.html","research/archive/index.html"])])
    write("workbench/knowledge/index.html", "Knowledge：モデルより、用途と仕組みから", "目的に合う道具を選ぶための詳細解説です。公式事実、提供側評価、未実行の教材を分けて読みます。", [
        ("modelと入口", "現行名は台帳に、利用方法は各guideに分離します。", ["workbench/knowledge/model-generations.html"] + [p for p in prefix("workbench/knowledge/", deep=False) if Path(p).stem in ["chatgpt","openai-codex","openai-flow","claude-model","claude-mythos","claude-code","grok","grok-build","gemini","gemini-advanced","gemini-spark","gemini-omni","gemini-live","gemma","llama","mistral","deepseek","qwen","kimi","glm","china-ai-coding","phi"]]),
        ("agent・知識設計", "入力、状態、接続、権限、反復をそれぞれ設計します。", [p for p in prefix("workbench/knowledge/", deep=False) if any(w in Path(p).stem for w in ["engineering","memory","protocol","intelligence","external-ops","tools-map","hermes","openclaw"])]),
        ("制作", "素材と工程と検査をつなぐ入口です。", ["workbench/knowledge/ai-creative.html","workbench/knowledge/veo.html","workbench/knowledge/flow.html","workbench/knowledge/imagen.html","workbench/knowledge/lyria.html","workbench/knowledge/comfyui.html","workbench/knowledge/comfyui-cloud.html"]),
        ("企業・Google", "app・契約・企業platformの範囲を分けます。", [p for p in prefix("workbench/knowledge/", deep=False) if any(w in Path(p).stem for w in ["microsoft","copilot","power-","azure","google-ecosystem","ai-studio","vertex","workspace","notebook","jules","opal","anti-gravity"])]),
        ("開発・local", "Git、実行環境、小型model、harnessの具体的な使い方。", [p for p in prefix("workbench/knowledge/", deep=False) if Path(p).stem in ["git-basics","docker","mac","vscode","obsidian","cursor","base44","bonsai-8b","slm-guide","pi-coding-agent","physical-ai"]])])
    write("workbench/index.html", "Workbench：道具を、試せる仕事へ", "導入前の使い分けと限定試験、既存workflowを変えるときの注意を探せます。", [
        ("設計と選択", "仕様の確認から小さい検証へ進みます。", ["workbench/knowledge/index.html","workbench/knowledge/intelligence-matrix.html","workbench/knowledge/ai-tools-map.html","workbench/vibe-coding-guide/index.html"]),
        ("開発agent", "モデル名とharnessを区別します。", ["workbench/knowledge/claude-code/index.html","workbench/knowledge/openai-codex.html","workbench/knowledge/grok-build.html","claw-stack/index.html"]),
        ("制作・企業", "工程の評価と、契約や機能の範囲を読む。", ["creator-system/index.html","workbench/knowledge/microsoft-ecosystem.html","workbench/knowledge/google-ecosystem.html","Operations_Manuals/index.html"])])
    for path, title, lead, folder in [
        ("creator-system/index.html","Creator System：制作を再利用可能にする","生成するだけでなく、入力、工程、検査、repair、採用、公開の区切りまでを設計します。架空の収益や作品成功を実績として扱いません。","creator-system/"),
        ("demos/index.html","Demos：失敗を見つける試験教材","過去のデモを無条件に成功例とみなさず、合成fixture・比較・反証・検査範囲から使い直します。","demos/"),
        ("foundations/index.html","Foundations：根拠・品質・履歴の作法","公開知識を維持するための編集方針と再利用方法です。組織権限、秘密、canon、契約の決定を代行しません。","foundations/"),
        ("strategy/index.html","Strategy：技術を導入判断へつなげる","最新という理由だけで採用せず、課題、限定試験、停止条件、維持費から順序を決める公開の設計ガイドです。","strategy/"),
        ("Operations_Manuals/index.html","操作マニュアル：入口と作用を分ける","操作の目的、作業場所、権限、検査、停止を確認します。コマンドを読むことは実行や課金の承認ではありません。","Operations_Manuals/"),
        ("logs/index.html","Hub運用記録：確認したことと残ること","品質点検と実際の変更記録を読みます。過去の日付を今日の成果へ書き換えません。","logs/")]:
        related = ["research/archive/index.html", "foundations/site-map.html"]
        if folder == "creator-system/":
            related.insert(0, "creator-system/Claude_Design/index.html")
        write(path,title,lead,[("実用ガイド","目的に合う章を選んでください。",prefix(folder,deep=False)),("関連","現行の解説と保存版を使い分けます。",related)])
    sitemap = "foundations/site-map.html"
    groups = []
    for folder in ["research/","workbench/","creator-system/","claw-stack/","Operations_Manuals/","foundations/","strategy/","demos/","logs/","_routines/"]:
        paths = [p for p, x in ENTRIES.items() if p.startswith(folder) and x["kind"] not in {"historical_record","retained_public_review"} and p != sitemap]
        groups.append((folder.rstrip("/"),"現行ガイド・入口。旧版は資料棚へ。",paths))
    groups.append(("当日の記録と旧版","日次ニュース・過去調査・sessionsは保存版として分けています。",["research/ai-news/index.html","research/archive/index.html"]))
    write(sitemap,"全ページ索引","既存の各URLを保ち、現行指南と日付付き履歴を分けた索引です。各ページの確認範囲はその本文で確認してください。",groups)
    template_body = '<section class="panel"><h2>新しい解説の最小構成</h2><pre><code>目的 / 読者が決められること\n製品・API・weights・提供段階を分けた現行事実\n仕組みと選択肢 / 固有の具体例\n失敗条件 / 合成fixture / 未実施か実測か\n近傍の一次source / 確認日 / 未確認範囲\n関連guide / 旧本文の固定版</code></pre><p>この型は編集の補助です。すべての章を同じ一般論で埋めず、そのtopicにしかない失敗と比較を選びます。未確認値を空欄のまま残す方が、推測で埋めるより有益な場合があります。</p></section>'
    (ROOT / "foundations/page-template.html").write_text(render("foundations/page-template.html","記事テンプレート：根拠と用途から書く","最新というラベルではなく、読者が選択・試験できる本文を作るための型です。",template_body),encoding="utf-8")
    log_body = '<section class="panel"><h2>実際の区切りを記録する</h2><pre><code>日付 / 対象 / 読者への価値\n実施した変更 / 固定commitまたはPR\n実施した検査と結果 / 未検査\n修正した失敗 / 残るgap\n外部反映: local | branch | PR | main | deployment確認\n次の再確認trigger</code></pre><p>作業予定、author確認、独立レビュー、サイト配備は別の状態です。記事数やtokenを価値の証明にせず、何が読みやすくなり、どの判断を支えるかを短く書きます。個人情報、credential、内部host経路は公開記録に載せません。</p></section>'
    (ROOT / "logs/template.html").write_text(render("logs/template.html","変更記録テンプレート","実施したことと未確認のことを分離する、短い公開運用記録です。",log_body),encoding="utf-8")
    INFO = {p: read_info(p) for p in ENTRIES}
    data = []
    for p, x in ENTRIES.items():
        info = INFO[p].copy(); info["kind"] = x["kind"]; data.append(info)
    (ROOT / "search-index.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    search_page(data)
    print(json.dumps({"indexed_pages":len(data),"navigation_rebuilt":14,"external_search_dependency":False}))
if __name__ == "__main__":
    if sys.argv[1:] == ["--news-only"]:
        restore_news_entries()
    elif sys.argv[1:]:
        raise SystemExit("Usage: build-reading-navigation.py [--news-only]")
    else:
        main()
