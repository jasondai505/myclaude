"""§4 三池合并验证：真实 getharden + 人气前100 + 20日涨幅前100，端到端跑一遍。
不调用 analyze_themes，不写 SQLite —— leveled 由 hot_df 原始题材标签现造，仅验证合并/归一/补票/新方向。
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from collections import Counter

import data
import engine


def _fake_leveled(hot_df):
    """从 hot_df 原始题材归因造一份 leveled（不碰 store），用于测 attach 的 canonical 匹配。"""
    cnt = Counter()
    if hot_df is not None and not hot_df.empty:
        for _, row in hot_df.iterrows():
            for tag in str(row.get("题材归因", "")).split("+"):
                tag = tag.strip()
                if tag:
                    cnt[tag] += 1
    return [{"theme": t, "level": 2, "today_count": n, "consecutive_days": 1,
             "cumulative_stocks": n, "narrative": "Validation"}
            for t, n in cnt.most_common(40)]


def main():
    print("=" * 60)
    print("拉取池① 涨停/强势（getharden）...")
    hot_df = data.fetch_hot_themes()
    print(f"  池① {0 if hot_df is None else len(hot_df)} 只")

    print("拉取池② 人气前100（问财）...")
    pop = data.fetch_popularity_top100()
    print(f"  池② {len(pop)} 只")

    print("拉取池③ 20日涨幅前100（问财）...")
    gain = data.fetch_gainers_20d()
    print(f"  池③ {len(gain)} 只")

    merged = engine.build_merged_theme_pool(hot_df, pop, gain)
    meta, themes, freq, longtail = (
        merged["meta"], merged["themes"], merged["theme_freq"], merged["longtail"])

    # --- 不变量检查 ---
    assert all(freq[c] >= engine.MERGE_POOL_MIN_FREQ for c in themes), "themes 含 <min_freq"
    assert all(n < engine.MERGE_POOL_MIN_FREQ for _, n in longtail), "longtail 含 >=min_freq"
    assert all(len(m["concepts"]) <= engine.MERGE_POOL_MAX_CONCEPTS for m in meta.values()), "概念超上限"
    print("\n[不变量] themes>=3票 / longtail<3票 / 单票概念<=8 全部通过 ✓")

    print(f"\n合并后: 个股 {len(meta)} 只 / 成立题材 {len(themes)} 个 / 长尾 {len(longtail)} 个")

    print("\n--- 成立题材 TOP20（按持票数）---")
    for c, n in sorted(freq.items(), key=lambda x: -x[1]):
        if n < engine.MERGE_POOL_MIN_FREQ:
            break
        sample = themes[c][:4]
        names = "、".join(f"{m['name']}({''.join(m['sources'])})" for m in sample)
        print(f"  {c:<14} {n:>2}票  {names}")

    print("\n--- 长尾题材（<3票）前30 ---")
    print("  " + "、".join(f"{t}({n})" for t, n in longtail[:30]))

    # --- attach：用现造 leveled 测 canonical 匹配 + 补票 + 新方向 ---
    leveled = _fake_leveled(hot_df)
    tsd = engine.build_theme_stock_details(hot_df, {"leveled": leveled})
    before = {k: len(v) for k, v in tsd.items()}
    new_dirs = engine.attach_merged_to_themes(tsd, leveled, merged)

    grown = [(k, before.get(k, 0), len(v)) for k, v in tsd.items() if len(v) > before.get(k, 0)]
    print(f"\n--- attach 结果 ---")
    print(f"  补票的已知题材 {len(grown)} 个（按增量，前8）:")
    for k, b, a in sorted(grown, key=lambda x: -(x[2] - x[1]))[:8]:
        print(f"    {k:<14} {b} -> {a} 只")

    print(f"\n  新方向 {len(new_dirs)} 个（≥3票、不在涨停分级内，前10）:")
    for d in new_dirs[:10]:
        names = "、".join(f"{s['name']}({''.join(s['sources'])})" for s in d["stocks"][:4])
        print(f"    {d['theme']:<14} {d['freq']:>2}票  {names}")

    has_src = all(s.get("sources") for v in tsd.values() for s in v)
    print(f"\n[来源标记] 所有明细票均带 sources: {'✓' if has_src else '✗'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
