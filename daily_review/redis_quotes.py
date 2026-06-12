"""Redis 实时行情查询 — catalyst_monitor / catalyst_tracker 公共依赖"""

import redis
from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, REDIS_MARKET_KEY,
)
from data import _normalize_code, _stock_board, calc_limit_price


def get_redis():
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
        db=REDIS_DB, decode_responses=True, protocol=2,
        socket_connect_timeout=5, socket_timeout=10,
    )


def fetch_redis_quotes() -> dict[str, dict]:
    """Redis 实时行情 → {code6: {name, price, change_pct, is_limit_up, vol_ratio}}"""
    try:
        r = get_redis()
        raw = r.hgetall(REDIS_MARKET_KEY)
    except Exception as e:
        print(f"  [WARN] Redis: {e}")
        return {}

    result = {}
    for code, csv_line in raw.items():
        parts = csv_line.split(",")
        if len(parts) < 38:
            continue
        try:
            price = float(parts[1]) if parts[1] else 0
            prev_close = float(parts[2]) if parts[2] else 0
            name = parts[0].strip() if parts[0] else ""
            vol_ratio = float(parts[37]) if parts[37] else 0
        except (ValueError, IndexError):
            continue
        if price <= 0 or prev_close <= 0:
            continue

        code6 = _normalize_code(code)
        board = _stock_board(code6)
        change_pct = round((price - prev_close) / prev_close * 100, 2)
        is_st = "ST" in name.upper()
        limit_up, _ = calc_limit_price(prev_close, board, is_st)
        is_limit_up = price >= limit_up - 0.001

        result[code6] = {
            "name": name, "price": price, "change_pct": change_pct,
            "is_limit_up": is_limit_up, "vol_ratio": vol_ratio,
        }
    return result
