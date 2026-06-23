# 数据源采集索引

> 更新于 2026-06-23 18:18

## 状态总览

| 数据源 | 最新到 | 上次跑 | 状态 | 7日条数 | 备注 |
|--------|--------|---------|------|---------|------|
| 知识星球 | 2026-06-23 | 2026-06-23 17:51 | ✅ ok | 1968 | sync 新增 5 条 |
| 公告 | 2026-06-23 | 2026-06-23 17:51 | ✅ ok | 8482 | 成功4/4天 |
| 公告深度研读 | 2026-06-23 | 2026-06-23 17:51 | ✅ ok | 783 | 7天: 0条公告→S1=0→S2=0→存档0条 | (2026-06-21) 无公告数据; (2026-06-22) 已有51条结果，跳过; (2026-06-23) 已有151条结果，跳过 |
| 个股新闻 | 2026-06-23 | 2026-06-23 17:52 | ✅ ok | 2618 | 136/136 只成功，新增 441 |
| 新闻边际信号 | 2026-06-23 | 2026-06-23 17:54 | ✅ ok | 2618 | 7天共79条 | 0条边际信号/1条新闻; 24条边际信号/226条新闻; 18条边际信号/376条新闻 |
| 个股研报 | 2026-06-23 | 2026-06-23 17:54 | ✅ ok | 80 | 全市场 66 篇，新增 None |
| 研报深度跟踪 | 2026-06-23 | 2026-06-23 17:56 | ✅ ok | 783 | 7天: 35只有信号, 32只LLM, 35份档案 |
| 互动易 | 2026-06-23 | 2026-06-23 17:58 | ✅ ok | 1008 | 132 只成功，新增 110 |
| 业绩预告快报 | 2026-06-23 | 2026-06-23 17:58 | ✅ ok | 0 | 预告0+快报0，新增0 |
| 机构调研 | 2026-06-23 | 2026-06-23 17:58 | ✅ ok | 24 | 命中10，新增5 |
| 调研+互动情绪 | 2026-06-23 | 2026-06-23 17:58 | ✅ ok | 24 | 7天: 调研17+互动35+业绩0=52只(52存档) |
| 限售解禁 | 2026-06-23 | 2026-06-23 17:59 | ✅ ok | 255 | 136只，命中81，新增19 |
| 一致预期EPS | 2026-06-23 | 2026-06-23 18:01 | ✅ ok | 524 | 136只，命中275 |
| 行业研报 | 2026-06-23 | 2026-06-23 18:01 | ✅ ok | 517 | 行业/策略/宏观 359 篇，新增 359 |
| 行业深度分析 | 2026-06-23 | 2026-06-23 18:04 | ✅ ok | 835 | 7个报告日: S1=135→S2=100→存档6 | (2026-06-21) S1=26→S2=19→合成; (2026-06-22) S1=26→S2=19→合成; (2026-06-23) S1=26→S2=19→合成 |
| 催化走势跟踪 | 2026-06-23 | 2026-06-23 18:04 | ✅ ok | 1228 | 确认0条催化（0条历史复活） |
| 共性扫描 | 2026-06-23 | 2026-06-23 18:04 | ✅ ok | 1228 | 强势池178只(涨停96) · 多概念标签1478个 |
| 个股档案构建 | 2026-06-23 | 2026-06-23 18:08 | ✅ ok | 783 | 优先池22只 → 聚合8维 → LLM合成22/22份档案 |
| 财务指标 | 2026-06-23 | 2026-06-23 18:09 | ✅ ok | 757 | 成功136/失败0，新增123 |
| 微信公众号 | 2026-06-23 | 2026-06-23 18:17 | ✅ ok | 55 | 拉取 200 篇，新增 1，全文 28/37 |
| 韭研脱水研报 | 2026-06-23 | 2026-06-23 18:11 | ➖ skip | 0 | PDF 采集 0 份 |
| 唐史主任微博 | 2026-06-23 | 2026-06-23 18:11 | ❌ error | 0 | cannot import name 'call' from 'llm' (C:\Users\daixin\myclaude\daily_review\llm.py) |

## 今日 (2026-06-23) 各源报告

- [公告](feeds/announcements/announcements_2026-06-23.md)
- [个股研报](feeds/research/research_2026-06-23.md)
- [机构调研](feeds/surveys/surveys_2026-06-23.md)
- [互动易](feeds/interactions/interactions_2026-06-23.md)
- [业绩预告快报](feeds/earnings/earnings_2026-06-23.md)
- [个股新闻](feeds/news/news_2026-06-23.md)
- [新闻边际信号](feeds/news_signals/news_signals_2026-06-23.md)
- [行业研报](feeds/industry/industry_2026-06-23.md)
- [微信公众号](feeds/wechat/wechat_2026-06-23.md)
- [知识星球](feeds/zsxq/zsxq_2026-06-23.md)
- [限售解禁](feeds/lockups/lockups_2026-06-23.md)
- [一致预期EPS](feeds/eps/eps_2026-06-23.md)
- [财务指标](feeds/financials/financials_2026-06-23.md)

## 最近 7 天报告

### 📄 公告

- **公告**: [2026-06-23](feeds/announcements/announcements_2026-06-23.md) · [2026-06-22](feeds/announcements/announcements_2026-06-22.md) · [2026-06-19](feeds/announcements/announcements_2026-06-19.md) · [2026-06-18](feeds/announcements/announcements_2026-06-18.md) · [2026-06-17](feeds/announcements/announcements_2026-06-17.md)
- **公告深度研读**: _—

### 📊 研报

- **个股研报**: [2026-06-23](feeds/research/research_2026-06-23.md) · [2026-06-22](feeds/research/research_2026-06-22.md) · [2026-06-21](feeds/research/research_2026-06-21.md) · [2026-06-20](feeds/research/research_2026-06-20.md) · [2026-06-19](feeds/research/research_2026-06-19.md) · [2026-06-18](feeds/research/research_2026-06-18.md) · [2026-06-17](feeds/research/research_2026-06-17.md)
- **研报深度跟踪**: _—

### 🔍 调研+互动

- **机构调研**: [2026-06-23](feeds/surveys/surveys_2026-06-23.md) · [2026-06-22](feeds/surveys/surveys_2026-06-22.md) · [2026-06-21](feeds/surveys/surveys_2026-06-21.md) · [2026-06-20](feeds/surveys/surveys_2026-06-20.md) · [2026-06-19](feeds/surveys/surveys_2026-06-19.md) · [2026-06-18](feeds/surveys/surveys_2026-06-18.md) · [2026-06-17](feeds/surveys/surveys_2026-06-17.md)
- **调研+互动情绪**: _—
- **互动易**: [2026-06-23](feeds/interactions/interactions_2026-06-23.md) · [2026-06-22](feeds/interactions/interactions_2026-06-22.md) · [2026-06-21](feeds/interactions/interactions_2026-06-21.md) · [2026-06-20](feeds/interactions/interactions_2026-06-20.md) · [2026-06-19](feeds/interactions/interactions_2026-06-19.md) · [2026-06-18](feeds/interactions/interactions_2026-06-18.md) · [2026-06-17](feeds/interactions/interactions_2026-06-17.md)

### 📈 业绩+新闻

- **业绩预告快报**: [2026-06-23](feeds/earnings/earnings_2026-06-23.md) · [2026-06-22](feeds/earnings/earnings_2026-06-22.md) · [2026-06-21](feeds/earnings/earnings_2026-06-21.md) · [2026-06-20](feeds/earnings/earnings_2026-06-20.md) · [2026-06-19](feeds/earnings/earnings_2026-06-19.md) · [2026-06-18](feeds/earnings/earnings_2026-06-18.md) · [2026-06-17](feeds/earnings/earnings_2026-06-17.md)
- **个股新闻**: [2026-06-23](feeds/news/news_2026-06-23.md) · [2026-06-22](feeds/news/news_2026-06-22.md) · [2026-06-21](feeds/news/news_2026-06-21.md) · [2026-06-20](feeds/news/news_2026-06-20.md) · [2026-06-19](feeds/news/news_2026-06-19.md) · [2026-06-18](feeds/news/news_2026-06-18.md) · [2026-06-17](feeds/news/news_2026-06-17.md)
- **新闻边际信号**: [2026-06-23](feeds/news_signals/news_signals_2026-06-23.md) · [2026-06-22](feeds/news_signals/news_signals_2026-06-22.md) · [2026-06-21](feeds/news_signals/news_signals_2026-06-21.md) · [2026-06-20](feeds/news_signals/news_signals_2026-06-20.md) · [2026-06-19](feeds/news_signals/news_signals_2026-06-19.md) · [2026-06-18](feeds/news_signals/news_signals_2026-06-18.md) · [2026-06-17](feeds/news_signals/news_signals_2026-06-17.md)

### 🏭 行业

- **行业研报**: [2026-06-23](feeds/industry/industry_2026-06-23.md) · [2026-06-22](feeds/industry/industry_2026-06-22.md) · [2026-06-21](feeds/industry/industry_2026-06-21.md) · [2026-06-20](feeds/industry/industry_2026-06-20.md) · [2026-06-19](feeds/industry/industry_2026-06-19.md) · [2026-06-18](feeds/industry/industry_2026-06-18.md) · [2026-06-17](feeds/industry/industry_2026-06-17.md)
- **行业深度分析**: _—

### 💬 社交信息源

- **微信公众号**: [2026-06-23](feeds/wechat/wechat_2026-06-23.md) · [2026-06-22](feeds/wechat/wechat_2026-06-22.md) · [2026-06-21](feeds/wechat/wechat_2026-06-21.md) · [2026-06-20](feeds/wechat/wechat_2026-06-20.md) · [2026-06-19](feeds/wechat/wechat_2026-06-19.md) · [2026-06-18](feeds/wechat/wechat_2026-06-18.md) · [2026-06-17](feeds/wechat/wechat_2026-06-17.md)
- **唐史主任微博**: [2026-06-17](feeds/weibo/weibo_2026-06-17.md)
- **知识星球**: [2026-06-23](feeds/zsxq/zsxq_2026-06-23.md) · [2026-06-22](feeds/zsxq/zsxq_2026-06-22.md) · [2026-06-21](feeds/zsxq/zsxq_2026-06-21.md) · [2026-06-20](feeds/zsxq/zsxq_2026-06-20.md) · [2026-06-19](feeds/zsxq/zsxq_2026-06-19.md) · [2026-06-18](feeds/zsxq/zsxq_2026-06-18.md) · [2026-06-17](feeds/zsxq/zsxq_2026-06-17.md)
- **韭研脱水研报**: [2026-06-22](feeds/jiuyang/jiuyang_2026-06-22.md) · [2026-06-21](feeds/jiuyang/jiuyang_2026-06-21.md) · [2026-06-17](feeds/jiuyang/jiuyang_2026-06-17.md)

### 📡 跟踪+数据

- **催化走势跟踪**: _—
- **限售解禁**: [2026-06-23](feeds/lockups/lockups_2026-06-23.md) · [2026-06-22](feeds/lockups/lockups_2026-06-22.md) · [2026-06-21](feeds/lockups/lockups_2026-06-21.md) · [2026-06-19](feeds/lockups/lockups_2026-06-19.md) · [2026-06-18](feeds/lockups/lockups_2026-06-18.md) · [2026-06-17](feeds/lockups/lockups_2026-06-17.md)
- **一致预期EPS**: [2026-06-23](feeds/eps/eps_2026-06-23.md) · [2026-06-22](feeds/eps/eps_2026-06-22.md) · [2026-06-21](feeds/eps/eps_2026-06-21.md) · [2026-06-19](feeds/eps/eps_2026-06-19.md) · [2026-06-18](feeds/eps/eps_2026-06-18.md) · [2026-06-17](feeds/eps/eps_2026-06-17.md)
- **财务指标**: [2026-06-23](feeds/financials/financials_2026-06-23.md) · [2026-06-22](feeds/financials/financials_2026-06-22.md) · [2026-06-21](feeds/financials/financials_2026-06-21.md) · [2026-06-20](feeds/financials/financials_2026-06-20.md) · [2026-06-19](feeds/financials/financials_2026-06-19.md) · [2026-06-18](feeds/financials/financials_2026-06-18.md) · [2026-06-17](feeds/financials/financials_2026-06-17.md)
- **共性扫描**: _—
- **个股档案构建**: _—
