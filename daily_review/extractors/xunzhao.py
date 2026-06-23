"""寻找低估 价格信号 + 催化剂日历提取器。

从每日简报中提取：
  涨价函/价格信号 → catalyst_tracker
  催化剂日历 → Dashboard
  海外映射 → advice
  业绩预告 → earnings_screen
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIMEOUT = 60
MAX_TOKENS = 2000
MODEL = "claude-haiku-4-5-20251001"

_EXTRACT_PROMPT = """从以下每日市场简报中提取四类信号。

简报正文:
{body}

输出 JSON:
{{
  "price_signals": [
    {{
      "product": "产品名（如CCL/MLCC/六氟化钨/氦气/碳酸锂）",
      "signal": "涨价/缺货/停产/扩产",
      "change_pct": "涨幅或跌幅%（如+30%/-5%）",
      "trigger": "触发原因（如建滔涨价函/日本酸素调涨/产能退出）",
      "affected_stocks": [{{"code": "6位代码", "name": "名称", "role": "受益/受损"}}],
      "urgency": "HIGH/MEDIUM/LOW"
    }}
  ],
  "calendar_events": [
    {{
      "date": "YYYY-MM-DD或日期范围（如2026-06-23）",
      "event": "事件名",
      "category": "科技大会/财报/政策/发射/IPO/行业会议",
      "a_stock_impact": "可能影响的A股方向（如AI算力/商业航天/半导体）",
      "importance": "HIGH/MEDIUM/LOW"
    }}
  ],
  "overseas_mapping": [
    {{
      "us_event": "美股事件（如英伟达涨跌/美光财报/苹果新品）",
      "a_stock_map": "映射A股方向",
      "direction": "利好/利空/中性"
    }}
  ],
  "earnings_signals": [
    {{
      "code": "6位代码",
      "name": "名称",
      "period": "H1/Q1/Q2",
      "signal": "暴增/扭亏/暴雷/预增/预减",
      "detail": "一句话（含关键数字如+111%）"
    }}
  ]
}}

规则:
1. 代码必须是6位纯数字，不确定不要编造。
2. price_signals 只提取明确有涨跌幅%或具体涨价金额的，不要模糊信号。
3. calendar_events 提取所有会议/发射/财报/IPO日程。
4. 只输出 JSON，不要解释。"""


def _get_client():
    from llm import _load_api_key
    from anthropic import Anthropic
    return Anthropic(api_key=_load_api_key(), timeout=TIMEOUT)


def extract(body: str, title: str = "", date_str: str = "") -> dict | None:
    if len(body) < 200:
        return None

    client = _get_client()
    prompt = _EXTRACT_PROMPT.format(body=body[:3000])

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                timeout=TIMEOUT,
            )
            parts = [block.text for block in resp.content
                     if hasattr(block, "text") and block.text]
            text = "\n".join(parts)
            break
        except Exception:
            if attempt == 2:
                return None
            continue

    data = None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        raw = m.group(0) if not m.lastindex else m.group(1)
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

    if data:
        data["_title"] = title
        data["_date"] = date_str
    return data


def inject(data: dict) -> dict[str, int]:
    """注入到各子系统。"""
    result = {"price": 0, "calendar": 0, "earnings": 0}
    if not data:
        return result

    base = Path(__file__).resolve().parent.parent / "reports"

    # Price signals → catalyst track
    ps = data.get("price_signals", []) or []
    if ps:
        out_dir = base / "catalyst" / "xunzhao_price"
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(ps):
            p["_date"] = data.get("_date", "")
            (out_dir / f"price_{data.get('_date','')}_{i}.json").write_text(
                json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        result["price"] = len(ps)

    # Calendar → Dashboard context
    cal = data.get("calendar_events", []) or []
    if cal:
        out_dir = base / "catalyst" / "calendar"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"calendar_{data.get('_date','')}.json").write_text(
            json.dumps(cal, ensure_ascii=False, indent=2), encoding="utf-8")
        result["calendar"] = len(cal)

    # Earnings → earnings_screen
    er = data.get("earnings_signals", []) or []
    if er:
        out_dir = base / "catalyst" / "earnings_signal"
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, e in enumerate(er):
            e["_date"] = data.get("_date", "")
            (out_dir / f"er_{data.get('_date','')}_{i}.json").write_text(
                json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
        result["earnings"] = len(er)

    return result


def format_summary(data: dict) -> str:
    """一句话摘要，供 Dashboard 采集管线行展示。"""
    if not data:
        return ""
    ps = len(data.get("price_signals", []) or [])
    cal = len(data.get("calendar_events", []) or [])
    er = len(data.get("earnings_signals", []) or [])
    parts = []
    if ps: parts.append(f"{ps}涨价信号")
    if cal: parts.append(f"{cal}日历事件")
    if er: parts.append(f"{er}业绩信号")
    return ", ".join(parts) if parts else "—"
