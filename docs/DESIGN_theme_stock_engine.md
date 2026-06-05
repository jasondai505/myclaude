# 主题-标的客观映射引擎 设计文档 v2.0

> 2026-06-05 | v2.0 — 五层架构 + 双反馈闭环

## 1. 问题定义

### 1.1 当前现状

整个项目 **没有统一的「标的-主题-产业链」知识层**。四个分析模块各自选股：

| 模块 | 选股方式 | 问题 |
|------|---------|------|
| 复盘报告 `engine_themes.py` | 同花顺强势股 `reason` 字段提取题材→标的 | 覆盖窄（仅强势股），一只股票可能被归因到边缘题材 |
| 公众号分析 `analyze_wechat.py` | Haiku 从文章提取 ticker → Sonnet 凭印象综合 | **完全依赖 LLM 一次性输出**，无数据验证 |
| BOM 分析 `run.py` | LLM 拆产业链 → LLM 选龙头 → 财务数据回填 | 产业链拆解本身靠 LLM，可能遗漏核心环节 |
| 晨间情报 `interpret.py` | LLM 读新闻 → 映射到 supply_chain DB 已有标的 | 映射依赖预填数据，新主题无覆盖 |

**四个模块，四套逻辑，互不验证。标的关联质量的底线完全取决于 LLM 单次输出的运气。**

### 1.2 目标

建成 **持续进化的知识系统**，而非一次性静态索引：

1. **客观**：标的来源可追溯到具体数据源，每个关联都有据可查
2. **深度**：不止知道「哪只股票在哪个主题」，更知道「在产业链什么位置、凭什么」
3. **自适应**：通过定性信号和盘面反馈持续修正，越用越准
4. **可审计**：每次查询返回置信度 + 所有数据来源，LLM 意见单独标注

---

## 2. 五层架构总览

```
                         ┌─────────────────────────────────┐
                         │         上层消费方               │
                         │  公众号分析 / 复盘 / 晨间情报     │
                         │  BOM分析 / Dashboard / 盘前建议   │
                         └──────────────┬──────────────────┘
                                        │
              ┌─────────────────────────▼──────────────────────────┐
              │              ThemeStockEngine (查询入口)             │
              │    query(theme) → StockList{stocks, confidence,     │
              │                chain_context, deep_analysis,        │
              │                qual_signals, market_feedback}       │
              └─────────────────────────┬──────────────────────────┘
                                        │
    ┌───────────────────────────────────┼───────────────────────────┐
    │                                   │                           │
    ▼                                   ▼                           ▼
┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
│  Layer 0  │ │  Layer 1  │ │  Layer 2  │ │  Layer 3  │ │  Layer 4  │
│ 外部图谱  │ │ 产业链层  │ │ 深度挖掘  │ │ 定性修正  │ │ 盘面验证  │
│ (种子)    │ │ (骨架)    │ │ (血肉)    │ │ (信号)    │ │ (纠错)    │
├───────────┤ ├───────────┤ ├───────────┤ ├───────────┤ ├───────────┤
│券商图谱   │ │BOM 上下游 │ │护城河评分 │ │公众号提及 │ │涨跌幅联动 │
│咨询报告   │ │SC 环节    │ │财务指标   │ │星球讨论   │ │资金流向   │
│产业白皮书 │ │申万分类   │ │营收构成   │ │研报覆盖   │ │题材共振   │
│人工整理   │ │人工梳理   │ │产品线     │ │机构调研   │ │龙头确认   │
│           │ │           │ │客户结构   │ │新闻催化   │ │背离检测   │
└─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
      │             │             │             │             │
      │   种子导入   │   结构化     │   定量       │   信号加权   │   动态修正
      ▼             ▼             ▼             ▼             ▼
┌───────────────────────────────────────────────────────────────────┐
│                    统一知识库 (theme_stock.db)                      │
│                                                                   │
│  chain_map    — Layer 0→1 产业链骨架（含外部图谱版本）              │
│  concept_index — Layer 1 概念→标的（东财+同花顺+百度）              │
│  stock_depth   — Layer 2 标的深度数据（护城河/财务/营收/产品）       │
│  qual_signals  — Layer 3 定性信号日志（时间/来源/方向/强度）         │
│  mkt_feedback  — Layer 4 盘面验证日志（日期/联动/背离/确认）         │
│  confidence_log — 置信度变更历史（谁在什么时间因为什么修改了分数）     │
└───────────────────────────────────────────────────────────────────┘
      ▲                                                           │
      │          ┌────────────────────────────────────────────────┘
      │          ▼
      │  ┌───────────────────────────────┐
      │  │       反馈闭环                 │
      │  │                               │
      │  │  闭环A (慢): Layer 3 定性信号   │
      │  │    → 调整 chain_map 标的权重    │
      │  │    → 新增/降权/移除标的         │
      │  │    → 周期: 每日                 │
      │  │                               │
      │  │  闭环B (快): Layer 4 盘面验证   │
      │  │    → 验证 Layer 2 映射准确性    │
      │  │    → 标记背离、触发人工复核      │
      │  │    → 周期: 盘中实时             │
      │  └───────────────────────────────┘
      │
      └── 回写更新
```

**五层之间的关系**：
- **L0→L1**：外部图谱作为种子导入产业链骨架，人工审核后入库
- **L1→L2**：产业链每个环节锁定标的后，展开深度挖掘（为什么会在这个位置）
- **L2→引擎**：引擎查询时合并 L1(概念匹配) + L2(产业链匹配) + L3(信号加权) + L4(盘面修正)
- **L3→L1/L2** (闭环A)：定性信号持续累积，达到阈值后修正底层映射
- **L4→L1/L2** (闭环B)：盘面背离触发即时复核，确认后修正

---

## 3. 各层详细设计

> **跨市场支持**：所有表均包含 `market` 字段（`A` / `HK` / `US`）。港股和美股不参与 A 股概念板块匹配，但可纳入 L0 外部图谱和 L1 产业链层，作为产业链全景的参考标的。即使暂不交易，也需纳入分析以建立完整的产业视野。

### 3.0 Layer 0 — 外部成熟产业链图谱（种子数据）

**来源**：用户从外部获取的券商/咨询机构/产业白皮书中的成熟产业链图谱。

**格式约定**（用户提供图谱时，按此模板填写即可）：

```
产业名称: 新能源汽车
来源: 某券商产业链图谱 2026Q2
版本: 2026-06-05

上游:
  - 锂矿: 天齐锂业(002466), 赣锋锂业(002460)
  - 钴矿: 华友钴业(603799), 寒锐钴业(300618)
  - 正极材料: 当升科技(300073), 容百科技(688005)
  - 负极材料: 璞泰来(603659), 杉杉股份(600884)
  - 电解液: 天赐材料(002709), 新宙邦(300037)
  - 隔膜: 恩捷股份(002812), 星源材质(300568)

中游:
  - 电芯: 宁德时代(300750), 比亚迪(002594)
  - BMS: 均胜电子(600699), 德赛西威(002920)
  - 电机: 方正电机(002196), 大洋电机(002249)
  - 电控: 汇川技术(300124), 英搏尔(300681)

下游:
  - 整车: 比亚迪(002594), 蔚来, 理想, 小鹏
  - 充电桩: 特锐德(300001), 万马股份(002276)
  - 换电: 山东威达(002026), 博众精工(688097)
```

**入库流程**：
1. 用户提供图谱（文本/表格/图片均可）
2. 解析 → 匹配现有 `chain_map` 中是否已有该产业
3. 比对差异：新增环节 / 新增标的 / 标的冲突
4. 输出差异报告给用户确认
5. 确认后写入 `chain_map`，标记 `source=external_map`

**版本管理**：
- 每次导入带版本号 + 来源 + 导入时间
- 同一产业可叠加多个版本（不同来源交叉验证）
- 标的在多版本中均出现 → 置信度提升
- 标的仅在一个版本中出现 → 标记为待验证

### 3.1 Layer 1 — 产业链知识层（骨架）

**目标**：自上而下梳理每个板块的产业链结构。

**数据来源（按优先级）**：
| 优先级 | 来源 | 导入方式 |
|--------|------|---------|
| P0 | 外部成熟图谱 (L0) | 人工审核后直接入库 |
| P1 | BOM chain_db (已有 26 行业) | 自动导入 |
| P2 | morning supply_chain (已有 ~20 事件) | 自动导入 |
| P3 | 同花顺概念板块成员 | 按概念热度自动提取 |
| P4 | LLM 辅助拆解（新主题首次出现时） | LLM 输出→人工审核→入库 |

**表结构 `chain_map`**：

```sql
CREATE TABLE chain_map (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    industry     TEXT NOT NULL,          -- 产业名称 (canonical)
    tier         TEXT NOT NULL,          -- 上游/中游/下游/设备/材料/应用
    segment      TEXT NOT NULL,          -- 环节名称
    code         TEXT NOT NULL,          -- 6位代码
    name         TEXT NOT NULL,
    role         TEXT,                   -- 在环节中的具体角色
    source       TEXT NOT NULL,          -- external_map / bom / supply_chain / llm_draft / manual
    source_ver   TEXT,                   -- 来源版本，如 "券商图谱2026Q2" / "bom_2026-06-02"
    confidence   TEXT DEFAULT 'medium',  -- high / medium / low
    is_verified  INTEGER DEFAULT 0,      -- 是否经人工确认
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE(industry, tier, segment, code)
);
```

**新主题首次出现时的处理**：
1. LLM 输出主题 + 推断的产业链结构（标记 `source=llm_draft, confidence=low`）
2. 人工确认或外部图谱覆盖后升级为 `verified`
3. 最长 7 天未确认的 llm_draft 标记为过期

### 3.2 Layer 2 — 标的深度挖掘（血肉）

**目标**：对产业链上的每个标的，展开「凭什么它在这个位置」的定量分析。

**数据维度**：

```
每个标的的深度档案:
├── 基础信息: 代码/名称/申万行业/市值/PE/PB
├── 产业链位置: [{industry, tier, segment, role}]
├── 护城河评分 (来自 BOM): {tech, cost, scale, brand, switch, network, total}
├── 财务指标 (来自理杏仁):
│   ├── ROE (连续3年): [%, %, %]
│   ├── 毛利率: %
│   ├── 营收增速 (CAGR 3Y): %
│   ├── EPS CAGR (3Y): %
│   └── 研发费用率: %
├── 业务构成 (远期):
│   ├── 产品线: [{name, revenue_share, yoy_growth}]
│   └── 客户结构: [{name, share}]
├── 定性标签:
│   ├── 行业地位: 龙头/一线/二线/边缘
│   ├── 国产替代阶段: 已替代/替代中/待突破
│   └── 产能阶段: 满产/爬坡/规划中
└── 关联概念: [concept1, concept2, ...] (来自 concept_index)
```

**表结构 `stock_depth`**：

```sql
CREATE TABLE stock_depth (
    code           TEXT PRIMARY KEY,       -- 6位代码
    name           TEXT NOT NULL,
    industry_l1    TEXT,                   -- 申万一级行业
    moat_total     INTEGER,                -- BOM 护城河总分 0-60
    moat_detail    TEXT,                   -- JSON: 六维度分项
    roe_3y         TEXT,                   -- JSON: [y1, y2, y3]
    gross_margin   REAL,                   -- 毛利率
    rev_cagr_3y    REAL,                   -- 营收 CAGR 3年
    eps_cagr_3y    REAL,                   -- EPS CAGR 3年
    rd_ratio       REAL,                   -- 研发费用率
    biz_segments   TEXT,                   -- JSON: 产品线构成 (远期)
    tier_label     TEXT,                   -- 行业地位: 龙头/一线/二线/边缘
    substitution   TEXT,                   -- 国产替代阶段
    capacity       TEXT,                   -- 产能阶段
    updated_at     TEXT NOT NULL
);
```

**构建方式**：

```
L1 chain_map 中每个 code
    ↓
已有 BOM leaders 数据 → 直接导入护城河评分
    ↓
理杏仁 API → ROE/毛利率/CAGR（已有 bominfo.py 实现）
    ↓
LLM 辅助: 从年报/调研纪要提取 tier_label/substitution/capacity
    ↓
人工抽查: 每个行业 top-5 龙头人工确认
```

### 3.3 Layer 3 — 定性信号修正（信号层）

**目标**：用公众号/知识星球/研报中的提及信号，对标的关联度进行加减权。

**信号类型**：

| 信号 | 方向 | 强度 | 衰减 | 说明 |
|------|------|------|------|------|
| 公众号文章提及 | + | 0.3 | 7天 | 文章明确将标的与主题关联 |
| 知识星球讨论 | + | 0.4 | 5天 | 星球被视为更高质量信号 |
| 研报覆盖 | + | 0.5 | 30天 | 机构正式覆盖，置信度高 |
| 机构调研 | + | 0.3 | 14天 | 调研 ≠ 推荐，但表明关注 |
| 持续未提及 | - | 0.1/天 | 累计 | 产业链核心标的长期无人讨论→降权 |
| 负面文章/质疑 | - | 0.3 | 7天 | 明确质疑关联性或竞争格局恶化 |

**表结构 `qual_signals`**：

```sql
CREATE TABLE qual_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL,
    theme        TEXT NOT NULL,          -- 关联主题 (canonical)
    signal_type  TEXT NOT NULL,          -- wechat_article / zsxq_post / research_report / survey
    direction    TEXT NOT NULL,          -- positive / negative / neutral
    strength     REAL NOT NULL,          -- 信号强度 0-1
    detail       TEXT,                   -- 原文摘要
    source_url   TEXT,                   -- 原始链接
    signal_date  TEXT NOT NULL,          -- 信号日期
    expires_at   TEXT,                   -- 过期时间
    created_at   TEXT NOT NULL
);
```

**加权公式**：

```
qual_bonus(code, theme) = Σ( strength × decay_factor )
  where decay_factor = max(0, 1 - days_since_signal / ttl_days)
```

**自动化采集**：
- 公众号分析 (`analyze_wechat.py`) 完成后，Haiku 提取的 ticker → 自动写入 `qual_signals`
- 知识星球 daily_collect 后，帖子中的代码 → 写入 `qual_signals`
- 研报采集后，覆盖标的 → 写入

### 3.4 Layer 4 — 盘面反馈修正（纠错层）

**目标**：用真实价格行为验证主题→标的映射是否仍然有效。

**核心原理**：如果某标的被映射到主题 X，但主题 X 大涨时该标的持续不跟涨，说明映射可能失效。

**检测规则**：

```
规则1: 联动检测 (每日收盘后)
  主题板块 index_chg > +3% 
    → 检查所有映射标的 chg%
    → 标的 chg% < 板块 chg% - 2σ → 标记「背离」
    → 连续 3 次背离 → 降权 0.3，触发人工复核

规则2: 龙头确认 (每日收盘后)
  主题板块 top-N 涨幅标的
    → 检查是否在 chain_map 中
    → 若 top-3 中某只不在 chain_map → 提示「可能遗漏核心标的」

规则3: 资金流向 (每日收盘后)
  概念板块大单净流入 > 5000万
    → 检查 target_stocks 是否同步获资金流入
    → 若板块流入但标的流出 → 标记「资金背离」
```

**表结构 `mkt_feedback`**：

```sql
CREATE TABLE mkt_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL,
    theme        TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    stock_chg    REAL,                   -- 个股涨跌幅
    theme_chg    REAL,                   -- 主题板块涨跌幅
    flow_yn      REAL,                   -- 个股资金净流入(亿)
    theme_flow   REAL,                   -- 板块资金净流入(亿)
    deviation    REAL,                   -- 偏离度 (标准差)
    flag         TEXT,                   -- aligned / diverged / missing_leader
    created_at   TEXT NOT NULL
);
```

**闭环B流程**：

```
每日收盘后:
  1. 东财 concept board 涨幅数据
  2. chain_map 中所有主题的映射标的
  3. 计算联动/背离
  4. 写入 mkt_feedback
  5. 触发条件:
     - 连续 3 次背离 → PushPlus 告警 + confidence_log 降权
     - 遗漏龙头 → 自动建议新增标的
```

### 3.5 两个反馈闭环

**闭环A（慢循环 — 定性信号累积）：**
```
公众号/星球/研报 每日采集
    → qual_signals 累积
    → 每个标的的 qual_bonus 每日重算
    → 累计 bonus > 阈值 → chain_map 中该标的-主题关联 confidence 升级
    → 累计 bonus < 阈值(长期无信号) → confidence 降级 → 标记待复核
```

**闭环B（快循环 — 盘面实时验证）：**
```
每日收盘/盘中
    → 主题板块行情 vs 映射标的行情 比对
    → 背离检测 → mkt_feedback 记录
    → 连续背离 → PushPlus 告警 → 人工复核
    → 确认后 → chain_map 更新/标的移除/新增
```

---

## 4. 引擎核心

### 4.1 查询接口

```python
class ThemeStockEngine:

    def query(self, theme: str, *,
              limit: int = 20,
              min_confidence: float = 0.3) -> StockList:
        """
        输入: 主题名称
        输出: 标的列表 + 产业链全景 + 深度数据 + 定性信号 + 盘面状态
        """

    def query_multi(self, themes: list[str], **kw) -> dict[str, StockList]:
        """批量查询"""

    def enrich(self, codes: list[str]) -> list[EnrichedStock]:
        """反向: 标的→关联主题+产业链位置+深度数据"""

    def get_chain_context(self, theme: str) -> ChainContext:
        """产业链全景"""

    def import_external_map(self, spec: ExternalMapSpec) -> DiffReport:
        """L0: 导入外部图谱，返回差异报告"""

    def record_signal(self, code: str, theme: str, signal: QualSignal):
        """L3: 记录定性信号"""

    def run_market_check(self, date: str) -> MarketCheckReport:
        """L4: 运行盘面验证"""
```

### 4.2 查询算法（v2 五层融合）

```
query(theme):

1. normalize(theme) → canonical (alias_map)

2. L1 chain_match:
   chain_map WHERE industry/segment LIKE canonical
   → {code: [(tier, segment, role, source, confidence)]}

3. L1 concept_match:
   concept_index WHERE concept = canonical
   → {code: [source_ref]}

4. L2 enrich:
   对每个候选 code → stock_depth 查询
   → 附加 护城河/财务/行业地位

5. 合并 L1+L2:
   merge by code, 保留所有 source

6. L3 qual_bonus:
   对每个 (code, theme) → qual_signals 加权
   → qual_score 叠加到 base_score

7. L4 mkt_adjust:
   对每个 (code, theme) → mkt_feedback 最近 5 天检查
   → 若标记 diverged → 降权 0.2
   → 若标记 aligned → 加权 0.1

8. final_score:
   base_score × (1 + qual_bonus) × mkt_multiplier

9. rank → top-N

10. return StockList (含 source_trail 完整追溯)
```

### 4.3 输出结构

```python
@dataclass
class StockEntry:
    code: str
    name: str
    score: float                  # 综合得分
    base_score: float             # L1+L2 基础分
    qual_bonus: float             # L3 定性信号加分
    mkt_status: str               # aligned / diverged / unknown
    chain_position: ChainPosition # {industry, tier, segment, role}
    depth: StockDepth | None      # L2 深度数据
    source_trail: list[SourceRef] # 完整追溯链

@dataclass
class ChainPosition:
    industry: str
    tier: str          # 上游/中游/下游/设备/材料/应用
    segment: str       # 具体环节
    role: str          # 在环节中的角色
    sources: list[str] # 该位置的数据来源

@dataclass
class StockDepth:
    moat_total: int | None
    roe_3y: list[float]
    gross_margin: float | None
    rev_cagr: float | None
    tier_label: str    # 龙头/一线/二线/边缘

@dataclass
class SourceRef:
    layer: str    # L0/L1/L2/L3/L4
    source: str   # 具体来源
    detail: str   # 可读描述

@dataclass
class StockList:
    theme: str
    canonical_name: str
    stocks: list[StockEntry]
    chain_context: dict       # {tier: [segment, ...]}
    llm_opinion: dict | None  # LLM 原始输出 (折叠展示, 用于审计)
    total: int
```

### 4.4 置信度计算

```
base_score = Σ(source_weight × match_quality) / Σ source_weight

source_weight:
  external_map (L0 外部图谱)     = 3.5   ← 最高权重
  chain_bom     (L1 BOM)        = 2.5
  concept_ths   (L1 同花顺归因)  = 3.0
  chain_sc      (L1 supply_chain)= 2.0
  concept_em    (L1 东财概念)    = 2.0
  concept_ths_hot (L1 人气)      = 1.5
  concept_baidu (L1 百度)        = 1.0
  llm_draft     (L1 LLM推断)     = 0.5   ← 最低权重

match_quality:
  精确 match canonical             = 1.0
  别名 match                       = 0.8
  产业链同 segment match            = 0.9
  产业链上下游关联                  = 0.5
  多源交叉验证 bonus (≥2 sources)   = +0.15
  多源交叉验证 bonus (≥3 sources)   = +0.25

qual_bonus = Σ(L3 signal_strength × decay) / 10
  范围: -0.3 ~ +0.3

mkt_multiplier:
  aligned   = 1.1
  unknown   = 1.0
  diverged  = 0.8
```

---

## 5. 与现有模块集成

### 5.1 公众号分析 `analyze_wechat.py`

```
改造前: Haiku 提取 ticker → Sonnet 综合 → related_stocks (LLM 拍脑袋)
改造后:
  1. Haiku 仍提取 ticker (作为 L3 定性信号写 qual_signals)
  2. Sonnet 输出主题名称 + 产业逻辑 (不含标的筛选)
  3. Python 端对每个主题调 engine.query(theme)
  4. 报告中:
     - 主表: engine 返回的标的 + 产业链位置 + 来源追溯
     - 旁注: Sonnet 原始 related_stocks (折叠，标注 [LLM意见])
     - L3 信号: 标注「公众号文章提及」的信号会计入
```

### 5.2 复盘报告 `engine_themes.py`

```
改造前: 仅同花顺 reason → 题材→标的 (覆盖窄)
改造后:
  1. 同花顺归因 = L1 P0 信号 (实盘验证)
  2. engine.query(theme) 补充全量标的 (含未爆炒的产业链核心)
  3. L4 盘面验证结果直接展示在聚焦池: 标记 aligned/diverged
```

### 5.3 晨间情报 `interpret.py`

```
改造前: LLM 读新闻 → 输出 supply_chain 标的
改造后:
  1. LLM 识别事件名称 + 涉及的产业链环节
  2. 标的走 engine.query(theme) + enging.query_by_chain(tier, segment)
  3. LLM 只叠加定性: 确信度/催化紧迫度/方向
```

### 5.4 Dashboard `data_bridge.py`

```python
def query_theme_stocks(theme: str) -> dict:
    """主题→标的 + 产业链全景 + 置信度"""

def get_stock_themes(code: str) -> dict:
    """标的→关联主题 + 产业链位置 + L3/L4状态"""

def get_theme_chain_diagram(theme: str) -> dict:
    """产业链上下游可视化数据 (上游→中游→下游 + 标的)"""
```

---

## 6. 实施计划

### Phase 1: 数据底座 + 外部图谱导入（4-5 天）

1. `theme_stock/store.py` — SQLite 建表 (全部 7 张表)
2. `theme_stock/build_chain.py` — L1 产业链索引构建
   - BOM chain_db 导入
   - supply_chain DB 导入
   - 外部图谱解析器（支持用户提供图谱的文本格式）
3. `theme_stock/build_concept.py` — L1 概念索引构建 (东财 f103 + 同花顺)
4. `theme_stock/build_depth.py` — L2 深度数据构建 (BOM 护城河 + 理杏仁财务)
5. `theme_stock/alias_map.py` — 别名映射
6. 集成到 `daily_collect.py` 每日触发

**验收**：
- `chain_map` ≥26 行业，≥500 条标的-产业链关联
- `concept_index` ≥4000 只
- `stock_depth` ≥200 只（BOM 已分析龙头）

### Phase 2: 引擎核心 + 双反馈闭环（3-4 天）

1. `theme_stock/engine.py` — ThemeStockEngine (五层融合查询)
2. `theme_stock/matchers/concept.py` — L1 概念匹配
3. `theme_stock/matchers/chain.py` — L1 产业链匹配
4. `theme_stock/signals.py` — L3 定性信号采集 + 加权
5. `theme_stock/market_check.py` — L4 盘面验证 (联动/背离/龙头检测)
6. `theme_stock/feedback.py` — 闭环A+B 逻辑 (阈值触发/告警)
7. 单元测试

**验收**：
- `query("CPO")` top-3 含 天孚通信/新易盛/中际旭创，置信度 high
- L4 背离检测能正确识别不跟涨标的

### Phase 3: 上层集成（3-4 天）

1. `analyze_wechat.py` — 引擎输出替换 LLM related_stocks
2. `engine_themes.py` — 两源合并
3. `interpret.py` — LLM 只做事件识别
4. `data_bridge.py` — Dashboard API
5. L3 信号自动化采集（公众号/星球 daily_collect 后写入 qual_signals）

**验收**：公众号分析报告每个主题可追溯标的来源

### Phase 4: 持续运营（长期）

1. **外部图谱维护**：用户每次提供新图谱 → 导入 → 差异对比 → 人工确认
2. **人工审核**：每月审核 top-20 主题的标的映射质量
3. **财报季更新**：季报后刷新 L2 stock_depth 财务数据
4. **回测**：历史主题引擎选股 vs 实际涨幅

---

## 7. 外部图谱版本管理

外部图谱是最高质量的数据源（L0），需要独立版本管理。

```
chain_map 表中 source_ver 字段追踪每笔数据来源:

source_ver 示例:
  "external:券商图谱_新能源汽车_2026Q2"
  "external:咨询报告_半导体产业链_2025"
  "bom:2026-06-02"
  "supply_chain:2026-05-15"

图谱导入时:
  1. 解析产业名 → 匹配现有 chain_map
  2. 同一 (industry, tier, segment, code) 出现于多个来源 → 置信度 high
  3. 同一 (industry, tier, segment) 但 code 不同 → 标记冲突, 人工裁决
  4. 外部图谱有但 chain_map 没有的 segment → 新增
  5. chain_map 有但外部图谱没有的 → 保留但标记「仅BOM/LLM来源」
```

---

## 8. LLM 角色边界

| 任务 | LLM | 引擎 | 说明 |
|------|:---:|:----:|------|
| 识别主题名称 | ✅ | | 从文章/新闻中提取 |
| 主题产业逻辑叙述 | ✅ | | 市场叙事、催化分析 |
| 产业链拆解 (新主题) | ✅(辅助) | | LLM 输出 → 人工审核 → 入库 (标记 llm_draft) |
| 标的筛选 | | ✅ | **LLM 禁止直接选股** |
| 概念→标的映射 | | ✅ | 走 concept_index + chain_map |
| 置信度评分 | | ✅ | 公式计算，可追溯 |
| 护城河/财务分析 | ✅(辅助) | ✅ | LLM 辅助写 qualitative 描述, 数据来自引擎 |
| 定性信号方向判断 | ✅(辅助) | | LLM 判断正/负/中性, 强度由规则计算 |
| 盘面联动判断 | | ✅ | 纯数据计算 |

---

## 9. 目录结构

```
theme_stock/
├── __init__.py              # 公开 API: ThemeStockEngine, StockEntry, StockList
├── engine.py                # 核心引擎 (五层融合查询)
├── store.py                 # SQLite 建表 + CRUD (全部 7 张表)
├── alias_map.py             # 别名表管理
├── build_chain.py           # L1 产业链索引构建 (BOM/SC/外部图谱导入)
├── build_concept.py         # L1 概念索引构建 (东财/同花顺/百度)
├── build_depth.py           # L2 深度数据构建
├── signals.py               # L3 定性信号采集+加权
├── market_check.py          # L4 盘面验证 (联动/背离/龙头检测)
├── feedback.py              # 闭环A+B 逻辑
├── external_map.py           # L0 外部图谱解析器+导入
├── __main__.py              # CLI
└── tests/
    ├── test_engine.py
    ├── test_matchers.py
    ├── test_signals.py
    ├── test_market_check.py
    └── test_integration.py
```

---

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储 | SQLite | 与项目一致，数据量 <100MB，零运维 |
| 概念归一化 | 别名表 + 人工维护 | 准确性优先，后期加向量语义匹配兜底 |
| LLM 角色 | 仅主题识别 + 逻辑叙述 | **标的筛选必须可追溯，LLM 输出单独标注 [LLM意见]** |
| 最高权重来源 | L0 外部成熟图谱 | 券商/咨询机构产业链研究 > 平台概念标签 > LLM 推断 |
| 反馈闭环 | L3(慢) + L4(快) 双闭环 | 慢闭环累积信号修正置信度，快闭环实时检测背离 |
| 数据刷新 | 每日增量构建 + L4 盘后自动运行 | 平衡新鲜度与构建成本 |
| 版本管理 | source_ver 字段追踪每条数据来源 | 可审计、可回滚 |
