"""BOM 产业链分析 — 核心数据结构"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BomSegment:
    """产业链细分环节"""
    name: str = ""
    tier: str = ""                # 上游/中游/下游
    description: str = ""         # 环节描述
    products: list[str] = field(default_factory=list)
    demand_driver: str = ""       # 需求驱动因素
    supply_status: str = ""       # 供给格局（紧缺/平衡/过剩）
    key_companies_hint: list[str] = field(default_factory=list)


@dataclass
class HighValueSegment:
    """三高赛道"""
    segment_name: str = ""
    tier: str = ""
    growth_logic: str = ""
    margin_est: str = ""
    barrier_level: str = ""
    supply_gap: str = ""
    market_size_hint: str = ""
    catalyst: str = ""


@dataclass
class LeaderStock:
    """赛道龙头"""
    code: str = ""
    name: str = ""
    segment: str = ""
    rank: int = 0
    moat_scores: MoatScores = field(default_factory=lambda: MoatScores())
    core_advantage: str = ""
    risk_note: str = ""
    pe_ttm: float = 0.0
    roe: float = 0.0
    revenue_cagr_3y: float = 0.0
    _hallucination_fixed: bool = False


@dataclass
class MoatScores:
    """护城河 6 维度评分 (0-10)"""
    tech: int = 0
    cost: int = 0
    scale: int = 0
    brand: int = 0
    switch_cost: int = 0
    network: int = 0

    @property
    def total(self) -> int:
        return self.tech + self.cost + self.scale + self.brand + self.switch_cost + self.network

    @property
    def avg(self) -> float:
        return self.total / 6


@dataclass
class BomAnalysisResult:
    """一次完整 BOM 分析"""
    industry: str = ""
    date: str = ""
    segments: list[BomSegment] = field(default_factory=list)
    high_value_segments: list[HighValueSegment] = field(default_factory=list)
    leaders: list[LeaderStock] = field(default_factory=list)
    stage1_json: dict | None = None
    stage2_json: dict | None = None
