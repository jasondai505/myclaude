"""知识星球包装，对齐 Collector 接口。

复用 zsxq_collector.sync 增量同步到 zsxq_topics 表，
然后按日切片写 reports/feeds/zsxq_YYYY-MM-DD.md。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

import store
import zsxq_collector
from .base import (
    fmt_iso, daterange,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "zsxq"


def _write_md(d: date, rows: list[dict]) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("知识星球", d, len(rows), 0)
    buf[2] = f"> 本日帖子数: **{len(rows)}**"
    if not rows:
        buf.append("_本日无帖子。_")
    else:
        research = [r for r in rows if r.get("topic_type") == "research"]
        review = [r for r in rows if r.get("topic_type") == "review"]
        other = [r for r in rows if r.get("topic_type") not in ("research", "review")]
        for label, group in [("研报/推荐", research), ("复盘/综述", review), ("其他", other)]:
            if not group:
                continue
            buf.append(f"## {label}（{len(group)}）")
            buf.append("")
            for r in group:
                _append_topic(buf, r)
            buf.append("")
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def _append_topic(buf: list[str], r: dict):
    title = (r.get("title") or "")[:120]
    author = r.get("author", "")
    readers = r.get("readers_count", 0)
    codes_str = ""
    raw_codes = r.get("stock_codes") or "[]"
    try:
        codes = json.loads(raw_codes) if isinstance(raw_codes, str) else raw_codes
        if codes:
            codes_str = f" `{','.join(codes[:5])}`"
    except Exception:
        pass
    ct = (r.get("create_time") or "")[:16]
    buf.append(f"### {ct} {author}: {title}{codes_str}  ({readers}阅读)")
    text = r.get("text") or ""
    body = text[len(title):].strip() if text.startswith(title) else text.strip()
    if body:
        preview = body[:600].replace("\n", "\n> ")
        buf.append(f"> {preview}")
        if len(body) > 600:
            buf.append(f"> ...（共{len(body)}字）")
    buf.append("")


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]] | None = None) -> dict:
    section(f"采集知识星球 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    try:
        added = zsxq_collector.sync(max_pages=10)
    except Exception as e:
        msg = f"sync 失败: {e}"
        progress(msg)
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "error", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "error", "message": msg}

    last_ok = None
    for d in daterange(since, until):
        rows = store.query_zsxq_by_date(fmt_iso(d))
        _write_md(d, rows)
        if rows:
            last_ok = d

    last_str = fmt_iso(last_ok) if last_ok else fmt_iso(until)
    msg = f"sync 新增 {added} 条"
    store.upsert_collect_status(SOURCE_NAME, last_str, "ok", msg, added or 0)
    return {"last_date": last_str, "added": added or 0, "status": "ok", "message": msg}
