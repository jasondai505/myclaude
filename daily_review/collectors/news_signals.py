"""个股新闻轻量信号提取 — Haiku 扫描新闻标题+摘要，标记边际变化。

在 news collector 之后运行，输出 feeds/news_signals_{date}.md。
成本极低：~$0.01/天（Haiku 批量扫描）。

用法:
    python -m daily_review.collectors.news_signals
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import store
from .base import fmt_iso, FEEDS_DIR

SOURCE_NAME = "news_signals"
BATCH_SIZE = 30
MAX_CHARS_PER_ITEM = 200

_SIGNAL_PROMPT = """你是A股研究员。从以下个股新闻列表中，只提取「有真实边际变化」的条目，返回JSON数组。

边际变化的定义（满足任一即可）：
- 订单/合同/中标（金额或客户有变化）
- 产品涨价/降价/产能变化
- 政策利好/利空（产业政策、监管、关税等）
- 业绩预告/修正（超预期或miss）
- 技术突破/新产品/新产线投产
- 大股东增减持/回购/定增/重组
- 行业供需格局变化（缺货/过剩）

排除（不算边际变化）：
- 股价涨跌/技术分析类
- 例行公告/股东大会通知
- 已发生事件的重复报道
- 机构调研/路演信息（那是另一个系统的事）
- 纯概念炒作无实质内容

## 新闻列表
{news_text}

## 输出格式
返回JSON数组（只返回JSON），每条：
{{"code":"6位代码","title":"原标题(<=50字)","change":"边际变化一句话(<=40字)","type":"订单/涨价/政策/业绩/技术/增减持/供需/其他","direction":"利好/利空/中性","confidence":"高/中/低"}}
最多返回15条，accuracy优先，不确定的宁可漏掉。"""


def _process_one_day(today: str) -> dict:
    feed_path = FEEDS_DIR / "news" / f"news_{today}.md"
    if not feed_path.exists():
        return {"last_date": today, "status": "skip",
                "message": f"news_{today}.md 未生成，跳过", "signal_count": 0}

    text = feed_path.read_text(encoding="utf-8")
    items = _extract_news_items(text)
    if not items:
        return {"last_date": today, "status": "ok",
                "message": "新闻列表为空", "signal_count": 0}

    all_signals = []
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        news_text = "\n".join(
            f"{j+1}. [{it['code']}] {it['title'][:120]} — {it['content'][:MAX_CHARS_PER_ITEM]}"
            for j, it in enumerate(batch)
        )
        signals = _haiku_scan(news_text)
        all_signals.extend(signals or [])

    seen = set()
    unique = []
    for s in all_signals:
        key = s.get("code", "") + s.get("title", "")[:30]
        if key not in seen:
            seen.add(key)
            unique.append(s)

    out_path = FEEDS_DIR / "news_signals" / f"news_signals_{today}.md"
    _write_signals_md(out_path, unique, today)
    return {"last_date": today, "status": "ok",
            "message": f"{len(unique)}条边际信号/{len(items)}条新闻",
            "signal_count": len(unique)}


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    from .base import daterange, fmt_iso

    total_signals = 0
    msgs = []
    last_result = None
    for d in daterange(since, until):
        last_result = _process_one_day(d.isoformat())
        total_signals += last_result.get("signal_count", 0)
        msgs.append(last_result.get("message", ""))

    if last_result is None:
        return {"last_date": fmt_iso(until), "status": "skip",
                "message": "日期范围为空", "signal_count": 0}

    last_result["signal_count"] = total_signals
    last_result["message"] = f"{len(msgs)}天共{total_signals}条 | {'; '.join(msgs[-3:])}"
    last_result["last_date"] = fmt_iso(until)
    store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "ok",
                                last_result["message"], total_signals)
    return last_result


def _extract_news_items(text: str) -> list[dict]:
    """从 feed.md 解析新闻条目。"""
    items = []
    current_code = ""
    for line in text.split("\n"):
        m = re.match(r"^## (\d{6})", line)
        if m:
            current_code = m.group(1)
            continue
        m = re.match(r"^- \*\*(.+?)\*\* \[(.+?)\] (.+)", line)
        if m and current_code:
            items.append({
                "code": current_code,
                "time": m.group(1),
                "source": m.group(2),
                "title": m.group(3),
                "content": "",
            })
        elif line.startswith("    > ") and items:
            items[-1]["content"] = line[6:].strip()[:200]
    return items


def _haiku_scan(news_text: str) -> list[dict] | None:
    try:
        from daily_review.roles import get_client, get_model
    except ImportError:
        return None

    prompt = _SIGNAL_PROMPT.format(news_text=news_text[:8000])
    try:
        client = get_client("scan", timeout=60)
        model = get_model("scan")
        resp = client.messages.create(
            model=model, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"}, timeout=60,
        )
        text = "".join(
            block.text for block in resp.content if hasattr(block, "text") and block.text
        )
        return _parse_json(text)
    except Exception as e:
        print(f"  [news_signals] Haiku失败: {e}")
        return None


def _parse_json(text: str) -> list[dict] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _write_signals_md(path: Path, signals: list[dict], today: str):
    buf = [
        f"# 个股新闻边际信号 {today}",
        f"\n> {len(signals)}条边际变化 | 自动提取于 {datetime.now().strftime('%H:%M')}",
        "",
    ]
    if not signals:
        buf.append("_今日新闻无显著边际变化信号。_")
    else:
        buf.append("| 代码 | 标题 | 变化 | 类型 | 方向 | 置信度 |")
        buf.append("|------|------|------|------|:--:|:-----:|")
        for s in signals:
            buf.append(
                f"| {s.get('code','')} | {s.get('title','')[:40]} | "
                f"{s.get('change','')[:40]} | {s.get('type','')} | "
                f"{'🔴' if s.get('direction')=='利空' else '🟢' if s.get('direction')=='利好' else '➖'} | "
                f"{s.get('confidence','')} |"
            )
    buf.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(buf), encoding="utf-8")
