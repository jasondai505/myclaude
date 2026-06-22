"""主题-标的客观映射引擎

五层架构 + 双反馈闭环:
  L0: 外部成熟产业链图谱（种子）
  L1: 产业链知识层（骨架）
  L2: 标的深度挖掘（血肉）
  L3: 定性信号修正（信号层）
  L4: 盘面反馈修正（纠错层）
"""

from .store import ThemeStockStore
from .engine import ThemeStockEngine, StockEntry, StockList, SourceRef

__all__ = ["ThemeStockStore", "ThemeStockEngine",
           "StockEntry", "StockList", "SourceRef",
           "get_chain_context"]


def get_chain_context(codes: list[str], map_type: str = "chain") -> dict[str, list[str]]:
    """批量查标的的产业链位置, 返回 {code: ['产业>层级1>层级2', ...]}

    轻量级, 不创建 Engine, 直接查 DB。供 catalyst_screen/advice 等管线调用。
    map_type='chain' 仅查产业链; map_type='all' 查全部(含筛选筐)。
    """
    if not codes:
        return {}
    store = ThemeStockStore()
    store.init_db()
    placeholders = ",".join("?" for _ in codes)
    if map_type == "all":
        cur = store._get_conn().execute(
            f"SELECT code, industry, tier, segment FROM chain_map WHERE code IN ({placeholders}) AND market='A'",
            codes,
        )
    else:
        cur = store._get_conn().execute(
            f"SELECT code, industry, tier, segment FROM chain_map WHERE code IN ({placeholders}) AND market='A' AND map_type=?",
            codes + [map_type],
        )
    result: dict[str, list[str]] = {c: [] for c in codes}
    for row in cur:
        seg = row["segment"] if row["segment"] and row["segment"] != "-" else ""
        chain = f"{row['industry']}>{row['tier']}>{seg}" if seg else f"{row['industry']}>{row['tier']}"
        result[row["code"]].append(chain)
    store.close()
    return result
