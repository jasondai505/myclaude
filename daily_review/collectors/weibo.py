"""唐史主任司马迁微博采集 — 调用 morning_intel.weibo_watch 获取新帖，产出 feed 文件。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable

import store
from .base import (
    fmt_iso, feed_md_path, md_header, section, progress,
)

SOURCE_NAME = "weibo"


def _fetch_from_weibo_watch() -> dict:
    """调用 weibo_watch.run()，返回 posts_data + verdict。"""
    parent = str(Path(__file__).resolve().parent.parent.parent / "morning_intel")
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        import weibo_watch
        return weibo_watch.run()
    except Exception as e:
        progress(f"weibo_watch 调用失败: {e}")
        return {"status": "error", "message": str(e), "posts_data": []}


def _write_md(d: date, posts_data: list[dict], verdict: str | None) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("唐史主任司马迁微博", d, len(posts_data), 0)
    buf.append("")

    if not posts_data:
        buf.append("_本日无新帖。_")
    else:
        if verdict:
            buf.append(f"## AI 分析")
            buf.append("")
            buf.append(f"> {verdict}")
            buf.append("")

        buf.append(f"## 帖子详情")
        buf.append("")
        for p in posts_data:
            ts = p.get("created_at", "")
            text = p.get("text", "")
            reposts = p.get("reposts_count", 0)
            buf.append(f"### {ts}")
            buf.append("")
            buf.append(f"> 转发 {reposts}")
            buf.append("")
            buf.append(text[:1500])
            buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]] | None = None) -> dict:
    section(f"采集唐史主任司马迁微博 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    result = _fetch_from_weibo_watch()
    if result.get("status") == "error":
        msg = result.get("message", "weibo_watch error")
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "error", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "error", "message": msg}

    posts_data = result.get("posts_data", [])
    verdict = result.get("verdict")

    if posts_data:
        _write_md(until, posts_data, verdict)
        msg = f"新帖 {len(posts_data)} 条"
        status = "ok"
    else:
        msg = "无新帖"
        status = "skip"

    store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), status, msg, len(posts_data))
    progress(f"完成: {msg}")
    return {"last_date": fmt_iso(until), "added": len(posts_data), "status": status, "message": msg}


if __name__ == "__main__":
    from datetime import date, timedelta
    today = date.today()
    since = today - timedelta(days=3)
    result = run(since, today)
    print(result)
