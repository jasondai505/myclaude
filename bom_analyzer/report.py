"""BOM 产业链分析 — Markdown 报告渲染"""
from datetime import datetime
from bom_analyzer.config import REPORT_DIR
from bom_analyzer.models import BomAnalysisResult, LeaderStock


def render_report(result: BomAnalysisResult) -> str:
    lines: list[str] = []
    _frontmatter(lines, result)
    _section_overview(lines, result)
    _section_chain_tree(lines, result)
    _section_3h(lines, result)
    _section_leaders(lines, result)
    _section_moat(lines, result)
    _section_tracking(lines, result)
    return "\n".join(lines)


def _frontmatter(lines: list[str], result: BomAnalysisResult):
    up = sum(1 for s in result.segments if s.tier == "上游")
    mid = sum(1 for s in result.segments if s.tier == "中游")
    down = sum(1 for s in result.segments if s.tier == "下游")
    lines.append("---")
    lines.append(f"date: {result.date}")
    lines.append(f"industry: {result.industry}")
    lines.append("type: bom_analysis")
    lines.append(f"segments: {up}上/{mid}中/{down}下")
    lines.append(f"three_high: {len(result.high_value_segments)}")
    lines.append(f"leaders: {len(result.leaders)}")
    lines.append("---")
    lines.append("")


def _section_overview(lines: list[str], result: BomAnalysisResult):
    h3 = len(result.high_value_segments)
    ld = len(result.leaders)
    lines.append(f"# {result.industry} · BOM 产业链分析")
    lines.append("")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"> 分析日期：{result.date} | 三高赛道：{h3} 个 | 锁定龙头：{ld} 家 | 生成时间: {now}")
    lines.append("")


def _section_chain_tree(lines: list[str], result: BomAnalysisResult):
    lines.append("## 一、产业链全景图")
    lines.append("")
    for tier_name, label in [("上游", "🔺 上游 — 原材料/核心零部件"),
                              ("中游", "🔸 中游 — 制造/模组/封装"),
                              ("下游", "🔹 下游 — 应用/集成/终端")]:
        segs = [s for s in result.segments if s.tier == tier_name]
        if not segs:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for seg in segs:
            h3 = " 🔥三高" if _is_3h(seg.name, result) else ""
            lines.append(f"#### {seg.name}{h3}")
            if seg.description:
                lines.append("")
                lines.append(seg.description)
            lines.append("")
            if seg.demand_driver:
                lines.append(f"- **驱动**：{seg.demand_driver}")
            if seg.supply_status:
                lines.append(f"- **供给**：{seg.supply_status}")
            if seg.products:
                lines.append(f"- **产品**：{'、'.join(seg.products)}")
            if seg.key_companies_hint:
                lines.append(f"- **代表公司**：{'、'.join(seg.key_companies_hint)}")
            lines.append("")
    lines.append("")


def _section_3h(lines: list[str], result: BomAnalysisResult):
    lines.append("## 二、三高赛道筛选")
    lines.append("")
    if not result.high_value_segments:
        lines.append("> 未筛选出符合三高标准的赛道。")
        lines.append("")
        return
    lines.append("| 赛道 | 层级 | 增长逻辑 | 毛利率 | 壁垒 | 供需缺口 |")
    lines.append("|------|------|----------|--------|------|----------|")
    for h in result.high_value_segments:
        lines.append(f"| {h.segment_name} | {h.tier} | {h.growth_logic} | "
                     f"{h.margin_est} | {h.barrier_level} | {h.supply_gap} |")
    lines.append("")


def _section_leaders(lines: list[str], result: BomAnalysisResult):
    lines.append("## 三、龙头锁定")
    lines.append("")
    if not result.leaders:
        lines.append("> 暂无龙头数据。")
        lines.append("")
        return
    by_seg: dict[str, list[LeaderStock]] = {}
    for ldr in result.leaders:
        by_seg.setdefault(ldr.segment, []).append(ldr)

    for seg_name, stocks in by_seg.items():
        lines.append(f"### {seg_name}")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | PE | ROE% | CAGR3y% | 护城河 | 核心优势 |")
        lines.append("|------|------|------|-----|------|---------|--------|----------|")
        for s in sorted(stocks, key=lambda x: x.rank):
            moat = str(s.moat_scores.total) if s.moat_scores.total > 0 else "-"
            fix = " ⚠️" if s._hallucination_fixed else ""
            lines.append(f"| {s.rank} | {s.code} | {s.name}{fix} | {s.pe_ttm:.1f} | "
                         f"{s.roe:.1f} | {s.revenue_cagr_3y:.1f} | {moat} | {s.core_advantage} |")
        lines.append("")


def _section_moat(lines: list[str], result: BomAnalysisResult):
    lines.append("## 四、护城河评分卡")
    lines.append("")
    if not result.leaders:
        lines.append("> 暂无评分数据。")
        lines.append("")
        return
    lines.append("| 股票 | 技术 | 成本 | 规模 | 品牌 | 转换成本 | 网络 | **总分** |")
    lines.append("|------|------|------|------|------|----------|------|----------|")
    for ldr in sorted(result.leaders, key=lambda x: x.moat_scores.total, reverse=True):
        s = ldr.moat_scores
        lines.append(f"| {ldr.name}({ldr.code}) | {s.tech} | {s.cost} | {s.scale} | "
                     f"{s.brand} | {s.switch_cost} | {s.network} | **{s.total}** |")
    lines.append("")
    lines.append("> 评分 0-10：0-2 无优势 / 3-5 一般 / 6-7 显著 / 8-9 领先 / 10 垄断")
    lines.append("")


def _section_tracking(lines: list[str], result: BomAnalysisResult):
    lines.append("## 五、跟踪清单")
    lines.append("")
    if not result.leaders:
        lines.append("> 暂无标的。")
        lines.append("")
        return
    top = sorted(result.leaders, key=lambda x: x.moat_scores.total, reverse=True)[:5]
    lines.append("### 重点关注（护城河 Top 5）")
    lines.append("")
    for i, ldr in enumerate(top, 1):
        lines.append(f"{i}. **{ldr.name}**（{ldr.code}）— {ldr.segment} — "
                     f"护城河 {ldr.moat_scores.total} 分 — {ldr.core_advantage}")
    lines.append("")
    lines.append("### 后续行动")
    lines.append("")
    lines.append("- [ ] 对 Top 5 标的做深度基本面分析（财报细读+估值建模）")
    lines.append("- [ ] 跟踪产业链催化事件（政策/新品发布/产能变化）")
    lines.append("- [ ] 等待合理买点（技术面 + 估值分位共振）")
    lines.append("")


def _is_3h(seg_name: str, result: BomAnalysisResult) -> bool:
    return any(h.segment_name == seg_name for h in result.high_value_segments)


def save_report(result: BomAnalysisResult) -> str:
    md = render_report(result)
    safe = result.industry.replace("/", "-").replace("\\", "-")
    filename = f"bom_{safe}_{result.date}.md"
    filepath = REPORT_DIR / filename
    filepath.write_text(md, encoding="utf-8")
    return str(filepath)
