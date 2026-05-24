"""daily_review — A股每日复盘系统"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from .utils import setup_console
setup_console()
