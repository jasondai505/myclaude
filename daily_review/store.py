"""每日复盘系统 - SQLite 持久化层（题材跟踪 + 市场快照）"""
import json
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from config import DB_PATH


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        # migrate: add total_amount_yi if missing
        try:
            conn.execute("SELECT total_amount_yi FROM market_snapshot LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE market_snapshot ADD COLUMN total_amount_yi REAL")
        # migrate: A1 — 10 日涨跌停趋势（排除 ST）
        for col in ("limit_up_count", "limit_up_2plus", "limit_down_count"):
            try:
                conn.execute(f"SELECT {col} FROM market_snapshot LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute(f"ALTER TABLE market_snapshot ADD COLUMN {col} INTEGER")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS theme_daily (
                date       TEXT NOT NULL,
                theme      TEXT NOT NULL,
                count      INTEGER DEFAULT 0,
                stocks     TEXT DEFAULT '',
                PRIMARY KEY (date, theme)
            );
            CREATE TABLE IF NOT EXISTS market_snapshot (
                date       TEXT PRIMARY KEY,
                sh_close   REAL,
                sh_chg_pct REAL,
                sz_close   REAL,
                sz_chg_pct REAL,
                cyb_close  REAL,
                cyb_chg_pct REAL,
                north_hgt  REAL,
                north_sgt  REAL,
                up_count   INTEGER,
                down_count INTEGER,
                total_amount_yi REAL
            );
            CREATE TABLE IF NOT EXISTS theme_level (
                theme             TEXT PRIMARY KEY,
                level             INTEGER DEFAULT 1,
                consecutive_days  INTEGER DEFAULT 1,
                first_seen        TEXT,
                last_seen         TEXT,
                cumulative_stocks INTEGER DEFAULT 0,
                updated_at        TEXT
            );
            CREATE TABLE IF NOT EXISTS valuation_cache (
                code       TEXT NOT NULL,
                data_type  TEXT NOT NULL,
                data_json  TEXT,
                updated_at TEXT,
                PRIMARY KEY (code, data_type)
            );
            CREATE TABLE IF NOT EXISTS sector_rotation_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                date           TEXT NOT NULL,
                row_type       TEXT NOT NULL,
                score          REAL,
                sector         TEXT DEFAULT '',
                status_code    TEXT DEFAULT '',
                leader_stock   TEXT DEFAULT '',
                auction_leader TEXT DEFAULT '',
                stocks_json    TEXT DEFAULT '[]',
                volume_ratio   REAL,
                raw_data       TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_srl_date ON sector_rotation_log(date);
            CREATE INDEX IF NOT EXISTS idx_srl_type ON sector_rotation_log(row_type);
            CREATE INDEX IF NOT EXISTS idx_srl_sector ON sector_rotation_log(sector);
        """)


# ---- 题材持久化 ----


def save_themes(date: str, theme_counts: dict[str, dict]):
    """
    theme_counts: {theme_name: {"count": N, "stocks": "code1,code2,..."}}
    """
    with _conn() as conn:
        for theme, info in theme_counts.items():
            conn.execute(
                "INSERT OR REPLACE INTO theme_daily (date, theme, count, stocks) VALUES (?,?,?,?)",
                (date, theme, info.get("count", 0), info.get("stocks", "")),
            )


def load_themes(date: str) -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT theme, count, stocks FROM theme_daily WHERE date = ?", (date,)
        ).fetchall()
    return {r["theme"]: {"count": r["count"], "stocks": r["stocks"]} for r in rows}


def load_themes_range(start_date: str, end_date: str) -> dict[str, list[dict]]:
    """返回 {theme: [{date, count, stocks}, ...]} 按日期升序"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT date, theme, count, stocks FROM theme_daily "
            "WHERE date BETWEEN ? AND ? ORDER BY date",
            (start_date, end_date),
        ).fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        result.setdefault(r["theme"], []).append(
            {"date": r["date"], "count": r["count"], "stocks": r["stocks"]}
        )
    return result


def get_theme_stock_frequency(theme: str, end_date: str, days: int = 30) -> dict[str, dict]:
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        rows = conn.execute(
            "SELECT date, stocks FROM theme_daily WHERE theme = ? AND date BETWEEN ? AND ?",
            (theme, start, end_date),
        ).fetchall()
    freq: dict[str, dict] = {}
    for r in rows:
        codes = [c.strip() for c in r["stocks"].split(",") if c.strip()]
        for code in codes:
            if code not in freq:
                freq[code] = {"freq": 0, "dates": []}
            freq[code]["freq"] += 1
            freq[code]["dates"].append(r["date"])
    return freq


def get_theme_stock_pool(end_date: str, lookback_days: int = 10) -> dict[str, dict[str, dict]]:
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
    with _conn() as conn:
        rows = conn.execute(
            "SELECT date, theme, stocks FROM theme_daily WHERE date BETWEEN ? AND ? AND date <= ?",
            (start, end_date, end_date),
        ).fetchall()
    pool: dict[str, dict[str, dict]] = {}
    for r in rows:
        theme = r["theme"]
        dt = r["date"]
        codes = [c.strip() for c in r["stocks"].split(",") if c.strip()]
        if theme not in pool:
            pool[theme] = {}
        for code in codes:
            if code not in pool[theme]:
                pool[theme][code] = {"freq": 0, "first_date": dt, "last_date": dt, "dates": []}
            pool[theme][code]["freq"] += 1
            pool[theme][code]["dates"].append(dt)
            if dt < pool[theme][code]["first_date"]:
                pool[theme][code]["first_date"] = dt
            if dt > pool[theme][code]["last_date"]:
                pool[theme][code]["last_date"] = dt
    return pool


def build_code_to_themes(theme_pool: dict[str, dict[str, dict]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for theme, stocks in theme_pool.items():
        for code in stocks:
            result.setdefault(code, []).append(theme)
    return result


def get_recent_theme_dates(n: int = 5) -> list[str]:
    """获取最近 n 个有数据的交易日"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM theme_daily ORDER BY date DESC LIMIT ?", (n,)
        ).fetchall()
    return [r["date"] for r in reversed(rows)]


# ---- 市场快照持久化 ----

def save_market_snapshot(date: str, data: dict):
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_snapshot "
            "(date, sh_close, sh_chg_pct, sz_close, sz_chg_pct, "
            " cyb_close, cyb_chg_pct, north_hgt, north_sgt, up_count, down_count, "
            " total_amount_yi, limit_up_count, limit_up_2plus, limit_down_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                date,
                data.get("sh_close"), data.get("sh_chg_pct"),
                data.get("sz_close"), data.get("sz_chg_pct"),
                data.get("cyb_close"), data.get("cyb_chg_pct"),
                data.get("north_hgt"), data.get("north_sgt"),
                data.get("up_count"), data.get("down_count"),
                data.get("total_amount_yi"),
                data.get("limit_up_count"), data.get("limit_up_2plus"),
                data.get("limit_down_count"),
            ),
        )


def load_recent_snapshots(n: int = 10) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM market_snapshot ORDER BY date DESC LIMIT ?", (n,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_market_snapshot_history(end_date: str, n: int = 10) -> list[dict]:
    """返回 end_date 当日及之前最多 n 个交易日的 snapshot，按日期升序"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM market_snapshot WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (end_date, n),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---- 题材分级持久化 ----

def save_theme_level(theme: str, level: int, consecutive_days: int,
                     first_seen: str, last_seen: str, cumulative_stocks: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO theme_level "
            "(theme, level, consecutive_days, first_seen, last_seen, cumulative_stocks, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (theme, level, consecutive_days, first_seen, last_seen, cumulative_stocks, now),
        )


def load_theme_levels() -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM theme_level ORDER BY level DESC").fetchall()
    return {r["theme"]: dict(r) for r in rows}


def get_theme_consecutive_days(theme: str, end_date: str, max_lookback: int = 90) -> int:
    """计算题材从 end_date 向前连续出现的天数"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM theme_daily WHERE theme = ? AND date <= ? "
            "ORDER BY date DESC LIMIT ?",
            (theme, end_date, max_lookback),
        ).fetchall()
    if not rows:
        return 0
    dates = [r["date"] for r in rows]
    count = 1
    for i in range(1, len(dates)):
        prev = datetime.strptime(dates[i - 1], "%Y-%m-%d")
        curr = datetime.strptime(dates[i], "%Y-%m-%d")
        gap = (prev - curr).days
        if gap <= 3:
            count += 1
        else:
            break
    return count


def get_theme_cumulative_stocks(theme: str, days: int = 30) -> int:
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        row = conn.execute(
            "SELECT SUM(count) as total FROM theme_daily "
            "WHERE theme = ? AND date BETWEEN ? AND ?",
            (theme, start, end),
        ).fetchone()
    return int(row["total"]) if row and row["total"] else 0


# ---- 估值缓存 ----

def save_valuation_cache(code: str, data_type: str, data_json: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO valuation_cache (code, data_type, data_json, updated_at) "
            "VALUES (?,?,?,?)",
            (code, data_type, data_json, now),
        )


def save_valuation_batch(rows: list[dict], data_type: str = "industry_pe"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        conn.execute("DELETE FROM valuation_cache WHERE data_type = ?", (data_type,))
        conn.executemany(
            "INSERT INTO valuation_cache (code, data_type, data_json, updated_at) "
            "VALUES (?,?,?,?)",
            [(r["code"], data_type, json.dumps(r, ensure_ascii=False), now) for r in rows],
        )


def query_valuation_cache(code: str, data_type: str = "industry_pe") -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT data_json FROM valuation_cache WHERE code = ? AND data_type = ?",
            (code, data_type),
        ).fetchone()
    return json.loads(row["data_json"]) if row else None


def save_scan_results(trade_date: str, candidates: list[dict]):
    with _conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scan_results ("
            "  date TEXT NOT NULL, rank INTEGER, code TEXT, name TEXT, "
            "  industry TEXT, pe REAL, pb REAL, mktcap REAL, "
            "  composite REAL, board_score REAL, val_score REAL, tech_score REAL, "
            "  matched_themes TEXT, "
            "  PRIMARY KEY (date, rank))"
        )
        for i, s in enumerate(candidates, 1):
            conn.execute(
                "INSERT OR REPLACE INTO scan_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trade_date, i, s["code"], s["name"],
                    s.get("industry", ""), s.get("pe", 0), s.get("pb", 0),
                    s.get("mktcap", 0), s.get("composite", 0),
                    s.get("board_score", 0), s.get("val_score", 0),
                    s.get("tech_score", 0),
                    ",".join(s.get("matched_themes", [])),
                ),
            )


def load_valuation_cache(code: str, data_type: str, max_age_days: int = 7) -> str | None:
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        row = conn.execute(
            "SELECT data_json FROM valuation_cache "
            "WHERE code = ? AND data_type = ? AND updated_at >= ?",
            (code, data_type, cutoff),
        ).fetchone()
    return row["data_json"] if row else None


def get_all_cached_codes(data_type: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT code FROM valuation_cache WHERE data_type = ?",
            (data_type,),
        ).fetchall()
    return [r["code"] for r in rows]


# ---- 知识星球持久化 ----

def init_zsxq_table():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS zsxq_topics (
                topic_id   TEXT PRIMARY KEY,
                create_time TEXT NOT NULL,
                author     TEXT,
                title      TEXT,
                text       TEXT,
                topic_type TEXT,
                readers_count  INTEGER DEFAULT 0,
                likes_count    INTEGER DEFAULT 0,
                comments_count INTEGER DEFAULT 0,
                stock_codes TEXT,
                fetched_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_zsxq_create_time ON zsxq_topics(create_time);
        """)


def zsxq_batch_existing(topic_ids: list[str]) -> set[str]:
    if not topic_ids:
        return set()
    with _conn() as conn:
        placeholders = ",".join("?" * len(topic_ids))
        rows = conn.execute(
            f"SELECT topic_id FROM zsxq_topics WHERE topic_id IN ({placeholders})",
            topic_ids,
        ).fetchall()
    return {r["topic_id"] for r in rows}


def save_zsxq_topics_batch(topics: list[dict]):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO zsxq_topics "
            "(topic_id, create_time, author, title, text, topic_type, "
            " readers_count, likes_count, comments_count, stock_codes, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    t["topic_id"], t["create_time"], t.get("author", ""),
                    t.get("title", ""), t.get("text", ""),
                    t.get("topic_type", ""), t.get("readers_count", 0),
                    t.get("likes_count", 0), t.get("comments_count", 0),
                    json.dumps(t.get("stock_codes", []), ensure_ascii=False),
                    now,
                )
                for t in topics
            ],
        )


def search_zsxq(keyword: str = None, code: str = None,
                date_from: str = None, date_to: str = None,
                limit: int = 50) -> list[dict]:
    conditions = []
    params = []
    if keyword:
        conditions.append("(title LIKE ? OR text LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if code:
        conditions.append("stock_codes LIKE ?")
        params.append(f'%"{code}"%')
    if date_from:
        conditions.append("create_time >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("create_time <= ?")
        params.append(date_to + "T23:59:59")

    where = " AND ".join(conditions) if conditions else "1=1"
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM zsxq_topics WHERE {where} ORDER BY create_time DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


def recent_zsxq(days: int = 7, limit: int = 50) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM zsxq_topics WHERE create_time >= ? ORDER BY create_time DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def zsxq_stats() -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "MIN(create_time) as earliest, MAX(create_time) as latest, "
            "COUNT(DISTINCT author) as authors "
            "FROM zsxq_topics"
        ).fetchone()
        type_rows = conn.execute(
            "SELECT topic_type, COUNT(*) as cnt FROM zsxq_topics GROUP BY topic_type"
        ).fetchall()
    return {
        "total": row["total"],
        "earliest": row["earliest"],
        "latest": row["latest"],
        "authors": row["authors"],
        "by_type": {r["topic_type"]: r["cnt"] for r in type_rows},
    }


def zsxq_top_authors(n: int = 10) -> list[tuple[str, int]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT author, COUNT(*) as cnt FROM zsxq_topics "
            "WHERE author != '' GROUP BY author ORDER BY cnt DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [(r["author"], r["cnt"]) for r in rows]


# ---- 研报持久化 ----

def save_research_reports(reports: list[dict]):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS research_reports ("
            "  code TEXT, report_date TEXT, institution TEXT, "
            "  name TEXT, title TEXT, rating TEXT, "
            "  target_price REAL, "
            "  eps_y1 REAL, eps_y2 REAL, eps_y3 REAL, "
            "  industry TEXT, pdf_url TEXT, "
            "  fetched_at TEXT, "
            "  PRIMARY KEY (code, report_date, institution))"
        )
        for r in reports:
            conn.execute(
                "INSERT OR REPLACE INTO research_reports "
                "(code, report_date, institution, name, title, rating, "
                " target_price, eps_y1, eps_y2, eps_y3, industry, pdf_url, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["code"], r["report_date"], r["institution"],
                    r.get("name", ""), r.get("title", ""), r.get("rating", ""),
                    r.get("target_price"), r.get("eps_y1"), r.get("eps_y2"), r.get("eps_y3"),
                    r.get("industry", ""), r.get("pdf_url", ""), now,
                ),
            )


def load_latest_scan_codes() -> list[str]:
    with _conn() as conn:
        try:
            row = conn.execute(
                "SELECT date FROM scan_results ORDER BY date DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return []
        if not row:
            return []
        rows = conn.execute(
            "SELECT code FROM scan_results WHERE date = ? ORDER BY rank",
            (row["date"],),
        ).fetchall()
    return [r["code"] for r in rows]


def save_consensus_snapshot(date: str, aggregated: list[dict],
                            consensus_map: dict, comment_map: dict):
    with _conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS consensus_snapshot ("
            "  date TEXT, code TEXT, name TEXT, "
            "  report_count INTEGER, latest_rating TEXT, "
            "  avg_target_price REAL, "
            "  eps_avg_y1 REAL, eps_avg_y2 REAL, inst_count INTEGER, "
            "  score REAL, inst_participation REAL, "
            "  PRIMARY KEY (date, code))"
        )
        for a in aggregated:
            code = a["code"]
            cons = consensus_map.get(code, {})
            comment = comment_map.get(code, {})
            conn.execute(
                "INSERT OR REPLACE INTO consensus_snapshot "
                "(date, code, name, report_count, latest_rating, "
                " avg_target_price, eps_avg_y1, eps_avg_y2, inst_count, "
                " score, inst_participation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    date, code, a.get("name", ""),
                    a.get("count", 0), a.get("latest_rating", ""),
                    a.get("target_price"),
                    cons.get("eps_avg_y1"), cons.get("eps_avg_y2"),
                    cons.get("inst_count"),
                    comment.get("score"), comment.get("inst_participation"),
                ),
            )


# ============================================================
# 框架二：每日数据源采集（公告 / 新闻 / 互动易 / 状态）
# ============================================================

def init_feeds_tables():
    """初始化每日数据源采集相关表，幂等。"""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS announcements (
                code        TEXT NOT NULL,
                name        TEXT,
                title       TEXT NOT NULL,
                type        TEXT,
                date        TEXT NOT NULL,
                url         TEXT,
                source      TEXT,
                fetched_at  TEXT,
                PRIMARY KEY (code, date, title)
            );
            CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(date);
            CREATE INDEX IF NOT EXISTS idx_ann_code ON announcements(code);

            CREATE TABLE IF NOT EXISTS stock_news (
                code         TEXT NOT NULL,
                title        TEXT NOT NULL,
                content      TEXT,
                source       TEXT,
                publish_time TEXT NOT NULL,
                url          TEXT,
                fetched_at   TEXT,
                PRIMARY KEY (code, publish_time, title)
            );
            CREATE INDEX IF NOT EXISTS idx_news_time ON stock_news(publish_time);
            CREATE INDEX IF NOT EXISTS idx_news_code ON stock_news(code);

            CREATE TABLE IF NOT EXISTS interactions (
                code        TEXT NOT NULL,
                question    TEXT NOT NULL,
                answer      TEXT,
                ask_time    TEXT,
                reply_time  TEXT,
                platform    TEXT,
                fetched_at  TEXT,
                PRIMARY KEY (code, ask_time, question)
            );
            CREATE INDEX IF NOT EXISTS idx_irm_reply ON interactions(reply_time);
            CREATE INDEX IF NOT EXISTS idx_irm_code ON interactions(code);

            CREATE TABLE IF NOT EXISTS collect_status (
                source       TEXT PRIMARY KEY,
                last_date    TEXT,
                last_run_at  TEXT,
                status       TEXT,
                message      TEXT,
                added_count  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS earnings_forecast (
                code          TEXT NOT NULL,
                name          TEXT,
                indicator     TEXT NOT NULL,
                forecast_type TEXT,
                change_desc   TEXT,
                value         REAL,
                change_pct    REAL,
                reason        TEXT,
                prev_value    REAL,
                notice_date   TEXT NOT NULL,
                period        TEXT NOT NULL,
                fetched_at    TEXT,
                PRIMARY KEY (code, period, indicator, notice_date)
            );
            CREATE INDEX IF NOT EXISTS idx_yjyg_notice ON earnings_forecast(notice_date);
            CREATE INDEX IF NOT EXISTS idx_yjyg_code ON earnings_forecast(code);

            CREATE TABLE IF NOT EXISTS earnings_express (
                code          TEXT NOT NULL,
                name          TEXT,
                eps           REAL,
                revenue       REAL,
                revenue_yoy   REAL,
                net_profit    REAL,
                net_profit_yoy REAL,
                bps           REAL,
                roe           REAL,
                industry      TEXT,
                notice_date   TEXT NOT NULL,
                period        TEXT NOT NULL,
                fetched_at    TEXT,
                PRIMARY KEY (code, period, notice_date)
            );
            CREATE INDEX IF NOT EXISTS idx_yjkb_notice ON earnings_express(notice_date);

            CREATE TABLE IF NOT EXISTS inst_survey (
                code         TEXT NOT NULL,
                name         TEXT,
                change_pct   REAL,
                inst_count   INTEGER,
                method       TEXT,
                attendees    TEXT,
                location     TEXT,
                survey_date  TEXT,
                notice_date  TEXT NOT NULL,
                period       TEXT,
                fetched_at   TEXT,
                PRIMARY KEY (code, survey_date, notice_date)
            );
            CREATE INDEX IF NOT EXISTS idx_survey_notice ON inst_survey(notice_date);
            CREATE INDEX IF NOT EXISTS idx_survey_code ON inst_survey(code);

            CREATE TABLE IF NOT EXISTS lockups (
                code         TEXT NOT NULL,
                name         TEXT,
                release_date TEXT NOT NULL,
                type         TEXT,
                shares       REAL,
                ratio        REAL,
                fetched_at   TEXT,
                PRIMARY KEY (code, release_date, type)
            );
            CREATE INDEX IF NOT EXISTS idx_lockup_date ON lockups(release_date);

            CREATE TABLE IF NOT EXISTS eps_forecast (
                code        TEXT NOT NULL,
                name        TEXT,
                year        TEXT NOT NULL,
                eps         REAL,
                max_eps     REAL,
                min_eps     REAL,
                inst_count  INTEGER,
                fetched_at  TEXT,
                PRIMARY KEY (code, year)
            );

            CREATE TABLE IF NOT EXISTS industry_research (
                info_code    TEXT PRIMARY KEY,
                title        TEXT,
                org          TEXT,
                industry     TEXT,
                rating       TEXT,
                publish_date TEXT NOT NULL,
                url          TEXT,
                fetched_at   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_indres_pub ON industry_research(publish_date);

            CREATE TABLE IF NOT EXISTS wechat_articles (
                feed_source  TEXT NOT NULL,
                title        TEXT NOT NULL,
                url          TEXT,
                pub_date     TEXT NOT NULL,
                description  TEXT,
                fetched_at   TEXT,
                analyzed_at  TEXT,
                PRIMARY KEY (feed_source, pub_date, title)
            );
            CREATE INDEX IF NOT EXISTS idx_wechat_pub ON wechat_articles(pub_date);
            CREATE INDEX IF NOT EXISTS idx_wechat_feed ON wechat_articles(feed_source);

            CREATE TABLE IF NOT EXISTS financial_indicators (
                code                TEXT NOT NULL,
                name                TEXT,
                report_date         TEXT NOT NULL,
                roe                 REAL,
                gross_margin        REAL,
                net_margin          REAL,
                debt_ratio          REAL,
                operating_margin    REAL,
                revenue_yoy         REAL,
                profit_yoy          REAL,
                opcash_to_profit    REAL,
                opcash_per_share    REAL,
                current_ratio       REAL,
                quick_ratio         REAL,
                diluted_eps         REAL,
                bv_per_share        REAL,
                asset_turnover      REAL,
                inventory_turnover  REAL,
                receivables_turnover REAL,
                dividend_payout     REAL,
                nav_growth          REAL,
                total_asset_growth  REAL,
                fetched_at          TEXT,
                PRIMARY KEY (code, report_date)
            );
            CREATE INDEX IF NOT EXISTS idx_finind_code ON financial_indicators(code);
            CREATE INDEX IF NOT EXISTS idx_finind_date ON financial_indicators(report_date);
        """)
        try:
            conn.execute("ALTER TABLE wechat_articles ADD COLUMN analyzed_at TEXT")
        except Exception:
            pass


def save_announcements(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO announcements "
            "(code, name, title, type, date, url, source, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    r.get("code", ""), r.get("name", ""),
                    r.get("title", ""), r.get("type", ""),
                    r.get("date", ""), r.get("url", ""),
                    r.get("source", ""), now,
                )
                for r in rows
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    return after - before


def save_stock_news(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM stock_news").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO stock_news "
            "(code, title, content, source, publish_time, url, fetched_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (
                    r.get("code", ""), r.get("title", ""),
                    r.get("content", ""), r.get("source", ""),
                    r.get("publish_time", ""), r.get("url", ""), now,
                )
                for r in rows
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM stock_news").fetchone()[0]
    return after - before


def save_wechat_articles(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM wechat_articles").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO wechat_articles "
            "(feed_source, title, url, pub_date, description, fetched_at) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    r.get("feed_source", ""), r.get("title", ""),
                    r.get("url", ""), r.get("pub_date", ""),
                    r.get("description", ""), now,
                )
                for r in rows
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM wechat_articles").fetchone()[0]
    return after - before


def query_wechat_articles(since: str, until: str = "", unanalyzed_only: bool = True) -> list[dict]:
    where = "WHERE pub_date >= ?"
    params = [since]
    if until:
        where += " AND pub_date < ?"
        params.append(until)
    if unanalyzed_only:
        where += " AND analyzed_at IS NULL"
    sql = f"SELECT * FROM wechat_articles {where} ORDER BY pub_date DESC, feed_source"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def mark_wechat_analyzed(articles: list[dict]):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        for a in articles:
            conn.execute(
                "UPDATE wechat_articles SET analyzed_at = ? "
                "WHERE feed_source = ? AND substr(pub_date, 1, 10) = ? AND title = ?",
                (now, a.get("feed", ""), a.get("date", ""), a.get("title", "")),
            )


def save_interactions(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO interactions "
            "(code, question, answer, ask_time, reply_time, platform, fetched_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (
                    r.get("code", ""), r.get("question", ""),
                    r.get("answer", ""), r.get("ask_time", ""),
                    r.get("reply_time", ""), r.get("platform", ""), now,
                )
                for r in rows
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    return after - before


def upsert_collect_status(source: str, last_date: str, status: str,
                          message: str = "", added: int = 0):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO collect_status "
            "(source, last_date, last_run_at, status, message, added_count) "
            "VALUES (?,?,?,?,?,?)",
            (source, last_date, now, status, message, added),
        )


def get_collect_status() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT source, last_date, last_run_at, status, message, added_count "
            "FROM collect_status ORDER BY source"
        ).fetchall()
    return [dict(r) for r in rows]


def count_recent(table: str, date_col: str, days: int = 7) -> int:
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _conn() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE substr({date_col},1,10) >= ?",
            (since,),
        ).fetchone()[0]


def query_announcements(date_str: str, codes: set[str] = None) -> list[dict]:
    with _conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM announcements WHERE date = ? AND code IN ({placeholders}) "
                f"ORDER BY code, title",
                (date_str, *codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM announcements WHERE date = ? ORDER BY code, title",
                (date_str,),
            ).fetchall()
    return [dict(r) for r in rows]


def query_news(date_str: str, codes: set[str] = None) -> list[dict]:
    with _conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM stock_news WHERE substr(publish_time,1,10) = ? "
                f"AND code IN ({placeholders}) ORDER BY publish_time DESC",
                (date_str, *codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock_news WHERE substr(publish_time,1,10) = ? "
                "ORDER BY publish_time DESC",
                (date_str,),
            ).fetchall()
    return [dict(r) for r in rows]


def query_interactions(date_str: str, codes: set[str] = None) -> list[dict]:
    with _conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM interactions WHERE substr(reply_time,1,10) = ? "
                f"AND code IN ({placeholders}) ORDER BY reply_time DESC",
                (date_str, *codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM interactions WHERE substr(reply_time,1,10) = ? "
                "ORDER BY reply_time DESC",
                (date_str,),
            ).fetchall()
    return [dict(r) for r in rows]


def query_research_by_date(date_str: str, codes: set[str] = None) -> list[dict]:
    """日期列名为 report_date。"""
    with _conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_reports'"
        ).fetchone()
        if not exists:
            return []
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM research_reports WHERE substr(report_date,1,10) = ? "
                f"AND code IN ({placeholders}) ORDER BY code",
                (date_str, *codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM research_reports WHERE substr(report_date,1,10) = ? ORDER BY code",
                (date_str,),
            ).fetchall()
    return [dict(r) for r in rows]


def query_zsxq_by_date(date_str: str) -> list[dict]:
    with _conn() as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='zsxq_topics'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            "SELECT * FROM zsxq_topics WHERE substr(create_time,1,10) = ? "
            "ORDER BY create_time DESC",
            (date_str,),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 框架二·基本面源：业绩预告/快报 / 机构调研 / 解禁 / 一致预期EPS / 行业研报
# ============================================================

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def save_earnings_forecast(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM earnings_forecast").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO earnings_forecast "
            "(code, name, indicator, forecast_type, change_desc, value, change_pct, "
            " reason, prev_value, notice_date, period, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("code"), r.get("name"), r.get("indicator"), r.get("forecast_type"),
              r.get("change_desc"), r.get("value"), r.get("change_pct"), r.get("reason"),
              r.get("prev_value"), r.get("notice_date"), r.get("period"), now) for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM earnings_forecast").fetchone()[0]
    return after - before


def save_earnings_express(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM earnings_express").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO earnings_express "
            "(code, name, eps, revenue, revenue_yoy, net_profit, net_profit_yoy, "
            " bps, roe, industry, notice_date, period, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("code"), r.get("name"), r.get("eps"), r.get("revenue"),
              r.get("revenue_yoy"), r.get("net_profit"), r.get("net_profit_yoy"),
              r.get("bps"), r.get("roe"), r.get("industry"),
              r.get("notice_date"), r.get("period"), now) for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM earnings_express").fetchone()[0]
    return after - before


def save_inst_survey(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM inst_survey").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO inst_survey "
            "(code, name, change_pct, inst_count, method, attendees, location, "
            " survey_date, notice_date, period, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("code"), r.get("name"), r.get("change_pct"), r.get("inst_count"),
              r.get("method"), r.get("attendees"), r.get("location"),
              r.get("survey_date"), r.get("notice_date"), r.get("period"), now) for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM inst_survey").fetchone()[0]
    return after - before


def save_lockups(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM lockups").fetchone()[0]
        conn.executemany(
            "INSERT OR REPLACE INTO lockups "
            "(code, name, release_date, type, shares, ratio, fetched_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [(r.get("code"), r.get("name", ""), r.get("release_date"), r.get("type"),
              r.get("shares"), r.get("ratio"), now) for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM lockups").fetchone()[0]
    return after - before


def save_eps_forecast(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM eps_forecast").fetchone()[0]
        conn.executemany(
            "INSERT OR REPLACE INTO eps_forecast "
            "(code, name, year, eps, max_eps, min_eps, inst_count, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(r.get("code"), r.get("name", ""), r.get("year"), r.get("eps"),
              r.get("max_eps"), r.get("min_eps"), r.get("inst_count"), now) for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM eps_forecast").fetchone()[0]
    return after - before


def save_industry_research(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM industry_research").fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO industry_research "
            "(info_code, title, org, industry, rating, publish_date, url, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(r.get("info_code"), r.get("title"), r.get("org"), r.get("industry"),
              r.get("rating"), r.get("publish_date"), r.get("url"), now)
             for r in rows if r.get("info_code")],
        )
        after = conn.execute("SELECT COUNT(*) FROM industry_research").fetchone()[0]
    return after - before


def _query_by_date(table: str, date_col: str, date_str: str,
                   codes: set[str] = None, order: str = "code") -> list[dict]:
    with _conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE substr({date_col},1,10) = ? "
                f"AND code IN ({placeholders}) ORDER BY {order}",
                (date_str, *codes),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE substr({date_col},1,10) = ? ORDER BY {order}",
                (date_str,),
            ).fetchall()
    return [dict(r) for r in rows]


def query_earnings_forecast(date_str: str, codes: set[str] = None) -> list[dict]:
    return _query_by_date("earnings_forecast", "notice_date", date_str, codes)


def query_earnings_express(date_str: str, codes: set[str] = None) -> list[dict]:
    return _query_by_date("earnings_express", "notice_date", date_str, codes)


def query_inst_survey(date_str: str, codes: set[str] = None) -> list[dict]:
    return _query_by_date("inst_survey", "notice_date", date_str, codes,
                          order="inst_count DESC")


def query_lockups(codes: set[str] = None, since: str = None) -> list[dict]:
    """快照型：返回 universe 内未来解禁记录（release_date >= since，默认今天）。"""
    since = since or date.today().strftime("%Y-%m-%d")
    with _conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM lockups WHERE release_date >= ? "
                f"AND code IN ({placeholders}) ORDER BY release_date, code",
                (since, *codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lockups WHERE release_date >= ? ORDER BY release_date, code",
                (since,),
            ).fetchall()
    return [dict(r) for r in rows]


def query_eps_forecast(codes: set[str] = None) -> list[dict]:
    """快照型：返回 universe 内一致预期EPS（按 code, year）。"""
    with _conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            rows = conn.execute(
                f"SELECT * FROM eps_forecast WHERE code IN ({placeholders}) ORDER BY code, year",
                tuple(codes),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM eps_forecast ORDER BY code, year"
            ).fetchall()
    return [dict(r) for r in rows]


def query_industry_research(date_str: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM industry_research WHERE substr(publish_date,1,10) = ? "
            "ORDER BY industry, org",
            (date_str,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_financial_indicators(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = _now()
    cols = ["code", "name", "report_date", "roe", "gross_margin", "net_margin",
            "debt_ratio", "operating_margin", "revenue_yoy", "profit_yoy",
            "opcash_to_profit", "opcash_per_share", "current_ratio", "quick_ratio",
            "diluted_eps", "bv_per_share", "asset_turnover", "inventory_turnover",
            "receivables_turnover", "dividend_payout", "nav_growth",
            "total_asset_growth"]
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM financial_indicators").fetchone()[0]
        conn.executemany(
            f"INSERT OR REPLACE INTO financial_indicators "
            f"({', '.join(cols)}, fetched_at) "
            f"VALUES ({', '.join(['?'] * len(cols))}, ?)",
            [tuple(r.get(c) for c in cols) + (now,) for r in rows],
        )
        after = conn.execute("SELECT COUNT(*) FROM financial_indicators").fetchone()[0]
    return after - before


def query_financial_indicators(code: str, limit: int = 4) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM financial_indicators WHERE code = ? "
            "ORDER BY report_date DESC LIMIT ?",
            (code, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# feed 内容缓存 — Markdown → SQLite，消费者优先读库，文件兜底
# ============================================================

def init_feed_cache_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feed_cache (
                source TEXT NOT NULL,
                date   TEXT NOT NULL,
                content TEXT NOT NULL,
                cached_at TEXT,
                PRIMARY KEY (source, date)
            )
        """)


def save_feed_cache(source: str, date_str: str, content: str) -> bool:
    if not content or not content.strip():
        return False
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feed_cache (source, date, content, cached_at) "
                "VALUES (?, ?, ?, ?)",
                (source, date_str, content, datetime.now().strftime("%Y-%m-%d %H:%M")),
            )
        return True
    except Exception:
        return False


def get_feed_cache(source: str, date_str: str) -> str | None:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT content FROM feed_cache WHERE source = ? AND date = ?",
                (source, date_str),
            ).fetchone()
        return row["content"] if row else None
    except Exception:
        return None
