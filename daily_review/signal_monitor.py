"""三信号触发框架 — 自动发现值得深挖的题材，每天 ≤3 个 deep_topic。

三路信号:
  1. 卡脖子≥8 — 从 serenity_kb 查高评分卡脖子环节
  2. 走势最强板块 — 机械Δ按行业聚合排序，取 top 2
  3. 预期差异常 — shendu 高置信度预期差（#2 已部分覆盖）

用法:
  python signal_monitor.py                    # 扫描信号，输出触发列表
  python signal_monitor.py --run              # 扫描信号 + 自动跑 deep_topic
"""
from __future__ import annotations

import json, os, re, sqlite3, sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

MAX_TRIGGERS = 3


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


# ============================================================
# 信号1: 卡脖子 ≥8
# ============================================================

def check_chokepoint() -> list[dict]:
    """查 serenity_kb 中高评分卡脖子环节，筛选有实质标的映射的。"""
    try:
        from daily_review.serenity_kb import get_all_chain_summary, get_chain_snapshot
        chains = get_all_chain_summary()
    except Exception as e:
        print(f"  [卡脖子] 查询失败: {e}")
        return []

    triggers = []
    for c in chains:
        score = c.get("max_score", 0) or 0
        name = c.get("chain_name", "")
        if score < 8 or not name:
            continue
        try:
            snap = get_chain_snapshot(name)
            mapped = [s for s in snap
                      if (s.get("a_stock_mapping") or "").strip()
                      and s.get("global_chokepoint_score", 0) >= 7]
            if not mapped:
                continue
            seg_names = ", ".join(s.get("segment", "") for s in mapped[:3])
        except Exception:
            seg_names = ""
        triggers.append({
            "topic": f"{name}产业链卡脖子深度分析",
            "keywords": f"{name},{seg_names}",
            "signal_type": "卡脖子≥8",
            "priority": 1,
            "score": score,
        })

    # 取前 3 条（如果 >3 条则按名排序保持稳定）
    triggers = triggers[:3]
    if triggers:
        labels = [f"{t['topic'][:20]}({t['score']})" for t in triggers]
        print(f"  [卡脖子] {len(triggers)} 条触发: {', '.join(labels)}")
    else:
        print("  [卡脖子] 无 >=8 分且有实质标的映射的环节")
    return triggers


# ============================================================
# 信号2: 走势最强板块
# ============================================================

def check_sector_momentum() -> list[dict]:
    """机械Δ按行业板块聚合，取净动能最强的 top 2。"""
    db = BASE / "data" / "serenity.db"
    if not db.exists():
        print("  [板块] serenity.db 不存在")
        return []

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    today = _today()
    rows = conn.execute(
        "SELECT code, mech_score FROM stock_delta WHERE date=? AND mech_score != 0",
        (today,)
    ).fetchall()
    conn.close()

    if not rows:
        conn2 = sqlite3.connect(str(db))
        latest = conn2.execute(
            "SELECT MAX(date) FROM stock_delta WHERE mech_score != 0"
        ).fetchone()[0]
        conn2.close()
        if latest:
            conn3 = sqlite3.connect(str(db))
            rows = conn3.execute(
                "SELECT code, mech_score FROM stock_delta WHERE date=? AND mech_score != 0",
                (latest,)
            ).fetchall()
            conn3.close()
            print(f"  [板块] 今日无Δ，回退到 {latest}")

    if not rows:
        print("  [板块] 无机械Δ数据")
        return []

    try:
        from config import STOCK_PRIMARY_CONCEPT, CONCEPT_HIERARCHY
    except Exception:
        print("  [板块] 概念映射加载失败")
        return []

    sector_scores: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        code = r["code"]
        mech = r["mech_score"]
        concept = STOCK_PRIMARY_CONCEPT.get(code, "")
        if not concept or concept not in CONCEPT_HIERARCHY:
            continue
        sector = CONCEPT_HIERARCHY[concept]
        sector_scores[sector].append(mech)

    if not sector_scores:
        print("  [板块] 无板块映射")
        return []

    ranked = []
    for sector, scores in sector_scores.items():
        if len(scores) < 3:
            continue
        avg = sum(scores) / len(scores)
        breadth = len(scores) ** 0.5
        net = avg * breadth
        pos = sum(1 for s in scores if s > 0)
        neg = sum(1 for s in scores if s < 0)
        ranked.append({
            "sector": sector,
            "net_score": round(net, 1),
            "avg_mech": round(avg, 1),
            "count": len(scores),
            "pos": pos,
            "neg": neg,
        })

    ranked.sort(key=lambda x: -x["net_score"])
    if ranked:
        summary = [f"{r['sector']}({r['net_score']},{r['count']}只)" for r in ranked[:5]]
        print(f"  [板块] top 5: {', '.join(summary)}")

    triggers = []
    for r in ranked[:2]:
        triggers.append({
            "topic": f"{r['sector']}板块走强深度分析",
            "keywords": f"{r['sector']}",
            "signal_type": "走势最强板块",
            "priority": 3,
            "score": r["net_score"],
            "detail": f"均Δ{r['avg_mech']:.1f}, {r['count']}只({r['pos']}正{r['neg']}负)",
        })

    return triggers


# ============================================================
# 信号3: 预期差异常
# ============================================================

def check_variant_perceptions() -> list[dict]:
    """查最近 shendu 提取中的高置信度预期差（#2 已自动触发，此处做补充扫描）。"""
    shendu_dir = BASE / "reports" / "serenity" / "shendu"
    if not shendu_dir.exists():
        return []

    triggers = []
    cutoff = (date.today() - timedelta(days=3)).isoformat()

    for f in sorted(shendu_dir.glob("shendu_*.json"), reverse=True):
        date_str = f.stem.replace("shendu_", "")
        if date_str < cutoff:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            continue  # 跳过非标准格式

        vps = data.get("variant_perceptions", [])
        high_vps = [vp for vp in vps if vp.get("confidence") == "高"]
        if high_vps:
            title = data.get("title", date_str)
            thesis = data.get("thesis", "")[:100]
            triggers.append({
                "topic": f"预期差深挖: {title[:60]}",
                "keywords": ",".join(data.get("themes", [])[:5]),
                "signal_type": "预期差异常",
                "priority": 2,
                "score": len(high_vps),
                "detail": f"{len(high_vps)}条高置信预期差: {thesis}",
            })

    if triggers:
        print(f"  [预期差] {len(triggers)} 篇有高置信预期差")
    else:
        print("  [预期差] 近3天无高置信预期差")
    return triggers


# ============================================================
# 合并 & 触发
# ============================================================

def merge_triggers(all_triggers: list[dict]) -> list[dict]:
    """去重 + 优先级排序 + 上限控制。"""
    seen = set()
    merged = []
    for t in sorted(all_triggers, key=lambda x: x["priority"]):
        key = t["topic"][:40]
        if key in seen:
            continue
        seen.add(key)
        merged.append(t)
    return merged[:MAX_TRIGGERS]


def run_deep_topic(trigger: dict):
    """对单个触发信号运行 deep_topic。"""
    try:
        from daily_review.deep_topic import (
            search_db, extract_per_source, synthesize, render_report,
        )
    except ImportError:
        print(f"    deep_topic 不可用")
        return

    import data as _data
    name_map = _data._load_name_to_code_map()

    topic = trigger["topic"]
    keywords = [kw.strip() for kw in trigger["keywords"].split(",") if kw.strip()]

    print(f"\n  deep_topic: {topic[:60]}...")

    sources = search_db(keywords, days=30)
    if len(sources) < 5:
        print(f"    相关源不足({len(sources)})，跳过")
        return

    extractions = extract_per_source(sources, name_map)
    if not extractions:
        print(f"    提取失败")
        return

    all_codes = set()
    for e in extractions:
        for s in e.get("stocks", []):
            code = str(s.get("code", "")).strip()
            if re.match(r"\d{6}$", code):
                all_codes.add(code)

    quotes_text = ""
    if all_codes:
        try:
            quotes = _data.fetch_stock_quotes(sorted(all_codes), batch_size=30)
            lines = []
            for code in sorted(all_codes):
                q = quotes.get(code, {})
                chg = q.get("change_pct", 0) or 0
                lines.append(f"{code} {name_map.get(code,'')}: {chg:+.2f}%")
            quotes_text = "\n".join(lines)
        except Exception:
            pass

    synthesis = synthesize(topic, extractions, quotes_text)
    if synthesis and not synthesis.startswith("Sonnet 综合失败"):
        today = _today()
        report = render_report(topic, synthesis, extractions, today)
        print(f"    Report: {report.name}")
    else:
        print(f"    Sonnet 失败")


# ============================================================
# CLI
# ============================================================

def main():
    import argparse
    p = argparse.ArgumentParser(description="三信号扫描 + 自动 deep_topic")
    p.add_argument("--run", action="store_true", help="扫描后自动执行 deep_topic")
    args = p.parse_args()

    print(f"\n{'='*60}")
    print(f"三信号扫描 {_today()} {datetime.now().strftime('%H:%M')}")
    print(f"{'='*60}\n")

    print("[信号1] 卡脖子评分 >=8...")
    t1 = check_chokepoint()

    print("\n[信号2] 走势最强板块...")
    t2 = check_sector_momentum()

    print("\n[信号3] 预期差异常...")
    t3 = check_variant_perceptions()

    all_triggers = t1 + t2 + t3
    merged = merge_triggers(all_triggers)

    print(f"\n{'='*60}")
    print(f"触发汇总: {len(all_triggers)} 信号 -> {len(merged)} 触发 (上限 {MAX_TRIGGERS})")
    for i, t in enumerate(merged):
        extra = t.get("detail", "")
        print(f"  P{t['priority']} [{t['signal_type']}] {t['topic'][:60]}")
        if extra:
            print(f"      {extra}")
    print(f"{'='*60}")

    if args.run and merged:
        print(f"\nAuto deep_topic ({len(merged)} topics)...")
        for t in merged:
            run_deep_topic(t)
        print(f"\nDone")


if __name__ == "__main__":
    main()
