"""公告采集（巨潮/东财汇总）。

按日拉取全市场公告，按 universe 过滤入库，再生成 md。
数据源: akshare.stock_notice_report —— 一次调用一个交易日全市场。
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import data
import store
from collectors.base import (
    fmt_compact, fmt_iso, trading_dates, with_retry,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "announcements"


@with_retry(retries=2, delay=2.0, on_fail={})
def _fetch_day(d: date) -> dict[str, list[dict]]:
    return data.fetch_announcements_all(fmt_compact(d))


def _flatten(day_map: dict[str, list[dict]], universe: set[str]) -> list[dict]:
    rows = []
    for code, items in day_map.items():
        code6 = str(code).zfill(6)
        if universe and code6 not in universe:
            continue
        for it in items:
            rows.append({
                "code": code6,
                "name": "",
                "title": it.get("title", ""),
                "type": it.get("type", ""),
                "date": it.get("date", "")[:10],
                "url": it.get("url", ""),
                "source": "em",
            })
    return rows


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("公告（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日 universe 内无新公告。_")
    else:
        by_code: dict[str, list[dict]] = {}
        for r in rows:
            by_code.setdefault(r["code"], []).append(r)
        buf.append("| 代码 | 标题 | 类型 | 链接 |")
        buf.append("|------|------|------|------|")
        for code in sorted(by_code.keys()):
            for r in by_code[code]:
                title = r["title"].replace("|", "丨")
                url = r["url"] or ""
                link = f"[查看]({url})" if url else ""
                buf.append(f"| {code} | {title} | {r['type']} | {link} |")
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集公告 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    days = trading_dates(since, until)
    if not days:
        msg = "区间无交易日"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    total_added = 0
    last_ok = None
    ok_days = 0

    for d in days:
        progress(f"{fmt_iso(d)} ...")
        day_map = _fetch_day(d)
        if not day_map:
            progress(f"  {fmt_iso(d)} 无公告或接口失败")
            time.sleep(0.5)
            continue
        universe = universe_fn(d)
        rows = _flatten(day_map, universe)
        added = store.save_announcements(rows)
        total_added += added
        last_ok = d
        ok_days += 1
        _write_md(d, rows, len(universe))
        progress(f"  {fmt_iso(d)}: 命中 {len(rows)} 条，新增 {added}")
        time.sleep(0.3)

    last_str = fmt_iso(last_ok) if last_ok else fmt_iso(until)
    status = "ok" if last_ok else "error"
    msg = f"成功{ok_days}/{len(days)}天"
    store.upsert_collect_status(SOURCE_NAME, last_str, status, msg, total_added)
    return {"last_date": last_str, "added": total_added, "status": status, "message": msg}
