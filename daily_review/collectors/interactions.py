"""互动易问答采集（深交所 + 上交所）。

按 universe 遍历个股，0/3 开头走深交所，6 开头走上交所；
北交所 920xxx 暂无对应接口，跳过。
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

SOURCE_NAME = "interactions"
PER_CODE_LIMIT = 10


@with_retry(retries=1, delay=1.0, on_fail=[])
def _fetch_szse(code: str) -> list[dict]:
    return data.fetch_irm_szse(code, limit=PER_CODE_LIMIT) or []


@with_retry(retries=1, delay=1.0, on_fail=[])
def _fetch_sse(code: str) -> list[dict]:
    return data.fetch_irm_sse(code, limit=PER_CODE_LIMIT) or []


def _normalize(code: str, raw: list[dict], platform: str) -> list[dict]:
    out = []
    for r in raw:
        q = str(r.get("question", "")).strip()
        if not q:
            continue
        out.append({
            "code": code,
            "question": q[:500],
            "answer": str(r.get("answer", ""))[:1000],
            "ask_time": str(r.get("ask_time", ""))[:16],
            "reply_time": str(r.get("reply_time", ""))[:16] or str(r.get("ask_time", ""))[:16],
            "platform": platform,
        })
    return out


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("互动易问答（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_今日 universe 内无新问答。_")
    else:
        by_code: dict[str, list[dict]] = {}
        for r in rows:
            by_code.setdefault(r["code"], []).append(r)
        for code in sorted(by_code.keys()):
            buf.append(f"## {code}")
            buf.append("")
            for r in by_code[code]:
                buf.append(f"**Q ({r['ask_time']})**: {r['question']}")
                buf.append(f"**A ({r['reply_time']})** [{r['platform']}]: {r['answer'] or '_未回复_'}")
                buf.append("")
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集互动易 {fmt_iso(since)} ~ {fmt_iso(until)}")
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
        if code.startswith("920"):
            continue
        if code.startswith(("0", "3")):
            rows = _normalize(code, _fetch_szse(code), "szse")
        elif code.startswith("6"):
            rows = _normalize(code, _fetch_sse(code), "sse")
        else:
            continue
        rows = [r for r in rows if r["reply_time"][:10] >= since_str]
        if rows:
            total_added += store.save_interactions(rows)
        fetched += 1
        time.sleep(0.3)

    progress(f"遍历 {fetched} 只，新增 {total_added}")

    for d in daterange(since, until):
        day_rows = store.query_interactions(fmt_iso(d), universe)
        _write_md(d, day_rows, len(universe))

    last_str = fmt_iso(until)
    status = "ok" if fetched > 0 else "error"
    msg = f"{fetched} 只成功，新增 {total_added}"
    store.upsert_collect_status(SOURCE_NAME, last_str, status, msg, total_added)
    return {"last_date": last_str, "added": total_added, "status": status, "message": msg}
