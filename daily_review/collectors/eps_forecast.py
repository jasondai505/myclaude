"""一致预期EPS采集（同花顺 per-code 快照）。

逐 universe 个股拉一致预期EPS（按年度），写单份快照 md（仅 until 当日）。
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

SOURCE_NAME = "eps"


@with_retry(retries=1, delay=1.0, on_fail=[])
def _fetch_code(code: str) -> list[dict]:
    return data.fetch_eps_forecast(code) or []


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    buf = md_header("一致预期EPS（自选+异动）", d, len(rows), universe_size)
    if not rows:
        buf.append("_universe 内无一致预期EPS数据。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    buf.append("| 代码 | 年度 | 预测EPS | 最高 | 最低 | 机构数 |")
    buf.append("|------|------|--------:|-----:|-----:|-------:|")
    for code in sorted(by_code.keys()):
        for r in sorted(by_code[code], key=lambda x: str(x.get("year", ""))):
            def f(x):
                return f"{x:.3f}" if isinstance(x, (int, float)) else ""
            buf.append(
                f"| {code} | {r.get('year','')} | {f(r.get('eps'))} | "
                f"{f(r.get('max_eps'))} | {f(r.get('min_eps'))} | {r.get('inst_count') or ''} |"
            )
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集一致预期EPS {fmt_iso(until)}（快照）")
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
            if it.get("eps") is None and it.get("year") is None:
                continue
            rows.append({
                "code": code,
                "name": "",
                "year": str(it.get("year", "")),
                "eps": it.get("eps"),
                "max_eps": it.get("max_eps"),
                "min_eps": it.get("min_eps"),
                "inst_count": it.get("inst_count"),
            })
        fetched += 1
        time.sleep(0.3)

    added = store.save_eps_forecast(rows)
    progress(f"遍历 {fetched} 只，命中 {len(rows)} 条，新增/更新 {added}")

    until_str = fmt_iso(until)
    _write_md(until, store.query_eps_forecast(universe), len(universe))

    msg = f"{fetched}只，命中{len(rows)}"
    store.upsert_collect_status(SOURCE_NAME, until_str, "ok", msg, added)
    return {"last_date": until_str, "added": added, "status": "ok", "message": msg}
