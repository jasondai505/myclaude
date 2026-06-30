"""深度题材分析 — 围绕一个主题/事件，多源交叉分析。

用法:
  python deep_topic.py --topic "康宁GlassBridge玻璃桥"
  python deep_topic.py --topic "功率半导体涨价" --extra web_results.txt

流程:
  1. DB 搜相关文章（公众号/星球/新闻）
  2. Haiku 逐源提取（论点/数据/标的）
  3. Sonnet 多源交叉综合（事件→技术→价值链→标的→节奏→风险）
  4. 输出 reports/deep_topic/{slug}_{date}.md
"""
from __future__ import annotations

import json, os, re, sqlite3, sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent))

from daily_review.llm import _load_api_key
from daily_review.roles import get_client

OUT_DIR = BASE / "reports" / "deep_topic"
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6-20250514"


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


# ============================================================
# 1. 信息采集
# ============================================================

def search_db(keywords: list[str], days: int = 30) -> list[dict]:
    """从 DB 搜索相关文章。"""
    sources: list[dict] = []
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    db = BASE / "data" / "review.db"
    if not db.exists():
        return sources

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    seen = set()
    for kw in keywords:
        like = f"%{kw}%"
        for row in conn.execute(
            "SELECT * FROM wechat_articles WHERE pub_date >= ? AND (title LIKE ? OR description LIKE ?) ORDER BY pub_date DESC LIMIT 15",
            (cutoff, like, like),
        ).fetchall():
            r = dict(row)
            key = (r.get("feed_source", ""), r.get("title", ""))
            if key not in seen:
                seen.add(key)
                r["source_type"] = f"公众号·{r.get('feed_source','')}"
                sources.append(r)
        for row in conn.execute(
            "SELECT * FROM zsxq_topics WHERE create_time >= ? AND (title LIKE ? OR text LIKE ?) ORDER BY create_time DESC LIMIT 30",
            (cutoff, like, like),
        ).fetchall():
            r = dict(row)
            key = ("zsxq", r.get("title", ""))
            if key not in seen:
                seen.add(key)
                r["source_type"] = "知识星球"
                sources.append(r)

    conn.close()
    return sources


def load_extra_sources(path: str) -> str:
    p = Path(path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


# ============================================================
# 2. Haiku 逐源提取
# ============================================================

EXTRACT_PROMPT = """从以下文章中提取结构化信息。只输出 JSON，不要其他文字。

文章来源: {source}
标题: {title}
正文:
{body}

```json
{{
  "thesis": "核心论点（一句话）",
  "key_facts": ["关键事实1", "关键事实2"],
  "stocks": [{{"code": "xxxxxx", "name": "xxx", "role": "在产业链中的角色", "direction": "利好/利空/中性"}}],
  "data_points": ["具体数字或数据"],
  "unique_angle": "本文独特视角（区别于其他来源）"
}}
```"""


def extract_per_source(sources: list[dict], name_map: dict) -> list[dict]:
    """Haiku 对每个信息源逐篇提取。"""
    client = get_client("synthesis", timeout=90)
    results = []

    for i, s in enumerate(sources):
        title = s.get("title", "")
        body = s.get("description") or s.get("text") or ""
        if len(body) < 100:
            continue

        name_refs = []
        seen_n = set()
        for m in re.finditer(r"[一-鿿]{2,6}", title + body[:3000]):
            name = m.group()
            code = name_map.get(name)
            if code and name not in seen_n:
                seen_n.add(name)
                name_refs.append(f"{name}={code}")

        prompt = EXTRACT_PROMPT.format(
            source=s.get("source_type", ""),
            title=title[:200],
            body=body[:4000],
        )
        if name_refs:
            prompt = prompt.replace(
                "正文:",
                f"名称→代码速查:\n{chr(10).join(name_refs[:30])}\n\n正文:",
            )

        try:
            resp = client.messages.create(
                model=MODEL_HAIKU, max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "disabled"}, timeout=90,
            )
            text = "\n".join(
                block.text for block in resp.content
                if hasattr(block, "text") and block.text
            )
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if not m:
                m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                data = json.loads(m.group(1) if m.lastindex else m.group(0))
            else:
                data = {"thesis": text[:200], "key_facts": [], "stocks": [], "data_points": [], "unique_angle": ""}
            data["source"] = s.get("source_type", "")
            data["title"] = title[:120]
            data["date"] = (s.get("pub_date") or s.get("create_time") or "")[:10]
            results.append(data)
            print(f"  [{i+1}/{len(sources)}] {title[:40]}...")
        except Exception as e:
            print(f"  [{i+1}/{len(sources)}] FAIL: {title[:30]}... {e}")

    return results


# ============================================================
# 3. Sonnet 多源交叉综合
# ============================================================

SYNTHESIS_PROMPT = """你是 A 股产业链深度分析师。围绕以下主题，基于多源信息，撰写一份深度题材分析报告。

## 主题
{topic}

## 多源提取结果
{extractions}

## 相关标的行情
{quotes}

## 报告要求

输出 Markdown，按以下结构（对标同花顺 DEEPTOPIC 标准）：

### 一、事件背景
- 什么事件、何时、谁、哪个环节
- 市场第一时间反应（涨跌幅佐证）

### 二、技术/产业深度解析
- 核心原理（用通俗语言解释）
- 关键性能数据
- 技术路线对比（至少 2 条路线的对比表）

### 三、价值链迁移分析
- 谁受益？为什么？（分确定性和弹性）
- 谁受损？为什么？（带标的名称和代码）
- 跨源交叉验证：多源一致看多/看空的标的有哪些？有分歧的有哪些？

### 四、行情交叉验证
- 用上面给的行情数据，验证市场是否已经在定价
- 涨了的：市场认什么逻辑？
- 跌了的：市场在交易什么担忧？

### 五、受益标的细分
- 🟢 核心受益（确定性最高，有订单/绑定/独家）
- 🟡 弹性受益（概念关联但暴露低或未兑现）
- 🔴 纯概念（伪相关，回避）

### 六、投资节奏判断
- 当前处于什么阶段（题材驱动期/业绩验证期/兑现期）
- 后续关键催化事件和时间节点
- 建议策略（追/等/回避）

### 七、风险条件化
- 不是列举风险，而是「什么条件成立→什么判断失效」
- 每条风险带证伪条件

### 八、核心结论
- 一句话总结
- 确定性排序

格式要求:
- 所有标的必须带代码（6位）和名称
- 数据来自多源提取时标注来源
- 行情数据直接引用
- 分歧/纠偏要明确写出哪条源说了什么
"""


def synthesize(topic: str, extractions: list[dict], quotes_text: str) -> str:
    """Sonnet 多源交叉综合。"""
    client = get_client("deep", timeout=180)
    extractions_text = json.dumps(extractions, ensure_ascii=False, indent=2)

    prompt = SYNTHESIS_PROMPT.format(
        topic=topic,
        extractions=extractions_text[:25000],
        quotes=quotes_text[:3000],
    )

    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=MODEL_SONNET, max_tokens=12000,
                messages=[{"role": "user", "content": prompt}],
                timeout=180,
            )
            text = "\n".join(
                block.text for block in resp.content
                if hasattr(block, "text") and block.text
            )
            return text
        except Exception as e:
            if attempt == 1:
                return f"Sonnet 综合失败: {e}"
            continue
    return ""


# ============================================================
# 4. 报告输出
# ============================================================

def render_report(topic: str, synthesis: str, extractions: list[dict],
                  date_str: str = "") -> Path:
    """输出 Markdown 报告。"""
    d = date_str or _today()
    slug = re.sub(r"[^\w一-鿿]+", "_", topic.strip()).strip("_")[:60]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{slug}_{d}.md"

    n_sources = len(extractions)
    sources_list = sorted(set(e.get("source", "") for e in extractions))

    lines = [
        f"# {topic} — 深度题材分析",
        f"",
        f"> {n_sources} 源 | {len(sources_list)} 类信息源 | 生成于 {d}",
        f"> 信息源: {', '.join(sources_list)}",
        f"",
        synthesis,
        f"",
        f"---",
        f"",
        f"## 附录：逐源提取摘要",
        f"",
    ]
    for i, e in enumerate(extractions):
        lines.append(f"### #{i+1} [{e.get('source','')}] {e.get('title','')}")
        lines.append(f"**论点**: {e.get('thesis','')}")
        facts = e.get("key_facts", [])
        if facts:
            for f in facts:
                lines.append(f"- {f}")
        stocks = e.get("stocks", [])
        if stocks:
            lines.append("")
            lines.append("| 代码 | 名称 | 角色 | 方向 |")
            lines.append("|------|------|------|:--:|")
            for s in stocks:
                lines.append(f"| {s.get('code','')} | {s.get('name','')} | {s.get('role','')} | {s.get('direction','')} |")
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ============================================================
# CLI
# ============================================================

def main():
    import argparse
    p = argparse.ArgumentParser(description="深度题材多源交叉分析")
    p.add_argument("--topic", required=True, help="分析主题")
    p.add_argument("--keywords", type=str, help="DB搜索关键词，逗号分隔")
    p.add_argument("--extra", type=str, help="外部补充信息源文件路径")
    p.add_argument("--date", type=str, help="日期 YYYY-MM-DD")
    p.add_argument("--days", type=int, default=30, help="DB搜索天数范围")
    args = p.parse_args()

    topic = args.topic
    date_str = args.date or _today()
    keywords = [kw.strip() for kw in (args.keywords or topic).split(",") if kw.strip()]

    if not _load_api_key():
        print("无 API key，退出")
        return

    print(f"\n{'='*60}")
    print(f"深度题材分析: {topic}")
    print(f"{'='*60}")
    print(f"\n[1/4] 信息采集...")
    print(f"  关键词: {keywords}")
    sources = search_db(keywords, args.days)
    print(f"  DB 搜到 {len(sources)} 篇")

    extra_text = ""
    if args.extra:
        extra_text = load_extra_sources(args.extra)
        if extra_text:
            sources.append({
                "title": "外部补充信息",
                "description": extra_text,
                "source_type": "外部补充(web/雪球/研报)",
                "pub_date": date_str,
            })
            print(f"  外部补充: {len(extra_text)} 字")

    if not sources:
        print("  无信息源，退出")
        return

    import data as _data
    name_map = _data._load_name_to_code_map()

    print(f"\n[2/4] Haiku 逐源提取 ({len(sources)} 篇)...")
    extractions = extract_per_source(sources, name_map)
    print(f"  成功提取 {len(extractions)} 篇")

    if not extractions:
        print("  提取全失败，退出")
        return

    print(f"\n[3/4] 拉取相关标的行情...")
    all_codes = set()
    for e in extractions:
        for s in e.get("stocks", []):
            code = str(s.get("code", "")).strip()
            if re.match(r"\d{6}$", code):
                all_codes.add(code)
    quotes_text = ""
    if all_codes:
        try:
            quotes = _data.fetch_stock_quotes(sorted(all_codes), batch_size=30)
            quote_lines = []
            for code in sorted(all_codes):
                q = quotes.get(code, {})
                name = q.get("name", name_map.get(code, ""))
                chg = q.get("change_pct", 0) or 0
                quote_lines.append(
                    f"{code} {name}: {chg:+.2f}% "
                    f"量比{q.get('vol_ratio',1) or 1:.1f} "
                    f"换手{q.get('turnover_pct',0) or 0:.1f}%"
                )
            quotes_text = "\n".join(quote_lines)
            print(f"  拉取 {len(all_codes)} 只标的行情")
        except Exception as e:
            quotes_text = f"行情获取失败: {e}"
            print(f"  {quotes_text}")
    else:
        quotes_text = "（未提取到标的代码）"
        print(f"  {quotes_text}")

    print(f"\n[4/4] Sonnet 多源综合...")
    synthesis = synthesize(topic, extractions, quotes_text)
    if not synthesis or synthesis.startswith("Sonnet 综合失败"):
        print(f"  FAIL: {synthesis}")
        return

    report = render_report(topic, synthesis, extractions, date_str)
    print(f"\n{'='*60}")
    print(f"Report: {report}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
