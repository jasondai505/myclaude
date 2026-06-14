"""Obsidian研报档案 — 个股累积式MD文件。

与公告深研不同：研报档案以「个股」为单位，每次新信号追加更新，而非每篇独立。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from config import REPORT_DIR

RESEARCH_DIR = REPORT_DIR / "research_dossiers"


def _sanitize_name(name: str) -> str:
    return "".join(c for c in str(name) if c not in r'\/:*?"<>|').strip()


def _find_dossier(code: str) -> Path | None:
    """按代码查找已有档案（兼容新旧命名）。"""
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    code = str(code).zfill(6)
    for p in RESEARCH_DIR.glob(f"{code}*.md"):
        return p
    return None


def _dossier_path(code: str, name: str = "") -> Path:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    code = str(code).zfill(6)
    if name:
        return RESEARCH_DIR / f"{code}_{_sanitize_name(name)}.md"
    return RESEARCH_DIR / f"{code}.md"


def _build_rating_table(reports: list[dict]) -> str:
    """构建评级历史表。"""
    rows = ["| 日期 | 机构 | 评级 | 目标价 | EPS(2026E) | EPS(2027E) |",
            "|------|------|:----:|-------:|----------:|----------:|"]
    for r in sorted(reports, key=lambda x: x.get("report_date", ""), reverse=True):
        rd = r.get("report_date", "")
        inst = r.get("institution", "")
        rating = r.get("rating", "")
        tp = f"{r['target_price']:.1f}" if r.get("target_price") else "—"
        e1 = f"{r['eps_y1']:.2f}" if r.get("eps_y1") else "—"
        e2 = f"{r['eps_y2']:.2f}" if r.get("eps_y2") else "—"
        rows.append(f"| {rd} | {inst} | {rating} | {tp} | {e1} | {e2} |")
    return "\n".join(rows)


def _build_signal_log(signals: list[dict]) -> str:
    """构建信号日志。"""
    if not signals:
        return "_暂无显著信号。_"
    rows = []
    for s in signals:
        weight = s.get("weight", 0)
        sign = "+" if weight >= 0 else ""
        rows.append(f"- [{s['type']}] {s['desc']} ({sign}{weight}分)")
    return "\n".join(rows)


def upsert_stock_dossier(result: dict) -> str:
    """更新个股研报档案（新信号追加）。返回文件路径。"""
    code = str(result.get("code", "")).zfill(6)
    name = result.get("name", "")
    today = date.today().isoformat()

    # 优先查找已有档案（兼容旧命名 {code}.md）
    existing = _find_dossier(code)
    if existing:
        path = existing
    else:
        path = _dossier_path(code, name)

    signals = result.get("signals", [])
    reports = result.get("reports", [])
    llm_thesis = result.get("investment_thesis", "")
    llm_score = result.get("total_score", 0)
    domain = result.get("hunting_domain", "")
    cp = result.get("chokepoint_key", "")

    # 如果是新文件，写完整的 frontmatter + 模板
    if not path.exists():
        frontmatter = f"""---
code: {code}
name: "{name}"
domain: "{domain}"
chokepoint: "{cp}"
created: {today}
tags: [research_dossier, {domain}]
---

# {code} {name} — 研报跟踪档案

## 评级历史

{_build_rating_table(reports)}

## 最新信号 ({today})

{_build_signal_log(signals)}

## AI 投资逻辑

{llm_thesis if llm_thesis else '_待AI分析_'} (评分: {llm_score})

## 信号日志
### {today}
{_build_signal_log(signals)}
"""
        path.write_text(frontmatter, encoding="utf-8")
        return str(path)

    # 已有文件，追加更新
    content = path.read_text(encoding="utf-8")
    content = content.rstrip()

    # 更新评级历史表
    old_table_start = content.find("## 评级历史")
    old_table_end = content.find("## 最新信号")
    if old_table_start > 0 and old_table_end > old_table_start:
        new_table = _build_rating_table(reports)
        content = content[:old_table_start] + "## 评级历史\n\n" + new_table + "\n\n" + content[old_table_end:]

    # 更新最新信号
    sig_marker = "## 最新信号"
    sig_start = content.find(sig_marker)
    if sig_start > 0:
        # 找到该段落的结束（下一个 ##）
        next_section = content.find("\n## ", sig_start + len(sig_marker))
        if next_section < 0:
            next_section = len(content)
        new_sig = f"## 最新信号 ({today})\n\n{_build_signal_log(signals)}\n"
        content = content[:sig_start] + new_sig + content[next_section:]

    # 更新投资逻辑（如果LLM给了新的）
    if llm_thesis:
        logic_marker = "## AI 投资逻辑"
        logic_start = content.find(logic_marker)
        if logic_start > 0:
            next_section = content.find("\n## ", logic_start + len(logic_marker))
            if next_section < 0:
                next_section = len(content)
            new_logic = f"## AI 投资逻辑\n\n{llm_thesis} (评分: {llm_score})\n\n"
            content = content[:logic_start] + new_logic + content[next_section:]

    # 追加信号日志（避免同日重复）
    if f"### {today}" not in content:
        sig_log = f"\n### {today}\n{_build_signal_log(signals)}\n"
        content += sig_log

    path.write_text(content, encoding="utf-8")
    return str(path)
