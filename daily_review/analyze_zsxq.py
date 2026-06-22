"""知识星球帖子深度 AI 分析（两阶段）。

阶段一 Haiku: 逐帖拆解（核心论点+关键数据+A股标的+分类+评分）
阶段二 Sonnet: 综合研判（共识主题+分歧+自选警示+行动建议）

对标 analyze_wechat.py 的两阶段模式。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
import store
from config import REPORT_DIR, WATCHLIST

MODEL_SONNET = os.getenv("DR_LLM_MODEL", "claude-sonnet-4-6-20250514")
MODEL_HAIKU = "claude-haiku-4-5-20251001"
TIMEOUT = 120
BATCH_SIZE = 10
MAX_POST_BODY = 600


from daily_review.llm import _load_api_key


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON 解析失败: {e}")
        return None


# ============================================================
# 阶段一：逐帖拆解（Haiku）
# ============================================================

_S1 = """你是 A 股基本面分析师。分析以下知识星球帖子。

{batch}

对每帖输出一个 JSON 对象，全部放入数组:
[
  {{
    "idx": 帖子序号(1-{n}),
    "title": "帖子标题(截取前50字)",
    "author": "作者",
    "thesis": "核心论点（1-2句，引用原文关键数据和逻辑）",
    "key_facts": ["关键事实1", "关键事实2"],
    "tickers": [{{"code": "6位代码", "name": "简称", "direction": "看多/看空/中性", "relevance": "关联逻辑"}}],
    "category": "半导体/新能源/AI算力/消费/宏观/医药/资源/汽车/军工/其他",
    "relevance_score": 1-5
  }}
]
只输出 JSON 数组，不要其他文字。"""


def _haiku_analyze_batch(client, batch: list[dict]) -> list[dict]:
    posts_text = []
    for i, p in enumerate(batch):
        title = (p.get("title") or "")[:120]
        author = p.get("author", "")
        text = p.get("text") or ""
        body = text[len(title):].strip() if text.startswith(title) else text.strip()
        body = (body or "")[:MAX_POST_BODY]
        codes = p.get("stock_codes") or "[]"
        posts_text.append(
            f"--- 帖子 {i+1} ---\n"
            f"作者: {author}\n"
            f"标题: {title}\n"
            f"正文: {body}\n"
            f"附带代码: {codes}"
        )

    prompt = _S1.format(batch="\n\n".join(posts_text), n=len(batch))
    try:
        resp = client.messages.create(
            model=MODEL_HAIKU, max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            articles = json.loads(m.group(0))
            # L2: 校验 tickers 代码
            from llm_validator import validate_codes as _vc
            invalid_count = 0
            for a in articles:
                for t in a.get("tickers", []):
                    if not t.get("code"): continue
                    v = _vc([t["code"]]).get(t["code"], {})
                    if not v.get("valid"):
                        t["code"] = ""
                        t["name"] = f"[无效代码]{t.get('name','')}"
                        invalid_count += 1
            if invalid_count:
                print(f"    [L2] 过滤 {invalid_count} 个无效代码")
            return articles
        return []
    except Exception as e:
        print(f"    [Haiku err] batch: {e}")
        return []


# ============================================================
# 阶段二：综合研判（Sonnet）
# ============================================================

_S2 = """你是 A 股基本面投资分析师。今天是 {today}。

以下是今日知识星球帖子的逐条拆解：

{articles_json}

我的自选股池（仅供参考我的关注方向）：{watchlist}

请综合研判，输出 JSON:
{{
  "market_narrative": "当前星球讨论的核心叙事（3-4句，共识+分歧+情绪）",
  "themes": [
    {{
      "name": "主题",
      "conviction": "高/中/低",
      "post_indices": [1,2,5],
      "thesis": "核心逻辑（引用原文数据和事件）",
      "catalyst": "近期催化及时间节点",
      "horizon": "短期/中期/长期",
      "related_stocks": ["6位代码1", "6位代码2"],
      "risk": "主要风险"
    }}
  ],
  "divergences": [
    {{
      "topic": "分歧话题",
      "bull_view": "看多逻辑及来源",
      "bear_view": "看空逻辑及来源",
      "our_take": "基于多源信息的判断（1-2句）"
    }}
  ],
  "watchlist_alerts": [
    {{
      "code": "6位代码",
      "signal": "正面/负面/关注",
      "reason": "具体逻辑（引用原文数据）",
      "urgency": "高/中/低"
    }}
  ],
  "action_items": [
    {{
      "action": "建议操作",
      "target": "6位代码",
      "rationale": "理由",
      "priority": 1-5
    }}
  ],
  "key_question": "当前最需要回答的关键问题",
  "summary": "200字整体摘要 - 今日星球最重要的3-5个方向"
}}
只输出 JSON。post_indices 指向上方拆解编号。"""


def _sonnet_synthesize(client, articles: list[dict], today: str) -> dict:
    prompt = _S2.format(
        today=today,
        articles_json=json.dumps(articles, ensure_ascii=False, indent=2),
        watchlist=", ".join(WATCHLIST),
    )
    try:
        resp = client.messages.create(
            model=MODEL_SONNET, max_tokens=12000,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = _extract_json(text) or {}
        # L2: 校验综合研判中的股票代码
        from llm_validator import validate_codes as _vc
        invalid = 0
        for theme in data.get("themes", []):
            valid_stocks = []
            for c in theme.get("related_stocks", []):
                if _vc([c]).get(c, {}).get("valid"):
                    valid_stocks.append(c)
                else:
                    invalid += 1
            theme["related_stocks"] = valid_stocks
        for alert in data.get("watchlist_alerts", []):
            code = alert.get("code", "")
            if code and not _vc([code]).get(code, {}).get("valid"):
                alert["code"] = ""
                alert["_invalid"] = True
                invalid += 1
        if invalid:
            print(f"    [L2] 综合研判过滤 {invalid} 个无效代码")
        return data
    except Exception as e:
        print(f"  [Sonnet err] {e}")
        return {}


# ============================================================
# 报告输出
# ============================================================

def _write_report(data: dict, single_results: list[dict], today: str, total_posts: int):
    path = REPORT_DIR / "zsxq_analysis" / f"zsxq_analysis_{today}.md"
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    buf = [
        f"# 知识星球深度分析 {today}",
        "",
        f"> {total_posts} 帖 | {len(single_results)} 条有实质内容 | 生成于 {now_ts}",
        "",
    ]

    n = data.get("market_narrative", "")
    if n:
        buf.extend(["## 市场叙事", "", n, ""])

    themes = data.get("themes", [])
    if themes:
        buf.append("## 核心主题")
        buf.append("")
        for t in themes:
            conv = t.get("conviction", "·")
            emoji = {"高": "🔥", "中": "📌", "低": "👀"}.get(conv, "·")
            indices = t.get("post_indices", [])
            idx_str = f" (#{', #'.join(str(i) for i in indices[:10])})" if indices else ""
            buf.append(f"### {emoji} {t.get('name', '')}（{conv}）{idx_str}")
            buf.append("")
            for key, label in [
                ("thesis", "逻辑"), ("catalyst", "催化"),
                ("horizon", "时间维度"), ("risk", "风险"),
            ]:
                v = t.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
            stocks = t.get("related_stocks", [])
            if stocks:
                buf.append(f"- **标的**: {', '.join(stocks)}")
            buf.append("")

    divs = data.get("divergences", [])
    if divs:
        buf.append("## 分歧")
        buf.append("")
        for d in divs:
            buf.append(f"### ⚡ {d.get('topic', '')}")
            buf.append("")
            for key, label in [
                ("bull_view", "看多"), ("bear_view", "看空"), ("our_take", "判断"),
            ]:
                v = d.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
            buf.append("")

    alerts = data.get("watchlist_alerts", [])
    if alerts:
        buf.append("## 自选股预警")
        buf.append("")
        buf.append("| 代码 | 信号 | 逻辑 | 紧迫度 |")
        buf.append("|------|------|------|--------|")
        for a in alerts:
            sig = a.get("signal", "·")
            sig_emoji = {"正面": "🟢", "负面": "🔴", "关注": "🟡"}.get(sig, "·")
            buf.append(
                f"| {a.get('code', '')} | {sig_emoji} {sig} | "
                f"{a.get('reason', '')} | {a.get('urgency', '')} |"
            )
        buf.append("")

    actions = data.get("action_items", [])
    if actions:
        buf.append("## 行动建议")
        buf.append("")
        for a in sorted(actions, key=lambda x: x.get("priority", 99)):
            buf.append(
                f"- **P{a.get('priority', '?')}** [{a.get('target', '')}] "
                f"{a.get('action', '')} — {a.get('rationale', '')}"
            )
        buf.append("")

    q = data.get("key_question", "")
    if q:
        buf.extend(["## 关键问题", "", f"> {q}", ""])

    s = data.get("summary", "")
    if s:
        buf.extend(["## 整体摘要", "", s, ""])

    if single_results:
        buf.append("## 逐帖拆解")
        buf.append("")
        for i, sr in enumerate(single_results):
            if not sr:
                continue
            title = sr.get("title", "?")
            author = sr.get("author", "?")
            cat = sr.get("category", "?")
            score = sr.get("relevance_score", 0)
            thesis = sr.get("thesis", "")
            facts = sr.get("key_facts", [])
            tickers = sr.get("tickers", [])

            buf.append(f"### #{i + 1} [{author}] {title}")
            buf.append("")
            buf.append(f"**{cat}** | {'★' * score}{'☆' * (5 - score)}")
            buf.append("")
            if thesis:
                buf.append(f"**论点**: {thesis}")
                buf.append("")
            if facts:
                for f in facts[:3]:
                    buf.append(f"- {f}")
                buf.append("")
            if tickers:
                buf.append("| 代码 | 名称 | 方向 | 逻辑 |")
                buf.append("|------|------|------|------|")
                for tk in tickers[:5]:
                    buf.append(
                        f"| {tk.get('code', '')} | {tk.get('name', '')} | "
                        f"{tk.get('direction', '')} | {tk.get('relevance', '')} |"
                    )
                buf.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(buf), encoding="utf-8")
    return path


# ============================================================
# 主流程
# ============================================================

def main(target_date: str = ""):
    if target_date:
        t = date.fromisoformat(target_date)
    else:
        t = date.today()
    today = t.isoformat()
    since = (t - timedelta(days=7)).isoformat()

    print(f"知识星球深度分析（两阶段）| {since} ~ {today}")

    store.init_feeds_tables()

    all_rows = store.query_zsxq_by_date(today)
    if not all_rows:
        all_rows = store.query_zsxq_by_date((date.today() - timedelta(days=1)).isoformat())

    signal_rows = [
        r for r in all_rows
        if r.get("topic_type") in ("research", "review")
        and (r.get("title") or "").strip()
        and len(r.get("text") or "") > 20
    ]

    if not signal_rows:
        print(f"  今日无有实质内容的星球帖子（共 {len(all_rows)} 帖，均为标题/简短消息）")
        return

    print(f"  总计 {len(all_rows)} 帖，有实质内容 {len(signal_rows)} 帖")

    from roles import get_client as _get_client

    # 阶段一：逐帖拆解 → synthesis
    print(f"  阶段一：逐帖拆解...")
    s1_client = _get_client("synthesis", timeout=TIMEOUT)
    all_results = []
    for start in range(0, len(signal_rows), BATCH_SIZE):
        batch = signal_rows[start:start + BATCH_SIZE]
        print(f"    批次 {start // BATCH_SIZE + 1}/{(len(signal_rows) - 1) // BATCH_SIZE + 1} "
              f"({len(batch)} 帖)...")
        results = _haiku_analyze_batch(s1_client, batch)
        all_results.extend(results)

    print(f"  阶段一完成: {len(all_results)} 条有效提取")

    # 阶段二：综合研判 → deep
    print(f"  阶段二：综合研判...")
    s2_client = _get_client("deep", timeout=TIMEOUT)
    data = _sonnet_synthesize(s2_client, all_results, today)
    if not data:
        print("  Sonnet 研判失败")
        return

    path = _write_report(data, all_results, today, len(signal_rows))
    print(f"  -> {path}")

    json_path = REPORT_DIR / "zsxq_analysis" / f"zsxq_analysis_{today}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {json_path}")

    print("完成")
    return path


if __name__ == "__main__":
    main()
