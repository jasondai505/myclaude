"""微信公众号文章采集（WeWe-RSS JSON Feed）。

从 WeWe-RSS JSON Feed 拉取文章，解析后去重入库。
"""
from __future__ import annotations

import json
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

import config
import store
from .base import (
    fmt_iso, feed_md_path, md_header, section, progress,
)

SOURCE_NAME = "wechat"
_JSON_URL = "http://111.231.44.12:4000/feeds/all.json"
REQUEST_TIMEOUT = 30


def _fetch() -> list[dict]:
    req = Request(_JSON_URL, headers={"User-Agent": config.UA,
                  "Accept": "application/json"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    items = data.get("items", [])
    rows = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue

        feed_source = (it.get("author", {}) or {}).get("name", "").strip()

        pub_date = ""
        dm = it.get("date_modified", "")
        if dm:
            try:
                parsed = datetime.strptime(dm[:19], "%Y-%m-%dT%H:%M:%S")
                pub_date = parsed.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pub_date = dm[:19]

        url = (it.get("url") or "").strip()
        image = (it.get("image") or "").strip()

        description = ""
        content = it.get("content_html", "") or it.get("summary", "")
        if content:
            import re, html
            text = html.unescape(content)
            text = re.sub(r"<[^>]+>", "", text)[:500]
            description = text

        rows.append({
            "feed_source": feed_source,
            "title": title,
            "url": url,
            "pub_date": pub_date,
            "description": description,
        })

    return rows


def _write_md(d: date, rows: list[dict]) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("微信公众号", d, len(rows), 0)
    if not rows:
        buf.append("_今日无新文章。_")
    else:
        by_feed: dict[str, list[dict]] = {}
        for r in rows:
            by_feed.setdefault(r["feed_source"] or "未分类", []).append(r)

        for feed in sorted(by_feed.keys()):
            buf.append(f"## {feed}")
            buf.append("")
            for r in by_feed[feed]:
                t = r["pub_date"][:10] if r["pub_date"] else "·"
                title = r["title"]
                url = r["url"]
                desc = r.get("description", "")
                if url:
                    buf.append(f"- **{t}** [{title}]({url})")
                else:
                    buf.append(f"- **{t}** {title}")
                if desc:
                    buf.append(f"  > {desc[:300]}")
            buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]] = None) -> dict:
    section(f"采集微信公众号 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    raw = _fetch()
    if not raw:
        msg = "JSON Feed 返回空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "error", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "error",
                "message": msg}

    since_str = fmt_iso(since)
    rows = [r for r in raw if r["pub_date"][:10] >= since_str]

    if not rows:
        msg = f"无新文章（最新 {len(raw)} 篇均早于 {since_str}）"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip",
                "message": msg}

    added = store.save_wechat_articles(rows)
    progress(f"拉取 {len(raw)} 篇，窗口内 {len(rows)} 篇，新增 {added}")

    today_str = fmt_iso(until)
    today_rows = [r for r in rows if r["pub_date"][:10] == today_str]
    _write_md(until, today_rows or rows)

    status = "ok" if added >= 0 else "error"
    msg = f"拉取 {len(raw)} 篇，新增 {added}"
    store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), status, msg, added)
    return {"last_date": fmt_iso(until), "added": added, "status": status,
            "message": msg}
