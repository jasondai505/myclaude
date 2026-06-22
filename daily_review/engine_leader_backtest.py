"""龙头股回溯 — 聚合历史龙头标的，按出现频率排序，可选 K 线后验"""

from __future__ import annotations

import json
from collections import defaultdict

from store import _conn


def leader_frequency(min_appearances: int = 2) -> list[dict]:
    """聚合所有被标记为 leader_stock / auction_leader 的标的，按出现次数排序"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT leader_stock, auction_leader, sector, date
            FROM sector_rotation_log
            WHERE leader_stock != '' OR auction_leader != ''
        """).fetchall()

    freq = defaultdict(lambda: {"count": 0, "sectors": set(), "dates": [], "roles": []})
    for r in rows:
        for field in ("leader_stock", "auction_leader"):
            name = r[field]
            if name:
                freq[name]["count"] += 1
                freq[name]["sectors"].add(r["sector"])
                freq[name]["dates"].append(r["date"])
                freq[name]["roles"].append(field)

    result = []
    for name, info in freq.items():
        if info["count"] >= min_appearances:
            result.append({
                "stock": name,
                "appearances": info["count"],
                "sectors": sorted(info["sectors"]),
                "first_date": min(info["dates"]),
                "last_date": max(info["dates"]),
                "as_leader": sum(1 for r in info["roles"] if r == "leader_stock"),
                "as_auction": sum(1 for r in info["roles"] if r == "auction_leader"),
            })
    return sorted(result, key=lambda x: x["appearances"], reverse=True)


def sector_leaders(sector: str) -> list[dict]:
    """按板块查看历史上的龙头标的分布"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT leader_stock, auction_leader, date, stocks_json
            FROM sector_rotation_log
            WHERE sector = ? AND (leader_stock != '' OR auction_leader != '')
            ORDER BY date
        """, (sector,)).fetchall()

    leaders = defaultdict(lambda: {"dates": [], "roles": []})
    for r in rows:
        for field in ("leader_stock", "auction_leader"):
            name = r[field]
            if name:
                leaders[name]["dates"].append(r["date"])
                leaders[name]["roles"].append(field)

    result = []
    for name, info in leaders.items():
        result.append({
            "stock": name,
            "appearances": len(info["dates"]),
            "first_date": min(info["dates"]),
            "last_date": max(info["dates"]),
        })
    return sorted(result, key=lambda x: x["appearances"], reverse=True)


def leader_backtest(leader_stock: str, lookback_days: int = 5, forward_days: int = 10) -> dict | None:
    """对单个龙头标的历史标记后的 K 线表现做后验。

    在每个被标记为龙头的日期，取前 lookback 日收盘和之后 forward_days 的收益表现。
    依赖 data.py 的 mootdx K 线接口。
    """
    try:
        from data import fetch_kline
    except ImportError:
        return {"error": "data.py not available"}

    with _conn() as conn:
        rows = conn.execute("""
            SELECT date FROM sector_rotation_log
            WHERE (leader_stock = ? OR auction_leader = ?)
            ORDER BY date
        """, (leader_stock, leader_stock)).fetchall()

    if not rows:
        return None

    dates = [r["date"] for r in rows]
    try:
        kline = fetch_kline(leader_stock, lookback_days + len(dates) * 2)
        if kline is None or kline.empty:
            return {"stock": leader_stock, "appearances": len(dates), "kline": "unavailable"}
    except Exception:
        return {"stock": leader_stock, "appearances": len(dates), "kline": "error"}

    results = []
    for d in dates:
        try:
            d_dt = d.replace("-", "")
            subset = kline[kline.index.astype(str).str.startswith(d_dt)]
            if subset.empty:
                continue
            idx = subset.index[0]
            pos = kline.index.get_loc(idx)
            entry_close = float(kline.iloc[pos]["close"])
            fwd_close = float(kline.iloc[min(pos + forward_days, len(kline) - 1)]["close"])
            fwd_return = round((fwd_close / entry_close - 1) * 100, 2)
            results.append({"date": d, "entry": entry_close, "fwd_return": fwd_return})
        except (KeyError, IndexError, TypeError):
            continue

    wins = sum(1 for r in results if r["fwd_return"] > 0)
    avg_return = round(sum(r["fwd_return"] for r in results) / len(results), 2) if results else 0

    return {
        "stock": leader_stock,
        "appearances": len(dates),
        "backtest_dates": len(results),
        "win_rate": round(wins / len(results) * 100, 1) if results else 0,
        "avg_fwd_return": avg_return,
        "details": results,
    }


def top_leader_report(limit: int = 20) -> str:
    """生成高频龙头股简要报告"""
    leaders = leader_frequency(min_appearances=3)
    lines = ["## 高频龙头股", "",
             "| 标的 | 出现次数 | 覆盖板块 | 首次 | 最近 |",
             "|------|---------|---------|------|------|"]
    for l in leaders[:limit]:
        sectors = "/".join(l["sectors"][:3])
        lines.append(
            f"| {l['stock']} | {l['appearances']} | {sectors} | "
            f"{l['first_date']} | {l['last_date']} |")
    return "\n".join(lines)
