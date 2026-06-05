"""概念索引构建 — 从东财/同花顺/外围提取概念→标的映射"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from theme_stock.store import ThemeStockStore, _today

SEED_ALIASES = {
    "AI": "AI算力", "人工智能": "AI算力", "AI芯片": "AI算力",
    "算力": "AI算力", "算力租赁": "AI算力", "东数西算": "AI算力",
    "液冷": "AI算力", "AI服务器": "AI算力", "数据中心": "AI算力",
    "CPO": "CPO", "光模块": "CPO", "光通信": "CPO", "硅光": "CPO",
    "LPO": "CPO", "共封装光学": "CPO", "光引擎": "CPO",
    "玻璃基板": "玻璃基板", "CoPoS": "玻璃基板", "TGV": "玻璃基板",
    "先进封装": "先进封装", "CoWoS": "先进封装", "chiplet": "先进封装",
    "HBM": "HBM", "高带宽内存": "HBM", "存储": "存储芯片",
    "半导体": "半导体", "芯片": "半导体", "晶圆": "半导体",
    "机器人": "机器人", "人形机器人": "机器人", "具身智能": "机器人",
    "固态电池": "固态电池", "半固态电池": "固态电池",
    "新能源车": "新能源车", "新能源汽车": "新能源车", "电动车": "新能源车",
    "光伏": "光伏", "太阳能": "光伏", "HJT": "光伏", "TOPCon": "光伏",
    "风电": "风电", "海上风电": "风电",
    "低空经济": "低空经济", "飞行汽车": "低空经济", "eVTOL": "低空经济",
    "商业航天": "商业航天", "卫星互联网": "商业航天",
    "煤炭": "煤炭", "煤化工": "煤炭",
    "电力": "电力", "电网": "电力", "特高压": "电力",
    "军工": "军工", "军工电子": "军工", "军工装备": "军工",
    "消费电子": "消费电子", "AIPC": "消费电子", "AI手机": "消费电子",
    "PCB": "PCB", "印制电路板": "PCB", "IC载板": "PCB",
    "SOFC": "SOFC", "固体氧化物燃料电池": "SOFC",
    "电感": "电感", "TLVR": "电感", "算力电感": "电感",
    "光刻机": "光刻机", "光刻胶": "光刻胶",
    "工业金属": "工业金属", "有色金属": "工业金属",
    "小金属": "小金属", "稀有金属": "小金属",
    "贵金属": "贵金属", "黄金": "贵金属",
    "氮化铝": "氮化铝", "陶瓷基板": "氮化铝",
    "物理AI": "AI算力", "Cosmos": "AI算力", "世界模型": "AI算力",
    "汽车零部件": "汽车零部件", "一体化压铸": "汽车零部件",
    "创新药": "创新药", "医药": "创新药", "CXO": "创新药",
}


def _parse_em_concepts(s: str) -> list[str]:
    if not s or s in ("None", "nan", "-"):
        return []
    return [c.strip() for c in str(s).split(" ") if c.strip()]


def _parse_ths_reason(s: str) -> list[str]:
    if not s or s in ("None", "nan", "-"):
        return []
    return [c.strip() for c in str(s).split("+") if c.strip()]


def build_from_live_scanner(store: ThemeStockStore):
    from daily_review.live_scanner import scan_all

    print("[concept] 全A概念标签...")
    df = scan_all()
    if df.empty:
        print("  [WARN] 扫描失败, 跳过")
        return 0

    rows = []
    for _, r in df.iterrows():
        code = str(r.get("code", ""))
        name = str(r.get("name", ""))
        for c in _parse_em_concepts(str(r.get("concepts", ""))):
            rows.append((code, name, "A", c, "eastmoney", 2.0))

    if rows:
        store.upsert_concept_batch(rows)
        print(f"  东财 → {len(rows)} 条, {len(set(r[0] for r in rows))} 只")
    return len(rows)


def build_from_ths_hot(store: ThemeStockStore):
    try:
        from daily_review.data import fetch_hot_themes
        df = fetch_hot_themes(date.today().strftime("%Y%m%d"))
    except Exception as e:
        print(f"  [WARN] 同花顺强势股: {e}")
        return 0
    if df is None or df.empty:
        return 0

    rows = []
    for _, r in df.iterrows():
        code, name = str(r.get("code", "")), str(r.get("name", ""))
        for c in _parse_ths_reason(str(r.get("reason", ""))):
            rows.append((code, name, "A", c, "tonghuashun_hot", 3.0))

    if rows:
        store.upsert_concept_batch(rows)
        print(f"  同花顺强势股 → {len(rows)} 条")
    return len(rows)


def build_overseas_seed(store: ThemeStockStore):
    try:
        from daily_review.config import OVERSEAS_MAP, GLOBAL_WATCHLIST_EM
    except ImportError:
        return 0

    rows = []
    for item in GLOBAL_WATCHLIST_EM:
        label = item["label"]
        code = label.split("(")[-1].rstrip(")") if "(" in label else label
        name = label.split("(")[0].strip() if "(" in label else label
        secid = item.get("secid", "")
        market = "HK" if "116." in secid else "US"
        tag = item.get("tag", "")
        if tag:
            rows.append((code, name, market, tag, "watchlist", 1.0))

    for label, concepts_str in OVERSEAS_MAP.items():
        code = label.split("(")[-1].rstrip(")") if "(" in label else label
        market = "HK" if "(H)" in label else "US"
        for part in concepts_str.split("/"):
            c = part.split("(")[0].strip()
            if c:
                rows.append((code, label, market, c, "overseas_map", 2.0))

    if rows:
        store.upsert_concept_batch(rows)
        print(f"  外围种子 → {len(rows)} 条 (US/HK)")
    return len(rows)


def seed_aliases(store: ThemeStockStore):
    for a, c in SEED_ALIASES.items():
        store.add_alias(a, c, "seed")
    print(f"  alias → {len(SEED_ALIASES)} 条")


def build_all(store: ThemeStockStore | None = None, live_scan: bool = True):
    if store is None:
        store = ThemeStockStore()
    store.init_db()

    print(f"[{_today()}] 概念索引构建...")
    seed_aliases(store)
    n1 = build_from_live_scanner(store) if live_scan else 0
    n2 = build_from_ths_hot(store)
    n3 = build_overseas_seed(store)

    s = store.get_concept_stats()
    print(f"  完成: 东财={n1} 同花顺={n2} 外围={n3} | DB: {s['concepts']}概念 {s['stocks']}标的")
    store.close()
    return n1 + n2 + n3


if __name__ == "__main__":
    build_all()
