"""晨间情报+盘面验证 — 配置文件"""
from pathlib import Path

BASE_DIR = Path(__file__).parent
SC_DB_PATH = BASE_DIR / "data" / "supply_chain.db"
REPORT_DIR = BASE_DIR / "reports"
PROMPT_DIR = BASE_DIR / "prompts"
DROPS_DIR = BASE_DIR / "drops"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DROPS_DIR.mkdir(parents=True, exist_ok=True)

# LLM
MODEL_INTERPRET = "claude-sonnet-4-6-20250514"
MODEL_AUDIT = "claude-haiku-4-5-20251001"
LLM_TIMEOUT = 120
LLM_MAX_TOKENS = 4000

# 盘中验证
VALIDATE_TIMES = ["10:30", "14:00"]
VALIDATE_CHG_THRESHOLD = 2.0
VALIDATE_VOL_THRESHOLD = 1.2
VALIDATE_FLOW_THRESHOLD = 500

FEEDS_LOOKBACK_DAYS = 2

# 盘中增量刷新
ZSYNC_MAX_PAGES = 3       # 盘中星球增量同步页数（盘前全量用 10）

# 微信推送通知 (PushPlus)
PUSHPLUS_TOKEN = "9cdb736206654981a8b230bee39ee56d"
PUSHPLUS_TOPIC = "morning_intel"
