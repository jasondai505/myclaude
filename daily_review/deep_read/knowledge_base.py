"""猎场定义 + 卡脖子环节清单 + 猎场股票缓存。

一只股票「属于猎场」当且仅当：
1. 其 STOCK_PRIMARY_CONCEPT 映射到 HUNTING_GROUND_DOMAINS
2. 或公告标题/股票名称匹配 CHOKE_POINT_TAXONOMY 关键词（跨界收购场景）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# ============================================================
# 猎场定义 — 用户有领域优势的一级主题
# 对应 config.CONCEPT_HIERARCHY 的父级 key
# ============================================================
HUNTING_GROUND_DOMAINS = {
    "算力硬件",   # 芯片/设备/材料/封装/EDA
    "机器人",     # 机器人零部件/减速器/传感器
    "新能源",     # 电池/储能/光伏
    "化工材料",   # 半导体材料/光刻胶/电子特气
}

# ============================================================
# 卡脖子环节清单 — 核心知识资产
# 结构：{key: {label, keywords, a_stock_concepts}}
# keywords: 中英文混合，用于公告标题/正文匹配
# a_stock_concepts: 对应同花顺概念板块名称
# ============================================================
CHOKE_POINT_TAXONOMY = {
    "advanced_chip_fab": {
        "label": "先进制程芯片制造",
        "keywords": [
            "半导体", "EUV", "光刻机", "7nm", "5nm", "3nm", "先进制程",
            "FinFET", "GAA", "激光隐形切割", "晶圆切割",
            "激光切割设备", "划片机", "芯片制造",
        ],
        "a_stock_concepts": [
            "光刻机", "光刻胶", "半导体设备", "芯片概念",
            "先进封装", "第三代半导体", "中芯国际概念",
        ],
    },
    "advanced_packaging": {
        "label": "先进封装与异构集成",
        "keywords": [
            "Chiplet", "CoWoS", "2.5D封装", "3D封装", "TSV",
            "混合键合", "玻璃基板", "扇出型", "FCBGA",
            "晶圆级封装", "系统级封装",
        ],
        "a_stock_concepts": [
            "先进封装", "芯片概念", "玻璃基板", "PCB概念",
        ],
    },
    "wafer_fab_equipment": {
        "label": "晶圆制造设备与零部件",
        "keywords": [
            "刻蚀机", "薄膜沉积", "CVD", "PVD", "ALD",
            "离子注入", "清洗设备", "量测", "检测",
            "半导体设备零部件", "真空泵", "射频电源",
        ],
        "a_stock_concepts": [
            "半导体设备", "芯片概念", "中芯国际概念",
        ],
    },
    "semiconductor_materials": {
        "label": "半导体关键材料",
        "keywords": [
            "光刻胶", "电子特气", "CMP抛光液", "高纯试剂",
            "靶材", "硅片", "碳化硅", "SiC衬底", "GaN",
            "氮化镓", "ABF", "BT树脂", "封装基板",
        ],
        "a_stock_concepts": [
            "光刻胶", "半导体材料", "芯片概念", "第三代半导体",
            "氟化工概念", "PCB概念",
        ],
    },
    "eda_ip": {
        "label": "EDA工具与IP核",
        "keywords": [
            "EDA", "IP核", "芯片设计", "仿真", "验证",
            "版图", "SPICE",
        ],
        "a_stock_concepts": [
            "芯片概念", "信创", "国产操作系统",
        ],
    },
    "ai_chips": {
        "label": "AI算力芯片",
        "keywords": [
            "GPU", "NPU", "TPU", "AI芯片", "推理芯片",
            "训练芯片", "HBM", "高带宽内存",
        ],
        "a_stock_concepts": [
            "芯片概念", "存储芯片", "算力租赁", "英伟达概念",
            "华为昇腾",
        ],
    },
    "robotics_core": {
        "label": "机器人核心零部件",
        "keywords": [
            "谐波减速器", "RV减速器", "伺服电机", "六维力传感器",
            "力矩传感器", "滚珠丝杠", "行星滚柱丝杠",
            "空心杯电机", "无框力矩电机",
        ],
        "a_stock_concepts": [
            "机器人概念", "人形机器人", "减速器", "传感器",
            "工业母机", "机器视觉",
        ],
    },
    "solid_state_battery": {
        "label": "固态电池",
        "keywords": [
            "固态电池", "硫化物电解质", "氧化物电解质",
            "锂金属负极", "固态电解质",
        ],
        "a_stock_concepts": [
            "固态电池", "锂电池概念", "钠离子电池",
        ],
    },
}

# ============================================================
# 猎场股票缓存
# ============================================================

_HUNTING_GROUND_CACHE: Optional[set[str]] = None
_CACHE_PATH = Path(__file__).parent.parent / "data" / "hunting_ground_stocks.json"


def build_hunting_ground() -> set[str]:
    """从 STOCK_PRIMARY_CONCEPT + CONCEPT_HIERARCHY 计算猎场股票集。

    返回 {code6, ...}
    同时缓存到 JSON 文件供后续快速加载。
    """
    global _HUNTING_GROUND_CACHE
    try:
        from config import STOCK_PRIMARY_CONCEPT, CONCEPT_HIERARCHY
    except ImportError:
        return set()

    codes = set()
    for code, concept in STOCK_PRIMARY_CONCEPT.items():
        parent = CONCEPT_HIERARCHY.get(concept, "")
        if parent in HUNTING_GROUND_DOMAINS:
            codes.add(str(code).zfill(6))

    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(sorted(codes), ensure_ascii=False))
    _HUNTING_GROUND_CACHE = codes
    return codes


def load_hunting_ground() -> set[str]:
    """加载猎场股票集（优先缓存文件，其次重新计算）。"""
    global _HUNTING_GROUND_CACHE
    if _HUNTING_GROUND_CACHE is not None:
        return _HUNTING_GROUND_CACHE
    if _CACHE_PATH.exists():
        _HUNTING_GROUND_CACHE = set(json.loads(_CACHE_PATH.read_text()))
        return _HUNTING_GROUND_CACHE
    return build_hunting_ground()


def is_in_hunting_ground(code: str) -> bool:
    """快速检查：股票是否属于猎场（概念匹配）。"""
    return str(code).zfill(6) in load_hunting_ground()


def _text_matches_chokepoint(text: str) -> list[dict]:
    """检查文本是否匹配任一卡脖子环节关键词。

    返回匹配的环节列表 [{key, label, matched_keywords}]
    """
    matches = []
    for key, entry in CHOKE_POINT_TAXONOMY.items():
        hit_kws = [kw for kw in entry["keywords"] if kw.lower() in text.lower()]
        if hit_kws:
            matches.append({
                "key": key,
                "label": entry["label"],
                "matched_keywords": hit_kws,
            })
    return matches


def is_chokepoint_announcement(title: str, code: str = "", name: str = "") -> list[dict]:
    """检查公告是否涉及卡脖子环节（即使股票本身不在猎场）。

    检查范围：公告标题 + 股票名称。
    返回匹配的环节列表。
    """
    search_text = title
    if name:
        # 公告收购的标的公司名可能出现在 title 中
        # 股票名本身有时也含关键词（如「芯原股份」）
        pass
    return _text_matches_chokepoint(search_text)


def get_chokepoint_context(code: str, announcement_title: str = "",
                           announcement_text: str = "") -> dict:
    """获取一只股票+公告的卡脖子环节上下文。

    用于丰富 LLM prompt 的领域背景。
    返回 {"domains": [...], "chokepoints": [...], "concepts": [...]}
    """
    try:
        from config import STOCK_PRIMARY_CONCEPT, CONCEPT_HIERARCHY
    except ImportError:
        return {"domains": [], "chokepoints": [], "concepts": []}

    concepts = []
    domains = set()
    primary = STOCK_PRIMARY_CONCEPT.get(str(code).zfill(6), "")
    if primary:
        concepts.append(primary)
        parent = CONCEPT_HIERARCHY.get(primary, "")
        if parent in HUNTING_GROUND_DOMAINS:
            domains.add(parent)

    search_text = f"{announcement_title} {announcement_text[:500] if announcement_text else ''}"
    chokepoints = _text_matches_chokepoint(search_text)

    for cp in chokepoints:
        for c in CHOKE_POINT_TAXONOMY[cp["key"]]["a_stock_concepts"]:
            if c not in concepts:
                concepts.append(c)

    return {
        "domains": sorted(domains),
        "chokepoints": chokepoints,
        "concepts": concepts,
    }
