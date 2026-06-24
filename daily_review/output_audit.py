"""输出自检 + 自修复 — 检查所有预期日产出，缺失的自动补跑。

每个产出有明确的目标日期（target_date）：review 类复盘上一个交易日，advice 类目标当天。
只检查目标日期的文件是否存在，不回退到前一天（防止旧文件伪装成新鲜产出）。

用法:
    python daily_review/output_audit.py           # 仅检查，打印报告
    python daily_review/output_audit.py --fix     # 检查 + 自动补跑 CRITICAL/HIGH 缺失
    python daily_review/output_audit.py --fix --all  # 全部补跑
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROJECT = Path(__file__).resolve().parent.parent

def _today() -> str:
    return date.today().isoformat()

def _last_trading_day(ref: date = None) -> date:
    d = (ref or date.today()) - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d

# ============================================================
# DB 类产出检查函数 (返回 (exists: bool, info: str))
# ============================================================

def _check_fev_today() -> tuple[bool, str]:
    import sqlite3
    db = PROJECT / "daily_review" / "data" / "serenity.db"
    if not db.exists():
        return False, "serenity.db 不存在"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT MAX(date) as latest FROM feval_scores").fetchone()
    conn.close()
    latest = row["latest"] if row and row["latest"] else ""
    if not latest:
        return False, "FEV 表为空"
    if latest == _today():
        return True, latest
    days = (date.today() - date.fromisoformat(latest)).days
    return False, f"{latest} (落后{days}天)"

def _check_delta_today() -> tuple[bool, str]:
    import sqlite3
    db = PROJECT / "daily_review" / "data" / "serenity.db"
    if not db.exists():
        return False, "serenity.db 不存在"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT MAX(date) as latest FROM stock_delta").fetchone()
    conn.close()
    latest = row["latest"] if row and row["latest"] else ""
    if not latest:
        return False, "Δ 表为空"
    if latest == _today():
        return True, latest
    days = (date.today() - date.fromisoformat(latest)).days
    return False, f"{latest} (落后{days}天)"

# ============================================================
# 输出注册表
#   target_date: "today" | "last_trade" — 文件命名用的日期
#   deadline:    预期完成时间 (HH:MM)，用于判断是否过期
# ============================================================

EXPECTED_OUTPUTS = [
    # (显示名, 文件路径模板, 目标日期策略, 管线步骤ID, 补跑命令, 优先级, 截止时间)
    ("复盘报告",   "reports/review/review_{date}.md",              "last_trade", "review",         "python daily_review/run.py",                        "CRITICAL", "23:59"),
    ("日报简报",   "morning_intel/reports/daily_brief_{date}.md",  "today",      "brief",          "python morning_intel/daily_brief.py",               "HIGH",     "18:00"),
    ("催化筛查",   "reports/catalyst/catalyst_screen_{date}.md",   "today",      "catalyst_screen","python daily_review/catalyst_screen.py",             "CRITICAL", "23:00"),
    ("催化跟踪",   "reports/catalyst/catalyst_track_{date}.md",    "today",      "catalyst_track", "python daily_review/daily_collect.py --tier post_market", "HIGH", "17:45"),
    ("四源交叉",   "reports/feeds/primary_synthesis/primary_synthesis_{date}.md",    "today",      "synthesis",      "python daily_review/primary_synthesis.py",           "HIGH",     "23:00"),
    ("公众号分析", "reports/wechat_analysis/wechat_analysis_{date}.md", "today", "wechat",       "python daily_review/analyze_wechat.py",              "HIGH",     "23:00"),
    ("星球分析",   "reports/zsxq_analysis/zsxq_analysis_{date}.md","today",      "zsxq_analysis",  "python daily_review/analyze_zsxq.py",                "HIGH",     "23:00"),
    ("边际变化",   "reports/marginal/marginal_{date}.md",           "today",      "marginal",       "python daily_review/engine_marginal.py",             "HIGH",     "05:30"),
    ("行业深研",   "reports/industry/industry_daily_{date}.md",     "today",      "industry",       "python daily_review/_analyze_industry.py",           "MEDIUM",   "23:00"),
    ("盘前建议",   "reports/advice/advice_{date}.md",               "today",      "advice",         "python daily_review/_run_advice.py",                 "CRITICAL", "07:00"),
    ("复盘摘要",   "reports/feeds/review_summary/review_summary_{date}.md",        "last_trade", "summary",        "python daily_review/review_summary.py",              "MEDIUM",   "23:00"),
    ("推荐追踪",   "reports/feeds/recap/recap_{date}.md",                 "last_trade", "track",          "python daily_review/track_recommendations.py",       "LOW",      "23:00"),
    ("FEV评分",    "_check_fev_today",                               "today",      "unified",        "python daily_review/unified_scorer.py --from-feeds",  "HIGH",     "05:30"),
    ("Δ边际评分",  "_check_delta_today",                             "today",      "delta",          "python daily_review/feval.py --update-delta",        "HIGH",     "05:30"),
    ("LLM输出质量", "_check_llm_quality",                              "today",      "audit",          "python daily_review/llm_validator.py --audit-all",    "HIGH",     "07:00"),
]

def _check_llm_quality() -> tuple[bool, str]:
    from llm_validator import _check_llm_quality as _f
    return _f()

DB_CHECKS = {
    "_check_fev_today": _check_fev_today,
    "_check_delta_today": _check_delta_today,
    "_check_llm_quality": _check_llm_quality,
}

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# 管线 → 步骤 ID 集合（用于 --pipeline 过滤）
PIPELINE_STEPS = {
    "close":    {"health", "review", "catalyst_track", "brief"},
    "night":    {"watchdog", "health", "collect", "collect_weekly", "wechat",
                 "zsxq_analysis", "summary", "track", "synthesis", "catalyst_screen", "serenity"},
    "pre_dawn": {"health", "unified", "delta", "marginal"},
    "pre":      {"health", "advice", "advice_upload", "advice_server"},
}


def _resolve_target_date(target_strategy: str) -> str:
    if target_strategy == "last_trade":
        return _last_trading_day().isoformat()
    return _today()


# ============================================================
# 核心检查逻辑
# ============================================================

def _check_output(entry: tuple, target_date: str = None) -> dict:
    """检查单个产出。
    返回 {name, ok, mtime_str, target_date, pipeline, deadline, priority, fix_cmd, is_db, stale}
    stale: 文件存在但修改时间早于截止时间（可能含过期数据）
    """
    name, path_or_fn, tgt_strategy, pipeline, fix_cmd, priority, deadline = entry
    tgt_date = target_date or _resolve_target_date(tgt_strategy)

    if path_or_fn.startswith("_"):
        fn = DB_CHECKS.get(path_or_fn)
        if fn:
            ok, info = fn()
        else:
            ok, info = False, f"未知检查函数 {path_or_fn}"
        return {
            "name": name, "ok": ok, "mtime_str": info if ok else "—",
            "target_date": tgt_date, "pipeline": pipeline, "deadline": deadline,
            "priority": priority, "fix_cmd": fix_cmd, "is_db": True, "stale": False,
        }

    # 文件类检查 — 只检查目标日期文件
    path = PROJECT / "daily_review" / path_or_fn.format(date=tgt_date)

    # morning_intel 产出的路径在 morning_intel/reports/ 而不是 daily_review/reports/
    if not path.exists() and "morning_intel" in path_or_fn:
        path = PROJECT / path_or_fn.format(date=tgt_date)

    if not path.exists():
        return {
            "name": name, "ok": False, "mtime_str": "—",
            "target_date": tgt_date, "pipeline": pipeline, "deadline": deadline,
            "priority": priority, "fix_cmd": fix_cmd, "is_db": False, "stale": False,
        }

    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    mtime_str = mtime.strftime("%m-%d %H:%M")

    # 检查是否过期：文件修改时间早于目标日期（不是截止时间之前，而是日期本身不对）
    stale = False
    try:
        dl = datetime.strptime(f"{tgt_date} {deadline}", "%Y-%m-%d %H:%M")
        # 过期 = 文件 mtime 日期 < 目标日期（即文件是更早的日期生成的）
        if mtime.date() < date.fromisoformat(tgt_date):
            stale = True
    except ValueError:
        pass

    return {
        "name": name, "ok": True, "mtime_str": mtime_str,
        "target_date": tgt_date, "pipeline": pipeline, "deadline": deadline,
        "priority": priority, "fix_cmd": fix_cmd, "is_db": False, "stale": stale,
    }


def check_all(target_date: str = None, pipeline: str = None) -> list[dict]:
    entries = EXPECTED_OUTPUTS
    if pipeline:
        steps = PIPELINE_STEPS.get(pipeline, set())
        entries = [e for e in EXPECTED_OUTPUTS if e[3] in steps]
    results = [_check_output(e, target_date) for e in entries]
    results.sort(key=lambda r: PRIORITY_ORDER.get(r["priority"], 99))
    return results


def check_all_condensed(target_date: str = None) -> list[dict]:
    return [r for r in check_all(target_date) if not r["ok"]]


# ============================================================
# 自修复
# ============================================================

def _notify(title: str, content: str):
    try:
        sys.path.insert(0, str(PROJECT / "morning_intel"))
        from notify import push
        push(title, content)
    except Exception:
        pass


def _run_fix(cmd: str, name: str, timeout_sec: int = 1200) -> bool:
    print(f"  🔧 补跑: {name}")
    print(f"     命令: {cmd}")
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(PROJECT),
            capture_output=True, text=True, timeout=timeout_sec,
            encoding="utf-8", errors="replace",
        )
        ok = proc.returncode == 0
        if ok:
            print(f"     ✅ 成功")
        else:
            print(f"     ❌ 失败 (exit={proc.returncode})")
            if proc.stderr:
                print(f"     stderr: {proc.stderr[:200]}")
        return ok
    except subprocess.TimeoutExpired:
        print(f"     ⏰ 超时 ({timeout_sec}s)")
        return False
    except Exception as e:
        print(f"     ❌ 异常: {e}")
        return False


def auto_fix(results: list[dict] = None, fix_all: bool = False) -> dict[str, bool]:
    if results is None:
        results = check_all()

    now = datetime.now()
    fix_results = {}
    alerted = []

    for r in results:
        if r["ok"] and not r.get("stale"):
            continue

        prio = r["priority"]
        if not fix_all and prio not in ("CRITICAL", "HIGH"):
            continue

        if r["deadline"]:
            try:
                dl = datetime.strptime(f"{r['target_date']} {r['deadline']}", "%Y-%m-%d %H:%M")
                if now > dl + timedelta(hours=2):
                    alerted.append(f"{r['name']} (目标{r['target_date']} 截止{r['deadline']}，已超2h)")
                    continue
            except ValueError:
                pass

        ok = _run_fix(r["fix_cmd"], r["name"])
        fix_results[r["name"]] = ok

    if alerted:
        msg = "\n".join(f"- {a}" for a in alerted)
        print(f"\n⚠️ 以下产出已过补跑窗口，需手动处理:\n{msg}")
        _notify("输出自检: 产出过期", msg)

    return fix_results


# ============================================================
# 报告输出
# ============================================================

def print_report(results: list[dict] = None):
    if results is None:
        results = check_all()

    now = datetime.now()
    print(f"\n{'='*70}")
    print(f"  📋 输出自检 {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")

    ok_count = sum(1 for r in results if r["ok"] and not r.get("stale"))
    stale_count = sum(1 for r in results if r["ok"] and r.get("stale"))
    missing = [r for r in results if not r["ok"]]
    critical_missing = [r for r in missing if r["priority"] == "CRITICAL"]

    for r in results:
        if not r["ok"]:
            icon = "❌"
        elif r.get("stale"):
            icon = "⚠️"
        else:
            icon = "✅"
        db_flag = " [DB]" if r["is_db"] else ""
        target_dt = f"{r['target_date'][5:]} {r['deadline']}"

        # 计算落后时长
        delay_str = ""
        try:
            dl = datetime.strptime(f"{r['target_date']} {r['deadline']}", "%Y-%m-%d %H:%M")
            gap_h = (now - dl).total_seconds() / 3600
            if gap_h > 0 and (not r["ok"] or r.get("stale")):
                delay_str = f" 落后{gap_h:.0f}h"
            elif not r["ok"] and gap_h > -0.5:
                delay_str = " 临近截止"
        except ValueError:
            pass

        stale_note = " 📂过期数据" if r.get("stale") else ""
        print(f"  {icon} {r['name']:<10} {r['mtime_str']:>11}{db_flag}  目标{target_dt}  [{r['pipeline']}]{delay_str}{stale_note}")

    print(f"\n  ─────────────────────")
    print(f"  总计: {ok_count}/{len(results)} 已产出", end="")
    if stale_count:
        print(f"（{stale_count} 过期）", end="")
    print()

    if critical_missing:
        names = ", ".join(r["name"] for r in critical_missing)
        print(f"  🔴 CRITICAL 缺失: {names}")
    if missing:
        names = ", ".join(r["name"] for r in missing)
        print(f"  🟡 缺失 ({len(missing)}): {names}")
    else:
        print(f"  ✅ 全部产出就绪")
    print()


# ============================================================
# CLI
# ============================================================

def main():
    do_fix = "--fix" in sys.argv
    fix_all = "--all" in sys.argv
    target_date = None
    pipeline = None
    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i + 1 < len(sys.argv):
            target_date = sys.argv[i + 1]
        if arg == "--pipeline" and i + 1 < len(sys.argv):
            pipeline = sys.argv[i + 1]

    results = check_all(target_date, pipeline=pipeline)
    print_report(results)

    if do_fix:
        gap = [r for r in results if not r["ok"]]
        if gap:
            print(f"🔧 开始自动补跑 {len(gap)} 项缺失产出...\n")
            fix_results = auto_fix(results, fix_all=fix_all)
            failed = [k for k, v in fix_results.items() if not v]
            if failed:
                print(f"\n❌ 补跑失败: {', '.join(failed)}")
                _notify("输出自检: 补跑失败", f"失败: {', '.join(failed)}")
                return 1
            else:
                print(f"\n✅ 全部补跑成功 ({len(fix_results)} 项)")
        else:
            print("✅ 无缺失产出，跳过补跑")

    missing = [r for r in results if not r["ok"]]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
