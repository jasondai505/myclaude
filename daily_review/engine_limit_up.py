"""每日涨停深度分析 — T1 LLM深度 / T2 结构化特征 / T3 基础统计"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

from store import _conn


# === 分层逻辑 ===

def _tier_stocks(
    zt_pool: dict,
    code_to_themes: dict[str, list[str]],
    theme_counts: dict[str, int],
) -> tuple[list, list, list]:
    t1, t2, t3 = [], [], []
    for code, info in zt_pool.items():
        boards = info.get("consecutive_boards", 1)
        themes = code_to_themes.get(code, [])
        max_theme_count = max((theme_counts.get(t, 0) for t in themes), default=0)
        is_leader = boards >= 3 or max_theme_count >= 5

        item = {"code": code, "name": info.get("name", ""),
                "boards": boards, "themes": themes,
                "first_time": info.get("first_time", ""),
                "last_time": info.get("last_time", ""),
                "blasted": info.get("blasted", 0),
                "zt_stats": info.get("zt_stats", "")}

        if is_leader:
            t1.append(item)
        elif themes:
            t2.append(item)
        else:
            t3.append(item)

    t1.sort(key=lambda x: (-x["boards"], x["first_time"]))
    t2.sort(key=lambda x: x["first_time"])
    return t1, t2, t3


# === T1: LLM 深度分析 ===

_PROMPT = """你是A股涨停板深度分析师。基于以下数据，对今日涨停票做简明分析（每支 3-5 行）：

{stock_list}

输出 JSON 数组，每元素字段：
- code: 代码
- analysis: 分析文本（含：涨停驱动逻辑、题材地位、封板质量、次日预期）
- driver: 驱动类型（题材催化/业绩驱动/消息刺激/超跌反弹/跟风补涨/独立逻辑）
- quality: 封板质量（强/中/弱）
- score: 综合评分 1-10（10=最强）

只输出 JSON，不要其他文字。"""


def _build_t1_context(t1_stocks: list, quotes: dict) -> str:
    lines = []
    for i, s in enumerate(t1_stocks, 1):
        code = s["code"]
        q = quotes.get(code, {})
        pe = q.get("pe_ttm", 0) or 0
        pb = q.get("pb", 0) or 0
        mcap = (q.get("mcap_yi", 0) or 0)
        to = q.get("turnover_pct", 0) or 0
        vol = q.get("vol_ratio", 0) or 0

        lines.append(
            f"{i}. {code} {s['name']} | {s['boards']}连板 | "
            f"封板{s['first_time']} | PE{pe:.0f} PB{pb:.1f} | "
            f"市值{mcap:.0f}亿 | 换手{to:.1f}% | 量比{vol:.1f} | "
            f"题材:{','.join(s['themes'][:3]) if s['themes'] else '无'}"
        )
    return "\n".join(lines)


def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    from pathlib import Path
    settings = Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            key = data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        except (json.JSONDecodeError, OSError):
            pass
    return key


def _call_llm_batch(stocks_batch: list, quotes: dict) -> list[dict]:
    ctx = _build_t1_context(stocks_batch, quotes)
    try:
        from anthropic import Anthropic
    except ImportError:
        return []
    client = Anthropic(api_key=_load_api_key())
    try:
        resp = client.messages.create(
            model=os.getenv("DR_LLM_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=2000,
            temperature=0.3,
            system="你是A股涨停板分析师。只输出JSON数组，不要其他文字。",
            messages=[{"role": "user", "content": _PROMPT.format(stock_list=ctx)}],
            thinking={"type": "disabled"},
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("\n```", 1)[0]
        results = json.loads(text)
        if isinstance(results, dict):
            results = [results]
        return results if isinstance(results, list) else []
    except Exception as e:
        print(f"  [limit_up] LLM batch 失败: {e}")
        return []


def _run_t1_llm(t1_stocks: list, quotes: dict) -> list[dict]:
    api_key = _load_api_key()
    if not api_key or not t1_stocks:
        return []

    BATCH_SIZE = 8
    all_results = []
    for i in range(0, len(t1_stocks), BATCH_SIZE):
        batch = t1_stocks[i:i + BATCH_SIZE]
        print(f"  [limit_up] LLM batch {i // BATCH_SIZE + 1}: "
              f"{len(batch)} stocks ({batch[0]['code']}..{batch[-1]['code']})")
        results = _call_llm_batch(batch, quotes)
        all_results.extend(results)
    return all_results


# === T2: 结构化特征提取 ===

def _extract_t2(t2_stocks: list, quotes: dict) -> list[dict]:
    results = []
    for s in t2_stocks:
        code = s["code"]
        q = quotes.get(code, {})
        results.append({
            "code": code,
            "name": s["name"],
            "boards": s["boards"],
            "first_time": s["first_time"],
            "last_time": s["last_time"],
            "blasted": s["blasted"],
            "themes": s["themes"],
            "pe": q.get("pe_ttm"),
            "pb": q.get("pb"),
            "mcap_yi": q.get("mcap_yi"),
            "turnover_pct": q.get("turnover_pct"),
            "vol_ratio": q.get("vol_ratio"),
            "amplitude_pct": q.get("amplitude_pct"),
        })
    return results


# === 主入口 ===

def analyze(date: str, zt_pool: dict, quotes: dict,
            code_to_themes: dict[str, list[str]],
            theme_counts: dict[str, int]) -> dict:
    if not zt_pool:
        return {"t1": [], "t2": [], "t3": [], "count": 0}

    t1, t2, t3 = _tier_stocks(zt_pool, code_to_themes, theme_counts)

    print(f"  [limit_up] 涨停 {len(zt_pool)} 支 → T1:{len(t1)} T2:{len(t2)} T3:{len(t3)}")

    t1_results = []
    if t1:
        t1_data = _run_t1_llm(t1, quotes)
        code_analysis = {r["code"]: r for r in t1_data}
        for s in t1:
            q = quotes.get(s["code"], {})
            llm = code_analysis.get(s["code"], {})
            t1_results.append({
                **s,
                "pe": q.get("pe_ttm"), "pb": q.get("pb"),
                "mcap_yi": q.get("mcap_yi"),
                "turnover_pct": q.get("turnover_pct"),
                "vol_ratio": q.get("vol_ratio"),
                "amplitude_pct": q.get("amplitude_pct"),
                "analysis": llm.get("analysis", ""),
                "driver": llm.get("driver", ""),
                "quality": llm.get("quality", ""),
                "score": llm.get("score"),
            })

    t2_results = _extract_t2(t2, quotes) if t2 else []
    t3_results = [{"code": s["code"], "name": s["name"],
                    "boards": s["boards"], "first_time": s["first_time"],
                    "themes": s["themes"]} for s in t3]

    _save(date, t1_results, t2_results, t3_results)

    return {"t1": t1_results, "t2": t2_results, "t3": t3_results,
            "count": len(zt_pool)}


# === 持久化 ===

def init_table():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS limit_up_analysis (
                date         TEXT NOT NULL,
                code         TEXT NOT NULL,
                name         TEXT,
                tier         INTEGER DEFAULT 2,
                boards       INTEGER DEFAULT 1,
                first_time   TEXT,
                last_time    TEXT,
                blasted      INTEGER DEFAULT 0,
                themes       TEXT DEFAULT '[]',
                pe           REAL,
                pb           REAL,
                mcap_yi      REAL,
                turnover_pct REAL,
                vol_ratio    REAL,
                amplitude_pct REAL,
                driver       TEXT,
                quality      TEXT,
                score        INTEGER,
                analysis     TEXT,
                next_day_chg REAL,
                PRIMARY KEY (date, code)
            );
            CREATE INDEX IF NOT EXISTS idx_lua_code ON limit_up_analysis(code);
            CREATE INDEX IF NOT EXISTS idx_lua_date ON limit_up_analysis(date);
        """)


def _save(date: str, t1: list[dict], t2: list[dict], t3: list[dict]):
    init_table()
    with _conn() as conn:
        for tier, items in [(1, t1), (2, t2), (3, t3)]:
            for s in items:
                conn.execute(
                    "INSERT OR REPLACE INTO limit_up_analysis "
                    "(date, code, name, tier, boards, first_time, last_time, "
                    " blasted, themes, pe, pb, mcap_yi, turnover_pct, vol_ratio, "
                    " amplitude_pct, driver, quality, score, analysis) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (date, s["code"], s.get("name", ""), tier,
                     s.get("boards", 1), s.get("first_time", ""),
                     s.get("last_time", ""), s.get("blasted", 0),
                     json.dumps(s.get("themes", []), ensure_ascii=False),
                     s.get("pe"), s.get("pb"), s.get("mcap_yi"),
                     s.get("turnover_pct"), s.get("vol_ratio"),
                     s.get("amplitude_pct"), s.get("driver", ""),
                     s.get("quality", ""), s.get("score"),
                     s.get("analysis", "")))

    print(f"  [limit_up] 已保存 T1:{len(t1)} T2:{len(t2)} T3:{len(t3)} → limit_up_analysis")


# === 查询 ===

def load_by_date(date: str) -> dict:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM limit_up_analysis WHERE date = ? ORDER BY tier, boards DESC",
            (date,),
        ).fetchall()
    result = {}
    for r in rows:
        d = dict(r)
        d["themes"] = json.loads(d.get("themes", "[]"))
        result.setdefault(f"t{d['tier']}", []).append(d)
    return result


def load_by_code(code: str, limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM limit_up_analysis WHERE code = ? ORDER BY date DESC LIMIT ?",
            (code, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def backfill_next_day(date: str, quotes_tomorrow: dict):
    with _conn() as conn:
        for code, q in quotes_tomorrow.items():
            chg = q.get("change_pct") if isinstance(q, dict) else None
            if chg is not None:
                conn.execute(
                    "UPDATE limit_up_analysis SET next_day_chg = ? "
                    "WHERE date = ? AND code = ?",
                    (chg, date, code),
                )
    print(f"  [limit_up] 已回填 {date} 次日涨跌幅")
