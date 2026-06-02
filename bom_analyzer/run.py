"""BOM 产业链分析 — CLI 入口
用法: python bom_analyzer/run.py "AI算力"
     python bom_analyzer/run.py --industry 固态电池
     python bom_analyzer/run.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from anthropic import Anthropic

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
PROJECT_ROOT = str(BASE.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, str(BASE.parent / "daily_review"))

from bom_analyzer.config import (
    BASE_DIR, PROMPT_DIR, MODEL, BASE_URL,
    STAGE1_MAX_TOKENS, STAGE2_MAX_TOKENS, LLM_TIMEOUT,
)
from bom_analyzer.models import (
    BomSegment, HighValueSegment, LeaderStock, MoatScores, BomAnalysisResult,
)
from bom_analyzer import chain_db, report


def _parse_args():
    p = argparse.ArgumentParser(description="BOM 产业链分析工具")
    p.add_argument("industry", nargs="?", type=str, help="行业名称（如 AI算力）")
    p.add_argument("--industry", "-i", dest="industry_opt", type=str, help="行业名称")
    p.add_argument("--phase", "-p", type=int, choices=[1, 2], help="只跑指定阶段")
    p.add_argument("--list", "-l", action="store_true", help="查看已分析行业")
    p.add_argument("--refresh", "-r", action="store_true", help="强制刷新")
    p.add_argument("--suggest", "-sg", action="store_true", help="推荐值得分析的赛道")
    p.add_argument("--auto", "-a", action="store_true", help="自动分析推荐 Top 1")
    p.add_argument("--daily", "-dl", action="store_true", help="批量分析今日 Top 5")
    return p.parse_args()


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def _fetch_industry_context(industry: str) -> dict[str, str]:
    result = {"industry": industry}
    try:
        import data
    except ImportError:
        for k in ("sector_ranking", "concept_heat", "industry_research"):
            result[k] = "（data 模块不可用）"
        return result

    try:
        ranking = data.fetch_industry_ranking()
        if ranking and ranking.get("all"):
            top = ranking["all"][:15]
            result["sector_ranking"] = "\n".join(
                f"{r['name']} 涨{r.get('change_pct',0):.1f}% 成交{r.get('turnover_yi',0):.0f}亿"
                for r in top)
        else:
            result["sector_ranking"] = "（暂无数据）"
    except Exception as e:
        result["sector_ranking"] = f"（获取失败: {e}）"

    try:
        heat = data.fetch_concept_heat(top_n=10)
        if heat is not None and not heat.empty:
            result["concept_heat"] = "\n".join(
                f"{r['名称']} 涨{r['涨跌幅']:.1f}% 净流入{r.get('主力净流入-净额',0):.0f}万"
                for _, r in heat.iterrows())
        else:
            result["concept_heat"] = "（暂无数据）"
    except Exception as e:
        result["concept_heat"] = f"（获取失败: {e}）"

    try:
        today = date.today().isoformat()
        research = data.fetch_industry_research(today, today)
        if research is not None and not research.empty:
            top = research.head(5)
            result["industry_research"] = "\n".join(
                f"{r['title']} — {r['org']}" for _, r in top.iterrows())
        else:
            result["industry_research"] = "（近期无行业研报）"
    except Exception as e:
        result["industry_research"] = f"（获取失败: {e}）"

    return result


def _fetch_stock_financials(segments: list[dict]) -> str:
    try:
        import data
    except ImportError:
        return json.dumps({"error": "data module unavailable"}, ensure_ascii=False)

    all_companies: list[str] = []
    for seg in segments:
        all_companies.extend(seg.get("representative_companies", []))

    if not all_companies:
        return json.dumps(
            {"info": "未从 Stage 1 提取到代表公司，请 LLM 基于行业知识自行判断"},
            ensure_ascii=False)

    candidates: list[dict] = []
    try:
        stock_list = data.fetch_stock_list_sina()
        if stock_list is not None and not stock_list.empty:
            for company in all_companies:
                matches = stock_list[
                    stock_list["name"].str.contains(company[:4], na=False)]
                for _, m in matches.head(3).iterrows():
                    candidates.append(
                        {"code": m["code"], "name": m["name"], "matched_from": company})
    except Exception:
        pass

    codes = list({c["code"] for c in candidates})[:30]
    financials: dict[str, dict] = {}
    if codes:
        try:
            quotes = data.fetch_stock_quotes(codes, batch_size=30)
            for code, q in quotes.items():
                financials[code] = {
                    "name": q.get("name", ""),
                    "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
                    "pb": round(q.get("pb", 0) or 0, 1),
                    "mcap_yi": round(q.get("mcap_yi", 0) or 0),
                }
        except Exception:
            pass
        for code in codes:
            try:
                eps = data.fetch_eps_forecast(code)
                if eps and code in financials:
                    financials[code]["eps_forecast"] = eps[:3]
            except Exception:
                pass

    return json.dumps(financials, ensure_ascii=False, indent=2)


def _call_llm(prompt: str, max_tokens: int) -> str:
    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN 未设置")
    client = Anthropic(api_key=api_key, base_url=BASE_URL)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "disabled"},
        timeout=LLM_TIMEOUT,
    )
    parts = []
    for block in resp.content:
        if hasattr(block, "text") and block.text:
            parts.append(block.text)
    return "\n".join(parts)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    m_brace = re.search(r"\{.*\}", text, re.DOTALL)
    if m_brace:
        text = m_brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = text.replace("\n", " ").replace("\r", " ")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(f"  [WARN] JSON 解析失败，原始输出前 500 字符:\n{text[:500]}")
            return None


def _build_result(industry: str, today: str,
                  s1: dict, s2: dict | None) -> BomAnalysisResult:
    result = BomAnalysisResult(industry=industry, date=today)
    result.stage1_json = s1
    result.stage2_json = s2

    for tier_key, tier_name in [("upstream", "上游"), ("midstream", "中游"),
                                 ("downstream", "下游")]:
        for seg_data in s1.get(tier_key, []):
            seg = BomSegment(
                name=seg_data.get("name", ""), tier=tier_name,
                description=seg_data.get("description", ""),
                products=seg_data.get("products", []),
                demand_driver=seg_data.get("demand_driver", ""),
                supply_status=seg_data.get("supply_status", ""),
                key_companies_hint=seg_data.get("representative_companies", []),
            )
            result.segments.append(seg)
            if seg_data.get("is_3h"):
                result.high_value_segments.append(HighValueSegment(
                    segment_name=seg_data.get("name", ""), tier=tier_name,
                    growth_logic=seg_data.get("growth_logic", ""),
                    margin_est=seg_data.get("margin_est", ""),
                    barrier_level=seg_data.get("barrier_level", ""),
                    supply_gap=seg_data.get("supply_gap", ""),
                ))

    if s2:
        for lg in s2.get("leaders", []):
            for sd in lg.get("stocks", []):
                ms = sd.get("moat_scores", {})
                result.leaders.append(LeaderStock(
                    code=sd.get("code", ""), name=sd.get("name", ""),
                    segment=lg.get("segment", ""), rank=sd.get("rank", 0),
                    moat_scores=MoatScores(
                        tech=ms.get("tech", 0), cost=ms.get("cost", 0),
                        scale=ms.get("scale", 0), brand=ms.get("brand", 0),
                        switch_cost=ms.get("switch_cost", 0),
                        network=ms.get("network", 0)),
                    core_advantage=sd.get("core_advantage", ""),
                    risk_note=sd.get("risk_note", ""),
                    pe_ttm=sd.get("pe_ttm") or 0, roe=sd.get("roe") or 0,
                    revenue_cagr_3y=sd.get("revenue_cagr_3y") or 0,
                ))
    return result


def _enrich_financials(leaders: list[LeaderStock]):
    """用真实 API 回填 PE/ROE/营收CAGR，同时校验代码-名称。"""
    codes = list({ldr.code for ldr in leaders if ldr.code})
    if not codes:
        return

    try:
        import data
    except ImportError:
        print("  [WARN] data 模块不可用，跳过财务数据补全")
        return

    # 1. 批量拉行情（PE/PB/市值 + 名称校验）
    print("  [Enrich] 拉取实时行情...")
    try:
        quotes = data.fetch_stock_quotes(codes, batch_size=30)
    except Exception as e:
        print(f"  [WARN] 行情获取失败: {e}")
        quotes = {}

    fixed = 0
    for ldr in leaders:
        q = quotes.get(ldr.code) if quotes else None
        if q is None:
            continue
        real_name = q.get("name", "")
        if real_name and real_name != ldr.name:
            print(f"  [FIX] {ldr.name}({ldr.code}) → {real_name}({ldr.code})")
            ldr.name = real_name
            ldr._hallucination_fixed = True
            fixed += 1
        if ldr.pe_ttm == 0:
            ldr.pe_ttm = round(q.get("pe_ttm", 0) or 0, 1)

    if fixed:
        print(f"  共修正 {fixed} 处代码-名称不匹配")

    # 2. 逐股拉财务指标（理杏仁：ROE/毛利率/营收增速）
    print("  [Enrich] 拉取财务指标（ROE/毛利率/营收增速）...")
    try:
        fin_data = data.fetch_financial_indicators_lixinger(codes)
    except Exception as e:
        print(f"  [WARN] 财务指标获取失败: {e}")
        fin_data = {}

    for ldr in leaders:
        records = fin_data.get(ldr.code, [])
        if not records:
            continue
        latest = records[0]
        if ldr.roe == 0 and latest.get("roe"):
            ldr.roe = round(latest["roe"], 1)
        rev_yoy_vals = [r.get("revenue_yoy") for r in records[:3]
                       if r.get("revenue_yoy") is not None]
        if rev_yoy_vals and ldr.revenue_cagr_3y == 0:
            ldr.revenue_cagr_3y = round(sum(rev_yoy_vals) / len(rev_yoy_vals), 1)

    # 3. 逐股拉 EPS 预测（补充 CAGR 参考）
    print("  [Enrich] 拉取 EPS 一致预期...")
    for ldr in leaders:
        try:
            eps_list = data.fetch_eps_forecast(ldr.code)
            if eps_list and len(eps_list) >= 2:
                eps_cur = eps_list[0].get("eps", 0) or 0
                eps_next = eps_list[1].get("eps", 0) or 0
                if eps_cur > 0 and eps_next > 0 and ldr.revenue_cagr_3y == 0:
                    eps_growth = (eps_next / eps_cur - 1) * 100
                    ldr.revenue_cagr_3y = round(eps_growth, 1)
        except Exception:
            pass

    filled_pe = sum(1 for l in leaders if l.pe_ttm and l.pe_ttm > 0)
    filled_roe = sum(1 for l in leaders if l.roe and l.roe > 0)
    filled_cagr = sum(1 for l in leaders if l.revenue_cagr_3y and l.revenue_cagr_3y > 0)
    print(f"  回填完成: PE {filled_pe}/{len(leaders)}, "
          f"ROE {filled_roe}/{len(leaders)}, CAGR {filled_cagr}/{len(leaders)}")


def _suggest_industries(today: str) -> list[dict]:
    """四维度评分推荐值得分析的赛道。"""
    try:
        import data
    except ImportError:
        print("data 模块不可用")
        return []

    print("  拉取行业排名数据...")
    try:
        ranking = data.fetch_industry_ranking()
        rows = ranking.get("all", [])
    except Exception as e:
        print(f"  获取失败: {e}")
        return []

    if not rows:
        print("  暂无行业数据")
        return []

    # 前两日快照（持续性加分）
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    day_before = (date.today() - timedelta(days=2)).isoformat()
    chain_db.init_db()
    yest_snap = chain_db.query_snapshot(yesterday)
    dbf_snap = chain_db.query_snapshot(day_before)

    # 归一化参数
    n = len(rows)
    max_turnover = max(r["turnover_yi"] for r in rows) or 1
    chg_pcts = [r["change_pct"] for r in rows]
    min_chg = min(chg_pcts)
    max_chg = max(chg_pcts)
    chg_range = (max_chg - min_chg) or 1

    scored = []
    for r in rows:
        # 价格强度 (0-100)：涨跌幅在全局中的位置
        price_score = (r["change_pct"] - min_chg) / chg_range * 100

        # 资金热度 (0-100)：成交额相对最大成交额
        turnover_score = (r["turnover_yi"] / max_turnover) * 100

        # 上涨广度 (0-100)：上涨家数占比
        total_stocks = r["up_count"] + r["down_count"]
        breadth = r["up_count"] / total_stocks if total_stocks > 0 else 0
        breadth_score = breadth * 100

        # 持续性加分 (0-100)：近两日连续上榜
        persistence = 0
        if yest_snap and r["name"] in yest_snap:
            persistence += max(0, 100 - yest_snap[r["name"]]["rank"] * 3)
        if dbf_snap and r["name"] in dbf_snap:
            persistence += max(0, 100 - dbf_snap[r["name"]]["rank"] * 3)
        persistence = min(100, persistence)

        total = (price_score * 0.20 + turnover_score * 0.35 +
                 breadth_score * 0.30 + persistence * 0.15)

        # 板块容量加分：成交额 > 1000亿的板块标的丰富，更适合 BOM 分析
        if r["turnover_yi"] > 1000:
            total += 5

        scored.append({
            "name": r["name"],
            "rank": r["rank"],
            "change_pct": r["change_pct"],
            "turnover_yi": r["turnover_yi"],
            "up_count": r["up_count"],
            "down_count": r["down_count"],
            "leader": r.get("leader", ""),
            "score": round(total, 1),
            "price_score": round(price_score, 1),
            "turnover_score": round(turnover_score, 1),
            "breadth_score": round(breadth_score, 1),
            "persistence": round(persistence, 1),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 保存今日快照
    chain_db.save_snapshot(today, scored[:30])

    return scored


def _print_suggest(scored: list[dict], top_n: int = 8):
    """打印推荐赛道表格。"""
    print(f"\n{'='*80}")
    print(f"  今日赛道推荐（资金35% + 广度30% + 价格20% + 持续性15% + 容量加分）")
    print(f"{'='*80}")
    print(f"{'排名':<4} {'赛道':<16} {'涨跌%':<8} {'成交(亿)':<10} "
          f"{'上涨':<6} {'评分':<7} {'领涨股':<10}")
    print("-" * 80)
    for r in scored[:top_n]:
        up_str = f"{r['up_count']}/{r['up_count']+r['down_count']}"
        print(f"{r['rank']:<4} {r['name']:<16} {r['change_pct']:>+6.1f}%  "
              f"{r['turnover_yi']:>8.0f}  {up_str:<6} {r['score']:>5.1f}  "
              f"{r['leader']:<10}")
    print("-" * 80)
    if scored:
        top = scored[0]
        print(f"\n  🥇 推荐: {top['name']}（{top['score']}分）")
        print(f"     涨跌幅 {top['change_pct']:+.1f}% | 成交 {top['turnover_yi']:.0f}亿 "
              f"| 上涨 {top['up_count']}/{top['up_count']+top['down_count']}")
        print(f"\n  运行分析: python bom_analyzer/run.py \"{top['name']}\"")
        print(f"  一键分析: python bom_analyzer/run.py --auto")


def run_stage1(industry: str, today: str, refresh: bool = False) -> dict | None:
    if not refresh:
        cached = chain_db.query_industry(industry)
        if cached.get("segments"):
            print(f"  [CACHE] 「{industry}」已有缓存，--refresh 强制重跑")
            return None
    print("  [Phase 0] 获取行业数据...")
    ctx = _fetch_industry_context(industry)
    print("  [Stage 1] LLM 拆产业链 + 筛三高...")
    tpl = (PROMPT_DIR / "stage1_chain.txt").read_text(encoding="utf-8")
    prompt = (tpl
        .replace("%%INDUSTRY%%", ctx["industry"])
        .replace("%%SECTOR_RANKING%%", ctx.get("sector_ranking", "（暂无）"))
        .replace("%%CONCEPT_HEAT%%", ctx.get("concept_heat", "（暂无）"))
        .replace("%%INDUSTRY_RESEARCH%%", ctx.get("industry_research", "（暂无）")))
    text = _call_llm(prompt, STAGE1_MAX_TOKENS)
    data = _extract_json(text)
    if data is None:
        print("  [ERROR] Stage 1 JSON 解析失败")
        return None
    up = len(data.get("upstream", []))
    mid = len(data.get("midstream", []))
    down = len(data.get("downstream", []))
    print(f"  上游: {up} 环节, 中游: {mid} 环节, 下游: {down} 环节")
    return data


def run_stage2(industry: str, today: str, s1: dict) -> dict | None:
    all_segs = []
    for tk in ("upstream", "midstream", "downstream"):
        for seg in s1.get(tk, []):
            seg_with_tier = dict(seg)
            seg_with_tier["tier"] = {"upstream": "上游", "midstream": "中游",
                                      "downstream": "下游"}[tk]
            all_segs.append(seg_with_tier)
    h3 = [s for s in all_segs if s.get("is_3h")]
    if not h3:
        print("  [WARN] 未发现三高赛道，对所有环节做龙头筛选")
        h3 = all_segs
    print(f"  [Phase 1] 拉取 {len(h3)} 个赛道候选股数据...")
    financials = _fetch_stock_financials(h3)
    print("  [Stage 2] LLM 锁龙头 + 验护城河...")
    s1_str = json.dumps(s1, ensure_ascii=False, indent=2)
    tpl = (PROMPT_DIR / "stage2_leaders.txt").read_text(encoding="utf-8")
    prompt = (tpl
        .replace("%%STAGE1_RESULT%%", s1_str)
        .replace("%%STOCK_FINANCIALS%%", financials))
    text = _call_llm(prompt, STAGE2_MAX_TOKENS)
    data = _extract_json(text)
    if data is None:
        print("  [ERROR] Stage 2 JSON 解析失败")
        return None
    total = sum(len(g.get("stocks", [])) for g in data.get("leaders", []))
    print(f"  赛道: {len(data.get('leaders',[]))} 个, 龙头: {total} 家")
    return data


def run_full(industry: str, today: str, refresh: bool = False):
    print(f"\n{'='*60}")
    print(f"  BOM 产业链分析: {industry}")
    print(f"  日期: {today}")
    print(f"{'='*60}\n")
    s1 = run_stage1(industry, today, refresh)
    if s1 is None:
        return
    s2 = run_stage2(industry, today, s1)
    if s2 is None:
        return
    result = _build_result(industry, today, s1, s2)
    _enrich_financials(result.leaders)
    print("\n  [Save] 写入数据库...")
    chain_db.init_db()
    flat_segs = []
    for tk in ("upstream", "midstream", "downstream"):
        for seg in s1.get(tk, []):
            sc = dict(seg)
            sc["tier"] = {"upstream": "上游", "midstream": "中游",
                          "downstream": "下游"}[tk]
            flat_segs.append(sc)
    ids = chain_db.save_chain(industry, flat_segs)
    if s2:
        for lg in s2.get("leaders", []):
            seg_name = lg.get("segment", "")
            match = next((cid for cid, seg in zip(ids, flat_segs)
                          if seg.get("name") == seg_name), ids[0] if ids else None)
            if match:
                chain_db.save_leaders(match, lg.get("stocks", []))
    print("  [Report] 生成报告...")
    rp = report.save_report(result)
    print(f"\n  ✓ 报告已生成: {rp}")
    up_c = sum(1 for s in result.segments if s.tier == "上游")
    mid_c = sum(1 for s in result.segments if s.tier == "中游")
    down_c = sum(1 for s in result.segments if s.tier == "下游")
    print(f"\n{'='*60}")
    print(f"  产业链: {up_c}上游/{mid_c}中游/{down_c}下游")
    print(f"  三高赛道: {len(result.high_value_segments)} 个")
    print(f"  锁定龙头: {len(result.leaders)} 家")
    if result.leaders:
        top = sorted(result.leaders, key=lambda x: x.moat_scores.total, reverse=True)
        print(f"  Top 3: {', '.join(f'{l.name}({l.code}) {l.moat_scores.total}分' for l in top[:3])}")
    print(f"{'='*60}\n")


def run_daily(today: str):
    """批量分析今日 Top 5 赛道，跳过近 3 天已分析的。"""
    print(f"\n{'='*60}")
    print(f"  BOM 每日批量分析 — {today}")
    print(f"{'='*60}")

    scored = _suggest_industries(today)
    if not scored:
        print("  无推荐数据，退出")
        return

    recent = chain_db.recent_industries(days=3)
    if recent:
        print(f"  近 3 天已分析: {', '.join(sorted(recent))}")

    to_analyze = []
    for r in scored:
        if r["name"] not in recent:
            to_analyze.append(r["name"])
        if len(to_analyze) >= 5:
            break

    if not to_analyze:
        print("  Top 5 赛道近 3 天均已分析，跳过")
        return

    print(f"  待分析 ({len(to_analyze)}): {', '.join(to_analyze)}")
    print()

    for i, name in enumerate(to_analyze, 1):
        print(f"\n  [{i}/{len(to_analyze)}] {name}")
        try:
            run_full(name, today, refresh=False)
        except Exception as e:
            print(f"  [ERROR] {name} 分析失败: {e}")

    print(f"\n{'='*60}")
    print(f"  每日批量分析完成: {len(to_analyze)} 个赛道")
    print(f"{'='*60}\n")


def main():
    args = _parse_args()
    industry = args.industry or args.industry_opt
    today = date.today().isoformat()
    chain_db.init_db()

    if args.daily:
        run_daily(today)
        return

    if args.suggest or args.auto:
        scored = _suggest_industries(today)
        if not scored:
            print("暂无推荐数据")
            return
        _print_suggest(scored)
        if args.auto:
            top_name = scored[0]["name"]
            print(f"\n  🚀 自动分析: {top_name}\n")
            run_full(top_name, today, refresh=False)
        return

    if args.list:
        industries = chain_db.list_industries()
        if industries:
            print("已分析行业:")
            for ind in industries:
                info = chain_db.query_industry(ind)
                print(f"  - {ind} ({len(info.get('segments',[]))} 环节, "
                      f"{len(info.get('leaders',[]))} 龙头)")
        else:
            print("暂无已分析行业。运行: python bom_analyzer/run.py \"AI算力\"")
        return

    if not industry:
        print("用法: python bom_analyzer/run.py \"AI算力\"")
        print("      python bom_analyzer/run.py --industry 固态电池")
        print("      python bom_analyzer/run.py --list")
        return

    if args.refresh:
        chain_db.clear_industry(industry)

    if args.phase == 1:
        s1 = run_stage1(industry, today, args.refresh)
        if s1:
            print(json.dumps(s1, ensure_ascii=False, indent=2))
    elif args.phase == 2:
        s1 = run_stage1(industry, today, args.refresh)
        if s1:
            s2 = run_stage2(industry, today, s1)
            if s2:
                print(json.dumps(s2, ensure_ascii=False, indent=2))
    else:
        run_full(industry, today, args.refresh)


if __name__ == "__main__":
    main()
