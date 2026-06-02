"""BOM 产业链知识库 — SQLite 持久化"""
import json
import sqlite3
from datetime import datetime

from bom_analyzer.config import DB_PATH


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bom_chains (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                industry    TEXT NOT NULL,
                tier        TEXT NOT NULL,
                segment     TEXT NOT NULL,
                description TEXT,
                is_3h       INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                UNIQUE(industry, tier, segment)
            );
            CREATE TABLE IF NOT EXISTS bom_leaders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chain_id      INTEGER REFERENCES bom_chains(id),
                stock_code    TEXT NOT NULL,
                stock_name    TEXT NOT NULL,
                rank           INTEGER,
                moat_tech     INTEGER,
                moat_cost     INTEGER,
                moat_scale    INTEGER,
                moat_brand    INTEGER,
                moat_switch   INTEGER,
                moat_network  INTEGER,
                moat_total    INTEGER,
                analysis_json TEXT,
                updated_at    TEXT NOT NULL,
                UNIQUE(chain_id, stock_code)
            );
            CREATE INDEX IF NOT EXISTS idx_chain_industry ON bom_chains(industry);
            CREATE INDEX IF NOT EXISTS idx_chain_3h ON bom_chains(is_3h);
            CREATE INDEX IF NOT EXISTS idx_leader_chain ON bom_leaders(chain_id);
            CREATE INDEX IF NOT EXISTS idx_leader_code ON bom_leaders(stock_code);

            CREATE TABLE IF NOT EXISTS industry_snapshot (
                date       TEXT NOT NULL,
                name       TEXT NOT NULL,
                rank        INTEGER,
                change_pct  REAL,
                score       REAL,
                UNIQUE(date, name)
            );
            CREATE INDEX IF NOT EXISTS idx_snap_date ON industry_snapshot(date);
        """)


def save_snapshot(date_str: str, industries: list[dict]):
    with _conn() as conn:
        for ind in industries:
            conn.execute(
                "INSERT OR REPLACE INTO industry_snapshot "
                "(date, name, rank, change_pct, score) VALUES (?,?,?,?,?)",
                (date_str, ind["name"], ind.get("rank", 0),
                 ind.get("change_pct", 0), ind.get("score", 0)),
            )


def query_snapshot(date_str: str) -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name, rank, change_pct, score FROM industry_snapshot "
            "WHERE date = ?", (date_str,)
        ).fetchall()
        return {r["name"]: {"rank": r["rank"], "change_pct": r["change_pct"],
                            "score": r["score"]} for r in rows}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def save_chain(industry: str, segments: list[dict]) -> list[int]:
    ids = []
    with _conn() as conn:
        for seg in segments:
            row = conn.execute(
                "INSERT OR REPLACE INTO bom_chains "
                "(industry, tier, segment, description, is_3h, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (industry, seg.get("tier", ""), seg.get("name", ""),
                 seg.get("description", ""), 1 if seg.get("is_3h") else 0, _now()),
            )
            ids.append(row.lastrowid)
    return ids


def save_leaders(chain_id: int, leaders: list[dict]):
    with _conn() as conn:
        for ldr in leaders:
            s = ldr.get("moat_scores", {})
            conn.execute(
                "INSERT OR REPLACE INTO bom_leaders "
                "(chain_id, stock_code, stock_name, rank, "
                "moat_tech, moat_cost, moat_scale, moat_brand, moat_switch, moat_network, "
                "moat_total, analysis_json, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (chain_id, ldr.get("code", ""), ldr.get("name", ""),
                 ldr.get("rank", 0),
                 s.get("tech", 0), s.get("cost", 0), s.get("scale", 0),
                 s.get("brand", 0), s.get("switch_cost", 0), s.get("network", 0),
                 s.get("tech",0)+s.get("cost",0)+s.get("scale",0)+
                 s.get("brand",0)+s.get("switch_cost",0)+s.get("network",0),
                 json.dumps(ldr, ensure_ascii=False), _now()),
            )


def query_industry(industry: str) -> dict:
    with _conn() as conn:
        chains = conn.execute(
            "SELECT * FROM bom_chains WHERE industry = ? ORDER BY tier, segment",
            (industry,)
        ).fetchall()
        result: dict = {"industry": industry, "segments": [], "leaders": []}
        for c in chains:
            seg = dict(c)
            leaders = conn.execute(
                "SELECT * FROM bom_leaders WHERE chain_id = ? ORDER BY rank",
                (c["id"],)
            ).fetchall()
            seg["leaders"] = [dict(l) for l in leaders]
            result["segments"].append(seg)
            result["leaders"].extend(seg["leaders"])
        return result


def list_industries() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT industry, MAX(created_at) as latest "
            "FROM bom_chains GROUP BY industry ORDER BY latest DESC"
        ).fetchall()
        return [r["industry"] for r in rows]


def recent_industries(days: int = 3) -> set[str]:
    """近 N 天内已分析的行业名集合。"""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT industry FROM bom_chains WHERE created_at >= ?",
            (cutoff + " 00:00",)
        ).fetchall()
        return {r["industry"] for r in rows}


def clear_industry(industry: str):
    with _conn() as conn:
        chain_ids = conn.execute(
            "SELECT id FROM bom_chains WHERE industry = ?", (industry,)
        ).fetchall()
        for c in chain_ids:
            conn.execute("DELETE FROM bom_leaders WHERE chain_id = ?", (c["id"],))
        conn.execute("DELETE FROM bom_chains WHERE industry = ?", (industry,))
