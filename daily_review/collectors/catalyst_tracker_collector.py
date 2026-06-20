"""催化剂走势跟踪 — Collector 包装。

包装 catalyst_tracker.track() 为标准 Collector 接口。
依赖 Redis 实时行情 → 盘后运行（post_market tier）。

用法:
    python -m daily_review.collectors.catalyst_tracker_collector
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Callable

import store

SOURCE_NAME = "catalyst_tracker"


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    """盘后调用 catalyst_tracker.track() 并记录状态。"""
    today = until.isoformat()
    store.init_feeds_tables()

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from catalyst_tracker import track
    except ImportError as e:
        return {
            "last_date": today, "status": "error",
            "message": f"导入 catalyst_tracker 失败: {e}",
        }

    try:
        result = track(today)
        if result is None:
            return {
                "last_date": today, "status": "ok",
                "message": "无活性催化或无Redis行情（跳过）",
            }
        confirmed, reactivated = result
        return {
            "last_date": today, "status": "ok",
            "message": f"确认{len(confirmed)}条催化（{len(reactivated)}条历史复活）",
        }
    except Exception as e:
        return {
            "last_date": today, "status": "error",
            "message": f"track 异常: {str(e)[:200]}",
        }
