"""情绪面分析 — 连板梯队 / 逻辑情绪四维分类 (B1)"""
import re
import pandas as pd

from utils import is_st, safe_str, safe_float

_LOGIC_DRIVER_STRONG = ["合同", "订单", "中标", "签约", "业绩预增", "扭亏",
                         "增持", "回购", "获批", "投产", "扩产", "并表"]
_LOGIC_DRIVER_WEAK = ["产品", "验证", "高新", "立项", "签订", "授权"]
_EMOTION_DRIVER_STRONG = ["澄清", "异常波动", "无应披露", "提示风险", "终止", "撤回"]
_EMOTION_DRIVER_WEAK = ["异动", "媒体报道", "传闻"]


def analyze_sentiment(hot_df: pd.DataFrame) -> dict:
    result = {
        "ladder": {}, "leader": None, "biggest_vol_limit": None,
        "logic_count": 0, "emotion_count": 0,
        "logic_stocks": [], "emotion_stocks": [], "st_stocks": [],
    }
    if hot_df is None or hot_df.empty:
        return result

    ladder: dict[int, list] = {}
    for _, row in hot_df.iterrows():
        reason = safe_str(row, "题材归因")
        name = safe_str(row, "名称")
        code = safe_str(row, "代码")
        amount = safe_float(row, "成交额")

        board_n = 0
        for tag in reason.split("+"):
            if "连板" in tag.strip():
                m = re.search(r"(\d+)", tag)
                if m:
                    board_n = int(m.group(1))
                    break

        info = {"name": name, "code": code, "reason": reason, "amount": amount, "board_n": board_n}
        if is_st(name):
            result["st_stocks"].append(info)
            continue

        if board_n >= 2:
            ladder.setdefault(board_n, []).append(
                {"name": name, "code": code, "reason": reason, "amount": amount})

        kw = {"摘帽", "次新", "超跌", "反弹", "低价"}
        if any(k in reason or k in name for k in kw):
            result["emotion_count"] += 1
            result["emotion_stocks"].append(info)
        else:
            result["logic_count"] += 1
            result["logic_stocks"].append(info)

    for n in sorted(ladder.keys(), reverse=True):
        ladder[n].sort(key=lambda x: x["amount"], reverse=True)
    result["ladder"] = dict(sorted(ladder.items(), reverse=True))

    if ladder:
        result["leader"] = {"board": max(ladder.keys()), "stocks": ladder[max(ladder.keys())]}

    rows = [{"name": safe_str(r, "名称"), "code": safe_str(r, "代码"),
             "amount": safe_float(r, "成交额")}
            for _, r in hot_df.iterrows()
            if not is_st(safe_str(r, "名称"))]
    if rows:
        result["biggest_vol_limit"] = max(rows, key=lambda x: x["amount"])
    return result


def _score_driver(announcements: list[dict]) -> int:
    if not announcements:
        return 0
    titles = [str(a.get("title", "")) for a in announcements if a]
    def _has(ks): return any(k in t for t in titles for k in ks)
    if _has(_LOGIC_DRIVER_STRONG) and not _has(_EMOTION_DRIVER_STRONG):
        return 2
    if _has(_EMOTION_DRIVER_STRONG) and not _has(_LOGIC_DRIVER_STRONG):
        return -2
    if _has(_LOGIC_DRIVER_WEAK) and not _has(_EMOTION_DRIVER_WEAK):
        return 1
    if _has(_EMOTION_DRIVER_WEAK) and not _has(_LOGIC_DRIVER_WEAK):
        return -1
    return 0


def _score_trend(kline) -> int:
    if kline is None:
        return 0
    try:
        if hasattr(kline, "empty") and kline.empty:
            return 0
        closes = kline["close"].tolist() if "close" in kline else []
        opens = kline["open"].tolist() if "open" in kline else []
        highs = kline["high"].tolist() if "high" in kline else []
        lows = kline["low"].tolist() if "low" in kline else []
    except Exception:
        return 0
    if len(closes) < 3:
        return 0
    ow = 0
    for i in range(max(0, len(closes) - 2), len(closes)):
        if i < len(opens) and i < len(highs) and i < len(lows):
            if opens[i] and opens[i] == highs[i] == lows[i]:
                ow += 1
    if ow >= 2:
        return -1
    w = closes[-5:] if len(closes) >= 5 else closes
    if len(w) >= 3 and w[-1] > w[0] and all(w[i] >= w[i-1] * 0.99 for i in range(1, len(w))):
        return 1
    return 0


def _score_vp(quote: dict) -> int:
    if not quote:
        return 0
    to = float(quote.get("turnover_pct", 0) or 0)
    op = float(quote.get("open", 0) or 0)
    hi = float(quote.get("high", 0) or 0)
    lo = float(quote.get("low", 0) or 0)
    lu = float(quote.get("limit_up", 0) or 0)
    amp = float(quote.get("amplitude_pct", 0) or 0)
    if op > 0 and lu > 0 and abs(op - lu) < 0.01 and abs(hi - lo) < 0.01 and to < 3:
        return -1
    if to > 35:
        return -1
    if 8 <= to <= 25 and amp >= 3:
        return 1
    return 0


def _score_lhb(lhb_info: dict | None) -> int:
    if not lhb_info:
        return 0
    c = str(lhb_info.get("comment", "")) or ""
    if any(k in c for k in ("机构", "深股通", "沪股通", "北向")):
        return 2
    if "营业部" in c:
        return -1
    return 0


def _score_theme_count(themes_count: int) -> int:
    if themes_count <= 1:
        return 1
    if themes_count >= 3:
        return -1
    return 0


def classify_limit_up_type(code: str, name: str, quote: dict | None = None,
                           kline=None, lhb_info: dict | None = None,
                           announcements: list[dict] | None = None,
                           themes_count: int = 1) -> dict:
    d = _score_driver(announcements or [])
    t = _score_trend(kline)
    v = _score_vp(quote or {})
    l = _score_lhb(lhb_info)
    th = _score_theme_count(themes_count)
    bd = {"driver": d, "trend": t, "vp": v, "lhb": l, "theme_count": th}
    logic = sum(s for s in bd.values() if s > 0)
    emotion = sum(-s for s in bd.values() if s < 0)
    net = logic - emotion
    if net >= 3:
        label = "纯逻辑"
    elif net >= 1:
        label = "偏逻辑"
    elif net <= -3:
        label = "纯情绪"
    elif net <= -1:
        label = "偏情绪"
    else:
        label = "混合"
    return {"logic_score": logic, "emotion_score": emotion,
            "net_score": net, "label": label, "breakdown": bd}


def apply_limit_up_classification(
    sentiment: dict, zt_pool: dict, quotes: dict, klines: dict,
    lhb_data: dict, corpus_map: dict,
    theme_counts: dict | None = None, code_themes: dict | None = None,
) -> None:
    theme_counts = theme_counts or {}
    code_themes = code_themes or {}
    originals = list(sentiment.get("logic_stocks", [])) + list(sentiment.get("emotion_stocks", []))
    seen = set()
    pool = []
    for s in originals:
        c = s.get("code")
        if c and c not in seen:
            seen.add(c)
            pool.append(s)

    new_logic, new_emotion, new_mixed = [], [], []
    by_label = {"纯逻辑": 0, "偏逻辑": 0, "混合": 0, "偏情绪": 0, "纯情绪": 0}

    for s in pool:
        code = s.get("code", "")
        themes = code_themes.get(code, [])
        max_tc = max((theme_counts.get(t, 1) for t in themes), default=1)
        anns = (corpus_map.get(code) or {}).get("announcements", [])
        r = classify_limit_up_type(
            code=code, name=s.get("name", ""),
            quote=quotes.get(code), kline=klines.get(code),
            lhb_info=lhb_data.get(code), announcements=anns,
            themes_count=max_tc,
        )
        s2 = dict(s)
        s2["label"] = r["label"]
        s2["net_score"] = r["net_score"]
        s2["logic_score"] = r["logic_score"]
        s2["emotion_score"] = r["emotion_score"]
        s2["breakdown"] = r["breakdown"]
        by_label[r["label"]] = by_label.get(r["label"], 0) + 1
        if r["label"] in ("纯逻辑", "偏逻辑"):
            new_logic.append(s2)
        elif r["label"] in ("纯情绪", "偏情绪"):
            new_emotion.append(s2)
        else:
            new_mixed.append(s2)

    sentiment["logic_stocks"] = new_logic
    sentiment["emotion_stocks"] = new_emotion
    sentiment["mixed_stocks"] = new_mixed
    sentiment["logic_count"] = len(new_logic)
    sentiment["emotion_count"] = len(new_emotion)
    sentiment["mixed_count"] = len(new_mixed)
    sentiment["by_label"] = by_label
