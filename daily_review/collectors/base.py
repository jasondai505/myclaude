"""每日采集共用工具：交易日判定、重试、日期范围、报告路径。"""
from __future__ import annotations

import json
import time
import functools
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from utils import setup_console
setup_console()

from config import REPORT_DIR

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
_TRADE_CAL_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_calendar.json"


def _load_trade_cal() -> set[str]:
    global _TRADE_CAL_CACHE
    if _TRADE_CAL_CACHE is not None:
        return _TRADE_CAL_CACHE
    # 1) 文件缓存 (24h 有效)
    try:
        if _TRADE_CAL_PATH.exists():
            mtime = datetime.fromtimestamp(_TRADE_CAL_PATH.stat().st_mtime)
            if (datetime.now() - mtime).total_seconds() < 86400:
                data = json.loads(_TRADE_CAL_PATH.read_text(encoding="utf-8"))
                _TRADE_CAL_CACHE = set(data.get("dates", []))
                return _TRADE_CAL_CACHE
    except Exception:
        pass
    # 2) 从 akshare 下载
    try:
        import akshare as ak
        from daily_review.data import _run_with_timeout
        df = _run_with_timeout(lambda: ak.tool_trade_date_hist_sina(), 30, default=None)
        if df is not None and not df.empty:
            _TRADE_CAL_CACHE = {str(d)[:10] for d in df["trade_date"]}
        else:
            _TRADE_CAL_CACHE = set()
    except Exception as e:
        print(f"  [WARN] 交易日历加载失败，退化为按自然日(排除周末)补全: {e}")
        _TRADE_CAL_CACHE = set()
    # 3) 写文件缓存
    if _TRADE_CAL_CACHE:
        try:
            _TRADE_CAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TRADE_CAL_PATH.write_text(
                json.dumps({"dates": sorted(_TRADE_CAL_CACHE)}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
    return _TRADE_CAL_CACHE


def is_trading_day(d: date) -> bool:
    cal = _load_trade_cal()
    if not cal:
        return d.weekday() < 5
    return fmt_iso(d) in cal


def trading_dates(since: date, until: date) -> list[date]:
    return [d for d in daterange(since, until) if is_trading_day(d)]


def with_retry(retries: int = 2, delay: float = 1.0, on_fail=None):
    """指数退避 + 随机 jitter，避免固定间隔撞限流。"""
    import random as _random
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if attempt < retries:
                        backoff = delay * (2 ** attempt) + _random.uniform(0, 1)
                        time.sleep(backoff)
                    else:
                        print(f"  [WARN] {fn.__name__} 失败({retries+1}次): {e}")
            return on_fail
        return wrapper
    return deco


def feed_md_path(source: str, d: date) -> Path:
    """feeds/{source}/{source}_{date}.md — 按来源分目录，Obsidian 可折叠"""
    subdir = FEEDS_DIR / source
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir / f"{source}_{fmt_iso(d)}.md"


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
