"""调研+互动易情绪跟踪采集器。

运行 survey_tracker 和 interaction_tracker，将信号追加到已有 Obsidian 档案。
零 LLM 成本，纯机械规则。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import store
from deep_read.survey_tracker import detect_survey_signals
from deep_read.interaction_tracker import detect_interaction_signals

SOURCE_NAME = "sentiment_track"
RESEARCH_DIR = Path("reports") / "research_dossiers"


def _append_to_dossier(code: str, today: str, section_title: str, signals: list[dict]) -> bool:
    """将信号追加到已有 Obsidian 个股档案。"""
    path = RESEARCH_DIR / f"{code}.md"
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")

    # 追加到文件末尾
    sig_lines = [f"\n## {section_title} ({today})"]
    for s in signals:
        w = s.get("weight", 0)
        sig_lines.append(f"- [{s.get('type','')}] {s.get('desc', str(s))} ({'+' if w>=0 else ''}{w}分)")

    content += "\n".join(sig_lines) + "\n"
    path.write_text(content, encoding="utf-8")
    return True


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    today_str = since.isoformat()

    survey_signals = detect_survey_signals(today_str)
    interaction_signals = detect_interaction_signals(today_str)

    survey_appended = 0
    for s in survey_signals:
        if _append_to_dossier(s["code"], today_str, "调研信号", s["signals"]):
            survey_appended += 1

    interaction_appended = 0
    for s in interaction_signals:
        if _append_to_dossier(s["code"], today_str, "互动易信号", s["signals"]):
            interaction_appended += 1

    total = len(survey_signals) + len(interaction_signals)
    return {
        "last_date": today_str,
        "status": "ok",
        "message": f"调研{len(survey_signals)}只({survey_appended}存档) + 互动{len(interaction_signals)}只({interaction_appended}存档)",
        "survey_count": len(survey_signals),
        "interaction_count": len(interaction_signals),
        "dossier_updates": survey_appended + interaction_appended,
    }
