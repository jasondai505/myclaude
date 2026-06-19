"""微信推送通知 — PushPlus 通道"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
from settings import PUSHPLUS_TOKEN, PUSHPLUS_TOPIC

API = "https://www.pushplus.plus/send"


def push(title: str, content: str) -> bool:
    """推送到微信。返回成功与否。"""
    if not PUSHPLUS_TOKEN:
        return False
    try:
        r = requests.post(
            API,
            json={"token": PUSHPLUS_TOKEN, "title": title, "content": content, "topic": PUSHPLUS_TOPIC},
            timeout=10,
        )
        ok = r.json().get("code") == 200
        if not ok:
            print(f"[notify] 推送失败: {r.json()}")
        return ok
    except Exception as e:
        print(f"[notify] 推送异常: {e}")
        return False


def morning_brief(summary: str, events_count: int, stocks_count: int, events: list[dict]) -> bool:
    """06:00 盘前情报推送。包含完整事件+标的+方向+依据。"""
    lines = [f"**{summary[:150]}**", "", f"{events_count}个事件 | {stocks_count}只标的", ""]
    for ev in events[:4]:
        name = ev.get("name", "").strip()
        conf = ev.get("confidence", "")
        lines.append(f"### {name}  ({conf})")
        lines.append("")
        for s in ev.get("target_stocks", [])[:4]:
            lines.append(
                f"- **{s.get('code', '')} {s.get('name', '')}** "
                f"{s.get('expected_direction', '')} — {s.get('rationale', '')[:80]}"
            )
        lines.append("")
    watch = []
    for ev in events:
        watch.extend(ev.get("watch_notes", []))
    if watch:
        lines.append("---")
        lines.append("🔍 观察:")
        for w in watch[:3]:
            lines.append(f"- {w[:100]}")
    content = "\n".join(lines)
    if len(content) > 4000:
        content = content[:4000] + "\n\n... (已截断)"
    return push(f"☀️ 盘前情报 {events_count}事件/{stocks_count}标的", content)


def intraday_validation(
    today: str,
    total: int,
    hit: int,
    miss: int,
    pending: int,
    rows: list[dict],
    spot_verdict: str = "",
) -> bool:
    """10:30 / 14:00 盘中验证推送 — 按事件分组，板块内按涨幅排序，全部展示。"""
    hit_rate = round(hit / total * 100, 1) if total > 0 else 0
    parts = [f"命中 {hit}/{total} ({hit_rate}%) | 背离 {miss} | 待定 {pending}", ""]

    events: dict[str, list[dict]] = {}
    for r in rows:
        ev = r.get("event", "") or "其他"
        if ev not in events:
            events[ev] = []
        events[ev].append(r)

    for ev_name, stocks in events.items():
        stocks.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
        parts.append(f"**{ev_name[:40]}**")
        for s in stocks:
            chg = s.get("change_pct", 0)
            icon = {1: "✅", -1: "❌", 0: "⏳"}.get(s.get("validated", 0), "—")
            parts.append(f"{icon} {s['name']}({s['code']}) {chg:+.1f}%")
        parts.append("")

    if spot_verdict:
        parts.append(f"🤖 {spot_verdict[:200]}")
    content = "\n".join(parts)
    if len(content) > 4000:
        content = content[:4000] + "\n\n...(已截断)"
    return push(f"📊 盘中验证 命中率{hit_rate}%", content)


def daily_result(
    today: str, hit_rate: float, hit: int, total: int, observing: int = 0,
    weakening: int = 0, confirmed: int = 0, validation_rows: list[dict] = None
) -> bool:
    """17:50 交易日简报推送。"""
    verdict = "优秀" if hit_rate >= 70 else "良好" if hit_rate >= 50 else "待改善"
    parts = [f"命中率 **{hit_rate}%** ({hit}/{total}) — {verdict}", ""]

    # 逐只验证结果
    if validation_rows:
        hits_list = [r for r in validation_rows if r.get("validated") == 1]
        misses_list = [r for r in validation_rows if r.get("validated") == -1]
        pending_list = [r for r in validation_rows if r.get("validated") == 0]

        if hits_list:
            parts.append(f"✅ 命中 ({len(hits_list)}):")
            for r in hits_list:
                chg = r.get("change_pct", 0)
                parts.append(f"  {r['name']}({r['code']}) {chg:+.1f}%")
            parts.append("")

        if misses_list:
            parts.append(f"❌ 背离 ({len(misses_list)}):")
            for r in misses_list:
                chg = r.get("change_pct", 0)
                parts.append(f"  {r['name']}({r['code']}) {chg:+.1f}%")
            parts.append("")

        if pending_list:
            parts.append(f"⏳ 待定 ({len(pending_list)}):")
            for r in pending_list[:3]:
                chg = r.get("change_pct", 0)
                parts.append(f"  {r['name']}({r['code']}) {chg:+.1f}%")
            if len(pending_list) > 3:
                parts.append(f"  ...等{len(pending_list)}只")
            parts.append("")
    else:
        parts.append(f"命中 {hit}/{total}")
        parts.append("")

    if observing > 0:
        parts.append(f"🔍 {observing}个题材观察中")
    if weakening > 0:
        parts.append(f"⚠️ {weakening}个连续退潮已确认趋弱")
    if confirmed > 0:
        parts.append(f"✅ {confirmed}个确认主线")

    content = "\n".join(parts)
    if len(content) > 4000:
        content = content[:4000] + "\n\n...(已截断)"
    return push(f"📋 交易日简报 命中率{hit_rate}%", content)
