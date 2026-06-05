"""每日数据源自动补全总入口（框架二·基本面研报收集）

用法:
    python daily_collect.py                       # 全部源补到今天 (默认7天)
    python daily_collect.py --source announcements
    python daily_collect.py --source news,research
    python daily_collect.py --days 30
    python daily_collect.py --since 2026-05-10 --until 2026-05-20
    python daily_collect.py --status              # 只看采集状态

输出:
    reports/feeds/{source}_YYYY-MM-DD.md   各源每日报告
    reports/feed_index.md                  索引页
"""
from __future__ import annotations

import sys
import os
import argparse
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from utils import setup_console
setup_console()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import store
from config import REPORT_DIR
from collectors.base import fmt_iso, FEEDS_DIR, daterange

# universe 是所有 collector 的依赖项，必须成功
from collectors import universe

_COLLECTOR_IMPORTS = {
    "zsxq": "zsxq",
    "announcements": "announcements",
    "news": "news",
    "research": "research_reports",
    "interactions": "interactions",
    "earnings": "earnings",
    "surveys": "surveys",
    "lockups": "lockups",
    "eps": "eps_forecast",
    "industry": "industry_research",
    "financials": "financials",
    "wechat": "wechat",
}

ALL_SOURCES = {}
for _key, _mod_name in _COLLECTOR_IMPORTS.items():
    try:
        _mod = __import__(f"collectors.{_mod_name}", fromlist=[_mod_name])
        ALL_SOURCES[_key] = _mod
    except Exception as _e:
        print(f"  [WARN] collector '{_key}' 导入失败: {_e}")

SOURCE_LABELS = {
    "zsxq": "知识星球",
    "announcements": "公告",
    "news": "个股新闻",
    "research": "个股研报",
    "interactions": "互动易",
    "earnings": "业绩预告快报",
    "surveys": "机构调研",
    "lockups": "限售解禁",
    "eps": "一致预期EPS",
    "industry": "行业研报",
    "financials": "财务指标",
    "wechat": "微信公众号",
}

SOURCE_TABLE = {
    "zsxq": ("zsxq_topics", "create_time"),
    "announcements": ("announcements", "date"),
    "news": ("stock_news", "publish_time"),
    "research": ("research_reports", "report_date"),
    "interactions": ("interactions", "reply_time"),
    "earnings": ("earnings_forecast", "notice_date"),
    "surveys": ("inst_survey", "notice_date"),
    "lockups": ("lockups", "release_date"),
    "eps": ("eps_forecast", "fetched_at"),
    "industry": ("industry_research", "publish_date"),
    "financials": ("financial_indicators", "fetched_at"),
    "wechat": ("wechat_articles", "pub_date"),
}


def _parse_args():
    p = argparse.ArgumentParser(description="每日数据源自动补全")
    p.add_argument("--source", type=str, default="",
                   help="逗号分隔: zsxq,announcements,news,research,interactions,"
                        "earnings,surveys,lockups,eps,industry,financials；默认全部")
    p.add_argument("--days", type=int, default=7, help="回补天数")
    p.add_argument("--since", type=str, help="起始日期 YYYY-MM-DD")
    p.add_argument("--until", type=str, help="截止日期 YYYY-MM-DD，默认今天")
    p.add_argument("--status", action="store_true", help="只看采集状态")
    return p.parse_args()


def _resolve_dates(args) -> tuple[date, date]:
    until = date.fromisoformat(args.until) if args.until else date.today()
    if args.since:
        since = date.fromisoformat(args.since)
    else:
        since = until - timedelta(days=max(args.days - 1, 0))
    return since, until


def _resolve_sources(arg: str) -> list[str]:
    if not arg.strip():
        return list(ALL_SOURCES.keys())
    items = [s.strip() for s in arg.split(",") if s.strip()]
    bad = [s for s in items if s not in ALL_SOURCES]
    if bad:
        raise SystemExit(f"未知数据源: {bad}，可选: {list(ALL_SOURCES.keys())}")
    return items


def _print_status_table():
    store.init_feeds_tables()
    rows = store.get_collect_status()
    if not rows:
        print("暂无采集记录。")
        return
    print(f"{'源':<14} {'最新到':<12} {'上次跑':<18} {'状态':<6} {'新增':<6} 备注")
    print("-" * 80)
    for r in rows:
        label = SOURCE_LABELS.get(r["source"], r["source"])
        print(f"{label:<14} {r['last_date'] or '':<12} {r['last_run_at'] or '':<18} "
              f"{r['status'] or '':<6} {r['added_count'] or 0:<6} {r['message'] or ''}")


def _write_index():
    path = REPORT_DIR / "feed_index.md"
    today_str = fmt_iso(date.today())
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    statuses = {r["source"]: r for r in store.get_collect_status()}

    buf = []
    buf.append("# 数据源采集索引")
    buf.append("")
    buf.append(f"> 更新于 {now}")
    buf.append("")
    buf.append("## 状态总览")
    buf.append("")
    buf.append("| 数据源 | 最新到 | 上次跑 | 状态 | 7日条数 | 备注 |")
    buf.append("|--------|--------|---------|------|---------|------|")
    for src in ALL_SOURCES.keys():
        st = statuses.get(src, {})
        label = SOURCE_LABELS.get(src, src)
        table, date_col = SOURCE_TABLE.get(src, (None, None))
        count_7d = store.count_recent(table, date_col, 7) if table else 0
        emoji = {"ok": "✅", "skip": "➖", "error": "❌"}.get(st.get("status", ""), "·")
        buf.append(
            f"| {label} | {st.get('last_date','')} | {st.get('last_run_at','')} | "
            f"{emoji} {st.get('status','')} | {count_7d} | {st.get('message','')} |"
        )
    buf.append("")

    buf.append(f"## 今日 ({today_str}) 各源报告")
    buf.append("")
    for src in ALL_SOURCES.keys():
        label = SOURCE_LABELS.get(src, src)
        fp = FEEDS_DIR / f"{src}_{today_str}.md"
        if fp.exists():
            buf.append(f"- [{label}](feeds/{src}_{today_str}.md)")
        else:
            buf.append(f"- {label}: _今日未生成_")
    buf.append("")

    buf.append("## 最近 7 天报告")
    buf.append("")
    for src in ALL_SOURCES.keys():
        label = SOURCE_LABELS.get(src, src)
        buf.append(f"### {label}")
        for i in range(7):
            d = date.today() - timedelta(days=i)
            fp = FEEDS_DIR / f"{src}_{fmt_iso(d)}.md"
            if fp.exists():
                buf.append(f"- [{fmt_iso(d)}](feeds/{src}_{fmt_iso(d)}.md)")
        buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n📋 索引页: {path}")


def main():
    args = _parse_args()
    if args.status:
        _print_status_table()
        return

    since, until = _resolve_dates(args)
    sources = _resolve_sources(args.source)

    print(f"采集窗口: {fmt_iso(since)} ~ {fmt_iso(until)}")
    print(f"目标源: {', '.join(SOURCE_LABELS.get(s, s) for s in sources)}")

    results = {}
    for src in sources:
        mod = ALL_SOURCES[src]
        try:
            results[src] = mod.run(since, until, universe.daily_universe)
        except Exception as e:
            print(f"\n  ❌ {src} 异常: {e}")
            traceback.print_exc()
            store.upsert_collect_status(src, fmt_iso(until), "error", str(e)[:200], 0)
            results[src] = {"status": "error", "message": str(e)[:200]}

    # 每日刷新行业估值分位（全市场计算，独立于数据源采集）
    try:
        from daily_review import valuation
        valuation.build()
    except Exception as e:
        print(f"  [WARN] 行业估值分位构建失败: {e}")

    print(f"\n{'='*60}")
    print("采集汇总")
    print("=" * 60)
    for src, r in results.items():
        label = SOURCE_LABELS.get(src, src)
        emoji = {"ok": "✅", "skip": "➖", "error": "❌"}.get(r.get("status", ""), "·")
        print(f"  {emoji} {label:<10}: {r.get('message','')}（最新到 {r.get('last_date','')}）")

    _write_index()

    store.init_feed_cache_table()
    cached = 0
    for src in sources:
        for d in daterange(since, until):
            fp = FEEDS_DIR / f"{src}_{fmt_iso(d)}.md"
            if fp.exists():
                content = fp.read_text(encoding="utf-8")
                if store.save_feed_cache(src, fmt_iso(d), content):
                    cached += 1
    if cached:
        print(f"  📦 feed 缓存: {cached} 篇 → SQLite feed_cache")

    _build_theme_index()

    print("\n✅ 全部完成")


def _build_theme_index():
    """采集后更新主题-标的映射索引"""
    try:
        from theme_stock.build_all import build_all
        print("\n--- 主题-标的索引 ---")
        result = build_all(live_scan=True)
        print(f"  索引: chain={result['chain']} concept={result['concept']} depth={result['depth']}")
    except Exception as e:
        print(f"  [WARN] 主题索引构建失败: {e}")


if __name__ == "__main__":
    main()

