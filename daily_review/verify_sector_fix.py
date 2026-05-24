"""验证 sector 兜底改动对自选股的影响

旧逻辑: code_themes 仅来自当天 hot_df 题材归因 —> 不在强势股的自选股 sector=0
新逻辑: 回落到 code_to_themes (历史题材池反查) —> 历史属于过 3级+题材的拿分
"""
import sys
from pathlib import Path
from datetime import date as _date

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from config import WATCHLIST, STRENGTH_POOL_LOOKBACK
import store


def sector_score(level: int, trend: str = "") -> int:
    if level >= 3 and trend in ("验证", "形成"):
        s = 20
    elif level >= 3:
        s = 12
    elif level == 2:
        s = 6
    else:
        s = 0
    if trend == "动摇":
        s = max(s - 10, 0)
    return s


def main():
    trade_date = _date.today().strftime("%Y-%m-%d")
    print(f"# 自选股 sector 兜底效果验证")
    print(f"# 自选股: {len(WATCHLIST)} 只 | 回溯: {STRENGTH_POOL_LOOKBACK} 天\n")

    theme_levels = store.load_theme_levels()
    print(f"题材级别库: {len(theme_levels)} 个题材")

    theme_pool = store.get_theme_stock_pool(trade_date, STRENGTH_POOL_LOOKBACK)
    code_to_themes = store.build_code_to_themes(theme_pool)
    print(f"历史题材池: {len(code_to_themes)} 只标的有题材归因\n")

    rows = []
    for code in WATCHLIST:
        themes = code_to_themes.get(code, [])
        best_level = 0
        best_theme = ""
        for t in themes:
            lv = theme_levels.get(t, {}).get("level", 0)
            if lv > best_level:
                best_level = lv
                best_theme = t
        score_new = sector_score(best_level)
        rows.append({
            "code": code, "themes": themes, "best_theme": best_theme,
            "best_level": best_level, "old_sector": 0, "new_sector": score_new,
            "delta": score_new - 0,
        })

    rows.sort(key=lambda x: -x["new_sector"])

    print(f"{'代码':<10}{'最高题材':<20}{'级别':<6}{'旧':<6}{'新':<6}{'变化':<8}")
    print("-" * 70)
    by_level = {}
    for r in rows:
        by_level[r["best_level"]] = by_level.get(r["best_level"], 0) + 1
        if r["new_sector"] > 0:
            print(f"{r['code']:<10}{r['best_theme'][:18]:<20}{r['best_level']:<6}"
                  f"{r['old_sector']:<6}{r['new_sector']:<6}+{r['delta']}")

    print("\n## 受益统计")
    benefit = sum(1 for r in rows if r["delta"] > 0)
    no_change = sum(1 for r in rows if r["delta"] == 0)
    print(f"  得到 sector 加分: {benefit} 只 ({benefit/len(WATCHLIST)*100:.0f}%)")
    print(f"  无变化(仍为0): {no_change} 只")

    print("\n## 加分档位分布")
    for delta_v in [20, 12, 6]:
        cnt = sum(1 for r in rows if r["delta"] == delta_v)
        if cnt:
            print(f"  +{delta_v} 分: {cnt} 只")

    print("\n## 题材级别命中分布")
    for lv in sorted(by_level.keys(), reverse=True):
        if by_level[lv]:
            print(f"  level {lv}: {by_level[lv]} 只")


if __name__ == "__main__":
    main()
