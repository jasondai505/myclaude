# 操作备忘录

工作笔记，AI 做相关任务时可 Read 参考。不自动加载。

## 聚焦池批量拉取三板斧

`run.py:_parallel_stock_fetch()` 替代逐只 sleep 循环：

| 环节 | 标的范围 | 并发数 | 条件 |
|------|---------|:---:|------|
| 研报 | 人气≤50 或 涨停标的 | 5 | `high_priority = lambda s: s.get("hot_rank", 0) <= 50 or "zt" in s.get("source", [])` |
| 盈利预测 | 同上，排除自选股（已拉过） | 5 | 同 `high_priority` |
| 互动+新闻 | 聚焦池全部标的 | 8 | 深交所 `fetch_irm_szse` / 上交所 `fetch_irm_sse` |

执行时 stdout 重定向到 `os.devnull` 压制线程噪音，完成后输出 `✓ X/Y 只有数据`。

## 主题归因数据源

**plate0611.xlsx**：
- Sheet 1 (5430行)：每只票的**全部**概念标签（`-` 分隔）
- Sheet 2 (10489行)：每只票的**第一、二顺位**概念（rank 1/2），5359 只票，369 个独立概念

**config.py 中的映射**：
- `CONCEPT_UNIVERSE`：349 个标准概念 → 全市场覆盖率（rank-1 only）
- `STOCK_PRIMARY_CONCEPT`：5359 只股票 → 第一顺位概念
- `STOCK_SECONDARY_CONCEPT`：4713 只股票 → 第二顺位概念

**更新方式**：重新导出 plate 的 Excel → 跑脚本生成 dict → 替换 config.py 中的对应段。

## 主因归因匹配策略

`engine_themes._pick_primary_tag()` 优先级：
1. Sheet 2 rank-1 精确匹配 → 直接返回
2. Sheet 2 rank-1 子串匹配 → 选名称最长的（六氟化钨 > 氟化工）
3. 尝试 rank-2（同上）
4. Fallback：不在 CONCEPT_UNIVERSE 中 (+200) + 名称长度 + 非噪音 (+50) → 选最高分

## 报告精简记录

- 题材个股表：移除了 F/E/V 列（`report_utils._render_theme_block`），FEV 评分只在聚焦池展示
- 自选股章节：「自选股扫描」→「自选股关键信号」，删完整 FEV 表格，仅保留 ≥20 分重点 + 大跌警示 + 技术信号
- 聚焦池章节：移除「自选股状态」小节（与自选股关键信号重复）

## 并行拉取

`run._fetch_market_data`：5 源 ThreadPoolExecutor 并行（指数/行业/强势股/北向/外围）。

## 踩坑记录

- `_parallel_stock_fetch` 中用 `os.devnull` 需确保文件顶部有 `import os`（2026-06-12 崩溃修复）
- 并行线程中的 tqdm 进度条会产生 ANSI 乱码，必须重定向 stdout
- `_pick_primary_tag` 非 CONCEPT_UNIVERSE 标签评分最初 +100（优先标准概念），修正为 +200（优先小众品种标签），因具体品种比泛概念更有信息量
