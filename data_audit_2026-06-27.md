# 运行时数据库资产盘点 2026-06-27

## 总览

| 数据库 | 大小 | 表数 | 总行数 |
|--------|------|:--:|:-----:|
| review.db | 299M | 35 | ~135K |
| serenity.db | 2.2M | 6 | ~6.4K |
| gfactor.db | 624K | 1 | 1.5K |
| llm_cache.db | 不存在 | — | — |
| supply_chain.db | 152K | 5 | ~637 |
| ocr_tracker.db | 232K | 1 | 61 |
| **合计** | **302M** | **48** | **~144K** |

## review.db — 核心库 (299M/35表)

| 表 | 行数 | 用途 |
|------|:--:|------|
| announcements | 46,231 | 公告采集 |
| zsxq_topics | 31,781 | 知识星球帖子 |
| theme_daily | 8,311 | 每日题材数据 |
| stock_news | 8,121 | 个股新闻 |
| valuation_cache | 7,633 | 估值缓存 |
| marginal_changes | 5,833 | 边际变化 |
| deep_read_queue | 4,420 | 公告深研队列 |
| deep_read_results | 4,409 | 公告深研结果 |
| sector_rotation_log | 3,979 | 板块轮动日志 |
| catalyst_signals | 3,872 | 催化信号 |
| research_reports | 3,102 | 研报 |
| interactions | 2,892 | 互动易 |
| financial_indicators | 2,189 | 财务指标 |
| industry_reports | 2,089 | 行业研报 |
| limit_up_analysis | 2,018 | 涨停分析 |
| industry_research | 1,309 | 行业研报索引 |
| eps_forecast | 897 | EPS一致预期 |
| catalyst_stock_map | 622 | 催化→标的映射 |
| theme_level | 563 | 主题强度 |
| wechat_articles | 440 | 公众号文章 |
| feed_cache | 374 | Feed缓存 |
| emerging_dragon_log | 357 | 潜龙日志 |
| lockups | 319 | 限售解禁 |
| inst_survey | 128 | 机构调研 |
| consensus_snapshot | 118 | 一致预期快照 |
| selection_history | 47 | 精选历史 |
| market_snapshot | 35 | 市场快照 |
| scan_results | 30 | 扫描结果 |
| collect_status | 22 | 采集状态 |
| 其余小表 | <20 | 业绩预告/express等 |

## serenity.db — 产业链卡脖子 (2.2M/6表)

| 表 | 行数 | 用途 |
|------|:--:|------|
| feval_scores | 4,250 | FEV 评分历史 |
| stock_chokepoint | 1,097 | 链→标的映射 |
| chain_snapshot | 540 | 链段级卡脖子评分 |
| stock_delta | 369 | Δ 边际评分 |
| analysis_log | 184 | 分析运行日志 |

## gfactor.db (624K/1表)

| 表 | 行数 | 用途 |
|------|:--:|------|
| gfactor_scores | 1,546 | G-Factor 四维评分 |

## supply_chain.db — 晨间供应链情报 (152K/5表)

| 表 | 行数 | 用途 |
|------|:--:|------|
| validation_log | 561 | 验证日志 |
| supply_chain_nodes | 35 | 供应链节点→标的 |
| theme_tracker | 32 | 主题追踪 |
| supply_chain_events | 5 | 供应链事件 |

## ocr_tracker.db (232K/1表)

| 表 | 行数 | 用途 |
|------|:--:|------|
| ocr_tracker | 61 | Serenity截图OCR追踪 |

## 注意事项

- **llm_cache.db 不存在** — LLM调用无本地缓存，每次走API全额计费
- **review.db 299M** 最大，announcements(46K) + zsxq_topics(32K) 占大头
- 数据库总计 302M，48 张表，~144K 行
