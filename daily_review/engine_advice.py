"""交易建议引擎 — 基于市场/题材/FEV/风控生成操作建议"""
from config import POSITION_THRESHOLDS, FEV_THRESHOLDS
from utils import extract_rsi


def generate_suggestions(
    market: dict,
    sectors: dict,
    themes: dict,
    northbound: dict,
    watchlist_results: list[dict],
    *,
    fev_scores: list[dict] = None,
    crash_warnings: dict = None,
) -> dict:
    sentiment = market.get("sentiment", "")
    breadth_pct = sectors.get("breadth", {}).get("pct", 50)
    nb_signal = northbound.get("signal", "")

    ops = _build_operations(sentiment, breadth_pct, market)
    focus = _build_focus_list(themes, northbound, nb_signal, watchlist_results, fev_scores)
    risks = _collect_risks(sentiment, breadth_pct, nb_signal, northbound, themes,
                           fev_scores, crash_warnings, watchlist_results)

    return {"operation": ops, "focus": focus, "risk": risks}


# ============================================================
# Helper: 操作建议
# ============================================================

def _build_operations(sentiment: str, breadth_pct: float,
                       market: dict) -> list[str]:
    ops = []
    profit_eff = market.get("profit_effect", "")

    if sentiment == "偏多" and breadth_pct > 60:
        ops.append("市场偏多，个股活跃度高，可适当加仓趋势股")
    elif sentiment == "偏空" and breadth_pct < 40:
        ops.append("市场偏空，普跌格局，控制仓位，观望为主")
    elif sentiment == "震荡分化":
        ops.append("市场分化，轻指数重个股，聚焦强势板块")

    if sentiment == "偏多" and profit_eff in ("强", "中等"):
        pos = POSITION_THRESHOLDS["aggressive"]
        position = f"建议仓位 {pos['min']}-{pos['max']}%（市场偏多+赚钱效应{profit_eff}）"
    elif sentiment == "偏空" or profit_eff == "冰点":
        pos = POSITION_THRESHOLDS["defensive"]
        position = f"建议仓位 {pos['min']}-{pos['max']}%（市场偏空/赚钱效应差）"
    else:
        pos = POSITION_THRESHOLDS["moderate"]
        position = f"建议仓位 {pos['min']}-{pos['max']}%（震荡市）"
    ops.insert(0, position)

    return ops


# ============================================================
# Helper: 关注列表
# ============================================================

def _build_focus_list(themes: dict, northbound: dict, nb_signal: str,
                       watchlist_results: list[dict],
                       fev_scores: list[dict] | None) -> list[str]:
    focus = []

    if "流入" in nb_signal and northbound["total"] > 30:
        focus.append(f"北向资金{nb_signal}（+{northbound['total']}亿），外资加仓信号")

    new_themes = themes.get("new", [])
    if new_themes:
        focus.append(f"今日新兴题材：{'、'.join(new_themes[:3])}，可关注首日上板标的")

    persistent = themes.get("persistent", [])
    accel = [t for t in persistent if t["trend"] == "↑" and t["today_count"] >= 3]
    if accel:
        names = [t["theme"] for t in accel[:3]]
        focus.append(f"加速发酵题材：{'、'.join(names)}，趋势跟踪优先")

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
    else:
        bullish = [
            s for s in watchlist_results
            if s["trend_score"] >= 30 and any(sig[0] == "BULL" for sig in s["signals"])
        ]
        bullish.sort(key=lambda x: x["trend_score"], reverse=True)
        for stock in bullish[:5]:
            focus.append(f"{stock['name']}(+{stock['trend_score']})")

    return focus


# ============================================================
# Helper: 风险收集
# ============================================================

def _collect_risks(sentiment: str, breadth_pct: float, nb_signal: str,
                    northbound: dict, themes: dict,
                    fev_scores: list[dict] | None,
                    crash_warnings: dict | None,
                    watchlist_results: list[dict]) -> list[str]:
    risk = []

    if sentiment == "偏空" and breadth_pct < 40:
        risk.append(f"涨跌比仅 {breadth_pct}%，系统性风险偏高")

    if "流出" in nb_signal:
        risk.append(f"北向资金{nb_signal}（{northbound['total']}亿），注意外资动向")

    fading = themes.get("fading", [])
    if fading:
        risk.append(f"退潮题材：{'、'.join(fading[:5])}，注意及时止盈")

    if fev_scores:
        low_fev = [s for s in fev_scores if s["fev_total"] <= 8]
        if low_fev:
            names = "、".join(s["name"] for s in sorted(low_fev, key=lambda x: x["fev_total"])[:5])
            risk.append(f"FEV低分（≤8）：{names}，基本面/预期/估值均弱")
    else:
        bearish = [s for s in watchlist_results if s["trend_score"] <= -20]
        bearish.sort(key=lambda x: x["trend_score"])
        for stock in bearish[:5]:
            descs = "、".join(s[1] for s in stock["signals"] if s[0] in ("BEAR",))[:40]
            risk.append(f"{stock['name']}（{stock['trend_score']}分）：{descs}")

    if crash_warnings:
        for code, warns in crash_warnings.items():
            if warns:
                name = next((s["name"] for s in fev_scores if s["code"] == code), code) if fev_scores else code
                for w in warns:
                    risk.append(f"{name}：{w}")

    for stock in watchlist_results:
        for sig_type, desc in stock["signals"]:
            if sig_type == "WARN" and "解禁" in desc:
                risk.append(f"{stock['name']}：{desc}")

    overbought = [
        s["name"] for s in watchlist_results
        if any(sig[0] == "WARN" and "超买" in sig[1] and extract_rsi(sig[1]) >= 85
               for sig in s["signals"])
    ]
    if overbought:
        risk.append(f"RSI极度超买（≥85）：{'、'.join(overbought)}，短线注意回调风险")

    mild_ob = [
        s["name"] for s in watchlist_results
        if any(sig[0] == "WARN" and "超买" in sig[1] and 70 <= extract_rsi(sig[1]) < 85
               for sig in s["signals"])
    ]
    if mild_ob:
        risk.append(f"RSI偏高（70-85）共 {len(mild_ob)} 只：{'、'.join(mild_ob[:8])}{'等' if len(mild_ob) > 8 else ''}")

    return risk
