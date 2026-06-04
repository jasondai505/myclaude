"""BOM 桥接 — 轻量查询，不加载 LLM 模块"""
import sqlite3
from pathlib import Path

BOM_DB = Path(__file__).resolve().parent.parent / "bom_analyzer" / "data" / "bom.db"


def _query(sql: str, params=()) -> list[dict]:
    if not BOM_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(BOM_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_theme_bom_context(theme_names: list[str]) -> dict[str, list[dict]]:
    """题材名 → BOM产业链匹配。返回 {industry: [{segment, tier}]}"""
    if not theme_names:
        return {}
    placeholders = ",".join("?" * len(theme_names))
    rows = _query(
        f"""SELECT DISTINCT c.industry, s.name as segment, s.tier
            FROM chains c
            JOIN segments s ON s.chain_id = c.chain_id
            WHERE c.industry IN ({placeholders})
               OR s.name IN ({placeholders})
            ORDER BY c.analyzed_at DESC""",
        theme_names * 2,
    )
    result: dict[str, list[dict]] = {}
    for r in rows:
        ind = r["industry"]
        if ind not in result:
            result[ind] = []
        result[ind].append({"segment": r["segment"], "tier": r["tier"]})
    return result


def get_stock_moat_scores(codes: list[str]) -> dict[str, dict]:
    """股票代码 → BOM护城河评分。返回 {code: {moat_score, rank, segment, industry}}"""
    if not codes:
        return {}
    placeholders = ",".join("?" * len(codes))
    rows = _query(
        f"""SELECT l.code, l.name, l.moat_score, l.rank, l.segment_name,
                   c.industry, l.core_strengths
            FROM leaders l
            JOIN chains c ON c.chain_id = l.chain_id
            WHERE l.code IN ({placeholders})
            ORDER BY l.moat_score DESC""",
        codes,
    )
    result = {}
    for r in rows:
        code = r["code"]
        if code not in result or r["moat_score"] > result[code].get("moat_score", 0):
            result[code] = {
                "moat_score": r["moat_score"],
                "rank": r["rank"],
                "segment": r["segment_name"],
                "industry": r["industry"],
                "strengths": r["core_strengths"],
            }
    return result


def get_sector_linkages() -> list[dict]:
    """获取产业链上下游联动关系。"""
    rows = _query("""
        SELECT DISTINCT c.industry, s.name as segment, s.tier
        FROM chains c
        JOIN segments s ON s.chain_id = c.chain_id
        ORDER BY c.industry, s.tier
    """)
    if not rows:
        return []

    chains: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        ind = r["industry"]
        tier = r["tier"]
        seg = r["segment"]
        if ind not in chains:
            chains[ind] = {}
        chains[ind].setdefault(tier, []).append(seg)

    linkages = []
    for ind, tiers in chains.items():
        upstream = tiers.get("上游", []) + tiers.get("上游材料", []) + tiers.get("上游设备", [])
        midstream = tiers.get("中游", []) + tiers.get("中游制造", [])
        downstream = tiers.get("下游", []) + tiers.get("下游应用", [])
        if upstream and (midstream or downstream):
            linkages.append({
                "industry": ind,
                "upstream": upstream,
                "midstream": midstream,
                "downstream": downstream,
            })
    return linkages
