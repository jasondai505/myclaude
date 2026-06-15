"""将成龙 — 独立模块。持续跟踪与迭代优化。

核心算法: score_emerging_dragons() — 跨板块筛选领先于板块的率先异动标的
持久化: save / track / compute_conversion — SQLite 存储 + 历史追踪
渲染: render_table / render_tracking — Markdown 报告渲染

用法:
    from daily_review.emerging_dragon import score_emerging_dragons
    dragons = score_emerging_dragons(theme_pool, klines_map, quotes_map, ...)
"""
from __future__ import annotations

import pandas as pd

from strength import _calc_nd_return, _is_limit_up


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ============================================================
# 核心算法
# ============================================================

def score_emerging_dragons(
    theme_pool: dict[str, dict[str, dict]],
    klines_map: dict[str, pd.DataFrame],
    quotes_map: dict[str, dict],
    zt_pool: dict[str, dict],
    fev_map: dict[str, dict],
    hot_rank_map: dict[str, int],
    hot100_codes: set[str],
    major_concepts: set[str],
    strength_data: dict,
) -> list[dict]:
    """跨板块筛选将成龙：个股领先于板块，在板块前中期率先走强。

    核心逻辑：涨停是结果不是预言。将成龙要找的是——
    1. 板块处于前中期（非高潮/退潮）
    2. 个股显著强于板块（相对强度）
    3. 能在板块调整时逆势走强（最强信号）
    """
    # 板块聚合
    sector_info: dict[str, dict] = {}
    for ts in (strength_data.get("strong_themes", [])
               + strength_data.get("emerging_themes", [])
               + strength_data.get("fading_themes", [])):
        sector_info[ts["theme"]] = ts

    def _sector_daily_returns(pool_stocks, days=5):
        if not pool_stocks:
            return [0.0] * days
        daily_sums = [0.0] * days
        daily_counts = [0] * days
        for code in pool_stocks:
            kdf = klines_map.get(code)
            if kdf is None or len(kdf) <= days:
                continue
            closes = kdf["close"].values
            for d in range(1, days + 1):
                if len(closes) > d and closes[-d-1] > 0:
                    daily_sums[d-1] += (closes[-d] - closes[-d-1]) / closes[-d-1] * 100
                    daily_counts[d-1] += 1
        return [daily_sums[i] / max(daily_counts[i], 1) for i in range(days)]

    def _sector_avg_return(pool_stocks, ndays):
        returns = []
        for code in pool_stocks:
            kdf = klines_map.get(code)
            r = _calc_nd_return(kdf, ndays)
            if r is not None:
                returns.append(r)
        return sum(returns) / len(returns) if returns else 0.0

    candidates = []
    for theme, pool_stocks in theme_pool.items():
        si = sector_info.get(theme, {})
        stage = si.get("stage", "")
        if stage in ("退潮", "高潮"):
            continue

        sector_r5 = si.get("avg_5d") or _sector_avg_return(pool_stocks, 5)
        sector_r10 = si.get("avg_10d") or _sector_avg_return(pool_stocks, 10)
        if not stage:
            today_count = si.get("today_count", 0) or 0
            breadth = si.get("breadth") or (0.5 if sector_r5 > 0 else 0.3)
            if sector_r5 < -3 and today_count == 0:
                stage = "退潮"
            elif today_count >= 10 and sector_r5 > 15:
                stage = "高潮"
            elif sector_r5 > 0:
                stage = "主升浪"
            else:
                stage = "活跃"
        if stage in ("退潮", "高潮"):
            continue
        sdr = _sector_daily_returns(pool_stocks, 5)

        for code, info in pool_stocks.items():
            q = quotes_map.get(code, {})
            kdf = klines_map.get(code)
            r5 = (_calc_nd_return(kdf, 5) or 0)
            r10 = (_calc_nd_return(kdf, 10) or 0)
            name = q.get("name") or code
            mcap = q.get("mcap_yi", 0) or 0

            stock_daily = []
            has_new_high = False
            near_high = False
            has_limit = False
            consecutive_up = 0
            if kdf is not None and len(kdf) >= 6:
                closes = kdf["close"].values
                highs = kdf["high"].values if "high" in kdf.columns else closes
                for d in range(1, 6):
                    if len(closes) > d and closes[-d-1] > 0:
                        stock_daily.append((closes[-d] - closes[-d-1]) / closes[-d-1] * 100)
                    else:
                        stock_daily.append(0.0)
                if len(highs) >= 20:
                    prev_high = max(highs[-20:-5])
                    recent_high = max(highs[-5:])
                    has_new_high = recent_high > prev_high
                    near_high = recent_high > prev_high * 0.9 and not has_new_high
                if len(kdf) >= 3:
                    for i in range(max(1, len(kdf) - 2), len(kdf)):
                        if _is_limit_up(kdf, i):
                            has_limit = True
                            break
                for j in range(1, min(4, len(closes))):
                    if closes[-j] > closes[-j - 1]:
                        consecutive_up += 1
                    else:
                        break
            else:
                stock_daily = [0.0] * 5

            # 相对强度 (0-60)
            rel_5d = r5 - sector_r5
            rel_10d = r10 - sector_r10
            rel_score = _clamp(rel_5d * 2, 0, 35) + _clamp(rel_10d * 1.2, 0, 25)

            # 逆势信号 (0-35, 近期加权)
            counter_days = 0
            counter_weighted = 0
            recency_w = [3, 2, 1, 1, 1]
            for i, (sd, sec_d) in enumerate(zip(stock_daily, sdr)):
                if sd > 0 and sec_d < 0:
                    counter_days += 1
                    counter_weighted += recency_w[i] if i < len(recency_w) else 1
            counter_score = counter_weighted * 3 + (10 if has_new_high and counter_days >= 1 else 0)

            # 涨停+连涨 (0-12)
            momentum_bonus = 0
            if has_limit:
                momentum_bonus += 5
            if consecutive_up >= 3:
                momentum_bonus += 4
            elif consecutive_up >= 2:
                momentum_bonus += 2
            if near_high:
                momentum_bonus += 3

            # 逻辑未定价 (0-20)
            unpriced = 0
            fev = fev_map.get(code)
            fev_total = fev.get("fev_total") if isinstance(fev, dict) else 0
            if fev_total > 0 and fev_total < 15:
                unpriced += 5
            elif fev_total == 0:
                unpriced += 8
            if code not in hot100_codes:
                unpriced += 4
            if mcap > 0 and mcap < 150:
                unpriced += 4
            if theme not in major_concepts:
                unpriced += 4

            # 板块阶段乘数
            if stage == "爆发初期":
                stage_mul = 1.5
            elif stage == "主升浪":
                breadth = si.get("breadth", 0.5) or 0.5
                stage_mul = 1.2 if breadth < 0.6 else 1.0
            else:
                stage_mul = 1.0

            total = (rel_score + counter_score + momentum_bonus + unpriced) * stage_mul

            candidates.append({
                "code": code, "name": name, "theme": theme, "stage": stage,
                "r5": r5, "r10": r10,
                "sector_r5": round(sector_r5, 1), "sector_r10": round(sector_r10, 1),
                "rel_5d": round(rel_5d, 1), "rel_10d": round(rel_10d, 1),
                "rel_score": round(rel_score, 1),
                "counter_days": counter_days, "has_new_high": has_new_high,
                "counter_score": counter_score,
                "has_limit": has_limit, "consecutive_up": consecutive_up,
                "near_high": near_high, "momentum_bonus": momentum_bonus,
                "unpriced_score": unpriced,
                "value_score": round(total, 1),
                "fev_total": fev_total,
                "hot_rank": f"#{hot_rank_map[code]}" if code in hot_rank_map else "-",
            })

    # 去重
    seen = {}
    for c in candidates:
        code = c["code"]
        if code not in seen or c["value_score"] > seen[code]["value_score"]:
            seen[code] = c

    result = sorted(seen.values(), key=lambda x: -x["value_score"])
    for i, c in enumerate(result):
        c["rank"] = i + 1

    return result[:30]


# ============================================================
# 持久化与追踪
# ============================================================

def save_emerging_dragons(trade_date: str, dragons: list[dict]):
    import store
    try:
        with store._conn() as conn:
            for d in dragons:
                conn.execute(
                    """INSERT OR REPLACE INTO emerging_dragon_log
                       (trade_date, code, name, theme, trend_score, unpriced_score,
                        value_score, r5, r10, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
                    (trade_date, d["code"], d.get("name", ""), d.get("theme", ""),
                     d.get("rel_score", 0), d.get("unpriced_score", 0),
                     d.get("value_score", 0), d.get("r5", 0), d.get("r10", 0)))
            conn.commit()
    except Exception as e:
        print(f"  [WARN] 将成龙日志保存失败: {e}")


def track_emerging_dragon_outcomes(trade_date: str,
                                   current_strength: dict | None = None) -> list[dict]:
    import store
    try:
        with store._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM emerging_dragon_log
                   WHERE trade_date < ? AND status = 'active'
                   ORDER BY trade_date DESC LIMIT 100""",
                (trade_date,)).fetchall()
    except Exception:
        return []
    if not rows:
        return []

    current_themes = {}
    if current_strength:
        for ts in current_strength.get("strong_themes", []) + current_strength.get("emerging_themes", []):
            for role_name in ("龙头", "中军"):
                for s in ts.get("roles", {}).get(role_name, []):
                    current_themes[s["code"]] = {"theme": ts["theme"], "role": role_name}

    results = []
    for r in rows:
        d = dict(r)
        info = current_themes.get(d["code"])
        if info:
            d["current_status"] = "🐉龙" if info["role"] == "龙头" else "活跃"
            d["current_theme"] = info["theme"]
            d["current_role"] = info["role"]
        else:
            d["current_status"] = "退出"
            d["current_theme"] = ""
            d["current_role"] = ""
        results.append(d)
    return results


def compute_conversion_rate(tracked: list[dict]) -> dict:
    if not tracked:
        return {"total": 0, "promoted": 0, "rate": "—", "note": "数据积累中"}
    total = len(tracked)
    promoted = sum(1 for t in tracked if t.get("current_status") == "🐉龙")
    rate = f"{promoted / total:.0%}" if total > 0 else "—"
    return {"total": total, "promoted": promoted, "rate": rate}


# ============================================================
# 报告渲染
# ============================================================

def render_table(lines: list, emerging_dragons: list[dict]):
    if not emerging_dragons:
        return
    lines.append("### 将成龙 — 领先于板块的率先异动\n")
    lines.append("> 板块前中期 + 个股显著强于板块 + 能在板块调整时逆势走强\n")
    lines.append("| # | 标的 | 代码 | 板块(阶段) | 个股5日 | 板块5日 | 领先 | 逆势 | 涨停 | 未定价 | 总分 | FEV | 人气# |")
    lines.append("|--:|------|------|-----------|------:|------:|-----:|:---:|:----:|------:|-----:|----:|------:|")
    for d in emerging_dragons[:20]:
        name = d.get("name") or d["code"]
        fev = d.get("fev_total") or "-"
        hr = d.get("hot_rank") or "-"
        stage = d.get("stage", "")
        counter_str = f"{d['counter_days']}d" + ("🔥" if d.get("has_new_high") else "") + ("↑" if d.get("near_high") else "")
        zt_parts = []
        if d.get("has_limit"):
            zt_parts.append("涨停")
        if d.get("consecutive_up", 0) >= 2:
            zt_parts.append(f"{d['consecutive_up']}连涨")
        if d.get("near_high"):
            zt_parts.append("近新高")
        zt_str = "/".join(zt_parts) if zt_parts else "—"
        lines.append(
            f"| {d['rank']} | {name} | {d['code']} | {d['theme']}({stage}) "
            f"| {d['r5']:+.1f}% | {d['sector_r5']:+.1f}% | +{d['rel_5d']:.1f}pp "
            f"| {counter_str} | {zt_str} | {d['unpriced_score']:.0f} | {d['value_score']:.0f} "
            f"| {fev} | {hr} |"
        )
    lines.append("")


def render_tracking(lines: list, tracked: list[dict], stats: dict):
    if not tracked:
        return
    lines.append("## 附：将成龙追踪\n")
    note = stats.get("note", "")
    if note:
        lines.append(f"> {note}\n")
    else:
        lines.append(f"> 累计发现 {stats['total']} 只 | 晋升 {stats['promoted']} 只 ({stats['rate']}) | 数据积累中\n")

    lines.append("| 发现日期 | 标的 | 板块 | 5日% | 性价比 | 当前状态 | 现属板块 |")
    lines.append("|---------|------|------|-----:|------:|:--------:|---------|")
    for t in tracked[:20]:
        name = t.get("name") or t.get("code", "")
        status = t.get("current_status", "—")
        cur_theme = t.get("current_theme", "") or "—"
        lines.append(
            f"| {t.get('trade_date', '')} | {name} | {t.get('theme', '')} "
            f"| {t.get('r5', 0):+.1f}% | {t.get('value_score', 0):.0f} "
            f"| {status} | {cur_theme} |"
        )
    lines.append("")
