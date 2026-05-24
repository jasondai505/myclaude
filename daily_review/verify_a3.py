"""A3 验证：§7 同花顺 Top100 按题材聚合"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import report


def test_render_ths_hot_buckets():
    ths_hot = [
        {"rank": 1, "code": "600519", "name": "贵州茅台", "hot_rate": 95.0,
         "change_pct": 2.5, "concept_tags": ["白酒", "消费"], "rank_chg": 0, "pop_tag": ""},
        {"rank": 2, "code": "002001", "name": "新和成", "hot_rate": 92.0,
         "change_pct": 9.8, "concept_tags": ["AI算力", "GPU"], "rank_chg": 5, "pop_tag": "新"},
        {"rank": 3, "code": "300001", "name": "正常股", "hot_rate": 90.0,
         "change_pct": -1.0, "concept_tags": ["AI算力"], "rank_chg": -2, "pop_tag": ""},
        {"rank": 4, "code": "888888", "name": "无概念股", "hot_rate": 85.0,
         "change_pct": 0.5, "concept_tags": [], "rank_chg": 0, "pop_tag": ""},
    ]
    md = report.render_report(
        trade_date="2026-05-20",
        market={"sentiment": "震荡", "indices": {}}, style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 30, "themes": [], "data_sources": {}},
        northbound={}, watchlist_results=[], suggestions={},
        concept_heat=[], hot_stocks=[],
        ths_hot=ths_hot,
    )
    assert "同花顺 Top100 按题材聚合" in md, "应渲染分桶标题"
    assert "**AI算力**（2只）" in md, f"AI算力应分桶 2 只"
    assert "**白酒**（1只）" in md, "白酒应分桶 1 只"
    assert "**其他**（1只）" in md, "无 concept 的应进「其他」"
    s_md = md
    pos_other = s_md.find("**其他**")
    pos_ai = s_md.find("**AI算力**")
    pos_baijiu = s_md.find("**白酒**")
    assert pos_other > pos_ai, "其他应在 AI算力 之后"
    assert pos_other > pos_baijiu, "其他应在 白酒 之后"
    assert "| 1 | 600519 | 贵州茅台" in md, "贵州茅台行应渲染"
    assert "其他题材" in md, "表头应含「其他题材」列"
    print("[PASS] A3 §7 Top100 按题材聚合渲染正确")


if __name__ == "__main__":
    test_render_ths_hot_buckets()
    print("\n=== All A3 checks passed ===")
