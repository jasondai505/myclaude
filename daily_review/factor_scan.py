"""缩量新高因子扫描

因子 = correl(high, volume, 10) × rank(stdev(high, 10))
值越负 → 缩量新高信号越强（机构静默吸筹 + 波动显著）
"""
import re
import sys
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

from config import (
    FACTOR_CORREL_WINDOW, FACTOR_STDEV_WINDOW,
    FACTOR_TOP_N, FACTOR_MIN_DAYS,
)

from utils import setup_console
setup_console()

REPORT_DIR = Path(__file__).parent / "reports"
REPORT_DIR.mkdir(exist_ok=True)

_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_FIELDS = "f2,f3,f6,f8,f12,f14,f20"
_PAGE_SIZE = 100

_SH_RE = re.compile(r"^(600|601|603|605|688)\d{3}$")
_SZ_RE = re.compile(r"^(000|001|002|003|300|301)\d{3}$")


def _fetch_all_stocks() -> dict[str, dict]:
    stocks = {}
    for fs, pattern in [
        ("m:1+t:2,m:1+t:23,m:1+t:80", _SH_RE),
        ("m:0+t:6,m:0+t:13,m:0+t:80", _SZ_RE),
    ]:
        pn = 1
        while True:
            params = {
                "fields": _FIELDS, "fs": fs,
                "pn": pn, "pz": _PAGE_SIZE,
                "ut": "fa5fd1943c7b386f172d6893dbbd1",
            }
            try:
                data = None
                for _retry in range(3):
                    resp = requests.get(_CLIST_URL, params=params, headers=_HEADERS, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json().get("data") or {}
                        break
                    time.sleep(0.5)
                if not data:
                    pn += 1
                    continue
                items = data.get("diff", [])
                if isinstance(items, dict):
                    items = list(items.values())
                if not items:
                    break
                for raw in items:
                    code = str(raw.get("f12", ""))
                    name = str(raw.get("f14", ""))
                    if not pattern.match(code):
                        continue
                    price_raw = raw.get("f2")
                    if price_raw is None or price_raw == "-":
                        continue
                    try:
                        price = float(price_raw) / 100
                    except (ValueError, TypeError):
                        continue
                    def _s(v, d=100):
                        if v is None or v == "-":
                            return 0
                        try:
                            return float(v) / d
                        except (ValueError, TypeError):
                            return 0
                    stocks[code] = {
                        "code": code, "name": name, "price": price,
                        "change_pct": _s(raw.get("f3")),
                        "turnover_pct": _s(raw.get("f8")),
                        "mktcap": float(raw.get("f20", 0) or 0),
                    }
                total = data.get("total", 0)
                if pn * _PAGE_SIZE >= total:
                    break
                pn += 1
                time.sleep(0.1)
            except Exception as e:
                print(f"  [WARN] clist p{pn}: {e}")
                break
    return stocks


_NEW_HIGH_LOOKBACK = 60
_NEW_HIGH_THRESHOLD = 0.95


def compute_factor(code: str) -> dict | None:
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        df = client.bars(symbol=code, category=4, offset=_NEW_HIGH_LOOKBACK)
        if df is None or df.empty or len(df) < FACTOR_CORREL_WINDOW:
            return None

        df = df.reset_index(drop=True)
        for col in ("high", "close", "vol"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        high_60d_max = df["high"].max()
        high_10d_max = df["high"].tail(FACTOR_CORREL_WINDOW).max()
        if high_60d_max <= 0 or high_10d_max < high_60d_max * _NEW_HIGH_THRESHOLD:
            return None

        h = df["high"].tail(FACTOR_CORREL_WINDOW)
        v = df["vol"].tail(FACTOR_CORREL_WINDOW)

        if h.std() == 0 or v.std() == 0:
            return None

        correl = h.corr(v)
        stdev = h.std()

        if pd.isna(correl) or pd.isna(stdev):
            return None

        return {
            "code": code,
            "correl": round(correl, 4),
            "stdev": round(stdev, 4),
            "close": float(df["close"].iloc[-1]),
            "high_10d_max": round(float(high_10d_max), 2),
            "high_60d_max": round(float(high_60d_max), 2),
        }
    except Exception:
        return None


def scan(top_n: int = FACTOR_TOP_N) -> tuple[list[dict], dict] | None:
    t0 = time.time()

    print("[1/4] 获取全市场代码+行情（分页）...")
    stocks = _fetch_all_stocks()
    print(f"  ✓ {len(stocks)} 只")

    print("[2/4] 基础过滤...")
    filtered_codes = []
    for code, s in stocks.items():
        if "ST" in s["name"]:
            continue
        if s["price"] <= 0:
            continue
        mktcap_yi = s["mktcap"] / 1e8 if s["mktcap"] > 0 else 0
        if mktcap_yi < 20:
            continue
        if s["turnover_pct"] < 1:
            continue
        filtered_codes.append(code)
    print(f"  ✓ {len(filtered_codes)} 只通过过滤")

    print(f"[3/4] 并发拉取K线 + 因子计算（{len(filtered_codes)}只，8线程）...")
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(compute_factor, code): code for code in filtered_codes}
        for future in tqdm(as_completed(futures), total=len(futures), desc="  K线扫描"):
            r = future.result()
            if r:
                results.append(r)
    print(f"  ✓ {len(results)} 只计算成功")

    if not results:
        print("  [WARN] 无有效结果")
        return None

    print("[4/4] 横截面排名 + 因子合成...")
    df = pd.DataFrame(results)
    df["rank_stdev"] = df["stdev"].rank(pct=True)
    df["factor"] = df["correl"] * df["rank_stdev"]
    df = df.sort_values("factor", ascending=True)

    top = df.head(top_n).to_dict("records")
    for r in top:
        s = stocks.get(r["code"], {})
        r["name"] = s.get("name", "")
        r["change_pct"] = s.get("change_pct", 0)
        r["mktcap_yi"] = round(s.get("mktcap", 0) / 1e8, 1)
        r["turnover_pct"] = s.get("turnover_pct", 0)

    total_neg = int((df["correl"] < -0.5).sum())
    total_pos = int((df["correl"] > 0.5).sum())

    elapsed = time.time() - t0
    print(f"\n完成！耗时 {elapsed:.0f}s，Top{top_n} 已生成")

    return top, {
        "total_scanned": len(results),
        "strong_neg": total_neg,
        "strong_pos": total_pos,
        "elapsed": elapsed,
        "filtered": len(filtered_codes),
        "universe": len(stocks),
    }


def render_report(results: list[dict], stats: dict, date: str) -> str:
    lines = [
        f"# 缩量新高选股 — {date}",
        "",
        f"> 因子: correl(high, vol, {FACTOR_CORREL_WINDOW}) x rank(stdev(high, {FACTOR_STDEV_WINDOW}))",
        f"> 扫描: 全市场{stats['universe']} → 过滤后{stats['filtered']} → 计算成功{stats['total_scanned']} → Top{len(results)}",
        f"> 耗时: {stats['elapsed']:.0f}s",
        "",
        "| # | 代码 | 名称 | 因子 | correl | stdev排名 | 收盘 | 涨跌% | 换手% | 市值(亿) |",
        "|--:|------|------|-----:|-------:|----------:|-----:|------:|------:|---------:|",
    ]

    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} "
            f"| {r['code']} "
            f"| {r['name']} "
            f"| {r['factor']:.3f} "
            f"| {r['correl']:.2f} "
            f"| {r['rank_stdev']:.0%} "
            f"| {r['close']:.2f} "
            f"| {r['change_pct']:+.2f}% "
            f"| {r['turnover_pct']:.1f}% "
            f"| {r['mktcap_yi']:.0f} |"
        )

    lines.extend([
        "",
        "## 因子分布",
        "",
        f"- correl < -0.5: **{stats['strong_neg']}只**（强缩量新高信号）",
        f"- correl > 0.5: **{stats['strong_pos']}只**（量价齐升，游资模式）",
        "",
        "---",
        "*缩量新高因子：值越负 = 价格创新高时成交量越萎缩（机构吸筹）且波动越大（突破力度强）*",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="缩量新高因子扫描")
    parser.add_argument("--top", type=int, default=FACTOR_TOP_N)
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    result = scan(top_n=args.top)
    if not result:
        print("扫描无结果")
        return

    top, stats = result
    md = render_report(top, stats, date)

    out_path = REPORT_DIR / f"factor_shrink_high_{date}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"报告已保存: {out_path}")


if __name__ == "__main__":
    main()
