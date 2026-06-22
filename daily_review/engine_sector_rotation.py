"""板块轮动分析 — 基于 sector_rotation_log 的历史板块频率、持续性、轮动模式"""

from __future__ import annotations

import json
from collections import defaultdict

from store import _conn


def sector_frequency(days: int = 60) -> list[dict]:
    """最近 N 天板块出现频率（按出现天数降序）"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT sector, COUNT(DISTINCT date) as days, COUNT(*) as rows,
                   MIN(date) as first_date, MAX(date) as last_date
            FROM sector_rotation_log
            WHERE sector != ''
              AND row_type IN ('index_score','volume','breadth','limit_up_leaders',
                               'limit_down','prev_limit_pnl','institutional','retail')
              AND date >= date('now', ? || ' days')
            GROUP BY sector ORDER BY days DESC
        """, (f"-{days}",)).fetchall()
    return [dict(r) for r in rows]


def sector_persistence(min_days: int = 3) -> list[dict]:
    """板块持续性统计：每个板块在历史上连续出现的最大天数，按 max_streak 降序"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT date, sector FROM sector_rotation_log
            WHERE sector != ''
              AND row_type IN ('index_score','volume','breadth','limit_up_leaders',
                               'limit_down','prev_limit_pnl')
            ORDER BY sector, date
        """).fetchall()

    from datetime import datetime as _dt, timedelta

    streaks = defaultdict(list)
    current_sector = None
    current_streak = []
    for r in rows:
        d = r["date"]
        same_sector = r["sector"] == current_sector
        gap_ok = False
        if same_sector and current_streak:
            prev = _dt.strptime(current_streak[-1], "%Y-%m-%d")
            curr = _dt.strptime(d, "%Y-%m-%d")
            gap_ok = (curr - prev).days <= 5
        if not same_sector or not gap_ok:
            if current_streak:
                streaks[current_sector].append(current_streak)
            current_sector = r["sector"]
            current_streak = [d]
        else:
            current_streak.append(d)
    if current_streak:
        streaks[current_sector].append(current_streak)

    result = []
    for sector, runs in streaks.items():
        streak_lens = [len(r) for r in runs]
        max_len = max(streak_lens)
        avg_len = sum(streak_lens) / len(streak_lens)
        total_days = sum(streak_lens)
        if total_days >= min_days:
            result.append({
                "sector": sector,
                "total_days": total_days,
                "runs": len(runs),
                "max_streak": max_len,
                "avg_streak": round(avg_len, 1),
                "first_date": runs[0][0],
                "last_date": runs[-1][-1],
            })
    return sorted(result, key=lambda x: x["max_streak"], reverse=True)


def sector_cooccurrence(days: int = 120) -> list[dict]:
    """板块共现矩阵：同一天出现的最常见板块对"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT date, sector FROM sector_rotation_log
            WHERE sector != ''
              AND row_type IN ('index_score','volume','breadth','limit_up_leaders')
              AND date >= date('now', ? || ' days')
            ORDER BY date
        """, (f"-{days}",)).fetchall()

    date_sectors = defaultdict(set)
    for r in rows:
        date_sectors[r["date"]].add(r["sector"])

    pairs = defaultdict(int)
    for sectors in date_sectors.values():
        s_list = sorted(sectors)
        for i in range(len(s_list)):
            for j in range(i + 1, len(s_list)):
                pairs[(s_list[i], s_list[j])] += 1

    result = [{"sector_a": k[0], "sector_b": k[1], "co_days": v}
              for k, v in pairs.items() if v >= 2]
    return sorted(result, key=lambda x: x["co_days"], reverse=True)


def sector_timeline(top_n: int = 15) -> list[dict]:
    """按周聚合 Top N 板块的出现热度（用于热力图）"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT strftime('%Y-W%W', date) as week, sector, COUNT(*) as cnt
            FROM sector_rotation_log
            WHERE sector != ''
              AND row_type IN ('index_score','volume','breadth','limit_up_leaders',
                               'limit_down','prev_limit_pnl')
            GROUP BY week, sector ORDER BY week, cnt DESC
        """).fetchall()

    top_sectors = sector_frequency(226)
    top_names = {s["sector"] for s in top_sectors[:top_n]}

    week_data = defaultdict(dict)
    for r in rows:
        if r["sector"] in top_names:
            week_data[r["week"]][r["sector"]] = r["cnt"]

    weeks = sorted(week_data.keys())
    result = []
    for week in weeks:
        entry = {"week": week}
        for s in top_names:
            entry[s] = week_data[week].get(s, 0)
        result.append(entry)
    return result


def sector_stocks(sector: str, limit: int = 30) -> list[dict]:
    """按板块聚合历史上出现过的所有关联个股，按出现次数排序"""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT stocks_json FROM sector_rotation_log
            WHERE sector = ? AND stocks_json != '[]'
        """, (sector,)).fetchall()

    freq = defaultdict(int)
    for r in rows:
        stocks = json.loads(r["stocks_json"])
        for s in stocks:
            freq[s] += 1

    result = [{"stock": k, "freq": v} for k, v in freq.items()]
    return sorted(result, key=lambda x: x["freq"], reverse=True)[:limit]
