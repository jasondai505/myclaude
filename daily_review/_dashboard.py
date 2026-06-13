"""每日仪表盘 — 自动生成五维状态+采集管线+异常警报+趋势。

在 daily_collect 末尾调用，产出 reports/Dashboard.md。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

import store
from config import REPORT_DIR

DASHBOARD_PATH = REPORT_DIR / "Dashboard.md"

# 五维 → collector 映射
DIMENSIONS = {
    "① 公告深研": {
        "collector": "announcement_deep_read",
        "data_source": "announcements",
        "signal_table": "deep_read_results",
        "signal_condition": "total_score >= 60",
        "cost": "$9/天",
    },
    "② 研报跟踪": {
        "collector": "research_deep_read",
        "data_source": "research_reports",
        "signal_dir": "reports/research_dossiers",
        "cost": "$1/天",
    },
    "③ 调研情绪": {
        "collector": "sentiment_track",
        "data_source": "inst_survey",
        "signal_dir": "reports/research_dossiers",
        "cost": "$0",
    },
    "④ 互动易": {
        "collector": "interactions",
        "data_source": "interactions",
        "signal_dir": "reports/research_dossiers",
        "cost": "$0",
    },
    "⑤ 业绩预告": {
        "collector": "earnings",
        "data_source": "earnings_forecast",
        "cost": "$0",
    },
}

# 采集管线 — 监控所有 collector
PIPELINE_SOURCES = [
    "announcements", "announcement_deep_read",
    "research", "research_deep_read",
    "surveys", "sentiment_track",
    "interactions", "earnings",
    "news", "industry", "wechat", "weibo", "zsxq", "jiuyang",
]

def _status_icon(status: str) -> str:
    if status == "ok":
        return "✅"
    if status in ("error", "timeout"):
        return "❌"
    if status == "skip":
        return "➖"
    return "⚠️"


def _dossier_count() -> int:
    d = Path("reports/research_dossiers")
    if not d.exists():
        return 0
    return len(list(d.glob("*.md")))


def _deep_read_count(days: int = 7) -> dict:
    try:
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT date, COUNT(*) as cnt, SUM(CASE WHEN total_score>=60 THEN 1 ELSE 0 END) as a60 "
                "FROM deep_read_results "
                "WHERE date >= ? GROUP BY date ORDER BY date",
                ((date.today() - timedelta(days=days)).isoformat(),),
            ).fetchall()
        total = sum(r["cnt"] or 0 for r in rows)
        a60 = sum(r["a60"] or 0 for r in rows)
        daily = {r["date"]: (r["cnt"] or 0, r["a60"] or 0) for r in rows}
        return {"total": total, "a60": a60, "daily": daily}
    except Exception:
        return {"total": 0, "a60": 0, "daily": {}}


def _collector_status() -> list[dict]:
    rows = []
    try:
        with store._conn() as conn:
            for src in PIPELINE_SOURCES:
                r = conn.execute(
                    "SELECT * FROM collect_status WHERE source = ? ORDER BY last_run_at DESC LIMIT 1",
                    (src,),
                ).fetchone()
                if r:
                    rows.append(dict(r))
                else:
                    rows.append({"source": src, "status": "unknown", "last_date": "", "message": "从未运行"})
    except Exception:
        pass
    return rows


def _data_freshness(source: str) -> str:
    """检查数据源的新鲜度。"""
    try:
        with store._conn() as conn:
            tbl = {
                "announcements": "date", "research_reports": "report_date",
                "inst_survey": "notice_date", "interactions": "reply_time",
                "earnings_forecast": "notice_date",
            }
            if source in tbl:
                col = tbl[source]
                r = conn.execute(
                    f"SELECT MAX({col}) FROM (SELECT {col} FROM {source} LIMIT 1000)"
                ).fetchone()[0]
                if r:
                    days_ago = (date.today() - date.fromisoformat(str(r)[:10])).days
                    if days_ago <= 1:
                        return f"新鲜(≤1天)"
                    elif days_ago <= 3:
                        return f"{days_ago}天前"
                    else:
                        return f"⚠️ {days_ago}天前"
    except Exception:
        pass
    return "未知"


def generate(today_str: str = "") -> str:
    """生成每日仪表盘。返回 markdown 内容。"""
    today = today_str or date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    L = []
    def w(s=""): L.append(s)

    w(f"# 每日仪表盘 {today}")
    w()
    w(f"> 自动生成于 {today} | [复盘报告](review_{yesterday}.md) | [盘前建议](advice_{today}.md)")
    w()

    # === 五维状态 ===
    w("## 五维状态")
    w()
    w("| 维度 | 采集状态 | 数据新鲜度 | 本周信号 | 成本 | 备注 |")
    w("|------|:------:|:---------:|--------:|:----:|------|")
    collectors = {r["source"]: r for r in _collector_status()}
    dr = _deep_read_count(7)

    for dim_name, dim_cfg in DIMENSIONS.items():
        col = dim_cfg["collector"]
        cs = collectors.get(col, {})
        icon = _status_icon(cs.get("status", "unknown"))

        # 信号数
        signal_text = "—"
        if "signal_table" in dim_cfg:
            signal_text = f">=60: {dr['a60']}条"
        elif "signal_dir" in dim_cfg:
            signal_text = f"{_dossier_count()}份"

        # 新鲜度
        freshness = _data_freshness(dim_cfg["data_source"])

        w(f"| {dim_name} | {icon} | {freshness} | {signal_text} | {dim_cfg['cost']} | {cs.get('message','')[:40]} |")

    w()

    # === 采集管线 ===
    w("## 采集管线")
    w()
    w("| 源 | 状态 | 上次成功 | 新增 | 备注 |")
    w("|----|:----:|---------|-----:|------|")
    for cs in _collector_status():
        icon = _status_icon(cs.get("status", "unknown"))
        last = cs.get("last_date", "") or "—"
        added = cs.get("added_count", 0) or 0
        msg = (cs.get("message", "") or "")[:50]
        w(f"| {cs['source']} | {icon} | {last} | {added} | {msg} |")
    w()

    # === 异常警报 ===
    alerts = []
    for cs in _collector_status():
        if cs.get("status") in ("error", "timeout"):
            alerts.append(f"- ❌ **{cs['source']}** 异常: {cs.get('message', '')}")
    if not alerts:
        alerts.append("- ✅ 全部采集源正常")
    w("## ⚠️ 异常警报")
    w()
    for a in alerts:
        w(a)
    w()

    # === 本周趋势 ===
    w("## 📊 本周公告深研趋势")
    w()
    daily = dr.get("daily", {})
    if daily:
        max_cnt = max(v[0] for v in daily.values()) or 1
        for d_sorted in sorted(daily.keys()):
            cnt, a60 = daily[d_sorted]
            bar_len = max(1, int(cnt / max_cnt * 20))
            bar = "█" * bar_len
            a60_flag = f"  **{a60}条≥60**" if a60 > 0 else ""
            w(f"- {d_sorted}: {bar} {cnt}条{a60_flag}")
    w()

    content = "\n".join(L)
    DASHBOARD_PATH.write_text(content, encoding="utf-8")
    return content
