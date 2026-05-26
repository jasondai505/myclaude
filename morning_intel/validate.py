"""盘中验证引擎 — 晨间假设 vs 腾讯行情 + 同花顺DDE/人气/题材共振"""
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
from data import fetch_stock_quotes, fetch_hot_themes, fetch_ths_hot_stocks


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


def _dde_signal(dde_val) -> str:
    """DDE 大单净量 → 信号标签。dde_val 可能是 float/str/None。"""
    try:
        v = float(dde_val)
    except (TypeError, ValueError):
        return "—"
    if v > 1:
        return f"DDE主力{v:+.1f}"
    if v < -1:
        return f"DDE流出{v:+.1f}"
    return f"DDE平衡{v:+.1f}"


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
    code_set = set(codes)

    # 1. 腾讯实时行情
    print(f"[validate] 腾讯行情 {len(codes)} 只...")
    quotes = fetch_stock_quotes(codes)

    # 2. 同花顺当日硬板股（DDE大单净量 + 题材归因）
    print("[validate] 同花顺硬板股/DDE...")
    themes_df = fetch_hot_themes(today)

    # 构建 DDE 交叉匹配表
    ths_dde: dict[str, dict] = {}
    theme_heat: dict[str, int] = {}
    if not themes_df.empty:
        for _, row in themes_df.iterrows():
            rc = str(row.get("代码", ""))
            reason = row.get("题材归因", "")
            if reason:
                for t in str(reason).split("+"):  # 同花顺用+连接多个题材
                    t = t.strip()
                    if t:
                        theme_heat[t] = theme_heat.get(t, 0) + 1
            ths_dde[rc] = {
                "name": row.get("名称", ""),
                "change_pct": row.get("涨幅%", 0),
                "dde": row.get("大单净量", 0),
                "reason": reason,
            }

    top_themes = sorted(theme_heat.items(), key=lambda x: -x[1])[:10]

    # 3. 同花顺人气排名
    print("[validate] 同花顺人气排名...")
    ths_hot = fetch_ths_hot_stocks(period="hour")
    ths_hot_map = {h["code"]: h for h in ths_hot}

    now_str = datetime.now().strftime("%H:%M")
    rows: list[dict] = []

    for s in stocks:
        code = s["code"]
        q = quotes.get(code, {})
        chg = q.get("change_pct", 0)
        vol = q.get("vol_ratio", 0)
        amt = q.get("amount_wan", 0)
        validated, label = _validate_direction(s["direction"], chg)

        # 资金流 = DDE 交叉匹配
        dde_info = ths_dde.get(code)
        flow_signal = _dde_signal(dde_info["dde"]) if dde_info else "—"

        # 人气排名
        ths = ths_hot_map.get(code)
        hot_badge = f"人气#{ths['rank']}" if ths else "—"

        # 硬板标记
        if dde_info:
            hot_badge += " 硬板" if hot_badge != "—" else "硬板"

        row = {
            "code": code, "name": s["name"], "event": s["event"],
            "predicted": s["direction"],
            "price": q.get("price", 0), "change_pct": chg,
            "vol_ratio": vol, "amount_wan": amt,
            "flow_signal": flow_signal, "hot_badge": hot_badge,
            "validated": validated, "label": label,
        }
        rows.append(row)

        log_validation(
            date=today, event_name=s["event"], code=code, name=s["name"],
            predicted_dir=s["direction"], actual_chg=chg, volume_ratio=vol,
            flow_signal=flow_signal, validated=validated,
        )

    # --- 报告 ---
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
        "| 代码 | 名称 | 涨跌% | 量比 | 成交(万) | DDE(同花顺) | 人气/硬板 | 结果 |",
        "|------|------|-------|------|----------|------------|----------|------|",
    ]
    for r in rows:
        icon = {1: "✅", -1: "❌", 0: "⏳"}.get(r["validated"], "—")
        lines.append(
            f"| {r['code']} | {r['name']} | {r['change_pct']:+.2f}% | "
            f"{r['vol_ratio']:.1f} | {r['amount_wan']:.0f} | "
            f"{r['flow_signal']} | {r['hot_badge']} | {icon} {r['label']} |"
        )

    if top_themes:
        lines.extend([
            "",
            "## 题材热度 Top10（当日硬板股归因，同花顺）",
            "| 题材 | 硬板股数 |",
            "|------|---------|",
        ])
        for t, count in top_themes:
            lines.append(f"| {t} | {count} |")

    risks = data.get("risk_flags", [])
    if risks:
        lines.extend(["", "## 晨间风险提示"])
        for r in risks:
            lines.append(f"- {r}")

    # --- 盘中增量情报 ---
    delta_path = REPORT_DIR / f"intraday_delta_{today}.md"
    if delta_path.exists():
        delta_content = delta_path.read_text(encoding="utf-8").strip()
        if delta_content:
            lines.append("")
            lines.append("---")
            lines.append("")
            # 去掉 delta 的一级标题，作为子 section 嵌入
            for line in delta_content.split("\n"):
                if line.startswith("# "):
                    line = "## " + line[2:]
                lines.append(line)

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
