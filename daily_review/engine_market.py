"""市场分析引擎 — 大盘/风格/行业/北向/外围"""
import pandas as pd

import store
from utils import is_st, safe_str


def analyze_market(indices: dict, industry_data: dict = None,
                    hot_df: pd.DataFrame = None,
                    zt_pool: dict = None, dt_pool: dict = None,
                    trade_date: str = None,
                    sentiment_result: dict = None) -> dict:
    result = {}

    _market_index_sentiment(result, indices)
    _market_amount_liquidity(result, indices, trade_date)
    _market_breadth_limits(result, industry_data, hot_df, zt_pool, dt_pool)
    _market_history_10d(result, trade_date, zt_pool, sentiment_result)
    _market_profit_effect(result, industry_data)

    return result


# ============================================================
# Helper: 指数情绪
# ============================================================

def _market_index_sentiment(result: dict, indices: dict):
    idx_map = {
        "上证指数": "sh", "深证成指": "sz", "创业板指": "cyb",
        "科创50": "kc50", "沪深300": "hs300", "中证500": "zz500",
    }
    result["indices"] = {}
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


# ============================================================
# Helper: 成交额与流动性
# ============================================================

def _market_amount_liquidity(result: dict, indices: dict, trade_date: str | None):
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


# ============================================================
# Helper: 涨跌家数 & 涨跌停统计
# ============================================================

def _market_breadth_limits(result: dict, industry_data: dict | None,
                            hot_df: pd.DataFrame | None,
                            zt_pool: dict | None, dt_pool: dict | None):
    breadth = industry_data or {}
    total_up = breadth.get("total_up", 0)
    total_down = breadth.get("total_down", 0)
    result["up_count"] = total_up
    result["down_count"] = total_down

    limit_up_count = len(hot_df) if hot_df is not None and not hot_df.empty else 0
    result["limit_up_count"] = limit_up_count

    limit_up_filtered = 0
    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            if not is_st(safe_str(row, "名称")):
                limit_up_filtered += 1
    result["limit_up_filtered"] = limit_up_filtered

    limit_up_2plus = 0
    if zt_pool:
        for code, info in zt_pool.items():
            if info.get("consecutive_boards", 1) >= 2 and not is_st(info.get("name", "")):
                limit_up_2plus += 1
    elif hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            if is_st(safe_str(row, "名称")):
                continue
            if "连板" in safe_str(row, "题材归因"):
                limit_up_2plus += 1
    result["limit_up_2plus"] = limit_up_2plus

    limit_down_count = 0
    if dt_pool:
        for code, info in dt_pool.items():
            if not is_st(info.get("name", "")):
                limit_down_count += 1
    result["limit_down_count"] = limit_down_count


# ============================================================
# Helper: 10日历史
# ============================================================

def _market_history_10d(result: dict, trade_date: str | None,
                        zt_pool: dict = None, sentiment_result: dict = None):
    if not trade_date:
        return

    max_board_count = None
    max_board_stock = ""
    if zt_pool:
        best = None
        for code, info in zt_pool.items():
            cb = info.get("consecutive_boards", 0) or 0
            if best is None or cb > best[0]:
                best = (cb, info.get("name", ""))
        if best and best[0] >= 2:
            max_board_count = best[0]
            max_board_stock = best[1]

    history = store.get_market_snapshot_history(trade_date, 10)
    today_row = {
        "date": trade_date,
        "total_amount_yi": result.get("total_amount_yi"),
        "limit_up_count": result.get("limit_up_filtered", 0),
        "limit_up_2plus": result.get("limit_up_2plus", 0),
        "limit_down_count": result.get("limit_down_count", 0),
        "max_board_count": max_board_count,
        "max_board_stock": max_board_stock,
    }
    if history and history[-1]["date"] == trade_date:
        history[-1].update(today_row)
    else:
        history.append(today_row)
    result["history_10d"] = history
    result["prev_total_amount_yi"] = (
        history[-2].get("total_amount_yi") if len(history) >= 2 else None
    )


# ============================================================
# Helper: 赚钱效应
# ============================================================

def _market_profit_effect(result: dict, industry_data: dict | None):
    breadth = industry_data or {}
    total_up = breadth.get("total_up", 0)
    total_down = breadth.get("total_down", 0)
    total_stocks = total_up + total_down
    limit_up_count = result.get("limit_up_count", 0)

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


# ============================================================
# analyze_style / analyze_sectors / analyze_northbound / analyze_global
# ============================================================

def analyze_style(indices: dict) -> dict:
    large = indices.get("大盘价值(上证50)", {})
    small = indices.get("小盘(中证1000)", {})
    growth = indices.get("成长(创业板指)", {})
    lc = large.get("change_pct", 0)
    sc = small.get("change_pct", 0)
    gc = growth.get("change_pct", 0)
    size = "小盘占优" if sc - lc > 0.5 else ("大盘占优" if lc - sc > 0.5 else "大小盘均衡")
    gv = "成长占优" if gc - lc > 0.5 else ("价值占优" if lc - gc > 0.5 else "成长价值均衡")
    return {"size": size, "growth_value": gv,
            "detail": {"大盘价值": lc, "成长": gc, "小盘": sc}}


def analyze_sectors(industry_data: dict) -> dict:
    all_sectors = industry_data.get("all", [])
    if not all_sectors:
        return {"top": [], "bottom": [], "breadth": {}}
    total_up = industry_data.get("total_up", 0)
    total_down = industry_data.get("total_down", 0)
    total = total_up + total_down
    pct = round(total_up / total * 100, 1) if total > 0 else 50
    return {
        "top": all_sectors[:5], "bottom": all_sectors[-5:],
        "breadth": {"up": total_up, "down": total_down, "pct": pct},
    }


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
    return {"hgt": round(hgt, 2), "sgt": round(sgt, 2),
            "total": round(total, 2), "signal": signal}


def analyze_global(global_data: dict) -> dict:
    result = {"indices": {}, "watchlist": {}, "signal": ""}
    if not global_data:
        return result
    result["indices"] = global_data.get("indices", {})
    result["watchlist"] = global_data.get("watchlist", {})

    us_chgs = [result["indices"].get(l, {}).get("change_pct", 0)
               for l in ("道琼斯", "纳斯达克", "标普500")]
    us_chgs = [c for c in us_chgs if c]
    hk_chgs = [result["indices"].get(l, {}).get("change_pct", 0)
               for l in ("恒生指数", "恒生科技")]
    hk_chgs = [c for c in hk_chgs if c]

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
