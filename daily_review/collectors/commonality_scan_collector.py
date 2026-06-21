"""共性扫描 — 每日强势池概念归因 → commonality_cache。

包装 _scan_commonality_v2 的 Redis 扫描 + 缓存落盘逻辑。
产出 daily_review/data/commonality_cache/scan_{date}.json，
供 catalyst_tracker 概念热度检查和 Dashboard 盘面概念信号使用。

用法:
    python -m daily_review.collectors.commonality_scan_collector
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Callable

import store

SOURCE_NAME = "commonality_scan"


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    today = until.isoformat()

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "_archive"))
        from _scan_commonality_v2 import fetch_redis_all, scan_day, save_cache  # type: ignore
    except ImportError as e:
        return {
            "last_date": today, "status": "error",
            "message": f"导入 _scan_commonality_v2 失败: {e}",
        }

    try:
        raw = fetch_redis_all()
        if not raw:
            return {
                "last_date": today, "status": "error",
                "message": "Redis 无数据（可能非交易时间或 Redis 未启动）",
            }

        day_data = scan_day(raw)
        day_data["date"] = today
        save_cache(day_data)

        pool_n = day_data.get("pool_count", 0)
        lu_n = day_data.get("limit_up_count", 0)
        mc_n = sum(day_data.get("multi_counts", {}).values())
        return {
            "last_date": today, "status": "ok",
            "message": f"强势池{pool_n}只(涨停{lu_n}) · 多概念标签{mc_n}个",
        }
    except Exception as e:
        return {
            "last_date": today, "status": "error",
            "message": f"扫描异常: {str(e)[:200]}",
        }
