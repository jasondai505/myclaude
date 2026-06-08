"""报告段落渲染函数 — 从 render_report 提取的各 Markdown 段落"""
from config import OVERSEAS_MAP
from engine import rate_theme
from report_utils import (
    _render_10d_row, _render_10d_board_row, _render_classified_table, _render_theme_block,
    _render_focus_table, _fmt_5d, _cell, _fmt_strength_row, _fmt_theme_amount_line,
)


def render_market_overview(lines, market, sectors):
    lines.append("## 一、大盘总览\n")
    lines.append(f"**市场情绪: {market.get('sentiment', 'N/A')}**\n")

    breadth = sectors.get("breadth", {})
    if breadth:
        up = breadth.get("up", 0)
        down = breadth.get("down", 0)
        lines.append(f"**涨跌家数: 上涨 {up} 家 / 下跌 {down} 家（{breadth.get('pct', 0)}%）**\n")

    total_amt = market.get("total_amount_yi", 0)
    liquidity = market.get("liquidity", "")
    amt_vs = market.get("amount_vs_ma5", 0)
    prev_amt = market.get("prev_total_amount_yi")
    if total_amt > 0:
        if prev_amt and prev_amt > 0:
            diff = total_amt - prev_amt
            pct = (diff / prev_amt) * 100
            cmp_str = f"，昨日{prev_amt:.0f}亿（{diff:+.0f}亿 / {pct:+.1f}%）"
        else:
            cmp_str = "，昨日 N/A"
        lines.append(f"**两市成交: {total_amt:.0f}亿（{liquidity}，vs5日均{amt_vs:+.1f}%{cmp_str}）**\n")

    profit_eff = market.get("profit_effect", "")
    limit_up = market.get("limit_up_count", 0)
    if profit_eff:
        lines.append(f"**赚钱效应: {profit_eff}** | 涨停 {limit_up} 家\n")

    history = market.get("history_10d") or []
    if history:
        dates = [h["date"][5:] for h in history]
        lines.append(f"\n**10 日涨跌停趋势（排除 ST，N={len(history)}）**\n")
        lines.append("| 指标 | " + " | ".join(dates) + " |")
        lines.append("|------|" + "------|" * len(dates))
        _render_10d_row(lines, history, "涨停", "limit_up_count")
        _render_10d_row(lines, history, "连板≥2", "limit_up_2plus")
        _render_10d_row(lines, history, "跌停", "limit_down_count")
        _render_10d_board_row(lines, history)
        lines.append("")

    lines.append("| 指数 | 收盘 | 涨跌幅 | 成交额(亿) | 振幅 |")
    lines.append("|------|-----:|-------:|-----------:|-----:|")
    sh_amt = 0
    sz_amt = 0
    for label, data in market.get("indices", {}).items():
        price = data.get("price", 0)
        chg = data.get("change_pct", 0)
        amount = data.get("amount_wan", 0) / 10000
        amp = data.get("amplitude_pct", 0)
        if label == "上证指数":
            sh_amt = amount
        elif label == "深证成指":
            sz_amt = amount
        sign = "+" if chg > 0 else ""
        lines.append(f"| {label} | {price:.2f} | {sign}{chg}% | {amount:.0f} | {amp}% |")

    if sh_amt > 0 and sz_amt > 0:
        total_amt = sh_amt + sz_amt
        if total_amt >= 10000:
            total_str = f"{total_amt:.0f}亿（{total_amt/10000:.2f}万亿）"
        else:
            total_str = f"{total_amt:.0f}亿"
        prev_amt = market.get("prev_total_amount_yi")
        if prev_amt and prev_amt > 0:
            chg_pct = (total_amt - prev_amt) / prev_amt * 100
            chg_str = f"环比{chg_pct:+.1f}%"
        else:
            chg_str = "—"
        lines.append(f"| **两市合计** | — | {chg_str} | **{total_str}** | — |")
    lines.append("")


def render_style(lines, style):
    lines.append("## 二、市场风格\n")
    lines.append(f"- **大小盘**: {style.get('size', 'N/A')}")
    lines.append(f"- **成长/价值**: {style.get('growth_value', 'N/A')}")
    detail = style.get("detail", {})
    if detail:
        parts = [f"{k} {v:+.2f}%" for k, v in detail.items()]
        lines.append(f"- 详情: {' | '.join(parts)}")
    lines.append("")


def render_sentiment(lines, sentiment):
    lines.append("### 情绪面\n")
    ladder = sentiment.get("ladder", {})
    if ladder:
        parts = []
        for n in sorted(ladder.keys(), reverse=True):
            names = "、".join(s["name"] for s in ladder[n][:3])
            parts.append(f"**{n}板**: {names}")
        lines.append(f"连板梯队: {' | '.join(parts)}\n")

    leader = sentiment.get("leader")
    if leader:
        top_stocks = "、".join(s["name"] for s in leader["stocks"][:2])
        lines.append(f"情绪龙头（最高{leader['board']}板）: {top_stocks}\n")

    biggest = sentiment.get("biggest_vol_limit")
    if biggest and biggest.get("amount", 0) > 0:
        lines.append(f"辨识度龙头（最大成交）: {biggest['name']}（{biggest['amount']/100000000:.1f}亿）\n")

    logic = sentiment.get("logic_count", 0)
    emotion = sentiment.get("emotion_count", 0)
    mixed = sentiment.get("mixed_count", 0)
    by_label = sentiment.get("by_label") or {}
    if logic + emotion + mixed > 0:
        if mixed or by_label:
            lines.append(f"涨停类型: 逻辑驱动 {logic} 只 / 情绪驱动 {emotion} 只 / 混合 {mixed} 只\n")
        else:
            lines.append(f"涨停类型: 逻辑驱动 {logic} 只 / 情绪驱动 {emotion} 只\n")
    if by_label:
        order = ["纯逻辑", "偏逻辑", "混合", "偏情绪", "纯情绪"]
        parts = [f"{k} {by_label.get(k, 0)}" for k in order]
        lines.append("> 五档分布: " + " / ".join(parts) + "\n")

    _render_classified_table(lines, "逻辑驱动涨停", sentiment.get("logic_stocks", []))
    _render_classified_table(lines, "混合涨停", sentiment.get("mixed_stocks", []))
    _render_classified_table(lines, "情绪驱动涨停", sentiment.get("emotion_stocks", []))

    st_stocks = sentiment.get("st_stocks", [])
    if st_stocks:
        lines.append(f"#### ST 异动（{len(st_stocks)}只，已从情绪/逻辑分析中剔除）\n")
        lines.append("| 代码 | 名称 | 连板 | 涨停原因 |")
        lines.append("|------|------|:----:|----------|")
        for s in sorted(st_stocks, key=lambda x: x["amount"], reverse=True):
            reason = s["reason"].replace("+", "、") if s["reason"] else ""
            board_n = s.get("board_n", 0)
            board_str = f"{board_n}板" if board_n >= 2 else "-"
            lines.append(f"| {s['code']} | {s['name']} | {board_str} | {reason} |")
        lines.append("")

    lines.append("")


def render_sectors(lines, sectors, trade_date=None):
    lines.append("## 三、行业轮动\n")
    lines.append("### 领涨 TOP5\n")
    lines.append("| # | 行业 | 涨跌幅 | 领涨股 |")
    lines.append("|---|------|-------:|--------|")
    for s in sectors.get("top", []):
        lines.append(f"| {s['rank']} | {s['name']} | +{s['change_pct']}% | {s.get('leader', '')} |")

    lines.append("\n### 领跌 TOP5\n")
    lines.append("| # | 行业 | 涨跌幅 | 领涨股 |")
    lines.append("|---|------|-------:|--------|")
    for s in sectors.get("bottom", []):
        lines.append(f"| {s['rank']} | {s['name']} | {s['change_pct']}% | {s.get('leader', '')} |")
    lines.append("")

    if trade_date:
        from engine_similar_days import similar_days_report
        lines.append(similar_days_report(trade_date))
        lines.append("")


def render_themes(lines, themes, theme_stock_details, focus_pool_data,
                  theme_groups, zt_pool, theme_new_dirs, theme_longtail,
                  theme_aesthetics):
    lines.append("## 四、题材热度\n")
    total_stocks = themes.get("total_stocks", 0)
    lines.append(f"当日涨停/强势股: **{total_stocks}** 只\n")

    narrative_labels = {
        "Formation": "形成", "Validation": "验证",
        "Violation": "动摇", "Reversal": "反转",
    }
    level_icons = {5: "5-赛道", 4: "4-主线", 3: "3-主升", 2: "2-轮动", 1: "1-异动", 0: "退潮"}

    details = theme_stock_details or {}

    theme_pool_lookup = {}
    if focus_pool_data:
        for item in focus_pool_data:
            theme_pool_lookup[item.get("code", "")] = item

    hot100_set = set()
    if focus_pool_data:
        hot100_set = {it["code"] for it in focus_pool_data
                       if it.get("hot_rank") and 1 <= it["hot_rank"] <= 100}

    if theme_groups:
        group_labels = [
            ("主升浪", "连续强势、叙事验证中的核心题材"),
            ("加速期", "热度正在加速上升的题材"),
            ("新兴题材", "首次出现或刚进入叙事形成期"),
            ("轮动", "间歇性出现的题材"),
            ("退潮", "热度下降或消失的题材"),
        ]
        for gname, gdesc in group_labels:
            group = theme_groups.get(gname, [])
            if not group:
                continue
            filtered = []
            for t in group:
                stocks = details.get(t["theme"], [])
                if t.get("today_count", 0) >= 2 or t.get("level", 0) >= 3 or len(stocks) >= 2:
                    filtered.append(t)
            if not filtered:
                continue
            lines.append(f"### {gname}（{gdesc}）\n")
            if gname == "退潮":
                names = [t["theme"] for t in filtered[:15]]
                lines.append(f"- {'、'.join(names)}\n")
            else:
                major = [t for t in filtered if len(details.get(t["theme"], [])) >= 2 or t.get("today_count", 0) >= 3]
                minor = [t for t in filtered if t not in major]
                for t in major:
                    stocks = details.get(t["theme"], [])
                    _render_theme_block(lines, t, stocks, narrative_labels, level_icons, zt_pool, hot100_set, theme_pool_lookup)
                if minor:
                    minor_parts = []
                    for t in minor:
                        label, score = rate_theme(t)
                        minor_parts.append(f"{t['theme']}({score}{label})")
                    lines.append(f"**其他相关题材**: {'、'.join(minor_parts)}\n")
    else:
        top_themes = themes.get("today", [])
        if top_themes:
            lines.append("### 热门题材 TOP10\n")
            lines.append("| 排名 | 题材 | 涨停股数 |")
            lines.append("|-----:|------|--------:|")
            for i, (theme, count) in enumerate(top_themes[:10], 1):
                lines.append(f"| {i} | {theme} | {count} |")
            lines.append("")

    if theme_new_dirs:
        lines.append("### 纯人气/中期强势新方向（不在涨停题材内）\n")
        lines.append("> 来源: 人气前100 ∪ 20日涨幅前100，≥3票成立；不计入题材分级历史\n")
        for d in theme_new_dirs[:8]:
            stocks = d.get("stocks", [])
            lines.append(f"**{d['theme']}**（{d.get('freq', len(stocks))}票）\n")
            lines.append("| 标的 | 代码 | 来源 | 当日% | 概念 |")
            lines.append("|------|------|:----:|------:|------|")
            for s in stocks[:10]:
                src_str = "".join(s.get("sources", []) or [])
                lines.append(
                    f"| {s['name']} | {s['code']} | {src_str} "
                    f"| {s.get('chg', 0):+.1f}% | {s.get('reason', '')} |"
                )
            lines.append("")

    if theme_longtail:
        names = [f"{t}({n})" for t, n in theme_longtail[:30]]
        lines.append(f"**其他长尾题材（<3票，共{len(theme_longtail)}个）**: " + "、".join(names) + "\n")

    leveled = themes.get("leveled", [])
    high_level = [t for t in leveled if t["level"] >= 2]
    aesthetics_map = {}
    if theme_aesthetics:
        aesthetics_map = {a["theme"]: a for a in theme_aesthetics}
    if high_level:
        lines.append("### 题材分级总览\n")
        lines.append("| 级别 | 题材 | 叙事阶段 | Alpha来源 | 评分 | 今日涨停 | 连续天数 | 累计涨停 |")
        lines.append("|:----:|------|:--------:|-----------|:----:|--------:|--------:|--------:|")
        for t in sorted(high_level, key=lambda x: (-x["level"], -x["today_count"])):
            n_label = narrative_labels.get(t.get("narrative", ""), "")
            a = aesthetics_map.get(t["theme"], {})
            alpha = a.get("alpha_label", "")
            entry = {**t, "alpha_label": alpha, "surge_score": a.get("surge_score", 0)}
            label, score = rate_theme(entry)
            lines.append(
                f"| {level_icons.get(t['level'], t['label'])} "
                f"| {t['theme']} | {n_label} | {alpha} | {score} {label} | {t['today_count']} "
                f"| {t['consecutive_days']} | {t['cumulative_stocks']} |"
            )
        lines.append("")

    if theme_aesthetics:
        lines.append("### 重点题材审美（3级以上）\n")
        for a in theme_aesthetics:
            lines.append(f"**{a['theme']}**（{a['label']}，连续{a['consecutive_days']}天）\n")
            lines.append(f"- A.驱动: {a.get('driver', 'N/A')}")
            lines.append(f"- B.落地: {a.get('landing', 'N/A')}")
            lines.append(f"- C.性价比: {a.get('value', 'N/A')}")
            lines.append(f"- D.资金: {a.get('capital', 'N/A')}")
            lines.append(f"- E.容量: {a.get('capacity', 'N/A')}")
            lines.append(f"- 置信度: {a.get('confidence', 'N/A')}")
            if a.get("alpha_label"):
                lines.append(f"- **Alpha来源: {a['alpha_label']}**")
            surge = a.get("surge_score", 0)
            surge_max = a.get("surge_max", 5)
            if a.get("surge_details"):
                detail_str = " ".join(a["surge_details"])
                lines.append(f"- **大涨前提: {surge}/{surge_max}**（{detail_str}）")
            lines.append("")


def render_northbound(lines, northbound):
    lines.append("## 五、北向资金\n")
    nb = northbound
    total = nb.get("total", 0)
    sign = "+" if total > 0 else ""
    lines.append(f"- **合计: {sign}{total}亿** — {nb.get('signal', '')}")
    lines.append(f"- 沪股通: {'+' if nb.get('hgt', 0) > 0 else ''}{nb.get('hgt', 0)}亿")
    lines.append(f"- 深股通: {'+' if nb.get('sgt', 0) > 0 else ''}{nb.get('sgt', 0)}亿")
    lines.append("")


def render_global_markets(lines, global_markets):
    lines.append("## 外围市场\n")
    g_signal = global_markets.get("signal", "")
    if g_signal:
        lines.append(f"**外围信号: {g_signal}**\n")

    if global_markets.get("indices"):
        lines.append("### 全球指数\n")
        lines.append("| 指数 | 收盘 | 涨跌幅 | 5日% |")
        lines.append("|------|-----:|-------:|-----:|")
        for label, q in global_markets.get("indices", {}).items():
            price = q.get("price", 0)
            chg = q.get("change_pct", 0)
            lines.append(f"| {label} | {price:.2f} | {chg:+.2f}% | {_fmt_5d(q.get('change_pct_5d'))} |")
        lines.append("")

    wl = global_markets.get("watchlist", {})
    us_tech = {k: v for k, v in wl.items() if v.get("tag") == "us_tech"}
    hk_stocks = {k: v for k, v in wl.items() if v.get("tag") == "hk"}
    others = {k: v for k, v in wl.items() if v.get("tag") not in ("us_tech", "hk")}

    if us_tech:
        lines.append("### 美股科技龙头\n")
        lines.append("| 标的 | 收盘 | 涨跌幅 | 5日% | 国内映射标的 | 最近催化 |")
        lines.append("|------|-----:|-------:|-----:|------|------|")
        for label, q in sorted(us_tech.items(), key=lambda x: x[1].get("change_pct", 0)):
            price = q.get("price", 0)
            chg = q.get("change_pct", 0)
            mapping = _cell(OVERSEAS_MAP.get(label, "—"))
            catalyst = _cell(q.get("catalyst", "—"))
            lines.append(f"| {label} | {price:.2f} | {chg:+.2f}% | {_fmt_5d(q.get('change_pct_5d'))} | {mapping} | {catalyst} |")
        lines.append("")

    if hk_stocks or others:
        lines.append("### 港股关注\n")
        lines.append("| 标的 | 收盘 | 涨跌幅 | 5日% | 国内映射标的 | 最近催化 |")
        lines.append("|------|-----:|-------:|-----:|------|------|")
        for label, q in {**hk_stocks, **others}.items():
            price = q.get("price", 0)
            chg = q.get("change_pct", 0)
            mapping = _cell(OVERSEAS_MAP.get(label, "—"))
            catalyst = _cell(q.get("catalyst", "—"))
            lines.append(f"| {label} | {price:.2f} | {chg:+.2f}% | {_fmt_5d(q.get('change_pct_5d'))} | {mapping} | {catalyst} |")
        lines.append("")
    if us_tech or hk_stocks or others:
        lines.append("> 最近催化为 LLM 基于产业逻辑推断，非实时新闻，仅供线索。\n")


def render_watchlist(lines, watchlist_results, fev_scores):
    lines.append("## 六、自选股扫描\n")
    valid_stocks = [s for s in watchlist_results if s.get("quote")]
    no_data = [s for s in watchlist_results if not s.get("quote")]

    fev_map = {}
    if fev_scores:
        fev_map = {f["code"]: f for f in fev_scores}

    if fev_map:
        lines.append("| 代码 | 名称 | 涨跌幅 | 换手率 | 量比 | FEV | F | E | V |")
        lines.append("|------|------|-------:|------:|-----:|----:|--:|--:|--:|")
        for s in sorted(valid_stocks, key=lambda x: fev_map.get(x["code"], {}).get("fev_total", 0), reverse=True):
            q = s.get("quote") or {}
            chg = q.get("change_pct", 0)
            turn = q.get("turnover_pct", 0)
            vr = q.get("vol_ratio", 0)
            sign = "+" if chg > 0 else ""
            fev = fev_map.get(s["code"], {})
            ft = fev.get("fev_total", 0)
            fs = fev.get("f_score", 0)
            es = fev.get("e_score", 0)
            vs = fev.get("v_score", 0)
            lines.append(f"| {s['code']} | {s['name']} | {sign}{chg}% | {turn}% | {vr} | {ft} | {fs} | {es} | {vs} |")
    else:
        lines.append("| 代码 | 名称 | 涨跌幅 | 换手率 | 量比 | 趋势分 |")
        lines.append("|------|------|-------:|------:|-----:|-------:|")
        for s in sorted(valid_stocks, key=lambda x: x.get("trend_score", 0), reverse=True):
            q = s.get("quote") or {}
            chg = q.get("change_pct", 0)
            turn = q.get("turnover_pct", 0)
            vr = q.get("vol_ratio", 0)
            score = s.get("trend_score", 0)
            sign = "+" if chg > 0 else ""
            score_sign = "+" if score > 0 else ""
            lines.append(f"| {s['code']} | {s['name']} | {sign}{chg}% | {turn}% | {vr} | {score_sign}{score} |")

    if no_data:
        codes = ", ".join(s["code"] for s in no_data)
        lines.append(f"\n> 以下标的无行情数据（北交所920xxx/已退市等）：{codes}")
    lines.append("")

    if fev_scores:
        highlights = [f for f in fev_scores if f["fev_total"] >= 20]
        if highlights:
            highlights.sort(key=lambda x: x["fev_total"], reverse=True)
            lines.append(f"### FEV重点标的（≥20分，共{len(highlights)}只）\n")
            for h in highlights[:15]:
                lines.append(f"**{h['name']}({h['code']})** FEV={h['fev_total']} (F:{h['f_score']} E:{h['e_score']} V:{h['v_score']})\n")
                if h["f_reasons"]:
                    lines.append(f"- F: {', '.join(h['f_reasons'])}")
                if h["e_reasons"]:
                    lines.append(f"- E: {', '.join(h['e_reasons'])}")
                if h["v_reasons"]:
                    lines.append(f"- V: {', '.join(h['v_reasons'])}")
                if h.get("surge_details"):
                    ss = h.get("surge_score", 0)
                    lines.append(f"- 大涨前提: {ss}/5（{' '.join(h['surge_details'])}）")
                if h.get("alpha_bucket"):
                    lines.append(f"- Alpha来源: {h['alpha_bucket']}")
                lines.append("")

        crash_stocks = [f for f in fev_scores if f.get("crash_warnings")]
        if crash_stocks:
            lines.append("### 大跌警示\n")
            for cs in crash_stocks:
                for w in cs["crash_warnings"]:
                    lines.append(f"- **{cs['name']}({cs['code']})** — {w}")
            lines.append("")

    has_signals = any(s["signals"] for s in watchlist_results)
    if has_signals:
        lines.append("### 信号明细\n")
        for s in watchlist_results:
            if not s["signals"]:
                continue
            lines.append(f"**{s['name']}({s['code']})**")
            for sig_type, desc in s["signals"]:
                icon = {"BULL": "🟢", "BEAR": "🔴", "WARN": "⚠️", "ALERT": "🔶", "INFO": "ℹ️"}.get(sig_type, "•")
                lines.append(f"- {icon} {desc}")
            lines.append("")


def render_watchlist_cross(lines, watchlist_themes):
    in_hot = watchlist_themes.get("in_hot", [])
    if in_hot:
        lines.append("### 自选股热点对标\n")
        lines.append("以下自选股出现在今日强势股中：\n")
        for s in in_hot:
            t_str = "、".join(s["themes"][:3])
            lines.append(f"- **{s['name']}**（{s['code']}）— {t_str}")
        lines.append("")

    coverage = watchlist_themes.get("theme_coverage", {})
    if coverage:
        lines.append("TOP5题材自选股覆盖：\n")
        for theme, info in coverage.items():
            pct = info["covered"] / info["total"] * 100 if info["total"] > 0 else 0
            lines.append(f"- {theme}: {info['covered']}/{info['total']}（{pct:.0f}%）")
        lines.append("")


def render_fundamentals(lines, fundamentals):
    has_data = [f for f in fundamentals if f.get("eps_forecast") or f.get("holder_signal") or f.get("recent_news")]
    if not has_data:
        return
    lines.append("### 基本面快照（趋势分TOP标的）\n")
    for f in has_data:
        lines.append(f"**{f['name']}（{f['code']}）**")
        if f.get("forward_pe"):
            lines.append(f"- 前瞻PE: {f['forward_pe']} | TTM PE: {f.get('pe_ttm', 'N/A')} | PB: {f.get('pb', 'N/A')}")
        if f.get("eps_forecast"):
            parts = [f"{e['year']}={e['eps']}" for e in f["eps_forecast"] if e.get("eps")]
            if parts:
                lines.append(f"- 一致预期EPS: {' / '.join(parts)}（{f.get('inst_count', '?')}家机构）")
        if f.get("holder_signal"):
            lines.append(f"- {f['holder_signal']}")
        if f.get("recent_news"):
            lines.append(f"- 近期新闻:")
            for n in f["recent_news"][:2]:
                lines.append(f"  - {n.get('title', '')}")
        lines.append("")


def render_popularity(lines, concept_heat, hot_stocks):
    lines.append("## 七、市场人气\n")

    if concept_heat:
        lines.append("### 概念板块热度 Top50\n")
        lines.append("| 排名 | 概念板块 | 涨跌幅 | 净流入(亿) | 家数 | 领涨股 | 领涨% |")
        lines.append("|-----:|---------|------:|----------:|-----:|-------|------:|")
        for i, c in enumerate(concept_heat, 1):
            net = c["net_flow"]
            net_str = f"{net:+.1f}" if net else "0.0"
            lines.append(
                f"| {i} | {c['name']} | {c['change_pct']:+.2f}% "
                f"| {net_str} | {c['count']} "
                f"| {c['leader']} | {c['leader_chg']:+.1f}% |"
            )
        lines.append("")

    if hot_stocks:
        lines.append("### 个股人气排名 Top200\n")
        lines.append("| 排名 | 代码 | 名称 | 最新价 | 涨跌幅 | 概念 | 人气标签 |")
        lines.append("|-----:|------|------|------:|------:|------|---------|")
        for s in hot_stocks:
            concepts = ",".join(s.get("concept_tags", [])[:2]) if s.get("concept_tags") else ""
            pop = s.get("pop_tag", "")
            lines.append(
                f"| {s['rank']} | {s['code']} | {s['name']} "
                f"| {s.get('price', 0):.2f} | {s.get('change_pct', 0):+.2f}% "
                f"| {concepts} | {pop} |"
            )
        lines.append("")


def render_zsxq(lines, zsxq_data):
    lines.append("## 八、知识星球要点\n")
    lines.append(f"> 来源: 调研纪要（{zsxq_data['topic_count']} 条帖子）\n")

    reviews = [h for h in zsxq_data["highlights"] if h["type"] == "review"]
    research = [h for h in zsxq_data["highlights"] if h["type"] == "research"]

    if reviews:
        lines.append("### 市场综述\n")
        for r in reviews[:3]:
            lines.append(f"**{r['title']}**（{r['author']}，{r['readers']}人阅读）\n")
            for para in r["text"].split("\n"):
                para = para.strip()
                if para:
                    lines.append(f"> {para}")
            lines.append("")

    if research:
        lines.append("### 机构观点\n")
        for r in research[:10]:
            lines.append(f"- **{r['title']}**（{r['readers']}人阅读）")
        lines.append("")


def render_focus_pool(lines, focus_pool_data):
    lines.append("## 九、个股深度分析\n")
    lines.append(f"> 聚焦池: {len(focus_pool_data)}只 = 人气Top100 ∪ 涨停池 ∪ 自选股（去重）\n")

    resonance = [s for s in focus_pool_data if len(s.get("source", [])) >= 2 and s.get("composite", {}).get("total", 0) >= 55]
    resonance.sort(key=lambda x: x.get("composite", {}).get("total", 0), reverse=True)

    hot_surge = [s for s in focus_pool_data if s.get("rank_chg", 0) and abs(s.get("rank_chg", 0)) >= 5 and s.get("composite", {}).get("total", 0) >= 40]
    hot_surge.sort(key=lambda x: x.get("composite", {}).get("total", 0), reverse=True)

    watch_items = [s for s in focus_pool_data if "watch" in s.get("source", [])]
    watch_items.sort(key=lambda x: x.get("composite", {}).get("total", 0), reverse=True)

    if resonance:
        lines.append("### 多维共振（人气+涨停+自选 多源交叉）\n")
        _render_focus_table(lines, resonance)

    if hot_surge:
        lines.append("### 人气飙升标的\n")
        _render_focus_table(lines, hot_surge, 10)

    if watch_items:
        lines.append("### 自选股状态\n")
        _render_focus_table(lines, watch_items, 30)

    corpus_items = [s for s in focus_pool_data if (s.get("corpus") or {}).get("announcements") or (s.get("corpus") or {}).get("irm") or (s.get("corpus") or {}).get("news")]
    if corpus_items:
        corpus_items.sort(key=lambda x: x.get("composite", {}).get("total", 0), reverse=True)
        lines.append("### 聚焦池语料摘要（公告 / 互动 / 新闻）\n")
        lines.append(f"> 共 {len(corpus_items)} 只有语料，按综合分降序，最多展示 30 只\n")
        for s in corpus_items[:30]:
            corpus = s.get("corpus") or {}
            anns = corpus.get("announcements", [])
            qas = corpus.get("irm", [])
            news = corpus.get("news", [])
            lines.append(f"#### {s.get('name','')}（{s.get('code','')}） · 综合{s.get('composite',{}).get('total',0)} · {s.get('composite',{}).get('advice','')}\n")
            if anns:
                lines.append("**公告:**")
                for a in anns[:3]:
                    title = a.get("title", "").replace("|", "/")
                    atype = a.get("type", "")
                    url = a.get("url", "")
                    if url:
                        lines.append(f"- [{atype}] [{title}]({url})")
                    else:
                        lines.append(f"- [{atype}] {title}")
                lines.append("")
            if qas:
                lines.append("**互动:**")
                for qa in qas[:3]:
                    q = qa.get("question", "").replace("\n", " ")[:120]
                    a = qa.get("answer", "").replace("\n", " ")[:160]
                    rt = qa.get("reply_time", "") or qa.get("ask_time", "")
                    lines.append(f"- Q（{rt}）: {q}")
                    if a:
                        lines.append(f"  A: {a}")
                lines.append("")
            if news:
                lines.append("**新闻:**")
                for n in news[:3]:
                    title = n.get("title", "").replace("|", "/")
                    src = n.get("source", "")
                    t = n.get("time", "")[:16]
                    lines.append(f"- ({t}, {src}) {title}")
                lines.append("")
        lines.append("")

    top_all = sorted(focus_pool_data, key=lambda x: x.get("composite", {}).get("total", 0), reverse=True)
    lines.append("### 最终个股建议\n")
    lines.append("| 操作 | 代码 | 名称 | 综合分 | 核心理由 |")
    lines.append("|:----:|------|------|------:|----------|")
    shown = set()
    for advice_level in ("买入", "加仓", "持有", "减仓", "回避"):
        candidates = [s for s in top_all if s.get("composite", {}).get("advice") == advice_level and s["code"] not in shown]
        limit = 5 if advice_level in ("买入", "加仓") else 10 if advice_level == "持有" else 5
        for s in candidates[:limit]:
            comp = s.get("composite", {})
            reason_parts = []
            if s.get("hot_rank") and s["hot_rank"] <= 30:
                reason_parts.append(f"人气#{s['hot_rank']}")
            if s.get("fev_total", 0) >= 15:
                fev_d = s.get("fev") or {}
                if fev_d:
                    reason_parts.append(
                        f"FEV={s['fev_total']}(F{fev_d.get('f_score',0)}/E{fev_d.get('e_score',0)}/V{fev_d.get('v_score',0)})"
                    )
                else:
                    reason_parts.append(f"FEV={s['fev_total']}")
            if s.get("zt_boards", 0) >= 2:
                reason_parts.append(f"{s['zt_boards']}连板")
            if s.get("concept_tags"):
                reason_parts.append("/".join(s["concept_tags"][:2]))
            if s.get("research_summary"):
                reason_parts.append(s["research_summary"])
            if s.get("bom_moat"):
                bm = s["bom_moat"]
                reason_parts.append(
                    f"BOM{bm.get('industry','')}#{bm.get('rank',0)}护城河{bm.get('moat_score',0)}分"
                )
            reason = ", ".join(reason_parts) if reason_parts else "-"
            lines.append(
                f"| **{advice_level}** | {s.get('code','')} | {s.get('name','')} "
                f"| {comp.get('total', 0)} | {reason} |"
            )
            shown.add(s["code"])
    lines.append("")


def render_advice(lines, suggestions):
    lines.append("## 十、操作建议\n")

    ops = suggestions.get("operation", [])
    if ops:
        lines.append("### 总体策略\n")
        for o in ops:
            lines.append(f"- {o}")
        lines.append("")

    focus = suggestions.get("focus", [])
    if focus:
        lines.append("### 关注机会\n")
        for f in focus:
            lines.append(f"- ✅ {f}")
        lines.append("")

    risk = suggestions.get("risk", [])
    if risk:
        lines.append("### 风险提示\n")
        for r in risk:
            lines.append(f"- ⚠️ {r}")
        lines.append("")


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
        lines.append(f"近5日涨幅>10%个股共**{n}**只：\n")

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

    bom_linkages = sd.get("bom_linkages")
    if bom_linkages:
        lines.append("### 产业链联动（BOM视角）\n")
        for link in bom_linkages[:6]:
            lines.append(f"**{link['industry']}**")
            lines.append(f"- 上游: {'、'.join(link['upstream'][:4])}")
            if link.get("midstream"):
                lines.append(f"- 中游: {'、'.join(link['midstream'][:4])}")
            lines.append(f"- 下游: {'、'.join(link['downstream'][:4])}")
            lines.append("")
        lines.append("")


def _fmt_limit_up_rows(lines: list[str], stocks: list, tier_label: str):
    if not stocks:
        return
    lines.append(f"### {tier_label}\n")
    lines.append("| 代码 | 名称 | 连板 | 封板 | 市值(亿) | PE | 题材 | 驱动 | 封板质量 | 评分 | 分析 |")
    lines.append("|------|------|:----:|:----:|--------:|----:|------|------|:--------:|:----:|------|")
    for s in stocks:
        themes = s.get("themes", [])
        if isinstance(themes, str):
            try:
                themes = __import__("json").loads(themes)
            except Exception:
                themes = []
        t_str = ",".join(themes[:2]) if themes else "-"
        mcap = s.get("mcap_yi") or 0
        pe = s.get("pe") or 0
        score = s.get("score")
        score_str = str(score) if score is not None else "-"
        lines.append(
            f"| {s['code']} | {s.get('name','')} | {s.get('boards',1)}板 "
            f"| {s.get('first_time','')} | {mcap:.0f} | {pe:.0f} "
            f"| {t_str} | {s.get('driver','')} "
            f"| {s.get('quality','')} | {score_str} "
            f"| {s.get('analysis','')[:60]} |"
        )
    lines.append("")


def render_limit_up_analysis(lines: list[str], lu: dict):
    t1 = lu.get("t1", [])
    t2 = lu.get("t2", [])
    t3 = lu.get("t3", [])

    lines.append("## 涨停深度分析\n")
    lines.append(f"> T1 龙头 {len(t1)} 支 | T2 题材 {len(t2)} 支 | T3 跟风 {len(t3)} 支\n")

    if not t1 and not t2:
        lines.append("*今日无涨停标的*\n")
        return

    _fmt_limit_up_rows(lines, t1, "T1 龙头")
    _fmt_limit_up_rows(lines, t2, "T2 题材")

    all_scored = [s for s in t1 + t2 if s.get("score")]
    if all_scored:
        top = sorted(all_scored, key=lambda x: x.get("score", 0), reverse=True)[:3]
        lines.append("**🏆 今日最强**: " + " > ".join(
            f"{s['name']}({s['code']}) #{s['score']}" for s in top
        ))
        lines.append("")

    lines.append("> 数据来源: Claude Haiku 深度分析，仅供参考。\n")
