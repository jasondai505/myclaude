"""昨日 advice 推荐标的表现追踪。

解析昨日 advice_YYYY-MM-DD.md 中的精选标的，获取当日行情表现，
生成 reports/feeds/recap_YYYY-MM-DD.md 供 LLM 回测推荐质量。

用法: python track_recommendations.py [--date YYYY-MM-DD]
默认追踪昨天的 advice。
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

from utils import setup_console
setup_console()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data
from config import REPORT_DIR

FEEDS_DIR = REPORT_DIR / "feeds"


def _parse_advice_stocks(advice_path: Path) -> list[dict]:
    """从 advice md 中提取精选标的列表。"""
    if not advice_path.exists():
        return []
    text = advice_path.read_text(encoding="utf-8")

    stocks = []
    seen = set()

    in_section = False
    for line in text.split("\n"):
        if "精选标的" in line:
            in_section = True
            continue
        if in_section and line.strip().startswith("#"):
            break
        if not in_section or "|" not in line:
            continue

        # 模式1: **名称(代码)** 或 **名称**
        m = re.search(r"\*?\*?([^*\n]+?)\((\d{6})\)\*?\*?", line)
        if m:
            name = m.group(1).strip()
            code = m.group(2)
            if code not in seen:
                seen.add(code)
                stocks.append({"name": name, "code": code})
            continue

        # 模式2: | 名称 | 代码 | ...
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 3:
            code_m = re.search(r"\(?(\d{6})\)?", cols[2])
            if code_m:
                code = code_m.group(1)
                if code not in seen:
                    seen.add(code)
                    stocks.append({"name": cols[1], "code": code})

    return stocks


def _get_performance(stocks: list[dict], trade_date: str) -> list[dict]:
    """获取指定日的个股行情。"""
    if not stocks:
        return []
    codes = [s["code"] for s in stocks]
    quotes = data.fetch_stock_quotes(codes, batch_size=30)

    results = []
    for s in stocks:
        code = s["code"]
        llm_name = s["name"].strip("*")
        q = quotes.get(code)
        if not q:
            results.append({**s, "chg": None, "hit_limit": None, "volume_yi": None, "note": "无数据"})
            continue
        real_name = q.get("name", "")
        chg = q.get("change_pct", 0)
        limit_up = q.get("limit_up", 9999)
        price = q.get("price", 0)
        amount_wan = q.get("amount_wan", 0) or 0
        amount_yi = amount_wan / 10000

        is_zombie = (chg == 0 and amount_wan == 0)
        if is_zombie:
            results.append({**s, "chg": None, "hit_limit": None, "volume_yi": None,
                            "note": "数据异常(可能停牌/退市/代码无效)"})
            print(f"  [WARN] {llm_name}({code}) 行情异常(chg=0,amount=0)，可能代码无效")
            continue

        if real_name and llm_name != real_name:
            print(f"  [FIX] recap 代码-名称修正: {llm_name}({code}) → {real_name}({code})")
            s["name"] = real_name
            llm_name = real_name

        hit_lu = abs(price - limit_up) / limit_up < 0.005 if limit_up else False

        results.append({
            **s,
            "chg": round(chg, 2),
            "hit_limit": hit_lu,
            "volume_yi": round(amount_yi, 1),
            "note": _judge(chg, hit_lu),
        })
    return results


def _judge(chg: float, hit_limit: bool) -> str:
    if hit_limit:
        return "✅ 涨停"
    if chg > 5:
        return "🟢 大涨"
    if chg > 0:
        return "🟢 上涨"
    if chg > -3:
        return "🟡 小跌"
    if chg > -7:
        return "🟠 回调"
    return "🔴 大跌"


def _generate_recap(results: list[dict], advice_date: str, trade_date: str) -> str:
    """生成 recap markdown。"""
    lines = [
        f"# 昨日推荐回顾 — {advice_date} 推荐 {trade_date} 表现",
        "",
        f"> 追踪 {advice_date} 盘前 advice 的精选标的在 {trade_date} 的日内表现。",
        "",
    ]

    if not results:
        lines.append("_未找到推荐标的或昨日 advice 不存在。_")
        return "\n".join(lines)

    good = [r for r in results if r["chg"] is not None and r["chg"] > 0]
    bad = [r for r in results if r["chg"] is not None and r["chg"] < 0]
    limit_ups = [r for r in results if r.get("hit_limit")]
    valid = [r for r in results if r["chg"] is not None]
    avg_chg = sum(r["chg"] for r in valid) / max(len(valid), 1)

    lines.append("## 汇总")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 推荐总数 | {len(results)} |")
    lines.append(f"| 上涨/下跌 | {len(good)}/{len(bad)} |")
    lines.append(f"| 涨停 | {len(limit_ups)} |")
    lines.append(f"| 平均涨跌 | {avg_chg:+.1f}% |")
    lines.append("")

    lines.append("## 明细")
    lines.append("")
    lines.append("| 标的 | 代码 | 涨跌幅 | 成交额(亿) | 评价 |")
    lines.append("|------|------|------:|----------:|------|")

    for r in sorted(results, key=lambda x: x["chg"] if x["chg"] is not None else -999, reverse=True):
        chg = f"{r['chg']:+.2f}%" if r["chg"] is not None else "--"
        vol = f"{r['volume_yi']:.0f}" if r.get("volume_yi") else "--"
        lines.append(f"| {r['name']} | {r['code']} | {chg} | {vol} | {r.get('note','')} |")

    lines.append("")
    lines.append("## 解读")
    lines.append("")
    lines.append("注意：一日涨跌不代表推荐逻辑对错。催化剂从发酵到定价通常需要3-5个交易日，")
    lines.append("持续追踪同一逻辑的兑现进程，而非根据单日涨跌频繁切换方向。")
    if avg_chg > 2 and len(good) >= len(bad) * 2:
        lines.append("今日整体涨幅较好，关注催化剂是否已充分兑现（若已大幅上涨考虑获利了结）。")
    elif avg_chg < -2:
        lines.append("今日整体回调，区分：是催化剂被证伪（需调整），还是正常波动（逻辑未变可维持）。")
    else:
        lines.append("整体表现分化，关注涨跌两端催化逻辑的差异，同一板块内也会有个股分化。")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    target = date.today() - timedelta(days=1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--date":
        target = date.fromisoformat(sys.argv[2])

    advice_date = target.isoformat()
    advice_path = REPORT_DIR / "advice" / f"advice_{advice_date}.md"
    if not advice_path.exists():
        print(f"  [WARN] {advice_path} 不存在，尝试前一日")
        for i in range(1, 4):
            d = target - timedelta(days=i)
            p = REPORT_DIR / "advice" / f"advice_{d.isoformat()}.md"
            if p.exists():
                advice_path = p
                advice_date = d.isoformat()
                break

    print(f"追踪: {advice_date} 推荐 -> {target.isoformat()} 表现")
    stocks = _parse_advice_stocks(advice_path)
    print(f"  提取 {len(stocks)} 只标的")

    perf = _get_performance(stocks, target.isoformat())
    md = _generate_recap(perf, advice_date, target.isoformat())

    recap_path = FEEDS_DIR / f"recap_{target.isoformat()}.md"
    recap_path.parent.mkdir(parents=True, exist_ok=True)
    recap_path.write_text(md, encoding="utf-8")
    print(f"  -> {recap_path}")
    valid = [r for r in perf if r["chg"] is not None]
    if valid:
        print(f"  均涨跌: {sum(r['chg'] for r in valid) / len(valid):+.1f}%")
