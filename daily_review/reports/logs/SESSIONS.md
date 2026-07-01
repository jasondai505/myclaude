# Session Log

上半部分：**辩论归档**（方法论来源 + 将来怎么用）。下半部分：**施工日志**（做了什么 + 待办）。

---

## 辩论归档

fact-debate 产出的决策和方法论。每一场都改变了后续的施工方式。

### 2026-06-30 辩论 #2 · 四条铁律 + 三信号触发

**背景**：AI 提了 #1-#4 方案，执行前先拉到 fact-debate。

**AI 攻击的四个漏洞 & 用户的回应**：
- 「触发信号未定义」→ "先做再调，数据跑回来才能校准"
- 「尽早没有精确解」→ "一棵树最好的植树时间是十年前和现在"
- 「5⭐ 评级无标准」→ "标准需要不断验证优化"
- 「成本太高（$15/天）」→ "只有到最后一步成本才需考虑"

**辩论产出**：
1. #1-#4 优先级清单（advice → bridge → signal → review）
2. 三条用户决策铁律 → CLAUDE.md
3. 迭代施工方法（框架先于精度、辩论→清单、dry-run再校准）
4. 施工优先级评估框架

**将来怎么用**：新功能启动前先 debate → 可执行清单；AI 因成本/精度自限时翻 CLAUDE.md「用户决策铁律」；施工按优先级框架排序。

### 2026-06-30 辩论 #1 · 语料直接调用问题

**背景**：deep_topic 第一版产出后，用户发现星球推销帖（「终极卖铲人」「强call」）和机构研报同权注入 LLM。

**辩论产出**：
1. T1/T2/T3 语料可信度三层分级 → 全管线渗透
2. 纠偏/被错杀是必选项 → deep_topic 强制章节
3. 源日期标注 + 全称铁律 → 可追溯查证
4. 四条新铁律 → CLAUDE.md

**将来怎么用**：新 LLM 管线接入多源信息时先做源可信度分层；深度分析必须含「被错杀/错涨」；T2 源的推销措辞不得直接作为建议。

### 2026-06-20 辩论 · 自下而上框架

**结论**：不另起炉灶——在 `_chain_heat` 上加三列（80 行），不建新模块（500+ 行）。改造类任务默认先 grep 审计现有基础设施。

### 2026-06-11 辩论 · 概念归因

**结论**：标签不是原因——覆盖率 >5% 的概念是噪音。产出防混锅铁律、富集比检验、单主因归因。

---

## 施工日志

### 2026-06-30 — RSS分析 → 全线升级 (14 commits)

### 触发
- morning_intel 8 项异常（Δ覆盖291 vs FEV 2721、review.db 302MB、多项 health_check 误报）
- RSS 手动刷新有 0629 新文章（含 1 篇深度投研）

### 关键决策
1. **Δ 双轨改造**：机械Δ（全市场 3837/$0）+ LLMΔ（去 200 上限/日期回退/DB 兜底），而非纯扩 LLM 覆盖
2. **语料 T1/T2/T3 三层分级**：全管线渗透（advice / wechat / review），而非单点修补
3. **deep_topic 多源交叉**：对标同花顺 DEEPTOPIC 标准，强制纠偏/被错杀，而非改进单篇 shendu
4. **成本原则**：成本是迭代约束不是初始否决。deep_topic ~$3/篇
5. **框架先于精度**：signal_monitor 一期 dry-run，跑数据再调阈值

### 新建文件
| 文件 | 作用 |
|------|------|
| `daily_review/deep_topic.py` | 多源交叉深度题材分析管道 |
| `daily_review/signal_monitor.py` | 三信号扫描 + 自动 deep_topic |

### 完成项
| # | 功能 | 成本/天 |
|:--|------|:--:|
| — | Δ 双轨：机械 3837 + LLM 去上限 | $0 |
| — | health_check 8→0 项异常 | $0 |
| — | DB 清理 302→273MB | $0 |
| — | shendu MAX_TOKENS 16000 + 失败告警 | $0 |
| — | deep_topic 多源交叉深度分析 | ~$3/篇 |
| 1 | advice prompt [T1]/[T2]/[HD] 分层 | $0 |
| 2 | shendu → deep_topic 自动桥接 | ~$3/天 |
| 3 | 三信号触发 + 自动深挖（一期 dry-run） | $0→~$6-9 |
| 4 | review/WeChat 语料分层 | $0 |
| — | run_wechat 日期窗口修复 | — |

### CLAUDE.md 新增规则
- 语料可信度分层铁律 / 深度分析质量框架 / LLM 输出截断防范 / 双轨互补模式
- 用户决策铁律（3 条）/ 迭代施工方法（4 条）

### 待办（带入下次）
- [x] 板块关键词映射 → `0935821`
- [ ] 卡脖子信号验证
- [ ] signal_monitor 阈值校准
- [ ] 磁盘 85%

### 收尾
- SESSIONS.md + worklog_2026-07.md 新建
- 辩论归档 4 场 + 日志体系建立
- run_wechat 日期窗口修复
- 0630 RSS 全链路验证（教育十五五 deep_topic 110 源）

---

### 2026-07-01 11:03 — W5 数值审计 + Obsidian 图谱整理 (13 commits)

### 触发
- 7/1 advice W5 风险排雷发现 3 个问题：MU/WDC 涨跌幅为 6/26 旧数据、海博思创一致预期 20.1 亿为 LLM 编造、W5 区数值零校验
- 深入审计后发现 7 只美股涨跌幅全是 6/25→26 旧数据，非仅 MU/WDC

### 关键决策
1. **事后补 validator → 事前约束 + 自动标注**：prompt 要求 LLM 标注 `[HD]`/`[推断]`，validator 扫无标签数字 → whack-a-mole → 系统化
2. **双源交叉比对**：发现真正根因是东方财富 API 全挂→缓存回退→`_saved_at` 被刷新→`_stale` 失效。yfinance + 东方财富双源矛盾才是可靠检测信号
3. **缓存降级无条件标记陈旧**：不依赖时间戳，只要从缓存回退就标记

### 新建/改造
| 文件 | 作用 |
|------|------|
| `data/sector_keyword_map.json` | 352 关键词 → 273 同花顺规范概念 |
| `data/__init__.py` | +`map_keyword_to_concepts()` + yfinance `_data_date` + 美股名称字段 |
| `_run_advice.py` | +`_validate_w5_numbers()` + `_validate_numerical_provenance()` + `_has_source_tag()` + 中文名映射 |
| `claude_prompt.txt` | W5 数字强制规则 + 数值溯源规则 + `%%US_MOVERS%%` 陈旧告警说明 |
| `health_check.py` | +`check_us_after_hours_freshness()` + `check_w5_revenue_hallucination()` |
| `CLAUDE.md` | 数值溯源铁律 + 数据陈旧检测铁律 |

### 四层防线覆盖
| 防线 | 检测点 | 状态 |
|------|--------|:--:|
| 1 | yfinance `_data_date` + 东方财富 `_stale_warning` | ✅ |
| 2 | prompt W5 数字强制规则 + 数值溯源规则 + 陈旧告警说明 | ✅ |
| 3 | `_validate_w5_numbers()`(8检查项) + `_validate_numerical_provenance()`(PE/偏离/倍数) | ✅ |
| 4 | health_check 美股新鲜度 + W5营收幻觉 | ✅ |

### 新规则
- 数值溯源铁律 / 数据陈旧检测铁律（双源交叉比对 + 缓存无条件标记）

### 待办（带入下次）
- [ ] 卡脖子信号验证
- [ ] signal_monitor 阈值校准
- [ ] 磁盘 86%
- [ ] 产业链 XLSX 过期（235/235 早于 0623）

---

### 2026-07-01 18:45 — Obsidian 图谱整理 + MOC 自动刷新 (5 commits)

### 触发
- Obsidian 图谱里 9 个 MOC 节点 8 个是空的（文件不存在），`活跃标的_MOC` 0 字节
- reports 根目录散落审计报告/架构图/临时文件
- 271 篇 deep_read 中 70 篇 `hunting_domain` 为空

### 关键决策
1. **MOC 从手动 → 自动刷新**：`regenerate_mocs()` 接入 `announcement_deep_read` 管线，每次运行后全量重建
2. **hunting_domain 漏检根因**：猎场分级时 Tier2 被移出 `HUNTING_GROUND_DOMAINS`，`get_chokepoint_context()` 只查 Tier1
3. **目录整理**：audit/ref/MOC 三个子目录，根目录只剩 `dashboard.md` + `CLAUDE.md`

### 新建/改造
| 文件 | 作用 |
|------|------|
| `MOC/*.md` (9个) | 板块索引，271→146→44→16→5 金字塔结构 |
| `deep_read/obsidian_archive.py` | +`regenerate_mocs()` 扫描全量 deep_read 按 domain/卡脖子分组 |
| `collectors/announcement_deep_read.py` | run() 末尾调用 `regenerate_mocs()` |
| `deep_read/knowledge_base.py` | `get_chokepoint_context()` 补查 Tier2 |
| `reports/` 目录 | audit/ ref/ MOC/ 三个子目录，删 3 个临时文件 |

### MOC 最终分布
| MOC | 篇数 |
|-----|:---:|
| 活跃标的_MOC | 271 |
| 算力硬件_深度研读 | 146 |
| 深度研读（通用） | 61 |
| 卡脖子 | 46 |
| 新能源_深度研读 | 44 |
| 算力硬件_卡脖子 | 27 |
| 机器人_深度研读 | 16 |
| 化工材料_深度研读 | 5 |
| 新能源_卡脖子 | 1 |

### 待办（带入下次）
- [ ] 卡脖子信号验证
- [ ] signal_monitor 阈值校准
- [ ] 磁盘 86%
- [ ] 产业链 XLSX 过期
