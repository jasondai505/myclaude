"""全系统健康检查 — 每个流水线第一步运行，异常即微信告警"""
from __future__ import annotations
import json
import re
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
        cutoff = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
        recent = [it for it in items if (it.get("date_modified", "")[:16]) >= cutoff]
        if not recent:
            ISSUES.append(f"RSS: 6h内无新文章(最新{latest})")
        print(f"  [RSS] OK: {len(recent)}篇/6h, 最新{latest}")
    except Exception as e:
        ISSUES.append(f"RSS: 不可达 ({e})")


def check_db_articles():
    import sqlite3
    from config import DB_PATH
    db = DB_PATH
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

    # DB size check
    size_mb = db.stat().st_size / (1024 * 1024)
    if size_mb > 300:
        ISSUES.append(f"DB: review.db 超300MB ({size_mb:.0f}MB)")
    print(f"  [DB] 大小: {size_mb:.0f}MB")


def check_reports():
    today = date.today()
    reports = {
        "复盘报告": f"daily_review/reports/review/review_{today.isoformat()}.md",
        "公众号分析": f"daily_review/reports/wechat_analysis/wechat_analysis_{today.isoformat()}.md",
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


def check_fev_delta():
    """FEV/Δ 表自检 — 日期对齐 / 交叉覆盖 / 分布合理性"""
    import sqlite3
    db = PROJECT / "daily_review" / "data" / "serenity.db"
    if not db.exists():
        ISSUES.append("FEV/Δ: serenity.db 不存在")
        return

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()

    for tbl, label, max_lag in [("feval_scores", "FEV", 1), ("stock_delta", "Δ", 1)]:
        row = conn.execute(f"SELECT MAX(date) as latest FROM {tbl}").fetchone()
        latest = row["latest"] if row and row["latest"] else ""
        if not latest:
            ISSUES.append(f"{label}: 表为空")
        elif latest != today:
            days_behind = (date.today() - date.fromisoformat(latest)).days
            if days_behind > max_lag:
                ISSUES.append(f"{label}: 最新日期={latest}(落后{days_behind}天>{max_lag}天容限)")
            else:
                print(f"  [FEV/Δ] {label} 最新={latest}(落后{days_behind}天，在{max_lag}天容限内)")

    fev_codes = {r[0] for r in conn.execute("SELECT DISTINCT code FROM feval_scores").fetchall()}
    delta_codes = {r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_delta").fetchall()}
    if fev_codes and delta_codes:
        intersection = fev_codes & delta_codes
        ratio = len(intersection) / max(len(fev_codes), len(delta_codes)) * 100
        print(f"  [FEV/Δ] 覆盖: FEV={len(fev_codes)} Δ={len(delta_codes)} 交集={len(intersection)}({ratio:.0f}%)")
        if ratio < 20:
            ISSUES.append(f"FEV/Δ: 交集率仅{ratio:.0f}%，硬排名Δ因子几乎无效")
        if len(delta_codes) < len(fev_codes) * 0.3:
            ISSUES.append(f"Δ覆盖严重不足: {len(delta_codes)} vs FEV {len(fev_codes)}")
    elif not fev_codes:
        ISSUES.append("FEV: 无评分数据")
    elif not delta_codes:
        ISSUES.append("Δ: 无评分数据")

    for tbl, label, lo, hi in [("feval_scores", "FEV", 0, 30), ("stock_delta", "Δ", -10, 10)]:
        col = "fev_total" if "fev" in tbl else "delta_score"
        out_of_range = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE {col} NOT BETWEEN {lo} AND {hi}"
        ).fetchone()[0]
        if out_of_range:
            ISSUES.append(f"{label}: {out_of_range}条超出范围[{lo},{hi}]")

        all_zero = conn.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE {col} = 0"
        ).fetchone()[0]
        total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        if total > 0 and all_zero == total:
            ISSUES.append(f"{label}: {total}条全为0，LLM打分可能失败")

    conn.close()


def check_placeholder_leaks():
    """检查 reports 目录是否有未被替换的占位符残留"""
    reports_dir = PROJECT / "daily_review" / "reports"
    if not reports_dir.exists():
        return
    today = date.today().isoformat()
    for pattern in [f"advice/advice_{today}.md", f"wechat_analysis/wechat_analysis_{today}.md"]:
        path = reports_dir / pattern
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            leaks = re.findall(r"%%[A-Z_]+%%", text)
            if leaks:
                unique = sorted(set(leaks))
                ISSUES.append(f"占位符残留({path.name}): {', '.join(unique)}")
        except Exception:
            pass


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


def check_engines():
    """检查分析引擎产出文件是否存在且新鲜。"""
    today = date.today()
    yesterday = today - timedelta(days=1)
    engines = [
        ("催化筛查", f"daily_review/reports/catalyst/catalyst_screen_{today.isoformat()}.md", True),
        ("催化跟踪", f"daily_review/reports/catalyst/catalyst_track_{today.isoformat()}.md", False),
        ("四源交叉", f"daily_review/reports/feeds/primary_synthesis/primary_synthesis_{today.isoformat()}.md", True),
        ("公众号分析", f"daily_review/reports/wechat_analysis/wechat_analysis_{today.isoformat()}.md", True),
        ("星球分析", f"daily_review/reports/zsxq_analysis/zsxq_analysis_{today.isoformat()}.md", True),
        ("边际变化", f"daily_review/reports/marginal/marginal_{today.isoformat()}.md", True),
        ("行业深研", f"daily_review/reports/industry/industry_daily_{today.isoformat()}.md", False),
    ]
    for name, rel, required in engines:
        p = PROJECT / rel
        if p.exists():
            print(f"  [引擎] {name}: OK")
        else:
            yp = PROJECT / rel.replace(today.isoformat(), yesterday.isoformat())
            if yp.exists():
                print(f"  [引擎] {name}: 最新{yesterday}(今日未生成)")
                if required:
                    ISSUES.append(f"引擎: {name} 今日未产出(昨日有)")
            elif required:
                ISSUES.append(f"引擎: {name} 近2天均未产出")


def check_system_resources():
    """磁盘使用率 + 内存余量监控。"""
    try:
        import psutil
    except ImportError:
        print("  [系统资源] psutil 未安装，跳过")
        return

    # 磁盘 — 项目盘
    disk = psutil.disk_usage(str(PROJECT))
    pct = disk.percent
    free_gb = disk.free / (1024 ** 3)
    print(f"  [磁盘] {PROJECT.drive}: 已用 {pct:.0f}% / 剩余 {free_gb:.0f}GB")
    if pct > 90:
        ISSUES.append(f"磁盘: {pct:.0f}%已用 (剩余{free_gb:.0f}GB)")
    elif pct > 80:
        print(f"  [磁盘] WARN: 已用 {pct:.0f}%，建议关注")

    # 内存
    mem = psutil.virtual_memory()
    mem_pct = mem.percent
    avail_gb = mem.available / (1024 ** 3)
    print(f"  [内存] 已用 {mem_pct:.0f}% / 可用 {avail_gb:.0f}GB")
    if mem_pct > 90:
        ISSUES.append(f"内存: {mem_pct:.0f}%已用 (可用{avail_gb:.0f}GB)")


def check_advice_server():
    """检查 advice HTTP 服务 (端口 8900) 是否存活"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(5)
        s.connect(("127.0.0.1", 8900))
        s.close()
        print("  [advice_server] 端口 8900: OK")
    except (ConnectionRefusedError, OSError, socket.timeout):
        ISSUES.append("advice_server: 端口 8900 无响应 → 手动 python daily_review/advice_server.py --daemon")


def check_name_map():
    """名称→代码映射行数检查。映射为空则 validator 全线断裂。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from data import _load_name_to_code_map
        nm = _load_name_to_code_map()
        count = len(nm)
        print(f"  [名称映射] {count} 条")
        if count == 0:
            ISSUES.append("名称映射: 0条 — stock_codes.json 路径或格式异常，validator/Δ/代码提取全断")
        elif count < 5000:
            ISSUES.append(f"名称映射: 仅{count}条(预期≥5000)，stock_codes.json 可能残缺")
    except Exception as e:
        ISSUES.append(f"名称映射: 加载失败 ({e})")


def check_chain_xlsx():
    """产业链 XLSX 文件数检查，<100 则三重共振涨跌幅数据过期。"""
    try:
        from _dashboard import _discover_chain_xlsx
        files = _discover_chain_xlsx()
        count = len(files)
        print(f"  [产业链XLSX] {count} 个")
        if count == 0:
            ISSUES.append("产业链XLSX: 0个 — 三重共振涨跌幅无数据源")
        elif count < 100:
            ISSUES.append(f"产业链XLSX: 仅{count}个(预期≥100)，alpha产业图谱/ 文件可能缺失或 glob 未递归")
        # 检查日期新鲜度
        import re as _re
        date_pat = _re.compile(r"(\d{8})")
        stale = 0
        from trade_calendar import prev_trading_day
        last_trade_str = prev_trading_day().strftime("%Y%m%d")
        for fp in files:
            m = date_pat.search(fp.stem)
            if m and m.group(1) < last_trade_str:
                stale += 1
        if stale > count * 0.5:
            ISSUES.append(f"产业链XLSX: {stale}/{count} 日期早于{last_trade_str}(最近交易日)，涨幅数据可能过时")
    except Exception as e:
        ISSUES.append(f"产业链XLSX: 检查失败 ({e})")


def check_prompt_audit():
    """扫描 advice prompt 模板，检查数值字段是否有数据注入。"""
    prompt = Path(__file__).resolve().parent / "claude_prompt.txt"
    if not prompt.exists():
        return
    try:
        import prompt_audit
        findings, _ = prompt_audit.audit_prompt(str(prompt))
        bugs = [f for f in findings if f["status"] == "❌"]
        warns = [f for f in findings if f["status"] == "⚠️"]
        print(f"  [prompt审计] {len(findings)}字段 | ❌{len(bugs)}未注入 | ⚠️{len(warns)}推断")
        for b in bugs:
            ISSUES.append(f"prompt审计: {b['label']} 无数据注入({b['source']})")
    except Exception as e:
        ISSUES.append(f"prompt审计: 扫描失败 ({e})")


def main():
    print(f"=== 系统健康检查 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    check_rss()
    check_db_articles()
    check_reports()
    check_fev_delta()
    check_name_map()
    check_chain_xlsx()
    check_placeholder_leaks()
    check_pipeline_logs()
    check_engines()
    check_advice_server()
    check_system_resources()
    check_prompt_audit()

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
