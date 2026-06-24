# 数据源采集索引

> 更新于 2026-06-24 23:15

## 状态总览

| 数据源 | 最新到 | 上次跑 | 状态 | 7日条数 | 备注 |
|--------|--------|---------|------|---------|------|
| 知识星球 | 2026-06-24 | 2026-06-24 23:07 | ✅ ok | 1885 | sync 新增 70 条 |
| 公告 | 2026-06-24 | 2026-06-24 22:00 | ✅ ok | 8616 | 成功5/5天 |
| 公告深度研读 | 2026-06-24 | 2026-06-24 22:37 | ✅ ok | 828 | 7天: 4338条公告→S1=425→S2=424→存档424条 | (2026-06-22) 915条→S1=85→S2=85→存档85条; (2026-06-23) 1756条→S1=187→S2=186→存档186条; (2026-06-24) 1667条→S1=153→S2=153→存档153条 |
| 个股新闻 | 2026-06-24 | 2026-06-24 23:03 | ✅ ok | 2525 | 132/132 只成功，新增 123 |
| 新闻边际信号 | 2026-06-24 | 2026-06-24 23:05 | ✅ ok | 2525 | 7天共89条 | 16条边际信号/220条新闻; 24条边际信号/196条新闻; 37条边际信号/352条新闻 |
| 个股研报 | 2026-06-24 | 2026-06-24 22:00 | ✅ ok | 86 | 全市场 71 篇，新增 None |
| 研报深度跟踪 | 2026-06-24 | 2026-06-24 22:39 | ✅ ok | 828 | 7天: 33只有信号, 28只LLM, 33份档案 |
| 互动易 | 2026-06-24 | 2026-06-24 23:02 | · timeout | 1023 | 超时(1200s) |
| 业绩预告快报 | 2026-06-24 | 2026-06-24 22:39 | ✅ ok | 0 | 预告0+快报0，新增0 |
| 机构调研 | 2026-06-24 | 2026-06-24 23:05 | ✅ ok | 19 | 命中6，新增0 |
| 调研+互动情绪 | 2026-06-24 | 2026-06-24 23:05 | ✅ ok | 19 | 7天: 调研13+互动42+业绩0=55只(55存档) |
| 限售解禁 | 2026-06-24 | 2026-06-24 19:02 | ✅ ok | 274 | 132只，命中90，新增20 |
| 一致预期EPS | 2026-06-24 | 2026-06-24 19:04 | ✅ ok | 532 | 132只，命中306 |
| 行业研报 | 2026-06-24 | 2026-06-24 22:39 | ✅ ok | 504 | 行业/策略/宏观 352 篇，新增 352 |
| 行业深度分析 | 2026-06-24 | 2026-06-24 22:42 | ✅ ok | 818 | 7个报告日: S1=135→S2=101→存档6 | (2026-06-22) S1=26→S2=19→合成; (2026-06-23) S1=26→S2=19→合成; (2026-06-24) S1=26→S2=20→合成 |
| 催化走势跟踪 | 2026-06-24 | 2026-06-24 22:20 | ✅ ok | 1349 | 确认0条催化（0条历史复活） |
| 共性扫描 | 2026-06-24 | 2026-06-24 22:20 | ✅ ok | 1349 | 强势池260只(涨停101) · 多概念标签2874个 · sector_log +43概念 |
| 个股档案构建 | 2026-06-24 | 2026-06-24 19:11 | ✅ ok | 828 | 优先池22只 → 聚合8维 → LLM合成22/22份档案 |
| 财务指标 | 2026-06-24 | 2026-06-24 19:11 | ✅ ok | 866 | 成功131/失败1，新增97 |
| 微信公众号 | 2026-06-24 | 2026-06-24 23:07 | ✅ ok | 56 | 拉取 34 篇，新增 0，全文 25/34 |
| 韭研脱水研报 | 2026-06-24 | 2026-06-24 23:02 | ✅ ok | 0 | PDF 采集 3 份 |
| 唐史主任微博 | 2026-06-24 | 2026-06-24 23:07 | ➖ skip | 0 | 无新帖 |

## 今日 (2026-06-24) 各源报告

- [公告](feeds/announcements/announcements_2026-06-24.md)
- [个股研报](feeds/research/research_2026-06-24.md)
- [机构调研](feeds/surveys/surveys_2026-06-24.md)
- [互动易](feeds/interactions/interactions_2026-06-24.md)
- [业绩预告快报](feeds/earnings/earnings_2026-06-24.md)
- [个股新闻](feeds/news/news_2026-06-24.md)
- [新闻边际信号](feeds/news_signals/news_signals_2026-06-24.md)
- [行业研报](feeds/industry/industry_2026-06-24.md)
- [知识星球](feeds/zsxq/zsxq_2026-06-24.md)
- [韭研脱水研报](feeds/jiuyang/jiuyang_2026-06-24.md)
- [限售解禁](feeds/lockups/lockups_2026-06-24.md)
- [一致预期EPS](feeds/eps/eps_2026-06-24.md)
- [财务指标](feeds/financials/financials_2026-06-24.md)

## 最近 7 天报告

### 📄 公告

- **公告**: [2026-06-24](feeds/announcements/announcements_2026-06-24.md) · [2026-06-23](feeds/announcements/announcements_2026-06-23.md) · [2026-06-22](feeds/announcements/announcements_2026-06-22.md) · [2026-06-19](feeds/announcements/announcements_2026-06-19.md) · [2026-06-18](feeds/announcements/announcements_2026-06-18.md)
- **公告深度研读**: _—

### 📊 研报

- **个股研报**: [2026-06-24](feeds/research/research_2026-06-24.md) · [2026-06-23](feeds/research/research_2026-06-23.md) · [2026-06-22](feeds/research/research_2026-06-22.md) · [2026-06-21](feeds/research/research_2026-06-21.md) · [2026-06-20](feeds/research/research_2026-06-20.md) · [2026-06-19](feeds/research/research_2026-06-19.md) · [2026-06-18](feeds/research/research_2026-06-18.md)
- **研报深度跟踪**: _—

### 🔍 调研+互动

- **机构调研**: [2026-06-24](feeds/surveys/surveys_2026-06-24.md) · [2026-06-23](feeds/surveys/surveys_2026-06-23.md) · [2026-06-22](feeds/surveys/surveys_2026-06-22.md) · [2026-06-21](feeds/surveys/surveys_2026-06-21.md) · [2026-06-20](feeds/surveys/surveys_2026-06-20.md) · [2026-06-19](feeds/surveys/surveys_2026-06-19.md) · [2026-06-18](feeds/surveys/surveys_2026-06-18.md)
- **调研+互动情绪**: _—
- **互动易**: [2026-06-24](feeds/interactions/interactions_2026-06-24.md) · [2026-06-23](feeds/interactions/interactions_2026-06-23.md) · [2026-06-22](feeds/interactions/interactions_2026-06-22.md) · [2026-06-21](feeds/interactions/interactions_2026-06-21.md) · [2026-06-20](feeds/interactions/interactions_2026-06-20.md) · [2026-06-19](feeds/interactions/interactions_2026-06-19.md) · [2026-06-18](feeds/interactions/interactions_2026-06-18.md)

### 📈 业绩+新闻

- **业绩预告快报**: [2026-06-24](feeds/earnings/earnings_2026-06-24.md) · [2026-06-23](feeds/earnings/earnings_2026-06-23.md) · [2026-06-22](feeds/earnings/earnings_2026-06-22.md) · [2026-06-21](feeds/earnings/earnings_2026-06-21.md) · [2026-06-20](feeds/earnings/earnings_2026-06-20.md) · [2026-06-19](feeds/earnings/earnings_2026-06-19.md) · [2026-06-18](feeds/earnings/earnings_2026-06-18.md)
- **个股新闻**: [2026-06-24](feeds/news/news_2026-06-24.md) · [2026-06-23](feeds/news/news_2026-06-23.md) · [2026-06-22](feeds/news/news_2026-06-22.md) · [2026-06-21](feeds/news/news_2026-06-21.md) · [2026-06-20](feeds/news/news_2026-06-20.md) · [2026-06-19](feeds/news/news_2026-06-19.md) · [2026-06-18](feeds/news/news_2026-06-18.md)
- **新闻边际信号**: [2026-06-24](feeds/news_signals/news_signals_2026-06-24.md) · [2026-06-23](feeds/news_signals/news_signals_2026-06-23.md) · [2026-06-22](feeds/news_signals/news_signals_2026-06-22.md) · [2026-06-21](feeds/news_signals/news_signals_2026-06-21.md) · [2026-06-20](feeds/news_signals/news_signals_2026-06-20.md) · [2026-06-19](feeds/news_signals/news_signals_2026-06-19.md) · [2026-06-18](feeds/news_signals/news_signals_2026-06-18.md)

### 🏭 行业

- **行业研报**: [2026-06-24](feeds/industry/industry_2026-06-24.md) · [2026-06-23](feeds/industry/industry_2026-06-23.md) · [2026-06-22](feeds/industry/industry_2026-06-22.md) · [2026-06-21](feeds/industry/industry_2026-06-21.md) · [2026-06-20](feeds/industry/industry_2026-06-20.md) · [2026-06-19](feeds/industry/industry_2026-06-19.md) · [2026-06-18](feeds/industry/industry_2026-06-18.md)
- **行业深度分析**: _—

### 💬 社交信息源

- **微信公众号**: [2026-06-23](feeds/wechat/wechat_2026-06-23.md) · [2026-06-22](feeds/wechat/wechat_2026-06-22.md) · [2026-06-21](feeds/wechat/wechat_2026-06-21.md) · [2026-06-20](feeds/wechat/wechat_2026-06-20.md) · [2026-06-19](feeds/wechat/wechat_2026-06-19.md) · [2026-06-18](feeds/wechat/wechat_2026-06-18.md)
- **唐史主任微博**: _—
- **知识星球**: [2026-06-24](feeds/zsxq/zsxq_2026-06-24.md) · [2026-06-23](feeds/zsxq/zsxq_2026-06-23.md) · [2026-06-22](feeds/zsxq/zsxq_2026-06-22.md) · [2026-06-21](feeds/zsxq/zsxq_2026-06-21.md) · [2026-06-20](feeds/zsxq/zsxq_2026-06-20.md) · [2026-06-19](feeds/zsxq/zsxq_2026-06-19.md) · [2026-06-18](feeds/zsxq/zsxq_2026-06-18.md)
- **韭研脱水研报**: [2026-06-24](feeds/jiuyang/jiuyang_2026-06-24.md) · [2026-06-23](feeds/jiuyang/jiuyang_2026-06-23.md) · [2026-06-22](feeds/jiuyang/jiuyang_2026-06-22.md) · [2026-06-21](feeds/jiuyang/jiuyang_2026-06-21.md)

### 📡 跟踪+数据

- **催化走势跟踪**: _—
- **限售解禁**: [2026-06-24](feeds/lockups/lockups_2026-06-24.md) · [2026-06-23](feeds/lockups/lockups_2026-06-23.md) · [2026-06-22](feeds/lockups/lockups_2026-06-22.md) · [2026-06-21](feeds/lockups/lockups_2026-06-21.md) · [2026-06-19](feeds/lockups/lockups_2026-06-19.md) · [2026-06-18](feeds/lockups/lockups_2026-06-18.md)
- **一致预期EPS**: [2026-06-24](feeds/eps/eps_2026-06-24.md) · [2026-06-23](feeds/eps/eps_2026-06-23.md) · [2026-06-22](feeds/eps/eps_2026-06-22.md) · [2026-06-21](feeds/eps/eps_2026-06-21.md) · [2026-06-19](feeds/eps/eps_2026-06-19.md) · [2026-06-18](feeds/eps/eps_2026-06-18.md)
- **财务指标**: [2026-06-24](feeds/financials/financials_2026-06-24.md) · [2026-06-23](feeds/financials/financials_2026-06-23.md) · [2026-06-22](feeds/financials/financials_2026-06-22.md) · [2026-06-21](feeds/financials/financials_2026-06-21.md) · [2026-06-20](feeds/financials/financials_2026-06-20.md) · [2026-06-19](feeds/financials/financials_2026-06-19.md) · [2026-06-18](feeds/financials/financials_2026-06-18.md)
- **共性扫描**: _—
- **个股档案构建**: _—
