"""催化剂生命周期跟踪 — 走势交叉确认 + 概念预热 + 历史催化重新提醒

用法:
    python daily_review/catalyst_tracker.py              # 今天
    python daily_review/catalyst_tracker.py --date 2026-06-12  # 指定日期

逻辑:
    1. 查14天内 actionability>=40 且未被走势确认的催化
    2. Redis 实时行情检查映射标的今日走势
    3. 标的涨停/大涨 → 标记 price_confirmed → 输出重新提醒
    4. 个股未确认 → 检查概念热度（成分股协同异动）→ 标记 sector_heating
    5. 14天无动静（个股+概念皆无信号）→ expired
"""
import sys, json, argparse
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")

from store import (
    init_db, query_catalyst_by_date, query_catalyst_stocks,
    save_catalyst_signals, mark_catalyst_expired,
    get_active_deep_reads, mark_deep_read_price_confirmed,
)
from config import REPORT_DIR
from redis_quotes import fetch_redis_quotes

sys.path.insert(0, str(Path(__file__).parent.parent / "morning_intel"))
try:
    from notify import push as _push
except ImportError:
    def _push(title, content): return False

CATALYST_DIR = REPORT_DIR / "catalyst"
CATALYST_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path(__file__).parent / "data" / "commonality_cache"

LOOKBACK_DAYS = 14
MIN_ACTIONABILITY = 40
CONFIRM_LIMIT_UP = 3       # 涨停得分
CONFIRM_STRONG_CHG = 2     # 涨>5%得分
CONFIRM_VOL_SPIKE = 1      # 放量得分
CONFIRM_THRESHOLD = 3       # 累计>=3分视为确认
DR_CONFIRM_THRESHOLD = 2   # 深度研读标的确认阈值（更低，因深度研读代表高确信度）


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


# === 概念热度检查（自下而上增强） ===

_CONCEPT_BASELINE: dict[str, int] | None = None
_CONCEPT_HEAT_CACHE: dict[str, float] = {}

CONCEPT_HEAT_RATIO = 0.10     # 概念中 ≥10% 的成分股进入强势池视为升温
CONCEPT_HEAT_MIN_COUNT = 3    # 至少3只强势股
CONCEPT_HEAT_ABS_COUNT = 10   # 或绝对强势股 ≥10 只（大概念的宽基强度）


def _load_concept_baseline() -> dict[str, int]:
    """加载概念→成分股数的 baseline（来自 multi_concept_map.json）。"""
    global _CONCEPT_BASELINE
    if _CONCEPT_BASELINE is not None:
        return _CONCEPT_BASELINE
    try:
        mp = Path(__file__).parent / "data" / "multi_concept_map.json"
        data = json.loads(mp.read_text(encoding="utf-8"))
        _CONCEPT_BASELINE = data.get("baseline", {})
    except Exception:
        _CONCEPT_BASELINE = {}
    return _CONCEPT_BASELINE


def _load_today_concept_counts(today_str: str, fallback: bool = True) -> dict[str, int]:
    """从 commonality_cache 中加载各概念的强势股数量。
    若当日文件不存在，自动回退到最近一个可用文件。
    """
    cache_file = CACHE_DIR / f"scan_{today_str}.json"
    if not cache_file.exists() and fallback:
        files = sorted(CACHE_DIR.glob("scan_*.json"))
        if files:
            cache_file = files[-1]
        else:
            return {}
    if not cache_file.exists():
        return {}
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data.get("concept_counts", {})
    except Exception:
        return {}


def _check_concept_heat(today_str: str) -> dict[str, float]:
    """计算今日各概念的热度比例 (强势股数 / 概念总成分股数)。

    Returns: {concept_name: heat_ratio} 仅返回超过阈值的概念。
    """
    global _CONCEPT_HEAT_CACHE
    cache_key = f"heat_{today_str}"
    if cache_key in _CONCEPT_HEAT_CACHE:
        return _CONCEPT_HEAT_CACHE[cache_key]

    baseline = _load_concept_baseline()
    today_counts = _load_today_concept_counts(today_str)
    if not baseline or not today_counts:
        _CONCEPT_HEAT_CACHE[cache_key] = {}
        return {}

    hot = {}
    for concept, cnt in today_counts.items():
        base = baseline.get(concept, 0)
        if base == 0:
            continue  # 不在 baseline 的概念视为噪音，跳过
        if cnt < CONCEPT_HEAT_MIN_COUNT:
            continue
        if cnt >= CONCEPT_HEAT_ABS_COUNT:
            hot[concept] = round(cnt / base, 3)
        else:
            ratio = cnt / base
            if ratio >= CONCEPT_HEAT_RATIO:
                hot[concept] = round(ratio, 3)

    _CONCEPT_HEAT_CACHE[cache_key] = hot
    return hot


def _get_catalyst_concepts(sig: dict) -> set[str]:
    """从 catalyst_stock_map 获取催化关联的概念集合。"""
    cname = sig.get("catalyst_name", "")
    date_str = sig.get("date", "")
    if not cname or not date_str:
        return set()
    stocks = query_catalyst_stocks(date_str, cname)
    concepts = set()
    for s in stocks:
        mc = s.get("matched_concept", "")
        if mc and mc != "—":
            concepts.add(mc)
    return concepts


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

    # 3b. 概念热度检查 — 个股未确认，但催化关联的概念在升温 → sector_heating
    concept_heat = _check_concept_heat(today_str)
    sector_heating = []
    if concept_heat:
        for sig in unconfirmed:
            if sig in confirmed_catalysts:
                continue
            concepts = _get_catalyst_concepts(sig)
            hot = {c: concept_heat[c] for c in concepts if c in concept_heat}
            if hot:
                sig["hot_concepts"] = hot
                sig["heat_score"] = max(hot.values())
                sector_heating.append(sig)
        if sector_heating:
            print(f"  概念预热: {len(sector_heating)}条催化（概念在升温但个股走势未确认）")

    # 3c. 深度研读标的专项跟踪（确认阈值更低）
    dr_confirmed = []
    try:
        active_dr = get_active_deep_reads(min_score=60, lookback_days=14)
        for dr in active_dr:
            code = dr.get("code", "")
            if dr.get("price_confirmed", 0):
                continue
            q = quotes.get(code, {})
            if not q:
                continue
            pd = _check_pool_history({code}, 3).get(code, 0)
            score = _score_stock_signal(q, pd)
            if score >= DR_CONFIRM_THRESHOLD:
                dr["confirm_score"] = score
                dr["confirm_stock"] = {
                    "code": code, "name": q.get("name", "?"),
                    "chg": q.get("change_pct", 0),
                    "limit_up": q.get("is_limit_up", False),
                    "score": score,
                }
                # 计算延迟天数
                dr_date = date.fromisoformat(dr.get("date", today_str))
                days_lag = (today_date - dr_date).days
                dr["days_lag"] = days_lag
                dr["delayed"] = days_lag > 3
                dr_confirmed.append(dr)
                # 更新数据库
                try:
                    mark_deep_read_price_confirmed(code, dr.get("date", ""))
                except Exception:
                    pass
    except Exception as e:
        print(f"  [WARN] deep_read tracking failed: {e}")

    # 4. 输出
    new_confirms = [c for c in confirmed_catalysts
                    if c.get("date", "") == today_str]
    old_reactivations = [c for c in confirmed_catalysts
                         if c.get("date", "") != today_str]

    print(f"  走势确认: {len(confirmed_catalysts)} (新催化{len(new_confirms)} + 历史复活{len(old_reactivations)})")

    # 生成报告
    L = []
    def w(s=""): L.append(s)

    sh_count = len(sector_heating)
    w(f"# 催化剂走势跟踪 {today_str}")
    w(f"\n> 扫描 {LOOKBACK_DAYS} 天内 {len(seen)} 条活性催化 | "
      f"走势确认 {len(confirmed_catalysts)} 条"
      f"{' | 概念预热 ' + str(sh_count) + ' 条' if sh_count else ''}\n")

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

    if sector_heating:
        w("## 🔥 概念预热 — 个股走势未确认但概念在升温\n")
        w("> 以下催化的映射标的尚未出现强走势信号，但其关联概念今日成分股协同异动，"
          "可能是市场开始定价的前兆。\n")
        for c in sorted(sector_heating, key=lambda x: -x.get("heat_score", 0))[:10]:
            days_ago = (today_date - date.fromisoformat(c["date"])).days
            hot_concepts = c.get("hot_concepts", {})
            top_concept = max(hot_concepts, key=hot_concepts.get) if hot_concepts else "?"
            heat_pct = f"{hot_concepts.get(top_concept, 0)*100:.0f}%"
            w(f"- [{c.get('catalyst_type','?')}] **{c['catalyst_name']}** "
              f"({days_ago}d ago, 行动性{c.get('actionability',0)}分) → "
              f"概念 **{top_concept}** 强势占比 {heat_pct}")
            w(f"  > {c.get('thesis','')[:150]}")
        w()

    if not confirmed_catalysts and not sector_heating:
        w("## 走势扫描：无新增确认\n")
        w("当前活性催化均未出现显著走势信号，继续跟踪中。")

    # 列出仍在跟踪的未确认催化（排除已 sector_heating 的）
    still_tracking = [s for s in unconfirmed
                      if s not in confirmed_catalysts and s not in sector_heating]
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

    if dr_confirmed:
        w("\n## 深度研读标的走势确认\n")
        for dr in dr_confirmed[:10]:
            cs = dr.get("confirm_stock", {})
            delay_tag = f" ⚠️延迟{(dr.get('days_lag', 0))}日确认" if dr.get("delayed") else ""
            w(f"- [{dr.get('hunting_domain','?')}] **{dr.get('name','?')}** ({dr.get('code','?')}) "
              f"研读{dr.get('total_score',0)}分 | {cs.get('name','?')} +{cs.get('chg',0):.1f}% "
              f"(得分{dr.get('confirm_score',0)}){delay_tag}")
            w(f"  > {dr.get('investment_thesis','')[:120]}")
        if any(dr.get("delayed") for dr in dr_confirmed):
            w("\n> ⚠️ 部分标的在深度研读后超过3日才被市场确认，说明逻辑领先于价格。")

    out = CATALYST_DIR / f"catalyst_track_{today_str}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  Report: {out}")

    # 5. 更新 DB: 标记走势确认
    for c in confirmed_catalysts:
        c["price_confirmed"] = 1
        c["price_confirm_date"] = today_str
        c["validation_note"] = f"走势确认({today_str}): {c.get('confirm_score',0)}分"
    if confirmed_catalysts:
        save_catalyst_signals(confirmed_catalysts)

    # 6. 标记过期：14天前未确认的催化 → expired
    expiry_cutoff = (today_date - timedelta(days=LOOKBACK_DAYS)).isoformat()
    n_expired = mark_catalyst_expired(expiry_cutoff)
    if n_expired:
        print(f"  Expired: {n_expired} catalysts older than {LOOKBACK_DAYS}d")

    # 7. 推送通知（仅在有确认时）
    if confirmed_catalysts:
        push_title = f"✅ 催化走势确认 {len(confirmed_catalysts)}条" + (
            f"（其中{len(old_reactivations)}条历史复活）" if old_reactivations else "")
        push_lines = []
        for c in old_reactivations[:3]:
            days_ago = (today_date - date.fromisoformat(c["date"])).days
            push_lines.append(f"🔄 [{days_ago}天前] **{c['catalyst_name']}** — 复活")
        for c in new_confirms[:3]:
            push_lines.append(f"🆕 **{c['catalyst_name']}** ({c.get('actionability',0)}分)")
        if len(confirmed_catalysts) > 6:
            push_lines.append(f"\n... 共 {len(confirmed_catalysts)} 条确认")
        _push(push_title, "\n".join(push_lines))

    return confirmed_catalysts, old_reactivations


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", "-d", type=str, default=date.today().isoformat())
    args = parser.parse_args()
    track(args.date)
