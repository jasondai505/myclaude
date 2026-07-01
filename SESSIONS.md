# Session Log

每次重开会话时翻到这里就能快速找回上下文：上次做了什么、决定了什么、还有什么没做。

---

## 2026-06-30 — RSS分析 → 全线升级 (13 commits)

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

### 待办
- [ ] 板块关键词映射：sector→DB 搜索用的自然语言关键词
- [ ] 卡脖子信号验证：跟踪何时有实质标的映射触发
- [ ] signal_monitor 阈值校准：跑一周后回头看
