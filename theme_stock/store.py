"""SQLite 数据层 — 建表 + CRUD"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "theme_stock.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

TZ8 = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(TZ8).strftime("%Y-%m-%d %H:%M")


def _today() -> str:
    return datetime.now(TZ8).strftime("%Y-%m-%d")


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS alias_map (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        alias       TEXT NOT NULL UNIQUE,
        canonical   TEXT NOT NULL,
        source      TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS chain_map (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        industry     TEXT NOT NULL,
        tier         TEXT NOT NULL,
        segment      TEXT NOT NULL,
        code         TEXT NOT NULL,
        name         TEXT NOT NULL,
        market       TEXT NOT NULL DEFAULT 'A',
        map_type     TEXT DEFAULT 'chain',
        role         TEXT,
        source       TEXT NOT NULL,
        source_ver   TEXT,
        confidence   TEXT DEFAULT 'medium',
        is_verified  INTEGER DEFAULT 0,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL,
        UNIQUE(industry, tier, segment, code)
    )""",

    """CREATE TABLE IF NOT EXISTS concept_index (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT NOT NULL,
        name        TEXT NOT NULL,
        market      TEXT NOT NULL DEFAULT 'A',
        concept     TEXT NOT NULL,
        source      TEXT NOT NULL,
        weight      REAL DEFAULT 1.0,
        updated_at  TEXT NOT NULL
    )""",

    """CREATE TABLE IF NOT EXISTS stock_depth (
        code           TEXT NOT NULL,
        market         TEXT NOT NULL DEFAULT 'A',
        name           TEXT NOT NULL,
        industry_l1    TEXT,
        moat_total     INTEGER,
        moat_detail    TEXT,
        roe_3y         TEXT,
        gross_margin   REAL,
        rev_cagr_3y    REAL,
        eps_cagr_3y    REAL,
        rd_ratio       REAL,
        tier_label     TEXT,
        substitution   TEXT,
        capacity       TEXT,
        updated_at     TEXT NOT NULL,
        PRIMARY KEY(code, market)
    )""",

    """CREATE TABLE IF NOT EXISTS qual_signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        code         TEXT NOT NULL,
        market       TEXT NOT NULL DEFAULT 'A',
        theme        TEXT NOT NULL,
        signal_type  TEXT NOT NULL,
        direction    TEXT NOT NULL,
        strength     REAL NOT NULL,
        detail       TEXT,
        source_url   TEXT,
        signal_date  TEXT NOT NULL,
        expires_at   TEXT,
        created_at   TEXT NOT NULL
    )""",

    """CREATE TABLE IF NOT EXISTS mkt_feedback (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        code         TEXT NOT NULL,
        market       TEXT NOT NULL DEFAULT 'A',
        theme        TEXT NOT NULL,
        trade_date   TEXT NOT NULL,
        stock_chg    REAL,
        theme_chg    REAL,
        flow_yn      REAL,
        theme_flow   REAL,
        deviation    REAL,
        flag         TEXT,
        created_at   TEXT NOT NULL
    )""",

    """CREATE TABLE IF NOT EXISTS confidence_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        code         TEXT NOT NULL,
        market       TEXT NOT NULL DEFAULT 'A',
        theme        TEXT NOT NULL,
        old_score    REAL,
        new_score    REAL,
        reason       TEXT,
        changed_by   TEXT,
        changed_at   TEXT NOT NULL
    )""",

    "CREATE INDEX IF NOT EXISTS idx_alias_canon ON alias_map(canonical)",
    "CREATE INDEX IF NOT EXISTS idx_cm_industry ON chain_map(industry)",
    "CREATE INDEX IF NOT EXISTS idx_cm_code ON chain_map(code)",
    "CREATE INDEX IF NOT EXISTS idx_cm_market ON chain_map(market)",
    "CREATE INDEX IF NOT EXISTS idx_ci_code ON concept_index(code)",
    "CREATE INDEX IF NOT EXISTS idx_ci_concept ON concept_index(concept)",
    "CREATE INDEX IF NOT EXISTS idx_ci_market ON concept_index(market)",
    "CREATE INDEX IF NOT EXISTS idx_qs_code_theme ON qual_signals(code, theme)",
    "CREATE INDEX IF NOT EXISTS idx_mf_code_theme ON mkt_feedback(code, theme)",
    "CREATE INDEX IF NOT EXISTS idx_cl_code_theme ON confidence_log(code, theme)",
]


class ThemeStockStore:
    def __init__(self, db_path: Path | None = None):
        self._path = db_path or DB_PATH
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_db(self):
        conn = self._get_conn()
        for stmt in _SCHEMA:
            conn.execute(stmt)
        # Migration: add map_type if missing
        try:
            conn.execute("ALTER TABLE chain_map ADD COLUMN map_type TEXT DEFAULT 'chain'")
        except sqlite3.OperationalError:
            pass
        conn.commit()

    def tag_buckets(self, bucket_industries: set[str]):
        """将指定产业标记为 bucket (筛选筐), 其余保持 chain。"""
        conn = self._get_conn()
        # Reset all to chain first
        conn.execute("UPDATE chain_map SET map_type = 'chain'")
        # Tag buckets
        for ind in bucket_industries:
            conn.execute("UPDATE chain_map SET map_type = 'bucket' WHERE industry = ?", (ind,))
        conn.commit()

    def get_map_types(self) -> dict[str, str]:
        """返回 {industry: map_type}"""
        cur = self._get_conn().execute("SELECT DISTINCT industry, map_type FROM chain_map")
        return {r["industry"]: r["map_type"] for r in cur}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ============================================================
    # alias_map
    # ============================================================

    def add_alias(self, alias: str, canonical: str, source: str = ""):
        self._get_conn().execute(
            "INSERT OR REPLACE INTO alias_map (alias, canonical, source) VALUES (?, ?, ?)",
            (alias.strip(), canonical.strip(), source),
        )
        self._get_conn().commit()

    def resolve_alias(self, alias: str) -> str | None:
        cur = self._get_conn().execute(
            "SELECT canonical FROM alias_map WHERE alias = ?", (alias.strip(),)
        )
        row = cur.fetchone()
        return row["canonical"] if row else None

    def load_alias_map(self) -> dict[str, str]:
        cur = self._get_conn().execute("SELECT alias, canonical FROM alias_map")
        return {row["alias"]: row["canonical"] for row in cur}

    # ============================================================
    # chain_map
    # ============================================================

    def upsert_chain(self, industry: str, tier: str, segment: str,
                     code: str, name: str, market: str = "A",
                     role: str = "", source: str = "", source_ver: str = "",
                     confidence: str = "medium", is_verified: int = 0):
        now = _now()
        self._get_conn().execute(
            """INSERT OR REPLACE INTO chain_map
               (industry, tier, segment, code, name, market, role, source, source_ver,
                confidence, is_verified, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                 COALESCE((SELECT created_at FROM chain_map
                  WHERE industry=? AND tier=? AND segment=? AND code=?), ?), ?)""",
            (industry, tier, segment, code, name, market, role, source, source_ver,
             confidence, is_verified,
             industry, tier, segment, code, now, now),
        )
        self._get_conn().commit()

    def upsert_chain_batch(self, rows: list[dict]):
        now = _now()
        conn = self._get_conn()
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO chain_map
                   (industry, tier, segment, code, name, market, role, source, source_ver,
                    confidence, is_verified, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                     COALESCE((SELECT created_at FROM chain_map
                      WHERE industry=? AND tier=? AND segment=? AND code=?), ?), ?)""",
                (r["industry"], r["tier"], r["segment"], r["code"], r["name"],
                 r.get("market", "A"), r.get("role", ""), r.get("source", ""),
                 r.get("source_ver", ""), r.get("confidence", "medium"),
                 r.get("is_verified", 0),
                 r["industry"], r["tier"], r["segment"], r["code"], now, now),
            )
        conn.commit()

    def query_chain_by_industry(self, industry: str,
                                 market: str | None = None) -> list[dict]:
        if market:
            cur = self._get_conn().execute(
                "SELECT * FROM chain_map WHERE industry=? AND market=? ORDER BY tier, segment",
                (industry, market),
            )
        else:
            cur = self._get_conn().execute(
                "SELECT * FROM chain_map WHERE industry=? ORDER BY market, tier, segment",
                (industry,),
            )
        return [dict(r) for r in cur]

    def query_chain_segments(self, industry: str) -> dict[str, list[str]]:
        cur = self._get_conn().execute(
            "SELECT DISTINCT tier, segment FROM chain_map WHERE industry=? ORDER BY tier",
            (industry,),
        )
        result: dict[str, list[str]] = {}
        for row in cur:
            result.setdefault(row["tier"], []).append(row["segment"])
        return result

    def search_chain(self, keyword: str, limit: int = 50) -> list[dict]:
        cur = self._get_conn().execute(
            """SELECT DISTINCT industry, tier, segment FROM chain_map
               WHERE industry LIKE ? OR segment LIKE ?
               ORDER BY industry LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", limit),
        )
        return [dict(r) for r in cur]

    def get_chain_stats(self) -> dict:
        conn = self._get_conn()
        ind = conn.execute("SELECT COUNT(DISTINCT industry) FROM chain_map").fetchone()[0]
        codes = conn.execute("SELECT COUNT(DISTINCT code||market) FROM chain_map").fetchone()[0]
        ver = conn.execute("SELECT COUNT(*) FROM chain_map WHERE is_verified=1").fetchone()[0]
        return {"industries": ind, "stocks": codes, "verified": ver}

    # ============================================================
    # concept_index
    # ============================================================

    def upsert_concept_batch(self, rows: list[tuple]):
        """[(code, name, market, concept, source, weight), ...]"""
        now = _now()
        self._get_conn().executemany(
            """INSERT OR REPLACE INTO concept_index
               (code, name, market, concept, source, weight, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(c, n, m, ct, s, w, now) for c, n, m, ct, s, w in rows],
        )
        self._get_conn().commit()

    def query_concept_stocks(self, concept: str, market: str | None = None,
                             limit: int = 100) -> list[dict]:
        if market:
            cur = self._get_conn().execute(
                """SELECT DISTINCT code, name, market, concept, source, MAX(weight) as weight
                   FROM concept_index WHERE concept=? AND market=?
                   GROUP BY code ORDER BY weight DESC LIMIT ?""",
                (concept, market, limit),
            )
        else:
            cur = self._get_conn().execute(
                """SELECT DISTINCT code, name, market, concept, source, MAX(weight) as weight
                   FROM concept_index WHERE concept=?
                   GROUP BY code, market ORDER BY weight DESC LIMIT ?""",
                (concept, limit),
            )
        return [dict(r) for r in cur]

    def get_concept_stats(self) -> dict:
        conn = self._get_conn()
        cs = conn.execute("SELECT COUNT(DISTINCT concept) FROM concept_index").fetchone()[0]
        st = conn.execute("SELECT COUNT(DISTINCT code||market) FROM concept_index").fetchone()[0]
        return {"concepts": cs, "stocks": st}

    # ============================================================
    # stock_depth
    # ============================================================

    def upsert_depth(self, code: str, name: str, market: str = "A", **kwargs):
        fields = ["code", "market", "name", "industry_l1", "moat_total", "moat_detail",
                  "roe_3y", "gross_margin", "rev_cagr_3y", "eps_cagr_3y", "rd_ratio",
                  "tier_label", "substitution", "capacity", "updated_at"]
        vals = {"code": code, "market": market, "name": name, "updated_at": _now()}
        for f in fields:
            if f in kwargs and f not in vals:
                vals[f] = kwargs[f]
        placeholders = ", ".join(vals.keys())
        qs = ", ".join("?" for _ in vals)
        self._get_conn().execute(
            f"INSERT OR REPLACE INTO stock_depth ({placeholders}) VALUES ({qs})",
            list(vals.values()),
        )
        self._get_conn().commit()

    def upsert_depth_batch(self, rows: list[dict]):
        now = _now()
        conn = self._get_conn()
        for r in rows:
            r["updated_at"] = now
            keys = list(r.keys())
            placeholders = ", ".join(keys)
            qs = ", ".join("?" for _ in keys)
            conn.execute(
                f"INSERT OR REPLACE INTO stock_depth ({placeholders}) VALUES ({qs})",
                [r[k] for k in keys],
            )
        conn.commit()

    def get_depth(self, code: str, market: str = "A") -> dict | None:
        cur = self._get_conn().execute(
            "SELECT * FROM stock_depth WHERE code=? AND market=?", (code, market)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    # ============================================================
    # qual_signals
    # ============================================================

    def add_signal(self, code: str, theme: str, signal_type: str, direction: str,
                   strength: float, detail: str = "", source_url: str = "",
                   market: str = "A", ttl_days: int = 7):
        now = _now()
        sd = _today()
        exp = (datetime.now(TZ8) + timedelta(days=ttl_days)).strftime("%Y-%m-%d")
        self._get_conn().execute(
            """INSERT INTO qual_signals
               (code, market, theme, signal_type, direction, strength, detail, source_url,
                signal_date, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, market, theme, signal_type, direction, strength, detail, source_url,
             sd, exp, now),
        )
        self._get_conn().commit()

    def get_active_signals(self, code: str, theme: str, market: str = "A") -> list[dict]:
        cur = self._get_conn().execute(
            """SELECT * FROM qual_signals
               WHERE code=? AND market=? AND theme=? AND expires_at >= ?
               ORDER BY signal_date DESC""",
            (code, market, theme, _today()),
        )
        return [dict(r) for r in cur]

    def calc_qual_bonus(self, code: str, theme: str, market: str = "A") -> float:
        signals = self.get_active_signals(code, theme, market)
        if not signals:
            return 0.0
        today_date = datetime.now(TZ8).date()
        total = 0.0
        for s in signals:
            try:
                sd = datetime.strptime(s["signal_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            days = (today_date - sd).days
            try:
                exp_dt = datetime.strptime(s["expires_at"], "%Y-%m-%d").date()
                ttl = (exp_dt - sd).days
            except (ValueError, TypeError):
                ttl = 7
            decay = max(0, 1 - days / max(ttl, 1))
            total += s["strength"] * decay
        return total / 10

    # ============================================================
    # mkt_feedback
    # ============================================================

    def add_feedback(self, code: str, theme: str, trade_date: str,
                     stock_chg: float, theme_chg: float,
                     flow_yn: float = 0, theme_flow: float = 0,
                     deviation: float = 0, flag: str = "", market: str = "A"):
        self._get_conn().execute(
            """INSERT INTO mkt_feedback
               (code, market, theme, trade_date, stock_chg, theme_chg,
                flow_yn, theme_flow, deviation, flag, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, market, theme, trade_date, stock_chg, theme_chg,
             flow_yn, theme_flow, deviation, flag, _now()),
        )
        self._get_conn().commit()

    def get_recent_feedback(self, code: str, theme: str, days: int = 5,
                            market: str = "A") -> list[dict]:
        since = (datetime.now(TZ8) - timedelta(days=days)).strftime("%Y-%m-%d")
        cur = self._get_conn().execute(
            """SELECT * FROM mkt_feedback
               WHERE code=? AND market=? AND theme=? AND trade_date >= ?
               ORDER BY trade_date DESC""",
            (code, market, theme, since),
        )
        return [dict(r) for r in cur]

    def get_diverged_stocks(self, theme: str, min_days: int = 3) -> list[dict]:
        cur = self._get_conn().execute(
            """SELECT code, market, COUNT(*) as div_count
               FROM mkt_feedback WHERE theme=? AND flag='diverged'
               GROUP BY code, market HAVING div_count >= ?
               ORDER BY div_count DESC""",
            (theme, min_days),
        )
        return [dict(r) for r in cur]

    # ============================================================
    # confidence_log
    # ============================================================

    def log_confidence_change(self, code: str, theme: str, old_score: float,
                              new_score: float, reason: str, changed_by: str = "system",
                              market: str = "A"):
        self._get_conn().execute(
            """INSERT INTO confidence_log
               (code, market, theme, old_score, new_score, reason, changed_by, changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, market, theme, old_score, new_score, reason, changed_by, _now()),
        )
        self._get_conn().commit()
