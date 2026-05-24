"""A1 验证：成交额对比 + 10 日涨跌停趋势渲染"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import store
import engine
import report


def test_store_history():
    store.init_db()
    rows = store.get_market_snapshot_history("2026-05-20", 10)
    print(f"[STORE] history rows = {len(rows)}, dates = {[r['date'] for r in rows]}")
    assert isinstance(rows, list)
    if rows:
        cols = list(rows[0].keys())
        for c in ("limit_up_count", "limit_up_2plus", "limit_down_count", "total_amount_yi"):
            assert c in cols, f"missing column: {c}"
    print("[PASS] store.get_market_snapshot_history + 新列存在")


def test_analyze_market_filters_st():
    hot_df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "题材归因": "白酒+3连板"},
        {"代码": "000001", "名称": "ST平安", "题材归因": "金融+2连板"},
        {"代码": "300001", "名称": "正常股", "题材归因": "AI"},
    ])
    zt_pool = {
        "600519": {"name": "贵州茅台", "consecutive_boards": 3},
        "000001": {"name": "ST平安", "consecutive_boards": 2},
        "300001": {"name": "正常股", "consecutive_boards": 1},
    }
    dt_pool = {
        "002001": {"name": "正常跌停", "close": 5, "chg_pct": -10},
        "002002": {"name": "*ST垃圾", "close": 1, "chg_pct": -5},
    }
    indices = {
        "上证指数": {"price": 4200, "change_pct": -0.2, "amount_wan": 5000000},
        "深证成指": {"price": 13000, "change_pct": -0.3, "amount_wan": 6000000},
    }
    industry = {"total_up": 1500, "total_down": 3500, "all": []}
    r = engine.analyze_market(indices, industry_data=industry, hot_df=hot_df,
                              zt_pool=zt_pool, dt_pool=dt_pool, trade_date="2026-05-20")
    print(f"limit_up_filtered={r['limit_up_filtered']} limit_up_2plus={r['limit_up_2plus']} limit_down_count={r['limit_down_count']}")
    print(f"prev_total_amount_yi={r.get('prev_total_amount_yi')}")
    print(f"history_10d len={len(r.get('history_10d', []))}")
    assert r["limit_up_filtered"] == 2, f"涨停应 2 只（排 ST），实际 {r['limit_up_filtered']}"
    assert r["limit_up_2plus"] == 1, f"连板≥2 应 1 只（排 ST），实际 {r['limit_up_2plus']}"
    assert r["limit_down_count"] == 1, f"跌停应 1 只（排 ST），实际 {r['limit_down_count']}"
    print("[PASS] analyze_market ST 过滤计数正确")


def test_render_history_table():
    market = {
        "sentiment": "震荡",
        "indices": {"上证指数": {"price": 4200, "change_pct": -0.2, "amount_wan": 5000000, "amplitude_pct": 1}},
        "total_amount_yi": 1100,
        "liquidity": "缩量",
        "amount_vs_ma5": -5,
        "prev_total_amount_yi": 1300,
        "profit_effect": "偏弱",
        "limit_up_count": 30,
        "history_10d": [
            {"date": "2026-05-13", "total_amount_yi": 1200, "limit_up_count": 50, "limit_up_2plus": 10, "limit_down_count": 3},
            {"date": "2026-05-14", "total_amount_yi": 1150, "limit_up_count": 40, "limit_up_2plus": 8, "limit_down_count": 5},
            {"date": "2026-05-15", "total_amount_yi": None, "limit_up_count": None, "limit_up_2plus": None, "limit_down_count": None},
            {"date": "2026-05-20", "total_amount_yi": 1100, "limit_up_count": 30, "limit_up_2plus": 6, "limit_down_count": 8},
        ],
    }
    md = report.render_report(
        trade_date="2026-05-20",
        market=market, style={}, sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 1500, "down": 3500}},
        themes={"total_stocks": 30, "themes": [], "data_sources": {}},
        northbound={}, watchlist_results=[], suggestions={},
    )
    assert "昨日1300亿" in md, "应渲染昨日成交额"
    assert "10 日涨跌停趋势" in md, "应渲染 10 日趋势表头"
    assert "05-13" in md and "05-20" in md, "应渲染日期列"
    assert "| 涨停 |" in md, "应有涨停行"
    assert "| 连板≥2 |" in md, "应有连板≥2 行"
    assert "| 跌停 |" in md, "应有跌停行"
    assert "—" in md, "缺失日应渲染破折号"
    print("[PASS] §1 渲染包含成交额对比 + 10 日趋势表")


if __name__ == "__main__":
    test_store_history()
    test_analyze_market_filters_st()
    test_render_history_table()
    print("\n=== All A1 checks passed ===")
