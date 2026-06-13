"""个股研报采集（东方财富全市场API）。

一次 HTTP GET 拉全市场研报，不再逐只股票遍历。
写入 research_reports 表，按日期范围切 md。
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Callable

import store
import research as research_mod
from .base import (
    fmt_iso, daterange,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "research"


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("个股研报（全市场）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日无新研报。_")
    else:
        buf.append("| 代码 | 名称 | 标题 | 评级 | 机构 | 目标价 | EPS(今年/明年) |")
        buf.append("|------|------|------|------|------|--------|----------------|")
        for r in rows:
            title = (r.get("title") or "").replace("|", "丨")
            tp = r.get("target_price")
            tp_str = f"{tp:.2f}" if tp else ""
            e1 = f"{r.get('eps_y1'):.2f}" if r.get("eps_y1") else ""
            e2 = f"{r.get('eps_y2'):.2f}" if r.get("eps_y2") else ""
            eps_str = f"{e1}/{e2}" if e1 or e2 else ""
            buf.append(
                f"| {r.get('code','')} | {r.get('name','')} | {title} | "
                f"{r.get('rating','')} | {r.get('institution','')} | {tp_str} | {eps_str} |"
            )
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集个股研报 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    universe = universe_fn(until)

    since_str = fmt_iso(since)
    until_str = fmt_iso(until)

    # 全市场一次拉取
    all_reports = research_mod.fetch_all_research_reports(since_str, until_str)
    progress(f"全市场拉取 {len(all_reports)} 篇研报")

    # 全部入库
    added = store.save_research_reports(all_reports) if all_reports else 0

    # 按日切 MD（仍按 universe 过滤展示）
    for d in daterange(since, until):
        day_str = fmt_iso(d)
        day_rows = [r for r in all_reports if str(r.get("report_date", ""))[:10] == day_str]
        _write_md(d, day_rows, len(universe) if universe else 5000)

    last_str = until_str
    msg = f"全市场 {len(all_reports)} 篇，新增 {added}"
    store.upsert_collect_status(SOURCE_NAME, last_str, "ok", msg, added)
    return {"last_date": last_str, "added": added, "status": "ok", "message": msg}
