"""全A实时行情快扫模块

用法:
    from daily_review.live_scanner import scan_all, top_movers

    df = scan_all()                        # 全A DataFrame (~5200只)
    df = top_movers(50)                    # 涨幅前50
    df = top_movers(50, direction="down")  # 跌幅前50

CLI:
    python daily_review/live_scanner.py                  # 全A扫描，打印前30
    python daily_review/live_scanner.py --top 50         # 涨幅前50
    python daily_review/live_scanner.py --down 50        # 跌幅前50
    python daily_review/live_scanner.py --filter "change_pct>5 and amount_wan>5000"
"""

import json
import re
import sys
import time
import argparse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import requests

from utils import setup_console

setup_console()

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_REQUEST_TIMEOUT = 10
_CACHE_DIR = Path(__file__).parent / "data"
_CACHE_DIR.mkdir(exist_ok=True)
_CODE_CACHE = _CACHE_DIR / "stock_codes.json"

_SH_RE = re.compile(r"^(600|601|603|605|688|689)\d{3}$")
_SZ_RE = re.compile(r"^(000|001|002|003|300|301)\d{3}$")
_BJ_RE = re.compile(r"^(8[3-9]|8[0-2])\d{3}$")


# ============================================================
# 股票代码列表
# ============================================================

def _fetch_codes_from_akshare() -> pd.DataFrame | None:
    """从 akshare 获取全A代码+名称，带超时保护"""
    try:
        from data import _ak
        import akshare as ak
        df = _ak(lambda: ak.stock_info_a_code_name(), timeout=20)
        if df is not None and len(df) > 4000:
            return df
    except Exception:
        pass

    try:
        import akshare as ak
        import threading
        result = [None]
        def _run():
            result[0] = ak.stock_info_a_code_name()
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(20)
        if result[0] is not None and len(result[0]) > 4000:
            return result[0]
    except Exception:
        pass

    return None


def _load_code_list(force_refresh: bool = False) -> pd.DataFrame:
    """加载全A代码列表（缓存优先）"""
    if not force_refresh and _CODE_CACHE.exists():
        try:
            cached = json.loads(_CODE_CACHE.read_text(encoding="utf-8"))
            if cached.get("date") == str(date.today()) and len(cached.get("codes", [])) > 4000:
                return pd.DataFrame(cached["codes"])
        except Exception:
            pass

    df = _fetch_codes_from_akshare()
    if df is not None:
        codes = [{"code": str(r["code"]).zfill(6), "name": str(r["name"])}
                 for _, r in df.iterrows()]
        _CODE_CACHE.write_text(
            json.dumps({"date": str(date.today()), "codes": codes}, ensure_ascii=False),
            encoding="utf-8")
        return pd.DataFrame(codes)

    # akshare 不可用时尝试读缓存（允许跨天）
    if _CODE_CACHE.exists():
        try:
            cached = json.loads(_CODE_CACHE.read_text(encoding="utf-8"))
            return pd.DataFrame(cached["codes"])
        except Exception:
            pass

    return pd.DataFrame()


def _market_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith("8"):
        return "bj"
    return "sz"


# ============================================================
# 方案1: 东财 clist（最快，需要能访问 push2.eastmoney.com）
# ============================================================

_EM_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f100,f103"
_EM_FIELD_MAP = {
    "f2": "price", "f3": "change_pct", "f4": "change_amt", "f5": "volume_shou",
    "f6": "amount", "f8": "turnover_pct", "f9": "pe_ttm", "f10": "vol_ratio",
    "f12": "code", "f14": "name",
    "f15": "high", "f16": "low", "f17": "open", "f18": "prev_close",
    "f20": "mcap", "f21": "float_mcap", "f23": "pb",
    "f100": "industry", "f103": "concepts",
}

_PREFERRED_COLS = [
    "code", "name", "price", "change_pct", "change_amt",
    "volume_shou", "amount_wan", "turnover_pct", "vol_ratio",
    "high", "low", "open", "prev_close",
    "pe_ttm", "pb", "mcap_yi", "float_mcap_yi",
    "industry", "concepts",
]


def _scan_via_eastmoney() -> pd.DataFrame | None:
    """东财 clist 分页扫描，失败返回 None

    防缓存: _ 时间戳 + Cache-Control header
    防频率限制: 页间 0.15s 间隔 + 单页最多重试 2 次
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {
        "User-Agent": _UA,
        "Referer": "https://quote.eastmoney.com/",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    }
    all_rows = []

    for fs, pattern in [
        ("m:1+t:2,m:1+t:23,m:1+t:80", _SH_RE),
        ("m:0+t:6,m:0+t:13,m:0+t:80", _SZ_RE),
    ]:
        pn = 1
        while True:
            params = {
                "fields": _EM_FIELDS, "fs": fs,
                "pn": pn, "pz": 100,
                "ut": "fa5fd1943c7b386f172d6893dbbd1",
                "_": int(time.time() * 1000),
            }

            page_items = None
            page_total = 0
            for attempt in range(3):
                try:
                    r = requests.get(url, params=params, headers=headers, timeout=15)
                    data = r.json().get("data") or {}
                    page_items = data.get("diff", [])
                    if isinstance(page_items, dict):
                        page_items = list(page_items.values())
                    page_total = data.get("total", 0)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(1.0 * (attempt + 1))
                    else:
                        return None

            if page_items is None:
                return None
            if not page_items:
                break

            for item in page_items:
                code = item.get("f12", "")
                if pattern.match(code):
                    row = {}
                    for fkey, col in _EM_FIELD_MAP.items():
                        val = item.get(fkey, None)
                        if val is None or val == "-":
                            row[col] = None
                        elif col in ("code", "name", "industry", "concepts"):
                            row[col] = str(val)
                        else:
                            try:
                                row[col] = float(val)
                            except (ValueError, TypeError):
                                row[col] = None
                    all_rows.append(row)

            if pn * 100 >= page_total:
                break
            pn += 1
            time.sleep(0.15)

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows)
    df["amount_wan"] = df["amount"].apply(
        lambda x: float(x) / 10000 if x is not None else None)
    df["mcap_yi"] = df["mcap"].apply(
        lambda x: float(x) / 1e8 if x is not None else None)
    df["float_mcap_yi"] = df["float_mcap"].apply(
        lambda x: float(x) / 1e8 if x is not None else None)
    existing = [c for c in _PREFERRED_COLS if c in df.columns]
    return df[existing]


# ============================================================
# 方案2: 腾讯行情 API（兼容性最强，几乎不被墙）
# ============================================================

def _tencent_batch(prefixed_codes: list[str]) -> dict[str, dict]:
    """批量查腾讯行情，返回 {prefixed_code: {...}}"""
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed_codes)
    url += f"?_={int(time.time() * 1000)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _UA)
    req.add_header("Cache-Control", "no-cache, no-store")
    req.add_header("Pragma", "no-cache")
    try:
        resp = urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT)
        raw = resp.read().decode("gbk")
    except Exception:
        return {}

    result = {}
    for line in raw.strip().split(";"):
        if "~" not in line or "=" not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        result[key] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_amt": float(vals[31]) if vals[31] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "vol_ratio": float(vals[49]) if vals[49] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
        }
    return result


def _scan_via_tencent(code_df: pd.DataFrame, workers: int = 8,
                      batch_size: int = 40) -> pd.DataFrame:
    """腾讯 API 多线程批量扫描"""
    codes = code_df["code"].tolist()
    name_map = dict(zip(code_df["code"], code_df["name"])) if "name" in code_df.columns else {}
    prefixed_list = [f"{_market_prefix(c)}{c}" for c in codes]

    batches = []
    for i in range(0, len(prefixed_list), batch_size):
        batches.append(prefixed_list[i:i + batch_size])

    all_results = {}

    def _fetch_batch(batch):
        return _tencent_batch(batch)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_batch, b): b for b in batches}
        for f in as_completed(futures):
            try:
                all_results.update(f.result())
            except Exception:
                pass

    rows = []
    for c, prefixed in zip(codes, prefixed_list):
        if prefixed not in all_results:
            continue
        d = all_results[prefixed]
        d["code"] = c
        if c in name_map:
            d["name"] = name_map[c]
        d.pop("amplitude_pct", None)
        d.pop("last_close", None)
        d.pop("limit_up", None)
        d.pop("limit_down", None)
        d.pop("pe_static", None)
        rows.append(d)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    existing = [c for c in _PREFERRED_COLS if c in df.columns]
    return df[existing]


# ============================================================
# 公共接口
# ============================================================

def scan_all(force_refresh_codes: bool = False,
             workers: int = 8) -> pd.DataFrame:
    """全A实时行情扫描，返回 DataFrame (~5200只)

    自动选数据源: 东财 clist > 腾讯行情 API
    耗时: 东财 ~8s, 腾讯 ~15-25s
    """
    t0 = time.perf_counter()

    # 优先东财 clist（最快）
    df = _scan_via_eastmoney()
    source = "东财"

    if df is None:
        code_df = _load_code_list(force_refresh=force_refresh_codes)
        if code_df.empty:
            print("[live_scanner] 无法获取股票代码列表")
            return pd.DataFrame(columns=_PREFERRED_COLS)
        df = _scan_via_tencent(code_df, workers=workers)
        source = "腾讯"
        # akshare 的名称更全，合并进来
        if "name" in code_df.columns and not df.empty:
            name_map = dict(zip(code_df["code"], code_df["name"]))
            df["name"] = df["code"].map(name_map).fillna(df.get("name", ""))

    elapsed = time.perf_counter() - t0
    print(f"[live_scanner] {source} | 全A {len(df)} 只 | {elapsed:.1f}s")

    return df


def top_movers(n: int = 50, direction: str = "up") -> pd.DataFrame:
    """涨/跌幅前 N 名"""
    df = scan_all()
    if df.empty:
        return df
    return df.nlargest(n, "change_pct") if direction == "up" else df.nsmallest(n, "change_pct")


# ============================================================
# CLI
# ============================================================

def _fmt_val(v, decimals: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:.{decimals}f}"


def main():
    parser = argparse.ArgumentParser(description="全A实时行情快扫")
    parser.add_argument("--top", type=int, default=0, help="涨幅前N")
    parser.add_argument("--down", type=int, default=0, help="跌幅前N")
    parser.add_argument("--filter", type=str, default="", help="过滤表达式, 如 'change_pct>5 and amount_wan>10000'")
    parser.add_argument("--workers", type=int, default=8, help="腾讯模式下并发线程数 (默认8)")
    parser.add_argument("--refresh-codes", action="store_true", help="强制刷新代码列表")
    parser.add_argument("--csv", type=str, default="", help="导出CSV路径")
    parser.add_argument("--raw", action="store_true", help="输出所有行不停顿")
    args = parser.parse_args()

    df = scan_all(force_refresh_codes=args.refresh_codes, workers=args.workers)
    if df.empty:
        print("无数据 (非交易时间?)")
        return

    if args.filter:
        df = df.query(args.filter)
        print(f"过滤后: {len(df)} 只")

    if args.down > 0:
        df = df.nsmallest(args.down, "change_pct")
    elif args.top > 0:
        df = df.nlargest(args.top, "change_pct")
    elif not args.raw and not args.csv:
        df = df.nlargest(30, "change_pct")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (f"{'代码':<8s} {'名称':<8s} {'现价':>8s} {'涨跌幅':>8s}  "
              f"{'成交额':>8s}  {'换手':>6s}  {'PE':>7s}  {'市值':>7s}  行业")
    print(f"\n时间: {now}")
    print(header)
    print("-" * 110)

    for _, row in df.iterrows():
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        price = row.get("price") or 0
        chg = row.get("change_pct") or 0
        amount = (row.get("amount_wan") or 0) / 10000  # 万元 → 亿元
        turnover = row.get("turnover_pct") or 0
        pe = row.get("pe_ttm") or 0
        mcap = row.get("mcap_yi") or 0
        ind = str(row.get("industry", "") or "")
        print(f"{code:<8s} {name:<8s} {price:>8.2f} {chg:>7.2f}%  "
              f"{amount:>7.1f}亿  {turnover:>5.1f}%  {pe:>6.1f}  {mcap:>6.0f}亿  {ind}")

    if args.csv:
        df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\n已导出: {args.csv}")


if __name__ == "__main__":
    main()
