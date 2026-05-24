"""P0 改动验证：ST 分流 + FEV 分项渲染"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import engine
import report


def test_st_split():
    df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "题材归因": "白酒+消费", "成交额": 1e9},
        {"代码": "000001", "名称": "ST平安", "题材归因": "金融+3连板", "成交额": 5e8},
        {"代码": "000002", "名称": "*ST万科", "题材归因": "地产+摘帽预期", "成交额": 3e8},
        {"代码": "002001", "名称": "新和成", "题材归因": "维生素+5连板", "成交额": 2e9},
        {"代码": "300001", "名称": "次新股代表", "题材归因": "次新+反弹", "成交额": 1e8},
    ])
    s = engine.analyze_sentiment(df)

    print(f"st_stocks: {[x['name'] for x in s['st_stocks']]}")
    print(f"logic_stocks: {[x['name'] for x in s['logic_stocks']]}")
    print(f"emotion_stocks: {[x['name'] for x in s['emotion_stocks']]}")
    print(f"logic_count={s['logic_count']} emotion_count={s['emotion_count']}")
    print(f"ladder keys: {list(s['ladder'].keys())}")
    print(f"leader: {s['leader']}")
    print(f"biggest_vol_limit: {s['biggest_vol_limit']}")

    assert len(s["st_stocks"]) == 2, "ST 应当 2 只"
    assert all(not x["name"].startswith(("ST", "*ST")) for x in s["logic_stocks"]), "logic 不应含 ST"
    assert all(not x["name"].startswith(("ST", "*ST")) for x in s["emotion_stocks"]), "emotion 不应含 ST"
    for n, items in s["ladder"].items():
        for x in items:
            assert not x["name"].startswith(("ST", "*ST")), f"ladder[{n}] 不应含 ST"
    if s["leader"]:
        for x in s["leader"]["stocks"]:
            assert not x["name"].startswith(("ST", "*ST")), "leader 不应含 ST"
    if s["biggest_vol_limit"]:
        assert not s["biggest_vol_limit"]["name"].startswith(("ST", "*ST")), "biggest 不应是 ST"

    print("[PASS] ST 分流正确")


def _stub_report(**overrides):
    base = dict(
        trade_date="2026-05-20",
        market={"sentiment": "震荡", "sh_pct": 0.5, "sz_pct": 0.3, "cyb_pct": 0.2, "limit_up_count": 30},
        style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 30, "themes": [], "data_sources": {}},
        northbound={},
        watchlist_results=[],
        suggestions={},
    )
    base.update(overrides)
    return report.render_report(**base)


def test_st_render():
    sentiment = engine.analyze_sentiment(pd.DataFrame([
        {"代码": "000001", "名称": "ST平安", "题材归因": "金融+3连板", "成交额": 5e8},
        {"代码": "600519", "名称": "贵州茅台", "题材归因": "白酒", "成交额": 1e9},
    ]))
    md = _stub_report(sentiment=sentiment)
    assert "ST 异动" in md, "报告应含 ST 异动表"
    assert "ST平安" in md, "ST 应出现在 ST 异动表"
    logic_section = md.split("ST 异动")[0]
    assert "ST平安" not in logic_section, "ST 不应出现在 ST 异动表之前"
    print("[PASS] ST 异动表渲染正确")


def test_fev_columns():
    focus_pool_data = [{
        "code": "600519", "name": "贵州茅台", "source": ["watch"],
        "hot_rank": 0, "hot_rate": 0, "rank_chg": 0,
        "concept_tags": [], "pop_tag": "", "zt_time": "", "zt_boards": 0,
        "fev_total": 25,
        "fev": {"f_score": 9, "e_score": 8, "v_score": 8, "fev_total": 25},
        "composite": {"total": 60, "advice": "加仓", "scores": {"sector": 15, "catalyst": 10, "tech": 8, "risk": 8}},
        "change_pct": 1.5, "research_summary": "", "lhb_summary": "", "zsxq_mentions": 0,
    }]
    md = _stub_report(focus_pool_data=focus_pool_data)
    assert "| F | E | V |" in md, "表头应含 F/E/V 三列"
    assert "25(83%)" in md, "FEV 列应含百分比"
    line = next(l for l in md.split("\n") if "贵州茅台" in l and "|" in l and "综合分" not in l)
    cells = [c.strip() for c in line.split("|")]
    print(f"row cells: {cells}")
    assert "9" in cells and "8" in cells, "应渲染 f/e/v 具体分数"
    print("[PASS] FEV 三列渲染正确")


if __name__ == "__main__":
    test_st_split()
    test_st_render()
    test_fev_columns()
    print("\n=== All P0 checks passed ===")
