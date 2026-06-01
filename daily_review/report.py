"""每日复盘系统 - Markdown 报告生成器"""
from datetime import datetime, timedelta

from report_sections import (
    render_market_overview, render_style, render_sentiment,
    render_sectors, render_themes, render_northbound,
    render_global_markets, render_watchlist, render_watchlist_cross,
    render_fundamentals, render_popularity, render_zsxq, render_focus_pool,
    render_advice, _render_strength,
)


def render_report(
    trade_date: str,
    market: dict,
    style: dict,
    sectors: dict,
    themes: dict,
    northbound: dict,
    watchlist_results: list[dict],
    suggestions: dict,
    *,
    sentiment: dict = None,
    global_markets: dict = None,
    watchlist_themes: dict = None,
    fundamentals: list = None,
    theme_aesthetics: list = None,
    zsxq_data: dict = None,
    fev_scores: list = None,
    theme_stock_details: dict = None,
    theme_groups: dict = None,
    theme_new_dirs: list = None,
    theme_longtail: list = None,
    strength_data: dict = None,
    zt_pool: dict = None,
    concept_heat: list = None,
    hot_stocks: list = None,
    focus_pool_data: list = None,
) -> str:
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    fm = _build_frontmatter(
        trade_date, market, sectors, northbound, themes,
        theme_groups, global_markets, fev_scores, suggestions,
    )
    lines.append(fm)

    lines.append(f"# A股每日复盘 — {trade_date}")
    lines.append(f"> 生成时间: {now}\n")

    # 一、大盘总览
    render_market_overview(lines, market, sectors)

    # 二、市场风格
    render_style(lines, style)

    # 2.5、情绪面
    if sentiment:
        render_sentiment(lines, sentiment)

    # 三、行业轮动
    render_sectors(lines, sectors, trade_date)

    # 四、题材热度
    render_themes(lines, themes, theme_stock_details, focus_pool_data,
                  theme_groups, zt_pool, theme_new_dirs, theme_longtail,
                  theme_aesthetics)

    # 4.5、板块/个股强弱分析
    if strength_data:
        lines.append("## 板块强弱分析\n")
        _render_strength(lines, strength_data, focus_pool_data)

    # 五、北向资金
    render_northbound(lines, northbound)

    # 5.5、外围市场
    if global_markets and (global_markets.get("indices") or global_markets.get("watchlist")):
        render_global_markets(lines, global_markets)

    # 六、自选股扫描
    render_watchlist(lines, watchlist_results, fev_scores)

    # 6.5、自选股 × 热点交叉
    if watchlist_themes:
        render_watchlist_cross(lines, watchlist_themes)

    # 6.6、基本面快照
    if fundamentals:
        render_fundamentals(lines, fundamentals)

    # 七、市场人气
    if concept_heat or hot_stocks:
        render_popularity(lines, concept_heat, hot_stocks)

    # 八、知识星球要点
    if zsxq_data and zsxq_data.get("highlights"):
        render_zsxq(lines, zsxq_data)

    # 九、个股深度分析（聚焦池）
    if focus_pool_data:
        render_focus_pool(lines, focus_pool_data)

    # 十、操作建议
    render_advice(lines, suggestions)

    lines.append("---")
    lines.append("*本报告由每日复盘系统自动生成，仅供参考，不构成投资建议。*")

    prev_date = _prev_trade_date(trade_date)
    if prev_date:
        lines.append(f"\n---\n← [[review_{prev_date}|前一交易日]] ")

    return "\n".join(lines)


# ============================================================
# Obsidian frontmatter + 双链
# ============================================================

def _prev_trade_date(date_str: str) -> str | None:
    """简单取前一天（跳周末）"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return None


def _build_frontmatter(
    trade_date, market, sectors, northbound, themes,
    theme_groups, global_markets, fev_scores, suggestions,
) -> str:
    """生成 YAML frontmatter，供 Obsidian Dataview 查询"""
    sentiment = market.get("sentiment", "N/A")
    total_amt = market.get("total_amount_yi", 0)
    limit_up = market.get("limit_up_count", 0)
    breadth = sectors.get("breadth", {})
    up_pct = breadth.get("pct", 0)
    if not up_pct:
        _up = market.get("up_count", 0)
        _down = market.get("down_count", 0)
        _total = _up + _down
        if _total > 0:
            up_pct = round(_up / _total * 100, 1)

    nb_total = 0
    if northbound:
        nb_total = northbound.get("total", 0)

    mainline = []
    if theme_groups:
        for t in theme_groups.get("主升浪", []):
            mainline.append(t.get("theme", ""))
        for t in theme_groups.get("加速期", []):
            mainline.append(t.get("theme", ""))

    emerging = []
    if theme_groups:
        for t in theme_groups.get("新兴题材", []):
            emerging.append(t.get("theme", ""))

    fading = []
    if theme_groups:
        for t in theme_groups.get("退潮", []):
            fading.append(t.get("theme", ""))

    fev_top = []
    if fev_scores:
        for s in fev_scores[:5]:
            fev_top.append(f"{s.get('name', '')}({s.get('fev_total', 0)})")

    us_chg = ""
    if global_markets:
        wl = global_markets.get("watchlist", {})
        for label, q in wl.items():
            if "NVDA" in label:
                us_chg = f"{q.get('change_pct', 0):+.2f}%"
                break

    position = ""
    if suggestions:
        for op in suggestions.get("operation", []):
            if "仓位" in str(op):
                position = str(op).split("仓位")[-1].strip().rstrip("）)").split("（")[0].strip()
                break

    def yaml_list(items):
        if not items:
            return "[]"
        return "[" + ", ".join(f'"{i}"' for i in items[:8]) + "]"

    fm_lines = [
        "---",
        f"date: {trade_date}",
        "type: 每日复盘",
        f"sentiment: \"{sentiment}\"",
        f"amount_yi: {total_amt:.0f}",
        f"limit_up: {limit_up}",
        f"up_pct: {up_pct}",
        f"northbound: {nb_total:.1f}",
        f"nvda: \"{us_chg}\"",
        f"mainline: {yaml_list(mainline)}",
        f"emerging: {yaml_list(emerging)}",
        f"fading: {yaml_list(fading)}",
        f"fev_top: {yaml_list(fev_top)}",
        "---",
        "",
    ]
    return "\n".join(fm_lines)
