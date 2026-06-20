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
        w = s.get("weight", 0)
        sig_lines.append(f"- [{s.get('type','')}] {s.get('desc', str(s))} ({'+' if w>=0 else ''}{w}分)")

    content += "\n".join(sig_lines) + "\n"
    path.write_text(content, encoding="utf-8")
    return True


def _process_one_day(today_str: str) -> dict:
    survey_signals = detect_survey_signals(today_str)
    interaction_signals = detect_interaction_signals(today_str)
    earnings_signals = detect_earnings_signals(today_str)

    updated = 0
    for label, signals in [
        ("调研信号", survey_signals),
        ("互动易信号", interaction_signals),
        ("业绩信号", earnings_signals),
    ]:
        for s in signals:
            if _append_to_dossier(s["code"], today_str, label, s["signals"]):
                updated += 1

    total = len(survey_signals) + len(interaction_signals) + len(earnings_signals)
    return {"survey": len(survey_signals), "interaction": len(interaction_signals),
            "earnings": len(earnings_signals), "total": total, "updated": updated,
            "msg": f"({today_str}) 调研{len(survey_signals)}+互动{len(interaction_signals)}+业绩{len(earnings_signals)}={total}只"}


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
