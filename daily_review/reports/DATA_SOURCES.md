# 数据源 & 语料源接入参考

> 供外部 agent (Codex) 调用。标注「稳定」= 已跑通 ≥1 周无变更。

## 1. 外部 Feed 源

| 源 | 类型 | URL/接入方式 | 频率 | 状态 |
|---|------|-------------|------|:--:|
| WeWe-RSS | JSON Feed | `GET http://111.231.44.12:4000/feeds/all.json?limit=200` | 按需 | 稳定 |
| RSS 健康检查 | CLI | `python daily_review/check_rss_health.py` | 按需 | 稳定 |
| 东方财富研报 | HTTP API | `reportapi.eastmoney.com` (in `research_reports.py`) | 日 | 稳定 |
| 东方财富公告 | HTTP API | akshare → `announcements.py` | 日 | 稳定 |
| 东方财富行业 | HTTP API | akshare → `industry_research.py` | 日 | 稳定 |
| 互动易 | 爬虫 | `interactions.py` | 日 | 稳定 |
| 调研 | 爬虫 | `surveys.py` | 日 | 稳定 |
| 业绩预告 | 爬虫 | `earnings.py` | 日 | 稳定 |
| 限售解禁 | 爬虫 | `lockups.py` | 日 | 稳定 |
| 财务指标 | akshare | `financials.py` | 日 | 稳定 |
| EPS 预测 | akshare | `eps_forecast.py` | 日 | 稳定 |
| 微博 | 爬虫 | `weibo.py` | 日 | skip(不稳定) |
| 韭研公社 | PDF 爬虫 | `jiuyang.py` | 日 | skip(不稳定) |
| 实时行情 | Redis | `stock.xiaoxinren.cn:5679` (key `Market`) | 实时 | 稳定 |
| 美股盘后 | yfinance | `config.py` → `%%US_AFTER_HOURS%%` 注入 | 盘后 | 稳定 |
| 美股历史 | yfinance | `data.py` → `_fetch_us_history()` | 按需 | 稳定 |
| 理杏仁 | REST API | `open.lixinger.com/api` (token: env `LIXINGER_TOKEN`) | 按需 | 稳定 |

## 2. CLI 入口（可直接 `python <script>` 调用）

### 2.1 采集（拉取→入库）

```bash
# 全量采集（按 orchestrator 配置的阶段）
python orchestrator.py <phase>          # phase: pre | morning | daily | night | pre_game

# 单源采集
python daily_review/daily_collect.py --source <name> [--since YYYY-MM-DD] [--days N]

# 可用 source 名:
#   wechat, announcements, research, news, industry, financials,
#   earnings, sentiment_track, surveys, interactions, lockups, eps,
#   zsxq, stock_dossiers, weibo, jiuyang
```

### 2.2 分析（LLM 管线）

```bash
# 公众号全流程（采集→两阶段AI分析）
python run_wechat.py                           # 完整流程
python run_wechat.py --skip-collect            # 仅分析不采集

# 零散调用
python daily_review/analyze_wechat.py          # 公众号分析（含深度投研双管道）
python daily_review/analyze_zsxq.py [date]     # 知识星球分析

# 盘前建议
python daily_review/_run_advice.py             # 产出 advice_YYYY-MM-DD.md

# Dashboard
python daily_review/_dashboard.py              # 产出 Dashboard.md
```

### 2.3 Serenity 海外情报

```bash
# OCR 图片→文本（增量，只处理新图）
python daily_review/extractors/image_ocr.py --dir serenity

# LLM 提取（从 OCR 文本→结构化情报）
python daily_review/_run_serenity_batch.py

# 图片放哪里：serenity/*.png（.gitignore 内，手动放入）
```

### 2.4 复盘

```bash
python daily_review/run.py                     # 完整复盘（题材+涨停+龙虎榜+引擎）
```

## 3. 编程接口（Python import）

### 3.1 Collector 统一签名

```python
# 所有 collector 在 daily_review/collectors/<name>.py
# 统一入口函数:
def run(since: date, until: date, universe_fn: Callable = None) -> dict:
    """返回 {"last_date": str, "added": int, "status": str, "message": str}"""

# 示例:
from daily_review.collectors.wechat import run as collect_wechat
from daily_review.collectors.zsxq import run as collect_zsxq
from datetime import date
result = collect_wechat(since=date.today(), until=date.today())
```

### 3.2 Store（DB 读写）

```python
import store  # daily_review/store.py
store.init_feeds_tables()

# 公众号
store.query_wechat_articles(since: str, until: str = "", unanalyzed_only: bool = True) -> list[dict]
store.save_wechat_articles(rows: list[dict]) -> int           # 返回新增数
store.mark_wechat_analyzed(articles: list[dict])

# 知识星球
store.query_zsxq_by_date(date_str: str) -> list[dict]

# 采集状态
store.upsert_collect_status(source, last_date, status, message, added_count)

# DB 路径: daily_review/data/review.db (sqlite3, WAL mode)
```

### 3.3 LLM 调用

```python
from daily_review.llm import _load_api_key                    # → str
from roles import get_client                                  # → Anthropic client
client = get_client("synthesis", timeout=120)                 # "synthesis"=Haiku, "deep"=Sonnet
client = get_client("deep", timeout=120)
```

### 3.4 数据工具

```python
from data import (
    fetch_stock_quotes,          # 批量行情 (codes: list[str]) → dict[code, {name,pe,pb,...}]
    extract_codes_from_text,     # 名称+代码双通道提取 (text: str) → list[str]
)
from llm_validator import (
    validate_codes,              # 代码白名单校验 (codes: list[str]) → dict[code, {valid,name}]
    validate_name_code_pairs,    # 名称-代码交叉验证
)
from config import (
    WATCHLIST,                   # list[str] 自选股池
    REPORT_DIR,                  # Path = daily_review/reports/
    DB_PATH,                     # Path = daily_review/data/review.db
    REDIS_HOST, REDIS_PORT,      # 实时行情
    UA,                          # HTTP User-Agent
)
```

## 4. 核心 DB 表（可直接 SQL 查询）

| 表 | 行数 | 用途 | 关键列 |
|----|------|------|--------|
| `wechat_articles` | 456 | 公众号文章 | feed_source, title, url, pub_date, description, analyzed_at |
| `zsxq_topics` | 31,921 | 知识星球帖子 | topic_id, author, title, text, topic_type, create_time |
| `announcements` | 46,231 | 公告 | code, name, title, type, date, url |
| `deep_read_results` | 4,409 | 公告深研结果 | code, name, event_type, hunting_domain, score, verdict |
| `research_reports` | 3,102 | 研报 | code, name, institution, title, rating, target_price |
| `industry_reports` | 2,089 | 行业研报 | title, industry_name, institution, pdf_url |
| `stock_news` | 8,121 | 个股新闻 | code, title, source, publish_time |
| `catalyst_signals` | 3,884 | 催化信号 | catalyst_name, catalyst_type, source_type |
| `marginal_changes` | 6,084 | 边际变化 | code, theme, direction, content |
| `collect_status` | 22 | 采集管线状态 | source, last_date, status, message |
| `market_snapshot` | 35 | 大盘快照 | sh_close, sz_close, north_hgt |
| `theme_daily` | 8,311 | 每日题材 | theme, count, stocks |
| `valuation_cache` | 7,631 | 估值缓存 | code, data_type, data_json |
| `consensus_snapshot` | 118 | 一致预期 | code, eps_avg_y1, avg_target_price, report_count |
| `interactions` | 2,892 | 互动易 | code, question, answer, ask_time |
| `inst_survey` | 128 | 机构调研 | code, inst_count, survey_date |
| `ocr_tracker` | — | Serenity OCR | file_path, ocr_text, source_type, analysis_done |

> DB 路径: `daily_review/data/review.db`（主库）+ `daily_review/data/ocr_tracker.db`（OCR）

## 5. 报告输出路径

| 路径 | 内容 |
|------|------|
| `reports/dashboard.md` | 每日仪表盘 |
| `reports/advice/advice_{date}.md` | 盘前建议 |
| `reports/wechat_analysis/wechat_analysis_{date}.md` | 公众号深度分析 |
| `reports/zsxq_analysis/zsxq_analysis_{date}.md` | 知识星球分析 |
| `reports/zsxq_analysis/zsxq_shendu_{date}.md` | 深度投研→zsxq 管线 |
| `reports/serenity/serenity_daily_{date}.md` | 海外情报日报 |
| `reports/serenity/serenity_extract_{date}.json` | 海外情报结构化 JSON |
| `reports/serenity/shendu/shendu_{date}.json` | 深度投研结构化 JSON |
| `reports/briefings/weekly_brief_{date}.md` | 周度综述 |
| `reports/review/review_{date}.md` | 复盘报告 |
| `reports/deep_read/` | 公告深研档案 |
| `reports/research_dossiers/` | 研报个股档案 |
| `reports/catalyst/` | 催化筛查 |
| `reports/feeds/` | 原始 feed 采集 |
| `reports/feed_index.md` | feed 索引页 |

> 所有路径相对于 `daily_review/`。Obsidian vault 根 = `daily_review/reports/`。

## 6. 公众号 RSS 完整调用链

```
图片放入 serenity/ 目录
  → python daily_review/extractors/image_ocr.py --dir serenity    # OCR
  → python daily_review/_run_serenity_batch.py                   # LLM 提取
  → reports/serenity/serenity_daily_{date}.md

RSS 有内容
  → python run_wechat.py                                         # 采集+分析
  或分步:
  → python daily_review/check_rss_health.py                      # 健康检查
  → python daily_review/daily_collect.py --source wechat --since YYYY-MM-DD
  → python daily_review/analyze_wechat.py                        # 含双管道:
      ├─ shendu 提取器 → reports/serenity/shendu/
      └─ zsxq 综合研判 → reports/zsxq_analysis/zsxq_shendu_{date}.md
```

## 7. 环境变量

| 变量 | 用途 |
|------|------|
| `ANTHROPIC_API_KEY` | LLM API (deepseek 代理) |
| `DR_LLM_MODEL` | Sonnet 模型覆盖 (默认 claude-sonnet-4-6) |
| `REDIS_PASSWORD` | 实时行情 Redis |
| `LIXINGER_TOKEN` | 理杏仁 API |
| `WEWE_RSS_URL` | RSS 地址覆盖 (默认 111.231.44.12:4000) |
