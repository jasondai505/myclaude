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
_JSON_URL = "http://111.231.44.12:4000/feeds/all.json?limit=200"
REQUEST_TIMEOUT = 90


def _alert_rss(msg: str):
    """公众号 RSS 异常时微信告警"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "morning_intel"))
        from notify import push
        push("WeWe-RSS 异常", msg)
    except Exception:
        pass


def _check_freshness(items: list[dict]) -> str | None:
    STALE_HOURS = 24
    if not items:
        return None
    dm = items[0].get("date_modified", "")
    if not dm:
        return None
    try:
        latest_dt = datetime.strptime(dm[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    age = datetime.now() - latest_dt
    if age.total_seconds() > STALE_HOURS * 3600:
        return (
            f"⚠️ 公众号RSS数据陈旧: 最新文章 {latest_dt.strftime('%m-%d %H:%M')}"
            f"（{age.total_seconds()/3600:.0f}h 前），"
            f"请检查 WeWe-RSS 是否需要重新登录: http://111.231.44.12:4000/dash"
        )
    return None


def _fetch() -> list[dict]:
    req = Request(_JSON_URL, headers={"User-Agent": config.UA,
                  "Accept": "application/json"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    items = data.get("items", [])
    stale_msg = _check_freshness(items)
    if stale_msg:
        print(f"  [STALE] {stale_msg}")
        _alert_rss(stale_msg)

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
            text = re.sub(r"<[^>]+>", "", text)[:2000]
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

    try:
        raw = _fetch()
    except Exception as e:
        msg = f"RSS 不可达: {e}"
        print(f"  [DEAD] {msg}")
        _alert_rss(f"🔴 公众号RSS断连: {e}")
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "error", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "error", "message": msg}

    if not raw:
        msg = "JSON Feed 返回空"
        _alert_rss("⚠️ 公众号RSS返回空 — 可能需要重新登录")
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "error", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "error",
                "message": msg}

    since_str = fmt_iso(since)
    rows = [r for r in raw if r["pub_date"][:10] >= since_str]

    if not rows:
        msg = f"无新文章（最新 {len(raw)} 篇均早于 {since_str}）"
        _alert_rss(f"⚠️ 公众号24h内无新文章 — 最新: {raw[0]['pub_date'][:10] if raw else '未知'}")
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
