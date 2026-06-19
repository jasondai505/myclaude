"""A股交易日历 — 基于 akshare 新浪财经交易日历，本地缓存。
用法:
    from daily_review.trade_calendar import is_trading_day, next_trading_day
    if not is_trading_day():
        print("今日休市，跳过")
"""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

CACHE_PATH = Path(__file__).resolve().parent / "data" / "trade_calendar.json"
CACHE_MAX_AGE_DAYS = 30

_trading_dates: set[str] | None = None


def _load_cache() -> set[str] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        cached_date = data.get("cached_date", "")
        if cached_date:
            age = (date.today() - date.fromisoformat(cached_date)).days
            if age > CACHE_MAX_AGE_DAYS:
                return None
        return set(data.get("trading_dates", []))
    except Exception:
        return None


def _save_cache(dates: set[str]):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cached_date": date.today().isoformat(),
        "trading_dates": sorted(dates),
    }
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _fetch_trading_dates() -> set[str]:
    cached = _load_cache()
    if cached:
        return cached

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        dates = set(str(d) for d in df["trade_date"])
        if len(dates) > 100:
            _save_cache(dates)
        return dates
    except Exception as e:
        print(f"[trade_calendar] akshare 交易日历获取失败: {e}")
        cached = _load_cache()
        if cached:
            print(f"[trade_calendar] 使用过期缓存 ({len(cached)} 条)")
            return cached
        print("[trade_calendar] 无缓存，假定为交易日")
        return set()


def _get_trading_dates() -> set[str]:
    global _trading_dates
    if _trading_dates is None:
        _trading_dates = _fetch_trading_dates()
    return _trading_dates


def is_trading_day(d: date | str | None = None) -> bool:
    if d is None:
        d = date.today()
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.isoformat() in _get_trading_dates()


def next_trading_day(d: date | str | None = None) -> date:
    if d is None:
        d = date.today()
    if isinstance(d, str):
        d = date.fromisoformat(d)
    dates = _get_trading_dates()
    for i in range(1, 31):
        nd = d + timedelta(days=i)
        if nd.isoformat() in dates:
            return nd
    return d + timedelta(days=1)  # fallback


def prev_trading_day(d: date | str | None = None) -> date:
    if d is None:
        d = date.today()
    if isinstance(d, str):
        d = date.fromisoformat(d)
    dates = _get_trading_dates()
    for i in range(1, 31):
        pd_ = d - timedelta(days=i)
        if pd_.isoformat() in dates:
            return pd_
    return d - timedelta(days=1)  # fallback


def refresh_cache():
    global _trading_dates
    _trading_dates = None
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
    _get_trading_dates()


if __name__ == "__main__":
    today = date.today()
    print(f"今日 {today.isoformat()}: {'交易日' if is_trading_day() else '休市'}")
    print(f"上一个交易日: {prev_trading_day().isoformat()}")
    print(f"下一个交易日: {next_trading_day().isoformat()}")
