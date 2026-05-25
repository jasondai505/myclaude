"""业绩预告 + 业绩快报采集（东财，按报告期末拉全市场，按 universe + 公告日期过滤）。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import data
import store
from .base import (
    fmt_iso, daterange, feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "earnings"


def _write_md(d: date, fc: list[dict], ex: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("业绩预告/快报（自选+异动）", d, len(fc) + len(ex), universe_size)
    if not fc and not ex:
        buf.append("_今日 universe 内无新业绩预告/快报。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path

    if fc:
        buf.append(f"## 业绩预告（{len(fc)}）")
        buf.append("")
        buf.append("| 代码 | 名称 | 指标 | 类型 | 变动幅度% | 变动说明 |")
        buf.append("|------|------|------|------|----------:|----------|")
        for r in fc:
            cp = f"{r['change_pct']:.1f}" if r.get("change_pct") is not None else ""
            desc = (r.get("change_desc") or "").replace("|", "丨")[:80]
            buf.append(
                f"| {r['code']} | {r.get('name','')} | {r.get('indicator','')} | "
                f"{r.get('forecast_type','')} | {cp} | {desc} |"
            )
        buf.append("")

    if ex:
        buf.append(f"## 业绩快报（{len(ex)}）")
        buf.append("")
        buf.append("| 代码 | 名称 | EPS | 营收同比% | 净利同比% | ROE% | 行业 |")
        buf.append("|------|------|----:|----------:|----------:|-----:|------|")
        for r in ex:
            def f(x, n=2):
                return f"{x:.{n}f}" if x is not None else ""
            buf.append(
                f"| {r['code']} | {r.get('name','')} | {f(r.get('eps'))} | "
                f"{f(r.get('revenue_yoy'),1)} | {f(r.get('net_profit_yoy'),1)} | "
                f"{f(r.get('roe'),1)} | {r.get('industry','')} |"
            )
        buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集业绩预告/快报 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    universe = universe_fn(until)
    if not universe:
        msg = "universe 为空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    since_str, until_str = fmt_iso(since), fmt_iso(until)
    periods = data.recent_report_periods(until_str, n=2)
    progress(f"报告期: {periods}")

    fc_rows, ex_rows = [], []
    for p in periods:
        for r in data.fetch_earnings_forecast(p):
            if since_str <= r["notice_date"] <= until_str and r["code"] in universe:
                fc_rows.append(r)
        for r in data.fetch_earnings_express(p):
            if since_str <= r["notice_date"] <= until_str and r["code"] in universe:
                ex_rows.append(r)

    added = store.save_earnings_forecast(fc_rows) + store.save_earnings_express(ex_rows)
    progress(f"预告命中 {len(fc_rows)}，快报命中 {len(ex_rows)}，新增 {added}")

    for d in daterange(since, until):
        ds = fmt_iso(d)
        _write_md(d, store.query_earnings_forecast(ds, universe),
                  store.query_earnings_express(ds, universe), len(universe))

    msg = f"预告{len(fc_rows)}+快报{len(ex_rows)}，新增{added}"
    store.upsert_collect_status(SOURCE_NAME, until_str, "ok", msg, added)
    return {"last_date": until_str, "added": added, "status": "ok", "message": msg}
