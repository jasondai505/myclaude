# 📊 每日仪表盘 2026-06-14

> 自动生成 | [复盘](daily_review/reports/review/review_2026-06-13.md) | [建议](daily_review/reports/advice/advice_2026-06-14.md) | [深研档案](daily_review/reports/deep_read/) | [个股档案](daily_review/reports/research_dossiers/)

## 五维信息源

| 维度 | 采集 | 数据 | 信号 | 成本 |
|------|:---:|------|-----:|:----:|
| 📄 ① 公告深研 | ⏰ | 🟡 2天前 | >=60: 14条 | $9/天 |
| 📊 ② 研报跟踪 | ⚠️ | 🟢 新鲜 | 0份档案 | $1/天 |
| 🔍 ③ 调研情绪 | ⚠️ | 🟡 2天前 | 0份档案 | $0 |
| 💬 ④ 互动易 | ✅ | — | 0份档案 | $0 |
| 📈 ⑤ 业绩预告 | ✅ | 🔴 26天前 | — | $0 |

## 采集管线

| 源 | 状态 | 上次成功 | 新增 | 备注 |
|----|:----:|---------|-----:|------|
| 📄 公告采集 | ✅ | 2026-06-12 | 0 | 成功5/5天 |
| 📄 公告深研 | ⏰ | 2026-06-13 | 0 | 超时(300s) ⏰ |
| 📊 研报采集 | ✅ | 2026-06-13 | 0 | 全市场 88 篇，新增 None |
| 📊 研报跟踪 | ⚠️ | — | 0 | 从未运行 |
| 🔍 机构调研 | ✅ | 2026-06-13 | 0 | 命中0，新增0 |
| 🔍 调研+互动情绪 | ⚠️ | — | 0 | 从未运行 |
| 💬 互动易 | ✅ | 2026-06-13 | 0 | 132 只成功，新增 0 |
| 📈 业绩预告 | ✅ | 2026-06-13 | 0 | 预告0+快报0，新增0 |
| 📰 个股新闻 | ✅ | 2026-06-13 | 0 | 137/137 只成功，新增 0 |
| 🏭 行业研报 | ✅ | 2026-06-07 | 900 | 行业/策略/宏观 900 篇，新增 900 |
| 💚 微信公众号 | ✅ | 2026-06-13 | 0 | 拉取 200 篇，新增 0 |
| 🐦 唐史主任微博 | ➖ | 2026-06-13 | 0 | 无新帖 |
| ⭐ 知识星球 | ✅ | 2026-06-13 | 0 | sync 新增 0 条 |
| 📝 韭研脱水研报 | ➖ | 2026-06-13 | 0 | PDF 采集 0 份 |

## ⚠️ 异常警报

- 📄 公告深研: 超时(300s) → 检查 DeepSeek API 是否正常 → 减少 --days 范围 → 或等下次重试
- 📊 研报跟踪: 从未运行 → 手动执行 python daily_collect.py --source research_deep_read
- 🔍 调研+互动情绪: 从未运行 → 检查 collector 是否在 SOURCE_TIERS 中正确注册

## 📈 本周公告深研趋势

- 2026-06-08: █████ 71条  **5条≥60**
- 2026-06-09: ████████████████████ 281条  **4条≥60**
- 2026-06-10: █ 22条
- 2026-06-11: ██████████████ 206条  **4条≥60**
- 2026-06-12: ██ 33条  **1条≥60**

## 📜 最近提交

- `82d21e5 feat: 行业月度分析扩展至53行业(90%+覆盖)`
- `66ad158 fix: 星级改用CSS class着色 + snippets`
- `20c1f85 fix: 星级改用emoji前缀(🟡🟢🔵🟠⚫)替代HTML span`
- `54c00c5 feat: 行业月度分析加星级+颜色评分`
- `01cb99b feat: 星级颜色编码(金/绿/蓝/橙/灰)`
- `d9c1658 feat: 研报档案五星评级系统`
- `a59be25 feat: 行业研报月度LLM分析(5/15~6/14)`
- `b4b95d9 fix: industry_reports INSERT OR REPLACE + 空info_code兜底`
- `57a1b18 feat: 行业/策略/宏观研报采集管线 (eastmoney-reports方案A)`
- `ada219f feat: 研报档案自动索引页(research_dossiers/README.md)`

---

### 🔗 快速链接
| 页面 | 路径 |
|------|------|
| 复盘报告 | `daily_review/reports/review/review_{date}.md` |
| 盘前建议 | `daily_review/reports/advice/advice_{date}.md` |
| 公告深研 | `daily_review/reports/deep_read/` |
| 个股档案 | `daily_review/reports/research_dossiers/` |
| 催化跟踪 | `daily_review/reports/feeds/catalyst_track_{date}.md` |
| Git 历史 | 终端: `git log --oneline` |
| 会话转录 | `~/.claude/projects/C--Users-daixin-myclaude/*.jsonl` (grep 关键词) |