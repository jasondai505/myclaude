"""个股深度档案构建 — 多维聚合 + LLM一页纸合成 → Obsidian档案。

优先池自动选股（催化/深研/研报/活跃档案 四源加权 Top 22），
8维数据聚合后逐只调 deep 模型生成一页纸档案。

用法:
    python -m daily_review.collectors.stock_dossier_collector
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Callable

SOURCE_NAME = "stock_dossiers"


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    today = until.isoformat()

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from stock_dossier_builder import (
            get_priority_pool, aggregate, synthesize_batch, save_dossiers,
        )
    except ImportError as e:
        return {
            "last_date": today, "status": "error",
            "message": f"导入 stock_dossier_builder 失败: {e}",
        }

    try:
        pool = get_priority_pool()
        if not pool:
            return {
                "last_date": today, "status": "ok",
                "message": "优先池为空（无满足条件的标的）",
            }

        dossiers = aggregate(pool)
        if not dossiers:
            return {
                "last_date": today, "status": "error",
                "message": "数据聚合失败",
            }

        results = synthesize_batch(dossiers, dry_run=False)
        save_dossiers(results, dossiers)

        ok_n = sum(1 for v in results.values() if not v.startswith("#") or "合成失败" not in v)
        return {
            "last_date": today, "status": "ok",
            "message": f"优先池{len(pool)}只 → 聚合8维 → LLM合成{ok_n}/{len(results)}份档案",
        }
    except Exception as e:
        return {
            "last_date": today, "status": "error",
            "message": f"档案构建异常: {str(e)[:200]}",
        }
