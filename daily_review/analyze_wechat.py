"""微信公众号文章 AI 分析。

读取 wechat_articles 表中近期文章，用 Claude 做摘要、打标签、
关联 A 股标的，输出 Markdown 摘要报告。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import store
from config import REPORT_DIR, WATCHLIST

MODEL = os.getenv("DR_LLM_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = 60
MAX_TOKENS = 4000


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def _build_ctx(rows: list[dict]) -> str:
    by_feed: dict[str, list[dict]] = {}
    for r in rows:
        by_feed.setdefault(r["feed_source"] or "未知", []).append(r)
    lines = []
    for feed, articles in by_feed.items():
        lines.append(f"\n### {feed}")
        for a in articles:
            t = a["pub_date"] or "·"
            title = a["title"]
            desc = (a.get("description") or "")[:200]
            lines.append(f"- [{t[:10]}] {title}")
            if desc:
                lines.append(f"  {desc}")
    return "\n".join(lines)


def analyze(rows: list[dict], today: str) -> dict:
    api_key = _load_api_key()
    if not api_key or not rows:
        return {}
    try:
        from anthropic import Anthropic
    except ImportError:
        print("  [WARN] 未安装 anthropic，跳过公众号 AI 分析")
        return {}

    ctx = _build_ctx(rows)
    prompt = (
        f"今天是 {today}。以下是最近抓取的微信公众号文章列表。\n\n"
        f"{ctx}\n\n"
        "请做以下分析，只输出 JSON：\n"
        "1. topics: 提取 3-8 个跨公众号重复讨论的核心主题，"
        "每个标注提及频率（高/中/低）和涉及的公众号名\n"
        "2. cross_ref: 同一主题被多个公众号同时讨论的，简要说明各号角度差异\n"
        "3. tickers: 文章中明确提及的 A 股相关公司/标的（如有），标注对应主题\n"
        "4. summary: 200 字以内整体摘要（聚焦投资/产业洞察）\n\n"
        '{"topics": [{"name": "", "freq": "", "feeds": []}], '
        '"cross_ref": "", '
        '"tickers": [{"code": "", "theme": ""}], '
        '"summary": ""}\n\n'
        "只输出 JSON，不要额外说明。若某字段无相关内容则用空数组/空字符串。"
    )
    try:
        client = Anthropic(api_key=api_key, timeout=TIMEOUT)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )
        data = _extract_json(text)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"  [WARN] 公众号 AI 分析失败: {e}")
        return {}


def _extract_json(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _write_report(data: dict, rows: list[dict], today: str):
    path = REPORT_DIR / f"wechat_analysis_{today}.md"
    buf = [f"# 公众号 AI 分析 {today}", "", f"> 分析 {len(rows)} 篇文章", ""]

    s = data.get("summary", "")
    if s:
        buf.append("## 整体摘要")
        buf.append("")
        buf.append(s)
        buf.append("")

    topics = data.get("topics", [])
    if topics:
        buf.append("## 核心主题")
        buf.append("")
        buf.append("| 主题 | 热度 | 提及公众号 |")
        buf.append("|------|------|-----------|")
        for t in topics:
            buf.append(f"| {t.get('name','·')} | {t.get('freq','·')} | "
                       f"{', '.join(t.get('feeds',[]))} |")
        buf.append("")

    cross = data.get("cross_ref", "")
    if cross:
        buf.append("## 交叉印证")
        buf.append("")
        buf.append(cross)
        buf.append("")

    tickers = data.get("tickers", [])
    if tickers:
        buf.append("## 关联标的")
        buf.append("")
        buf.append("| 标的 | 对应主题 |")
        buf.append("|------|---------|")
        for tk in tickers:
            buf.append(f"| {tk.get('code','·')} | {tk.get('theme','·')} |")
        buf.append("")

    buf.append("## 文章列表")
    buf.append("")
    by_feed: dict[str, list[dict]] = {}
    for r in rows:
        by_feed.setdefault(r["feed_source"] or "未知", []).append(r)
    for feed in sorted(by_feed.keys()):
        buf.append(f"### {feed}")
        for a in by_feed[feed]:
            t = a["pub_date"][:10] if a.get("pub_date") else "·"
            title = a["title"]
            url = a.get("url", "")
            if url:
                buf.append(f"- [{title}]({url}) ({t})")
            else:
                buf.append(f"- {title} ({t})")
        buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    print(f"  AI 分析报告: {path}")
    return path


def main():
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=3)).isoformat()

    print(f"公众号 AI 分析 | {since} ~ {today}")

    store.init_feeds_tables()
    rows = store.query_wechat_articles(since)
    if not rows:
        print("  无近期文章")
        return

    today_rows = [r for r in rows if (r.get("pub_date") or "")[:10] == today]
    analyze_rows = today_rows if len(today_rows) >= 3 else rows[:30]

    print(f"  分析 {len(analyze_rows)} 篇（当天 {len(today_rows)} 篇）")

    data = analyze(analyze_rows, today)
    if not data:
        print("  AI 分析不可用，写出空报告")
        _write_report({}, rows, today)
        return

    _write_report(data, rows, today)
    print("  完成")


if __name__ == "__main__":
    main()
