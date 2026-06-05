"""产业链索引构建 — 从 BOM chain_db + morning supply_chain 导入"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from theme_stock.store import ThemeStockStore, _today


def build_from_bom(store: ThemeStockStore):
    bom_db = Path(__file__).parent.parent / "bom_analyzer" / "data" / "bom.db"
    if not bom_db.exists():
        print("  [WARN] BOM DB 不存在, 跳过")
        return 0

    conn = sqlite3.connect(str(bom_db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("""
            SELECT c.industry, c.tier, c.segment, l.stock_code, l.stock_name,
                   l.rank, l.moat_total
            FROM bom_chains c
            JOIN bom_leaders l ON l.chain_id = c.id
            ORDER BY c.industry, c.tier, c.segment, l.rank
        """)
        rows = []
        for r in cur:
            rows.append({
                "industry": r["industry"], "tier": r["tier"],
                "segment": r["segment"],
                "code": str(r["stock_code"]).zfill(6),
                "name": r["stock_name"], "market": "A",
                "role": f"#{r['rank']} moat={r['moat_total']}",
                "source": "bom", "source_ver": "bom_import",
                "confidence": "high" if r["rank"] and r["rank"] <= 3 else "medium",
            })
    except sqlite3.OperationalError as e:
        print(f"  [WARN] BOM DB 读取失败: {e}")
        conn.close()
        return 0
    conn.close()

    if rows:
        store.upsert_chain_batch(rows)
        n_ind = len(set(r["industry"] for r in rows))
        print(f"  BOM → {len(rows)} 条, {n_ind} 行业")
    return len(rows)


def build_from_supply_chain(store: ThemeStockStore):
    sc_db = Path(__file__).parent.parent / "morning_intel" / "data" / "supply_chain.db"
    if not sc_db.exists():
        print("  [WARN] SC DB 不存在, 跳过")
        return 0

    conn = sqlite3.connect(str(sc_db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("""
            SELECT e.event_name, n.tier, n.stock_code, n.stock_name,
                   n.role, n.confidence
            FROM supply_chain_events e
            JOIN supply_chain_nodes n ON n.event_id = e.id
            WHERE n.stock_code IS NOT NULL AND n.stock_code != ''
        """)
        rows = []
        for r in cur:
            code = str(r["stock_code"]).strip()
            if not code:
                continue
            rows.append({
                "industry": r["event_name"], "tier": r["tier"] or "中游",
                "segment": r["tier"] or "中游",
                "code": code.zfill(6) if code.isdigit() and len(code) <= 6 else code,
                "name": r["stock_name"] or "", "market": "A",
                "role": r["role"] or "",
                "source": "supply_chain", "source_ver": "sc_import",
                "confidence": r["confidence"] or "medium",
                "is_verified": 1 if r["confidence"] == "confirmed" else 0,
            })
    except sqlite3.OperationalError as e:
        print(f"  [WARN] SC DB 读取失败: {e}")
        conn.close()
        return 0
    conn.close()

    if rows:
        store.upsert_chain_batch(rows)
        n_ev = len(set(r["industry"] for r in rows))
        print(f"  SC → {len(rows)} 条, {n_ev} 事件")
    return len(rows)


def build_all(store: ThemeStockStore | None = None):
    if store is None:
        store = ThemeStockStore()
    store.init_db()
    print(f"[{_today()}] 产业链索引构建...")
    n1 = build_from_bom(store)
    n2 = build_from_supply_chain(store)
    s = store.get_chain_stats()
    print(f"  完成: BOM={n1} SC={n2} | DB: {s['industries']}产业 {s['stocks']}标的 {s['verified']}已确认")
    store.close()
    return n1 + n2


if __name__ == "__main__":
    build_all()
