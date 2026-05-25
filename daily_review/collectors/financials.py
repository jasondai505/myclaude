"""深度财务指标采集（ROE/利润率/增长率/周转率/负债率/现金流质量）。

批量调用理杏仁 fs/non_financial，一次拉取所有股票的年报财务指标。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import data
import store
from .base import (
    fmt_iso, daterange, feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "financials"


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("深度财务指标（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日无新增财务指标数据。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path

    latest_date = rows[0]["report_date"] if rows else ""
    buf.append(f"> 最新报告期: **{latest_date}**")
    buf.append("")
    buf.append(f"## 核心指标一览（{len(rows)} 只）")
    buf.append("")
    buf.append("| 代码 | 名称 | 报告期 | ROE% | 毛利率% | 净利率% | 营收YoY% | 净利YoY% | 经营CF/净利 | 负债率% |")
    buf.append("|------|------|--------|-----:|------:|------:|--------:|--------:|----------:|------:|")
    for r in rows:
        rd = r.get("report_date", "")[:10] if r.get("report_date") else ""
        buf.append(
            f"| {r['code']} | {r.get('name','')} | {rd} | "
            f"{_f(r,'roe')} | {_f(r,'gross_margin')} | {_f(r,'net_margin')} | "
            f"{_f(r,'revenue_yoy',1)} | {_f(r,'profit_yoy',1)} | "
            f"{_f(r,'opcash_to_profit',1)} | {_f(r,'debt_ratio')} |"
        )
    buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def _f(r: dict, key: str, precision: int = 2) -> str:
    v = r.get(key)
    if v is None:
        return ""
    return f"{v:.{precision}f}"


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集财务指标 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    universe = universe_fn(until)
    if not universe:
        msg = "universe 为空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    codes = sorted(universe)
    name_map = _build_name_map(codes)

    progress(f"批量拉取 {len(codes)} 只股票财务指标...")
    per_code = data.fetch_financial_indicators_lixinger(codes)

    all_rows: list[dict] = []
    ok, fail = 0, 0
    for code in codes:
        rows = per_code.get(code, [])
        if rows:
            name = name_map.get(code, code)
            for r in rows:
                r["name"] = name
            all_rows.extend(rows)
            ok += 1
        else:
            fail += 1

    added = store.save_financial_indicators(all_rows)
    progress(f"成功 {ok} / 失败 {fail} / 新增行 {added}")

    for d in daterange(since, until):
        latest_per_code = _latest_per_code(all_rows, universe)
        _write_md(d, list(latest_per_code.values()), len(universe))

    msg = f"成功{ok}/失败{fail}，新增{added}"
    store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "ok", msg, added)
    return {"last_date": fmt_iso(until), "added": added, "status": "ok", "message": msg}


def _build_name_map(codes: list[str]) -> dict[str, str]:
    quotes = data.fetch_stock_quotes(codes)
    return {c: q.get("name", c) for c, q in quotes.items()}


def _latest_per_code(rows: list[dict], universe: set[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        code = r["code"]
        if code not in universe:
            continue
        if code not in out or (r.get("report_date") or "") > (out[code].get("report_date") or ""):
            out[code] = r
    return out
