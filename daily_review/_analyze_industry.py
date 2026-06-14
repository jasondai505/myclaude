"""行业/策略/宏观研报 LLM 分析 — 快速主题提炼"""
import json, sqlite3, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from daily_review.config import REPORT_DIR
from daily_review.roles import get_client, get_model

DB = Path(__file__).parent / "data" / "review.db"
OUT = REPORT_DIR / "feeds" / "industry_analysis_2026-05-15_06-14.md"

PROMPT = """你是A股行业分析师。以下是过去一个月某行业的所有研报标题和机构。

请用100字以内提炼：
1. 这个行业近一个月的核心议题和变化趋势是什么？
2. 机构共识方向（看多什么？担心什么？）
3. 如果有分歧，分歧在哪？

只返回JSON：
{"industry": "行业名", "core_theme": "核心议题", "consensus": "共识方向", "divergence": "分歧(无则空)", "hot_level": "🔥/📌/👀"}"""

START = "2026-05-15"
END = "2026-06-14"

STAR_EMOJI = {5: "🟡", 4: "🟢", 3: "🔵", 2: "🟠", 1: "⚫"}
STARS = {5: "★★★★★", 4: "★★★★", 3: "★★★", 2: "★★", 1: "★"}

def _industry_stars(count: int, hot_level: str, inst_count: int) -> str:
    """根据研报篇数、LLM热度、机构数计算星级(emoji前缀)。"""
    score = 0
    score += min(count * 0.8, 40)
    score += min(inst_count * 2.5, 25)
    hot_score = {"🔥": 30, "📌": 20, "👀": 10}
    score += hot_score.get(hot_level, 5)
    if count >= 30: score += 5

    if score >= 70: n = 5
    elif score >= 50: n = 4
    elif score >= 30: n = 3
    elif score >= 15: n = 2
    else: n = 1
    return f'{STAR_EMOJI[n]} **{STARS[n]}**'

def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    # 1. Get industry report groups
    rows = conn.execute("""
        SELECT industry_name, title, institution, report_date
        FROM industry_reports
        WHERE report_subtype='industry' AND report_date >= ? AND report_date <= ?
        ORDER BY industry_name, report_date DESC
    """, (START, END)).fetchall()

    by_industry = defaultdict(list)
    for r in rows:
        by_industry[r["industry_name"] or "其他"].append(dict(r))

    # 2. Get strategy/macro titles (full month)
    strategy_titles = [dict(r) for r in conn.execute("""
        SELECT title, institution, report_date FROM industry_reports
        WHERE report_subtype='strategy' AND report_date >= ? AND report_date <= ?
        ORDER BY report_date DESC
    """, (START, END)).fetchall()]

    macro_titles = [dict(r) for r in conn.execute("""
        SELECT title, institution, report_date FROM industry_reports
        WHERE report_subtype='macro' AND report_date >= ? AND report_date <= ?
        ORDER BY report_date DESC
    """, (START, END)).fetchall()]

    conn.close()

    # 3. LLM analysis: top 15 industries
    top_industries = sorted(by_industry.items(), key=lambda x: -len(x[1]))[:15]
    
    client = get_client("deep", timeout=60)
    model = get_model("deep")
    
    analyses = []
    for ind_name, ind_reports in top_industries:
        titles_text = "\n".join(
            f"[{r['report_date']}] {r['institution']}: {r['title']}"
            for r in ind_reports[:10]
        )
        inst_count = len(set(r["institution"] for r in ind_reports))
        prompt = PROMPT + f"\n\n行业: {ind_name}\n篇数: {len(ind_reports)}\n\n{titles_text}"

        try:
            resp = client.messages.create(
                model=model, max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "disabled"},
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            j = json.loads(text.strip().lstrip("```json").rstrip("```").strip())
            j["count"] = len(ind_reports)
            j["inst_count"] = inst_count
            analyses.append(j)
            print(f"  [OK] {ind_name}: {j.get('core_theme','')[:40]}")
        except Exception as e:
            print(f"  [FAIL] {ind_name}: {e}")
            analyses.append({"industry": ind_name, "core_theme": str(e), "count": len(ind_reports), "inst_count": inst_count})
    
    # 4. Strategy synthesis
    print("\n=== 策略研报合成 ===")
    strat_text = "\n".join(
        f"[{s['report_date']}] {s['institution']}: {s['title']}"
        for s in strategy_titles[:50]
    )
    strat_prompt = f"""你是A股策略分析师。以下是过去一个月的策略研报标题。请用200字总结机构的核心观点共识、分歧和变化趋势。返回JSON：{{"consensus": "核心共识", "divergence": "分歧", "trend": "趋势变化", "key_words": ["关键词1","关键词2"]}}"""
    
    try:
        resp = client.messages.create(
            model=model, max_tokens=300,
            messages=[{"role": "user", "content": strat_prompt + "\n\n" + strat_text}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        strategy_synthesis = json.loads(text.strip().lstrip("```json").rstrip("```").strip())
        print(f"  [OK] 策略: {strategy_synthesis.get('consensus','')[:50]}")
    except Exception as e:
        strategy_synthesis = {"consensus": str(e)}
        print(f"  [FAIL] 策略失败: {e}")
    
    # 5. Write report
    buf = [
        f"# 行业/策略/宏观研报月度分析 {START} ~ {END}",
        "",
        f"> 行业 {len(rows)} 篇 + 策略 {len(strategy_titles)} 篇 + 宏观 {len(macro_titles)} 篇 | 覆盖 {len(by_industry)} 个行业",
        "",
        "## 行业热度分布",
        "",
        "| 评级 | 行业 | 篇数 | 机构 | 核心议题 | 共识方向 |",
        "|:----:|------|:----:|:----:|----------|----------|",
    ]

    for a in analyses:
        hot = a.get("hot_level", "👀")
        stars = _industry_stars(a.get("count", 0), hot, a.get("inst_count", 0))
        buf.append(
            f"| {stars} | {a['industry']} | {a.get('count','')} | "
            f"{a.get('inst_count','')} | "
            f"{a.get('core_theme','')[:40]} | {a.get('consensus','')[:40]} |"
        )
    
    buf.extend([
        "",
        "## 策略共识",
        "",
        f"**共识**: {strategy_synthesis.get('consensus', '')}",
        f"**分歧**: {strategy_synthesis.get('divergence', '')}",
        f"**趋势**: {strategy_synthesis.get('trend', '')}",
        f"**关键词**: {', '.join(strategy_synthesis.get('key_words', []))}",
        "",
        "## 宏观研报列表",
        "",
    ])
    for m in macro_titles[:30]:
        buf.append(f"- [{m['report_date']}] **{m['institution']}**: {m['title']}")
    
    buf.extend(["", "---", f"*自动生成，基于 eastmoney-reports 数据*"])
    
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n报告已生成: {OUT}")

if __name__ == "__main__":
    main()
