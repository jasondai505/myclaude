"""五维情绪跟踪采集器（调研+互动易+业绩预告）。

三条腿全部零LLM成本，纯机械规则。信号追加到 Obsidian 个股档案。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import store
from deep_read.survey_tracker import detect_survey_signals
from deep_read.interaction_tracker import detect_interaction_signals
from deep_read.earnings_tracker import detect_earnings_signals

SOURCE_NAME = "sentiment_track"
RESEARCH_DIR = Path("reports") / "research_dossiers"


def _find_dossier(code: str) -> Path | None:
    code = str(code).zfill(6)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    for p in RESEARCH_DIR.glob(f"{code}*.md"):
        return p
    return None


def _append_to_dossier(code: str, today: str, section_title: str, signals: list[dict]) -> bool:
    """将信号追加到 Obsidian 个股档案（无档案则新建）。"""
    code = str(code).zfill(6)
    existing = _find_dossier(code)
    path = existing or (RESEARCH_DIR / f"{code}.md")

    if not path.exists():
        # 新建轻量档案
        sig_lines = "\n".join(
            f"- [{s.get('type','')}] {s.get('desc', str(s))}" for s in signals
        )
        path.write_text(f"""---
code: {code}
created: {today}
tags: [research_dossier]
---

# {code} — 情绪跟踪档案

## {section_title} ({today})

{sig_lines}
""", encoding="utf-8")
        return True

    content = path.read_text(encoding="utf-8")

    # 追加到文件末尾
    sig_lines = [f"\n## {section_title} ({today})"]
    for s in signals:
        w = s.get("weighted", s.get("weight", 0))
        src = s.get("source", "")
        src_tag = f"[{src}] " if src else ""
        sig_lines.append(f"- {src_tag}[{s.get('type','')}] {s.get('desc', str(s))} ({'+' if w>=0 else ''}{w}分)")

    content += "\n".join(sig_lines) + "\n"
    path.write_text(content, encoding="utf-8")
    return True


def _merge_signals(survey: list[dict], interaction: list[dict], earnings: list[dict]) -> list[dict]:
    """三源信号合并：按股票代码聚合，统一评分。

    权重: 业绩×1.0（硬数字） > 调研×0.8（机构行为） > 互动易×0.7（公司口径）
    同一股票的三源信号合并为一条记录，信号按权重降序排列。
    """
    merged: dict[str, dict] = {}

    for source_weight, source_name, src_list in [
        (0.8, "调研", survey),
        (0.7, "互动易", interaction),
        (1.0, "业绩", earnings),
    ]:
        for entry in src_list:
            code = entry["code"]
            if code not in merged:
                merged[code] = {
                    "code": code,
                    "name": entry.get("name", ""),
                    "signals": [],
                    "total_score": 0,
                    "sources": set(),
                }
            # 给每条信号打来源标签 + 加权
            for sig in entry.get("signals", []):
                sig["source"] = source_name
                sig["weighted"] = round(sig["weight"] * source_weight, 1)
            merged[code]["signals"].extend(entry.get("signals", []))
            merged[code]["sources"].add(source_name)
            merged[code]["total_score"] += round(entry.get("total_score", 0) * source_weight, 1)

    # 每股内部信号按加权权重降序
    for m in merged.values():
        m["signals"].sort(key=lambda s: -s.get("weighted", 0))
        m["sources"] = sorted(m["sources"])

    return sorted(merged.values(), key=lambda m: -m["total_score"])


def _process_one_day(today_str: str) -> dict:
    survey_signals = detect_survey_signals(today_str)
    interaction_signals = detect_interaction_signals(today_str)
    earnings_signals = detect_earnings_signals(today_str)

    merged = _merge_signals(survey_signals, interaction_signals, earnings_signals)

    updated = 0
    for m in merged:
        src_tags = "·".join(m["sources"])
        label = f"情绪信号（{src_tags}）"
        if _append_to_dossier(m["code"], today_str, label, m["signals"]):
            updated += 1

    total = len(merged)
    return {"survey": len(survey_signals), "interaction": len(interaction_signals),
            "earnings": len(earnings_signals), "total": total, "updated": updated,
            "msg": f"({today_str}) 调研{len(survey_signals)}+互动{len(interaction_signals)}+业绩{len(earnings_signals)}→合并{total}只({updated}存档)"}


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    from .base import daterange, fmt_iso

    total_survey = 0
    total_interaction = 0
    total_earnings = 0
    total_updated = 0
    total_signals = 0
    msgs = []
    last_date = fmt_iso(until)

    for d in daterange(since, until):
        day = _process_one_day(d.isoformat())
        total_survey += day["survey"]
        total_interaction += day["interaction"]
        total_earnings += day["earnings"]
        total_signals += day["total"]
        total_updated += day["updated"]
        msgs.append(day["msg"])

    msg = f"{len(msgs)}天: 调研{total_survey}+互动{total_interaction}+业绩{total_earnings}={total_signals}只({total_updated}存档)"
    store.upsert_collect_status(SOURCE_NAME, last_date, "ok", msg, total_updated)
    return {"last_date": last_date, "status": "ok", "message": msg,
            "survey_count": total_survey, "interaction_count": total_interaction,
            "earnings_count": total_earnings, "dossier_updates": total_updated}
