"""机构调研采集（东财 stock_jgdy_tj_em，按报告期拉，按 universe + 公告日期过滤）。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import data
import store
from .base import (
    fmt_iso, daterange, feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "surveys"


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("机构调研（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日 universe 内无新机构调研。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path
    buf.append("| 代码 | 名称 | 接待机构数 | 接待方式 | 接待日期 |")
    buf.append("|------|------|-----------:|----------|----------|")
    for r in rows:
        method = (r.get("method") or "").replace("|", "丨")[:30]
        buf.append(
            f"| {r['code']} | {r.get('name','')} | {r.get('inst_count',0)} | "
            f"{method} | {r.get('survey_date','')} |"
        )
    buf.append("")
    for r in rows:
        att = (r.get("attendees") or "").strip()
        if att:
            buf.append(f"- **{r['code']} {r.get('name','')}** 接待: {att[:200]}")
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集机构调研 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    universe = universe_fn(until)
    if not universe:
        msg = "universe 为空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    since_str, until_str = fmt_iso(since), fmt_iso(until)
    periods = data.recent_report_periods(until_str, n=2)
    progress(f"报告期: {periods}")

    rows = []
    seen = set()
    for p in periods:
        for r in data.fetch_inst_survey(p):
            key = (r["code"], r["survey_date"], r["notice_date"])
            if key in seen:
                continue
            if since_str <= r["notice_date"] <= until_str and r["code"] in universe:
                rows.append(r)
                seen.add(key)

    added = store.save_inst_survey(rows)
    progress(f"命中 {len(rows)}，新增 {added}")

    for d in daterange(since, until):
        _write_md(d, store.query_inst_survey(fmt_iso(d), universe), len(universe))

    msg = f"命中{len(rows)}，新增{added}"
    store.upsert_collect_status(SOURCE_NAME, until_str, "ok", msg, added)
    return {"last_date": until_str, "added": added, "status": "ok", "message": msg}
