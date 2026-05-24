"""聚焦池 — 三源合并 + 综合评分"""
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

    scores["fev"] = min(round(fev_total / 30 * 25), 25)

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
    if limit_up_label == "纯逻辑":
        cat += 5
    elif limit_up_label == "偏逻辑":
        cat += 3
    scores["catalyst"] = min(cat, 15)

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
