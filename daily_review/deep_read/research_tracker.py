"""研报信号检测 — 纯Python规则，零LLM成本。

从 research_reports 表读取研报，按个股聚合，检测以下信号：
1. 首次覆盖：该股此前90天无研报
2. 评级变化：上调/下调
3. 目标价大幅变动：变化>15%
4. EPS大幅修正：eps_y1变化>10%
5. 多机构集中调高：同日≥2家上调
6. 多机构集中覆盖：同日≥3家发报告

返回有信号的个股列表，仅这些个股触发LLM分析。
"""
from __future__ import annotations

import json
from datetime import date, timedelta, datetime
from collections import defaultdict

import store

# 信号阈值
FIRST_COVERAGE_LOOKBACK = 90      # 90天无研报视为首次覆盖
TARGET_PRICE_CHANGE_PCT = 0.15    # 目标价变化>15%视为大幅变动
EPS_CHANGE_PCT = 0.10             # EPS变化>10%视为大幅修正
MULTI_UPGRADE_MIN = 2             # 同日≥2家上调视为集中调高
MULTI_COVERAGE_MIN = 3            # 同日≥3家发报告视为集中覆盖

# 评级分级
RATING_LEVEL = {
    "买入": 5, "强烈推荐": 5, "推荐": 4, "增持": 4,
    "优于大市": 4, "强于大市": 4, "跑赢行业": 4,
    "持有": 3, "中性": 3, "谨慎推荐": 3,
    "审慎推荐": 3, "同步大市": 3, "标配": 3,
    "减持": 2, "弱于大市": 2, "回避": 1, "卖出": 1,
}


def _rating_level(rating_str: str) -> int:
    """评级字符串 → 数值等级（5=最正面）。"""
    if not rating_str:
        return 3
    for kw, lv in RATING_LEVEL.items():
        if kw in rating_str:
            return lv
    return 3


def _today_reports(today: str) -> list[dict]:
    """获取当日所有研报（最近7天窗口，取最新一天的数据）。"""
    reports = []
    for i in range(7):
        d = (date.fromisoformat(today) - timedelta(days=i)).isoformat()
        # 查询当日研报
        try:
            with store._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM research_reports WHERE report_date = ?", (d,),
                ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        except Exception:
            continue
    return []


def _historical_reports(code: str, before: str, lookback: int) -> list[dict]:
    """获取某股历史研报。"""
    since = (date.fromisoformat(before) - timedelta(days=lookback)).isoformat()
    try:
        with store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM research_reports "
                "WHERE code = ? AND report_date >= ? AND report_date < ? "
                "ORDER BY report_date DESC",
                (str(code).zfill(6), since, before),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _previous_report(code: str, institution: str, before: str) -> dict | None:
    """获取该机构对此股的上一次研报。"""
    try:
        with store._conn() as conn:
            row = conn.execute(
                "SELECT * FROM research_reports "
                "WHERE code = ? AND institution = ? AND report_date < ? "
                "ORDER BY report_date DESC LIMIT 1",
                (str(code).zfill(6), institution, before),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def detect_signals(today: str) -> list[dict]:
    """主入口：检测当日研报的信号变化。

    返回: [{code, name, signals: [...], reports: [...], total_score: int}]
    """
    reports = _today_reports(today)
    if not reports:
        return []

    # 按个股聚合
    by_code = defaultdict(list)
    for r in reports:
        code = str(r.get("code", "")).zfill(6)
        if code:
            by_code[code].append(r)

    results = []
    for code, code_reports in by_code.items():
        signal_list = []
        score = 0

        # 信号1：首次覆盖
        hist = _historical_reports(code, today, FIRST_COVERAGE_LOOKBACK)
        if len(hist) == 0:
            signal_list.append({
                "type": "first_coverage",
                "desc": f"首次覆盖（此前{FIRST_COVERAGE_LOOKBACK}天无研报）",
                "weight": 15,
            })
            score += 15

        # 信号2：多机构集中覆盖
        if len(code_reports) >= MULTI_COVERAGE_MIN:
            signal_list.append({
                "type": "multi_coverage",
                "desc": f"同日{len(code_reports)}家机构发布研报",
                "weight": 10,
            })
            score += 10

        # 信号3：多机构集中调高
        upgrades_today = sum(
            1 for r in code_reports
            if _rating_level(r.get("rating", "")) >= 4
        )
        if upgrades_today >= MULTI_UPGRADE_MIN:
            signal_list.append({
                "type": "multi_upgrade",
                "desc": f"同日{upgrades_today}家机构给予正面评级",
                "weight": 10,
            })
            score += 10

        # 逐家检查评级/EPS/目标价变化
        for r in code_reports:
            inst = r.get("institution", "")
            prev = _previous_report(code, inst, today)

            rating_now = r.get("rating", "")
            tp_now = r.get("target_price") or 0
            eps_now = r.get("eps_y1") or 0

            if not prev:
                continue

            rating_prev = prev.get("rating", "")
            tp_prev = prev.get("target_price") or 0
            eps_prev = prev.get("eps_y1") or 0

            # 信号4：评级变化
            lv_now = _rating_level(rating_now)
            lv_prev = _rating_level(rating_prev)
            if lv_now > lv_prev:
                signal_list.append({
                    "type": "rating_upgrade",
                    "desc": f"{inst}: {rating_prev}→{rating_now}（上调）",
                    "weight": 12,
                })
                score += 12
            elif lv_now < lv_prev:
                signal_list.append({
                    "type": "rating_downgrade",
                    "desc": f"{inst}: {rating_prev}→{rating_now}（下调）",
                    "weight": -8,
                })
                score -= 8

            # 信号5：目标价大幅变动
            if tp_now > 0 and tp_prev > 0:
                tp_chg = (tp_now - tp_prev) / tp_prev
                if abs(tp_chg) > TARGET_PRICE_CHANGE_PCT:
                    direction = "上调" if tp_chg > 0 else "下调"
                    signal_list.append({
                        "type": "target_price_change",
                        "desc": f"{inst}: 目标价{tp_prev}→{tp_now}（{direction}{abs(tp_chg)*100:.0f}%）",
                        "weight": 8 if tp_chg > 0 else -5,
                    })
                    score += 8 if tp_chg > 0 else -5

            # 信号6：EPS大幅修正
            if eps_now > 0 and eps_prev > 0:
                eps_chg = (eps_now - eps_prev) / eps_prev
                if abs(eps_chg) > EPS_CHANGE_PCT:
                    direction = "上调" if eps_chg > 0 else "下调"
                    signal_list.append({
                        "type": "eps_revision",
                        "desc": f"{inst}: EPS预测{eps_prev}→{eps_now}（{direction}{abs(eps_chg)*100:.0f}%）",
                        "weight": 6 if eps_chg > 0 else -4,
                    })
                    score += 6 if eps_chg > 0 else -4

        if signal_list:
            name = code_reports[0].get("name", "")
            # 只对信号得分>0的个股触发LLM（跳过纯负面信号）
            results.append({
                "code": code,
                "name": name,
                "signals": signal_list,
                "reports": code_reports,
                "signal_score": score,
                "trigger_llm": score > 0,
            })

    results.sort(key=lambda x: -x["signal_score"])
    return results
