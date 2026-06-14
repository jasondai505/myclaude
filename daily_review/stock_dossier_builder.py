"""个股深度档案构建器 — 多维数据聚合 + LLM 一页纸合成。

用法:
    python stock_dossier_builder.py                          # 默认优先池
    python stock_dossier_builder.py --codes 688767,300624    # 指定股票
    python stock_dossier_builder.py --dry-run                # 仅聚合不调LLM
"""
from __future__ import annotations

import json, sqlite3, sys, time
from pathlib import Path
from datetime import date, timedelta, datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from daily_review.config import REPORT_DIR, STOCK_PRIMARY_CONCEPT

BASE = Path(__file__).parent
DB = BASE / "data" / "review.db"
DOSSIER_DIR = REPORT_DIR / "stock_dossiers"
FEVAL_CACHE = None


# === 数据聚合层 ============================================================

def _conn():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def _load_name_map(codes: list[str]) -> dict[str, str]:
    """从多源获取股票名称。"""
    names = {}
    conn = _conn()
    for c in codes:
        row = conn.execute(
            "SELECT name FROM research_reports WHERE code=? AND name IS NOT NULL AND name!='' "
            "ORDER BY report_date DESC LIMIT 1", (c,)
        ).fetchone()
        if row:
            names[c] = row["name"]
    conn.close()

    # 从概念表补
    for c in codes:
        if c not in names and c in STOCK_PRIMARY_CONCEPT:
            names[c] = ""

    # 从行情 API 补
    still_missing = [c for c in codes if c not in names]
    if still_missing:
        try:
            import data
            quotes = data.fetch_stock_quotes(still_missing, batch_size=50)
            for c, q in quotes.items():
                names[c] = q.get("name", "")
        except Exception:
            pass

    return names


def _load_fev(codes: list[str]) -> dict[str, dict]:
    """加载最新 FEV 评分（从 serenity.db 的 feval_scores 表）。"""
    serenity_db = BASE / "data" / "serenity.db"
    result = {}
    if not serenity_db.exists():
        return {c: {"f": 0, "e": 0, "v": 0, "total": 0, "delta": 0, "trend": "待补充"} for c in codes}
    conn_s = sqlite3.connect(str(serenity_db))
    conn_s.row_factory = sqlite3.Row
    for c in codes:
        row = conn_s.execute(
            "SELECT f_score, e_score, v_score, fev_total, f_note, e_note, v_note, date "
            "FROM feval_scores WHERE code=? ORDER BY date DESC LIMIT 1", (c,)
        ).fetchone()
        if row:
            # 趋势：比较最近两期
            rows2 = conn_s.execute(
                "SELECT fev_total FROM feval_scores WHERE code=? ORDER BY date DESC LIMIT 2", (c,)
            ).fetchall()
            delta = 0
            if len(rows2) >= 2:
                delta = (rows2[0]["fev_total"] or 0) - (rows2[1]["fev_total"] or 0)
            result[c] = {
                "f": round(row["f_score"], 1) if row["f_score"] else 0,
                "e": round(row["e_score"], 1) if row["e_score"] else 0,
                "v": round(row["v_score"], 1) if row["v_score"] else 0,
                "total": round(row["fev_total"], 1) if row["fev_total"] else 0,
                "f_note": row["f_note"] or "",
                "e_note": row["e_note"] or "",
                "v_note": row["v_note"] or "",
                "delta": round(delta, 1),
                "trend": "up" if delta > 2 else "down" if delta < -2 else "flat",
            }
        else:
            result[c] = {"f": 0, "e": 0, "v": 0, "total": 0, "delta": 0, "trend": "待补充"}
    conn_s.close()
    return result


def _load_financials(codes: list[str]) -> dict[str, dict]:
    """从 DB 加载财务指标（最近 4 期）。"""
    conn = _conn()
    result = {}
    for c in codes:
        rows = conn.execute(
            "SELECT report_date, roe, gross_margin, net_margin, debt_ratio, "
            "operating_margin, revenue_yoy, profit_yoy, diluted_eps, bv_per_share, "
            "opcash_to_profit, current_ratio "
            "FROM financial_indicators WHERE code=? ORDER BY report_date DESC LIMIT 4",
            (c,)
        ).fetchall()
        if rows:
            fin = []
            for r in reversed(rows):
                fin.append({
                    "period": r["report_date"][:10] if r["report_date"] else "",
                    "roe": round(r["roe"], 1) if r["roe"] else 0,
                    "gross_margin": round(r["gross_margin"], 1) if r["gross_margin"] else 0,
                    "net_margin": round(r["net_margin"], 1) if r["net_margin"] else 0,
                    "debt_ratio": round(r["debt_ratio"], 1) if r["debt_ratio"] else 0,
                    "revenue_yoy": round(r["revenue_yoy"], 1) if r["revenue_yoy"] else 0,
                    "profit_yoy": round(r["profit_yoy"], 1) if r["profit_yoy"] else 0,
                    "eps": round(r["diluted_eps"], 2) if r["diluted_eps"] else 0,
                    "bvps": round(r["bv_per_share"], 2) if r["bv_per_share"] else 0,
                    "ocf_quality": round(r["opcash_to_profit"], 1) if r["opcash_to_profit"] else 0,
                })
            last = rows[-1]
            result[c] = {
                "history": fin[-4:],
                "roe_now": round(last["roe"], 1) if last["roe"] else 0,
                "debt_ratio": round(last["debt_ratio"], 1) if last["debt_ratio"] else 0,
                "eps": round(last["diluted_eps"], 2) if last["diluted_eps"] else 0,
                "bvps": round(last["bv_per_share"], 2) if last["bv_per_share"] else 0,
            }
        else:
            result[c] = {"history": [], "roe_now": 0, "debt_ratio": 0, "eps": 0, "bvps": 0}
    conn.close()
    return result


def _load_industry(codes: list[str]) -> dict[str, dict]:
    """行业归属 + 同花顺一顺位概念。"""
    result = {}
    for c in codes:
        primary = STOCK_PRIMARY_CONCEPT.get(c, "")
        result[c] = {
            "primary_concept": primary,
            "industry_1st": primary,  # 同花顺一顺位即行业
        }
    return result


def _load_shareholders(codes: list[str]) -> dict[str, list]:
    """从 akshare 获取十大股东。"""
    result = {}
    for c in codes:
        try:
            import akshare as ak
            df = ak.stock_zh_stock_holder_report(c)
            if df is not None and not df.empty:
                # 取最新一期
                latest_date = df["统计日期"].iloc[0]
                latest = df[df["统计日期"] == latest_date].head(10)
                holders = []
                for _, row in latest.iterrows():
                    holders.append({
                        "name": str(row.get("股东名称", "")),
                        "pct": float(row.get("持股比例", 0)) if row.get("持股比例") else 0,
                        "shares": str(row.get("持股数", "")),
                        "type": str(row.get("股东性质", "")),
                    })
                result[c] = holders
        except Exception:
            pass
        time.sleep(0.3)
    return result


def _load_signals(codes: list[str]) -> dict[str, dict]:
    """从各信息源汇总近期信号。"""
    conn = _conn()
    d7 = (date.today() - timedelta(days=7)).isoformat()
    d30 = (date.today() - timedelta(days=30)).isoformat()
    d90 = (date.today() - timedelta(days=90)).isoformat()

    result = {c: {
        "announcements_30d": 0, "deep_read_max": 0, "deep_read_count": 0,
        "research_90d": 0, "research_latest_rating": "",
        "research_latest_inst": "", "research_signal": "",
        "zsxq_mentions_7d": 0, "catalyst_count": 0, "catalyst_active": [],
    } for c in codes}

    code_tuple = tuple(codes)

    # 公告 + 深研
    for r in conn.execute(
        f"SELECT code, MAX(total_score) as ms, COUNT(*) as cnt FROM deep_read_results "
        f"WHERE code IN ({','.join('?'*len(codes))}) AND date >= ? GROUP BY code",
        (*code_tuple, d30)
    ).fetchall():
        result[r["code"]]["deep_read_max"] = r["ms"] or 0
        result[r["code"]]["deep_read_count"] = r["cnt"]

    # 研报
    for r in conn.execute(
        f"SELECT code, COUNT(*) as cnt FROM research_reports "
        f"WHERE code IN ({','.join('?'*len(codes))}) AND report_date >= ? GROUP BY code",
        (*code_tuple, d90)
    ).fetchall():
        result[r["code"]]["research_90d"] = r["cnt"]

    for c in codes:
        row = conn.execute(
            "SELECT rating, institution FROM research_reports WHERE code=? "
            "ORDER BY report_date DESC LIMIT 1", (c,)
        ).fetchone()
        if row:
            result[c]["research_latest_rating"] = row["rating"] or ""
            result[c]["research_latest_inst"] = row["institution"] or ""

    # 星球提及
    for r in conn.execute(
        "SELECT stock_codes FROM zsxq_topics WHERE create_time >= ? AND stock_codes IS NOT NULL",
        (d7,)
    ).fetchall():
        try:
            scs = json.loads(r["stock_codes"])
            for sc in scs:
                if sc in result:
                    result[sc]["zsxq_mentions_7d"] += 1
        except Exception:
            pass

    # 催化
    for c in codes:
        rows = conn.execute(
            "SELECT catalyst_name, actionability, date FROM catalyst_signals "
            "WHERE mentioned_codes LIKE ? ORDER BY date DESC LIMIT 5",
            (f"%{c}%",)
        ).fetchall()
        for r in rows:
            result[c]["catalyst_active"].append({
                "name": r["catalyst_name"], "score": r["actionability"] or 0, "date": r["date"]
            })
        result[c]["catalyst_count"] = len(result[c]["catalyst_active"])

    conn.close()
    return result


def _load_valuation(codes: list[str]) -> dict[str, dict]:
    """估值分位数据（估值缓存表）。"""
    conn = _conn()
    result = {}
    for c in codes:
        row = conn.execute(
            "SELECT pe_pct_5y, pe_pct_3y, pb_pct_5y, pe_min_5y, pe_max_5y, pe_current, pb_current "
            "FROM valuation_cache WHERE code=? ORDER BY date DESC LIMIT 1", (c,)
        ).fetchone()
        if row:
            result[c] = {
                "pe_current": round(row["pe_current"], 1) if row["pe_current"] else 0,
                "pb_current": round(row["pb_current"], 2) if row["pb_current"] else 0,
                "pe_pct_5y": round(row["pe_pct_5y"], 1) if row["pe_pct_5y"] else 0,
                "pe_pct_3y": round(row["pe_pct_3y"], 1) if row["pe_pct_3y"] else 0,
                "pb_pct_5y": round(row["pb_pct_5y"], 1) if row["pb_pct_5y"] else 0,
                "pe_min_5y": round(row["pe_min_5y"], 1) if row["pe_min_5y"] else 0,
                "pe_max_5y": round(row["pe_max_5y"], 1) if row["pe_max_5y"] else 0,
            }
        else:
            result[c] = {}
    conn.close()
    return result


def aggregate(codes: list[str]) -> dict[str, dict]:
    """对指定股票列表执行全维度数据聚合，返回 code → dossier_json。"""
    codes = [c.zfill(6) for c in codes]
    print(f"聚合 {len(codes)} 只标的...")

    print("  [1/6] 名称...", end=" ")
    names = _load_name_map(codes)
    print(f"{len(names)}只")

    print("  [2/6] FEV...", end=" ")
    fevs = _load_fev(codes)
    print(f"{len(fevs)}只")

    print("  [3/6] 财务...", end=" ")
    fins = _load_financials(codes)
    print(f"{len(fins)}只")

    print("  [4/6] 行业...", end=" ")
    industry = _load_industry(codes)
    print(f"{len(industry)}只")

    print("  [5/6] 信号...", end=" ")
    signals = _load_signals(codes)
    print(f"{len(signals)}只")

    print("  [6/6] 股东...", end=" ")
    holders = _load_shareholders(codes)
    print(f"{len(holders)}只有数据")

    # 组装
    dossiers = {}
    for c in codes:
        dossiers[c] = {
            "code": c,
            "name": names.get(c, "?"),
            "fev": fevs.get(c, {}),
            "financials": fins.get(c, {}),
            "industry": industry.get(c, {}),
            "signals": signals.get(c, {}),
            "shareholders": holders.get(c, []),
            "built_at": date.today().isoformat(),
        }
    return dossiers


# === LLM 合成层 ============================================================

SYNTHESIS_PROMPT = """你是A股基本面分析师。以下是某只股票的多维数据聚合。请写一份「一页纸深度档案」(600-800字)，供盘中快速查阅。

## 股票数据
{data_json}

## 要求
用 Markdown 格式，包含以下章节（每章必须有具体数据和判断）：

### 一句话
用一句话概括这家公司的核心逻辑和当前状态。

### 财务纵览
用表格展示最近4个报告期的营收/净利/毛利率/ROE/资产负债率（数据已给）。
分析趋势：增长/下滑/拐点？盈利能力改善还是恶化？

### 产业链位置
基于同花顺概念和行业归属，简要描述公司在产业链中的位置。
上游是什么？下游是什么？主要竞争对手？（如信息不足则标注「待补充」）

### FEV 三角
三句话解读 F、E、V 三个维度：基本面质量、估值吸引力、预期差。
FEV 数据已给出，在此基础上做判断。

### 机构共识与预期差
引用研报信号和评级，指出机构在关注什么，市场可能在哪些方面定价不足。

### 近期催化
列出近期的催化剂事件，每个催化说明时间、类型、可能影响。

### 风险
列出 2-3 个核心风险，每个一句话。

### 操作备忘
一句话：当前时点最值得跟踪的变量是什么？

只返回 Markdown 正文（不要用 ``` 代码块包裹，直接从 ### 一行开始）。"""


def synthesize_one(code: str, dossier: dict) -> str:
    """对单只股票执行 LLM 深度合成，返回 Markdown 文本。"""
    from roles import get_client, get_model
    client = get_client("deep", timeout=120)
    model = get_model("deep")

    # 精简数据以减少 token
    compact = {
        "code": dossier["code"],
        "name": dossier["name"],
        "fev": dossier.get("fev", {}),
        "financials": dossier.get("financials", {}),
        "industry": dossier.get("industry", {}),
        "signals": dossier.get("signals", {}),
        "shareholders": dossier.get("shareholders", [])[:5],  # top 5
    }

    prompt = SYNTHESIS_PROMPT.format(data_json=json.dumps(compact, ensure_ascii=False, indent=2))

    resp = client.messages.create(
        model=model, max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "disabled"},
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def synthesize_batch(dossiers: dict[str, dict], dry_run: bool = False) -> dict[str, str]:
    """批量合成，返回 code → markdown_text。"""
    results = {}
    for i, (code, dossier) in enumerate(dossiers.items()):
        name = dossier.get("name", "?")
        print(f"  [{i+1}/{len(dossiers)}] {code} {name}...", end=" ")
        if dry_run:
            results[code] = f"# {code} {name}\n\n*DRY RUN — 未调 LLM*\n\n```json\n{json.dumps(dossier, ensure_ascii=False, indent=2)[:2000]}\n```"
            print("DRY-RUN")
        else:
            try:
                md = synthesize_one(code, dossier)
                results[code] = md
                print(f"OK ({len(md)}字)")
            except Exception as e:
                print(f"FAIL: {e}")
                results[code] = f"# {code} {name}\n\n*合成失败: {e}*"
            time.sleep(0.5)
    return results


def save_dossiers(results: dict[str, str], dossiers: dict[str, dict]):
    """存档到 Obsidian 目录。"""
    DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    for code, md in results.items():
        name = dossiers.get(code, {}).get("name", "")
        safe_name = "".join(c for c in name if c not in r'\/:*?"<>|').strip()
        path = DOSSIER_DIR / f"{code}_{safe_name}.md"
        path.write_text(md, encoding="utf-8")
    print(f"\n存档: {DOSSIER_DIR} ({len(results)} 份)")


# === 主入口 ================================================================

def get_priority_pool() -> list[str]:
    """获取优先池股票列表：档案活跃 + 催化活跃 + 深研高评分。"""
    d14 = (date.today() - timedelta(days=14)).isoformat()
    d7 = (date.today() - timedelta(days=7)).isoformat()

    # 档案活跃
    active_dos = set()
    for f in Path("daily_review/reports/research_dossiers").glob("*.md"):
        if not f.name[:6].isdigit():
            continue
        code = f.name[:6]
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if mtime.date() >= date.today() - timedelta(days=14):
            active_dos.add(code)

    # 催化标的
    cat_codes = set()
    for f in sorted(Path("daily_review/reports/catalyst").glob("catalyst_screen_*.json"), reverse=True)[:3]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for c in data.get("catalysts", []):
                for m in data.get("stock_maps", {}).get(c.get("catalyst_name", ""), []):
                    code = m.get("code", "")
                    if code and code.isdigit() and len(code) == 6:
                        cat_codes.add(code.zfill(6))
        except Exception:
            pass

    # 深研高评分
    conn = _conn()
    deep_codes = {}
    for r in conn.execute(
        f"SELECT code, MAX(total_score) as ms FROM deep_read_results "
        f"WHERE date >= ? AND total_score >= 55 GROUP BY code", (d7,)
    ).fetchall():
        if r["code"].isdigit() and len(r["code"]) == 6:
            deep_codes[r["code"]] = r["ms"]

    # 研报覆盖
    research_cnt = {}
    for r in conn.execute(
        f"SELECT code, COUNT(*) as cnt FROM research_reports "
        f"WHERE report_date >= ? GROUP BY code", (d7,)
    ).fetchall():
        research_cnt[r["code"].zfill(6)] = r["cnt"]
    conn.close()

    # 综合评分
    all_codes = set(deep_codes) | active_dos | set(research_cnt) | cat_codes
    scored = []
    for c in all_codes:
        ds = deep_codes.get(c, 0)
        dos = 15 if c in active_dos else 0
        rpt = min(research_cnt.get(c, 0), 10)
        cat = 20 if c in cat_codes else 0
        scored.append((ds * 2 + dos + rpt * 3 + cat, c))

    scored.sort(reverse=True)
    return [c for _, c in scored[:22]]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="个股深度档案构建器")
    p.add_argument("--codes", type=str, default="", help="逗号分隔股票代码")
    p.add_argument("--dry-run", action="store_true", help="仅聚合不调LLM")
    p.add_argument("--pool", action="store_true", help="显示优先池并退出")
    args = p.parse_args()

    if args.pool:
        pool = get_priority_pool()
        names = _load_name_map(pool)
        print(f"优先池 ({len(pool)} 只):")
        for i, c in enumerate(pool):
            print(f"  {i+1:2d}. {c} {names.get(c, '?')}")
        sys.exit(0)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = get_priority_pool()

    print(f"标的: {len(codes)} 只")

    # Step 1: 聚合
    dossiers = aggregate(codes)

    # Step 2: 合成
    print("\nLLM 合成...")
    results = synthesize_batch(dossiers, dry_run=args.dry_run)

    # Step 3: 存档
    save_dossiers(results, dossiers)
    print("\n完成")
