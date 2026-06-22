"""G-Factor 成长动能评分器 — 费雪轨，和 FEV 对称但不合成。

用法:
    python gfactor.py --codes 688256,300308,002050           # 指定标的
    python gfactor.py --from-pool                             # 优先池(有财务+催化)
    python gfactor.py --init                                  # 建表

架构:
    G1 成长质量: 财务增速 + 非结构化信号(产能/订单/涨价/新客户)
    G2 催化密度: 催化剂数量 × 烈度 (30天)
    G3 叙事强度: 星球/公众号/微博讨论密度变化 (7天)
    G4 机构动量: 首次覆盖 + 评级上调 + EPS上修 (90天)

四个维度独立输出，不合成总分。
"""
from __future__ import annotations

import json, sqlite3, sys, re, time
from pathlib import Path
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

DB_PATH = BASE / "data" / "gfactor.db"
REVIEW_DB = BASE / "data" / "review.db"
MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 5
MAX_RETRIES = 2


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


from daily_review.llm import _load_api_key


# ============================================================
# SQLite
# ============================================================

def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c

def _review_conn():
    c = sqlite3.connect(str(REVIEW_DB))
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gfactor_scores (
                code        TEXT NOT NULL,
                name        TEXT,
                date        TEXT NOT NULL,
                g1_score    INTEGER NOT NULL DEFAULT 0,
                g1_note     TEXT,
                g2_score    INTEGER NOT NULL DEFAULT 0,
                g2_note     TEXT,
                g3_score    INTEGER NOT NULL DEFAULT 0,
                g3_note     TEXT,
                g4_score    INTEGER NOT NULL DEFAULT 0,
                g4_note     TEXT,
                source      TEXT DEFAULT 'gfactor',
                PRIMARY KEY (code, date)
            );
            CREATE INDEX IF NOT EXISTS idx_gf_code ON gfactor_scores(code);
            CREATE INDEX IF NOT EXISTS idx_gf_date ON gfactor_scores(date);
        """)
    print("gfactor 表初始化完成")


def save_scores(scores: list[dict]):
    if not scores: return
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO gfactor_scores "
            "(code, name, date, g1_score, g1_note, g2_score, g2_note, "
            " g3_score, g3_note, g4_score, g4_note, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(s["code"], s["name"], s["date"],
              s["g1_score"], s["g1_note"], s["g2_score"], s["g2_note"],
              s["g3_score"], s["g3_note"], s["g4_score"], s["g4_note"],
              s.get("source", "gfactor")) for s in scores]
        )
    print(f"  gfactor: 已保存 {len(scores)} 个评分")


# ============================================================
# LLM Prompt
# ============================================================

GFACTOR_PROMPT = """你是A股成长股分析师。对以下标的分别给出 G1/G2/G3/G4 评分（每项 0-10 分，整数），附一句话理由。

评分标准:
  G1 (成长质量) 0-10: 营收增速/利润弹性/利润率趋势/产能扩张信号 — "这家公司增长有多猛且可持续？"
  G2 (催化密度) 0-10: 催化剂数量×烈度 — "最近有多少事件在推动预期变化？"
  G3 (叙事强度) 0-10: 专业投资者讨论频率变化 — "聪明钱在关注它吗？关注度在上升还是下降？"
  G4 (机构动量) 0-10: 首次覆盖/评级上调/EPS上修 — "机构是在追还是逃？"

数据解读提示:
  - G1: 营收增速>30%=强成长, 利润增速>营收增速=经营杠杆释放, 毛利率连续上升=产品升级
  - G1 非结构化信号: "产能扩张/新客户导入/产品涨价/订单饱满" 各+1分基准
  - G2: >=5个催化=高密度, 深研>=60分=重大催化, 0个=冷清
  - G3: 星球>=3次/7d=热, 0次=冷, 公众号+微博是辅助
  - G4: 首次覆盖=强信号, >=5篇研报=机构高度关注, 评级上调=加分, 0篇=机构忽视

输出格式 (JSON):
```json
[{"code":"xxxxxx","name":"xxx","G1":x,"G1_note":"xxx","G2":x,"G2_note":"xxx","G3":x,"G3_note":"xxx","G4":x,"G4_note":"xxx"}]
```

注意: G1/G2/G3/G4 必须填整数 0-10。数据不足时填 0 并标注原因。"""


# ============================================================
# 数据增强
# ============================================================

def _parse_json_codes(raw: str) -> list[str]:
    """解析 mentioned_codes/stock_codes，兼容 JSON 数组和逗号分隔两种格式。"""
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
    """检查 code 是否出现在任一行 JSON 数组字段中（一次 DB 查询，Python 侧过滤）。"""
    for r in rows:
        codes = _parse_json_codes(r[field] or "")
        if code in codes:
            return True
    return False


def _enrich_stocks(codes: list[str]) -> list[dict]:
    """多维数据增强 — 财务+信号+催化+机构。"""
    from config import STOCK_PRIMARY_CONCEPT

    # 行情
    try:
        import data
        quotes = data.fetch_stock_quotes(codes, batch_size=30)
    except Exception as e:
        print(f"  [WARN] 行情获取失败: {e}")
        quotes = {}

    conn = _review_conn()
    thirty_d = (date.today() - timedelta(days=30)).isoformat()
    seven_d = (date.today() - timedelta(days=7)).isoformat()
    ninety_d = (date.today() - timedelta(days=90)).isoformat()

    # G1 财务 — 每个 code 取最新一期报告
    fin_map = {}
    for c in codes:
        row = conn.execute(
            "SELECT roe, gross_margin, debt_ratio, revenue_yoy, profit_yoy "
            "FROM financial_indicators WHERE code=? ORDER BY report_date DESC LIMIT 1", (c,)
        ).fetchone()
        if row: fin_map[c] = dict(row)

    # G2 催化 + G1 非结构化信号 — 批量拉 JSON 数组，Python 匹配
    cat_30d = conn.execute(
        "SELECT mentioned_codes, catalyst_name, actionability, date FROM catalyst_signals "
        "WHERE date >= ? AND mentioned_codes IS NOT NULL AND mentioned_codes != '[]'",
        (thirty_d,)
    ).fetchall()

    nonstruct_map: dict[str, list[str]] = {}
    catalyst_map: dict[str, dict] = {}
    signal_keywords = [
        ("产能扩张", ["产能", "扩产", "投产", "新建", "募投", "达产"]),
        ("新客户导入", ["新客户", "导入", "认证", "通过验证", "送样"]),
        ("产品涨价", ["涨价", "提价", "上调价格", "价格上调"]),
        ("订单饱满", ["订单", "合同", "中标", "签约", "协议"]),
        ("技术突破", ["突破", "量产", "首发", "领先", "自主研发"]),
        ("收购重组", ["收购", "重组", "并购", "资产注入"]),
    ]
    for c in codes:
        signals = []
        matched = [(r["actionability"] or 0, r["catalyst_name"] or "")
                   for r in cat_30d if _batch_json_contains([r], "mentioned_codes", c)]
        catalyst_map[c] = {
            "count": len(matched),
            "max_score": max([m[0] for m in matched]) if matched else 0,
        }
        for _, cat_name in matched:
            for sig_type, keywords in signal_keywords:
                if any(kw in cat_name for kw in keywords) and sig_type not in signals:
                    signals.append(sig_type)
        nonstruct_map[c] = signals

    # G1 非结构化信号 (deep_read 补充)
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

    # G2 深研信号
    deep_map = {}
    for c in codes:
        row = conn.execute(
            "SELECT MAX(total_score) as ms, COUNT(*) as cnt FROM deep_read_results "
            "WHERE code=? AND date >= ?", (c, thirty_d)
        ).fetchone()
        deep_map[c] = {"max_score": row["ms"] or 0, "count": row["cnt"]}

    # G3 星球提及 — 批量拉 JSON 数组，Python 匹配
    zsxq_7d = conn.execute(
        "SELECT stock_codes FROM zsxq_topics "
        "WHERE create_time >= ? AND stock_codes IS NOT NULL AND stock_codes != '[]' AND stock_codes != ''",
        (seven_d,)
    ).fetchall()
    zsxq_map = {}
    for c in codes:
        zsxq_map[c] = sum(1 for r in zsxq_7d if _batch_json_contains([r], "stock_codes", c))

    # G3 微博 — 表为空，直接置 0
    weibo_map = {c: 0 for c in codes}

    # G4 研报覆盖 + 评级变化
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
            "is_first_coverage": is_first
        }

    conn.close()

    # 组装
    stocks = []
    for code in codes:
        q = quotes.get(code, {})
        fin = fin_map.get(code, {})
        cat = catalyst_map.get(code, {})
        dp = deep_map.get(code, {})
        res = research_map.get(code, {})

        s = {
            "code": code,
            "name": q.get("name", ""),
            # G1
            "roe": fin.get("roe"),
            "gross_margin": fin.get("gross_margin"),
            "debit_ratio": fin.get("debt_ratio"),
            "revenue_yoy": fin.get("revenue_yoy"),
            "profit_yoy": fin.get("profit_yoy"),
            "nonstruct_signals": nonstruct_map.get(code, []),
            "industry": STOCK_PRIMARY_CONCEPT.get(code, ""),
            "mcap_yi": round(q.get("mcap_yi", 0) or 0),
            # G2
            "catalyst_count": cat.get("count", 0),
            "catalyst_max": cat.get("max_score", 0),
            "deep_read_max": dp.get("max_score", 0),
            "deep_read_count": dp.get("count", 0),
            # G3
            "zsxq_mentions_7d": zsxq_map.get(code, 0),
            "weibo_mentions_7d": weibo_map.get(code, 0),
            # G4
            "research_count_90d": res.get("count", 0),
            "latest_rating": res.get("latest_rating", ""),
            "is_first_coverage": res.get("is_first_coverage", False),
            # 行情
            "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
            "chg_pct": round(q.get("change_pct", 0) or 0, 2),
        }
        stocks.append(s)
    return stocks


# ============================================================
# Prompt 构建
# ============================================================

def _build_batch_prompt(stocks: list[dict]) -> str:
    lines = ["标的列表 (含成长多维数据):\n"]
    for s in stocks:
        parts = [f"- {s['code']} {s.get('name','?')}"]

        # G1 财务
        roe = s.get("roe")
        gm = s.get("gross_margin")
        ry = s.get("revenue_yoy")
        py = s.get("profit_yoy")
        if ry is not None:
            fin_parts = []
            if ry: fin_parts.append(f"营收YoY:{ry:+.1f}%")
            if py: fin_parts.append(f"净利YoY:{py:+.1f}%")
            if gm: fin_parts.append(f"毛利率:{gm:.1f}%")
            if roe: fin_parts.append(f"ROE:{roe:.1f}%")
            parts.append("财务: " + " ".join(fin_parts))

        # G1 非结构化信号
        ns = s.get("nonstruct_signals", [])
        if ns:
            parts.append(f"信号: {', '.join(ns)}")

        # 行业+市值
        ind = s.get("industry", "")
        mcap = s.get("mcap_yi", 0)
        if ind or mcap:
            parts.append(f"{ind} 市值{mcap}亿")

        # G2 催化
        cc = s.get("catalyst_count", 0)
        dp_max = s.get("deep_read_max", 0)
        g2_parts = [f"催化{cc}个"]
        if dp_max >= 40: g2_parts.append(f"深研{dp_max}分")
        parts.append("G2: " + " ".join(g2_parts))

        # G3 叙事
        zs = s.get("zsxq_mentions_7d", 0)
        wb = s.get("weibo_mentions_7d", 0)
        g3_str = f"星球{zs}次/7d" if zs else "星球0次"
        if wb: g3_str += f" 微博{wb}次"
        parts.append(g3_str)

        # G4 机构
        rc = s.get("research_count_90d", 0)
        fc = s.get("is_first_coverage", False)
        lr = s.get("latest_rating", "")
        g4_str = f"研报{rc}篇/90d"
        if fc: g4_str += " 首次覆盖!"
        if lr: g4_str += f" 最新{lr}"
        parts.append(g4_str)

        lines.append(" | ".join(parts))
    return "\n".join(lines)


# ============================================================
# LLM 评分
# ============================================================

def _parse_response(text: str, stocks: list[dict]) -> list[dict]:
    json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\[.*?\])", text, re.DOTALL)
    if not json_match:
        print(f"  [WARN] gfactor: 无法从响应中提取 JSON")
        return []
    try:
        raw = json.loads(json_match.group(1))
    except json.JSONDecodeError as e:
        print(f"  [WARN] gfactor: JSON 解析失败: {e}")
        return []

    code_map = {s["code"]: s["name"] for s in stocks}
    results = []
    for item in raw:
        code = str(item.get("code", "")).strip()
        if code not in code_map: continue
        results.append({
            "code": code, "name": code_map[code], "date": _today(),
            "g1_score": max(0, min(10, int(item.get("G1", 0) or 0))),
            "g1_note": str(item.get("G1_note", "") or "")[:150],
            "g2_score": max(0, min(10, int(item.get("G2", 0) or 0))),
            "g2_note": str(item.get("G2_note", "") or "")[:150],
            "g3_score": max(0, min(10, int(item.get("G3", 0) or 0))),
            "g3_note": str(item.get("G3_note", "") or "")[:150],
            "g4_score": max(0, min(10, int(item.get("G4", 0) or 0))),
            "g4_note": str(item.get("G4_note", "") or "")[:150],
            "source": "gfactor",
        })
    return results


def score_batch(stocks: list[dict]) -> list[dict]:
    api_key = _load_api_key()
    if not api_key:
        print("  [WARN] gfactor: 无 API key")
        return []

    from daily_review.roles import get_client as _rc, get_model as _rm
    client = _rc("synthesis", timeout=120)

    all_results = []
    for i in range(0, len(stocks), BATCH_SIZE):
        batch = stocks[i:i + BATCH_SIZE]
        prompt = GFACTOR_PROMPT + "\n" + _build_batch_prompt(batch)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = client.messages.create(
                    model=MODEL, max_tokens=1500,
                    messages=[{"role": "user", "content": prompt}],
                    thinking={"type": "disabled"}, timeout=60,
                )
                parts = []
                for block in resp.content:
                    if hasattr(block, "text") and block.text:
                        parts.append(block.text)
                text = "\n".join(parts)
                parsed = _parse_response(text, batch)
                if parsed:
                    all_results.extend(parsed)
                    codes_str = ",".join(s["code"] for s in parsed)
                    gs = "/".join(f"G1={s['g1_score']}" for s in parsed)
                    print(f"  gfactor batch {i//BATCH_SIZE+1}: {codes_str} -> {gs}")
                else:
                    print(f"  gfactor batch {i//BATCH_SIZE+1}: parse failed, retry {attempt}")
                    if attempt < MAX_RETRIES: continue
                break
            except Exception as e:
                print(f"  gfactor batch {i//BATCH_SIZE+1}: API error: {e}")
                if attempt < MAX_RETRIES: continue
    return all_results


def score_codes(codes: list[str]) -> list[dict]:
    print(f"  gfactor: {len(codes)} 只，开始数据增强...")
    stocks = _enrich_stocks(codes)
    return score_batch(stocks)


# ============================================================
# CLI
# ============================================================

def _main():
    import argparse
    p = argparse.ArgumentParser(description="G-Factor 成长动能评分器")
    p.add_argument("--init", action="store_true", help="初始化数据库")
    p.add_argument("--codes", type=str, help="逗号分隔代码")
    p.add_argument("--from-pool", action="store_true", help="优先池(有财务+催化)")
    args = p.parse_args()

    if args.init:
        init_db()
        return

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.from_pool:
        # 全量有财务数据的标的 (G1 基础)。G2/G3/G4 由 _enrich_stocks 按实际数据补充。
        conn = _review_conn()
        codes = sorted(
            r[0] for r in conn.execute(
                "SELECT DISTINCT code FROM financial_indicators"
            ).fetchall()
        )
        conn.close()
        print(f"G-Factor 全量池: {len(codes)} 只 (有财务数据)")
    else:
        p.print_help()
        return

    if not codes:
        print("无待评标的")
        return

    results = score_codes(codes)
    if results:
        save_scores(results)
        for s in results:
            print(f"  {s['code']} {s['name']}: "
                  f"G1={s['g1_score']} G2={s['g2_score']} G3={s['g3_score']} G4={s['g4_score']}")
    else:
        print("  无评分结果")


if __name__ == "__main__":
    _main()
