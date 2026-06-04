"""全系统健康检查 — 每个流水线第一步运行，异常即微信告警"""
from __future__ import annotations
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8")

PROJECT = Path(__file__).resolve().parent.parent
ISSUES: list[str] = []


def _alert(msg: str):
    try:
        sys.path.insert(0, str(PROJECT / "morning_intel"))
        from notify import push
        push("系统健康告警", msg)
    except Exception:
        pass


def check_rss():
    try:
        req = Request("http://111.231.44.12:4000/feeds/all.json?limit=10",
                      headers={"User-Agent": "HealthCheck/1.0", "Accept": "application/json"})
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        items = data.get("items", [])
        if not items:
            ISSUES.append("RSS: 可达但无文章")
            return
        latest = items[0].get("date_modified", "")[:10]
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d")
        recent = [it for it in items if (it.get("date_modified", "")[:10]) >= cutoff]
        if not recent:
            ISSUES.append(f"RSS: 24h内无新文章(最新{latest})")
        print(f"  [RSS] OK: {len(recent)}篇/24h, 最新{latest}")
    except Exception as e:
        ISSUES.append(f"RSS: 不可达 ({e})")


def check_db_articles():
    import sqlite3
    db = PROJECT / "daily_review" / "data" / "review.db"
    if not db.exists():
        ISSUES.append("DB: review.db 不存在")
        return
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT max(pub_date) as latest FROM wechat_articles").fetchone()
    conn.close()
    latest = row["latest"] if row and row["latest"] else ""
    if not latest:
        ISSUES.append("DB: wechat_articles 表为空")
    else:
        days = (date.today() - date.fromisoformat(latest[:10])).days
        if days > 2:
            ISSUES.append(f"DB: 公众号文章最新={latest[:10]}(落后{days}天)")
        print(f"  [DB] 公众号文章最新: {latest}")


def check_reports():
    today = date.today()
    reports = {
        "复盘报告": f"daily_review/reports/review_{today.isoformat()}.md",
        "公众号分析": f"daily_review/reports/wechat_analysis_{today.isoformat()}.md",
    }
    for label, rel in reports.items():
        p = PROJECT / rel
        if p.exists():
            print(f"  [报告] {label}: OK")
        else:
            yest = (today - timedelta(days=1)).isoformat()
            yp = PROJECT / rel.replace(today.isoformat(), yest)
            if yp.exists():
                print(f"  [报告] {label}: 最新{yest}(今天未生成)")
            else:
                ISSUES.append(f"报告: {label} 最近2天均未生成")


def check_pipeline_logs():
    log_dir = PROJECT / "dashboard" / "logs"
    if not log_dir.exists():
        return
    logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return
    latest = logs[0]
    mtime = datetime.fromtimestamp(latest.stat().st_mtime)
    hours_ago = (datetime.now() - mtime).total_seconds() / 3600
    if hours_ago > 24:
        ISSUES.append(f"流水线: 最近日志{mtime.strftime('%m-%d %H:%M')}(>{hours_ago:.0f}h前)")
    else:
        print(f"  [流水线] 最近日志: {latest.name} ({hours_ago:.0f}h前)")


def main():
    print(f"=== 系统健康检查 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    check_rss()
    check_db_articles()
    check_reports()
    check_pipeline_logs()

    if ISSUES:
        msg = "\n".join(f"- {i}" for i in ISSUES)
        print(f"\n[WARN] {len(ISSUES)} 项异常:\n{msg}")
        _alert(f"{len(ISSUES)}项异常\n{msg}")
        return 1
    else:
        print("\n[OK] 全部正常")
        return 0


if __name__ == "__main__":
    sys.exit(main())
