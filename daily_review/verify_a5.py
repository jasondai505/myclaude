"""A5 验证：外围标的加「国内映射标的」+「最近催化」两列（离线，不调 API）"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import report
import llm
from config import OVERSEAS_MAP


def test_render_overseas_columns():
    global_markets = {
        "indices": {
            "纳斯达克": {"price": 15000.0, "change_pct": 0.5, "change_pct_5d": 2.3},
        },
        "watchlist": {
            "英伟达(NVDA)": {"price": 800.0, "change_pct": -2.0, "change_pct_5d": -5.2,
                             "tag": "us_tech", "catalyst": "Blackwell放量|数据中心capex超预期"},
            "AMD": {"price": 150.0, "change_pct": 1.0, "change_pct_5d": 3.5, "tag": "us_tech"},
            "中芯国际(H)": {"price": 30.0, "change_pct": 0.5, "change_pct_5d": 1.0,
                          "tag": "hk", "catalyst": "国产替代加速"},
        },
    }
    md = report.render_report(
        trade_date="2026-05-24",
        market={"sentiment": "震荡", "indices": {}}, style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 0, "themes": [], "data_sources": {}},
        northbound={}, watchlist_results=[], suggestions={},
        global_markets=global_markets,
    )

    assert "| 标的 | 收盘 | 涨跌幅 | 5日% | 国内映射标的 | 最近催化 |" in md, "watchlist 表头应含两新列"
    assert OVERSEAS_MAP["英伟达(NVDA)"] in md, "NVDA 映射标的应渲染"
    assert OVERSEAS_MAP["中芯国际(H)"] in md, "中芯国际映射标的应渲染"
    # LLM 催化里的 | 必须被转义成 / 才不破坏表格
    assert "Blackwell放量/数据中心capex超预期" in md, "催化中的 | 应被转义为 /"
    assert "国产替代加速" in md, "港股催化应渲染"
    # 无 catalyst 字段的 AMD：映射在、催化兜底为 —
    assert OVERSEAS_MAP["AMD"] in md, "AMD 映射标的应渲染"
    print("[PASS] A5 外围两列渲染 + 单元转义正确")


def test_llm_graceful_no_key():
    import os
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        out = llm.generate_overseas_catalysts({"英伟达(NVDA)": {"change_pct": 1.0}}, "2026-05-24")
        assert out == {}, "无 API key 时应返回空 dict（兜底）"
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
    print("[PASS] A5 LLM 无 key 优雅兜底")


def test_llm_extract_json():
    assert llm._extract_json('```json\n{"a": "b"}\n```') == {"a": "b"}, "应抠出围栏内 JSON"
    assert llm._extract_json('好的，结果：{"x": 1} 完毕') == {"x": 1}, "应抠出前后赘述中的 JSON"
    assert llm._extract_json("没有json") is None, "无 JSON 应返回 None"
    print("[PASS] A5 LLM JSON 抽取健壮")


if __name__ == "__main__":
    test_render_overseas_columns()
    test_llm_graceful_no_key()
    test_llm_extract_json()
    print("\n=== All A5 checks passed ===")
