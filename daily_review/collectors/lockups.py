"""限售解禁采集（东财 per-code，未来解禁快照）。

逐 universe 个股拉未来解禁记录，写单份快照 md（仅 until 当日）。
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
    fmt_iso, with_retry, feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "lockups"


@with_retry(retries=1, delay=1.0, on_fail=[])
def _fetch_code(code: str) -> list[dict]:
    return data.fetch_lockup(code) or []


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("限售解禁（自选+异动·未来）", d, len(rows), universe_size)
    if not rows:
        buf.append("_universe 内近期无解禁。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path
    buf.append("| 解禁日 | 代码 | 类型 | 解禁数量 | 占总市值% |")
    buf.append("|--------|------|------|---------:|----------:|")
    for r in rows:
        shares = r.get("shares")
        ratio = r.get("ratio")
        sh = f"{shares:,.0f}" if isinstance(shares, (int, float)) else (str(shares) if shares else "")
        rt = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else (str(ratio) if ratio else "")
        buf.append(
            f"| {r.get('release_date','')} | {r['code']} | {r.get('type','')} | {sh} | {rt} |"
        )
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集限售解禁 {fmt_iso(until)}（未来解禁快照）")
    store.init_feeds_tables()

    universe = universe_fn(until)
    if not universe:
        msg = "universe 为空"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    rows = []
    fetched = 0
    for code in sorted(universe):
        for it in _fetch_code(code):
            rows.append({
                "code": code,
                "name": "",
                "release_date": it.get("date", ""),
                "type": it.get("type", ""),
                "shares": it.get("shares"),
                "ratio": it.get("ratio"),
            })
        fetched += 1
        time.sleep(0.3)

    added = store.save_lockups(rows)
    progress(f"遍历 {fetched} 只，命中 {len(rows)} 条解禁，新增 {added}")

    until_str = fmt_iso(until)
    snapshot = store.query_lockups(universe, since=until_str)
    _write_md(until, snapshot, len(universe))

    msg = f"{fetched}只，命中{len(rows)}，新增{added}"
    store.upsert_collect_status(SOURCE_NAME, until_str, "ok", msg, added)
    return {"last_date": until_str, "added": added, "status": "ok", "message": msg}
