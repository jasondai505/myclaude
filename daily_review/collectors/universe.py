"""每日股池：自选股 + 当日涨幅前50 + 当日人气前50。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from config import WATCHLIST
from .base import fmt_iso, with_retry, progress

TOP_GAINERS_N = 50
TOP_HOT_N = 50


def _watchlist() -> set[str]:
    return {str(c).zfill(6) for c in WATCHLIST}


@with_retry(retries=1, delay=0.5, on_fail=set())
def _fetch_top_gainers(top_n: int = TOP_GAINERS_N) -> set[str]:
    from daily_review.data import redis_quote_all, redis_available

    if redis_available():
        quotes = redis_quote_all()
        if quotes:
            sorted_codes = sorted(
                quotes.keys(),
                key=lambda c: quotes[c].get("change_pct", 0),
                reverse=True,
            )
            return {c for c in sorted_codes[:top_n] if quotes[c].get("change_pct", 0) > 0}

    # 兜底: akshare
    import akshare as ak
    from daily_review.data import _run_with_timeout
    df = _run_with_timeout(lambda: ak.stock_zh_a_spot_em(), 30, default=None)
    if df is None or df.empty:
        return set()
    if "涨跌幅" not in df.columns or "代码" not in df.columns:
        return set()
    df = df[df["涨跌幅"].notna()].copy()
    df = df.sort_values("涨跌幅", ascending=False).head(top_n)
    return {str(c).zfill(6) for c in df["代码"]}


@with_retry(retries=1, delay=0.5, on_fail=set())
def _fetch_top_hot(top_n: int = TOP_HOT_N) -> set[str]:
    import data
    rows = data.fetch_hot_stocks(top_n=top_n)
    return {str(r["code"]).zfill(6) for r in rows if r.get("code")} if rows else set()


def daily_universe(d: date, include_dynamic: bool = True) -> set[str]:
    """当日股池 = 自选 ∪ 涨幅前50 ∪ 人气前50（异动榜仅当日有效）。"""
    wl = _watchlist()
    if include_dynamic and d == date.today():
        gainers = _fetch_top_gainers() or set()
        hot = _fetch_top_hot() or set()
    else:
        gainers, hot = set(), set()
    universe = wl | gainers | hot
    progress(
        f"universe@{fmt_iso(d)}: 自选{len(wl)} + 涨幅前{TOP_GAINERS_N} {len(gainers)} "
        f"+ 人气前{TOP_HOT_N} {len(hot)} = 去重后 {len(universe)} 只"
    )
    return universe


def watchlist_only() -> set[str]:
    return _watchlist()


if __name__ == "__main__":
    u = daily_universe(date.today())
    print(f"共 {len(u)} 只: {sorted(u)[:20]}{'...' if len(u) > 20 else ''}")
