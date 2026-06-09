"""基本面研报自动采集 — 采集 + 存储 + 报告

数据源:
  stock_research_report_em  — 个股研报（评级/EPS/PE/机构/PDF）
  stock_profit_forecast_ths — 一致预期EPS统计
  stock_comment_em          — 全市场综合得分/机构参与度
"""
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
from tqdm import tqdm

from config import UA, RESEARCH_CONFIG, WATCHLIST, REPORT_DIR
import store


# ============================================================
# 数据采集
# ============================================================

def _get_target_codes() -> list[str]:
    """目标池 = WATCHLIST ∪ 扫描器最新TOP30"""
    codes = set(WATCHLIST)
    scan_codes = store.load_latest_scan_codes()
    codes.update(scan_codes)
    return sorted(codes)


def fetch_research_reports(code: str, days: int = 30) -> list[dict]:
    """拉取个股研报列表（最近N天）"""
    try:
        import akshare as ak
        from daily_review.data import _run_with_timeout
        df = _run_with_timeout(lambda: ak.stock_research_report_em(symbol=code), 30, default=None)
        if df is None or df.empty:
            return []

        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        reports = []
        for _, row in df.iterrows():
            report_date = str(row.get("日期", ""))[:10]
            if report_date < cutoff:
                continue

            eps_cols = [c for c in df.columns if "盈利预测-收益" in c]
            pe_cols = [c for c in df.columns if "盈利预测-市盈率" in c]
            growth_cols = [c for c in df.columns if "盈利预测-增长率" in c]

            eps_values = []
            pe_values = []
            for ec in eps_cols[:3]:
                v = row.get(ec)
                eps_values.append(float(v) if v and str(v) != "nan" else None)
            for pc in pe_cols[:3]:
                v = row.get(pc)
                pe_values.append(float(v) if v and str(v) != "nan" else None)

            target_price = None
            if eps_values and pe_values and eps_values[0] and pe_values[0]:
                target_price = round(eps_values[0] * pe_values[0], 2)

            reports.append({
                "code": code,
                "name": str(row.get("股票简称", "")),
                "title": str(row.get("报告名称", "")),
                "rating": str(row.get("东财评级", "")),
                "institution": str(row.get("机构", "")),
                "report_date": report_date,
                "eps_y1": eps_values[0] if eps_values else None,
                "eps_y2": eps_values[1] if len(eps_values) > 1 else None,
                "eps_y3": eps_values[2] if len(eps_values) > 2 else None,
                "pe_y1": pe_values[0] if pe_values else None,
                "target_price": target_price,
                "industry": str(row.get("行业", "")),
                "pdf_url": str(row.get("报告PDF链接", "")),
            })
        return reports
    except Exception as e:
        print(f"  [WARN] 研报获取失败 {code}: {e}")
        return []


def fetch_consensus_eps(code: str) -> dict | None:
    """一致预期EPS统计"""
    try:
        import akshare as ak
        df = ak.stock_profit_forecast_ths(symbol=code)
        if df is None or df.empty:
            return None
        result = {"code": code, "forecasts": []}
        for _, row in df.iterrows():
            result["forecasts"].append({
                "year": str(row.get("年度", "")),
                "eps_avg": row.get("均值"),
                "eps_max": row.get("最大值"),
                "eps_min": row.get("最小值"),
                "inst_count": row.get("预测机构数"),
            })
        if result["forecasts"]:
            result["inst_count"] = result["forecasts"][0].get("inst_count", 0)
            result["eps_avg_y1"] = result["forecasts"][0].get("eps_avg")
            if len(result["forecasts"]) > 1:
                result["eps_avg_y2"] = result["forecasts"][1].get("eps_avg")
        return result
    except Exception:
        return None


def fetch_market_comment() -> dict[str, dict]:
    """全市场综合评分/机构参与度"""
    try:
        import akshare as ak
        df = ak.stock_comment_em()
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            result[code] = {
                "score": row.get("综合得分"),
                "inst_participation": row.get("机构参与度"),
                "attention": row.get("关注指数"),
                "rank": row.get("目前排名"),
            }
        return result
    except Exception as e:
        print(f"  [WARN] 全市场评分获取失败: {e}")
        return {}


# ============================================================
# 分析与聚合
# ============================================================

def aggregate_reports(code: str, reports: list[dict]) -> dict:
    """聚合同一股票的研报数据"""
    if not reports:
        return {"code": code, "count": 0}

    ratings = Counter(r["rating"] for r in reports if r["rating"])
    latest = reports[0]

    target_prices = [r["target_price"] for r in reports if r["target_price"]]
    avg_target = sum(target_prices) / len(target_prices) if target_prices else None

    return {
        "code": code,
        "name": latest["name"],
        "count": len(reports),
        "latest_date": latest["report_date"],
        "latest_rating": latest["rating"],
        "latest_institution": latest["institution"],
        "latest_title": latest["title"],
        "target_price": avg_target,
        "ratings": dict(ratings),
        "eps_y1": latest.get("eps_y1"),
        "eps_y2": latest.get("eps_y2"),
        "industry": latest.get("industry", ""),
    }


# ============================================================
# 报告生成
# ============================================================

def render_research_report(
    trade_date: str,
    aggregated: list[dict],
    consensus_map: dict,
    comment_map: dict,
    current_prices: dict,
    no_coverage: list[str],
    stats: dict,
) -> str:
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 研报采集报告 — {trade_date}")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"> 目标池: {stats.get('total', 0)} 只 | "
                 f"有研报: {stats.get('with_reports', 0)} 只 | "
                 f"采集研报: {stats.get('report_count', 0)} 篇 | "
                 f"耗时: {stats.get('elapsed', 0):.0f}s\n")

    # === 一、评级概览 ===
    with_target = [a for a in aggregated if a.get("target_price")]
    for a in with_target:
        price = current_prices.get(a["code"], {}).get("price", 0)
        if price and a["target_price"]:
            a["upside"] = round((a["target_price"] / price - 1) * 100, 1)
        else:
            a["upside"] = None
    with_target.sort(key=lambda x: x.get("upside") or -999, reverse=True)

    lines.append("## 一、评级概览\n")
    lines.append("| 代码 | 名称 | 评级 | 研报数 | 平均目标价 | 当前价 | 空间% |")
    lines.append("|------|------|------|------:|----------:|------:|------:|")
    for a in with_target[:40]:
        price = current_prices.get(a["code"], {}).get("price", 0)
        upside = a.get("upside")
        up_str = f"+{upside:.1f}%" if upside and upside > 0 else (
            f"{upside:.1f}%" if upside else "N/A")
        lines.append(
            f"| {a['code']} | {a['name']} | {a.get('latest_rating', '')} "
            f"| {a['count']} | {a['target_price']:.2f} "
            f"| {price:.2f} | {up_str} |"
        )
    lines.append("")

    # === 二、近期重点研报 ===
    top_stocks = sorted(aggregated, key=lambda x: x["count"], reverse=True)
    top_stocks = [a for a in top_stocks if a["count"] > 0][:20]

    if top_stocks:
        lines.append("## 二、近期重点研报\n")
        for a in top_stocks:
            price = current_prices.get(a["code"], {}).get("price", 0)
            lines.append(f"### {a['name']}（{a['code']}）\n")

            lines.append(
                f"- **最新评级**: {a['latest_rating']}"
                f"（{a['latest_institution']}，{a['latest_date']}）"
            )
            if a.get("target_price") and price:
                upside = (a["target_price"] / price - 1) * 100 if price else 0
                lines.append(
                    f"- **平均目标价**: {a['target_price']:.2f}元"
                    f"（当前{price:.2f}，空间{upside:+.1f}%）"
                )

            cons = consensus_map.get(a["code"])
            if cons and cons.get("eps_avg_y1"):
                parts = []
                for fc in cons.get("forecasts", [])[:3]:
                    if fc.get("eps_avg"):
                        parts.append(f"{fc['year']}={fc['eps_avg']}")
                if parts:
                    inst = cons.get("inst_count", "?")
                    lines.append(f"- **一致预期EPS**: {' / '.join(parts)}（{inst}家机构）")

            ratings = a.get("ratings", {})
            if ratings:
                r_parts = [f"{k}{v}篇" for k, v in ratings.items()]
                lines.append(f"- **近期研报**: {a['count']}篇（{'、'.join(r_parts)}）")

            comment = comment_map.get(a["code"])
            if comment and comment.get("score"):
                lines.append(
                    f"- 综合得分: {comment['score']:.1f} | "
                    f"机构参与度: {comment.get('inst_participation', 0):.1f}%"
                )

            lines.append(f"- 最新标题: {a['latest_title']}")
            lines.append("")

    # === 三、评级变动提醒 ===
    changes = _detect_rating_changes(aggregated)
    if changes:
        lines.append("## 三、评级变动提醒\n")
        for c in changes:
            lines.append(f"- {c}")
        lines.append("")

    # === 四、无研报覆盖 ===
    if no_coverage:
        lines.append("## 四、无研报覆盖\n")
        lines.append("以下自选股近30天无券商研报，需人工研究：\n")
        for code in no_coverage[:30]:
            name = current_prices.get(code, {}).get("name", code)
            lines.append(f"- {name}（{code}）")
        lines.append("")

    lines.append("---")
    lines.append("*本报告由研报采集系统自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def _detect_rating_changes(aggregated: list[dict]) -> list[str]:
    """检测评级变动（首次覆盖、多家一致看好等）"""
    changes = []
    for a in aggregated:
        if a["count"] == 0:
            continue
        ratings = a.get("ratings", {})
        buy_count = ratings.get("买入", 0) + ratings.get("增持", 0)

        if a["count"] == 1 and a.get("latest_date"):
            changes.append(f"**首次覆盖**: {a['name']}（{a['code']}）"
                          f"— {a['latest_institution']} 给予「{a['latest_rating']}」")

        if buy_count >= 3:
            changes.append(f"**多家看好**: {a['name']}（{a['code']}）"
                          f"— 近期 {buy_count} 篇买入/增持")

    return changes[:15]


# ============================================================
# 主流程
# ============================================================

def run_research(trade_date: str = None) -> str:
    """执行研报采集，返回报告路径"""
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    store.init_db()
    t0 = time.time()
    cfg = RESEARCH_CONFIG

    # Step 1: 目标池
    print("[1/5] 确定目标池...")
    target_codes = _get_target_codes()
    print(f"  ✓ {len(target_codes)} 只目标股")

    # Step 2: 全市场评分
    print("[2/5] 获取全市场评分...")
    comment_map = fetch_market_comment()
    print(f"  ✓ {len(comment_map)} 只有评分数据")

    # Step 3: 研报明细
    print(f"[3/5] 采集研报明细（{len(target_codes)} 只）...")
    all_reports: dict[str, list[dict]] = {}
    total_reports = 0
    pbar = tqdm(target_codes, desc="  研报采集", unit="只",
                bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    for code in pbar:
        reports = fetch_research_reports(code, days=cfg["lookback_days"])
        if reports:
            all_reports[code] = reports
            total_reports += len(reports)
            store.save_research_reports(reports)
        pbar.set_postfix(篇=total_reports)
        time.sleep(cfg["fetch_delay"])
    print(f"  ✓ {len(all_reports)} 只有研报，共 {total_reports} 篇")

    # Step 4: 一致预期EPS
    print("[4/5] 采集一致预期EPS...")
    consensus_map: dict[str, dict] = {}
    codes_need_eps = list(all_reports.keys())
    pbar = tqdm(codes_need_eps, desc="  EPS预测", unit="只",
                bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    for code in pbar:
        cached = store.load_valuation_cache(code, "eps_forecast", max_age_days=3)
        if cached:
            import json
            try:
                consensus_map[code] = json.loads(cached)
                continue
            except Exception:
                pass
        cons = fetch_consensus_eps(code)
        if cons:
            consensus_map[code] = cons
            import json
            store.save_valuation_cache(code, "eps_forecast", json.dumps(cons, ensure_ascii=False))
        pbar.set_postfix(hit=len(consensus_map))
        time.sleep(0.3)
    print(f"  ✓ {len(consensus_map)} 只有EPS预测")

    # Step 5: 获取当前价格 & 生成报告
    print("[5/5] 生成报告...")
    from data import fetch_stock_quotes
    price_codes = list(set(target_codes) | set(all_reports.keys()))
    current_prices = fetch_stock_quotes(price_codes)

    aggregated = []
    no_coverage = []
    for code in target_codes:
        reports = all_reports.get(code, [])
        agg = aggregate_reports(code, reports)
        if agg["count"] == 0 and code in WATCHLIST:
            name = current_prices.get(code, {}).get("name", code)
            no_coverage.append(code)
        aggregated.append(agg)

    store.save_consensus_snapshot(trade_date, aggregated, consensus_map, comment_map)

    elapsed = time.time() - t0
    stats = {
        "total": len(target_codes),
        "with_reports": len(all_reports),
        "report_count": total_reports,
        "elapsed": elapsed,
    }

    md = render_research_report(
        trade_date, aggregated, consensus_map, comment_map,
        current_prices, no_coverage, stats,
    )
    report_path = REPORT_DIR / f"research_{trade_date}.md"
    report_path.write_text(md, encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"  ✅ 研报采集完成！耗时 {elapsed:.0f}s")
    print(f"  📄 报告: {report_path}")
    print(f"  研报: {total_reports} 篇（覆盖 {len(all_reports)} 只）")
    print(f"{'='*50}")

    if aggregated:
        top = sorted([a for a in aggregated if a["count"] > 0],
                     key=lambda x: x["count"], reverse=True)
        print(f"\n研报最多 TOP5:")
        for a in top[:5]:
            print(f"  {a['name']:8s} {a['code']}  "
                  f"{a['count']}篇  {a.get('latest_rating', '')}")

    return str(report_path)
