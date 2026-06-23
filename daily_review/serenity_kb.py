"""Serenity 产业链卡脖子知识库 — SQLite 持久化 + CLI 入口。

三张表：
  chain_snapshot   — 产业链每层卡脖子评分快照
  stock_chokepoint — A 股标的卡脖子分 + FEV 评分（按日）
  analysis_log     — 每次完整分析的记录

用法:
  python serenity_kb.py --init                  # 初始化数据库
  python serenity_kb.py --chain AI算力           # 对指定链跑全量分析
  python serenity_kb.py --update-all             # 所有链增量更新（收盘流水线）
  python serenity_kb.py --update-stocks          # 仅更新 FEV 评分（盘前流水线）
  python serenity_kb.py --list                   # 列出已有分析的产业链
  python serenity_kb.py --report AI算力          # 输出某链最新 Markdown 报告
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

DB_PATH = BASE / "data" / "serenity.db"
REPORT_DIR = BASE / "reports" / "serenity"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

TECH_CHAINS = [
    "AI算力", "半导体", "光伏设备", "消费电子", "汽车零部件",
    "通信设备", "元件", "自动化设备", "电子化学品", "光学光电子",
    "军工电子", "其他电子", "电机", "金属新材料",
]


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


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
            CREATE TABLE IF NOT EXISTS chain_snapshot (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chain_name   TEXT NOT NULL,
                tier         TEXT NOT NULL,
                segment      TEXT NOT NULL,
                global_chokepoint_score INTEGER DEFAULT 0,
                supply_status     TEXT DEFAULT '',
                attention_level   TEXT DEFAULT '',
                a_stock_mapping   TEXT DEFAULT '',
                scene            TEXT DEFAULT '',
                last_updated  TEXT NOT NULL,
                UNIQUE(chain_name, segment)
            );

            CREATE TABLE IF NOT EXISTS stock_chokepoint (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                code          TEXT NOT NULL,
                name          TEXT NOT NULL,
                chain_name    TEXT NOT NULL,
                segment       TEXT DEFAULT '',
                chokepoint_score INTEGER DEFAULT 0,
                f_score       INTEGER DEFAULT 0,
                e_score       INTEGER DEFAULT 0,
                v_score       INTEGER DEFAULT 0,
                fev_total     INTEGER DEFAULT 0,
                scene         TEXT DEFAULT '',
                date          TEXT NOT NULL,
                UNIQUE(code, chain_name, date)
            );

            CREATE TABLE IF NOT EXISTS analysis_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chain_name    TEXT NOT NULL,
                date          TEXT NOT NULL,
                trigger       TEXT DEFAULT 'manual',
                layer1_summary TEXT DEFAULT '',
                layer2_stocks  TEXT DEFAULT '',
                layer3_fev_top TEXT DEFAULT '',
                full_report_path TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_cs_chain ON chain_snapshot(chain_name);
            CREATE INDEX IF NOT EXISTS idx_sc_code ON stock_chokepoint(code);
            CREATE INDEX IF NOT EXISTS idx_sc_chain ON stock_chokepoint(chain_name);
            CREATE INDEX IF NOT EXISTS idx_sc_date ON stock_chokepoint(date);
            CREATE INDEX IF NOT EXISTS idx_al_chain ON analysis_log(chain_name);
            CREATE INDEX IF NOT EXISTS idx_al_date ON analysis_log(date);
        """)


# ============================================================
# chain_snapshot CRUD
# ============================================================

def save_chain_snapshot(chain_name: str, segments: list[dict]):
    with _conn() as conn:
        for seg in segments:
            conn.execute(
                """INSERT OR REPLACE INTO chain_snapshot
                   (chain_name, tier, segment, global_chokepoint_score,
                    supply_status, attention_level, a_stock_mapping, scene, last_updated)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (chain_name, seg.get("tier", ""), seg.get("segment", ""),
                 seg.get("chokepoint_score", 0), seg.get("supply_status", ""),
                 seg.get("attention_level", ""), seg.get("a_stock_mapping", ""),
                 seg.get("scene", ""), _today()),
            )


def get_chain_snapshot(chain_name: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chain_snapshot WHERE chain_name=? ORDER BY tier, segment",
            (chain_name,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_chain_summary() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT chain_name,
                   MAX(global_chokepoint_score) as max_score,
                   COUNT(*) as segment_count,
                   MAX(last_updated) as last_updated
            FROM chain_snapshot
            GROUP BY chain_name
            ORDER BY max_score DESC
        """).fetchall()
        return [dict(r) for r in rows]


def chain_needs_update(chain_name: str, max_days: int = 7) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(last_updated) as lu FROM chain_snapshot WHERE chain_name=?",
            (chain_name,)
        ).fetchone()
        if not row or not row["lu"]:
            return True
        last = datetime.strptime(row["lu"], "%Y-%m-%d").date()
        return (date.today() - last).days >= max_days


# ============================================================
# stock_chokepoint CRUD
# ============================================================

def save_stock_scores(chain_name: str, stocks: list[dict]):
    with _conn() as conn:
        for s in stocks:
            conn.execute(
                """INSERT OR REPLACE INTO stock_chokepoint
                   (code, name, chain_name, segment, chokepoint_score,
                    f_score, e_score, v_score, fev_total, scene, date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get("code", ""), s.get("name", ""), chain_name,
                 s.get("segment", ""), s.get("chokepoint_score", 0),
                 s.get("f_score", 0), s.get("e_score", 0), s.get("v_score", 0),
                 s.get("fev_total", 0), s.get("scene", ""), _today()),
            )


def get_stock_scores(chain_name: str = "", date_str: str = "") -> list[dict]:
    with _conn() as conn:
        if chain_name and date_str:
            rows = conn.execute(
                "SELECT * FROM stock_chokepoint WHERE chain_name=? AND date=? ORDER BY fev_total DESC",
                (chain_name, date_str)
            ).fetchall()
        elif chain_name:
            rows = conn.execute(
                """SELECT * FROM stock_chokepoint
                   WHERE chain_name=?
                   AND date=(SELECT MAX(date) FROM stock_chokepoint WHERE chain_name=?)
                   ORDER BY fev_total DESC""",
                (chain_name, chain_name)
            ).fetchall()
        elif date_str:
            rows = conn.execute(
                "SELECT * FROM stock_chokepoint WHERE date=? ORDER BY fev_total DESC",
                (date_str,)
            ).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM stock_chokepoint
                WHERE date=(SELECT MAX(date) FROM stock_chokepoint)
                ORDER BY fev_total DESC
            """).fetchall()
        return [dict(r) for r in rows]


def get_stock_history(code: str, days: int = 30) -> list[dict]:
    with _conn() as conn:
        cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT date, chokepoint_score, fev_total FROM stock_chokepoint WHERE code=? AND date>=? ORDER BY date",
            (code, cutoff)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# analysis_log CRUD
# ============================================================

def save_analysis_log(chain_name: str, trigger: str, layer1: str,
                      layer2_stocks: str, layer3_fev: str, report_path: str = ""):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO analysis_log
               (chain_name, date, trigger, layer1_summary, layer2_stocks, layer3_fev_top, full_report_path)
               VALUES (?,?,?,?,?,?,?)""",
            (chain_name, _today(), trigger, layer1[:500], layer2_stocks[:500],
             layer3_fev[:500], report_path),
        )


def get_latest_analysis(chain_name: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM analysis_log WHERE chain_name=? ORDER BY date DESC LIMIT 1",
            (chain_name,)
        ).fetchone()
        return dict(row) if row else None


def get_recent_analyses(days: int = 7) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM analysis_log WHERE date>=? ORDER BY date DESC",
            (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# Markdown report
# ============================================================

def save_markdown_report(chain_name: str, content: str) -> str:
    path = REPORT_DIR / f"{chain_name}_{_today()}.md"
    path.write_text(content, encoding="utf-8")
    _update_index()
    return str(path)


def _update_index():
    lines = ["# 产业链卡脖子分析 · 索引", "", f"更新: {_now()}", ""]
    with _conn() as conn:
        rows = conn.execute("""
            SELECT chain_name, MAX(date) as latest, COUNT(*) as count
            FROM analysis_log GROUP BY chain_name ORDER BY latest DESC
        """).fetchall()
    if rows:
        lines.append("| 产业链 | 最近分析 | 累计次数 | 卡脖子最高分 |")
        lines.append("|--------|---------|---------|------------|")
        for r in rows:
            s = _max_chain_score(r["chain_name"])
            lines.append(f"| {r['chain_name']} | {r['latest']} | {r['count']} | {s} |")
    lines.append("")
    (REPORT_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _max_chain_score(chain_name: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(global_chokepoint_score) as ms FROM chain_snapshot WHERE chain_name=?",
            (chain_name,)
        ).fetchone()
        return row["ms"] or 0


# ============================================================
# 增量更新逻辑
# ============================================================

def update_chain_kb(chain_name: str, force_full: bool = False):
    needs_full = force_full or chain_needs_update(chain_name)
    trigger = "manual" if force_full else "auto"

    try:
        from engine_serenity import analyze_global_chain, map_to_a_shares, validate_with_fe
    except ImportError:
        print(f"  [ERROR] 无法导入 engine_serenity")
        return

    print(f"  {'[全量]' if needs_full else '[增量]'} {chain_name} ...")

    if needs_full:
        layer1 = analyze_global_chain(chain_name)
        layer2 = map_to_a_shares(chain_name, layer1) if layer1 else ""
    else:
        layer1 = ""
        layer2 = ""

    layer3 = ""
    try:
        from engine_serenity import _get_chain_codes
        codes = _get_chain_codes(chain_name)
        if codes:
            layer3 = validate_with_fe(codes, chain_name)
    except Exception as e:
        print(f"    FEV 更新失败: {e}")

    if layer1:
        segments = _parse_layer1_scores(layer1)
        if segments:
            save_chain_snapshot(chain_name, segments)

    if layer3:
        stocks = _parse_layer3_scores(layer3, chain_name)
        if stocks:
            save_stock_scores(chain_name, stocks)

    layer2_codes = _extract_codes(layer2) if layer2 else ""
    layer3_top = _extract_fev_top(layer3) if layer3 else ""

    full_report = ""
    if needs_full:
        parts = [layer1, "", layer2, "", layer3]
        full_content = "\n\n".join(p for p in parts if p)
        full_report = save_markdown_report(chain_name, full_content)

    save_analysis_log(chain_name, trigger, layer1[:500], layer2_codes[:500],
                      layer3_top[:500], full_report)
    print(f"    ✅ {chain_name}")


def batch_update_all(chains: list[str] | None = None):
    if chains is None:
        try:
            from bom_analyzer import chain_db
            chain_db.init_db()
            all_chains = chain_db.list_industries()
            chains = [c for c in TECH_CHAINS if c in all_chains]
            if not chains:
                chains = all_chains
        except Exception:
            chains = TECH_CHAINS

    full_count = 0
    for chain_name in chains:
        needs_full = chain_needs_update(chain_name)
        if needs_full and full_count >= 3:
            continue
        update_chain_kb(chain_name, force_full=needs_full)
        if needs_full:
            full_count += 1


def ingest_shendu_serenity_inject(days: int = 60) -> list[str]:
    """消费 shendu JSON 中的 serenity_inject 字段。

    扫描 shendu JSON，提取 chains_to_update 和 expectation_gap_signals，
    触发对应产业链的 Serenity 更新。

    Returns: 被触发更新的产业链列表
    """
    import json, re
    from datetime import date, timedelta

    shendu_dir = REPORT_DIR / "shendu"
    if not shendu_dir.exists():
        return []

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    triggered = []

    for fp in sorted(shendu_dir.iterdir()):
        if not fp.name.startswith("shendu_2026") or fp.name.startswith("shendu__"):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("date", "") < cutoff:
            continue

        si = data.get("serenity_inject", {})
        chains_to_update = si.get("chains_to_update", [])
        gap_signals = si.get("expectation_gap_signals", [])

        for chain in chains_to_update:
            if chain not in triggered:
                triggered.append(chain)
                print(f"  [shendu→Serenity] {chain} 触发更新 "
                      f"({len(gap_signals)} 预期差信号)")
        # 将预期差信号追加到 analysis_log
        if gap_signals:
            for gs in gap_signals:
                chain_seg = gs.get("chain_segment", "")
                gap_type = gs.get("gap_type", "")
                detail = gs.get("detail", "")[:200]
                print(f"    gap: [{gap_type}] {chain_seg}: {detail[:80]}")

    return list(set(triggered))


def update_stock_scores_only():
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT code, name, chain_name FROM stock_chokepoint"
        ).fetchall()

    if not rows:
        print("  暂无已分析标的")
        return

    from engine_serenity import validate_with_fe
    chain_groups: dict[str, list[str]] = {}
    for r in rows:
        chain_groups.setdefault(r["chain_name"], []).append(r["code"])

    for chain_name, codes in chain_groups.items():
        unique_codes = list(dict.fromkeys(codes))
        print(f"  更新 FEV: {chain_name} ({len(unique_codes)} 标的)")
        result = validate_with_fe(unique_codes, chain_name)
        if result:
            stocks = _parse_layer3_scores(result, chain_name)
            if stocks:
                save_stock_scores(chain_name, stocks)


# ============================================================
# 简易解析 helper
# ============================================================

def _parse_layer1_scores(text: str) -> list[dict]:
    import re
    segments = []
    in_table = False
    score_col = -1
    for line in text.split("\n"):
        line = line.strip()
        if ("卡脖子评分" in line or "卡脖子风险" in line) and line.startswith("|"):
            header_parts = [p.strip() for p in line.split("|")]
            header_parts = [p for p in header_parts if p]
            for i, h in enumerate(header_parts):
                if "评分" in h or "卡脖子" in h:
                    score_col = i
                    break
            in_table = True
            continue
        if in_table and line.startswith("|") and not line.startswith("|-"):
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p]
            if len(parts) >= 4:
                idx = score_col if 0 <= score_col < len(parts) else max(0, len(parts) - 2)
                score_str = parts[idx]
                score_match = re.search(r"(\d+)", score_str)
                score = int(score_match.group(1)) if score_match else 0
                if score > 10:
                    score = min(10, score // 10)
                segments.append({
                    "tier": parts[0],
                    "segment": parts[1] if len(parts) >= 5 else parts[0],
                    "chokepoint_score": score,
                    "supply_status": parts[2] if len(parts) >= 5 else "",
                })
        elif in_table and not line.startswith("|"):
            in_table = False
    return segments


def _parse_layer3_scores(text: str, chain_name: str) -> list[dict]:
    """从 LLM 叙事输出中提取 FEV 评分。

    LLM 输出格式（非表格）：
        #### 1. 天岳先进 (688234)
        - **FEV 总分**：F(6/10) + E(8/10) + V(3/10) = **17/30**

    也兜底处理 LLM 回显的 Markdown 表格。
    """
    import re
    stocks = []
    # 按“数字. 名称 (代码)”分割各标的段落
    sections = re.split(r"\n(?=(?:#{1,4}\s+)?\d{1,2}\.\s*\S.*?\(?\d{6}\)?)", text)
    for sec in sections:
        code_match = re.search(r"(\d{6})", sec)
        if not code_match:
            continue
        code = code_match.group(1)
        name_match = re.search(rf"(\S+).*?{code}", sec)
        name = name_match.group(1).strip("# *") if name_match else code

        f_score = e_score = v_score = fev_total = 0
        # 优先匹配 "FEV 总分：F(6/10) + E(8/10) + V(3/10) = 17/30"
        fev_line_match = re.search(
            r"FEV\s*总分.*?[：:]\s*F\s*\(?\s*(\d+)\s*/\s*10.*?E\s*\(?\s*(\d+)\s*/\s*10.*?V\s*\(?\s*(\d+)\s*/\s*10.*?[=＝]\s*\*{0,2}(\d+)",
            sec
        )
        if fev_line_match:
            f_score = int(fev_line_match.group(1))
            e_score = int(fev_line_match.group(2))
            v_score = int(fev_line_match.group(3))
            fev_total = int(fev_line_match.group(4))
        else:
            # fallback: 找独立的 **F(X/10)** / **E(Y/10)** / **V(Z/10)** 行
            fm = re.search(r"\bF\s*[（(]\s*(\d+)\s*/\s*10", sec)
            em = re.search(r"\bE\s*[（(]\s*(\d+)\s*/\s*10", sec)
            vm = re.search(r"\bV\s*[（(]\s*(\d+)\s*/\s*10", sec)
            if fm and em and vm:
                f_score = int(fm.group(1))
                e_score = int(em.group(1))
                v_score = int(vm.group(1))
                # 试试从文本里找 total
                tm = re.search(r"[=＝]\s*\*{0,2}(\d+)\s*/\s*30", sec)
                fev_total = int(tm.group(1)) if tm else f_score + e_score + v_score

        if f_score == 0 and e_score == 0 and v_score == 0:
            continue

        stocks.append({
            "code": code,
            "name": name,
            "chain_name": chain_name,
            "chokepoint_score": 0,
            "f_score": f_score,
            "e_score": e_score,
            "v_score": v_score,
            "fev_total": fev_total,
            "scene": "",
        })

    # 兜底：如果叙事解析没结果，尝试解析 LLM 回显的注入表格
    if not stocks:
        in_table = False
        f_col = e_col = v_col = fev_col = -1
        for line in text.split("\n"):
            line = line.strip()
            if "FEV" in line and ("F（" in line or "总分" in line or ("|" in line and "F" in line)):
                header_parts = [p.strip() for p in line.split("|")]
                header_parts = [p for p in header_parts if p]
                for i, h in enumerate(header_parts):
                    hl = h.lower()
                    if re.search(r"\bf\b", hl) and "fe" not in hl:
                        f_col = i
                    elif re.search(r"\be\b", hl) and "fe" not in hl:
                        e_col = i
                    elif re.search(r"\bv\b", hl) and "fe" not in hl:
                        v_col = i
                    elif "总分" in h or "fe" in hl.lower():
                        fev_col = i
                in_table = True
                continue
            if in_table and line.startswith("|") and not line.startswith("|-"):
                parts = [p.strip() for p in line.split("|")]
                parts = [p for p in parts if p]
                if len(parts) >= 5:
                    code_m = re.search(r"(\d{6})", line)
                    if code_m:
                        def _col(idx, max_val=10):
                            if 0 <= idx < len(parts):
                                v = _try_int(parts[idx])
                                return v if v <= max_val else 0
                            return 0
                        f_s = _col(f_col) if f_col >= 0 else _try_int(parts[-4])
                        e_s = _col(e_col) if e_col >= 0 else _try_int(parts[-3])
                        v_s = _col(v_col) if v_col >= 0 else _try_int(parts[-2])
                        fev_t = _col(fev_col, 30) if fev_col >= 0 else _try_int(parts[-1].split("/")[0] if "/" in parts[-1] else parts[-1])
                        if fev_t == 0 and f_s > 0:
                            fev_t = f_s + e_s + v_s
                        if f_s == 0 and e_s == 0 and v_s == 0:
                            continue
                        stocks.append({
                            "code": code_m.group(1),
                            "name": parts[0].replace(code_m.group(1), "").strip(" *"),
                            "chain_name": chain_name,
                            "chokepoint_score": 0,
                            "f_score": f_s,
                            "e_score": e_s,
                            "v_score": v_s,
                            "fev_total": fev_t,
                            "scene": "",
                        })
            elif in_table and not line.startswith("|"):
                in_table = False

    return stocks


def _try_int(s: str) -> int:
    import re
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else 0


def _extract_codes(text: str) -> str:
    import data
    return ",".join(sorted(data.extract_codes_from_text(text)))


def _extract_fev_top(text: str) -> str:
    import re
    pairs = re.findall(r"(\d{6})\D+(\d+)\s*分", text)
    pairs.sort(key=lambda x: int(x[1]), reverse=True)
    return ",".join(f"{c}({s}分)" for c, s in pairs[:3])


# ============================================================
# CLI
# ============================================================

def _main():
    import argparse
    p = argparse.ArgumentParser(description="Serenity 产业链卡脖子知识库")
    p.add_argument("--init", action="store_true", help="初始化数据库")
    p.add_argument("--chain", type=str, help="对指定产业链跑全量分析")
    p.add_argument("--update-all", action="store_true", help="批量增量更新")
    p.add_argument("--update-stocks", action="store_true", help="仅更新 FEV 评分")
    p.add_argument("--list", action="store_true", help="列出已分析产业链")
    p.add_argument("--report", type=str, help="输出某链最新 Markdown 报告路径")
    p.add_argument("--force-full", action="store_true", help="配合 --chain 强制全量")

    args = p.parse_args()

    if args.init:
        init_db()
        print("✅ serenity.db 初始化完成")
        return

    init_db()

    if args.chain:
        update_chain_kb(args.chain, force_full=args.force_full)
    elif args.update_all:
        batch_update_all()
    elif args.update_stocks:
        update_stock_scores_only()
    elif args.list:
        for s in get_all_chain_summary():
            print(f"  {s['chain_name']:12s} 卡脖子={s['max_score']:2d}  环节={s['segment_count']:2d}  更新={s['last_updated']}")
    elif args.report:
        analysis = get_latest_analysis(args.report)
        if analysis and analysis.get("full_report_path"):
            print(Path(analysis["full_report_path"]).read_text(encoding="utf-8"))
        else:
            print(f"  {args.report}: 暂无报告")
    else:
        p.print_help()


if __name__ == "__main__":
    _main()
