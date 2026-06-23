"""深度投研洞见 交叉分析层。

从 111 篇结构化 JSON 中提取:
  1. 主题演化时间线
  2. 预测回溯（准确度记分卡）
  3. 标的聚合（跨文章共现）
  4. 非共识全景（未兑现预期差汇总）
  5. 承重判断链
"""
from __future__ import annotations

import json, os, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SHENDU_DIR = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu"
REPORT_DIR = Path(__file__).resolve().parent / "reports" / "serenity"


def _load_all() -> list[dict]:
    articles = []
    for f in sorted(SHENDU_DIR.iterdir()):
        if not f.name.startswith("shendu_2026") or f.name.startswith("shendu__"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("thesis") or data.get("variant_perceptions"):
                articles.append(data)
        except Exception:
            pass
    articles.sort(key=lambda a: a.get("date", ""))
    return articles


def _theme_timeline(articles: list[dict]) -> str:
    """主题演化时间线。"""
    # 按产业链聚合
    chain_months = defaultdict(lambda: defaultdict(list))
    for a in articles:
        date = a.get("date", "")[:7]  # YYYY-MM
        for chain in a.get("chains_involved", []):
            chain_months[chain][date].append(a.get("title_clean", "")[:40])

    lines = ["## 1. 主题演化时间线", ""]
    lines.append("| 产业链 | 首次提及 | 累计篇数 | 活跃月份 |")
    lines.append("|--------|---------|:-------:|---------|")

    for chain in sorted(chain_months, key=lambda c: min(chain_months[c].keys()), reverse=True):
        months = sorted(chain_months[chain].keys())
        first = months[0]
        total = sum(len(v) for v in chain_months[chain].values())
        active = ", ".join(f"{m}({len(chain_months[chain][m])})" for m in months)
        lines.append(f"| {chain} | {first} | {total} | {active} |")

    return "\n".join(lines)


def _top_variant_perceptions(articles: list[dict]) -> str:
    """非共识全景 — 所有预期差汇总。"""
    all_vps = []
    for a in articles:
        for vp in a.get("variant_perceptions", []):
            all_vps.append({
                "date": a.get("date", ""),
                "article": a.get("title_clean", "")[:60],
                "consensus": vp.get("consensus", ""),
                "variant": vp.get("variant", ""),
                "confidence": vp.get("confidence", ""),
                "falsification": vp.get("falsification", ""),
            })

    lines = ["## 2. 非共识预期差全景", ""]
    lines.append(f"**总计 {len(all_vps)} 条预期差**，按置信度分布：")
    conf_dist = Counter(vp["confidence"] for vp in all_vps)
    for c in ["高", "中", "低"]:
        lines.append(f"- **{c}** 置信度: {conf_dist.get(c, 0)} 条")
    lines.append("")

    # 高置信度预期差
    lines.append("### 高置信度预期差（前 30 条）")
    lines.append("| 日期 | 文章 | 市场共识 | 非共识判断 | 证伪条件 |")
    lines.append("|------|------|---------|-----------|---------|")
    high_vps = [vp for vp in all_vps if vp["confidence"] == "高"]
    for vp in high_vps[:30]:
        lines.append(f"| {vp['date']} | {vp['article'][:30]} | {vp['consensus'][:40]} | {vp['variant'][:50]} | {vp['falsification'][:40]} |")
    lines.append("")

    return "\n".join(lines)


def _stock_aggregation(articles: list[dict]) -> str:
    """标的聚合 — 跨文章共现。"""
    code_mentions = defaultdict(lambda: {"count": 0, "names": set(), "articles": [], "tiers": Counter()})
    for a in articles:
        for v in a.get("valuation_spectrum", []):
            tier = v.get("tier", "未分类")
            for i, code in enumerate(v.get("codes", [])):
                name = (v.get("names", []) or [""])[i] if i < len(v.get("names", []) or []) else ""
                code_mentions[code]["count"] += 1
                code_mentions[code]["tiers"][tier] += 1
                if name:
                    code_mentions[code]["names"].add(name)
                code_mentions[code]["articles"].append(a.get("date", "")[:7])

    # 按提及次数排序
    sorted_codes = sorted(code_mentions.items(), key=lambda x: x[1]["count"], reverse=True)

    lines = ["## 3. 标的聚合 — 跨文章共现", ""]
    lines.append("| 代码 | 名称 | 提及次数 | 核心仓/弹性层/规避 | 涉及月份 |")
    lines.append("|------|------|:-------:|:---:|------|")

    for code, info in sorted_codes[:40]:
        names = ", ".join(sorted(info["names"])[:2])
        core = info["tiers"].get("核心仓", 0)
        elastic = info["tiers"].get("弹性层", 0)
        avoid = info["tiers"].get("规避", 0)
        tier_str = f"{core}/{elastic}/{avoid}"
        months = ", ".join(sorted(set(info["articles"])))
        lines.append(f"| {code} | {names} | {info['count']} | {tier_str} | {months} |")

    lines.append("")
    return "\n".join(lines)


def _load_bearing_judgments(articles: list[dict]) -> str:
    """承重判断链 — 按时间排列。"""
    lines = ["## 4. 承重判断链（按时间）", ""]
    lines.append("| 日期 | 文章 | 承重判断 |")
    lines.append("|------|------|---------|")

    for a in articles:
        lbj = a.get("load_bearing_judgment", "")
        if lbj:
            lines.append(f"| {a.get('date','')} | {a.get('title_clean','')[:40]} | {lbj[:100]} |")

    lines.append("")
    return "\n".join(lines)


def _risk_aggregation(articles: list[dict]) -> str:
    """风险信号聚合。"""
    risk_counter = Counter()
    risk_details = defaultdict(list)
    for a in articles:
        for r in a.get("risk_signals", []):
            rtype = r.get("type", "未知")
            risk_counter[rtype] += 1
            risk_details[rtype].append({
                "date": a.get("date", ""),
                "target": r.get("target", ""),
                "detail": r.get("detail", ""),
            })

    lines = ["## 5. 风险信号聚合", ""]
    lines.append("| 风险类型 | 次数 | 涉及标的 |")
    lines.append("|---------|:---:|------|")
    for rtype, count in risk_counter.most_common():
        targets = list(set(d["target"] for d in risk_details[rtype] if d["target"]))[:5]
        lines.append(f"| {rtype} | {count} | {', '.join(targets)} |")
    lines.append("")

    return "\n".join(lines)


def _theme_evolution(articles: list[dict]) -> str:
    """关键主题的演变路径。"""
    # 手动定义关键主题 → 从 themes 字段聚合
    theme_months = defaultdict(set)
    theme_articles = defaultdict(list)
    for a in articles:
        for theme in a.get("themes", []):
            theme_months[theme].add(a.get("date", "")[:7])
            theme_articles[theme].append(a.get("date", ""))

    # 选出现次数最多的主题
    top_themes = sorted(theme_articles, key=lambda t: len(theme_articles[t]), reverse=True)

    lines = ["## 6. 关键主题演变", ""]
    lines.append("| 主题 | 首次 | 最近 | 篇数 | 时间跨度 |")
    lines.append("|------|------|------|:---:|---------|")

    for theme in top_themes[:30]:
        dates = sorted(theme_articles[theme])
        first = dates[0]
        last = dates[-1]
        span_months = len(set(d[:7] for d in dates))
        lines.append(f"| {theme} | {first} | {last} | {len(dates)} | {span_months}个月 |")

    lines.append("")
    return "\n".join(lines)


def main():
    articles = _load_all()
    print(f"加载 {len(articles)} 篇结构化文章")

    sections = [
        _theme_timeline(articles),
        _top_variant_perceptions(articles),
        _stock_aggregation(articles),
        _load_bearing_judgments(articles),
        _risk_aggregation(articles),
        _theme_evolution(articles),
    ]

    report = "# 深度投研洞见 · 交叉分析报告\n\n"
    report += f"> 分析范围: {articles[0].get('date','')} ~ {articles[-1].get('date','')}\n"
    report += f"> 文章总数: {len(articles)} 篇\n"
    report += f"> 预期差总计: {sum(len(a.get('variant_perceptions',[])) for a in articles)} 条\n"
    report += f"> 标的映射: {sum(len(a.get('valuation_spectrum',[])) for a in articles)} 组\n"
    report += f"> 风险信号: {sum(len(a.get('risk_signals',[])) for a in articles)} 条\n"
    report += f"> 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    report += "---\n\n"
    report += "\n\n---\n\n".join(sections)
    report += "\n---\n*本报告由交叉分析模型自动生成，仅供参考，不构成投资建议。*"

    out_path = REPORT_DIR / "shendu_cross_analysis.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n报告已生成: {out_path}")
    print(f"共 {len(report)} 字符")


if __name__ == "__main__":
    main()
