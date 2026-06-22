"""重跑 Sonnet 综合研判，追加到已有报告"""
import json, os, re, sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))
from daily_review.config import REPORT_DIR, WATCHLIST

REPORT_PATH = REPORT_DIR / "wechat" / f"wechat_analysis_{date.today().isoformat()}.md"

from daily_review.llm import _load_api_key


def _extract_articles_from_report(path: Path) -> list[dict]:
    """从已有报告的逐篇拆解部分提取文章数据"""
    text = path.read_text(encoding="utf-8")
    articles = []
    sections = re.split(r"\n### #\d+ ", text)
    for sec in sections[1:]:
        lines = sec.strip().split("\n")
        feed_title = lines[0] if lines else ""
        m = re.match(r"\[(.+?)\]\s+(.+)", feed_title)
        feed = m.group(1) if m else "?"
        title = m.group(2) if m else feed_title

        cat_score_m = re.search(r"\*\*(.+?)\*\* \| (.+)", text[text.find(sec):text.find(sec)+200] if text.find(sec) >= 0 else "")
        category = cat_score_m.group(1) if cat_score_m else "?"

        thesis_m = re.search(r"\*\*论点\*\*:\s*(.+?)(?:\n|$)", sec)
        thesis = thesis_m.group(1) if thesis_m else ""

        oneliner_m = re.search(r">\s*(.+?)(?:\n|$)", sec)
        oneliner = oneliner_m.group(1) if oneliner_m else ""

        facts = []
        for line in lines:
            if line.startswith("- ") and "|" not in line:
                facts.append(line[2:])

        tickers = []
        in_table = False
        for line in lines:
            if line.startswith("| 标的 |"):
                in_table = True
                continue
            if in_table and line.startswith("| ") and "关联逻辑" not in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    tickers.append({"code": parts[0], "relevance": parts[1]})
            elif in_table and not line.startswith("|"):
                in_table = False

        articles.append({
            "feed": feed, "title": title, "category": category,
            "thesis": thesis, "one_liner": oneliner,
            "key_facts": facts, "tickers": tickers,
        })
    return articles


def _synthesize(client, articles: list[dict], today: str) -> dict:
    prompt = f"""你是 A 股基本面投资分析师。今天是 {today}。

以下是近 3 天微信公众号文章的逐篇深度拆解：

{json.dumps(articles, ensure_ascii=False, indent=2)}

我的自选股池：{', '.join(WATCHLIST)}

请综合研判，输出 JSON:
{{
  "market_narrative": "当前市场核心叙事（3-4 句，共识+分歧+情绪）",
  "themes": [
    {{
      "name": "主题",
      "conviction": "高/中/低",
      "article_indices": [1,2,5],
      "feeds": ["号1","号2"],
      "thesis": "核心逻辑（引用原文数据和事件）",
      "catalyst": "近期催化及时间节点",
      "horizon": "短期/中期/长期",
      "related_stocks": ["自选股代码"],
      "risk": "主要风险"
    }}
  ],
  "cross_validation": [
    {{
      "theme": "主题",
      "consensus_view": "一致看法",
      "divergent_view": "分歧或反对意见",
      "our_take": "基于自选股持仓的判断"
    }}
  ],
  "watchlist_alerts": [
    {{
      "code": "自选股代码",
      "signal": "正面/负面/关注",
      "reason": "具体逻辑（引用原文数据）",
      "urgency": "高/中/低"
    }}
  ],
  "action_items": [
    {{
      "action": "建议操作",
      "target": "标的（尽量用自选股代码）",
      "rationale": "理由",
      "priority": 1-5
    }}
  ],
  "key_question": "当前最需要回答的关键问题",
  "summary": "200 字整体摘要"
}}
只输出 JSON。article_indices 指向上方拆解编号。"""

    resp = client.messages.create(
        model="claude-sonnet-4-6-20250514", max_tokens=12000,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "disabled"},
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        print("  [ERR] 未找到 JSON")
        print(text[:500])
        return {}
    try:
        return json.loads(text[start:end+1])
    except json.JSONDecodeError as e:
        print(f"  [ERR] JSON 解析失败: {e}")
        with open(REPORT_DIR / "_sonnet_raw.txt", "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  原始输出已保存到 _sonnet_raw.txt")
        return {}


def _format_synthesis(data: dict) -> str:
    buf = []
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
            indices = t.get("article_indices", [])
            idx_str = ""
            if indices:
                idx_str = " （#" + ", #".join(str(i) for i in indices) + "）"
            buf.append(f"### {emoji} {t.get('name','')} （{conv}确信度）{idx_str}")
            buf.append("")
            for key, label in [("thesis", "逻辑"), ("catalyst", "催化"),
                               ("horizon", "时间维度"), ("risk", "风险")]:
                v = t.get(key, "")
                if v:
                    buf.append(f"- **{label}**: {v}")
            feeds = t.get("feeds", [])
            if feeds:
                buf.append(f"- **来源**: {', '.join(feeds)}")
            stocks = t.get("related_stocks", [])
            if stocks:
                buf.append(f"- **关联标的**: {', '.join(stocks)}")
            buf.append("")

    cross = data.get("cross_validation", [])
    if cross:
        buf.append("## 交叉验证")
        buf.append("")
        for c in cross:
            buf.append(f"### {c.get('theme', '')}")
            buf.append("")
            for key, label in [("consensus_view", "一致看法"),
                               ("divergent_view", "分歧/补充"),
                               ("our_take", "我们的判断")]:
                v = c.get(key, "")
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
            buf.append(f"| {a.get('code','')} | {sig_emoji} {sig} | "
                       f"{a.get('reason','')} | {a.get('urgency','')} |")
        buf.append("")

    actions = data.get("action_items", [])
    if actions:
        buf.append("## 行动建议")
        buf.append("")
        for a in sorted(actions, key=lambda x: x.get("priority", 99)):
            buf.append(f"- **P{a.get('priority','?')}** [{a.get('target','')}] "
                       f"{a.get('action','')} — {a.get('rationale','')}")
        buf.append("")

    q = data.get("key_question", "")
    if q:
        buf.extend(["## 关键问题", "", f"> {q}", ""])

    s = data.get("summary", "")
    if s:
        buf.extend(["## 整体摘要", "", s, ""])

    return "\n".join(buf)


def main():
    today = date.today().isoformat()
    print(f"从报告提取文章数据: {REPORT_PATH}")
    articles = _extract_articles_from_report(REPORT_PATH)
    print(f"  提取 {len(articles)} 篇")
    if not articles:
        print("  无文章数据，退出")
        return

    api_key = _load_api_key()
    if not api_key:
        print("  无 API Key")
        return

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    print("调用 Sonnet 综合研判...")
    data = _synthesize(client, articles, today)
    if not data:
        print("  综合研判失败")
        return

    synthesis = _format_synthesis(data)
    if not synthesis:
        print("  格式化失败")
        return

    report_text = REPORT_PATH.read_text(encoding="utf-8")
    marker = "## 逐篇拆解"
    idx = report_text.find(marker)
    if idx == -1:
        print(f"  找不到 '{marker}' 标记")
        return

    new_text = report_text[:idx] + synthesis + "\n" + report_text[idx:]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(new_text, encoding="utf-8")
    print(f"  综合研判已插入报告: {REPORT_PATH}")


if __name__ == "__main__":
    main()
