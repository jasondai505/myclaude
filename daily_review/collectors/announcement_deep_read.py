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


def _process_one_day(date_str: str) -> dict:
    """处理单日公告深度研读，返回 {stage1_count, stage2_count, saved_count, msg, raw_count}。"""
    stage1_count = 0
    stage2_count = 0
    saved_count = 0
    raw_count = 0

    # 幂等：超过3天的旧日期，已有结果就跳过
    # 近3天不跳过——公告可能延迟发布（盘后/次日），INSERT OR REPLACE 处理重复
    from datetime import date as _date, timedelta as _td
    days_ago = (_date.today() - _date.fromisoformat(date_str)).days
    conn = store.get_conn()
    try:
        already = conn.execute(
            "SELECT COUNT(*) FROM deep_read_results WHERE date = ?", (date_str,)
        ).fetchone()[0]
    finally:
        conn.close()
    if already > 0 and days_ago > 3:
        return {"stage1_count": 0, "stage2_count": 0, "saved_count": 0,
                "raw_count": 0, "msg": f"({date_str}) 已有{already}条结果(>{3}天前)，跳过"}

    announcements = store.query_announcements(date_str)
    if not announcements:
        return {"stage1_count": 0, "stage2_count": 0, "saved_count": 0,
                "raw_count": 0, "msg": f"({date_str}) 无公告数据"}

    raw_count = len(announcements)

    # 补全股票名称
    codes_needed = list({str(a.get("code", "")).zfill(6) for a in announcements})
    name_map = {}
    try:
        import data
        quotes = data.fetch_stock_quotes(codes_needed, batch_size=50)
        name_map = {c: q.get("name", "") for c, q in quotes.items()}
    except Exception:
        pass

    from pdf_utils import download_announcement_pdf

    raw_anns = []
    for a in announcements:
        code = str(a.get("code", "")).zfill(6)
        content = a.get("content", "")
        art_code = a.get("art_code", "")
        if not content and art_code:
            content = download_announcement_pdf(art_code, code) or ""
            if content:
                store.save_announcement_content(art_code, content)
        raw_anns.append({
            "code": code,
            "name": name_map.get(code, a.get("name", "")),
            "ann_title": a.get("title", ""),
            "ann_type": a.get("type", ""),
            "ann_url": a.get("url", ""),
            "ann_full_text": content or a.get("title", ""),
            "date": a.get("date", ""),
        })

    qualified = stage1_filter(raw_anns)
    stage1_count = len(qualified)

    if not qualified:
        return {"stage1_count": 0, "stage2_count": 0, "saved_count": 0,
                "raw_count": raw_count, "msg": f"({date_str}) {raw_count}条公告→0条通过硬筛选"}

    store.save_deep_read_queue(qualified)

    scored = deep_read_batch(qualified)
    stage2_count = len(scored)

    if not scored:
        return {"stage1_count": stage1_count, "stage2_count": 0, "saved_count": 0,
                "raw_count": raw_count, "msg": f"({date_str}) Stage1={stage1_count}, Stage2全部失败"}

    from config import DEEP_READ_RULES
    min_score = DEEP_READ_RULES.get("min_deep_read_score", 60)

    for r in scored:
        total = r.get("total_score", 0)
        event_type = "deep_read"
        ann_type = r.get("ann_type", "")

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
                "risk_factors": json.dumps(r.get("risk_factors", []) if isinstance(r.get("risk_factors"), list) else []),
                "comparable_precedents": r.get("comparable_precedents", ""),
                "haiku_extraction": r.get("haiku_extraction", ""),
                "sonnet_scoring": r.get("sonnet_scoring", ""),
                "obsidian_path": obsidian_path,
                "catalyst_signal_id": r.get("catalyst_signal_id", ""),
            }])
            saved_count += 1
        except Exception as e:
            print(f"  [WARN] deep_read_results 保存失败: {e}")

    return {"stage1_count": stage1_count, "stage2_count": stage2_count,
            "saved_count": saved_count, "raw_count": raw_count,
            "msg": f"({date_str}) {raw_count}条→S1={stage1_count}→S2={stage2_count}→存档{saved_count}条"}


def run(since: date, until: date, universe_fn: Callable[[date], set[str]]) -> dict:
    """主入口：对指定日期范围的公告执行深度研读，遍历每一天。"""
    store.init_feeds_tables()

    # 加载猎场缓存（所有日期共享）
    try:
        from deep_read.knowledge_base import load_hunting_ground
        hg = load_hunting_ground()
        if not hg:
            from deep_read.knowledge_base import build_hunting_ground
            hg = build_hunting_ground()
    except Exception as e:
        return {"last_date": fmt_iso(until), "stage1_count": 0, "stage2_count": 0,
                "saved_count": 0, "status": "error", "message": f"猎场缓存构建失败: {e}"}

    from .base import daterange, fmt_iso

    days = list(daterange(since, until))
    if not days:
        days = [since]

    total_stage1 = 0
    total_stage2 = 0
    total_saved = 0
    total_raw = 0
    msgs = []
    last_date = fmt_iso(days[-1])

    for d in days:
        day_result = _process_one_day(d.isoformat())
        total_raw += day_result["raw_count"]
        total_stage1 += day_result["stage1_count"]
        total_stage2 += day_result["stage2_count"]
        total_saved += day_result["saved_count"]
        msgs.append(day_result["msg"])

    msg = f"{len(days)}天: {total_raw}条公告→S1={total_stage1}→S2={total_stage2}→存档{total_saved}条 | {'; '.join(msgs[-3:])}"
    store.upsert_collect_status(SOURCE_NAME, last_date, "ok", msg, total_saved)
    return {"last_date": last_date, "stage1_count": total_stage1,
            "stage2_count": total_stage2, "saved_count": total_saved,
            "status": "ok", "message": msg}
