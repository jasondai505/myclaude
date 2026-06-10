# 宏观事件分析框架 + 边际变化跟踪 — 规划文档

> 基于 Alpha派 skill 理念，结合 myclaude 现有系统能力的集成方案
> 2026-06-10

---

## 一、宏观事件分析框架

### 1.1 核心理念（来自 Alpha 派）

六步结构化框架：
1. **关键变量** — 驱动事件演化的 3-7 个核心变量，含当前读数与关键阈值
2. **传导路径** — 从事件冲击到资产定价的因果链，标注时滞与放大/衰减
3. **历史类似案例** — 2-4 个可比案例，含量化资产回报与异同分析
4. **情景推演** — 基准/乐观/悲观三情景 + 概率权重
5. **交易表达** — 可执行组合，含标的/方向/仓位/入场/止损
6. **失效条件** — 3-5 个可量化失效信号，附退出规则

### 1.2 现有系统能力盘点

| 能力 | 已有模块 | 覆盖度 |
|------|---------|--------|
| 美股指数实时 | `data.py:fetch_global_markets()` | ✅ |
| 美股板块异动 | `data.py:fetch_us_movers()` | ✅ |
| 日韩早盘 | `data.py:fetch_kr_jp_markets()` | ✅ |
| 港股指数 | `data.py:fetch_global_markets()` | ✅ |
| 外围→A股映射 | `config.py:OVERSEAS_MAP` | ✅ 静态 |
| 外围催化摘要 | `daily_review/llm.py` | ⚠️ 仅 20 字 |
| 北向资金 | `engine_market.py:analyze_northbound()` | ✅ |
| 供应链映射 | `morning_intel/supply_chain.py` | ✅ 5题材 |
| 宏观数据 | `collectors/` | ❌ 缺失 |
| 历史案例库 | — | ❌ 无 |
| 情景推演 | — | ❌ 无 |
| 交易表达 | `_run_advice.py` 精选标的 | ⚠️ 无宏观驱动 |

### 1.3 集成方案

#### 方案 A：Claude Code skill `macro-event`（推荐先做）

**定位**：交互式宏观事件分析工具。用户描述事件 → LLM 按六步框架输出完整报告。

**优势**：
- 纯 LLM 驱动，不依赖新数据源
- 与 `fact-debate` skill 互补（一个攻击论点，一个推演宏观）
- 可在 morning_advice 前手动触发，也可在检测到重大宏观事件时自动调用
- WebSearch 能力可查历史案例（如 Exa MCP）

**触发词**：`宏观分析`、`宏观事件`、`关税分析`、`加息分析`、`地缘分析`

**输入**：
- 宏观事件描述（必填）
- 关注资产类别（选填，默认 A股+人民币+黄金）
- 地域范围（选填，默认中国市场）

**输出**：六步完整 Markdown 报告

#### 方案 B：`engine_macro.py` 自动化评分（后续做）

**定位**：流水线中的自动化宏观评分模块。在 advice 生成前检测重大宏观事件并注入评分。

**核心输出**：`macro_score`（0-100），含风险等级 + 关键变量偏离度

**新增数据源需求**：
| 指标 | 数据源 | 难度 |
|------|-------|------|
| 美债 10Y 收益率 | akshare | 低 |
| 美元指数 DXY | akshare | 低 |
| VIX 恐慌指数 | akshare | 中 |
| WTI 原油 | akshare | 低 |
| 伦敦金 | akshare | 低 |
| 离岸人民币 | akshare | 低 |

#### 方案 C：`morning_advice` 提示词增强（低成本快赢）

在现有 `claude_prompt.txt` 中新增「宏观事件推演」段落，让 LLM 在生成建议时主动识别并推演当日重大宏观事件的传导路径。不新增代码模块，只改 prompt。

### 1.4 实施优先级

| 优先级 | 事项 | 类型 | 预估 |
|--------|------|------|------|
| P0 | `macro-event` Claude Code skill | 新建 skill | 小 |
| P1 | 宏观指标数据源（6 个指标） | 扩展 `data.py` | 中 |
| P2 | `engine_macro.py` 评分模块 | 新建 module | 中 |
| P2 | claude_prompt.txt 宏观段落增强 | 改 prompt | 小 |

---

## 二、边际变化跟踪

### 2.1 核心理念（来自 Alpha 派）

> 每条变化记录必须与历史记录形成可追溯的对比链——**不允许孤立记录**。标明"相较何时何值，本次变成了什么"。首次记录标注为"首次记录"。

核心字段：
| 字段 | 说明 |
|------|------|
| 日期 | 信息披露日期 |
| 主题 | 所属跟踪主题（如"海外收入占比"） |
| 变化方向 | 边际向好 / 边际下滑 / 符合预期 |
| 变动内容 | 具体数值或事件描述 |
| 变动原因 | 与上次记录的具体对比（前次数值 vs 本次数值） |
| 来源 | 信息出处（纪要/公告/研报） |
| 对比来源 | 作为对比基准的历史记录 ID |

### 2.2 现有数据源中可提取的边际变化

| 采集器 | 可提取变化 | 状态 |
|--------|----------|------|
| **earnings** | 业绩预告方向变化、增速变化 | 需 diff |
| **eps_forecast** | 一致预期 EPS 上修/下修 | 需历史对比 |
| **financials** | ROE/毛利率/净利率趋势 | 需多期对比 |
| **inst_survey** | 调研频次变化、机构参与数 | 需计数对比 |
| **announcements** | 重大事件（并购/重组/增减持） | 需事件检测 |
| **interactions** | 互动易问答密度变化 | 需计数对比 |
| **research_reports** | 研报覆盖变化、评级调整 | 需 diff |
| **limit_up_analysis** | 涨停逻辑变化 | 已有存储 |
| **WATCHLIST** | 自选股调入/调出 | 需 diff config |

### 2.3 设计方案

#### 2.3.1 新 SQLite 表

```sql
CREATE TABLE IF NOT EXISTS marginal_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    theme TEXT NOT NULL,
    direction TEXT NOT NULL,          -- 边际向好 / 边际下滑 / 符合预期
    content TEXT NOT NULL,
    previous_value TEXT,
    current_value TEXT,
    source TEXT NOT NULL,            -- earnings/eps_forecast/inst_survey/...
    source_detail TEXT,
    previous_record_id INTEGER,      -- 对比基准记录 ID，首次为 NULL
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### 2.3.2 新模块 `engine_marginal.py`

核心函数：
- `detect_changes(today)` — 主入口，遍历采集器+自选股
- `_diff_eps_forecast(code)` — 一致预期变化
- `_diff_earnings(code)` — 业绩预告变化
- `_diff_financials(code)` — 财务指标趋势
- `_diff_inst_survey(code)` — 调研频次变化
- `_diff_research(code)` — 研报评级/覆盖变化
- `_build_comparison_chain(code, theme)` — 对比链
- `render_marginal_report(changes, today)` — 日报

#### 2.3.3 跟踪主题定义（config.py 新增）

```python
MARGINAL_TRACKING = {
    "300750": {  # 宁德时代
        "themes": ["产能利用率", "海外收入占比", "新签订单", "毛利率"],
        "metrics": {
            "eps_forecast": True,
            "earnings": True,
            "inst_survey": True,
            "financials": ["roe", "gross_margin", "net_margin"],
        }
    },
    # ...
}
```

#### 2.3.4 流水线集成

在 `orchestrator.py` 的 `pre` 流水线中新增步骤：
```
("marginal", "检测边际变化", lambda: run_marginal(args))
```

注入 `_run_advice.py`：
```
%%MARGINAL_CHANGES%%  # 当日检测到的所有边际变化摘要
```

### 2.4 实施优先级

| 优先级 | 事项 | 预估 |
|--------|------|------|
| P0 | `marginal_changes` 表 + store.py CRUD | 小 |
| P0 | `engine_marginal.py` 核心 diff 逻辑 | 中 |
| P1 | 首批 20 只自选股 MARGINAL_TRACKING 配置 | 小 |
| P1 | orchestrator 集成 + `%%MARGINAL_CHANGES%%` 注入 | 小 |
| P2 | 全量自选股配置 + 边际变化日报 | 中 |
| P3 | Dashboard 边际变化页面 | 中 |

---

## 三、两个框架的协同关系

```
                    ┌──────────────────────────────┐
                    │     morning_advice 流水线      │
                    │   (orchestrator.py pre)       │
                    └──────────┬───────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
           ▼                   ▼                   ▼
   ┌───────────────┐   ┌──────────────┐   ┌────────────────┐
   │ macro-event   │   │ engine_macro │   │ engine_marginal│
   │ skill         │   │ .py          │   │ .py            │
   │ (交互式六步)   │   │ (自动化评分)  │   │ (边际变化检测)  │
   └───────┬───────┘   └──────┬───────┘   └───────┬────────┘
           │                   │                   │
           │     ┌─────────────┴──────────────┐     │
           │     │  %%MACRO_EVENT_ANALYSIS%%  │     │
           │     │  %%MARGINAL_CHANGES%%      │     │
           │     └─────────────┬──────────────┘     │
           │                   │                    │
           │                   ▼                    │
           │     ┌────────────────────────┐         │
           │     │  _run_advice.py        │         │
           │     │  (Claude Sonnet 推理)   │         │
           │     └───────────┬────────────┘         │
           │                 │                      │
           │                 ▼                      │
           │     ┌────────────────────────┐         │
           └────►│  fact-debate skill     │◄────────┘
                 │  (攻击性验证 advice)    │
                 └────────────────────────┘
```

**三个阶段的分工**：
1. **边际变化跟踪** → 「什么在变」（客观事实层）
2. **宏观事件分析** → 「为什么变」（驱动逻辑层）
3. **事实辩论** → 「结论对不对」（验证层）
