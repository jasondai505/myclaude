"""外围标的「最近逻辑催化原因」LLM 摘要。

仅用于外围市场表（美股科技/港股）。单次批量调用拿全部标的的催化原因。
任何失败返回 {}，由调用方兜底为「—」，绝不让复盘流程 hang 或崩。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MODEL = os.getenv("DR_LLM_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = 30
MAX_TOKENS = 1500


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


def generate_overseas_catalysts(watchlist: dict, today: str) -> dict:
    """返回 {标的label: 最近逻辑催化原因}；失败返回 {}。"""
    api_key = _load_api_key()
    if not api_key or not watchlist:
        return {}

    try:
        from anthropic import Anthropic
    except ImportError:
        print("  [WARN] 未安装 anthropic，跳过外围催化摘要")
        return {}

    ctx_lines = []
    for label, q in watchlist.items():
        chg = q.get("change_pct", 0)
        chg5 = q.get("change_pct_5d")
        c5 = f"{chg5:+.1f}%" if chg5 is not None else "NA"
        ctx_lines.append(f"- {label}: 今日{chg:+.2f}%, 近5日{c5}")
    ctx = "\n".join(ctx_lines)

    prompt = (
        f"今天是 {today}。下面是部分美股科技龙头/港股标的的最新涨跌幅：\n{ctx}\n\n"
        "请为每个标的给出当前阶段「最近的逻辑/基本面催化原因」："
        "一句话，≤20字，聚焦驱动其股价的核心产业逻辑或近期事件，不要复述涨跌幅数字。\n"
        "只输出 JSON，键为标的名（与上面完全一致），值为催化原因字符串，不要任何额外说明。"
    )

    try:
        client = Anthropic(api_key=api_key, base_url="https://api.deepseek.com/anthropic", timeout=TIMEOUT)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = _extract_json(text)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if label_in_watchlist(k, watchlist)}
    except Exception as e:
        print(f"  [WARN] 外围催化 LLM 摘要失败: {e}")
        return {}


def label_in_watchlist(key, watchlist: dict) -> bool:
    return str(key) in watchlist


def _extract_json(text: str) -> dict | None:
    """从可能带 ```json 围栏或前后赘述的文本中抠出第一段 JSON 对象。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
