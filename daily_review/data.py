"""DEPRECATED — 兼容层。所有代码已迁移到 data/ 包。2026-07-20 后可删除。

用法不变:
    import data                → 自动解析到 data/ 包
    from data import X         → 同上
    data.fetch_stock_quotes()  → 同上
"""
from data import *  # noqa: F403, F401
