"""个股分析 — 自选股扫描/基本面/FEV三脚凳"""
from datetime import datetime

import pandas as pd

from config import (
    MA_PERIODS, VOLUME_BREAKOUT_RATIO, RSI_OVERBOUGHT, RSI_OVERSOLD,
    FEV_THRESHOLDS,
)
from engine_themes import normalize_theme
from utils import safe_str, safe_float, extract_rsi


# ============================================================
# analyze_single_stock — 编排 + 5 helper
# ============================================================

def analyze_single_stock(
    code: str,
    quote: dict | None,
    kline_df: pd.DataFrame | None,
    fund_flow: list[dict] | None,
    lockup: list[dict] | None,
) -> dict:
    result = {
        "code": code,
        "name": quote.get("name", code) if quote else code,
        "quote": quote,
        "signals": [],
        "trend_score": 0,
    }

    if quote is None:
        result["signals"].append(("WARN", "行情数据缺失"))
        return result

    _check_price_signals(result, quote)
    _check_volume_signals(result, quote)
    _check_technical_indicators(result, quote, kline_df)
    _check_fund_flow(result, fund_flow)
    _check_lockup(result, lockup)
    return result


def _check_price_signals(result: dict, quote: dict):
    chg = quote.get("change_pct", 0)
    if chg >= 9.8:
        result["signals"].append(("BULL", "涨停"))
    elif chg >= 5:
        result["signals"].append(("BULL", f"大涨 {chg}%"))
    elif chg <= -9.8:
        result["signals"].append(("BEAR", "跌停"))
    elif chg <= -5:
        result["signals"].append(("BEAR", f"大跌 {chg}%"))


def _check_volume_signals(result: dict, quote: dict):
    vol_ratio = quote.get("vol_ratio", 0)
    if vol_ratio >= 3:
        result["signals"].append(("ALERT", f"量比 {vol_ratio}（异常放量）"))
    elif vol_ratio >= 2:
        result["signals"].append(("INFO", f"量比 {vol_ratio}（明显放量）"))


def _check_technical_indicators(result: dict, quote: dict, kline_df: pd.DataFrame | None):
    if kline_df is None or len(kline_df) < 60:
        return
    last = kline_df.iloc[-1]
    prev = kline_df.iloc[-2]
    chg = quote.get("change_pct", 0)
    score = 0

    mas = [last.get(f"ma{p}") for p in MA_PERIODS]
    if all(m is not None and not pd.isna(m) for m in mas):
        if mas[0] > mas[1] > mas[2] > mas[3]:
            result["signals"].append(("BULL", "均线多头排列"))
            score += 30
        elif mas[0] < mas[1] < mas[2] < mas[3]:
            result["signals"].append(("BEAR", "均线空头排列"))
            score -= 30

    ma20 = last.get("ma20")
    ma20_prev = prev.get("ma20")
    if ma20 and ma20_prev:
        if prev["close"] < ma20_prev and last["close"] > ma20:
            result["signals"].append(("BULL", "突破20日均线"))
            score += 20
        elif prev["close"] > ma20_prev and last["close"] < ma20:
            result["signals"].append(("BEAR", "跌破20日均线"))
            score -= 20

    if not pd.isna(last.get("dif")) and not pd.isna(prev.get("dif")):
        if prev["dif"] < prev["dea"] and last["dif"] > last["dea"]:
            result["signals"].append(("BULL", "MACD金叉"))
            score += 15
        elif prev["dif"] > prev["dea"] and last["dif"] < last["dea"]:
            result["signals"].append(("BEAR", "MACD死叉"))
            score -= 15

    vol_r = last.get("vol_ratio_20")
    if vol_r and not pd.isna(vol_r) and vol_r >= VOLUME_BREAKOUT_RATIO:
        if chg > 0:
            result["signals"].append(("BULL", f"放量上涨（{vol_r:.1f}倍于20日均量）"))
            score += 10
        else:
            result["signals"].append(("BEAR", f"放量下跌（{vol_r:.1f}倍于20日均量）"))
            score -= 10

    rsi = last.get("rsi")
    if rsi and not pd.isna(rsi):
        if rsi > RSI_OVERBOUGHT:
            result["signals"].append(("WARN", f"RSI={rsi:.0f}（超买区）"))
            score -= 5
        elif rsi < RSI_OVERSOLD:
            result["signals"].append(("INFO", f"RSI={rsi:.0f}（超卖区）"))
            score += 5

    result["trend_score"] = max(-100, min(100, score))


def _check_fund_flow(result: dict, fund_flow: list[dict] | None):
    if not fund_flow:
        return
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


def _check_lockup(result: dict, lockup: list[dict] | None):
    if not lockup:
        return
    nearest = lockup[0]
    days_to = _days_between(datetime.now().strftime("%Y-%m-%d"), nearest["date"])
    if days_to is not None and days_to <= 30:
        result["signals"].append(("WARN", f"距解禁 {days_to}天（{nearest['date']}）"))


def _days_between(d1: str, d2: str) -> int | None:
    try:
        a = datetime.strptime(d1[:10], "%Y-%m-%d")
        b = datetime.strptime(d2[:10], "%Y-%m-%d")
        return (b - a).days
    except Exception:
        return None


# ============================================================
# analyze_watchlist_themes
# ============================================================

def analyze_watchlist_themes(watchlist_results: list[dict],
                             hot_df: pd.DataFrame,
                             theme_result: dict) -> dict:
    result = {"in_hot": [], "theme_coverage": {}}
    if hot_df is None or hot_df.empty:
        return result

    hot_codes = set()
    code_themes: dict[str, list[str]] = {}
    for _, row in hot_df.iterrows():
        code = safe_str(row, "代码")
        hot_codes.add(code)
        reason = safe_str(row, "题材归因")
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
            reason = safe_str(row, "题材归因")
            canon_tags = {normalize_theme(x) for x in reason.split("+")}
            if theme in canon_tags:
                theme_stocks.add(safe_str(row, "代码"))
        covered = watchlist_codes & theme_stocks
        result["theme_coverage"][theme] = {
            "total": len(theme_stocks),
            "covered": len(covered),
            "stocks": list(covered),
        }

    return result


# ============================================================
# analyze_fundamentals
# ============================================================

def analyze_fundamentals(codes: list[str], quotes: dict,
                         eps_data: dict, shareholder_data: dict,
                         news_data: dict) -> list[dict]:
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
                pct = safe_float(sh[0], "change_pct")
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
# score_fev — 编排 + 4 helper
# ============================================================

def score_fev(stock: dict, eps_data: dict, shareholder_data: dict,
              hot_codes: set, hot_theme_names: set,
              code_themes: dict, theme_narratives: dict) -> dict:
    code = stock["code"]
    q = stock.get("quote") or {}
    signals = stock.get("signals", [])
    sig_descs = [s[1] for s in signals]
    th = FEV_THRESHOLDS

    f_score, f_reasons, cagr, holder_chg = _score_fev_fundamentals(
        stock, eps_data, shareholder_data, sig_descs, th)
    e_score, e_reasons, inst_count = _score_fev_expectations(
        stock, hot_codes, code_themes, theme_narratives, th, q, eps_data)
    v_score, v_reasons, forward_pe = _score_fev_valuation(
        stock, eps_data, signals, q, th)

    return _assemble_fev_result(
        stock, code, f_score, e_score, v_score,
        f_reasons, e_reasons, v_reasons,
        forward_pe, cagr, inst_count, holder_chg,
        hot_codes, code_themes, theme_narratives,
    )


def _score_fev_fundamentals(stock: dict, eps_data: dict, shareholder_data: dict,
                             sig_descs: list[str], th: dict) -> tuple[int, list[str], float | None, float | None]:
    code = stock["code"]
    q = stock.get("quote") or {}
    score = 0
    reasons = []

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
                    score += 3
                    reasons.append(f"EPS增速{cagr:.0%}")
        except (ValueError, TypeError):
            pass

    pe_ttm = q.get("pe_ttm", 0)
    if 0 < pe_ttm < th["f_pe_max"]:
        score += 2
        reasons.append(f"PE_TTM={pe_ttm:.1f}")

    sh = shareholder_data.get(code, [])
    holder_chg = None
    if len(sh) >= 2:
        try:
            holder_chg = safe_float(sh[0], "change_pct")
            if holder_chg < th["f_holder_pct"]:
                score += 2
                reasons.append(f"股东集中{holder_chg:.1f}%")
        except (ValueError, TypeError):
            pass

    if any("均线多头" in d for d in sig_descs):
        score += 3
        reasons.append("均线多头")

    return score, reasons, cagr, holder_chg


def _score_fev_expectations(stock: dict, hot_codes: set, code_themes: dict,
                             theme_narratives: dict, th: dict, q: dict,
                             eps_data: dict) -> tuple[int, list[str], int]:
    code = stock["code"]
    score = 0
    reasons = []

    if code in hot_codes:
        score += 3
        themes = code_themes.get(code, [])
        if themes:
            reasons.append(f"题材:{','.join(themes[:2])}")
        else:
            reasons.append("在涨停/强势股中")

    stock_themes = code_themes.get(code, [])
    for st in stock_themes:
        n = theme_narratives.get(st, "")
        if n in ("Formation", "Validation"):
            score += 2
            reasons.append(f"叙事{n}")
            break

    vol_ratio = q.get("vol_ratio", 0)
    if vol_ratio >= th["e_vol_ratio"]:
        score += 2
        reasons.append(f"量比{vol_ratio:.1f}")

    inst_count = 0
    eps = eps_data.get(code, [])
    if eps:
        inst_count = eps[0].get("inst_count") or 0
        try:
            inst_count = int(inst_count)
        except (ValueError, TypeError):
            inst_count = 0
    if inst_count >= th["e_inst_min"]:
        score += 3
        reasons.append(f"{inst_count}家机构")

    return score, reasons, inst_count


def _score_fev_valuation(stock: dict, eps_data: dict, signals: list,
                          q: dict, th: dict) -> tuple[int, list[str], float | None]:
    code = stock["code"]
    pe_ttm = q.get("pe_ttm", 0)
    current_price = q.get("price", 0)
    score = 0
    reasons = []

    eps = eps_data.get(code, [])
    forward_pe = None
    if eps and current_price:
        try:
            last_eps = float(eps[-1].get("eps", 0))
            if last_eps > 0:
                forward_pe = current_price / last_eps
                if forward_pe < th["v_forward_pe_max"]:
                    score += 3
                    reasons.append(f"前瞻PE={forward_pe:.1f}")
                if pe_ttm > 0 and forward_pe < pe_ttm:
                    score += 2
                    reasons.append("盈利改善")
        except (ValueError, TypeError):
            pass

    pb = q.get("pb", 0)
    if 0 < pb < th["v_pb_max"]:
        score += 2
        reasons.append(f"PB={pb:.1f}")

    rsi = None
    for sig_type, desc in signals:
        val = extract_rsi(desc)
        if val:
            rsi = val
            break
    if rsi is None or rsi < th["v_rsi_safe"]:
        score += 3
        if rsi:
            reasons.append(f"RSI={rsi:.0f}")

    return score, reasons, forward_pe


def _assemble_fev_result(stock: dict, code: str, f_score: int, e_score: int,
                          v_score: int, f_reasons: list[str], e_reasons: list[str],
                          v_reasons: list[str], forward_pe: float | None,
                          cagr: float | None, inst_count: int, holder_chg: float | None,
                          hot_codes: set, code_themes: dict,
                          theme_narratives: dict) -> dict:
    total = f_score + e_score + v_score

    stock_themes = code_themes.get(code, [])
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


# ============================================================
# check_surge_preconditions / check_crash_warnings
# ============================================================

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

    has_break_ma = any("跌破20日" in d for d in sig_descs)
    has_death_cross = any("MACD死叉" in d for d in sig_descs)
    if has_break_ma and has_death_cross:
        warnings.append("趋势破裂（跌破20日线+MACD死叉）")

    sh = shareholder_data.get(code, [])
    if len(sh) >= 2:
        try:
            pct = safe_float(sh[0], "change_pct")
            if pct > 10:
                warnings.append(f"筹码松动（股东户数+{pct:.1f}%）")
        except (ValueError, TypeError):
            pass

    return warnings
