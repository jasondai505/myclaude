"""全市场扫描器 — 5步漏斗筛选 + 综合评分

漏斗流程:
  全市场 ~5200只
  → Step1: 基础过滤（ST/停牌/亏损/微盘）
  → Step2: 板块筛选（概念/行业匹配热门题材）
  → Step3: 估值过滤（PE/PB 合理区间）
  → Step4: 技术面过滤（均线/MACD/量能/RSI）
  → Step5: 综合评分 TOP30
"""
import re
import time
from datetime import datetime

import pandas as pd
from tqdm import tqdm
import requests

from config import (
    UA, SCREENER_CONFIG, MA_PERIODS,
    RSI_OVERBOUGHT,
)
import store


# ============================================================
# 数据层
# ============================================================

_EM_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_EM_HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
_ULIST_FIELDS = "f2,f3,f6,f8,f9,f10,f12,f14,f20,f23,f100,f103"


def fetch_universe() -> list[dict]:
    """从东方财富获取全部A股代码列表"""
    sh_re = re.compile(r"^(600|601|603|605|688)\d{3}$")
    sz_re = re.compile(r"^(000|001|002|003|300|301)\d{3}$")

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    stocks = []
    for fs, market_id, pattern in [
        ("m:1+t:2,m:1+t:23,m:1+t:80", "1", sh_re),
        ("m:0+t:6,m:0+t:13,m:0+t:80", "0", sz_re),
    ]:
        pn = 1
        while True:
            params = {
                "fields": "f12",
                "fs": fs,
                "pn": pn, "pz": 500,
                "ut": "fa5fd1943c7b386f172d6893dbbd1",
            }
            try:
                r = requests.get(url, params=params, headers=_EM_HEADERS, timeout=15)
                data = r.json().get("data") or {}
                items = data.get("diff", [])
                if isinstance(items, dict):
                    items = list(items.values())
                if not items:
                    break
                for item in items:
                    code = item.get("f12", "")
                    if pattern.match(code):
                        stocks.append({"code": code, "secid": f"{market_id}.{code}"})
                total = data.get("total", 0)
                if pn * 500 >= total:
                    break
                pn += 1
                time.sleep(0.1)
            except Exception as e:
                print(f"  [WARN] fetch_universe {market_id} p{pn} 失败: {e}")
                break
    return stocks


def fetch_market_data(secids: list[str], batch_size: int = 200) -> list[dict]:
    """批量查询 EM ulist 获取实时行情+行业+概念"""
    all_items = []
    for i in range(0, len(secids), batch_size):
        batch = secids[i:i + batch_size]
        params = {
            "fields": _ULIST_FIELDS,
            "secids": ",".join(batch),
            "ut": "fa5fd1943c7b386f172d6893dbbd1",
        }
        try:
            r = requests.get(_EM_ULIST_URL, params=params,
                             headers=_EM_HEADERS, timeout=15)
            data = r.json()
            items = data.get("data", {}).get("diff", [])
            if isinstance(items, dict):
                items = list(items.values())
            all_items.extend(items)
        except Exception as e:
            print(f"  [WARN] 批次 {i//batch_size+1} 获取失败: {e}")
        time.sleep(0.15)
    return all_items


def _parse_stock(raw: dict) -> dict | None:
    """将 EM ulist 原始数据解析为标准 dict"""
    code = raw.get("f12", "")
    name = raw.get("f14", "")
    if not code or not name:
        return None

    price_raw = raw.get("f2")
    if price_raw is None or price_raw == "-":
        return None

    try:
        price = float(price_raw) / 100
    except (ValueError, TypeError):
        return None

    def _safe(val, divisor=100):
        if val is None or val == "-":
            return 0
        try:
            return float(val) / divisor
        except (ValueError, TypeError):
            return 0

    return {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": _safe(raw.get("f3")),
        "amount": float(raw.get("f6", 0) or 0),
        "turnover_pct": _safe(raw.get("f8")),
        "pe": _safe(raw.get("f9")),
        "vol_ratio": _safe(raw.get("f10")),
        "mktcap": float(raw.get("f20", 0) or 0),
        "pb": _safe(raw.get("f23")),
        "industry": str(raw.get("f100", "") or ""),
        "concepts": str(raw.get("f103", "") or ""),
    }


# ============================================================
# Step 1: 基础过滤
# ============================================================

def filter_junk(stocks: list[dict]) -> list[dict]:
    """移除 ST/停牌/亏损/微盘"""
    cfg = SCREENER_CONFIG
    result = []
    for s in stocks:
        if "ST" in s["name"] or "*ST" in s["name"]:
            continue
        if s["price"] <= 0 or s["amount"] <= 0:
            continue
        if s["pe"] <= 0:
            continue
        mktcap_yi = s["mktcap"] / 1e8
        if mktcap_yi < cfg["min_mktcap_yi"]:
            continue
        result.append(s)
    return result


# ============================================================
# Step 2: 板块筛选
# ============================================================

def _fuzzy_match(theme: str, concepts: str) -> bool:
    """题材名与概念列表模糊匹配（要求至少3字匹配）"""
    if not theme or not concepts or len(theme) < 2:
        return False
    concept_list = [c.strip() for c in concepts.split(",") if c.strip()]
    for c in concept_list:
        if theme == c:
            return True
        if len(theme) >= 3 and theme in c:
            return True
        if len(c) >= 3 and c in theme:
            return True
    return False


def filter_by_board(stocks: list[dict],
                    hot_themes: dict,
                    hot_industries: list[str]) -> list[dict]:
    """保留与热门题材/行业有交集的股票，同时计算 board_score"""
    result = []
    for s in stocks:
        board_score = 0
        matched_themes = []

        for theme, info in hot_themes.items():
            level = info.get("level", 1)
            if level < 2:
                continue
            if _fuzzy_match(theme, s["concepts"]):
                level_score = {2: 20, 3: 50, 4: 80, 5: 100}.get(level, 10)
                board_score = max(board_score, level_score)
                matched_themes.append(theme)

        if s["industry"] and s["industry"] in hot_industries:
            board_score = max(board_score, 30)

        if board_score > 0:
            s["board_score"] = board_score
            s["matched_themes"] = matched_themes[:3]
            result.append(s)

    return result


# ============================================================
# Step 3: 估值过滤
# ============================================================

def filter_valuation(stocks: list[dict]) -> list[dict]:
    cfg = SCREENER_CONFIG
    result = []
    for s in stocks:
        if s["pe"] > cfg["max_pe"]:
            continue
        if s["pb"] > cfg["max_pb"]:
            continue
        result.append(s)
    return result


# ============================================================
# Step 4: 技术面过滤
# ============================================================

_mootdx_client = None


def _get_mootdx():
    global _mootdx_client
    if _mootdx_client is None:
        from mootdx.quotes import Quotes
        _mootdx_client = Quotes.factory(market="std")
    return _mootdx_client


def _compute_technicals(code: str) -> dict | None:
    """拉取K线并计算技术指标"""
    try:
        client = _get_mootdx()
        df = client.bars(symbol=code, category=4, offset=120)
        if df is None or df.empty or len(df) < 30:
            return None

        df = df.reset_index(drop=True)
        for col in ["open", "close", "high", "low", "vol", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        for p in MA_PERIODS:
            df[f"ma{p}"] = df["close"].rolling(p).mean()
        df["vol_ma20"] = df["vol"].rolling(20).mean()

        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        df["dif"] = ema12 - ema26
        df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()

        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        df["rsi"] = 100 - 100 / (1 + rs)

        last = df.iloc[-1]
        result = {"code": code}

        for p in MA_PERIODS:
            result[f"ma{p}"] = last.get(f"ma{p}")

        result["close"] = last["close"]
        result["dif"] = last.get("dif")
        result["dea"] = last.get("dea")
        result["rsi"] = last.get("rsi")
        result["vol_ratio_20"] = (
            last["vol"] / last["vol_ma20"]
            if last.get("vol_ma20") and last["vol_ma20"] > 0
            else 0
        )

        return result
    except Exception:
        return None


def filter_technicals(stocks: list[dict],
                      tech_data: dict[str, dict]) -> list[dict]:
    """技术面过滤：趋势 + 量能 + RSI"""
    result = []
    for s in stocks:
        t = tech_data.get(s["code"])
        if not t:
            continue

        ma5 = t.get("ma5")
        ma10 = t.get("ma10")
        ma20 = t.get("ma20")
        close = t.get("close", 0)
        dif = t.get("dif")
        dea = t.get("dea")
        rsi = t.get("rsi")

        if ma20 is None or pd.isna(ma20):
            continue

        above_ma20 = close > ma20
        ma_bull = (
            ma5 is not None and ma10 is not None
            and not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20)
            and ma5 > ma10 > ma20
        )
        macd_bull = (
            dif is not None and dea is not None
            and not pd.isna(dif) and not pd.isna(dea)
            and dif > dea
        )

        if not above_ma20:
            continue
        if not macd_bull:
            continue
        if rsi is not None and not pd.isna(rsi) and rsi > RSI_OVERBOUGHT:
            continue

        s["tech"] = t
        s["tech_score"] = _score_technical(t)
        result.append(s)

    return result


def _score_technical(t: dict) -> float:
    """技术面评分 0-100"""
    score = 0
    ma5 = t.get("ma5")
    ma10 = t.get("ma10")
    ma20 = t.get("ma20")
    ma60 = t.get("ma60")
    close = t.get("close", 0)

    if all(v is not None and not pd.isna(v) for v in [ma5, ma10, ma20, ma60]):
        if ma5 > ma10 > ma20 > ma60:
            score += 35
        elif ma5 > ma10 > ma20:
            score += 25
        elif close > ma20:
            score += 15

    dif, dea = t.get("dif"), t.get("dea")
    if dif is not None and dea is not None and not pd.isna(dif) and not pd.isna(dea):
        if dif > dea and dif > 0:
            score += 25
        elif dif > dea:
            score += 15

    vol_r = t.get("vol_ratio_20", 0)
    if vol_r and not pd.isna(vol_r):
        if vol_r >= 1.5:
            score += 25
        elif vol_r >= 1.0:
            score += 15
        elif vol_r >= 0.7:
            score += 5

    rsi = t.get("rsi")
    if rsi is not None and not pd.isna(rsi):
        if 40 <= rsi <= 65:
            score += 15
        elif 30 <= rsi <= 75:
            score += 10

    return min(100, score)


# ============================================================
# Step 5: 综合评分
# ============================================================

def score_composite(stocks: list[dict]) -> list[dict]:
    """综合评分: 板块(20%) + 估值(25%) + 技术(30%) + 市值/换手(25%)"""
    for s in stocks:
        board = s.get("board_score", 0)

        pe = s["pe"]
        pb = s["pb"]
        val_score = 0
        if pe > 0:
            if pe < 15:
                val_score += 40
            elif pe < 30:
                val_score += 30
            elif pe < 50:
                val_score += 15
            elif pe < 80:
                val_score += 5
        if pb > 0:
            if pb < 3:
                val_score += 30
            elif pb < 5:
                val_score += 20
            elif pb < 10:
                val_score += 10
        mktcap_yi = s["mktcap"] / 1e8
        if 50 <= mktcap_yi <= 500:
            val_score += 30
        elif 500 < mktcap_yi <= 2000:
            val_score += 20
        elif 30 <= mktcap_yi < 50:
            val_score += 10
        val_score = min(100, val_score)

        tech = s.get("tech_score", 0)

        turnover = s.get("turnover_pct", 0)
        turn_score = 0
        if 2 <= turnover <= 10:
            turn_score = 30
        elif 1 <= turnover < 2:
            turn_score = 20
        elif 10 < turnover <= 15:
            turn_score = 15

        vol_ratio = s.get("vol_ratio", 0)
        if 1.0 <= vol_ratio <= 3.0:
            turn_score += 20
        elif vol_ratio > 3:
            turn_score += 10

        s["val_score"] = val_score
        s["turn_score"] = min(50, turn_score)
        s["composite"] = round(
            board * 0.20 + val_score * 0.25 + tech * 0.30 + turn_score * 0.25, 1
        )

    stocks.sort(key=lambda x: x["composite"], reverse=True)
    return stocks


# ============================================================
# 报告生成
# ============================================================

def render_scan_report(trade_date: str, candidates: list[dict],
                       stats: dict) -> str:
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 全市场扫描报告 — {trade_date}")
    lines.append(f"> 生成时间: {now}\n")

    lines.append("## 漏斗统计\n")
    lines.append("| 步骤 | 数量 |")
    lines.append("|------|-----:|")
    for step, count in stats.get("funnel", []):
        lines.append(f"| {step} | {count} |")
    lines.append(f"\n耗时: {stats.get('elapsed', 0):.1f}s\n")

    if not candidates:
        lines.append("**本次扫描无符合条件的候选股。**\n")
        return "\n".join(lines)

    lines.append(f"## 候选池 TOP{min(len(candidates), 30)}\n")
    lines.append("| # | 代码 | 名称 | 行业 | 涨跌% | PE | PB | 市值(亿) | 综合分 | 板块 | 估值 | 技术 |")
    lines.append("|--:|------|------|------|------:|---:|---:|---------:|-------:|-----:|-----:|-----:|")

    for i, s in enumerate(candidates[:30], 1):
        chg = s["change_pct"]
        sign = "+" if chg > 0 else ""
        mktcap = s["mktcap"] / 1e8
        lines.append(
            f"| {i} | {s['code']} | {s['name']} | {s['industry']} "
            f"| {sign}{chg:.2f}% | {s['pe']:.1f} | {s['pb']:.1f} "
            f"| {mktcap:.0f} | **{s['composite']:.1f}** "
            f"| {s.get('board_score', 0):.0f} | {s.get('val_score', 0):.0f} "
            f"| {s.get('tech_score', 0):.0f} |"
        )
    lines.append("")

    lines.append("### 板块归属\n")
    for s in candidates[:30]:
        themes = s.get("matched_themes", [])
        if themes:
            lines.append(f"- **{s['name']}**（{s['code']}）: {'、'.join(themes)}")
    lines.append("")

    lines.append("---")
    lines.append("*本报告由全市场扫描器自动生成，仅供参考，不构成投资建议。*")
    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def run_scan(trade_date: str = None) -> str:
    """执行全市场扫描，返回报告路径"""
    from config import REPORT_DIR
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    store.init_db()
    t0 = time.time()
    funnel = []

    # Step 1: 股票池
    print("[1/6] 获取A股代码列表...")
    universe = fetch_universe()
    secids = [s["secid"] for s in universe]
    print(f"  ✓ {len(universe)} 只A股")
    funnel.append(("全市场", len(universe)))

    # Step 2: 批量行情
    print("[2/6] 获取全市场行情+行业+概念...")
    raw_items = fetch_market_data(secids)
    parsed = []
    for item in raw_items:
        s = _parse_stock(item)
        if s:
            parsed.append(s)
    print(f"  ✓ {len(parsed)} 只有效数据")

    # Step 3: 基础过滤
    print("[3/6] 基础过滤（ST/停牌/亏损/微盘）...")
    clean = filter_junk(parsed)
    print(f"  ✓ {len(clean)} 只通过")
    funnel.append(("基础过滤", len(clean)))

    # Step 4: 板块筛选
    print("[4/6] 板块筛选...")
    hot_themes = store.load_theme_levels()
    hot_industries = _get_hot_industries()
    board_filtered = filter_by_board(clean, hot_themes, hot_industries)
    print(f"  ✓ {len(board_filtered)} 只在热门板块")
    funnel.append(("板块筛选", len(board_filtered)))

    # Step 5: 估值过滤
    print("[5/6] 估值过滤...")
    val_filtered = filter_valuation(board_filtered)
    print(f"  ✓ {len(val_filtered)} 只估值合理")
    funnel.append(("估值过滤", len(val_filtered)))

    # Step 6: 技术面过滤
    print("[6/6] 技术面过滤（拉取K线）...")
    codes = [s["code"] for s in val_filtered]
    tech_data = {}
    for code in tqdm(codes, desc="  K线扫描", unit="只",
                     bar_format="  {desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"):
        t = _compute_technicals(code)
        if t:
            tech_data[code] = t
    print(f"  ✓ {len(tech_data)} 只有K线数据")

    tech_filtered = filter_technicals(val_filtered, tech_data)
    print(f"  ✓ {len(tech_filtered)} 只通过技术面")
    funnel.append(("技术面过滤", len(tech_filtered)))

    # 综合评分
    candidates = score_composite(tech_filtered)
    funnel.append(("候选池", min(len(candidates), 30)))

    elapsed = time.time() - t0
    stats = {"funnel": funnel, "elapsed": elapsed}

    # 保存扫描结果
    store.save_scan_results(trade_date, candidates[:30])

    # 生成报告
    md = render_scan_report(trade_date, candidates, stats)
    report_path = REPORT_DIR / f"scan_{trade_date}.md"
    report_path.write_text(md, encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"  ✅ 扫描完成！耗时 {elapsed:.1f}s")
    print(f"  📄 报告: {report_path}")
    print(f"  候选池: {len(candidates)} 只")
    print(f"{'='*50}")

    if candidates:
        print(f"\nTOP 10:")
        for i, s in enumerate(candidates[:10], 1):
            themes = "、".join(s.get("matched_themes", [])[:2])
            print(f"  {i:2d}. {s['name']:8s} {s['code']}  "
                  f"综合{s['composite']:.1f}  PE={s['pe']:.1f}  "
                  f"{themes}")

    return str(report_path)


def _get_hot_industries(top_n: int = 15) -> list[str]:
    """从行业排名获取TOP N行业名称"""
    try:
        from data import fetch_industry_ranking
        ind = fetch_industry_ranking(top_n=90)
        return [r["name"] for r in ind.get("all", [])[:top_n]]
    except Exception:
        return []
