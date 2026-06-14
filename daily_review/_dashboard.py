"""每日仪表盘 — 五维状态+采集管线+可操作警报+趋势。

在 daily_collect 末尾调用，产出 reports/Dashboard.md 并同步到根目录。
Obsidian 中可用 Homepage 插件设为首页，或固定标签页。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import store
from config import REPORT_DIR

DASHBOARD_PATH = REPORT_DIR / "Dashboard.md"

# === 五维 → collector 映射 ===
DIMENSIONS = [
    {
        "name": "① 公告深研",
        "icon": "📄",
        "collector": "announcement_deep_read",
        "data_source": "announcements",
        "cost": "$9/天",
        "desc": "全市场公告 → 四层筛选 → LLM五维评分 → Obsidian存档",
        "signal_query": "announce_signal",
    },
    {
        "name": "② 研报跟踪",
        "icon": "📊",
        "collector": "research_deep_read",
        "data_source": "research_reports",
        "cost": "$1/天",
        "desc": "东方财富全市场API → 信号检测 → LLM分析 → 累积档案",
        "signal_query": "dossier",
    },
    {
        "name": "③ 调研情绪",
        "icon": "🔍",
        "collector": "sentiment_track",
        "data_source": "inst_survey",
        "cost": "$0",
        "desc": "机构调研 → 密集/首次/升温检测 → 追加到档案",
        "signal_query": "dossier",
    },
    {
        "name": "④ 互动易",
        "icon": "💬",
        "collector": "interactions",
        "data_source": "interactions",
        "cost": "$0",
        "desc": "深交所+上交所互动易 → 关键词检测 → 追加到档案",
        "signal_query": "dossier",
    },
    {
        "name": "⑤ 业绩预告",
        "icon": "📈",
        "collector": "earnings",
        "data_source": "earnings_forecast",
        "cost": "$0",
        "desc": "全市场业绩预告/快报 → 暴增/扭亏/暴雷检测 → 追加到档案",
        "signal_query": "none",
    },
]

# === 采集管线中文名 ===
SOURCE_LABELS = {
    "announcements": "📄 公告采集",
    "announcement_deep_read": "📄 公告深研",
    "research": "📊 研报采集",
    "research_deep_read": "📊 研报跟踪",
    "surveys": "🔍 机构调研",
    "sentiment_track": "🔍 调研+互动情绪",
    "interactions": "💬 互动易",
    "earnings": "📈 业绩预告",
    "news": "📰 个股新闻",
    "industry": "🏭 行业研报",
    "wechat": "💚 微信公众号",
    "weibo": "🐦 唐史主任微博",
    "zsxq": "⭐ 知识星球",
    "jiuyang": "📝 韭研脱水研报",
}

# === 可操作警报：异常 → 排查指引 ===
ALERT_ACTIONS = {
    "announcement_deep_read": {
        "超时": "检查 DeepSeek API 是否正常 → 减少 --days 范围 → 或等下次重试",
        "无公告数据": "检查 akshare.stock_notice_report 接口 → 确认交易日历",
    },
    "research": {
        "从未运行": "手动执行 python daily_collect.py --source research 验证 API",
    },
    "research_deep_read": {
        "从未运行": "手动执行 python daily_collect.py --source research_deep_read",
    },
    "wechat": {
        "RSS数据陈旧": "打开 http://111.231.44.12:4000/dash 检查 WeWe-RSS 是否需要重新登录",
    },
    "interactions": {
        "超时": "日频72只逐只调约需6分钟 → 检查网络 → 或降为周频",
    },
    "zsxq": {
        "从未运行": "检查知识星球 cookie 是否过期 → 需手动更新",
    },
}


def _status_icon(status: str) -> str:
    return {"ok": "✅", "error": "❌", "timeout": "⏰", "skip": "➖"}.get(status, "⚠️")


def _collector_status() -> list[dict]:
    rows = []
    try:
        with store._conn() as conn:
            for src in SOURCE_LABELS:
                r = conn.execute(
                    "SELECT * FROM collect_status WHERE source = ? ORDER BY last_run_at DESC LIMIT 1",
                    (src,),
                ).fetchone()
                rows.append(dict(r) if r else {
                    "source": src, "status": "unknown", "last_date": "",
                    "last_run_at": "", "message": "从未运行", "added_count": 0,
                })
    except Exception:
        pass
    return rows


def _deep_read_weekly() -> dict:
    try:
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT date, COUNT(*) as cnt, SUM(CASE WHEN total_score>=60 THEN 1 ELSE 0 END) as a60 "
                "FROM deep_read_results WHERE date >= ? GROUP BY date ORDER BY date",
                ((date.today() - timedelta(days=7)).isoformat(),),
            ).fetchall()
        daily = {r["date"]: (r["cnt"] or 0, r["a60"] or 0) for r in rows}
        total_a60 = sum(v[1] for v in daily.values())
        return {"daily": daily, "total_a60": total_a60}
    except Exception:
        return {"daily": {}, "total_a60": 0}


def _dossier_count() -> int:
    d = Path("reports/research_dossiers")
    return len(list(d.glob("*.md"))) if d.exists() else 0


def _generate_dossier_index():
    """生成研报档案索引 README → reports/research_dossiers/README.md"""
    import re
    d = REPORT_DIR / "research_dossiers"
    today = date.today()
    week_ago = today - timedelta(days=7)

    dossiers = []
    for fp in sorted(d.glob("*.md")):
        if fp.name == "README.md":
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        dates = re.findall(r"### (\d{4}-\d{2}-\d{2})", text)
        dates += re.findall(r"## .+ \((\d{4}-\d{2}-\d{2})\)", text)
        latest = max(dates) if dates else fp.stat().st_mtime
        if isinstance(latest, float):
            from datetime import datetime
            latest = datetime.fromtimestamp(latest).strftime("%Y-%m-%d")
        nm = re.search(r'^name:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
        name = nm.group(1).strip() if nm else ""
        display = f"{name} ({fp.stem[:6]})" if name else fp.stem[:6]
        sigs = re.findall(r"- \[(\w+)\]\s*(.+?)(?:\s*\([+-]?\d+分\))?\s*$", text, re.MULTILINE)
        sig_type, sig_desc = sigs[-1] if sigs else ("", "")
        dossiers.append({
            "display": display, "code": fp.stem[:6],
            "latest_date": latest,
            "sig_type": sig_type, "sig_desc": sig_desc,
        })

    dossiers.sort(key=lambda x: x["latest_date"], reverse=True)

    today_list = [x for x in dossiers if x["latest_date"] == today.isoformat()]
    week_list = [x for x in dossiers if today.isoformat() > x["latest_date"] >= week_ago.isoformat()]
    older_list = [x for x in dossiers if x["latest_date"] < week_ago.isoformat()]

    buf = [
        f"# 研报档案索引",
        "",
        f"> 自动生成于 {today.isoformat()} | 共 {len(dossiers)} 份档案",
        "",
    ]

    sections = [
        (f"## 🔥 今日更新 ({len(today_list)})", today_list),
        (f"## 📅 近7天更新 ({len(week_list)})", week_list),
        (f"## 📦 更早 ({len(older_list)})", older_list),
    ]
    for heading, items in sections:
        buf.append(heading)
        buf.append("")
        if items:
            buf.append("| 标的 | 代码 | 最后信号日 | 最新信号 |")
            buf.append("|------|:----:|:----------:|----------|")
            for x in items:
                sig = f"{x['sig_type']}: {x['sig_desc'][:30]}" if x["sig_type"] else "—"
                buf.append(f"| {x['display']} | {x['code']} | {x['latest_date']} | {sig} |")
        else:
            buf.append("_暂无_")
        buf.append("")

    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("\n".join(buf), encoding="utf-8")


def _data_freshness(source: str) -> tuple[str, str]:
    """(新鲜度文字, 天数)"""
    col_map = {
        "announcements": "date", "research_reports": "report_date",
        "inst_survey": "notice_date", "interactions": "reply_time",
        "earnings_forecast": "notice_date",
    }
    if source not in col_map:
        return "—", ""
    try:
        with store._conn() as conn:
            col = col_map[source]
            r = conn.execute(
                f"SELECT MAX({col}) FROM {source} LIMIT 1"
            ).fetchone()[0]
            if r:
                days = (date.today() - date.fromisoformat(str(r)[:10])).days
                if days <= 1:
                    return "🟢 新鲜", str(days)
                elif days <= 3:
                    return f"🟡 {days}天前", str(days)
                else:
                    return f"🔴 {days}天前", str(days)
    except Exception:
        pass
    return "—", ""


def generate(today_str: str = "") -> str:
    today = today_str or date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    L = []
    def w(s=""): L.append(s)

    w(f"# 📊 每日仪表盘 {today}")
    w()
    w(f"> 自动生成 | [复盘](daily_review/reports/review/review_{yesterday}.md) | [建议](daily_review/reports/advice/advice_{today}.md) | [深研档案](daily_review/reports/deep_read/) | [个股档案](daily_review/reports/research_dossiers/)")
    w()

    # === 五维状态 ===
    w("## 五维信息源")
    w()
    w("| 维度 | 采集 | 数据 | 信号 | 成本 |")
    w("|------|:---:|------|-----:|:----:|")
    collectors = {r["source"]: r for r in _collector_status()}
    dr = _deep_read_weekly()
    dc = _dossier_count()
    _generate_dossier_index()

    for dim in DIMENSIONS:
        cs = collectors.get(dim["collector"], {})
        icon = _status_icon(cs.get("status", "unknown"))
        freshness, _ = _data_freshness(dim["data_source"])

        # 信号
        if dim["signal_query"] == "announce_signal":
            sig = f">=60: {dr['total_a60']}条"
        elif dim["signal_query"] == "dossier":
            sig = f"{dc}份档案"
        else:
            sig = "—"

        w(f"| {dim['icon']} {dim['name']} | {icon} | {freshness} | {sig} | {dim['cost']} |")

    w()

    # === 采集管线 ===
    w("## 采集管线")
    w()
    w("| 源 | 状态 | 上次成功 | 新增 | 备注 |")
    w("|----|:----:|---------|-----:|------|")
    for cs in _collector_status():
        icon = _status_icon(cs.get("status", "unknown"))
        label = SOURCE_LABELS.get(cs["source"], cs["source"])
        last = cs.get("last_date", "") or "—"
        added = cs.get("added_count", 0) or 0
        msg = (cs.get("message", "") or "")[:40]
        if cs.get("status") == "timeout":
            msg += " ⏰"
        w(f"| {label} | {icon} | {last} | {added} | {msg} |")
    w()

    # === 异常警报（可操作） ===
    alerts = []
    for cs in _collector_status():
        if cs.get("status") in ("error", "timeout", "unknown"):
            label = SOURCE_LABELS.get(cs["source"], cs["source"])
            msg = cs.get("message", "无详情")
            actions = ALERT_ACTIONS.get(cs["source"], {})
            # 匹配排查指引
            hint = ""
            for kw, action in actions.items():
                if kw in msg:
                    hint = action
                    break
            if not hint and cs["status"] == "timeout":
                hint = "调大 daily_collect.COLLECTOR_TIMEOUTS 或检查网络"
            elif not hint and cs["status"] == "unknown":
                hint = "检查 collector 是否在 SOURCE_TIERS 中正确注册"
            action_link = f" → {hint}" if hint else ""
            alerts.append(f"- {label}: {msg}{action_link}")

    w("## ⚠️ 异常警报")
    w()
    if alerts:
        for a in alerts:
            w(a)
    else:
        w("✅ 全部采集源正常，无异常。")
    w()

    # === 本周趋势 ===
    w("## 📈 本周公告深研趋势")
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
    else:
        w("_暂无数据_")
    w()

    # === 最近提交 ===
    w("## 📜 最近提交")
    w()
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).parent.parent),
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    w(f"- `{line}`")
        else:
            w("_git 不可用_")
    except Exception:
        w("_git 不可用_")
    w()

    # === 底部快速链接 ===
    w("---")
    w()
    w("### 🔗 快速链接")
    w("| 页面 | 路径 |")
    w("|------|------|")
    w("| 复盘报告 | `daily_review/reports/review/review_{date}.md` |")
    w("| 盘前建议 | `daily_review/reports/advice/advice_{date}.md` |")
    w("| 公告深研 | `daily_review/reports/deep_read/` |")
    w("| 个股档案 | `daily_review/reports/research_dossiers/` |")
    w("| 催化跟踪 | `daily_review/reports/feeds/catalyst_track_{date}.md` |")
    w("| Git 历史 | 终端: `git log --oneline` |")
    w("| 会话转录 | `~/.claude/projects/C--Users-daixin-myclaude/*.jsonl` (grep 关键词) |")

    content = "\n".join(L)
    DASHBOARD_PATH.write_text(content, encoding="utf-8")
    return content
