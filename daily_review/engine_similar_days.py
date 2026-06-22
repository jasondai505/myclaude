"""历史相似日匹配 — 基于市场结构特征的余弦相似度，找最像的历史交易日"""

from __future__ import annotations

import json
import math
from collections import defaultdict

from store import _conn

FEATURE_DIMS = [
    "index_score",
    "volume",
    "breadth",
    "limit_up_leaders",
    "limit_down",
    "prev_limit_pnl",
]


# 仅保留有区分度的客观数值维度（人工打分维度方差太小，排除）
RAW_DIMS = [
    "prev_limit_pnl_score",  # 昨日涨停盈亏，范围 ~ -5 到 +4
    "limit_down_score",      # 跌停数，范围 ~ -30 到 0
    "limit_up_leaders_score",# 连板数，范围 ~ 0 到 20+
    "breadth_today",         # 涨跌家数，范围 ~ 500 到 4000
    "index_chg",             # 指数涨跌幅
    "volume_chg",            # 量能变化%
    "max_board",             # 最高连板数
    "volume_amount_log",     # 量能 log10
]


def _build_all_features() -> dict[str, dict]:
    """预计算所有交易日特征向量，同时返回全局 min/max 用于归一化"""
    with _conn() as conn:
        dates = conn.execute(
            "SELECT DISTINCT date FROM sector_rotation_log ORDER BY date"
        ).fetchall()

    all_features = {}
    for r in dates:
        f = _extract_features_raw(r["date"])
        if f:
            all_features[r["date"]] = f

    # 计算每个维度的 min/max
    bounds = {}
    for dim in RAW_DIMS:
        vals = [feat.get(dim) for feat in all_features.values()
                if feat.get(dim) is not None]
        if vals:
            bounds[dim] = (min(vals), max(vals))

    return all_features, bounds


def _extract_features_raw(date: str) -> dict:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT row_type, score, raw_data FROM sector_rotation_log
            WHERE date = ? AND row_type IN (
                'index_score','volume','breadth','limit_up_leaders','limit_down','prev_limit_pnl'
            )
        """, (date,)).fetchall()

    features = {}
    for r in rows:
        rt = r["row_type"]
        if r["score"] is not None:
            features[f"{rt}_score"] = r["score"]

        raw = json.loads(r["raw_data"]) if r["raw_data"] and r["raw_data"] != "{}" else {}
        if rt == "breadth" and "today" in raw:
            features["breadth_today"] = raw["today"]
        if rt == "volume":
            if "amount_yi" in raw:
                features["volume_amount"] = raw["amount_yi"]
            if "chg_pct" in raw:
                features["volume_chg"] = raw.get("chg_pct", 0)
        if rt == "index_score" and "chg_pct" in raw:
            features["index_chg"] = raw["chg_pct"]
        if rt == "limit_up_leaders" and "max_board" in raw:
            features["max_board"] = raw["max_board"]

    if features.get("volume_amount", 0) > 0:
        features["volume_amount_log"] = math.log10(features["volume_amount"])

    return features


def _extract_features(date: str) -> dict:
    """返回归一化后的特征向量（0-1 范围）"""
    raw = _extract_features_raw(date)
    if not raw:
        return {}

    all_features, bounds = _build_all_features()
    return _normalize(raw, bounds)


def _normalize(features: dict, bounds: dict) -> dict:
    norm = {}
    for dim in RAW_DIMS:
        v = features.get(dim)
        if v is None or dim not in bounds:
            continue
        lo, hi = bounds[dim]
        if hi - lo == 0:
            norm[dim] = 0.5
        else:
            norm[dim] = (v - lo) / (hi - lo)
    return norm


def _euclidean_similarity(vec_a: dict, vec_b: dict) -> float:
    """归一化向量间的欧氏距离转为相似度 (0-1, 1=完全相同)"""
    keys = sorted(set(vec_a.keys()) & set(vec_b.keys()))
    if len(keys) < 4:
        return 0.0
    sq_sum = sum((vec_a[k] - vec_b[k]) ** 2 for k in keys)
    max_dist = math.sqrt(len(keys))  # each dim in [0,1]
    dist = math.sqrt(sq_sum)
    return 1.0 - (dist / max_dist)


_CACHE = {"all_features": None, "bounds": None}


def _get_cache():
    if _CACHE["all_features"] is None:
        _CACHE["all_features"], _CACHE["bounds"] = _build_all_features()
    return _CACHE["all_features"], _CACHE["bounds"]


def find_similar(target_date: str, top_n: int = 10, exclude_days: int = 10) -> list[dict]:
    """找与 target_date 市场结构最相似的 top_n 个历史交易日"""
    all_features, bounds = _get_cache()

    raw_target = _extract_features_raw(target_date)
    if not raw_target:
        return []
    target_norm = _normalize(raw_target, bounds)

    from datetime import datetime as _dt
    target_dt = _dt.strptime(target_date, "%Y-%m-%d")

    scored = []
    for d, raw in all_features.items():
        if d == target_date:
            continue
        d_dt = _dt.strptime(d, "%Y-%m-%d")
        if abs((d_dt - target_dt).days) <= exclude_days:
            continue
        other_norm = _normalize(raw, bounds)
        sim = _euclidean_similarity(target_norm, other_norm)
        if sim > 0:
            scored.append({"date": d, "similarity": round(sim, 4)})

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_n]


def _sectors_on_date(date: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT sector FROM sector_rotation_log
            WHERE date = ? AND sector != ''
              AND row_type IN ('index_score','volume','breadth','limit_up_leaders',
                               'limit_down','prev_limit_pnl','institutional','retail')
        """, (date,)).fetchall()
    return [r["sector"] for r in rows]


def _sectors_after(date: str, lookahead: int = 5) -> list[str]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT sector FROM sector_rotation_log
            WHERE sector != ''
              AND date > ?
              AND date <= date(?, '+' || ? || ' days')
              AND row_type IN ('index_score','volume','breadth','limit_up_leaders',
                               'limit_down','prev_limit_pnl','institutional','retail')
        """, (date, date, lookahead)).fetchall()
    return [r["sector"] for r in rows]


def similar_sectors(target_date: str, top_n: int = 5, lookahead: int = 5) -> dict:
    """主入口：找到相似日，汇总它们之后 lookahead 日内出现的板块"""
    similar_days = find_similar(target_date, top_n=top_n)

    sector_freq = defaultdict(float)
    sector_by_date = defaultdict(list)

    for sd in similar_days:
        d = sd["date"]
        sectors = _sectors_after(d, lookahead)
        weight = sd["similarity"]
        for s in sectors:
            sector_freq[s] += weight
        sector_by_date[d] = {
            "similarity": sd["similarity"],
            "today_sectors": _sectors_on_date(d),
            "next_sectors": sectors,
        }

    ranked = sorted(sector_freq.items(), key=lambda x: x[1], reverse=True)

    return {
        "target_date": target_date,
        "target_sectors": _sectors_on_date(target_date),
        "similar_days": similar_days,
        "predicted_sectors": [{"sector": s, "score": round(v, 2)} for s, v in ranked[:15]],
        "details": sector_by_date,
    }


def similar_days_report(target_date: str = None) -> str:
    """生成历史相似日分析报告"""
    from datetime import date as _date

    if target_date is None:
        target_date = _date.today().strftime("%Y-%m-%d")

    result = similar_sectors(target_date, top_n=5)

    if not result["similar_days"]:
        return f"## 历史相似日\n\n{target_date} 无可用数据"

    lines = [
        "## 历史相似日",
        "",
        f"### {target_date} 市场结构特征",
        f"今日板块: {' / '.join(result['target_sectors'][:8]) or '(无)'}",
        "",
        "### 最相似的 5 个交易日",
        "",
        "| 日期 | 相似度 | 当日的板块 | 之后 5 日出现的板块 |",
        "|------|--------|-----------|-------------------|",
    ]

    for sd in result["similar_days"]:
        detail = result["details"].get(sd["date"], {})
        today_s = " / ".join(detail.get("today_sectors", [])[:4]) or "-"
        next_s = " / ".join(detail.get("next_sectors", [])[:4]) or "-"
        lines.append(
            f"| {sd['date']} | {sd['similarity']:.2%} | {today_s} | {next_s} |")

    lines.append("")
    lines.append("### 加权预测板块（相似度加权 x 出现频率）")
    lines.append("")
    lines.append("| 板块 | 加权得分 |")
    lines.append("|------|---------|")
    for s in result["predicted_sectors"][:12]:
        lines.append(f"| {s['sector']} | {s['score']} |")

    current = set(result["target_sectors"])
    new_sectors = [s for s in result["predicted_sectors"] if s["sector"] not in current]
    if new_sectors:
        lines.append("")
        lines.append("### 新方向提示（预测中但今日尚未出现）")
        for s in new_sectors[:5]:
            lines.append(f"- {s['sector']} (得分: {s['score']})")

    return "\n".join(lines)
