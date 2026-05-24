"""A2 验证：题材热度 + 板块强弱表 F/E/V 列 + 板块成交求和"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import strength
import report


def test_attach_amount_aggregates():
    theme_pool = {
        "AI算力": {"600519": {}, "300001": {}, "002001": {}},
        "小盘股": {"888888": {}},  # 假设体量极小
    }
    quotes_map = {
        "600519": {"amount_wan": 50000, "change_pct": 9.8},
        "300001": {"amount_wan": 30000, "change_pct": 5.0},
        "002001": {"amount_wan": 20000, "change_pct": -1.2},
        "888888": {"amount_wan": 3000, "change_pct": 2.0},  # 仅 0.3亿
    }
    zt_pool = {"600519": {"name": "贵州茅台"}}
    hot100 = {"600519", "300001"}
    strength_result = {
        "strong_themes": [{"theme": "AI算力", "stage": "主升浪", "catalyst_type": "逻辑催化", "avg_5d": 5}],
        "emerging_themes": [{"theme": "小盘股", "stage": "爆发初期", "catalyst_type": "情绪催化", "avg_5d": 8,
                              "consecutive_days": 2, "today_count": 5}],
        "fading_themes": [],
        "rising_commonalities": {"count": 0},
    }
    strength.attach_theme_amount_aggregates(strength_result, theme_pool, quotes_map, zt_pool, hot100)
    ai = strength_result["strong_themes"][0]
    sp = strength_result["emerging_themes"][0]
    print(f"AI: zt={ai['amount_zt_wan']} nonzt={ai['amount_nonzt_wan']} top100={ai['amount_top100_wan']} total={ai['amount_total_wan']}")
    print(f"小盘: total={sp['amount_total_wan']} small_cap={sp['small_cap_flag']}")
    assert ai["amount_zt_wan"] == 50000, f"涨停应 5万w(=5亿)，实际 {ai['amount_zt_wan']}"
    assert ai["amount_nonzt_wan"] == 50000, "非涨停应 5万w (=300+200)"
    assert ai["amount_top100_wan"] == 80000, "人气100 应 8万w (600519+300001)"
    assert ai["amount_total_wan"] == 100000
    assert ai["small_cap_flag"] is False
    assert sp["small_cap_flag"] is True
    print("[PASS] attach_theme_amount_aggregates 正确")


def test_attach_fev_aggregates():
    theme_pool = {
        "AI算力": {"600519": {}, "300001": {}, "002001": {}},
    }
    fev_per_code = {
        "600519": {"f_score": 9, "e_score": 8, "v_score": 7},
        "300001": {"f_score": 7, "e_score": 6, "v_score": 5},
        # 002001 无 FEV
    }
    sr = {
        "strong_themes": [{"theme": "AI算力"}],
        "emerging_themes": [], "fading_themes": [],
    }
    strength.attach_theme_fev_aggregates(sr, theme_pool, fev_per_code)
    t = sr["strong_themes"][0]
    print(f"f_avg={t['f_avg']} e_avg={t['e_avg']} v_avg={t['v_avg']} n={t['fev_n']}")
    assert t["f_avg"] == 8.0, f"f_avg 应 8.0，实际 {t['f_avg']}"
    assert t["e_avg"] == 7.0
    assert t["v_avg"] == 6.0
    assert t["fev_n"] == 2
    print("[PASS] attach_theme_fev_aggregates 正确")


def test_render_strength_split_fev():
    strength_data = {
        "strong_themes": [{
            "theme": "AI算力", "stage": "主升浪", "catalyst_type": "逻辑催化",
            "avg_5d": 5.0,
            "amount_zt_wan": 50000, "amount_nonzt_wan": 30000,
            "amount_top100_wan": 60000, "amount_total_wan": 80000,
            "small_cap_flag": False,
            "f_avg": 8.5, "e_avg": 7.2, "v_avg": 6.5, "fev_n": 3,
            "roles": {
                "龙头": [{"code": "600519", "name": "贵州茅台", "mcap_yi": 2000,
                          "chg": 9.8, "zt_time": "09:30:00", "consecutive_boards": 2,
                          "r10": 12.3, "r5": 5.1, "role_reason": "启动早"}],
                "中军": [], "量化标的": [],
            },
        }],
        "emerging_themes": [], "fading_themes": [], "rising_commonalities": {"count": 0},
    }
    focus_pool_data = [{
        "code": "600519", "name": "贵州茅台",
        "source": ["zt", "hot"], "hot_rank": 5,
        "fev_total": 24, "fev": {"f_score": 9, "e_score": 8, "v_score": 7, "fev_total": 24},
        "composite": {"total": 60, "advice": "加仓", "scores": {}},
        "change_pct": 9.8, "amount_wan": 50000,
    }]
    md = report.render_report(
        trade_date="2026-05-20",
        market={"sentiment": "震荡", "indices": {}}, style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 30, "themes": [], "data_sources": {}},
        northbound={}, watchlist_results=[], suggestions={},
        strength_data=strength_data, focus_pool_data=focus_pool_data,
    )
    # 表头 F | E | V
    assert "| F | E | V | 依据 |" in md, "走强板块表头应有 F | E | V"
    # 板块成交聚合行
    assert "板块成交:" in md, "应有板块成交汇总行"
    assert "涨停 5.0亿" in md, "涨停聚合渲染错误"
    assert "FEV 平均(3只)" in md or "F̄ 8.5" in md, "F̄/Ē/V̄ 平均应渲染"
    # 个股 F E V 分列（值 9 / 8 / 7）
    row = next(l for l in md.split("\n") if "贵州茅台" in l and "龙头" in l)
    cells = [c.strip() for c in row.split("|")]
    print(f"row cells: {cells}")
    assert "9" in cells and "8" in cells and "7" in cells, "F/E/V 三列具体值应渲染"
    print("[PASS] 板块强弱表 F/E/V 列 + 成交汇总渲染正确")


def test_render_theme_block_fev():
    theme_stock_details = {
        "AI算力": [{
            "code": "600519", "name": "贵州茅台", "label": "涨停", "chg": 9.8,
            "chg5": 5.0, "r10": 12.0, "amount_wan": 50000, "reason": "白酒",
        }, {
            "code": "300001", "name": "正常股", "label": "强势", "chg": 5.0,
            "chg5": 3.0, "r10": 8.0, "amount_wan": 30000, "reason": "AI",
        }],
    }
    theme_groups = {
        "主升浪": [{"theme": "AI算力", "level": 4, "narrative": "Validation",
                    "consecutive_days": 5, "today_count": 8, "alpha_label": "",
                    "driver": "算力", "label": "主线", "cumulative_stocks": 30}],
    }
    focus_pool_data = [{
        "code": "600519", "name": "贵州茅台", "source": ["zt"], "hot_rank": 5,
        "fev_total": 24, "fev": {"f_score": 9, "e_score": 8, "v_score": 7, "fev_total": 24},
        "composite": {"total": 60, "advice": "加仓", "scores": {}},
        "change_pct": 9.8, "amount_wan": 50000,
    }]
    md = report.render_report(
        trade_date="2026-05-20",
        market={"sentiment": "震荡", "indices": {}}, style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 30, "themes": [], "data_sources": {}, "today": [],
                 "leveled": [{"theme": "AI算力", "level": 4, "today_count": 8,
                              "consecutive_days": 5, "cumulative_stocks": 30, "label": "主线",
                              "narrative": "Validation"}]},
        northbound={}, watchlist_results=[], suggestions={},
        theme_stock_details=theme_stock_details, theme_groups=theme_groups,
        zt_pool={"600519": {"first_time": "09:30:00", "consecutive_boards": 2}},
        focus_pool_data=focus_pool_data,
    )
    # 题材块成交汇总
    assert "板块成交（明细汇总）" in md, "题材块应有成交汇总"
    # 表头加 F/E/V
    headers = [l for l in md.split("\n") if "| 标的 | 代码" in l]
    assert any("F | E | V" in h for h in headers), f"题材详情表头应加 F | E | V，找到 {headers}"
    # 贵州茅台行有 9/8/7
    rows = [l for l in md.split("\n") if "贵州茅台" in l and "|" in l]
    assert any("9" in r and "8" in r and "7" in r for r in rows), "题材详情 F/E/V 应渲染"
    print("[PASS] 题材热度详情表 F/E/V + 成交汇总渲染正确")


if __name__ == "__main__":
    test_attach_amount_aggregates()
    test_attach_fev_aggregates()
    test_render_strength_split_fev()
    test_render_theme_block_fev()
    print("\n=== All A2 checks passed ===")
