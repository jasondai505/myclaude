# 操作备忘录

工作笔记，AI 做相关任务时可 Read 参考。不自动加载。

---

## 2026-06-20 架构优化终章

### 完成项 (8 commits)
- 走势归因 `engine_attribution.py` — α/β/γ 分解 + 2σ 证伪告警 `6280585`
- 盘前建议仓位管理 — 止损(短线-12%/中线-15%/长线-19%) + 仓位(单票30%/行业50%/总仓90%) `6280585` `3a9c11b`
- 行业双表合并 — 删死表 industry_research + 修 SOURCE_TABLE 映射 `6280585`
- 理杏仁并行化 — 串行→ThreadPoolExecutor(max_workers=5) `49e2ab4`
- orchestrator --from 校验 — 拼错步骤名报错退出 `49e2ab4`
- 海外数据降级 — akshare→JSON缓存(全覆盖7指数,24h TTL) `b54a547`
- health_check 系统资源监控 — psutil 磁盘/内存 `b54a547`
- 架构审计: 代码审计 30/30 ✅, 逻辑审计 11/14

### 踩坑
- Edit 工具对缩进含 Tab 的文件匹配差，批量替换用 Python 脚本更可靠
- claude_prompt.txt 代码块内文本含反引号时 Edit 匹配困难
- akshare 网络不稳定，不适合做降级方案（JSON 文件缓存更稳定）

---

## 2026-06-20 架构大重构

### 完成项 (10 commits)
- feeds/ 553 文件 → 20 子目录 + feed_index 7 大类折叠 `597678c`
- 流水线：执行锁 + depends_on 失败阻断 + 通知日志 + 日志30天轮转 `7bb9ef5`
- config.py 11213→464 行：4 大字典惰性加载 JSON `9da0507`
- store.py WAL + foreign_keys + 版本化迁移框架 `9da0507`
- data.py 1819→3 行：data/ 包结构 `a73f99f`
- 线程泄漏修复：_run_with_timeout → ThreadPoolExecutor `9676cdf`
- LLM 缓存基础设施：llm_cache.py + roles.cached_create() `9676cdf`
- 密钥外提：config.py → .env `9676cdf`
- 重试指数退避 + jitter `9676cdf` + RateLimiter 统一限速
- PHASE 日期驱动自动切换 + DB 大小监控 + RSS 6h `f88cff7`
- F轨市值分层：大盘≤2 中盘≤2 小盘≥1 `e176cdb`
- G2 催化去重 prompt 修正 `890d577`
- LLM 管线去重：llm_processed 追踪表 `4ac5bb4`
- 三份审计文档：架构图谱 / 代码审计 / 逻辑架构功能审计

### 踩坑
- Edit 工具对 Tab 缩进文件匹配敏感，用 Python 脚本 patch 更可靠
- File has been modified since read → 需要 Read 后立即 Edit
- .env.example 被 .env.* 规则误杀，需 git add -f
- `reports/` 在 .gitignore，dashboard/feed_index 不能 git add
- 探索 Agent 审计可能出现误报（engine_focus/emerging_dragon 实际被 run.py 调用，单主因归因实际已正确）
- 理杏仁并行化因 Tab 缩进问题中途放弃（revert 了文件），下次用 Write 工具重写整个函数

### 下次会话建议
- ✅ ~~走势归因分析~~ — 已完成 2026-06-20
- ✅ ~~盘前建议仓位管理~~ — 已完成 2026-06-20
- ✅ ~~行业双表合并~~ — 已完成 2026-06-20
- ✅ ~~理杏仁并行化~~ — 已完成 2026-06-20
- ✅ ~~海外数据降级JSON缓存~~ — 已完成 2026-06-20
- ✅ ~~health_check磁盘/内存监控~~ — 已完成 2026-06-20
- ✅ ~~--from 校验~~ — 已完成 2026-06-20
- 公告深研管线不稳定 — 排查 Dashboard 滞后触发条件
- catalyst_tracker C 轨确认加分 — 走势确认后提升 C 轨排名
- 情绪跟踪三源合并 — 调研/互动/业绩信号优先级消解

## LLM 输出全链路校验体系建设 (2026-06-17)

### 背景

发现 advice 精选标的中，DeepSeek 融资催化被映射到东风汽车(600006)、华能水电(600025)——与催化逻辑零关系。追踪发现 catalyst_screen.py 的 stock_maps 全部 65 个映射均为 `method=llm_direct`（LLM 直接猜测），零校验通过。

扩展到全项目审计：35 个 LLM 调用点分布在 15+ 个文件中，**9 个高风险点零校验**。

### 审计发现的全部风险点

| 风险 | 文件 | 问题 |
|:---:|------|------|
| 🔴 | `catalyst_screen.py` | stock_maps 65条全部 llm_direct，LLM 不知道600006=东风汽车(商用车) |
| 🔴 | `analyze_zsxq.py` | Haiku tickers + Sonnet related_stocks 零校验 |
| 🔴 | `primary_synthesis.py` | 四源交叉 consensus_themes stocks 零校验 |
| 🔴 | `feval.py` Δ | code 只交叉 feed 文本（可能漏），signal 文本无验证 |
| 🟡 | `_run_advice.py` | `_validate_code_names` 无效代码只 warn 不拒绝 |
| 🟡 | `analyze_wechat.py` | Haiku/Sonnet 原始输出无校验，仅引擎层有 `^\d{6}$` |

### 修复方案：四层防御

**第一轮：catalyst_screen 专项**

| 层 | 做什么 | 效果 |
|:--:|------|------|
| L0 数据层 | `_load_multi_map_filtered()` 过滤覆盖率>5%的概念 | DeepSeek概念(761只) + 52个噪音概念被排除 |
| L1 Prompt层 | `_build_stock_context()` 注入名称+主业+FEV | LLM 看到候选标的的行业信息，不再盲猜 |
| L2 校验层 | `_validate_stock_mapping()` 行业一致性+FEV+名称三验 | 不匹配 → rejected，不入库，不进入下游 |
| L3 审计层 | `_audit_stock_maps()` + Dashboard 指标 | 映射质量实时可见 |

**第二轮：全链路校验基础设施**

- 新建 `llm_validator.py`（6个函数，共享校验层）
- 5 个高风险文件接入：analyze_zsxq / primary_synthesis / feval / _run_advice / analyze_wechat
- `_run_advice.py` 无效代码从 warn 升级为 remove（直接删除整行）
- Dashboard 新增 `🤖 LLM输出质量` 行（当日 187/196 有效, 4.6%）
- output_audit 新增 LLM 输出质量检查项

### 关键经验

1. **LLM 输出必须有机模校验层**：LLM 定性 + 机械定量 = 防幻觉底座。catalyst_screen 修了 4 层才算完整。
2. **共享基础设施 > 各文件自造轮子**：`llm_validator.py` 替代了原本分散在 5+ 个文件中的零散校验逻辑。
3. **概念覆盖率是数据质量的先行指标**：multi_concept_map 中 53 个概念覆盖率>5%，全是噪音。与 防混锅铁律 的富集比阈值共用同一逻辑。
4. **减持信号必须带比例判断烈度**：feval.py prompt 加了比例分档规则 + 机械校验（减持+正分→clamp）。
5. **管线自检必须按日期精确匹配**：output_audit 之前回退到昨天文件就标 ✅，修复后只检查目标日期文件，过期标 ⚠️。
6. **C轨 llm_direct 全部不可靠**：catalyst_screen 的 stock_maps 全部 65 条都是 llm_direct（LLM 不知道600006是做什么的）。已全量过滤，回退到 FEVΔ 补位。
7. **Windows 任务计划 + Claude Code cron 双保险**：防止管线漏跑。5 条管线 + 08:58 星球末轮补充。

## G-Factor 全量部署 (2026-06-15)

**状态**: ✅ 224 只全量评分入库，双轨筛选器就位，档案集成完成，advice 三轨化升级。

**关键数字**:
- G1≥7: 60 只 (27%), G4≥7: 54 只 (24%), G2≥7: 3 只 (1%), G3≥7: 2 只 (1%)
- FEV≥14: 8 只 vs G任一≥7: 91 只 → 交集仅 2 只 → 互补性验证通过
- G2/G3 稀疏根因: catalyst_signals 与 financial_indicators 交集仅 7 只，zsxq 代码格式不统一

**advice 报告升级 (12 commits)**:
- ChokeMap 评分差异化 (6-10分 + ±Δ对比) + 三轨拆三列 HTML width:100% 表
- W1 信号改为 7 列铺满表 (标签/烈度/周期/产业链传导/来源)
- 精选标的加 📌F📌G📌Δ 轨标签列
- STOCK_CONTEXT 注入 G-Factor 四维数据
- 昨日取最近交易日 (跳过周末)
- LLM 寒暄去除 + 日韩诚实化

**架构**: 格雷厄姆轨 (FEV) + 费雪轨 (G-Factor) + 事件轨 (Δ) 三轨并行。

**待解决**:
- G2/G3 数据稀疏
- 美股行情 EM API 频繁断连 (不影响 A 股)
- Obsidian 需关「可读行宽」才能看到 width:100% 效果

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

## 催化筛查管线（2026-06-12 建成）

### 核心文件
- `daily_review/catalyst_screen.py` — Haiku 四维提取 + Sonnet 交叉验证 + 三级标的映射
- `daily_review/catalyst_tracker.py` — 14 天生命周期跟踪，走势交叉确认
- `daily_review/store.py` — `catalyst_signals` / `catalyst_stock_map` 两张表
- `orchestrator.py` — pre pipeline 第 9/10 步

### 盘前必须跨日加载
核心催化往往在前一晚 22:00 后的星球帖中出现。catalyst_screen 已改为自动加载今天+昨天双日数据。如果以后加新的信息管线，盘前模式必须照此处理。

### 多概念交集（Sheet1）的适用边界
集群清晰的日子（如有色/军工/PCB 各自清楚）多概念几乎无增量。价值在集群模糊的日子——当单概念看过去全是 1-2 只、看不出模式时，多概念可能揭示「这批票共同拥有的第二概念」。日常用单概念（Sheet2），模糊时切多概念。

### Haiku 提取的不一致性
同一批数据两次跑，氧化钇有时抓到（#3, 66分）有时漏。Haiku 变异性约 10-20%。缓解方向：① 调大 batch_size ② 对高价值源（星球）跑两轮取并集 ③ 对 supply_shock/price_spike 类型做 prompt 加权。Phase 5 迭代时解决。

### catalyst_tracker 信号阈值
走势确认：涨停 +3 分 / 涨>5% +2 分 / 放量(量比>2 且上涨) +1 分 / 连续出现在强势池 +1 分。累计 ≥3 分视为确认。阈值可调。

### 概念父子层级维护
`CONCEPT_HIERARCHY` 在 config.py。349 个概念中已有约 65% 归入 18 个父级，其余自动独立。新增同花顺概念时检查是否需要补映射。
- `_pick_primary_tag` 非 CONCEPT_UNIVERSE 标签评分最初 +100（优先标准概念），修正为 +200（优先小众品种标签），因具体品种比泛概念更有信息量

## 运行命令

```powershell
# 框架零：统一调度
python orchestrator.py close                             # 收盘流水线
python orchestrator.py pre                               # 盘前流水线
python orchestrator.py pre --dry-run                     # 仅打印计划不执行
python orchestrator.py pre --from summary                # 从复盘摘要步骤恢复
python orchestrator.py bom                               # BOM产业链日更
python orchestrator.py collect                           # 仅数据采集
python orchestrator.py list                              # 列出所有流水线
run_close.bat / run_pre.bat / run_bom.bat                # 一键启动（Task Scheduler 用）

# 框架零：仪表盘
dashboard\run_dashboard.bat                             # 启动 Web 仪表盘 (http://localhost:8501)
streamlit run dashboard/app.py                          # 或直接用 streamlit 启动

# 框架一：每日复盘
python daily_review/run.py                              # 今天复盘
python daily_review/run.py --date 2026-05-12            # 指定日期
python daily_review/run.py --scan / -s                  # 全市场扫描
python daily_review/run.py --report / -r                # 研报采集
python daily_review/run.py --earnings / -e              # 盈利预测选股
python daily_review/run.py --cross / -x                 # 知识星球×盈利预测交叉验证
python daily_review/run.py --zsxq                       # 同步知识星球帖子
python daily_review/run.py --list / -l                  # 查看自选股

# 框架二：每日数据源采集
python daily_review/daily_collect.py                    # 全部10源补到今天
python daily_review/daily_collect.py --source news,research  # 指定源
python daily_review/daily_collect.py --days 30               # 回补30天
python daily_review/daily_collect.py --status                # 查看采集状态

# 定时任务（Windows Task Scheduler）
AStock_DailyReview          # 每日复盘 @ 17:50
morning_advice              # 盘前流水线 @ 5:00
BOM_Daily_Analysis          # BOM产业链每日分析 @ 18:30
```

## 项目结构

- `daily_review/` — 框架一：每日复盘系统
- `daily_review/collectors/` — 框架二：10源基本面采集
- `a-stock-data/` — A股数据源 SKILL v2.1
- 框架三（待建）：量化回测模型

### daily_review 架构

```
run.py                    # 主入口，编排 9 阶段流水线
config.py                 # 自选股池、参数、阈值、概念字典
data.py                   # 数据抓取层
engine.py                 # 分析引擎 facade
  engine_market.py        #   大盘/风格/行业/北向/外围
  engine_themes.py        #   题材词频/分级/三池合并/审美
  engine_sentiment.py     #   情绪面/连板梯队/四维分类
  engine_stocks.py        #   个股扫描/基本面/FEV三脚凳
  engine_advice.py        #   交易建议生成
  engine_focus.py         #   聚焦池/综合评分
report.py + report_sections.py  # Markdown 报告渲染
store.py                  # SQLite 持久化
llm.py                    # Haiku 催化摘要
_run_critique.py          # 复盘后批判脚本
```

### morning_advice.bat 流水线（6 步，05:00 触发）

```
Step 1: daily_collect.py          # 补全今日数据源（含微信公众号采集）
Step 2: analyze_wechat.py         # 公众号两阶段 AI 分析
Step 3: review_summary.py         # 生成复盘摘要
Step 4: track_recommendations.py  # 追踪历史推荐标的
Step 5: morning_intel/run_morning.py --phase pre  # 盘前解读
Step 6: _run_advice.py            # SDK 直调生成盘前建议
```

**设计原则**：prompt 模板只放指令框架，数据由 Python 预取注入 `%%PLACEHOLDER%%`。**禁止在 prompt 模板中写「读取文件」「运行命令」等指令**——LLM 无法执行代码或访问文件系统。

### dashboard 仪表盘

```
dashboard/
  app.py                      # Streamlit 首页
  pages/
    2_📊_报告浏览.py           # 历史报告浏览
    3_🚀_流程触发.py           # 手动触发流水线
    4_📈_监控状态.py           # 数据新鲜度监控
    5_🔍_个股查询.py           # 个股行情/深度分析
```

设计原则：dashboard 是对现有模块的**只读消费层**，不修改下层代码。所有数据通过 `data_bridge.py` 统一访问。

### 数据流

所有行情数据经 `data.py` 统一抓取（腾讯/mootdx/akshare/同花顺/百度/东财），`engine*.py` 做分析，`report.py` 渲染 → `reports/review_YYYY-MM-DD.md`。每天 17:50 触发 `run_review.bat`，次日 5:00 触发 `morning_advice.bat`。

## 技术栈

- Python 3.13 / Windows / PowerShell
- 依赖：mootdx, akshare, pandas, stockstats, requests, anthropic
- 数据源：mootdx（K线）、腾讯财经（行情/PE/PB）、akshare（研报/行业/龙虎榜）、同花顺（题材/北向）、百度（概念/资金流）、东财（外围）
- 持久化：SQLite（`daily_review/data/review.db`）
- 输出：Markdown 报告（`daily_review/reports/`）
- LLM：Claude Haiku 4.5（催化摘要 + 盘前建议），需 `ANTHROPIC_API_KEY`

## 常见坑

- Windows 控制台 GBK 编码：脚本顶部加 `sys.stdout.reconfigure(encoding="utf-8")`
- mootdx DataFrame datetime 索引：用 `reset_index(drop=True)` 避免重复列
- 腾讯行情 API 超时：个股批量查询需分批（batch_size=30）+ 0.3s 间隔
- 北交所 920xxx：腾讯 API 不支持，需标注无数据
- akshare 接口不稳定：行业排名等偶尔返回空表，需 try/except
- akshare 调用会**挂死**：所有 `ak.*` 须走 `data._ak(lambda: ak.xxx(...))` 薄封装（timeout=12，守护线程），**已全覆盖 22/22**。新增 `ak.*` 调用一律走 `_ak`，勿裸调
- GateGuard 阻断时：陈述四事实后重试，不要跳过 hook
- **LLM prompt 模板不能写「运行命令」「读取文件」等指令**——LLM 无法执行代码或访问文件系统，会静默编造数据而不报错
- DeepSeek API 作为 Claude 后端时**必须禁用 extended thinking**：`thinking={"type": "disabled"}`，否则返回 ThinkingBlock 导致解析失败
- Task Scheduler 运行时**没有用户环境变量**：`ANTHROPIC_AUTH_TOKEN` 需从 `~/.claude/settings.json` fallback 读取

## 知识星球导出流程

1. `python run.py --zsxq` 增量同步新帖子到 SQLite
2. `python zsxq_collector.py export --from 2026-01-29 --to {当天}` 全量导出到单个 md
3. 输出 `reports/zsxq_{当天}.md`，覆盖式生成

## Morning Intel 设计原则

1. **盘中分析：嵌入现有调度，不新增轮询** — 定时全量分析噪音>信号
2. **题材判定：≥2 天观察期 + 子环节粒度** — 首日一律 active，连续 ≥2 天同向才变更
3. **交叉对照匹配：子环节名 vs 盘后题材名，括号拆开** — `光模块(CPO)` → 分别试 `光模块` 和 `CPO`
4. **模型分工** — interpret(Sonnet深度)、spot_check(Haiku扫描)、daily_brief(纯组装不调LLM)
5. **数据契约** — morning JSON 三阶段核心，validate 写 YAML frontmatter 供 Obsidian Dataview

## 数据管线自检

`daily_review/health_check.py`，五维自动检查：
- 日期对齐：FEV/Δ/feeds 的 MAX(date) 是否匹配
- 数据覆盖：FEV vs Δ 交集、Δ 为空但 feeds 有数据 → LLM 打分失败告警
- 数据合理性：FEV 0-30 范围、Δ -10~+10、无全 0/满分异常
- 链路完整性：advice 报告存在+非空、无残留 %%PLACEHOLDER%%
- 上游数据源：星球最新帖子 >24h → 告警、各 feed 文件是否存在

## 3分钟微信提醒模块

AskUserQuestion 3 分钟无响应 → PushPlus 微信推送。4 个文件：`_hook_remind.py` / `_remind_pending.py` / `_hook_cancel_remind.py` / `_hook_debug.log`。Token: `9cdb736206654981a8b230bee39ee56d`，Topic: `morning_intel`。ECC 插件接管 hook 系统后，settings.json 的 hooks 不生效，当前手动后台进程模式可用。详见 [[3min-ask-reminder]]。

## sys.path 时序错误 → ModuleNotFoundError 但 import module 正常

**现象**：`python daily_review/_run_advice.py` 报 `ModuleNotFoundError: No module named 'daily_review'`，但 `python -c "import daily_review._run_advice"` 正常。

**原因**：`sys.path.insert(0, str(BASE))` 在第 18 行，`from daily_review.roles import ...` 在第 12 行——path 操作在 import 之后。脚本直接运行时，Python 只用脚本目录 + cwd 搜索包，`daily_review` 作为包找不到。module 方式运行时 Python 自动加了父目录。

**修复**：所有 `sys.path` 操作移到本地包 import 之前，用 `BASE.parent`（项目根目录）而非 `BASE`（脚本目录）。commit: `5d5cbe5`。

## 五维信息源框架搭建（2026-06-13）

**数据源踩坑：**
- 东方财富研报 HTTP API（`reportapi.eastmoney.com/report/list`）一次 GET 拉全市场，比 akshare 逐只调快 100 倍
- akshare `stock_research_report_em` 只能逐只调，无全市场接口
- 互动易 72 只逐只调需 ~360s，超时设 600s
- 公告采集：`akshare.stock_notice_report` 有 `symbol="全部"` 全市场模式，但 `stock_research_report_em` 没有

**SQLite 踩坑：**
- `risk_factors` Python list 需 `json.dumps` 序列化才能存 SQLite（Error binding parameter 17: type 'list' is not supported）
- 公告表 `name` 字段全为空，需从 `data.fetch_stock_quotes` 补名称
- f-string 中 `{moc_name}` 需双写 `{{moc_name}}` 转义，否则提前求值报 NameError

**过滤规则迭代（4 轮）：**
1. 初始 SKIP_TYPES 35 个关键词 → 通过率 22%
2. 加 SKIP_PATTERNS 28 个正则（会议决议/可转债/套保/授信等）→ 12.8%
3. 加"异常波动/权益分派/现金管理"→ 10.8%
4. 综合收紧（减持/增持/回购结果 + 猎场分级）→ 6.1%
   净效果：2,199 → 99（95.5% 降噪），同时净删 100 行代码

**超时设置：**
- 公告深研 33 条 LLM 调用需 ~15 分钟 → 设 1200s（300s 不够）
- 互动易 72 只逐只调需 ~6 分钟 → 设 600s（300s 不够）
- daily_collect 执行顺序：用 `sorted(key=lambda s: ...)` 确保公告→deep_read 顺序

**公告深研 21 天全量统计：**
- 2,775 条精读，37 条 ≥60（1.3%），3 条 ≥70
- 领域分布：算力硬件 21(57%) + 机器人 7(19%) + 新能源 4 + 化工材料 4
- ≥60 组 5 只后续走势全部跑输大盘（均值 -15.3% vs 上证 +2.3%），说明评分衡量"逻辑质量"非"短期涨跌"
- 公告 ≥60 vs 研报档案：0% 重叠，两条腿完全互补

**猎场分级效果：**
- 一级（算力硬件+新能源）→ 1,031 只，跑完整 deep_read
- 二级（机器人+化工材料）→ 省 37% LLM 成本，≥60 命中全部来自一级猎场
- 机器人和化工材料的催化主要来自产业链调研而非公告

**仪表盘：**
- `_dashboard.py` 自动生成，中文标签 + emoji + 可操作警报
- Obsidian Homepage 插件设为首页
- 每日 daily_collect 末尾自动生成并同步到根目录

## 标题党事故（2026-06-14 审计发现并修复）

**问题**：audit 发现 `announcement_deep_read` / `research_deep_read` / `_analyze_industry` 三条 LLM 管线，LLM 实际只读到标题+元数据，从未见到原文正文。

**根因**：`ann_full_text = a.get("title", "")` 把标题当全文。东财 API 只返回元数据，正文在 PDF 里，系统没有 PDF→文本提取层。

**修复**（3 个 commit，233 行新增）：
1. `d42cf90` — 新建 `pdf_utils.py`（pdfplumber 提取 + UA 池 + 清洗），三条管线接入 PDF 正文
2. `492a89b` — 研报 PDF 新格式 (`h3_base64`) 部分 404 → 加 HTML 详情页降级 + URL 修复
3. `e898e2f` — `_analyze_industry` SQL 漏选+漏传 `info_code` 导致双降级全失败

**验证**：重跑 33 条 deep_read，Haiku 提取从全 None → 具体数字；53 行业月报 435/800 篇缓存正文 (54%)。

**教训**：
- 代码正确 ≠ 数据正确。必须抽查 LLM 实际收到的 prompt 内容
- 共享层缺失（PDF→文本）会导致所有下游管线同时出问题
- 多级降级的每个参数必须在所有调用点传齐，缺一个 = 降级链断裂
- 输出有数字 = 正文生效，全是定性描述 = 大概率标题党

**pdf_utils.py 使用方式**：
```python
from pdf_utils import download_announcement_pdf, download_report_pdf

# 公告 PDF（需 art_code，从 URL 提取 ANxxxx）
text = download_announcement_pdf(art_code)

# 研报 PDF（需 pdf_url + info_code，支持 PDF→HTML 降级）
text = download_report_pdf(pdf_url, info_code)
```
- 公告截断 4000 字（取头尾各 2000），研报 3000 字
- 扫描件 PDF 自动返回 None（pdfplumber 对图片返回空）
- 正文缓存到 DB：`store.save_announcement_content` / `store.save_report_body_text`
- DB 表变更：`announcements` 加 `art_code`+`content`，`research_reports` 加 `body_text`+`info_code`，`industry_reports` 加 `body_text`
- 31910 条历史公告已回填 `art_code`

## 标题党验证 & 后续修复（2026-06-14）

**HTML 降级是生命线**：实测 10 篇个股研报 PDF 全挂（东财 `h3_base64` 新格式 404），HTML 降级 100% 救回，每篇 652-2488 字。HTML 降级不是 nice-to-have，没了它个股研报管线直接归零。改 `pdf_utils.py` 时绝对不能动 HTML 降级路径。

**正文生效效果对比**：
- 修复前（仅有标题）："原因**可能**在于…" — LLM 在用常识猜
- 修复后（有正文）："AI收入突破1.3亿同比+90%" / "资产负债率6.62%、经营性现金流1.06亿" — 具体数字做论据

**`sqlite3.Row` 陷阱**：`conn.row_factory = sqlite3.Row` 返回的 Row 对象没有 `.get()` 方法。写脚本时用 `[dict(r) for r in conn.execute(...)]` 统一转换最安全。

**`obsidian_research.py` upsert bug**：`new_sig` 字符串拼接了 `## AI 投资逻辑`，同时 `## AI 投资逻辑` 段又单独 replace，导致每次 upsert 多一个标题（15 个档案受影响，最多重复 4 次）。修复：`new_sig` 不拼接投资逻辑标题，让单独 replace 段处理。

**测试脚本模式**：`_test_pdf_body.py` 四段式 — DB 覆盖率统计 → 三管线逐一抽样 → 区分缓存/下载/降级来源 → 汇总。不调 LLM、不改 DB，纯验证数据链路。后续新增信息源可用同样模式。

## 名称→代码双通道提取 (2026-06-15)

**问题**：星球帖子大量使用股票名称不加代码（"炬光科技授权台积电CPO核心IP"不写688167，"罗博特科"不写300757）。全项目7文件14处只用 `\b\d{6}\b` 正则提取，名称全漏。炬光科技6/15 zsxq被提及12次，但全管线认为它不存在。

**根因链**（4层卡点）：
1. `_extract_codes_from_feeds` 只用正则抓6位代码 → 名称不命中
2. 大文件经Haiku摘要截断 → 名称被丢弃（zsxq 134KB→摘要2833字）
3. Delta评分每文件只取前3000字符 → 最新帖子在末尾被截断
4. 原始文件扫描如果用全量代码提取 → 代码数从60暴涨到800+，LLM消化不了

**修复**：
- `data.py` 新增 `extract_codes_from_text()` — 正则+全名反向匹配双通道
- `data.py` 新增 `_load_name_to_code_map()` — stock_codes.json 名称→代码反向索引
- `_extract_codes_from_feeds` 对原始大文件只做名称匹配（不做正则），精准补漏不膨胀
- Delta prompt 注入名称→代码速查表，zsxq大文件用名称反查摘录上下文
- catalyst_screen Haiku提取prompt同样注入名称速查
- 全项目6文件14处统一替换

**性能**：正则提取中文序列→字典查找，O(文本长度)，<1秒完成。

## 盘前建议防未来函数 (2026-06-15)

**问题**：`_inject_stock_context` 注入的 `chg_pct` 来自实时行情API。15:15跑盘前建议时，LLM看到厦门钨业+10%、中国巨石+10%——用收盘价推盘前票=未来函数。

**修复**：`chg_pct` 恒为0。盘前不知道涨跌，不应注入当日涨跌幅。经验证：去掉chg_pct后精选结果发生变化（兴发/寒武纪出局，拓普/紫金入局）。

## 三轨席位制上线 (2026-06-15)

**改动**：G-Factor从贴标签升级为独立3席（G轨），与FEVΔ轨（5席）、催化轨（2席）并列。G_composite = G1×2 + G2×2 + G3×1 + G4×1 + G2Δbonus。

**验证**：G轨选出了中际旭创(G=44)、中芯国际(G=33)、寒武纪(G=31)——纯FEVΔ排名均进不了前5。验证了格雷厄姆+费雪双轨互补的价值。

---

## 逻辑×走势双轨验证上线 (2026-06-21)

### 背景

/fact-debate 辩论「要不要新建自下而上框架」→ 结论：不另起炉灶，增强现有 `_chain_heat` 表。在现有板块→层级1→层级2→标的四列上加三列（走势信号/预期差/综合判断），一张表贯通逻辑侧和走势侧。

### 三条操作备忘

**③ 数据新鲜度是实时面板的隐形杀手**：三个 engine（sector_rotation/market_rhythm/similar_days）已写好但依赖手动导入的 `sector_rotation_log`，数据停在 2026-05-27。`commonality_cache` 生成脚本在 `_archive/` 里但没接入管线。功能在，数据死。任何 Dashboard 新段落的 checklist：**这个数据源是谁在刷新？多久刷新一次？**

**④ 大概念的噪音需要双重阈值**：锂电池概念 593 只成分股，8 只涨停 = 1.3%，不算热但绝对数触发了阈值。解法：比率阈值（10%）管小概念 + 绝对数阈值（≥10只）管大概念，且概念必须在 baseline 中才纳入。走势信号与链段的重叠阈值 ≥2 只股票才算有效匹配，避免单只股票的多标签噪音污染链段级别信号。

**⑤ 不可公度的维度不合并——分类代替加权**：共振/预期差/走势先行/过滤 是四个离散分类，不是一个连续分数。读者看到四个象限自行交叉判断。遵循 CLAUDE.md 双轨排名原则「不可公度的维度不要强行加权合并」。
