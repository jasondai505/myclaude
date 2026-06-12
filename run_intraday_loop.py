"""盘中流水线循环执行 — 双频：监控5分钟 + 全管线30分钟，9:30-15:00。

用法:
    python run_intraday_loop.py
"""
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

MONITOR_INTERVAL = 5   # 催化监控 快频
FULL_INTERVAL = 30     # 全管线（健康+情报+验证）慢频
START_TIME = dtime(9, 30)
END_TIME = dtime(15, 0)
PROJECT = Path(__file__).resolve().parent


def _in_trading_hours() -> bool:
    now = datetime.now().time()
    return START_TIME <= now <= END_TIME


def _run_step(name: str, cmd: list[str]) -> bool:
    print(f"\n--- {name} ---")
    try:
        result = subprocess.run(
            cmd, cwd=str(PROJECT),
            capture_output=False, timeout=300,
        )
        ok = result.returncode == 0
        print(f"  {'OK' if ok else f'FAIL({result.returncode})'}")
        return ok
    except subprocess.TimeoutExpired:
        print("  TIMEOUT")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


# 慢频步骤（仅每 FULL_INTERVAL 分钟跑一次）
FULL_STEPS = [
    ("健康检查", ["python", "daily_review/health_check.py"]),
    ("盘中情报", ["python", "morning_intel/intraday_feeds.py"]),
    ("盘中验证", ["python", "morning_intel/run_morning.py", "--phase", "intraday"]),
]

# 快频步骤（每次循环都跑）
FAST_STEPS = [
    ("催化监控", ["python", "daily_review/catalyst_monitor.py"]),
]


def main():
    cycles_per_full = FULL_INTERVAL // MONITOR_INTERVAL
    print(f"盘中双频扫描 | 监控 {MONITOR_INTERVAL}min + 全管线 {FULL_INTERVAL}min | "
          f"{START_TIME.strftime('%H:%M')}-{END_TIME.strftime('%H:%M')}")
    print("等待交易时间...")

    run_count = 0
    while True:
        now = datetime.now()

        if not _in_trading_hours():
            if now.time() > END_TIME:
                print(f"\n[15:00] 收盘，退出（共执行 {run_count} 次）")
                break
            wait_seconds = (
                datetime.combine(now.date(), START_TIME) - now
            ).total_seconds()
            if wait_seconds > 0:
                print(f"  距开盘 {wait_seconds/60:.0f} 分钟，等待...")
                time.sleep(min(wait_seconds, 300))
            continue

        is_full = run_count % cycles_per_full == 0
        label = "全管线" if is_full else "快频监控"
        print(f"\n{'='*50}")
        print(f"[{now.strftime('%H:%M')}] 盘中 {label} (#{run_count+1})")
        print(f"{'='*50}")

        # 监控步骤 — 每次都跑
        for name, cmd in FAST_STEPS:
            _run_step(name, cmd)

        # 全管线步骤 — 仅每 FULL_INTERVAL 分钟
        if is_full:
            for name, cmd in FULL_STEPS:
                _run_step(name, cmd)

        run_count += 1

        # 对齐到下一个 MONITOR_INTERVAL 分钟边界
        next_minute = ((now.minute // MONITOR_INTERVAL) + 1) * MONITOR_INTERVAL
        sleep_minutes = next_minute - now.minute
        if sleep_minutes <= 0:
            sleep_minutes = MONITOR_INTERVAL
        print(f"\n  下次: ~{sleep_minutes} 分钟后")
        time.sleep(sleep_minutes * 60)


if __name__ == "__main__":
    main()
