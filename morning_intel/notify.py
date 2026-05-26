"""微信推送通知 — PushPlus 通道"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
from settings import PUSHPLUS_TOKEN

API = "https://www.pushplus.plus/send"


def push(title: str, content: str) -> bool:
    """推送到微信。返回成功与否。"""
    if not PUSHPLUS_TOKEN:
        return False
    try:
        r = requests.post(
            API,
            json={"token": PUSHPLUS_TOKEN, "title": title, "content": content},
            timeout=10,
        )
        ok = r.json().get("code") == 200
        if not ok:
            print(f"[notify] 推送失败: {r.json()}")
        return ok
    except Exception as e:
        print(f"[notify] 推送异常: {e}")
        return False


def morning_brief(summary: str, events_count: int, stocks_count: int, top_events: list[str]) -> bool:
    """06:00 盘前情报推送。"""
    events_lines = "\n".join(f"- {e[:80]}" for e in top_events[:5])
    content = f"**{summary[:150]}**\n\n{events_count}个事件 | {stocks_count}只标的\n\n{events_lines}"
    return push(f"☀️ 盘前情报 {len(top_events)}事件/{stocks_count}标的", content)


def intraday_validation(
    today: str,
    total: int,
    hit: int,
    miss: int,
    pending: int,
    top_gainers: list[tuple],
    top_losers: list[tuple],
    spot_verdict: str = "",
) -> bool:
    """10:30 / 14:00 盘中验证推送。"""
    hit_rate = round(hit / total * 100, 1) if total > 0 else 0
    parts = [f"命中 {hit}/{total} ({hit_rate}%) | 背离 {miss} | 待定 {pending}", ""]
    if top_gainers:
        items = "\n".join(f"- {c} {n} {chg:+.1f}% ✅" for c, n, chg in top_gainers[:5])
        parts.append(f"📈 涨幅前5:\n{items}")
    if top_losers:
        items = "\n".join(f"- {c} {n} {chg:+.1f}% ❌" for c, n, chg in top_losers[:5])
        parts.append(f"📉 跌幅前5:\n{items}")
    if spot_verdict:
        parts.append(f"\n🤖 {spot_verdict[:200]}")
    return push(f"📊 盘中验证 命中率{hit_rate}%", "\n".join(parts))


def daily_result(
    today: str, hit_rate: float, hit: int, total: int, observing: int = 0, weakening: int = 0, confirmed: int = 0
) -> bool:
    """17:50 交易日简报推送。"""
    verdict = "优秀" if hit_rate >= 70 else "良好" if hit_rate >= 50 else "待改善"
    parts = [f"命中率 **{hit_rate}%** ({hit}/{total}) — {verdict}", ""]
    if observing > 0:
        parts.append(f"🔍 {observing}个题材观察中")
    if weakening > 0:
        parts.append(f"⚠️ {weakening}个连续退潮已确认趋弱")
    if confirmed > 0:
        parts.append(f"✅ {confirmed}个确认主线")
    return push(f"📋 交易日简报 命中率{hit_rate}%", "\n".join(parts))
