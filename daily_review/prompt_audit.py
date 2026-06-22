"""扫描 LLM prompt 模板，检查数值字段的数据来源是否已被注入。

用法:
    python daily_review/prompt_audit.py daily_review/claude_prompt.txt
    python daily_review/prompt_audit.py --all   # 扫所有已知 prompt
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

NUMERIC_PATTERNS = [
    # (regex, label, expected_placeholder_hint)
    (r"入场价.*?[-–].*?[元股]", "入场区间", "STOCK_CONTEXT.price"),
    (r"止损.*?[-\d]+%", "止损百分比", "STOCK_CONTEXT (持有期判定)"),
    (r"[-–]\d+[%％]\s*硬止损|[-–]\d+[%％].*止损", "硬止损比例", "持有期规则(固定值，无需注入)"),
    (r"FEV=\d+", "FEV 分数", "FEV_TABLE"),
    (r"G[\d]=\d+", "G-Factor 分数", "GFACTOR_TABLE"),
    (r"Δ=[+-]?\d+", "Δ 分数", "STOCK_CONTEXT.delta"),
    # 只在 prompt 要求计算 ATR/MA 时告警，排除免责声明
    (r"(?<!无\s)(?:入场价.*?ATR|-2\s*ATR)", "ATR 止损计算", "❌ 未注入 — 需 30日K线"),
    (r"(?<!无\s)(?:MA\d+.*?入场|入场.*?MA\d+)", "MA 入场计算", "❌ 未注入 — 需 K线数据"),
    (r"\d+日振幅", "振幅/波动率", "❌ 未注入 — 需 K线数据"),
    (r"涨跌幅.*?[+-]?\d+\.?\d*%", "涨跌幅数值", "US_INDICES / KR_JP_MARKETS"),
    (r"PE\s*分位|PE分位|pe_pct", "PE 分位数", "STOCK_CONTEXT (仅 feval enriched 注入)"),
    (r"PEG", "PEG", "STOCK_CONTEXT (未注入)"),
    (r"ROE", "ROE", "feval enriched / unified_scorer"),
    (r"毛利率", "毛利率", "feval enriched / unified_scorer"),
    (r"负债率|债务", "负债率", "feval enriched / unified_scorer"),
    (r"拥挤度", "拥挤度(文章计数)", "⚠️ LLM 估计，无结构化注入"),
    (r"\d+篇", "文章/报告计数", "⚠️ LLM 可能估计"),
    (r"前日评分", "ChokeMap 前日评分", "⚠️ 从文本推断，无结构化注入"),
]


def _extract_placeholders(text: str) -> set[str]:
    return set(re.findall(r"%%(\w+)%%", text))


def audit_prompt(prompt_path: str) -> list[dict]:
    text = Path(prompt_path).read_text(encoding="utf-8")
    placeholders = _extract_placeholders(text)
    findings = []
    for pattern, label, expected in NUMERIC_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            # 去重展示前 3 个匹配
            unique = list(dict.fromkeys(matches))[:3]
            findings.append({
                "label": label,
                "matches": unique,
                "source": expected,
                "status": "✅" if not expected.startswith("❌") and not expected.startswith("⚠️") else
                          "⚠️" if expected.startswith("⚠️") else "❌",
            })
    return findings, placeholders


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--all":
        base = Path(__file__).resolve().parent
        prompts = list(base.glob("claude_prompt.txt")) + list(base.glob("*/*prompt*"))
        if not prompts:
            print("未找到 prompt 文件")
            return
    else:
        prompts = [Path(p) for p in sys.argv[1:]]

    for pp in prompts:
        if not pp.exists():
            print(f"[SKIP] 不存在: {pp}")
            continue
        findings, placeholders = audit_prompt(str(pp))
        print(f"\n{'='*60}")
        print(f"  {pp.name}")
        print(f"  注入占位符: {len(placeholders)} 个 → {', '.join(sorted(placeholders)[:8])}...")
        print(f"{'='*60}")
        bugs = [f for f in findings if f["status"] == "❌"]
        warns = [f for f in findings if f["status"] == "⚠️"]
        for f in findings:
            marker = f["status"]
            samples = ", ".join(f["matches"])
            print(f"  {marker} {f['label']}: {f['source']}")
            if f["status"] in ("❌", "⚠️"):
                print(f"     → 匹配: {samples[:100]}")

        print(f"\n  合计: {len(findings)} 字段 | "
              f"❌ {len(bugs)} 未注入 | ⚠️ {len(warns)} 部分/LLM推断")
        if bugs:
            print(f"  🔴 需修复: {', '.join(b['label'] for b in bugs)}")

    return bool(bugs or warns)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
