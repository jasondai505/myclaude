"""每日复盘系统 - 主入口

用法:
    python run.py                    # 今天的复盘
    python run.py --date 2026-05-12  # 指定日期
    python run.py --list             # 查看当前自选股
"""
import sys
import time
import argparse
from datetime import date as _date
from pathlib import Path

from utils import setup_console, is_headless, progress_bar
setup_console()

# 确保能导入同级模块
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    WATCHLIST, REPORT_DIR, FETCH_DELAY, FUNDAMENTAL_TOP_N,
    THEME_EXPAND_MIN_LEVEL, THEME_ZHONGJUN_MIN_LEVEL,
    THEME_RECENT_DAYS, THEME_ZHONGJUN_LOOKBACK,
    STRENGTH_POOL_LOOKBACK, STRENGTH_MAX_KLINES_PER_THEME,
)
import store
import data
import engine
import report
from screener import run_scan
from research import run_research
from earnings_screen import run_earnings_screen
from zsxq_cross import run_cross, load_cookie, fetch_recent_topics, analyze_zsxq_topics, COOKIE_PATH
from zsxq_collector import sync
from llm import generate_overseas_catalysts
import strength as strength_mod
from engine_sector_rotation import sector_frequency, sector_persistence, sector_cooccurrence
from engine_market_rhythm import rhythm_report
from engine_themes import _calc_chg5, _calc_r10
from engine_leader_backtest import top_leader_report
from engine_similar_days import similar_days_report
from engine_limit_up import analyze as limit_up_analyze


def check_deps():
    missing = []
    for pkg in ["mootdx", "akshare", "requests", "pandas", "stockstats"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[ERROR] 缺少依赖: {', '.join(missing)}")
        print(f"  运行: pip install {' '.join(missing)}")
        sys.exit(1)


def resolve_trade_date(date_str: str | None) -> str:
    if date_str:
        return date_str
    return _date.today().strftime("%Y-%m-%d")


# ============================================================
# Phase 1: 参数解析 & 子命令分发
# ============================================================

def _parse_args():
    parser = argparse.ArgumentParser(description="A股每日复盘系统")
    parser.add_argument("--date", "-d", type=str, default=None, help="交易日期 YYYY-MM-DD")
    parser.add_argument("--list", "-l", action="store_true", help="查看当前自选股")
    parser.add_argument("--scan", "-s", action="store_true", help="全市场扫描")
    parser.add_argument("--report", "-r", action="store_true", help="研报采集")
    parser.add_argument("--earnings", "-e", action="store_true", help="盈利预测选股")
    parser.add_argument("--cross", "-x", action="store_true", help="知识星球×盈利预测交叉验证")
    parser.add_argument("--zsxq", action="store_true", help="同步知识星球帖子")
    parser.add_argument("--sector-rotation", "-sr", action="store_true",
                        help="板块轮动分析（基于强势板块.xlsx 历史数据）")
    return parser.parse_args()


def _generate_sector_rotation_report() -> str:
    """生成板块轮动分析报告"""
    lines = ["", "=" * 50, "  板块轮动分析（强势板块.xlsx 历史复盘数据）", "=" * 50, ""]

    freq = sector_frequency(60)
    lines.append("## 近 60 日高频板块")
    lines.append("| 板块 | 出现天数 | 首次 | 最近 |")
    lines.append("|------|---------|------|------|")
    for s in freq[:15]:
        lines.append(f"| {s['sector']} | {s['days']} | {s['first_date']} | {s['last_date']} |")

    lines.append("")
    pers = sector_persistence(5)
    lines.append("## 板块持续性 Top 10")
    lines.append("| 板块 | 累计天数 | 轮次 | 最长连续 | 平均连续 |")
    lines.append("|------|---------|------|---------|---------|")
    for s in pers[:10]:
        lines.append(
            f"| {s['sector']} | {s['total_days']} | {s['runs']} | "
            f"{s['max_streak']} | {s['avg_streak']} |")

    lines.append("")
    cooc = sector_cooccurrence(120)
    lines.append("## 板块共现 Top 10（近 120 日）")
    lines.append("| 板块 A | 板块 B | 共现天数 |")
    lines.append("|--------|--------|---------|")
    for c in cooc[:10]:
        lines.append(f"| {c['sector_a']} | {c['sector_b']} | {c['co_days']} |")

    lines.append("")
    lines.append(rhythm_report())
    lines.append("")
    lines.append(top_leader_report(15))
    lines.append("")
    lines.append(similar_days_report(target_date=None))

    return "\n".join(lines)


def _dispatch_early(args) -> bool:
    if args.list:
        print("当前自选股池:")
        for c in WATCHLIST:
            print(f"  {c}")
        print(f"\n编辑 config.py 修改自选股池")
        return True

    if args.scan:
        check_deps()
        trade_date = resolve_trade_date(args.date)
        run_scan(trade_date)
        return True

    if args.report:
        check_deps()
        trade_date = resolve_trade_date(args.date)
        run_research(trade_date)
        return True

    if args.earnings:
        check_deps()
        trade_date = resolve_trade_date(args.date)
        run_earnings_screen(trade_date)
        return True

    if args.cross:
        check_deps()
        trade_date = resolve_trade_date(args.date)
        run_cross(trade_date)
        return True

    if args.zsxq:
        sync()
        return True

    if args.sector_rotation:
        check_deps()
        store.init_db()
        print(_generate_sector_rotation_report())
        return True

    return False


# ============================================================
# Phase 2: 数据拉取
# ============================================================

def _fetch_market_data(trade_date):
    total_steps = 12

    print(f"[1/{total_steps}] 拉取指数行情...")
    indices = data.fetch_indices()
    print(f"  ✓ {len(indices)} 个指数")

    print(f"[2/{total_steps}] 拉取行业排名...")
    try:
        industry = data.fetch_industry_ranking()
        print(f"  ✓ {len(industry.get('all', []))} 个行业")
    except Exception as e:
        print(f"  ✗ 行业数据获取失败: {e}")
        industry = {"all": [], "total_up": 0, "total_down": 0}

    print(f"[3/{total_steps}] 拉取题材热度...")
    hot_df = data.fetch_hot_themes(trade_date)
    print(f"  ✓ {len(hot_df)} 只强势股")

    print(f"[4/{total_steps}] 拉取北向资金...")
    try:
        northbound = data.fetch_northbound()
        print(f"  ✓ 北向合计: {northbound['total']:+.1f}亿")
    except Exception as e:
        print(f"  ✗ 北向数据获取失败: {e}")
        northbound = {"hgt_close": 0, "sgt_close": 0, "total": 0, "df": None}

    print(f"[5/{total_steps}] 拉取外围市场...")
    try:
        global_data = data.fetch_global_markets()
        g_count = len(global_data.get("indices", {})) + len(global_data.get("watchlist", {}))
        print(f"  ✓ {g_count} 个标的")
    except Exception as e:
        print(f"  ✗ 外围数据获取失败: {e}")
        global_data = {}

    return indices, industry, hot_df, northbound, global_data


def _fetch_watchlist_data(watchlist):
    total_steps = 12

    print(f"[6/{total_steps}] 扫描自选股 ({len(watchlist)} 只)...")
    stock_quotes = data.fetch_stock_quotes(watchlist)
    print(f"  ✓ 行情: {len(stock_quotes)} 只")

    stock_klines = {}
    stock_flows = {}
    stock_lockups = {}

    pbar = progress_bar(watchlist, desc="  自选股扫描", unit="只")
    for code in pbar:
        label = stock_quotes.get(code, {}).get("name", code)

        kl = data.fetch_klines(code)
        if kl is not None:
            stock_klines[code] = kl
        time.sleep(FETCH_DELAY)

        fl = data.fetch_fund_flow(code)
        if fl:
            stock_flows[code] = fl
        time.sleep(FETCH_DELAY)

        lk = data.fetch_lockup(code)
        if lk:
            stock_lockups[code] = lk
        time.sleep(FETCH_DELAY)

    return stock_quotes, stock_klines, stock_flows, stock_lockups


def _fetch_popularity():
    print(f"[10/12] 拉取市场人气...")
    concept_heat = data.fetch_concept_heat(top_n=50)
    hot_stocks = data.fetch_hot_stocks(top_n=200)
    if concept_heat:
        print(f"  ✓ 概念板块 {len(concept_heat)} 个")
    if hot_stocks:
        print(f"  ✓ 人气个股 {len(hot_stocks)} 只")
    return concept_heat, hot_stocks


def _fetch_zsxq(trade_date):
    zsxq_data = None
    try:
        if COOKIE_PATH.exists():
            print(f"[11/12] 拉取知识星球...")
            cookie = load_cookie()
            zsxq_topics = fetch_recent_topics(cookie, pages=50)
            if zsxq_topics:
                zsxq_data = analyze_zsxq_topics(zsxq_topics, trade_date)
                print(f"  ✓ {len(zsxq_topics)} 条帖子，提取 {len(zsxq_data['highlights'])} 条要点")
            else:
                print("  ✗ 未拉到帖子（Cookie可能过期）")
        else:
            print(f"[11/12] 跳过知识星球（无cookie.txt）")
    except Exception as e:
        print(f"[11/12] 知识星球采集失败: {e}")
    return zsxq_data


# ============================================================
# Phase 3: 分析
# ============================================================

def _run_core_analysis(indices, industry, hot_df, northbound, global_data,
                       stock_quotes, stock_klines, stock_flows, stock_lockups,
                       trade_date, watchlist):
    print(f"[7/12] 分析中...")

    zt_pool = data.fetch_zt_pool(trade_date)
    if zt_pool:
        print(f"  ✓ 涨停池 {len(zt_pool)} 只")
    dt_pool = data.fetch_dt_pool(trade_date)
    if dt_pool:
        print(f"  ✓ 跌停池 {len(dt_pool)} 只")

    sentiment_result = engine.analyze_sentiment(hot_df)
    market_result = engine.analyze_market(
        indices, industry_data=industry, hot_df=hot_df,
        zt_pool=zt_pool, dt_pool=dt_pool, trade_date=trade_date,
        sentiment_result=sentiment_result,
    )
    style_result = engine.analyze_style(indices)
    sector_result = engine.analyze_sectors(industry)
    theme_result = engine.analyze_themes(hot_df, trade_date)
    theme_result = engine.enrich_themes_with_bom(theme_result)
    nb_result = engine.analyze_northbound(northbound)
    global_result = engine.analyze_global(global_data)

    try:
        catalysts = generate_overseas_catalysts(global_result.get("watchlist", {}), trade_date)
        for label, cat in catalysts.items():
            if label in global_result.get("watchlist", {}):
                global_result["watchlist"][label]["catalyst"] = cat
        if catalysts:
            print(f"  ✓ 外围催化摘要: {len(catalysts)} 个")
    except Exception as e:
        print(f"  [WARN] 外围催化摘要跳过: {e}")

    watchlist_results = []
    for code in watchlist:
        r = engine.analyze_single_stock(
            code=code,
            quote=stock_quotes.get(code),
            kline_df=stock_klines.get(code),
            fund_flow=stock_flows.get(code),
            lockup=stock_lockups.get(code),
        )
        watchlist_results.append(r)

    wt_result = engine.analyze_watchlist_themes(watchlist_results, hot_df, theme_result)

    return (market_result, style_result, sentiment_result, sector_result,
            theme_result, nb_result, global_result, watchlist_results,
            wt_result, zt_pool, dt_pool)


def _run_theme_expansion(hot_df, theme_result, stock_quotes, stock_klines,
                         trade_date, zt_pool):
    leveled = theme_result.get("leveled", [])
    high_themes = [t for t in leveled if t["level"] >= 3]
    theme_news_map = {}

    if high_themes:
        print(f"[8/12] 题材审美分析（{len(high_themes)}个3级+题材）...")
        for t in progress_bar(high_themes, desc="  题材新闻", unit="个",
                      bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            news = data.fetch_theme_news(t["theme"], limit=8)
            theme_news_map[t["theme"]] = news
            time.sleep(0.3)
    else:
        print(f"[8/12] 无3级以上题材，跳过审美分析")

    aesthetics_result = engine.analyze_theme_aesthetics(leveled, theme_news_map)

    hot_klines = {}
    if hot_df is not None and not hot_df.empty:
        hot_codes_list = [str(row.get("代码", "")) for _, row in hot_df.iterrows()]
        print(f"  拉取涨停股K线（{len(hot_codes_list)}只）...")
        for code in progress_bar(hot_codes_list, desc="  涨停K线", unit="只",
                         bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            if code in stock_klines:
                hot_klines[code] = stock_klines[code]
            else:
                kl = data.fetch_klines(code, days=15)
                if kl is not None:
                    hot_klines[code] = kl
                time.sleep(0.1)

    hot_quotes = {}
    if hot_df is not None and not hot_df.empty:
        zero_chg_codes = [
            str(row.get("代码", "")) for _, row in hot_df.iterrows()
            if float(row.get("涨幅%", 0) or 0) == 0
            and str(row.get("代码", "")) not in stock_quotes
        ]
        if zero_chg_codes:
            print(f"  补全涨停股行情（{len(zero_chg_codes)}只）...")
            hot_quotes = data.fetch_stock_quotes(zero_chg_codes)

    theme_stock_details = engine.build_theme_stock_details(
        hot_df, theme_result, hot_klines, hot_quotes=hot_quotes, zt_pool=zt_pool)

    leveled = theme_result.get("leveled", [])
    theme_freq_5d = {}
    theme_freq_30d = {}
    extra_codes_needed = set()
    hot_codes_set = set()
    if hot_df is not None and not hot_df.empty:
        hot_codes_set = {str(row.get("代码", "")) for _, row in hot_df.iterrows()}

    for t in leveled:
        if t.get("level", 0) < THEME_ZHONGJUN_MIN_LEVEL:
            continue
        theme_name = t["theme"]
        freq_5d = store.get_theme_stock_frequency(theme_name, trade_date, days=THEME_RECENT_DAYS * 2)
        freq_30d = store.get_theme_stock_frequency(theme_name, trade_date, days=THEME_ZHONGJUN_LOOKBACK)
        theme_freq_5d[theme_name] = freq_5d
        theme_freq_30d[theme_name] = freq_30d
        for code in set(list(freq_5d.keys()) + list(freq_30d.keys())):
            if code not in hot_codes_set and code not in stock_klines:
                extra_codes_needed.add(code)

    extra_quotes = {}
    extra_klines_map = {}
    if extra_codes_needed:
        extra_list = sorted(extra_codes_needed)
        print(f"  拉取题材扩展行情（{len(extra_list)}只）...")
        extra_quotes = data.fetch_stock_quotes(extra_list)

        kline_codes = set()
        for t in leveled:
            theme_name = t["theme"]
            level = t.get("level", 0)
            if level < THEME_ZHONGJUN_MIN_LEVEL:
                continue
            freq_30d = theme_freq_30d.get(theme_name, {})
            freq_5d = theme_freq_5d.get(theme_name, {})
            for code, info in freq_30d.items():
                if code in hot_klines or code in stock_klines:
                    continue
                q = extra_quotes.get(code, {})
                mcap = q.get("mcap_yi", 0) if q else 0
                if info["freq"] >= 3 and mcap > 50:
                    kline_codes.add(code)
            if level >= THEME_EXPAND_MIN_LEVEL:
                b_sorted = sorted(freq_5d.items(), key=lambda x: -x[1]["freq"])
                for code, info in b_sorted[:5]:
                    if code not in hot_codes_set and code not in hot_klines and code not in stock_klines:
                        kline_codes.add(code)

        if kline_codes:
            print(f"  拉取扩展K线（{len(kline_codes)}只）...")
            for code in progress_bar(sorted(kline_codes), desc="  扩展K线", unit="只",
                             bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
                kl = data.fetch_klines(code, days=15)
                if kl is not None:
                    extra_klines_map[code] = kl
                time.sleep(0.1)

    all_extra_klines = {**hot_klines, **stock_klines, **extra_klines_map}
    theme_stock_details = engine.expand_theme_stocks(
        theme_stock_details, leveled,
        {**stock_quotes, **extra_quotes},
        all_extra_klines,
        theme_freq_5d, theme_freq_30d,
    )

    print("  拉取人气前100 / 20日涨幅前100（问财）...")
    pop_pool = data.fetch_popularity_top100()
    gain_pool = data.fetch_gainers_20d()
    print(f"    人气池 {len(pop_pool)} 只 / 涨幅池 {len(gain_pool)} 只")
    merged_pool = engine.build_merged_theme_pool(hot_df, pop_pool, gain_pool)
    theme_new_dirs = engine.attach_merged_to_themes(theme_stock_details, leveled, merged_pool)
    theme_longtail = merged_pool["longtail"]
    print(f"    合并题材 {len(merged_pool['themes'])} 个 / 新方向 {len(theme_new_dirs)} 个 / 长尾 {len(theme_longtail)} 个")

    missing_codes = set()
    all_existing_quotes = {**stock_quotes, **extra_quotes}
    for stocks in theme_stock_details.values():
        for s in stocks:
            code = s["code"]
            if (s.get("amount_wan") or 0) == 0:
                eq = all_existing_quotes.get(code)
                if eq:
                    s["amount_wan"] = eq.get("amount_wan", 0) or 0
                    s["chg"] = s["chg"] or eq.get("change_pct", 0) or 0
                    s["turnover"] = s["turnover"] or eq.get("turnover_pct", 0) or 0
                    s["mcap_yi"] = s.get("mcap_yi") or eq.get("mcap_yi", 0) or 0
                else:
                    missing_codes.add(code)
    if missing_codes:
        missing_list = sorted(missing_codes)
        print(f"  补全合池标的行情（{len(missing_list)}只）...")
        merged_quotes = data.fetch_stock_quotes(missing_list)
        for s_list in theme_stock_details.values():
            for s in s_list:
                q = merged_quotes.get(s["code"])
                if q:
                    if not s.get("amount_wan"):
                        s["amount_wan"] = q.get("amount_wan", 0) or 0
                    if not s.get("chg"):
                        s["chg"] = q.get("change_pct", 0) or 0
                    if not s.get("turnover"):
                        s["turnover"] = q.get("turnover_pct", 0) or 0
                    if not s.get("mcap_yi"):
                        s["mcap_yi"] = q.get("mcap_yi", 0) or 0

    nonzt_need_klines = set()
    all_klines_now = {**hot_klines, **stock_klines, **extra_klines_map}
    for stocks in theme_stock_details.values():
        for s in stocks:
            if s.get("is_limit_up"):
                continue
            if s.get("r10") is None or s.get("chg5") is None:
                code = s["code"]
                kdf = all_klines_now.get(code)
                if kdf is not None:
                    if s.get("r10") is None:
                        s["r10"] = _calc_r10(kdf)
                    if s.get("chg5") is None:
                        s["chg5"] = _calc_chg5(kdf)
                else:
                    nonzt_need_klines.add(code)
    if nonzt_need_klines:
        kline_list = sorted(nonzt_need_klines)[:80]
        print(f"  拉取非涨停标的K线（{len(kline_list)}只）...")
        for code in progress_bar(kline_list, desc="  非涨停K线", unit="只",
                         bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            kl = data.fetch_klines(code, days=15)
            if kl is not None:
                all_klines_now[code] = kl
                all_extra_klines[code] = kl
            time.sleep(0.1)
        for stocks in theme_stock_details.values():
            for s in stocks:
                if s.get("is_limit_up"):
                    continue
                kdf = all_klines_now.get(s["code"])
                if kdf is not None:
                    if s.get("r10") is None:
                        s["r10"] = _calc_r10(kdf)
                    if s.get("chg5") is None:
                        s["chg5"] = _calc_chg5(kdf)

    theme_groups = engine.classify_themes_by_trend(theme_result, aesthetics_result)

    return (theme_stock_details, aesthetics_result, theme_groups,
            theme_new_dirs, theme_longtail, all_extra_klines,
            extra_quotes, hot_quotes)


def _run_strength_phase(theme_result, all_extra_klines, stock_klines,
                        stock_quotes, extra_quotes, zt_pool, trade_date,
                        aesthetics_result):
    print("  板块强弱分析...")
    leveled = theme_result.get("leveled", [])
    theme_pool = store.get_theme_stock_pool(trade_date, STRENGTH_POOL_LOOKBACK)
    code_to_themes = store.build_code_to_themes(theme_pool)

    leveled_for_strength = {t["theme"]: t for t in leveled if t.get("level", 0) >= 2}
    strength_codes_needed = set()
    for theme, stocks_info in theme_pool.items():
        if theme not in leveled_for_strength:
            continue
        top_codes_s = sorted(stocks_info.items(), key=lambda x: -x[1]["freq"])[:STRENGTH_MAX_KLINES_PER_THEME]
        for code, _ in top_codes_s:
            if code not in all_extra_klines and code not in stock_klines:
                strength_codes_needed.add(code)

    all_strength_codes = set()
    for theme, stocks_info in theme_pool.items():
        if theme not in leveled_for_strength:
            continue
        top_codes_s = sorted(stocks_info.items(), key=lambda x: -x[1]["freq"])[:STRENGTH_MAX_KLINES_PER_THEME]
        for code, _ in top_codes_s:
            all_strength_codes.add(code)

    existing_quotes = {**stock_quotes, **extra_quotes}
    quotes_needed = sorted(c for c in all_strength_codes if c not in existing_quotes)

    strength_quotes = {}
    strength_klines = {}
    if quotes_needed:
        print(f"  拉取强弱分析行情（{len(quotes_needed)}只）...")
        strength_quotes = data.fetch_stock_quotes(quotes_needed)
    if strength_codes_needed:
        strength_list = sorted(strength_codes_needed)
        print(f"  拉取强弱分析K线（{len(strength_list)}只）...")
        for code in progress_bar(strength_list, desc="  强弱K线", unit="只",
                         bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            kl = data.fetch_klines(code, days=60)
            if kl is not None:
                strength_klines[code] = kl
            time.sleep(0.1)

    all_klines_merged = {**all_extra_klines, **stock_klines, **strength_klines}
    all_quotes_merged = {**stock_quotes, **extra_quotes, **strength_quotes}

    aesthetics_map = {a["theme"]: a for a in aesthetics_result}
    strength_result = strength_mod.run_strength_analysis(
        theme_pool, all_klines_merged, all_quotes_merged,
        leveled, aesthetics_map, code_to_themes, zt_pool,
    )
    print(f"  ✓ 走强{len(strength_result['strong_themes'])}个 | "
          f"潜在{len(strength_result['emerging_themes'])}个 | "
          f"退潮{len(strength_result['fading_themes'])}个 | "
          f"走强个股{strength_result['rising_commonalities']['count']}只")

    return strength_result, all_klines_merged, all_quotes_merged, code_to_themes, theme_pool


def _run_fundamentals_fev(watchlist, stock_quotes, watchlist_results, hot_df,
                          theme_result, market_result, sector_result, nb_result):
    print(f"[9/12] 基本面扫描（FEV评分）...")
    eps_data = {}
    shareholder_data = {}
    news_data = {}
    valid_codes = [c for c in watchlist if stock_quotes.get(c)]
    pbar = progress_bar(valid_codes, desc="  基本面", unit="只")
    for code in pbar:
        label = stock_quotes.get(code, {}).get("name", code)
        eps = data.fetch_eps_forecast(code)
        if eps:
            eps_data[code] = eps
        time.sleep(0.15)
        sh_cnt = data.fetch_shareholder_count(code)
        if sh_cnt:
            shareholder_data[code] = sh_cnt
        time.sleep(0.15)

    sorted_stocks = sorted(watchlist_results, key=lambda x: x["trend_score"], reverse=True)
    top_codes = [s["code"] for s in sorted_stocks[:FUNDAMENTAL_TOP_N] if s.get("quote")]
    for code in top_codes[:10]:
        ns = data.fetch_stock_news(code, limit=3)
        if ns:
            news_data[code] = ns
        time.sleep(0.3)

    fundamentals_result = engine.analyze_fundamentals(
        top_codes[:10], stock_quotes, eps_data, shareholder_data, news_data
    )

    hot_codes = set()
    code_themes: dict[str, list[str]] = {}
    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            c = str(row.get("代码", ""))
            hot_codes.add(c)
            reason = str(row.get("题材归因", ""))
            code_themes[c] = [t.strip() for t in reason.split("+") if t.strip()]

    hot_theme_names = set(t[0] for t in theme_result.get("today", [])[:10])
    theme_narratives = {}
    for t in theme_result.get("leveled", []):
        theme_narratives[t["theme"]] = t.get("narrative", "")

    fev_scores = []
    crash_warnings_map = {}
    for stock in watchlist_results:
        if not stock.get("quote"):
            continue
        fev = engine.score_fev(
            stock, eps_data, shareholder_data,
            hot_codes, hot_theme_names, code_themes, theme_narratives
        )
        fev_scores.append(fev)

        surge_score, surge_details = engine.check_surge_preconditions(
            stock, hot_codes, hot_theme_names, code_themes
        )
        fev["surge_score"] = surge_score
        fev["surge_details"] = surge_details

        warns = engine.check_crash_warnings(stock, shareholder_data)
        if warns:
            crash_warnings_map[stock["code"]] = warns
        fev["crash_warnings"] = warns

    suggestions = engine.generate_suggestions(
        market=market_result,
        sectors=sector_result,
        themes=theme_result,
        northbound=nb_result,
        watchlist_results=watchlist_results,
        fev_scores=fev_scores,
        crash_warnings=crash_warnings_map,
    )

    return (fundamentals_result, eps_data, shareholder_data,
            fev_scores, crash_warnings_map, suggestions,
            hot_codes, code_themes, hot_theme_names, theme_narratives)


# ============================================================
# Phase 4: 聚焦池
# ============================================================

def _build_focus_pool_full(trade_date, watchlist, zt_pool, all_quotes_merged,
                           all_klines_merged, stock_klines, fev_scores,
                           eps_data, shareholder_data, hot_codes, code_themes,
                           hot_theme_names, theme_narratives, zsxq_data,
                           sentiment_result, leveled, code_to_themes,
                           crash_warnings_map, strength_result, theme_pool):
    print(f"[12/12] 构建聚焦池...")
    ths_hot = data.fetch_ths_hot_stocks(period="hour")
    print(f"  ✓ 同花顺人气 {len(ths_hot)} 只")

    focus_pool = engine.build_focus_pool(ths_hot, zt_pool or {}, watchlist)
    print(f"  ✓ 聚焦池 {len(focus_pool)} 只（去重后）")

    pool_codes_need_quotes = [c for c in focus_pool if c not in all_quotes_merged]
    if pool_codes_need_quotes:
        print(f"  补全聚焦池行情（{len(pool_codes_need_quotes)}只）...")
        pool_quotes = data.fetch_stock_quotes(pool_codes_need_quotes)
        all_quotes_merged.update(pool_quotes)

    lhb_data = data.fetch_lhb(trade_date)
    print(f"  ✓ 龙虎榜 {len(lhb_data)} 只")

    pool_research = {}
    research_candidates = [c for c, s in focus_pool.items()
                           if s.get("hot_rank", 0) <= 50 or "zt" in s["source"]]
    if research_candidates:
        print(f"  拉取研报（{len(research_candidates)}只）...")
        for code in progress_bar(research_candidates, desc="  研报", unit="只",
                         bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            r = data.fetch_stock_research(code, limit=3)
            if r:
                pool_research[code] = r
            time.sleep(0.2)

    zsxq_mentions_map = {}
    if zsxq_data:
        for code, mentions in zsxq_data.get("stock_mentions", {}).items():
            zsxq_mentions_map[code] = len(mentions)

    fev_map = {f["code"]: f for f in fev_scores}

    pool_need_klines = [c for c in focus_pool
                        if c not in all_klines_merged and c not in stock_klines]
    if pool_need_klines:
        print(f"  拉取聚焦池K线（{len(pool_need_klines)}只）...")
        for code in progress_bar(pool_need_klines[:120], desc="  聚焦池K线", unit="只",
                         bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            kl = data.fetch_klines(code, days=30)
            if kl is not None:
                all_klines_merged[code] = kl
            time.sleep(0.1)

    pool_non_watch = [c for c in focus_pool if c not in set(watchlist)]
    eps_candidates = [c for c in pool_non_watch if c not in eps_data
                      and (focus_pool[c].get("hot_rank", 0) <= 50
                           or "zt" in focus_pool[c]["source"])]
    if eps_candidates:
        print(f"  拉取聚焦池盈利预测（{len(eps_candidates)}只）...")
        for code in progress_bar(eps_candidates[:80], desc="  盈利预测", unit="只",
                         bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
            eps = data.fetch_eps_forecast(code)
            if eps:
                eps_data[code] = eps
            time.sleep(0.15)

    for code in pool_non_watch:
        if code in fev_map:
            continue
        q = all_quotes_merged.get(code)
        if not q:
            continue
        stock_result = engine.analyze_single_stock(
            code=code, quote=q, kline_df=all_klines_merged.get(code),
            fund_flow=None, lockup=None,
        )
        fev = engine.score_fev(
            stock_result, eps_data, shareholder_data,
            hot_codes, hot_theme_names, code_themes, theme_narratives
        )
        fev_map[code] = fev

    print(f"  拉取聚焦池语料（公告/互动/新闻，{len(focus_pool)}只）...")
    corpus_map: dict[str, dict] = {}
    ann_date = trade_date.replace("-", "")
    announcements_all = data.fetch_announcements_all(ann_date)
    for code in focus_pool:
        corpus_map[code] = {
            "announcements": announcements_all.get(code, [])[:5],
            "irm": [],
            "news": [],
        }
    for code in progress_bar(list(focus_pool.keys()), desc="  互动+新闻", unit="只",
                     bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"):
        if code.startswith(("0", "3")):
            qa = data.fetch_irm_szse(code, limit=3)
        elif code.startswith("6"):
            qa = data.fetch_irm_sse(code, limit=3)
        else:
            qa = []
        if qa:
            corpus_map[code]["irm"] = qa
        if not corpus_map[code]["news"]:
            ns = data.fetch_stock_news(code, limit=3)
            if ns:
                corpus_map[code]["news"] = ns
        time.sleep(0.15)

    # B1: 逻辑/情绪涨停四维分类
    theme_counts: dict[str, int] = {}
    for zt_code in (zt_pool or {}):
        for tname in (code_themes.get(zt_code) or code_to_themes.get(zt_code, [])):
            theme_counts[tname] = theme_counts.get(tname, 0) + 1
    code_themes_merged = {
        c: (code_themes.get(c) or code_to_themes.get(c, []))
        for c in (zt_pool or {})
    }
    engine.apply_limit_up_classification(
        sentiment_result, zt_pool or {}, all_quotes_merged,
        all_klines_merged, lhb_data, corpus_map,
        theme_counts, code_themes_merged,
    )
    by_label = sentiment_result.get("by_label", {})
    if by_label:
        bl_parts = " / ".join(f"{k}{v}" for k, v in by_label.items() if v)
        print(f"  ✓ 涨停四维分类: {bl_parts}")

    # B2: 涨停 label 映射
    limit_up_label_map: dict[str, str] = {}
    for bucket in ("logic_stocks", "emotion_stocks", "mixed_stocks"):
        for s in sentiment_result.get(bucket, []):
            if s.get("code") and s.get("label"):
                limit_up_label_map[s["code"]] = s["label"]

    try:
        from bom_bridge import get_stock_moat_scores
        bom_moat_map = get_stock_moat_scores(list(focus_pool.keys()))
        if bom_moat_map:
            print(f"  BOM护城河覆盖: {len(bom_moat_map)} 只")
    except ImportError:
        bom_moat_map = {}

    focus_pool_data = []
    for code, pool_info in focus_pool.items():
        fev = fev_map.get(code, {})
        fev_total = fev.get("fev_total", 0)

        stock_themes_list = code_themes.get(code) or code_to_themes.get(code, [])
        best_level = 0
        best_trend = ""
        for tname in stock_themes_list:
            for t in leveled:
                if t["theme"] == tname and t["level"] > best_level:
                    best_level = t["level"]
                    best_trend = theme_narratives.get(tname, "")

        score_result = engine.compute_composite_score(
            stock=pool_info,
            fev_total=fev_total,
            theme_level=best_level,
            theme_trend=best_trend,
            lhb_info=lhb_data.get(code),
            research=pool_research.get(code),
            zsxq_mentions=zsxq_mentions_map.get(code, 0),
            crash_warnings=crash_warnings_map.get(code, []),
            limit_up_label=limit_up_label_map.get(code),
            bom_moat=bom_moat_map.get(code),
        )

        q = all_quotes_merged.get(code, {})

        research_list = pool_research.get(code, [])
        research_summary = ""
        if research_list:
            ratings = [r.get("rating", "") for r in research_list if r.get("rating")]
            buy_n = sum(1 for r in ratings if r in ("买入", "增持"))
            if buy_n:
                research_summary = f"研报{buy_n}家买入/增持"
            elif ratings:
                research_summary = f"研报{len(ratings)}家覆盖"

        lhb_info = lhb_data.get(code)
        lhb_summary = ""
        if lhb_info:
            net_yi = lhb_info.get("net_buy", 0) / 1e8
            if "机构" in (lhb_info.get("comment") or "") or "机构" in (lhb_info.get("reason") or ""):
                lhb_summary = f"龙虎榜机构{net_yi:+.2f}亿"
            else:
                lhb_summary = f"龙虎榜{net_yi:+.2f}亿"

        focus_pool_data.append({
            "code": code,
            "name": pool_info.get("name") or q.get("name", ""),
            "source": pool_info["source"],
            "hot_rank": pool_info.get("hot_rank", 0),
            "hot_rate": pool_info.get("hot_rate", 0),
            "rank_chg": pool_info.get("rank_chg", 0),
            "concept_tags": pool_info.get("concept_tags", []),
            "pop_tag": pool_info.get("pop_tag", ""),
            "zt_time": pool_info.get("zt_time", ""),
            "zt_boards": pool_info.get("zt_boards", 0),
            "fev_total": fev_total,
            "fev": fev,
            "composite": score_result,
            "change_pct": q.get("change_pct", 0),
            "price": q.get("price", 0),
            "amount_wan": q.get("amount_wan", 0),
            "mcap_yi": q.get("mcap_yi", 0),
            "research": research_list,
            "research_summary": research_summary,
            "lhb": lhb_info,
            "lhb_summary": lhb_summary,
            "zsxq_mentions": zsxq_mentions_map.get(code, 0),
            "crash_warnings": crash_warnings_map.get(code, []),
            "bom_moat": bom_moat_map.get(code),
            "corpus": corpus_map.get(code, {"announcements": [], "irm": [], "news": []}),
        })

    focus_pool_data.sort(key=lambda x: x["composite"]["total"], reverse=True)
    buy_cnt = sum(1 for s in focus_pool_data if s["composite"]["advice"] == "买入")
    add_cnt = sum(1 for s in focus_pool_data if s["composite"]["advice"] == "加仓")
    hold_cnt = sum(1 for s in focus_pool_data if s["composite"]["advice"] == "持有")
    print(f"  ✓ 综合评分完成 | 买入{buy_cnt} | 加仓{add_cnt} | 持有{hold_cnt}")

    # A2: 板块强弱表 — 附加成交聚合 + F/E/V 平均
    hot100_codes = {s["code"] for s in ths_hot[:100]} if ths_hot else set()
    fev_per_code = {code: fev for code, fev in fev_map.items()}
    strength_mod.attach_theme_amount_aggregates(
        strength_result, theme_pool, all_quotes_merged, zt_pool or {}, hot100_codes,
    )
    strength_mod.attach_theme_fev_aggregates(
        strength_result, theme_pool, fev_per_code,
    )

    return focus_pool_data, fev_map


# ============================================================
# Phase 5: 报告生成 & 保存
# ============================================================

def _save_and_report(trade_date, market_result, style_result, sector_result,
                     theme_result, nb_result, global_result, watchlist_results,
                     suggestions, sentiment_result, wt_result,
                     fundamentals_result, aesthetics_result, zsxq_data,
                     fev_scores, theme_stock_details, theme_groups,
                     theme_new_dirs, theme_longtail, strength_result,
                     zt_pool, concept_heat, hot_stocks, focus_pool_data,
                     limit_up_data, t0):
    md = report.render_report(
        trade_date=trade_date,
        market=market_result,
        style=style_result,
        sectors=sector_result,
        themes=theme_result,
        northbound=nb_result,
        watchlist_results=watchlist_results,
        suggestions=suggestions,
        sentiment=sentiment_result,
        global_markets=global_result,
        watchlist_themes=wt_result,
        fundamentals=fundamentals_result,
        theme_aesthetics=aesthetics_result,
        zsxq_data=zsxq_data,
        fev_scores=fev_scores,
        theme_stock_details=theme_stock_details,
        theme_groups=theme_groups,
        theme_new_dirs=theme_new_dirs,
        theme_longtail=theme_longtail,
        strength_data=strength_result,
        zt_pool=zt_pool,
        concept_heat=concept_heat,
        hot_stocks=hot_stocks,
        focus_pool_data=focus_pool_data,
        limit_up_data=limit_up_data,
    )

    report_path = REPORT_DIR / f"review_{trade_date}.md"
    report_path.write_text(md, encoding="utf-8")

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"  ✅ 复盘完成！耗时 {elapsed:.1f}s")
    print(f"  📄 报告: {report_path}")
    print(f"{'='*50}")

    amt = market_result.get("total_amount_yi", 0)
    liq = market_result.get("liquidity", "")
    pe = market_result.get("profit_effect", "")
    print(f"\n  市场情绪: {market_result['sentiment']} | 成交{amt:.0f}亿({liq}) | 赚钱效应{pe}")
    print(f"  北向资金: {nb_result['total']:+.1f}亿 ({nb_result['signal']})")

    if global_result.get("signal"):
        print(f"  外围: {global_result['signal']}")

    top_themes = theme_result.get("today", [])[:5]
    if top_themes:
        print(f"  热门题材: {'、'.join(t[0] for t in top_themes)}")

    ladder = sentiment_result.get("ladder", {})
    if ladder:
        max_b = max(ladder.keys())
        top_name = ladder[max_b][0]["name"] if ladder[max_b] else ""
        print(f"  最高板: {max_b}板 {top_name}")

    print("\n--- 操作建议 ---")
    for o in suggestions.get("operation", []):
        print(f"  {o}")
    for f in suggestions.get("focus", [])[:3]:
        print(f"  {f}")
    for r in suggestions.get("risk", [])[:3]:
        print(f"  {r}")


# ============================================================
# 主入口
# ============================================================

def main():
    args = _parse_args()
    if _dispatch_early(args):
        return

    check_deps()
    store.init_db()

    trade_date = resolve_trade_date(args.date)
    print(f"{'='*50}")
    print(f"  A股每日复盘 — {trade_date}")
    print(f"  自选股: {len(WATCHLIST)} 只")
    print(f"{'='*50}\n")

    t0 = time.time()

    # Phase 1: 拉取数据
    indices, industry, hot_df, northbound, global_data = _fetch_market_data(trade_date)
    stock_quotes, stock_klines, stock_flows, stock_lockups = _fetch_watchlist_data(WATCHLIST)

    # Phase 2: 核心分析
    (market_result, style_result, sentiment_result, sector_result,
     theme_result, nb_result, global_result, watchlist_results,
     wt_result, zt_pool, dt_pool) = _run_core_analysis(
        indices, industry, hot_df, northbound, global_data,
        stock_quotes, stock_klines, stock_flows, stock_lockups,
        trade_date, WATCHLIST)

    # Phase 3: 题材扩展
    (theme_stock_details, aesthetics_result, theme_groups,
     theme_new_dirs, theme_longtail, all_extra_klines,
     extra_quotes, hot_quotes) = _run_theme_expansion(
        hot_df, theme_result, stock_quotes, stock_klines,
        trade_date, zt_pool)

    # Phase 4: 板块强弱
    strength_result, all_klines_merged, all_quotes_merged, code_to_themes, theme_pool = _run_strength_phase(
        theme_result, all_extra_klines, stock_klines, stock_quotes,
        extra_quotes, zt_pool, trade_date, aesthetics_result)

    try:
        from bom_bridge import get_sector_linkages
        linkages = get_sector_linkages()
        if linkages:
            strength_result["bom_linkages"] = linkages
            print(f"  BOM产业链联动: {len(linkages)} 条")
    except ImportError:
        pass

    all_quotes_merged.update(hot_quotes)

    # 涨停深度分析
    theme_counts_lu = {t["theme"]: t.get("today_count", 0)
                       for t in theme_result.get("leveled", [])}
    limit_up_data = limit_up_analyze(trade_date, zt_pool, all_quotes_merged,
                                     code_to_themes, theme_counts_lu)

    # Phase 5: 基本面 + FEV
    (fundamentals_result, eps_data, shareholder_data,
     fev_scores, crash_warnings_map, suggestions,
     hot_codes, code_themes, hot_theme_names,
     theme_narratives) = _run_fundamentals_fev(
        WATCHLIST, stock_quotes, watchlist_results, hot_df,
        theme_result, market_result, sector_result, nb_result)

    # Phase 6: 人气 + 知识星球
    concept_heat, hot_stocks = _fetch_popularity()
    zsxq_data = _fetch_zsxq(trade_date)

    # Phase 7: 聚焦池
    leveled = theme_result.get("leveled", [])
    focus_pool_data, fev_map = _build_focus_pool_full(
        trade_date, WATCHLIST, zt_pool, all_quotes_merged,
        all_klines_merged, stock_klines, fev_scores,
        eps_data, shareholder_data, hot_codes, code_themes,
        hot_theme_names, theme_narratives, zsxq_data,
        sentiment_result, leveled, code_to_themes,
        crash_warnings_map, strength_result, theme_pool)

    # Phase 8: 保存市场快照
    sh = indices.get("上证指数", {})
    sz = indices.get("深证成指", {})
    cyb = indices.get("创业板指", {})
    store.save_market_snapshot(trade_date, {
        "sh_close": sh.get("price"), "sh_chg_pct": sh.get("change_pct"),
        "sz_close": sz.get("price"), "sz_chg_pct": sz.get("change_pct"),
        "cyb_close": cyb.get("price"), "cyb_chg_pct": cyb.get("change_pct"),
        "north_hgt": nb_result["hgt"], "north_sgt": nb_result["sgt"],
        "up_count": sector_result.get("breadth", {}).get("up"),
        "down_count": sector_result.get("breadth", {}).get("down"),
        "total_amount_yi": market_result.get("total_amount_yi"),
        "limit_up_count": market_result.get("limit_up_filtered"),
        "limit_up_2plus": market_result.get("limit_up_2plus"),
        "limit_down_count": market_result.get("limit_down_count"),
    })

    # Phase 9: 生成报告
    _save_and_report(
        trade_date, market_result, style_result, sector_result,
        theme_result, nb_result, global_result, watchlist_results,
        suggestions, sentiment_result, wt_result,
        fundamentals_result, aesthetics_result, zsxq_data,
        fev_scores, theme_stock_details, theme_groups,
        theme_new_dirs, theme_longtail, strength_result,
        zt_pool, concept_heat, hot_stocks, focus_pool_data,
        limit_up_data, t0)


if __name__ == "__main__":
    main()
