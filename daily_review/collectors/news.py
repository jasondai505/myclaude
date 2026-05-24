"""个股新闻采集（东方财富）。

按 universe 遍历个股拉新闻；接口只返回最近若干条，无法按历史日期补，
故策略 = 每次跑都拉最新，按日期范围过滤入库，再按日期写多份 md。
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
    fmt_iso, with_retry, daterange,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "news"
PER_CODE_LIMIT = 30


@with_retry(retries=1, delay=1.0, on_fail=[])
def _fetch_code(code: str) -> list[dict]:
    return data.fetch_stock_news(code, limit=PER_CODE_LIMIT) or []


def _normalize(code: str, raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        t = str(r.get("time", "")).strip()
        title = str(r.get("title", "")).strip()
        if not title or not t:
            continue
        if len(t) >= 10 and "/" in t[:10]:
            t = t.replace("/", "-")
        out.append({
            "code": code,
            "title": title,
            "content": str(r.get("content", ""))[:500],
            "source": str(r.get("source", "")),
            "publish_time": t[:19],
            "url": "",
        })
    return out


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("个股新闻（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日 universe 内无新闻。_")
    else:
        by_code: dict[str, list[dict]] = {}
        for r in rows:
            by_code.setdefault(r["code"], []).append(r)
        for code in sorted(by_code.keys()):
            buf.append(f"## {code}")
            buf.append("")
            for r in by_code[code]:
                t = r["publish_time"]
                src = r["source"]
                title = r["title"]
                buf.append(f"- **{t}** [{src}] {title}")
                if r["content"]:
                    buf.append(f"    > {r['content'][:200]}")
            buf.append("")
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集个股新闻 {fmt_iso(since)} ~ {fmt_iso(until)} (按当日 universe 遍历)")
    store.init_feeds_tables()

    universe = universe_fn(until)
    if not universe:
        msg = "universe 为空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    since_str = fmt_iso(since)
    total_added = 0
    fetched = 0

    for code in sorted(universe):
        raw = _fetch_code(code)
        rows = _normalize(code, raw)
        rows = [r for r in rows if r["publish_time"][:10] >= since_str]
        if rows:
            total_added += store.save_stock_news(rows)
        fetched += 1
        time.sleep(0.3)

    progress(f"遍历 {fetched}/{len(universe)} 只，新增 {total_added}")

    for d in daterange(since, until):
        day_rows = store.query_news(fmt_iso(d), universe)
        _write_md(d, day_rows, len(universe))

    last_str = fmt_iso(until)
    status = "ok" if fetched > 0 else "error"
    msg = f"{fetched}/{len(universe)} 只成功，新增 {total_added}"
    store.upsert_collect_status(SOURCE_NAME, last_str, status, msg, total_added)
    return {"last_date": last_str, "added": total_added, "status": status, "message": msg}
