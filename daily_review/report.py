"""每日复盘系统 - Markdown 报告生成器"""
from datetime import datetime, timedelta

from report_sections import (
    render_market_overview, render_style, render_sentiment,
    render_sectors, render_themes, render_northbound,
    render_global_markets, render_watchlist, render_watchlist_cross,
    render_fundamentals, render_popularity, render_zsxq, render_focus_pool,
    render_advice, _render_strength, render_limit_up_analysis,
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
    limit_up_data: dict = None,
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

    # 4.6、涨停深度分析
    if limit_up_data and limit_up_data.get("t1"):
        render_limit_up_analysis(lines, limit_up_data)

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

    # 十一、BOM 产业链交叉视角
    render_bom_cross_ref(lines, sectors)

    # 十二、产业链卡脖子分析（Serenity）
    render_serenity_chain(lines, sectors)

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


def render_bom_cross_ref(lines: list[str], sectors: dict | None):
    """BOM 产业链 × 今日强势行业 交叉引用。"""
    lines.append("## 十一、BOM 产业链视角")
    lines.append("")
    try:
        import sys
        from pathlib import Path
        parent = str(Path(__file__).resolve().parent.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from bom_analyzer import chain_db
        chain_db.init_db()
        industries = chain_db.list_industries()
    except Exception:
        lines.append("> BOM 知识库暂不可用")
        lines.append("")
        return

    if not industries:
        lines.append("> BOM 知识库暂无数据")
        lines.append("")
        return

    # 取当日强势行业名
    strong_names: set[str] = set()
    if sectors and sectors.get("all"):
        for r in sectors["all"][:15]:
            strong_names.add(r["name"])

    matched = []
    for ind in industries[:15]:
        data = chain_db.query_industry(ind)
        h3_segs = [s for s in data.get("segments", []) if s.get("is_3h")]
        if not h3_segs:
            continue
        # 检查是否与当日强势行业匹配
        match = ind in strong_names
        match_tag = "🔥" if match else ""
        seg_strs = []
        for s in h3_segs:
            ldrs = s.get("leaders", [])[:2]
            stock_str = "、".join(
                f"{l['stock_name']}({l['stock_code']})" for l in ldrs)
            seg_strs.append(f"{s['segment']} → {stock_str}")
        matched.append((match, ind, seg_strs))

    matched.sort(key=lambda x: (not x[0], x[1]))

    if matched:
        lines.append("| 赛道 | 当日表现 | 三高环节 | 核心龙头 |")
        lines.append("|------|:--------:|----------|----------|")
        for is_strong, ind, segs in matched:
            flag = "🔥强势" if is_strong else "已覆盖"
            lines.append(f"| {ind} | {flag} | {'<br>'.join(segs[:3])} | - |")
        lines.append("")
        strong_count = sum(1 for m in matched if m[0])
        lines.append(f"> BOM 覆盖 {len(matched)} 个赛道，其中 {strong_count} 个为今日强势行业。")
    else:
        lines.append("> 暂无交叉覆盖")
    lines.append("")


def render_serenity_chain(lines: list[str], sectors: dict | None):
    """Serenity 产业链卡脖子分析 — 全球视角 → A股映射。"""
    lines.append("## 十二、产业链卡脖子分析（Serenity）")
    lines.append("")
    try:
        import sys
        from pathlib import Path
        parent = str(Path(__file__).resolve().parent.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from bom_analyzer import chain_db
        chain_db.init_db()
        industries = chain_db.list_industries()
    except Exception:
        lines.append("> BOM 知识库暂不可用")
        lines.append("")
        return

    if not industries:
        lines.append("> BOM 知识库暂无数据")
        lines.append("")
        return

    # 找当日最强行业 × BOM 覆盖赛道
    strong_names: set[str] = set()
    if sectors and sectors.get("all"):
        for r in sectors["all"][:10]:
            strong_names.add(r["name"])

    target = None
    for ind in industries:
        if ind in strong_names:
            target = ind
            break
    if not target:
        target = industries[0]  # fallback 到最近更新的赛道

    try:
        from engine_serenity import analyze_global_chain, map_to_a_shares
        chain_name = target.split("】")[-1].split("（")[0].strip() if "】" in target else target
        lines.append(f"### 当日最强赛道：{target}")
        lines.append("")

        # 跑第一层+第二层（第三层太重，复盘报告只到映射层）
        layer1 = analyze_global_chain(chain_name)
        if layer1:
            # 提取核心结论（取前 15 行关键内容）
            key_lines = [l for l in layer1.split("\n") if l.strip() and not l.startswith("#")]
            summary = "\n".join(key_lines[:25])
            lines.append(summary)
            lines.append("")
        else:
            lines.append("> 分析暂不可用（API 调用失败或暂无数据）")
            lines.append("")

        lines.append("*Serenity 供应链卡脖子分析，基于全球视角 → A股映射。"
                      "不构成投资建议。*")
    except Exception as e:
        lines.append(f"> 分析模块暂不可用: {e}")
    lines.append("")
