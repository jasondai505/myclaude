"""每日复盘系统 - 分析引擎"""
import re
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

from config import (
    MA_PERIODS, VOLUME_BREAKOUT_RATIO, RSI_OVERBOUGHT, RSI_OVERSOLD,
    THEME_LEVEL_RULES, THEME_DRIVER_KEYWORDS, THEME_LANDING_KEYWORDS,
    POSITION_THRESHOLDS, ALPHA_BUCKET_LABELS, SURGE_NEWS_KEYWORDS,
    FEV_THRESHOLDS,
    THEME_EXPAND_MIN_LEVEL, THEME_ZHONGJUN_MIN_LEVEL,
    THEME_EXPAND_MAX_STOCKS, THEME_ZHONGJUN_MIN_FREQ_30D,
    THEME_STOPWORDS, THEME_ALIAS,
    MERGE_POOL_MAX_CONCEPTS, MERGE_POOL_MIN_FREQ,
)
import store


# ============================================================
# 1. 大盘分析
# ============================================================

def analyze_market(indices: dict, industry_data: dict = None,
                    hot_df: pd.DataFrame = None,
                    zt_pool: dict = None, dt_pool: dict = None,
                    trade_date: str = None) -> dict:
    idx_map = {
        "上证指数": "sh", "深证成指": "sz", "创业板指": "cyb",
        "科创50": "kc50", "沪深300": "hs300", "中证500": "zz500",
    }
    result = {"indices": {}, "sentiment": ""}
    up_cnt = 0
    for label, data in indices.items():
        if label in idx_map:
            result["indices"][label] = data
            if data.get("change_pct", 0) > 0:
                up_cnt += 1

    total = len(result["indices"])
    if total == 0:
        result["sentiment"] = "数据不足"
    elif up_cnt >= total * 0.7:
        result["sentiment"] = "偏多"
    elif up_cnt <= total * 0.3:
        result["sentiment"] = "偏空"
    else:
        result["sentiment"] = "震荡分化"

    # --- 流动性指标 ---
    sh_amount = indices.get("上证指数", {}).get("amount_wan", 0)
    sz_amount = indices.get("深证成指", {}).get("amount_wan", 0)
    total_amount_yi = (sh_amount + sz_amount) / 10000
    result["total_amount_yi"] = round(total_amount_yi, 0)

    snapshots = store.load_recent_snapshots(20)
    amounts = [s["total_amount_yi"] for s in snapshots if s.get("total_amount_yi")]
    if amounts:
        ma5 = sum(amounts[-5:]) / len(amounts[-5:]) if len(amounts) >= 5 else sum(amounts) / len(amounts)
        ma20 = sum(amounts) / len(amounts)
        result["amount_ma5"] = round(ma5, 0)
        result["amount_ma20"] = round(ma20, 0)
        if total_amount_yi > ma5 * 1.1:
            result["liquidity"] = "放量"
        elif total_amount_yi < ma5 * 0.9:
            result["liquidity"] = "缩量"
        else:
            result["liquidity"] = "平量"
        result["amount_vs_ma5"] = round((total_amount_yi / ma5 - 1) * 100, 1) if ma5 > 0 else 0
    else:
        result["liquidity"] = "首日无对比"
        result["amount_ma5"] = 0
        result["amount_ma20"] = 0
        result["amount_vs_ma5"] = 0

    # --- 赚钱效应 ---
    breadth = industry_data or {}
    total_up = breadth.get("total_up", 0)
    total_down = breadth.get("total_down", 0)
    total_stocks = total_up + total_down
    result["up_count"] = total_up
    result["down_count"] = total_down

    limit_up_count = len(hot_df) if hot_df is not None and not hot_df.empty else 0
    result["limit_up_count"] = limit_up_count

    # --- A1: 涨停/连板/跌停（排除 ST），用于 10 日趋势 ---
    def _is_st(name: str) -> bool:
        return bool(name) and (name.startswith("ST") or name.startswith("*ST"))

    limit_up_filtered = 0
    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            if not _is_st(str(row.get("名称", ""))):
                limit_up_filtered += 1
    result["limit_up_filtered"] = limit_up_filtered

    limit_up_2plus = 0
    if zt_pool:
        for code, info in zt_pool.items():
            if info.get("consecutive_boards", 1) >= 2 and not _is_st(info.get("name", "")):
                limit_up_2plus += 1
    elif hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            if _is_st(str(row.get("名称", ""))):
                continue
            tags = str(row.get("题材归因", ""))
            if "连板" in tags:
                limit_up_2plus += 1
    result["limit_up_2plus"] = limit_up_2plus

    limit_down_count = 0
    if dt_pool:
        for code, info in dt_pool.items():
            if not _is_st(info.get("name", "")):
                limit_down_count += 1
    result["limit_down_count"] = limit_down_count

    # 10 日历史（含今日，按升序）
    if trade_date:
        history = store.get_market_snapshot_history(trade_date, 10)
        # 替换今日为最新值（DB 写入发生在 analyze 之后）
        today_row = {
            "date": trade_date,
            "total_amount_yi": result.get("total_amount_yi"),
            "limit_up_count": limit_up_filtered,
            "limit_up_2plus": limit_up_2plus,
            "limit_down_count": limit_down_count,
        }
        if history and history[-1]["date"] == trade_date:
            history[-1].update(today_row)
        else:
            history.append(today_row)
        result["history_10d"] = history
        # 昨日成交额（10 日序列倒数第二个）
        if len(history) >= 2:
            prev = history[-2].get("total_amount_yi")
            result["prev_total_amount_yi"] = prev
        else:
            result["prev_total_amount_yi"] = None

    if total_stocks > 0:
        up_ratio = total_up / total_stocks
        if up_ratio > 0.65 and limit_up_count > 40:
            result["profit_effect"] = "强"
        elif up_ratio > 0.55:
            result["profit_effect"] = "中等"
        elif up_ratio > 0.4:
            result["profit_effect"] = "偏弱"
        else:
            result["profit_effect"] = "冰点"
    else:
        result["profit_effect"] = "N/A"

    return result


# ============================================================
# 2. 市场风格分析
# ============================================================

def analyze_style(indices: dict) -> dict:
    """对比大盘/小盘、成长/价值"""
    style = {}

    large = indices.get("大盘价值(上证50)", {})
    small = indices.get("小盘(中证1000)", {})
    growth = indices.get("成长(创业板指)", {})

    large_chg = large.get("change_pct", 0)
    small_chg = small.get("change_pct", 0)
    growth_chg = growth.get("change_pct", 0)

    if small_chg - large_chg > 0.5:
        style["size"] = "小盘占优"
    elif large_chg - small_chg > 0.5:
        style["size"] = "大盘占优"
    else:
        style["size"] = "大小盘均衡"

    if growth_chg - large_chg > 0.5:
        style["growth_value"] = "成长占优"
    elif large_chg - growth_chg > 0.5:
        style["growth_value"] = "价值占优"
    else:
        style["growth_value"] = "成长价值均衡"

    style["detail"] = {
        "大盘价值": large_chg,
        "成长": growth_chg,
        "小盘": small_chg,
    }

    return style


# ============================================================
# 2.5 情绪面分析（连板 / 龙头 / 涨停类型）
# ============================================================

def analyze_sentiment(hot_df: pd.DataFrame) -> dict:
    """从涨停/强势股数据提取情绪面指标"""
    result = {
        "ladder": {},
        "leader": None,
        "biggest_vol_limit": None,
        "logic_count": 0,
        "emotion_count": 0,
        "logic_stocks": [],
        "emotion_stocks": [],
        "st_stocks": [],
    }
    if hot_df is None or hot_df.empty:
        return result

    # 连板梯队
    ladder: dict[int, list] = {}
    emotion_keywords = {"摘帽", "次新", "超跌", "反弹", "低价"}

    for _, row in hot_df.iterrows():
        reason = str(row.get("题材归因", ""))
        name = str(row.get("名称", ""))
        code = str(row.get("代码", ""))
        amount = float(row.get("成交额", 0) or 0)

        # 连板检测
        board_n = 0
        for tag in reason.split("+"):
            tag = tag.strip()
            if "连板" in tag:
                import re
                m = re.search(r"(\d+)", tag)
                if m:
                    board_n = int(m.group(1))
                    break

        stock_info = {"name": name, "code": code, "reason": reason, "amount": amount, "board_n": board_n}

        if name.startswith(("ST", "*ST")):
            result["st_stocks"].append(stock_info)
            continue

        if board_n >= 2:
            ladder.setdefault(board_n, []).append({
                "name": name, "code": code, "reason": reason, "amount": amount,
            })

        is_emotion = any(kw in reason or kw in name for kw in emotion_keywords)
        if is_emotion:
            result["emotion_count"] += 1
            result["emotion_stocks"].append(stock_info)
        else:
            result["logic_count"] += 1
            result["logic_stocks"].append(stock_info)

    # 连板梯队排序
    for n in sorted(ladder.keys(), reverse=True):
        ladder[n].sort(key=lambda x: x["amount"], reverse=True)
    result["ladder"] = dict(sorted(ladder.items(), reverse=True))

    # 最高板 = 情绪龙头
    if ladder:
        max_board = max(ladder.keys())
        result["leader"] = {
            "board": max_board,
            "stocks": ladder[max_board],
        }

    # 成交额最大的涨停股 = 辨识度龙头（排除 ST）
    all_rows = []
    for _, row in hot_df.iterrows():
        nm = str(row.get("名称", ""))
        if nm.startswith(("ST", "*ST")):
            continue
        all_rows.append({
            "name": nm,
            "code": str(row.get("代码", "")),
            "amount": float(row.get("成交额", 0) or 0),
        })
    if all_rows:
        biggest = max(all_rows, key=lambda x: x["amount"])
        result["biggest_vol_limit"] = biggest

    return result


# ============================================================
# 2.6 逻辑/情绪涨停四维分类（B1）
# ============================================================

_LOGIC_DRIVER_STRONG = ["合同", "订单", "中标", "签约", "业绩预增", "扭亏",
                         "增持", "回购", "获批", "投产", "扩产", "并表"]
_LOGIC_DRIVER_WEAK = ["产品", "验证", "高新", "立项", "签订", "授权"]
_EMOTION_DRIVER_STRONG = ["澄清", "异常波动", "无应披露", "提示风险", "终止", "撤回"]
_EMOTION_DRIVER_WEAK = ["异动", "媒体报道", "传闻"]


def _score_driver(announcements: list[dict]) -> int:
    """公告标题驱动力评分: 强逻辑 +2 / 弱逻辑 +1 / 中性 0 / 弱情绪 -1 / 强情绪 -2"""
    if not announcements:
        return 0
    titles = [str(a.get("title", "")) for a in announcements if a]
    has_strong_logic = any(kw in t for t in titles for kw in _LOGIC_DRIVER_STRONG)
    has_strong_emo = any(kw in t for t in titles for kw in _EMOTION_DRIVER_STRONG)
    if has_strong_logic and not has_strong_emo:
        return 2
    if has_strong_emo and not has_strong_logic:
        return -2
    has_weak_logic = any(kw in t for t in titles for kw in _LOGIC_DRIVER_WEAK)
    has_weak_emo = any(kw in t for t in titles for kw in _EMOTION_DRIVER_WEAK)
    if has_weak_logic and not has_weak_emo:
        return 1
    if has_weak_emo and not has_weak_logic:
        return -1
    return 0


def _score_trend(kline) -> int:
    """走势：均线上行 +1 / 一字板连续 -1 / 异常波动 -1 / 中性 0"""
    if kline is None:
        return 0
    try:
        if hasattr(kline, "empty") and kline.empty:
            return 0
        closes = kline["close"].tolist() if "close" in kline else []
        opens = kline["open"].tolist() if "open" in kline else []
        highs = kline["high"].tolist() if "high" in kline else []
        lows = kline["low"].tolist() if "low" in kline else []
    except Exception:
        return 0
    if len(closes) < 3:
        return 0
    one_word_days = 0
    for i in range(max(0, len(closes) - 2), len(closes)):
        if i < len(opens) and i < len(highs) and i < len(lows):
            if opens[i] and opens[i] == highs[i] == lows[i]:
                one_word_days += 1
    if one_word_days >= 2:
        return -1
    ma_window = closes[-5:] if len(closes) >= 5 else closes
    if len(ma_window) >= 3 and ma_window[-1] > ma_window[0] and all(
        ma_window[i] >= ma_window[i - 1] * 0.99 for i in range(1, len(ma_window))
    ):
        return 1
    return 0


def _score_vp(quote: dict) -> int:
    """量价：温和放量 +1 / 缩量一字 -1 / 极端放量 -1 / 中性 0"""
    if not quote:
        return 0
    turnover = float(quote.get("turnover_pct", 0) or 0)
    open_p = float(quote.get("open", 0) or 0)
    high_p = float(quote.get("high", 0) or 0)
    low_p = float(quote.get("low", 0) or 0)
    limit_up = float(quote.get("limit_up", 0) or 0)
    amplitude = float(quote.get("amplitude_pct", 0) or 0)
    is_one_word = (
        open_p > 0 and limit_up > 0
        and abs(open_p - limit_up) < 0.01
        and abs(high_p - low_p) < 0.01
    )
    if is_one_word and turnover < 3:
        return -1
    if turnover > 35:
        return -1
    if 8 <= turnover <= 25 and amplitude >= 3:
        return 1
    return 0


def _score_lhb(lhb_info: dict | None) -> int:
    """龙虎榜：机构/北向 +2 / 全游资 -1 / 无上榜 0"""
    if not lhb_info:
        return 0
    comment = str(lhb_info.get("comment", "")) or ""
    if "机构" in comment or "深股通" in comment or "沪股通" in comment or "北向" in comment:
        return 2
    if "营业部" in comment:
        return -1
    return 0


def _score_theme_count(themes_count: int) -> int:
    """题材联动：独狼 +1 / 1-2 只 0 / ≥3 只联动 -1"""
    if themes_count <= 1:
        return 1
    if themes_count >= 3:
        return -1
    return 0


def classify_limit_up_type(
    code: str, name: str,
    quote: dict | None = None,
    kline=None,
    lhb_info: dict | None = None,
    announcements: list[dict] | None = None,
    themes_count: int = 1,
) -> dict:
    """单股涨停类型四维分类。返回 {logic_score, emotion_score, net_score, label, breakdown}.
    label ∈ {纯逻辑, 偏逻辑, 混合, 偏情绪, 纯情绪}.
    """
    d = _score_driver(announcements or [])
    t = _score_trend(kline)
    v = _score_vp(quote or {})
    l = _score_lhb(lhb_info)
    th = _score_theme_count(themes_count)

    breakdown = {"driver": d, "trend": t, "vp": v, "lhb": l, "theme_count": th}
    logic_score = sum(s for s in breakdown.values() if s > 0)
    emotion_score = sum(-s for s in breakdown.values() if s < 0)
    net = logic_score - emotion_score

    if net >= 3:
        label = "纯逻辑"
    elif net >= 1:
        label = "偏逻辑"
    elif net <= -3:
        label = "纯情绪"
    elif net <= -1:
        label = "偏情绪"
    else:
        label = "混合"
    return {
        "logic_score": logic_score,
        "emotion_score": emotion_score,
        "net_score": net,
        "label": label,
        "breakdown": breakdown,
    }


def apply_limit_up_classification(
    sentiment: dict,
    zt_pool: dict,
    quotes: dict,
    klines: dict,
    lhb_data: dict,
    corpus_map: dict,
    theme_counts: dict | None = None,
    code_themes: dict | None = None,
) -> None:
    """用四维分类覆盖 sentiment 的 logic/emotion 列表 + 增加 mixed_stocks / by_label。原地修改。"""
    theme_counts = theme_counts or {}
    code_themes = code_themes or {}
    originals = list(sentiment.get("logic_stocks", [])) + list(sentiment.get("emotion_stocks", []))
    seen = set()
    pool: list[dict] = []
    for s in originals:
        c = s.get("code")
        if c and c not in seen:
            seen.add(c)
            pool.append(s)

    new_logic: list[dict] = []
    new_emotion: list[dict] = []
    new_mixed: list[dict] = []
    by_label: dict[str, int] = {"纯逻辑": 0, "偏逻辑": 0, "混合": 0, "偏情绪": 0, "纯情绪": 0}

    for s in pool:
        code = s.get("code", "")
        themes = code_themes.get(code, [])
        max_theme_count = max((theme_counts.get(t, 1) for t in themes), default=1)
        announcements = (corpus_map.get(code) or {}).get("announcements", [])
        r = classify_limit_up_type(
            code=code, name=s.get("name", ""),
            quote=quotes.get(code),
            kline=klines.get(code),
            lhb_info=lhb_data.get(code),
            announcements=announcements,
            themes_count=max_theme_count,
        )
        s2 = dict(s)
        s2["label"] = r["label"]
        s2["net_score"] = r["net_score"]
        s2["logic_score"] = r["logic_score"]
        s2["emotion_score"] = r["emotion_score"]
        s2["breakdown"] = r["breakdown"]
        by_label[r["label"]] = by_label.get(r["label"], 0) + 1
        if r["label"] in ("纯逻辑", "偏逻辑"):
            new_logic.append(s2)
        elif r["label"] in ("纯情绪", "偏情绪"):
            new_emotion.append(s2)
        else:
            new_mixed.append(s2)

    sentiment["logic_stocks"] = new_logic
    sentiment["emotion_stocks"] = new_emotion
    sentiment["mixed_stocks"] = new_mixed
    sentiment["logic_count"] = len(new_logic)
    sentiment["emotion_count"] = len(new_emotion)
    sentiment["mixed_count"] = len(new_mixed)
    sentiment["by_label"] = by_label


# ============================================================
# 3. 行业轮动分析
# ============================================================

def analyze_sectors(industry_data: dict) -> dict:
    all_sectors = industry_data.get("all", [])
    if not all_sectors:
        return {"top": [], "bottom": [], "breadth": {}}

    top5 = all_sectors[:5]
    bottom5 = all_sectors[-5:]

    total_up = industry_data.get("total_up", 0)
    total_down = industry_data.get("total_down", 0)
    total = total_up + total_down
    breadth_pct = round(total_up / total * 100, 1) if total > 0 else 50

    return {
        "top": top5,
        "bottom": bottom5,
        "breadth": {
            "up": total_up,
            "down": total_down,
            "pct": breadth_pct,
        },
    }


# ============================================================
# 4. 题材热度分析（含持续性跟踪）
# ============================================================

def analyze_themes(hot_df: pd.DataFrame, trade_date: str) -> dict:
    if hot_df.empty:
        return {"today": [], "new": [], "persistent": [], "fading": [], "raw_counts": {}}

    # 词频统计（题材标签归一：剔板数碎片/停用词，别名归一到 canonical；同一票同 canonical 只计一次）
    theme_stocks: dict[str, set[str]] = {}
    for _, row in hot_df.iterrows():
        reason = str(row.get("题材归因", ""))
        code = str(row.get("代码", ""))
        for raw in reason.split("+"):
            tag = normalize_theme(raw)
            if tag:
                theme_stocks.setdefault(tag, set()).add(code)

    cnt = Counter({theme: len(codes) for theme, codes in theme_stocks.items()})
    top_themes = cnt.most_common(20)

    theme_data = {}
    for theme, codes in theme_stocks.items():
        theme_data[theme] = {"count": len(codes), "stocks": ",".join(sorted(codes))}

    store.save_themes(trade_date, theme_data)

    # 与前一天对比
    recent_dates = store.get_recent_theme_dates(5)
    yesterday_date = None
    for d in recent_dates:
        if d < trade_date:
            yesterday_date = d

    yesterday_themes = store.load_themes(yesterday_date) if yesterday_date else {}
    today_set = set(cnt.keys())
    yesterday_set = set(yesterday_themes.keys())

    new_themes = today_set - yesterday_set
    fading_themes = yesterday_set - today_set

    # 持续性：连续出现的题材
    persistent = []
    for theme in today_set & yesterday_set:
        today_count = cnt[theme]
        yest_count = yesterday_themes[theme]["count"]
        trend = "↑" if today_count > yest_count else ("↓" if today_count < yest_count else "→")
        persistent.append({
            "theme": theme,
            "today_count": today_count,
            "yesterday_count": yest_count,
            "trend": trend,
        })
    persistent.sort(key=lambda x: x["today_count"], reverse=True)

    # --- 题材分级（5级）+ 叙事周期 ---
    leveled_themes = []
    for theme, count in cnt.most_common(30):
        cons_days = store.get_theme_consecutive_days(theme, trade_date)
        cum_stocks = store.get_theme_cumulative_stocks(theme)
        level = 1
        for lv in range(5, 0, -1):
            rule = THEME_LEVEL_RULES[lv]
            if cons_days >= rule["min_days"]:
                level = lv
                break
        label = THEME_LEVEL_RULES[level]["label"]

        yest_count = yesterday_themes.get(theme, {}).get("count", 0)
        if theme in new_themes:
            narrative = "Formation"
        elif count > yest_count:
            narrative = "Validation"
        elif count < yest_count:
            narrative = "Violation"
        else:
            narrative = "Validation"

        leveled_themes.append({
            "theme": theme,
            "level": level,
            "label": label,
            "today_count": count,
            "consecutive_days": cons_days,
            "cumulative_stocks": cum_stocks,
            "narrative": narrative,
        })
        store.save_theme_level(theme, level, cons_days,
                               trade_date if cons_days <= 1 else "",
                               trade_date, cum_stocks)

    fading_with_narrative = []
    for theme in fading_themes:
        fading_with_narrative.append({"theme": theme, "narrative": "Reversal"})

    return {
        "today": top_themes,
        "new": sorted(new_themes, key=lambda t: cnt.get(t, 0), reverse=True),
        "persistent": persistent,
        "fading": list(fading_themes),
        "fading_narrative": fading_with_narrative,
        "raw_counts": dict(cnt),
        "total_stocks": len(hot_df),
        "leveled": leveled_themes,
    }


def build_theme_stock_details(hot_df: pd.DataFrame,
                              theme_result: dict,
                              hot_klines: dict[str, pd.DataFrame] = None,
                              hot_quotes: dict[str, dict] = None,
                              zt_pool: dict[str, dict] = None,
                              ) -> dict[str, list[dict]]:
    if hot_df is None or hot_df.empty:
        return {}
    if hot_quotes is None:
        hot_quotes = {}
    if zt_pool is None:
        zt_pool = {}

    theme_stocks: dict[str, list[dict]] = {}
    for _, row in hot_df.iterrows():
        code = str(row.get("代码", ""))
        name = str(row.get("名称", ""))
        reason = str(row.get("题材归因", ""))
        chg = float(row.get("涨幅%", 0) or 0)
        amount = float(row.get("成交额", 0) or 0)
        turnover = float(row.get("换手率%", 0) or 0)
        if chg == 0 and code in hot_quotes:
            q = hot_quotes[code]
            chg = q.get("change_pct", 0) or 0
            amount = q.get("amount_wan", 0) or 0
            turnover = q.get("turnover_pct", 0) or 0

        chg5 = None
        r10 = None
        if hot_klines and code in hot_klines:
            kdf = hot_klines[code]
            if kdf is not None and len(kdf) >= 6:
                c_now = kdf["close"].iloc[-1]
                c_5 = kdf["close"].iloc[-6]
                if c_5 > 0:
                    chg5 = (c_now / c_5 - 1) * 100
            if kdf is not None and len(kdf) >= 11:
                c_now = kdf["close"].iloc[-1]
                c_10 = kdf["close"].iloc[-11]
                if c_10 > 0:
                    r10 = (c_now / c_10 - 1) * 100

        is_limit_up = chg >= 9.5 or (code.startswith("3") and chg >= 19.5) or (code.startswith("68") and chg >= 19.5)

        if is_limit_up:
            label = "涨停"
        elif chg >= 7:
            label = "强势"
        elif turnover >= 15:
            label = "高换手"
        else:
            label = ""

        zt = zt_pool.get(code, {})
        stock_info = {
            "code": code, "name": name, "chg": chg, "chg5": chg5, "r10": r10,
            "amount_wan": amount, "turnover": turnover,
            "reason": reason, "is_limit_up": is_limit_up,
            "label": label, "mcap_yi": 0,
            "consecutive_boards": zt.get("consecutive_boards", 0),
            "zt_time": zt.get("first_time", ""),
        }

        seen_tags: set[str] = set()
        for raw in reason.split("+"):
            tag = normalize_theme(raw)
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            theme_stocks.setdefault(tag, []).append(stock_info)

    known_themes = {t["theme"] for t in (theme_result.get("leveled", []))}
    for theme_name in known_themes:
        for tag, stocks_list in list(theme_stocks.items()):
            if tag == theme_name:
                continue
            match = False
            if theme_name in tag:
                match = True
            elif tag in theme_name and len(tag) >= 4:
                match = True
            if match:
                existing_codes = {s["code"] for s in theme_stocks.get(theme_name, [])}
                for s in stocks_list:
                    if s["code"] not in existing_codes:
                        theme_stocks.setdefault(theme_name, []).append(s)
                        existing_codes.add(s["code"])

    for stocks in theme_stocks.values():
        stocks.sort(key=lambda x: (-x.get("consecutive_boards", 0), x.get("zt_time", "") or "99"))

    return theme_stocks


def _calc_r10(kdf: pd.DataFrame) -> float | None:
    if kdf is None or len(kdf) < 11:
        return None
    c_now = kdf["close"].iloc[-1]
    c_10 = kdf["close"].iloc[-11]
    if c_10 > 0:
        return (c_now / c_10 - 1) * 100
    return None


def _calc_chg5(kdf: pd.DataFrame) -> float | None:
    if kdf is None or len(kdf) < 6:
        return None
    c_now = kdf["close"].iloc[-1]
    c_5 = kdf["close"].iloc[-6]
    if c_5 > 0:
        return (c_now / c_5 - 1) * 100
    return None


def expand_theme_stocks(
    theme_stock_details: dict[str, list[dict]],
    leveled_themes: list[dict],
    extra_quotes: dict[str, dict],
    extra_klines: dict[str, pd.DataFrame],
    theme_freq_5d: dict[str, dict[str, dict]],
    theme_freq_30d: dict[str, dict[str, dict]],
) -> dict[str, list[dict]]:
    label_priority = {"涨停": 0, "中军": 1, "近期活跃": 2, "强势": 3, "高换手": 4, "": 5}

    def _sort_key(s):
        lbl = s.get("label", "")
        pri = 5
        for prefix, p in label_priority.items():
            if lbl.startswith(prefix):
                pri = p
                break
        cb = -(s.get("consecutive_boards", 0))
        zt = s.get("zt_time", "") or "99"
        return (pri, cb, zt)

    for t in leveled_themes:
        theme = t["theme"]
        level = t.get("level", 1)
        if level < THEME_ZHONGJUN_MIN_LEVEL:
            continue

        existing = theme_stock_details.get(theme, [])
        existing_codes = {s["code"] for s in existing}

        freq_5d = theme_freq_5d.get(theme, {})
        freq_30d = theme_freq_30d.get(theme, {})

        # Source B: 近期活跃（level>=3时展开）
        source_b = []
        if level >= THEME_EXPAND_MIN_LEVEL:
            candidates_b = []
            for code, info in freq_5d.items():
                if code in existing_codes or info["freq"] < 2:
                    continue
                candidates_b.append((code, info["freq"]))
            candidates_b.sort(key=lambda x: -x[1])
            for code, freq in candidates_b[:5]:
                q = extra_quotes.get(code, {})
                if not q:
                    continue
                kdf_b = extra_klines.get(code)
                chg5 = _calc_chg5(kdf_b)
                r10 = _calc_r10(kdf_b)
                source_b.append({
                    "code": code,
                    "name": q.get("name", code),
                    "chg": q.get("change_pct", 0),
                    "chg5": chg5,
                    "r10": r10,
                    "amount_wan": q.get("amount_wan", 0),
                    "turnover": q.get("turnover_pct", 0),
                    "reason": f"近5日{freq}次涨停",
                    "is_limit_up": False,
                    "label": f"近期({freq}天)",
                    "mcap_yi": q.get("mcap_yi", 0),
                })

        source_b_codes = {s["code"] for s in source_b}

        # Source C: 中军（level>=2时）
        source_c = []
        candidates_c = []
        for code, info in freq_30d.items():
            if code in existing_codes or code in source_b_codes:
                continue
            if info["freq"] < THEME_ZHONGJUN_MIN_FREQ_30D:
                continue
            q = extra_quotes.get(code, {})
            mcap = q.get("mcap_yi", 0) if q else 0
            if mcap <= 0:
                continue
            candidates_c.append((code, info["freq"], mcap))
        candidates_c.sort(key=lambda x: -(x[1] * x[2]))
        for code, freq, mcap in candidates_c[:3]:
            q = extra_quotes.get(code, {})
            kdf_c = extra_klines.get(code)
            chg5 = _calc_chg5(kdf_c)
            r10 = _calc_r10(kdf_c)
            source_c.append({
                "code": code,
                "name": q.get("name", code),
                "chg": q.get("change_pct", 0),
                "chg5": chg5,
                "r10": r10,
                "amount_wan": q.get("amount_wan", 0),
                "turnover": q.get("turnover_pct", 0),
                "reason": f"30日{freq}次，{mcap:.0f}亿",
                "is_limit_up": False,
                "label": "中军",
                "mcap_yi": mcap,
            })

        merged = existing + source_c + source_b
        merged.sort(key=_sort_key)
        theme_stock_details[theme] = merged[:THEME_EXPAND_MAX_STOCKS]

    return theme_stock_details


def classify_themes_by_trend(theme_result: dict,
                             theme_aesthetics: list[dict] = None,
                             ) -> dict[str, list[dict]]:
    leveled = theme_result.get("leveled", [])
    aesthetics_map = {}
    if theme_aesthetics:
        aesthetics_map = {a["theme"]: a for a in theme_aesthetics}

    groups = {
        "主升浪": [],
        "加速期": [],
        "新兴题材": [],
        "轮动": [],
        "退潮": [],
    }

    new_themes = set(theme_result.get("new", []))

    for t in leveled:
        theme = t["theme"]
        narrative = t.get("narrative", "")
        level = t.get("level", 1)
        cons = t.get("consecutive_days", 0)

        entry = {**t}
        a = aesthetics_map.get(theme, {})
        entry["alpha_label"] = a.get("alpha_label", "")
        entry["surge_score"] = a.get("surge_score", 0)
        entry["surge_max"] = a.get("surge_max", 5)
        entry["driver"] = a.get("driver", "")

        if level >= 3 and cons >= 3 and narrative == "Validation" and t.get("today_count", 0) >= 2:
            groups["主升浪"].append(entry)
        elif narrative == "Validation" and level >= 2:
            groups["加速期"].append(entry)
        elif theme in new_themes or narrative == "Formation":
            groups["新兴题材"].append(entry)
        elif narrative == "Violation":
            groups["退潮"].append(entry)
        else:
            groups["轮动"].append(entry)

    fading = theme_result.get("fading_narrative", [])
    for f in fading:
        groups["退潮"].append({
            "theme": f["theme"], "level": 0, "label": "退潮",
            "today_count": 0, "consecutive_days": 0,
            "cumulative_stocks": 0, "narrative": "Reversal",
            "alpha_label": "", "surge_score": 0, "surge_max": 5, "driver": "",
        })

    for v in groups.values():
        v.sort(key=lambda x: (-x.get("level", 0), -x.get("today_count", 0)))

    return groups


def rate_theme(t: dict) -> tuple[str, int]:
    level = t.get("level", 0)
    narrative = t.get("narrative", "")
    cons = t.get("consecutive_days", 0)
    today_c = t.get("today_count", 0)
    surge = t.get("surge_score", 0)

    score = 0
    if level >= 4:
        score += 3
    elif level >= 3:
        score += 2
    elif level >= 2:
        score += 1

    if narrative == "Validation":
        score += 2
    elif narrative == "Formation":
        score += 1
    elif narrative == "Violation":
        score -= 1

    if cons >= 5:
        score += 2
    elif cons >= 3:
        score += 1

    if today_c >= 5:
        score += 2
    elif today_c >= 3:
        score += 1

    score += min(surge, 2)

    score = max(1, min(score, 10))

    if score >= 8:
        label = "★★★"
    elif score >= 6:
        label = "★★"
    elif score >= 4:
        label = "★"
    else:
        label = "☆"
    return label, score


# ============================================================
# 4.6 三池合并 + 词频共振（涨停 ∪ 人气前100 ∪ 20日涨幅前100）
# ============================================================

SOURCE_ZT = "涨"      # 池① 涨停/强势（getharden）
SOURCE_POP = "人"     # 池② 人气前100
SOURCE_GAIN = "强"    # 池③ 20日涨幅前100（中期强势）
SOURCE_EXPAND = "扩"  # 历史频次扩展（中军/近期活跃）

_BOARD_RE = re.compile(r"^\d+连板$|^\d+天\d+板$|^连续\d+")
_BOARD_WORDS = {"连板", "涨停", "首板", "二连板", "炸板", "T字板",
                "一字板", "几天几板", "连续涨停", "强势股"}


def normalize_theme(tag: str) -> str | None:
    """题材标签归一：剔板数碎片/停用词，别名归一到 canonical；无效返回 None。"""
    if not tag:
        return None
    t = tag.strip()
    if not t or t in _BOARD_WORDS or _BOARD_RE.match(t):
        return None
    if t in THEME_STOPWORDS:
        return None
    t = THEME_ALIAS.get(t, t)
    if t in THEME_STOPWORDS:
        return None
    return t


def build_merged_theme_pool(hot_df, pop_pool, gain_pool, *,
                            max_concepts=MERGE_POOL_MAX_CONCEPTS,
                            min_freq=MERGE_POOL_MIN_FREQ) -> dict:
    """三池去重合并 + 词频共振。
    返回 {meta, themes(freq>=min_freq), theme_freq, longtail}。
    频次 = 该 canonical 题材在合并池内的持票数。
    """
    meta: dict[str, dict] = {}

    def _add(code, name, chg, source, raw_concepts):
        code = str(code or "").strip()
        if not code:
            return
        m = meta.get(code)
        if m is None:
            m = {"code": code, "name": name or "", "chg": chg or 0.0,
                 "sources": [], "concepts": []}
            meta[code] = m
        if source not in m["sources"]:
            m["sources"].append(source)
        if not m["name"] and name:
            m["name"] = name
        if not m["chg"] and chg:
            m["chg"] = chg
        for c in raw_concepts:
            nc = normalize_theme(c)
            if nc and nc not in m["concepts"]:
                m["concepts"].append(nc)

    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            reason = str(row.get("题材归因", ""))
            raw = [x.strip() for x in reason.split("+") if x.strip()]
            _add(row.get("代码", ""), str(row.get("名称", "")),
                 float(row.get("涨幅%", 0) or 0), SOURCE_ZT, raw)

    for s in (pop_pool or []):
        _add(s.get("code"), s.get("name"), s.get("chg", 0),
             SOURCE_POP, s.get("concepts", []))
    for s in (gain_pool or []):
        _add(s.get("code"), s.get("name"), s.get("chg", 0),
             SOURCE_GAIN, s.get("concepts", []))

    # 单票概念封顶（加入顺序：池①题材归因优先，其次人气、涨幅）
    for m in meta.values():
        m["concepts"] = m["concepts"][:max_concepts]

    freq: Counter = Counter()
    theme_codes: dict[str, list[str]] = {}
    for m in meta.values():
        for c in m["concepts"]:
            freq[c] += 1
            theme_codes.setdefault(c, []).append(m["code"])

    themes = {c: [meta[code] for code in codes]
              for c, codes in theme_codes.items() if freq[c] >= min_freq}
    longtail = sorted(((c, freq[c]) for c in theme_codes if freq[c] < min_freq),
                      key=lambda x: -x[1])
    return {"meta": meta, "themes": themes,
            "theme_freq": dict(freq), "longtail": longtail}


def _merged_stock_dict(m: dict) -> dict:
    return {
        "code": m["code"], "name": m["name"],
        "chg": m.get("chg", 0), "chg5": None, "r10": None,
        "amount_wan": 0, "turnover": 0,
        "reason": "/".join(m["concepts"][:3]),
        "is_limit_up": False,
        "label": "人气" if SOURCE_POP in m["sources"] else "中期强势",
        "mcap_yi": 0, "consecutive_boards": 0, "zt_time": "",
        "sources": list(m["sources"]),
    }


def _base_source(s: dict) -> str:
    if s.get("is_limit_up"):
        return SOURCE_ZT
    if str(s.get("label", "")).startswith(("中军", "近期")):
        return SOURCE_EXPAND
    return SOURCE_ZT


def attach_merged_to_themes(theme_stock_details: dict,
                            leveled_themes: list[dict],
                            merged_pool: dict) -> list[dict]:
    """合并池的人气/涨幅票补进已知 leveled 题材明细（原地改），返回未匹配的新方向。
    不写 SQLite 历史；leveled 分级仍只由涨停口径决定。
    """
    for stocks in theme_stock_details.values():
        for s in stocks:
            if not s.get("sources"):
                s["sources"] = [_base_source(s)]

    themes = merged_pool.get("themes", {})
    theme_freq = merged_pool.get("theme_freq", {})
    matched = set()

    for t in leveled_themes:
        name = t["theme"]
        canon = normalize_theme(name) or name
        merged_stocks = themes.get(canon)
        if not merged_stocks:
            continue
        matched.add(canon)
        existing = theme_stock_details.setdefault(name, [])
        by_code = {s["code"]: s for s in existing}
        for m in merged_stocks:
            cur = by_code.get(m["code"])
            if cur is not None:
                for src in m["sources"]:
                    if src not in cur.setdefault("sources", []):
                        cur["sources"].append(src)
            else:
                nd = _merged_stock_dict(m)
                existing.append(nd)
                by_code[m["code"]] = nd
        existing.sort(key=lambda s: (
            0 if s.get("is_limit_up") else 1,
            -(s.get("consecutive_boards", 0) or 0),
            s.get("zt_time", "") or "99",
            -(s.get("chg", 0) or 0),
        ))

    # 新方向 = 纯人气/中期强势：合并题材内无涨停(涨)成分。
    # 含涨停的题材属涨停宇宙，已由分组明细承载，不在此重复。
    new_dirs = []
    for canon, stocks in themes.items():
        if canon in matched:
            continue
        if any(SOURCE_ZT in m["sources"] for m in stocks):
            continue
        new_dirs.append({
            "theme": canon,
            "freq": theme_freq.get(canon, len(stocks)),
            "stocks": sorted((_merged_stock_dict(m) for m in stocks),
                             key=lambda s: -(s.get("chg", 0) or 0)),
        })
    new_dirs.sort(key=lambda x: -x["freq"])
    return new_dirs


# ============================================================
# 5. 北向资金分析
# ============================================================

def analyze_northbound(nb_data: dict) -> dict:
    hgt = nb_data.get("hgt_close", 0)
    sgt = nb_data.get("sgt_close", 0)
    total = hgt + sgt

    if total > 50:
        signal = "大幅流入"
    elif total > 10:
        signal = "小幅流入"
    elif total > -10:
        signal = "基本持平"
    elif total > -50:
        signal = "小幅流出"
    else:
        signal = "大幅流出"

    return {
        "hgt": round(hgt, 2),
        "sgt": round(sgt, 2),
        "total": round(total, 2),
        "signal": signal,
    }


# ============================================================
# 6. 自选股扫描
# ============================================================

def analyze_single_stock(
    code: str,
    quote: dict | None,
    kline_df: pd.DataFrame | None,
    fund_flow: list[dict] | None,
    lockup: list[dict] | None,
) -> dict:
    """分析单只股票，生成信号列表"""
    result = {
        "code": code,
        "name": quote.get("name", code) if quote else code,
        "quote": quote,
        "signals": [],
        "trend_score": 0,    # -100 ~ +100
    }

    if quote is None:
        result["signals"].append(("WARN", "行情数据缺失"))
        return result

    chg = quote.get("change_pct", 0)
    turnover = quote.get("turnover_pct", 0)
    vol_ratio = quote.get("vol_ratio", 0)

    # ---- 涨跌异动 ----
    if chg >= 9.8:
        result["signals"].append(("BULL", "涨停"))
    elif chg >= 5:
        result["signals"].append(("BULL", f"大涨 {chg}%"))
    elif chg <= -9.8:
        result["signals"].append(("BEAR", "跌停"))
    elif chg <= -5:
        result["signals"].append(("BEAR", f"大跌 {chg}%"))

    # ---- 量比异动 ----
    if vol_ratio >= 3:
        result["signals"].append(("ALERT", f"量比 {vol_ratio}（异常放量）"))
    elif vol_ratio >= 2:
        result["signals"].append(("INFO", f"量比 {vol_ratio}（明显放量）"))

    # ---- K线技术分析 ----
    if kline_df is not None and len(kline_df) >= 60:
        last = kline_df.iloc[-1]
        prev = kline_df.iloc[-2]
        score = 0

        # 均线多头排列
        mas = [last.get(f"ma{p}") for p in MA_PERIODS]
        if all(m is not None and not pd.isna(m) for m in mas):
            if mas[0] > mas[1] > mas[2] > mas[3]:
                result["signals"].append(("BULL", "均线多头排列"))
                score += 30
            elif mas[0] < mas[1] < mas[2] < mas[3]:
                result["signals"].append(("BEAR", "均线空头排列"))
                score -= 30

        # 突破/跌破 MA20
        ma20 = last.get("ma20")
        ma20_prev = prev.get("ma20")
        if ma20 and ma20_prev:
            if prev["close"] < ma20_prev and last["close"] > ma20:
                result["signals"].append(("BULL", "突破20日均线"))
                score += 20
            elif prev["close"] > ma20_prev and last["close"] < ma20:
                result["signals"].append(("BEAR", "跌破20日均线"))
                score -= 20

        # MACD 金叉/死叉
        if not pd.isna(last.get("dif")) and not pd.isna(prev.get("dif")):
            if prev["dif"] < prev["dea"] and last["dif"] > last["dea"]:
                result["signals"].append(("BULL", "MACD金叉"))
                score += 15
            elif prev["dif"] > prev["dea"] and last["dif"] < last["dea"]:
                result["signals"].append(("BEAR", "MACD死叉"))
                score -= 15

        # 放量
        vol_r = last.get("vol_ratio_20")
        if vol_r and not pd.isna(vol_r) and vol_r >= VOLUME_BREAKOUT_RATIO:
            if chg > 0:
                result["signals"].append(("BULL", f"放量上涨（{vol_r:.1f}倍于20日均量）"))
                score += 10
            else:
                result["signals"].append(("BEAR", f"放量下跌（{vol_r:.1f}倍于20日均量）"))
                score -= 10

        # RSI
        rsi = last.get("rsi")
        if rsi and not pd.isna(rsi):
            if rsi > RSI_OVERBOUGHT:
                result["signals"].append(("WARN", f"RSI={rsi:.0f}（超买区）"))
                score -= 5
            elif rsi < RSI_OVERSOLD:
                result["signals"].append(("INFO", f"RSI={rsi:.0f}（超卖区）"))
                score += 5

        result["trend_score"] = max(-100, min(100, score))

    # ---- 资金流向 ----
    if fund_flow and len(fund_flow) > 0:
        latest = fund_flow[0]
        main_in = latest.get("main_in", "")
        try:
            main_val = float(main_in)
            if main_val > 5000:
                result["signals"].append(("BULL", f"主力净流入 {main_val/10000:.1f}亿"))
            elif main_val < -5000:
                result["signals"].append(("BEAR", f"主力净流出 {abs(main_val)/10000:.1f}亿"))
        except (ValueError, TypeError):
            pass

    # ---- 解禁预警 ----
    if lockup:
        nearest = lockup[0]
        days_to = _days_between(datetime.now().strftime("%Y-%m-%d"), nearest["date"])
        if days_to is not None and days_to <= 30:
            result["signals"].append(("WARN", f"距解禁 {days_to}天（{nearest['date']}）"))

    return result


def _days_between(d1: str, d2: str) -> int | None:
    try:
        a = datetime.strptime(d1[:10], "%Y-%m-%d")
        b = datetime.strptime(d2[:10], "%Y-%m-%d")
        return (b - a).days
    except Exception:
        return None


# ============================================================
# 7. 自选股 × 热点交叉对标
# ============================================================

def analyze_watchlist_themes(watchlist_results: list[dict],
                             hot_df: pd.DataFrame,
                             theme_result: dict) -> dict:
    """自选股与当日热点题材交叉匹配"""
    result = {"in_hot": [], "theme_coverage": {}}
    if hot_df is None or hot_df.empty:
        return result

    hot_codes = set()
    code_themes: dict[str, list[str]] = {}
    for _, row in hot_df.iterrows():
        code = str(row.get("代码", ""))
        hot_codes.add(code)
        reason = str(row.get("题材归因", ""))
        tags = [t.strip() for t in reason.split("+") if t.strip()]
        code_themes[code] = tags

    watchlist_codes = {s["code"] for s in watchlist_results}
    overlap = watchlist_codes & hot_codes

    for code in overlap:
        name = next((s["name"] for s in watchlist_results if s["code"] == code), code)
        themes = code_themes.get(code, [])
        result["in_hot"].append({
            "code": code, "name": name, "themes": themes,
        })

    top5_themes = [t[0] for t in theme_result.get("today", [])[:5]]
    for theme in top5_themes:
        theme_stocks = set()
        for _, row in hot_df.iterrows():
            reason = str(row.get("题材归因", ""))
            canon_tags = {normalize_theme(x) for x in reason.split("+")}
            if theme in canon_tags:
                theme_stocks.add(str(row.get("代码", "")))
        covered = watchlist_codes & theme_stocks
        result["theme_coverage"][theme] = {
            "total": len(theme_stocks),
            "covered": len(covered),
            "stocks": list(covered),
        }

    return result


# ============================================================
# 8. 外围市场分析
# ============================================================

def analyze_global(global_data: dict) -> dict:
    """分析外围市场对A股的影响"""
    result = {"indices": {}, "watchlist": {}, "signal": ""}
    if not global_data:
        return result

    result["indices"] = global_data.get("indices", {})
    result["watchlist"] = global_data.get("watchlist", {})

    us_chgs = []
    for label in ("道琼斯", "纳斯达克", "标普500"):
        q = result["indices"].get(label, {})
        if q:
            us_chgs.append(q.get("change_pct", 0))

    hk_chgs = []
    for label in ("恒生指数", "恒生科技"):
        q = result["indices"].get(label, {})
        if q:
            hk_chgs.append(q.get("change_pct", 0))

    signals = []
    if us_chgs:
        avg = sum(us_chgs) / len(us_chgs)
        if avg > 1:
            signals.append("美股大涨")
        elif avg < -1:
            signals.append("美股大跌")
    if hk_chgs:
        avg = sum(hk_chgs) / len(hk_chgs)
        if avg > 1:
            signals.append("港股走强")
        elif avg < -1:
            signals.append("港股走弱")

    result["signal"] = "、".join(signals) if signals else "外围平稳"
    return result


# ============================================================
# 9. 基本面快照
# ============================================================

def analyze_fundamentals(codes: list[str], quotes: dict,
                         eps_data: dict, shareholder_data: dict,
                         news_data: dict) -> list[dict]:
    """汇总基本面扫描结果"""
    results = []
    for code in codes:
        q = quotes.get(code, {})
        item = {
            "code": code,
            "name": q.get("name", code),
            "pe_ttm": q.get("pe_ttm", 0),
            "pb": q.get("pb", 0),
            "mcap_yi": q.get("mcap_yi", 0),
        }

        eps = eps_data.get(code, [])
        if eps:
            current_price = q.get("price", 0)
            next_year_eps = eps[0].get("eps")
            if next_year_eps and current_price and float(next_year_eps) > 0:
                item["forward_pe"] = round(current_price / float(next_year_eps), 1)
            item["eps_forecast"] = eps[:3]
            item["inst_count"] = eps[0].get("inst_count")

        sh = shareholder_data.get(code, [])
        if len(sh) >= 2:
            try:
                pct = float(sh[0].get("change_pct", 0) or 0)
                if pct < -5:
                    item["holder_signal"] = f"股东户数减少{abs(pct):.1f}%（筹码集中）"
                elif pct > 10:
                    item["holder_signal"] = f"股东户数增加{pct:.1f}%（筹码分散）"
            except (ValueError, TypeError):
                pass

        news = news_data.get(code, [])
        if news:
            item["recent_news"] = news[:3]

        results.append(item)
    return results


# ============================================================
# 10. 题材审美定性（3级以上题材5维分析）
# ============================================================

def _classify_alpha_bucket(t: dict, drivers: list[str]) -> tuple[int, str]:
    narrative = t.get("narrative", "")
    level = t.get("level", 1)

    if narrative == "Reversal":
        return 2, ALPHA_BUCKET_LABELS[2]
    if narrative == "Violation":
        return 4, ALPHA_BUCKET_LABELS[4]
    if "事件驱动" in drivers:
        return 3, ALPHA_BUCKET_LABELS[3]
    if "政策驱动" in drivers and narrative in ("Formation", "Validation"):
        return 6, ALPHA_BUCKET_LABELS[6]
    if level >= 4 and narrative == "Validation":
        return 1, ALPHA_BUCKET_LABELS[1]
    if "技术/落地驱动" in drivers:
        return 1, ALPHA_BUCKET_LABELS[1]
    if narrative == "Formation":
        return 3, ALPHA_BUCKET_LABELS[3]
    return 1, ALPHA_BUCKET_LABELS[1]


def _score_theme_surge(t: dict, headlines: list[str]) -> tuple[int, list[str]]:
    details = []
    score = 0
    narrative = t.get("narrative", "")
    today_c = t.get("today_count", 0)

    if narrative == "Validation" and today_c > 0:
        details.append("加速动量✓")
        score += 1
    else:
        details.append("加速动量✗")

    has_shock = narrative == "Formation" or any(
        kw in h for h in headlines for kw in SURGE_NEWS_KEYWORDS
    )
    if has_shock:
        details.append("冲击/拐点✓")
        score += 1
    else:
        details.append("冲击/拐点✗")

    details.append("上行空间(需人工)")

    if t.get("consecutive_days", 0) >= 3 and narrative in ("Validation", "Formation"):
        details.append("更容易持有✓")
        score += 1
    else:
        details.append("更容易持有✗")

    if narrative == "Validation" and today_c >= 3:
        details.append("论文扩散✓")
        score += 1
    else:
        details.append("论文扩散✗")

    if narrative == "Formation" or t.get("consecutive_days", 0) <= 2:
        details.append("低迷起点✓")
        score += 1
    else:
        details.append("低迷起点✗")

    return score, details


def analyze_theme_aesthetics(leveled_themes: list[dict],
                             news_map: dict[str, list[str]]) -> list[dict]:
    results = []
    for t in leveled_themes:
        if t["level"] < 3:
            continue
        theme_name = t["theme"]
        analysis = {
            "theme": theme_name,
            "level": t["level"],
            "label": t["label"],
            "consecutive_days": t["consecutive_days"],
        }

        headlines = news_map.get(theme_name, [])

        # A. 驱动类型
        drivers = []
        for dtype, keywords in THEME_DRIVER_KEYWORDS.items():
            if any(kw in h for h in headlines for kw in keywords):
                drivers.append(dtype)
        analysis["driver"] = "、".join(drivers) if drivers else "待确认"

        # B. 落地性
        landing_hits = sum(1 for h in headlines for kw in THEME_LANDING_KEYWORDS if kw in h)
        if landing_hits >= 3:
            analysis["landing"] = "高（多个落地信号）"
        elif landing_hits >= 1:
            analysis["landing"] = "中（有落地线索）"
        else:
            analysis["landing"] = "低（暂无明确时间线）"

        # C. 性价比
        if t["consecutive_days"] > 10:
            analysis["value"] = "偏高位（连续超10天）"
        elif t["consecutive_days"] > 5:
            analysis["value"] = "中等位（连续5-10天）"
        else:
            analysis["value"] = "早期（连续<5天）"

        # D. 主导资金
        analysis["capital"] = "待结合龙虎榜/北向数据判断"

        # E. 板块容量
        analysis["capacity"] = f"累计{t['cumulative_stocks']}只涨停"

        # 置信度
        if headlines:
            analysis["confidence"] = "中" if len(headlines) >= 3 else "低"
        else:
            analysis["confidence"] = "低（无新闻数据）"

        # FE: Alpha Bucket
        bucket_id, bucket_label = _classify_alpha_bucket(t, drivers)
        analysis["alpha_bucket"] = bucket_id
        analysis["alpha_label"] = f"Bucket{bucket_id}「{bucket_label}」"

        # FE: 大涨前提评分
        surge_score, surge_details = _score_theme_surge(t, headlines)
        analysis["surge_score"] = surge_score
        analysis["surge_max"] = 5
        analysis["surge_details"] = surge_details

        results.append(analysis)
    return results


# ============================================================
# 11. FEV三脚凳评分 + 大涨前提 + 大跌警示
# ============================================================

def score_fev(stock: dict, eps_data: dict, shareholder_data: dict,
              hot_codes: set, hot_theme_names: set,
              code_themes: dict, theme_narratives: dict) -> dict:
    code = stock["code"]
    q = stock.get("quote") or {}
    signals = stock.get("signals", [])
    sig_descs = [s[1] for s in signals]

    th = FEV_THRESHOLDS
    f_score, e_score, v_score = 0, 0, 0
    f_reasons, e_reasons, v_reasons = [], [], []

    # --- F: Fundamentals ---
    eps = eps_data.get(code, [])
    cagr = None
    if len(eps) >= 2:
        try:
            e1 = float(eps[0].get("eps", 0))
            e_last = float(eps[-1].get("eps", 0))
            if e1 > 0 and e_last > 0:
                years = len(eps) - 1
                cagr = (e_last / e1) ** (1 / years) - 1 if years > 0 else 0
                if cagr > th["f_cagr_min"]:
                    f_score += 3
                    f_reasons.append(f"EPS增速{cagr:.0%}")
        except (ValueError, TypeError):
            pass

    pe_ttm = q.get("pe_ttm", 0)
    if 0 < pe_ttm < th["f_pe_max"]:
        f_score += 2
        f_reasons.append(f"PE_TTM={pe_ttm:.1f}")

    sh = shareholder_data.get(code, [])
    holder_chg = None
    if len(sh) >= 2:
        try:
            holder_chg = float(sh[0].get("change_pct", 0) or 0)
            if holder_chg < th["f_holder_pct"]:
                f_score += 2
                f_reasons.append(f"股东集中{holder_chg:.1f}%")
        except (ValueError, TypeError):
            pass

    if any("均线多头" in d for d in sig_descs):
        f_score += 3
        f_reasons.append("均线多头")

    # --- E: Expectations ---
    if code in hot_codes:
        e_score += 3
        themes = code_themes.get(code, [])
        if themes:
            e_reasons.append(f"题材:{','.join(themes[:2])}")
        else:
            e_reasons.append("在涨停/强势股中")

    stock_themes = code_themes.get(code, [])
    for st in stock_themes:
        n = theme_narratives.get(st, "")
        if n in ("Formation", "Validation"):
            e_score += 2
            e_reasons.append(f"叙事{n}")
            break

    vol_ratio = q.get("vol_ratio", 0)
    if vol_ratio >= th["e_vol_ratio"]:
        e_score += 2
        e_reasons.append(f"量比{vol_ratio:.1f}")

    inst_count = 0
    if eps:
        inst_count = eps[0].get("inst_count") or 0
        try:
            inst_count = int(inst_count)
        except (ValueError, TypeError):
            inst_count = 0
    if inst_count >= th["e_inst_min"]:
        e_score += 3
        e_reasons.append(f"{inst_count}家机构")

    # --- V: Valuation ---
    current_price = q.get("price", 0)
    forward_pe = None
    if eps and current_price:
        try:
            last_eps = float(eps[-1].get("eps", 0))
            if last_eps > 0:
                forward_pe = current_price / last_eps
                if forward_pe < th["v_forward_pe_max"]:
                    v_score += 3
                    v_reasons.append(f"前瞻PE={forward_pe:.1f}")
                if pe_ttm > 0 and forward_pe < pe_ttm:
                    v_score += 2
                    v_reasons.append("盈利改善")
        except (ValueError, TypeError):
            pass

    pb = q.get("pb", 0)
    if 0 < pb < th["v_pb_max"]:
        v_score += 2
        v_reasons.append(f"PB={pb:.1f}")

    rsi = None
    for sig_type, desc in signals:
        if "RSI=" in desc:
            import re
            m = re.search(r"RSI=(\d+)", desc)
            if m:
                rsi = float(m.group(1))
    if rsi is None or rsi < th["v_rsi_safe"]:
        v_score += 3
        if rsi:
            v_reasons.append(f"RSI={rsi:.0f}")

    total = f_score + e_score + v_score

    # Alpha Bucket for individual stock
    alpha_bucket = None
    if cagr and cagr > 0.2 and forward_pe and forward_pe < 30:
        alpha_bucket = "Bucket1「成长被低估」"
    elif code in hot_codes and any(
        theme_narratives.get(t) == "Formation" for t in stock_themes
    ):
        alpha_bucket = "Bucket3「催化剂定价错误」"
    elif holder_chg and holder_chg < -10:
        alpha_bucket = "Bucket5「复杂性消散」"

    return {
        "code": code,
        "name": stock.get("name", code),
        "fev_total": total,
        "f_score": f_score, "e_score": e_score, "v_score": v_score,
        "f_reasons": f_reasons, "e_reasons": e_reasons, "v_reasons": v_reasons,
        "forward_pe": round(forward_pe, 1) if forward_pe else None,
        "cagr": cagr,
        "inst_count": inst_count,
        "holder_chg": holder_chg,
        "alpha_bucket": alpha_bucket,
    }


def check_surge_preconditions(stock: dict, hot_codes: set,
                              hot_theme_names: set, code_themes: dict) -> tuple[int, list[str]]:
    q = stock.get("quote") or {}
    signals = stock.get("signals", [])
    sig_descs = [s[1] for s in signals]
    code = stock["code"]
    score = 0
    details = []

    chg = q.get("change_pct", 0)
    if chg > 5:
        details.append("加速动量✓")
        score += 1
    else:
        details.append("加速动量✗")

    has_breakout = any("突破20日" in d or "MACD金叉" in d for d in sig_descs)
    if has_breakout:
        details.append("冲击/拐点✓")
        score += 1
    else:
        details.append("冲击/拐点✗")

    if any("均线多头" in d for d in sig_descs):
        rsi_ok = not any("超买" in d for d in sig_descs)
        if rsi_ok:
            details.append("更容易持有✓")
            score += 1
        else:
            details.append("更容易持有✗")
    else:
        details.append("更容易持有✗")

    in_hot = code in hot_codes
    on_theme = bool(set(code_themes.get(code, [])) & hot_theme_names)
    if in_hot and on_theme:
        details.append("论文扩散✓")
        score += 1
    else:
        details.append("论文扩散✗")

    has_oversold = any("超卖" in d for d in sig_descs)
    if has_oversold:
        details.append("低迷起点✓")
        score += 1
    else:
        details.append("低迷起点✗")

    return score, details


def check_crash_warnings(stock: dict, shareholder_data: dict) -> list[str]:
    q = stock.get("quote") or {}
    signals = stock.get("signals", [])
    sig_descs = [s[1] for s in signals]
    code = stock["code"]
    warnings = []

    pe_ttm = q.get("pe_ttm", 0)
    has_high_rsi = any("超买" in d for d in sig_descs)
    if has_high_rsi and pe_ttm > 80:
        warnings.append(f"Peak on Peak风险（PE={pe_ttm:.0f}，RSI超买）")

    bear_signals = sum(1 for t, d in signals if t == "BEAR")
    has_break_ma = any("跌破20日" in d for d in sig_descs)
    has_death_cross = any("MACD死叉" in d for d in sig_descs)
    has_vol_down = any("放量下跌" in d for d in sig_descs)
    if has_break_ma and has_death_cross:
        warnings.append("趋势破裂（跌破20日线+MACD死叉）")

    sh = shareholder_data.get(code, [])
    if len(sh) >= 2:
        try:
            pct = float(sh[0].get("change_pct", 0) or 0)
            if pct > 10:
                warnings.append(f"筹码松动（股东户数+{pct:.1f}%）")
        except (ValueError, TypeError):
            pass

    return warnings


# ============================================================
# 12. 综合建议生成
# ============================================================

def generate_suggestions(
    market: dict,
    style: dict,
    sectors: dict,
    themes: dict,
    northbound: dict,
    watchlist_results: list[dict],
    *,
    fev_scores: list[dict] = None,
    crash_warnings: dict = None,
) -> dict:
    focus = []
    risk = []
    operation = []

    sentiment = market.get("sentiment", "")
    breadth = sectors.get("breadth", {})
    breadth_pct = breadth.get("pct", 50)
    nb_signal = northbound.get("signal", "")

    # ---- 市场层面 ----
    if sentiment == "偏多" and breadth_pct > 60:
        operation.append("市场偏多，个股活跃度高，可适当加仓趋势股")
    elif sentiment == "偏空" and breadth_pct < 40:
        operation.append("市场偏空，普跌格局，控制仓位，观望为主")
        risk.append(f"涨跌比仅 {breadth_pct}%，系统性风险偏高")
    elif sentiment == "震荡分化":
        operation.append("市场分化，轻指数重个股，聚焦强势板块")

    if "流出" in nb_signal:
        risk.append(f"北向资金{nb_signal}（{northbound['total']}亿），注意外资动向")
    elif "流入" in nb_signal and northbound["total"] > 30:
        focus.append(f"北向资金{nb_signal}（+{northbound['total']}亿），外资加仓信号")

    # ---- 题材层面 ----
    new_themes = themes.get("new", [])
    if new_themes:
        top_new = new_themes[:3]
        focus.append(f"今日新兴题材：{'、'.join(top_new)}，可关注首日上板标的")

    persistent = themes.get("persistent", [])
    accel = [t for t in persistent if t["trend"] == "↑" and t["today_count"] >= 3]
    if accel:
        names = [t["theme"] for t in accel[:3]]
        focus.append(f"加速发酵题材：{'、'.join(names)}，趋势跟踪优先")

    fading = themes.get("fading", [])
    if fading:
        risk.append(f"退潮题材：{'、'.join(fading[:5])}，注意及时止盈")

    # ---- 个股层面（FEV驱动）----
    if fev_scores:
        highlight = FEV_THRESHOLDS["highlight_total"]
        top_fev = [s for s in fev_scores if s["fev_total"] >= highlight]
        top_fev.sort(key=lambda x: x["fev_total"], reverse=True)

        r1 = [s for s in top_fev if s["f_score"] >= 7]
        r2 = [s for s in top_fev if s["e_score"] >= 7 and s not in r1]
        r3 = [s for s in top_fev if s["v_score"] >= 7 and s not in r1 and s not in r2]

        if r1:
            names = "、".join(f"{s['name']}(FEV={s['fev_total']})" for s in r1[:5])
            focus.append(f"[R1复利持有] {names}（基本面强劲）")
        if r2:
            names = "、".join(f"{s['name']}(FEV={s['fev_total']})" for s in r2[:5])
            focus.append(f"[R2修正驱动] {names}（预期差打开）")
        if r3:
            names = "、".join(f"{s['name']}(FEV={s['fev_total']})" for s in r3[:5])
            focus.append(f"[R3重估驱动] {names}（估值有吸引力）")

        low_fev = [s for s in fev_scores if s["fev_total"] <= 8]
        if low_fev:
            names = "、".join(s["name"] for s in sorted(low_fev, key=lambda x: x["fev_total"])[:5])
            risk.append(f"FEV低分（≤8）：{names}，基本面/预期/估值均弱")
    else:
        bullish = [
            s for s in watchlist_results
            if s["trend_score"] >= 30 and any(sig[0] == "BULL" for sig in s["signals"])
        ]
        bullish.sort(key=lambda x: x["trend_score"], reverse=True)
        for stock in bullish[:5]:
            focus.append(f"{stock['name']}(+{stock['trend_score']})")

    # 大跌警示
    if crash_warnings:
        for code, warns in crash_warnings.items():
            if warns:
                name = next((s["name"] for s in fev_scores if s["code"] == code), code) if fev_scores else code
                for w in warns:
                    risk.append(f"{name}：{w}")

    # 风险：趋势分最低的（仅无FEV时）
    if not fev_scores:
        bearish = [s for s in watchlist_results if s["trend_score"] <= -20]
        bearish.sort(key=lambda x: x["trend_score"])
        for stock in bearish[:5]:
            descs = "、".join(s[1] for s in stock["signals"] if s[0] in ("BEAR",))[:40]
            risk.append(f"{stock['name']}（{stock['trend_score']}分）：{descs}")

    # 解禁预警
    for stock in watchlist_results:
        for sig_type, desc in stock["signals"]:
            if sig_type == "WARN" and "解禁" in desc:
                risk.append(f"{stock['name']}：{desc}")

    # RSI 超买汇总
    overbought = [
        s["name"] for s in watchlist_results
        if any(sig[0] == "WARN" and "超买" in sig[1] and _extract_rsi(sig[1]) >= 85
               for sig in s["signals"])
    ]
    if overbought:
        risk.append(f"RSI极度超买（≥85）：{'、'.join(overbought)}，短线注意回调风险")

    mild_ob = [
        s["name"] for s in watchlist_results
        if any(sig[0] == "WARN" and "超买" in sig[1] and 70 <= _extract_rsi(sig[1]) < 85
               for sig in s["signals"])
    ]
    if mild_ob:
        risk.append(f"RSI偏高（70-85）共 {len(mild_ob)} 只：{'、'.join(mild_ob[:8])}{'等' if len(mild_ob) > 8 else ''}")

    # ---- 仓位建议 ----
    profit_eff = market.get("profit_effect", "")
    position = ""
    if sentiment == "偏多" and profit_eff in ("强", "中等"):
        pos = POSITION_THRESHOLDS["aggressive"]
        position = f"建议仓位 {pos['min']}-{pos['max']}%（市场偏多+赚钱效应{profit_eff}）"
    elif sentiment == "偏空" or profit_eff == "冰点":
        pos = POSITION_THRESHOLDS["defensive"]
        position = f"建议仓位 {pos['min']}-{pos['max']}%（市场偏空/赚钱效应差）"
    else:
        pos = POSITION_THRESHOLDS["moderate"]
        position = f"建议仓位 {pos['min']}-{pos['max']}%（震荡市）"
    operation.insert(0, position)

    return {
        "focus": focus,
        "risk": risk,
        "operation": operation,
    }


def _extract_rsi(desc: str) -> float:
    """从 'RSI=80（超买区）' 中提取数值"""
    import re
    m = re.search(r"RSI=(\d+)", desc)
    return float(m.group(1)) if m else 0


# ============================================================
# 13. 聚焦池 + 综合评分
# ============================================================

def build_focus_pool(
    ths_hot: list[dict],
    zt_pool: dict[str, dict],
    watchlist_codes: list[str],
) -> dict[str, dict]:
    pool = {}
    for s in ths_hot:
        code = s["code"]
        pool[code] = {"code": code, "name": s["name"], "source": ["hot"],
                      "hot_rank": s["rank"], "hot_rate": s["hot_rate"],
                      "rank_chg": s["rank_chg"],
                      "concept_tags": s.get("concept_tags", []),
                      "pop_tag": s.get("pop_tag", "")}

    for code, z in zt_pool.items():
        if code in pool:
            pool[code]["source"].append("zt")
        else:
            pool[code] = {"code": code, "name": z.get("name", ""),
                          "source": ["zt"], "hot_rank": 0, "hot_rate": 0,
                          "rank_chg": 0, "concept_tags": [], "pop_tag": ""}
        pool[code]["zt_time"] = z.get("first_time", "")
        pool[code]["zt_boards"] = z.get("consecutive_boards", 0)

    for code in watchlist_codes:
        if code in pool:
            pool[code]["source"].append("watch")
        else:
            pool[code] = {"code": code, "name": "", "source": ["watch"],
                          "hot_rank": 0, "hot_rate": 0, "rank_chg": 0,
                          "concept_tags": [], "pop_tag": ""}
    return pool


def compute_composite_score(
    stock: dict,
    fev_total: int = 0,
    theme_level: int = 0,
    theme_trend: str = "",
    lhb_info: dict = None,
    research: list[dict] = None,
    zsxq_mentions: int = 0,
    crash_warnings: list[str] = None,
    limit_up_label: str | None = None,
) -> dict:
    scores = {}

    # 板块共振 (0-20)
    if theme_level >= 3 and theme_trend in ("验证", "形成"):
        scores["sector"] = 20
    elif theme_level >= 3:
        scores["sector"] = 12
    elif theme_level == 2:
        scores["sector"] = 6
    else:
        scores["sector"] = 0
    if theme_trend == "动摇":
        scores["sector"] = max(scores["sector"] - 10, 0)

    # FEV (0-25)
    scores["fev"] = min(round(fev_total / 30 * 25), 25)

    # 人气 (0-10)
    rank = stock.get("hot_rank", 0)
    if 1 <= rank <= 10:
        scores["hot"] = 10
    elif rank <= 30:
        scores["hot"] = 7
    elif rank <= 50:
        scores["hot"] = 5
    elif rank <= 100:
        scores["hot"] = 3
    else:
        scores["hot"] = 0

    # 涨停动量 (0-10)
    boards = stock.get("zt_boards", 0)
    if boards >= 4:
        scores["momentum"] = 10
    elif boards == 3:
        scores["momentum"] = 8
    elif boards == 2:
        scores["momentum"] = 6
    elif boards == 1 or stock.get("zt_time"):
        scores["momentum"] = 4
    else:
        scores["momentum"] = 0

    # 催化剂 (0-15)
    cat = 0
    if lhb_info:
        if "机构" in (lhb_info.get("comment") or ""):
            cat += 5
        elif lhb_info.get("net_buy", 0) > 0:
            cat += 3
    if research:
        buy_count = sum(1 for r in research if r.get("rating") in ("买入", "增持"))
        if buy_count >= 2:
            cat += 5
        elif buy_count >= 1:
            cat += 3
    if zsxq_mentions >= 2:
        cat += 3
    elif zsxq_mentions >= 1:
        cat += 2
    # B2: 逻辑涨停标签加分
    if limit_up_label == "纯逻辑":
        cat += 5
    elif limit_up_label == "偏逻辑":
        cat += 3
    scores["catalyst"] = min(cat, 15)

    # 技术面 (0-10)
    tech = 5
    signals = stock.get("signals", [])
    sig_descs = [s[1] if isinstance(s, (list, tuple)) else str(s) for s in signals]
    if any("多头排列" in d for d in sig_descs):
        tech += 3
    if any("MACD金叉" in d for d in sig_descs):
        tech += 2
    if any("超买" in d for d in sig_descs):
        tech -= 3
    if any("跌破20日" in d for d in sig_descs):
        tech -= 4
    if any("MACD死叉" in d for d in sig_descs):
        tech -= 3
    scores["tech"] = max(min(tech, 10), 0)

    # 风险扣分 (0-10, 越高越好=无风险)
    risk_penalty = 0
    warnings = crash_warnings or []
    if any("Peak on Peak" in w for w in warnings):
        risk_penalty += 4
    if any("趋势破裂" in w for w in warnings):
        risk_penalty += 4
    if any("筹码松动" in w for w in warnings):
        risk_penalty += 3
    if "ST" in stock.get("name", ""):
        risk_penalty += 5
    scores["risk"] = max(10 - risk_penalty, 0)

    total = sum(scores.values())

    if total >= 60:
        advice = "买入"
    elif total >= 50:
        advice = "加仓"
    elif total >= 35:
        advice = "持有"
    elif total >= 20:
        advice = "减仓"
    else:
        advice = "回避"

    return {
        "total": total,
        "scores": scores,
        "advice": advice,
    }
