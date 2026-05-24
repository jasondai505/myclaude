"""个股研报采集（东方财富）。

复用 research.fetch_research_reports（按代码拉最近N天），按 universe 遍历，
存入既有的 research_reports 表，按日期范围切 md。
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable

from .. import store
from .. import research as research_mod
from .base import (
    fmt_iso, with_retry, daterange,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "research"


@with_retry(retries=1, delay=1.0, on_fail=[])
def _fetch_code(code: str, days: int) -> list[dict]:
    return research_mod.fetch_research_reports(code, days=days) or []


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("个股研报（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日 universe 内无新研报。_")
    else:
        buf.append("| 代码 | 名称 | 标题 | 评级 | 机构 | 目标价 | PDF |")
        buf.append("|------|------|------|------|------|--------|-----|")
        for r in rows:
            title = (r.get("title") or "").replace("|", "丨")
            pdf = r.get("pdf_url") or ""
            link = f"[PDF]({pdf})" if pdf else ""
            tp = r.get("target_price")
            tp_str = f"{tp:.2f}" if tp else ""
            buf.append(
                f"| {r.get('code','')} | {r.get('name','')} | {title} | "
                f"{r.get('rating','')} | {r.get('institution','')} | {tp_str} | {link} |"
            )
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集个股研报 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    universe = universe_fn(until)
    if not universe:
        msg = "universe 为空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    since_str = fmt_iso(since)
    days_lookback = max((until - since).days + 1, 7)
    total_in = 0
    fetched = 0

    for code in sorted(universe):
        rows = _fetch_code(code, days_lookback)
        rows = [r for r in rows if str(r.get("report_date", ""))[:10] >= since_str]
        if rows:
            store.save_research_reports(rows)
            total_in += len(rows)
        fetched += 1
        time.sleep(0.3)

    progress(f"遍历 {fetched}/{len(universe)} 只，命中 {total_in} 条")

    for d in daterange(since, until):
        day_rows = store.query_research_by_date(fmt_iso(d), universe)
        _write_md(d, day_rows, len(universe))

    last_str = fmt_iso(until)
    status = "ok" if fetched > 0 else "error"
    msg = f"{fetched}/{len(universe)} 只成功，命中 {total_in}"
    store.upsert_collect_status(SOURCE_NAME, last_str, status, msg, total_in)
    return {"last_date": last_str, "added": total_in, "status": status, "message": msg}
