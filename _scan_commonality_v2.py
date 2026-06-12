"""共性扫描 v2.1 — 3日滚动 + 父子层级 + 多概念交集(Sheet1)
用法:
    python _scan_commonality_v2.py              # 今天的数据（自动缓存）
    python _scan_commonality_v2.py --date 2026-06-11  # 指定日期
    python _scan_commonality_v2.py --export          # 导出缓存汇总
"""
import sys, json, argparse
from pathlib import Path
from datetime import date, timedelta
from collections import Counter, defaultdict
from itertools import combinations

sys.path.insert(0, str(Path(__file__).parent / "daily_review"))

from config import (
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB, REDIS_MARKET_KEY,
    STOCK_PRIMARY_CONCEPT, CONCEPT_UNIVERSE, CONCEPT_HIERARCHY,
)
from data import _normalize_code, _stock_board, calc_limit_price
import redis

# ============================================================
# 缓存 & 数据路径
# ============================================================
CACHE_DIR = Path(__file__).parent / "daily_review" / "data" / "commonality_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MULTI_MAP_PATH = Path(__file__).parent / "daily_review" / "data" / "multi_concept_map.json"

# ============================================================
# 多概念数据（Sheet 1）
# ============================================================
_multi_data = None

def load_multi_map():
    global _multi_data
    if _multi_data is None and MULTI_MAP_PATH.exists():
        _multi_data = json.loads(MULTI_MAP_PATH.read_text(encoding="utf-8"))
    return _multi_data

def stock_multi_concepts(code: str) -> list[str]:
    """获取一只股票的所有非噪音概念（Sheet 1）"""
    m = load_multi_map()
    if m is None:
        return []
    return m["stocks"].get(code, [])

def multi_baseline_count(concept: str) -> int:
    """全市场多标签基线：有多少只股票含有此概念"""
    m = load_multi_map()
    if m is None:
        return 0
    return m["baseline"].get(concept, 0)

def multi_total_stocks() -> int:
    m = load_multi_map()
    return m["total_stocks"] if m else 0

# ============================================================
# Step 1: 数据获取
# ============================================================
def fetch_redis_all():
    r = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD,
        db=REDIS_DB, decode_responses=True, protocol=2,
        socket_connect_timeout=5, socket_timeout=10,
    )
    return r.hgetall(REDIS_MARKET_KEY)

def parse_row(code, csv_line):
    parts = csv_line.split(",")
    if len(parts) < 38:
        return None
    try:
        price = float(parts[1]) if parts[1] else 0
        prev_close = float(parts[2]) if parts[2] else 0
        name = parts[0].strip() if parts[0] else ""
        amount = float(parts[9]) if parts[9] else 0
        total_shares = float(parts[44]) if len(parts) > 44 and parts[44] else 0
    except (ValueError, IndexError):
        return None
    if price <= 0 or prev_close <= 0:
        return None

    code6 = _normalize_code(code)
    board = _stock_board(code6)
    change_pct = round((price - prev_close) / prev_close * 100, 2)
    is_st = "ST" in name.upper() or "*ST" in name.upper()
    limit_up, _ = calc_limit_price(prev_close, board, is_st)
    is_limit_up = price >= limit_up - 0.001
    mkt_cap = price * total_shares / 1e8 if total_shares > 0 else 0

    return {
        "code": code6, "name": name, "board": board,
        "price": price, "change_pct": change_pct,
        "amount": amount, "is_limit_up": is_limit_up,
        "is_st": is_st, "mkt_cap": mkt_cap,
    }

# ============================================================
# Step 2: 单日扫描（含多概念）
# ============================================================
def scan_day(raw_data):
    all_stocks = {}
    for code, csv_line in raw_data.items():
        row = parse_row(code, csv_line)
        if row:
            all_stocks[row["code"]] = row

    limit_up = {k: v for k, v in all_stocks.items() if v["is_limit_up"]}
    strong = {k: v for k, v in all_stocks.items() if v["change_pct"] >= 7 and not v["is_limit_up"]}
    pool = {**limit_up, **strong}

    # --- 单概念归因（Sheet 2, 第一顺位）---
    single_counts = Counter()
    single_stocks = defaultdict(list)
    for code, row in pool.items():
        c = STOCK_PRIMARY_CONCEPT.get(code, "--")
        single_counts[c] += 1
        single_stocks[c].append({"code": code, "name": row["name"],
                                  "chg": row["change_pct"], "amt": row["amount"],
                                  "mkt_cap": row["mkt_cap"], "board": row["board"]})

    # --- 多概念归因（Sheet 1, 全标签）---
    multi_counts = Counter()
    multi_stocks = defaultdict(list)
    for code, row in pool.items():
        for c in stock_multi_concepts(code):
            multi_counts[c] += 1
            multi_stocks[c].append({"code": code, "name": row["name"],
                                     "chg": row["change_pct"], "amt": row["amount"]})

    # --- 非逻辑因子 ---
    def tag_nl(r):
        tags = []
        if r["price"] < 10: tags.append("低价(<10)")
        if r["mkt_cap"] > 0 and r["mkt_cap"] < 30: tags.append("微盘(<30亿)")
        if r["board"] in ("cyb", "kcb"): tags.append("20cm")
        if r["is_st"]: tags.append("ST")
        return tags

    nl_counts = Counter()
    for code, row in pool.items():
        for t in tag_nl(row):
            nl_counts[t] += 1

    return {
        "date": date.today().isoformat(),
        "total_stocks": len(all_stocks),
        "limit_up_count": len(limit_up),
        "strong_count": len(strong),
        "pool_count": len(pool),
        "concept_counts": dict(single_counts),
        "concept_stocks": {k: v for k, v in single_stocks.items()},
        "multi_counts": dict(multi_counts),
        "multi_stocks": {k: v for k, v in multi_stocks.items()},
        "nl_counts": dict(nl_counts),
    }

# ============================================================
# Step 3: 缓存管理
# ============================================================
def cache_path(d: str) -> Path:
    return CACHE_DIR / f"scan_{d}.json"

def save_cache(day_data: dict):
    p = cache_path(day_data["date"])
    # 多概念数据量较大，精简缓存（只保留计数，不保留完整stocks list）
    slim = {k: v for k, v in day_data.items()
            if k not in ("concept_stocks", "multi_stocks")}
    # 但保留单概念的 stocks 列表用于后续分析
    p.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

def load_cache(d: str) -> dict | None:
    p = cache_path(d)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None

def load_window(window: int = 3):
    today = date.today()
    days = []
    d = today
    max_lookback = 30
    while len(days) < window and max_lookback > 0:
        ds = d.isoformat()
        if cache_path(ds).exists():
            days.append(load_cache(ds))
        d -= timedelta(days=1)
        max_lookback -= 1
    return days

# ============================================================
# Step 4: 概念层级聚合
# ============================================================
def resolve_parent(concept: str) -> str:
    return CONCEPT_HIERARCHY.get(concept, concept)

def aggregate_hierarchy(day_data_list: list[dict]):
    merged_concept_counts = Counter()
    merged_concept_stocks = defaultdict(list)
    seen_pairs = set()

    for day_data in day_data_list:
        dd = day_data.get("date", "?")
        for concept, stocks in day_data.get("concept_stocks", {}).items():
            for s in stocks:
                key = (dd, s["code"])
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    merged_concept_counts[concept] += 1
                    merged_concept_stocks[concept].append({**s, "date": dd})

    parent_counts = Counter()
    parent_stocks = defaultdict(list)
    for concept, count in merged_concept_counts.items():
        parent = resolve_parent(concept)
        parent_counts[parent] += count
        parent_stocks[parent].extend(merged_concept_stocks[concept])

    parent_children = defaultdict(lambda: Counter())
    for concept, count in merged_concept_counts.items():
        parent = resolve_parent(concept)
        parent_children[parent][concept] += count

    return {
        "merged_concept_counts": merged_concept_counts,
        "merged_concept_stocks": merged_concept_stocks,
        "parent_counts": parent_counts,
        "parent_stocks": parent_stocks,
        "parent_children": parent_children,
    }

# ============================================================
# Step 5: 多概念交集分析
# ============================================================
def multi_concept_analysis(day_data_list: list[dict], avg_pool: float):
    """从多日数据中做多概念交集分析"""
    # 合并多日多概念计数
    merged_multi = Counter()
    merged_multi_stocks = defaultdict(list)
    seen_pairs = set()

    for day_data in day_data_list:
        dd = day_data.get("date", "?")
        for concept, stocks in day_data.get("multi_stocks", {}).items():
            for s in stocks:
                key = (dd, s["code"])
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    merged_multi[concept] += 1
                    merged_multi_stocks[concept].append({**s, "date": dd})

    total_mkt = multi_total_stocks()
    if total_mkt == 0:
        return []

    results = []
    for concept, count in merged_multi.most_common(60):
        base = multi_baseline_count(concept)
        if base < 3:
            continue
        # 多标签ER
        er_multi = (count / avg_pool) / (base / total_mkt) if avg_pool > 0 else 0
        if er_multi < 2.0:
            continue

        # 对比单概念：此概念在单概念归因中是否已经显著？
        single_count = sum(
            d.get("concept_counts", {}).get(concept, 0)
            for d in day_data_list
        )

        parent = resolve_parent(concept)
        # 单概念覆盖率
        single_base = CONCEPT_UNIVERSE.get(concept, 0)
        er_single = (single_count / avg_pool) / single_base if single_base > 0 and avg_pool > 0 else 0

        # 交叉概念：和这些股票最常共现的其他概念
        stock_codes = {s["code"] for s in merged_multi_stocks[concept]}
        co_concepts = Counter()
        for code in stock_codes:
            for c in stock_multi_concepts(code):
                if c != concept and c in merged_multi:
                    co_concepts[c] += 1
        top_co = [(c, n) for c, n in co_concepts.most_common(5) if n >= 2]

        # 判断：是否单概念已覆盖
        if single_count >= 2:
            tag = "已覆盖"
        elif single_count == 1:
            tag = "单概念薄弱"
        else:
            tag = "仅多标签可见"

        results.append({
            "concept": concept, "count": count, "er_multi": er_multi,
            "single_count": single_count, "er_single": er_single,
            "parent": parent, "co_concepts": top_co, "tag": tag,
        })

    return results

def multi_cooccurrence(days_data: list[dict], avg_pool: float):
    """找出强势池中最常共现的概念对（Jaccard 交集）"""
    # 收集强势池中所有股票的代码及其多概念
    seen = set()
    stock_pair_data = {}  # code -> set of concepts

    for day_data in days_data:
        for concept, stocks in day_data.get("multi_stocks", {}).items():
            for s in stocks:
                code = s["code"]
                if code not in seen:
                    seen.add(code)
                    stock_pair_data[code] = set(stock_multi_concepts(code))

    # 统计概念共现
    pair_counter = Counter()
    concept_set = set()
    for code, concepts in stock_pair_data.items():
        concept_set.update(concepts)
        for c1, c2 in combinations(sorted(concepts), 2):
            pair_counter[(c1, c2)] += 1

    # 筛选显著共现对：共现次数 >= 3，且 Jaccard > 期望
    total_mkt = multi_total_stocks()
    results = []
    for (c1, c2), co_count in pair_counter.most_common(50):
        if co_count < 3:
            continue
        # 全市场期望共现
        b1 = multi_baseline_count(c1) / total_mkt
        b2 = multi_baseline_count(c2) / total_mkt
        expected = b1 * b2 * len(stock_pair_data)
        if expected > 0 and co_count / expected > 2.0:
            results.append({
                "c1": c1, "c2": c2, "co_count": co_count,
                "ratio": co_count / expected,
            })

    return results[:20]

# ============================================================
# Step 6: 输出
# ============================================================
def format_report(days_data: list[dict], output_path: Path):
    L = []
    def w(s=""): L.append(s)

    w("=" * 70)
    w(f"  Commonality Scan v2.1 — {len(days_data)}日滚动窗口")
    w(f"  日期: {', '.join(d.get('date','?') for d in days_data)}")
    w("=" * 70)

    agg = aggregate_hierarchy(days_data)
    avg_pool = sum(d.get("pool_count", 0) for d in days_data) / max(len(days_data), 1)
    daily_stocks = sum(d.get("total_stocks", 0) for d in days_data) / max(len(days_data), 1)

    w(f"\n日均强势池: {avg_pool:.0f} 只  |  全市场: ~{daily_stocks:.0f} 只")
    w(f"涨停累计: {sum(d.get('limit_up_count',0) for d in days_data)} 只")
    w(f"归因模式: 单概念(Sheet2第一顺位) + 多概念交集(Sheet1全标签)")

    # ========================
    # 一、单概念父级聚合
    # ========================
    er_list = []
    for parent, count in agg["parent_counts"].most_common(25):
        if parent == "--":
            continue
        children = agg["parent_children"][parent]
        base_sum = sum(CONCEPT_UNIVERSE.get(c, 0) for c in children)
        er = (count / avg_pool) / base_sum if base_sum > 0 and avg_pool > 0 else 0
        children_detail = [(c, cnt) for c, cnt in children.most_common(6)]
        er_list.append({"parent": parent, "count": count, "er": er, "children": children_detail})

    w("\n" + "-" * 70)
    w("  一、一级主题（单概念归因 · 父级聚合）")
    w("-" * 70)
    w(f"  {'主题':<16s} {'出现次':>5s} {'富集比':>8s}  |  细分（按贡献排序）")
    w("  " + "-" * 66)
    for item in er_list:
        cs = " > ".join(f"{c}({n})" for c, n in item["children"][:5])
        w(f"  {item['parent']:<16s} {item['count']:>5d} {item['er']:>7.1f}x  |  {cs}")

    # ========================
    # 二、多概念交集
    # ========================
    multi_results = multi_concept_analysis(days_data, avg_pool)

    w("\n" + "-" * 70)
    w("  二、多概念交集分析（Sheet1全标签 · 发现隐藏共性）")
    w("-" * 70)
    w(f"  {'概念':<22s} {'多标签':>5s} {'ER多':>7s} {'单概念':>5s} {'ER单':>7s} {'判定':<12s}  |  常共现概念")
    w("  " + "-" * 78)

    for r in multi_results[:30]:
        co = " · ".join(f"{c}({n})" for c, n in r["co_concepts"][:3])
        if not co:
            co = "—"
        w(f"  {r['concept']:<22s} {r['count']:>5d} {r['er_multi']:>7.1f}x "
          f"{r['single_count']:>5d} {r['er_single']:>7.1f}x {r['tag']:<12s}  |  {co}")

    # 标注：哪些是仅多标签发现的
    new_finds = [r for r in multi_results if r["tag"] in ("仅多标签可见", "单概念薄弱")]
    if new_finds:
        w(f"\n  >>> 多概念交集新发现（单概念未覆盖或薄弱）:")
        for r in new_finds:
            co = " · ".join(f"{c}({n})" for c, n in r["co_concepts"][:3])
            w(f"      [{r['parent']}] {r['concept']} — 常与 {co or '—'} 共现")

    # ========================
    # 三、概念共现对
    # ========================
    co_pairs = multi_cooccurrence(days_data, avg_pool)
    if co_pairs:
        w("\n" + "-" * 70)
        w("  三、强势池概念共现对（≥3次共现 · 超期望2x）")
        w("-" * 70)
        for r in co_pairs:
            w(f"  {r['c1']} × {r['c2']}  —  共现{r['co_count']}次  (超期望{r['ratio']:.1f}x)")

    # ========================
    # 四、细分概念（单概念ER显著的）
    # ========================
    w("\n" + "-" * 70)
    w("  四、细分概念富集比（单概念 ER>2.0）")
    w("-" * 70)
    for concept, count in agg["merged_concept_counts"].most_common(50):
        if concept == "--":
            continue
        base_rate = CONCEPT_UNIVERSE.get(concept, 0)
        if base_rate > 0:
            er = (count / avg_pool) / base_rate if avg_pool > 0 else 0
            if er > 2.0:
                parent = resolve_parent(concept)
                w(f"  {concept:<24s} {count:>5d} {base_rate*100:>7.2f}% {er:>8.1f}x  [{parent}]")

    # ========================
    # 五、个股异动
    # ========================
    w("\n" + "-" * 70)
    w("  五、个股异动（孤立走强，无同概念同伴）")
    w("-" * 70)
    orphans = []
    for concept, stocks in agg["merged_concept_stocks"].items():
        if len(stocks) == 1 and concept != "--":
            orphans.append((concept, stocks[0]))
    unmapped = agg["merged_concept_stocks"].get("--", [])
    for s in unmapped:
        if not s["code"].startswith(("118", "123")) and s["amt"] > 1e8:
            # 用多概念补充标注
            mc = stock_multi_concepts(s["code"])
            orphans.append((f"未映射→{'+'.join(mc[:3])}" if mc else "未映射", s))

    if orphans:
        w(f"  {'概念':<34s} {'代码':<8s} {'名称':<10s} {'涨幅':>7s} {'成交额(亿)':>10s}")
        w("  " + "-" * 74)
        for concept, s in sorted(orphans, key=lambda x: -x[1]["amt"]):
            amt_e = s["amt"] / 1e8
            w(f"  {concept:<34s} {s['code']:<8s} {s['name']:<10s} {s['chg']:>6.1f}% {amt_e:>10.1f}")
    else:
        w("  (无)")

    # ========================
    # 六、非逻辑因子
    # ========================
    w("\n" + "-" * 70)
    w("  六、非逻辑因子")
    w("-" * 70)
    merged_nl = Counter()
    for d in days_data:
        for tag, cnt in d.get("nl_counts", {}).items():
            merged_nl[tag] += cnt
    for tag, cnt in merged_nl.most_common():
        share = cnt / avg_pool * 100 if avg_pool > 0 else 0
        w(f"  {tag:<20s} {cnt:>5d}  ({share:.0f}% of pool)")

    output_path.write_text("\n".join(L), encoding="utf-8")
    return output_path

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", "-d", type=str)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--window", "-w", type=int, default=3)
    args = parser.parse_args()

    today_str = args.date or date.today().isoformat()
    window = args.window

    if not args.export:
        raw = fetch_redis_all()
        if raw:
            day_data = scan_day(raw)
            day_data["date"] = today_str
            save_cache(day_data)
            print(f"[{today_str}] 涨停:{day_data['limit_up_count']}  >7%:{day_data['strong_count']}  池:{day_data['pool_count']}  "
                  f"多概念标签数:{sum(day_data['multi_counts'].values())}")
        else:
            print("[ERROR] Redis empty")

    days_data = load_window(window)
    if not days_data:
        days_data = load_window(1)

    if days_data:
        # 重新完整扫描当天（含多概念详情，不依赖缓存）
        if not args.export:
            raw2 = fetch_redis_all()
            if raw2:
                day_full = scan_day(raw2)
                day_full["date"] = today_str
                # 替换缓存中的精简版
                for i, d in enumerate(days_data):
                    if d.get("date") == today_str:
                        days_data[i] = day_full
                        break
                else:
                    days_data.append(day_full)

        out = Path(__file__).parent / f"_commonality_report_{today_str}.txt"
        format_report(days_data, out)
        print(f"Done. Report: {out}")
    else:
        print("No data")
