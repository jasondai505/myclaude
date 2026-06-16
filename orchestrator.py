"""统一调度器 — 四段流水线编排。

时序:
    close     17:00  收盘复盘 + 催化走势确认 (~15min)
    night     22:00  批量采集 + 所有深度分析 (~45min)
    pre_dawn   5:00  美股收盘数据 + FEV/Δ 刷新 (~10min)
    pre        6:30  最终盘前建议生成 (~5min)

用法:
    python orchestrator.py close / night / pre_dawn / pre
    python orchestrator.py pre --dry-run
    python orchestrator.py list
"""
from __future__ import annotations

import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "dashboard" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

PIPELINES: dict[str, dict[str, Any]] = {
    "close": {
        "name": "收盘流水线",
        "desc": "复盘 + 催化走势确认 + 日报简报",
        "trigger": "17:00",
        "steps": [
            {"id": "health", "name": "系统健康检查", "cmd": "python daily_review/health_check.py"},
            {"id": "review", "name": "收盘复盘", "cmd": "python daily_review/run.py"},
            {"id": "catalyst_track", "name": "催化走势跟踪", "cmd": "python daily_review/daily_collect.py --tier post_market"},
            {"id": "brief", "name": "日报简报", "cmd": "python morning_intel/daily_brief.py"},
            {"id": "audit", "name": "输出自检(close)", "cmd": "python daily_review/output_audit.py --fix --pipeline close"},
        ],
    },
    "night": {
        "name": "深夜流水线",
        "desc": "批量采集 + 全部深度分析引擎",
        "trigger": "22:00",
        "steps": [
            {"id": "watchdog", "name": "流水线看门狗", "cmd": "python daily_review/pipeline_watchdog.py"},
            {"id": "health", "name": "系统健康检查", "cmd": "python daily_review/health_check.py"},
            {"id": "collect", "name": "数据采集(日更·含行业深研+新闻信号)", "cmd": "python daily_review/daily_collect.py --tier daily"},
            {"id": "collect_weekly", "name": "数据采集(低频·仅周五)", "cmd": "python daily_review/daily_collect.py --tier weekly", "only_on": [4]},
            {"id": "wechat", "name": "公众号两阶段AI分析", "cmd": "python daily_review/analyze_wechat.py"},
            {"id": "zsxq_analysis", "name": "星球两阶段AI分析", "cmd": "python daily_review/analyze_zsxq.py"},
            {"id": "summary", "name": "复盘摘要", "cmd": "python daily_review/review_summary.py"},
            {"id": "track", "name": "推荐追踪", "cmd": "python daily_review/track_recommendations.py"},
            {"id": "synthesis", "name": "四源交叉验证", "cmd": "python daily_review/primary_synthesis.py"},
            {"id": "catalyst_screen", "name": "催化快速筛查", "cmd": "python daily_review/catalyst_screen.py"},
            {"id": "serenity", "name": "产业链卡脖子更新", "cmd": "python daily_review/serenity_kb.py --update-all"},
            {"id": "audit", "name": "输出自检(night)", "cmd": "python daily_review/output_audit.py --fix --pipeline night"},
        ],
    },
    "pre_dawn": {
        "name": "凌晨刷新",
        "desc": "美股收盘数据 + FEV/G-Factor/Δ 评分 + 边际变化",
        "trigger": "05:00",
        "steps": [
            {"id": "health", "name": "系统健康检查", "cmd": "python daily_review/health_check.py"},
            {"id": "unified", "name": "统一FEV+G-Factor评分", "cmd": "python daily_review/unified_scorer.py --from-feeds"},
            {"id": "delta", "name": "Δ边际变化评分", "cmd": "python daily_review/feval.py --update-delta"},
            {"id": "marginal", "name": "边际变化检测", "cmd": "python daily_review/engine_marginal.py"},
            {"id": "serenity", "name": "标的FEV评分更新", "cmd": "python daily_review/serenity_kb.py --update-stocks"},
            {"id": "audit", "name": "输出自检(pre_dawn)", "cmd": "python daily_review/output_audit.py --fix --pipeline pre_dawn"},
        ],
    },
    "pre": {
        "name": "盘前建议",
        "desc": "健康 → 解读 → 盘前建议 → 启动服务",
        "trigger": "06:30",
        "steps": [
            {"id": "audit", "name": "输出自检(全量)", "cmd": "python daily_review/output_audit.py --fix"},
            {"id": "health", "name": "系统健康检查", "cmd": "python daily_review/health_check.py"},
            {"id": "interpret", "name": "盘前解读", "cmd": "python morning_intel/run_morning.py --phase pre"},
            {"id": "advice", "name": "生成盘前建议", "cmd": "python daily_review/_run_advice.py"},
            {"id": "advice_upload", "name": "上传 advice 图片", "cmd": "python daily_review/upload_advice.py"},
            {"id": "advice_server", "name": "启动Advice HTTP服务", "cmd": "python daily_review/advice_server.py --daemon"},
        ],
    },
    "bom": {
        "name": "BOM 产业链分析",
        "desc": "产业链拆解 + 龙头护城河评分",
        "trigger": "18:30",
        "steps": [
            {"id": "bom", "name": "BOM日更", "cmd": "python bom_analyzer/run.py --daily"},
        ],
    },
    "collect": {
        "name": "仅数据采集",
        "desc": "全量采集（日更+低频），不分层",
        "steps": [
            {"id": "collect", "name": "数据采集", "cmd": "python daily_review/daily_collect.py"},
        ],
    },
    "intraday": {
        "name": "盘中流水线",
        "desc": "⚠️ 已由 run_intraday_loop.py 双频自循环替代，保留仅作手动备用",
        "trigger": "10:30 / 14:00",
        "steps": [
            {"id": "health", "name": "系统健康检查", "cmd": "python daily_review/health_check.py"},
            {"id": "feeds", "name": "盘中情报", "cmd": "python morning_intel/intraday_feeds.py"},
            {"id": "catalyst_monitor", "name": "催化盘中监控", "cmd": "python daily_review/catalyst_monitor.py"},
            {"id": "validate", "name": "盘中验证", "cmd": "python morning_intel/run_morning.py --phase intraday"},
        ],
    },
    "wechat_only": {
        "name": "仅公众号分析",
        "desc": "微信公众号两阶段 AI 分析",
        "steps": [
            {"id": "wechat", "name": "公众号分析", "cmd": "python daily_review/analyze_wechat.py"},
        ],
    },
}


def _notify(title: str, content: str):
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "morning_intel"))
        from notify import push
        push(title, content)
    except Exception:
        pass


def run_step(step: dict, log_file) -> bool:
    cmd = step["cmd"]
    log_file.write(f"\n{'='*60}\n")
    log_file.write(f"步骤: {step['name']}\n")
    log_file.write(f"命令: {cmd}\n")
    log_file.write(f"开始: {datetime.now()}\n")
    log_file.write(f"{'='*60}\n\n")
    log_file.flush()

    try:
        proc = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            text=True, encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            log_file.write(line)
            log_file.flush()
        try:
            proc.wait(timeout=1200)  # 20min timeout, prevent akshare hang
        except subprocess.TimeoutExpired:
            proc.kill()
            log_file.write(f"\n!!! step timeout (20min), killed: {step['name']}\n")
            log_file.flush()
            rc = 1
        else:
            rc = proc.returncode
    except Exception as e:
        log_file.write(f"\nexception: {e}\n")
        log_file.flush()
        rc = 1

    log_file.write(f"\n退出码: {rc}\n")
    log_file.write(f"结束: {datetime.now()}\n\n")
    log_file.flush()
    return rc == 0


def run_pipeline(name: str, *, dry_run: bool = False, from_step: str = None) -> bool:
    if name not in PIPELINES:
        print(f"未知流水线: {name}")
        print(f"可用: {', '.join(PIPELINES.keys())}")
        return False

    cfg = PIPELINES[name]
    steps = cfg["steps"]
    skip = from_step is not None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"{name}_{ts}.log"

    print(f"\n{'='*60}")
    print(f"  {cfg['name']}")
    print(f"  {cfg.get('desc', '')}")
    print(f"{'='*60}")
    print(f"  日志: {log_path}")
    if dry_run:
        print("  DRY RUN — 仅打印不执行")
    if from_step:
        print(f"  从步骤 [{from_step}] 恢复")
    print()

    if dry_run:
        for i, s in enumerate(steps):
            marker = "[SKIP] " if (skip and s["id"] != from_step) else "[RUN] "
            print(f"  {i+1}. {marker}{s['name']}: {s['cmd']}")
        return True

    failed = []
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"=== {cfg['name']} ===\n")
        log.write(f"启动: {datetime.now()}\n")
        log.write(f"日期: {date.today().isoformat()}\n\n")

        today_wd = date.today().weekday()
        for i, step in enumerate(steps):
            if skip:
                if step["id"] == from_step:
                    skip = False
                else:
                    print(f"  [{i+1}/{len(steps)}] [SKIP] {step['name']}")
                    log.write(f"[SKIP] {step['name']}\n\n")
                    continue

            only_on = step.get("only_on")
            if only_on and today_wd not in only_on:
                print(f"  [{i+1}/{len(steps)}] [SKIP] {step['name']} (only_on={only_on})")
                log.write(f"[SKIP] {step['name']} (only_on={only_on})\n\n")
                continue

            print(f"  [{i+1}/{len(steps)}] [RUN] {step['name']}...", end=" ", flush=True)
            ok = run_step(step, log)
            print("OK" if ok else "FAIL")
            if not ok:
                failed.append(step["name"])

        log.write(f"\n{'='*60}\n")
        if failed:
            log.write(f"失败步骤: {', '.join(failed)}\n")
        else:
            log.write("全部成功\n")
        log.write(f"结束: {datetime.now()}\n")

    if failed:
        _notify(f"❌ {cfg['name']} 失败", f"失败步骤: {', '.join(failed)}\n日志: {log_path}")
        print(f"\n[FAIL] 失败步骤: {', '.join(failed)}")
        return False

    _notify(f"[OK] {cfg['name']} 完成", f"全部 {len(steps)} 步成功\n日志: {log_path}")
    print(f"\n[OK] 全部完成 ({len(steps)} 步)")
    return True


def list_pipelines():
    print("\n可用流水线:\n")
    for key, cfg in PIPELINES.items():
        print(f"  {key:<15} {cfg['name']}")
        print(f"  {' ':<15} {cfg.get('desc', '')}")
        if cfg.get("trigger"):
            print(f"  {' ':<15} @ {cfg['trigger']}")
        steps_str = " -> ".join(s["name"] for s in cfg["steps"])
        print(f"  {' ':<15} {len(cfg['steps'])} 步骤: {steps_str}")
        print()


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("list", "-l", "--list"):
        list_pipelines()
        return

    pipeline = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    from_step = None
    for i, arg in enumerate(sys.argv):
        if arg == "--from" and i + 1 < len(sys.argv):
            from_step = sys.argv[i + 1]

    ok = run_pipeline(pipeline, dry_run=dry_run, from_step=from_step)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
