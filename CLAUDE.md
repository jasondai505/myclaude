# CLAUDE.md

## 用户画像

A股基本面趋势跟随型投资者。目标：搭建「基本面研究 + AI量化」工具体系。

## 项目结构

- `daily_review/` — 框架一：每日复盘系统（已完成一二期增强）
- `a-stock-data/` — A股数据源 SKILL v2.1（mootdx/腾讯/akshare/同花顺/百度/东财）
- 框架二（待建）：基本面研报收集与整理
- 框架三（待建）：量化回测模型

## 协作规则

1. **简洁直接**：回答不废话，结论先行，必要时附依据。
2. **先规划再执行**：非 trivial 任务先出计划（用 Plan 模式），对齐后再动手写代码。
3. 代码风格：无注释优先，变量名自解释；只在 WHY 不明显时加一行注释。
4. 不做多余的事：不加用户没要求的功能、不写没必要的文档。

## 技术栈

- Python 3.13 / Windows / PowerShell
- 数据源：mootdx（K线）、腾讯财经（实时行情/PE/PB）、akshare（研报/行业/龙虎榜）、同花顺（题材/北向）、百度股市通（概念/资金流）、东方财富（外围市场）
- 持久化：SQLite
- 输出：Markdown 报告

## 常见坑

- Windows 控制台 GBK 编码：Python 脚本顶部加 `sys.stdout.reconfigure(encoding="utf-8")`
- mootdx DataFrame 的 datetime 索引：用 `reset_index(drop=True)` 避免重复列
- 腾讯行情 API 超时：个股批量查询需分批（batch_size=30）+ 0.3s 间隔
- 北交所 920xxx 代码：腾讯 API 不支持，需在报告中标注无数据
- akshare 接口不稳定：行业排名等 API 偶尔返回空表，需 try/except 兜底
- akshare 调用会**挂死**（底层 requests 无 socket 超时，单请求可卡数小时；try/except 拦不住 hang，曾使 run.py 卡 9h 出不来报告）：所有 `ak.*` 调用须走 `data._ak(lambda: ak.xxx(...))` 薄封装（默认 timeout=12，内部复用 `_run_with_timeout` 守护线程，超时/异常均返 None）。**已全覆盖 22/22**（含 5 个 per-stock 循环：研报/盈利预测/股东户数/解禁/题材新闻）。新增 `ak.*` 调用一律走 `_ak`，勿裸调
