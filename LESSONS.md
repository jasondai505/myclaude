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
