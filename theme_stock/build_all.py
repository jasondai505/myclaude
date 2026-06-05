"""Phase 1 全量构建入口 — 供 daily_collect 和 CLI 调用"""

from __future__ import annotations

from theme_stock.build_concept import build_all as build_concept
from theme_stock.build_chain import build_all as build_chain
from theme_stock.build_depth import build_all as build_depth
from theme_stock.store import ThemeStockStore


def build_all(live_scan: bool = True):
    store = ThemeStockStore()
    store.init_db()
    n1 = build_chain(store)
    n2 = build_concept(store, live_scan=live_scan)
    n3 = build_depth(store)
    store.close()
    return {"chain": n1, "concept": n2, "depth": n3}


if __name__ == "__main__":
    import sys
    quick = "--quick" in sys.argv
    build_all(live_scan=not quick)
