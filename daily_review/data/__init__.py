"""每日复盘系统 - 数据抓取层（封装 a-stock-data SKILL 的所有数据源）"""
import re
import time
import urllib.request
from datetime import datetime, timedelta

import pandas as pd
import requests

from config import (
    UA, REQUEST_TIMEOUT, FETCH_DELAY, INDICES, STYLE_INDICES,
    GLOBAL_INDICES_EM, GLOBAL_WATCHLIST_EM, OVERSEAS_MAP,
    LIXINGER_TOKEN, LIXINGER_BASE,
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, REDIS_MARKET_KEY,
)

# === 全局 API 限速 ===
class RateLimiter:
    """按域名统一限速，替代散落的 time.sleep。"""
    def __init__(self, default_delay: float = 0.3):
        self._last: dict[str, float] = {}
        self._default = default_delay

    def wait(self, domain: str = "default", delay: float | None = None):
        d = delay if delay is not None else self._default
        now = time.time()
        if domain in self._last:
            elapsed = now - self._last[domain]
            if elapsed < d:
                time.sleep(d - elapsed)
        self._last[domain] = time.time()

_rate_limiter = RateLimiter()


# ============================================================
# 工具函数
# ============================================================

def _normalize_code(raw: str) -> str:
    """任意格式 → 纯6位代码"""
    raw = raw.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        raw = raw.removeprefix(prefix)
    raw = raw.split(".")[0]
    return raw


def _market_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


# ============================================================
# Redis 实时行情
# ============================================================

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
            db=REDIS_DB, decode_responses=True, protocol=2,
            socket_connect_timeout=5, socket_timeout=10,
        )
    return _redis_client


def redis_quote_all() -> dict[str, dict]:
    """从 Redis 读取全市场实时行情，返回 {code: {price, prev_close, open, ...}}
    失败返回 {}"""
    try:
        r = _get_redis()
        raw = r.hgetall(REDIS_MARKET_KEY)
        if not raw:
            return {}
        result = {}
        for code, csv_line in raw.items():
            parts = csv_line.split(",")
            if len(parts) < 38:
                continue
            code6 = _normalize_code(code)
            price = float(parts[1]) if parts[1] else 0
            prev_close = float(parts[2]) if parts[2] else 0
            if price <= 0 or prev_close <= 0:
                continue
            result[code6] = {
                "price": price,
                "prev_close": prev_close,
                "open": float(parts[3]) if parts[3] else 0,
                "high": float(parts[4]) if parts[4] else 0,
                "low": float(parts[5]) if parts[5] else 0,
                "volume_shou": float(parts[7]) if parts[7] else 0,
                "amount": float(parts[9]) if parts[9] else 0,
                "turnover_pct": float(parts[35]) if parts[35] else 0,
                "vol_ratio": float(parts[37]) if parts[37] else 0,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0,
                "change_amt": round(price - prev_close, 2),
                "low52": float(parts[33]) if parts[33] else 0,
                "high52": float(parts[34]) if parts[34] else 0,
            }
        return result
    except Exception as e:
        print(f"  [WARN] Redis 行情读取失败: {e}")
        return {}


_redis_available = None


def redis_available() -> bool:
    """检查 Redis 是否可用（缓存结果，避免反复尝试）"""
    global _redis_available
    if _redis_available is None:
        try:
            r = _get_redis()
            r.ping()
            _redis_available = True
        except Exception:
            _redis_available = False
    return _redis_available


def _stock_board(code: str) -> str:
    """返回板块: main/kcb/cyb/bj"""
    if code.startswith(("688", "689")):
        return "kcb"
    if code.startswith(("300", "301")):
        return "cyb"
    if code.startswith("8"):
        return "bj"
    return "main"


_LIMIT_PCT = {"main": 0.10, "kcb": 0.20, "cyb": 0.20, "bj": 0.30}


def _round_half_up(x: float, decimals: int = 2) -> float:
    """四舍五入（非 Python 默认的银行家舍入）"""
    import math
    m = 10 ** decimals
    return math.floor(x * m + 0.5) / m


def calc_limit_price(prev_close: float, board: str, is_st: bool = False) -> tuple[float, float]:
    """返回 (涨停价, 跌停价)
    北交所: 不能超±30%，向内取2位小数（floor/ceil）
    其他板块: 四舍五入取2位小数"""
    import math
    pct = 0.05 if is_st else _LIMIT_PCT.get(board, 0.10)
    if board == "bj":
        up = math.floor(prev_close * (1 + pct) * 100) / 100
        down = math.ceil(prev_close * (1 - pct) * 100) / 100
    else:
        up = _round_half_up(prev_close * (1 + pct))
        down = _round_half_up(prev_close * (1 - pct))
    return up, down


# ============================================================
# 腾讯行情（指数 + 个股通用）
# ============================================================

def tencent_quote_raw(prefixed_codes: list[str]) -> dict[str, dict]:
    """
    接受已带前缀的代码列表（如 ['sh000001', 'sz300476']），
    返回 {prefixed_code: {name, price, ...}}
    """
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed_codes)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
    raw = resp.read().decode("gbk")

    result = {}
    for line in raw.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        result[key] = {
            "name":         vals[1],
            "price":        float(vals[3]) if vals[3] else 0,
            "last_close":   float(vals[4]) if vals[4] else 0,
            "open":         float(vals[5]) if vals[5] else 0,
            "change_amt":   float(vals[31]) if vals[31] else 0,
            "change_pct":   float(vals[32]) if vals[32] else 0,
            "high":         float(vals[33]) if vals[33] else 0,
            "low":          float(vals[34]) if vals[34] else 0,
            "amount_wan":   float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm":       float(vals[39]) if vals[39] else 0,
            "amplitude_pct": float(vals[43]) if vals[43] else 0,
            "mcap_yi":      float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb":           float(vals[46]) if vals[46] else 0,
            "limit_up":     float(vals[47]) if vals[47] else 0,
            "limit_down":   float(vals[48]) if vals[48] else 0,
            "vol_ratio":    float(vals[49]) if vals[49] else 0,
            "pe_static":    float(vals[52]) if vals[52] else 0,
            "date":         vals[30] if len(vals) > 30 else "",
        }
    return result


def fetch_indices() -> dict[str, dict]:
    """拉取所有配置指数 + 风格指数"""
    all_codes = {}
    all_codes.update(INDICES)
    all_codes.update(STYLE_INDICES)
    prefixed = list(all_codes.values())
    raw = tencent_quote_raw(prefixed)

    result = {}
    for label, code in all_codes.items():
        if code in raw:
            result[label] = raw[code]
    return result


def fetch_stock_quotes(codes: list[str], batch_size: int = 30, max_retries: int = 3) -> dict[str, dict]:
    """拉取个股行情，Redis 优先（腾讯 API 兜底）"""
    if redis_available():
        all_quotes = redis_quote_all()
        if all_quotes:
            name_map = _load_name_map()
            result = {}
            for c in codes:
                if c in all_quotes:
                    q = all_quotes[c]
                    result[c] = {
                        "name": name_map.get(c, ""), "price": q["price"],
                        "last_close": q["prev_close"], "open": q["open"],
                        "high": q["high"], "low": q["low"],
                        "change_pct": q["change_pct"], "change_amt": q["change_amt"],
                        "amount_wan": q["amount"] / 10000 if q["amount"] else 0,
                        "turnover_pct": q["turnover_pct"], "vol_ratio": q["vol_ratio"],
                        "pe_ttm": 0, "pb": 0, "mcap_yi": 0, "float_mcap_yi": 0,
                        "amplitude_pct": 0, "limit_up": 0, "limit_down": 0,
                        "pe_static": 0, "date": q.get("time", ""),
                    }
            return result

    # 兜底: 腾讯 API
    result = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        prefixed = [f"{_market_prefix(c)}{c}" for c in batch]
        for attempt in range(1, max_retries + 1):
            try:
                raw = tencent_quote_raw(prefixed)
                for c in batch:
                    key = f"{_market_prefix(c)}{c}"
                    if key in raw:
                        result[c] = raw[key]
                break
            except Exception as e:
                if attempt < max_retries:
                    print(f"  [WARN] 行情批次 {i//batch_size+1} 第{attempt}次失败: {e}，{attempt}s后重试")
                    time.sleep(attempt)
                else:
                    print(f"  [WARN] 行情批次 {i//batch_size+1} 重试{max_retries}次仍失败: {e}")
        time.sleep(0.3)
    return result


# ============================================================
# 市场人气 — 概念板块热度 + 个股人气排名
# ============================================================

def fetch_concept_heat(top_n: int = 50) -> list[dict]:
    import akshare as ak
    df = None
    for attempt in range(3):
        df = _ak(lambda: ak.stock_fund_flow_concept())
        if df is not None and not df.empty:
            break
        if attempt < 2:
            time.sleep(2)
    if df is None or df.empty:
        print("  [WARN] 概念板块热度获取失败")
        return []
    df = df.sort_values("行业-涨跌幅", ascending=False).head(top_n)
    result = []
    for _, row in df.iterrows():
        result.append({
            "rank": int(row.get("序号", 0)),
            "name": str(row.get("行业", "")),
            "change_pct": float(row.get("行业-涨跌幅", 0)),
            "net_flow": float(row.get("净额", 0)),
            "inflow": float(row.get("流入资金", 0)),
            "outflow": float(row.get("流出资金", 0)),
            "count": int(row.get("公司家数", 0)),
            "leader": str(row.get("领涨股", "")),
            "leader_chg": float(row.get("领涨股-涨跌幅", 0)),
        })
    return result


def fetch_hot_stocks(top_n: int = 200) -> list[dict]:
    import akshare as ak
    df = None
    for attempt in range(3):
        df = _ak(lambda: ak.stock_hot_rank_em())
        if df is not None and not df.empty:
            break
        if attempt < 2:
            time.sleep(3)
    if df is None or df.empty:
        print("  [WARN] 个股人气排名获取失败")
        return []
    df = df.head(top_n)
    result = []
    for _, row in df.iterrows():
        code = _normalize_code(str(row.get("代码", "")))
        result.append({
            "rank": int(row.get("当前排名", 0)),
            "code": code,
            "name": str(row.get("股票名称", "")),
            "price": float(row.get("最新价", 0)),
            "change_pct": float(row.get("涨跌幅", 0)),
        })
    return result


# ============================================================
# 同花顺人气排名 Top100
# ============================================================

def fetch_ths_hot_stocks(period: str = "hour") -> list[dict]:
    url = f"https://eq.10jqka.com.cn/open/api/hot_list/v1/hot_stock/a/{period}/data.txt"
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.10jqka.com.cn/",
    }
    import urllib.request as _ur, json as _json
    req = _ur.Request(url, headers=headers)
    for attempt in range(3):
        try:
            resp = _ur.urlopen(req, timeout=REQUEST_TIMEOUT)
            data = _json.loads(resp.read().decode("utf-8"))
            if data.get("status_code") != 0:
                return []
            items = data.get("data", {}).get("stock_list", [])
            result = []
            for s in items:
                tag = s.get("tag") or {}
                result.append({
                    "rank": s.get("order", 0),
                    "code": str(s.get("code", "")),
                    "name": str(s.get("name", "")),
                    "hot_rate": float(s.get("rate", 0)),
                    "rank_chg": s.get("hot_rank_chg", 0),
                    "concept_tags": tag.get("concept_tag", []),
                    "pop_tag": tag.get("popularity_tag", ""),
                })
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  [WARN] THS人气排名获取失败: {e}")
                return []


# ============================================================
# 研报评级（东方财富）
# ============================================================

def fetch_stock_research(code: str, limit: int = 5) -> list[dict]:
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_research_report_em(symbol=code))
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.head(limit).iterrows():
            rows.append({
                "title": str(row.get("报告名称", "")),
                "rating": str(row.get("东财评级", "")),
                "org": str(row.get("机构", "")),
                "date": str(row.get("日期", "")),
                "eps_cur": row.get("2026-盈利预测-收益"),
                "pe_cur": row.get("2026-盈利预测-市盈率"),
                "eps_next": row.get("2027-盈利预测-收益"),
                "pe_next": row.get("2027-盈利预测-市盈率"),
                "report_count": row.get("近一月个股研报数", 0),
            })
        return rows
    except Exception:
        return []


# ============================================================
# 龙虎榜（东方财富）
# ============================================================

def fetch_lhb(date: str = None) -> dict[str, dict]:
    try:
        import akshare as ak
        if date is None:
            from datetime import date as _date
            date = _date.today().strftime("%Y-%m-%d")
        date_compact = date.replace("-", "")
        df = _ak(lambda: ak.stock_lhb_detail_em(start_date=date_compact, end_date=date_compact))
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            code = _normalize_code(str(row.get("代码", "")))
            result[code] = {
                "code": code,
                "name": str(row.get("名称", "")),
                "net_buy": float(row.get("龙虎榜净买额", 0)),
                "buy_amt": float(row.get("龙虎榜买入额", 0)),
                "sell_amt": float(row.get("龙虎榜卖出额", 0)),
                "reason": str(row.get("上榜原因", "")),
                "comment": str(row.get("解读", "")),
            }
        return result
    except Exception as e:
        print(f"  [WARN] 龙虎榜获取失败: {e}")
        return {}


# ============================================================
# 涨停池 — Redis 实时行情 + akshare 补充连板数
# ============================================================

def _load_name_map() -> dict[str, str]:
    """加载代码→名称映射（优先本地缓存）"""
    try:
        from pathlib import Path
        import json
        cache = Path(__file__).parent / "data" / "stock_codes.json"
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            return {c["code"]: c["name"] for c in data.get("codes", [])}
    except Exception:
        pass
    return {}


# 名称→代码反向映射（懒加载缓存）
_NAME_TO_CODE: dict[str, str] | None = None


def _load_name_to_code_map() -> dict[str, str]:
    """加载名称→代码反向映射（去除名称中的空格）。"""
    global _NAME_TO_CODE
    if _NAME_TO_CODE is not None:
        return _NAME_TO_CODE
    _NAME_TO_CODE = {}
    try:
        from pathlib import Path
        import json
        cache = Path(__file__).parent / "data" / "stock_codes.json"
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            for c in data.get("codes", []):
                name = str(c.get("name", "")).replace(" ", "").strip()
                code = str(c.get("code", ""))
                if name and code:
                    _NAME_TO_CODE[name] = code
    except Exception:
        pass
    return _NAME_TO_CODE


def extract_codes_from_text(text: str) -> set[str]:
    """从文本中提取股票代码：正则6位代码 + 全名反向匹配。

    覆盖两种常见写法：
    - 写代码的：「688167」「300757」→ 正则直接命中
    - 只写名称的：「炬光科技」「罗博特科」→ 全名反向映射命中
    """
    codes = set(re.findall(r"\b(\d{6})\b", text))
    name_map = _load_name_to_code_map()
    if name_map:
        for name, code in name_map.items():
            if name in text:
                codes.add(code)
    return codes


def _fetch_zt_pool_redis(name_map: dict[str, str] | None = None) -> dict[str, dict]:
    """Redis 涨停池: 价格触板判定"""
    quotes = redis_quote_all()
    if not quotes:
        return {}
    if name_map is None:
        name_map = _load_name_map()
    result = {}
    for code, q in quotes.items():
        if q["prev_close"] <= 0:
            continue
        board = _stock_board(code)
        name = name_map.get(code, "")
        is_st = "ST" in name.upper()
        limit_up, _ = calc_limit_price(q["prev_close"], board, is_st)
        if q["price"] >= limit_up:
            result[code] = {
                "name": name,
                "price": q["price"],
                "change_pct": q["change_pct"],
                "consecutive_boards": 1,  # Redis 快照无历史，引擎有 hot_df 兜底
                "first_time": "",
                "last_time": "",
                "zt_stats": "",
                "blasted": 0,
            }
    return result


def fetch_zt_pool(date: str = None) -> dict[str, dict]:
    """涨停池: Redis 优先，akshare 兜底"""
    if redis_available():
        name_map = _load_name_map()
        result = _fetch_zt_pool_redis(name_map)
        if result:
            print(f"  ✓ 涨停池(Redis) {len(result)} 只")
            return result

    # 兜底: akshare
    import akshare as ak
    if date is None:
        from datetime import date as _date
        date = _date.today().strftime("%Y-%m-%d")
    date_compact = date.replace("-", "")
    df = _ak(lambda: ak.stock_zt_pool_em(date=date_compact))
    if df is None:
        print("  [WARN] 涨停池获取失败")
        return {}
    if df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        ft = str(row.get("首次封板时间", ""))
        lt = str(row.get("最后封板时间", ""))
        first_time = f"{ft[:2]}:{ft[2:4]}:{ft[4:]}" if len(ft) == 6 else ft
        last_time = f"{lt[:2]}:{lt[2:4]}:{lt[4:]}" if len(lt) == 6 else lt
        result[code] = {
            "name": str(row.get("名称", "")),
            "first_time": first_time,
            "last_time": last_time,
            "zt_stats": str(row.get("涨停统计", "")),
            "consecutive_boards": int(row.get("连板数", 1)),
            "blasted": int(row.get("炸板次数", 0)),
        }
    print(f"  ✓ 涨停池(akshare) {len(result)} 只")
    return result


# ============================================================
# 跌停池
# ============================================================

def _fetch_dt_pool_redis(name_map: dict[str, str] | None = None) -> dict[str, dict]:
    """Redis 跌停池: 价格触板判定"""
    quotes = redis_quote_all()
    if not quotes:
        return {}
    if name_map is None:
        name_map = _load_name_map()
    result = {}
    for code, q in quotes.items():
        if q["prev_close"] <= 0:
            continue
        board = _stock_board(code)
        name = name_map.get(code, "")
        is_st = "ST" in name.upper()
        _, limit_down = calc_limit_price(q["prev_close"], board, is_st)
        if q["price"] <= limit_down:
            result[code] = {
                "name": name,
                "close": q["price"],
                "chg_pct": q["change_pct"],
            }
    return result


def fetch_dt_pool(date: str = None) -> dict[str, dict]:
    """跌停池: Redis 优先，akshare 兜底"""
    if redis_available():
        name_map = _load_name_map()
        result = _fetch_dt_pool_redis(name_map)
        if result:
            print(f"  ✓ 跌停池(Redis) {len(result)} 只")
            return result

    import akshare as ak
    if date is None:
        from datetime import date as _date
        date = _date.today().strftime("%Y-%m-%d")
    date_compact = date.replace("-", "")
    df = _ak(lambda: ak.stock_zt_pool_dtgc_em(date=date_compact))
    if df is None:
        print("  [WARN] 跌停池获取失败")
        return {}
    if df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        result[code] = {
            "name": str(row.get("名称", "")),
            "close": float(row.get("最新价", 0) or 0),
            "chg_pct": float(row.get("涨跌幅", 0) or 0),
        }
    print(f"  ✓ 跌停池(akshare) {len(result)} 只")
    return result


# ============================================================
# 同花顺热点 — 当日强势股 + 题材归因
# ============================================================

def fetch_hot_themes(date: str = None) -> pd.DataFrame:
    """
    date: YYYY-MM-DD，None=今天
    返回 DataFrame: 代码, 名称, 涨幅%, 换手率%, 成交额, 题材归因, 大单净量
    """
    from datetime import date as _date
    if date is None:
        date = _date.today().strftime("%Y-%m-%d")

    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{date}/orderby/date/orderway/desc/charset/GBK/"
    )
    headers = {"User-Agent": UA}
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    data = r.json()
    if data.get("errocode", 0) != 0:
        return pd.DataFrame()

    rows = data.get("data") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    rename_map = {
        "name": "名称", "code": "代码", "reason": "题材归因",
        "close": "收盘价", "zhangfu": "涨幅%",
        "huanshou": "换手率%", "chengjiaoe": "成交额",
        "ddejingliang": "大单净量", "market": "市场",
    }
    df = df.rename(columns=rename_map)
    return df


# ============================================================
# 问财股池 — 人气前100 / 20日涨幅前100（§4 三池合并用）
# ============================================================

def _fetch_wencai(query: str):
    """问财查询，失败/未装返回 None（兜底，不抛异常）。"""
    try:
        import pywencai
    except ImportError:
        print("  [WARN] 未安装 pywencai，跳过该池")
        return None
    try:
        df = pywencai.get(query=query, query_type="stock", loop=True)
    except Exception as e:
        print(f"  [WARN] 问财查询失败（{query}）: {e}")
        return None
    if df is None or not hasattr(df, "empty") or df.empty:
        return None
    return df


def _parse_concepts(raw) -> list[str]:
    """所属概念字符串 → 列表（兼容 ; 与 ；分隔）。"""
    if raw is None:
        return []
    txt = str(raw).strip()
    if not txt or txt.lower() == "nan":
        return []
    return [p.strip() for p in re.split(r"[;；]", txt) if p.strip()]


def _wencai_pool(query: str) -> list[dict]:
    """返回 [{code, name, chg, concepts:[...]}]，失败返回 []。"""
    df = _fetch_wencai(query)
    if df is None:
        return []
    cols = list(df.columns)
    concept_col = "所属概念" if "所属概念" in cols else None
    chg_col = "最新涨跌幅" if "最新涨跌幅" in cols else None
    out = []
    for _, row in df.iterrows():
        code = str(row.get("code", "") or "").strip()
        if not code:
            code = str(row.get("股票代码", "") or "").split(".")[0].strip()
        if not code:
            continue
        chg = 0.0
        if chg_col:
            try:
                chg = float(row.get(chg_col) or 0)
            except (ValueError, TypeError):
                chg = 0.0
        out.append({
            "code": code,
            "name": str(row.get("股票简称", "") or ""),
            "chg": chg,
            "concepts": _parse_concepts(row.get(concept_col)) if concept_col else [],
        })
    return out


def fetch_popularity_top100() -> list[dict]:
    """池②：人气前100 + 所属概念。"""
    return _wencai_pool("人气前100 所属概念")


def fetch_gainers_20d() -> list[dict]:
    """池③：20日涨幅前100 + 所属概念。"""
    return _wencai_pool("20日涨幅前100 所属概念")


# ============================================================
# 北向资金
# ============================================================

_HSGT_HEADERS = {
    "User-Agent": UA,
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}


def fetch_northbound() -> dict:
    """返回 {df: 分钟级DataFrame, hgt_close: 沪股通收盘, sgt_close: 深股通收盘}"""
    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    r = requests.get(url, headers=_HSGT_HEADERS, timeout=REQUEST_TIMEOUT)
    d = r.json()
    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])

    n = len(times)
    df = pd.DataFrame({
        "time": times,
        "hgt_yi": hgt[:n] + [None] * max(0, n - len(hgt)),
        "sgt_yi": sgt[:n] + [None] * max(0, n - len(sgt)),
    })

    hgt_close = df["hgt_yi"].dropna().iloc[-1] if not df["hgt_yi"].dropna().empty else 0
    sgt_close = df["sgt_yi"].dropna().iloc[-1] if not df["sgt_yi"].dropna().empty else 0

    return {
        "df": df,
        "hgt_close": float(hgt_close),
        "sgt_close": float(sgt_close),
        "total": float(hgt_close) + float(sgt_close),
    }


# ============================================================
# 行业排名
# ============================================================

def fetch_industry_ranking(top_n: int = 90) -> dict:
    """返回 {all: [...], total_up: int, total_down: int}"""
    import akshare as ak
    df = _ak(lambda: ak.stock_board_industry_summary_ths())
    if df is None or df.empty:
        return {"all": [], "total_up": 0, "total_down": 0}

    rows = []
    total_up = 0
    total_down = 0
    for i, row in df.iterrows():
        up = int(row.get("上涨家数", 0) or 0)
        down = int(row.get("下跌家数", 0) or 0)
        total_up += up
        total_down += down
        rows.append({
            "rank": i + 1,
            "name": row.get("板块", ""),
            "change_pct": float(row.get("涨跌幅", 0) or 0),
            "turnover_yi": row.get("总成交额", 0),
            "net_inflow": row.get("净流入", None) if "净流入" in df.columns else None,
            "up_count": up,
            "down_count": down,
            "leader": row.get("领涨股", ""),
            "leader_pct": row.get("领涨股-涨跌幅", None) if "领涨股-涨跌幅" in df.columns else None,
        })

    return {"all": rows, "total_up": total_up, "total_down": total_down}


# ============================================================
# K线 + 技术指标（mootdx）
# ============================================================

def fetch_klines(code: str, days: int = 120) -> pd.DataFrame | None:
    """拉取日K线并计算MA和MACD，返回带技术指标的DataFrame"""
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std')
        df = client.bars(symbol=code, category=4, offset=days)
        if df is None or df.empty:
            return None

        df = df.reset_index(drop=True)
        for col in ["open", "close", "high", "low", "vol", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        from config import MA_PERIODS
        for p in MA_PERIODS:
            df[f"ma{p}"] = df["close"].rolling(p).mean()

        df["vol_ma20"] = df["vol"].rolling(20).mean()
        df["vol_ratio_20"] = df["vol"] / df["vol_ma20"]

        # MACD
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        df["dif"] = ema12 - ema26
        df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
        df["macd"] = (df["dif"] - df["dea"]) * 2

        # RSI 14
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        df["rsi"] = 100 - 100 / (1 + rs)

        return df
    except Exception as e:
        print(f"  [WARN] K线获取失败 {code}: {e}")
        return None


# ============================================================
# 百度股市通 — 资金流向
# ============================================================

_BAIDU_HEADERS = {
    "Host": "finance.pae.baidu.com",
    "User-Agent": UA,
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


def fetch_fund_flow(code: str, days: int = 10) -> list[dict]:
    """个股资金流向（日级），返回最近N天"""
    try:
        url = (
            f"https://finance.pae.baidu.com/vapi/v1/fundsortlist"
            f"?code={code}&market=ab&pn=0&rn={days}&finClientType=pc"
        )
        r = requests.get(url, headers=_BAIDU_HEADERS, timeout=REQUEST_TIMEOUT)
        d = r.json()
        if str(d.get("ResultCode", -1)) != "0":
            return []
        rows = []
        for item in d.get("Result", {}).get("list", []):
            rows.append({
                "date": item.get("showtime", ""),
                "close": item.get("closepx", ""),
                "change_pct": item.get("ratio", ""),
                "main_in": item.get("extMainIn", ""),
            })
        return rows
    except Exception:
        return []


# ============================================================
# 龙虎榜
# ============================================================

def fetch_dragon_tiger(code: str, trade_date: str, look_back: int = 10) -> list[dict]:
    """近 look_back 天龙虎榜上榜记录"""
    try:
        import akshare as ak
        start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
        df = _ak(lambda: ak.stock_lhb_detail_em(
            start_date=start.strftime("%Y%m%d"),
            end_date=trade_date.replace("-", ""),
        ))
        if df is None or df.empty:
            return []
        df_stock = df[df["代码"] == code]
        records = []
        for _, row in df_stock.iterrows():
            records.append({
                "date": str(row.get("日期", "")),
                "reason": row.get("解读", ""),
                "net_buy": row.get("龙虎榜净买额", 0),
            })
        return records
    except Exception:
        return []


# ============================================================
# 限售解禁
# ============================================================

def fetch_lockup(code: str) -> list[dict]:
    """未来解禁记录"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_restricted_release_queue_em(symbol=code))
        if df is None or df.empty:
            return []
        upcoming = []
        today = datetime.now().strftime("%Y-%m-%d")
        for _, row in df.iterrows():
            d = str(row.get("解禁时间", ""))[:10]
            if d >= today:
                upcoming.append({
                    "date": d,
                    "type": row.get("限售股类型", ""),
                    "shares": row.get("解禁数量", 0),
                    "ratio": row.get("实际解禁市值占总市值比例", 0),
                })
        return upcoming[:5]
    except Exception:
        return []


# ============================================================
# 概念板块归属
# ============================================================

def fetch_concept_tags(code: str) -> list[str]:
    """返回个股所属概念标签列表"""
    try:
        url = (
            f"https://finance.pae.baidu.com/api/getrelatedblock"
            f"?code={code}&market=ab&typeCode=all&finClientType=pc"
        )
        r = requests.get(url, headers=_BAIDU_HEADERS, timeout=REQUEST_TIMEOUT)
        d = r.json()
        if str(d.get("ResultCode", -1)) != "0":
            return []
        tags = []
        for block in d.get("Result", []):
            if "概念" in block.get("type", ""):
                for item in block.get("list", []):
                    tags.append(item.get("name", ""))
        return tags
    except Exception:
        return []


# ============================================================
# 外围市场（东方财富 push2 API）
# ============================================================

_EM_HEADERS = {
    "User-Agent": UA,
    "Referer": "https://quote.eastmoney.com/",
}


def _em_kline_5d_pct(secid: str) -> float | None:
    """东财 push2his 拿最近 6 根日线，返回 (close[-1]/close[-6]-1)*100，即 5 个交易日累计涨幅。"""
    try:
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&klt=101&fqt=1&end=20500101&lmt=6"
            f"&fields1=f1&fields2=f51,f53"
        )
        r = requests.get(url, headers=_EM_HEADERS, timeout=REQUEST_TIMEOUT)
        d = r.json()
        klines = (d.get("data") or {}).get("klines") or []
        if len(klines) < 2:
            return None
        closes = [float(k.split(",")[1]) for k in klines]
        base = closes[0]
        last = closes[-1]
        if base <= 0:
            return None
        return round((last / base - 1) * 100, 2)
    except Exception as e:
        print(f"  [WARN] EM 5日K线获取失败 {secid}: {e}")
        return None


def _em_quote_single(secid: str) -> dict | None:
    """东方财富单只证券实时行情"""
    try:
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get"
            f"?secid={secid}&fields=f43,f44,f45,f46,f47,f48,f57,f58,f169,f170"
            f"&_={int(time.time()*1000)}"
        )
        r = requests.get(url, headers=_EM_HEADERS, timeout=REQUEST_TIMEOUT)
        d = r.json()
        raw = d.get("data")
        if not raw:
            return None
        market = int(secid.split(".")[0])
        divisor = 1000 if market in (105, 106, 116, 128) else 100
        # market 124 = HK indices, also uses /100
        price = raw.get("f43", 0) or 0
        return {
            "name": raw.get("f58", ""),
            "code": raw.get("f57", ""),
            "price": price / divisor,
            "high": (raw.get("f44", 0) or 0) / divisor,
            "low": (raw.get("f45", 0) or 0) / divisor,
            "open": (raw.get("f46", 0) or 0) / divisor,
            "change_pct": (raw.get("f170", 0) or 0) / 100,
            "change_amt": (raw.get("f169", 0) or 0) / divisor,
            "amount_wan": (raw.get("f48", 0) or 0) / 10000,
        }
    except Exception as e:
        print(f"  [WARN] EM行情获取失败 {secid}: {e}")
        return None


def fetch_global_markets() -> dict:
    """拉取外围市场指数 + 关注标的"""
    result = {"indices": {}, "watchlist": {}}

    for label, secid in GLOBAL_INDICES_EM.items():
        q = _em_quote_single(secid)
        if q:
            q["change_pct_5d"] = _em_kline_5d_pct(secid)
            result["indices"][label] = q
        time.sleep(0.2)

    for item in GLOBAL_WATCHLIST_EM:
        q = _em_quote_single(item["secid"])
        if q:
            q["name"] = q["name"] or item["label"]
            q["tag"] = item.get("tag", "")
            q["change_pct_5d"] = _em_kline_5d_pct(item["secid"])
            result["watchlist"][item["label"]] = q
        time.sleep(0.2)

    if len(result["indices"]) < len(GLOBAL_INDICES_EM):
        fallback = _fetch_indices_akshare()
        for k, v in fallback.items():
            if k not in result["indices"] or not result["indices"][k].get("price"):
                result["indices"][k] = v

    us_missing = [
        item for item in GLOBAL_WATCHLIST_EM
        if item.get("tag", "").startswith("us_") and item["label"] not in result["watchlist"]
    ]
    if us_missing:
        result["watchlist"].update(_fetch_us_stocks_akshare(us_missing))

    return result


def fetch_us_movers() -> dict:
    """拉取美股异动股（按涨跌幅绝对值排序，>3% 标注），含 A 股映射。
    返回 {sector_tag: {label, change_pct, a_map, movers[]}}。
    """
    global_data = fetch_global_markets()
    wl = global_data.get("watchlist", {})

    sectors = {}
    for label, q in wl.items():
        chg = q.get("change_pct") or 0
        tag = q.get("tag", "")
        s_name = tag.split("_", 1)[-1] if tag.startswith("us_") else tag
        if s_name not in sectors:
            sectors[s_name] = {"movers": [], "weighted_chg": 0}
        sectors[s_name]["movers"].append({"label": label, "chg": chg})
        sectors[s_name]["weighted_chg"] += chg

    for key in sectors:
        movers = sectors[key]["movers"]
        movers.sort(key=lambda x: abs(x["chg"]), reverse=True)
        n = len(movers)
        sectors[key]["weighted_chg"] = round(sectors[key]["weighted_chg"] / n, 2) if n else 0
        sectors[key]["top_gainers"] = [m for m in movers if m["chg"] > 3]
        sectors[key]["top_losers"] = [m for m in movers if m["chg"] < -3]

    return sectors


def fetch_kr_jp_markets() -> dict:
    """拉取日韩早盘指数快照（KOSPI + 日经225），含涨跌幅和 5 日趋势。"""
    result = {}
    for label in ("韩国KOSPI", "日经225"):
        secid = GLOBAL_INDICES_EM.get(label)
        if not secid:
            continue
        q = _em_quote_single(secid)
        if q:
            q["change_pct_5d"] = _em_kline_5d_pct(secid)
            result[label] = q
        time.sleep(0.2)
    return result


_INDEX_AKSHARE_MAP = {
    "道琼斯":   (".DJI",  "us"),
    "纳斯达克": (".IXIC", "us"),
    "标普500":  (".INX",  "us"),
    "恒生指数": ("HSI",   "hk"),
    "恒生科技": ("HSTECH", "hk"),
}

def _fetch_indices_akshare() -> dict:
    """akshare备用：拉取全球指数日线"""
    import akshare as ak
    out = {}
    for label, (symbol, market) in _INDEX_AKSHARE_MAP.items():
        try:
            if market == "us":
                df = _ak(lambda: ak.index_us_stock_sina(symbol=symbol))
            else:
                df = _ak(lambda: ak.stock_hk_index_daily_sina(symbol=symbol))
            if df is None or len(df) < 2:
                continue
            last, prev = df.iloc[-1], df.iloc[-2]
            chg = (last["close"] - prev["close"]) / prev["close"] * 100
            data_date = str(last.get("date", df.index[-1]))[:10] if hasattr(df.index[-1], '__str__') else str(last.get("date", ""))[:10]
            out[label] = {
                "name": label,
                "price": float(last["close"]),
                "change_pct": round(chg, 2),
                "data_date": data_date,
            }
            time.sleep(0.2)
        except Exception as e:
            print(f"  [WARN] akshare指数获取失败 {label}: {e}")
    return out


def _fetch_us_stocks_akshare(items: list[dict]) -> dict:
    """akshare备用：通过stock_us_daily拉美股收盘数据"""
    import akshare as ak
    out = {}
    for item in items:
        ticker = item["secid"].split(".")[-1]
        try:
            df = _ak(lambda: ak.stock_us_daily(symbol=ticker, adjust="qfq"))
            if df is None or len(df) < 2:
                continue
            last, prev = df.iloc[-1], df.iloc[-2]
            chg = (last["close"] - prev["close"]) / prev["close"] * 100
            out[item["label"]] = {
                "name": item["label"],
                "code": ticker,
                "price": float(last["close"]),
                "change_pct": round(chg, 2),
                "tag": item.get("tag", ""),
            }
            time.sleep(0.3)
        except Exception as e:
            print(f"  [WARN] akshare美股获取失败 {ticker}: {e}")
    return out


# ============================================================
# 基本面数据（akshare）
# ============================================================

def fetch_eps_forecast(code: str) -> list[dict]:
    """一致预期EPS（同花顺）"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_profit_forecast_ths(symbol=code))
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "year": str(row.get("年度", "")),
                "eps": row.get("均值", None),
                "max_eps": row.get("最大值", None),
                "min_eps": row.get("最小值", None),
                "inst_count": row.get("预测机构数", None),
            })
        return rows
    except Exception:
        return []


def fetch_stock_news(code: str, limit: int = 5) -> list[dict]:
    """个股最近新闻（东方财富）。

    akshare stock_news_em 在 .str.replace(r\"\\u3000\", regex=True) 处
    与新版 pyarrow 不兼容（ArrowInvalid: invalid escape sequence \\u），
    此处直调东财 API 并自行清洗 HTML，绕过该 bug。
    """
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_news_em(symbol=code))
        if df is not None and not df.empty:
            rows = []
            for _, row in df.head(limit).iterrows():
                title = _s(row.get("新闻标题"))
                if not title or any(p.search(title) for p in _NEWS_TITLE_BLACKLIST):
                    continue
                rows.append({
                    "title": title,
                    "content": _clean_html(_s(row.get("新闻内容")))[:200],
                    "time": _s(row.get("发布时间")),
                    "source": _s(row.get("文章来源")),
                })
            return rows
    except Exception:
        pass

    return _fetch_stock_news_direct(code, limit)


def _fetch_stock_news_direct(code: str, limit: int = 5) -> list[dict]:
    """绕过 akshare，直调东方财富搜索 API 拉个股新闻。"""
    import json as _json
    inner = {
        "uid": "", "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": 10,
            "preTag": "<em>", "postTag": "</em>",
        }},
    }
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": "jQuery",
        "param": _json.dumps(inner, ensure_ascii=False),
        "_": str(int(time.time() * 1000)),
    }
    headers = {
        "User-Agent": UA,
        "Referer": f"https://so.eastmoney.com/news/s?keyword={code}",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        text = r.text
        start = text.find("(")
        end = text.rfind(")")
        if start == -1 or end == -1:
            return []
        data = _json.loads(text[start + 1:end])
        items = data.get("result", {}).get("cmsArticleWebOld", [])
        rows = []
        for item in items[:limit]:
            title = _clean_html(_s(item.get("title")))
            if not title or any(p.search(title) for p in _NEWS_TITLE_BLACKLIST):
                continue
            content = _clean_html(_s(item.get("content")))[:200]
            rows.append({
                "title": title,
                "content": content,
                "time": _s(item.get("date")),
                "source": _s(item.get("mediaName")),
            })
        return rows
    except Exception:
        return []


def _clean_html(text: str) -> str:
    """去除 HTML 标签 + 全角空格 + 换行符。"""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("　", "").replace("\r\n", " ").replace("\n", " ")
    return text.strip()


def fetch_shareholder_count(code: str) -> list[dict]:
    """股东户数变化（东方财富）"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_zh_a_gdhs_detail_em(symbol=code))
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.head(5).iterrows():
            rows.append({
                "date": str(row.get("股东户数统计截止日", ""))[:10],
                "count": row.get("股东户数-本次", None),
                "change_pct": row.get("股东户数-增减比例", None),
            })
        return rows
    except Exception:
        return []


def fetch_theme_news(keyword: str, limit: int = 10) -> list[str]:
    """搜索题材相关新闻标题（用于AI审美分析）"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_news_em(symbol=keyword))
        if df is None or df.empty:
            return []
        return [str(row.get("新闻标题", "")) for _, row in df.head(limit).iterrows()]
    except Exception:
        return []


# ============================================================
# 语料：公告 / 互动平台（用于聚焦池语料摘要）
# ============================================================

def _run_with_timeout(fn, timeout_sec, default=None):
    """concurrent.futures 超时防护，正确释放线程资源（不再泄漏守护线程）。"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_sec)
        except (concurrent.futures.TimeoutError, Exception):
            future.cancel()
            return default


IRM_TIMEOUT_SEC = 12


def _ak(fn, timeout=IRM_TIMEOUT_SEC):
    """所有 akshare 调用的统一入口：套 _run_with_timeout 守护线程超时兜底。
    akshare 底层 requests 无 socket 超时，单请求可挂死数小时（try/except 拦不住 hang），
    per-stock 循环里尤甚。超时/异常均返 None（=抓取失败语义，调用方已用
    `if df is None or df.empty` 容忍）。新增 ak.* 调用一律走这里。"""
    return _run_with_timeout(fn, timeout)


def fetch_announcements_all(date_yyyymmdd: str) -> dict[str, list[dict]]:
    """一次拉当日全市场公告（巨潮/东财汇总），返回 {code: [{title, type, date, url}, ...]}"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_notice_report(symbol="全部", date=date_yyyymmdd))
        if df is None or df.empty:
            return {}
        out: dict[str, list[dict]] = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            out.setdefault(code, []).append({
                "title": str(row.get("公告标题", "")),
                "type": str(row.get("公告类型", "")),
                "date": str(row.get("公告日期", ""))[:10],
                "url": str(row.get("网址", "")),
            })
        return out
    except Exception:
        return {}


def fetch_irm_szse(code: str, limit: int = 3) -> list[dict]:
    """深交所互动易问答（002/300/301）"""
    if not code.startswith(("0", "3")):
        return []
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_irm_cninfo(symbol=code))
        if df is None or df.empty:
            return []
        df = df.sort_values("更新时间", ascending=False)
        rows = []
        for _, row in df.head(limit).iterrows():
            rows.append({
                "question": _s(row.get("问题"))[:200],
                "answer": _s(row.get("回答内容"))[:300],
                "ask_time": _s(row.get("提问时间"))[:16],
                "reply_time": _s(row.get("更新时间"))[:16],
            })
        return rows
    except Exception:
        return []


def fetch_irm_sse(code: str, limit: int = 3) -> list[dict]:
    """上交所 e 互动问答（6 开头），接口偶尔返回空，失败静默"""
    if not code.startswith("6"):
        return []
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_sns_sseinfo(symbol=code))
        if df is None or df.empty:
            return []
        cols = list(df.columns)
        q_col = next((c for c in cols if "问" in c and "时间" not in c), None)
        a_col = next((c for c in cols if "答" in c), None)
        t_col = next((c for c in cols if "时间" in c), None)
        if not q_col:
            return []
        if t_col:
            df = df.sort_values(t_col, ascending=False)
        rows = []
        for _, row in df.head(limit).iterrows():
            rows.append({
                "question": _s(row.get(q_col))[:200],
                "answer": _s(row.get(a_col))[:300] if a_col else "",
                "ask_time": _s(row.get(t_col))[:16] if t_col else "",
                "reply_time": "",
            })
        return rows
    except Exception:
        return []


# ============================================================
# 基本面源：业绩预告/快报 / 机构调研 / 行业研报
# ============================================================

def _num(v):
    """安全转 float，NaN/空 → None。"""
    try:
        if v is None:
            return None
        f = float(v)
        return None if pd.isna(f) else f
    except Exception:
        return None


def _s(v, default: str = "") -> str:
    """安全转 str，NaN/None → default。"""
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    return str(v)


_NEWS_TITLE_BLACKLIST = [re.compile(r"龙虎榜数据\s*\d{2}-\d{2}")]


def recent_report_periods(until: str | None = None, n: int = 2) -> list[str]:
    """返回截至 until(YYYY-MM-DD) 的最近 n 个报告期末，格式 YYYYMMDD（倒序，最新在前）。"""
    if until:
        ref = datetime.strptime(until[:10], "%Y-%m-%d")
    else:
        ref = datetime.now()
    quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    periods: list[str] = []
    year = ref.year
    while len(periods) < n + 1:
        for m, d in reversed(quarter_ends):
            pe = datetime(year, m, d)
            if pe <= ref:
                periods.append(pe.strftime("%Y%m%d"))
                if len(periods) >= n + 1:
                    break
        year -= 1
    return periods[:n]


def fetch_earnings_forecast(period: str) -> list[dict]:
    """业绩预告（东财），period=YYYYMMDD 报告期末。返回全市场，调用方按 universe/公告日期过滤。"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_yjyg_em(date=period))
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "code": str(r.get("股票代码", "")).zfill(6),
                "name": str(r.get("股票简称", "")),
                "indicator": str(r.get("预测指标", "")),
                "forecast_type": str(r.get("预告类型", "")),
                "change_desc": str(r.get("业绩变动", ""))[:300],
                "value": _num(r.get("预测数值")),
                "change_pct": _num(r.get("业绩变动幅度")),
                "reason": str(r.get("业绩变动原因", ""))[:200] if r.get("业绩变动原因") is not None else "",
                "prev_value": _num(r.get("上年同期值")),
                "notice_date": str(r.get("公告日期", ""))[:10],
                "period": period,
            })
        return rows
    except Exception as e:
        print(f"  [WARN] 业绩预告获取失败({period}): {e}")
        return []


def fetch_earnings_express(period: str) -> list[dict]:
    """业绩快报（东财），period=YYYYMMDD 报告期末。"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_yjkb_em(date=period))
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "code": str(r.get("股票代码", "")).zfill(6),
                "name": str(r.get("股票简称", "")),
                "eps": _num(r.get("每股收益")),
                "revenue": _num(r.get("营业收入-营业收入")),
                "revenue_yoy": _num(r.get("营业收入-同比增长")),
                "net_profit": _num(r.get("净利润-净利润")),
                "net_profit_yoy": _num(r.get("净利润-同比增长")),
                "bps": _num(r.get("每股净资产")),
                "roe": _num(r.get("净资产收益率")),
                "industry": str(r.get("所处行业", "")),
                "notice_date": str(r.get("公告日期", ""))[:10],
                "period": period,
            })
        return rows
    except Exception as e:
        print(f"  [WARN] 业绩快报获取失败({period}): {e}")
        return []


def fetch_inst_survey(period: str) -> list[dict]:
    """机构调研统计（东财 stock_jgdy_tj_em），period=YYYYMMDD 报告期末。"""
    try:
        import akshare as ak
        df = _ak(lambda: ak.stock_jgdy_tj_em(date=period))
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "code": str(r.get("代码", "")).zfill(6),
                "name": str(r.get("名称", "")),
                "change_pct": _num(r.get("涨跌幅")),
                "inst_count": int(_num(r.get("接待机构数量")) or 0),
                "method": str(r.get("接待方式", "")),
                "attendees": str(r.get("接待人员", ""))[:300],
                "location": str(r.get("接待地点", ""))[:120],
                "survey_date": str(r.get("接待日期", ""))[:10],
                "notice_date": str(r.get("公告日期", ""))[:10],
                "period": period,
            })
        return rows
    except Exception as e:
        print(f"  [WARN] 机构调研获取失败({period}): {e}")
        return []


def fetch_industry_research(begin: str, end: str,
                            page_size: int = 100, max_pages: int = 5) -> list[dict]:
    """行业研报（东财研报中心 reportapi，qType=1），按发布日期区间 begin~end(YYYY-MM-DD)。"""
    import json as _json
    headers = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/report/"}
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        url = (
            "https://reportapi.eastmoney.com/report/list?"
            f"industryCode=*&pageSize={page_size}&industry=*&rating=&ratingChange=&"
            f"beginTime={begin}&endTime={end}&pageNo={page}&fields=&qType=1&"
            f"orgCode=&code=*&rcode=&p={page}&pageNum={page}"
        )
        try:
            req = urllib.request.Request(url, headers=headers)
            raw = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT).read().decode("utf-8")
            d = _json.loads(raw)
        except Exception as e:
            print(f"  [WARN] 行业研报获取失败(p{page}): {e}")
            break
        arr = d.get("data", []) or []
        for it in arr:
            info = str(it.get("infoCode", ""))
            out.append({
                "info_code": info,
                "title": str(it.get("title", "")),
                "org": str(it.get("orgSName", "") or it.get("orgName", "")),
                "industry": str(it.get("industryName", "")),
                "rating": str(it.get("emRatingName", "")),
                "publish_date": str(it.get("publishDate", ""))[:10],
                "url": f"https://data.eastmoney.com/report/info/{info}.html" if info else "",
            })
        total_page = int(d.get("TotalPage", 1) or 1)
        if page >= total_page:
            break
        time.sleep(0.3)
    return out


# ============================================================
# 理杏仁 API（基本面数据源）
# ============================================================

_LX_HEADERS = {
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip",
}


def _lixinger_post(endpoint: str, body: dict, timeout: int = 60) -> dict | None:
    """理杏仁 API POST 请求，返回 JSON 或 None。"""
    import json as _json, gzip as _gzip
    url = f"{LIXINGER_BASE}/{endpoint}"
    body["token"] = LIXINGER_TOKEN
    data = _json.dumps(body).encode("utf-8")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=_LX_HEADERS)
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw_bytes = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw_bytes = _gzip.decompress(raw_bytes)
            result = _json.loads(raw_bytes.decode("utf-8"))
            if result.get("code") == 1:
                return result
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  [WARN] Lixinger API 失败 ({endpoint}): {e}")
    return None


def _lx_extract(d: dict, path: list[str]) -> float | None:
    """从嵌套 dict 中按路径提取数值，如 _lx_extract(d, ['y','ps','wroe','t'])"""
    for key in path:
        if isinstance(d, dict):
            d = d.get(key)
        else:
            return None
    if d is None:
        return None
    try:
        v = float(d)
        return None if abs(v) > 1e15 else v
    except (ValueError, TypeError):
        return None


# 理杏仁指标 → (内部字段名, 是否需要*100转百分比)
_LX_METRICS_MAP = [
    ("y.ps.wroe.t", "roe", True),
    ("y.ps.gp_m.t", "gross_margin", True),
    ("y.ps.np_s_r.t", "net_margin", True),
    ("y.ps.op_s_r.t", "operating_margin", True),
    ("y.ps.toi.t_y2y", "revenue_yoy", True),
    ("y.ps.np.t_y2y", "profit_yoy", True),
    ("y.ps.beps.t", "eps", False),
    ("y.ps.d_np_r.t", "dividend_payout", True),
    ("y.bs.ta.t", "_ta", False),
    ("y.bs.tl.t", "_tl", False),
    ("y.m.ncffoa_np_r.t", "opcash_to_profit", True),
    ("y.m.ncffoa_ps.t", "opcash_per_share", False),
]


def fetch_financial_indicators_lixinger(codes: list[str]) -> dict[str, list[dict]]:
    """拉取财务指标（理杏仁 fs/non_financial，per-stock），返回 {code: [{...}, ...]}。

    该接口仅支持单股票查询，逐股调用，每股获取最近 6 份年报。
    """
    if not codes:
        return {}

    today = datetime.now().strftime("%Y-%m-%d")
    metrics = [m[0] for m in _LX_METRICS_MAP]
    key_map = {m[0]: (m[1], m[2]) for m in _LX_METRICS_MAP}

    def _path(m: str) -> list[str]:
        parts = m.split(".")
        return [parts[0], parts[1], parts[2], parts[3]]

    result: dict[str, list[dict]] = {}
    ok, fail = 0, 0

    for i, code in enumerate(codes):
        body = {
            "stockCodes": [code],
            "startDate": "2020-01-01",
            "endDate": today,
            "metricsList": metrics,
            "limit": 10,
        }
        resp = _lixinger_post("cn/company/fs/non_financial", body)
        if resp is None:
            fail += 1
            continue

        rows: list[dict] = []
        for item in resp.get("data", []) or []:
            std_date = str(item.get("standardDate", "") or "")[:10]
            if not std_date:
                continue

            row = {"code": code, "report_date": std_date}
            for m_name, (key_name, is_pct) in key_map.items():
                v = _lx_extract(item, _path(m_name))
                if v is not None and is_pct:
                    v = round(v * 100, 2)
                row[key_name] = v

            ta = row.pop("_ta", None)
            tl = row.pop("_tl", None)
            if ta and tl and ta > 0:
                row["debt_ratio"] = round(tl / ta * 100, 2)
            else:
                row["debt_ratio"] = None

            rows.append(row)

        rows.sort(key=lambda r: r["report_date"], reverse=True)
        if rows:
            result[code] = rows
            ok += 1
        else:
            fail += 1

        if (i + 1) % 20 == 0:
            print(f"  理杏仁财务指标: 已处理 {i+1}/{len(codes)}（成功 {ok} / 失败 {fail}）")
        time.sleep(0.3)

    if ok + fail > 0:
        print(f"  理杏仁财务指标: 完成 {len(codes)} 只（成功 {ok} / 失败 {fail}）")
    return result


def fetch_financial_indicators(code: str, start_year: str = "2020") -> list[dict]:
    """个股财务指标（理杏仁），兼容旧接口签名。

    内部调用批量接口，返回该股票的财务指标列表。
    """
    result = fetch_financial_indicators_lixinger([code])
    return result.get(code, [])


def fetch_stock_list_sina() -> list[dict]:
    """全 A 股列表（新浪），含行业分类。返回 [{code, name, board, industry}, ...]

    深交所股票从 stock_info_sz_name_code（含 CSRC 行业），
    上交所股票从 stock_info_sh_name_code（通过代码段推导行业大类）。
    """
    import akshare as ak

    rows: list[dict] = []

    # 深交所（含行业）
    df_sz = _ak(lambda: ak.stock_info_sz_name_code(symbol="A股列表"))
    if df_sz is not None and not df_sz.empty:
        for _, r in df_sz.iterrows():
            code = str(r.get("A股代码", "")).zfill(6)
            ind = str(r.get("所属行业", "")).strip()
            if not code or code == "0" or len(code) < 6:
                continue
            if len(ind) >= 3 and ind[1] == " ":
                ind = ind[2:]
            rows.append({
                "code": code,
                "name": str(r.get("A股简称", "")).replace(" ", ""),
                "board": str(r.get("板块", "")),
                "industry": ind or _code_industry_fallback(code),
            })

    # 上交所（无行业，用代码段推导）
    df_sh = _ak(lambda: ak.stock_info_sh_name_code(symbol="主板A股"))
    if df_sh is not None and not df_sh.empty:
        for _, r in df_sh.iterrows():
            code = str(r.get("证券代码", "")).zfill(6)
            if not code or code == "0" or len(code) < 6:
                continue
            rows.append({
                "code": code,
                "name": str(r.get("证券简称", "")).replace(" ", ""),
                "board": _sh_board(code),
                "industry": _code_industry_fallback(code),
            })

    return rows


def _sh_board(code: str) -> str:
    if code.startswith("688"):
        return "科创板"
    return "主板"


def _code_industry_fallback(code: str) -> str:
    """无法获取 CSRC 行业时，按代码段映射大类（仅用于分组计算分位）。"""
    seg = code[:3]
    if seg in ("688",):
        return "信息技术"
    if seg in ("600", "601", "603", "605"):
        return "沪市主板"
    if code.startswith("300"):
        return "创业板"
    if code.startswith("00"):
        return "深市主板"
    if code.startswith("8"):
        return "北交所"
    return "其他"


def fetch_bulk_pe_pb(codes: list[str]) -> dict[str, dict[str, float]]:
    """批量拉取 PE/PB，返回 {code: {pe_ttm, pb}}。自动分批+重试。"""
    result: dict[str, dict[str, float]] = {}
    total_batches = (len(codes) + 29) // 30
    for bi in range(0, len(codes), 30):
        batch = codes[bi:bi + 30]
        prefixed = [f"{_market_prefix(c)}{c}" for c in batch]
        ok = False
        for attempt in range(3):
            try:
                raw = tencent_quote_raw(prefixed)
                for c in batch:
                    key = f"{_market_prefix(c)}{c}"
                    if key in raw:
                        q = raw[key]
                        pe = float(q.get("pe_ttm", 0) or 0)
                        pb = float(q.get("pb", 0) or 0)
                        if pe > 0 or pb > 0:
                            result[c] = {"pe_ttm": pe, "pb": pb}
                ok = True
                break
            except Exception:
                time.sleep(0.5 * (attempt + 1))
        if not ok and bi // 30 % 20 == 0:
            print(f"  [WARN] 批次 {bi//30+1}/{total_batches} 3次重试均失败")
        time.sleep(0.35)
        if bi // 30 % 20 == 19:
            time.sleep(1.5)  # 每 20 批额外等待，防限流
    return result
