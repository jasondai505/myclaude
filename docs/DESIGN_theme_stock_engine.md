# 主题-标的客观映射引擎 设计文档

> 2026-06-05 | v1.0

## 1. 问题定义

### 当前现状

整个项目 **没有统一的「标的-主题-产业链」知识层**。四个分析模块各自选股：

| 模块 | 选股方式 | 问题 |
|------|---------|------|
| 复盘报告 `engine_themes.py` | 同花顺强势股 `reason` 字段提取题材→标的 | 覆盖窄（仅强势股），一只股票可能被归因到边缘题材 |
| 公众号分析 `analyze_wechat.py` | Haiku 从文章提取 ticker → Sonnet 凭印象综合 | **完全依赖 LLM 一次性输出**，无数据验证 |
| BOM 分析 `run.py` | LLM 拆产业链 → LLM 选龙头 → 财务数据回填 | 产业链拆解本身靠 LLM，可能遗漏核心环节 |
| 晨间情报 `interpret.py` | LLM 读新闻 → 映射到 supply_chain DB 已有标的 | 映射依赖预填数据，新主题无覆盖 |

**四个模块，四套逻辑，互不验证。标的关联质量的底线完全取决于 LLM 单次输出的运气。**

### 目标

建成 **单一数据底座**，所有上层分析统一从此获取「主题→标的」映射：

1. **客观**：标的来源可追溯到具体数据（概念板块、行业分类、产业链位置）
2. **可验证**：每个标的有明确的产业链位置 + 置信度评分 + 数据来源
3. **全覆盖**：基于实时数据源，不依赖 LLM 训练数据
4. **可演进**：新增数据源时，映射质量自动提升，无需改上层代码

---

## 2. 架构总览

```
                         ┌─────────────────────────────────┐
                         │         上层消费方               │
                         ├─────────────────────────────────┤
                         │ analyze_wechat  公众号分析       │
                         │ engine_themes   复盘题材         │
                         │ interpret.py    晨间情报         │
                         │ run.py          BOM分析          │
                         │ data_bridge     Dashboard        │
                         │ _run_advice.py  盘前建议         │
                         └──────────────┬──────────────────┘
                                        │ Python API
                         ┌──────────────▼──────────────────┐
                         │     ThemeStockEngine             │
                         │     (theme_stock/engine.py)      │
                         │                                  │
                         │  query(theme) → StockList        │
                         │  query_multi(themes) → ThemeMap  │
                         │  query_by_chain(tier,seg) → ...  │
                         │  enrich(stock_list) → Enriched   │
                         └──────────────┬──────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
    ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
    │  ConceptMatcher  │     │  ChainMatcher    │     │  BizMatcher      │
    │  (概念板块匹配)   │     │  (产业链匹配)     │     │  (业务匹配)       │
    │                 │     │                 │     │                 │
    │ 东财 f103概念    │     │ BOM chain_db    │     │ 营收构成(远期)    │
    │ 同花顺 强势股    │     │ SC supply_chain │     │ 客户/供应商(远期) │
    │ 同花顺 hot_stock│     │ 申万行业→环节    │     │ 产品线(远期)     │
    │ 百度 concept_tag│     │                 │     │                 │
    └────────┬────────┘     └────────┬────────┘     └────────┬────────┘
              │                      │                        │
              └──────────────────────┼────────────────────────┘
                                     │
                         ┌───────────▼──────────┐
                         │    StockMetaStore     │
                         │  (theme_stock/store)  │
                         │                       │
                         │  全A基础信息缓存       │
                         │  概念-标的索引         │
                         │  产业链-标的索引       │
                         │  置信度评分引擎        │
                         └───────────────────────┘
```

---

## 3. 数据层设计

### 3.1 数据源盘点

| 优先级 | 数据源 | 覆盖范围 | 可靠性 | 刷新频率 | 当前可用 |
|--------|--------|---------|--------|---------|---------|
| P0 | 东财 `f103` concepts 字段 | 全A 5200+ 只 | 高（东财官方维护） | 每日 | ✅ live_scanner |
| P0 | 同花顺 hot_themes `reason` | 强势股 ~200只/日 | 高（实盘归因） | 每日 | ✅ data.py |
| P1 | BOM chain_db | 26 行业 | 中（LLM+可人工审） | 按需 | ✅ bom_analyzer |
| P1 | morning supply_chain | ~20 事件 | 中（LLM+种子） | 按需 | ✅ morning_intel |
| P2 | 同花顺 hot_stocks `concept_tags` | 人气前100 | 中 | 每日 | ✅ data.py |
| P2 | 百度 `concept_tags` | 按需查询 | 中 | 实时 | ✅ data.py |
| P3 | 申万行业 `f100` | 全A | 高 | 每日 | ✅ live_scanner |
| 远期 | 营收构成（理杏仁/财报） | 财报披露公司 | 高 | 季报 | ❌ |

### 3.2 核心表结构

#### 概念索引 `concept_index`

从东财 f103 + 同花顺 reason + 百度 concept_tags 构建。

```sql
CREATE TABLE concept_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,          -- 6位代码
    name        TEXT NOT NULL,          -- 股票名称
    concept     TEXT NOT NULL,          -- canonical 概念名称
    source      TEXT NOT NULL,          -- eastmoney / tonghuashun / baidu
    weight      REAL DEFAULT 1.0,      -- 来源权重（强势股归因更高）
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_ci_code ON concept_index(code);
CREATE INDEX idx_ci_concept ON concept_index(concept);
```

#### 产业链索引 `chain_index`

从 BOM chain_db + morning supply_chain 导入。

```sql
CREATE TABLE chain_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    industry    TEXT NOT NULL,          -- 产业名称 (canonical)
    tier        TEXT NOT NULL,          -- 上游/中游/下游/设备/材料/应用
    segment     TEXT NOT NULL,          -- 环节名称
    code        TEXT NOT NULL,          -- 6位代码
    name        TEXT NOT NULL,
    role        TEXT,                   -- 在环节中的角色描述
    source      TEXT NOT NULL,          -- bom / supply_chain / manual
    confidence  TEXT DEFAULT 'medium',  -- high / medium / low
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_ci2_industry ON chain_index(industry);
CREATE INDEX idx_ci2_code ON chain_index(code);
```

#### 别名映射 `alias_map`

主题名称归一化。

```sql
CREATE TABLE alias_map (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alias       TEXT NOT NULL UNIQUE,   -- 原始名称，如 "Spectrum-X"
    canonical   TEXT NOT NULL,          -- 归一化名称，如 "CPO"
    source      TEXT                     -- 来源
);
```

### 3.3 索引构建流程

```
┌─────────────────────────────────────────────────────┐
│              每日增量构建（daily_collect 后触发）      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  东财 f103: "AI芯片 国产芯片 半导体"                   │
│    → split by space → ["AI芯片", "国产芯片", "半导体"] │
│    → normalize(alias_map) → insert concept_index     │
│                                                     │
│  同花顺 reason: "AI算力+光模块+CPO"                    │
│    → split by + → ["AI算力", "光模块", "CPO"]        │
│    → normalize → insert concept_index (weight=2.0)   │
│                                                     │
│  BOM chain_db → chain_index (直接导入)               │
│  morning supply_chain → chain_index (直接导入)        │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## 4. 引擎核心

### 4.1 查询接口

```python
class ThemeStockEngine:
    """主题→标的客观映射引擎"""

    def query(self, theme: str, *,
              limit: int = 20,
              min_confidence: float = 0.3) -> StockList:
        """输入主题名称，返回按置信度排序的标的列表"""

    def query_multi(self, themes: list[str], **kw) -> dict[str, StockList]:
        """批量查询"""

    def enrich(self, codes: list[str]) -> list[EnrichedStock]:
        """反向：给标的附加产业链位置+概念标签"""

    def get_chain_context(self, theme: str) -> ChainContext:
        """获取主题的产业链全景（上游/中游/下游各有哪些环节）"""

    def search_themes(self, keyword: str, limit: int = 20) -> list[str]:
        """模糊搜索主题名称"""
```

### 4.2 输出结构

```python
@dataclass
class StockEntry:
    code: str                    # 6位代码
    name: str                    # 名称
    score: float                 # 综合置信度 0-1
    sources: list[SourceRef]     # 可追溯的数据来源

@dataclass
class SourceRef:
    source: str        # concept_eastmoney / concept_ths / chain_bom / chain_sc
    detail: str        # 如 "东财概念→AI芯片" / "BOM→光模块→上游→光器件"
    tier: str | None   # 产业链层级
    segment: str | None  # 具体环节
    confidence: str    # high / medium / low

@dataclass
class StockList:
    theme: str
    canonical_name: str
    stocks: list[StockEntry]
    chain_context: dict          # {tier: [segment, ...]}
    total: int

@dataclass
class EnrichedStock:
    code: str
    name: str
    concepts: list[str]
    chain_positions: list[dict]  # [{industry, tier, segment, role}]
    moat_scores: dict | None     # BOM 护城河评分（如有）
```

### 4.3 置信度计算

```
score = Σ(source_weight × match_quality) / Σ source_weight

source_weight:
  concept_ths (强势股实盘归因)  = 3.0
  chain_bom    (BOM产业链)      = 2.5
  chain_sc     (supply_chain)   = 2.0
  concept_eastmoney (东财概念)  = 2.0
  concept_tonghuashun (人气)    = 1.5
  concept_baidu                 = 1.0

match_quality (0-1):
  精确匹配 canonical name       = 1.0
  别名匹配                      = 0.8
  产业链同环节匹配               = 0.9
  产业链上下游关联               = 0.5
  多源交叉验证 bonus             = +0.15 if ≥2 sources agree
  多源交叉验证 bonus             = +0.25 if ≥3 sources agree
```

**置信度分级**：
- `high`（≥0.7）：≥2 个来源一致，且至少一个来自产业链
- `medium`（0.4-0.7）：有概念匹配或产业链匹配
- `low`（<0.4）：仅宽松匹配，需人工复核

### 4.4 查询算法

```
query(theme):

1. normalize(theme) → canonical  (alias_map)
2. concept_match: concept_index WHERE concept = canonical
   → {code: [SourceRef]}
3. chain_match: chain_index WHERE industry/segment LIKE canonical
   → {code: [SourceRef(tier, segment)]}
4. chain_expand: chain_match 中找到的 segment → 该 segment 下所有 code
5. merge: 合并 concept_match + chain_match，按 code 去重
6. score: 对每个 code 计算加权置信度
7. rank: score 降序，取 top-N（默认 20）
8. enrich: 附加实时行情（PE/PB/市值/涨跌幅）
9. return StockList
```

---

## 5. 与现有模块集成

### 5.1 公众号分析 (`analyze_wechat.py`)

```
改造前:  Sonnet prompt → related_stocks（LLM 拍脑袋）
改造后:
  1. Sonnet 输出主题名称 + 产业逻辑（不含标的）
  2. Python 端调 engine.query(theme) → 获取客观标的列表
  3. 报告中标注：
     - 主表: engine 返回的标的 + 产业链位置 + 来源
     - 折叠区: Sonnet 原始 related_stocks（用于对比审计 LLM 行为）
```

### 5.2 复盘报告 (`engine_themes.py`)

```
改造前: 同花顺 reason 字段 → 题材→标的（覆盖窄，仅强势股）
改造后:
  1. 同花顺归因仍作为 P0 信号（实盘验证过的关联）
  2. engine.query(theme) 补充全量标的（含未被爆炒但产业链核心的标的）
  3. 两源合并去重，标注来源
```

### 5.3 晨间情报 (`interpret.py`)

```
改造前: LLM 读新闻 → 输出 supply_chain 标的
改造后:
  1. LLM 只识别事件 + 涉及的产业链环节
  2. 标的走 engine.query(theme) + engine.query_by_chain(tier, segment)
  3. LLM 叠加定性: 确信度、催化紧迫度、方向判断
```

### 5.4 Dashboard (`data_bridge.py`)

新增 API：

```python
def query_theme_stocks(theme: str) -> dict:
    """Dashboard 个股查询页: 输入主题→输出标的数据表"""

def get_stock_themes(code: str) -> dict:
    """Dashboard 个股查询页: 输入代码→该股关联的所有主题+产业链位置"""
```

---

## 6. 实施计划

### Phase 1: 数据底座（预计 3-5 天）

1. `theme_stock/store.py` — SQLite 建表 + CRUD
2. `theme_stock/build_concept_index.py` — 东财 f103 + 同花顺 reason → `concept_index`
3. `theme_stock/build_chain_index.py` — BOM DB + supply_chain DB → `chain_index`
4. `theme_stock/alias_map.py` — 别名映射（人工种子 + 导入现有 `normalize_theme` 规则）
5. 集成到 `daily_collect.py` 每日触发增量构建

**验证**：`concept_index` ≥4000 只，`chain_index` ≥26 行业

### Phase 2: 引擎核心（预计 2-3 天）

1. `theme_stock/engine.py` — ThemeStockEngine 完整实现
2. `theme_stock/matchers/concept.py` — ConceptMatcher
3. `theme_stock/matchers/chain.py` — ChainMatcher
4. 置信度评分 + 排序逻辑
5. 单元测试

**验证**：`query("CPO")` 返回 天孚通信/新易盛/中际旭创 且 top-3 置信度 high

### Phase 3: 上层集成（预计 3-4 天）

1. `analyze_wechat.py` — 引擎输出替换 LLM related_stocks
2. `engine_themes.py` — 两源合并（同花顺归因 + 引擎）
3. `data_bridge.py` — Dashboard API
4. `interpret.py` — LLM 只做事件识别，标的走引擎

**验证**：公众号分析报告标的可追溯来源

### Phase 4: 持续优化（长期）

1. 人工审核：每个主题 3-5 个 gold standard 标的
2. 财报营收构成接入
3. 语义匹配兜底（向量化搜索，覆盖 LLM 新造的主题词）
4. 回测验证：历史主题引擎选股 vs 实际涨幅

---

## 7. CLI

```bash
python -m theme_stock query "CPO" --top 20
python -m theme_stock query "玻璃基板" --chain-only
python -m theme_stock query "AI算力" --min-confidence 0.5
python -m theme_stock build --full          # 全量重建索引
python -m theme_stock build --incremental   # 增量更新
python -m theme_stock alias add "Spectrum-X" "CPO"
python -m theme_stock validate --theme "CPO"  # 人工审核模式
```

---

## 8. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储 | SQLite | 与项目一致，数据量 <100MB，零运维 |
| 概念归一化 | 别名表为主 | 先保证准确性，后期加向量语义匹配兜底 |
| LLM 角色 | 仅做主题识别+逻辑叙述 | 标的筛选必须可追溯，不能被 LLM 黑盒化 |
| 数据刷新 | 每日增量 + 每周全量 | 平衡新鲜度和构建成本 |
| BOM 数据集成 | 引用导入（带 source 标注） | 保持 BOM 为单一数据源，更新自动反映 |

---

## 9. 目录结构

```
theme_stock/
├── __init__.py           # 公开 API
├── engine.py             # 核心引擎 ThemeStockEngine
├── store.py              # SQLite 建表 + CRUD
├── alias_map.py          # 别名表管理
├── build_index.py        # 索引构建（全量+增量）
├── matchers/
│   ├── __init__.py
│   ├── concept.py        # 概念板块匹配器
│   └── chain.py          # 产业链匹配器
├── enrich.py             # 标的富化（附加行情/财务数据）
├── __main__.py           # CLI 入口
└── tests/
    ├── test_engine.py
    ├── test_matchers.py
    └── test_integration.py
```
