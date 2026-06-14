"""行业/策略/宏观研报采集（eastmoney-reports 方案A）。

一次拉取三种类型研报，写入 industry_reports 表，
按日切 MD 到 feeds/。
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import store
from .base import (
    fmt_iso, daterange,
    feed_md_path, md_header, progress, section,
)

SOURCE_NAME = "industry"

SUBTYPES = [
    ("industry", "行业研报", 1),
    ("strategy", "策略研报", 2),
    ("macro",    "宏观研报", 3),
]


def _fetch(subtype: str, qtype: int, begin: str, end: str) -> list[dict]:
    """用 eastmoney-reports 拉取指定类型研报（全量翻页）。"""
    try:
        from eastmoney.report_client import EastMoneyReportClient, ReportType
    except ImportError:
        print("  [WARN] eastmoney-reports 未安装，pip install eastmoney-reports")
        return []

    client = EastMoneyReportClient(output_dir="./_em_cache")
    all_reports = []
    page = 1
    page_size = 200

    while True:
        try:
            result = client.fetch_reports(
                report_type=ReportType.INDUSTRY if qtype == 1
                else ReportType.STRATEGY if qtype == 2
                else ReportType.MACRO,
                industry_code="*", stock_code="",
                page_no=page, page_size=page_size,
                begin_time=begin, end_time=end,
            )
        except Exception as e:
            print(f"  [WARN] {subtype}研报第{page}页失败: {e}")
            break

        items = result.get("data", [])
        if not items:
            break

        for item in items:
            encode_url = (item.get("encodeUrl") or "").strip()
            pdf_url = ""
            if encode_url:
                pdf_url = f"https://pdf.dfcfw.com/pdf/h3_{encode_url}_1.pdf"

            all_reports.append({
                "title": str(item.get("title", "")),
                "institution": str(item.get("orgSName", "") or item.get("orgName", "")),
                "report_date": str(item.get("publishDate", ""))[:10],
                "industry_name": str(item.get("industryName", "")),
                "industry_code": str(item.get("industryCode", "")),
                "researcher": str(item.get("researcher", "")),
                "pdf_url": pdf_url,
                "encode_url": encode_url,
                "info_code": str(item.get("infoCode", "")),
                "report_subtype": subtype,
                "attach_pages": item.get("attachPages") or 0,
                "org_code": str(item.get("orgCode", "")),
            })

        total = result.get("hits", 0)
        if page * page_size >= total:
            break
        page += 1

    return all_reports


def _write_md(subtype: str, label: str, d: date, rows: list[dict]) -> Path:
    path = feed_md_path(subtype, d)
    buf = md_header(f"{label}（全市场）", d, len(rows), 5000)
    if not rows:
        buf.append("_今日无新研报。_")
    else:
        buf.append("| 标题 | 机构 | 行业 | 研究员 | 页数 |")
        buf.append("|------|------|------|--------|:----:|")
        for r in rows:
            title = (r.get("title") or "").replace("|", "丨")[:60]
            buf.append(
                f"| {title} | {r.get('institution','')} | "
                f"{r.get('industry_name','')} | {r.get('researcher','')[:20]} | "
                f"{r.get('attach_pages','')} |"
            )
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


def run(since: date, until: date,
        universe_fn: Callable[[date], set[str]]) -> dict:
    section(f"采集行业/策略/宏观研报 {fmt_iso(since)} ~ {fmt_iso(until)}")
    store.init_feeds_tables()

    since_str = fmt_iso(since)
    until_str = fmt_iso(until)

    total = 0
    added_total = 0

    for subtype, label, qtype in SUBTYPES:
        all_reports = _fetch(subtype, qtype, since_str, until_str)
        progress(f"{label}: {len(all_reports)} 篇")

        if all_reports:
            added = store.save_industry_reports(all_reports)
            added_total += added
            total += len(all_reports)

            for d in daterange(since, until):
                day_str = fmt_iso(d)
                day_rows = [
                    r for r in all_reports
                    if str(r.get("report_date", ""))[:10] == day_str
                ]
                _write_md(subtype, label, d, day_rows)

    msg = f"行业/策略/宏观 {total} 篇，新增 {added_total}"
    store.upsert_collect_status(SOURCE_NAME, until_str, "ok", msg, added_total)
    return {"last_date": until_str, "added": added_total, "status": "ok", "message": msg}
