"""报告渲染共享辅助 — report.py 和 report_sections.py 共用，避免循环导入"""
from engine import rate_theme


def _fmt_amount(v):
    if v >= 10000:
        return f"{v/10000:.1f}亿"
    return f"{v:.0f}万"


def _fmt_5d(v):
    return f"{v:+.2f}%" if v is not None else "—"


def _cell(s):
    return str(s).replace("|", "/").replace("\n", " ").strip() or "—"


def _render_10d_row(lines, history, label, key):
    vals = []
    for h in history:
        v = h.get(key)
        vals.append("—" if v is None else str(v))
    lines.append(f"| {label} | " + " | ".join(vals) + " |")


def _render_classified_table(lines, title, stocks):
    if not stocks:
        return
    lines.append(f"#### {title}（{len(stocks)}只）\n")
    has_label = any("label" in s for s in stocks)
    if has_label:
        lines.append("| 代码 | 名称 | 类型 | 净分 | 涨停原因 |")
        lines.append("|------|------|------|:----:|----------|")
        for s in sorted(stocks, key=lambda x: (-(x.get("net_score", 0)), -x.get("amount", 0))):
            reason = s["reason"].replace("+", "、") if s.get("reason") else ""
            lbl = s.get("label", "")
            net = s.get("net_score", 0)
            lines.append(f"| {s['code']} | {s['name']} | {lbl} | {net:+d} | {reason} |")
    else:
        lines.append("| 代码 | 名称 | 涨停原因 |")
        lines.append("|------|------|----------|")
        for s in sorted(stocks, key=lambda x: x.get("amount", 0), reverse=True):
            reason = s["reason"].replace("+", "、") if s.get("reason") else ""
            lines.append(f"| {s['code']} | {s['name']} | {reason} |")
    lines.append("")


def _theme_block_amount_summary(stocks, zt_set, hot100_set):
    zt_w = nonzt_w = top100_w = total_w = 0.0
    for s in stocks:
        amt = s.get("amount_wan", 0) or 0
        total_w += amt
        chg = s.get("chg", 0) or 0
        if s["code"] in zt_set or chg >= 9.5:
            zt_w += amt
        else:
            nonzt_w += amt
        if s["code"] in hot100_set:
            top100_w += amt
    parts = [
        f"涨停 {_fmt_amount(zt_w)}",
        f"非涨停 {_fmt_amount(nonzt_w)}",
        f"人气100 {_fmt_amount(top100_w)}",
        f"合计 {_fmt_amount(total_w)}",
    ]
    line = "- 板块成交（明细汇总）: " + " / ".join(parts)
    if total_w > 0 and total_w < 10000:
        line += " ⚠️板块体量太小(<1亿)"
    return line


def _render_theme_block(lines, t, stocks, narrative_labels, level_icons, zt_pool, hot100_set, theme_pool_lookup):
    n_label = narrative_labels.get(t.get("narrative", ""), "")
    label, score = rate_theme(t)
    alpha = t.get("alpha_label", "")
    driver = t.get("driver", "")
    cons = t.get("consecutive_days", 0)
    lv = level_icons.get(t.get("level", 0), "")

    header = f"**{t['theme']}** [{lv}] {n_label}"
    if cons > 0:
        header += f" | 连续{cons}天"
    header += f" | 评分:{score}/10 {label}"
    if alpha:
        header += f" | {alpha}"
    lines.append(header + "\n")
    if driver:
        lines.append(f"- 驱动: {driver}")

    if not stocks:
        lines.append("- （无个股明细）\n")
        return

    show_stocks = stocks[:12]
    if not show_stocks:
        lines.append("")
        return

    zt_set = set((zt_pool or {}).keys())
    lines.append(_theme_block_amount_summary(show_stocks, zt_set, hot100_set))
    lines.append("")
    _zt = zt_pool or {}
    lines.append("| 标的 | 代码 | 标签 | 来源 | 当日% | 涨停时间 | 连板 | 10日% | 5日% | 成交额 | F | E | V | 备注 |")
    lines.append("|------|------|:----:|:----:|------:|:--------:|:----:|------:|-----:|-------:|--:|--:|--:|------|")
    for s in show_stocks:
        chg5_str = f"{s['chg5']:+.1f}%" if s.get("chg5") is not None else "—"
        r10_str = f"{s['r10']:+.1f}%" if s.get("r10") is not None else "—"
        amt_str = _fmt_amount(s.get("amount_wan", 0))
        lbl = s.get("label", "")
        src_str = "".join(s.get("sources", []) or [])
        reason = s.get("reason", "").replace("+", "/")
        zt = _zt.get(s["code"], {})
        zt_time = zt.get("first_time", "")
        cb = zt.get("consecutive_boards", 0)
        cb_str = f"{cb}板" if cb else ""
        f_str = e_str = v_str = "-"
        p = theme_pool_lookup.get(s["code"])
        if p:
            fev = p.get("fev") or {}
            if fev.get("f_score") is not None:
                f_str = str(fev["f_score"])
            if fev.get("e_score") is not None:
                e_str = str(fev["e_score"])
            if fev.get("v_score") is not None:
                v_str = str(fev["v_score"])
        lines.append(
            f"| {s['name']} | {s['code']} | {lbl} | {src_str} "
            f"| {s['chg']:+.1f}% | {zt_time} | {cb_str} "
            f"| {r10_str} | {chg5_str} "
            f"| {amt_str} | {f_str} | {e_str} | {v_str} | {reason} |"
        )
    lines.append("")


def _render_focus_table(lines, items, max_n=15):
    lines.append("| # | 代码 | 名称 | 综合分 | 建议 | 人气# | FEV | F | E | V | 连板 | 当日% | 板块 | 催化 | 技术 | 风险 | 来源 | 核心逻辑 |")
    lines.append("|--:|------|------|------:|:----:|------:|----:|--:|--:|--:|:----:|------:|:----:|:----:|:----:|:----:|:----:|----------|")
    for i, s in enumerate(items[:max_n], 1):
        comp = s.get("composite", {})
        sc = comp.get("scores", {})
        rank_str = str(s.get("hot_rank", "")) if s.get("hot_rank") else "-"
        fev_total = s.get("fev_total", 0)
        fev_str = f"{fev_total}({fev_total/30*100:.0f}%)" if fev_total else "-"
        fev = s.get("fev") or {}
        f_str = str(fev.get("f_score", "")) if fev else "-"
        e_str = str(fev.get("e_score", "")) if fev else "-"
        v_str = str(fev.get("v_score", "")) if fev else "-"
        boards = s.get("zt_boards", 0)
        board_str = f"{boards}板" if boards else "-"
        chg = s.get("change_pct", 0)
        src = "+".join(s.get("source", []))
        logic_parts = []
        if s.get("concept_tags"):
            logic_parts.append("/".join(s["concept_tags"][:2]))
        if s.get("pop_tag"):
            logic_parts.append(s["pop_tag"])
        if s.get("research_summary"):
            logic_parts.append(s["research_summary"])
        if s.get("lhb_summary"):
            logic_parts.append(s["lhb_summary"])
        if s.get("zsxq_mentions", 0) > 0:
            logic_parts.append(f"星球{s['zsxq_mentions']}次")
        logic = " | ".join(logic_parts) if logic_parts else ""
        lines.append(
            f"| {i} | {s.get('code','')} | {s.get('name','')} "
            f"| {comp.get('total', 0)} | {comp.get('advice', '')} "
            f"| {rank_str} | {fev_str} | {f_str} | {e_str} | {v_str} | {board_str} "
            f"| {chg:+.1f}% "
            f"| {sc.get('sector', 0)} | {sc.get('catalyst', 0)} "
            f"| {sc.get('tech', 0)} | {sc.get('risk', 0)} "
            f"| {src} | {logic} |"
        )
    lines.append("")
