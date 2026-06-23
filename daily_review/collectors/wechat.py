"""微信公众号文章采集（WeWe-RSS JSON Feed）。

从 WeWe-RSS JSON Feed 拉取文章 → 逐篇抓取全文 → 去重入库。
所有文章 100% 抓正文，微信网页直接抓，PDF 下载提取。
"""
from __future__ import annotations

import html as _html
import io
import json
import re
import sys
import time
from datetime import date, datetime
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
FETCH_DELAY = 1.5  # 逐篇抓取间隔，防被ban
MAX_BODY_CHARS = 6000

# 微信 UA 池
_UA_POOL = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.43",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0.46",
    "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 Chrome/118.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.42",
]


def _alert_rss(msg: str):
    try:
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


# ============================================================
# 全文抓取
# ============================================================

def _scrape_wechat(url: str) -> str:
    """抓取微信公众号文章正文。"""
    import random
    headers = {
        "User-Agent": random.choice(_UA_POOL),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=60) as resp:
            chunks = []
            total = 0
            max_read = 500 * 1024
            while total < max_read:
                try:
                    chunk = resp.read(min(8192, max_read - total))
                except Exception:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            html_text = b"".join(chunks).decode("utf-8", errors="replace")
    except Exception:
        return ""

    m = re.search(r'id="js_content"[^>]*>(.*?)</div>', html_text, re.DOTALL)
    if not m:
        m = re.search(r'class="rich_media_content[^"]*"[^>]*>(.*?)</div>',
                      html_text, re.DOTALL)
    if not m:
        return ""

    text = m.group(1)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"微信扫一扫.*$", "", text)
    text = re.sub(r"关注该公众号.*$", "", text)
    return text[:MAX_BODY_CHARS]


def _scrape_pdf(url: str) -> str:
    """下载 PDF 并提取文字。"""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        req = Request(url, headers={"User-Agent": config.UA, "Accept": "application/pdf"})
        with urlopen(req, timeout=60) as resp:
            pdf_bytes = resp.read()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            parts = []
            for page in pdf.pages[:30]:  # 最多30页
                t = page.extract_text()
                if t:
                    parts.append(t)
            return "\n".join(parts)[:MAX_BODY_CHARS]
    except Exception:
        return ""


def _scrape_body(url: str) -> str:
    """根据 URL 类型选择抓取方式。"""
    if not url:
        return ""
    if "mp.weixin.qq.com" in url:
        return _scrape_wechat(url)
    if url.endswith(".pdf") or "jiuyangongshe" in url:
        return _scrape_pdf(url)
    # 其他 URL：尝试通用抓取
    try:
        req = Request(url, headers={"User-Agent": config.UA, "Accept": "text/html"})
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        text = _html.unescape(raw)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_BODY_CHARS]
    except Exception:
        return ""


# ============================================================
# RSS 拉取 + 全文抓取
# ============================================================

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

        # RSS 摘要作为 fallback
        description = ""
        content = it.get("content_html", "") or it.get("summary", "")
        if content:
            text = _html.unescape(content)
            text = re.sub(r"<[^>]+>", "", text)[:2000]
            description = text

        rows.append({
            "feed_source": feed_source,
            "title": title,
            "url": url,
            "pub_date": pub_date,
            "description": description,
        })

    # 逐篇抓取全文
    fetched = 0
    for i, r in enumerate(rows):
        url = r.get("url", "")
        body = _scrape_body(url)
        if body:
            r["description"] = body  # 全文覆盖 RSS 摘要
            fetched += 1
        if url:
            # 进度提示（长文章跳过短间隔）
            status = "OK" if body else "FAIL"
            title_preview = r["title"][:25]
            print(f"  [{i+1}/{len(rows)}] {status} {title_preview}...")
            time.sleep(FETCH_DELAY)

    print(f"  全文抓取: {fetched}/{len(rows)} 成功")
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
    fetched = sum(1 for r in rows if r.get("description"))
    progress(f"拉取 {len(raw)} 篇，窗口内 {len(rows)} 篇，新增 {added}，全文 {fetched}/{len(rows)}")

    from .base import daterange
    for d in daterange(since, until):
        d_str = fmt_iso(d)
        day_rows = [r for r in rows if r["pub_date"][:10] == d_str]
        if day_rows:
            _write_md(d, day_rows)

    status = "ok" if added >= 0 else "error"
    msg = f"拉取 {len(raw)} 篇，新增 {added}，全文 {fetched}/{len(rows)}"
    store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), status, msg, added)
    return {"last_date": fmt_iso(until), "added": added, "status": status,
            "message": msg}
