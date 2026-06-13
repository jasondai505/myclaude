"""互动易信号检测 — 纯Python规则+关键词，零LLM成本。

从 interactions 表读取，检测回复中的前瞻性信号：
1. 产品送样/测试/验证
2. 产能利用率高/接近满产
3. 新客户/订单/突破
4. 量产/小批量
5. 战略合作/联合研发
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from collections import defaultdict

import store

# 关键词 → 信号类型
SIGNAL_KEYWORDS = {
    "sampling": {
        "label": "送样/测试",
        "patterns": [
            r"送样|已送样|正在送样",
            r"测试.*通过|验证.*通过|通过.*测试|通过.*验证",
            r"客户.*测试|客户.*验证|在.*测试中|在.*验证中",
        ],
        "weight": 8,
    },
    "capacity_full": {
        "label": "产能接近满产",
        "patterns": [
            r"满产|接近满产|产能利用率.{0,5}(?:9\d|8\d)%|产能.{0,5}饱和|产能.{0,5}紧张",
            r"产能利用率.{0,5}(?:高|提升|大幅|显著)",
        ],
        "weight": 10,
    },
    "new_customer": {
        "label": "新客户/订单突破",
        "patterns": [
            r"新客户|新增客户|客户突破|首次.{0,3}客户",
            r"新订单|订单.{0,3}(?:增长|突破|大幅|显著)",
            r"突破.{0,5}(?:客户|订单|市场)",
        ],
        "weight": 8,
    },
    "mass_production": {
        "label": "量产/小批量",
        "patterns": [
            r"量产|小批量|小规模量产|进入量产|开始量产|规模量产",
            r"批量供货|批量交付|批量化",
        ],
        "weight": 7,
    },
    "strategic_coop": {
        "label": "战略合作/联合研发",
        "patterns": [
            r"战略合作|联合研发|联合开发|共同研发",
            r"签署.{0,5}(?:合作|协议|备忘录).{0,10}(?:研|产|供)",
        ],
        "weight": 6,
    },
    "supply_chain": {
        "label": "供应链地位确认",
        "patterns": [
            r"(?:核心供应商|一级供应商|主要供应商|独家供应)",
            r"进入.{0,5}(?:供应链|供应商体系)",
        ],
        "weight": 7,
    },
}


def detect_interaction_signals(today_str: str) -> list[dict]:
    """检测当日互动易回复中的前瞻性信号。

    返回: [{code, name, signals: [...], total_score: int, excerpts: [...]}]
    """
    # 查询最近互动易回复
    interactions = []
    for i in range(5):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            with store._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM interactions WHERE reply_time >= ? AND reply_time < ?",
                    (d, (date.fromisoformat(d) + timedelta(days=1)).isoformat()),
                ).fetchall()
            if rows:
                interactions = [dict(r) for r in rows]
                break
        except Exception:
            continue

    if not interactions:
        return []

    # 按个股聚合
    by_code = defaultdict(list)
    for it in interactions:
        code = str(it.get("code", "")).zfill(6)
        if code:
            by_code[code].append(it)

    results = []
    for code, code_items in by_code.items():
        signals = []
        score = 0
        excerpts = []

        for item in code_items:
            answer = str(item.get("answer", ""))
            question = str(item.get("question", ""))
            combined = f"{question} {answer}"

            for sig_key, sig_info in SIGNAL_KEYWORDS.items():
                for pat in sig_info["patterns"]:
                    m = re.search(pat, combined)
                    if m:
                        # 取匹配上下文
                        start = max(0, m.start() - 20)
                        end = min(len(combined), m.end() + 30)
                        excerpt = combined[start:end].replace("\n", " ")

                        signals.append({
                            "type": sig_key,
                            "label": sig_info["label"],
                            "desc": excerpt,
                            "weight": sig_info["weight"],
                            "question": question[:100],
                        })
                        excerpts.append(excerpt)
                        score += sig_info["weight"]
                        break  # 一条回复只触发一次同类信号

        if signals:
            # 去重（同一类型只保留一条）
            seen_types = set()
            unique_signals = []
            for s in signals:
                if s["type"] not in seen_types:
                    seen_types.add(s["type"])
                    unique_signals.append(s)

            name = code_items[0].get("name", "") or ""
            results.append({
                "code": code,
                "name": name,
                "signals": unique_signals,
                "total_score": score,
                "interaction_count": len(code_items),
                "excerpts": excerpts,
            })

    results.sort(key=lambda x: -x["total_score"])
    return results
