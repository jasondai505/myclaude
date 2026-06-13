"""业绩预告信号检测 — 纯Python规则，零LLM成本。

从 earnings_forecast + earnings_express 表读取，检测：
1. 业绩暴增：净利润/营收同比 >100%
2. 业绩扭亏：上期亏损→本期盈利
3. 业绩暴雷：净利润同比 < -50% 或首亏
4. 大幅上修：预告修正向上 >20%
5. 大幅下修：预告修正向下 < -20%
"""
from __future__ import annotations

from datetime import date, timedelta
from collections import defaultdict

import store

# 业绩信号阈值
SURGE_YOY = 100        # 同比增长>100% 视为暴增
CRASH_YOY = -50        # 同比下降<-50% 视为暴雷
REVISION_UP = 20       # 修正向上>20%
REVISION_DOWN = -20    # 修正向下<-20%


def detect_earnings_signals(today: str) -> list[dict]:
    """检测当日业绩预告/快报信号。

    返回: [{code, name, signals: [...], total_score: int}]
    """
    results = []

    # 查询最近业绩预告/快报（最近7天窗口取最新一天）
    for i in range(7):
        d = (date.fromisoformat(today) - timedelta(days=i)).isoformat()
        forecasts = []
        expresses = []
        try:
            with store._conn() as conn:
                fc = conn.execute(
                    "SELECT * FROM earnings_forecast WHERE notice_date = ?", (d,),
                ).fetchall()
                ex = conn.execute(
                    "SELECT * FROM earnings_express WHERE notice_date = ?", (d,),
                ).fetchall()
            forecasts = [dict(r) for r in fc]
            expresses = [dict(r) for r in ex]
        except Exception:
            continue

        if forecasts or expresses:
            break

    if not forecasts and not expresses:
        return []

    # 按个股聚合
    by_code = defaultdict(lambda: {"forecasts": [], "expresses": []})
    for f in forecasts:
        code = str(f.get("code", "")).zfill(6)
        if code:
            by_code[code]["forecasts"].append(f)
    for e in expresses:
        code = str(e.get("code", "")).zfill(6)
        if code:
            by_code[code]["expresses"].append(e)

    for code, data in by_code.items():
        signals = []
        score = 0

        # 处理业绩预告
        for f in data["forecasts"]:
            chg_pct = f.get("change_pct") or 0
            ftype = str(f.get("forecast_type", ""))
            desc = str(f.get("change_desc", ""))
            period = str(f.get("period", ""))

            # 暴增
            if chg_pct >= SURGE_YOY:
                signals.append({
                    "type": "earnings_surge",
                    "desc": f"{period} {f.get('indicator','')} 同比+{chg_pct:.0f}%，{desc}",
                    "weight": 15,
                })
                score += 15

            # 扭亏
            elif ftype and "扭亏" in ftype:
                signals.append({
                    "type": "earnings_turnaround",
                    "desc": f"{period} 扭亏为盈，{desc}",
                    "weight": 12,
                })
                score += 12

            # 暴雷
            elif chg_pct <= CRASH_YOY or "首亏" in ftype or "续亏" in ftype:
                signals.append({
                    "type": "earnings_crash",
                    "desc": f"{period} {f.get('indicator','')} 同比{chg_pct:+.0f}%，{desc}",
                    "weight": -10,
                })
                score -= 10

            # 上修/下修
            prev = f.get("prev_value")
            val = f.get("value")
            if prev and val and prev != 0:
                rev = (val - prev) / abs(prev) * 100
                if rev >= REVISION_UP:
                    signals.append({
                        "type": "earnings_upgrade",
                        "desc": f"{period} 业绩上修 +{rev:.0f}%（{prev}→{val}）",
                        "weight": 10,
                    })
                    score += 10
                elif rev <= REVISION_DOWN:
                    signals.append({
                        "type": "earnings_downgrade",
                        "desc": f"{period} 业绩下修 {rev:.0f}%（{prev}→{val}）",
                        "weight": -8,
                    })
                    score -= 8

        # 处理业绩快报
        for e in data["expresses"]:
            profit_yoy = e.get("net_profit_yoy") or 0
            revenue_yoy = e.get("revenue_yoy") or 0
            period = str(e.get("period", ""))

            if profit_yoy >= SURGE_YOY:
                signals.append({
                    "type": "earnings_express_surge",
                    "desc": f"{period} 快报：净利+{profit_yoy:.1f}% 营收+{revenue_yoy:.1f}%",
                    "weight": 12,
                })
                score += 12
            elif profit_yoy <= CRASH_YOY:
                signals.append({
                    "type": "earnings_express_crash",
                    "desc": f"{period} 快报：净利{profit_yoy:+.1f}% 营收{revenue_yoy:+.1f}%",
                    "weight": -10,
                })
                score -= 10

        if signals:
            name = (
                data["forecasts"][0].get("name", "") if data["forecasts"]
                else data["expresses"][0].get("name", "")
            )
            results.append({
                "code": code,
                "name": name,
                "signals": signals,
                "total_score": score,
                "source": "earnings",
            })

    results.sort(key=lambda x: -x["total_score"])
    return results
