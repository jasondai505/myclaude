"""核心引擎 — 五层融合查询"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from theme_stock.store import ThemeStockStore
from theme_stock.matchers.concept import ConceptMatcher
from theme_stock.matchers.chain import ChainMatcher


@dataclass
class SourceRef:
    source: str
    detail: str
    tier: str | None = None
    segment: str | None = None
    confidence: str = "medium"

@dataclass
class StockEntry:
    code: str
    name: str
    market: str
    score: float
    sources: list[SourceRef] = field(default_factory=list)
    tier: str | None = None
    segment: str | None = None
    role: str | None = None
    moat_total: int | None = None
    tier_label: str | None = None

@dataclass
class StockList:
    theme: str
    canonical: str
    stocks: list[StockEntry]
    chain_context: dict = field(default_factory=dict)
    total: int = 0


class ThemeStockEngine:
    def __init__(self, store: ThemeStockStore | None = None):
        self._store = store or ThemeStockStore()
        self._store.init_db()
        self._concept = ConceptMatcher(self._store)
        self._chain = ChainMatcher(self._store)
        self._alias = self._store.load_alias_map()

    def query(self, theme: str, *, limit: int = 20,
              min_score: float = 0.2, market: str | None = None) -> StockList:
        """主题→标的 五层融合查询"""

        canonical = self._normalize(theme)

        c_result = self._concept.match(canonical, market=market)
        ch_result = self._chain.match(canonical, market=market)
        segments = self._chain.get_segments(canonical)

        if not c_result and not ch_result:
            return StockList(theme=theme, canonical=canonical, stocks=[])

        all_codes = set(c_result) | set(ch_result)
        entries = []
        for code in all_codes:
            sources: list[SourceRef] = []
            base = 0.0
            total_w = 0.0

            for ref in c_result.get(code, []):
                w = ref.get("weight", 1.0)
                sources.append(SourceRef(
                    source=ref["source"],
                    detail=f"概念→{ref.get('concept','')} ({ref.get('name','')})",
                ))
                base += w * 1.0
                total_w += w

            for ref in ch_result.get(code, []):
                w = ref.get("weight", 1.0)
                sources.append(SourceRef(
                    source=ref["source"],
                    detail=f"产业链→{ref.get('industry','')}→{ref.get('tier','')}→{ref.get('segment','')}",
                    tier=ref.get("tier"), segment=ref.get("segment"),
                    confidence="high" if ref.get("is_verified") else "medium",
                ))
                base += w * 0.9
                total_w += w

            if total_w > 0:
                base /= total_w

            bonus = 0.15 if len(sources) >= 2 else 0.0
            bonus += 0.1 if len(sources) >= 3 else 0.0

            score = min(1.0, base + bonus)
            if score < min_score:
                continue

            name, mkt, tier, seg, role = "", "A", None, None, None
            for ref in ch_result.get(code, []):
                name = ref.get("name", "") or name
                mkt = ref.get("market", "A")
                tier = ref.get("tier") or tier
                seg = ref.get("segment") or seg
                role = ref.get("role") or role
            if not name:
                for ref in c_result.get(code, []):
                    name = ref.get("name", "") or name
                    mkt = ref.get("market", "A")

            depth = self._store.get_depth(code, mkt)

            entries.append(StockEntry(
                code=code, name=name, market=mkt,
                score=round(score, 3), sources=sources,
                tier=tier, segment=seg, role=role,
                moat_total=depth.get("moat_total") if depth else None,
                tier_label=depth.get("tier_label") if depth else None,
            ))

        entries.sort(key=lambda e: e.score, reverse=True)
        top = entries[:limit]

        return StockList(
            theme=theme, canonical=canonical, stocks=top,
            chain_context=segments, total=len(entries),
        )

    def query_multi(self, themes: list[str], **kw) -> dict[str, StockList]:
        return {t: self.query(t, **kw) for t in themes}

    def enrich(self, codes: list[str], market: str = "A") -> list[dict]:
        """反向查询标的关联的主题+产业链位置"""
        results = []
        for code in codes:
            depth = self._store.get_depth(code, market)
            entry: dict[str, Any] = {
                "code": code, "market": market,
                "name": depth.get("name", "") if depth else "",
                "depth": depth,
                "themes": [],
            }
            for c in self._store.query_chain_by_code(code, market):
                entry["themes"].append({
                    "industry": c["industry"], "tier": c["tier"],
                    "segment": c["segment"], "role": c.get("role", ""),
                    "source": c["source"],
                })
            results.append(entry)
        return results

    def search_themes(self, keyword: str, limit: int = 20) -> list[str]:
        cs = self._concept.search(keyword, limit)
        ch = [c["industry"] for c in self._chain.search(keyword, limit) if "industry" in c]
        return list(dict.fromkeys(cs + ch))[:limit]

    def _normalize(self, theme: str) -> str:
        return self._alias.get(theme.strip(), theme.strip())

    def close(self):
        self._store.close()
