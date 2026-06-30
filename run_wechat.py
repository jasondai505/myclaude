"""微信公众号 RSS 一键全流程：健康检查 → 采集入库 → 两阶段 AI 分析。

用法:
    python run_wechat.py           # 采集今天+分析
    python run_wechat.py --skip-collect  # 只分析不采集
"""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(__file__).parent
DAILY = PROJECT / "daily_review"


def _run(cmd: list[str], desc: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable] + cmd,
        cwd=str(PROJECT), timeout=600,
    )
    ok = result.returncode == 0
    print(f"  -> {'OK' if ok else 'FAIL'} (exit {result.returncode})")
    return ok


def main():
    skip_collect = "--skip-collect" in sys.argv

    # Step 1: RSS 健康检查
    if not skip_collect:
        ok = _run(
            [str(DAILY / "check_rss_health.py")],
            "Step 1/3: RSS 健康检查",
        )
        if not ok:
            print("\n⚠️ RSS 不可达或数据陈旧，请先手动刷新 WeWe-RSS 后再运行！")
            print("  刷新地址: http://111.231.44.12:4000/dash")
            sys.exit(1)

        # Step 2: 采集入库（从昨天开始，防跨天漏文章）
        since = (date.today() - timedelta(days=1)).isoformat()
        _run(
            [str(DAILY / "daily_collect.py"), "--source", "wechat",
             "--since", since],
            f"Step 2/3: 采集最新文章入库 (since {since})",
        )

    # Step 3: 两阶段 AI 分析
    _run(
        [str(DAILY / "analyze_wechat.py")],
        "Step 3/3: 两阶段 AI 分析 (Haiku 逐篇 + Sonnet 研判)",
    )

    print(f"\n{'='*60}")
    print(f"  完成！报告: daily_review/reports/wechat_analysis/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
