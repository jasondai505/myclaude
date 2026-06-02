"""BOM 产业链分析 — 配置"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompts"
REPORT_DIR = BASE_DIR / "reports"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bom.db"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "claude-sonnet-4-6-20250514"
BASE_URL = "https://api.deepseek.com/anthropic"
STAGE1_MAX_TOKENS = 8000
STAGE2_MAX_TOKENS = 12000
LLM_TIMEOUT = 120

H3_GROSS_MARGIN_MIN = 30.0
H3_ROE_MIN = 15.0
H3_REVENUE_CAGR_3Y_MIN = 20.0

FETCH_DELAY = 0.3
REQUEST_TIMEOUT = 15
