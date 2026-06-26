"""Serenity 海外产业链情报一键全流程。

用法:
    python run_serenity.py           # Haiku 逐条提取 + 生成日报
"""

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).parent
DAILY = PROJECT / "daily_review"


def _run(cmd: list[str], desc: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable] + cmd, cwd=str(PROJECT), timeout=600,
    )
    ok = result.returncode == 0
    print(f"  -> {'OK' if ok else 'FAIL'} (exit {result.returncode})")
    return ok


def main():
    print("Serenity 海外产业链情报管道")
    print(f"工作目录: {PROJECT}")

    _run(
        [str(DAILY / "_run_serenity_batch.py")],
        "Step 1/1: Haiku 逐条提取 + 生成日报",
    )

    print(f"\n  输出:")
    print(f"    JSON: daily_review/reports/serenity/serenity_extract_*.json")
    print(f"    日报: daily_review/reports/serenity/serenity_daily_*.md")


if __name__ == "__main__":
    main()
