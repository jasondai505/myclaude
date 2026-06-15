"""板块/个股强弱分析与角色识别引擎"""
import pandas as pd
from config import (
    STRENGTH_MIN_LEVEL, STRENGTH_RISING_THRESHOLD,
    LEADER_WEIGHT_DATE, LEADER_WEIGHT_RETURN, LEADER_WEIGHT_FREQ,
    ZHONGJUN_MIN_MCAP, ZHONGJUN_TURNOVER_RANGE,
    QUANT_MAX_MCAP, QUANT_BOARDS,
    EMERGING_MAX_LEVEL, EMERGING_MAX_COUNT,
)


def _board_type(code: str) -> str:
    if code.startswith("68"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith(("8", "920")):
        return "北交所"
    return "主板"


def _calc_nd_return(kdf: pd.DataFrame, n: int) -> float | None:
    if kdf is None or len(kdf) < n + 1:
        return None
    closes = kdf["close"].values
    return (closes[-1] / closes[-(n + 1)] - 1) * 100


def _is_limit_up(kdf: pd.DataFrame, idx: int) -> bool:
    if kdf is None or idx < 1 or idx >= len(kdf):
        return False
    prev_close = kdf["close"].iloc[idx - 1]
    close = kdf["close"].iloc[idx]
    if prev_close <= 0:
        return False
    pct = (close / prev_close - 1) * 100
    return pct >= 9.5


def _has_ma_bull(kdf: pd.DataFrame) -> bool:
    if kdf is None or len(kdf) < 20:
        return False
    c = kdf["close"].values
    ma5 = c[-5:].mean()
    ma10 = c[-10:].mean()
    ma20 = c[-20:].mean()
    return ma5 > ma10 > ma20


def _broke_ma20(kdf: pd.DataFrame) -> bool:
    if kdf is None or len(kdf) < 21:
        return False
    ma20_today = kdf["close"].values[-20:].mean()
    ma20_yest = kdf["close"].values[-21:-1].mean()
    return kdf["close"].values[-1] > ma20_today and kdf["close"].values[-2] <= ma20_yest


def _macd_golden(kdf: pd.DataFrame) -> bool:
    if kdf is None or "dif" not in kdf.columns or "dea" not in kdf.columns:
        return False
    if len(kdf) < 2:
        return False
    return kdf["dif"].iloc[-1] > kdf["dea"].iloc[-1] and kdf["dif"].iloc[-2] <= kdf["dea"].iloc[-2]


def _price_bucket(price: float) -> str:
    if price < 10:
        return "<10元"
    if price < 30:
        return "10-30元"
    if price < 100:
        return "30-100元"
    return ">100元"


def _mcap_bucket(mcap: float) -> str:
    if mcap < 50:
        return "<50亿"
    if mcap < 150:
        return "50-150亿"
    if mcap < 500:
        return "150-500亿"
    return ">500亿"


# ============================================================
# 1. 板块强度计算
# ============================================================

def compute_theme_strength(
    theme_pool: dict[str, dict[str, dict]],
    klines_map: dict[str, pd.DataFrame],
    quotes_map: dict[str, dict],
    leveled_themes: list[dict],
    aesthetics_map: dict[str, dict],
) -> list[dict]:
    leveled_lookup = {t["theme"]: t for t in leveled_themes}
    results = []

    for theme, stocks_info in theme_pool.items():
        t = leveled_lookup.get(theme)
        if not t or t.get("level", 0) < STRENGTH_MIN_LEVEL:
            continue

        pool_codes = list(stocks_info.keys())
        returns_5d = []
        returns_10d = []
        today_up = 0
        today_limit = 0

        for code in pool_codes:
            kdf = klines_map.get(code)
            q = quotes_map.get(code, {})
            r5 = _calc_nd_return(kdf, 5)
            r10 = _calc_nd_return(kdf, 10)
            if r5 is not None:
                returns_5d.append(r5)
            if r10 is not None:
                returns_10d.append(r10)
            chg = q.get("change_pct", 0)
            if chg and chg > 0:
                today_up += 1
            if chg and chg >= 9.5:
                today_limit += 1

        avg_5d = sum(returns_5d) / len(returns_5d) if returns_5d else 0
        avg_10d = sum(returns_10d) / len(returns_10d) if returns_10d else 0
        momentum = avg_5d - avg_10d / 2 if avg_10d else avg_5d
        breadth = today_up / len(pool_codes) if pool_codes else 0

        level = t.get("level", 1)
        cons = t.get("consecutive_days", 0)
        narrative = t.get("narrative", "")
        today_count = t.get("today_count", 0)

        if narrative in ("Violation", "Reversal") or (avg_5d < -3 and today_count == 0):
            stage = "退潮"
        elif level <= EMERGING_MAX_LEVEL and cons <= 3 and today_count < EMERGING_MAX_COUNT:
            stage = "爆发初期"
        elif level >= 3 and cons >= 4 and breadth > 0.4:
            stage = "主升浪"
        elif today_limit >= 10 and avg_5d > 15:
            stage = "高潮"
        else:
            stage = "活跃"

        aes = aesthetics_map.get(theme, {})
        driver = aes.get("driver", "待确认")
        catalyst_type = "情绪催化" if driver == "待确认" else f"逻辑催化（{driver}）"

        score = level * 2 + avg_5d * 0.1 + momentum * 0.05 + breadth * 5

        results.append({
            "theme": theme,
            "level": level,
            "label": t.get("label", ""),
            "consecutive_days": cons,
            "narrative": narrative,
            "stage": stage,
            "catalyst_type": catalyst_type,
            "avg_5d": round(avg_5d, 1),
            "avg_10d": round(avg_10d, 1),
            "momentum": round(momentum, 1),
            "breadth": round(breadth * 100, 1),
            "today_count": today_count,
            "today_limit": today_limit,
            "pool_size": len(pool_codes),
            "score": round(score, 2),
        })

    results.sort(key=lambda x: -x["score"])
    return results


# ============================================================
# 2. 板块内角色识别
# ============================================================

def identify_roles(
    theme: str,
    pool_stocks: dict[str, dict],
    klines_map: dict[str, pd.DataFrame],
    quotes_map: dict[str, dict],
    theme_info: dict,
    zt_pool: dict[str, dict] = None,
) -> dict[str, list[dict]]:
    if zt_pool is None:
        zt_pool = {}
    candidates = []
    for code, info in pool_stocks.items():
        kdf = klines_map.get(code)
        q = quotes_map.get(code, {})
        r10 = _calc_nd_return(kdf, 10)
        r5 = _calc_nd_return(kdf, 5)
        mcap = q.get("mcap_yi", 0) or 0
        turnover = q.get("turnover_pct", 0) or 0
        price = q.get("price", 0) or 0
        name = q.get("name") or code
        chg = q.get("change_pct", 0) or 0
        vol_ratio = q.get("vol_ratio", 0) or 0

        zt = zt_pool.get(code, {})
        zt_time = zt.get("first_time", "")
        consecutive_boards = zt.get("consecutive_boards", 0)

        has_limit = False
        if kdf is not None and len(kdf) >= 11:
            for i in range(max(1, len(kdf) - 10), len(kdf)):
                if _is_limit_up(kdf, i):
                    has_limit = True
                    break

        consecutive_up = 0
        if kdf is not None and len(kdf) >= 4:
            closes = kdf["close"].values
            for j in range(1, min(4, len(closes))):
                if closes[-j] > closes[-j - 1]:
                    consecutive_up += 1
                else:
                    break

        candidates.append({
            "code": code,
            "name": name,
            "freq": info["freq"],
            "first_date": info["first_date"],
            "last_date": info["last_date"],
            "r10": r10 if r10 is not None else (r5 if r5 is not None else 0),
            "r5": r5 if r5 is not None else 0,
            "chg": chg,
            "mcap_yi": mcap,
            "turnover": turnover,
            "price": price,
            "board": _board_type(code),
            "has_limit": has_limit,
            "consecutive_up": consecutive_up,
            "vol_ratio": vol_ratio,
            "zt_time": zt_time,
            "consecutive_boards": consecutive_boards,
            "amount_wan": q.get("amount_wan", 0) or 0,
        })

    if not candidates:
        return {"龙头": [], "中军": [], "量化标的": [], "将成龙": []}

    # --- 龙头 ---
    sorted_by_date = sorted(candidates, key=lambda x: x["first_date"])
    sorted_by_return = sorted(candidates, key=lambda x: -x["r10"])
    sorted_by_freq = sorted(candidates, key=lambda x: -x["freq"])

    n = len(candidates)
    scores = {}
    for i, c in enumerate(sorted_by_date):
        scores[c["code"]] = scores.get(c["code"], 0) + (1 - i / max(n, 1)) * LEADER_WEIGHT_DATE
    for i, c in enumerate(sorted_by_return):
        scores[c["code"]] = scores.get(c["code"], 0) + (1 - i / max(n, 1)) * LEADER_WEIGHT_RETURN
    for i, c in enumerate(sorted_by_freq):
        scores[c["code"]] = scores.get(c["code"], 0) + (1 - i / max(n, 1)) * LEADER_WEIGHT_FREQ

    for c in candidates:
        c["leader_score"] = round(scores.get(c["code"], 0), 3)

    candidates_sorted = sorted(candidates, key=lambda x: -x["leader_score"])
    leaders = candidates_sorted[:2]
    leader_codes = {c["code"] for c in leaders}
    for c in leaders:
        c["role_reason"] = f"启动{c['first_date'][-5:]}，10日+{c['r10']:.1f}%，出现{c['freq']}次"

    # --- 中军 ---
    remaining = [c for c in candidates if c["code"] not in leader_codes]
    mcap_values = [c["mcap_yi"] for c in candidates if c["mcap_yi"] > 0]
    mcap_median = sorted(mcap_values)[len(mcap_values) // 2] if mcap_values else 100
    mcap_threshold = max(mcap_median, ZHONGJUN_MIN_MCAP)

    zhongjun_pool = [
        c for c in remaining
        if c["mcap_yi"] >= mcap_threshold
        and ZHONGJUN_TURNOVER_RANGE[0] <= c["turnover"] <= ZHONGJUN_TURNOVER_RANGE[1]
    ]
    if not zhongjun_pool:
        zhongjun_pool = [c for c in remaining if c["mcap_yi"] >= mcap_threshold]

    zhongjun_pool.sort(key=lambda x: -(x["freq"] * x["mcap_yi"]))
    zhongjun = zhongjun_pool[:3]
    zhongjun_codes = {c["code"] for c in zhongjun}
    for c in zhongjun:
        c["role_reason"] = f"市值{c['mcap_yi']:.0f}亿，出现{c['freq']}次"

    # --- 量化标的 ---
    remaining2 = [c for c in remaining if c["code"] not in zhongjun_codes]
    quant_pool = [
        c for c in remaining2
        if c["board"] in ("创业板", "科创板", "北交所")
        and c["mcap_yi"] < QUANT_MAX_MCAP
        and c["has_limit"]
    ]
    if not quant_pool:
        quant_pool = [
            c for c in remaining2
            if c["board"] in ("创业板", "科创板", "北交所")
            and c["mcap_yi"] < QUANT_MAX_MCAP
            and c["r10"] > 5
        ]
    quant_pool.sort(key=lambda x: -x["r10"])
    quant = quant_pool[:5]
    for c in quant:
        c["role_reason"] = f"{c['board']}，{c['mcap_yi']:.0f}亿，10日+{c['r10']:.1f}%"

    return {
        "龙头": leaders,
        "中军": zhongjun,
        "量化标的": quant,
        "将成龙": [],
    }


# ============================================================
# 2b. 将成龙 — 跨板块性价比筛选
# ============================================================

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


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

    # 板块聚合：stage + 平均涨跌幅
    sector_info: dict[str, dict] = {}
    for ts in (strength_data.get("strong_themes", [])
               + strength_data.get("emerging_themes", [])
               + strength_data.get("fading_themes", [])):
        sector_info[ts["theme"]] = ts

    # 板块逐日平均涨跌（从板块内 K线计算）
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
        """从 K线直接计算板块 N 日平均涨跌幅"""
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

        # 从 K线直接算板块均值（strength_data 可能因 level<2 不包含此主题）
        sector_r5 = si.get("avg_5d") or _sector_avg_return(pool_stocks, 5)
        sector_r10 = si.get("avg_10d") or _sector_avg_return(pool_stocks, 10)
        # 板块阶段，strength_data 没有时从涨跌和涨停数推断
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

            # 逐日个股涨跌（5天，最近=index 0）
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
                # 20日新高 / 近新高(>90%分位)
                if len(highs) >= 20:
                    prev_high = max(highs[-20:-5])
                    recent_high = max(highs[-5:])
                    has_new_high = recent_high > prev_high
                    near_high = recent_high > prev_high * 0.9 and not has_new_high
                # 近期涨停（只看今天+昨天）
                if len(kdf) >= 3:
                    for i in range(max(1, len(kdf) - 2), len(kdf)):
                        if _is_limit_up(kdf, i):
                            has_limit = True
                            break
                # 连涨天数
                for j in range(1, min(4, len(closes))):
                    if closes[-j] > closes[-j - 1]:
                        consecutive_up += 1
                    else:
                        break
            else:
                stock_daily = [0.0] * 5

            # 相对强度：领先板块多少 (0-60)
            rel_5d = r5 - sector_r5
            rel_10d = r10 - sector_r10
            rel_score = _clamp(rel_5d * 2, 0, 35) + _clamp(rel_10d * 1.2, 0, 25)

            # 逆势信号：个股涨+板块跌 (0-35, 近期加权)
            counter_days = 0
            counter_weighted = 0
            recency_w = [3, 2, 1, 1, 1]
            for i, (sd, sec_d) in enumerate(zip(stock_daily, sdr)):
                if sd > 0 and sec_d < 0:
                    counter_days += 1
                    counter_weighted += recency_w[i] if i < len(recency_w) else 1
            counter_score = counter_weighted * 3 + (10 if has_new_high and counter_days >= 1 else 0)

            # 涨停 + 连涨 (0-12, 适度加分)
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
                "code": code, "name": name, "theme": theme,
                "stage": stage,
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

    # 去重：同股票只保留最高分
    seen = {}
    for c in candidates:
        code = c["code"]
        if code not in seen or c["value_score"] > seen[code]["value_score"]:
            seen[code] = c

    result = sorted(seen.values(), key=lambda x: -x["value_score"])
    for i, c in enumerate(result):
        c["rank"] = i + 1

    return result[:30]


def save_emerging_dragons(trade_date: str, dragons: list[dict]):
    """将当日的将成龙标的持久化到 SQLite"""
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
    """回顾历史将成龙标的，对比当前状态。"""
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
    """统计将成龙→龙的转化率"""
    if not tracked:
        return {"total": 0, "promoted": 0, "rate": "—", "note": "数据积累中"}
    total = len(tracked)
    promoted = sum(1 for t in tracked if t.get("current_status") == "🐉龙")
    rate = f"{promoted / total:.0%}" if total > 0 else "—"
    return {"total": total, "promoted": promoted, "rate": rate}


# ============================================================
# 3. 走强个股共性分析
# ============================================================

def analyze_rising_commonalities(
    all_codes: list[str],
    klines_map: dict[str, pd.DataFrame],
    quotes_map: dict[str, dict],
    code_to_themes: dict[str, list[str]],
) -> dict:
    rising = []
    for code in all_codes:
        kdf = klines_map.get(code)
        q = quotes_map.get(code, {})
        r5 = _calc_nd_return(kdf, 5)
        if r5 is not None and r5 >= STRENGTH_RISING_THRESHOLD * 100:
            rising.append({
                "code": code,
                "name": q.get("name", code),
                "r5": r5,
                "mcap_yi": q.get("mcap_yi", 0) or 0,
                "price": q.get("price", 0) or 0,
                "board": _board_type(code),
                "themes": code_to_themes.get(code, []),
                "ma_bull": _has_ma_bull(kdf),
                "broke_ma20": _broke_ma20(kdf),
                "macd_golden": _macd_golden(kdf),
            })

    if not rising:
        return {"count": 0, "stocks": [], "theme_dist": [], "mcap_dist": {},
                "board_dist": {}, "price_dist": {}, "tech_dist": {}, "conclusion": "近期无明显走强个股"}

    rising.sort(key=lambda x: -x["r5"])

    theme_counter: dict[str, int] = {}
    for s in rising:
        for t in s["themes"]:
            theme_counter[t] = theme_counter.get(t, 0) + 1
    theme_dist = sorted(theme_counter.items(), key=lambda x: -x[1])[:5]

    mcap_dist: dict[str, int] = {}
    board_dist: dict[str, int] = {}
    price_dist: dict[str, int] = {}
    ma_bull_count = 0
    broke_ma20_count = 0
    macd_golden_count = 0

    for s in rising:
        b = _mcap_bucket(s["mcap_yi"])
        mcap_dist[b] = mcap_dist.get(b, 0) + 1
        board_dist[s["board"]] = board_dist.get(s["board"], 0) + 1
        p = _price_bucket(s["price"])
        price_dist[p] = price_dist.get(p, 0) + 1
        if s["ma_bull"]:
            ma_bull_count += 1
        if s["broke_ma20"]:
            broke_ma20_count += 1
        if s["macd_golden"]:
            macd_golden_count += 1

    n = len(rising)
    tech_dist = {
        "多头排列": f"{ma_bull_count / n * 100:.0f}%",
        "突破MA20": f"{broke_ma20_count / n * 100:.0f}%",
        "MACD金叉": f"{macd_golden_count / n * 100:.0f}%",
    }

    def _pct_str(d: dict) -> dict:
        return {k: f"{v}只({v / n * 100:.0f}%)" for k, v in sorted(d.items(), key=lambda x: -x[1])}

    top_themes = "、".join(f"{t}({c})" for t, c in theme_dist[:3]) if theme_dist else "分散"
    top_mcap = max(mcap_dist, key=mcap_dist.get) if mcap_dist else "未知"
    top_board = max(board_dist, key=board_dist.get) if board_dist else "未知"

    conclusion = f"近期赚钱效应集中在 {top_mcap}+{top_board}+{top_themes} 方向"

    return {
        "count": n,
        "stocks": rising[:20],
        "theme_dist": theme_dist,
        "mcap_dist": _pct_str(mcap_dist),
        "board_dist": _pct_str(board_dist),
        "price_dist": _pct_str(price_dist),
        "tech_dist": tech_dist,
        "conclusion": conclusion,
    }


# ============================================================
# 4. 汇总入口
# ============================================================

def run_strength_analysis(
    theme_pool: dict[str, dict[str, dict]],
    klines_map: dict[str, pd.DataFrame],
    quotes_map: dict[str, dict],
    leveled_themes: list[dict],
    aesthetics_map: dict[str, dict],
    code_to_themes: dict[str, list[str]],
    zt_pool: dict[str, dict] = None,
) -> dict:
    if zt_pool is None:
        zt_pool = {}
    theme_strengths = compute_theme_strength(
        theme_pool, klines_map, quotes_map, leveled_themes, aesthetics_map,
    )

    strong_themes = []
    emerging_themes = []
    fading_themes = []

    for ts in theme_strengths:
        theme = ts["theme"]
        pool_stocks = theme_pool.get(theme, {})
        roles = identify_roles(theme, pool_stocks, klines_map, quotes_map, ts, zt_pool)
        ts["roles"] = roles

        if ts["stage"] == "退潮":
            fading_themes.append(ts)
        elif ts["stage"] == "爆发初期":
            emerging_themes.append(ts)
        else:
            strong_themes.append(ts)

    all_pool_codes = list({code for stocks in theme_pool.values() for code in stocks})
    commonalities = analyze_rising_commonalities(
        all_pool_codes, klines_map, quotes_map, code_to_themes,
    )

    return {
        "strong_themes": strong_themes,
        "emerging_themes": emerging_themes,
        "fading_themes": fading_themes,
        "rising_commonalities": commonalities,
    }


# ============================================================
# 4. A2: 板块成交聚合 + F/E/V 平均（板块强弱表用）
# ============================================================

SMALL_CAP_THRESHOLD_WAN = 10000  # 1 亿


def attach_theme_amount_aggregates(
    strength_result: dict,
    theme_pool: dict[str, dict[str, dict]],
    quotes_map: dict[str, dict],
    zt_pool: dict[str, dict],
    hot100_codes: set,
) -> None:
    """为 strong/emerging/fading 各 theme 附加成交聚合（万）+ 体量标记。"""
    zt_set = set(zt_pool.keys()) if zt_pool else set()
    h100 = hot100_codes or set()

    def _agg(theme: str) -> dict:
        codes = list(theme_pool.get(theme, {}).keys())
        zt_w = nonzt_w = top100_w = total_w = 0.0
        for code in codes:
            q = quotes_map.get(code, {})
            amt = q.get("amount_wan", 0) or 0
            total_w += amt
            if code in zt_set or (q.get("change_pct", 0) or 0) >= 9.5:
                zt_w += amt
            else:
                nonzt_w += amt
            if code in h100:
                top100_w += amt
        return {
            "amount_zt_wan": zt_w,
            "amount_nonzt_wan": nonzt_w,
            "amount_top100_wan": top100_w,
            "amount_total_wan": total_w,
            "small_cap_flag": total_w > 0 and total_w < SMALL_CAP_THRESHOLD_WAN,
        }

    for key in ("strong_themes", "emerging_themes", "fading_themes"):
        for ts in strength_result.get(key, []):
            ts.update(_agg(ts["theme"]))


def attach_theme_fev_aggregates(
    strength_result: dict,
    theme_pool: dict[str, dict[str, dict]],
    fev_per_code: dict[str, dict],
) -> None:
    """为 strong/emerging/fading 各 theme 计算 F/E/V 平均（仅含有 FEV 数据的成员）。"""
    def _avg(theme: str) -> dict:
        codes = list(theme_pool.get(theme, {}).keys())
        fs, es, vs = [], [], []
        for code in codes:
            fev = fev_per_code.get(code)
            if not fev:
                continue
            f = fev.get("f_score")
            e = fev.get("e_score")
            v = fev.get("v_score")
            if f is not None:
                fs.append(f)
            if e is not None:
                es.append(e)
            if v is not None:
                vs.append(v)
        return {
            "f_avg": round(sum(fs) / len(fs), 1) if fs else None,
            "e_avg": round(sum(es) / len(es), 1) if es else None,
            "v_avg": round(sum(vs) / len(vs), 1) if vs else None,
            "fev_n": len(fs),
        }

    for key in ("strong_themes", "emerging_themes", "fading_themes"):
        for ts in strength_result.get(key, []):
            ts.update(_avg(ts["theme"]))
