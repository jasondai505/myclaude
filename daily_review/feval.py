"""独立 FEV 评分器 — 任何 A 股标的入参即出 F/E/V 评分，不依赖产业链分析。

用法:
  python feval.py --codes 301373,688300,000657          # 对指定标的评分
  python feval.py --from-feeds 2026-06-10               # 从当天 feeds 提取所有代码并评分
  python feval.py --list                                 # 列出已有评分
  python feval.py --init                                 # 初始化数据库表

数据流:
  _run_advice.py → 提取 feeds 代码 → feval.score_batch() → 注入 STOCK_CONTEXT
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
# SQLite
# ============================================================

def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS feval_scores (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                code    TEXT NOT NULL,
                name    TEXT NOT NULL,
                date    TEXT NOT NULL,
                f_score INTEGER DEFAULT 0,
                e_score INTEGER DEFAULT 0,
                v_score INTEGER DEFAULT 0,
                fev_total INTEGER DEFAULT 0,
                f_note  TEXT DEFAULT '',
                e_note  TEXT DEFAULT '',
                v_note  TEXT DEFAULT '',
                source  TEXT DEFAULT 'feval',
                UNIQUE(code, date)
            );
            CREATE INDEX IF NOT EXISTS idx_feval_code ON feval_scores(code);
            CREATE INDEX IF NOT EXISTS idx_feval_date ON feval_scores(date);
        """)


def save_scores(scores: list[dict]):
    with _conn() as conn:
        for s in scores:
            conn.execute(
                """INSERT OR REPLACE INTO feval_scores
                   (code, name, date, f_score, e_score, v_score, fev_total,
                    f_note, e_note, v_note, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (s["code"], s["name"], s.get("date", _today()),
                 s.get("f_score", 0), s.get("e_score", 0), s.get("v_score", 0),
                 s.get("fev_total", 0),
                 s.get("f_note", ""), s.get("e_note", ""), s.get("v_note", ""),
                 s.get("source", "feval")),
            )


def get_scores(codes: list[str] | None = None, date_str: str = "") -> dict[str, dict]:
    with _conn() as conn:
        if date_str:
            rows = conn.execute(
                "SELECT * FROM feval_scores WHERE date=?", (date_str,)
            ).fetchall()
        elif codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM feval_scores WHERE code IN ({placeholders}) "
                f"AND date=(SELECT MAX(date) FROM feval_scores WHERE code=feval_scores.code)",
                codes,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM feval_scores WHERE date=(SELECT MAX(date) FROM feval_scores)"
            ).fetchall()
        return {r["code"]: dict(r) for r in rows}


def list_scores(date_str: str = "") -> list[dict]:
    d = date_str or _today()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM feval_scores WHERE date=? ORDER BY fev_total DESC", (d,)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# LLM scoring
# ============================================================

FEVAL_PROMPT = """你是A股基本面分析师。对以下标的分别给出 F/E/V 评分（每项 0-10 分，整数），附一句话理由。

评分标准:
  F (护城河) 0-10: 品牌/专利/牌照/规模/网络效应/转换成本/成本优势
  E (盈利)   0-10: ROE/毛利率/营收增速/现金流/盈利确定性
  V (估值)   0-10: PE分位/PB分位/PEG/股息率/市值空间 (10=极度低估, 0=泡沫)

输出格式 (JSON):
```json
[{"code":"xxxxxx","name":"xxx","F":x,"F_note":"xxx","E":x,"E_note":"xxx","V":x,"V_note":"xxx"}]
```

注意: F/E/V 必须填整数 0-10, FEV=F+E+V 不填由程序计算。如果标的数据不足无法评分, 各项填 0 并标注 "数据不足"。
"""


def _build_batch_prompt(stocks: list[dict]) -> str:
    lines = ["标的列表:\n"]
    for s in stocks:
        lines.append(
            f"- {s['code']} {s['name']}"
            f" | 市值:{s.get('mcap_yi',0)}亿 | PE:{s.get('pe_ttm',0):.1f}"
            f" | 涨跌:{s.get('chg_pct',0):+.1f}%"
        )
    return "\n".join(lines)


def _parse_response(text: str, stocks: list[dict]) -> list[dict]:
    json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\[.*?\])", text, re.DOTALL)
    if not json_match:
        print(f"  [WARN] feval: 无法从响应中提取 JSON，原文前200字: {text[:200]}")
        return []

    try:
        raw = json.loads(json_match.group(1))
    except json.JSONDecodeError as e:
        print(f"  [WARN] feval: JSON 解析失败: {e}")
        return []

    code_map = {s["code"]: s["name"] for s in stocks}
    results = []
    for item in raw:
        code = str(item.get("code", "")).strip()
        if code not in code_map:
            continue
        f_s = max(0, min(10, int(item.get("F", 0) or 0)))
        e_s = max(0, min(10, int(item.get("E", 0) or 0)))
        v_s = max(0, min(10, int(item.get("V", 0) or 0)))
        results.append({
            "code": code,
            "name": code_map[code],
            "date": _today(),
            "f_score": f_s,
            "e_score": e_s,
            "v_score": v_s,
            "fev_total": f_s + e_s + v_s,
            "f_note": str(item.get("F_note", "") or "")[:120],
            "e_note": str(item.get("E_note", "") or "")[:120],
            "v_note": str(item.get("V_note", "") or "")[:120],
            "source": "feval",
        })
    return results


def score_batch(stocks: list[dict]) -> list[dict]:
    """对一批标的一次 LLM 调用评分。stocks: [{code, name, mcap_yi, pe_ttm, chg_pct}]"""
    api_key = _load_api_key()
    if not api_key:
        print("  [WARN] feval: 无 API key，跳过评分")
        return []

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key, base_url="https://api.deepseek.com/anthropic")

    all_results = []
    for i in range(0, len(stocks), BATCH_SIZE):
        batch = stocks[i:i + BATCH_SIZE]
        prompt = FEVAL_PROMPT + "\n" + _build_batch_prompt(batch)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=1500,
                    messages=[{"role": "user", "content": prompt}],
                    thinking={"type": "disabled"},
                    timeout=60,
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
                    fevs = "/".join(str(s["fev_total"]) for s in parsed)
                    print(f"  feval batch {i//BATCH_SIZE+1}: {codes_str} -> FEV {fevs}")
                else:
                    print(f"  feval batch {i//BATCH_SIZE+1}: parse failed, retry {attempt}")
                    if attempt < MAX_RETRIES:
                        continue
                break
            except Exception as e:
                print(f"  feval batch {i//BATCH_SIZE+1}: API error: {e}")
                if attempt < MAX_RETRIES:
                    continue

    return all_results


# ============================================================
# Integration with daily pipeline
# ============================================================

def score_from_feeds(date_str: str = "") -> list[dict]:
    """从当天 feeds 中提取所有代码并评分（增量：跳过已有评分的标的）"""
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

    if not codes_found:
        print("  feval: 未从 feeds 中提取到股票代码")
        return []

    existing = set(get_scores(date_str=d).keys())
    new_codes = sorted(codes_found - existing)
    if not new_codes:
        print(f"  feval: {len(codes_found)} 个代码均已评分，跳过")
        return []

    print(f"  feval: feeds 共 {len(codes_found)} 个代码，{len(new_codes)} 个待评分")

    try:
        import data
        quotes = data.fetch_stock_quotes(new_codes, batch_size=30)
    except Exception:
        print("  feval: 行情获取失败")
        return []

    stocks = []
    for code in new_codes:
        q = quotes.get(code, {})
        stocks.append({
            "code": code,
            "name": q.get("name", ""),
            "mcap_yi": round(q.get("mcap_yi", 0) or 0),
            "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
            "chg_pct": round(q.get("change_pct", 0) or 0, 2),
        })

    results = score_batch(stocks)
    if results:
        save_scores(results)
        print(f"  feval: 已保存 {len(results)} 个评分")
    return results


# ============================================================
# CLI
# ============================================================

def _main():
    import argparse
    p = argparse.ArgumentParser(description="独立 FEV 评分器")
    p.add_argument("--init", action="store_true", help="初始化数据库")
    p.add_argument("--codes", type=str, help="逗号分隔代码列表，如 301373,688300")
    p.add_argument("--from-feeds", type=str, nargs="?", const=_today(),
                   help="从当天 feeds 提取代码并评分 (日期可选)")
    p.add_argument("--list", type=str, nargs="?", const=_today(),
                   help="列出评分 (日期可选)")
    args = p.parse_args()

    if args.init:
        init_db()
        print("feval 表初始化完成")
        return

    init_db()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        try:
            import data
            quotes = data.fetch_stock_quotes(codes, batch_size=30)
        except Exception:
            print("行情获取失败")
            return
        stocks = []
        for code in codes:
            q = quotes.get(code, {})
            stocks.append({
                "code": code,
                "name": q.get("name", ""),
                "mcap_yi": round(q.get("mcap_yi", 0) or 0),
                "pe_ttm": round(q.get("pe_ttm", 0) or 0, 1),
                "chg_pct": round(q.get("change_pct", 0) or 0, 2),
            })
        results = score_batch(stocks)
        if results:
            save_scores(results)
            for s in results:
                print(f"  {s['code']} {s['name']}: F={s['f_score']} E={s['e_score']} "
                      f"V={s['v_score']} FEV={s['fev_total']}")

    elif args.from_feeds:
        score_from_feeds(args.from_feeds)

    elif args.list:
        scores = list_scores(args.list)
        if scores:
            print(f"{'代码':<8} {'名称':<10} {'F':>2} {'E':>2} {'V':>2} {'FEV':>3}  F备注")
            for s in scores:
                print(f"{s['code']:<8} {s['name']:<10} {s['f_score']:>2} {s['e_score']:>2} "
                      f"{s['v_score']:>2} {s['fev_total']:>3}  {s['f_note'][:40]}")
        else:
            print(f"  {args.list}: 暂无评分")

    else:
        p.print_help()


if __name__ == "__main__":
    _main()
