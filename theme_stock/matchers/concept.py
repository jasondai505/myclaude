"""概念板块匹配器 — 从 concept_index 查询主题→标的"""

from __future__ import annotations
from theme_stock.store import ThemeStockStore

SOURCE_W = {
    "tonghuashun_hot": 3.0, "eastmoney": 2.0, "overseas_map": 2.0,
    "tonghuashun": 1.5, "watchlist": 1.0, "baidu": 1.0,
}


class ConceptMatcher:
    def __init__(self, store: ThemeStockStore):
        self._s = store

    def match(self, theme: str, market: str | None = None) -> dict[str, list[dict]]:
        stocks = self._s.query_concept_stocks(theme, market=market, limit=200)
        result: dict[str, list[dict]] = {}
        for s in stocks:
            code = s["code"]
            src = s.get("source", "")
            result.setdefault(code, []).append({
                "source": f"concept_{src}",
                "weight": SOURCE_W.get(src, 1.0),
                "concept": s["concept"],
                "market": s.get("market", "A"),
                "name": s.get("name", ""),
            })
        return result

    def search(self, keyword: str, limit: int = 20) -> list[str]:
        stocks = self._s.query_concept_stocks(keyword, limit=limit)
        return list(dict.fromkeys(s["concept"] for s in stocks if s.get("concept")))[:limit]
