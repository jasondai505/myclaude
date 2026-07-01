# 数据源采集索引

> 更新于 2026-07-01 07:44

## 状态总览

| 数据源 | 最新到 | 上次跑 | 状态 | 7日条数 | 备注 |
|--------|--------|---------|------|---------|------|
| 知识星球 | 2026-06-30 | 2026-06-30 23:57 | ✅ ok | 1223 | sync 新增 54 条 |
| 公告 | 2026-06-30 | 2026-06-30 22:00 | ✅ ok | 8739 | 成功5/5天 |
| 公告深度研读 | 2026-06-30 | 2026-06-30 23:00 | · timeout | 494 | 超时(3600s) |
| 个股新闻 | 2026-06-30 | 2026-06-30 23:33 | ✅ ok | 2726 | 135/135 只成功，新增 59 |
| 新闻边际信号 | 2026-06-30 | 2026-06-30 23:50 | ✅ ok | 2726 | 7天共112条 | 2条边际信号/9条新闻; 19条边际信号/254条新闻; 29条边际信号/430条新闻 |
| 个股研报 | 2026-06-30 | 2026-06-30 22:00 | ✅ ok | 99 | 全市场 98 篇，新增 None |
| 研报深度跟踪 | 2026-06-30 | 2026-06-30 23:07 | ✅ ok | 494 | 7天: 46只有信号, 42只LLM, 46份档案 |
| 互动易 | 2026-06-30 | 2026-06-30 23:32 | ❌ error | 1206 | database is locked |
| 业绩预告快报 | 2026-06-30 | 2026-06-30 23:08 | ✅ ok | 2 | 预告2+快报0，新增0 |
| 机构调研 | 2026-06-30 | 2026-06-30 23:52 | ✅ ok | 11 | 命中0，新增0 |
| 调研+互动情绪 | 2026-06-30 | 2026-06-30 23:48 | ❌ error | 11 | database is locked |
| 限售解禁 | 2026-06-30 | 2026-06-30 19:25 | ✅ ok | 344 | 135只，命中73，新增11 |
| 一致预期EPS | 2026-06-30 | 2026-06-30 19:24 | ✅ ok | 663 | 135只，命中282 |
| 行业研报 | 2026-06-30 | 2026-06-30 23:08 | ✅ ok | 425 | 行业/策略/宏观 328 篇，新增 328 |
| 行业深度分析 | 2026-06-30 | 2026-06-30 23:16 | ✅ ok | 722 | 7个报告日: S1=160→S2=117→存档7 | (2026-06-28) S1=26→S2=18→合成; (2026-06-29) S1=26→S2=20→合成; (2026-06-30) S1=26→S2=18→合成 |
| 催化走势跟踪 | 2026-07-01 | 2026-07-01 07:32 | ✅ ok | 1024 | 确认10条催化（3条历史复活） |
| 共性扫描 | 2026-07-01 | 2026-07-01 07:32 | ✅ ok | 1024 | 强势池480只(涨停157) · 多概念标签5685个 · sector_log +62概念 |
| 个股档案构建 | 2026-06-30 | 2026-06-30 19:33 | ✅ ok | 494 | 优先池22只 → 聚合8维 → LLM合成22/22份档案 |
| 财务指标 | 2026-06-30 | 2026-06-30 19:38 | ✅ ok | 1016 | 成功130/失败5，新增92 |
| 微信公众号 | 2026-07-01 | 2026-07-01 06:22 | ✅ ok | 58 | 拉取 7 篇，新增 7，全文 3/7 |
| 韭研脱水研报 | 2026-06-30 | 2026-06-30 23:32 | ✅ ok | 0 | PDF 采集 2 份 |
| 唐史主任微博 | 2026-06-30 | 2026-06-30 23:54 | ❌ error | 0 | no posts |

## 今日 (2026-07-01) 各源报告

_今日暂无数据_

## 最近 7 天报告

### 📄 公告

- **公告**: [2026-06-30](feeds/announcements/announcements_2026-06-30.md) · [2026-06-29](feeds/announcements/announcements_2026-06-29.md) · [2026-06-26](feeds/announcements/announcements_2026-06-26.md) · [2026-06-25](feeds/announcements/announcements_2026-06-25.md)
- **公告深度研读**: _—

### 📊 研报

- **个股研报**: [2026-06-30](feeds/research/research_2026-06-30.md) · [2026-06-29](feeds/research/research_2026-06-29.md) · [2026-06-28](feeds/research/research_2026-06-28.md) · [2026-06-27](feeds/research/research_2026-06-27.md) · [2026-06-26](feeds/research/research_2026-06-26.md) · [2026-06-25](feeds/research/research_2026-06-25.md)
- **研报深度跟踪**: _—

### 🔍 调研+互动

- **机构调研**: [2026-06-30](feeds/surveys/surveys_2026-06-30.md) · [2026-06-29](feeds/surveys/surveys_2026-06-29.md) · [2026-06-28](feeds/surveys/surveys_2026-06-28.md) · [2026-06-27](feeds/surveys/surveys_2026-06-27.md) · [2026-06-26](feeds/surveys/surveys_2026-06-26.md) · [2026-06-25](feeds/surveys/surveys_2026-06-25.md)
- **调研+互动情绪**: _—
- **互动易**: [2026-06-30](feeds/interactions/interactions_2026-06-30.md) · [2026-06-29](feeds/interactions/interactions_2026-06-29.md) · [2026-06-28](feeds/interactions/interactions_2026-06-28.md) · [2026-06-27](feeds/interactions/interactions_2026-06-27.md) · [2026-06-26](feeds/interactions/interactions_2026-06-26.md) · [2026-06-25](feeds/interactions/interactions_2026-06-25.md)

### 📈 业绩+新闻

- **业绩预告快报**: [2026-06-30](feeds/earnings/earnings_2026-06-30.md) · [2026-06-29](feeds/earnings/earnings_2026-06-29.md) · [2026-06-28](feeds/earnings/earnings_2026-06-28.md) · [2026-06-27](feeds/earnings/earnings_2026-06-27.md) · [2026-06-26](feeds/earnings/earnings_2026-06-26.md) · [2026-06-25](feeds/earnings/earnings_2026-06-25.md)
- **个股新闻**: [2026-06-30](feeds/news/news_2026-06-30.md) · [2026-06-29](feeds/news/news_2026-06-29.md) · [2026-06-28](feeds/news/news_2026-06-28.md) · [2026-06-27](feeds/news/news_2026-06-27.md) · [2026-06-26](feeds/news/news_2026-06-26.md) · [2026-06-25](feeds/news/news_2026-06-25.md)
- **新闻边际信号**: [2026-06-30](feeds/news_signals/news_signals_2026-06-30.md) · [2026-06-29](feeds/news_signals/news_signals_2026-06-29.md) · [2026-06-28](feeds/news_signals/news_signals_2026-06-28.md) · [2026-06-27](feeds/news_signals/news_signals_2026-06-27.md) · [2026-06-26](feeds/news_signals/news_signals_2026-06-26.md) · [2026-06-25](feeds/news_signals/news_signals_2026-06-25.md)

### 🏭 行业

- **行业研报**: [2026-06-30](feeds/industry/industry_2026-06-30.md) · [2026-06-29](feeds/industry/industry_2026-06-29.md) · [2026-06-28](feeds/industry/industry_2026-06-28.md) · [2026-06-27](feeds/industry/industry_2026-06-27.md) · [2026-06-26](feeds/industry/industry_2026-06-26.md) · [2026-06-25](feeds/industry/industry_2026-06-25.md)
- **行业深度分析**: _—

### 💬 社交信息源

- **微信公众号**: [2026-06-30](feeds/wechat/wechat_2026-06-30.md) · [2026-06-29](feeds/wechat/wechat_2026-06-29.md) · [2026-06-28](feeds/wechat/wechat_2026-06-28.md) · [2026-06-27](feeds/wechat/wechat_2026-06-27.md) · [2026-06-26](feeds/wechat/wechat_2026-06-26.md) · [2026-06-25](feeds/wechat/wechat_2026-06-25.md)
- **唐史主任微博**: _—
- **知识星球**: [2026-06-30](feeds/zsxq/zsxq_2026-06-30.md) · [2026-06-29](feeds/zsxq/zsxq_2026-06-29.md) · [2026-06-28](feeds/zsxq/zsxq_2026-06-28.md) · [2026-06-27](feeds/zsxq/zsxq_2026-06-27.md) · [2026-06-26](feeds/zsxq/zsxq_2026-06-26.md) · [2026-06-25](feeds/zsxq/zsxq_2026-06-25.md)
- **韭研脱水研报**: [2026-06-30](feeds/jiuyang/jiuyang_2026-06-30.md) · [2026-06-29](feeds/jiuyang/jiuyang_2026-06-29.md) · [2026-06-28](feeds/jiuyang/jiuyang_2026-06-28.md) · [2026-06-25](feeds/jiuyang/jiuyang_2026-06-25.md)

### 📡 跟踪+数据

- **催化走势跟踪**: _—
- **限售解禁**: [2026-06-30](feeds/lockups/lockups_2026-06-30.md) · [2026-06-29](feeds/lockups/lockups_2026-06-29.md) · [2026-06-26](feeds/lockups/lockups_2026-06-26.md) · [2026-06-25](feeds/lockups/lockups_2026-06-25.md)
- **一致预期EPS**: [2026-06-30](feeds/eps/eps_2026-06-30.md) · [2026-06-29](feeds/eps/eps_2026-06-29.md) · [2026-06-26](feeds/eps/eps_2026-06-26.md) · [2026-06-25](feeds/eps/eps_2026-06-25.md)
- **财务指标**: [2026-06-30](feeds/financials/financials_2026-06-30.md) · [2026-06-29](feeds/financials/financials_2026-06-29.md) · [2026-06-28](feeds/financials/financials_2026-06-28.md) · [2026-06-27](feeds/financials/financials_2026-06-27.md) · [2026-06-26](feeds/financials/financials_2026-06-26.md) · [2026-06-25](feeds/financials/financials_2026-06-25.md)
- **共性扫描**: _—
- **个股档案构建**: _—
