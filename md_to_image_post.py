"""MD → HTML → Playwright 渲染 → PNG。
相比 PIL 手绘方案：全彩、表格专业、无重影、字体正常。
"""
import sys, io, json, base64, re
from pathlib import Path

import markdown
import requests
from playwright.sync_api import sync_playwright

URL = 'http://data.xiaoxinren.cn:9003/Api/Agent/PostDaily'

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", sans-serif;
  font-size: 15px; line-height: 1.7; color: #1a1a1a;
  background: #fff; padding: 40px 48px; max-width: 880px; margin: 0 auto;
}
h1 { font-size: 22px; color: #111; border-bottom: 2px solid #2563eb; padding-bottom: 8px; margin: 28px 0 14px; }
h2 { font-size: 19px; color: #1e40af; margin: 24px 0 10px; padding-left: 8px; border-left: 3px solid #2563eb; }
h3 { font-size: 16px; color: #333; margin: 18px 0 8px; }
p { margin: 6px 0 10px; }
blockquote { border-left: 3px solid #94a3b8; padding: 6px 14px; margin: 10px 0; color: #555; background: #f8fafc; font-size: 14px; }
code { background: #f1f5f9; padding: 1px 5px; border-radius: 3px; font-size: 13px; font-family: "Cascadia Code", "Fira Code", Consolas, monospace; }
pre { background: #1e293b; color: #e2e8f0; padding: 14px 18px; border-radius: 6px; overflow-x: auto; margin: 10px 0; font-size: 13px; line-height: 1.5; }
pre code { background: none; padding: 0; color: inherit; }
ul, ol { margin: 6px 0 10px 22px; }
li { margin: 2px 0; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 16px 0; }
strong { color: #c2410c; }
a { color: #2563eb; text-decoration: none; }

table { width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 14px; }
th { background: #2563eb; color: #fff; font-weight: 600; padding: 8px 10px; text-align: left; white-space: nowrap; }
td { padding: 7px 10px; border-bottom: 1px solid #e2e8f0; }
tr:nth-child(even) td { background: #f8fafc; }
tr:hover td { background: #eff6ff; }

details { margin: 8px 0; padding: 8px 14px; background: #f8fafc; border-radius: 4px; font-size: 14px; }
summary { cursor: pointer; color: #2563eb; font-weight: 500; }
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>"""


def md_to_image(md_path: str, max_width=880) -> bytes:
    text = Path(md_path).read_text(encoding='utf-8')

    # MD → HTML
    md_body = markdown.markdown(
        text,
        extensions=['tables', 'fenced_code', 'sane_lists'],
    )
    html = HTML_TEMPLATE.format(css=CSS, body=md_body)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": max_width + 80, "height": 800})
        page.set_content(html, wait_until="networkidle")
        # 获取完整页面高度
        page.set_viewport_size({"width": max_width + 80, "height": 6000})
        height = page.evaluate("document.body.scrollHeight")
        page.set_viewport_size({"width": max_width + 80, "height": height + 20})
        buf = page.screenshot(full_page=True, type="png")
        browser.close()
        return buf


if __name__ == '__main__':
    md_path = sys.argv[1] if len(sys.argv) > 1 else 'daily_review/reports/advice/advice_2026-06-16.md'

    print(f'Converting {md_path} to PNG (MD→HTML→Playwright)...')
    png_bytes = md_to_image(md_path)
    print(f'PNG size: {len(png_bytes)} bytes ({len(png_bytes)/1024:.1f} KB)')

    b64 = base64.b64encode(png_bytes).decode()
    print(f'Base64 size: {len(b64)} chars')

    print(f'POSTing to {URL}...')
    r = requests.post(URL, data=json.dumps(b64), headers={'Content-Type': 'application/json'}, timeout=60)
    print(f'status={r.status_code} body={r.text[:300]}')
    print('Done.')
