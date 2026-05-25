"""行业估值分位计算。

用全 A 股 PE/PB 按申万（CSRC）行业分组，计算个股权在行业内的分位数。
结果缓存到 SQLite valuation_cache，每次调用 build() 全量刷新。
"""
from __future__ import annotations

import statistics

import data
import store


def _compute_percentile(values: list[float], target: float) -> float:
    if not values or target <= 0:
        return 0.0
    below = sum(1 for v in values if v > 0 and v < target)
    total = sum(1 for v in values if v > 0)
    if total == 0:
        return 0.0
    return round(below / total * 100, 1)


def build(max_stocks: int = 0) -> dict:
    """构建全市场行业 PE/PB 分位数映射。max_stocks=0 表示全量。"""
    store.init_feeds_tables()
    all_stocks = data.fetch_stock_list_sina()
    if not all_stocks:
        return {}

    if max_stocks and max_stocks < len(all_stocks):
        sh = [s for s in all_stocks if s["code"].startswith("6")]
        sz = [s for s in all_stocks if not s["code"].startswith("6")]
        stocks: list[dict] = []
        i, j = 0, 0
        while len(stocks) < max_stocks and (i < len(sz) or j < len(sh)):
            if i < len(sz):
                stocks.append(sz[i]); i += 1
            if j < len(sh) and len(stocks) < max_stocks:
                stocks.append(sh[j]); j += 1
    else:
        stocks = all_stocks

    codes = [s["code"] for s in stocks]
    print(f"  全 A 股 {len(stocks)} 只，开始批量拉取 PE/PB...")

    quotes = data.fetch_bulk_pe_pb(codes)
    pe_map = {c: q["pe_ttm"] for c, q in quotes.items() if q.get("pe_ttm", 0) > 0}
    pb_map = {c: q["pb"] for c, q in quotes.items() if q.get("pb", 0) > 0}
    print(f"  获取 PE {len(pe_map)} / PB {len(pb_map)} 只")

    industry_codes: dict[str, list[str]] = {}
    code_info: dict[str, dict] = {}
    for s in stocks:
        ind = s.get("industry", "其他") or "其他"
        industry_codes.setdefault(ind, []).append(s["code"])
        code_info[s["code"]] = s

    rows: list[dict] = []
    for ind, ind_codes in industry_codes.items():
        pe_vals = [pe_map[c] for c in ind_codes if c in pe_map]
        pb_vals = [pb_map[c] for c in ind_codes if c in pb_map]
        pe_med = statistics.median(pe_vals) if pe_vals else 0
        pb_med = statistics.median(pb_vals) if pb_vals else 0

        for c in ind_codes:
            pe = pe_map.get(c, 0)
            pb = pb_map.get(c, 0)
            info = code_info.get(c, {})
            rows.append({
                "code": c, "name": info.get("name", ""),
                "industry": ind, "pe_ttm": pe, "pb": pb,
                "pe_pct": _compute_percentile(pe_vals, pe),
                "pb_pct": _compute_percentile(pb_vals, pb),
                "pe_median": pe_med, "pb_median": pb_med,
                "stock_count": len(ind_codes),
            })

    store.save_valuation_batch(rows)
    print(f"  行业 {len(industry_codes)} 个，估值分位已缓存")
    return {r["code"]: r for r in rows}


def get_industry_rank(code: str) -> dict | None:
    return store.query_valuation_cache(code)
