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


def _save_to_sector_log(today: str, day_data: dict) -> int:
    """将概念计数写入 sector_rotation_log，供板块轮动 engine 使用。"""
    concept_counts = day_data.get("concept_counts", {})
    if not concept_counts:
        return 0
    # 去噪：排除占位符概念和单只标的的概念
    rows = []
    for concept, cnt in sorted(concept_counts.items()):
        if not concept or concept == "--" or cnt <= 1:
            continue
        rows.append({
            "date": today,
            "row_type": "index_score",
            "score": cnt,
            "sector": concept,
        })
    if rows:
        import store as _st
        _st.init_db()
        with _st._conn() as conn:
            conn.execute("DELETE FROM sector_rotation_log WHERE date = ? AND row_type = 'index_score'", (today,))
            conn.executemany(
                "INSERT INTO sector_rotation_log "
                "(date, row_type, score, sector) VALUES (?, ?, ?, ?)",
                [(r["date"], r["row_type"], r["score"], r["sector"]) for r in rows],
            )
    return len(rows)


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

        n_sectors = _save_to_sector_log(today, day_data)

        pool_n = day_data.get("pool_count", 0)
        lu_n = day_data.get("limit_up_count", 0)
        mc_n = sum(day_data.get("multi_counts", {}).values())
        return {
            "last_date": today, "status": "ok",
            "message": f"强势池{pool_n}只(涨停{lu_n}) · 多概念标签{mc_n}个 · sector_log +{n_sectors}概念",
        }
    except Exception as e:
        return {
            "last_date": today, "status": "error",
            "message": f"扫描异常: {str(e)[:200]}",
        }
