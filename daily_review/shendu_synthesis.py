"""深度投研洞见 · Sonnet 综合研判。

让 LLM 通读 111 篇结构化 JSON，产出：
  1. 核心叙事线（半年的非共识框架）
  2. 预测准确度回溯（哪些 VP 已验证/证伪）
  3. 当前未兑现预期差 Top 20
  4. 跨主题共性发现
"""
from __future__ import annotations

import json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import _load_api_key
from anthropic import Anthropic

SHENDU_DIR = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu"
OUT_PATH = Path(__file__).resolve().parent / "reports" / "serenity" / "shendu_synthesis.md"
MODEL = "claude-sonnet-4-6-20250514"
MAX_TOKENS = 8000


def _build_article_summary(data: dict, idx: int) -> str:
    """将单篇文章压缩为结构化摘要，节省 token。"""
    date = data.get("date", "")
    title = (data.get("title_clean", "") or data.get("title", ""))[:80]
    thesis = (data.get("thesis", "") or "")[:150]
    lbj = (data.get("load_bearing_judgment", "") or "")[:120]

    vps = data.get("variant_perceptions", [])
    vp_lines = []
    for vp in vps:
        conf = vp.get("confidence", "")
        vp_lines.append(f"  [{conf}] {vp.get('consensus','')[:60]} -> {vp.get('variant','')[:80]}")

    chains = data.get("chains_involved", [])[:5]
    themes = data.get("themes", [])[:5]

    vs = data.get("valuation_spectrum", [])
    stock_lines = []
    for v in vs:
        codes = ",".join(v.get("codes", [])[:4])
        tier = v.get("tier", "")
        stock_lines.append(f"  {tier}: {codes}")

    risks = data.get("risk_signals", [])
    risk_lines = [f"  [{r.get('type','')}] {r.get('target','')}: {r.get('detail','')[:60]}" for r in risks[:3]]

    parts = [
        f"### [{idx}] {date} | {title}",
        f"**论点**: {thesis}",
    ]
    if lbj:
        parts.append(f"**承重判断**: {lbj}")
    if vp_lines:
        parts.append("**预期差**:\n" + "\n".join(vp_lines))
    if stock_lines:
        parts.append("**标的**:\n" + "\n".join(stock_lines))
    if risk_lines:
        parts.append("**风险**:\n" + "\n".join(risk_lines))
    if chains:
        parts.append(f"**产业链**: {', '.join(chains)}")
    if themes:
        parts.append(f"**主题**: {', '.join(themes)}")

    return "\n".join(parts)


THEMES_HISTORICAL = """
## 部分已被市场验证的主题（供回溯参考）

1. 光纤 (2/7): Bruce 判断供给硬缺口6000万芯公里，价格年内涨80%。实际：长飞光纤2-6月涨约60%，G.652.D价格从25→35+元/芯公里。
2. 钨 (3/1): 中国双重锁死机制，供给曲线垂直。实际：钨精矿从14→22万/吨，厦门钨业涨约45%。
3. 氧化钇 (2/28): 中国出口管制→海内外价差10倍。实际：氧化钇持续紧缺，稀土板块整体走强。
4. 碳酸锂 (3/28): 价格中枢锚定12-15万。实际：碳酸锂在10-14万区间震荡，目前约12万。
5. H200解禁 (5/17): 销售分成机制=中国市场准入商品化。实际：国产算力芯片在5-6月持续走强。
6. 日本断供 (6/22): 四子链必须分层甄别，一体化握上游者纯受益。实际：厦门钨业/金力永磁等6月最后一周走强。
"""

SYSTEM_PROMPT = """你是 A 股基本面分析师。收到 111 篇深度投研洞见文章的压缩摘要（每篇含论点/承重判断/预期差/标的/产业链/主题）。这些文章的时间范围是 2026年1月16日 到 6月23日。

你的任务是完成以下四个部分的综合研判。用中文输出，格式为 Markdown。"""

USER_PROMPT = """以下是 2026年1月16日～6月23日期间的深度投研洞见文章摘要。请完成四个部分的综合研判：

{articles}

{themes_historical}

---

## 研判要求

### 第一部分：核心叙事线

这不是简单的时间线罗列。请回答：
1. 这半年 Bruce 的核心非共识框架是什么？用 1-2 句话概括他的「底层世界观」。
2. 按阶段划分：哪些主题是第一幕（铺垫）、第二幕（市场发现）、第三幕（正在演绎）？
3. 几个关键主题之间的逻辑关系——它们是一条主线还是多条并行线？

### 第二部分：预测准确度回溯

对比上文提供的「已被市场验证的主题」，逐条给出：
- 准确度评分（完全正确/方向正确但幅度有偏差/部分错误/待验证）
- 主要贡献：哪条预期差（variant perception）最有价值？
- 漏了什么：回头看，文章有没有遗漏重要变量？

### 第三部分：当前未兑现预期差 Top 20

从全部文章中筛选出**置信度=高、且当前大概率未被市场定价**的预期差。
每条包含：
- 来源文章日期和标题
- 市场共识 vs Bruce 判断
- 为什么它还未被定价
- 证伪条件

注意：不要选择已经被市场证实或证伪的。优先选仍在窗口期内的。

### 第四部分：跨主题共性

1. 扫描全部文章，识别反复出现的底层逻辑模式。例如「供给刚性+需求非线性=价格暴利」、「政策从防风险→稳市场=定价范式切换」等。
2. 这些模式中，哪些在当前市场中仍有解释力？
3. 哪条逻辑已经被市场广泛接受（从非共识变为共识）？

输出格式：直接输出四大段 Markdown，不要加前言后语。"""


def main():
    articles_data = []
    for f in sorted(SHENDU_DIR.iterdir()):
        if not f.name.startswith("shendu_2026") or f.name.startswith("shendu__"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("thesis") or data.get("variant_perceptions"):
                articles_data.append(data)
        except Exception:
            pass

    articles_data.sort(key=lambda a: a.get("date", ""))

    print(f"加载 {len(articles_data)} 篇")

    # 构建压缩摘要
    summaries = []
    total_chars = 0
    for i, a in enumerate(articles_data):
        s = _build_article_summary(a, i + 1)
        summaries.append(s)
        total_chars += len(s)

    articles_text = "\n\n---\n\n".join(summaries)
    print(f"摘要总长度: {total_chars} 字符")

    # 如果太长，裁剪（保留全部但每篇更短）
    max_prompt = 180000
    if total_chars > max_prompt:
        ratio = max_prompt / total_chars * 0.9
        print(f"超长，压缩比例: {ratio:.1%}")
        # 重新生成更短的版本
        summaries = []
        for i, a in enumerate(articles_data):
            date = a.get("date", "")
            title = (a.get("title_clean", "") or a.get("title", ""))[:50]
            thesis = (a.get("thesis", "") or "")[:100]
            vps = a.get("variant_perceptions", [])
            vp_str = "; ".join(
                f"[{vp.get('confidence','')}] {vp.get('consensus','')[:40]} -> {vp.get('variant','')[:50]}"
                for vp in vps[:2]
            )
            summaries.append(f"[{i+1}] {date} | {title}\n  {thesis}\n  {vp_str}")

        articles_text = "\n".join(summaries)
        print(f"压缩后: {len(articles_text)} 字符")

    prompt = USER_PROMPT.format(
        articles=articles_text,
        themes_historical=THEMES_HISTORICAL,
    )

    print(f"Prompt 总长度: {len(prompt)} 字符")
    print("调用 Sonnet...")

    client = Anthropic(api_key=_load_api_key(), timeout=600)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        timeout=600,
    )

    parts = [block.text for block in resp.content if hasattr(block, "text") and block.text]
    result = "\n".join(parts)

    # 加标题头
    header = f"# 深度投研洞见 · Sonnet 综合研判\n\n"
    header += f"> 分析范围: {articles_data[0].get('date','')} ~ {articles_data[-1].get('date','')}\n"
    header += f"> 文章总数: {len(articles_data)} 篇\n"
    header += f"> 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    header += "---\n\n"

    OUT_PATH.write_text(header + result, encoding="utf-8")
    print(f"\n输出: {OUT_PATH} ({len(result)} 字符)")


if __name__ == "__main__":
    main()
