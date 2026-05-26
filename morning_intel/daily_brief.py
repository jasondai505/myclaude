"""交易日简报 — 合并晨间情报+盘中验证+盘后复盘为一份完整日报"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REPORT_DIR = BASE / "reports"
REVIEW_REPORT_DIR = BASE.parent / "daily_review" / "reports"


def _read_frontmatter(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            # 尝试解析 JSON 数组
            if val.startswith("[") and val.endswith("]"):
                try:
                    arr = json.loads(val)
                    fm[key] = arr
                except json.JSONDecodeError:
                    fm[key] = val
            else:
                fm[key] = val
    return fm


def _fmt_list(val) -> str:
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val) if val else "—"


def _morning_summary(today: str) -> str:
    json_path = REPORT_DIR / f"morning_{today}.json"
    if not json_path.exists():
        return "_晨间报告未生成_"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    lines = ["## 一、晨间情报 — 催化事件与标的"]
    lines.append("")
    lines.append(f"主题: {data.get('summary', '—')}")
    lines.append("")

    for ev in data.get("events", []):
        name = ev.get("name", "")
        confidence = ev.get("confidence", "")
        lines.append(f"### {name} (置信度: {confidence})")
        lines.append(f"{ev.get('narrative', '')[:300]}")
        lines.append("")
        lines.append("| 代码 | 名称 | 方向 | 依据 |")
        lines.append("|------|------|------|------|")
        for s in ev.get("target_stocks", []):
            code = s.get("code", "")
            sname = s.get("name", "")
            direction = s.get("expected_direction", "")
            rationale = s.get("rationale", "")[:80]
            lines.append(f"| {code} | {sname} | {direction} | {rationale} |")
        lines.append("")

    return "\n".join(lines)


def _validation_summary(today: str) -> str:
    fm = _read_frontmatter(REPORT_DIR / f"validation_{today}.md")

    lines = ["## 二、盘中验证 — 假设检验"]
    lines.append("")

    if fm:
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 标的数 | {fm.get('total', '—')} |")
        lines.append(f"| 命中 | {fm.get('hit', '—')} |")
        lines.append(f"| 背离 | {fm.get('miss', '—')} |")
        lines.append(f"| 待定 | {fm.get('pending', '—')} |")
        lines.append(f"| 命中率 | {fm.get('hit_rate', '—')}% |")
    else:
        # 无 frontmatter: 尝试从旧格式报告提取
        vp = REPORT_DIR / f"validation_{today}.md"
        if vp.exists():
            text = vp.read_text(encoding="utf-8")
            import re
            m = re.search(r"标的数:\s*(\d+)\s*\|\s*命中:\s*(\d+)\s*\|\s*背离:\s*(\d+)\s*\|\s*待定:\s*(\d+)", text)
            if m:
                total, hit, miss, pending = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                hr = round(hit / total * 100, 1) if total > 0 else 0
                lines.append("| 指标 | 数值 |")
                lines.append("|------|------|")
                lines.append(f"| 标的数 | {total} |")
                lines.append(f"| 命中 | {hit} |")
                lines.append(f"| 背离 | {miss} |")
                lines.append(f"| 待定 | {pending} |")
                lines.append(f"| 命中率 | {hr}% |")
            else:
                lines.append("_验证报告格式无法解析_")
        else:
            lines.append("_盘中验证未运行_")

    lines.append("")
    validate_path = REPORT_DIR / f"validation_{today}.md"
    if validate_path.exists():
        lines.append(f"> 详细验证: [validation_{today}.md](validation_{today}.md)")

    return "\n".join(lines)


def _review_summary(today: str) -> str:
    fm = _read_frontmatter(REVIEW_REPORT_DIR / f"review_{today}.md")
    if not fm:
        return "_盘后复盘未生成_"

    lines = ["## 三、盘后复盘 — 市场全景"]
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 市场情绪 | {fm.get('sentiment', '—')} |")
    lines.append(f"| 成交额(亿) | {fm.get('amount_yi', '—')} |")
    lines.append(f"| 涨停数 | {fm.get('limit_up', '—')} |")
    lines.append(f"| 上涨家数% | {fm.get('up_pct', '—')}% |")
    lines.append(f"| 北向资金(亿) | {fm.get('northbound', '—')} |")
    lines.append(f"| NVDA | {fm.get('nvda', '—')} |")
    lines.append(f"| 主线题材 | {_fmt_list(fm.get('mainline'))} |")
    lines.append(f"| 新兴题材 | {_fmt_list(fm.get('emerging'))} |")
    lines.append(f"| 退潮题材 | {_fmt_list(fm.get('fading'))} |")
    lines.append("")
    lines.append(f"> 完整复盘: ../../daily_review/reports/review_{today}.md")
    lines.append("")

    return "\n".join(lines)


def _accuracy_summary(today: str) -> str:
    fm = _read_frontmatter(REPORT_DIR / f"validation_{today}.md")

    lines = ["## 四、AI 预测准确性"]
    lines.append("")

    total = int(fm.get("total", 0)) if fm else 0
    hit = int(fm.get("hit", 0)) if fm else 0
    miss = int(fm.get("miss", 0)) if fm else 0

    if not fm:
        vp = REPORT_DIR / f"validation_{today}.md"
        if vp.exists():
            import re
            text = vp.read_text(encoding="utf-8")
            m = re.search(r"标的数:\s*(\d+)\s*\|\s*命中:\s*(\d+)\s*\|\s*背离:\s*(\d+)", text)
            if m:
                total, hit, miss = int(m[1]), int(m[2]), int(m[3])

    if total > 0:
        hit_rate = round(hit / total * 100, 1)
        verdict = "优秀" if hit_rate >= 70 else "良好" if hit_rate >= 50 else "待改善"
        lines.append(f"命中率 **{hit_rate}%** ({hit}/{total}) — {verdict}")
        if miss > 0:
            lines.append(f"背离 **{miss}** 只标的，需复盘的错误预测。")
    else:
        lines.append("_本日无有效预测数据_")
    lines.append("")

    return "\n".join(lines)


def run(today: str = None) -> Path | None:
    if today is None:
        today = date.today().isoformat()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        f"# 交易日简报 {today}",
        "",
        f"> 生成时间: {now}",
        "> 流水线: 晨间情报(6:00) → 盘中验证(10:30/14:00) → 盘后复盘(17:50)",
        "",
        _morning_summary(today),
        _validation_summary(today),
        _review_summary(today),
        _accuracy_summary(today),
        "---",
        "",
        "*自动生成，仅供参考，不构成投资建议。*",
    ]

    brief_path = REPORT_DIR / f"daily_brief_{today}.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"[daily_brief] 报告已生成: {brief_path}")
    return brief_path


if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    result = run(today=today)
    if result:
        print(f"OK: {result}")
