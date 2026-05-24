"""行业研报采集（东财研报中心 reportapi，按发布日期区间，行业级不按 universe 过滤）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data
import store
from collectors.base import (
    fmt_iso, daterange, feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "industry"


def _write_md(d: date, rows: list[dict]) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("行业研报", d, len(rows), 0)
    buf[2] = f"> 本日行业研报: **{len(rows)}** 篇  |  生成于 {date.today()}"
    if not rows:
        buf.append("_本日无行业研报。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path
    by_ind: dict[str, list[dict]] = {}
    for r in rows:
        by_ind.setdefault(r.get("industry") or "其他", []).append(r)
    for ind in sorted(by_ind.keys()):
        buf.append(f"## {ind}（{len(by_ind[ind])}）")
        buf.append("")
        buf.append("| 标题 | 机构 | 评级 | 链接 |")
        buf.append("|------|------|------|------|")
        for r in by_ind[ind]:
            title = (r.get("title") or "").replace("|", "丨")
            url = r.get("url") or ""
            link = f"[查看]({url})" if url else ""
            buf.append(f"| {title} | {r.get('org','')} | {r.get('rating','')} | {link} |")
        buf.append("")
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]] | None = None) -> dict:
    section(f"采集行业研报 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    since_str, until_str = fmt_iso(since), fmt_iso(until)
    rows = data.fetch_industry_research(since_str, until_str, page_size=100, max_pages=10)
    added = store.save_industry_research(rows)
    progress(f"区间命中 {len(rows)} 篇，新增 {added}")

    for d in daterange(since, until):
        _write_md(d, store.query_industry_research(fmt_iso(d)))

    status = "ok" if rows else "skip"
    msg = f"命中{len(rows)}，新增{added}"
    store.upsert_collect_status(SOURCE_NAME, until_str, status, msg, added)
    return {"last_date": until_str, "added": added, "status": status, "message": msg}
