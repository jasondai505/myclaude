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


# ============================================================
# compute_composite_score — 编排 + 6 helper
# ============================================================

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
    bom_moat: dict | None = None,
) -> dict:
    scores = {}

    _score_sector(scores, theme_level, theme_trend)
    _score_fev_component(scores, fev_total)
    _score_hot(scores, stock)
    _score_momentum(scores, stock)
    _score_catalyst(scores, stock, lhb_info, research, zsxq_mentions, limit_up_label)
    _score_bom_moat(scores, stock["code"], bom_moat)
    _score_chain_anchor(scores, stock["code"])
    _score_shendu_tier(scores, stock["code"])
    _score_tech_risk(scores, stock, crash_warnings)

    return _compute_advice(scores)


# ============================================================
# Helper: 行业/题材得分
# ============================================================

def _score_sector(scores: dict, theme_level: int, theme_trend: str):
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


# ============================================================
# Helper: FEV 得分
# ============================================================

def _score_fev_component(scores: dict, fev_total: int):
    scores["fev"] = min(round(fev_total / 30 * 25), 25)


# ============================================================
# Helper: 人气排名
# ============================================================

def _score_hot(scores: dict, stock: dict):
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


# ============================================================
# Helper: 动量（连板）
# ============================================================

def _score_momentum(scores: dict, stock: dict):
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


# ============================================================
# Helper: 催化剂（龙虎榜/研报/星球/涨停标签）
# ============================================================

def _score_catalyst(scores: dict, stock: dict, lhb_info: dict | None,
                     research: list[dict] | None, zsxq_mentions: int,
                     limit_up_label: str | None):
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


# ============================================================
# Helper: 技术面 & 风险惩罚
# ============================================================

def _score_tech_risk(scores: dict, stock: dict, crash_warnings: list[str] | None):
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


# ============================================================
# Helper: BOM 护城河加权
# ============================================================

def _score_bom_moat(scores: dict, code: str, bom_moat: dict | None):
    """BOM 产业链龙头护城河加权：三高赛道龙头额外加分。"""
    if not bom_moat or code not in bom_moat:
        scores["bom"] = 0
        return

    m = bom_moat[code]
    moat = m.get("moat_score", 0)
    rank = m.get("rank", 99)

    if rank == 1 and moat >= 8:
        bonus = 10
    elif rank <= 3 and moat >= 6:
        bonus = 7
    elif rank <= 5 and moat >= 4:
        bonus = 4
    elif moat >= 2:
        bonus = 2
    else:
        bonus = 0

    scores["bom"] = bonus


def _score_chain_anchor(scores: dict, code: str):
    """chain_map DB 产业链锚定加权：有链位置 +3~5 分。"""
    try:
        from theme_stock.store import ThemeStockStore
        store = ThemeStockStore()
        store.init_db()
        row = store._get_conn().execute(
            "SELECT COUNT(*) as cnt FROM chain_map WHERE code=? AND map_type='chain'",
            (code,)
        ).fetchone()
        if row and row["cnt"] >= 3:
            scores["chain"] = 5
        elif row and row["cnt"] >= 1:
            scores["chain"] = 3
        else:
            scores["chain"] = 0
    except Exception:
        scores["chain"] = 0


def _score_shendu_tier(scores: dict, code: str):
    """Shendu 估值分层加权：核心仓 +5，弹性层 +2，规避 -3。"""
    try:
        import json
        from pathlib import Path
        shendu_dir = Path(__file__).parent / "reports" / "serenity" / "shendu"
        if not shendu_dir.exists():
            scores["shendu"] = 0
            return
        for fp in shendu_dir.iterdir():
            if not fp.name.startswith("shendu_2026") or fp.name.startswith("shendu__"):
                continue
            data = json.loads(fp.read_text(encoding="utf-8"))
            for v in data.get("valuation_spectrum", []):
                if str(code).zfill(6) in [str(c).zfill(6) for c in v.get("codes", [])]:
                    tier = v.get("tier", "")
                    if tier == "核心仓":
                        scores["shendu"] = 5
                    elif tier == "弹性层":
                        scores["shendu"] = 2
                    elif tier == "规避":
                        scores["shendu"] = -3
                    else:
                        scores["shendu"] = 1
                    return
        scores["shendu"] = 0
    except Exception:
        scores["shendu"] = 0


# ============================================================
# Helper: 综合建议
# ============================================================

def _compute_advice(scores: dict) -> dict:
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

    return {"total": total, "scores": scores, "advice": advice}
