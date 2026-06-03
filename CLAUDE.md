# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 用户画像

A股基本面趋势跟随型投资者。目标：搭建「基本面研究 + AI量化」工具体系。

## 运行命令

```powershell
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
python daily_review/daily_collect.py                    # 全部10源补到今天（默认7天窗口）
python daily_review/daily_collect.py --source news,research  # 指定源
python daily_review/daily_collect.py --days 30               # 回补30天
python daily_review/daily_collect.py --status                # 查看采集状态

# 定时任务（Windows Task Scheduler）
AStock_DailyReview          # 每日复盘 @ 17:50 (daily_review/run_review.bat)
morning_advice              # 盘前流水线 @ 5:00（采集→公众号深度分析→摘要→跟踪→Claude advice）
BOM_Daily_Analysis          # BOM产业链每日分析 @ 18:30 (bom_analyzer/run_daily.bat)
```

## 项目结构

- `daily_review/` — 框架一：每日复盘系统（运行中）
- `daily_review/collectors/` — 框架二：10源基本面采集（公告/新闻/研报/互动易/业绩/调研/解禁/EPS/行业/财务）
- `a-stock-data/` — A股数据源 SKILL v2.1（mootdx/腾讯/akshare/同花顺/百度/东财）
- 框架三（待建）：量化回测模型

### daily_review 架构

```
run.py                    # 主入口，编排 9 阶段流水线
config.py                 # 自选股池(WATCHLIST)、参数、阈值
data.py                   # 数据抓取层（封装 a-stock-data）
engine.py                 # 分析引擎 facade → 6 个子模块
  engine_market.py        #   大盘/风格/行业/北向/外围
  engine_themes.py        #   题材词频/分级/三池合并/审美
  engine_sentiment.py     #   情绪面/连板梯队/四维分类
  engine_stocks.py        #   个股扫描/基本面/FEV三脚凳
  engine_advice.py        #   交易建议生成
  engine_focus.py         #   聚焦池/综合评分
report.py + report_sections.py  # Markdown 报告渲染
store.py                  # SQLite 持久化（题材跟踪+市场快照+数据源采集表）
llm.py                    # Anthropic Haiku 催化摘要（外围标的，无 key 兜底「—」）
strength.py               # 板块强弱分析
valuation.py              # 行业估值分位
models.py                 # 核心 dataclass（StockQuote/ThemeEntry/FEVScore 等）
```

### morning_advice.bat 流水线（6 步，05:00 触发）

```
Step 1: daily_collect.py          # 补全今日数据源（含微信公众号文章采集）
Step 2: analyze_wechat.py         # 公众号文章 两阶段 AI 分析（Haiku逐篇+Sonnet综合研判）→ reports/wechat_analysis_YYYY-MM-DD.md
Step 3: review_summary.py         # 生成复盘摘要
Step 4: track_recommendations.py  # 追踪历史推荐标的
Step 5: morning_intel/run_morning.py --phase pre  # 盘前解读（供应链映射+催化→标的）
Step 6: _run_advice.py            # SDK 直调生成盘前建议（claude_prompt.txt 模板，注入 %%WECHAT_ANALYSIS%%）
```

**设计原则**：prompt 模板只放指令框架，**数据由 Python 预先拉取注入 `%%PLACEHOLDER%%`**。

> **禁止在 prompt 模板中写「读取文件」「运行命令」等指令**——LLM 无法执行代码或访问文件系统，会导致模型**静默编造/幻觉**所有数据（如瞎编成交额、编造涨跌幅、错误分类个股大小盘），且不会报错。2026-05-29 事故：claude_prompt.txt 写了「阅读以下文件」但 LLM 读不到，导致 advice 中成交额编造为 8000 亿（实际 29682 亿）、寒武纪 8700 亿被标为小盘股、推荐回顾数据全部虚构。<br>
> 正确做法：Python 端读文件/调 API → 注入 `%%PLACEHOLDER%%` → LLM 只做推理。参考 `_run_advice.py` 的注入模式。

### 数据流

所有行情数据经 `data.py` 统一抓取（腾讯财经 + mootdx + akshare + 同花顺 + 百度 + 东财），`engine*.py` 做分析，`report.py` 渲染 Markdown → `reports/review_YYYY-MM-DD.md`。

每天 17:50 Task Scheduler 触发 `run_review.bat`，次日 5:00 触发 `morning_advice.bat`。

## 协作规则

### 防幻觉铁律（CRITICAL — 任何 prompt 模板变更前必读）

LLM 遇到它不知道的数据时不会报错，而是静默编造。三道防线：

1. **注入覆盖**：prompt 中出现的所有股票代码，其市值/PE/PB/名称等客观数据必须通过 `%%PLACEHOLDER%%` 注入。LLM 训练数据里的市值是过时的，必须用实时 API 覆盖。
2. **代码全量提取**：`_extract_codes_from_feeds` 必须从所有注入源（recap/review/zsxq/wechat/news/industry）提取代码，不能遗漏任何 feed。
3. **输出校验**：LLM 输出中的股票名称-代码匹配、大小盘分类、估值区间等可验证字段，Python 端须用行情 API 逐条校验，不匹配的自动修正。

> 历史事故：2026-05-29 prompt 含「读取文件」指令→LLM 编造成交额(8000亿 vs 实际29682亿)、2026-06-03 STOCK_CONTEXT 未覆盖公众号标的→协创数据1100亿被归为小盘股。根因相同：LLM 需要的数据没注入，它就编。

### 其他协作规则

1. **深度思考**：任何任务都先想清楚再动手，不假思索直接写代码。
2. **追求简洁**：能写 10 行别写 50 行。代码优先短小精悍，方案优先简单直接。
3. **精准修改**：只改要改的，别顺手优化、别扩大范围、别修周边代码。
4. **目标驱动**：始终回到最初需求，中途不跑偏、不画蛇添足。
5. 回答风格：简洁直接，结论先行，必要时附依据。
6. **先规划再执行**：非 trivial 任务先出计划（用 Plan 模式），对齐后再动手。
7. 代码风格：无注释优先，变量名自解释；只在 WHY 不明显时加一行注释。

## 技术栈

- Python 3.13 / Windows / PowerShell
- 依赖：mootdx, akshare, pandas, stockstats, requests, anthropic
- 数据源：mootdx（K线）、腾讯财经（实时行情/PE/PB）、akshare（研报/行业/龙虎榜）、同花顺（题材/北向）、百度股市通（概念/资金流）、东方财富（外围市场）
- 持久化：SQLite（`daily_review/data/review.db`）
- 输出：Markdown 报告（`daily_review/reports/`）
- LLM：Anthropic Claude Haiku 4.5（外围催化摘要 + 盘前建议），需 `ANTHROPIC_API_KEY`

## 常见坑

- Windows 控制台 GBK 编码：Python 脚本顶部加 `sys.stdout.reconfigure(encoding="utf-8")`
- mootdx DataFrame 的 datetime 索引：用 `reset_index(drop=True)` 避免重复列
- 腾讯行情 API 超时：个股批量查询需分批（batch_size=30）+ 0.3s 间隔
- 北交所 920xxx 代码：腾讯 API 不支持，需在报告中标注无数据
- akshare 接口不稳定：行业排名等 API 偶尔返回空表，需 try/except 兜底
- akshare 调用会**挂死**（底层 requests 无 socket 超时，单请求可卡数小时；try/except 拦不住 hang，曾使 run.py 卡 9h 出不来报告）：所有 `ak.*` 调用须走 `data._ak(lambda: ak.xxx(...))` 薄封装（默认 timeout=12，内部复用 `_run_with_timeout` 守护线程，超时/异常均返 None）。**已全覆盖 22/22**（含 5 个 per-stock 循环：研报/盈利预测/股东户数/解禁/题材新闻）。新增 `ak.*` 调用一律走 `_ak`，勿裸调
- GateGuard `pre:edit-write` / `pre:bash` 阻断时：陈述四事实（引用方/受影响函数/数据文件/用户指令）后重试，不要跳过 hook
- **LLM prompt 模板不能写「运行命令」「读取文件」等指令**：LLM 只能生成文本，不能执行代码或访问文件系统。会导致模型编造/幻觉数据（如编造美股涨跌幅）。正确做法：Python 端调用 API/读文件，将结果以 `%%PLACEHOLDER%%` 注入 prompt。参考 `_run_advice.py` + `claude_prompt.txt` 的 `%%US_MOVERS%%` / `%%KR_JP_MARKETS%%` / `%%OVERSEAS_MAP%%` 模式
- DeepSeek API 作为 Claude 后端时**必须禁用 extended thinking**：`thinking={"type": "disabled"}`。否则返回 `ThinkingBlock` 导致解析失败。所有 SDK 直调（`morning_intel/llm.py`、`daily_review/_run_advice.py`、`daily_review/llm.py`）均已加上
- Task Scheduler 运行时**没有用户环境变量**：`ANTHROPIC_AUTH_TOKEN` 等需从 `~/.claude/settings.json` fallback 读取。`morning_intel/llm.py` 和 `daily_review/_run_advice.py` 的 `_load_api_key()` 已实现此模式
