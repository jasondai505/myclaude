"""每日复盘系统 - 分析引擎（re-export facade）
所有实现已拆分到子模块：
  engine_market   — 大盘/风格/行业/北向/外围
  engine_themes   — 题材词频/分级/三池合并/审美
  engine_sentiment — 情绪面/连板梯队/逻辑情绪四维分类
  engine_stocks   — 个股扫描/基本面/FEV/建议生成
  engine_focus    — 聚焦池/综合评分
"""
from engine_market import (
    analyze_market, analyze_style, analyze_sectors,
    analyze_northbound, analyze_global,
)
from engine_sentiment import (
    analyze_sentiment, classify_limit_up_type, apply_limit_up_classification,
)
from engine_themes import (
    analyze_themes, build_theme_stock_details, expand_theme_stocks,
    classify_themes_by_trend, rate_theme, normalize_theme,
    build_merged_theme_pool, attach_merged_to_themes, analyze_theme_aesthetics,
)
from engine_stocks import (
    analyze_single_stock, analyze_watchlist_themes, analyze_fundamentals,
    score_fev, check_surge_preconditions, check_crash_warnings,
    generate_suggestions, _extract_rsi,
)
from engine_focus import (
    build_focus_pool, compute_composite_score,
)
