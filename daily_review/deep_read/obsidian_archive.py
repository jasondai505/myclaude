"""Obsidian 存档 — 生成带 YAML frontmatter 的 depth read markdown 文件。

兼容 Obsidian Dataview 查询和 MOC 导航。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from config import REPORT_DIR

DEEP_READ_DIR = REPORT_DIR / "deep_read"


def _sanitize_filename(s: str) -> str:
    """清理文件名中的非法字符。"""
    return s.replace("/", "-").replace("\\", "-").replace(":", "-").replace("*", "-")[:30]


def _get_event_type(ann: dict) -> str:
    """从公告类型推断事件类型标签。"""
    ann_type = ann.get("ann_type", ann.get("type", ""))
    title = ann.get("ann_title", ann.get("title", ""))
    combined = f"{ann_type} {title}"

    if any(kw in combined for kw in ["收购", "重组", "并购"]):
        if ann.get("chokepoint_key"):
            return "卡脖子收购"
        return "收购"
    if any(kw in combined for kw in ["业绩预告", "业绩快报"]):
        return "业绩预告"
    if any(kw in combined for kw in ["股权激励"]):
        return "股权激励"
    if any(kw in combined for kw in ["增持"]):
        return "增持"
    if any(kw in combined for kw in ["减持"]):
        return "减持"
    if any(kw in combined for kw in ["立案", "处罚", "监管", "问询"]):
        return "监管事件"
    if any(kw in combined for kw in ["回购"]):
        return "回购"
    if any(kw in combined for kw in ["战略合作", "框架协议"]):
        return "战略合作"
    return "其他"


def _build_yaml_frontmatter(result: dict) -> str:
    """构建 YAML frontmatter。"""
    code = str(result.get("code", "")).zfill(6)
    name = result.get("name", "")
    event_type = _get_event_type(result)
    concepts = result.get("chokepoint_context", {}).get("concepts", [])

    return f"""---
date: {result.get('date', '')}
code: {code}
name: "{name}"
event_type: "{event_type}"
deep_read_score: {result.get('total_score', 0)}
core_contradiction: {result.get('core_contradiction_score', 0)}
info_delta: {result.get('info_delta_score', 0)}
interest_alignment: {result.get('interest_alignment_score', 0)}
governance_signal: {result.get('governance_signal_score', 0)}
scenario_calibration: {result.get('scenario_calibration_score', 0)}
domain: "{result.get('hunting_domain', '')}"
chokepoint: "{result.get('chokepoint_key', '')}"
concepts: {json.dumps(concepts, ensure_ascii=False)}
time_horizon: "{result.get('time_horizon', 'month')}"
status: active
tags: [deep_read, {result.get('hunting_domain', '')}, "{event_type}"]
moc: "[[{moc_name}]]"
created: {date.today().isoformat()}
---"""


def _get_moc_name(result: dict) -> str:
    """推断 MOC 名称。"""
    domain = result.get("hunting_domain", "")
    cp = result.get("chokepoint_key", "")
    if cp:
        return f"{domain}_卡脖子_MOC"
    if domain:
        return f"{domain}_深度研读_MOC"
    return "深度研读_MOC"


def _build_table_row(label: str, score: int, max_score: int, weight: str, thesis: str) -> str:
    """构建评分表格的一行。"""
    bar_len = 20
    filled = int(score / max_score * bar_len) if max_score > 0 else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    return (
        f"| {label} | {score} | {max_score} | {weight} | {bar} |\n"
        f"| | | | | {thesis} |"
    )


def write_obsidian_file(result: dict) -> str:
    """生成一篇深度研读的 Obsidian markdown 文件。

    返回文件的相对路径。
    """
    DEEP_READ_DIR.mkdir(parents=True, exist_ok=True)

    code = str(result.get("code", "")).zfill(6)
    name = result.get("name", "未知")
    event_type = _get_event_type(result)
    d = result.get("date", date.today().isoformat())
    safe_name = _sanitize_filename(name)
    safe_event = _sanitize_filename(event_type)
    filename = f"{d}_{code}_{safe_name}_{safe_event}.md"
    filepath = DEEP_READ_DIR / filename

    moc_name = _get_moc_name(result)
    frontmatter = _build_yaml_frontmatter(result).replace("{moc_name}", moc_name)

    risk_factors = result.get("risk_factors", [])
    if isinstance(risk_factors, str):
        try:
            risk_factors = json.loads(risk_factors)
        except (json.JSONDecodeError, TypeError):
            risk_factors = [risk_factors] if risk_factors else []

    risk_list = "\n".join(f"- {r}" for r in risk_factors) if risk_factors else "- （未列出）"

    content = f"""{frontmatter}

# {code} {name} — {result.get('ann_title', '')}

## 事件摘要
{result.get('investment_thesis', result.get('core_contradiction_thesis', '（暂无）'))[:500]}

## 五维评分

| 维度 | 得分 | 满分 | 权重 | 分析 |
|------|:----:|:----:|:----:|------|
| 核心矛盾 | {result.get('core_contradiction_score', 0)} | 40 | 40% | {result.get('core_contradiction_thesis', '—')} |
| 信息增量 | {result.get('info_delta_score', 0)} | 30 | 30% | {result.get('info_delta_details', '—')} |
| 利益一致性 | {result.get('interest_alignment_score', 0)} | 15 | 15% | {result.get('interest_alignment_analysis', '—')} |
| 治理信号 | {result.get('governance_signal_score', 0)} | 10 | 10% | {result.get('governance_signal_details', '—')} |
| 场景校准 | {result.get('scenario_calibration_score', 0)} | 5 | 5% | {result.get('scenario_calibration_rationale', '—')} |
| **总分** | **{result.get('total_score', 0)}** | **100** | | |

## 投资论述

{result.get('investment_thesis', '（暂无）')}

## 风险因子

{risk_list}

## 可比先例

{result.get('comparable_precedents', '（暂无）')}

## 走势跟踪

| 日期 | 价格 | 涨跌幅 | 备注 |
|------|------|--------|------|
| {d} | — | — | 公告日，待行情确认 |

---
## 关联

- 公告原文: {result.get('ann_url', '—')}
- 催化信号: {result.get('catalyst_signal_id', '—')}
- MOC: [[{moc_name}]]
- 活跃标的索引: [[活跃标的_MOC]]

---
*由公告深度研读系统自动生成，仅供参考，不构成投资建议。*
"""
    filepath.write_text(content, encoding="utf-8")
    return str(filepath.relative_to(REPORT_DIR.parent)) if REPORT_DIR.parent != Path.cwd() else str(filepath)


def update_tracking_table(obsidian_path: str, trade_date: str, price: float,
                          chg_pct: float, note: str = "") -> None:
    """更新 Obsidian 文件中的走势跟踪表。"""
    obs_path = Path(obsidian_path)
    if not obs_path.is_absolute():
        obs_path = Path.cwd() / obs_path
    if not obs_path.exists():
        return
    content = obs_path.read_text(encoding="utf-8")
    new_row = f"| {trade_date} | {price:.2f} | {chg_pct:+.1f}% | {note} |"
    # 在「走势跟踪」表格最后一个 |---| 行之后插入
    marker = "|------|------|--------|------|"
    if marker in content:
        # 找到走势跟踪部分中的 marker
        tracking_start = content.find("## 走势跟踪")
        if tracking_start > 0:
            section = content[tracking_start:]
            marker_pos = section.find(marker)
            if marker_pos > 0:
                insert_pos = tracking_start + marker_pos + len(marker)
                updated = content[:insert_pos] + "\n" + new_row + content[insert_pos:]
                obs_path.write_text(updated, encoding="utf-8")
