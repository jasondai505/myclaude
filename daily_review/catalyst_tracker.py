"""催化剂生命周期跟踪 — 走势交叉确认 + 历史催化重新提醒

用法:
    python daily_review/catalyst_tracker.py              # 今天
    python daily_review/catalyst_tracker.py --date 2026-06-12  # 指定日期

逻辑:
    1. 查14天内 actionability>=40 且未被走势确认的催化
    2. Redis 实时行情检查映射标的今日走势
    3. 标的涨停/大涨 → 标记 price_confirmed → 输出重新提醒
    4. 14天无动静 → expired
"""
import sys, json, argparse
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")

import redis
from store import (
    init_db, query_catalyst_by_date, query_catalyst_stocks, save_catalyst_signals,
)
from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, REDIS_MARKET_KEY,
    STOCK_PRIMARY_CONCEPT, REPORT_DIR,
)
from data import _normalize_code, _market_prefix, calc_limit_price, _stock_board

FEEDS_DIR = REPORT_DIR / "feeds"
FEEDS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path(__file__).parent / "data" / "commonality_cache"

LOOKBACK_DAYS = 14
MIN_ACTIONABILITY = 40
CONFIRM_LIMIT_UP = 3       # 涨停得分
CONFIRM_STRONG_CHG = 2     # 涨>5%得分
CONFIRM_VOL_SPIKE = 1      # 放量得分
CONFIRM_THRESHOLD = 3       # 累计>=3分视为确认


def _get_redis():
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
        db=REDIS_DB, decode_responses=True, protocol=2,
        socket_connect_timeout=5, socket_timeout=10,
    )


def fetch_redis_quotes() -> dict[str, dict]:
    """Redis 实时行情 → {code6: {price, change_pct, is_limit_up, vol_ratio, ...}}"""
    try:
        r = _get_redis()
        raw = r.hgetall(REDIS_MARKET_KEY)
    except Exception as e:
        print(f"  [WARN] Redis unavailable: {e}")
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


def _check_pool_history(stock_codes: set[str], lookback: int = 3) -> dict[str, int]:
    """检查标的在最近N天的 commonality_cache 中出现了多少次（强势池）"""
    appearances = defaultdict(int)
    today = date.today()
    for i in range(lookback):
        d = (today - timedelta(days=i)).isoformat()
        cache_file = CACHE_DIR / f"scan_{d}.json"
        if not cache_file.exists():
            continue
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            pool_codes = set()
            for concept, stocks in data.get("concept_stocks", {}).items():
                for s in stocks:
                    pool_codes.add(s.get("code", ""))
            for code in stock_codes:
                if code in pool_codes:
                    appearances[code] += 1
        except Exception:
            pass
    return appearances


def _score_stock_signal(q: dict, pool_days: int) -> int:
    """单只标的的走势信号强度"""
    score = 0
    if q.get("is_limit_up"):
        score += CONFIRM_LIMIT_UP
    if q.get("change_pct", 0) >= 5:
        score += CONFIRM_STRONG_CHG
    if q.get("vol_ratio", 0) >= 2 and q.get("change_pct", 0) > 0:
        score += CONFIRM_VOL_SPIKE
    if pool_days >= 2:
        score += 1  # 连续出现在强势池
    return score


def track(today_str: str):
    init_db()
    print(f"[catalyst_tracker] {today_str}")

    # 1. 获取 Redis 实时行情
    quotes = fetch_redis_quotes()
    if not quotes:
        print("  [SKIP] Redis not available")
        return
    print(f"  Redis stocks: {len(quotes)}")

    # 2. 收集过去14天内所有活跃催化（actionability>=40）
    today_date = date.fromisoformat(today_str)
    all_signals = []
    for i in range(LOOKBACK_DAYS):
        d = (today_date - timedelta(days=i)).isoformat()
        signals = query_catalyst_by_date(d, min_score=MIN_ACTIONABILITY)
        all_signals.extend(signals)

    if not all_signals:
        print(f"  No active catalysts in {LOOKBACK_DAYS} days")
        return

    # 去重（同一个 catalyst_name 可能出现在多天，只取最新的一条）
    seen = {}
    for s in sorted(all_signals, key=lambda x: x.get("date", "")):
        seen[s["catalyst_name"]] = s
    unconfirmed = [s for s in seen.values() if not s.get("price_confirmed", 0)]

    print(f"  Active catalysts: {len(seen)}, unconfirmed: {len(unconfirmed)}")

    # 3. 对每条未确认催化，检查映射标的走势
    confirmed_catalysts = []
    all_stock_signals = {}

    for sig in unconfirmed:
        cname = sig["catalyst_name"]
        stocks = query_catalyst_stocks(sig["date"], cname)
        if not stocks:
            continue

        codes = set(s["stock_code"] for s in stocks if s["stock_code"])
        if not codes:
            continue

        pool_days = _check_pool_history(codes, 3)

        total_score = 0
        stock_details = []
        for code in codes:
            q = quotes.get(code, {})
            if not q:
                continue
            pd = pool_days.get(code, 0)
            score = _score_stock_signal(q, pd)
            total_score += score
            if score > 0:
                stock_details.append({
                    "code": code, "name": q.get("name", "?"),
                    "chg": q.get("change_pct", 0),
                    "limit_up": q.get("is_limit_up", False),
                    "vol_ratio": q.get("vol_ratio", 0),
                    "pool_days": pd, "score": score,
                })

        if total_score >= CONFIRM_THRESHOLD:
            sig["stock_signals"] = stock_details
            sig["confirm_score"] = total_score
            confirmed_catalysts.append(sig)
        all_stock_signals[cname] = stock_details

    # 4. 输出
    new_confirms = [c for c in confirmed_catalysts
                    if c.get("date", "") == today_str]
    old_reactivations = [c for c in confirmed_catalysts
                         if c.get("date", "") != today_str]

    print(f"  走势确认: {len(confirmed_catalysts)} (新催化{len(new_confirms)} + 历史复活{len(old_reactivations)})")

    # 生成报告
    L = []
    def w(s=""): L.append(s)

    w(f"# 催化剂走势跟踪 {today_str}")
    w(f"\n> 扫描 {LOOKBACK_DAYS} 天内 {len(seen)} 条活性催化 | 走势确认 {len(confirmed_catalysts)} 条\n")

    if old_reactivations:
        w("## 历史催化复活 — 前期逻辑被走势确认\n")
        for c in old_reactivations[:5]:
            days_ago = (today_date - date.fromisoformat(c["date"])).days
            w(f"### {c['catalyst_name']} (←{days_ago}天前, 原行动性{c.get('actionability',0)}分)")
            w(f"- **原逻辑**: {c.get('thesis','')[:200]}")
            w(f"- **走势确认**: {len(c.get('stock_signals',[]))}只标的异动")
            for sd in sorted(c.get("stock_signals", []), key=lambda x: -x["score"])[:5]:
                flags = []
                if sd["limit_up"]: flags.append("涨停")
                if sd["chg"] >= 5: flags.append(f"+{sd['chg']:.1f}%")
                if sd["vol_ratio"] >= 2: flags.append(f"量比{sd['vol_ratio']:.1f}")
                w(f"  - {sd['code']} {sd['name']} {' '.join(flags)} (得分{sd['score']})")
            w()

    if new_confirms:
        w("## 今日催化已确认\n")
        for c in new_confirms[:5]:
            w(f"- **{c['catalyst_name']}** ({c.get('actionability',0)}分) — "
              f"{len(c.get('stock_signals',[]))}只标的异动")

    if not confirmed_catalysts:
        w("## 走势扫描：无新增确认\n")
        w("当前活性催化均未出现显著走势信号，继续跟踪中。")

    # 列出仍在跟踪的未确认催化
    still_tracking = [s for s in unconfirmed if s not in confirmed_catalysts]
    if still_tracking:
        w("\n---\n")
        w("## 仍在跟踪（走势尚未确认）\n")
        for s in still_tracking[:10]:
            days_ago = (today_date - date.fromisoformat(s["date"])).days
            codes_in_pool = all_stock_signals.get(s["catalyst_name"], [])
            best_chg = max((sd["chg"] for sd in codes_in_pool), default=0)
            w(f"- [{s.get('catalyst_type','?')}] **{s['catalyst_name']}** "
              f"({days_ago}d ago, {s.get('actionability',0)}分) "
              f"最佳标的{days_ago}日涨幅{best_chg:+.1f}%")

    out = FEEDS_DIR / f"catalyst_track_{today_str}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  Report: {out}")

    # 5. 更新 DB: 标记走势确认
    for c in confirmed_catalysts:
        c["price_confirmed"] = 1
        c["price_confirm_date"] = today_str
        c["validation_note"] = f"走势确认({today_str}): {c.get('confirm_score',0)}分"
    if confirmed_catalysts:
        save_catalyst_signals(confirmed_catalysts)

    return confirmed_catalysts, old_reactivations


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", "-d", type=str, default=date.today().isoformat())
    args = parser.parse_args()
    track(args.date)
