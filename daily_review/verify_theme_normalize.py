"""离线验证：§4 分级侧接入 normalize_theme（计数层归一·仅前向）。
stub 掉 store，不碰真实 SQLite、不联网。合成 hot_df 覆盖别名/停用词/板数碎片/同票多别名。"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import store
import engine

# --- stub store：analyze_themes 用到的全部读写都改内存/空操作 ---
_saved = {}
store.save_themes = lambda date, data: _saved.update({"themes": data})
store.save_theme_level = lambda *a, **k: None
store.get_recent_theme_dates = lambda n=5: []          # 无昨日 → 全部 new（模拟首跑/孤儿）
store.load_themes = lambda d: {}
store.get_theme_consecutive_days = lambda theme, date, **k: 1
store.get_theme_cumulative_stocks = lambda theme, **k: 0

TRADE_DATE = "2026-05-23"

# 合成热点：
#  A 同票多别名(算力+AI算力) → 只计 1 票, canonical=算力
#  B 别名(人形机器人→机器人) + 停用词(人工智能,应剔)
#  C 板数碎片(3连板,应剔) + 别名(芯片概念→半导体)
#  D 停用词(华为概念,应剔) 但保留 算力 → 算力共 A/D 两票
#  E 非别名非停用词(北京国资) → 原样保留(口径外，验证不会误剔)
hot_df = pd.DataFrame([
    {"代码": "300001", "名称": "甲", "涨幅%": 20.0, "题材归因": "算力+AI算力"},
    {"代码": "300002", "名称": "乙", "涨幅%": 19.9, "题材归因": "人形机器人+人工智能"},
    {"代码": "300003", "名称": "丙", "涨幅%": 10.0, "题材归因": "3连板+芯片概念"},
    {"代码": "688004", "名称": "丁", "涨幅%": 20.1, "题材归因": "华为概念+算力"},
    {"代码": "600005", "名称": "戊", "涨幅%": 9.9,  "题材归因": "北京国资"},
])

res = engine.analyze_themes(hot_df, TRADE_DATE)
raw_counts = res["raw_counts"]

print("=== analyze_themes raw_counts ===")
for k, v in sorted(raw_counts.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

checks = []
def chk(name, cond):
    checks.append((name, cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")

# 别名归一
chk("AI算力 已并入 算力（无独立 AI算力）", "AI算力" not in raw_counts and "算力" in raw_counts)
chk("人形机器人 → 机器人", "人形机器人" not in raw_counts and "机器人" in raw_counts)
chk("芯片概念 → 半导体", "芯片概念" not in raw_counts and "半导体" in raw_counts)
# 停用词剔除
chk("人工智能 已剔除", "人工智能" not in raw_counts)
chk("华为概念 已剔除", "华为概念" not in raw_counts)
# 板数碎片剔除
chk("3连板 已剔除", "3连板" not in raw_counts)
# 同票多别名只计一次：算力 = 甲(A) + 丁(D) = 2
chk("算力 = 2 票（同票 算力+AI算力 只计一次）", raw_counts.get("算力") == 2)
# 口径外词原样保留
chk("北京国资 原样保留（非别名非停用词）", raw_counts.get("北京国资") == 1)
# stocks 串与 count 一致
themes_saved = _saved["themes"]
chk("算力 stocks 去重为 300001,688004",
    themes_saved.get("算力", {}).get("stocks") == "300001,688004"
    and themes_saved.get("算力", {}).get("count") == 2)

# --- 明细字典 key 与 leveled 名对齐（核心：details.get(canonical) 不落空）---
details = engine.build_theme_stock_details(hot_df, res)
print("\n=== build_theme_stock_details keys ===")
print("  ", sorted(details.keys()))
chk("明细字典含 canonical 键 算力/机器人/半导体",
    {"算力", "机器人", "半导体"}.issubset(details.keys()))
chk("明细字典无 raw 键 AI算力/人形机器人/芯片概念/人工智能/华为概念/3连板",
    not ({"AI算力", "人形机器人", "芯片概念", "人工智能", "华为概念", "3连板"} & details.keys()))
chk("算力 明细 2 只（同票去重）", len(details.get("算力", [])) == 2)
# leveled 名能在 details 命中（模拟 report 的 details.get(t["theme"])）
hit = all(t["theme"] in details for t in res["leveled"]
          if t["theme"] in {"算力", "机器人", "半导体", "北京国资"})
chk("leveled 题名均能在 details 命中", hit)

print("\n" + ("✅ 全部通过" if all(c for _, c in checks) else "❌ 有失败项"))
sys.exit(0 if all(c for _, c in checks) else 1)
