"""题材分析 — 词频统计/分级/三池合并/审美定性"""
import re
from collections import Counter

import pandas as pd

from config import (
    THEME_LEVEL_RULES, THEME_DRIVER_KEYWORDS, THEME_LANDING_KEYWORDS,
    ALPHA_BUCKET_LABELS, SURGE_NEWS_KEYWORDS,
    THEME_EXPAND_MIN_LEVEL, THEME_ZHONGJUN_MIN_LEVEL,
    THEME_EXPAND_MAX_STOCKS, THEME_ZHONGJUN_MIN_FREQ_30D,
    THEME_STOPWORDS, THEME_ALIAS,
    MERGE_POOL_MAX_CONCEPTS, MERGE_POOL_MIN_FREQ,
    CONCEPT_UNIVERSE, ENRICHMENT_THRESHOLD,
    TEMPORAL_TAG_PATTERNS, GENERIC_ATTRIBUTE_TAGS,
    TOTAL_CONCEPT_STOCKS,
    STOCK_PRIMARY_CONCEPT, STOCK_SECONDARY_CONCEPT,
)
from utils import safe_str, safe_float
import store

SOURCE_ZT = "涨"
SOURCE_POP = "人"
SOURCE_GAIN = "强"
SOURCE_EXPAND = "扩"


def _is_temporal_tag(tag: str) -> bool:
    """检查标签是否为临时事件（季报/分红/摘帽等），非可投资主题"""
    return any(p in tag for p in TEMPORAL_TAG_PATTERNS)


def _is_generic_attribute(tag: str) -> bool:
    """检查标签是否为宽泛属性（央企/国企/xx国资），无投资信息量"""
    if tag in GENERIC_ATTRIBUTE_TAGS:
        return True
    if tag.endswith("国资") and tag not in CONCEPT_UNIVERSE:
        return True
    return False


def _is_noise_tag(tag: str, today_count: int, total_stocks: int) -> bool:
    """富集比检验：标签是否在统计上不显著。

    标准概念（在 CONCEPT_UNIVERSE 中）：ER < 阈值 → 噪音
    非标准概念：临时事件或宽泛属性 → 噪音；其余（具体品种）默认保留
    """
    base_rate = CONCEPT_UNIVERSE.get(tag)
    if base_rate is not None:
        expected = total_stocks * base_rate
        if expected > 0:
            er = today_count / expected
            return er < ENRICHMENT_THRESHOLD
        return False
    if _is_temporal_tag(tag):
        return True
    if _is_generic_attribute(tag):
        return True
    return False


def _pick_primary_tag(tags: list[str], code: str) -> str | None:
    """为一只股票选择主因标签，优先匹配 Sheet 2 第一顺位概念。

    匹配策略：
    1. 精确匹配 Sheet 2 rank-1 → 直接返回
    2. 子串匹配 → 选名称最长的（越具体越好）→ 六氟化钨 > 氟化工
    3. 尝试 rank-2（同上逻辑）
    4. Fallback：选特异性最高的标签
    """
    if not tags:
        return None

    primary = STOCK_PRIMARY_CONCEPT.get(code, "")
    secondary = STOCK_SECONDARY_CONCEPT.get(code, "")

    for concept in (primary, secondary):
        if not concept:
            continue
        exact = [t for t in tags if t == concept]
        if exact:
            return exact[0]
        fuzzy = [t for t in tags if t in concept or concept in t]
        if fuzzy:
            fuzzy.sort(key=len, reverse=True)
            return fuzzy[0]

    scored = []
    for tag in tags:
        score = 0
        if tag not in CONCEPT_UNIVERSE:
            score += 200  # 不在标准概念库中 = 更具体的品种标签
        score += len(tag)
        if not _is_temporal_tag(tag) and not _is_generic_attribute(tag):
            score += 50
        scored.append((score, tag))
    scored.sort(reverse=True)
    return scored[0][1]


_BOARD_RE = re.compile(r"^\d+连板$|^\d+天\d+板$|^连续\d+")
_BOARD_WORDS = {"连板", "涨停", "首板", "二连板", "炸板", "T字板",
                "一字板", "几天几板", "连续涨停", "强势股"}


def normalize_theme(tag: str) -> str | None:
    if not tag:
        return None
    t = tag.strip()
    if not t or t in _BOARD_WORDS or _BOARD_RE.match(t):
        return None
    if t in THEME_STOPWORDS:
        return None
    t = THEME_ALIAS.get(t, t)
    if t in THEME_STOPWORDS:
        return None
    return t


def analyze_themes(hot_df: pd.DataFrame, trade_date: str) -> dict:
    if hot_df.empty:
        return {"today": [], "new": [], "persistent": [], "fading": [], "raw_counts": {}}

    theme_stocks: dict[str, set[str]] = {}
    for _, row in hot_df.iterrows():
        reason = safe_str(row, "题材归因")
        code = safe_str(row, "代码")
        tags = []
        for raw in reason.split("+"):
            tag = normalize_theme(raw)
            if tag:
                tags.append(tag)
        primary = _pick_primary_tag(tags, code)
        if primary:
            theme_stocks.setdefault(primary, set()).add(code)
        for tag in tags:
            theme_stocks.setdefault(tag, set())  # 保留空集合，维持标签存在性

    # 清理未获得任何主因归因的空标签
    theme_stocks = {t: codes for t, codes in theme_stocks.items() if codes}

    cnt = Counter({theme: len(codes) for theme, codes in theme_stocks.items()})

    total_stocks = len(hot_df)
    noise_tags = []
    for tag in list(theme_stocks.keys()):
        today_count = len(theme_stocks[tag])
        if _is_noise_tag(tag, today_count, total_stocks):
            noise_tags.append((tag, today_count))
            del theme_stocks[tag]

    cnt = Counter({theme: len(codes) for theme, codes in theme_stocks.items()})
    if noise_tags:
        noise_summary = ", ".join(f"{t}({n})" for t, n in sorted(noise_tags, key=lambda x: -x[1]))
        print(f"  [ER过滤] 剔除噪音标签: {noise_summary}")

    top_themes = cnt.most_common(20)

    theme_data = {}
    for theme, codes in theme_stocks.items():
        theme_data[theme] = {"count": len(codes), "stocks": ",".join(sorted(codes))}

    store.save_themes(trade_date, theme_data)

    recent_dates = store.get_recent_theme_dates(5)
    yesterday_date = None
    for d in recent_dates:
        if d < trade_date:
            yesterday_date = d

    yesterday_themes = store.load_themes(yesterday_date) if yesterday_date else {}
    today_set = set(cnt.keys())
    yesterday_set = set(yesterday_themes.keys())

    new_themes = today_set - yesterday_set
    fading_themes = yesterday_set - today_set

    persistent = []
    for theme in today_set & yesterday_set:
        today_count = cnt[theme]
        yest_count = yesterday_themes[theme]["count"]
        trend = "↑" if today_count > yest_count else ("↓" if today_count < yest_count else "→")
        persistent.append({
            "theme": theme,
            "today_count": today_count,
            "yesterday_count": yest_count,
            "trend": trend,
        })
    persistent.sort(key=lambda x: x["today_count"], reverse=True)

    leveled_themes = []
    for theme, count in cnt.most_common(30):
        cons_days = store.get_theme_consecutive_days(theme, trade_date)
        cum_stocks = store.get_theme_cumulative_stocks(theme)
        level = 1
        for lv in range(5, 0, -1):
            rule = THEME_LEVEL_RULES[lv]
            if cons_days >= rule["min_days"]:
                level = lv
                break
        label = THEME_LEVEL_RULES[level]["label"]

        yest_count = yesterday_themes.get(theme, {}).get("count", 0)
        if theme in new_themes:
            narrative = "Formation"
        elif count > yest_count:
            narrative = "Validation"
        elif count < yest_count:
            narrative = "Violation"
        else:
            narrative = "Validation"

        leveled_themes.append({
            "theme": theme,
            "level": level,
            "label": label,
            "today_count": count,
            "consecutive_days": cons_days,
            "cumulative_stocks": cum_stocks,
            "narrative": narrative,
        })
        store.save_theme_level(theme, level, cons_days,
                               trade_date if cons_days <= 1 else "",
                               trade_date, cum_stocks)

    fading_with_narrative = []
    for theme in fading_themes:
        fading_with_narrative.append({"theme": theme, "narrative": "Reversal"})

    return {
        "today": top_themes,
        "new": sorted(new_themes, key=lambda t: cnt.get(t, 0), reverse=True),
        "persistent": persistent,
        "fading": sorted(fading_themes, key=lambda t: yesterday_themes.get(t, {}).get("count", 0), reverse=True),
        "fading_narrative": fading_with_narrative,
        "raw_counts": dict(cnt),
        "total_stocks": len(hot_df),
        "leveled": leveled_themes,
    }


def _calc_r10(kdf: pd.DataFrame) -> float | None:
    if kdf is None or len(kdf) < 11:
        return None
    c_now = kdf["close"].iloc[-1]
    c_10 = kdf["close"].iloc[-11]
    if c_10 > 0:
        return (c_now / c_10 - 1) * 100
    return None


def _calc_chg5(kdf: pd.DataFrame) -> float | None:
    if kdf is None or len(kdf) < 6:
        return None
    c_now = kdf["close"].iloc[-1]
    c_5 = kdf["close"].iloc[-6]
    if c_5 > 0:
        return (c_now / c_5 - 1) * 100
    return None


def build_theme_stock_details(hot_df: pd.DataFrame,
                              theme_result: dict,
                              hot_klines: dict[str, pd.DataFrame] = None,
                              hot_quotes: dict[str, dict] = None,
                              zt_pool: dict[str, dict] = None,
                              ) -> dict[str, list[dict]]:
    if hot_df is None or hot_df.empty:
        return {}
    if hot_quotes is None:
        hot_quotes = {}
    if zt_pool is None:
        zt_pool = {}

    theme_stocks: dict[str, list[dict]] = {}
    for _, row in hot_df.iterrows():
        code = safe_str(row, "代码")
        name = safe_str(row, "名称")
        reason = safe_str(row, "题材归因")
        chg = safe_float(row, "涨幅%")
        amount = safe_float(row, "成交额")
        turnover = safe_float(row, "换手率%")
        if chg == 0 and code in hot_quotes:
            q = hot_quotes[code]
            chg = q.get("change_pct", 0) or 0
            amount = q.get("amount_wan", 0) or 0
            turnover = q.get("turnover_pct", 0) or 0

        chg5 = None
        r10 = None
        if hot_klines and code in hot_klines:
            kdf = hot_klines[code]
            chg5 = _calc_chg5(kdf)
            r10 = _calc_r10(kdf)

        is_limit_up = chg >= 9.5 or (code.startswith("3") and chg >= 19.5) or (code.startswith("68") and chg >= 19.5)

        if is_limit_up:
            label = "涨停"
        elif chg >= 7:
            label = "强势"
        elif turnover >= 15:
            label = "高换手"
        else:
            label = ""

        zt = zt_pool.get(code, {})
        stock_info = {
            "code": code, "name": name, "chg": chg, "chg5": chg5, "r10": r10,
            "amount_wan": amount, "turnover": turnover,
            "reason": reason, "is_limit_up": is_limit_up,
            "label": label, "mcap_yi": 0,
            "consecutive_boards": zt.get("consecutive_boards", 0),
            "zt_time": zt.get("first_time", ""),
        }

        seen_tags: set[str] = set()
        for raw in reason.split("+"):
            tag = normalize_theme(raw)
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            theme_stocks.setdefault(tag, []).append(stock_info)

    known_themes = {t["theme"] for t in (theme_result.get("leveled", []))}
    for theme_name in known_themes:
        for tag, stocks_list in list(theme_stocks.items()):
            if tag == theme_name:
                continue
            match = False
            if theme_name in tag:
                match = True
            elif tag in theme_name and len(tag) >= 4:
                match = True
            if match:
                existing_codes = {s["code"] for s in theme_stocks.get(theme_name, [])}
                for s in stocks_list:
                    if s["code"] not in existing_codes:
                        theme_stocks.setdefault(theme_name, []).append(s)
                        existing_codes.add(s["code"])

    for stocks in theme_stocks.values():
        stocks.sort(key=lambda x: (-x.get("consecutive_boards", 0), x.get("zt_time", "") or "99"))

    return theme_stocks


def expand_theme_stocks(
    theme_stock_details: dict[str, list[dict]],
    leveled_themes: list[dict],
    extra_quotes: dict[str, dict],
    extra_klines: dict[str, pd.DataFrame],
    theme_freq_5d: dict[str, dict[str, dict]],
    theme_freq_30d: dict[str, dict[str, dict]],
) -> dict[str, list[dict]]:
    label_priority = {"涨停": 0, "中军": 1, "近期活跃": 2, "强势": 3, "高换手": 4, "": 5}

    def _sort_key(s):
        lbl = s.get("label", "")
        pri = 5
        for prefix, p in label_priority.items():
            if lbl.startswith(prefix):
                pri = p
                break
        cb = -(s.get("consecutive_boards", 0))
        zt = s.get("zt_time", "") or "99"
        return (pri, cb, zt)

    for t in leveled_themes:
        theme = t["theme"]
        level = t.get("level", 1)
        if level < THEME_ZHONGJUN_MIN_LEVEL:
            continue

        existing = theme_stock_details.get(theme, [])
        existing_codes = {s["code"] for s in existing}

        freq_5d = theme_freq_5d.get(theme, {})
        freq_30d = theme_freq_30d.get(theme, {})

        source_b = []
        if level >= THEME_EXPAND_MIN_LEVEL:
            candidates_b = []
            for code, info in freq_5d.items():
                if code in existing_codes or info["freq"] < 2:
                    continue
                candidates_b.append((code, info["freq"]))
            candidates_b.sort(key=lambda x: -x[1])
            for code, freq in candidates_b[:5]:
                q = extra_quotes.get(code, {})
                if not q:
                    continue
                kdf_b = extra_klines.get(code)
                chg5 = _calc_chg5(kdf_b)
                r10 = _calc_r10(kdf_b)
                source_b.append({
                    "code": code,
                    "name": q.get("name", code),
                    "chg": q.get("change_pct", 0),
                    "chg5": chg5,
                    "r10": r10,
                    "amount_wan": q.get("amount_wan", 0),
                    "turnover": q.get("turnover_pct", 0),
                    "reason": f"近5日{freq}次涨停",
                    "is_limit_up": False,
                    "label": f"近期({freq}天)",
                    "mcap_yi": q.get("mcap_yi", 0),
                })

        source_b_codes = {s["code"] for s in source_b}

        source_c = []
        candidates_c = []
        for code, info in freq_30d.items():
            if code in existing_codes or code in source_b_codes:
                continue
            if info["freq"] < THEME_ZHONGJUN_MIN_FREQ_30D:
                continue
            q = extra_quotes.get(code, {})
            mcap = q.get("mcap_yi", 0) if q else 0
            if mcap <= 0:
                continue
            candidates_c.append((code, info["freq"], mcap))
        candidates_c.sort(key=lambda x: -(x[1] * x[2]))
        for code, freq, mcap in candidates_c[:3]:
            q = extra_quotes.get(code, {})
            kdf_c = extra_klines.get(code)
            chg5 = _calc_chg5(kdf_c)
            r10 = _calc_r10(kdf_c)
            source_c.append({
                "code": code,
                "name": q.get("name", code),
                "chg": q.get("change_pct", 0),
                "chg5": chg5,
                "r10": r10,
                "amount_wan": q.get("amount_wan", 0),
                "turnover": q.get("turnover_pct", 0),
                "reason": f"30日{freq}次，{mcap:.0f}亿",
                "is_limit_up": False,
                "label": "中军",
                "mcap_yi": mcap,
            })

        merged = existing + source_c + source_b
        merged.sort(key=_sort_key)
        theme_stock_details[theme] = merged[:THEME_EXPAND_MAX_STOCKS]

    return theme_stock_details


def classify_themes_by_trend(theme_result: dict,
                             theme_aesthetics: list[dict] = None,
                             ) -> dict[str, list[dict]]:
    leveled = theme_result.get("leveled", [])
    aesthetics_map = {}
    if theme_aesthetics:
        aesthetics_map = {a["theme"]: a for a in theme_aesthetics}

    groups = {
        "主升浪": [],
        "加速期": [],
        "新兴题材": [],
        "轮动": [],
        "退潮": [],
    }

    new_themes = set(theme_result.get("new", []))

    for t in leveled:
        theme = t["theme"]
        narrative = t.get("narrative", "")
        level = t.get("level", 1)
        cons = t.get("consecutive_days", 0)

        entry = {**t}
        a = aesthetics_map.get(theme, {})
        entry["alpha_label"] = a.get("alpha_label", "")
        entry["surge_score"] = a.get("surge_score", 0)
        entry["surge_max"] = a.get("surge_max", 5)
        entry["driver"] = a.get("driver", "")

        if level >= 3 and cons >= 3 and narrative == "Validation" and t.get("today_count", 0) >= 2:
            groups["主升浪"].append(entry)
        elif narrative == "Validation" and level >= 2:
            groups["加速期"].append(entry)
        elif theme in new_themes or narrative == "Formation":
            groups["新兴题材"].append(entry)
        elif narrative == "Violation":
            groups["退潮"].append(entry)
        else:
            groups["轮动"].append(entry)

    fading = theme_result.get("fading_narrative", [])
    for f in fading:
        groups["退潮"].append({
            "theme": f["theme"], "level": 0, "label": "退潮",
            "today_count": 0, "consecutive_days": 0,
            "cumulative_stocks": 0, "narrative": "Reversal",
            "alpha_label": "", "surge_score": 0, "surge_max": 5, "driver": "",
        })

    for v in groups.values():
        v.sort(key=lambda x: (-x.get("level", 0), -x.get("today_count", 0)))

    return groups


def rate_theme(t: dict) -> tuple[str, int]:
    level = t.get("level", 0)
    narrative = t.get("narrative", "")
    cons = t.get("consecutive_days", 0)
    today_c = t.get("today_count", 0)
    surge = t.get("surge_score", 0)

    score = 0
    if level >= 4:
        score += 3
    elif level >= 3:
        score += 2
    elif level >= 2:
        score += 1

    if narrative == "Validation":
        score += 2
    elif narrative == "Formation":
        score += 1
    elif narrative == "Violation":
        score -= 1

    if cons >= 5:
        score += 2
    elif cons >= 3:
        score += 1

    if today_c >= 5:
        score += 2
    elif today_c >= 3:
        score += 1

    score += min(surge, 2)

    score = max(1, min(score, 10))

    if score >= 8:
        label = "★★★"
    elif score >= 6:
        label = "★★"
    elif score >= 4:
        label = "★"
    else:
        label = "☆"
    return label, score


def build_merged_theme_pool(hot_df, pop_pool, gain_pool, *,
                            max_concepts=MERGE_POOL_MAX_CONCEPTS,
                            min_freq=MERGE_POOL_MIN_FREQ) -> dict:
    meta: dict[str, dict] = {}

    def _add(code, name, chg, source, raw_concepts):
        code = str(code or "").strip()
        if not code:
            return
        m = meta.get(code)
        if m is None:
            m = {"code": code, "name": name or "", "chg": chg or 0.0,
                 "sources": [], "concepts": []}
            meta[code] = m
        if source not in m["sources"]:
            m["sources"].append(source)
        if not m["name"] and name:
            m["name"] = name
        if not m["chg"] and chg:
            m["chg"] = chg
        for c in raw_concepts:
            nc = normalize_theme(c)
            if nc and nc not in m["concepts"]:
                m["concepts"].append(nc)

    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            reason = safe_str(row, "题材归因")
            raw = [x.strip() for x in reason.split("+") if x.strip()]
            _add(row.get("代码", ""), safe_str(row, "名称"),
                 safe_float(row, "涨幅%"), SOURCE_ZT, raw)

    for s in (pop_pool or []):
        _add(s.get("code"), s.get("name"), s.get("chg", 0),
             SOURCE_POP, s.get("concepts", []))
    for s in (gain_pool or []):
        _add(s.get("code"), s.get("name"), s.get("chg", 0),
             SOURCE_GAIN, s.get("concepts", []))

    for m in meta.values():
        m["concepts"] = m["concepts"][:max_concepts]

    freq: Counter = Counter()
    theme_codes: dict[str, list[str]] = {}
    for m in meta.values():
        for c in m["concepts"]:
            freq[c] += 1
            theme_codes.setdefault(c, []).append(m["code"])

    themes = {c: [meta[code] for code in codes]
              for c, codes in theme_codes.items() if freq[c] >= min_freq}
    longtail = sorted(((c, freq[c]) for c in theme_codes if freq[c] < min_freq),
                      key=lambda x: -x[1])
    return {"meta": meta, "themes": themes,
            "theme_freq": dict(freq), "longtail": longtail}


def _merged_stock_dict(m: dict) -> dict:
    return {
        "code": m["code"], "name": m["name"],
        "chg": m.get("chg", 0), "chg5": None, "r10": None,
        "amount_wan": 0, "turnover": 0,
        "reason": "/".join(m["concepts"][:3]),
        "is_limit_up": False,
        "label": "人气" if SOURCE_POP in m["sources"] else "中期强势",
        "mcap_yi": 0, "consecutive_boards": 0, "zt_time": "",
        "sources": list(m["sources"]),
    }


def _base_source(s: dict) -> str:
    if s.get("is_limit_up"):
        return SOURCE_ZT
    if str(s.get("label", "")).startswith(("中军", "近期")):
        return SOURCE_EXPAND
    return SOURCE_ZT


def attach_merged_to_themes(theme_stock_details: dict,
                            leveled_themes: list[dict],
                            merged_pool: dict) -> list[dict]:
    for stocks in theme_stock_details.values():
        for s in stocks:
            if not s.get("sources"):
                s["sources"] = [_base_source(s)]

    themes = merged_pool.get("themes", {})
    theme_freq = merged_pool.get("theme_freq", {})
    matched = set()

    for t in leveled_themes:
        name = t["theme"]
        canon = normalize_theme(name) or name
        merged_stocks = themes.get(canon)
        if not merged_stocks:
            continue
        matched.add(canon)
        existing = theme_stock_details.setdefault(name, [])
        by_code = {s["code"]: s for s in existing}
        for m in merged_stocks:
            cur = by_code.get(m["code"])
            if cur is not None:
                for src in m["sources"]:
                    if src not in cur.setdefault("sources", []):
                        cur["sources"].append(src)
            else:
                nd = _merged_stock_dict(m)
                existing.append(nd)
                by_code[m["code"]] = nd
        existing.sort(key=lambda s: (
            0 if s.get("is_limit_up") else 1,
            -(s.get("consecutive_boards", 0) or 0),
            s.get("zt_time", "") or "99",
            -(s.get("chg", 0) or 0),
        ))

    new_dirs = []
    for canon, stocks in themes.items():
        if canon in matched:
            continue
        if any(SOURCE_ZT in m["sources"] for m in stocks):
            continue
        new_dirs.append({
            "theme": canon,
            "freq": theme_freq.get(canon, len(stocks)),
            "stocks": sorted((_merged_stock_dict(m) for m in stocks),
                             key=lambda s: -(s.get("chg", 0) or 0)),
        })
    new_dirs.sort(key=lambda x: -x["freq"])
    return new_dirs


def _classify_alpha_bucket(t: dict, drivers: list[str]) -> tuple[int, str]:
    narrative = t.get("narrative", "")
    level = t.get("level", 1)

    if narrative == "Reversal":
        return 2, ALPHA_BUCKET_LABELS[2]
    if narrative == "Violation":
        return 4, ALPHA_BUCKET_LABELS[4]
    if "事件驱动" in drivers:
        return 3, ALPHA_BUCKET_LABELS[3]
    if "政策驱动" in drivers and narrative in ("Formation", "Validation"):
        return 6, ALPHA_BUCKET_LABELS[6]
    if level >= 4 and narrative == "Validation":
        return 1, ALPHA_BUCKET_LABELS[1]
    if "技术/落地驱动" in drivers:
        return 1, ALPHA_BUCKET_LABELS[1]
    if narrative == "Formation":
        return 3, ALPHA_BUCKET_LABELS[3]
    return 1, ALPHA_BUCKET_LABELS[1]


def _score_theme_surge(t: dict, headlines: list[str]) -> tuple[int, list[str]]:
    details = []
    score = 0
    narrative = t.get("narrative", "")
    today_c = t.get("today_count", 0)

    if narrative == "Validation" and today_c > 0:
        details.append("加速动量✓")
        score += 1
    else:
        details.append("加速动量✗")

    has_shock = narrative == "Formation" or any(
        kw in h for h in headlines for kw in SURGE_NEWS_KEYWORDS
    )
    if has_shock:
        details.append("冲击/拐点✓")
        score += 1
    else:
        details.append("冲击/拐点✗")

    details.append("上行空间(需人工)")

    if t.get("consecutive_days", 0) >= 3 and narrative in ("Validation", "Formation"):
        details.append("更容易持有✓")
        score += 1
    else:
        details.append("更容易持有✗")

    if narrative == "Validation" and today_c >= 3:
        details.append("论文扩散✓")
        score += 1
    else:
        details.append("论文扩散✗")

    if narrative == "Formation" or t.get("consecutive_days", 0) <= 2:
        details.append("低迷起点✓")
        score += 1
    else:
        details.append("低迷起点✗")

    return score, details


def analyze_theme_aesthetics(leveled_themes: list[dict],
                             news_map: dict[str, list[str]]) -> list[dict]:
    results = []
    for t in leveled_themes:
        if t["level"] < 3:
            continue
        theme_name = t["theme"]
        analysis = {
            "theme": theme_name,
            "level": t["level"],
            "label": t["label"],
            "consecutive_days": t["consecutive_days"],
        }

        headlines = news_map.get(theme_name, [])

        drivers = []
        for dtype, keywords in THEME_DRIVER_KEYWORDS.items():
            if any(kw in h for h in headlines for kw in keywords):
                drivers.append(dtype)
        analysis["driver"] = "、".join(drivers) if drivers else "待确认"

        landing_hits = sum(1 for h in headlines for kw in THEME_LANDING_KEYWORDS if kw in h)
        if landing_hits >= 3:
            analysis["landing"] = "高（多个落地信号）"
        elif landing_hits >= 1:
            analysis["landing"] = "中（有落地线索）"
        else:
            analysis["landing"] = "低（暂无明确时间线）"

        if t["consecutive_days"] > 10:
            analysis["value"] = "偏高位（连续超10天）"
        elif t["consecutive_days"] > 5:
            analysis["value"] = "中等位（连续5-10天）"
        else:
            analysis["value"] = "早期（连续<5天）"

        analysis["capital"] = "待结合龙虎榜/北向数据判断"

        analysis["capacity"] = f"累计{t['cumulative_stocks']}只涨停"

        if headlines:
            analysis["confidence"] = "中" if len(headlines) >= 3 else "低"
        else:
            analysis["confidence"] = "低（无新闻数据）"

        bucket_id, bucket_label = _classify_alpha_bucket(t, drivers)
        analysis["alpha_bucket"] = bucket_id
        analysis["alpha_label"] = f"Bucket{bucket_id}「{bucket_label}」"

        surge_score, surge_details = _score_theme_surge(t, headlines)
        analysis["surge_score"] = surge_score
        analysis["surge_max"] = 5
        analysis["surge_details"] = surge_details

        results.append(analysis)
    return results


def enrich_themes_with_bom(theme_results: dict) -> dict:
    """用 BOM 产业链知识库验证题材逻辑，添加 bom_context 字段。"""
    try:
        from bom_bridge import get_theme_bom_context
    except ImportError:
        return theme_results

    themes = []
    for t in theme_results.get("today", []):
        themes.append(t[0] if isinstance(t, tuple) else t.get("theme", ""))
    for t in theme_results.get("persistent", []):
        themes.append(t[0] if isinstance(t, tuple) else t.get("theme", ""))

    themes = [t for t in themes if t]
    if not themes:
        return theme_results

    bom_ctx = get_theme_bom_context(themes)
    if not bom_ctx:
        return theme_results

    matched_industries = list(bom_ctx.keys())
    segments_summary = []
    for ind, segs in bom_ctx.items():
        tier_map: dict[str, list[str]] = {}
        for s in segs:
            tier_map.setdefault(s["tier"], []).append(s["segment"])
        parts = [f"{t}:{','.join(ss)}" for t, ss in tier_map.items()]
        segments_summary.append(f"{ind}({' | '.join(parts)})")

    theme_results["bom_context"] = {
        "matched_industries": matched_industries,
        "segments_summary": segments_summary,
        "detail": {ind: segs for ind, segs in bom_ctx.items()},
    }
    return theme_results
