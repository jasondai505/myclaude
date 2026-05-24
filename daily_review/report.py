"""每日复盘系统 - Markdown 报告生成器"""
from datetime import datetime, timedelta


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
    render_sectors(lines, sectors)

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


# ============================================================
# 板块强弱分析渲染
# ============================================================

def _fmt_strength_row(role_name: str, s: dict, pool_lookup: dict = None) -> str:
    mcap = s.get("mcap_yi", 0)
    mcap_str = f"{mcap:.0f}亿" if mcap else "-"
    zt_str = s.get("zt_time", "") or ""
    cb = s.get("consecutive_boards", 0)
    cb_str = f"{cb}板" if cb else ""
    hot_str = "-"
    f_str = e_str = v_str = "-"
    if pool_lookup:
        p = pool_lookup.get(s["code"])
        if p:
            hr = p.get("hot_rank")
            if hr:
                hot_str = str(hr)
            fev = p.get("fev") or {}
            if fev.get("f_score") is not None:
                f_str = str(fev["f_score"])
            if fev.get("e_score") is not None:
                e_str = str(fev["e_score"])
            if fev.get("v_score") is not None:
                v_str = str(fev["v_score"])
    return (
        f"| {role_name} | {s['name']} | {s['code']} | {mcap_str} "
        f"| {s['chg']:+.1f}% | {zt_str} | {cb_str} "
        f"| {s['r10']:+.1f}% | {s['r5']:+.1f}% "
        f"| {hot_str} | {f_str} | {e_str} | {v_str} | {s.get('role_reason', '')} |"
    )


def _fmt_amount_wan(v: float) -> str:
    if v >= 10000:
        return f"{v/10000:.1f}亿"
    return f"{v:.0f}万"


def _fmt_theme_amount_line(ts: dict) -> str:
    zt = ts.get("amount_zt_wan", 0)
    nonzt = ts.get("amount_nonzt_wan", 0)
    top100 = ts.get("amount_top100_wan", 0)
    total = ts.get("amount_total_wan", 0)
    f_avg = ts.get("f_avg")
    e_avg = ts.get("e_avg")
    v_avg = ts.get("v_avg")
    fev_n = ts.get("fev_n", 0)
    parts = [
        f"涨停 {_fmt_amount_wan(zt)}",
        f"非涨停 {_fmt_amount_wan(nonzt)}",
        f"人气100 {_fmt_amount_wan(top100)}",
        f"合计 {_fmt_amount_wan(total)}",
    ]
    line = "- 板块成交: " + " / ".join(parts)
    if ts.get("small_cap_flag"):
        line += " ⚠️板块体量太小(<1亿)"
    if fev_n:
        fev_parts = []
        if f_avg is not None:
            fev_parts.append(f"F̄ {f_avg}")
        if e_avg is not None:
            fev_parts.append(f"Ē {e_avg}")
        if v_avg is not None:
            fev_parts.append(f"V̄ {v_avg}")
        if fev_parts:
            line += f" | FEV 平均({fev_n}只): " + "/".join(fev_parts)
    return line


def _render_strength(lines: list, sd: dict, focus_pool_data: list = None):
    pool_lookup = {}
    if focus_pool_data:
        for item in focus_pool_data:
            pool_lookup[item.get("code", "")] = item

    strong = sd.get("strong_themes", [])
    emerging = sd.get("emerging_themes", [])
    fading = sd.get("fading_themes", [])
    common = sd.get("rising_commonalities", {})

    if strong:
        lines.append("### 走强板块\n")
        for ts in strong[:8]:
            theme = ts["theme"]
            stage = ts["stage"]
            catalyst = ts["catalyst_type"]
            avg5 = ts["avg_5d"]
            lines.append(f"**{theme}** | {stage} | {catalyst} | 成分股均5日{avg5:+.1f}%\n")
            lines.append(_fmt_theme_amount_line(ts))

            roles = ts.get("roles", {})
            has_roles = any(roles.get(r) for r in ("龙头", "中军", "量化标的"))
            if has_roles:
                lines.append("| 角色 | 标的 | 代码 | 市值 | 当日% | 涨停时间 | 连板 | 10日% | 5日% | 人气# | F | E | V | 依据 |")
                lines.append("|------|------|------|-----:|------:|:--------:|:----:|------:|-----:|------:|--:|--:|--:|------|")
                for role_name in ("龙头", "中军", "量化标的"):
                    for s in roles.get(role_name, []):
                        lines.append(_fmt_strength_row(role_name, s, pool_lookup))
                lines.append("")

    if emerging:
        lines.append("### 潜在走强（将成龙）\n")
        for ts in emerging[:5]:
            theme = ts["theme"]
            catalyst = ts["catalyst_type"]
            cons = ts["consecutive_days"]
            cnt = ts["today_count"]
            lines.append(f"**{theme}** | 爆发初期({cons}天) | {catalyst} | 今日涨停{cnt}只\n")
            lines.append(_fmt_theme_amount_line(ts))

            dragons = ts.get("roles", {}).get("将成龙", [])
            if dragons:
                lines.append("| 将成龙 | 代码 | 当日% | 涨停时间 | 连板 | 5日% | 人气# | F | E | V | 信号 |")
                lines.append("|--------|------|------:|:--------:|:----:|-----:|------:|--:|--:|--:|------|")
                for s in dragons:
                    zt_str = s.get("zt_time", "") or ""
                    cb = s.get("consecutive_boards", 0)
                    cb_str = f"{cb}板" if cb else ""
                    hot_str = "-"
                    f_str = e_str = v_str = "-"
                    p = pool_lookup.get(s["code"])
                    if p:
                        hr = p.get("hot_rank")
                        if hr:
                            hot_str = str(hr)
                        fev = p.get("fev") or {}
                        if fev.get("f_score") is not None:
                            f_str = str(fev["f_score"])
                        if fev.get("e_score") is not None:
                            e_str = str(fev["e_score"])
                        if fev.get("v_score") is not None:
                            v_str = str(fev["v_score"])
                    lines.append(
                        f"| {s['name']} | {s['code']} | {s['chg']:+.1f}% "
                        f"| {zt_str} | {cb_str} | {s['r5']:+.1f}% "
                        f"| {hot_str} | {f_str} | {e_str} | {v_str} "
                        f"| {s.get('role_reason', '')} |"
                    )
                lines.append("")

            other_roles = ts.get("roles", {})
            has_other = any(other_roles.get(r) for r in ("龙头", "中军", "量化标的"))
            if has_other:
                lines.append("| 角色 | 标的 | 代码 | 市值 | 当日% | 涨停时间 | 连板 | 10日% | 5日% | 人气# | F | E | V | 依据 |")
                lines.append("|------|------|------|-----:|------:|:--------:|:----:|------:|-----:|------:|--:|--:|--:|------|")
                for role_name in ("龙头", "中军", "量化标的"):
                    for s in other_roles.get(role_name, []):
                        lines.append(_fmt_strength_row(role_name, s, pool_lookup))
                lines.append("")

    if fading:
        lines.append("### 退潮板块\n")
        lines.append("| 板块 | 此前级别 | 退潮信号 | 今日涨停 | 5日涨幅 |")
        lines.append("|------|---------|---------|---------|---------|")
        for ts in fading[:8]:
            level_label = f"{ts['level']}-{ts['label']}" if ts.get('label') else str(ts['level'])
            narrative = ts.get("narrative", "")
            signal = "count下降" if narrative == "Violation" else "题材消失" if narrative == "Reversal" else narrative
            lines.append(
                f"| {ts['theme']} | {level_label} | {signal} "
                f"| {ts['today_count']} | {ts['avg_5d']:+.1f}% |"
            )
        lines.append("")

    if common and common.get("count", 0) > 0:
        n = common["count"]
        lines.append(f"### 近期赚钱模式\n")
        lines.append(f"近5日涨幅>10%个股共**{n}只**：\n")

        theme_dist = common.get("theme_dist", [])
        if theme_dist:
            dist_str = "、".join(f"{t}({c})" for t, c in theme_dist[:5])
            lines.append(f"- **板块集中**：{dist_str}")

        mcap = common.get("mcap_dist", {})
        if mcap:
            lines.append(f"- **市值分布**：{'、'.join(f'{k} {v}' for k, v in mcap.items())}")

        board = common.get("board_dist", {})
        if board:
            lines.append(f"- **板块类型**：{'、'.join(f'{k} {v}' for k, v in board.items())}")

        price = common.get("price_dist", {})
        if price:
            lines.append(f"- **价格区间**：{'、'.join(f'{k} {v}' for k, v in price.items())}")

        tech = common.get("tech_dist", {})
        if tech:
            lines.append(f"- **技术面**：{'、'.join(f'{k} {v}' for k, v in tech.items())}")

        conclusion = common.get("conclusion", "")
        if conclusion:
            lines.append(f"\n> {conclusion}")

        lines.append("")


from report_sections import (
    render_market_overview, render_style, render_sentiment,
    render_sectors, render_themes, render_northbound,
    render_global_markets, render_watchlist, render_watchlist_cross,
    render_fundamentals, render_popularity, render_zsxq, render_focus_pool,
    render_advice,
)
