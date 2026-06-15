# 📊 每日仪表盘 2026-06-15

> 自动生成 | [复盘](daily_review/reports/review/review_2026-06-14.md) | [建议](daily_review/reports/advice/advice_2026-06-15.md) | [深研档案](daily_review/reports/deep_read/) | [个股档案](daily_review/reports/research_dossiers/)

## 五维信息源

| 维度 | 采集 | 数据 | 信号 | 成本 |
|------|:---:|------|-----:|:----:|
| 📄 ① 公告深研 | ⏰ | 🟢 新鲜 | >=60: 18条 | $9/天 |
| 📊 ② 研报跟踪 | ⚠️ | 🟢 新鲜 | 11份档案 | $1/天 |
| 🔍 ③ 调研情绪 | ⚠️ | 🟡 3天前 | 11份档案 | $0 |
| 💬 ④ 互动易 | ✅ | — | 11份档案 | $0 |
| 📈 ⑤ 业绩预告 | ✅ | 🔴 27天前 | — | $0 |

## 采集管线

| 源 | 状态 | 上次成功 | 新增 | 备注 |
|----|:----:|---------|-----:|------|
| 📄 公告采集 | ✅ | 2026-06-15 | 206 | 成功5/5天 |
| 📄 公告深研 | ⏰ | 2026-06-15 | 0 | 超时(1200s) ⏰ |
| 📊 研报采集 | ✅ | 2026-06-15 | 0 | 全市场 69 篇，新增 None |
| 📊 研报跟踪 | ⚠️ | — | 0 | 从未运行 |
| 🔍 机构调研 | ✅ | 2026-06-15 | 0 | 命中15，新增0 |
| 🔍 调研+互动情绪 | ⚠️ | — | 0 | 从未运行 |
| 💬 互动易 | ✅ | 2026-06-15 | 0 | 132 只成功，新增 0 |
| 📈 业绩预告 | ✅ | 2026-06-15 | 0 | 预告0+快报0，新增0 |
| 📰 个股新闻 | ✅ | 2026-06-15 | 15 | 137/137 只成功，新增 15 |
| 🏭 行业研报 | ✅ | 2026-06-15 | 317 | 行业/策略/宏观 317 篇，新增 317 |
| 💚 微信公众号 | ✅ | 2026-06-15 | 5 | 拉取 200 篇，新增 5 |
| 🐦 唐史主任微博 | ➖ | 2026-06-15 | 0 | 无新帖 |
| ⭐ 知识星球 | ✅ | 2026-06-15 | 132 | sync 新增 132 条 |
| 📝 韭研脱水研报 | ✅ | 2026-06-14 | 3 | PDF 采集 3 份 |

## ⚠️ 异常警报

- 📄 公告深研: 超时(1200s) → 检查 DeepSeek API 是否正常 → 减少 --days 范围 → 或等下次重试
- 📊 研报跟踪: 从未运行 → 手动执行 python daily_collect.py --source research_deep_read
- 🔍 调研+互动情绪: 从未运行 → 检查 collector 是否在 SOURCE_TIERS 中正确注册

## 📈 本周公告深研趋势

- 2026-06-08: █████ 79条  **10条≥60**
- 2026-06-09: ████████████████████ 281条  **4条≥60**
- 2026-06-10: █ 22条
- 2026-06-11: ██████████████ 206条  **4条≥60**
- 2026-06-12: ██ 33条

## 📜 最近提交

- `b25e113 data: FEV覆盖扩展至3064只(五源并集) — 2400只有效A股, 均值14.9, ≥20分323只`
- `37e9f35 fix: dossier prompt要求显式输出FEV数值(F=x E=y V=z FEV=total格式)`
- `7ea6528 fix: stock_dossier_builder FEV数据源从review.db修正为serenity.db, 25份档案FEV生效`
- `a75f124 data: 974只标的增强版FEV评分完成(日均, 8分钟) — FEV范围0-25, 均值13.5`
- `96c9370 feat: FEV增强版评分 — 注入财务/行业/估值/信号多维数据,50只验证通过`
- `051f643 feat: 个股深度档案系统prototype — 多维聚合+LLM一页纸合成(22只验证通过)`
- `20107f7 feat: analyze_zsxq支持指定日期,回填6/1~6/14共13天星球深度分析`
- `098226a feat: daily_collect接入星球深度分析(analyze_zsxq),每次zsxq采集后自动运行`
- `cea5bf3 fix: 微信公众号分析报告迁至wechat_analysis/ + 清理7处旧路径引用`
- `8d18820 fix: ZSXQ正文截断300→500字 + 分析报告迁至zsxq_analysis/ + 清理feeds残留industry文件`

---

### 🔗 快速链接
| 页面 | 路径 |
|------|------|
| 复盘报告 | `daily_review/reports/review/review_{date}.md` |
| 盘前建议 | `daily_review/reports/advice/advice_{date}.md` |
| 公告深研 | `daily_review/reports/deep_read/` |
| 个股档案 | `daily_review/reports/research_dossiers/` |
| 催化筛查 | `daily_review/reports/catalyst/` |
| 行业研报 | `daily_review/reports/industry/` |
| Git 历史 | 终端: `git log --oneline` |
| 会话转录 | `~/.claude/projects/C--Users-daixin-myclaude/*.jsonl` (grep 关键词) |