"""边际变化跟踪引擎 — 多源数据 diff，构建可追溯对比链"""
from __future__ import annotations

from datetime import datetime, timedelta, date
from pathlib import Path

import data
import store
from config import WATCHLIST


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _name_map(codes: list[str]) -> dict[str, str]:
    quotes = data.fetch_stock_quotes(codes)
    return {c: q.get("name", c) for c, q in quotes.items()}


# ============================================================
# 主入口
# ============================================================


def detect_changes(today: str | None = None, dry_run: bool = False) -> list[dict]:
    """遍历自选股 + 五大数据源，检测边际变化并写入 DB。"""
    today = today or _today()
    names = _name_map(WATCHLIST)
    all_changes: list[dict] = []

    all_changes.extend(_diff_eps_forecast(today, names))
    all_changes.extend(_diff_earnings(today, names))
    all_changes.extend(_diff_inst_survey(today, names))
    all_changes.extend(_diff_research(today, names))
    all_changes.extend(_diff_financials(today, names))

    saved = 0
    for c in all_changes:
        if dry_run:
            continue
        prev = store.get_latest_marginal(c["code"], c["theme"])
        prev_id = prev["id"] if prev else None
        rid = store.save_marginal_change(
            date=c["date"], code=c["code"], name=c["name"],
            theme=c["theme"], direction=c["direction"],
            content=c["content"],
            previous_value=c.get("previous_value"),
            current_value=c.get("current_value"),
            source=c["source"], source_detail=c.get("source_detail"),
            previous_record_id=prev_id,
        )
        if rid:
            saved += 1

    if not dry_run and saved:
        print(f"  [边际变化] 写入 {saved} 条 ({today})")
    return all_changes


# ============================================================
# 各数据源 diff
# ============================================================


def _diff_eps_forecast(today: str, names: dict[str, str]) -> list[dict]:
    """一致预期EPS变化：当前快照 vs marginal_changes 最新记录。

    eps_forecast 表是快照型（覆盖写入），自身无法 diff。
    依赖 marginal_changes 中上一次记录的值做对比。
    """
    changes: list[dict] = []
    universe = set(WATCHLIST)
    rows = store.query_eps_forecast(universe)
    if not rows:
        return changes

    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)

    for code, years in by_code.items():
        name = names.get(code, code)
        for r in sorted(years, key=lambda x: str(x.get("year", ""))):
            year = str(r.get("year", ""))
            eps = r.get("eps")
            inst = r.get("inst_count")
            if eps is None:
                continue

            prev = store.get_latest_marginal(code, f"一致预期EPS_{year}")
            if prev is None:
                changes.append({
                    "date": today, "code": code, "name": name,
                    "theme": f"一致预期EPS_{year}",
                    "direction": "首次记录",
                    "content": f"{year}年一致预期EPS首次记录为 {eps:.3f}（{inst or '?'}家机构）",
                    "previous_value": None,
                    "current_value": f"{eps:.3f}",
                    "source": "eps_forecast",
                    "source_detail": f"{year}年一致预期",
                })
                continue

            prev_val = prev.get("current_value")
            try:
                prev_eps = float(prev_val) if prev_val else None
            except (ValueError, TypeError):
                prev_eps = None

            if prev_eps is None or abs(eps - prev_eps) < 0.001:
                continue

            if eps > prev_eps:
                direction = "边际向好"
                verb = "上修"
            else:
                direction = "边际下滑"
                verb = "下修"

            delta_pct = (eps - prev_eps) / abs(prev_eps) * 100 if prev_eps else 0
            changes.append({
                "date": today, "code": code, "name": name,
                "theme": f"一致预期EPS_{year}",
                "direction": direction,
                "content": f"{year}年一致预期EPS {verb}：{prev_eps:.3f}→{eps:.3f}（{delta_pct:+.1f}%，{inst or '?'}家机构）",
                "previous_value": f"{prev_eps:.3f}",
                "current_value": f"{eps:.3f}",
                "source": "eps_forecast",
                "source_detail": f"{year}年一致预期",
            })

    return changes


def _diff_earnings(today: str, names: dict[str, str]) -> list[dict]:
    """业绩预告/快报变化：当日新发布的预告。"""
    changes: list[dict] = []
    universe = set(WATCHLIST)

    forecasts = store.query_earnings_forecast(today, universe)
    for r in forecasts:
        code = r["code"]
        name = names.get(code, code)
        period = r.get("period", "")
        ftype = r.get("forecast_type", "")
        chg_pct = r.get("change_pct")
        desc = (r.get("change_desc") or "")[:60]

        prev = store.get_latest_marginal(code, f"业绩预告_{period}")
        dir_label = "边际向好" if (chg_pct or 0) > 0 else "边际下滑"
        detail = f"{ftype} | 变动 {chg_pct:+.1f}%" if chg_pct is not None else ftype
        if desc:
            detail += f" | {desc}"

        changes.append({
            "date": today, "code": code, "name": name,
            "theme": f"业绩预告_{period}",
            "direction": "首次记录" if prev is None else dir_label,
            "content": f"发布{period}业绩预告：{detail}",
            "previous_value": prev.get("current_value") if prev else None,
            "current_value": f"{chg_pct}" if chg_pct is not None else ftype,
            "source": "earnings",
            "source_detail": f"{period} 业绩预告（{ftype}）",
        })

    expresses = store.query_earnings_express(today, universe)
    for r in expresses:
        code = r["code"]
        name = names.get(code, code)
        period = r.get("period", "")
        roe = r.get("roe")
        rev = r.get("revenue_yoy")
        profit = r.get("net_profit_yoy")

        prev = store.get_latest_marginal(code, f"业绩快报_{period}")
        parts = []
        if rev is not None: parts.append(f"营收同比 {rev:+.1f}%")
        if profit is not None: parts.append(f"净利同比 {profit:+.1f}%")
        if roe is not None: parts.append(f"ROE {roe:.1f}%")

        changes.append({
            "date": today, "code": code, "name": name,
            "theme": f"业绩快报_{period}",
            "direction": "首次记录" if prev is None else "符合预期",
            "content": f"发布{period}业绩快报：{'，'.join(parts)}",
            "previous_value": prev.get("current_value") if prev else None,
            "current_value": f"ROE={roe}" if roe else "",
            "source": "earnings",
            "source_detail": f"{period} 业绩快报",
        })

    return changes


def _diff_inst_survey(today: str, names: dict[str, str]) -> list[dict]:
    """机构调研变化：当日新调研 vs 历史均值。"""
    changes: list[dict] = []
    universe = set(WATCHLIST)

    today_rows = store.query_inst_survey(today, universe)
    if not today_rows:
        return changes

    by_code: dict[str, list[dict]] = {}
    for r in today_rows:
        by_code.setdefault(r["code"], []).append(r)

    thirty_ago = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")

    for code, rows in by_code.items():
        name = names.get(code, code)
        today_count = len(rows)
        inst_count = rows[0].get("inst_count") or today_count

        prev = store.get_latest_marginal(code, "机构调研")
        hist = store.query_marginal_changes(
            code=code, theme="机构调研",
            date_from=thirty_ago, date_to=today, limit=50,
        )
        hist_count = len([h for h in hist if h["date"] != today])

        if prev is None:
            changes.append({
                "date": today, "code": code, "name": name,
                "theme": "机构调研",
                "direction": "首次记录",
                "content": f"首次检测到机构调研，当日 {today_count} 场" + (f"，{inst_count}家机构" if inst_count else ""),
                "previous_value": None,
                "current_value": str(today_count),
                "source": "inst_survey",
                "source_detail": f"当日 {today_count} 场调研",
            })
            continue

        prev_count = int(prev.get("current_value") or 0)
        avg_30d = max(hist_count / 30.0, 0.1) if hist_count > 0 else 0.1

        if today_count >= avg_30d * 2:
            direction = "边际向好"
            desc = f"机构调研频次显著上升：近30日均 {avg_30d:.1f} 场/日，今日 {today_count} 场"
        elif today_count == prev_count:
            continue
        elif today_count > prev_count:
            direction = "边际向好"
            desc = f"机构调研频次上升：{prev_count}→{today_count} 场（{inst_count}家机构）"
        else:
            direction = "边际下滑"
            desc = f"机构调研频次下降：{prev_count}→{today_count} 场（{inst_count}家机构）"

        changes.append({
            "date": today, "code": code, "name": name,
            "theme": "机构调研",
            "direction": direction,
            "content": desc,
            "previous_value": str(prev_count),
            "current_value": str(today_count),
            "source": "inst_survey",
            "source_detail": f"当日 {today_count} 场调研",
        })

    return changes


def _diff_research(today: str, names: dict[str, str]) -> list[dict]:
    """研报变化：当日新研报 + 评级变化。"""
    changes: list[dict] = []
    universe = set(WATCHLIST)

    rows = store.query_research_by_date(today, universe)
    if not rows:
        return changes

    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)

    for code, reports in by_code.items():
        name = names.get(code, code)
        for r in reports:
            rating = r.get("rating") or ""
            institution = r.get("institution") or "?"
            title = (r.get("title") or "")[:60]
            tp = r.get("target_price")

            tp_str = f"，目标价 {tp:.2f}" if tp else ""
            detail = f"{institution} | {rating}{tp_str}"

            prev = store.get_latest_marginal(code, "研报跟踪")
            if prev is None:
                direction = "首次记录"
                content = f"首次检测到研报覆盖：{detail} | {title}"
            elif rating and prev.get("source_detail"):
                prev_detail = prev["source_detail"]
                prev_rating = prev_detail.split("|")[1].strip() if "|" in prev_detail else ""
                if prev_rating and rating != prev_rating:
                    direction = "边际向好" if ("买入" in rating or "增持" in rating) else "边际下滑"
                else:
                    direction = "符合预期"
                content = f"新研报：{detail} | {title}"
            else:
                direction = "符合预期"
                content = f"新研报：{detail} | {title}"

            changes.append({
                "date": today, "code": code, "name": name,
                "theme": "研报跟踪",
                "direction": direction,
                "content": content[:200],
                "previous_value": prev.get("source_detail") if prev else None,
                "current_value": detail,
                "source": "research",
                "source_detail": detail,
            })

    return changes


def _diff_financials(today: str, names: dict[str, str]) -> list[dict]:
    """财务指标变化：最新报告期 vs 上一报告期。"""
    changes: list[dict] = []
    tracked = {
        "roe": ("ROE", "%", 1.0),
        "gross_margin": ("毛利率", "%", 2.0),
        "net_margin": ("净利率", "%", 1.5),
        "revenue_yoy": ("营收增速", "%", 5.0),
    }

    for code in WATCHLIST:
        name = names.get(code, code)
        rows = store.query_financial_indicators(code, limit=2)
        if len(rows) < 2:
            continue

        latest, previous = rows[0], rows[1]
        for field, (label, unit, threshold) in tracked.items():
            cur = latest.get(field)
            prev = previous.get(field)
            if cur is None or prev is None:
                continue
            delta = cur - prev
            if abs(delta) < threshold:
                continue

            period = str(latest.get("report_date", ""))[:10]
            prev_period = str(previous.get("report_date", ""))[:10]
            direction = "边际向好" if delta > 0 else "边际下滑"

            changes.append({
                "date": today, "code": code, "name": name,
                "theme": f"财务_{label}",
                "direction": direction,
                "content": f"{label}变化：{prev_period} {prev:.2f}{unit} → {period} {cur:.2f}{unit}（{delta:+.2f}{unit}）",
                "previous_value": f"{prev:.2f}",
                "current_value": f"{cur:.2f}",
                "source": "financials",
                "source_detail": f"报告期 {prev_period}→{period}",
            })

    return changes


# ============================================================
# 报告渲染
# ============================================================


def render_marginal_report(changes: list[dict], today: str | None = None) -> str:
    """生成边际变化日报 Markdown。"""
    today = today or _today()

    if not changes:
        return f"# 边际变化日报 · {today}\n\n_今日未检测到边际变化。_"

    up = [c for c in changes if c["direction"] == "边际向好"]
    down = [c for c in changes if c["direction"] == "边际下滑"]
    first = [c for c in changes if c["direction"] == "首次记录"]
    flat = [c for c in changes if c["direction"] == "符合预期"]

    lines = [
        f"# 边际变化日报 · {today}",
        "",
        f"> 边际向好 **{len(up)}** · 边际下滑 **{len(down)}** · 首次记录 **{len(first)}** · 符合预期 **{len(flat)}**",
        "",
    ]

    def _table(title: str, rows: list[dict]):
        if not rows:
            return
        lines.append(f"## {title}（{len(rows)}）")
        lines.append("")
        lines.append("| 代码 | 名称 | 跟踪主题 | 变化内容 | 来源 |")
        lines.append("|------|------|----------|----------|------|")
        for c in rows:
            lines.append(
                f"| {c['code']} | {c['name']} | {c['theme']} | "
                f"{c['content'][:120]} | {c['source']} |"
            )
        lines.append("")

    _table("边际向好", up)
    _table("边际下滑", down)
    _table("首次记录", first)
    _table("符合预期", flat)

    lines.append("---")
    lines.append(f"*由 engine_marginal.py 自动生成 · {today}*")
    return "\n".join(lines)


def run(today: str | None = None, dry_run: bool = False) -> list[dict]:
    """CLI 入口：检测变化并输出报告。"""
    today = today or _today()
    print(f"边际变化检测 {today}")
    store.init_db()
    changes = detect_changes(today, dry_run=dry_run)
    report = render_marginal_report(changes, today)
    if not dry_run and changes:
        out = Path("daily_review/reports") / f"marginal_{today}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"  → {out} ({len(changes)} 条)")
    else:
        print(report)
    return changes


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    run(d)
