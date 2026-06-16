"""流水线看门狗 — 检测未按时执行的管线，自动补跑。

挂在 pre 管线第一步（pre 是唯一确认被计划任务调度的管线）。
每次 pre 触发时，检查其他管线是否在预期窗口内执行过，
缺失的自动补跑。

用法:
    python daily_review/pipeline_watchdog.py          # 检查 + 自动补跑
    python daily_review/pipeline_watchdog.py --dry-run  # 仅报告，不执行
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROJECT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT / "dashboard" / "logs"

# 管线 → 预期频率 (小时) + 一天内预期最少触发次数
PIPELINE_EXPECTATIONS: dict[str, dict] = {
    "close":   {"freq_hours": 26, "min_runs": 1, "desc": "收盘流水线 (review+catalyst_track+daily_brief)"},
    "pre":     {"freq_hours": 26, "min_runs": 1, "desc": "盘前流水线 (advice+FEV+catalyst_screen)"},
    "bom":     {"freq_hours": 48, "min_runs": 0, "desc": "BOM产业链分析"},
    "intraday": {"freq_hours": 48, "min_runs": 0, "desc": "盘中流水线 (intraday_feeds+验证)"},
}

# 交易日历（简单周末降级）
def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _pipeline_last_run() -> dict[str, datetime | None]:
    """扫描日志目录，返回每条管线最近一次执行时间"""
    result: dict[str, datetime | None] = {k: None for k in PIPELINE_EXPECTATIONS}
    if not LOG_DIR.exists():
        return result
    for p in sorted(LOG_DIR.glob("*.log")):
        stem = p.stem
        # 日志命名: {pipeline}_{YYYYMMDD}_{HHMMSS}.log  (orchestrator 格式)
        for name in PIPELINE_EXPECTATIONS:
            if stem.startswith(f"{name}_"):
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                if result[name] is None or mtime > result[name]:
                    result[name] = mtime
                break
    return result


def check_gaps(now: datetime | None = None) -> list[str]:
    """返回应该跑但没跑的管线列表"""
    now = now or datetime.now()
    today = now.date()
    last_runs = _pipeline_last_run()
    gaps = []

    for name, cfg in PIPELINE_EXPECTATIONS.items():
        if _is_weekend(today) and name in ("close", "intraday"):
            continue  # 周末不强制要求
        last = last_runs.get(name)
        if last is None:
            # 从未跑过 — 只有需要 min_runs > 0 的才报
            if cfg["min_runs"] > 0:
                gaps.append(name)
        else:
            hours_ago = (now - last).total_seconds() / 3600
            if hours_ago > cfg["freq_hours"]:
                gaps.append(name)

    return gaps


def backfill(pipelines: list[str], dry_run: bool = False) -> dict[str, bool]:
    """对缺失管线执行补跑 — 优先精确定位缺失产出而非整条重跑。"""
    from output_audit import check_all_condensed, EXPECTED_OUTPUTS

    results = {}
    # 先检查具体哪些产出缺失
    gaps = check_all_condensed()
    gap_names = {g["name"] for g in gaps}

    for name in pipelines:
        if name == "pre":
            continue  # pre 正在跑，不递归

        # 找到该管线对应的缺失产出
        # EXPECTED_OUTPUTS: (name, path, target_strategy, pipeline_step, fix_cmd, priority, deadline)
        pipeline_outputs = [
            e for e in EXPECTED_OUTPUTS
            if e[3] in _pipeline_steps(name) and e[0] in gap_names
        ]
        if pipeline_outputs:
            # 精确定位：只补跑缺失的步骤
            for out in pipeline_outputs:
                display_name, _, _, step_id, fix_cmd, priority, deadline = out
                print(f"\n  ⚠️ {display_name} 未产出（管线 {name}/{step_id}），自动补跑...")
                if dry_run:
                    print(f"    [DRY RUN] 将执行: {fix_cmd}")
                    results[f"{name}/{display_name}"] = True
                    continue
                try:
                    proc = subprocess.run(
                        fix_cmd, shell=True, cwd=str(PROJECT),
                        capture_output=False,
                    )
                    ok = proc.returncode == 0
                    results[f"{name}/{display_name}"] = ok
                    status = "OK" if ok else f"FAIL({proc.returncode})"
                    print(f"    补跑结果: {status}")
                except Exception as e:
                    print(f"    ERROR: {e}")
                    results[f"{name}/{display_name}"] = False
        else:
            # 没有具体产出缺失，但管线日志显示没跑 → 整条补跑
            bat = PROJECT / f"run_{name}.bat"
            cmd = f'python orchestrator.py {name}'
            print(f"\n  ⚠️ {name} 未按时执行，自动补跑...")
            if bat.exists():
                print(f"    触发: {bat}")
            if dry_run:
                print(f"    [DRY RUN] 将执行: {cmd}")
                results[name] = True
                continue
            try:
                proc = subprocess.run(
                    cmd, shell=True, cwd=str(PROJECT),
                    capture_output=False,
                )
                ok = proc.returncode == 0
                results[name] = ok
                status = "OK" if ok else f"FAIL({proc.returncode})"
                print(f"    补跑结果: {status}")
            except Exception as e:
                print(f"    ERROR: {e}")
                results[name] = False

    return results


def _pipeline_steps(name: str) -> list[str]:
    """返回管线包含的步骤 ID 列表（从 orchestrator 导入，避免硬编码）。"""
    try:
        sys.path.insert(0, str(PROJECT))
        from orchestrator import PIPELINES
        cfg = PIPELINES.get(name, {})
        return [s["id"] for s in cfg.get("steps", [])]
    except Exception:
        # 硬编码回退
        STEPS_MAP = {
            "close": ["health", "review", "catalyst_track", "brief"],
            "night": ["watchdog", "health", "collect", "collect_weekly", "wechat",
                      "zsxq_analysis", "summary", "track", "synthesis", "catalyst_screen", "serenity"],
            "pre_dawn": ["health", "unified", "delta", "marginal", "serenity"],
            "pre": ["health", "interpret", "advice", "advice_upload", "advice_server"],
            "bom": ["bom"],
        }
        return STEPS_MAP.get(name, [])


def main():
    dry_run = "--dry-run" in sys.argv
    now = datetime.now()
    print(f"=== 流水线看门狗 {now.strftime('%Y-%m-%d %H:%M')} ===")

    last_runs = _pipeline_last_run()
    for name, cfg in PIPELINE_EXPECTATIONS.items():
        last = last_runs.get(name)
        if last:
            ago = (now - last).total_seconds() / 3600
            print(f"  [{name}] 上次: {last.strftime('%m-%d %H:%M')} ({ago:.0f}h前)")
        else:
            print(f"  [{name}] 从未执行")

    gaps = check_gaps(now)
    if not gaps:
        print("\n  全部管线按时执行 ✅")
        return 0

    print(f"\n  发现 {len(gaps)} 条管线未按时执行: {', '.join(gaps)}")
    results = backfill(gaps, dry_run=dry_run)
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"\n  补跑失败: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
