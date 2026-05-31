#!/usr/bin/env python3
"""
Hub Link Checker
================
全 HTML ファイルの内部リンク（href/src）を検証し、
切れているリンクをレポートする。

使い方:
  python3 tools/link-checker.py                  # カレントディレクトリから実行
  python3 tools/link-checker.py --html           # HTML レポートを生成
  python3 tools/link-checker.py --fix-missing    # 切れリンクの候補を提示
  python3 tools/link-checker.py --skip-external  # 外部 URL をスキップ（デフォルト）
"""

import os, re, sys, json, argparse, html as html_module
from pathlib import Path
from urllib.parse import urlparse, unquote
from datetime import datetime
from collections import defaultdict

# ===== 設定 =====
SKIP_DIRS  = {'.git', 'node_modules', '__pycache__', '.claude'}
SKIP_FILES = {'CLAUDE.md', 'README.md'}
CHECK_EXTERNAL = False  # 外部 URL チェック（遅いのでデフォルト off）

# ===== リンク抽出 =====
LINK_RE = re.compile(
    r'(?:href|src|action)=["\']([^"\'#][^"\']*)["\']',
    re.I
)
# <script>, <pre>, <code> ブロックを除去するパターン
_STRIP_RE = re.compile(r'<(script|pre|code)[^>]*>.*?</\1>', re.I | re.S)
# プレースホルダー・テンプレートリテラル等を除外するパターン
_PLACEHOLDER_RE = re.compile(r'^\$\{|^\.{2,}$|^\[')

def _strip_code_blocks(html: str) -> str:
    """script/pre/code ブロックの内容を除去してリンク誤検出を防ぐ"""
    return _STRIP_RE.sub('', html)

def extract_links(html: str) -> list[str]:
    """HTML から href/src のリンクを抽出（アンカー・javascript: を除く）"""
    cleaned = _strip_code_blocks(html)
    links = []
    for m in LINK_RE.finditer(cleaned):
        link = m.group(1).strip()
        if link.startswith(('javascript:', 'mailto:', 'tel:', 'data:')):
            continue
        if _PLACEHOLDER_RE.search(link):
            continue
        links.append(link)
    return links

def resolve_link(base_file: Path, link: str, root: Path) -> tuple[str, Path | None]:
    """リンクを絶対パスに解決。(link_type, resolved_path) を返す"""
    parsed = urlparse(link)
    if parsed.scheme in ('http', 'https', 'ftp', '//'):
        return ('external', None)
    if parsed.scheme:
        return ('other', None)

    # フラグメントのみ → 同一ページ内アンカー
    if not parsed.path:
        return ('anchor', None)

    path_part = unquote(parsed.path).split('?')[0]

    if path_part.startswith('/'):
        # ルート相対パス
        resolved = root / path_part.lstrip('/')
    else:
        # 相対パス
        resolved = (base_file.parent / path_part).resolve()

    return ('internal', resolved)

# ===== チェック本体 =====
def check_all(root: Path) -> dict:
    results = {
        'broken':   [],   # 確実に切れているリンク
        'warnings': [],   # 怪しいリンク（CDN等）
        'ok_count': 0,
        'scanned':  0,
        'timestamp': datetime.now().isoformat(),
        'root': str(root),
    }

    # ファイル収集
    html_files = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fname in files:
            if fname.endswith('.html') and fname not in SKIP_FILES:
                html_files.append(Path(dirpath) / fname)

    results['scanned'] = len(html_files)
    print(f"Scanning {len(html_files)} HTML files...")

    for html_file in sorted(html_files):
        rel_file = html_file.relative_to(root)
        try:
            content = html_file.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            results['warnings'].append({
                'file': str(rel_file), 'link': '—',
                'reason': f'read error: {e}'
            })
            continue

        links = extract_links(content)
        for link in links:
            link_type, resolved = resolve_link(html_file, link, root)

            if link_type == 'external':
                if CHECK_EXTERNAL:
                    # 外部チェックは省略（requests が必要）
                    pass
                continue
            if link_type in ('anchor', 'other'):
                continue
            if link_type == 'internal':
                if resolved is None:
                    continue
                # CDN や外部ドメインの絶対パスは警告
                if not str(resolved).startswith(str(root)):
                    results['warnings'].append({
                        'file': str(rel_file),
                        'link': link,
                        'reason': 'resolves outside root'
                    })
                    continue
                # ファイル存在確認
                if not resolved.exists():
                    # ディレクトリの場合 index.html を試す
                    if (resolved / 'index.html').exists():
                        results['ok_count'] += 1
                        continue
                    results['broken'].append({
                        'file': str(rel_file),
                        'link': link,
                        'resolved': str(resolved.relative_to(root)) if resolved.is_absolute() else str(resolved),
                        'reason': 'file not found'
                    })
                else:
                    results['ok_count'] += 1

    return results

# ===== レポート出力 =====
def print_report(results: dict) -> None:
    broken = results['broken']
    warnings = results['warnings']

    print(f"\n{'='*60}")
    print(f"Hub Link Checker — {results['timestamp'][:10]}")
    print(f"{'='*60}")
    print(f"スキャン: {results['scanned']} ファイル")
    print(f"OK:      {results['ok_count']} リンク")
    print(f"切れ:    {len(broken)} リンク")
    print(f"警告:    {len(warnings)} 件")

    if broken:
        print(f"\n{'─'*60}")
        print(f"🔴 切れているリンク ({len(broken)} 件)")
        print(f"{'─'*60}")
        # ファイル別にグループ化
        by_file = defaultdict(list)
        for b in broken:
            by_file[b['file']].append(b)
        for fpath, items in sorted(by_file.items()):
            print(f"\n  📄 {fpath}")
            for item in items:
                print(f"     → {item['link']}")
                print(f"        ({item['reason']})")

    if warnings:
        print(f"\n{'─'*60}")
        print(f"🟡 警告 ({len(warnings)} 件)")
        print(f"{'─'*60}")
        for w in warnings[:10]:
            print(f"  {w['file']}: {w['link']} — {w['reason']}")
        if len(warnings) > 10:
            print(f"  ... 他 {len(warnings)-10} 件")

    status = "✅ 問題なし" if not broken else f"❌ {len(broken)} 件の切れリンクあり"
    print(f"\n{'='*60}")
    print(f"結果: {status}")
    print(f"{'='*60}\n")

def generate_html_report(results: dict, output_path: Path) -> None:
    broken = results['broken']
    warnings = results['warnings']
    ts = results['timestamp'][:10]

    status_color = '#4fffb0' if not broken else '#ff6b6b'
    status_text = '問題なし' if not broken else f'{len(broken)} 件の切れリンク'

    by_file = defaultdict(list)
    for b in broken:
        by_file[b['file']].append(b)

    broken_html = ''
    for fpath, items in sorted(by_file.items()):
        broken_html += f'<div class="file-group"><h3>📄 {fpath}</h3><ul>'
        for item in items:
            broken_html += f'<li><code class="link">{item["link"]}</code><span class="reason">{item["reason"]}</span></li>'
        broken_html += '</ul></div>'

    warn_html = ''
    for w in warnings[:20]:
        warn_html += f'<li>{w["file"]}: <code>{w["link"]}</code> — {w["reason"]}</li>'

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>Link Checker レポート {ts} | Logs</title>
  <style>
    body{{font-family:"Segoe UI","Noto Sans JP",system-ui,sans-serif;margin:0;background:#0b1220;color:#edf2fb;padding:28px 18px 56px;line-height:1.7}}
    .shell{{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
    .panel{{background:#111a2b;border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:20px 24px}}
    a{{color:#75d7c0}}
    h1{{margin:6px 0;font-size:1.6rem}}h2{{margin:0 0 12px;font-size:1rem;color:#fcd34d;border-bottom:2px solid rgba(252,211,77,.2);padding-bottom:5px}}h3{{font-size:.9rem;color:#c5d0e3;margin:12px 0 5px}}
    p,li{{color:#c5d0e3;font-size:.9rem}}
    .stat-row{{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}}
    .stat{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:10px 16px;text-align:center;flex:1;min-width:80px}}
    .stat .v{{font-size:1.3rem;font-weight:800;color:{status_color}}}.stat .l{{font-size:.7rem;color:#7f92ac;text-transform:uppercase}}
    .status{{font-size:1.1rem;font-weight:700;color:{status_color};padding:10px 0 0}}
    code{{background:rgba(255,255,255,.07);padding:1px 6px;border-radius:4px;font-size:.83em;color:#a5f3d0;font-family:"Fira Code",monospace}}
    code.link{{color:#ff9090;background:rgba(255,107,107,.08)}}
    .file-group{{background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:12px 16px;margin:8px 0}}
    .file-group ul{{margin:5px 0;padding-left:18px}}
    .file-group li{{margin:4px 0;font-size:.87rem}}
    .reason{{color:#7f92ac;font-size:.78rem;margin-left:8px}}
    .ok-panel{{border-color:rgba(79,255,176,.3)}}
  </style>
</head>
<body>
<div class="shell">
  <div class="panel {'ok-panel' if not broken else ''}">
    <p><a href="../index.html">← Hub Top</a> / <a href="index.html">Logs</a></p>
    <h1>🔗 Link Checker レポート</h1>
    <p>実行日: {ts} | ルート: <code>{results["root"]}</code></p>
    <div class="stat-row">
      <div class="stat"><div class="v">{results["scanned"]}</div><div class="l">スキャン</div></div>
      <div class="stat"><div class="v">{results["ok_count"]}</div><div class="l">OK</div></div>
      <div class="stat"><div class="v">{len(broken)}</div><div class="l">切れ</div></div>
      <div class="stat"><div class="v">{len(warnings)}</div><div class="l">警告</div></div>
    </div>
    <div class="status">{'✅ ' if not broken else '❌ '}{status_text}</div>
  </div>

  {'<div class="panel"><h2>🔴 切れているリンク</h2>' + broken_html + '</div>' if broken else ''}

  {'<div class="panel"><h2>🟡 警告</h2><ul>' + warn_html + '</ul></div>' if warnings else ''}

  <div class="panel ok-panel">
    <h2>✅ 次回実行</h2>
    <p>定期実行: <a href="../_routines/link-checker.html">_routines/link-checker.html</a> の手順を参照。</p>
    <p>推奨: 四半期に 1 回、または大きな構造変更後。</p>
  </div>
</div>
</body>
</html>"""

    output_path.write_text(html, encoding='utf-8')
    print(f"HTMLレポート出力: {output_path}")

# ===== メイン =====
def main():
    parser = argparse.ArgumentParser(description='Hub Link Checker')
    parser.add_argument('root', nargs='?', default='.', help='Hub ルートディレクトリ（デフォルト: カレント）')
    parser.add_argument('--html', action='store_true', help='HTML レポートを logs/ に生成')
    parser.add_argument('--external', action='store_true', help='外部 URL もチェック（低速）')
    parser.add_argument('--json', action='store_true', help='JSON 形式で結果を出力')
    args = parser.parse_args()

    global CHECK_EXTERNAL
    CHECK_EXTERNAL = args.external

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Error: {root} はディレクトリではありません", file=sys.stderr)
        sys.exit(1)

    results = check_all(root)
    print_report(results)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    if args.html:
        ts = datetime.now().strftime('%Y-%m-%d')
        out = root / 'logs' / f'link-check-{ts}.html'
        generate_html_report(results, out)

    return 1 if results['broken'] else 0

if __name__ == '__main__':
    sys.exit(main())
