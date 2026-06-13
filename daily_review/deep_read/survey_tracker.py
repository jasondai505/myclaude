"""机构调研信号检测 — 纯Python规则，零LLM成本。

从 inst_survey 表读取，检测：
1. 密集调研：同日 ≥5 家机构
2. 首次调研：此前90天无调研
3. 调研升温：近30天机构数 > 前30天 × 1.5
4. 连续调研：近90天 ≥3 次调研
"""
from __future__ import annotations

from datetime import date, timedelta
from collections import defaultdict

import store

SURGE_THRESHOLD = 5          # 同日≥5家机构视为密集
FIRST_LOOKBACK = 90          # 首次调研回溯
HEATING_MULTIPLIER = 1.5     # 升温倍数
CONSECUTIVE_MIN = 3          # 连续调研最低次数


def detect_survey_signals(today: str) -> list[dict]:
    """检测当日调研信号。

    返回: [{code, name, signals: [...], total_score: int}]
    """
    # 查最近调研（最近7天窗口取最新一天）
    surveys_today = []
    for i in range(7):
        d = (date.fromisoformat(today) - timedelta(days=i)).isoformat()
        try:
            with store._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM inst_survey WHERE notice_date = ?", (d,),
                ).fetchall()
            if rows:
                surveys_today = [dict(r) for r in rows]
                break
        except Exception:
            continue

    if not surveys_today:
        return []

    # 按个股聚合今日调研
    by_code = defaultdict(list)
    for s in surveys_today:
        code = str(s.get("code", "")).zfill(6)
        if code:
            by_code[code].append(s)

    results = []
    for code, code_surveys in by_code.items():
        signals = []
        score = 0
        inst_today = max(s.get("inst_count", 0) or 0 for s in code_surveys)

        # 信号1：密集调研
        if inst_today >= SURGE_THRESHOLD:
            signals.append({
                "type": "survey_surge",
                "desc": f"同日{inst_today}家机构密集调研",
                "weight": 10,
            })
            score += 10

        # 信号2：首次调研
        try:
            with store._conn() as conn:
                hist_count = conn.execute(
                    "SELECT COUNT(*) FROM inst_survey WHERE code = ? AND notice_date < ? AND notice_date >= ?",
                    (str(code).zfill(6), today,
                     (date.fromisoformat(today) - timedelta(days=FIRST_LOOKBACK)).isoformat()),
                ).fetchone()[0]
            if hist_count == 0:
                signals.append({
                    "type": "first_survey",
                    "desc": f"首次调研（此前{FIRST_LOOKBACK}天无调研）",
                    "weight": 12,
                })
                score += 12
        except Exception:
            pass

        # 信号3：调研升温
        try:
            with store._conn() as conn:
                recent = conn.execute(
                    "SELECT SUM(inst_count) as total FROM inst_survey "
                    "WHERE code = ? AND notice_date >= ? AND notice_date < ?",
                    (str(code).zfill(6),
                     (date.fromisoformat(today) - timedelta(days=30)).isoformat(), today),
                ).fetchone()
                prior = conn.execute(
                    "SELECT SUM(inst_count) as total FROM inst_survey "
                    "WHERE code = ? AND notice_date >= ? AND notice_date < ?",
                    (str(code).zfill(6),
                     (date.fromisoformat(today) - timedelta(days=60)).isoformat(),
                     (date.fromisoformat(today) - timedelta(days=30)).isoformat()),
                ).fetchone()
            r_total = (recent["total"] or 0) if recent else 0
            p_total = (prior["total"] or 0) if prior else 0
            if p_total > 0 and r_total > p_total * HEATING_MULTIPLIER:
                signals.append({
                    "type": "survey_heating",
                    "desc": f"调研升温：近30天{r_total}家 vs 前30天{p_total}家",
                    "weight": 8,
                })
                score += 8
        except Exception:
            pass

        # 信号4：连续调研
        try:
            with store._conn() as conn:
                con_count = conn.execute(
                    "SELECT COUNT(DISTINCT notice_date) FROM inst_survey "
                    "WHERE code = ? AND notice_date >= ?",
                    (str(code).zfill(6),
                     (date.fromisoformat(today) - timedelta(days=90)).isoformat()),
                ).fetchone()[0]
            if con_count >= CONSECUTIVE_MIN:
                signals.append({
                    "type": "consecutive_survey",
                    "desc": f"连续调研：近90天{con_count}次",
                    "weight": 6,
                })
                score += 6
        except Exception:
            pass

        if signals:
            name = code_surveys[0].get("name", "")
            results.append({
                "code": code,
                "name": name,
                "signals": signals,
                "survey_count": len(code_surveys),
                "inst_today": inst_today,
                "total_score": score,
                "source_data": code_surveys,
            })

    results.sort(key=lambda x: -x["total_score"])
    return results
