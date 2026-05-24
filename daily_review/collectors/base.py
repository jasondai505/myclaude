"""每日采集共用工具：交易日判定、重试、日期范围、报告路径。"""
from __future__ import annotations

import time
import functools
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from ..utils import setup_console
setup_console()

from ..config import REPORT_DIR

FEEDS_DIR: Path = REPORT_DIR / "feeds"
FEEDS_DIR.mkdir(parents=True, exist_ok=True)


def today() -> date:
    return date.today()


def daterange(since: date, until: date) -> list[date]:
    if since > until:
        return []
    days = (until - since).days + 1
    return [since + timedelta(days=i) for i in range(days)]


def fmt_compact(d: date) -> str:
    return d.strftime("%Y%m%d")


def fmt_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


_TRADE_CAL_CACHE: set[str] | None = None


def _load_trade_cal() -> set[str]:
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is not None:
        return _TRADE_CAL_CACHE
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            _TRADE_CAL_CACHE = set()
        else:
            _TRADE_CAL_CACHE = {str(d)[:10] for d in df["trade_date"]}
    except Exception as e:
        print(f"  [WARN] 交易日历加载失败，退化为按自然日(排除周末)补全: {e}")
        _TRADE_CAL_CACHE = set()
    return _TRADE_CAL_CACHE


def is_trading_day(d: date) -> bool:
    cal = _load_trade_cal()
    if not cal:
        return d.weekday() < 5
    return fmt_iso(d) in cal


def trading_dates(since: date, until: date) -> list[date]:
    return [d for d in daterange(since, until) if is_trading_day(d)]


def with_retry(retries: int = 2, delay: float = 1.0, on_fail=None):
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if attempt < retries:
                        time.sleep(delay * (attempt + 1))
                    else:
                        print(f"  [WARN] {fn.__name__} 失败({retries+1}次): {e}")
            return on_fail
        return wrapper
    return deco


def feed_md_path(source: str, d: date) -> Path:
    return FEEDS_DIR / f"{source}_{fmt_iso(d)}.md"


def md_header(title: str, d: date, count: int, universe_size: int) -> list[str]:
    return [
        f"# {title}  {fmt_iso(d)}",
        "",
        f"> 自选+异动股池: **{universe_size}** 只  |  本日条目: **{count}**  |  生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]


def progress(msg: str):
    print(f"  · {msg}", flush=True)


def section(msg: str):
    print(f"\n[{msg}]", flush=True)
