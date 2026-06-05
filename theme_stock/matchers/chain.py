"""产业链匹配器 — 从 chain_map 查询主题→产业链→标的"""

from __future__ import annotations
from theme_stock.store import ThemeStockStore

SOURCE_W = {
    "external_map": 3.5, "manual": 3.0, "bom": 2.5,
    "supply_chain": 2.0, "llm_draft": 0.5,
}
CONF_MULT = {"high": 1.0, "medium": 0.7, "low": 0.4}


class ChainMatcher:
    def __init__(self, store: ThemeStockStore):
        self._s = store

    def match(self, theme: str, market: str | None = None) -> dict[str, list[dict]]:
        stocks = self._s.query_chain_by_industry(theme, market=market)
        result: dict[str, list[dict]] = {}
        for s in stocks:
            code = s["code"]
            src = s.get("source", "")
            conf = s.get("confidence", "medium")
            result.setdefault(code, []).append({
                "source": f"chain_{src}",
                "weight": SOURCE_W.get(src, 1.0) * CONF_MULT.get(conf, 0.7),
                "tier": s["tier"], "segment": s["segment"],
                "role": s.get("role", ""), "industry": s["industry"],
                "market": s.get("market", "A"), "name": s.get("name", ""),
                "is_verified": s.get("is_verified", 0),
            })
        return result

    def get_segments(self, theme: str) -> dict:
        return self._s.query_chain_segments(theme)

    def search(self, keyword: str, limit: int = 50) -> list[dict]:
        return self._s.search_chain(keyword, limit)
