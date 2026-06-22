"""市场节奏周期识别 — 基于 +1启动/+2接力/扩容/回流 四阶段框架"""

from __future__ import annotations

import json
from collections import defaultdict

from store import _conn


def daily_rhythm(date: str) -> dict:
    """单日市场节奏快照：返回当日各阶段的板块分布"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT row_type, sector, stocks_json, leader_stock, auction_leader
            FROM sector_rotation_log
            WHERE date = ? AND row_type IN (
                'phase_launch','phase_relay','phase_expand1','phase_expand2','phase_backflow'
            ) AND sector != ''
        """, (date,)).fetchall()

    phases = defaultdict(list)
    for r in rows:
        stocks = json.loads(r["stocks_json"]) if r["stocks_json"] else []
        phases[r["row_type"]].append({
            "sector": r["sector"],
            "leader": r["leader_stock"] or r["auction_leader"],
            "stock_count": len(stocks),
        })

    return {"date": date, "phases": dict(phases)}


def rhythm_history(days: int = 60) -> list[dict]:
    """最近 N 天每日节奏概览"""
    with _conn() as conn:
        dates = conn.execute("""
            SELECT DISTINCT date FROM sector_rotation_log
            WHERE date >= date('now', ? || ' days')
            ORDER BY date DESC
        """, (f"-{days}",)).fetchall()

    result = []
    for r in dates:
        rhythm = daily_rhythm(r["date"])
        if any(rhythm["phases"].get(p) for p in
               ["phase_launch", "phase_relay", "phase_expand1", "phase_expand2", "phase_backflow"]):
            result.append(rhythm)
    return result


def rhythm_summary(days: int = 60) -> dict:
    """节奏阶段统计：各阶段活跃天数、板块数"""
    history = rhythm_history(days)
    phase_stats = defaultdict(lambda: {"days": 0, "total_sectors": 0, "sectors": defaultdict(int)})
    day_count = len(history)

    for day in history:
        for phase, sectors in day["phases"].items():
            phase_stats[phase]["days"] += 1
            for s in sectors:
                phase_stats[phase]["total_sectors"] += 1
                phase_stats[phase]["sectors"][s["sector"]] += 1

    summary = {"total_days": day_count}
    labels = ["phase_launch", "phase_relay", "phase_expand1", "phase_expand2", "phase_backflow"]
    for phase in labels:
        stats = phase_stats[phase]
        top_sectors = sorted(stats["sectors"].items(), key=lambda x: x[1], reverse=True)[:10]
        summary[phase] = {
            "active_days": stats["days"],
            "total_sectors": stats["total_sectors"],
            "top_sectors": [{"sector": s, "days": d} for s, d in top_sectors],
        }
    return summary


def classify_rhythm_stage(date: str) -> str:
    """将单日归类为市场节奏阶段：idle / launch / relay / momentum / expansion / backflow"""
    rhythm = daily_rhythm(date)
    phases = rhythm["phases"]

    has_launch = len(phases.get("phase_launch", [])) > 0
    has_relay = len(phases.get("phase_relay", [])) > 0
    has_expand1 = len(phases.get("phase_expand1", [])) > 0
    has_expand2 = len(phases.get("phase_expand2", [])) > 0
    has_backflow = len(phases.get("phase_backflow", [])) > 0

    if has_relay and (has_expand1 or has_expand2):
        return "expansion"
    if has_launch and has_relay:
        return "momentum"
    if has_launch:
        return "launch"
    if has_expand1 or has_expand2:
        return "expansion"
    if has_backflow:
        return "backflow"
    if has_relay:
        return "relay"
    return "idle"


def rhythm_transitions(days: int = 120) -> list[dict]:
    """节奏状态转移序列"""
    with _conn() as conn:
        dates = conn.execute("""
            SELECT DISTINCT date FROM sector_rotation_log
            WHERE date >= date('now', ? || ' days')
            ORDER BY date
        """, (f"-{days}",)).fetchall()

    prev_stage = None
    transitions = []
    for r in dates:
        stage = classify_rhythm_stage(r["date"])
        if prev_stage and stage != prev_stage:
            transitions.append({"date": r["date"], "from": prev_stage, "to": stage})
        prev_stage = stage

    return transitions


def rhythm_report() -> str:
    """生成市场节奏简要报告文本"""
    today_summary = rhythm_summary(30)
    recent = rhythm_history(14)

    lines = ["## 市场节奏周期", ""]
    lines.append("近30日活跃天数：")
    labels = {
        "phase_launch": "+1启动", "phase_relay": "+2接力",
        "phase_expand1": "扩容1", "phase_expand2": "扩容2",
        "phase_backflow": "回流",
    }
    for phase, label in labels.items():
        stats = today_summary.get(phase, {})
        lines.append(f"- {label}: {stats.get('active_days', 0)}天")

    lines.append("")
    lines.append("近两周节奏序列：")
    for day in recent:
        date = day["date"]
        stage = classify_rhythm_stage(date)
        launch = len(day["phases"].get("phase_launch", []))
        relay = len(day["phases"].get("phase_relay", []))
        lines.append(f"- {date} [{stage}] 启动{launch} 接力{relay}")

    return "\n".join(lines)
