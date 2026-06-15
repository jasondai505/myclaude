"""统一评分器 — 合并 FEV(格雷厄姆轨) + G-Factor(费雪轨) 为一次 Haiku 调用。

用法:
    python unified_scorer.py --init
    python unified_scorer.py --codes 300400,688256,002050
    python unified_scorer.py --from-feeds [date]

架构:
    一次 _enrich_stocks → 一次 Haiku 调用 → F/E/V + G1/G2/G3/G4 (7维)
    → 分别写入 serenity.db(feval_scores) + gfactor.db(gfactor_scores)
    → 向后兼容，feval.py/gfactor.py 可作回退

Δ 评分保持独立（feed-based，非 per-stock），继续用 feval.score_delta_from_feeds。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

DB_PATH = BASE / "data" / "serenity.db"
GFACTOR_DB = BASE / "data" / "gfactor.db"
REVIEW_DB = BASE / "data" / "review.db"
MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 5
MAX_RETRIES = 2


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if key:
        return key
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


# ============================================================
# Prompt
# ============================================================

UNIFIED_PROMPT = """你是A股基本面+成长动能双框架分析师。对以下标的分别给出 F/E/V + G1/G2/G3/G4 评分（每项 0-10 分，整数），附一句话理由。

=== 格雷厄姆框架: F/E/V (质量价值) ===
F (护城河) 0-10: 品牌/专利/牌照/规模/网络效应/转换成本/成本优势 — 参考行业地位和概念标签
E (盈利质量) 0-10: ROE/毛利率/营收增速/现金流/盈利确定性 — 参考提供的财务数据
V (估值吸引力) 0-10: PE分位/PB分位/PEG/市值空间 (10=极度低估, 0=泡沫) — 参考PE分位

=== 费雪框架: G1/G2/G3/G4 (成长动能) ===
G1 (成长质量) 0-10: 营收增速/利润弹性/利润率趋势/产能扩张信号 — "增长有多猛且可持续？"
G2 (催化密度) 0-10: 催化剂数量×烈度 (30天) — "最近有多少事件在推动预期变化？"
G3 (叙事强度) 0-10: 专业投资者讨论频率变化 (星球7d提及) — "聪明钱在关注它吗？"
G4 (机构动量) 0-10: 首次覆盖/评级上调/EPS上修 (90d) — "机构是在追还是逃？"

数据解读提示:
  - ROE趋势: 连续上升=护城河增强, 连续下降=竞争加剧
  - 毛利率: >40%=强定价权, <15%=同质化竞争
  - 负债率: >70%=财务风险高, <30%=保守或轻资产
  - 营收增速+利润增速同向=确定性高, 背离=结构性问题
  - PE分位: <20%=低估, >80%=高估
  - 非结构化信号(产能扩张/新客户/涨价/订单/技术突破/收购)各+1分基准
  - G1: 营收增速>30%=强成长, 利润增速>营收增速=经营杠杆释放
  - G2: >=5个催化=高密度, 深研>=60分=重大催化, 0个=冷清
  - G3: 星球>=3次/7d=热, 0次=冷
  - G4: 首次覆盖=强信号, >=5篇研报=高度关注, 评级上调=加分

输出格式 (JSON):
```json
[{"code":"xxxxxx","name":"xxx",
  "F":x,"F_note":"xxx","E":x,"E_note":"xxx","V":x,"V_note":"xxx",
  "G1":x,"G1_note":"xxx","G2":x,"G2_note":"xxx",
  "G3":x,"G3_note":"xxx","G4":x,"G4_note":"xxx"}]
```

注意: 所有分必须填整数 0-10。数据不足时填 0 并标注原因。FEV=F+E+V、G_composite 不填由程序计算。"""


# ============================================================
# 数据增强 (合并 feval + gfactor 的 _enrich_stocks)
# ============================================================

def _parse_json_codes(raw: str) -> list[str]:
    """解析 mentioned_codes/stock_codes，兼容 JSON 数组和逗号分隔。"""
    if not raw or raw in ("[]", ""):
        return []
    try:
        arr = json.loads(raw)
        if isinstance(arr, list):
            return [str(c).strip().zfill(6) for c in arr
                    if str(c).strip().lstrip("0").isdigit()]
    except (json.JSONDecodeError, TypeError):
        pass
    return [c.strip().zfill(6) for c in raw.split(",")
            if c.strip().isdigit() and len(c.strip()) <= 6]


def _batch_json_contains(rows: list[sqlite3.Row], field: str, code: str) -> bool:
    for r in rows:
        codes = _parse_json_codes(r[field] or "")
        if code in codes:
            return True
    return False


def _enrich_stocks(codes: list[str]) -> list[dict]:
    """一次查询覆盖 F/E/V + G1/G2/G3/G4 全部数据源。"""
    from config import STOCK_PRIMARY_CONCEPT

    # 行情
    try:
        import data
        quotes = data.fetch_stock_quotes(codes, batch_size=30)
    except Exception as e:
        print(f"  [WARN] 行情获取失败: {e}")
        quotes = {}

    conn = sqlite3.connect(str(REVIEW_DB))
    conn.row_factory = sqlite3.Row

    thirty_d = (date.today() - timedelta(days=30)).isoformat()
    seven_d = (date.today() - timedelta(days=7)).isoformat()
    ninety_d = (date.today() - timedelta(days=90)).isoformat()

    # --- 财务指标 (E + G1) ---
    fin_map = {}
    for c in codes:
        row = conn.execute(
            "SELECT roe, gross_margin, debt_ratio, revenue_yoy, profit_yoy "
            "FROM financial_indicators WHERE code=? ORDER BY report_date DESC LIMIT 1",
            (c,)
        ).fetchone()
        if row:
            fin_map[c] = dict(row)

    # --- 估值分位 (V) ---
    val_map = {}
    for c in codes:
        try:
            row = conn.execute(
                "SELECT data_json FROM valuation_cache WHERE code=? AND data_type='percentile' LIMIT 1",
                (c,)
            ).fetchone()
            if row:
                data = json.loads(row["data_json"])
                val_map[c] = {
                    "pe_pct_5y": data.get("pe_pct_5y") if isinstance(data, dict) else None,
                }
        except Exception:
            val_map[c] = {}

    # --- 深研信号 30d (F/E/V + G2) ---
    deep_map = {}
    for c in codes:
        row = conn.execute(
            "SELECT MAX(total_score) as ms, COUNT(*) as cnt FROM deep_read_results "
            "WHERE code=? AND date >= ?", (c, thirty_d)
        ).fetchone()
        deep_map[c] = {"max_score": row["ms"] or 0, "count": row["cnt"]}

    # --- 催化信号 30d (G2) + 非结构化信号 (G1) ---
    cat_30d = conn.execute(
        "SELECT mentioned_codes, catalyst_name, actionability, date FROM catalyst_signals "
        "WHERE date >= ? AND mentioned_codes IS NOT NULL AND mentioned_codes != '[]'",
        (thirty_d,)
    ).fetchall()

    signal_keywords = [
        ("产能扩张", ["产能", "扩产", "投产", "新建", "募投", "达产"]),
        ("新客户导入", ["新客户", "导入", "认证", "通过验证", "送样"]),
        ("产品涨价", ["涨价", "提价", "上调价格", "价格上调"]),
        ("订单饱满", ["订单", "合同", "中标", "签约", "协议"]),
        ("技术突破", ["突破", "量产", "首发", "领先", "自主研发"]),
        ("收购重组", ["收购", "重组", "并购", "资产注入"]),
    ]

    catalyst_map = {}
    nonstruct_map: dict[str, list[str]] = {}
    for c in codes:
        matched = [(r["actionability"] or 0, r["catalyst_name"] or "")
                    for r in cat_30d if _batch_json_contains([r], "mentioned_codes", c)]
        catalyst_map[c] = {
            "count": len(matched),
            "max_score": max([m[0] for m in matched]) if matched else 0,
        }
        signals = []
        for _, cat_name in matched:
            for sig_type, keywords in signal_keywords:
                if any(kw in cat_name for kw in keywords) and sig_type not in signals:
                    signals.append(sig_type)
        nonstruct_map[c] = signals

    # G1 非结构化信号补充 (deep_read)
    for c in codes:
        dr_rows = conn.execute(
            "SELECT ann_title, total_score FROM deep_read_results "
            "WHERE code=? AND date >= ? AND total_score >= 40", (c, thirty_d)
        ).fetchall()
        for r in dr_rows:
            title = r["ann_title"] or ""
            for sig_type, keywords in signal_keywords:
                if any(kw in title for kw in keywords) and sig_type not in nonstruct_map[c]:
                    nonstruct_map[c].append(sig_type)

    # --- 星球提及 7d (G3) ---
    zsxq_7d = conn.execute(
        "SELECT stock_codes FROM zsxq_topics "
        "WHERE create_time >= ? AND stock_codes IS NOT NULL AND stock_codes != '[]' AND stock_codes != ''",
        (seven_d,)
    ).fetchall()
    zsxq_map = {}
    for c in codes:
        zsxq_map[c] = sum(1 for r in zsxq_7d if _batch_json_contains([r], "stock_codes", c))

    # --- 研报覆盖 + 评级变化 90d (G4) ---
    research_map = {}
    for c in codes:
        rows = conn.execute(
            "SELECT rating, report_date FROM research_reports "
            "WHERE code=? AND report_date >= ? ORDER BY report_date DESC", (c, ninety_d)
        ).fetchall()
        count = len(rows)
        latest_rating = rows[0]["rating"] if rows else ""
        older = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_reports "
            "WHERE code=? AND report_date < ?", (c, ninety_d)
        ).fetchone()
        is_first = older["cnt"] == 0 and count > 0
        research_map[c] = {
            "count": count, "latest_rating": latest_rating,
            "is_first_coverage": is_first,
        }

    conn.close()

    # --- 组装 ---
    stocks = []
    for code in codes:
        q = quotes.get(code, {})
        fin = fin_map.get(code, {})
        val = val_map.get(code, {})
        cat = catalyst_map.get(code, {})
        dp = deep_map.get(code, {})
        res = research_map.get(code, {})

        s = {
            "code": code,
            "name": q.get("name", ""),
            # 行情
            "mcap_yi": round(q.get("mcap_yi", 0) or 0),
            "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
            "chg_pct": round(q.get("change_pct", 0) or 0, 2),
            # 财务 (E + G1)
            "roe": fin.get("roe"),
            "gross_margin": fin.get("gross_margin"),
            "debit_ratio": fin.get("debt_ratio"),
            "revenue_yoy": fin.get("revenue_yoy"),
            "profit_yoy": fin.get("profit_yoy"),
            # 行业 (F + G1)
            "industry": STOCK_PRIMARY_CONCEPT.get(code, ""),
            # 估值 (V)
            "pe_pct_5y": val.get("pe_pct_5y"),
            # 深研 (F/E/V + G2)
            "deep_read_max": dp.get("max_score", 0),
            "deep_read_count": dp.get("count", 0),
            # 催化 (G2)
            "catalyst_count": cat.get("count", 0),
            "catalyst_max": cat.get("max_score", 0),
            # 非结构化信号 (G1)
            "nonstruct_signals": nonstruct_map.get(code, []),
            # 星球 (G3)
            "zsxq_mentions_7d": zsxq_map.get(code, 0),
            # 机构 (G4)
            "research_count_90d": res.get("count", 0),
            "latest_rating": res.get("latest_rating", ""),
            "is_first_coverage": res.get("is_first_coverage", False),
        }
        stocks.append(s)
    return stocks


# ============================================================
# Prompt 构建
# ============================================================

def _build_batch_prompt(stocks: list[dict]) -> str:
    lines = ["标的列表 (含多维数据):\n"]
    for s in stocks:
        parts = [f"- {s['code']} {s.get('name','?')}"]

        # 基本面
        roe = s.get("roe")
        gm = s.get("gross_margin")
        dr = s.get("debit_ratio")
        ry = s.get("revenue_yoy")
        py = s.get("profit_yoy")
        fin_parts = []
        if ry is not None:
            if ry: fin_parts.append(f"营收YoY:{ry:+.1f}%")
            if py: fin_parts.append(f"净利YoY:{py:+.1f}%")
            if gm: fin_parts.append(f"毛利率:{gm:.1f}%")
            if roe: fin_parts.append(f"ROE:{roe:.1f}%")
            if dr: fin_parts.append(f"负债率:{dr:.1f}%")
        if fin_parts:
            parts.append("财务: " + " ".join(fin_parts))

        # 行业 + 市值
        ind = s.get("industry", "")
        mcap = s.get("mcap_yi", 0)
        if ind or mcap:
            parts.append(f"{ind} 市值{mcap}亿")

        # 估值
        pe = s.get("pe_ttm")
        pe_pct = s.get("pe_pct_5y")
        if pe and pe > 0:
            val_str = f"PE:{pe:.1f}"
            if pe_pct is not None:
                val_str += f" PE分位:{pe_pct:.0f}%"
            parts.append(val_str)

        # 信号 (F/E/V + G2)
        ds = s.get("deep_read_max", 0)
        rc = s.get("research_count_90d", 0)
        cc = s.get("catalyst_count", 0)
        sig_items = []
        if ds >= 50: sig_items.append(f"深研{ds}分")
        if rc > 0: sig_items.append(f"研报{rc}篇")
        if cc > 0: sig_items.append(f"催化{cc}个")
        if sig_items:
            parts.append("信号: " + " ".join(sig_items))

        # G1 非结构化信号
        ns = s.get("nonstruct_signals", [])
        if ns:
            parts.append(f"事件: {', '.join(ns)}")

        # G3 叙事
        zs = s.get("zsxq_mentions_7d", 0)
        parts.append(f"星球{zs}次/7d")

        # G4 机构
        fc = s.get("is_first_coverage", False)
        lr = s.get("latest_rating", "")
        g4_str = f"研报{rc}篇/90d"
        if fc: g4_str += " 首次覆盖!"
        if lr: g4_str += f" 最新{lr}"
        parts.append(g4_str)

        lines.append(" | ".join(parts))
    return "\n".join(lines)


# ============================================================
# 解析 + 评分
# ============================================================

def _parse_response(text: str, stocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """解析统一 JSON → (fev_results, gfactor_results)。"""
    json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\[.*?\])", text, re.DOTALL)
    if not json_match:
        print(f"  [WARN] unified: 无法从响应中提取 JSON，原文前200字: {text[:200]}")
        return [], []

    try:
        raw = json.loads(json_match.group(1))
    except json.JSONDecodeError as e:
        print(f"  [WARN] unified: JSON 解析失败: {e}")
        return [], []

    code_map = {s["code"]: s["name"] for s in stocks}
    fev_results = []
    gfactor_results = []
    today = _today()

    for item in raw:
        code = str(item.get("code", "")).strip()
        if code not in code_map:
            continue

        f_s = max(0, min(10, int(item.get("F", 0) or 0)))
        e_s = max(0, min(10, int(item.get("E", 0) or 0)))
        v_s = max(0, min(10, int(item.get("V", 0) or 0)))
        g1 = max(0, min(10, int(item.get("G1", 0) or 0)))
        g2 = max(0, min(10, int(item.get("G2", 0) or 0)))
        g3 = max(0, min(10, int(item.get("G3", 0) or 0)))
        g4 = max(0, min(10, int(item.get("G4", 0) or 0)))

        fev_results.append({
            "code": code, "name": code_map[code], "date": today,
            "f_score": f_s, "e_score": e_s, "v_score": v_s,
            "fev_total": f_s + e_s + v_s,
            "f_note": str(item.get("F_note", "") or "")[:120],
            "e_note": str(item.get("E_note", "") or "")[:120],
            "v_note": str(item.get("V_note", "") or "")[:120],
            "source": "unified",
        })
        gfactor_results.append({
            "code": code, "name": code_map[code], "date": today,
            "g1_score": g1, "g1_note": str(item.get("G1_note", "") or "")[:150],
            "g2_score": g2, "g2_note": str(item.get("G2_note", "") or "")[:150],
            "g3_score": g3, "g3_note": str(item.get("G3_note", "") or "")[:150],
            "g4_score": g4, "g4_note": str(item.get("G4_note", "") or "")[:150],
            "source": "unified",
        })

    return fev_results, gfactor_results


def _score_batch_internal(stocks: list[dict]) -> tuple[list[dict], list[dict]]:
    api_key = _load_api_key()
    if not api_key:
        print("  [WARN] unified: 无 API key，跳过评分")
        return [], []

    from daily_review.roles import get_client as _rc
    client = _rc("synthesis", timeout=120)

    all_fev = []
    all_gf = []

    for i in range(0, len(stocks), BATCH_SIZE):
        batch = stocks[i:i + BATCH_SIZE]
        prompt = UNIFIED_PROMPT + "\n" + _build_batch_prompt(batch)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = client.messages.create(
                    model=MODEL, max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}],
                    thinking={"type": "disabled"}, timeout=60,
                )
                parts = []
                for block in resp.content:
                    if hasattr(block, "text") and block.text:
                        parts.append(block.text)
                text = "\n".join(parts)

                fev, gf = _parse_response(text, batch)
                if fev and gf:
                    all_fev.extend(fev)
                    all_gf.extend(gf)
                    codes_str = ",".join(s["code"] for s in fev)
                    fevs = "/".join(str(s["fev_total"]) for s in fev)
                    g1s = "/".join(f"G1={s['g1_score']}" for s in gf)
                    print(f"  unified batch {i//BATCH_SIZE+1}: {codes_str} -> FEV {fevs} | {g1s}")
                else:
                    print(f"  unified batch {i//BATCH_SIZE+1}: parse failed, retry {attempt}")
                    if attempt < MAX_RETRIES:
                        continue
                break
            except Exception as e:
                print(f"  unified batch {i//BATCH_SIZE+1}: API error: {e}")
                if attempt < MAX_RETRIES:
                    continue

    return all_fev, all_gf


# ============================================================
# 保存 (复用 feval + gfactor 的 save 函数)
# ============================================================

def _save_fev(scores: list[dict]):
    if not scores:
        return
    from daily_review.feval import save_scores
    save_scores(scores)


def _save_gfactor(scores: list[dict]):
    if not scores:
        return
    from daily_review.gfactor import save_scores as gf_save
    gf_save(scores)


# ============================================================
# Public API
# ============================================================

def score_codes(codes: list[str]) -> tuple[list[dict], list[dict]]:
    """统一评分：一次数据增强 + 一次 LLM → FEV + G-Factor。"""
    print(f"  unified: {len(codes)} 只，数据增强中...")
    stocks = _enrich_stocks(codes)
    fev, gf = _score_batch_internal(stocks)
    if fev:
        _save_fev(fev)
    if gf:
        _save_gfactor(gf)
    print(f"  unified: FEV {len(fev)}只 + G-Factor {len(gf)}只 已保存")
    return fev, gf


def score_from_feeds(date_str: str = "") -> tuple[list[dict], list[dict]]:
    """从当天 feeds 提取代码 → 统一评分 → 双库保存。"""
    d = date_str or _today()
    feeds_dir = BASE / "reports" / "feeds"

    codes_found: set[str] = set()
    for pattern in ["zsxq_*.md", "news_*.md", "announcements_*.md", "industry_*.md"]:
        for f in sorted(feeds_dir.glob(pattern)):
            if d in f.name:
                try:
                    text = f.read_text(encoding="utf-8")
                    codes_found.update(re.findall(r"\b(\d{6})\b", text))
                except Exception:
                    pass

    for stem in ["recap", "review_summary"]:
        path = feeds_dir / f"{stem}_{d}.md"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                codes_found.update(re.findall(r"\b(\d{6})\b", text))
            except Exception:
                pass

    # 也加入 wechat_analysis
    wechat_path = BASE / "reports" / "wechat" / f"wechat_analysis_{d}.md"
    if wechat_path.exists():
        try:
            text = wechat_path.read_text(encoding="utf-8")
            codes_found.update(re.findall(r"\b(\d{6})\b", text))
        except Exception:
            pass

    if not codes_found:
        print("  unified: 未从 feeds 中提取到代码")
        return [], []

    # 跳过已评分的
    existing_fev = set()
    try:
        from daily_review.feval import get_scores as fev_get
        existing_fev = set(fev_get(date_str=d).keys())
    except Exception:
        pass
    existing_gf = set()
    try:
        gdb = GFACTOR_DB
        if gdb.exists():
            conn_g = sqlite3.connect(str(gdb))
            conn_g.row_factory = sqlite3.Row
            for r in conn_g.execute("SELECT code FROM gfactor_scores WHERE date=?", (d,)):
                existing_gf.add(r["code"])
            conn_g.close()
    except Exception:
        pass

    already = existing_fev & existing_gf
    new_codes = sorted(codes_found - already)
    if not new_codes:
        print(f"  unified: {len(codes_found)} 个代码均已评分，跳过")
        return [], []

    # 只评新的，但也把已有 FEV 的标的补 G-Factor（反之亦然）
    fev_only = existing_fev - existing_gf
    gf_only = existing_gf - existing_fev
    to_score = sorted(set(new_codes) | fev_only | gf_only)

    print(f"  unified: feeds 共 {len(codes_found)} 代码，{len(to_score)} 待评分"
          f"（新{len(new_codes)} + FEV独有{len(fev_only)} + GF独有{len(gf_only)}）")

    return score_codes(to_score)


def init_db():
    from daily_review.feval import init_db as fev_init
    from daily_review.gfactor import init_db as gf_init
    fev_init()
    gf_init()
    print("unified: feval + gfactor 双库初始化完成")


# ============================================================
# CLI
# ============================================================

def _main():
    import argparse
    p = argparse.ArgumentParser(description="统一评分器 — FEV + G-Factor 合并")
    p.add_argument("--init", action="store_true", help="初始化双库")
    p.add_argument("--codes", type=str, help="逗号分隔代码列表")
    p.add_argument("--from-feeds", type=str, nargs="?", const=_today(),
                   help="从当天 feeds 提取代码并评分")
    args = p.parse_args()

    if args.init:
        init_db()
        return

    init_db()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        fev, gf = score_codes(codes)
        if fev:
            print(f"\nFEV ({len(fev)}只):")
            for s in sorted(fev, key=lambda x: -x["fev_total"]):
                print(f"  {s['code']} {s['name']}: F={s['f_score']} E={s['e_score']} "
                      f"V={s['v_score']} FEV={s['fev_total']}")
        if gf:
            print(f"\nG-Factor ({len(gf)}只):")
            for s in sorted(gf, key=lambda x: -(x["g1_score"]*2 + x["g2_score"]*2 + x["g3_score"] + x["g4_score"])):
                g_comp = s["g1_score"]*2 + s["g2_score"]*2 + s["g3_score"] + s["g4_score"]
                print(f"  {s['code']} {s['name']}: G1={s['g1_score']} G2={s['g2_score']} "
                      f"G3={s['g3_score']} G4={s['g4_score']} G={g_comp}")

    elif args.from_feeds:
        score_from_feeds(args.from_feeds)

    else:
        p.print_help()


if __name__ == "__main__":
    _main()
