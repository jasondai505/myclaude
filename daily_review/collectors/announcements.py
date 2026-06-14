"""公告采集（巨潮/东财汇总）。

按日拉取全市场公告，按 universe 过滤入库，再生成 md。
数据源: akshare.stock_notice_report —— 一次调用一个交易日全市场。
"""
from __future__ import annotations

import re
import time
from datetime import date
from pathlib import Path
from typing import Callable

import data
import store
from .base import (
    fmt_compact, fmt_iso, trading_dates, with_retry,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "announcements"

_ALPHA_SIGNALS = [
    "减持", "增持", "回购", "重组", "收购", "增发", "异常波动",
    "业绩预告", "业绩快报", "权益分派", "股权激励", "重大合同",
    "重大资产", "停牌", "复牌", "风险警示", "退市", "立案",
    "处罚", "诉讼", "仲裁", "实控人", "控制权", "要约",
]

def _is_key_announcement(r: dict) -> bool:
    t = r.get("type", "")
    title = r.get("title", "")
    combined = t + title
    return any(kw in combined for kw in _ALPHA_SIGNALS)


@with_retry(retries=2, delay=2.0, on_fail={})
def _fetch_day(d: date) -> dict[str, list[dict]]:
    return data.fetch_announcements_all(fmt_compact(d))


def _flatten(day_map: dict[str, list[dict]], universe: set[str]) -> list[dict]:
    rows = []
    for code, items in day_map.items():
        code6 = str(code).zfill(6)
        if universe and code6 not in universe:
            continue
        for it in items:
            url = it.get("url", "")
            m = re.search(r'/(AN\d+)\.html', url)
            rows.append({
                "code": code6,
                "name": "",
                "title": it.get("title", ""),
                "type": it.get("type", ""),
                "date": it.get("date", "")[:10],
                "url": url,
                "art_code": m.group(1) if m else "",
                "source": "em",
            })
    return rows


def _write_md(d: date, rows: list[dict], universe_size: int) -> Path:
    path = feed_md_path(SOURCE_NAME, d)
    key = [r for r in rows if _is_key_announcement(r)]
    routine = [r for r in rows if not _is_key_announcement(r)]

    buf = md_header("公告（自选+异动）", d, len(rows), universe_size)
    header_note = f"> 关键 {len(key)} 条 + 常规 {len(routine)} 条（已过滤担保/理财/制度等非事件性公告）"
    buf[2] = header_note

    if not rows:
        buf.append("_今日 universe 内无新公告。_")
        path.write_text("\n".join(buf), encoding="utf-8")
        return path

    if key:
        buf.append("## 关键公告")
        buf.append("")
        buf.append("| 代码 | 标题 | 类型 |")
        buf.append("|------|------|------|")
        for r in key:
            title = r["title"].replace("|", "丨")
            buf.append(f"| {r['code']} | {title} | {r['type']} |")

    if routine:
        buf.append("")
        buf.append("## 常规公告")
        buf.append("")
        buf.append("| 代码 | 标题 | 类型 |")
        buf.append("|------|------|------|")
        for r in routine:
            title = r["title"].replace("|", "丨")
            buf.append(f"| {r['code']} | {title} | {r['type']} |")

    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集公告 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    days = trading_dates(since, until)
    if not days:
        msg = "区间无交易日"
        store.upsert_collect_status(SOURCE_NAME, fmt_iso(until), "skip", msg, 0)
        return {"last_date": fmt_iso(until), "added": 0, "status": "skip", "message": msg}

    total_added = 0
    last_ok = None
    ok_days = 0

    for d in days:
        progress(f"{fmt_iso(d)} ...")
        day_map = _fetch_day(d)
        if not day_map:
            progress(f"  {fmt_iso(d)} 无公告或接口失败")
            time.sleep(0.5)
            continue
        universe = universe_fn(d)
        # DB 保存全市场公告（供 deep_read 等下游消费）
        full_rows = _flatten(day_map, set())
        added = store.save_announcements(full_rows)
        total_added += added
        # MD feed 仍按 universe 过滤（兼容现有复盘流程）
        md_rows = [r for r in full_rows if not universe or r["code"] in universe]
        last_ok = d
        ok_days += 1
        _write_md(d, md_rows, len(universe))
        progress(f"  {fmt_iso(d)}: 全市场 {len(full_rows)} 条, universe {len(md_rows)} 条, 新增 {added}")
        time.sleep(0.3)

    last_str = fmt_iso(last_ok) if last_ok else fmt_iso(until)
    status = "ok" if last_ok else "error"
    msg = f"成功{ok_days}/{len(days)}天"
    store.upsert_collect_status(SOURCE_NAME, last_str, status, msg, total_added)
    return {"last_date": last_str, "added": total_added, "status": status, "message": msg}
