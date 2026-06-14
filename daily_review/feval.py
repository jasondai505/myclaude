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

            CREATE TABLE IF NOT EXISTS stock_delta (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                code      TEXT NOT NULL,
                name      TEXT NOT NULL,
                date      TEXT NOT NULL,
                delta_score INTEGER DEFAULT 0,
                signal    TEXT DEFAULT '',
                source    TEXT DEFAULT '',
                UNIQUE(code, date)
            );
            CREATE INDEX IF NOT EXISTS idx_delta_code ON stock_delta(code);
            CREATE INDEX IF NOT EXISTS idx_delta_date ON stock_delta(date);
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

    from daily_review.roles import get_client as _rc2, get_model as _rm2
    client = _rc2("synthesis", timeout=120)

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
# Delta scoring (边际变化评分)
# ============================================================

DELTA_PROMPT = """你是A股事件驱动分析师。从以下今日信息源中，**找出每一个被提及且有任何边际信号的标的**，标注其边际变化方向和烈度。

Δ 评分标准 (-10 ~ +10):
  +8~10: 产业级利好 (国家级政策、百亿级订单、技术路线确立、IPO过会、龙头企业涨价函、海外巨头锁货)
  +5~7:  个股级重大利好 (签订大额合同、涨价落地、突破量产、大客户导入、机构大幅上调盈利预测、实控人增持)
  +3~4:  明确利好 (产能释放、新品发布、行业数据向好、子公司业绩爆发)
  +1~2:  边际改善 (调研密度上升、机构首次覆盖、行业情绪回暖、Q2展望积极)
  0:     被提及但无实质增量信号（默认值，不输出）
  -1~2:  边际走弱 (股东减持(非为扩产)、需求弱于预期、竞争加剧)
  -3~4:  明确利空 (业绩miss、客户流失、价格战、核心人员离职)
  -5~7:  重大利空 (大客户砍单、技术路线被替代、业绩大幅下调、监管处罚)
  -8~10: 逻辑证伪 (政策转向、核心专利丧失、财务造假、退市风险)

**重要**: 只要信息源中提到了某标的且伴随任何利多/利空信息，就应该输出。不要只挑最重磅的——所有有信号的标的都要输出。涨价、签单、扩产、突破、增持、政策受益都算信号。

信息源:
{feed_text}

输出格式 (JSON):
```json
[{{"code":"xxxxxx","name":"xxx","delta":±X,"signal":"一句话总结边际变化","source":"星球/公告/研报/公众号"}}]
```
delta 必须是整数 -10~10。至少输出 5 个标的，尽可能覆盖所有有信号的标的。
"""


def save_delta_scores(scores: list[dict]):
    with _conn() as conn:
        for s in scores:
            conn.execute(
                """INSERT OR REPLACE INTO stock_delta
                   (code, name, date, delta_score, signal, source)
                   VALUES (?,?,?,?,?,?)""",
                (s["code"], s["name"], s.get("date", _today()),
                 s.get("delta_score", 0), s.get("signal", ""), s.get("source", "")),
            )


def get_delta_scores(codes: list[str] | None = None, date_str: str = "") -> dict[str, dict]:
    d = date_str or _today()
    with _conn() as conn:
        # 如果指定日期没有数据，回退到最新可用日期
        exists = conn.execute(
            "SELECT COUNT(*) FROM stock_delta WHERE date=?", (d,)
        ).fetchone()[0]
        if not exists and not date_str:
            latest = conn.execute(
                "SELECT MAX(date) FROM stock_delta"
            ).fetchone()[0]
            if latest:
                d = latest

        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM stock_delta WHERE code IN ({placeholders}) AND date=?",
                codes + [d],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock_delta WHERE date=?", (d,)
            ).fetchall()
        return {r["code"]: dict(r) for r in rows}


def score_delta_from_feeds(date_str: str = "") -> list[dict]:
    """从当天 feeds 提取所有代码的 Δ 评分。"""
    d = date_str or _today()
    feeds_dir = BASE / "reports" / "feeds"

    # 收集 feeds
    feed_texts = []
    codes_found: set[str] = set()
    for pattern in ["zsxq_*.md", "news_*.md", "announcements_*.md", "industry_*.md"]:
        for f in sorted(feeds_dir.glob(pattern)):
            if d in f.name:
                try:
                    text = f.read_text(encoding="utf-8")
                    feed_texts.append(f"## {f.stem}\n{text[:3000]}")
                    codes_found.update(re.findall(r"\b(\d{6})\b", text))
                except Exception:
                    pass

    # 也加入 wechat_analysis 和 recap
    for stem, label in [("recap", "昨日回顾"), ("review_summary", "复盘摘要")]:
        path = feeds_dir / f"{stem}_{d}.md"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                feed_texts.append(f"## {label}\n{text[:2000]}")
                codes_found.update(re.findall(r"\b(\d{6})\b", text))
            except Exception:
                pass

    wechat_path = BASE / "reports" / "wechat" / f"wechat_analysis_{d}.md"
    if wechat_path.exists():
        try:
            text = wechat_path.read_text(encoding="utf-8")
            feed_texts.append(f"## 公众号分析\n{text[:3000]}")
            codes_found.update(re.findall(r"\b(\d{6})\b", text))
        except Exception:
            pass

    if not codes_found:
        print("  delta: 未从 feeds 中提取到代码")
        return []

    # 跳过今日已有评分的
    existing = set(get_delta_scores(date_str=d).keys())
    if codes_found.issubset(existing):
        print(f"  delta: {len(codes_found)} 个代码均已评分，跳过")
        return []

    print(f"  delta: feeds 共 {len(codes_found)} 个代码，{len(codes_found - existing)} 个待评分")

    # 获取行情（用于获取名称）
    try:
        import data
        codes_list = sorted(codes_found)[:200]  # 扩大到200只，减少遗漏
        quotes = data.fetch_stock_quotes(codes_list, batch_size=30)
    except Exception:
        print("  delta: 行情获取失败")
        return []

    api_key = _load_api_key()
    if not api_key:
        print("  [WARN] delta: 无 API key，跳过")
        return []

    client = _rc2("synthesis", timeout=120)

    combined_feeds = "\n".join(feed_texts)
    prompt = DELTA_PROMPT.format(feed_text=combined_feeds[:20000])

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "disabled"},
                timeout=120,
            )
            parts = []
            for block in resp.content:
                if hasattr(block, "text") and block.text:
                    parts.append(block.text)
            text = "\n".join(parts)
            break
        except Exception as e:
            print(f"  delta: API error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                continue
            return []

    # 解析
    json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if not json_match:
        json_match = re.search(r"(\[.*?\])", text, re.DOTALL)
    if not json_match:
        print(f"  delta: 无法解析 JSON，原文前200字: {text[:200]}")
        return []

    try:
        raw = json.loads(json_match.group(1))
    except json.JSONDecodeError as e:
        print(f"  delta: JSON 解析失败: {e}")
        return []

    name_map = {code: q.get("name", "") for code, q in quotes.items()}
    results = []
    for item in raw:
        code = str(item.get("code", "")).strip()
        if code not in name_map:
            continue
        ds = max(-10, min(10, int(item.get("delta", 0) or 0)))
        results.append({
            "code": code,
            "name": name_map[code],
            "date": d,
            "delta_score": ds,
            "signal": str(item.get("signal", "") or "")[:120],
            "source": str(item.get("source", "") or "feeds")[:40],
        })

    if results:
        save_delta_scores(results)
        for r in sorted(results, key=lambda x: -abs(x["delta_score"]))[:10]:
            sign = "+" if r["delta_score"] >= 0 else ""
            print(f"  Δ {sign}{r['delta_score']:d} {r['code']} {r['name']}: {r['signal'][:50]}")
        print(f"  delta: 已保存 {len(results)} 个评分")

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
    p.add_argument("--update-delta", type=str, nargs="?", const=_today(),
                   help="更新今日 Δ 边际变化评分 (日期可选)")
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

    elif args.update_delta:
        score_delta_from_feeds(args.update_delta)

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
