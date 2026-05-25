"""从昨日复盘报告提取结构化摘要，减少 LLM 上下文消耗。

输出 reports/feeds/review_summary_YYYY-MM-DD.md，包含：
- 大盘情绪/成交/涨跌/北向
- 主线/新兴/退潮题材
- FEV top 标的
- 涨停结构统计

用法: python review_summary.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

from utils import setup_console
setup_console()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import REPORT_DIR

FEEDS_DIR = REPORT_DIR / "feeds"


def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val.startswith("[") and val.endswith("]"):
            val = [v.strip().strip('"') for v in val[1:-1].split(",") if v.strip()]
        fm[key] = val
    return fm


def _extract_style(text: str) -> str:
    m = re.search(r"\*\*大小盘\*\*[：:]\s*(.+?)(?:\n|$)", text)
    size = m.group(1) if m else ""
    m = re.search(r"\*\*成长/价值\*\*[：:]\s*(.+?)(?:\n|$)", text)
    gv = m.group(1) if m else ""
    return f"{size}, {gv}".strip(", ")


def _count_limit_types(text: str) -> dict:
    logic = len(re.findall(r"纯逻辑|偏逻辑", text))
    emotion = len(re.findall(r"纯情绪|偏情绪", text))
    mixed = len(re.findall(r"混合", text))
    return {"logic": logic, "emotion": emotion, "mixed": mixed}


def generate(review_path: Path, trade_date: str) -> str:
    if not review_path.exists():
        return f"# 复盘摘要 {trade_date}\n\n_复盘报告不存在。_\n"

    text = review_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)

    style = _extract_style(text)
    lines = [
        f"# 复盘摘要 {trade_date}",
        "",
        "## 大盘",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 情绪 | {fm.get('sentiment', '--')} |",
        f"| 成交 | {fm.get('amount_yi', '--')} 亿 |",
        f"| 上涨占比 | {fm.get('up_pct', '--')}% |",
        f"| 涨停 | {fm.get('limit_up', '--')} 家 |",
        f"| 北向 | {fm.get('northbound', '--')} 亿 |",
        f"| 风格 | {style} |",
        "",
    ]

    mainline = fm.get("mainline", [])
    if mainline:
        lines.append("## 主线题材")
        lines.append("")
        lines.append(", ".join(str(x) for x in mainline))
        lines.append("")

    emerging = fm.get("emerging", [])
    if emerging:
        lines.append("## 新兴题材")
        lines.append("")
        lines.append(", ".join(str(x) for x in emerging))
        lines.append("")

    fading = fm.get("fading", [])
    if fading:
        lines.append("## 退潮题材")
        lines.append("")
        lines.append(", ".join(str(x) for x in fading))
        lines.append("")

    fev_top = fm.get("fev_top", [])
    if fev_top:
        lines.append("## FEV Top 标的")
        lines.append("")
        lines.append("| 标的 | FEV 总分 |")
        lines.append("|------|:--------:|")
        for item in fev_top:
            parts = str(item).split("(")
            name = parts[0]
            score = parts[1].rstrip(")") if len(parts) > 1 else "--"
            lines.append(f"| {name} | {score} |")
        lines.append("")

    lt = _count_limit_types(text)
    if any(lt.values()):
        lines.append("## 涨停结构")
        lines.append("")
        lines.append(f"| 类型 | 数量 |")
        lines.append(f"|------|:----:|")
        lines.append(f"| 逻辑驱动 | {lt['logic']} |")
        lines.append(f"| 情绪驱动 | {lt['emotion']} |")
        lines.append(f"| 混合 | {lt['mixed']} |")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    target = date.today() - timedelta(days=1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--date":
        target = date.fromisoformat(sys.argv[2])

    review_path = REPORT_DIR / f"review_{target.isoformat()}.md"
    md = generate(review_path, target.isoformat())

    out = FEEDS_DIR / f"review_summary_{target.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"  -> {out}  ({len(md)} chars)")
