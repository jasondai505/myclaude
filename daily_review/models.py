"""核心数据结构 — 替代模块间裸 dict 传递"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StockQuote:
    code: str = ""
    name: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    amount_wan: float = 0.0
    turnover_pct: float = 0.0
    vol_ratio: float = 0.0
    amplitude_pct: float = 0.0
    pe_ttm: float = 0.0
    pb: float = 0.0
    mcap_yi: float = 0.0
    limit_up: float = 0.0
    change_pct_5d: float | None = None
    tag: str = ""


@dataclass
class IndexData:
    label: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    amount_wan: float = 0.0
    amplitude_pct: float = 0.0
    change_pct_5d: float | None = None


@dataclass
class ThemeEntry:
    theme: str = ""
    level: int = 1
    label: str = ""
    today_count: int = 0
    consecutive_days: int = 0
    cumulative_stocks: int = 0
    narrative: str = ""
    alpha_label: str = ""
    surge_score: int = 0
    surge_max: int = 5
    driver: str = ""


@dataclass
class StockAnalysis:
    code: str = ""
    name: str = ""
    quote: dict | None = None
    signals: list = field(default_factory=list)
    trend_score: int = 0


@dataclass
class FEVScore:
    code: str = ""
    name: str = ""
    fev_total: int = 0
    f_score: int = 0
    e_score: int = 0
    v_score: int = 0
    f_reasons: list[str] = field(default_factory=list)
    e_reasons: list[str] = field(default_factory=list)
    v_reasons: list[str] = field(default_factory=list)
    forward_pe: float | None = None
    cagr: float | None = None
    inst_count: int = 0
    holder_chg: float | None = None
    alpha_bucket: str | None = None
    surge_score: int = 0
    surge_details: list[str] = field(default_factory=list)
    crash_warnings: list[str] = field(default_factory=list)
