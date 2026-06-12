"""催化剂盘中监控 — Redis 实时行情检查今日催化标的异动

用法:
    python daily_review/catalyst_monitor.py              # 今天
    python daily_review/catalyst_monitor.py --date 2026-06-12

逻辑:
    1. 读取今日 catalyst_screen JSON → 提取所有映射标的
    2. Redis 实时行情检查每只标的
    3. 异动(涨>3%/涨停/放量) → 关联回催化事件 → 输出提醒
"""
import sys, json, argparse
from pathlib import Path
from datetime import date
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")

import redis
from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, REDIS_MARKET_KEY,
    REPORT_DIR,
)
from data import _normalize_code, _stock_board, calc_limit_price

FEEDS_DIR = REPORT_DIR / "feeds"
FEEDS_DIR.mkdir(parents=True, exist_ok=True)

ALERT_CHG_PCT = 3.0       # 涨幅超过此值触发提醒
ALERT_VOL_RATIO = 2.0     # 量比超过此值触发提醒
ALERT_LIMIT_UP = True     # 涨停始终提醒


def _get_redis():
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
        db=REDIS_DB, decode_responses=True, protocol=2,
        socket_connect_timeout=5, socket_timeout=10,
    )


def fetch_redis_quotes() -> dict[str, dict]:
    try:
        r = _get_redis()
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


def load_catalyst_stocks(today_str: str) -> dict[str, list[dict]]:
    """读取今日催化筛查结果 → {catalyst_name: [{code, name, confidence}, ...]}"""
    path = FEEDS_DIR / f"catalyst_screen_{today_str}.json"
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    result = {}
    stock_maps = data.get("stock_maps", {})
    for cname, stocks in stock_maps.items():
        result[cname] = stocks
    return result


def monitor(today_str: str):
    print(f"[catalyst_monitor] {today_str}")

    catalyst_stocks = load_catalyst_stocks(today_str)
    if not catalyst_stocks:
        print("  [SKIP] No catalyst screen data for today")
        return

    total_mapped = sum(len(v) for v in catalyst_stocks.values())
    print(f"  Monitoring {len(catalyst_stocks)} catalysts, {total_mapped} mapped stocks")

    quotes = fetch_redis_quotes()
    if not quotes:
        print("  [SKIP] Redis unavailable")
        return

    alerts = []
    seen = set()

    for cname, stocks in catalyst_stocks.items():
        for s in stocks:
            code = s.get("code", "")
            if not code or code in seen:
                continue
            seen.add(code)

            q = quotes.get(code, {})
            if not q:
                continue

            chg = q.get("change_pct", 0)
            limit_up = q.get("is_limit_up", False)
            vol = q.get("vol_ratio", 0)

            triggered = False
            reasons = []

            if ALERT_LIMIT_UP and limit_up:
                triggered = True
                reasons.append("涨停")
            elif chg >= ALERT_CHG_PCT:
                triggered = True
                reasons.append(f"+{chg:.1f}%")
            if vol >= ALERT_VOL_RATIO and chg > 0:
                if not triggered:
                    triggered = True
                reasons.append(f"量比{vol:.1f}")

            if triggered:
                alerts.append({
                    "catalyst": cname,
                    "code": code,
                    "name": q.get("name", "?"),
                    "chg": chg,
                    "limit_up": limit_up,
                    "vol_ratio": vol,
                    "confidence": s.get("confidence", "?"),
                    "reasons": reasons,
                })

    if alerts:
        alerts.sort(key=lambda x: (-x["limit_up"], -x["chg"], -x["vol_ratio"]))

        print(f"\n  ALERTS ({len(alerts)} stocks):")
        for a in alerts:
            flags = " ".join(a["reasons"])
            print(f"    [{a['catalyst'][:40]}] {a['code']} {a['name']} {flags} (conf={a['confidence']})")

        # 按催化聚合输出
        by_catalyst = defaultdict(list)
        for a in alerts:
            by_catalyst[a["catalyst"]].append(a)

        L = []
        L.append(f"# 催化剂盘中监控 {today_str}")
        L.append(f"\n> 监控 {len(catalyst_stocks)} 条催化 {total_mapped} 只标的 | 异动 {len(alerts)} 只\n")

        for cname, items in by_catalyst.items():
            L.append(f"## {cname}")
            for a in items:
                flags = " ".join(a["reasons"])
                L.append(f"- **{a['code']} {a['name']}** {flags} (置信度={a['confidence']})")
            L.append("")

        out = FEEDS_DIR / f"catalyst_monitor_{today_str}.md"
        out.write_text("\n".join(L), encoding="utf-8")
        print(f"  Report: {out}")
    else:
        print("  No alerts (all catalyst stocks within normal range)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", "-d", type=str, default=date.today().isoformat())
    args = parser.parse_args()
    monitor(args.date)
