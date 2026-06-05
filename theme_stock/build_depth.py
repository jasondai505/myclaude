"""标的深度数据构建 — 从 BOM leaders 导入护城河评分"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from theme_stock.store import ThemeStockStore, _today


def build_from_bom_leaders(store: ThemeStockStore):
    bom_db = Path(__file__).parent.parent / "bom_analyzer" / "data" / "bom.db"
    if not bom_db.exists():
        print("  [WARN] BOM DB 不存在, 跳过")
        return 0

    conn = sqlite3.connect(str(bom_db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("""
            SELECT l.stock_code, l.stock_name, l.rank, l.moat_total,
                   l.moat_tech, l.moat_cost, l.moat_scale,
                   l.moat_brand, l.moat_switch, l.moat_network,
                   c.industry, c.tier, c.segment
            FROM bom_leaders l
            JOIN bom_chains c ON c.id = l.chain_id
            ORDER BY l.stock_code, l.rank
        """)
    except sqlite3.OperationalError as e:
        print(f"  [WARN] BOM DB 读取失败: {e}")
        conn.close()
        return 0

    rows_by_code: dict[str, dict] = {}
    for r in cur:
        code = str(r["stock_code"]).zfill(6)
        if code not in rows_by_code:
            moat = {
                "tech": r["moat_tech"] or 0, "cost": r["moat_cost"] or 0,
                "scale": r["moat_scale"] or 0, "brand": r["moat_brand"] or 0,
                "switch_cost": r["moat_switch"] or 0,
                "network": r["moat_network"] or 0,
            }
            rows_by_code[code] = {
                "code": code, "market": "A", "name": r["stock_name"],
                "moat_total": r["moat_total"] or 0,
                "moat_detail": json.dumps(moat, ensure_ascii=False),
                "industry_l1": r["industry"],
                "tier_label": "龙头" if (r["rank"] or 99) <= 2 else "一线",
            }
    conn.close()

    if rows_by_code:
        store.upsert_depth_batch(list(rows_by_code.values()))
        print(f"  BOM深度 → {len(rows_by_code)} 只")
    return len(rows_by_code)


def build_all(store: ThemeStockStore | None = None):
    if store is None:
        store = ThemeStockStore()
    store.init_db()
    print(f"[{_today()}] 标的深度构建...")
    n = build_from_bom_leaders(store)
    print(f"  完成: {n} 只")
    store.close()
    return n


if __name__ == "__main__":
    build_all()
