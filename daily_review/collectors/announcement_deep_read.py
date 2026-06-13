"""公告深度研读采集器。

在 announcements collector 之后运行。从 SQLite 读取当日公告，
经过 Stage 1 硬筛选 → Stage 2 LLM 精读 → 存档 Obsidian → 写入 catalyst_signals。

模式：collector 标准接口 run(since, until, universe_fn)。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import store
from deep_read.hard_filters import stage1_filter
from deep_read.llm_deep_read import deep_read_batch
from deep_read.obsidian_archive import write_obsidian_file

SOURCE_NAME = "announcement_deep_read"


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    """主入口：对指定日期范围的公告执行深度研读。

    返回: {"last_date": str, "stage1_count": int, "stage2_count": int, "saved_count": int, "status": str, "message": str}
    """
    store.init_feeds_tables()
    today = since  # 公告深度研读按天处理

    date_str = today.isoformat()
    stage1_count = 0
    stage2_count = 0
    saved_count = 0

    # 1. 加载猎场缓存（首次运行或缓存过期时重建）
    try:
        from deep_read.knowledge_base import load_hunting_ground
        hg = load_hunting_ground()
        if not hg:
            from deep_read.knowledge_base import build_hunting_ground
            hg = build_hunting_ground()
    except Exception as e:
        return {
            "last_date": date_str, "stage1_count": 0, "stage2_count": 0,
            "saved_count": 0, "status": "error",
            "message": f"猎场缓存构建失败: {e}",
        }

    # 2. 从 SQLite 读取当日公告
    announcements = store.query_announcements(date_str)
    if not announcements:
        return {
            "last_date": date_str, "stage1_count": 0, "stage2_count": 0,
            "saved_count": 0, "status": "ok",
            "message": f"当日 ({date_str}) 无公告数据",
        }

    # 3. 转换为硬筛选需要的格式
    raw_anns = []
    for a in announcements:
        raw_anns.append({
            "code": a.get("code", ""),
            "name": a.get("name", ""),
            "ann_title": a.get("title", ""),
            "ann_type": a.get("type", ""),
            "ann_url": a.get("url", ""),
            "ann_full_text": a.get("title", ""),  # SQLite 未存全文，用标题代替
            "date": a.get("date", ""),
        })

    # 4. Stage 1: 硬筛选
    qualified = stage1_filter(raw_anns)
    stage1_count = len(qualified)

    if not qualified:
        return {
            "last_date": date_str, "stage1_count": 0, "stage2_count": 0,
            "saved_count": 0, "status": "ok",
            "message": f"Stage 1 完成，{len(raw_anns)} 条公告中 0 条通过硬筛选",
        }

    # 保存到 deep_read_queue
    store.save_deep_read_queue(qualified)

    # 5. Stage 2: LLM 精读
    scored = deep_read_batch(qualified)
    stage2_count = len(scored)

    if not scored:
        return {
            "last_date": date_str, "stage1_count": stage1_count, "stage2_count": 0,
            "saved_count": 0, "status": "ok",
            "message": f"Stage 1 通过 {stage1_count} 条，Stage 2 全部失败或未达到阈值",
        }

    # 6. 存档到 DB + Obsidian
    from config import DEEP_READ_RULES
    min_score = DEEP_READ_RULES.get("min_deep_read_score", 60)

    for r in scored:
        total = r.get("total_score", 0)
        event_type = "deep_read"
        ann_type = r.get("ann_type", "")

        # 推断 event_type
        if "收购" in ann_type or "重组" in ann_type:
            event_type = "acquisition" if not r.get("chokepoint_key") else "chokepoint_acquisition"
        elif "业绩" in ann_type:
            event_type = "earnings"
        elif "股权激励" in ann_type:
            event_type = "equity_incentive"
        elif "增持" in ann_type:
            event_type = "insider_buy"
        elif "减持" in ann_type:
            event_type = "insider_sell"

        obsidian_path = ""
        if total >= min_score:
            try:
                obsidian_path = write_obsidian_file(r)
            except Exception as e:
                print(f"  [WARN] Obsidian 存档失败: {e}")

        # 写入 catalyst_signals（深度研读结果作为催化剂信号）
        try:
            signal_id = f"dr_{r['code']}_{date_str}_{event_type}"
            store.save_catalyst_signals([{
                "date": date_str,
                "signal_id": signal_id,
                "source_type": "deep_read",
                "source_title": r.get("ann_title", ""),
                "source_text": r.get("investment_thesis", ""),
                "catalyst_name": r.get("ann_title", "")[:80],
                "catalyst_type": event_type,
                "magnitude_score": min(r.get("core_contradiction_score", 0), 10),
                "specificity_score": min(r.get("info_delta_score", 0), 10),
                "novelty_score": 5,
                "urgency_score": r.get("scenario_calibration_score", 0),
                "actionability": total,
                "source_count": 1,
                "mentioned_codes": r.get("code", ""),
                "thesis": r.get("investment_thesis", ""),
                "time_horizon": r.get("time_horizon", "month"),
                "sonnet_validated": 1,
            }])
            r["catalyst_signal_id"] = signal_id
        except Exception as e:
            print(f"  [WARN] catalyst_signals 写入失败: {e}")

        # 写入 deep_read_results
        try:
            store.save_deep_read_results([{
                "date": date_str,
                "code": r.get("code", ""),
                "name": r.get("name", ""),
                "ann_title": r.get("ann_title", ""),
                "ann_type": r.get("ann_type", ""),
                "event_type": event_type,
                "hunting_domain": r.get("hunting_domain", ""),
                "chokepoint_key": r.get("chokepoint_key", ""),
                "core_contradiction_score": r.get("core_contradiction_score", 0),
                "info_delta_score": r.get("info_delta_score", 0),
                "interest_alignment_score": r.get("interest_alignment_score", 0),
                "governance_signal_score": r.get("governance_signal_score", 0),
                "scenario_calibration_score": r.get("scenario_calibration_score", 0),
                "total_score": total,
                "investment_thesis": r.get("investment_thesis", ""),
                "time_horizon": r.get("time_horizon", "month"),
                "risk_factors": r.get("risk_factors", []),
                "comparable_precedents": r.get("comparable_precedents", ""),
                "haiku_extraction": r.get("haiku_extraction", ""),
                "sonnet_scoring": r.get("sonnet_scoring", ""),
                "obsidian_path": obsidian_path,
                "catalyst_signal_id": r.get("catalyst_signal_id", ""),
            }])
            saved_count += 1
        except Exception as e:
            print(f"  [WARN] deep_read_results 保存失败: {e}")

    return {
        "last_date": date_str,
        "stage1_count": stage1_count,
        "stage2_count": stage2_count,
        "saved_count": saved_count,
        "status": "ok",
        "message": (
            f"{len(raw_anns)} 条公告 → Stage 1 通过 {stage1_count} 条 → "
            f"Stage 2 完成 {stage2_count} 条 → 存档 {saved_count} 条"
        ),
    }
