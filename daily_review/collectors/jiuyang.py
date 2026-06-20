"""韭研公社脱水研报采集 — wayzhang007 每日脱水研报/评级/强势。

从用户主页 HTML 提取 attach_list 中的 PDF 链接，
下载 PDF 文本，按日产出 reports/feeds/jiuyang_YYYY-MM-DD.md。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

import store
from .base import (
    fmt_iso, feed_md_path, md_header, section, progress,
)

SOURCE_NAME = "jiuyang"
USER_PAGE = "https://www.jiuyangongshe.com/u/42ba01f7cc33451ea0ee10c83b4941eb"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FETCH_DELAY = 2.0
MAX_PDF_CHARS = 12000


def _fetch_posts() -> list[dict]:
    """抓取用户主页，解析帖子列表（含 PDF 附件链接）。"""
    req = Request(USER_PAGE, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    posts = []
    block_pattern = re.compile(
        r'create_time:"(2026-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"'
        r'(.*?)'
        r'(?=create_time:"2026|$)',
        re.DOTALL,
    )
    for m in block_pattern.finditer(html):
        create_time = m.group(1)
        block = m.group(2)

        text_m = re.search(r'text:"((?:[^"\\]|\\.)*)"', block)
        text = ""
        if text_m:
            text = text_m.group(1)
            text = text.replace('\\"', '"').replace('\\n', '\n').replace('\\t', ' ')

        attach_m = re.search(r'attach_list:\[(.*?)\]\s*[,}]', block, re.DOTALL)
        pdfs = []
        if attach_m:
            raw = attach_m.group(1)
            for pm in re.finditer(
                r'title:"([^"]+\.pdf)",url:"(https:\\u002F\\u002Fcdn\.[^"]+\.pdf)"',
                raw,
            ):
                filename = pm.group(1)
                pdf_url = pm.group(2)
                pdf_url = pdf_url.replace('\\u002F', '/')
                pdfs.append({"filename": filename, "url": pdf_url})

        if pdfs:
            posts.append({
                "create_time": create_time,
                "date": create_time[:10],
                "text": text[:300],
                "pdfs": pdfs,
            })

    return posts


def _fetch_pdf_text(url: str) -> str:
    """下载 PDF 并解析为文本（pdfplumber / PyPDF2 / 纯文本兜底）。"""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        progress(f"PDF 下载失败: {e}")
        return ""

    if data[:5] == b"%PDF-":
        return _parse_pdf_binary(data, url)
    else:
        text = data.decode("utf-8", errors="replace")
        return text[:MAX_PDF_CHARS]


def _parse_pdf_binary(data: bytes, url: str) -> str:
    """用 pdfplumber 或 PyPDF2 解析 PDF 二进制。"""
    tmp = Path(__file__).resolve().parent.parent / "reports" / "_tmp_jiuyang.pdf"
    tmp.write_bytes(data)
    text = ""

    try:
        import pdfplumber
        with pdfplumber.open(str(tmp)) as pdf:
            parts = []
            for page in pdf.pages[:10]:
                t = page.extract_text()
                if t:
                    parts.append(t)
            text = "\n".join(parts)
    except ImportError:
        pass
    except Exception as e:
        progress(f"pdfplumber 解析失败: {e}")

    if not text:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(tmp))
            parts = []
            for page in reader.pages[:10]:
                t = page.extract_text()
                if t:
                    parts.append(t)
            text = "\n".join(parts)
        except ImportError:
            pass
        except Exception as e:
            progress(f"PyPDF2 解析失败: {e}")

    try:
        tmp.unlink()
    except Exception:
        pass

    if not text:
        progress(f"PDF 文本提取失败，需手动解析: {url}")
    return text[:MAX_PDF_CHARS]


def _write_md(d: date, pdf_contents: list[dict]) -> Path:
    """按日产出 feed 文件。"""
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("韭研公社脱水研报", d, len(pdf_contents), 0)
    buf[2] = f"> 来源: wayzhang007 | PDF 数: **{len(pdf_contents)}**"
    buf.append("")

    if not pdf_contents:
        buf.append("_本日无脱水研报。_")
    else:
        for pc in pdf_contents:
            title = pc.get("title", "未命名")
            content = pc.get("content", "")
            buf.append(f"## {title}")
            buf.append("")
            if content:
                buf.append(content)
            else:
                buf.append(f"_PDF 内容解析失败: {pc.get('url', '')}_")
            buf.append("")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]] | None = None) -> dict:
    section(f"采集韭研公社脱水研报 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    try:
        posts = _fetch_posts()
    except Exception as e:
        msg = f"页面抓取失败: {e}"
        progress(msg)
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "error", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "error", "message": msg}

    progress(f"抓取 {len(posts)} 条帖子")

    by_date: dict[str, list[dict]] = {}
    for post in posts:
        d = post["date"]
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(post)

    # 按日期范围过滤（不提前退出 — 即使 until 有 feed，since 可能还没覆盖）
    since_str = fmt_iso(since)
    until_str = fmt_iso(until)

    seen_urls = set()
    all_contents: list[dict] = []

    for d_str, day_posts in sorted(by_date.items()):
        if d_str < since_str or d_str > until_str:
            continue
        day_feed = feed_md_path(SOURCE_NAME, date.fromisoformat(d_str))
        if day_feed.exists() and day_feed.stat().st_size > 500:
            progress(f"  {d_str} feed 已存在，跳过")
            continue
        for post in day_posts:
            for pdf in post["pdfs"]:
                url = pdf["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                progress(f"  {pdf.get('filename', url[-40:])[:60]}...")
                text = _fetch_pdf_text(url)
                all_contents.append({
                    "title": pdf.get("filename", "未命名"),
                    "url": url,
                    "content": text,
                    "date": post["date"],
                })
                time.sleep(FETCH_DELAY)

    # 写入 wechat_articles 表，统一两阶段 AI 分析
    if all_contents:
        article_rows = []
        for pc in all_contents:
            article_rows.append({
                "feed_source": "韭研脱水研报",
                "title": pc["title"],
                "url": pc["url"],
                "pub_date": pc["date"],
                "description": pc["content"],
            })
        db_added = store.save_wechat_articles(article_rows)
        progress(f"DB 写入 {db_added} 篇（共 {len(article_rows)} 篇）")

    # 按日写 feed 文件
    last_ok = None
    for d_str in sorted(set(pc["date"] for pc in all_contents)):
        d = date.fromisoformat(d_str)
        day_pcs = [pc for pc in all_contents if pc["date"] == d_str]
        _write_md(d, day_pcs)
        last_ok = d

    total = len(all_contents)
    msg = f"PDF 采集 {total} 份"
    last_str = fmt_iso(last_ok) if last_ok else fmt_iso(until)
    status = "ok" if total > 0 else "skip"
    store.upsert_collect_status(SOURCE_NAME, last_str, status, msg, total)

    progress(f"完成: {msg}")
    return {"last_date": last_str, "added": total, "status": status, "message": msg}


if __name__ == "__main__":
    from datetime import date, timedelta
    today = date.today()
    since = today - timedelta(days=7)
    result = run(since, today)
    print(result)
