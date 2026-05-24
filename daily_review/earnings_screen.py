"""盈利预测选股模型 — 全A市场CAGR+前瞻PE筛选

漏斗流程:
  全A ~5200
  → 基础过滤（ST/停牌/亏损/市值<20亿）
  → PE预筛（PE_TTM < 150）
  → 一致预期EPS筛选（CAGR>30% + 前瞻PE<30）
  → 附加：股东人数集中度 + 10日涨幅排序
"""
import json
import time
from datetime import datetime

import pandas as pd
from tqdm import tqdm

from config import UA, EARNINGS_SCREEN_CONFIG, REPORT_DIR
import store


_mootdx_client = None


def _get_mootdx():
    global _mootdx_client
    if _mootdx_client is None:
        from mootdx.quotes import Quotes
        _mootdx_client = Quotes.factory(market="std")
    return _mootdx_client


# ============================================================
# Step 1-2: 全市场行情 + 基础过滤
# ============================================================

def _fetch_tencent_quotes(codes: list[str], batch_size=50) -> dict[str, dict]:
    import requests
    results = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        symbols = []
        for c in batch:
            prefix = "sh" if c.startswith(("6",)) else "sz"
            symbols.append(f"{prefix}{c}")
        try:
            url = f"https://qt.gtimg.cn/q={','.join(symbols)}"
            r = requests.get(url, timeout=10)
            for line in r.text.strip().split(";"):
                parts = line.split("~")
                if len(parts) < 50:
                    continue
                code = parts[2]
                price = float(parts[3]) if parts[3] else 0
                pe = float(parts[39]) if parts[39] else 0
                mktcap = float(parts[45]) if parts[45] else 0
                name = parts[1]
                results[code] = {
                    "code": code, "name": name, "price": price,
                    "pe": pe, "mktcap": mktcap * 1e8,
                    "industry": "", "chg_pct": float(parts[32]) if parts[32] else 0,
                }
        except Exception:
            pass
        time.sleep(0.3)
    return results


def fetch_and_filter_market() -> list[dict]:
    from screener import fetch_universe, fetch_market_data, _parse_stock

    cfg = EARNINGS_SCREEN_CONFIG
    universe = fetch_universe()
    secids = [s["secid"] for s in universe]
    raw_items = fetch_market_data(secids, batch_size=200)

    stocks = []
    for raw in raw_items:
        s = _parse_stock(raw)
        if not s:
            continue
        if "ST" in s["name"] or "退" in s["name"]:
            continue
        if s["price"] <= 0:
            continue
        if s["pe"] <= 0:
            continue
        mktcap_yi = s["mktcap"] / 1e8 if s["mktcap"] > 1e6 else 0
        if mktcap_yi < cfg["min_mktcap_yi"]:
            continue
        if s["pe"] > cfg["max_pe_prefilter"]:
            continue
        s["mktcap_yi"] = round(mktcap_yi, 1)
        stocks.append(s)

    if stocks:
        return stocks

    print("  [INFO] EM行情不可用，使用EPS缓存+腾讯行情降级模式...")
    cached_codes = store.get_all_cached_codes("eps_forecast_v2")
    if not cached_codes:
        cached_codes = store.get_all_cached_codes("eps_forecast")
    if not cached_codes:
        return []

    quotes = _fetch_tencent_quotes(cached_codes)
    for code, q in quotes.items():
        if "ST" in q["name"] or "退" in q["name"]:
            continue
        if q["price"] <= 0 or q["pe"] <= 0:
            continue
        mktcap_yi = q["mktcap"] / 1e8 if q["mktcap"] > 1e6 else 0
        if mktcap_yi < cfg["min_mktcap_yi"]:
            continue
        if q["pe"] > cfg["max_pe_prefilter"]:
            continue
        q["mktcap_yi"] = round(mktcap_yi, 1)
        stocks.append(q)
    return stocks


# ============================================================
# Step 3: 批量拉取一致预期EPS（含缓存）
# ============================================================

def _fetch_eps_single(code: str) -> dict | None:
    cached = store.load_valuation_cache(code, "eps_forecast_v2", max_age_days=7)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass

    try:
        import akshare as ak
        df = ak.stock_profit_forecast_ths(symbol=code)
        if df is None or df.empty:
            store.save_valuation_cache(code, "eps_forecast_v2", "{}")
            return None

        forecasts = []
        for _, row in df.iterrows():
            eps_avg = row.get("均值")
            if eps_avg is None or str(eps_avg) == "nan":
                continue
            forecasts.append({
                "year": str(row.get("年度", "")),
                "eps_avg": float(eps_avg),
                "inst_count": row.get("预测机构数"),
            })

        if not forecasts:
            store.save_valuation_cache(code, "eps_forecast_v2", "{}")
            return None

        result = {"code": code, "forecasts": forecasts}
        store.save_valuation_cache(
            code, "eps_forecast_v2", json.dumps(result, ensure_ascii=False)
        )
        return result
    except Exception:
        return None


def fetch_eps_batch(codes: list[str]) -> dict[str, dict]:
    results = {}
    pbar = tqdm(codes, desc="  EPS采集", unit="只",
                bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    for code in pbar:
        had_cache = store.load_valuation_cache(code, "eps_forecast_v2", max_age_days=7) is not None
        eps = _fetch_eps_single(code)
        if eps and eps.get("forecasts"):
            results[code] = eps
        pbar.set_postfix(hit=len(results))
        if not had_cache:
            time.sleep(0.3)
    return results


# ============================================================
# Step 4: CAGR + 前瞻PE 筛选
# ============================================================

def filter_by_earnings(stocks: list[dict], eps_map: dict) -> list[dict]:
    cfg = EARNINGS_SCREEN_CONFIG
    min_cagr = cfg["min_cagr"]
    max_forward_pe = cfg["max_forward_pe"]
    hits = []

    for s in stocks:
        code = s["code"]
        eps_data = eps_map.get(code)
        if not eps_data:
            continue

        forecasts = eps_data["forecasts"]
        if len(forecasts) < 2:
            continue

        eps_y1 = forecasts[0]["eps_avg"]
        eps_y2 = forecasts[1]["eps_avg"] if len(forecasts) > 1 else None
        eps_y3 = forecasts[2]["eps_avg"] if len(forecasts) > 2 else None

        if not eps_y1 or eps_y1 <= 0:
            continue

        if eps_y3 and eps_y3 > 0:
            cagr = (eps_y3 / eps_y1) ** 0.5 - 1
            forward_eps = eps_y3
            forecast_year = forecasts[2]["year"]
        elif eps_y2 and eps_y2 > 0:
            cagr = eps_y2 / eps_y1 - 1
            forward_eps = eps_y2
            forecast_year = forecasts[1]["year"]
        else:
            continue

        if cagr < min_cagr:
            continue

        forward_pe = s["price"] / forward_eps
        if forward_pe > max_forward_pe or forward_pe <= 0:
            continue

        s["eps_y1"] = round(eps_y1, 2)
        s["eps_y2"] = round(eps_y2, 2) if eps_y2 else None
        s["eps_y3"] = round(eps_y3, 2) if eps_y3 else None
        s["cagr"] = round(cagr * 100, 1)
        s["forward_pe"] = round(forward_pe, 1)
        s["forecast_year"] = forecast_year
        s["inst_count"] = forecasts[0].get("inst_count", 0)
        hits.append(s)

    return hits


# ============================================================
# Step 5: 股东人数（2年历史）
# ============================================================

def fetch_shareholder_history(code: str) -> dict | None:
    try:
        import akshare as ak
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        if df is None or df.empty:
            return None

        records = []
        for _, row in df.iterrows():
            date_str = str(row.get("股东户数统计截止日", ""))[:10]
            count = row.get("股东户数-本次")
            if count and str(count) != "nan":
                records.append({"date": date_str, "count": int(count)})

        if not records:
            return None

        records.sort(key=lambda r: r["date"], reverse=True)

        cutoff = (datetime.now().year - 2)
        records_2y = [r for r in records if r["date"][:4].isdigit() and int(r["date"][:4]) >= cutoff]
        if not records_2y:
            records_2y = records[:8]

        latest = records_2y[0]["count"]
        max_count = max(r["count"] for r in records_2y)
        ratio = round(latest / max_count, 2) if max_count > 0 else 1.0

        return {
            "latest": latest,
            "max_2y": max_count,
            "ratio": ratio,
            "data_points": len(records_2y),
        }
    except Exception:
        return None


# ============================================================
# Step 6: 10日涨幅（mootdx K线）
# ============================================================

def calc_10d_changes(codes: list[str]) -> dict[str, float]:
    client = _get_mootdx()
    results = {}
    for code in codes:
        try:
            df = client.bars(symbol=code, category=4, offset=20)
            if df is not None and len(df) >= 11:
                df = df.reset_index(drop=True)
                close_today = float(df.iloc[-1]["close"])
                close_10d = float(df.iloc[-11]["close"])
                if close_10d > 0:
                    results[code] = round((close_today / close_10d - 1) * 100, 2)
        except Exception:
            pass
    return results


# ============================================================
# Focus Five 评分（基于已有数据，无额外API调用）
# ============================================================

def _calc_focus_five(s: dict) -> int:
    score = 0
    eps_y1 = s.get("eps_y1", 0)
    eps_y2 = s.get("eps_y2")
    eps_y3 = s.get("eps_y3")

    # 1. 增长加速度（Y2→Y3 vs Y1→Y2）：加速=2分，匀速=1分
    if eps_y1 and eps_y2 and eps_y3 and eps_y1 > 0 and eps_y2 > 0:
        g_12 = eps_y2 / eps_y1 - 1
        g_23 = eps_y3 / eps_y2 - 1
        if g_23 > g_12 * 1.1:
            score += 2
        elif g_23 >= g_12 * 0.8:
            score += 1

    # 2. 机构覆盖度：>=10家=2分，>=5家=1分
    inst = s.get("inst_count", 0) or 0
    if inst >= 10:
        score += 2
    elif inst >= 5:
        score += 1

    # 3. 增长幅度：CAGR>=40%=2分，>=30%=1分
    cagr = s.get("cagr", 0)
    if cagr >= 40:
        score += 2
    elif cagr >= 30:
        score += 1

    # 4. 估值安全边际：前瞻PE<=15=2分，<=22=1分
    fpe = s.get("forward_pe", 99)
    if fpe <= 15:
        score += 2
    elif fpe <= 22:
        score += 1

    # 5. 盈利确定性：3年预测=2分，2年预测=1分
    if eps_y3 and eps_y3 > 0:
        score += 2
    elif eps_y2 and eps_y2 > 0:
        score += 1

    return score


# ============================================================
# 报告生成
# ============================================================

def render_earnings_report(
    trade_date: str,
    hits: list[dict],
    shareholder_map: dict,
    funnel: dict,
    elapsed: float,
) -> str:
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 盈利预测选股 — {trade_date}")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"> 漏斗: {funnel['total']} → {funnel['basic']} → "
                 f"{funnel['pe_pre']} → {funnel['eps_fetched']}(有EPS) → "
                 f"**{funnel['hits']} 命中** | 耗时: {elapsed:.0f}s\n")

    lines.append("## 筛选结果（按10日涨幅排序）\n")
    lines.append("| # | 代码 | 名称 | 行业 | 现价 | CAGR | 前瞻PE | F5 | 10日% | 股东比 | 机构 |")
    lines.append("|--:|------|------|------|-----:|-----:|-------:|---:|------:|-------:|-----:|")

    for i, s in enumerate(hits, 1):
        sh = shareholder_map.get(s["code"])
        sh_str = f"{sh['ratio']:.0%}" if sh else "N/A"
        chg_10d = s.get("chg_10d")
        chg_str = f"{chg_10d:+.1f}%" if chg_10d is not None else "N/A"
        f5 = s.get("f5_score", 0)

        lines.append(
            f"| {i} | {s['code']} | {s['name']} | {s.get('industry', '')} "
            f"| {s['price']:.2f} | {s['cagr']:.0f}% | {s['forward_pe']:.1f} "
            f"| {f5} | {chg_str} | {sh_str} | {s.get('inst_count', 0)} |"
        )

    lines.append("")

    sh_details = [(s, shareholder_map.get(s["code"]))
                  for s in hits if shareholder_map.get(s["code"])]
    if sh_details:
        concentrated = [(s, sh) for s, sh in sh_details if sh["ratio"] < 0.9]
        if concentrated:
            lines.append("## 股东集中度亮点\n")
            lines.append("以下标的最新股东人数不足2年内高点的90%（筹码集中）：\n")
            for s, sh in sorted(concentrated, key=lambda x: x[1]["ratio"]):
                lines.append(
                    f"- **{s['name']}**（{s['code']}）："
                    f"最新 {sh['latest']:,} 户，2年最高 {sh['max_2y']:,} 户，"
                    f"比值 {sh['ratio']:.0%}"
                )
            lines.append("")

    lines.append("---")
    lines.append("*本报告由盈利预测选股模型自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def render_earnings_excel(
    trade_date: str,
    hits: list[dict],
    shareholder_map: dict,
) -> str:
    rows = []
    for i, s in enumerate(hits, 1):
        sh = shareholder_map.get(s["code"])
        chg_10d = s.get("chg_10d")
        rows.append({
            "#": i,
            "代码": s["code"],
            "名称": s["name"],
            "行业": s.get("industry", ""),
            "现价": s["price"],
            "PE": round(s["pe"], 1),
            "EPS Y1": s["eps_y1"],
            "EPS Y2": s.get("eps_y2"),
            "EPS Y3": s.get("eps_y3"),
            "CAGR": s["cagr"] / 100,
            "前瞻PE": s["forward_pe"],
            "F5评分": s.get("f5_score", 0),
            "10日%": chg_10d / 100 if chg_10d is not None else None,
            "最新股东数": sh["latest"] if sh else None,
            "2年最高股东": sh["max_2y"] if sh else None,
            "股东比值": sh["ratio"] if sh else None,
            "机构": s.get("inst_count", 0),
        })

    df_main = pd.DataFrame(rows)

    concentrated = []
    for s in hits:
        sh = shareholder_map.get(s["code"])
        if sh and sh["ratio"] < 0.9:
            concentrated.append({
                "代码": s["code"],
                "名称": s["name"],
                "最新股东数": sh["latest"],
                "2年最高股东": sh["max_2y"],
                "股东比值": sh["ratio"],
            })
    concentrated.sort(key=lambda x: x["股东比值"])
    df_conc = pd.DataFrame(concentrated) if concentrated else pd.DataFrame()

    xlsx_path = REPORT_DIR / f"earnings_{trade_date}.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df_main.to_excel(writer, sheet_name="筛选结果", index=False)
        if not df_conc.empty:
            df_conc.to_excel(writer, sheet_name="股东集中度", index=False)
    return str(xlsx_path)


# ============================================================
# 主流程
# ============================================================

def run_earnings_screen(trade_date: str = None) -> str:
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    store.init_db()
    t0 = time.time()

    # Step 1-2: 全市场行情 + 基础过滤
    print("[1/5] 拉取全市场行情 + 基础过滤...")
    stocks = fetch_and_filter_market()
    total_count = len(stocks) + 2700  # 粗估被过滤掉的
    print(f"  ✓ 基础过滤后: {len(stocks)} 只（PE>0, 市值>20亿, PE<150）")

    funnel = {"total": total_count, "basic": len(stocks)}

    # Step 3: 拉取一致预期EPS
    codes = [s["code"] for s in stocks]
    print(f"[2/5] 采集一致预期EPS（{len(codes)} 只）...")
    eps_map = fetch_eps_batch(codes)
    print(f"  ✓ {len(eps_map)} 只有EPS预测数据")
    funnel["pe_pre"] = len(stocks)
    funnel["eps_fetched"] = len(eps_map)

    # Step 4: CAGR + 前瞻PE 筛选
    print("[3/5] 盈利筛选（CAGR>30% + 前瞻PE<30）...")
    hits = filter_by_earnings(stocks, eps_map)
    print(f"  ✓ {len(hits)} 只命中")
    funnel["hits"] = len(hits)

    if not hits:
        print("  无命中标的，结束")
        return ""

    # Focus Five评��
    for s in hits:
        s["f5_score"] = _calc_focus_five(s)

    # Step 5: 10日涨幅
    hit_codes = [s["code"] for s in hits]
    print(f"[4/5] 计算10日涨幅（{len(hit_codes)} 只）...")
    chg_map = calc_10d_changes(hit_codes)
    for s in hits:
        s["chg_10d"] = chg_map.get(s["code"])
    hits.sort(key=lambda x: x.get("chg_10d") or -999, reverse=True)
    print(f"  ✓ {len(chg_map)} 只有K线数据")

    # Step 6: 股东人数
    print(f"[5/5] 查询股东人数（{len(hit_codes)} 只）...")
    shareholder_map = {}
    pbar = tqdm(hit_codes, desc="  股东查询", unit="只",
                bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")
    for code in pbar:
        sh = fetch_shareholder_history(code)
        if sh:
            shareholder_map[code] = sh
        pbar.set_postfix(hit=len(shareholder_map))
        time.sleep(0.3)
    print(f"  ✓ {len(shareholder_map)} 只有股东数据")

    # 生成报告
    elapsed = time.time() - t0
    md = render_earnings_report(trade_date, hits, shareholder_map, funnel, elapsed)
    report_path = REPORT_DIR / f"earnings_{trade_date}.md"
    report_path.write_text(md, encoding="utf-8")

    xlsx_path = render_earnings_excel(trade_date, hits, shareholder_map)

    print(f"\n{'='*50}")
    print(f"  ✅ 盈利预测选股完成！耗时 {elapsed:.0f}s")
    print(f"  📄 报告: {report_path}")
    print(f"  📊 Excel: {xlsx_path}")
    print(f"  命中: {len(hits)} 只")
    print(f"{'='*50}")

    print(f"\nTOP10（按10日涨幅）:")
    for s in hits[:10]:
        chg = s.get("chg_10d")
        chg_s = f"{chg:+.1f}%" if chg is not None else "N/A"
        print(f"  {s['name']:8s} {s['code']}  CAGR={s['cagr']:.0f}%  "
              f"前瞻PE={s['forward_pe']:.1f}  10日{chg_s}")

    return str(report_path)
