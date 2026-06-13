"""公告深度研读存档系统 — 领域漏斗 + 硬门槛 + LLM五维精读 + Obsidian存档"""

from .knowledge_base import (
    HUNTING_GROUND_DOMAINS,
    CHOKE_POINT_TAXONOMY,
    build_hunting_ground,
    is_in_hunting_ground,
    get_chokepoint_context,
)
from .hard_filters import stage1_filter
from .llm_deep_read import deep_read_announcement
from .obsidian_archive import write_obsidian_file, update_tracking_table
