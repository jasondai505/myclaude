"""B1 验证：逻辑/情绪涨停四维分类"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import engine
import report


def _kline(close_seq, open_seq=None, high_seq=None, low_seq=None):
    n = len(close_seq)
    return pd.DataFrame({
        "close": close_seq,
        "open": open_seq or close_seq,
        "high": high_seq or close_seq,
        "low": low_seq or close_seq,
        "volume": [1e7] * n,
    })


def test_classify_pure_logic():
    """业绩公告 + 均线上行 + 温和放量 + 机构席位 + 独狼 → 纯逻辑"""
    r = engine.classify_limit_up_type(
        code="600519", name="贵州茅台",
        quote={"turnover_pct": 12.0, "open": 100, "high": 110, "low": 99, "limit_up": 110, "amplitude_pct": 11},
        kline=_kline([95, 96, 97, 98, 100]),
        lhb_info={"comment": "机构专用买入 1.2亿"},
        announcements=[{"title": "关于签订重大销售合同的公告"}],
        themes_count=1,
    )
    print(f"pure_logic: net={r['net_score']} label={r['label']} breakdown={r['breakdown']}")
    assert r["label"] == "纯逻辑", f"应纯逻辑，实际 {r['label']}"
    assert r["net_score"] >= 3
    assert r["logic_score"] > r["emotion_score"]


def test_classify_pure_emotion():
    """澄清公告 + 一字板 + 缩量 + 全游资 + 题材联动 → 纯情绪"""
    r = engine.classify_limit_up_type(
        code="002001", name="妖股",
        quote={"turnover_pct": 0.8, "open": 110, "high": 110, "low": 110, "limit_up": 110, "amplitude_pct": 0},
        kline=_kline([100, 100, 100, 100, 110], open_seq=[100, 100, 100, 100, 110], high_seq=[100, 100, 100, 100, 110], low_seq=[100, 100, 100, 100, 110]),
        lhb_info={"comment": "东方财富证券拉萨团结路第二营业部 / 国泰君安南京太平南路营业部"},
        announcements=[{"title": "关于股票交易异常波动的公告"}],
        themes_count=5,
    )
    print(f"pure_emotion: net={r['net_score']} label={r['label']} breakdown={r['breakdown']}")
    assert r["label"] == "纯情绪", f"应纯情绪，实际 {r['label']}"
    assert r["net_score"] <= -3


def test_classify_mixed():
    """无显著信号 → 混合"""
    r = engine.classify_limit_up_type(
        code="300001", name="正常股",
        quote={"turnover_pct": 5.0, "open": 100, "high": 110, "low": 99, "limit_up": 110, "amplitude_pct": 11},
        kline=_kline([100, 99, 101, 100, 100]),
        lhb_info=None,
        announcements=[],
        themes_count=2,
    )
    print(f"mixed: net={r['net_score']} label={r['label']} breakdown={r['breakdown']}")
    assert r["label"] in ("混合", "偏逻辑", "偏情绪"), f"应混合附近，实际 {r['label']}"
    assert -2 <= r["net_score"] <= 2


def test_apply_to_sentiment():
    """apply_limit_up_classification 改写 sentiment_result 的家数/列表"""
    sentiment = {
        "ladder": {},
        "leader": None,
        "biggest_vol_limit": None,
        "logic_count": 0, "emotion_count": 0,
        "logic_stocks": [
            {"code": "600519", "name": "贵州茅台", "reason": "白酒+消费", "amount": 1e8, "board_n": 0},
            {"code": "002001", "name": "妖股", "reason": "次新+1连板", "amount": 5e7, "board_n": 1},
        ],
        "emotion_stocks": [],
        "st_stocks": [],
    }
    zt_pool = {
        "600519": {"first_time": "09:30:00", "consecutive_boards": 1},
        "002001": {"first_time": "09:25:00", "consecutive_boards": 2},
    }
    quotes = {
        "600519": {"turnover_pct": 12.0, "open": 100, "high": 110, "low": 99, "limit_up": 110, "amplitude_pct": 11},
        "002001": {"turnover_pct": 0.5, "open": 110, "high": 110, "low": 110, "limit_up": 110, "amplitude_pct": 0},
    }
    klines = {
        "600519": _kline([95, 96, 97, 98, 100]),
        "002001": _kline([100, 100, 100, 100, 110], open_seq=[100]*5, high_seq=[100, 100, 100, 100, 110], low_seq=[100, 100, 100, 100, 110]),
    }
    lhb = {"600519": {"comment": "机构专用"}, "002001": {"comment": "游资席位"}}
    corpus = {
        "600519": {"announcements": [{"title": "关于签订重大订单的公告"}]},
        "002001": {"announcements": [{"title": "异动公告"}]},
    }
    theme_counts = {"白酒": 1, "次新": 5}
    code_themes = {"600519": ["白酒"], "002001": ["次新"]}

    engine.apply_limit_up_classification(
        sentiment, zt_pool, quotes, klines, lhb, corpus, theme_counts, code_themes,
    )

    print(f"after apply: logic={sentiment['logic_count']} emotion={sentiment['emotion_count']} mixed={sentiment.get('mixed_count', 0)}")
    assert sentiment["logic_count"] >= 1, "应至少 1 只逻辑"
    assert sentiment["emotion_count"] >= 1, "应至少 1 只情绪"
    by_label = sentiment.get("by_label", {})
    assert "纯逻辑" in by_label or "偏逻辑" in by_label, f"应有逻辑分档，by_label={list(by_label.keys())}"
    all_classified = sentiment["logic_stocks"] + sentiment["emotion_stocks"] + sentiment.get("mixed_stocks", [])
    for s in all_classified:
        assert "label" in s and "net_score" in s and "breakdown" in s, f"stock 缺 label/net_score/breakdown: {s}"
    print("[PASS] apply_limit_up_classification 改写 sentiment 完成")


def test_render_b1_overview():
    """§2.5 渲染加三档家数 + 5 档明细 + 详情表加类型/净分列"""
    market = {
        "sentiment": "震荡", "indices": {"上证指数": {"price": 4200, "change_pct": 0, "amount_wan": 5000000, "amplitude_pct": 1}},
        "total_amount_yi": 1100, "liquidity": "缩量", "amount_vs_ma5": -5,
        "limit_up_count": 30,
    }
    sentiment = {
        "ladder": {}, "leader": None, "biggest_vol_limit": None,
        "logic_count": 10, "emotion_count": 12, "mixed_count": 5,
        "logic_stocks": [
            {"code": "600519", "name": "贵州茅台", "reason": "白酒", "amount": 1e8, "label": "纯逻辑", "net_score": 4,
             "breakdown": {"driver": 2, "trend": 1, "vp": 1, "lhb": 2, "theme_count": 1}},
        ],
        "emotion_stocks": [
            {"code": "002001", "name": "妖股", "reason": "次新", "amount": 5e7, "label": "纯情绪", "net_score": -4,
             "breakdown": {"driver": -1, "trend": -1, "vp": -1, "lhb": -1, "theme_count": -1}},
        ],
        "mixed_stocks": [], "st_stocks": [],
        "by_label": {"纯逻辑": 5, "偏逻辑": 5, "混合": 5, "偏情绪": 6, "纯情绪": 6},
    }
    md = report.render_report(
        trade_date="2026-05-20",
        market=market, style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 30, "themes": [], "data_sources": {}},
        northbound={}, watchlist_results=[], suggestions={},
        sentiment=sentiment,
    )
    assert "涨停类型: 逻辑驱动 10 只 / 情绪驱动 12 只 / 混合 5 只" in md, "§2.5 应渲染三档家数"
    assert "纯逻辑 5 / 偏逻辑 5 / 混合 5 / 偏情绪 6 / 纯情绪 6" in md, "§2.5 应渲染 5 档明细"
    assert "| 代码 | 名称 | 类型 | 净分 | 涨停原因 |" in md, "详情表表头应加 类型 净分"
    print("[PASS] §2.5 渲染 B1 五档 + 详情表加类型/净分")


if __name__ == "__main__":
    test_classify_pure_logic()
    test_classify_pure_emotion()
    test_classify_mixed()
    test_apply_to_sentiment()
    test_render_b1_overview()
    print("\n=== All B1 checks passed ===")
