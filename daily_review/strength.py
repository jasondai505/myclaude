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
        name = q.get("name", code)
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

    # --- 将成龙 ---
    emerging = []
    stage = theme_info.get("stage", "")
    if stage == "爆发初期":
        all_codes = leader_codes | zhongjun_codes | {c["code"] for c in quant}
        emerging_pool = [c for c in candidates if c["code"] not in all_codes]
        emerging_pool = [
            c for c in emerging_pool
            if (c["consecutive_up"] >= 2 or c["has_limit"])
            and c["vol_ratio"] > 1.2
        ]
        if not emerging_pool:
            emerging_pool = [
                c for c in candidates if c["code"] not in all_codes
                and (c["consecutive_up"] >= 2 or c["has_limit"])
            ]
        emerging_pool.sort(key=lambda x: -x["r5"])
        emerging = emerging_pool[:3]
        for c in emerging:
            signals = []
            if c["has_limit"]:
                signals.append("近期涨停")
            if c["consecutive_up"] >= 2:
                signals.append(f"{c['consecutive_up']}日连涨")
            if c["vol_ratio"] > 1.5:
                signals.append(f"量比{c['vol_ratio']:.1f}")
            c["role_reason"] = "，".join(signals) if signals else f"5日+{c['r5']:.1f}%"

    return {
        "龙头": leaders,
        "中军": zhongjun,
        "量化标的": quant,
        "将成龙": emerging,
    }


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
