"""行业/策略/宏观研报 LLM 分析 — 快速主题提炼"""
import json, sqlite3, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from daily_review.config import REPORT_DIR
from daily_review.roles import get_client, get_model

DB = Path(__file__).parent / "data" / "review.db"
OUT = REPORT_DIR / "feeds" / "industry_analysis_2026-06-07_14.md"

PROMPT = """你是A股行业分析师。以下是过去一周某行业的所有研报标题和机构。

请用100字以内提炼：
1. 这个行业本周的核心议题是什么？
2. 机构共识方向（看多什么？担心什么？）
3. 如果有分歧，分歧在哪？

只返回JSON：
{"industry": "行业名", "core_theme": "核心议题", "consensus": "共识方向", "divergence": "分歧(无则空)", "hot_level": "🔥/📌/👀"}"""

def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    
    # 1. Get industry report groups
    rows = conn.execute("""
        SELECT industry_name, title, institution, report_date
        FROM industry_reports
        WHERE report_subtype='industry' AND report_date >= '2026-06-07'
        ORDER BY industry_name, report_date DESC
    """).fetchall()
    
    by_industry = defaultdict(list)
    for r in rows:
        by_industry[r["industry_name"] or "其他"].append(dict(r))
    
    # 2. Get strategy/macro titles
    strategy_titles = [dict(r) for r in conn.execute("""
        SELECT title, institution, report_date FROM industry_reports
        WHERE report_subtype='strategy' AND report_date >= '2026-06-07'
        ORDER BY report_date DESC
    """).fetchall()]
    
    macro_titles = [dict(r) for r in conn.execute("""
        SELECT title, institution, report_date FROM industry_reports
        WHERE report_subtype='macro' AND report_date >= '2026-06-07'
        ORDER BY report_date DESC
    """).fetchall()]
    
    conn.close()
    
    # 3. LLM analysis: only top 10 industries by count
    top_industries = sorted(by_industry.items(), key=lambda x: -len(x[1]))[:10]
    
    client = get_client("deep", timeout=60)
    model = get_model("deep")
    
    analyses = []
    for ind_name, ind_reports in top_industries:
        titles_text = "\n".join(
            f"[{r['report_date']}] {r['institution']}: {r['title']}"
            for r in ind_reports[:10]  # max 10 per industry
        )
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
            analyses.append(j)
            print(f"  [OK] {ind_name}: {j.get('core_theme','')[:40]}")
        except Exception as e:
            print(f"  [FAIL] {ind_name}: {e}")
            analyses.append({"industry": ind_name, "core_theme": str(e), "count": len(ind_reports)})
    
    # 4. Strategy synthesis
    print("\n=== 策略研报合成 ===")
    strat_text = "\n".join(
        f"[{s['report_date']}] {s['institution']}: {s['title']}"
        for s in strategy_titles[:20]
    )
    strat_prompt = f"""你是A股策略分析师。以下是过去一周的策略研报标题。请用150字总结机构的核心观点共识和分歧。返回JSON：{{"consensus": "核心共识", "divergence": "分歧", "key_words": ["关键词1","关键词2"]}}"""
    
    try:
        resp = client.messages.create(
            model=model, max_tokens=200,
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
        "# 行业/策略/宏观研报周分析 2026-06-07 ~ 06-14",
        "",
        f"> 行业 {len(rows)} 篇 + 策略 {len(strategy_titles)} 篇 + 宏观 {len(macro_titles)} 篇",
        "",
        "## 行业热度分布",
        "",
        "| 热度 | 行业 | 篇数 | 核心议题 | 共识方向 |",
        "|:----:|------|:----:|----------|----------|",
    ]
    
    for a in analyses:
        hot = a.get("hot_level", "👀")
        buf.append(
            f"| {hot} | {a['industry']} | {a.get('count','')} | "
            f"{a.get('core_theme','')[:40]} | {a.get('consensus','')[:40]} |"
        )
    
    buf.extend([
        "",
        "## 策略共识",
        "",
        f"**共识**: {strategy_synthesis.get('consensus', '')}",
        f"**分歧**: {strategy_synthesis.get('divergence', '')}",
        f"**关键词**: {', '.join(strategy_synthesis.get('key_words', []))}",
        "",
        "## 宏观研报列表",
        "",
    ])
    for m in macro_titles[:15]:
        buf.append(f"- [{m['report_date']}] **{m['institution']}**: {m['title']}")
    
    buf.extend(["", "---", f"*自动生成，基于 eastmoney-reports 数据*"])
    
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n报告已生成: {OUT}")

if __name__ == "__main__":
    main()
