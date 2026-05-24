"""A4 验证：外围市场加 5日% 列"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import report


def test_render_global_5d():
    global_markets = {
        "indices": {
            "道琼斯": {"price": 38000.5, "change_pct": -0.2, "change_pct_5d": -1.5},
            "纳斯达克": {"price": 15000.0, "change_pct": 0.5, "change_pct_5d": 2.3},
            "恒生指数": {"price": 17000.0, "change_pct": -1.2, "change_pct_5d": None},
        },
        "watchlist": {
            "英伟达(NVDA)": {"price": 800.0, "change_pct": -2.0, "change_pct_5d": -5.2, "tag": "us_tech"},
            "AMD": {"price": 150.0, "change_pct": 1.0, "change_pct_5d": 3.5, "tag": "us_tech"},
            "中芯国际(H)": {"price": 30.0, "change_pct": 0.5, "change_pct_5d": 1.0, "tag": "hk"},
        },
    }
    md = report.render_report(
        trade_date="2026-05-20",
        market={"sentiment": "震荡", "indices": {}}, style={},
        sectors={"top": [], "bottom": [], "breadth": {"breadth_pct": 50, "up": 2000, "down": 2000}},
        themes={"total_stocks": 0, "themes": [], "data_sources": {}},
        northbound={}, watchlist_results=[], suggestions={},
        global_markets=global_markets,
    )
    assert "| 指数 | 收盘 | 涨跌幅 | 5日% |" in md, "全球指数表头应加 5日%"
    assert "| 标的 | 收盘 | 涨跌幅 | 5日% |" in md, "watchlist 表头应加 5日%"
    assert "-1.50%" in md, "道琼斯 5日% -1.5 应渲染"
    assert "+2.30%" in md, "纳斯达克 5日% +2.3 应渲染"
    assert "—" in md, "缺失 5日% 应渲染为 —"
    assert "+3.50%" in md and "-5.20%" in md, "美股 5日% 应渲染"
    print("[PASS] A4 外围 5日% 列渲染正确")


if __name__ == "__main__":
    test_render_global_5d()
    print("\n=== All A4 checks passed ===")
