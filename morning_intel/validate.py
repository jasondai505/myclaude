"""盘中验证引擎 — 晨间假设 vs 实时行情 + 资金流向"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
REVIEW_BASE = BASE.parent / "daily_review"
REPORT_DIR = BASE / "reports"

from settings import (
    VALIDATE_CHG_THRESHOLD, VALIDATE_VOL_THRESHOLD, VALIDATE_FLOW_THRESHOLD,
)
from supply_chain import log_validation, validation_stats

sys.path.insert(0, str(REVIEW_BASE))
from data import fetch_stock_quotes, fetch_fund_flow, fetch_concept_heat


def _read_morning_json(today: str) -> dict | None:
    path = REPORT_DIR / f"morning_{today}.json"
    if not path.exists():
        print(f"[WARN] 晨间 JSON 不存在: {path}")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_stocks(data: dict) -> list[dict]:
    seen: set[str] = set()
    stocks: list[dict] = []
    for ev in data.get("events", []):
        for s in ev.get("target_stocks", []):
            code = str(s.get("code", "")).strip()
            if code not in seen:
                seen.add(code)
                stocks.append({
                    "code": code,
                    "name": s.get("name", ""),
                    "direction": s.get("expected_direction", "不确定"),
                    "event": ev.get("name", ""),
                })
    return stocks


def _validate_direction(predicted: str, actual_chg: float) -> tuple[int, str]:
    if predicted == "看多" and actual_chg > VALIDATE_CHG_THRESHOLD:
        return 1, "命中(看多+涨)"
    if predicted == "看空" and actual_chg < -VALIDATE_CHG_THRESHOLD:
        return 1, "命中(看空+跌)"
    if predicted == "看多" and actual_chg < -VALIDATE_CHG_THRESHOLD:
        return -1, "背离(看多但跌)"
    if predicted == "看空" and actual_chg > VALIDATE_CHG_THRESHOLD:
        return -1, "背离(看空但涨)"
    if predicted == "不确定":
        return 0, "待定(方向不确定)"
    if abs(actual_chg) <= VALIDATE_CHG_THRESHOLD:
        return 0, f"待定(幅度{actual_chg:+.2f}%未超阈值)"
    return 0, "待定"


def run(today: str = None) -> Path | None:
    if today is None:
        today = date.today().isoformat()

    data = _read_morning_json(today)
    if data is None:
        return None

    stocks = _extract_stocks(data)
    if not stocks:
        print("[WARN] 晨间报告无标的")
        return None

    codes = [s["code"] for s in stocks]

    print(f"[validate] 拉取 {len(codes)} 只标的行情...")
    quotes = fetch_stock_quotes(codes)

    print("[validate] 拉取概念板块热度...")
    concepts = fetch_concept_heat(top_n=30)

    now_str = datetime.now().strftime("%H:%M")
    rows: list[dict] = []

    for s in stocks:
        code = s["code"]
        q = quotes.get(code, {})
        chg = q.get("change_pct", 0)
        vol = q.get("vol_ratio", 0)
        amt = q.get("amount_wan", 0)
        validated, label = _validate_direction(s["direction"], chg)

        flow_signal = ""
        flows = fetch_fund_flow(code, days=1)
        if flows:
            main_in = float(flows[0].get("main_in", 0))
            if main_in > VALIDATE_FLOW_THRESHOLD:
                flow_signal = "主力流入"
            elif main_in < -VALIDATE_FLOW_THRESHOLD:
                flow_signal = "主力流出"

        row = {
            "code": code, "name": s["name"], "event": s["event"],
            "predicted": s["direction"],
            "price": q.get("price", 0), "change_pct": chg,
            "vol_ratio": vol, "amount_wan": amt,
            "flow_signal": flow_signal,
            "validated": validated, "label": label,
        }
        rows.append(row)

        log_validation(
            date=today, event_name=s["event"], code=code, name=s["name"],
            predicted_dir=s["direction"], actual_chg=chg, volume_ratio=vol,
            flow_signal=flow_signal, validated=validated,
        )

    # 生成报告
    report_path = REPORT_DIR / f"validation_{today}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(rows)
    hit = sum(1 for r in rows if r["validated"] == 1)
    miss = sum(1 for r in rows if r["validated"] == -1)
    pending = total - hit - miss

    lines = [
        f"# 盘中验证 {today} {now_str}",
        f"标的数: {total} | 命中: {hit} | 背离: {miss} | 待定: {pending}",
        f"主题: {data.get('summary', '—')}",
        "",
        "| 代码 | 名称 | 事件 | 预期 | 现价 | 涨跌% | 量比 | 成交(万) | 资金 | 结果 |",
        "|------|------|------|------|------|-------|------|----------|------|------|",
    ]
    for r in rows:
        icon = {1: "✅", -1: "❌", 0: "⏳"}.get(r["validated"], "—")
        lines.append(
            f"| {r['code']} | {r['name']} | {r['event']} | {r['predicted']} | "
            f"{r['price']:.2f} | {r['change_pct']:+.2f}% | {r['vol_ratio']:.1f} | "
            f"{r['amount_wan']:.0f} | {r['flow_signal'] or '—'} | {icon} {r['label']} |"
        )

    if concepts:
        lines.extend([
            "",
            "## 概念 Top10",
            "| 排名 | 板块 | 涨跌幅% | 净流入(亿) | 领涨股 |",
            "|------|------|---------|------------|--------|",
        ])
        for c in concepts[:10]:
            lines.append(
                f"| {c['rank']} | {c['name']} | {c['change_pct']:+.2f}% | "
                f"{c['inflow']/1e8:.2f} | {c['leader']} {c['leader_chg']:+.2f}% |"
            )

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")
    print(f"[validate] 报告已生成: {report_path}")

    stats = validation_stats(days=30)
    if stats["total"] > 0:
        print(f"[validate] 近30日累计命中率: {stats['hit_rate']}% ({stats['hits']}/{stats['total']})")

    return report_path


if __name__ == "__main__":
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    result = run(today=today)
    if result:
        print(f"OK: {result}")
    else:
        print("SKIP: 无晨间报告或无需验证")
