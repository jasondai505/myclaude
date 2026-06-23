"""韭研脱水研报 事件提取器。

每篇含 3-4 个卖方研报主题。提取：催化事件、供应链环节、标的映射、催化类型。
输出喂给 catalyst_screen 的 M/S/N/U 四维打分。
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

_EXTRACT_PROMPT = """从以下券商研报精要中提取催化事件。每条催化独立输出。

研报精要:
{body}

输出 JSON 数组，每个元素一个催化事件:
[
  {{
    "event": "催化事件描述（30字以内，包含关键数字如涨幅%/缺口量/产能数据）",
    "type": "supply_shock|demand_surge|tech_breakthrough|policy_change|price_spike|earnings_beat",
    "chain": "产业链名称（如AI算力/半导体/新能源/机器人/新材料/消费电子/其他）",
    "segment": "具体环节（如EML光芯片/MLCC介质粉/钨精矿/HBM封装）",
    "intensity": "CRITICAL|HIGH|MEDIUM|LOW",
    "stocks": [
      {{"code": "6位代码", "name": "名称", "role": "龙头/弹性/上游/下游/替代受益"}}
    ],
    "key_numbers": ["关键数字1", "关键数字2"],
    "source_report": "原始研报来源（如提及）"
  }}
]

规则:
1. 每篇研报精要通常含 3-4 个独立主题，每个主题输出一条催化。
2. 代码必须是6位纯数字，不确定的代码不要编造（留空 stocks 数组）。
3. intensity 判断: CRITICAL=供给断供/停产/出口归零, HIGH=涨价30%+/扩产翻倍/业绩暴增, MEDIUM=常规需求拉动/份额提升, LOW=远期预期/主题炒作。
4. chain 尽量匹配标准产业链名: AI算力/半导体/新能源/机器人/新材料/光模块/PCB/MLCC/小金属/稀土永磁/消费电子/军工/医药/消费。
5. 只输出 JSON，不要任何解释。"""


def _get_client():
    from llm import _load_api_key
    from anthropic import Anthropic
    return Anthropic(api_key=_load_api_key(), timeout=TIMEOUT)


def extract(body: str, title: str = "", date_str: str = "") -> list[dict]:
    """从一篇韭研脱水研报中提取催化事件列表。

    Returns list of catalyst events, each with event/type/chain/segment/stocks/intensity.
    """
    if len(body) < 200:
        return []

    client = _get_client()
    prompt = _EXTRACT_PROMPT.format(body=body[:4000])

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                timeout=TIMEOUT,
            )
            parts = [block.text for block in resp.content
                     if hasattr(block, "text") and block.text]
            text = "\n".join(parts)
            break
        except Exception as e:
            if attempt == 2:
                print(f"  [韭研] LLM 失败: {e}")
                return []
            continue

    # Parse JSON array
    events = []
    try:
        # Try ```json ... ``` first
        m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if not m:
            m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            raw = m.group(0) if not m.lastindex else m.group(1)
            raw = re.sub(r",\s*([}\]])", r"\1", raw)  # trailing comma
            events = json.loads(raw)
    except (json.JSONDecodeError, AttributeError):
        return []

    # Validate and enrich
    valid = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if not e.get("event"):
            continue
        # Validate stock codes
        stocks = []
        for s in e.get("stocks", []) or []:
            code = str(s.get("code", "")).strip()
            if re.match(r"^\d{6}$", code):
                stocks.append({"code": code, "name": s.get("name", ""), "role": s.get("role", "")})
        e["stocks"] = stocks
        e["_title"] = title
        e["_date"] = date_str
        valid.append(e)

    return valid


def inject_to_catalyst_screen(events: list[dict]) -> int:
    """将催化事件写入 catalyst_screen 的输入目录。

    每个事件作为一个独立催化，后续由 catalyst_screen 做 M/S/N/U 四维打分。
    """
    if not events:
        return 0

    out_dir = Path(__file__).resolve().parent.parent / "reports" / "catalyst" / "jiuyan"
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for e in events:
        date_str = e.get("_date", "unknown")
        out_path = out_dir / f"jiuyan_{date_str}_{e.get('type','unknown')}_{count}.json"
        out_path.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1

    return count


def format_for_dashboard(events: list[dict]) -> str:
    """格式化催化事件为 Dashboard 可用的 Markdown 摘要。"""
    if not events:
        return ""

    lines = ["### 韭研脱水今日催化", ""]
    for e in events[:10]:
        stocks_str = ", ".join(f"{s['name']}({s['code']})" for s in e.get("stocks", [])[:3])
        lines.append(f"- [{e.get('intensity','?')}] **{e.get('event','')}**")
        lines.append(f"  链: {e.get('chain','?')} > {e.get('segment','?')} | {stocks_str}")
    return "\n".join(lines)
