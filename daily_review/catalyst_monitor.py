"""催化剂盘中监控 — Redis 实时行情检查今日催化标的异动

用法:
    python daily_review/catalyst_monitor.py              # 今天
    python daily_review/catalyst_monitor.py --date 2026-06-12

逻辑:
    1. 监控池 = 今日催化标的 + 7天历史未确认 HIGH/CRITICAL 催化标的 + 盘中增量
    2. Redis 实时行情检查每只标的
    3. 异动(涨>3%/涨停/放量) → 关联回催化事件 → 输出提醒+推送
    4. 历史催化标的盘中异动 → 计算复活得分 → ≥4分推送盘中复活提醒
"""
import hashlib, re, sys, json, argparse
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")

from config import REPORT_DIR
from redis_quotes import fetch_redis_quotes

sys.path.insert(0, str(Path(__file__).parent.parent / "morning_intel"))
try:
    from notify import push as _push
except ImportError:
    def _push(title, content): return False

CATALYST_DIR = REPORT_DIR / "catalyst"
CATALYST_DIR.mkdir(parents=True, exist_ok=True)
INTEL_REPORTS = Path(__file__).parent.parent / "morning_intel" / "reports"

ALERT_CHG_PCT = 3.0
ALERT_VOL_RATIO = 2.0
ALERT_LIMIT_UP = True

# 盘中复活评分
RESURRECT_LIMIT_UP = 4       # 涨停
RESURRECT_STRONG_CHG = 3     # 涨>7%
RESURRECT_MODERATE_CHG = 2   # 涨>5%
RESURRECT_VOL_SPIKE = 2      # 量比>3
RESURRECT_CHG_VOL = 1        # 涨>3% + 量比>2
RESURRECT_THRESHOLD = 4      # 累计>=4分盘中复活

HISTORY_LOOKBACK = 7          # 历史催化回溯天数
HISTORY_MIN_SCORE = 40        # 历史催化最低行动性

CATALYST_KEYWORDS = [
    ("supply_shock", "停产|断供|出口管[制限]|禁运|限产|产能退出|减[产少].*[0-9]+%"),
    ("price_spike", "涨价|跳涨|暴涨|翻[倍番]|新高|提价|上调.*价"),
    ("demand_surge", "爆单|订单暴|供不应求|抢购|放量.*[0-9]+[倍%]"),
    ("policy_change", "新政|补贴|禁令|标准.*出台|法规.*实施"),
    ("tech_breakthrough", "突破|量产|认证.*通过|专利.*授权|良率.*提升"),
    ("order_contract", "签约|中标|大单|合同.*[0-9]+亿|框架协议"),
]


def _load_multi_map() -> dict | None:
    mp = Path(__file__).parent / "data" / "multi_concept_map.json"
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _scan_intraday_delta(today_str: str) -> dict[str, list[dict]]:
    """扫描盘中增量情报，提取新催化 → {catalyst_name: [{code, name, confidence}]}"""
    delta_path = INTEL_REPORTS / f"intraday_delta_{today_str}.md"
    if not delta_path.exists():
        return {}

    try:
        text = delta_path.read_text(encoding="utf-8")
    except Exception:
        return {}

    if len(text) < 50:
        return {}

    # 内容去重缓存
    cache_path = CATALYST_DIR / f"_intraday_hash_{today_str}.txt"
    content_hash = hashlib.md5(text.encode()).hexdigest()
    if cache_path.exists():
        try:
            if cache_path.read_text().strip() == content_hash:
                return {}
        except Exception:
            pass

    # 从文本中提取所有6位股票代码
    codes_found = set(re.findall(r"\b(\d{6})\b", text))

    # 关键词匹配催化
    mm = _load_multi_map()
    concept_stocks = defaultdict(list)
    if mm:
        for code, concepts in mm.get("stocks", {}).items():
            for c in concepts:
                concept_stocks[c.lower()].append(code)

    result = {}
    for ctype, pattern in CATALYST_KEYWORDS:
        for m in re.finditer(pattern, text):
            # 提取匹配处前后各80字作为上下文
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 80)
            ctx = text[start:end].replace("\n", " ").strip()
            if len(ctx) > 120:
                ctx = ctx[:120] + "..."

            cname = f"[盘中{ctype}] {ctx}"
            # 如果太长，截断
            if len(cname) > 80:
                cname = cname[:77] + "..."

            matched_codes = []
            # 从上下文提取代码
            ctx_codes = set(re.findall(r"\b(\d{6})\b", text[max(0, m.start()-200):m.end()+200]))
            for code in ctx_codes:
                matched_codes.append({
                    "code": code, "name": "",
                    "confidence": "low", "method": "intraday_scan",
                })

            # 关键词匹配概念→股票
            if mm and len(matched_codes) < 5:
                kw = m.group(0).lower()
                for concept, codes in concept_stocks.items():
                    if kw in concept or any(t in concept for t in kw.split()):
                        for code in codes[:3]:
                            if code not in {x["code"] for x in matched_codes}:
                                matched_codes.append({
                                    "code": code, "name": "",
                                    "confidence": "low", "method": "concept_match",
                                })

            if matched_codes:
                result[cname] = matched_codes[:5]

    cache_path.write_text(content_hash)
    return result


def _load_history_catalysts(today_str: str) -> dict[str, list[dict]]:
    """DB 查询过去 N 天未确认的 HIGH/CRITICAL 催化及其映射标的"""
    try:
        from store import init_db, query_catalyst_by_date, query_catalyst_stocks
        init_db()
    except Exception:
        return {}

    today_date = date.fromisoformat(today_str)
    result = {}
    seen = set()
    for i in range(1, HISTORY_LOOKBACK + 1):
        d = (today_date - timedelta(days=i)).isoformat()
        signals = query_catalyst_by_date(d, min_score=HISTORY_MIN_SCORE)
        for s in signals:
            if s.get("price_confirmed") or s.get("expired"):
                continue
            cname = f"[历史复活池] {s['catalyst_name']}"
            if cname in seen:
                continue
            stocks = query_catalyst_stocks(d, s["catalyst_name"])
            if stocks:
                result[cname] = [{
                    "code": st["stock_code"], "name": st.get("stock_name", ""),
                    "confidence": st.get("confidence", "low"),
                    "catalyst_date": d,
                    "actionability": s.get("actionability", 0),
                } for st in stocks if st.get("stock_code")]
                seen.add(cname)
    return result


def _score_resurrection(q: dict) -> tuple[int, list[str]]:
    """盘中复活得分 + 触发原因列表"""
    score = 0
    reasons = []
    chg = q.get("change_pct", 0)
    limit_up = q.get("is_limit_up", False)
    vol = q.get("vol_ratio", 0)

    if limit_up:
        score += RESURRECT_LIMIT_UP
        reasons.append("涨停")
    elif chg >= 7:
        score += RESURRECT_STRONG_CHG
        reasons.append(f"+{chg:.1f}%")
    elif chg >= 5:
        score += RESURRECT_MODERATE_CHG
        reasons.append(f"+{chg:.1f}%")
    if vol >= 3 and chg > 0:
        score += RESURRECT_VOL_SPIKE
        reasons.append(f"量比{vol:.1f}")
    elif vol >= 2 and chg >= ALERT_CHG_PCT:
        score += RESURRECT_CHG_VOL
        reasons.append(f"+{chg:.1f}%+量比{vol:.1f}")
    return score, reasons


def load_catalyst_stocks(today_str: str) -> dict[str, list[dict]]:
    """监控池 = 今日催化 + 7天历史未确认催化 + 盘中增量"""
    result = {}
    path = CATALYST_DIR / f"catalyst_screen_{today_str}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for cname, stocks in data.get("stock_maps", {}).items():
                result[cname] = [{
                    "code": s.get("code", ""), "name": s.get("name", ""),
                    "confidence": s.get("confidence", "?"), "source": "today",
                } for s in stocks]
        except Exception:
            pass

    history = _load_history_catalysts(today_str)
    if history:
        result.update(history)
        print(f"  History catalysts: {len(history)} from past {HISTORY_LOOKBACK}d")

    intraday = _scan_intraday_delta(today_str)
    if intraday:
        for cname, stocks in intraday.items():
            result[cname] = [{
                "code": s.get("code", ""), "name": s.get("name", ""),
                "confidence": "low", "source": "intraday",
            } for s in stocks]
        print(f"  Intraday catalysts: {len(intraday)} new")
    return result


def monitor(today_str: str):
    print(f"[catalyst_monitor] {today_str}")

    catalyst_stocks = load_catalyst_stocks(today_str)
    if not catalyst_stocks:
        print("  [SKIP] No catalyst data for today")
        return

    today_count = sum(1 for v in catalyst_stocks.values()
                      if v and v[0].get("source") == "today")
    history_count = sum(1 for v in catalyst_stocks.values()
                        if v and v[0].get("source") != "today" and v[0].get("source") != "intraday")
    total_mapped = sum(len(v) for v in catalyst_stocks.values())
    print(f"  Pool: {len(catalyst_stocks)} catalysts ({today_count} today + {history_count} history)"
          f" → {total_mapped} stocks")

    quotes = fetch_redis_quotes()
    if not quotes:
        print("  [SKIP] Redis unavailable")
        return

    alerts = []
    resurrections = []
    seen = set()

    for cname, stocks in catalyst_stocks.items():
        is_history = cname.startswith("[历史复活池]")
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

            # 通用异动检测
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

            alert = {
                "catalyst": cname.replace("[历史复活池] ", ""), "code": code,
                "name": q.get("name", "?"), "chg": chg,
                "limit_up": limit_up, "vol_ratio": vol,
                "confidence": s.get("confidence", "?"), "reasons": reasons,
                "is_history": is_history,
                "actionability": s.get("actionability", 0),
            }

            if triggered:
                alerts.append(alert)

            # 历史催化：计算盘中复活得分
            if is_history and chg > 0:
                rscore, rreasons = _score_resurrection(q)
                if rscore >= RESURRECT_THRESHOLD:
                    alert["resurrection_score"] = rscore
                    alert["resurrection_reasons"] = rreasons
                    resurrections.append(alert)

    # --- 盘中复活推送（独立于常规异动） ---
    if resurrections:
        resurrections.sort(key=lambda x: (-x["resurrection_score"], -x["chg"]))
        rtitle = f"🔄 盘中复活 {len(resurrections)}条催化"
        rlines = []
        for r in resurrections[:5]:
            flags = " ".join(r["resurrection_reasons"])
            rlines.append(
                f"- [{r['catalyst'][:25]}] **{r['code']} {r['name']}** "
                f"{flags} (原行动性{r['actionability']}分)"
            )
        if len(resurrections) > 5:
            rlines.append(f"\n... 共 {len(resurrections)} 条复活")
        _push(rtitle, "\n".join(rlines))

    # --- 常规异动 ---
    if alerts:
        alerts.sort(key=lambda x: (-x["limit_up"], -x["chg"], -x["vol_ratio"]))

        # 区分今日和历史
        today_alerts = [a for a in alerts if not a["is_history"]]
        history_alerts = [a for a in alerts if a["is_history"]]
        print(f"\n  ALERTS ({len(alerts)} stocks, {len(history_alerts)} from history):")
        for a in alerts[:10]:
            tag = "[H]" if a["is_history"] else "[T]"
            flags = " ".join(a["reasons"])
            print(f"    {tag} [{a['catalyst'][:35]}] {a['code']} {a['name']} {flags}")

        by_catalyst = defaultdict(list)
        for a in alerts:
            by_catalyst[a["catalyst"]].append(a)

        L = [f"# 催化剂盘中监控 {today_str}",
             f"\n> 监控池: {today_count}今日 + {history_count}历史 + {len(catalyst_stocks)-today_count-history_count}盘中 "
             f"→ {total_mapped}只标的 | 异动 {len(alerts)}只 "
             f"(历史{len(history_alerts)})\n"]
        for cname, items in by_catalyst.items():
            L.append(f"## {cname}")
            for a in items:
                flags = " ".join(a["reasons"])
                hist_tag = " [历史复活池]" if a["is_history"] else ""
                L.append(f"- **{a['code']} {a['name']}** {flags} (置信度={a['confidence']}){hist_tag}")
            L.append("")

        out = CATALYST_DIR / f"catalyst_monitor_{today_str}.md"
        out.write_text("\n".join(L), encoding="utf-8")
        print(f"  Report: {out}")

        limit_ups = sum(1 for a in alerts if a["limit_up"])
        chg_alerts = sum(1 for a in alerts if not a["limit_up"] and a["chg"] >= ALERT_CHG_PCT)
        vol_alerts = sum(1 for a in alerts if a["vol_ratio"] >= ALERT_VOL_RATIO)
        push_title = (
            f"⚡ 催化异动 {len(alerts)}只 "
            f"({f'涨停{limit_ups} ' if limit_ups else ''}"
            f"{f'+3% {chg_alerts} ' if chg_alerts else ''}"
            f"{f'放量{vol_alerts}' if vol_alerts else ''})"
        )
        push_lines = []
        for a in today_alerts[:3]:
            flags = " ".join(a["reasons"])
            push_lines.append(
                f"- [{a['catalyst'][:30]}] **{a['code']} {a['name']}** "
                f"{flags} ({a['chg']:+.1f}%)"
            )
        if history_alerts:
            push_lines.append(f"\n📜 历史催化异动 {len(history_alerts)}只:")
            for a in history_alerts[:3]:
                flags = " ".join(a["reasons"])
                push_lines.append(
                    f"- [{a['catalyst'][:25]}] **{a['code']} {a['name']}** "
                    f"{flags} ({a['chg']:+.1f}%)"
                )
        if len(alerts) > 6:
            push_lines.append(f"\n... 共 {len(alerts)} 只异动")
        _push(push_title, "\n".join(push_lines))
    else:
        print("  No alerts (all catalyst stocks within normal range)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", "-d", type=str, default=date.today().isoformat())
    args = parser.parse_args()
    monitor(args.date)
