# 复盘仪表盘

> Dataview 插件驱动，自动汇总全时段报告（晨间情报 → 盘中验证 → 盘后复盘）

## 晨间情报 — 催化事件与标的假设

```dataview
TABLE events_count AS "事件数", stocks_count AS "标的数", summary AS "主题摘要"
FROM "morning_intel/reports"
WHERE type = "晨间情报"
SORT date DESC
LIMIT 5
```

## 盘中验证 — 假设命中率

```dataview
TABLE total AS "标的数", hit AS "命中", miss AS "背离", pending AS "待定", hit_rate AS "命中率%"
FROM "morning_intel/reports"
WHERE type = "盘中验证"
SORT date DESC
LIMIT 5
```

## 今日交易流水线

- 晨间情报: `morning_intel/reports/morning_{{date}}.md`
- 盘中验证: `morning_intel/reports/validation_{{date}}.md`
- 盘后复盘: `daily_review/reports/review_{{date}}.md`

---

## 近期复盘总览

```dataview
TABLE sentiment AS "情绪", amount_yi AS "成交(亿)", limit_up AS "涨停", northbound AS "北向(亿)", nvda AS "NVDA"
FROM ""
WHERE type = "每日复盘"
SORT date DESC
LIMIT 10
```

## 主线题材追踪

```dataview
TABLE mainline AS "主线/加速题材", emerging AS "新兴题材", fading AS "退潮题材"
FROM ""
WHERE type = "每日复盘"
SORT date DESC
LIMIT 10
```

## FEV高分标的

```dataview
TABLE fev_top AS "FEV Top5"
FROM ""
WHERE type = "每日复盘"
SORT date DESC
LIMIT 10
```

## 筛选：情绪偏多的交易日

```dataview
TABLE sentiment AS "情绪", amount_yi AS "成交(亿)", limit_up AS "涨停", northbound AS "北向"
FROM ""
WHERE sentiment = "偏多" OR sentiment = "强势"
SORT date DESC
```

## 筛选：北向大幅流入（>30亿）

```dataview
TABLE sentiment AS "情绪", northbound AS "北向(亿)"
FROM ""
WHERE northbound > 30
SORT date DESC
```

## 筛选：NVDA大跌日（关注A股联动）

```dataview
TABLE sentiment AS "情绪", nvda AS "NVDA", amount_yi AS "成交"
FROM ""
WHERE contains(nvda, "-")
SORT date DESC
```
