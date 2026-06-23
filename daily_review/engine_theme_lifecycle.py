"""主题生命周期追踪 — 从 catalyst_signals × catalyst_stock_map 聚合时间轴。

回答：这个主题哪天开始的、持续多久、逻辑在加剧还是减缓、走势确认了没有。

用法:
    python engine_theme_lifecycle.py              # 终端输出
    from engine_theme_lifecycle import lifecycle   # Dashboard 调用
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from collections import defaultdict
from pathlib import Path

from store import _conn

LOOKBACK = 60
MIN_APPEARANCES = 2      # 至少出现2天才纳入
MIN_ACTIONABILITY = 20
CACHE_DIR = Path(__file__).parent / "data" / "commonality_cache"


def lifecycle(lookback: int = LOOKBACK) -> list[dict]:
    """从 catalyst_signals + catalyst_stock_map 构建主题生命周期表。"""
    cutoff = (date.today() - timedelta(days=lookback)).isoformat()

    with _conn() as conn:
        rows = conn.execute("""
            SELECT cs.date, csm.matched_concept, cs.actionability, cs.price_confirmed
            FROM catalyst_signals cs
            JOIN catalyst_stock_map csm
              ON cs.date = csm.date AND cs.catalyst_name = csm.catalyst_name
            WHERE cs.date >= ?
              AND cs.actionability >= ?
              AND csm.matched_concept != ''
              AND csm.matched_concept != '--'
            ORDER BY cs.date
        """, (cutoff, MIN_ACTIONABILITY)).fetchall()

    if not rows:
        return []

    themes: dict[str, dict] = defaultdict(lambda: {
        "dates": set(), "max_score": 0, "total_score": 0, "count": 0, "confirmed_dates": set(),
    })
    for r in rows:
        t = themes[r["matched_concept"]]
        t["dates"].add(r["date"])
        t["max_score"] = max(t["max_score"], r["actionability"] or 0)
        t["total_score"] += r["actionability"] or 0
        t["count"] += 1
        if r["price_confirmed"]:
            t["confirmed_dates"].add(r["date"])

    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    two_weeks_ago = (today - timedelta(days=14)).isoformat()

    # Shendu 主题背书：匹配 shendu JSON 中的 themes/chains
    shendu_backed = set()
    try:
        shendu_dir = Path(__file__).parent / "reports" / "serenity" / "shendu"
        if shendu_dir.exists():
            two_months = (date.today() - timedelta(days=60)).isoformat()
            for fp in shendu_dir.iterdir():
                if not fp.name.startswith("shendu_2026") or fp.name.startswith("shendu__"):
                    continue
                try:
                    d = json.loads(fp.read_text(encoding="utf-8"))
                    if d.get("date", "") >= two_months:
                        for t in d.get("themes", []) + d.get("chains_involved", []):
                            shendu_backed.add(t)
                except Exception:
                    pass
    except Exception:
        pass

    result = []
    for theme, data in themes.items():
        dates = sorted(data["dates"])
        if len(dates) < MIN_APPEARANCES:
            continue

        first = dates[0]
        last = dates[-1]
        days_active = len(dates)
        days_since_last = (today - date.fromisoformat(last)).days

        recent = sum(1 for d in dates if d >= week_ago)
        prior = sum(1 for d in dates if two_weeks_ago <= d < week_ago)
        if prior > 0 and recent >= prior * 1.3:
            trend = "🔥加剧"
        elif prior > 0 and recent <= prior * 0.5:
            trend = "⬇️减缓"
        else:
            trend = "➡️平稳"

        if days_since_last > 14:
            state = "dormant"
        elif days_active <= 2:
            state = "emerging"
        elif trend == "⬇️减缓":
            state = "cooling"
        elif data["confirmed_dates"]:
            state = "confirmed"
        else:
            state = "active"

        has_trend = bool(data["confirmed_dates"])
        avg_score = round(data["total_score"] / data["count"], 1)

        result.append({
            "theme": theme, "state": state, "days_active": days_active,
            "first_date": first, "last_date": last, "trend": trend,
            "has_trend": has_trend, "signal_count": data["count"],
            "max_score": data["max_score"], "avg_score": avg_score,
            "shendu_backed": theme in shendu_backed,
        })

    state_order = {"active": 0, "confirmed": 1, "emerging": 2, "cooling": 3, "dormant": 4}
    result.sort(key=lambda x: (state_order.get(x["state"], 5), -x["days_active"]))
    return result


def _check_concept_heat_for_theme(theme: str) -> tuple[bool, float]:
    """检查主题对应概念在 commonality_cache 中的热度。"""
    try:
        files = sorted(CACHE_DIR.glob("scan_*.json"))
        if not files:
            return False, 0.0
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        counts = data.get("concept_counts", {})
        if theme in counts:
            from catalyst_tracker import _load_concept_baseline
            baseline = _load_concept_baseline()
            base = baseline.get(theme, 1)
            ratio = counts[theme] / base if base > 0 else 0
            return ratio >= 0.05, round(ratio * 100, 1)
    except Exception:
        pass
    return False, 0.0


if __name__ == "__main__":
    rows = lifecycle()
    print(f"\n{'主题':<20s} {'状态':<10s} {'持续':>4s} {'趋势':<8s} {'走势':<4s} {'首次':>10s} {'最近':>10s} {'信号':>4s} {'均分':>5s}")
    print("-" * 90)
    for r in rows[:25]:
        trend_icon = "✅" if r["has_trend"] else "—"
        print(f"{r['theme']:<20s} {r['state']:<10s} {r['days_active']:>4d}天 {r['trend']:<8s} {trend_icon:<4s} "
              f"{r['first_date']:>10s} {r['last_date']:>10s} {r['signal_count']:>4d} {r['avg_score']:>5.0f}")
