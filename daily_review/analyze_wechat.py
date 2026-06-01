"""微信公众号文章深度 AI 分析（两阶段）。

阶段一 Haiku: 逐篇拆解（核心论点+关键数据+A股标的+自选关联）
阶段二 Sonnet: 综合研判（交叉印证+确信度+行动建议）
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

MODEL_SONNET = os.getenv("DR_LLM_MODEL", "claude-sonnet-4-6-20250514")
MODEL_HAIKU = "claude-haiku-4-5-20251001"
TIMEOUT = 90
MAX_BODY_CHARS = 1200
FETCH_DELAY_MIN = 3.0
FETCH_DELAY_MAX = 6.0

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


def _get_client():
    from anthropic import Anthropic
    return Anthropic(api_key=_load_api_key(), timeout=TIMEOUT)


def _scrape_article(url: str) -> str:
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


# ============================================================
# 阶段一：逐篇深度拆解（Haiku）
# ============================================================

_S1 = """你是 A 股基本面分析师。分析以下微信公众号文章。

来源: {feed}  日期: {date}
标题: {title}
正文: {body}

输出 JSON:
{{
  "title": "原标题",
  "feed": "来源",
  "thesis": "核心论点（1-2 句，引用原文关键数据和逻辑）",
  "key_facts": ["关键事实1", "关键事实2"],
  "tickers": [{{"code": "股票代码或名称", "name": "简称", "relevance": "关联逻辑"}}],
  "category": "AI算力/半导体/AIPC/新能源/消费/宏观/地产/电力/其他",
  "relevance_score": 1-5,
  "one_liner": "一句话投资摘要"
}}
只输出 JSON。"""


def _analyze_single(client, feed: str, pub_date: str, title: str,
                    body: str) -> dict:
    prompt = _S1.format(feed=feed, date=pub_date[:10], title=title,
                        body=body or "（无正文）")
    try:
        resp = client.messages.create(
            model=MODEL_HAIKU, max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        return _extract_json(text) or {}
    except Exception as e:
        print(f"      [Haiku err] {e}")
        return {}


# ============================================================
# 阶段二：综合研判（Sonnet）
# ============================================================

_S2 = """你是 A 股基本面投资分析师。今天是 {today}。

以下是近 3 天微信公众号文章的逐篇深度拆解：

{articles_json}

我的自选股池：{watchlist}

请综合研判，输出 JSON:
{{
  "market_narrative": "当前市场核心叙事（3-4 句，共识+分歧+情绪）",
  "themes": [
    {{
      "name": "主题",
      "conviction": "高/中/低",
      "article_indices": [1,2,5],
      "feeds": ["号1","号2"],
      "thesis": "核心逻辑（引用原文数据和事件）",
      "catalyst": "近期催化及时间节点",
      "horizon": "短期/中期/长期",
      "related_stocks": ["自选股代码"],
      "risk": "主要风险"
    }}
  ],
  "cross_validation": [
    {{
      "theme": "主题",
      "consensus_view": "一致看法",
      "divergent_view": "分歧或反对意见",
      "our_take": "基于自选股持仓的判断"
    }}
  ],
  "watchlist_alerts": [
    {{
      "code": "自选股代码",
      "signal": "正面/负面/关注",
      "reason": "具体逻辑（引用原文数据）",
      "urgency": "高/中/低"
    }}
  ],
  "action_items": [
    {{
      "action": "建议操作",
      "target": "标的（尽量用自选股代码）",
      "rationale": "理由",
      "priority": 1-5
    }}
  ],
  "key_question": "当前最需要回答的关键问题",
  "summary": "200 字整体摘要"
}}
只输出 JSON。article_indices 指向上方拆解编号。"""


def _synthesize(client, articles: list[dict], today: str) -> dict:
    prompt = _S2.format(today=today,
                        articles_json=json.dumps(articles, ensure_ascii=False, indent=2),
                        watchlist=", ".join(WATCHLIST))
    try:
        resp = client.messages.create(
            model=MODEL_SONNET, max_tokens=12000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text")
        return _extract_json(text) or {}
    except Exception as e:
        print(f"  [Sonnet err] {e}")
        return {}


def _extract_json(text: str) -> dict | None:
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
        return None


# ============================================================
# 报告输出
# ============================================================

def _write_report(data: dict, single_results: list[dict],
                  today: str, fetched: int, failed: int):
    path = REPORT_DIR / f"wechat_analysis_{today}.md"
    n_feeds = len(set(a.get("feed", "") for a in single_results if a))
    buf = [
        f"# 公众号深度分析 {today}", "",
        f"> {len(single_results)} 篇 | {n_feeds} 个号 | {fetched} 篇有正文",
    ]
    if failed:
        buf[-1] += f" | {failed} 篇无正文"
    buf.append("")

    n = data.get("market_narrative", "")
    if n:
        buf.extend(["## 市场叙事", "", n, ""])

    themes = data.get("themes", [])
    if themes:
        buf.append("## 核心主题")
        buf.append("")
        for t in themes:
            conv = t.get("conviction", "·")
            emoji = {"高": "🔥", "中": "📌", "低": "👀"}.get(conv, "·")
            indices = t.get("article_indices", [])
            idx_str = ""
            if indices:
                idx_str = " （#" + ", #".join(str(i) for i in indices) + "）"
            buf.append(f"### {emoji} {t.get('name','')} （{conv}确信度）{idx_str}")
            buf.append("")
            for key, label in [("thesis", "逻辑"), ("catalyst", "催化"),
                               ("horizon", "时间维度"), ("risk", "风险")]:
                v = t.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
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
                v = c.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
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
            buf.append(f"- **P{a.get('priority','?')}** [{a.get('target','')}] "
                       f"{a.get('action','')} — {a.get('rationale','')}")
        buf.append("")

    q = data.get("key_question", "")
    if q:
        buf.extend(["## 关键问题", "", f"> {q}", ""])

    s = data.get("summary", "")
    if s:
        buf.extend(["## 整体摘要", "", s, ""])

    # 逐篇拆解详情
    buf.append("## 逐篇拆解")
    buf.append("")
    for i, sr in enumerate(single_results, 1):
        if not sr:
            continue
        feed = sr.get("feed", "?")
        title = sr.get("title", "?")
        thesis = sr.get("thesis", "")
        facts = sr.get("key_facts", [])
        tickers = sr.get("tickers", [])
        cat = sr.get("category", "?")
        score = sr.get("relevance_score", 0)
        oneliner = sr.get("one_liner", "")

        buf.append(f"### #{i} [{feed}] {title}")
        buf.append("")
        buf.append(f"**{cat}** | {'★' * score}{'☆' * (5 - score)}")
        buf.append("")
        if thesis:
            buf.append(f"**论点**: {thesis}")
            buf.append("")
        if facts:
            for f in facts:
                buf.append(f"- {f}")
            buf.append("")
        if tickers:
            buf.append("| 标的 | 关联逻辑 |")
            buf.append("|------|---------|")
            for tk in tickers:
                buf.append(f"| {tk.get('code','')} | {tk.get('relevance','')} |")
            buf.append("")
        if oneliner:
            buf.append(f"> {oneliner}")
            buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n  报告: {path}")
    return path


# ============================================================
# 主流程
# ============================================================

def main():
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=3)).isoformat()

    print(f"公众号深度分析（两阶段）| {since} ~ {today}")

    store.init_feeds_tables()
    rows = store.query_wechat_articles(since)
    if not rows:
        print("  无近期文章")
        return

    key = _load_api_key()
    if not key:
        print("  API key 不可用")
        return

    from anthropic import Anthropic
    client = Anthropic(api_key=key, timeout=TIMEOUT)

    # 抓取正文
    print(f"  抓取 {len(rows)} 篇文章正文...")
    articles_with_body = []
    for r in rows:
        url = (r.get("url") or "").strip()
        body = ""
        if url.startswith("https://mp.weixin.qq.com"):
            time.sleep(random.uniform(FETCH_DELAY_MIN, FETCH_DELAY_MAX))
            body = _scrape_article(url)
        articles_with_body.append({
            "feed": r.get("feed_source", "").strip() or "未分类",
            "date": (r.get("pub_date") or "")[:10],
            "title": r.get("title", "").strip(),
            "body": body,
        })
    fetched = sum(1 for a in articles_with_body if a["body"])
    failed = len(articles_with_body) - fetched
    print(f"  正文: {fetched} 成功, {failed} 失败")

    # 阶段一
    print(f"\n  阶段一: Haiku 逐篇拆解...")
    single_results = []
    for i, a in enumerate(articles_with_body):
        sr = _analyze_single(client, a["feed"], a["date"], a["title"], a["body"])
        single_results.append(sr)
        score = sr.get("relevance_score", 0)
        stars = "★" * score + "☆" * (5 - score)
        print(f"    [{i+1}/{len(articles_with_body)}] {stars} "
              f"{a['title'][:30]}...")

    # 阶段二
    print(f"\n  阶段二: Sonnet 综合研判...")
    data = _synthesize(client, single_results, today)
    if not data:
        print("  Sonnet 不可用")
        _write_report({}, single_results, today, fetched, failed)
        return

    _write_report(data, single_results, today, fetched, failed)
    print("  完成")


if __name__ == "__main__":
    main()
