"""盘中流水线循环执行 — 每30分钟一次，9:30-15:00。

替代 Windows Task Scheduler 的 10:30/14:00 两次触发。
用法:
    python run_intraday_loop.py
"""
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

INTERVAL_MINUTES = 30
START_TIME = dtime(9, 30)
END_TIME = dtime(15, 0)
PROJECT = Path(__file__).resolve().parent


def _in_trading_hours() -> bool:
    now = datetime.now().time()
    return START_TIME <= now <= END_TIME


def _run_pipeline() -> bool:
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%H:%M')}] 盘中流水线")
    print(f"{'='*50}")

    steps = [
        ("health", "系统健康检查",
         ["python", "daily_review/health_check.py"]),
        ("feeds", "盘中情报",
         ["python", "morning_intel/intraday_feeds.py"]),
        ("validate", "盘中验证",
         ["python", "morning_intel/run_morning.py", "--phase", "intraday"]),
    ]

    ok = True
    for sid, name, cmd in steps:
        print(f"\n--- {name} ---")
        try:
            result = subprocess.run(
                cmd, cwd=str(PROJECT),
                capture_output=False, timeout=300,
            )
            status = "OK" if result.returncode == 0 else f"FAIL({result.returncode})"
            print(f"  {status}")
            if result.returncode != 0:
                ok = False
        except subprocess.TimeoutExpired:
            print("  TIMEOUT")
            ok = False
        except Exception as e:
            print(f"  ERROR: {e}")
            ok = False

    return ok


def main():
    print(f"盘中循环扫描 | 间隔 {INTERVAL_MINUTES} 分钟 | "
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

        _run_pipeline()
        run_count += 1

        next_minute = ((now.minute // INTERVAL_MINUTES) + 1) * INTERVAL_MINUTES
        sleep_minutes = next_minute - now.minute
        if sleep_minutes <= 0:
            sleep_minutes = INTERVAL_MINUTES
        print(f"\n  下次扫描: ~{sleep_minutes} 分钟后")
        time.sleep(sleep_minutes * 60)


if __name__ == "__main__":
    main()
