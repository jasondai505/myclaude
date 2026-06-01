"""微信公众号文章深度 AI 分析。

逐篇抓取文章正文 → Sonnet 深度推理 → 主题聚合 → 交叉印证 → 自选股映射 → 行动建议。
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))
import store
from config import REPORT_DIR, WATCHLIST

MODEL = os.getenv("DR_LLM_MODEL", "claude-sonnet-4-6-20250514")
TIMEOUT = 120
MAX_TOKENS = 16000
FETCH_DELAY_MIN = 3.0
FETCH_DELAY_MAX = 6.0
MAX_BODY_CHARS = 1200

UA_POOL = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.43",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0.46",
    "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 Chrome/118.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.42",
]


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


def _scrape_article(url: str) -> str:
    """抓取单篇微信文章正文。"""
    headers = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    [skip] {e}")
        return ""

    m = re.search(r'id="js_content"[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        m = re.search(r'class="rich_media_content[^"]*"[^>]*>(.*?)</div>',
                      html, re.DOTALL)
    if not m:
        return ""

    text = m.group(1)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"微信扫一扫.*$", "", text)
    text = re.sub(r"关注该公众号.*$", "", text)

    return text[:MAX_BODY_CHARS]


def _build_ctx(rows: list[dict]) -> tuple[str, int, int]:
    by_feed: dict[str, list[dict]] = {}
    for r in rows:
        feed = r.get("feed_source", "").strip() or "未分类"
        by_feed.setdefault(feed, []).append(r)

    lines = []
    fetched = 0
    failed = 0
    total = len(rows)

    for feed, articles in by_feed.items():
        lines.append(f"\n## {feed}（{len(articles)} 篇）")
        for i, a in enumerate(articles, 1):
            t = (a.get("pub_date") or "")[:10]
            title = a.get("title", "").strip()
            url = a.get("url", "").strip()
            lines.append(f"\n### [{feed}#{i}] {title}")
            if t:
                lines.append(f"日期: {t}")

            if url and url.startswith("https://mp.weixin.qq.com"):
                delay = random.uniform(FETCH_DELAY_MIN, FETCH_DELAY_MAX)
                time.sleep(delay)
                body = _scrape_article(url)
                if body:
                    lines.append(f"正文: {body}")
                    fetched += 1
                    print(f"    [{fetched+failed}/{total}] {title[:30]}... "
                          f"({len(body)}字)")
                else:
                    failed += 1
                    print(f"    [{fetched+failed}/{total}] {title[:30]}... "
                          f"无内容")

    return "\n".join(lines), fetched, failed


def analyze(rows: list[dict], today: str, ctx: str = "") -> dict:
    api_key = _load_api_key()
    if not api_key or not rows:
        return {}
    try:
        from anthropic import Anthropic
    except ImportError:
        print("  [WARN] 未安装 anthropic")
        return {}

    if not ctx:
        ctx, _, _ = _build_ctx(rows)

    watchlist_str = ", ".join(WATCHLIST)

    prompt = f"""你是 A 股基本面投资分析师。今天是 {today}。
以下是近 3 天微信公众号文章及正文内容。

我的自选股池：{watchlist_str}

---
{ctx}
---

请深度分析，输出 JSON：

{{
  "market_narrative": "当前市场核心叙事（3-4 句，点明共识、分歧与情绪）",
  "themes": [
    {{
      "name": "主题",
      "conviction": "高/中/低",
      "feeds": ["号1","号2"],
      "thesis": "核心逻辑（引用原文关键信息，50-80 字）",
      "catalyst": "近期催化事件及时间节点",
      "horizon": "短期/中期/长期",
      "related_stocks": ["自选股代码"],
      "risk": "主要风险或反面逻辑"
    }}
  ],
  "cross_validation": [
    {{
      "theme": "主题",
      "consensus_view": "多号一致看法（引用原文差异角度）",
      "divergent_view": "不同角度、反对意见或被忽略的视角",
      "our_take": "基于自选股持仓的判断"
    }}
  ],
  "watchlist_alerts": [
    {{
      "code": "自选股代码",
      "signal": "正面/负面/关注",
      "reason": "原文具体逻辑（引用文章中的关键信息，50 字）",
      "urgency": "高/中/低"
    }}
  ],
  "action_items": [
    {{
      "action": "建议操作",
      "target": "标的或板块（尽量用自选股代码）",
      "rationale": "理由（引用原文支撑）",
      "priority": 1-5
    }}
  ],
  "key_question": "当前最需要回答的关键问题（1 个）",
  "summary": "200 字整体摘要"
}}

规则：
- 引用原文中的具体数据、事件、逻辑，不要泛泛而谈
- watchlist_alerts 仅当文章与自选股有明确关联时才输出
- action_items 最多 5 条，按 priority 降序
- 只输出 JSON"""

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
        import traceback
        print(f"  [WARN] AI 分析失败: {e}")
        traceback.print_exc()
        return {}


def _extract_json(text: str) -> dict | None:
    # 尝试提取 JSON from markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON 解析失败: {e}")
        print(f"  原文前200字符: {text[:200]}")
        return None


def _write_report(data: dict, rows: list[dict], today: str,
                  fetched: int = 0, failed: int = 0):
    path = REPORT_DIR / f"wechat_analysis_{today}.md"
    n_feeds = len(set(r.get("feed_source", "") for r in rows))
    status = f"{len(rows)} 篇 | {n_feeds} 个来源"
    if fetched:
        status += f" | {fetched} 篇有正文"
    if failed:
        status += f" | {failed} 篇无正文"
    buf = [f"# 公众号深度分析 {today}", "", f"> {status}", ""]

    narrative = data.get("market_narrative", "")
    if narrative:
        buf.append("## 市场叙事")
        buf.append("")
        buf.append(narrative)
        buf.append("")

    themes = data.get("themes", [])
    if themes:
        buf.append("## 核心主题")
        buf.append("")
        for t in themes:
            conv = t.get("conviction", "·")
            emoji = {"高": "🔥", "中": "📌", "低": "👀"}.get(conv, "·")
            buf.append(f"### {emoji} {t.get('name','')} （{conv}确信度）")
            buf.append("")
            for key, label in [("thesis", "逻辑"), ("catalyst", "催化"),
                               ("horizon", "时间维度"), ("risk", "风险")]:
                val = t.get(key, "")
                if val:
                    buf.append(f"- **{label}**: {val}")
            feeds = t.get("feeds", [])
            if feeds:
                buf.append(f"- **来源**: {', '.join(feeds)}")
            stocks = t.get("related_stocks", [])
            if stocks:
                buf.append(f"- **关联标的**: {', '.join(stocks)}")
            buf.append("")

    cross = data.get("cross_validation", [])
    if cross:
        buf.append("## 交叉验证")
        buf.append("")
        for c in cross:
            buf.append(f"### {c.get('theme', '')}")
            buf.append("")
            for key, label in [("consensus_view", "一致看法"),
                               ("divergent_view", "分歧/补充"),
                               ("our_take", "我们的判断")]:
                val = c.get(key, "")
                if val:
                    buf.append(f"- **{label}**: {val}")
            buf.append("")

    alerts = data.get("watchlist_alerts", [])
    if alerts:
        buf.append("## 自选股预警")
        buf.append("")
        buf.append("| 代码 | 信号 | 逻辑 | 紧迫度 |")
        buf.append("|------|------|------|--------|")
        for a in alerts:
            sig = a.get("signal", "·")
            sig_emoji = {"正面": "🟢", "负面": "🔴", "关注": "🟡"}.get(sig, "·")
            buf.append(f"| {a.get('code','')} | {sig_emoji} {sig} | "
                       f"{a.get('reason','')} | {a.get('urgency','')} |")
        buf.append("")

    actions = data.get("action_items", [])
    if actions:
        buf.append("## 行动建议")
        buf.append("")
        for a in sorted(actions, key=lambda x: x.get("priority", 99)):
            buf.append(f"- **P{a.get('priority', '?')}** [{a.get('target','')}] "
                       f"{a.get('action','')} — {a.get('rationale','')}")
        buf.append("")

    q = data.get("key_question", "")
    if q:
        buf.append("## 关键问题")
        buf.append("")
        buf.append(f"> {q}")
        buf.append("")

    s = data.get("summary", "")
    if s:
        buf.append("## 整体摘要")
        buf.append("")
        buf.append(s)
        buf.append("")

    buf.append("## 分析文章")
    buf.append("")
    by_feed: dict[str, list[dict]] = {}
    for r in rows:
        by_feed.setdefault(r.get("feed_source", "").strip() or "未分类", []).append(r)
    for feed in sorted(by_feed.keys()):
        buf.append(f"### {feed}")
        for a in by_feed[feed]:
            t = (a.get("pub_date") or "")[:10]
            title = a.get("title", "").strip()
            url = a.get("url", "").strip()
            if url:
                buf.append(f"- [{title}]({url}) ({t})")
            else:
                buf.append(f"- {title} ({t})")
        buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n  报告: {path}")
    return path


def main():
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=3)).isoformat()

    print(f"公众号深度分析 | {since} ~ {today}")
    print("  抓取文章正文...")

    store.init_feeds_tables()
    rows = store.query_wechat_articles(since)
    if not rows:
        print("  无近期文章")
        return

    n_feeds = len(set(r.get("feed_source", "") for r in rows))
    url_count = sum(1 for r in rows
                    if (r.get("url") or "").startswith("https://mp.weixin.qq.com"))
    print(f"  共 {len(rows)} 篇（{n_feeds} 个号），{url_count} 篇有链接")

    ctx, fetched, failed = _build_ctx(rows)
    print(f"  正文: {fetched} 成功, {failed} 失败")

    data = analyze(rows, today, ctx)
    if not data:
        print("  AI 分析不可用")
        _write_report({}, rows, today, fetched, failed)
        return

    _write_report(data, rows, today, fetched, failed)
    print("  完成")


if __name__ == "__main__":
    main()
