"""强势板块.xlsx 清洗导入 — 手工复盘模板数据 -> SQLite sector_rotation_log

Usage: python daily_review/sector_rotation_import.py [--force]

数据按模板逐日展开，每日 ~20 行，每行最多 48 列。核心输出四个维度：
- 行类型（index_score / volume / breadth / limit_up_leaders / …）
- 板块名称（标准化后，如 "半导体-碳化硅"）
- 龙头标的 & 成分股列表
- 大盘环境数值（量比 / 涨跌家数 / 成交量变化）
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import _conn, init_db

XLSX_PATH = Path(__file__).resolve().parent.parent / "强势板块.xlsx"

# ============================================================
# Row type mapping — col_1 chinese -> English code
# ============================================================
ROW_TYPE_MAP = {
    "1指数（潜在波峰-2，波谷+2，竞价反外盘）": "index_score",
    "2成交量（昨日+-10%单位以内0，增减10%1分）": "volume",
    "3涨跌家数（4000以上2分，3000-4000 1分，1000以下-2，1000-2000 -1）": "breadth",
    "4连扳票": "limit_up_leaders",
    "5普通票(跌停)": "limit_down",
    "6昨日涨停盈亏": "prev_limit_pnl",
    "3机构(-2,2)": "institutional",
    "4游资(-2,2)": "retail",
    "5逻辑主线": "logic_mainline",
    "6最强风格(业绩、大票，小票，连板、高股息、垃圾、次新等)": "dominant_style",
    "+1启动：": "phase_launch",
    "+2接力：": "phase_relay",
    "扩容1(2-3):": "phase_expand1",
    "扩容2(3-4)首5:": "phase_expand2",
    "回流大单背离：": "phase_backflow",
    "个股逻辑：": "stock_logic",
    "缩量": "shrink_volume",
}


def _normalize_row_type(raw: str) -> str:
    key = raw.strip()
    if key in ROW_TYPE_MAP:
        return ROW_TYPE_MAP[key]
    for prefix, code in ROW_TYPE_MAP.items():
        if key.startswith(prefix.rstrip("：")):
            return code
    return key


# ============================================================
# Sector name normalization
# ============================================================
def _normalize_sector(raw: str) -> str:
    s = str(raw).strip()
    s = s.replace("，", ",").replace("：", ":").rstrip(":,")
    s = re.sub(r"^海外[-—]", "", s)
    return s


# ============================================================
# Data parsing per row type
# ============================================================
def _extract_stocks(row: pd.Series) -> list[str]:
    stocks = []
    for c in range(15, 48):
        v = row.iloc[c]
        if pd.notna(v) and str(v).strip():
            s = str(v).strip().rstrip(":：")
            if s and s not in ("nan", "NaN", ""):
                stocks.append(s)
    return stocks


def _parse_breadth_sequence(raw: str) -> dict:
    parts = re.split(r"[-—–]", str(raw).strip())
    nums = []
    for p in parts:
        try:
            nums.append(int(float(p)))
        except ValueError:
            pass
    if not nums:
        return {}
    labels = ["today", "d1", "d2", "d3", "d4", "d5"]
    return {labels[i]: n for i, n in enumerate(nums) if i < len(labels)}


def _parse_pnl_sequence(raw: str) -> list[float]:
    parts = re.split(r"[，,、]", str(raw).strip())
    result = []
    for p in parts:
        try:
            result.append(float(p.strip()))
        except ValueError:
            pass
    return result


def _parse_index_value(raw: str) -> dict:
    s = str(raw).strip().replace("，", ",").replace("%", "")
    parts = [p.strip() for p in s.split(",")]
    result = {}
    if parts:
        try:
            result["close"] = float(parts[0])
        except ValueError:
            pass
    if len(parts) > 1:
        try:
            result["chg_pct"] = float(parts[1])
        except ValueError:
            pass
    return result


def _parse_volume_data(raw: str) -> dict:
    s = str(raw).strip().replace("，", ",").replace("%", "").replace("万亿", "0000")
    parts = [p.strip() for p in s.split(",")]
    result = {}
    if parts:
        try:
            result["amount_yi"] = float(parts[0])
        except ValueError:
            pass
    if len(parts) > 1:
        try:
            result["ratio"] = float(parts[1])
        except ValueError:
            pass
    if len(parts) > 2:
        try:
            result["chg_pct"] = float(parts[2])
        except ValueError:
            pass
    return result


def _parse_limit_up_board(raw: str) -> dict:
    s = str(raw).strip()
    pairs = re.findall(r"(\d+)\s*([^\d，,\s]+)", s)
    stocks = [name for _, name in pairs]
    max_board = max((int(n) for n, _ in pairs), default=0)
    return {"stocks": stocks, "max_board": max_board}


def parse_row(row: pd.Series, row_type: str) -> dict:
    raw = str(row.iloc[3]) if pd.notna(row.iloc[3]) else ""
    result = {"raw": raw}
    if row_type == "index_score":
        result.update(_parse_index_value(raw))
    elif row_type == "volume":
        result.update(_parse_volume_data(raw))
    elif row_type == "breadth":
        result.update(_parse_breadth_sequence(raw))
    elif row_type == "limit_up_leaders":
        result.update(_parse_limit_up_board(raw))
    elif row_type == "prev_limit_pnl":
        result["pnl_sequence"] = _parse_pnl_sequence(raw)
    return result


# ============================================================
# Import logic
# ============================================================
def import_xlsx(force: bool = False) -> int:
    df = pd.read_excel(XLSX_PATH, sheet_name="Sheet1")

    # Date fill-forward
    date_col = df.iloc[:, 0]
    last_date = None
    filled_dates = []
    for v in date_col:
        if pd.notna(v) and str(v).strip():
            try:
                dt_float = float(v)
                dt_str = str(int(dt_float))
                parsed = datetime.strptime(dt_str, "%Y%m%d")
                last_date = parsed.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        filled_dates.append(last_date)

    rows_out = []
    for idx in range(len(df)):
        date = filled_dates[idx]
        if date is None:
            continue
        row = df.iloc[idx]
        raw_type = str(row.iloc[1]) if pd.notna(row.iloc[1]) else ""
        if not raw_type.strip():
            continue
        row_type = _normalize_row_type(raw_type)
        score = row.iloc[2] if pd.notna(row.iloc[2]) else None
        sector = _normalize_sector(str(row.iloc[4])) if pd.notna(row.iloc[4]) else ""
        status_code = str(row.iloc[6]) if pd.notna(row.iloc[6]) else ""
        leader = str(row.iloc[8]).strip() if pd.notna(row.iloc[8]) else ""
        auction_leader = str(row.iloc[9]).strip() if pd.notna(row.iloc[9]) else ""
        volume_ratio = row.iloc[14] if pd.notna(row.iloc[14]) else None
        stocks = _extract_stocks(row)
        parsed = parse_row(row, row_type)

        try:
            score_f = float(score) if score is not None else None
        except (ValueError, TypeError):
            score_f = None
        try:
            vr = float(volume_ratio) if volume_ratio is not None else None
        except (ValueError, TypeError):
            vr = None

        rows_out.append({
            "date": date,
            "row_type": row_type,
            "score": score_f,
            "sector": sector,
            "status_code": status_code.strip(),
            "leader_stock": leader.rstrip(":："),
            "auction_leader": auction_leader.rstrip(":："),
            "stocks_json": json.dumps(stocks, ensure_ascii=False) if stocks else "[]",
            "volume_ratio": vr,
            "raw_data": json.dumps(parsed, ensure_ascii=False),
        })

    init_db()
    table_name = "sector_rotation_log"

    with _conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT NOT NULL,
                row_type      TEXT NOT NULL,
                score         REAL,
                sector        TEXT DEFAULT '',
                status_code   TEXT DEFAULT '',
                leader_stock  TEXT DEFAULT '',
                auction_leader TEXT DEFAULT '',
                stocks_json   TEXT DEFAULT '[]',
                volume_ratio  REAL,
                raw_data      TEXT DEFAULT '{{}}'
            )
        """)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_srl_date ON {table_name}(date)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_srl_type ON {table_name}(row_type)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_srl_sector ON {table_name}(sector)")

        if force:
            conn.execute(f"DELETE FROM {table_name}")

        conn.executemany(
            f"INSERT INTO {table_name} "
            "(date, row_type, score, sector, status_code, leader_stock, auction_leader, "
            " stocks_json, volume_ratio, raw_data) "
            "VALUES (:date, :row_type, :score, :sector, :status_code, :leader_stock, "
            " :auction_leader, :stocks_json, :volume_ratio, :raw_data)",
            rows_out,
        )

    sector_rows = sum(1 for r in rows_out if r["sector"])
    stock_rows = sum(1 for r in rows_out if r["stocks_json"] != "[]")
    types = set(r["row_type"] for r in rows_out)
    dates = set(r["date"] for r in rows_out)
    print(f"[import] {len(rows_out)} rows, {len(dates)} dates, {len(types)} row_types")
    print(f"[import] {sector_rows} rows with sector, {stock_rows} rows with stocks")
    return len(rows_out)


# ============================================================
# Query helpers
# ============================================================
def query_by_date(date: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sector_rotation_log WHERE date = ? ORDER BY id", (date,)
        ).fetchall()
    return [dict(r) for r in rows]


def query_by_sector(sector: str, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sector_rotation_log WHERE sector = ? "
            "ORDER BY date DESC LIMIT ?", (sector, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def query_sector_dates(sector: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM sector_rotation_log "
            "WHERE sector = ? AND row_type IN "
            "('index_score','volume','breadth','limit_up_leaders','limit_down','prev_limit_pnl') "
            "ORDER BY date", (sector,),
        ).fetchall()
    return [r["date"] for r in rows]


def query_all_sectors() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT sector, COUNT(*) as cnt, COUNT(DISTINCT date) as days, "
            "MIN(date) as first_date, MAX(date) as last_date "
            "FROM sector_rotation_log WHERE sector != '' "
            "GROUP BY sector ORDER BY days DESC"
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="clear old data before import")
    args = parser.parse_args()

    n = import_xlsx(force=args.force)
    print(f"[done] {n} rows imported")

    sectors = query_all_sectors()
    print(f"[summary] {len(sectors)} unique sectors")
    for s in sectors[:5]:
        print(f"  {s['sector']}: {s['days']}d, {s['cnt']} rows, {s['first_date']} ~ {s['last_date']}")
